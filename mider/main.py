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
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
import httpx
from openai import APIConnectionError, APIError, APITimeoutError, RateLimitError
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from mider import __version__
from mider.agents.orchestrator import OrchestratorAgent
from mider.config.llm_client import AICAError
from mider.config.logging_config import setup_logging
from mider.config.reasoning_logger import ReasoningLogger
from mider.tools.utility.markdown_report_formatter import format_markdown_report

logger = logging.getLogger(__name__)


def get_base_dir() -> Path:
    """실행 환경에 따른 기준 디렉토리를 반환한다.

    - PyInstaller frozen 환경: 실행파일이 위치한 디렉토리
    - 개발 환경: 프로젝트 루트 (main.py 기준 한 단계 상위)
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def resolve_input_files(base_dir: Path, filenames: list[str]) -> list[str]:
    """input 폴더 기준으로 파일명을 절대경로로 변환한다.

    이미 절대경로이거나 존재하는 상대경로인 파일은 그대로 사용한다.
    그 외에는 base_dir / 'input' / filename으로 해석한다.

    Args:
        base_dir: 기준 디렉토리 (get_base_dir() 반환값)
        filenames: CLI에서 전달받은 파일명 목록

    Returns:
        절대경로로 변환된 파일 목록
    """
    input_dir = base_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    console = Console(stderr=True)
    resolved: list[str] = []
    has_error = False

    for name in filenames:
        p = Path(name)
        # 이미 절대경로이거나 현재 위치에서 존재하는 상대경로
        if p.is_absolute() or p.exists():
            resolved.append(str(p.resolve()))
            continue
        # input 폴더 기준으로 해석
        input_path = input_dir / name
        if input_path.exists():
            resolved.append(str(input_path.resolve()))
        else:
            console.print(
                f"[red bold]오류:[/] 파일을 찾을 수 없습니다: {name}"
            )
            console.print(f"  확인 경로: {input_path}")
            has_error = True

    if has_error and not resolved:
        console.print(
            "\n[yellow]input 폴더에 분석할 파일을 넣어주세요:[/]"
            f" {input_dir}"
        )
        sys.exit(EXIT_FILE_ERROR)

    return resolved

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


def get_base_dir() -> Path:
    """실행 환경에 따른 기준 디렉토리를 반환한다.

    - PyInstaller frozen 환경: 실행파일이 위치한 디렉토리
    - 개발 환경: 프로젝트 루트 (main.py 기준 한 단계 상위)
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def resolve_input_files(base_dir: Path, filenames: list[str]) -> list[str]:
    """input 폴더 기준으로 파일명을 절대경로로 변환한다.

    검색 순서:
    1. 절대경로 또는 CWD 기준 존재하는 상대경로 → 그대로 사용
    2. base_dir/input/ 폴더 → 사용
    3. base_dir 하위 전체 rglob 검색 → 유일하면 사용, 복수면 에러
    4. 못 찾음 → 에러

    Args:
        base_dir: 기준 디렉토리 (get_base_dir() 반환값)
        filenames: CLI에서 전달받은 파일명 목록

    Returns:
        절대경로로 변환된 파일 목록
    """
    input_dir = base_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    console = Console(stderr=True)
    resolved: list[str] = []
    has_error = False

    for name in filenames:
        p = Path(name)
        # 이미 절대경로이거나 현재 위치에서 존재하는 상대경로
        if p.is_absolute() or p.exists():
            resolved.append(str(p.resolve()))
            continue
        # input 폴더 기준으로 해석
        input_path = input_dir / name
        if input_path.exists():
            resolved.append(str(input_path.resolve()))
            continue
        # base_dir 하위 전체에서 파일명으로 검색
        matches = list(base_dir.rglob(name))
        if len(matches) == 1:
            resolved.append(str(matches[0].resolve()))
            continue
        elif len(matches) > 1:
            console.print(f"[yellow]'{name}' 파일이 {len(matches)}개 발견되었습니다:[/]")
            for i, m in enumerate(matches, 1):
                console.print(f"  {i}. {m.relative_to(base_dir)}")
            console.print("[yellow]상대경로로 정확히 지정해주세요.[/]")
            has_error = True
            continue
        # 어디에서도 찾을 수 없음
        console.print(
            f"[red bold]오류:[/] 파일을 찾을 수 없습니다: {name}"
        )
        console.print(f"  확인 경로: {input_path}")
        has_error = True

    if has_error and not resolved:
        console.print(
            "\n[yellow]input 폴더에 분석할 파일을 넣어주세요:[/]"
            f" {input_dir}"
        )
        sys.exit(EXIT_FILE_ERROR)

    return resolved


