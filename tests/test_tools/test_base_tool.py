"""BaseTool, ToolResult, ToolExecutionError 단위 테스트."""

from typing import Any

import pytest

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult


class TestToolResult:
    def test_success(self):
        result = ToolResult(
            success=True,
            data={"content": "hello"},
        )
        assert result.success is True
        assert result.data["content"] == "hello"
        assert result.error is None

    def test_failure(self):
        result = ToolResult(
            success=False,
            data={},
            error="file not found",
        )
        assert result.success is False
        assert result.error == "file not found"

    def test_defaults(self):
        result = ToolResult(success=True)
        assert result.data == {}
        assert result.error is None

    def test_json_roundtrip(self):
        result = ToolResult(
            success=True,
            data={"lines": 100, "encoding": "utf-8"},
        )
        json_str = result.model_dump_json()
        restored = ToolResult.model_validate_json(json_str)
        assert restored.data["lines"] == 100


class TestToolExecutionError:
    def test_message_format(self):
        error = ToolExecutionError("eslint_runner", "binary not found")
        assert str(error) == "[eslint_runner] binary not found"
        assert error.tool_name == "eslint_runner"

    def test_raise_and_catch(self):
        with pytest.raises(ToolExecutionError, match=r"\[grep\] timeout"):
            raise ToolExecutionError("grep", "timeout")


class DummyTool(BaseTool):
    """테스트용 BaseTool 구현."""

    def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data={"key": "value"})


class TestBaseTool:
    def test_execute(self):
        tool = DummyTool()
        result = tool.execute()
        assert result.success is True
        assert result.data["key"] == "value"

    def test_is_abstract(self):
        with pytest.raises(TypeError):
            BaseTool()  # type: ignore[abstract]
