"""TaskPlanner 단위 테스트."""

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.utility.task_planner import TaskPlanner


class TestTaskPlanner:
    def setup_method(self):
        self.planner = TaskPlanner()

    def test_basic_plan(self, tmp_path):
        js_file = tmp_path / "app.js"
        js_file.write_text("const x = 1;")

        result = self.planner.execute(files=[str(js_file)])
        assert result.success is True
        assert result.data["total_files"] == 1
        task = result.data["sub_tasks"][0]
        assert task["task_id"] == "task_1"
        assert task["language"] == "javascript"
        assert task["priority"] == 1

    def test_multiple_languages(self, tmp_path):
        js = tmp_path / "app.js"
        c = tmp_path / "main.c"
        sql = tmp_path / "query.sql"
        js.write_text("var x;")
        c.write_text("int main() {}")
        sql.write_text("SELECT 1;")

        result = self.planner.execute(files=[str(js), str(c), str(sql)])
        assert result.data["total_files"] == 3
        languages = {t["language"] for t in result.data["sub_tasks"]}
        assert languages == {"javascript", "c", "sql"}

    def test_topological_sort(self, tmp_path):
        main_c = tmp_path / "main.c"
        utils_h = tmp_path / "utils.h"
        main_c.write_text('#include "utils.h"\nint main() {}')
        utils_h.write_text("void helper();")

        edges = [{
            "source": str(main_c.resolve()),
            "target": str(utils_h.resolve()),
            "type": "include",
        }]

        result = self.planner.execute(
            files=[str(main_c), str(utils_h)],
            edges=edges,
        )

        tasks = result.data["sub_tasks"]
        files_order = [t["file"] for t in tasks]
        # utils.h should come before main.c (depended upon first)
        utils_idx = next(
            i for i, f in enumerate(files_order)
            if f.endswith("utils.h")
        )
        main_idx = next(
            i for i, f in enumerate(files_order)
            if f.endswith("main.c")
        )
        assert utils_idx < main_idx

    def test_metadata_collected(self, tmp_path):
        f = tmp_path / "test.c"
        f.write_text("int main() {\n    return 0;\n}\n")

        result = self.planner.execute(files=[str(f)])
        meta = result.data["sub_tasks"][0]["metadata"]
        assert meta["line_count"] == 3
        assert meta["file_size"] > 0
        assert "last_modified" in meta

    def test_estimated_time(self, tmp_path):
        js = tmp_path / "app.js"
        c = tmp_path / "main.c"
        js.write_text("var x;")
        c.write_text("int main() {}")

        result = self.planner.execute(files=[str(js), str(c)])
        # js=15s + c=20s = 35s
        assert result.data["estimated_time_seconds"] == 35

    def test_empty_files_raises(self):
        with pytest.raises(ToolExecutionError, match="files list is empty"):
            self.planner.execute(files=[])

    def test_unsupported_files_only_raises(self, tmp_path):
        f = tmp_path / "test.rb"
        f.write_text("puts 'hello'")

        with pytest.raises(ToolExecutionError, match="no supported files"):
            self.planner.execute(files=[str(f)])

    def test_unsupported_files_skipped(self, tmp_path):
        js = tmp_path / "app.js"
        rb = tmp_path / "test.rb"
        js.write_text("var x;")
        rb.write_text("puts 'hello'")

        result = self.planner.execute(files=[str(js), str(rb)])
        assert result.data["total_files"] == 1

    def test_dependencies_passed_through(self, tmp_path):
        f = tmp_path / "test.c"
        f.write_text("int main() {}")

        edges = [{"source": "a.c", "target": "b.c", "type": "include"}]
        result = self.planner.execute(
            files=[str(f)],
            edges=edges,
            has_circular=True,
            warnings=["순환 발견"],
        )
        deps = result.data["dependencies"]
        assert deps["has_circular"] is True
        assert deps["warnings"] == ["순환 발견"]
        assert deps["edges"] == edges

    def test_proc_file(self, tmp_path):
        f = tmp_path / "batch.pc"
        f.write_text("EXEC SQL SELECT 1 FROM DUAL;")

        result = self.planner.execute(files=[str(f)])
        assert result.data["sub_tasks"][0]["language"] == "proc"
