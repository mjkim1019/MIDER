"""ProCClangTidyRunner 단위 테스트."""

from unittest.mock import MagicMock, patch

import pytest

from mider.tools.static_analysis.proc_clang_tidy_runner import (
    ProCClangTidyRunner,
    _guess_category,
    _guess_severity,
)


class TestStripExecSql:
    """EXEC SQL 블록 제거 테스트."""

    def test_simple_exec_sql(self):
        """단일 줄 EXEC SQL이 빈 줄로 치환된다."""
        content = (
            '#include <stdio.h>\n'
            'EXEC SQL INCLUDE sqlca;\n'
            'int main() {\n'
            '    EXEC SQL SELECT 1;\n'
            '    return 0;\n'
            '}\n'
        )
        result = ProCClangTidyRunner._strip_exec_sql(content)
        lines = result.splitlines()

        # EXEC SQL 줄은 빈 줄(또는 공백만 남은 줄)로 치환
        assert lines[1].strip() == ""  # EXEC SQL INCLUDE sqlca;
        assert lines[3].strip() == ""  # EXEC SQL SELECT 1;
        # 일반 C 코드는 보존
        assert "int main()" in lines[2]
        assert "return 0;" in lines[4]

    def test_multiline_exec_sql(self):
        """여러 줄에 걸친 EXEC SQL이 동일 줄 수의 빈 줄로 치환된다."""
        content = (
            'int x = 1;\n'
            'EXEC SQL SELECT col1,\n'
            '    col2,\n'
            '    col3\n'
            '    INTO :var1, :var2, :var3\n'
            '    FROM table1;\n'
            'int y = 2;\n'
        )
        result = ProCClangTidyRunner._strip_exec_sql(content)
        lines = result.splitlines()

        # 원본과 동일한 줄 수
        assert len(lines) == len(content.splitlines())
        # 첫 줄과 마지막 줄은 보존
        assert "int x = 1;" in lines[0]
        assert "int y = 2;" in lines[6]

    def test_sqlca_include_replaced(self):
        """#include <sqlca.h>가 주석으로 치환된다."""
        content = '#include <sqlca.h>\nint x;\n'
        result = ProCClangTidyRunner._strip_exec_sql(content)
        assert "sqlca.h removed" in result
        assert '#include <sqlca.h>' not in result

    def test_line_count_preserved(self):
        """EXEC SQL 제거 후에도 전체 줄 수가 보존된다."""
        content = (
            'line1\n'
            'EXEC SQL BEGIN DECLARE SECTION;\n'
            'int id;\n'
            'EXEC SQL END DECLARE SECTION;\n'
            'EXEC SQL SELECT 1;\n'
            'line6\n'
        )
        original_count = content.count('\n')
        result = ProCClangTidyRunner._strip_exec_sql(content)
        assert result.count('\n') == original_count


class TestConvertWarnings:
    """clang-tidy 경고 → Finding 변환 테스트."""

    def test_basic_conversion(self):
        """기본 경고가 Finding으로 변환된다."""
        warnings = [
            {
                "message": "variable 'x' is not initialized",
                "check": "cppcoreguidelines-init-variables",
                "line": 42,
                "column": 5,
                "severity": "warning",
            }
        ]
        findings = ProCClangTidyRunner._convert_warnings(warnings, "/app/test.pc")

        assert len(findings) == 1
        f = findings[0]
        assert f.finding_id == "CT-001"
        assert f.source_layer == "static"
        assert f.tool == "clang_tidy"
        assert f.rule_id == "cppcoreguidelines-init-variables"
        assert f.severity == "high"
        assert f.origin_line_start == 42

    def test_header_error_filtered(self):
        """'file not found' 에러는 제외된다."""
        warnings = [
            {
                "message": "'custom.h' file not found",
                "check": "",
                "line": 1,
                "column": 10,
                "severity": "error",
            },
            {
                "message": "variable 'y' is not initialized",
                "check": "cppcoreguidelines-init-variables",
                "line": 10,
                "column": 5,
                "severity": "warning",
            },
        ]
        findings = ProCClangTidyRunner._convert_warnings(warnings, "/app/test.pc")
        assert len(findings) == 1
        assert findings[0].origin_line_start == 10

    def test_note_filtered(self):
        """note 레벨은 제외된다."""
        warnings = [
            {
                "message": "some note",
                "check": "bugprone-something",
                "line": 5,
                "column": 1,
                "severity": "note",
            }
        ]
        findings = ProCClangTidyRunner._convert_warnings(warnings, "/app/test.pc")
        assert len(findings) == 0

    def test_multiple_warnings_numbered(self):
        """여러 경고의 finding_id가 순차 번호를 받는다."""
        warnings = [
            {"message": "warn1", "check": "bugprone-a", "line": 1, "column": 1, "severity": "warning"},
            {"message": "warn2", "check": "bugprone-b", "line": 2, "column": 1, "severity": "warning"},
            {"message": "warn3", "check": "bugprone-c", "line": 3, "column": 1, "severity": "warning"},
        ]
        findings = ProCClangTidyRunner._convert_warnings(warnings, "/app/test.pc")
        assert [f.finding_id for f in findings] == ["CT-001", "CT-002", "CT-003"]


