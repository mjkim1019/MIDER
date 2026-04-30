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
from mider.config.llm_client import AICAError, AICASessionExpiredError
from mider.config.logging_config import setup_logging
from mider.config.reasoning_logger import ReasoningLogger
from mider.tools.utility.console_styles import print_verbose_error, rainbow_text
from mider.tools.utility.markdown_report_formatter import format_markdown_report

logger = logging.getLogger(__name__)


# 종료 코드
EXIT_OK = 0
EXIT_CRITICAL_FOUND = 1
EXIT_FILE_ERROR = 2
EXIT_LLM_ERROR = 3

# 1회 분석 시 허용할 최대 파일 수
# (LLM 호출 비용/시간/안정성 보호)
MAX_FILES_PER_RUN = 20

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
        try:
            matches = list(base_dir.rglob(name))
        except (ValueError, OSError):
            matches = []
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
        return []

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
        "--check",
        action="store_true",
        help="도구 상태 점검 (ESLint, clang-tidy, config, LLM)",
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


def validate_api_key() -> None:
    """LLM API 키를 검증한다 (provider에 따라 분기).

    API_PROVIDER 환경 변수:
    - "openai" (기본): AZURE_OPENAI_API_KEY 또는 OPENAI_API_KEY
    - "aica": AICA_ENDPOINT (AICA_API_KEY는 백엔드에서 설정)

    Raises:
        SystemExit: 필수 환경 변수가 없으면 exit code 3으로 종료
    """
    provider = os.environ.get("API_PROVIDER", "openai").lower()

    if provider == "aica":
        endpoint = os.environ.get("AICA_ENDPOINT", "")
        if endpoint:
            return
        console = Console(stderr=True)
        console.print("[red bold]오류:[/] AICA_ENDPOINT가 설정되지 않았습니다.")
        console.print("  AICA_ENDPOINT를 환경 변수로 설정하세요.")
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

    # 배포 판정 (전체)
    deployment_risk = risk.get("deployment_risk", "LOW")
    deployment_allowed = risk.get("deployment_allowed", True)

    if deployment_risk == "UNABLE_TO_ANALYZE":
        console.print(f"\n배포 판정 (전체): [yellow bold]분석불가[/] (분석 중 오류 발생)")
        risk_desc = risk.get("risk_description", "")
        if risk_desc:
            console.print(f"  사유: {risk_desc[:200]}")
    elif deployment_allowed:
        console.print(f"\n배포 판정 (전체): [green bold]가능[/] ({deployment_risk})")
    else:
        blocking = risk.get("blocking_issues", [])
        reason = f"Critical {by_severity.get('critical', 0)}건"
        if by_severity.get("high", 0) >= 3:
            reason += f", High {by_severity.get('high', 0)}건"
        console.print(
            f"\n배포 판정 (전체): [red bold]위험[/] ({reason})"
        )
        if blocking:
            console.print(f"  차단 이슈: {', '.join(blocking[:5])}")

    # 파일별 배포 판정 (다중 파일 분석 시)
    by_file_risk = risk.get("by_file") or []
    if by_file_risk:
        console.print(f"\n배포 판정 (파일별):")
        for item in by_file_risk:
            fpath = item.get("file", "")
            file_risk = item.get("deployment_risk", "")
            file_allowed = item.get("deployment_allowed", False)
            crit_n = item.get("critical_count", 0)
            high_n = item.get("high_count", 0)
            med_n = item.get("medium_count", 0)
            if file_risk == "UNABLE_TO_ANALYZE":
                tag = "[yellow bold]분석불가[/]"
            elif file_allowed:
                tag = "[green bold]가능[/]"
            else:
                tag = "[red bold]위험[/]"
            counts = f"C{crit_n}/H{high_n}/M{med_n}"
            console.print(
                f"  {tag} ({file_risk}) [{counts}] [dim]{fpath}[/]"
            )

    # 출력 파일 경로
    prefix = get_output_prefix(source_files)

    console.print(f"\n출력 파일: {output_dir}/{prefix}report.md")


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
    """분석 결과를 Markdown 리포트로 출력한다."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    prefix = get_output_prefix(source_files)

    # Markdown 리포트 생성
    md_content = format_markdown_report(
        issue_list=result.get("issue_list", {}),
        checklist=result.get("checklist", {}),
        summary=result.get("summary", {}),
        deployment_checklist=result.get("deployment_checklist", {}),
        source_files=source_files,
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

    # LLM 분석 실패 파일 감지
    analysis_errors = result.get("analysis_errors", [])
    if analysis_errors:
        console.print()
        console.print("[red bold]LLM 분석 실패:[/]")
        for ae in analysis_errors:
            fname = Path(ae["file"]).name
            console.print(f"  [red]- {fname}:[/] {ae['error']}")
        console.print(
            "\n[yellow bold]분석 실패 — LLM이 정상 응답하지 못해 결과를 신뢰할 수 없습니다.[/]"
        )
        write_output_files(output_dir, result, files)
        return EXIT_LLM_ERROR

    # 결과 출력
    write_output_files(output_dir, result, files)
    print_issues(console, issue_list)
    print_summary(console, result.get("summary", {}), output_dir, files)
    print_analysis_stats(console, result.get("analysis_stats", []))

    return determine_exit_code(result)


def _run_sso_login(console: Console, force: bool = False) -> bool:
    """SSO 브라우저 로그인을 실행하고 환경변수에 세션을 설정한다.

    Args:
        console: Rich Console
        force: True이면 캐시 파일을 삭제하고 무조건 브라우저 로그인 수행

    Returns:
        True이면 로그인 성공
    """
    try:
        from mider.config.sso_auth import SSOAuthenticator
    except ImportError:
        console.print(
            "[red bold]오류:[/] SSO 로그인에 websocket-client가 필요합니다.\n"
            "  pip install websocket-client\n"
            "또는 AICA_SSO_SESSION 환경변수를 직접 설정하세요."
        )
        return False

    auth = SSOAuthenticator(
        base_url=os.environ.get("AICA_ENDPOINT", ""),
    )
    if force:
        # 서버에서는 만료됐는데 로컬 캐시가 아직 유효할 수 있으므로 선제 삭제
        auth.invalidate_session()
    try:
        creds = auth.authenticate(force_login=force)
    except Exception as e:
        console.print(f"[red bold]SSO 인증 실패:[/] {e}")
        return False

    os.environ["AICA_SSO_SESSION"] = creds.sso_session
    os.environ["AICA_USER_ID"] = creds.user_id
    console.print(f"[green]\\[OK][/] SSO 로그인: {creds.user_id} ({creds.name})")
    return True


def _check_sso_session(console: Console) -> None:
    """AICA provider일 때 SSO 세션을 확보한다.

    캐시된 세션 → 환경변수 설정 후 즉시 반환.
    세션 없음 → 브라우저 로그인 성공할 때까지 반복 (사용자 Ctrl+C로 종료 가능).
    """
    provider = os.environ.get("API_PROVIDER", "openai").lower()
    if provider != "aica":
        return

    sso_session = os.environ.get("AICA_SSO_SESSION", "")
    if sso_session:
        return

    # 캐시된 세션 파일 확인
    try:
        from mider.config.sso_auth import SSOAuthenticator
        auth = SSOAuthenticator(
            base_url=os.environ.get("AICA_ENDPOINT", ""),
        )
        cached = auth._load_session()
        if cached:
            os.environ["AICA_SSO_SESSION"] = cached.sso_session
            os.environ["AICA_USER_ID"] = cached.user_id
            console.print(
                f"[green]\\[OK][/] 기존 SSO 세션 재사용: {cached.user_id}"
            )
            return
    except Exception:
        pass

    # 세션 없음 → 로그인 성공할 때까지 반복
    console.print(
        "[yellow]SSO 세션이 없습니다. 로그인이 필요합니다.[/]"
    )
    while True:
        if _run_sso_login(console):
            return
        # 로그인 실패 → 재시도 또는 종료
        console.print(
            "\n[yellow]SSO 로그인에 실패했습니다.[/]"
        )
        console.print("Enter 키를 눌러 다시 시도하거나, 'exit'를 입력하여 종료하세요.")
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if user_input.lower() in ("exit", "quit", "q"):
            sys.exit(0)


def _print_input_help() -> None:
    """입력 도움말을 출력한다."""
    print("\n[도움말]")
    print("  소스파일명을 입력하세요 (지원: .xml, .c, .h, .pc, .sql, .js)")
    print("  여러 파일: 쉼표로 구분 (예: file1.c, file2.xml)")
    print("  명령어: login (SSO 재로그인) | exit (종료) | help (도움말)")
    print()


def prompt_for_files(console: Console, *, is_repeat: bool = False) -> list[str] | None:
    """인터랙티브 모드: 사용자에게 파일명을 입력받는다.

    'login' 입력 시 SSO 로그인을 실행한 후 다시 파일명을 입력받는다.
    'exit' 또는 'quit' 입력 시 None을 반환하여 종료를 알린다.

    Args:
        console: Rich Console 인스턴스
        is_repeat: True이면 '다시 분석하고자 하는' 문구 사용

    Returns:
        파일명 리스트 또는 None (종료 요청)
    """
    if is_repeat:
        print("\n다시 분석하고자 하는 소스파일을 입력해주세요.")
    else:
        print("\n분석하고자 하는 소스파일을 입력해주세요.")
    print("(예: ZORDSB0100010.xml, payspmt10050t04.c, zinvbreps8030.pc / 현위치의 하위 폴더는 모두 접근가능합니다)")
    print("SSO 로그인: login  |  종료: exit")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        if not user_input:
            print("파일명을 입력해주세요.")
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            return None

        if user_input.lower() == "login":
            # 사용자가 명시적으로 login을 요청한 경우 — 캐시를 무시하고 항상 새로 로그인
            _run_sso_login(console, force=True)
            print("\n분석하고자 하는 소스파일을 입력해주세요.")
            print("(예: ZORDSB0100010.xml, payspmt10050t04.c, zinvbreps8030.pc / 현위치의 하위 폴더는 모두 접근가능합니다)")
            continue

        if user_input.lower() in ("help", "h", "?"):
            _print_input_help()
            continue

        if user_input.lower() == "log_on":
            from mider.config.debug_logger import enable as _enable_debug
            log_dir = _enable_debug(get_base_dir())
            console.print(f"[green]디버그 로그 활성화: {log_dir}[/]")
            continue

        if user_input.lower() == "log_off":
            from mider.config.debug_logger import disable as _disable_debug
            _disable_debug()
            console.print("[yellow]디버그 로그 비활성화[/]")
            continue

        # hidden: "alswn chlrh" — verbose error 모드 토글 (API 오류 시 전체 traceback 출력)
        if " ".join(user_input.lower().split()) == "alswn chlrh":
            from mider.config.debug_logger import (
                disable_verbose_errors,
                enable_verbose_errors,
                is_verbose_errors,
            )
            if is_verbose_errors():
                disable_verbose_errors()
                console.print(rainbow_text("디버깅 모드 종료"))
            else:
                enable_verbose_errors()
                console.print(rainbow_text("디버깅 모드 시작"))
            continue

        # 파일명 파싱
        filenames = [f.strip() for f in user_input.split(",") if f.strip()]

        # 유효한 소스 파일 확장자 검사
        valid_exts = {".xml", ".c", ".h", ".pc", ".sql", ".js"}
        has_valid = any(
            Path(f).suffix.lower() in valid_exts for f in filenames
        )
        if not has_valid:
            print(f"인식할 수 없는 입력입니다: {user_input}")
            _print_input_help()
            continue

        return filenames


def prompt_for_explain_plan(
    resolved_files: list[str],
    base_dir: Path,
) -> str | None:
    """SQL 파일이 포함되어 있으면 Explain Plan 파일 경로를 질문한다.

    Args:
        resolved_files: resolve_input_files()로 해석된 파일 목록
        base_dir: 파일 경로 해석 기준 디렉토리

    Returns:
        Explain Plan 파일 절대경로, 또는 None (SQL 없거나 Enter 입력 시)
    """
    has_sql = any(Path(f).suffix.lower() == ".sql" for f in resolved_files)
    if not has_sql:
        return None

    print("\nℹ SQL 파일이 포함되어 있습니다.")
    print("  Explain Plan 파일이 있으면 입력하세요 (없으면 Enter):")
    try:
        user_input = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not user_input:
        return None

    # 파일 경로 해석 (resolve_input_files와 동일 로직)
    p = Path(user_input)
    if p.exists():
        return str(p.resolve())

    # base_dir 기준 검색
    input_path = base_dir / "input" / user_input
    if input_path.exists():
        return str(input_path.resolve())

    # workspace 재귀 검색
    matches = list(base_dir.rglob(user_input))
    if len(matches) == 1:
        return str(matches[0].resolve())

    print(f"  ⚠ Explain Plan 파일을 찾을 수 없습니다: {user_input}")
    print("  Explain Plan 없이 분석을 계속합니다.")
    return None


def _set_console_icon() -> None:
    """Windows 콘솔 창 + 작업표시줄 아이콘을 EXE 내장 아이콘으로 설정한다."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # 1) AppUserModelID 설정 — 작업표시줄에서 conhost가 아닌 독립 앱으로 인식
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SKT.Mider.CLI")

        # 2) 콘솔 창 핸들 획득
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return

        # 3) EXE에서 아이콘 추출
        hicon_large = ctypes.c_void_p()
        hicon_small = ctypes.c_void_p()
        ctypes.windll.shell32.ExtractIconExW(
            sys.executable, 0,
            ctypes.byref(hicon_large),
            ctypes.byref(hicon_small),
            1,
        )

        # 4) 콘솔 창에 아이콘 설정 (제목 표시줄 + 작업표시줄)
        WM_SETICON = 0x0080
        user32 = ctypes.windll.user32
        if hicon_small.value:
            user32.SendMessageW(hwnd, WM_SETICON, 0, hicon_small.value)
        if hicon_large.value:
            user32.SendMessageW(hwnd, WM_SETICON, 1, hicon_large.value)
    except Exception:
        pass  # 아이콘 설정 실패해도 프로그램 실행에 영향 없음


