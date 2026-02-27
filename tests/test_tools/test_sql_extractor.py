"""SQLExtractor 단위 테스트."""

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.utility.sql_extractor import SQLExtractor


class TestSQLExtractor:
    def setup_method(self):
        self.extractor = SQLExtractor()

    def test_basic_extraction(self, tmp_path, sample_proc_content):
        f = tmp_path / "test.pc"
        f.write_text(sample_proc_content)

        result = self.extractor.execute(file=str(f))
        assert result.success is True
        assert result.data["total_blocks"] > 0

    def test_host_variables(self, tmp_path):
        content = """
EXEC SQL UPDATE ORDERS
    SET STATUS = :h_status
    WHERE ORDER_ID = :h_order_id;
"""
        f = tmp_path / "test.pc"
        f.write_text(content)

        result = self.extractor.execute(file=str(f))
        block = result.data["sql_blocks"][0]
        assert "h_status" in block["host_variables"]
        assert "h_order_id" in block["host_variables"]

    def test_indicator_variables(self, tmp_path):
        content = """
EXEC SQL SELECT NAME INTO :h_name:ind_name
    FROM CUSTOMERS WHERE ID = :h_id;
"""
        f = tmp_path / "test.pc"
        f.write_text(content)

        result = self.extractor.execute(file=str(f))
        block = result.data["sql_blocks"][0]
        assert "ind_name" in block["indicator_variables"]
        assert "h_name" in block["host_variables"]

    def test_sqlca_check_detected(self, tmp_path):
        content = """
EXEC SQL UPDATE ORDERS SET STATUS = :h_status;
if (sqlca.sqlcode != 0) {
    printf("error");
}
"""
        f = tmp_path / "test.pc"
        f.write_text(content)

        result = self.extractor.execute(file=str(f))
        block = result.data["sql_blocks"][0]
        assert block["has_sqlca_check"] is True

    def test_sqlca_check_missing(self, tmp_path):
        content = """
EXEC SQL UPDATE ORDERS SET STATUS = :h_status;
printf("done");
"""
        f = tmp_path / "test.pc"
        f.write_text(content)

        result = self.extractor.execute(file=str(f))
        block = result.data["sql_blocks"][0]
        assert block["has_sqlca_check"] is False

    def test_skips_declare_section(self, tmp_path, sample_proc_content):
        f = tmp_path / "test.pc"
        f.write_text(sample_proc_content)

        result = self.extractor.execute(file=str(f))
        for block in result.data["sql_blocks"]:
            assert "DECLARE" not in block["sql"].upper().split()[0] if block["sql"] else True

    def test_skips_include(self, tmp_path, sample_proc_content):
        f = tmp_path / "test.pc"
        f.write_text(sample_proc_content)

        result = self.extractor.execute(file=str(f))
        for block in result.data["sql_blocks"]:
            first_word = block["sql"].split()[0].upper() if block["sql"] else ""
            assert first_word != "INCLUDE"

    def test_multiple_blocks(self, tmp_path):
        content = """
EXEC SQL SELECT COUNT(*) INTO :h_count FROM ORDERS;
EXEC SQL UPDATE ORDERS SET STATUS = :h_status WHERE ID = :h_id;
EXEC SQL DELETE FROM TEMP_ORDERS WHERE CREATED < :h_date;
"""
        f = tmp_path / "test.pc"
        f.write_text(content)

        result = self.extractor.execute(file=str(f))
        assert result.data["total_blocks"] == 3
        assert result.data["sql_blocks"][0]["id"] == 0
        assert result.data["sql_blocks"][1]["id"] == 1
        assert result.data["sql_blocks"][2]["id"] == 2

    def test_line_numbers(self, tmp_path):
        content = """line1
line2
EXEC SQL SELECT 1 FROM DUAL;
line4
"""
        f = tmp_path / "test.pc"
        f.write_text(content)

        result = self.extractor.execute(file=str(f))
        block = result.data["sql_blocks"][0]
        assert block["line"] == 3

    def test_file_not_found(self):
        with pytest.raises(ToolExecutionError, match="file not found"):
            self.extractor.execute(file="/nonexistent.pc")

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.pc"
        f.write_text("")

        result = self.extractor.execute(file=str(f))
        assert result.success is True
        assert result.data["total_blocks"] == 0
        assert result.data["sql_blocks"] == []