def build_parser(output_default: str = "./output") -> argparse.ArgumentParser:
    """CLI 인자 파서를 생성한다.

    Args:
        output_default: --output 옵션의 기본값
    """
    parser = argparse.ArgumentParser(
        prog="mider",
        description="Mider - 폐쇄망 소스코드 분석 CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--files", "-f",
        nargs="+",
        required=False,
        help="분석할 파일명 (input/ 폴더 기준, 미지정 시 인터랙티브 모드)",
    )
    parser.add_argument(
        "--output", "-o",
        default=output_default,
        help=f"결과 출력 디렉토리 (기본: {output_default})",
    )
    parser.add_argument(
        "--model", "-m",
        default=None,
        help="사용할 LLM 모델명 (기본: MIDER_MODEL 환경변수 또는 settings.yaml)",
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
        "--sso",
        action="store_true",
        help="SSO 브라우저 로그인으로 인증 (AICA provider 전용)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def resolve_model(args_model: str | None) -> str:
    """모델명을 결정한다: CLI 인자 > 환경변수 > settings.yaml."""
    if args_model:
        return args_model
    if os.environ.get("MIDER_MODEL"):
        return os.environ["MIDER_MODEL"]
    from mider.config.settings_loader import get_agent_model
    return get_agent_model("orchestrator")


def validate_api_key(sso_mode: bool = False) -> None:
    """LLM API 키를 검증한다 (provider에 따라 분기).

    API_PROVIDER 환경 변수:
    - "openai" (기본): AZURE_OPENAI_API_KEY 또는 OPENAI_API_KEY
    - "aica": AICA_API_KEY + AICA_ENDPOINT

    Args:
        sso_mode: True이면 AICA provider에서 AICA_API_KEY + AICA_ENDPOINT만
                  확인하고, SSO_SESSION은 불필요 (로그인 후 자동 설정됨)

    Raises:
        SystemExit: 필수 환경 변수가 없으면 exit code 3으로 종료
    """
    provider = os.environ.get("API_PROVIDER", "openai").lower()

    if provider == "aica":
        api_key = os.environ.get("AICA_API_KEY", "")
        endpoint = os.environ.get("AICA_ENDPOINT", "")
        if sso_mode:
            # SSO 모드: AICA_ENDPOINT만 필수 (API_KEY는 있으면 좋고, SSO_SESSION은 로그인 후 설정됨)
            if endpoint:
                return
            console = Console(stderr=True)
            console.print("[red bold]오류:[/] SSO 모드에 AICA_ENDPOINT가 필요합니다.")
            console.print("  AICA_ENDPOINT를 환경 변수로 설정하세요.")
            sys.exit(EXIT_LLM_ERROR)
        if api_key and endpoint:
            return
        console = Console(stderr=True)
        console.print("[red bold]오류:[/] LLM API 키가 설정되지 않았습니다.")
        console.print("  AICA_API_KEY와 AICA_ENDPOINT를 환경 변수로 설정하세요.")
        sys.exit(EXIT_LLM_ERROR)
    else:
        azure_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
        azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        if azure_key and azure_endpoint:
            return
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if openai_key:
            return
        console = Console(stderr=True)
        console.print("[red bold]오류:[/] LLM API 키가 설정되지 않았습니다.")
        console.print("  Azure: AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT")
        console.print("  OpenAI: OPENAI_API_KEY")
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
        ".xml": "XML",
    }

    console.print(f"\n\\[파일] {len(files)}개")
    for f in files:
        ext = Path(f).suffix.lower()
        lang = ext_to_lang.get(ext, "Unknown")
        console.print(f"  {f}      ({lang})")
    console.print()