class TestGuessSeverity:
    """severity 매핑 테스트."""

    def test_security_critical(self):
        assert _guess_severity("clang-analyzer-security.insecureAPI") == "critical"

    def test_core_high(self):
        assert _guess_severity("clang-analyzer-core.NullDereference") == "high"

    def test_bugprone_uninit_high(self):
        assert _guess_severity("bugprone-uninitialized") == "high"

    def test_init_variables_high(self):
        assert _guess_severity("cppcoreguidelines-init-variables") == "high"

    def test_unknown_medium(self):
        assert _guess_severity("unknown-check") == "medium"


class TestGuessCategory:
    """category 매핑 테스트."""

    def test_security(self):
        assert _guess_category("clang-analyzer-security.foo") == "security"

    def test_core_data_integrity(self):
        assert _guess_category("clang-analyzer-core.NullDereference") == "data_integrity"

    def test_bugprone_data_integrity(self):
        assert _guess_category("bugprone-sizeof-expression") == "data_integrity"

    def test_unknown_code_quality(self):
        assert _guess_category("some-unknown-check") == "code_quality"


class TestAnalyzeIntegration:
    """analyze() 통합 테스트 (clang-tidy 바이너리 모킹)."""

    def test_file_not_found(self):
        """존재하지 않는 파일 → 빈 리스트."""
        runner = ProCClangTidyRunner()
        result = runner.analyze(file="/nonexistent/file.pc")
        assert result == []

    def test_clang_tidy_skipped(self, tmp_path):
        """clang-tidy 바이너리 없으면 빈 리스트."""
        pc_file = tmp_path / "test.pc"
        pc_file.write_text(
            '#include <stdio.h>\nint main() { return 0; }\n',
            encoding="utf-8",
        )

        runner = ProCClangTidyRunner()
        # ClangTidyRunner.execute가 skipped를 반환하도록 모킹
        mock_result = MagicMock()
        mock_result.data = {"skipped": True}
        runner._clang_tidy = MagicMock()
        runner._clang_tidy.execute.return_value = mock_result

        result = runner.analyze(file=str(pc_file))
        assert result == []

    def test_warnings_converted(self, tmp_path):
        """clang-tidy 경고가 Finding으로 변환된다."""
        pc_file = tmp_path / "test.pc"
        pc_file.write_text(
            '#include <stdio.h>\n'
            'EXEC SQL INCLUDE sqlca;\n'
            'int main() {\n'
            '    long a;\n'
            '    return 0;\n'
            '}\n',
            encoding="utf-8",
        )

        runner = ProCClangTidyRunner()

        # ClangTidyRunner.execute 모킹
        mock_result = MagicMock()
        mock_result.data = {
            "skipped": False,
            "warnings": [
                {
                    "message": "variable 'a' is not initialized",
                    "check": "cppcoreguidelines-init-variables",
                    "line": 4,
                    "column": 10,
                    "severity": "warning",
                },
            ],
        }
        runner._clang_tidy = MagicMock()
        runner._clang_tidy.execute.return_value = mock_result
        runner._stub_gen = MagicMock()

        result = runner.analyze(file=str(pc_file), source_file=str(pc_file))

        assert len(result) == 1
        assert result[0].finding_id == "CT-001"
        assert result[0].severity == "high"
        assert result[0].origin_line_start == 4

    def test_source_file_override(self, tmp_path):
        """source_file 파라미터가 Finding에 반영되지 않더라도 에러 없이 동작."""
        pc_file = tmp_path / "test.pc"
        pc_file.write_text('int x;\n', encoding="utf-8")

        runner = ProCClangTidyRunner()
        mock_result = MagicMock()
        mock_result.data = {"skipped": False, "warnings": []}
        runner._clang_tidy = MagicMock()
        runner._clang_tidy.execute.return_value = mock_result
        runner._stub_gen = MagicMock()

        result = runner.analyze(file=str(pc_file), source_file="/original/path.pc")
        assert result == []
