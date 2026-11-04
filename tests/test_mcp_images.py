import asyncio
import base64
import io
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from mcp import Client
from PIL import Image

from app.api import mcp
from app.api.schema import ImageRequest, ReadRequest, TaskRequest
from app.tools import wechat
from app.tools.images import preview
from app.workflow.context import ContextStore

SESSION = {
    "session_id": "selected@chatroom",
    "name": "测试群",
    "chat_type": "群聊",
    "unread": 0,
    "last_time": "",
}


class PreviewTests(unittest.TestCase):
    def test_large_transparent_image_is_bounded_jpeg_without_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.png"
            source = Image.new("RGBA", (2400, 1200), (255, 0, 0, 0))
            source.save(path, exif=Image.Exif())
            data, width, height = preview(path)
        self.assertEqual((width, height), (1280, 640))
        self.assertLessEqual(len(data), 512 * 1024)
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.format, "JPEG")
            self.assertEqual(result.getpixel((0, 0)), (255, 255, 255))
            self.assertFalse(result.getexif())

    def test_invalid_and_oversized_images_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.png"
            path.write_bytes(b"invalid image")
            with self.assertRaisesRegex(ValueError, "解码"):
                preview(path)
            Image.new("1", (5000, 5000)).save(path)
            with self.assertRaisesRegex(ValueError, "像素"):
                preview(path)


class ImageContextTests(unittest.TestCase):
    def setUp(self):
        self.store = ContextStore()
        with patch.object(wechat, "sessions", return_value=("self", [SESSION])):
            self.task = self.store.start(
                TaskRequest(session_ids=[SESSION["session_id"]])
            )
        row = {
            "local_id": 7,
            "sort_seq": 10,
            "_db_rel": "message/message_0.db",
            "create_time": 1000,
            "sender": "我",
            "sender_status": "resolved",
            "content": "[图片]",
        }
        with patch.object(wechat, "messages", return_value=[row]):
            page = self.store.read(
                ReadRequest(task_id=self.task.task_id, session_id=SESSION["session_id"])
            )
        self.request = ImageRequest(
            task_id=self.task.task_id, message_id=page.messages[0].message_id
        )

    def test_image_scope_and_budgets(self):
        state = self.store.tasks[self.task.task_id]
        chars = state.chars
        with patch.object(
            wechat, "image", return_value=(b"jpeg", 640, 480, "high")
        ) as read:
            result, data = self.store.image(self.request)
            self.assertEqual(data, b"jpeg")
            self.assertEqual(result.remaining_reads, 5)
            self.assertLess(result.remaining_chars, chars)
            self.assertEqual(
                read.call_args.args,
                ("self", SESSION, state.window, ("message/message_0.db", 7)),
            )
            self.store.image(self.request)
            result, _ = self.store.image(self.request)
            self.assertEqual(read.call_count, 3)
            self.assertEqual(result.remaining_reads, 5)

    def test_unknown_expired_and_exhausted_requests_never_read_images(self):
        with patch.object(wechat, "image") as read:
            with self.assertRaisesRegex(ValueError, "已读取"):
                self.store.image(
                    ImageRequest(task_id=self.task.task_id, message_id="foreign")
                )
            state = self.store.tasks[self.task.task_id]
            state.chars = 100
            with self.assertRaisesRegex(ValueError, "预算"):
                self.store.image(self.request)
            state.chars = 1000
            state.chars = 0
            with self.assertRaisesRegex(ValueError, "预算"):
                self.store.image(self.request)
            state.chars = 1000
            state.expires = 0
            with self.assertRaisesRegex(ValueError, "过期"):
                self.store.image(self.request)
            read.assert_not_called()

    def test_failed_image_does_not_spend_success_budget(self):
        state = self.store.tasks[self.task.task_id]
        before = (state.chars, state.reads)
        with (
            patch.object(wechat, "image", side_effect=ValueError("无缓存")),
            self.assertRaisesRegex(ValueError, "无缓存"),
        ):
            self.store.image(self.request)
        self.assertEqual(before, (state.chars, state.reads))

    def test_image_reads_are_independent_of_text_read_count(self):
        self.store.tasks[self.task.task_id].reads = 0
        with patch.object(wechat, "image", return_value=(b"jpeg", 640, 480, "high")):
            result, _ = self.store.image(self.request)
        self.assertEqual(result.remaining_reads, 0)

    def test_protocol_returns_real_image_block_instead_of_base64_text(self):
        buffer = io.BytesIO()
        Image.new("RGB", (64, 32), "green").save(buffer, format="JPEG")
        data = buffer.getvalue()

        async def check():
            with (
                patch.object(mcp, "store", self.store),
                patch.object(
                    wechat, "image", return_value=(data, 64, 32, "thumbnail-cache")
                ),
            ):
                async with Client(mcp.server) as client:
                    result = await client.call_tool(
                        "read_image", {"request": self.request.model_dump()}
                    )
            self.assertFalse(result.is_error)
            self.assertIsNone(result.structured_content)
            self.assertEqual(
                [block.type for block in result.content], ["text", "image"]
            )
            text, image = result.content
            self.assertEqual(json.loads(text.text)["source_variant"], "thumbnail-cache")
            self.assertEqual(image.mime_type, "image/jpeg")
            self.assertEqual(base64.b64decode(image.data), data)

        asyncio.run(check())


