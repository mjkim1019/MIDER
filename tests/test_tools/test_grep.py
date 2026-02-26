"""Grep 단위 테스트."""

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.search.grep import Grep


class TestGrep:
    def setup_method(self):
        self.grep = Grep()

    def test_simple_match(self, tmp_path):
        f = tmp_path / "test.c"
        f.write_text("#include <stdio.h>\n#include <string.h>\nint main() {}\n")

        result = self.grep.execute(pattern=r"#include", file=str(f))
        assert result.success is True
        assert result.data["total_matches"] == 2
        assert result.data["matches"][0]["line"] == 1
        assert result.data["matches"][1]["line"] == 2

    def test_regex_pattern(self, tmp_path):
        f = tmp_path / "test.js"
        f.write_text("const x = 1;\nlet y = 2;\nvar z = 3;\n")

        result = self.grep.execute(pattern=r"(const|let)\s+\w+", file=str(f))
        assert result.data["total_matches"] == 2

    def test_no_match(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")

        result = self.grep.execute(pattern=r"foobar", file=str(f))
        assert result.success is True
        assert result.data["total_matches"] == 0
        assert result.data["matches"] == []

    def test_match_details(self, tmp_path):
        f = tmp_path / "test.c"
        f.write_text("strcpy(dest, src);\n")

        result = self.grep.execute(pattern=r"strcpy", file=str(f))
        match = result.data["matches"][0]
        assert match["line"] == 1
        assert match["match"] == "strcpy"
        assert match["start"] == 0
        assert match["end"] == 6

    def test_invalid_regex(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("test\n")

        with pytest.raises(ToolExecutionError, match="invalid regex"):
            self.grep.execute(pattern=r"[invalid", file=str(f))

    def test_file_not_found(self):
        with pytest.raises(ToolExecutionError, match="file not found"):
            self.grep.execute(pattern=r"test", file="/nonexistent.txt")

    def test_search_proc_patterns(self, tmp_path, sample_proc_content):
        f = tmp_path / "test.pc"
        f.write_text(sample_proc_content)

        result = self.grep.execute(pattern=r"EXEC\s+SQL", file=str(f))
        assert result.data["total_matches"] > 0
