"""FileReader: 파일 읽기 Tool.

Agent가 분석할 파일의 내용을 읽어 반환한다.
"""

import logging
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult

logger = logging.getLogger(__name__)


class FileReader(BaseTool):
    """파일 내용을 읽어 반환하는 Tool."""

    def execute(self, *, path: str, **kwargs: Any) -> ToolResult:
        """파일을 읽고 내용을 반환한다.

        Args:
            path: 읽을 파일의 경로

        Returns:
            ToolResult (data: content, line_count, encoding, file_size)

        Raises:
            ToolExecutionError: 파일이 존재하지 않거나 읽을 수 없을 때
        """
        file_path = Path(path)

        if not file_path.exists():
            raise ToolExecutionError("file_reader", f"file not found: {path}")

        if not file_path.is_file():
            raise ToolExecutionError("file_reader", f"not a file: {path}")

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = file_path.read_text(encoding="euc-kr")
            except Exception as e:
                raise ToolExecutionError(
                    "file_reader", f"encoding error: {e}"
                ) from e
        except PermissionError as e:
            raise ToolExecutionError(
                "file_reader", f"permission denied: {path}"
            ) from e

        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        file_size = file_path.stat().st_size

        logger.debug(f"파일 읽기 완료: {path} ({line_count}줄, {file_size}bytes)")

        return ToolResult(
            success=True,
            data={
                "content": content,
                "line_count": line_count,
                "encoding": "utf-8",
                "file_size": file_size,
            },
        )
