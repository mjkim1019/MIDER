"""Logging 설정: Rich 기반 로깅.

print() 대신 이 모듈을 통해 로깅한다.
DEBUG/INFO는 dim(회색), WARNING/ERROR는 기본 색상.
"""

import logging
import os
from typing import Any

from rich.logging import RichHandler


class _DimRichHandler(RichHandler):
    """DEBUG/INFO 메시지를 dim으로 출력하는 RichHandler."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno <= logging.INFO:
            record.msg = f"[dim]{record.msg}[/dim]"
        super().emit(record)


def setup_logging(level: str | None = None) -> None:
    """Rich 기반 로깅을 설정한다.

    Args:
        level: 로그 레벨 문자열 (DEBUG, INFO, WARNING, ERROR).
               None이면 환경 변수 MIDER_LOG_LEVEL 사용. 기본값: INFO.
    """
    log_level = level or os.environ.get("MIDER_LOG_LEVEL", "INFO")

    logging.basicConfig(
        level=log_level.upper(),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            _DimRichHandler(
                rich_tracebacks=True,
                show_path=False,
                markup=True,
                highlighter=None,
            )
        ],
        force=True,
    )

    # 외부 라이브러리 로깅 레벨 조정
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
