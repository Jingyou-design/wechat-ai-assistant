import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_sender_mapping import FakeDB, load_export_modules


class ExportTimeRangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core, _ = load_export_modules()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = FakeDB(self.root)
        self.now = 1_800_000_000

    def export(self, time_range):
        with (
            patch.object(self.core, "WeChatDB", return_value=self.db),
            patch.object(self.core.time, "time", return_value=self.now),
        ):
            result = self.core.export_chat(
                "Target remark",
                out_root=self.root / "output",
                time_range=time_range,
            )
        data = json.loads(Path(result["json"]).read_text(encoding="utf-8-sig"))
        return result, data

    def test_all_six_ranges_select_messages_by_age(self):
        ages = [1800, 7200, 2 * 86400, 8 * 86400, 40 * 86400, 400 * 86400]
        self.db.add_shard(
            {2: "wxid_target"},
            [
                {"create_time": self.now - age, "sort_seq": index}
                for index, age in enumerate(ages)
            ],
        )
        for label, expected in (
            ("最近一小时", 1),
            ("最近一天", 2),
            ("最近一星期", 3),
            ("最近一个月", 4),
            ("最近一年", 5),
            ("所有", 6),
        ):
            with self.subTest(time_range=label):
                result, data = self.export(label)
                self.assertEqual(data["message_count"], expected)
                self.assertEqual(result["time_range"], label)
                self.assertTrue(result["sensitive_cache_cleanup"])
                if label == "所有":
                    self.assertIsNone(data["time_window"])
                else:
                    self.assertEqual(
                        data["time_window"]["end"], self.core.fmt_time(self.now)
                    )

    def test_inclusive_boundaries_and_milliseconds_across_shards(self):
        start = self.now - 3600
        self.db.add_shard(
            {2: "wxid_target"},
            [
                {"create_time": start - 1, "message_content": "old"},
                {"create_time": start, "message_content": "start"},
                {"create_time": self.now, "message_content": "end"},
                {"create_time": self.now + 1, "message_content": "future"},
            ],
        )
        self.db.add_shard(
            {2: "wxid_target"},
            [
                {"create_time": start * 1000 - 1, "message_content": "old-ms"},
                {"create_time": start * 1000, "message_content": "start-ms"},
                {"create_time": self.now * 1000 + 999, "message_content": "end-ms"},
                {"create_time": (self.now + 1) * 1000, "message_content": "future-ms"},
            ],
        )
        _, data = self.export("最近一小时")
        self.assertEqual(
            {message["content"] for message in data["messages"]},
            {"start", "end", "start-ms", "end-ms"},
        )
        self.assertTrue(any("create_time BETWEEN" in sql for _, sql in self.db.queries))

    def test_public_account_time_filter_and_complete_export_preserved(self):
        self.db.target["username"] = "gh_synthetic"
        self.db.nicks["gh_synthetic"] = "Synthetic public account"
        original = self.db.add_shard(
            {2: "gh_synthetic"},
            [
                {"create_time": self.now - 2 * 86400, "message_content": "old article"},
                {"create_time": self.now - 100, "message_content": "recent article"},
            ],
        )
        self.db.shards["message\\biz_message_0.db"] = self.db.shards.pop(original)
        complete, _ = self.export("所有")
        complete_json = Path(complete["json"]).read_bytes()
        filtered, data = self.export("最近一天")
        self.assertEqual(data["message_count"], 1)
        self.assertEqual(data["messages"][0]["content"], "recent article")
        self.assertEqual(filtered["chat_type"], "公众号")
        self.assertEqual(
            Path(filtered["output_dir"]).parent, Path(complete["output_dir"])
        )
        self.assertEqual(Path(complete["json"]).read_bytes(), complete_json)

    def test_empty_time_range_does_not_create_export_files(self):
        self.db.add_shard({2: "wxid_target"}, [{"create_time": self.now - 4000}])
        with self.assertRaisesRegex(ValueError, "所选时间范围内没有消息"):
            self.export("最近一小时")
        self.assertFalse((self.root / "output").exists())

    def test_invalid_range_does_not_open_database(self):
        with patch.object(self.core, "WeChatDB") as database:
            with self.assertRaisesRegex(ValueError, "不支持的时间范围"):
                self.core.export_chat("Synthetic", time_range="invalid")
        database.assert_not_called()


if __name__ == "__main__":
    unittest.main()
