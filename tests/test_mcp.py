import asyncio
import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError

from app.api.mcp import server
from app.api.schema import DetailRequest, ReadRequest, SearchRequest, TaskRequest
from app.tools import wechat
from app.workflow.context import ContextStore

SESSION = {
    "session_id": "gh_test",
    "name": "测试公众号",
    "chat_type": "公众号",
    "unread": 0,
    "last_time": "",
}


def message(local_id, content="测试正文"):
    return {
        "local_id": local_id,
        "sort_seq": local_id,
        "_db_rel": "message/biz_message_0.db",
        "create_time": 1000,
        "sender": "测试公众号",
        "sender_status": "resolved",
        "content": content,
    }


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.catalog = patch.object(
            wechat, "sessions", return_value=("self", [SESSION])
        ).start()
        self.addCleanup(patch.stopall)
        patch.object(
            wechat, "messages", side_effect=AssertionError("Unmocked real DB access")
        ).start()
        self.store = ContextStore()
        self.task = self.store.start(TaskRequest(session_ids=["gh_test"]))

    def read(self, **kwargs):
        return self.store.read(
            ReadRequest(task_id=self.task.task_id, session_id="gh_test", **kwargs)
        )

    def test_metadata_does_not_contain_chat_body(self):
        result = self.store.search(SearchRequest(query="公众号"))
        self.assertEqual(result.sessions[0].session_id, "gh_test")
        self.assertNotIn("summary", result.model_dump_json())
        self.assertEqual(self.store.search(SearchRequest(query="不存在")).sessions, [])

    def test_scope_and_time_window_are_fixed(self):
        task = self.store.tasks[self.task.task_id]
        self.assertEqual(task.window[1] - task.window[0], 86400)
        with self.assertRaisesRegex(ValueError, "范围"):
            self.store.read(ReadRequest(task_id=self.task.task_id, session_id="other"))
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.store.start(TaskRequest(session_ids=["other"]))

    def test_page_chronology_and_cursor_reference(self):
        with patch.object(
            wechat, "messages", return_value=[message(3), message(2), message(1)]
        ) as fetch:
            page = self.read(limit=2)
            self.assertTrue(page.has_more)
            self.assertEqual([item.content for item in page.messages], ["测试正文"] * 2)
            with patch.object(wechat, "messages", return_value=[message(1)]):
                last = self.read(cursor=page.next_cursor)
            self.assertFalse(last.has_more)
            assert page.next_cursor is not None
            saved = self.store.tasks[self.task.task_id].cursors[page.next_cursor]
            self.assertEqual(saved[1], (2, "message/biz_message_0.db", 2))
            self.assertEqual(fetch.call_args.args[3], 3)

    def test_foreign_cursor_and_evidence_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "游标"):
            self.read(cursor="forged")
        with self.assertRaisesRegex(ValueError, "已读取"):
            self.store.detail(
                DetailRequest(task_id=self.task.task_id, message_id="forged")
            )

    def test_expired_task_is_removed(self):
        self.store.tasks[self.task.task_id].expires = 0
        with self.assertRaisesRegex(ValueError, "过期"):
            self.read()
        self.assertNotIn(self.task.task_id, self.store.tasks)

    def test_empty_pages_consume_read_budget(self):
        with patch.object(wechat, "messages", return_value=[]):
            for _ in range(6):
                self.read()
            with self.assertRaisesRegex(ValueError, "预算"):
                self.read()

    def test_escaped_long_body_is_bounded_and_expandable(self):
        body = "\x01" * 10000
        with patch.object(wechat, "messages", return_value=[message(1, body)]):
            page = self.read()
            self.assertEqual(len(page.messages), 1)
            self.assertTrue(page.messages[0].truncated)
            self.assertLessEqual(len(page.model_dump_json()), 6000)
            detail = self.store.detail(
                DetailRequest(
                    task_id=self.task.task_id,
                    message_id=page.messages[0].message_id,
                    offset=len(page.messages[0].content),
                )
            )
        self.assertLessEqual(len(detail.model_dump_json()), 4000)
        self.assertEqual(detail.content, body[: len(detail.content)])
        self.assertLessEqual(
            len(page.model_dump_json()) + len(detail.model_dump_json()),
            18000 - detail.remaining_chars,
        )
        self.assertEqual(
            detail.next_offset, len(page.messages[0].content) + len(detail.content)
        )

    def test_small_remaining_budget_does_not_return_unreadable_cursor(self):
        self.store.tasks[self.task.task_id].chars = 100
        with self.assertRaisesRegex(ValueError, "预算"):
            self.read()

    def test_input_limits(self):
        for kwargs in ({"limit": 31}, {"limit": 0}, {"limit": "bad"}):
            with self.assertRaises(ValidationError):
                ReadRequest.model_validate(
                    {"task_id": "task", "session_id": "id", **kwargs}
                )
        with self.assertRaises(ValidationError):
            TaskRequest(session_ids=["a"] * 4)

    def test_all_six_ranges_and_deduplication(self):
        for time_range, duration in wechat.core.EXPORT_TIME_RANGES.items():
            result = self.store.start(
                TaskRequest.model_validate(
                    {"session_ids": ["gh_test", "gh_test"], "time_range": time_range}
                )
            )
            task = self.store.tasks[result.task_id]
            self.assertEqual(len(task.sessions), 1)
            self.assertEqual(
                task.window[0], task.window[1] - duration if duration else 0
            )


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.table = "Msg_" + hashlib.md5(b"gh_test").hexdigest()
        self.shards = ["message/message_0.db", "message/biz_message_0.db"]
        for index, rel in enumerate(self.shards):
            path = self.root / rel
            path.parent.mkdir(exist_ok=True)
            with closing(sqlite3.connect(path)) as conn:
                conn.execute(
                    f"CREATE TABLE {self.table} (local_id INTEGER, sort_seq INTEGER, create_time INTEGER, real_sender_id INTEGER, local_type INTEGER, message_content TEXT)"
                )
                conn.execute("CREATE TABLE Name2Id (user_name TEXT)")
                conn.execute("INSERT INTO Name2Id VALUES ('gh_test')")
                # 相同排序序号、不同分片，时间同时覆盖秒和毫秒。
                conn.executemany(
                    f"INSERT INTO {self.table} VALUES (?, ?, ?, 1, 1, ?)",
                    [
                        (1, 10, 1000 if index == 0 else 1000000, "a"),
                        (2, 10, 1001 if index == 0 else 1001000, "b"),
                        (3, 9, 900, "older"),
                        (4, 11, 2000, "future"),
                    ],
                )
                conn.commit()
        self.db = Mock(wxid="self", _db_files=[(rel, "", 0) for rel in self.shards])
        self.db._message_dbs.return_value = [self.shards[0]]
        self.db._open.side_effect = lambda rel: sqlite3.connect(self.root / rel)

    def test_paging_has_no_gaps_at_equal_sort_values(self):
        seen = []
        before = None
        while True:
            rows = wechat.query_rows(self.db, "gh_test", (1000, 1001), 1, before)
            if not rows:
                break
            seen.append(wechat.row_key(rows[0]))
            before = seen[-1]
        self.assertEqual(len(seen), 4)
        self.assertEqual(len(set(seen)), 4)
        self.assertEqual(seen, sorted(seen, reverse=True))

    def test_reference_is_restricted_to_shard_and_time_window(self):
        rows = wechat.query_rows(
            self.db, "gh_test", (1000, 1001), 1, reference=(self.shards[1], 2)
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["_sender_username"], "gh_test")
        self.assertEqual(
            wechat.query_rows(
                self.db, "gh_test", (1000, 1001), 1, reference=(self.shards[1], 4)
            ),
            [],
        )

    def test_optional_voice_fields_are_read_when_present(self):
        with closing(sqlite3.connect(self.root / self.shards[1])) as conn:
            conn.execute(f"ALTER TABLE {self.table} ADD COLUMN server_id INTEGER")
            conn.execute(f"ALTER TABLE {self.table} ADD COLUMN source TEXT")
            conn.execute(
                f"UPDATE {self.table} SET server_id=42, source='synthetic transcript' WHERE local_id=2"
            )
            conn.commit()
        rows = wechat.query_rows(
            self.db, "gh_test", (1000, 1001), 1, reference=(self.shards[1], 2)
        )
        self.assertEqual(rows[0]["server_id"], 42)
        self.assertEqual(rows[0]["source"], "synthetic transcript")

    def test_sensitive_files_cleaned_on_success_and_error(self):
        for fail in (False, True):
            workdir = self.root / str(fail)
            workdir.mkdir()
            with (
                patch.object(
                    wechat.core, "_create_sensitive_workdir", return_value=workdir
                ),
                patch.object(wechat.core, "WeChatDB", return_value=self.db),
            ):
                if fail:
                    with (
                        self.assertRaisesRegex(RuntimeError, "读取失败"),
                        wechat.database(),
                    ):
                        (workdir / "secret").write_text("synthetic")
                        raise OSError("synthetic secret must not escape")
                else:
                    with wechat.database():
                        (workdir / "secret").write_text("synthetic")
            self.assertFalse(workdir.exists())

    def test_cleanup_failure_is_reported(self):
        workdir = self.root / "cleanup_failure"
        workdir.mkdir()
        with (
            patch.object(
                wechat.core, "_create_sensitive_workdir", return_value=workdir
            ),
            patch.object(wechat.core, "WeChatDB", return_value=self.db),
            patch.object(
                wechat.core,
                "_cleanup_sensitive_workdir",
                return_value=(False, "secret-path"),
            ),
        ):
            with (
                self.assertRaisesRegex(RuntimeError, "清理失败") as caught,
                wechat.database(),
            ):
                pass
            self.assertNotIn("secret-path", str(caught.exception))

    def test_account_switch_is_rejected(self):
        @contextmanager
        def fake_database():
            yield self.db

        with (
            patch.object(wechat, "database", fake_database),
            self.assertRaisesRegex(ValueError, "账号已改变"),
        ):
            wechat.messages("different", SESSION, (1000, 1001), 1)


