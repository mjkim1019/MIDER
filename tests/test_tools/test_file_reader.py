"""FileReader 단위 테스트."""

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.file_io.file_reader import FileReader


class TestFileReader:
    def setup_method(self):
        self.reader = FileReader()

    def test_read_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")

        result = self.reader.execute(path=str(f))
        assert result.success is True
        assert result.data["line_count"] == 3
        assert "line1" in result.data["content"]
        assert result.data["encoding"] == "utf-8"

    def test_read_file_no_trailing_newline(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2", encoding="utf-8")

        result = self.reader.execute(path=str(f))
        assert result.data["line_count"] == 2

    def test_read_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")

        result = self.reader.execute(path=str(f))
        assert result.success is True
        assert result.data["content"] == ""
        assert result.data["line_count"] == 0

    def test_file_not_found(self):
        with pytest.raises(ToolExecutionError, match="file not found"):
            self.reader.execute(path="/nonexistent/file.txt")

    def test_not_a_file(self, tmp_path):
        with pytest.raises(ToolExecutionError, match="not a file"):
            self.reader.execute(path=str(tmp_path))

    def test_file_size(self, tmp_path):
        f = tmp_path / "test.txt"
        content = "hello world"
        f.write_text(content, encoding="utf-8")

        result = self.reader.execute(path=str(f))
        assert result.data["file_size"] == len(content.encode("utf-8"))

    def test_read_js_file(self, tmp_path, sample_js_content):
        f = tmp_path / "test.js"
        f.write_text(sample_js_content, encoding="utf-8")

        result = self.reader.execute(path=str(f))
        assert result.success is True
        assert "function processOrder" in result.data["content"]

    def test_read_c_file(self, tmp_path, sample_c_content):
        f = tmp_path / "test.c"
        f.write_text(sample_c_content, encoding="utf-8")

        result = self.reader.execute(path=str(f))
        assert result.success is True
        assert "#include <stdio.h>" in result.data["content"]

    def test_read_cp949_file(self, tmp_path):
        f = tmp_path / "legacy.pc"
        content = "한글 주석\nEXEC SQL SELECT 1;\n확장문자 ①㈜"
        f.write_text(content, encoding="cp949")

        result = self.reader.execute(path=str(f))

        assert result.success is True
        assert result.data["content"] == content
        assert result.data["encoding"] == "cp949"

    def test_read_utf8_bom_file(self, tmp_path):
        f = tmp_path / "bom.pc"
        content = "EXEC SQL INCLUDE sqlca;\n"
        f.write_text(content, encoding="utf-8-sig")

        result = self.reader.execute(path=str(f))

        assert result.success is True
        assert result.data["content"] == content
        assert result.data["encoding"] == "utf-8-sig"

    def test_read_malformed_legacy_file_with_replace(self, tmp_path):
        f = tmp_path / "broken.pc"
        raw = b"EXEC SQL SELECT 1;\\ncomment '" + bytes([0x87, 0x3F]) + " test".encode("ascii")
        f.write_bytes(raw)

        result = self.reader.execute(path=str(f))

        assert result.success is True
        assert "EXEC SQL SELECT 1;" in result.data["content"]
        assert "\ufffd" in result.data["content"]
        assert result.data["encoding"] == "cp949-replace"
