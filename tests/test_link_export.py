import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_sender_mapping import FakeDB, load_export_modules


ARTICLE_URL = "https://mp.weixin.qq.com/s?__biz=synthetic&mid=123&idx=1&sn=abc"
ARTICLE_XML = (
    "<msg><appmsg><type>5</type><title>文章标题</title><des>文章摘要</des>"
    f"<url><![CDATA[{ARTICLE_URL}]]></url></appmsg></msg>"
)


class LinkExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core, _ = load_export_modules()

    def test_real_url_and_query_parameters_survive_parsing(self):
        content = self.core.parse_content(49, ARTICLE_XML)
        self.assertIn("文章标题｜文章摘要", content)
        self.assertEqual(content.splitlines()[-1], ARTICLE_URL)

    def test_url_without_title_is_retained(self):
        xml = f"<appmsg><type>5</type><url><![CDATA[{ARTICLE_URL}]]></url></appmsg>"
        self.assertEqual(self.core.parse_app_message(xml), f"[链接]\n{ARTICLE_URL}")

    def test_file_card_does_not_gain_a_download_url(self):
        xml = f"<appmsg><type>6</type><title>文档.pdf</title><url>{ARTICLE_URL.replace('&', '&amp;')}</url></appmsg>"
        self.assertEqual(self.core.parse_app_message(xml), "[文件] 文档.pdf")

    def test_all_export_formats_preserve_url_and_markdown_is_clickable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = FakeDB(root, username="gh_synthetic")
            database.nicks["gh_synthetic"] = "测试公众号"
            database.add_shard(
                {2: "gh_synthetic"},
                [
                    {"local_type": 49, "message_content": ARTICLE_XML},
                ],
            )
            with patch.object(self.core, "WeChatDB", return_value=database):
                result = self.core.export_chat(
                    "Target remark", out_root=root / "output"
                )
            txt = Path(result["txt"]).read_text(encoding="utf-8-sig")
            markdown = Path(result["md"]).read_text(encoding="utf-8")
            data = json.loads(Path(result["json"]).read_text(encoding="utf-8-sig"))
            self.assertIn(ARTICLE_URL, txt)
            self.assertIn(f"<{ARTICLE_URL}>", markdown)
            self.assertIn(ARTICLE_URL, data["messages"][0]["content"])
            self.assertNotIn("&amp;", data["messages"][0]["content"])
            self.assertTrue(result["sensitive_cache_cleanup"])


if __name__ == "__main__":
    unittest.main()
