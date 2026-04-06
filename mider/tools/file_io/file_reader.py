"""FileReader: 파일 읽기 Tool.

Agent가 분석할 파일의 내용을 읽어 반환한다.
"""

import logging
import codecs
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult

logger = logging.getLogger(__name__)

_SUPPORTED_ENCODINGS = ("utf-8", "cp949", "euc-kr")
_LOSSY_FALLBACKS = ("cp949", "euc-kr")


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

        content = None
        used_encoding = None
        last_error: Exception | None = None

        try:
            raw = file_path.read_bytes()
        except PermissionError as e:
            raise ToolExecutionError(
                "file_reader", f"permission denied: {path}"
            ) from e

        if raw.startswith(codecs.BOM_UTF8):
            content = raw.decode("utf-8-sig")
            used_encoding = "utf-8-sig"
        else:
            for encoding in _SUPPORTED_ENCODINGS:
                try:
                    content = raw.decode(encoding)
                    used_encoding = encoding
                    break
                except UnicodeDecodeError as e:
                    last_error = e

        if content is None or used_encoding is None:
            for encoding in _LOSSY_FALLBACKS:
                try:
                    content = raw.decode(encoding, errors="replace")
                    used_encoding = f"{encoding}-replace"
                    logger.warning(
                        "손상된 바이트를 치환하여 파일 읽기 계속: %s (%s)",
                        path,
                        used_encoding,
                    )
                    break
                except Exception:
                    continue

        if content is None or used_encoding is None:
            raise ToolExecutionError(
                "file_reader", f"encoding error: {last_error}"
            ) from last_error

        content = content.replace("\r\n", "\n").replace("\r", "\n")

        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        file_size = file_path.stat().st_size

        logger.debug(f"파일 읽기 완료: {path} ({line_count}줄, {file_size}bytes)")

        return ToolResult(
            success=True,
            data={
                "content": content,
                "line_count": line_count,
                "encoding": used_encoding,
                "file_size": file_size,
            },
        )
