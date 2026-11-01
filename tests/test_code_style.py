"""源码风格检查器的边界条件回归测试。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import check_code_style


class CodeStyleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def scan_source(self, source: str):
        source_file = self.root / "example.py"
        source_file.write_text(source, encoding="utf-8")
        with patch.object(check_code_style, "ROOT", self.root):
            return check_code_style.scan(source_file)

    def test_long_separator_is_rejected(self):
        problems, _ = self.scan_source('"""入口说明。"""\n# ============\n')
        self.assertTrue(any("长装饰分隔线" in item for item in problems))

    def test_normal_comment_is_accepted(self):
        problems, chinese = self.scan_source('"""入口说明。"""\n# 安全检查只采用可信回执。\n')
        self.assertEqual(problems, [])
        self.assertTrue(chinese)

    def test_long_header_comments_are_rejected(self):
        source = "\n".join("# 模块说明" for _ in range(13)) + "\nvalue = 1\n"
        problems, _ = self.scan_source(source)
        self.assertTrue(any("超过 12 行" in item for item in problems))

    def test_long_multiline_docstring_is_rejected(self):
        source = '"""\n' + ("说明\n" * 12) + '"""\nvalue = 1\n'
        problems, _ = self.scan_source(source)
        self.assertTrue(any("文件头说明过长" in item for item in problems))

    def test_short_docstring_is_accepted(self):
        problems, _ = self.scan_source('"""模块说明。"""\n\nvalue = 1\n')
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
