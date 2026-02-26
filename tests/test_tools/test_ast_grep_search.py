"""AstGrepSearch 단위 테스트."""

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.search.ast_grep_search import AstGrepSearch


class TestAstGrepSearch:
    def setup_method(self):
        self.search = AstGrepSearch()

    def test_js_import(self, tmp_path, sample_js_content):
        f = tmp_path / "test.js"
        f.write_text(sample_js_content)

        result = self.search.execute(
            pattern="dom_manipulation", file=str(f), language="javascript"
        )
        assert result.success is True
        assert result.data["total_matches"] > 0
        assert result.data["pattern_name"] == "dom_manipulation"

    def test_c_include(self, tmp_path, sample_c_content):
        f = tmp_path / "test.c"
        f.write_text(sample_c_content)

        result = self.search.execute(
            pattern="include", file=str(f), language="c"
        )
        assert result.data["total_matches"] == 3

    def test_c_strcpy(self, tmp_path, sample_c_content):
        f = tmp_path / "test.c"
        f.write_text(sample_c_content)

        result = self.search.execute(
            pattern="strcpy", file=str(f), language="c"
        )
        assert result.data["total_matches"] == 1
        assert result.data["matches"][0]["match"] == "strcpy("

    def test_c_malloc(self, tmp_path, sample_c_content):
        f = tmp_path / "test.c"
        f.write_text(sample_c_content)

        result = self.search.execute(
            pattern="malloc", file=str(f), language="c"
        )
        assert result.data["total_matches"] == 1

    def test_proc_exec_sql(self, tmp_path, sample_proc_content):
        f = tmp_path / "test.pc"
        f.write_text(sample_proc_content)

        result = self.search.execute(
            pattern="exec_sql", file=str(f), language="proc"
        )
        assert result.data["total_matches"] > 0

    def test_proc_sqlca_check(self, tmp_path, sample_proc_content):
        f = tmp_path / "test.pc"
        f.write_text(sample_proc_content)

        result = self.search.execute(
            pattern="sqlca_check", file=str(f), language="proc"
        )
        assert result.data["total_matches"] == 1

    def test_sql_select_star(self, tmp_path, sample_sql_content):
        f = tmp_path / "test.sql"
        f.write_text(sample_sql_content)

        result = self.search.execute(
            pattern="select_star", file=str(f), language="sql"
        )
        assert result.data["total_matches"] == 1

    def test_sql_function_in_where(self, tmp_path, sample_sql_content):
        f = tmp_path / "test.sql"
        f.write_text(sample_sql_content)

        result = self.search.execute(
            pattern="function_in_where", file=str(f), language="sql"
        )
        assert result.data["total_matches"] > 0

    def test_custom_regex(self, tmp_path):
        f = tmp_path / "test.js"
        f.write_text("console.log('hello');\nconsole.error('world');\n")

        result = self.search.execute(
            pattern=r"console\.\w+",
            file=str(f),
            language="javascript",
        )
        assert result.data["total_matches"] == 2
        assert result.data["pattern_name"] == "custom"

    def test_unsupported_language(self, tmp_path):
        f = tmp_path / "test.rb"
        f.write_text("puts 'hello'")

        with pytest.raises(ToolExecutionError, match="unsupported language"):
            self.search.execute(
                pattern="test", file=str(f), language="ruby"
            )

    def test_invalid_pattern(self, tmp_path):
        f = tmp_path / "test.js"
        f.write_text("var x;")

        with pytest.raises(ToolExecutionError, match="invalid pattern"):
            self.search.execute(
                pattern=r"[invalid",
                file=str(f),
                language="javascript",
            )

    def test_file_not_found(self):
        with pytest.raises(ToolExecutionError, match="file not found"):
            self.search.execute(
                pattern="include",
                file="/nonexistent.c",
                language="c",
            )
