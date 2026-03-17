"""ReasoningLogger: Agent 추론 과정을 컬러 dot으로 시각화한다.

Agent의 의사결정 과정(스캔 → 경로 선택 → 프롬프트 구성 → LLM 응답 → 후처리)을
Rich Console 기반 컬러 dot 로그로 CLI에 실시간 출력한다.

색상 규칙:
  cyan    — 입력 데이터 / 스캔 결과
  red     — 오류 / 경고 / 탐지된 문제
  yellow  — 의사결정 + 근거(∵)
  blue    — 내부 처리 (프롬프트, 파싱, 검증)
  magenta — LLM 호출 / 응답
  green   — 최종 결과
"""

import time
from contextlib import contextmanager
from typing import Any, Generator

from rich.console import Console
from rich.live import Live
from rich.text import Text


class ReasoningLogger:
    """Agent 추론 로그를 컬러 dot으로 출력한다.

    verbose=False이면 모든 출력을 무시한다 (no-op).
    """

    def __init__(
        self,
        console: Console | None = None,
        verbose: bool = False,
    ) -> None:
        self._console = console or Console()
        self._verbose = verbose

    @property
    def enabled(self) -> bool:
        """로그 출력이 활성화되어 있는지 반환한다."""
        return self._verbose

    # ──────────────────────────────────────────────
    # Phase 헤더
    # ──────────────────────────────────────────────

    def phase_header(self, phase: int, agent_name: str) -> None:
        """Phase 시작 헤더를 출력한다.

        예: [Phase 2] ── XMLAnalyzerAgent ────────────────
        """
        if not self._verbose:
            return
        line = f"── {agent_name} "
        pad = max(0, 50 - len(line))
        self._console.print(
            f"\n[bold][Phase {phase}] {line}{'─' * pad}[/bold]"
        )

    # ──────────────────────────────────────────────
    # 로그 메서드 (색상별)
    # ──────────────────────────────────────────────

    def scan(self, message: str) -> None:
        """입력 데이터 / 스캔 결과 (dot 없이 dim 텍스트)."""
        self._print(f"    [grey70]{message}[/grey70]")

    def detect(self, message: str) -> None:
        """탐지된 문제 / 경고 (red dot + dim 내용)."""
        self._dot_styled("red", message)

    def decision(self, message: str, reason: str | None = None) -> None:
        """의사결정 (yellow dot + bold 키워드 + dim 내용).

        Args:
            message: 결정 내용 (예: "Decision: Error-Focused path")
            reason: 근거 (예: "duplicate_ids=1건, parse_errors=0건")
        """
        self._dot_styled("yellow", message)
        if reason:
            self._print(f"     [grey70]∵ {reason}[/grey70]")

    def prompt(self, message: str) -> None:
        """프롬프트 구성 등 내부 처리 (blue dot + dim 내용)."""
        self._dot_styled("blue", message)

    def process(self, message: str) -> None:
        """파싱, 검증 등 내부 처리 (blue dot + dim 내용)."""
        self._dot_styled("blue", message)

    def llm_request(self, message: str) -> None:
        """LLM 호출 시작 (magenta dot + dim 내용)."""
        self._dot_styled("magenta", message)

    def llm_response(self, message: str) -> None:
        """LLM 응답 수신 (magenta dot + dim 내용)."""
        self._dot_styled("magenta", message)

    @contextmanager
    def spinner(self, message: str) -> Generator[None, None, None]:
        """LLM 호출 중 spinner 애니메이션을 표시한다.

        verbose=False이면 아무것도 하지 않는다.
        Rich Live 충돌 시(병렬 호출) 단순 출력으로 fallback.

        사용법:
            with self.rl.spinner("LLM 분석 중..."):
                response = await self.call_llm(messages)
        """
        if not self._verbose:
            yield
            return

        import threading
        from rich.errors import LiveError

        start = time.time()
        spinner_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

        def _render() -> Text:
            elapsed = time.time() - start
            frame = spinner_frames[int(elapsed * 8) % len(spinner_frames)]
            text = Text()
            text.append(f"  {frame} ", style="magenta")
            text.append(message, style="magenta")
            text.append(f" ({elapsed:.1f}초 경과)", style="dim")
            return text

        try:
            with Live(
                _render(),
                console=self._console,
                refresh_per_second=8,
                transient=True,
            ) as live:
                stop_event = threading.Event()

                def _update_loop() -> None:
                    while not stop_event.is_set():
                        try:
                            live.update(_render())
                        except LiveError:
                            break
                        stop_event.wait(0.125)

                updater = threading.Thread(target=_update_loop, daemon=True)
                updater.start()
                try:
                    yield
                finally:
                    stop_event.set()
                    updater.join(timeout=1)
            # Live 블록 종료 후 줄바꿈 (spinner가 지워진 뒤)
            self._console.print()
        except LiveError:
            # 병렬 호출 시 Live 충돌 → 단순 출력으로 fallback
            self._dot_styled("magenta", message)
            yield

    def result(
        self,
        message: str,
        issues: list[dict[str, Any]] | None = None,
    ) -> None:
        """최종 결과 (green) + 선택적 이슈 목록.

        Args:
            message: 결과 요약 (예: "3 issues, 14.5초")
            issues: 이슈 딕셔너리 리스트 (severity, issue_id, title 포함)
        """
        self._dot_styled("green", message)
        if issues:
            for issue in issues:
                severity = issue.get("severity", "").upper()
                issue_id = issue.get("issue_id", "")
                title = issue.get("title", "")
                color = _severity_color(severity)
                self._print(
                    f"     [{color}][{severity}][/{color}] [grey70]{issue_id} {title}[/grey70]"
                )

    # ──────────────────────────────────────────────
    # 내부 유틸
    # ──────────────────────────────────────────────

    def _dot_styled(self, color: str, message: str) -> None:
        """컬러 dot + 키워드(bold) + 내용(dim)을 출력한다.

        메시지에서 ':'가 있으면 앞부분을 bold 키워드, 뒷부분을 dim으로 분리.
        예: "Decision: Error-Focused path" → "Decision:" bold + "Error-Focused path" dim
        ':'가 없으면 전체를 dim으로 출력.
        """
        if not self._verbose:
            return
        if ": " in message:
            keyword, content = message.split(": ", 1)
            self._print(
                f"  [{color}]●[/{color}] [bold]{keyword}:[/bold] [grey70]{content}[/grey70]"
            )
        else:
            self._print(f"  [{color}]●[/{color}] [grey70]{message}[/grey70]")

    def _print(self, text: str) -> None:
        """Rich Console로 출력한다."""
        if not self._verbose:
            return
        self._console.print(text)


def _severity_color(severity: str) -> str:
    """이슈 severity에 맞는 Rich 색상을 반환한다."""
    return {
        "CRITICAL": "red bold",
        "HIGH": "red",
        "MEDIUM": "yellow",
        "LOW": "dim",
    }.get(severity, "dim")
