import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_sensitive_cache import load_core


class SessionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.core = load_core()

    def test_session_metadata_uses_names_and_cleans_sensitive_files(self):
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp) / "sensitive"
            workdir.mkdir()
            (workdir / "keys.json").write_text("synthetic secret")
            database = Mock(wxid="wxid_self")
            database._nickname_index.return_value = {
                "wxid_self": "测试账号",
                "group@chatroom": "测试群",
            }
            database.get_sessions.return_value = [
                {
                    "username": "group@chatroom",
                    "unread": 2,
                    "last_time": 0,
                    "summary": "测试摘要",
                },
                {"username": "gh_unknown", "unread": 0, "last_time": 0, "summary": ""},
            ]
            with (
                patch.object(
                    self.core, "_create_sensitive_workdir", return_value=workdir
                ),
                patch.object(self.core, "WeChatDB", return_value=database) as factory,
            ):
                result = self.core.list_chat_sessions()
                factory.assert_called_once_with(db_dir=None, workdir=str(workdir))
            self.assertEqual(result["account"], "测试账号")
            self.assertEqual(result["sessions"][0]["name"], "测试群")
            self.assertEqual(result["sessions"][0]["chat_type"], "群聊")
            self.assertEqual(result["sessions"][0]["unread"], 2)
            self.assertEqual(result["sessions"][1]["name"], "gh_unknown")
            self.assertEqual(result["sessions"][1]["chat_type"], "公众号")
            database.get_sessions.assert_called_once_with(limit=-1)
            self.assertFalse(workdir.exists())

    def test_session_read_failure_still_cleans_sensitive_files(self):
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp) / "sensitive"
            workdir.mkdir()
            (workdir / "keys.json").write_text("synthetic secret")
            with (
                patch.object(
                    self.core, "_create_sensitive_workdir", return_value=workdir
                ),
                patch.object(
                    self.core, "WeChatDB", side_effect=RuntimeError("read failed")
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "read failed"):
                    self.core.list_chat_sessions()
            self.assertFalse(workdir.exists())

    def test_exact_username_wins_over_duplicate_display_names(self):
        database = Mock()
        wrong = {"username": "wrong", "remark": "wxid_target"}
        target = {"username": "wxid_target", "remark": "同名联系人"}
        database.search_contact.return_value = [wrong, target]
        self.assertEqual(self.core.find_contact(database, "wxid_target"), target)

    def test_session_without_contact_wins_over_partial_contact_match(self):
        database = Mock()
        database.search_contact.return_value = [{"username": "wxid_target_extra"}]
        database.get_sessions.return_value = [{"username": "wxid_target"}]
        database._nickname_index.return_value = {}
        self.assertEqual(
            self.core.find_contact(database, "wxid_target")["username"], "wxid_target"
        )

    def test_missing_contact_session_wins_over_other_contacts_remark(self):
        database = Mock()
        database.search_contact.return_value = [
            {"username": "wrong", "remark": "wxid_target"},
        ]
        database.get_sessions.return_value = [{"username": "wxid_target"}]
        database._nickname_index.return_value = {}
        self.assertEqual(
            self.core.find_contact(database, "wxid_target")["username"], "wxid_target"
        )


if __name__ == "__main__":
    unittest.main()