class ProtocolTests(unittest.TestCase):
    def test_sdk_protocol_schema_readonly_and_validation(self):
        from mcp import Client

        async def check():
            async with Client(server) as client:
                result = await client.list_tools()
                self.assertEqual(
                    {tool.name for tool in result.tools},
                    {
                        "search_sessions",
                        "start_task",
                        "read_messages",
                        "expand_message",
                        "read_image",
                        "transcribe_voice",
                    },
                )
                for tool in result.tools:
                    assert tool.annotations is not None
                    self.assertTrue(tool.annotations.read_only_hint)
                    self.assertFalse(tool.annotations.destructive_hint)
                with patch.object(wechat, "sessions", return_value=("self", [SESSION])):
                    result = await client.call_tool(
                        "search_sessions", {"request": {"query": "测试"}}
                    )
                self.assertFalse(result.is_error)
                self.assertEqual(len(result.content), 1)
                self.assertIsNone(result.structured_content)
                result = await client.call_tool(
                    "read_messages",
                    {"request": {"task_id": "invalid", "session_id": "gh_test"}},
                )
                self.assertTrue(result.is_error)
                self.assertIn("过期", str(result.content))
                result = await client.call_tool(
                    "read_messages",
                    {
                        "request": {
                            "task_id": "invalid",
                            "session_id": "gh_test",
                            "limit": 10000,
                        }
                    },
                )
                self.assertTrue(result.is_error)

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
