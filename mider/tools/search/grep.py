"""Grep: 파일 내 패턴 검색 Tool.

정규표현식 기반으로 파일에서 패턴을 검색한다.
"""

import logging
import re
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult
from mider.tools.file_io.file_reader import FileReader

logger = logging.getLogger(__name__)


class Grep(BaseTool):
    """파일에서 정규표현식 패턴을 검색하는 Tool."""

    def __init__(self) -> None:
        self._file_reader = FileReader()

    def execute(
        self, *, pattern: str, file: str, **kwargs: Any
    ) -> ToolResult:
        """파일에서 패턴을 검색한다.

        Args:
            pattern: 정규표현식 패턴
            file: 검색할 파일 경로

        Returns:
            ToolResult (data: matches, total_matches)

        Raises:
            ToolExecutionError: 파일 읽기 실패 또는 패턴 컴파일 실패 시
        """
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            raise ToolExecutionError(
                "grep", f"invalid regex pattern: {e}"
            ) from e

        read_result = self._file_reader.execute(path=file)
        content = read_result.data["content"]
        lines = content.splitlines()

        matches: list[dict[str, Any]] = []
        for line_num, line in enumerate(lines, start=1):
            match = compiled.search(line)
            if match:
                matches.append({
                    "line": line_num,
                    "content": line.strip(),
                    "match": match.group(),
                    "start": match.start(),
                    "end": match.end(),
                })

        logger.debug(
            f"패턴 검색 완료: {pattern} in {file} → {len(matches)}건"
        )

        return ToolResult(
            success=True,
            data={
                "matches": matches,
                "total_matches": len(matches),
            },
        )
