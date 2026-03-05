"""ProcRunner 단위 테스트."""

from unittest.mock import MagicMock, patch

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.static_analysis.proc_runner import ProcRunner


@pytest.fixture
def mock_binary(tmp_path):
    """mock proc 바이너리."""
    binary = tmp_path / "proc"
    binary.write_text("#!/bin/sh\necho mock")
    binary.chmod(0o755)
    return binary


@pytest.fixture
def runner(mock_binary):
    """ProcRunner with mock binary."""
    return ProcRunner(binary_path=str(mock_binary))


# 샘플 proc 출력
_SAMPLE_PROC_ERRORS = """
PCC-S-02201, Encountered the symbol "ORDER" when expecting one of the following:
Semantic error at line 89, column 15
PCC-W-02345, Warning: host variable not declared
"""

_SAMPLE_PROC_SUCCESS = """
Pro*C/C++: Release 21.0.0.0.0 - Production
"""


class TestProcRunner:
    def test_file_not_found(self, runner):
        with pytest.raises(ToolExecutionError, match="file not found"):
            runner.execute(file="/nonexistent.pc")

    def test_binary_not_found_skips_gracefully(self, tmp_path):
        """바이너리 없으면 skipped=True로 빈 결과 반환."""
        runner = ProcRunner(binary_path="/nonexistent/proc")
        f = tmp_path / "test.pc"
        f.write_text("EXEC SQL SELECT 1;")
        result = runner.execute(file=str(f))
        assert result.success is True
        assert result.data["skipped"] is True
        assert result.data["errors"] == []

    @patch("mider.tools.static_analysis.proc_runner.subprocess.run")
    def test_parse_errors(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.pc"
        f.write_text("EXEC SQL SELECT 1;")

        mock_run.return_value = MagicMock(
            stdout=_SAMPLE_PROC_ERRORS,
            stderr="",
            returncode=1,
        )

        result = runner.execute(file=str(f))
        assert result.success is True  # ToolResult.success는 항상 True (에러는 data에)
        assert result.data["success"] is False
        assert result.data["total_errors"] > 0

    @patch("mider.tools.static_analysis.proc_runner.subprocess.run")
    def test_semantic_error(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.pc"
        f.write_text("EXEC SQL SELECT 1;")

        mock_run.return_value = MagicMock(
            stdout="Semantic error at line 42, column 10\nPCC-S-02201, some error\n",
            stderr="",
            returncode=1,
        )

        result = runner.execute(file=str(f))
        errors = result.data["errors"]
        # Semantic error 매칭
        sem_errors = [e for e in errors if e["line"] == 42]
        assert len(sem_errors) >= 1
        assert sem_errors[0]["column"] == 10

    @patch("mider.tools.static_analysis.proc_runner.subprocess.run")
    def test_success_no_errors(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.pc"
        f.write_text("EXEC SQL SELECT 1 FROM DUAL;")

        mock_run.return_value = MagicMock(
            stdout=_SAMPLE_PROC_SUCCESS,
            stderr="",
            returncode=0,
        )

        result = runner.execute(file=str(f))
        assert result.data["success"] is True
        assert result.data["total_errors"] == 0
        assert result.data["errors"] == []

    @patch("mider.tools.static_analysis.proc_runner.subprocess.run")
    def test_include_dirs(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.pc"
        f.write_text("EXEC SQL INCLUDE SQLCA;")

        mock_run.return_value = MagicMock(
            stdout="",
            stderr="",
            returncode=0,
        )

        runner.execute(
            file=str(f),
            include_dirs=["/usr/include/oracle", "/opt/oracle/include"],
        )
        call_args = mock_run.call_args[0][0]
        assert "include=/usr/include/oracle" in call_args
        assert "include=/opt/oracle/include" in call_args

    @patch("mider.tools.static_analysis.proc_runner.subprocess.run")
    def test_stderr_errors(self, mock_run, runner, tmp_path):
        """proc가 stderr로 에러를 출력하는 경우."""
        f = tmp_path / "test.pc"
        f.write_text("EXEC SQL SELECT 1;")

        mock_run.return_value = MagicMock(
            stdout="",
            stderr="Semantic error at line 10, column 5\n",
            returncode=1,
        )

        result = runner.execute(file=str(f))
        assert result.data["total_errors"] >= 1

    @patch("mider.tools.static_analysis.proc_runner.subprocess.run")
    def test_pcc_code_extracted(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.pc"
        f.write_text("EXEC SQL SELECT 1;")

        mock_run.return_value = MagicMock(
            stdout="PCC-S-02201, Encountered symbol\nSemantic error at line 5, column 1\n",
            stderr="",
            returncode=1,
        )

        result = runner.execute(file=str(f))
        errors = result.data["errors"]
        sem_errors = [e for e in errors if e.get("line") == 5]
        assert len(sem_errors) >= 1

    @patch("mider.tools.static_analysis.proc_runner.subprocess.run")
    def test_empty_output(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.pc"
        f.write_text("EXEC SQL SELECT 1 FROM DUAL;")

        mock_run.return_value = MagicMock(
            stdout="",
            stderr="",
            returncode=0,
        )

        result = runner.execute(file=str(f))
        assert result.data["success"] is True
        assert result.data["total_errors"] == 0
