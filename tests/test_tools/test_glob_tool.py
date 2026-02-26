"""GlobTool 단위 테스트."""

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.search.glob_tool import GlobTool


class TestGlobTool:
    def setup_method(self):
        self.glob = GlobTool()

    def test_find_files(self, tmp_path):
        (tmp_path / "a.js").write_text("var x;")
        (tmp_path / "b.js").write_text("var y;")
        (tmp_path / "c.py").write_text("x = 1")

        result = self.glob.execute(pattern="*.js", root=str(tmp_path))
        assert result.success is True
        assert result.data["total_files"] == 2

    def test_recursive_glob(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "a.c").write_text("int main() {}")
        (sub / "b.c").write_text("void foo() {}")
        (tmp_path / "c.c").write_text("void bar() {}")

        result = self.glob.execute(pattern="**/*.c", root=str(tmp_path))
        assert result.data["total_files"] == 3

    def test_no_match(self, tmp_path):
        (tmp_path / "test.txt").write_text("hello")

        result = self.glob.execute(pattern="*.sql", root=str(tmp_path))
        assert result.success is True
        assert result.data["total_files"] == 0
        assert result.data["matched_files"] == []

    def test_directory_not_found(self):
        with pytest.raises(ToolExecutionError, match="directory not found"):
            self.glob.execute(pattern="*.js", root="/nonexistent/dir")

    def test_not_a_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")

        with pytest.raises(ToolExecutionError, match="not a directory"):
            self.glob.execute(pattern="*.js", root=str(f))

    def test_default_root(self):
        result = self.glob.execute(pattern="*.py", root=".")
        assert result.success is True

    def test_only_files_returned(self, tmp_path):
        sub = tmp_path / "subdir.js"
        sub.mkdir()
        (tmp_path / "real.js").write_text("code")

        result = self.glob.execute(pattern="*.js", root=str(tmp_path))
        assert result.data["total_files"] == 1
