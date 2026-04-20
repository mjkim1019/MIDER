"""Hidden 디버그 로그 모듈.

log_on 시 분석 파일별로 상세 로그 파일을 생성한다.
LLM 요청/응답, 정적분석 결과, 오류 정보를 기록한다.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 모듈 상태 ──────────────────────────────────────
_enabled: bool = False
_log_dir: Path | None = None
_current_fh: logging.FileHandler | None = None
_file_logger: logging.Logger = logging.getLogger("mider.debug_file")


def is_enabled() -> bool:
    """디버그 로깅이 활성화되어 있는지 반환한다."""
    return _enabled


def enable(base_dir: Path) -> Path:
    """디버그 로깅을 활성화한다.

    Args:
        base_dir: mider 실행 파일이 있는 기본 디렉토리

    Returns:
        로그 디렉토리 경로
    """
    global _enabled, _log_dir
    _log_dir = base_dir / "log"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _enabled = True
    logger.info(f"디버그 로그 활성화: {_log_dir}")
    return _log_dir


def disable() -> None:
    """디버그 로깅을 비활성화한다."""
    global _enabled
    end_file()
    _enabled = False
    logger.info("디버그 로그 비활성화")


def start_file(filename: str) -> None:
    """분석 파일별 로그 핸들러를 시작한다."""
    global _current_fh

    if not _enabled or _log_dir is None:
        return

    end_file()

    stem = Path(filename).stem
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = _log_dir / f"{stem}_{ts}.log"

    _current_fh = logging.FileHandler(str(log_path), encoding="utf-8")
    _current_fh.setLevel(logging.DEBUG)
    _current_fh.setFormatter(
        logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )

    _file_logger.addHandler(_current_fh)
    _file_logger.setLevel(logging.DEBUG)

    _write(f"{'=' * 60}")
    _write(f"분석 시작: {filename}")
    _write(f"시각: {datetime.now().isoformat()}")
    _write(f"{'=' * 60}")


def end_file() -> None:
    """현재 파일 로그 핸들러를 종료한다."""
    global _current_fh

    if _current_fh is not None:
        _write(f"{'=' * 60}")
        _write(f"분석 종료: {datetime.now().isoformat()}")
        _write(f"{'=' * 60}")
        _file_logger.removeHandler(_current_fh)
        _current_fh.close()
        _current_fh = None


def _write(text: str) -> None:
    """로그 파일에 기록한다."""
    if _current_fh is not None:
        _file_logger.debug(text)


# ── LLM 로깅 ──────────────────────────────────────

def log_llm_request(model: str, messages: list[dict[str, Any]]) -> None:
    """LLM 요청을 기록한다."""
    if not _enabled or _current_fh is None:
        return

    _write("")
    _write(f"{'=' * 60}")
    _write(f"LLM REQUEST  (model: {model})")
    _write(f"{'=' * 60}")

    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        _write(f"--- Message[{i}] ({role}) ---")
        _write(content)
        _write("")


def log_llm_response(response: str, elapsed_ms: float = 0) -> None:
    """LLM 응답을 기록한다."""
    if not _enabled or _current_fh is None:
        return

    _write("")
    _write(f"{'=' * 60}")
    _write(f"LLM RESPONSE  ({len(response)} chars, {elapsed_ms:.0f}ms)")
    _write(f"{'=' * 60}")
    _write(response)
    _write("")


# ── 정적분석 로깅 ──────────────────────────────────

def log_static_result(tool_name: str, data: dict[str, Any]) -> None:
    """정적분석 결과를 기록한다."""
    if not _enabled or _current_fh is None:
        return

    _write("")
    _write(f"{'=' * 60}")
    _write(f"STATIC ANALYSIS: {tool_name}")
    _write(f"{'=' * 60}")
    _write(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    _write("")


# ── 범용 로깅 ──────────────────────────────────────

def log_info(category: str, message: str) -> None:
    """범용 정보를 기록한다."""
    if not _enabled or _current_fh is None:
        return

    _write(f"[{category}] {message}")


def log_error(category: str, message: str) -> None:
    """에러 정보를 기록한다."""
    if not _enabled or _current_fh is None:
        return

    _write(f"[ERROR:{category}] {message}")
