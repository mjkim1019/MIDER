"""DependencyResolver 단위 테스트."""

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.utility.dependency_resolver import DependencyResolver


class TestDependencyResolver:
    def setup_method(self):
        self.resolver = DependencyResolver()

    def test_c_include_dependency(self, tmp_path):
        main_c = tmp_path / "main.c"
        utils_h = tmp_path / "utils.h"
        main_c.write_text('#include "utils.h"\nint main() {}')
        utils_h.write_text("void helper();")

        result = self.resolver.execute(
            files=[str(main_c), str(utils_h)]
        )
        assert result.success is True
        assert len(result.data["edges"]) == 1
        edge = result.data["edges"][0]
        assert edge["source"] == str(main_c.resolve())
        assert edge["target"] == str(utils_h.resolve())
        assert edge["type"] == "include"

    def test_js_import_dependency(self, tmp_path):
        app_js = tmp_path / "app.js"
        utils_js = tmp_path / "utils.js"
        app_js.write_text("import { helper } from './utils';")
        utils_js.write_text("export function helper() {}")

        result = self.resolver.execute(
            files=[str(app_js), str(utils_js)]
        )
        assert len(result.data["edges"]) == 1
        assert result.data["edges"][0]["type"] == "import"

    def test_proc_include_dependency(self, tmp_path):
        proc_file = tmp_path / "batch.pc"
        header = tmp_path / "common.h"
        proc_file.write_text(
            '#include "common.h"\nEXEC SQL INCLUDE SQLCA;\n'
            "EXEC SQL SELECT 1 FROM DUAL;"
        )
        header.write_text("void common_func();")

        result = self.resolver.execute(
            files=[str(proc_file), str(header)]
        )
        assert len(result.data["edges"]) == 1
        assert result.data["edges"][0]["type"] == "include"

    def test_no_dependencies(self, tmp_path):
        a = tmp_path / "a.sql"
        b = tmp_path / "b.sql"
        a.write_text("SELECT 1;")
        b.write_text("SELECT 2;")

        result = self.resolver.execute(files=[str(a), str(b)])
        assert result.data["edges"] == []
        assert result.data["has_circular"] is False

    def test_circular_dependency(self, tmp_path):
        a_c = tmp_path / "a.c"
        b_c = tmp_path / "b.c"
        a_c.write_text('#include "b.c"\n')
        b_c.write_text('#include "a.c"\n')

        result = self.resolver.execute(files=[str(a_c), str(b_c)])
        assert result.data["has_circular"] is True
        assert len(result.data["warnings"]) > 0

    def test_empty_files_raises(self):
        with pytest.raises(ToolExecutionError, match="files list is empty"):
            self.resolver.execute(files=[])

    def test_unresolved_import(self, tmp_path):
        f = tmp_path / "app.js"
        f.write_text("import axios from 'axios';")

        result = self.resolver.execute(files=[str(f)])
        assert result.data["edges"] == []

    def test_sql_files_skipped(self, tmp_path):
        f = tmp_path / "query.sql"
        f.write_text("SELECT * FROM ORDERS;")

        result = self.resolver.execute(files=[str(f)])
        assert result.data["edges"] == []

    def test_multiple_dependencies(self, tmp_path):
        main_c = tmp_path / "main.c"
        utils_h = tmp_path / "utils.h"
        config_h = tmp_path / "config.h"
        main_c.write_text('#include "utils.h"\n#include "config.h"\n')
        utils_h.write_text("void util();")
        config_h.write_text("#define MAX 100")

        result = self.resolver.execute(
            files=[str(main_c), str(utils_h), str(config_h)]
        )
        assert len(result.data["edges"]) == 2
