"""콘솔 출력 스타일 — Rich 기반 UI 헬퍼 모음.

main.py나 에이전트 코드에 색상/스타일 로직이 흩어지지 않도록
프레젠테이션 계층은 이 모듈에 모은다.
"""

from __future__ import annotations

import traceback

from rich.console import Console
from rich.text import Text

RAINBOW_COLORS: tuple[str, ...] = (
    "red",
    "orange1",
    "yellow",
    "green",
    "cyan",
    "blue",
    "magenta",
)


def rainbow_text(message: str) -> Text:
    """글자 단위로 무지개 색을 순환 적용한 Rich Text를 반환한다 (공백은 제외)."""
    text = Text()
    idx = 0
    for ch in message:
        if ch.strip():
            text.append(ch, style=f"bold {RAINBOW_COLORS[idx % len(RAINBOW_COLORS)]}")
            idx += 1
        else:
            text.append(ch)
    return text


def print_verbose_error(console: Console, exc: BaseException) -> None:
    """verbose error 모드일 때 traceback과 응답 본문을 출력한다.

    OpenAI/httpx 예외에서 response.status_code, body, response.text를 추출 시도한다.
    """
    from mider.config.debug_logger import is_verbose_errors

    if not is_verbose_errors():
        return

    console.print("[dim]── verbose error log ──[/]")
    console.print(traceback.format_exc())

    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status is not None:
            console.print(f"[dim]Response status:[/] {status}")
        body = getattr(exc, "body", None)
        if body is not None:
            console.print(f"[dim]Response body:[/] {body}")
        else:
            try:
                text = response.text
            except Exception:
                text = None
            if text:
                console.print(f"[dim]Response text:[/] {text}")
