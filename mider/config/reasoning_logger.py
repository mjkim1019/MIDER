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

from typing import Any

from rich.console import Console


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
        """입력 데이터 / 스캔 결과 (cyan)."""
        self._dot("cyan", message)

    def detect(self, message: str) -> None:
        """탐지된 문제 / 경고 (red)."""
        self._dot("red", message)

    def decision(self, message: str, reason: str | None = None) -> None:
        """의사결정 (yellow) + 선택적 근거 라인.

        Args:
            message: 결정 내용 (예: "Error-Focused path")
            reason: 근거 (예: "duplicate_ids=1건, parse_errors=0건")
        """
        self._dot("yellow", message)
        if reason:
            self._print(f"     [dim]∵ {reason}[/dim]")

    def prompt(self, message: str) -> None:
        """프롬프트 구성 등 내부 처리 (blue)."""
        self._dot("blue", message)

    def process(self, message: str) -> None:
        """파싱, 검증 등 내부 처리 (blue)."""
        self._dot("blue", message)

    def llm_request(self, message: str) -> None:
        """LLM 호출 시작 (magenta)."""
        self._dot("magenta", message)

    def llm_response(self, message: str) -> None:
        """LLM 응답 수신 (magenta)."""
        self._dot("magenta", message)

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
        self._dot("green", message)
        if issues:
            for issue in issues:
                severity = issue.get("severity", "").upper()
                issue_id = issue.get("issue_id", "")
                title = issue.get("title", "")
                color = _severity_color(severity)
                self._print(
                    f"     [{color}][{severity}][/{color}] {issue_id} {title}"
                )

    # ──────────────────────────────────────────────
    # 내부 유틸
    # ──────────────────────────────────────────────

    def _dot(self, color: str, message: str) -> None:
        """컬러 dot + 메시지를 출력한다."""
        if not self._verbose:
            return
        self._print(f"  [{color}]●[/{color}] {message}")

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
