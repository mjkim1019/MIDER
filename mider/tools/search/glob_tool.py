"""GlobTool: 파일 패턴 검색 Tool.

glob 패턴으로 파일을 검색한다.
"""

import logging
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult

logger = logging.getLogger(__name__)


class GlobTool(BaseTool):
    """glob 패턴으로 파일을 검색하는 Tool."""

    def execute(
        self, *, pattern: str, root: str = ".", **kwargs: Any
    ) -> ToolResult:
        """glob 패턴으로 파일을 검색한다.

        Args:
            pattern: glob 패턴 (예: "**/*.js", "*.c")
            root: 검색 시작 디렉토리 (기본: 현재 디렉토리)

        Returns:
            ToolResult (data: matched_files, total_files)

        Raises:
            ToolExecutionError: 루트 디렉토리가 존재하지 않을 때
        """
        root_path = Path(root)

        if not root_path.exists():
            raise ToolExecutionError(
                "glob_tool", f"directory not found: {root}"
            )

        if not root_path.is_dir():
            raise ToolExecutionError(
                "glob_tool", f"not a directory: {root}"
            )

        matched_files = sorted(
            str(p) for p in root_path.glob(pattern) if p.is_file()
        )

        logger.debug(
            f"Glob 검색 완료: {pattern} in {root} → {len(matched_files)}건"
        )

        return ToolResult(
            success=True,
            data={
                "matched_files": matched_files,
                "total_files": len(matched_files),
            },
        )
