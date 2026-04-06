"""ESLintRunner 단위 테스트."""

import json
from unittest.mock import MagicMock, patch

import pytest

from mider.tools.base_tool import ToolExecutionError
from mider.tools.static_analysis.eslint_runner import ESLintRunner


@pytest.fixture
def mock_binary(tmp_path):
    """mock ESLint 바이너리 경로 생성."""
    binary = tmp_path / "node"
    binary.write_text("#!/bin/sh\necho mock")
    binary.chmod(0o755)
    # _find_eslint() 후보 경로에 eslint 바이너리 생성
    eslint_dir = tmp_path / "node_modules" / ".bin"
    eslint_dir.mkdir(parents=True)
    eslint = eslint_dir / "eslint"
    eslint.write_text("#!/bin/sh\necho mock")
    eslint.chmod(0o755)
    return binary


@pytest.fixture
def mock_config(tmp_path):
    """mock ESLint config."""
    config = tmp_path / ".eslintrc.json"
    config.write_text('{"rules": {}}')
    return config


@pytest.fixture
def runner(mock_binary, mock_config):
    """ESLintRunner with mock binary and config."""
    return ESLintRunner(
        binary_path=str(mock_binary),
        config_path=str(mock_config),
    )


def _eslint_json_output(messages: list[dict]) -> str:
    """ESLint JSON 형식 출력 생성."""
    return json.dumps([{
        "filePath": "/test.js",
        "messages": messages,
        "errorCount": sum(1 for m in messages if m.get("severity") == 2),
        "warningCount": sum(1 for m in messages if m.get("severity") == 1),
    }])


class TestESLintRunner:
    def test_file_not_found(self, runner):
        with pytest.raises(ToolExecutionError, match="file not found"):
            runner.execute(file="/nonexistent.js")

    @patch("shutil.which", return_value=None)
    def test_binary_not_found(self, _mock_which, mock_config, tmp_path):
        runner = ESLintRunner(
            binary_path="/nonexistent/node",
            config_path=str(mock_config),
        )
        f = tmp_path / "test.js"
        f.write_text("var x;")
        # node 바이너리도 시스템 PATH에도 없으면 skipped 반환
        result = runner.execute(file=str(f))
        assert result.data.get("skipped") is True

    def test_config_not_found(self, mock_binary, tmp_path):
        runner = ESLintRunner(
            binary_path=str(mock_binary),
            config_path="/nonexistent/.eslintrc.json",
        )
        f = tmp_path / "test.js"
        f.write_text("var x;")
        with pytest.raises(ToolExecutionError, match="config not found"):
            runner.execute(file=str(f))

    @patch("mider.tools.static_analysis.eslint_runner.subprocess.run")
    def test_parse_errors(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.js"
        f.write_text("var x;")

        mock_run.return_value = MagicMock(
            stdout=_eslint_json_output([
                {
                    "ruleId": "no-undef",
                    "severity": 2,
                    "message": "'foo' is not defined",
                    "line": 1,
                    "column": 5,
                    "endLine": 1,
                    "endColumn": 8,
                },
            ]),
            stderr="",
            returncode=1,
        )

        result = runner.execute(file=str(f))
        assert result.success is True
        assert result.data["total_errors"] == 1
        assert result.data["total_warnings"] == 0
        error = result.data["errors"][0]
        assert error["rule"] == "no-undef"
        assert error["line"] == 1
        assert error["message"] == "'foo' is not defined"

    @patch("mider.tools.static_analysis.eslint_runner.subprocess.run")
    def test_parse_warnings(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.js"
        f.write_text("var x;")

        mock_run.return_value = MagicMock(
            stdout=_eslint_json_output([
                {
                    "ruleId": "no-unused-vars",
                    "severity": 1,
                    "message": "'x' is defined but never used",
                    "line": 1,
                    "column": 5,
                },
            ]),
            stderr="",
            returncode=0,
        )

        result = runner.execute(file=str(f))
        assert result.data["total_warnings"] == 1
        assert result.data["total_errors"] == 0
        assert result.data["warnings"][0]["rule"] == "no-unused-vars"

    @patch("mider.tools.static_analysis.eslint_runner.subprocess.run")
    def test_no_issues(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.js"
        f.write_text("const x = 1;")

        mock_run.return_value = MagicMock(
            stdout=_eslint_json_output([]),
            stderr="",
            returncode=0,
        )

        result = runner.execute(file=str(f))
        assert result.data["total_errors"] == 0
        assert result.data["total_warnings"] == 0

    @patch("mider.tools.static_analysis.eslint_runner.subprocess.run")
    def test_mixed_errors_warnings(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.js"
        f.write_text("var x;")

        mock_run.return_value = MagicMock(
            stdout=_eslint_json_output([
                {"ruleId": "no-undef", "severity": 2, "message": "error", "line": 1, "column": 1},
                {"ruleId": "no-var", "severity": 1, "message": "warning", "line": 1, "column": 1},
                {"ruleId": "no-eval", "severity": 2, "message": "error2", "line": 2, "column": 1},
            ]),
            stderr="",
            returncode=1,
        )

        result = runner.execute(file=str(f))
        assert result.data["total_errors"] == 2
        assert result.data["total_warnings"] == 1

    @patch("mider.tools.static_analysis.eslint_runner.subprocess.run")
    def test_empty_output(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.js"
        f.write_text("const x = 1;")

        mock_run.return_value = MagicMock(
            stdout="",
            stderr="",
            returncode=0,
        )

        result = runner.execute(file=str(f))
        assert result.success is True
        assert result.data["total_errors"] == 0

    @patch("mider.tools.static_analysis.eslint_runner.subprocess.run")
    def test_invalid_json_output(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.js"
        f.write_text("var x;")

        mock_run.return_value = MagicMock(
            stdout="not json",
            stderr="",
            returncode=1,
        )

        with pytest.raises(ToolExecutionError, match="invalid JSON"):
            runner.execute(file=str(f))

    @patch("mider.tools.static_analysis.eslint_runner.subprocess.run")
    def test_execution_error(self, mock_run, runner, tmp_path):
        f = tmp_path / "test.js"
        f.write_text("var x;")

        mock_run.return_value = MagicMock(
            stdout="",
            stderr="ESLint crash: some error",
            returncode=2,
        )

        with pytest.raises(ToolExecutionError, match="execution failed"):
            runner.execute(file=str(f))
