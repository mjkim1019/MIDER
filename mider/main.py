"""Mider CLI 진입점.

폐쇄망 소스코드 분석 CLI.
Phase 0(분류) → Phase 1(컨텍스트) → Phase 2(분석) → Phase 3(리포트).
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, APITimeoutError, RateLimitError
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from mider import __version__
from mider.agents.orchestrator import OrchestratorAgent
from mider.config.logging_config import setup_logging

logger = logging.getLogger(__name__)

# 종료 코드
EXIT_OK = 0
EXIT_CRITICAL_FOUND = 1
EXIT_FILE_ERROR = 2
EXIT_LLM_ERROR = 3

# 심각도별 색상
_SEVERITY_COLORS = {
    "critical": "red bold",
    "high": "yellow bold",
    "medium": "cyan",
    "low": "dim",
}

_SEVERITY_ORDER = ["critical", "high", "medium", "low"]


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자 파서를 생성한다."""
    parser = argparse.ArgumentParser(
        prog="mider",
        description="Mider - 폐쇄망 소스코드 분석 CLI",
    )
    parser.add_argument(
        "--files", "-f",
        nargs="+",
        required=True,
        help="분석할 파일 경로 (1개 이상, glob 지원)",
    )
    parser.add_argument(
        "--output", "-o",
        default="./output",
        help="결과 출력 디렉토리 (기본: ./output)",
    )
    parser.add_argument(
        "--model", "-m",
        default=None,
        help="LLM 모델명 (기본: MIDER_MODEL 환경변수 또는 gpt-4o)",
    )
    parser.add_argument(
        "--explain-plan", "-e",
        default=None,
        help="Explain Plan 결과 파일 경로 (SQL 분석 시 사용)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="상세 로그 출력",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def resolve_model(args_model: str | None) -> str:
    """모델명을 결정한다: CLI 인자 > 환경변수 > 기본값."""
    if args_model:
        return args_model
    return os.environ.get("MIDER_MODEL", "gpt-4o")


def validate_api_key() -> str | None:
    """LLM API 키를 검증한다.

    우선순위:
    1. AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT (Azure)
    2. MIDER_API_KEY → OPENAI_API_KEY 브리징
    3. OPENAI_API_KEY (직접 설정)

    Returns:
        MIDER_API_KEY 값 (Azure 경로면 None)

    Raises:
        SystemExit: 어떤 API 키도 없으면 exit code 3으로 종료
    """
    # Azure 경로: AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT
    azure_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    if azure_key and azure_endpoint:
        return None  # LLMClient가 Azure 변수를 직접 읽음

    # OpenAI 경로: MIDER_API_KEY → OPENAI_API_KEY 브리징
    mider_key = os.environ.get("MIDER_API_KEY", "")
    if mider_key:
        return mider_key

    # OPENAI_API_KEY 직접 설정
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        return None  # 이미 설정됨

    console = Console(stderr=True)
    console.print(
        "[red bold]오류:[/] LLM API 키가 설정되지 않았습니다.",
    )
    console.print("  Azure: AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT")
    console.print("  OpenAI: MIDER_API_KEY 또는 OPENAI_API_KEY")
    sys.exit(EXIT_LLM_ERROR)


def _create_progress_callback(
    console: Console,
) -> Callable[[int, str, int, int, str], None]:
    """OrchestratorAgent용 Rich Progress 콜백을 생성한다."""
    phase_names = {
        0: "파일 분류",
        1: "컨텍스트 수집",
        2: "코드 분석",
        3: "리포트 생성",
    }
    phase_start_times: dict[int, float] = {}
    completed_phases: set[int] = set()

    def callback(
        phase: int,
        phase_name: str,
        current: int,
        total: int,
        message: str,
    ) -> None:
        if phase not in phase_start_times:
            phase_start_times[phase] = time.time()

        display_name = phase_names.get(phase, phase_name)

        if total > 0 and current >= total and phase not in completed_phases:
            completed_phases.add(phase)
            elapsed = time.time() - phase_start_times[phase]
            console.print(
                f"[Phase {phase}] {display_name}...        "
                f"[green]done[/] ({elapsed:.1f}s)"
            )
        elif phase == 2 and total > 0:
            # Phase 2는 파일별 진행률 표시
            console.print(
                f"[Phase {phase}] {display_name}...        "
                f"[{current}/{total}] {message}",
                end="\r",
            )

    return callback


def print_file_list(console: Console, files: list[str]) -> None:
    """분석 대상 파일 목록을 출력한다."""
    ext_to_lang = {
        ".js": "JavaScript",
        ".c": "C",
        ".h": "C",
        ".pc": "Pro*C",
        ".sql": "SQL",
    }

    console.print(f"\n\\[파일] {len(files)}개")
    for f in files:
        ext = Path(f).suffix.lower()
        lang = ext_to_lang.get(ext, "Unknown")
        console.print(f"  {f}      ({lang})")
    console.print()


def print_issues(console: Console, issue_list: dict[str, Any]) -> None:
    """모든 이슈를 심각도별로 출력한다."""
    issues = issue_list.get("issues", [])
    if not issues:
        return

    # 심각도 순서로 정렬
    severity_rank = {s: i for i, s in enumerate(_SEVERITY_ORDER)}
    sorted_issues = sorted(
        issues,
        key=lambda x: severity_rank.get(x.get("severity", "low"), 99),
    )

    console.print()

    for issue in sorted_issues:
        severity = issue.get("severity", "low").upper()
        issue_id = issue.get("issue_id", "")
        title = issue.get("title", "")
        file_path = issue.get("file", "")
        location = issue.get("location", {})
        line = location.get("line_start", 0)
        fix = issue.get("fix", {})
        before = fix.get("before", "")
        after = fix.get("after", "")
        description = issue.get("description", "")

        severity_color = _SEVERITY_COLORS.get(
            issue.get("severity", "low"), "dim",
        )

        content = Text()
        content.append(f"[{severity}] ", style=severity_color)
        content.append(f"{issue_id}  ", style="bold")
        content.append(title)
        content.append(f"\n  {file_path}:{line}\n")

        # Critical/High는 Before/After 코드 표시
        if issue.get("severity") in ("critical", "high"):
            if before:
                content.append("\n  - Before:\n", style="red")
                for bline in before.strip().splitlines():
                    content.append(f"    {bline}\n", style="red")

            if after:
                content.append("  + After:\n", style="green")
                for aline in after.strip().splitlines():
                    content.append(f"    {aline}\n", style="green")

        if description:
            content.append(f"\n  {description}\n")

        console.print(Panel(content, border_style="dim"))


def print_summary(
    console: Console,
    summary: dict[str, Any],
    output_dir: str,
) -> None:
    """심각도별 요약과 배포 판정을 출력한다."""
    issue_summary = summary.get("issue_summary", {})
    by_severity = issue_summary.get("by_severity", {})
    risk = summary.get("risk_assessment", {})

    # 심각도 바
    parts: list[str] = []
    for sev in _SEVERITY_ORDER:
        count = by_severity.get(sev, 0)
        color = _SEVERITY_COLORS.get(sev, "dim")
        parts.append(f"[{color}]{sev.upper()}  {count}[/]")

    bar = "    ".join(parts)

    console.print()
    console.rule(style="dim")
    console.print(f"  {bar}")
    console.rule(style="dim")

    # 배포 판정
    deployment_risk = risk.get("deployment_risk", "LOW")
    deployment_allowed = risk.get("deployment_allowed", True)

    if deployment_allowed:
        console.print(f"\n배포 판정: [green bold]가능[/] ({deployment_risk})")
    else:
        blocking = risk.get("blocking_issues", [])
        reason = f"Critical {by_severity.get('critical', 0)}건"
        if by_severity.get("high", 0) >= 3:
            reason += f", High {by_severity.get('high', 0)}건"
        console.print(
            f"\n배포 판정: [red bold]불가[/] ({reason})"
        )
        if blocking:
            console.print(f"  차단 이슈: {', '.join(blocking[:5])}")

    # 출력 파일 경로
    console.print(f"\n출력: {output_dir}/issue-list.json")
    console.print(f"      {output_dir}/checklist.json")
    console.print(f"      {output_dir}/summary.json")
    console.print(f"      {output_dir}/deployment-checklist.json")


def write_output_files(
    output_dir: str,
    result: dict[str, Any],
) -> None:
    """분석 결과를 JSON 파일로 출력한다."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    files_to_write = {
        "issue-list.json": result.get("issue_list", {}),
        "checklist.json": result.get("checklist", {}),
        "summary.json": result.get("summary", {}),
        "deployment-checklist.json": result.get("deployment_checklist", {}),
    }

    for filename, data in files_to_write.items():
        filepath = out_path / filename
        filepath.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info(f"출력 파일 생성: {filepath}")


def determine_exit_code(result: dict[str, Any]) -> int:
    """분석 결과에서 종료 코드를 결정한다.

    Returns:
        0: 정상 완료, Critical 없음
        1: 정상 완료, Critical 있음 (배포 불가)
    """
    summary = result.get("summary", {})
    issue_summary = summary.get("issue_summary", {})
    by_severity = issue_summary.get("by_severity", {})
    critical_count = by_severity.get("critical", 0)

    if critical_count > 0:
        return EXIT_CRITICAL_FOUND

    return EXIT_OK


async def run_analysis(
    files: list[str],
    output_dir: str,
    model: str,
    console: Console,
    explain_plan: str | None = None,
) -> int:
    """분석 파이프라인을 실행한다.

    Returns:
        종료 코드 (0, 1, 2, 3)
    """
    progress_callback = _create_progress_callback(console)

    orchestrator = OrchestratorAgent(
        model=model,
        progress_callback=progress_callback,
    )

    result = await orchestrator.run(
        files=files,
        explain_plan_file=explain_plan,
    )

    # 파일 검증 오류만 있고 분석 결과가 없는 경우
    errors = result.get("errors", [])
    issue_list = result.get("issue_list", {})
    total_issues = issue_list.get("total_issues", 0)

    if errors and total_issues == 0 and not result.get("execution_plan", {}).get("sub_tasks"):
        write_output_files(output_dir, result)
        return EXIT_FILE_ERROR

    # 결과 출력
    write_output_files(output_dir, result)
    print_issues(console, issue_list)
    print_summary(console, result.get("summary", {}), output_dir)

    return determine_exit_code(result)


def main() -> None:
    """CLI 메인 함수."""
    # .env 파일 로드 (있으면)
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    # 로깅 설정
    log_level = "DEBUG" if args.verbose else None
    setup_logging(log_level)

    console = Console()
    console.print(f"Mider v{__version__}")

    # API 키 검증
    api_key = validate_api_key()
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key

    # 모델 결정
    model = resolve_model(args.model)

    # API Base URL 설정
    api_base = os.environ.get("MIDER_API_BASE")
    if api_base:
        os.environ["OPENAI_BASE_URL"] = api_base

    # 파일 목록 출력
    print_file_list(console, args.files)

    # Explain Plan 파일 검증
    explain_plan = getattr(args, "explain_plan", None)
    if explain_plan and not Path(explain_plan).exists():
        console.print(
            f"[red bold]오류:[/] Explain Plan 파일 없음: {explain_plan}",
        )
        sys.exit(EXIT_FILE_ERROR)

    # 분석 실행
    try:
        exit_code = asyncio.run(
            run_analysis(
                files=args.files,
                output_dir=args.output,
                model=model,
                console=console,
                explain_plan=explain_plan,
            )
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]분석이 사용자에 의해 중단되었습니다.[/]")
        sys.exit(130)
    except (APIError, APIConnectionError, RateLimitError, APITimeoutError, EnvironmentError) as e:
        logger.error(f"LLM API 오류: {e}")
        console.print(f"[red bold]LLM API 오류:[/] {e}")
        sys.exit(EXIT_LLM_ERROR)
    except Exception as e:
        logger.error(f"분석 중 오류 발생: {e}")
        console.print(f"[red bold]오류:[/] {e}")
        sys.exit(EXIT_FILE_ERROR)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
