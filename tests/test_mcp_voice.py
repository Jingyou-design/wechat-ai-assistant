import asyncio
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from mcp import Client

from app.api import mcp
from app.api.schema import DetailRequest, ReadRequest, TaskRequest
from app.tools import wechat
from app.workflow.context import ContextStore

SESSION = {
    "session_id": "voice@chatroom",
    "name": "语音测试",
    "chat_type": "群聊",
    "unread": 0,
    "last_time": "",
}


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Mock(wxid="self", workdir=self.temp.name)

        @contextmanager
        def database():
            yield self.db

        patch.object(wechat, "database", database).start()
        self.addCleanup(patch.stopall)
        self.row = {
            "local_type": 34,
            "local_id": 7,
            "server_id": 70,
            "source": '<msg><voicetrans transtext="明天上午发修改稿" /></msg>',
        }
        patch.object(wechat, "query_rows", return_value=[self.row]).start()

    def read(self):
        return wechat.voice("self", SESSION, (0, 1000), ("message/message_0.db", 7))

    def test_native_transcript_does_not_decode_or_run_asr(self):
        with (
            patch.object(wechat.core, "_write_voice_from_row") as audio,
            patch.object(wechat.core, "_create_local_asr_recognizer") as asr,
        ):
            self.assertEqual(self.read(), ("明天上午发修改稿", "wechat"))
        audio.assert_not_called()
        asr.assert_not_called()

    def test_local_asr_uses_existing_decoder_and_does_not_persist_transcript(self):
        self.row["source"] = ""
        path = Path(self.temp.name) / "voice.wav"
        with (
            patch.object(wechat.core, "rust_silk_status", return_value=(True, "")),
            patch.object(wechat.core, "local_asr_status", return_value=(True, "")),
            patch.object(
                wechat.core,
                "_write_voice_from_row",
                return_value=({"path": str(path), "decoded": True}, None, ""),
            ) as decode,
            patch.object(
                wechat.core, "_create_local_asr_recognizer", return_value="recognizer"
            ),
            patch.object(
                wechat.core,
                "_transcribe_wav_local",
                return_value=("本地转写结果", None),
            ) as transcribe,
            patch.object(wechat.core, "_load_asr_cache") as cache,
        ):
            self.assertEqual(self.read(), ("本地转写结果", "local_asr"))
            self.assertEqual(decode.call_args.args[1], self.row)
            transcribe.assert_called_once_with("recognizer", path)
            cache.assert_not_called()

    def test_missing_components_and_nonvoice_fail_clearly(self):
        self.row["source"] = ""
        with (
            patch.object(wechat.core, "rust_silk_status", return_value=(False, "")),
            self.assertRaisesRegex(wechat.ReadError, "start_all.cmd"),
        ):
            self.read()
        self.row["local_type"] = 1
        with self.assertRaisesRegex(wechat.ReadError, "不是语音"):
            self.read()
        with self.assertRaisesRegex(wechat.ReadError, "账号已改变"):
            wechat.voice("other", SESSION, (0, 1000), ("message/message_0.db", 7))


class VoiceContextTests(unittest.TestCase):
    def setUp(self):
        self.store = ContextStore()
        row = {
            "local_id": 7,
            "sort_seq": 7,
            "_db_rel": "message/message_0.db",
            "create_time": 1000,
            "sender": "我",
            "sender_status": "resolved",
            "content": "[语音]",
        }
        with (
            patch.object(wechat, "sessions", return_value=("self", [SESSION])),
            patch.object(wechat, "messages", return_value=[row]),
        ):
            self.task = self.store.start(
                TaskRequest(session_ids=[SESSION["session_id"]])
            )
            page = self.store.read(
                ReadRequest(task_id=self.task.task_id, session_id=SESSION["session_id"])
            )
        self.request = DetailRequest(
            task_id=self.task.task_id, message_id=page.messages[0].message_id
        )

    def test_voice_scope_truncation_and_budget(self):
        with patch.object(
            wechat, "voice", return_value=("\x01" * 10000, "local_asr")
        ) as transcribe:
            result = self.store.voice(self.request)
        self.assertLessEqual(len(result.model_dump_json()), 4000)
        self.assertIsNotNone(result.next_offset)
        self.assertEqual(result.remaining_reads, 5)
        self.assertEqual(transcribe.call_args.args[3], ("message/message_0.db", 7))
        with patch.object(wechat, "voice") as transcribe:
            with self.assertRaisesRegex(ValueError, "已读取"):
                self.store.voice(
                    DetailRequest(task_id=self.task.task_id, message_id="foreign")
                )
            transcribe.assert_not_called()

    def test_voice_reads_are_independent_of_text_page_limit(self):
        task = self.store.tasks[self.task.task_id]
        task.reads = 0
        previous_chars = task.chars
        with patch.object(wechat, "voice", return_value=("直接转写", "local_asr")):
            result = self.store.voice(self.request)
        self.assertEqual(result.remaining_reads, 0)
        self.assertLess(result.remaining_chars, previous_chars)

    def test_protocol_returns_bounded_text_and_source(self):
        async def check():
            with (
                patch.object(mcp, "store", self.store),
                patch.object(
                    wechat, "voice", return_value=("明天上午发修改稿", "wechat")
                ),
            ):
                async with Client(mcp.server) as client:
                    result = await client.call_tool(
                        "transcribe_voice", {"request": self.request.model_dump()}
                    )
            self.assertFalse(result.is_error)
            self.assertIsNone(result.structured_content)
            content = json.loads(result.content[0].text)
            self.assertEqual(content["content"], "明天上午发修改稿")
            self.assertEqual(content["transcript_source"], "wechat")

        asyncio.run(check())

    def test_audio_files_cleaned_even_when_transcription_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "run-voice"
            directory.mkdir()
            db = Mock(wxid="self", workdir=str(directory))

            def decode(*_args):
                path = directory / "private.wav"
                path.write_bytes(b"synthetic audio")
                return {"path": str(path), "decoded": True}, None, ""

            with (
                patch.object(
                    wechat.core, "_create_sensitive_workdir", return_value=directory
                ),
                patch.object(wechat.core, "WeChatDB", return_value=db),
                patch.object(wechat, "query_rows", return_value=[{"local_type": 34}]),
                patch.object(wechat.core, "rust_silk_status", return_value=(True, "")),
                patch.object(wechat.core, "local_asr_status", return_value=(True, "")),
                patch.object(wechat.core, "_write_voice_from_row", side_effect=decode),
                patch.object(
                    wechat.core,
                    "_create_local_asr_recognizer",
                    side_effect=OSError("private path or key"),
                ),
                self.assertRaisesRegex(RuntimeError, "读取失败") as error,
            ):
                wechat.voice("self", SESSION, (0, 1000), ("message/message_0.db", 7))
            self.assertFalse(directory.exists())
            self.assertNotIn("private", str(error.exception))


if __name__ == "__main__":
    unittest.main()