def _expand_code_lines(code: str) -> str:
    """코드 문자열의 리터럴 \\n과 { } 내 ; 구분을 실제 줄바꿈으로 변환한다."""
    import re
    # 리터럴 \n → 줄바꿈
    expanded = code.replace("\\n", "\n")
    # { stmt1; stmt2; } → 줄바꿈 분리
    # { 뒤에 줄바꿈
    expanded = re.sub(r"\{\s*", "{\n    ", expanded)
    # ; 뒤에 줄바꿈 (단, 문자열 리터럴 내부 제외)
    expanded = re.sub(r";\s*(?![\s\n]*})", ";\n    ", expanded)
    # } 앞에 줄바꿈
    expanded = re.sub(r"\s*}", "\n}", expanded)
    return expanded


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
                before_expanded = _expand_code_lines(before)
                content.append("\n  - Before:\n", style="red")
                for bline in before_expanded.strip().splitlines():
                    content.append(f"    {bline}\n", style="red")

            if after:
                after_expanded = _expand_code_lines(after)
                content.append("  + After:\n", style="green")
                for aline in after_expanded.strip().splitlines():
                    content.append(f"    {aline}\n", style="green")

        if description:
            content.append(f"\n  {description}\n")

        console.print(Panel(content, border_style="dim"))


def get_output_prefix(files: list[str]) -> str:
    """분석 대상 파일명과 현재 일시를 결합한 파일명 접두사를 반환한다."""
    if not files:
        base_name = "analysis"
    else:
        # 첫 번째 파일의 이름을 대표 이름으로 사용 (확장자 제외)
        try:
            base_name = Path(files[0]).stem
        except Exception:
            base_name = "analysis"
            
    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    return f"{base_name}_{timestamp}_"


def print_summary(
    console: Console,
    summary: dict[str, Any],
    output_dir: str,
    source_files: list[str],
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

    if deployment_risk == "UNABLE_TO_ANALYZE":
        console.print(f"\n배포 판정: [yellow bold]분석불가[/] (분석 중 오류 발생)")
        risk_desc = risk.get("risk_description", "")
        if risk_desc:
            console.print(f"  사유: {risk_desc[:200]}")
    elif deployment_allowed:
        console.print(f"\n배포 판정: [green bold]가능[/] ({deployment_risk})")
    else:
        blocking = risk.get("blocking_issues", [])
        reason = f"Critical {by_severity.get('critical', 0)}건"
        if by_severity.get("high", 0) >= 3:
            reason += f", High {by_severity.get('high', 0)}건"
        console.print(
            f"\n배포 판정: [red bold]위험[/] ({reason})"
        )
        if blocking:
            console.print(f"  차단 이슈: {', '.join(blocking[:5])}")

    # 출력 파일 경로
    prefix = get_output_prefix(source_files)
    
    console.print(f"\n출력 디렉토리: {output_dir}")
    console.print(f"      {prefix}issue-list.json")
    console.print(f"      {prefix}checklist.json")
    console.print(f"      {prefix}summary.json")
    console.print(f"      {prefix}deployment-checklist.json")
    console.print(f"      {prefix}report.md")


def _format_duration(seconds: float) -> str:
    """초 단위 시간을 'N분 N초' 형식으로 변환한다."""
    if seconds < 60:
        return f"{seconds:.0f}초"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes}분 {secs}초"


def print_analysis_stats(
    console: Console,
    stats: dict[str, Any] | list[dict[str, Any]],
) -> None:
    """분석 요약 메트릭을 출력한다.

    Args:
        console: Rich Console 인스턴스
        stats: 단일 stats dict 또는 언어별 stats 리스트
    """
    # 하위 호환: dict → 리스트로 변환
    if isinstance(stats, dict):
        stats_list = [stats] if stats else []
    else:
        stats_list = stats

    for s in stats_list:
        if not s or not s.get("delivery_mode"):
            continue
        _print_single_stats(console, s)


