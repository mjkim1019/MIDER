"""ClangTidyRunner 단위 테스트."""

from unittest.mock import MagicMock, patch

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.static_analysis.clang_tidy_runner import ClangTidyRunner


@pytest.fixture
def mock_binary(tmp_path):
    """mock clang-tidy 바이너리."""
    binary = tmp_path / "clang-tidy"
    binary.write_text("#!/bin/sh\necho mock")
    binary.chmod(0o755)
    return binary


@pytest.fixture
def runner(mock_binary):
    """ClangTidyRunner with mock binary."""
    return ClangTidyRunner(binary_path=str(mock_binary))


# 샘플 clang-tidy 출력
_SAMPLE_OUTPUT = """/app/calc.c:234:5: warning: function 'strcpy' is insecure [bugprone-not-null-terminated-result]
/app/calc.c:120:10: warning: memory leak [clang-analyzer-core.NullDereference]
/app/calc.c:300:1: note: previous definition is here [misc-no-recursion]
"""

_SAMPLE_ERROR_OUTPUT = """/app/calc.c:50:3: error: use of undeclared identifier 'foo' [clang-diagnostic-error]
"""


class TestClangTidyRunner:
    def test_file_not_found(self, runner):
        with pytest.raises(ToolExecutionError, match="file not found"):
            runner.execute(file="/nonexistent.c")

    def test_binary_not_found(self, tmp_path):
        runner = ClangTidyRunner(binary_path="/nonexistent/clang-tidy")
        f = tmp_path / "test.c"
        f.write_text("int main() {}")
        with pytest.raises(ToolExecutionError, match="binary not found"):
            runner.execute(file=str(f))

    @patch("mider.tools.static_analysis.clang_tidy_runner.subprocess.run")
    def test_parse_warnings(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.c"
        f.write_text("int main() {}")

        mock_run.return_value = MagicMock(
            stdout=_SAMPLE_OUTPUT,
            stderr="",
            returncode=0,
        )

        result = runner.execute(file=str(f))
        assert result.success is True
        # note는 제외, warning만 포함
        assert result.data["total_warnings"] == 2
        w1 = result.data["warnings"][0]
        assert w1["line"] == 234
        assert w1["check"] == "bugprone-not-null-terminated-result"
        assert w1["severity"] == "warning"

    @patch("mider.tools.static_analysis.clang_tidy_runner.subprocess.run")
    def test_parse_errors(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.c"
        f.write_text("int main() {}")

        mock_run.return_value = MagicMock(
            stdout=_SAMPLE_ERROR_OUTPUT,
            stderr="",
            returncode=1,
        )

        result = runner.execute(file=str(f))
        assert result.data["total_warnings"] == 1
        assert result.data["warnings"][0]["severity"] == "error"
        assert result.data["warnings"][0]["check"] == "clang-diagnostic-error"

    @patch("mider.tools.static_analysis.clang_tidy_runner.subprocess.run")
    def test_no_warnings(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.c"
        f.write_text("int main() { return 0; }")

        mock_run.return_value = MagicMock(
            stdout="",
            stderr="",
            returncode=0,
        )

        result = runner.execute(file=str(f))
        assert result.data["total_warnings"] == 0
        assert result.data["warnings"] == []

    @patch("mider.tools.static_analysis.clang_tidy_runner.subprocess.run")
    def test_custom_checks(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.c"
        f.write_text("int main() {}")

        mock_run.return_value = MagicMock(
            stdout="",
            stderr="",
            returncode=0,
        )

        runner.execute(file=str(f), checks="-*,bugprone-*")
        call_args = mock_run.call_args[0][0]
        assert "--checks=-*,bugprone-*" in call_args

    @patch("mider.tools.static_analysis.clang_tidy_runner.subprocess.run")
    def test_stderr_output(self, mock_run, runner, tmp_path):
        """clang-tidy가 stderr로 출력하는 경우."""
        f = tmp_path / "test.c"
        f.write_text("int main() {}")

        mock_run.return_value = MagicMock(
            stdout="",
            stderr='/app/test.c:10:5: warning: test warning [bugprone-test]\n',
            returncode=0,
        )

        result = runner.execute(file=str(f))
        assert result.data["total_warnings"] == 1

    @patch("mider.tools.static_analysis.clang_tidy_runner.subprocess.run")
    def test_warning_details(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.c"
        f.write_text("int main() {}")

        mock_run.return_value = MagicMock(
            stdout="/app/test.c:42:8: warning: potential null dereference [clang-analyzer-core.NullDereference]\n",
            stderr="",
            returncode=0,
        )

        result = runner.execute(file=str(f))
        w = result.data["warnings"][0]
        assert w["file"] == "/app/test.c"
        assert w["line"] == 42
        assert w["column"] == 8
        assert w["message"] == "potential null dereference"
        assert w["check"] == "clang-analyzer-core.NullDereference"