def main() -> None:
    """CLI 메인 함수."""
    _set_console_icon()
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

    # --check: 도구 상태 점검 모드
    if args.check:
        from mider.healthcheck import run_healthcheck
        sys.exit(run_healthcheck(console))

    # API 키 검증
    validate_api_key()

    # AICA SSO 세션 자동 체크
    _check_sso_session(console)

    # 모델 결정
    model = resolve_model(args.model)

    # -f 인자가 있으면 1회 실행 후 종료, 없으면 인터랙티브 반복 모드
    is_interactive = not args.files

    if not is_interactive:
        # CLI 모드: 1회 실행 후 종료
        exit_code = _run_once(
            file_args=args.files,
            base_dir=base_dir,
            output_dir=args.output,
            model=model,
            console=console,
            explain_plan=getattr(args, "explain_plan", None),
            verbose=args.verbose,
            is_interactive=False,
        )
        sys.exit(exit_code)

    # 인터랙티브 모드: 반복 실행
    is_first = True
    while True:
        file_args = prompt_for_files(console, is_repeat=not is_first)
        if file_args is None:
            console.print("[green]Mider를 종료합니다.[/]")
            sys.exit(EXIT_OK)

        _run_once(
            file_args=file_args,
            base_dir=base_dir,
            output_dir=args.output,
            model=model,
            console=console,
            explain_plan=None,
            verbose=args.verbose,
            is_interactive=True,
        )
        is_first = False