def _print_single_stats(
    console: Console,
    stats: dict[str, Any],
) -> None:
    """단일 언어의 분석 요약 메트릭을 출력한다."""
    mode = stats["delivery_mode"]
    total_time = stats.get("analysis_time_seconds", 0.0)
    total_tokens = stats.get("total_tokens", 0)
    total_lines = stats.get("total_lines", 0)
    language = stats.get("language", "")

    header = f"\n[bold]{language} 분석 요약[/]" if language else "\n[bold]분석 요약[/]"
    console.print(header)
    console.print(f"  모드: {mode}")
    console.print(f"  총 분석시간: {_format_duration(total_time)}")

    if mode == "v3_pipeline":
        # V3 파이프라인 전용 요약
        console.print(f"  총 토큰: {total_tokens:,}")
        console.print(f"  총 line 수: {total_lines:,}줄")
        v3_findings = stats.get("v3_findings", {})
        if v3_findings:
            ct_count = v3_findings.get('clang_tidy', 0)
            ct_label = f" (clang-tidy {ct_count}개 포함)" if ct_count else ""
            console.print(
                f"  Findings: 정적 {v3_findings.get('static', 0)}개{ct_label} + "
                f"교차 {v3_findings.get('cross', 0)}개 → "
                f"LLM {v3_findings.get('llm_output', 0)}개 → "
                f"최종 {v3_findings.get('final', 0)}개"
            )
        phase_ms = stats.get("v3_phase_ms", {})
        if phase_ms:
            console.print(
                f"  Phase별 소요: "
                f"파티셔닝 {phase_ms.get('partition', 0)}ms, "
                f"그래프 {phase_ms.get('graph', 0)}ms, "
                f"정적 {phase_ms.get('static', 0)}ms, "
                f"교차 {phase_ms.get('cross', 0)}ms, "
                f"LLM {phase_ms.get('llm', 0)}ms, "
                f"병합 {phase_ms.get('merge', 0)}ms"
            )
    elif mode == "grouped":
        gs = stats.get("group_stats", [])
        n = stats.get("total_groups", len(gs))
        console.print(f"  총 그룹 수: {n}")
        console.print(f"  총 토큰: {total_tokens:,}")
        if n > 0 and gs:
            console.print(f"  그룹당 평균 토큰: {total_tokens // n:,}")
            times = [g["elapsed_seconds"] for g in gs]
            avg_time = sum(times) / len(times)
            console.print(f"  그룹당 평균 분석시간: {_format_duration(avg_time)}")
            lines_list = [g["lines"] for g in gs]
            avg_lines = sum(lines_list) // len(lines_list)
            console.print(f"  그룹당 평균 line 수: {avg_lines}줄")
            console.print(
                f"  그룹 line 수 범위: {min(lines_list)}~{max(lines_list)}줄"
            )
    else:
        console.print(f"  총 토큰: {total_tokens:,}")
        console.print(f"  총 line 수: {total_lines}줄")