class ImageDatabaseTests(unittest.TestCase):
    def test_media_export_uses_sensitive_directory_and_cleans_on_success_or_error(self):
        for fail in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                workdir = Path(temp) / "run-image"
                workdir.mkdir()
                db = Mock(wxid="self", workdir=str(workdir))
                row = {"local_id": 7, "local_type": 3, "create_time": 1000}

                def write(_db, _md, _row, _user, _index, output, _keys, fail=fail):
                    output.mkdir()
                    path = output / "selected.png"
                    Image.new("RGB", (100, 50), "blue").save(path)
                    if fail:
                        raise OSError("sensitive path or key must not escape")
                    return {
                        "path": str(path),
                        "variant": "high",
                        "previewable": True,
                    }, None

                with (
                    patch.object(
                        wechat.core, "_create_sensitive_workdir", return_value=workdir
                    ),
                    patch.object(wechat.core, "WeChatDB", return_value=db),
                    patch.object(wechat, "query_rows", return_value=[row]),
                    patch.object(
                        wechat.core, "_ensure_cfg_dword", return_value=(None, "")
                    ),
                    patch.object(wechat.core, "MediaDownloader"),
                    patch.object(wechat.core, "_index_image_files", return_value={}),
                    patch.object(
                        wechat.core, "_write_image_from_row", side_effect=write
                    ),
                ):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "读取失败") as error:
                            wechat.image(
                                "self",
                                SESSION,
                                (900, 1001),
                                ("message/message_0.db", 7),
                            )
                        self.assertNotIn("sensitive", str(error.exception))
                    else:
                        data, width, height, variant = wechat.image(
                            "self", SESSION, (900, 1001), ("message/message_0.db", 7)
                        )
                        self.assertTrue(data.startswith(b"\xff\xd8"))
                        self.assertEqual((width, height, variant), (100, 50, "high"))
                self.assertFalse(workdir.exists())

    def test_account_switch_nonimage_and_deleted_message_are_rejected_before_export(
        self,
    ):
        db = Mock(wxid="self")

        @contextmanager
        def database():
            yield db

        with (
            patch.object(wechat, "database", database),
            patch.object(wechat.core, "_write_image_from_row") as write,
        ):
            with self.assertRaisesRegex(ValueError, "账号已改变"):
                wechat.image("other", SESSION, (0, 1000), ("message/message_0.db", 7))
            for rows, error in (([], "原消息"), ([{"local_type": 1}], "不是图片")):
                with (
                    patch.object(wechat, "query_rows", return_value=rows),
                    self.assertRaisesRegex(ValueError, error),
                ):
                    wechat.image(
                        "self", SESSION, (0, 1000), ("message/message_0.db", 7)
                    )
            write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