def _run_once(
    *,
    file_args: list[str],
    base_dir: Path,
    output_dir: str,
    model: str,
    console: Console,
    explain_plan: str | None,
    verbose: bool,
    is_interactive: bool,
) -> int:
    """1회 분석을 실행한다.

    Returns:
        종료 코드 (0, 1, 2, 3)
    """
    # 파일 경로 해석
    resolved_files = resolve_input_files(base_dir, file_args)
    if not resolved_files:
        return EXIT_FILE_ERROR

    # 1회 분석 최대 파일 수 제한 (LLM 비용/시간/안정성 보호)
    if len(resolved_files) > MAX_FILES_PER_RUN:
        console.print(
            f"[red bold]오류:[/] 1회 분석 가능한 파일 수는 최대 "
            f"{MAX_FILES_PER_RUN}개입니다. (입력: {len(resolved_files)}개)"
        )
        console.print(
            "[yellow]대상을 줄이거나 여러 번 나누어 실행하세요.[/]"
        )
        logger.warning(
            f"파일 수 한도 초과: {len(resolved_files)} > {MAX_FILES_PER_RUN}"
        )
        return EXIT_FILE_ERROR

    # 파일 목록 출력
    print_file_list(console, resolved_files)

    # Explain Plan 파일 결정
    if not explain_plan and is_interactive:
        explain_plan = prompt_for_explain_plan(resolved_files, base_dir)
    if explain_plan and not Path(explain_plan).exists():
        console.print(
            f"[red bold]오류:[/] Explain Plan 파일 없음: {explain_plan}",
        )
        return EXIT_FILE_ERROR

    # 분석 실행
    try:
        exit_code = asyncio.run(
            run_analysis(
                files=resolved_files,
                output_dir=output_dir,
                model=model,
                console=console,
                explain_plan=explain_plan,
                verbose=verbose,
            )
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]분석이 사용자에 의해 중단되었습니다.[/]")
        return 130
    except AICASessionExpiredError:
        logger.warning("SSO 세션 만료 — 분석 중단")
        console.print("\n[yellow bold]SSO 세션이 만료되었습니다. 재로그인합니다...[/]")
        _run_sso_login(console, force=True)
        console.print("[green]재로그인 완료. 파일을 다시 입력해주세요.[/]")
        return EXIT_LLM_ERROR
    except (APIError, APIConnectionError, RateLimitError, APITimeoutError, AICAError, httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException, EnvironmentError) as e:
        logger.error(f"LLM API 오류: {e}")
        console.print(f"[red bold]LLM API 오류:[/] {e}")
        print_verbose_error(console, e)
        return EXIT_LLM_ERROR
    except Exception as e:
        logger.error(f"분석 중 오류 발생: {e}")
        console.print(f"[red bold]오류:[/] {e}")
        print_verbose_error(console, e)
        return EXIT_FILE_ERROR

    return exit_code


if __name__ == "__main__":
    main()