def write_output_files(
    output_dir: str,
    result: dict[str, Any],
    source_files: list[str],
) -> None:
    """분석 결과를 JSON 파일로 출력한다."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    prefix = get_output_prefix(source_files)

    files_to_write = {
        f"{prefix}issue-list.json": result.get("issue_list", {}),
        f"{prefix}checklist.json": result.get("checklist", {}),
        f"{prefix}summary.json": result.get("summary", {}),
        f"{prefix}deployment-checklist.json": result.get("deployment_checklist", {}),
    }

    for filename, data in files_to_write.items():
        filepath = out_path / filename
        filepath.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info(f"출력 파일 생성: {filepath}")

    # Markdown 리포트 생성
    md_content = format_markdown_report(
        issue_list=result.get("issue_list", {}),
        checklist=result.get("checklist", {}),
        summary=result.get("summary", {}),
        deployment_checklist=result.get("deployment_checklist", {}),
        source_files=source_files,
        json_filenames=list(files_to_write.keys()),
    )
    md_filepath = out_path / f"{prefix}report.md"
    md_filepath.write_text(md_content, encoding="utf-8")
    logger.info(f"출력 파일 생성: {md_filepath}")


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
    verbose: bool = False,
) -> int:
    """분석 파이프라인을 실행한다.

    Returns:
        종료 코드 (0, 1, 2, 3)
    """
    progress_callback = _create_progress_callback(console)
    reasoning_logger = ReasoningLogger(console=console, verbose=verbose)

    orchestrator = OrchestratorAgent(
        model=model,
        progress_callback=progress_callback,
        reasoning_logger=reasoning_logger,
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
        write_output_files(output_dir, result, files)
        return EXIT_FILE_ERROR

    # 결과 출력
    write_output_files(output_dir, result, files)
    print_issues(console, issue_list)
    print_summary(console, result.get("summary", {}), output_dir, files)
    print_analysis_stats(console, result.get("analysis_stats", []))

    return determine_exit_code(result)


def prompt_for_files() -> list[str]:
    """인터랙티브 모드: 사용자에게 파일명을 입력받는다."""
    print("\n분석하고자 하는 소스파일을 입력해주세요. 예시) ordsb0100010t01.c")
    try:
        user_input = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)

    if not user_input:
        print("파일명이 입력되지 않았습니다. 종료합니다.")
        sys.exit(0)

    return [f.strip() for f in user_input.split(",") if f.strip()]


def main() -> None:
    """CLI 메인 함수."""
    base_dir = get_base_dir()
    # .env 파일 로드
    # PyInstaller onefile 모드: 번들된 .env는 _MEIPASS 임시 디렉토리에 풀림
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        env_path = meipass / ".env"
    else:
        env_path = base_dir / ".env"
    load_dotenv(dotenv_path=env_path)

    parser = build_parser(output_default=str(base_dir / "output"))
    args = parser.parse_args()

    # 로깅 설정
    log_level = "DEBUG" if args.verbose else None
    setup_logging(log_level)

    console = Console()
    console.print(f"Mider v{__version__}")

    # API 키 검증
    validate_api_key(sso_mode=getattr(args, "sso", False))

    # SSO 인증
    if getattr(args, "sso", False):
        try:
            from mider.config.sso_auth import SSOAuthenticator
        except ImportError:
            console.print(
                "[red bold]오류:[/] SSO 로그인에 selenium이 필요합니다.\n"
                "  pip install selenium\n"
                "또는 AICA_SSO_SESSION 환경변수를 직접 설정하세요."
            )
            sys.exit(EXIT_LLM_ERROR)

        auth = SSOAuthenticator(
            base_url=os.environ.get("AICA_ENDPOINT", ""),
        )
        try:
            creds = auth.authenticate()
        except Exception as e:
            console.print(f"[red bold]SSO 인증 실패:[/] {e}")
            sys.exit(EXIT_LLM_ERROR)
        os.environ["AICA_SSO_SESSION"] = creds.sso_session
        os.environ["AICA_USER_ID"] = creds.user_id
        console.print(f"[green]\\[OK][/] SSO 로그인: {creds.user_id} ({creds.name})")

    # 모델 결정
    model = resolve_model(args.model)

    # -f 인자가 있으면 사용, 없으면 인터랙티브 모드
    file_args = args.files if args.files else prompt_for_files()

    # 파일 경로 해석 (input 폴더 + workspace 재귀 검색)
    resolved_files = resolve_input_files(base_dir, file_args)

    # 파일 목록 출력
    print_file_list(console, resolved_files)

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
                files=resolved_files,
                output_dir=args.output,
                model=model,
                console=console,
                explain_plan=explain_plan,
                verbose=args.verbose,
            )
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]분석이 사용자에 의해 중단되었습니다.[/]")
        sys.exit(130)
    except (APIError, APIConnectionError, RateLimitError, APITimeoutError, AICAError, httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException, EnvironmentError) as e:
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
