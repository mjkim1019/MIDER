"""CAnalyzerAgent: Phase 2 - C 언어 분석.

clang-tidy 정적분석 + LLM 심층분석을 결합하여
C 파일의 메모리 안전성 및 장애 유발 패턴을 탐지한다.

clang-tidy 없고 대형 파일(>500줄)이면 2-Pass 전략:
  Pass 1: Heuristic Pre-Scanner → mini 모델로 위험 함수 선별
  Pass 2: 선별된 함수 → primary 모델로 심층 분석
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from mider.agents.base_agent import BaseAgent
from mider.config.prompt_loader import load_prompt
from mider.config.settings_loader import (
    get_agent_fallback_model,
    get_agent_model,
    get_agent_temperature,
)
from mider.models.analysis_result import AnalysisResult
from mider.tools.file_io.file_reader import FileReader
from mider.tools.static_analysis.c_heuristic_scanner import CHeuristicScanner
from mider.tools.static_analysis.clang_tidy_runner import ClangTidyRunner
from mider.tools.utility.token_optimizer import (
    build_structure_summary,
    extract_error_functions,
    optimize_file_content,
    find_function_boundaries,
)

logger = logging.getLogger(__name__)

# clang-tidy 헤더 누락 에러 판정 키워드 (메시지 소문자 매칭)
_HEADER_ERROR_KEYWORDS = frozenset({
    "file not found",
    "unknown type name",
    "use of undeclared identifier",
    "no such file or directory",
})

# Level 2 체크 접두사 — 데이터 흐름 분석 (AST 완성 필요, 헤더 없으면 동작 불가)
_LEVEL2_CHECK_PREFIX = "clang-analyzer-"


def _is_header_error(warning: dict[str, Any]) -> bool:
    """clang-tidy 경고가 헤더 누락으로 인한 컴파일 에러인지 판정한다."""
    if warning.get("severity") != "error":
        return False
    message = warning.get("message", "").lower()
    return any(kw in message for kw in _HEADER_ERROR_KEYWORDS)


def _is_level2_warning(warning: dict[str, Any]) -> bool:
    """clang-tidy Level 2(데이터 흐름 분석) 경고인지 판정한다.

    Level 2: clang-analyzer-* — AST 완성이 필요, 헤더 없으면 동작 불가.
    Level 1: bugprone-*, cert-*, misc-* 등 — 텍스트/구문 패턴, 헤더 없이도 동작.
    """
    check = warning.get("check", "")
    return check.startswith(_LEVEL2_CHECK_PREFIX)


# ──────────────────────────────────────────────
# 이슈 후처리: 동일 패턴 병합 + 노이즈 제거
# ──────────────────────────────────────────────

# 중복 병합 그룹 키워드 (title 소문자에서 매칭)
_DEDUP_GROUPS: list[tuple[str, list[str]]] = [
    ("strncpy 널 종료", ["strncpy", "널 종료", "null 종료", "strlcpy"]),
    ("NULL 체크 누락", ["null 체크", "null 검증", "유효성 검증", "널 체크"]),
    ("ix 미초기화", ["ix 변수", "ix 미초기화"]),
]

# 자동 제거 키워드 (title 소문자에서 매칭 → 이슈 삭제)
_REMOVE_KEYWORDS: list[str] = [
    "스레드 안전", "동기화 부재", "경쟁 상태", "race condition",
    "mutex", "동시 접근", "스레드 안전성",
    "데이터 레이스", "동시성", "요청 간 공유", "멀티스레드",
    "race", "concurrent", "동기화 누락",
    "안전 대안", "관례 개선", "memset_s",
]

# severity 우선순위 (높을수록 우선)
_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def _deduplicate_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """함수별 LLM 결과에서 동일 패턴 이슈를 병합하고 노이즈를 제거한다.

    1. 스레드 안전성 이슈 자동 제거 (Proframe 단일스레드)
    2. 동일 패턴 그룹의 이슈를 대표 1건으로 병합
       - severity가 가장 높은 것 유지
       - description에 "(외 N곳 동일 패턴)" 추가
    3. 동일 변수명 + 동일 카테고리 이슈 병합
    """
    # Step 1: 스레드 안전성 제거
    filtered: list[dict[str, Any]] = []
    for issue in issues:
        title_lower = issue.get("title", "").lower()
        if any(kw in title_lower for kw in _REMOVE_KEYWORDS):
            continue
        filtered.append(issue)

    # Step 2: 키워드 그룹 병합
    group_map: dict[str, list[dict[str, Any]]] = {}
    ungrouped: list[dict[str, Any]] = []

    for issue in filtered:
        title_lower = issue.get("title", "").lower()
        matched_group = None
        for group_name, keywords in _DEDUP_GROUPS:
            if any(kw in title_lower for kw in keywords):
                matched_group = group_name
                break
        if matched_group:
            group_map.setdefault(matched_group, []).append(issue)
        else:
            ungrouped.append(issue)

    # 각 그룹에서 대표 1건 선택
    merged: list[dict[str, Any]] = []
    for group_name, group_issues in group_map.items():
        if not group_issues:
            continue
        # severity 최고 우선
        group_issues.sort(
            key=lambda x: _SEVERITY_RANK.get(x.get("severity", "low"), 0),
            reverse=True,
        )
        representative = group_issues[0].copy()
        if len(group_issues) > 1:
            representative["description"] += (
                f" (외 {len(group_issues) - 1}곳 동일 패턴)"
            )
        merged.append(representative)

    # Step 3: 동일 변수 + 동일 카테고리 병합 (ungrouped 중)
    var_dedup: dict[str, list[dict[str, Any]]] = {}
    final_ungrouped: list[dict[str, Any]] = []

    for issue in ungrouped:
        title_lower = issue.get("title", "").lower()
        category = issue.get("category", "")
        # "svc_cnt", "currsvclist_s", "out_04" 등 변수명 + 카테고리로 그룹
        var_key = None
        for var in ["svc_cnt", "currsvclist_s", "out_04", "g_chg_psbl_flag"]:
            if var in title_lower:
                var_key = f"{var}_{category}"
                break
        if var_key:
            var_dedup.setdefault(var_key, []).append(issue)
        else:
            final_ungrouped.append(issue)

    for var_key, var_issues in var_dedup.items():
        if not var_issues:
            continue
        var_issues.sort(
            key=lambda x: _SEVERITY_RANK.get(x.get("severity", "low"), 0),
            reverse=True,
        )
        representative = var_issues[0].copy()
        if len(var_issues) > 1:
            representative["description"] += (
                f" (외 {len(var_issues) - 1}곳 동일 패턴)"
            )
        merged.append(representative)

    result = merged + final_ungrouped

    # severity 순 정렬
    result.sort(
        key=lambda x: _SEVERITY_RANK.get(x.get("severity", "low"), 0),
        reverse=True,
    )

    return result


class CAnalyzerAgent(BaseAgent):
    """Phase 2: C 파일을 분석하는 Agent.

    clang-tidy 정적분석 결과를 기반으로 LLM이 심층 분석하여
    Error-Focused 또는 Heuristic 경로로 이슈를 탐지한다.
    """

    def __init__(
        self,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        _name = "c_analyzer"
        model = model or get_agent_model(_name)
        fallback_model = fallback_model or get_agent_fallback_model(_name)
        temperature = temperature if temperature is not None else get_agent_temperature(_name)
        super().__init__(
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
        )
        self._file_reader = FileReader()
        self._clang_tidy_runner = ClangTidyRunner()
        self._heuristic_scanner = CHeuristicScanner()

    async def run(
        self,
        *,
        task_id: str,
        file: str,
        language: str = "c",
        file_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """C 파일을 분석한다.

        Args:
            task_id: ExecutionPlan의 task_id
            file: 분석할 파일 경로
            language: 파일 언어 ("c")
            file_context: Phase 1에서 수집한 파일 컨텍스트

        Returns:
            AnalysisResult 형식의 딕셔너리
        """
        start_time = time.time()
        logger.info(f"C 분석 시작: {file}")

        try:
            # Step 1: 파일 읽기
            read_result = self._file_reader.execute(path=file)
            file_content = read_result.data["content"]
            line_count = len(file_content.splitlines())
            self.rl.scan(f"File: [sky_blue2]{Path(file).name}[/sky_blue2] ({line_count}줄, ~{line_count * 10 // 1000}K tokens)")

            # Step 2: clang-tidy 정적분석 (내부에서 추론 로그 출력)
            clang_data = self._run_clang_tidy(file)

            # Step 3: 분석 경로 선택
            tokens_estimate = 0
            if not clang_data and line_count > 500:
                # 2-Pass 전략: clang-tidy 없고 대형 파일
                self.rl.decision(
                    "Decision: 2-Pass 전략",
                    reason=f"clang-tidy 없음 + {line_count}줄(>500)",
                )
                issues = await self._run_two_pass(
                    file=file,
                    file_content=file_content,
                    file_context=file_context,
                )
            else:
                # 기존 경로: clang-tidy 있음 or 500줄 이하
                if clang_data:
                    w_count = len(clang_data.get("warnings", []))
                    self.rl.decision(
                        "Decision: Error-Focused path",
                        reason=f"clang-tidy {w_count}건 유의미 경고",
                    )
                else:
                    self.rl.decision(
                        "Decision: Heuristic path",
                        reason=f"clang-tidy 없음 + {line_count}줄(≤500) → 전체 코드 LLM 검증",
                    )
                prompt, messages = self._build_messages(
                    file=file,
                    file_content=file_content,
                    clang_data=clang_data,
                    file_context=file_context,
                )

                response = await self.call_llm(messages, json_mode=True)
                llm_result = json.loads(response)

                if not isinstance(llm_result, dict):
                    raise ValueError(f"LLM 응답이 dict가 아님: {type(llm_result)}")

                issues = llm_result.get("issues", [])
                tokens_estimate = (len(prompt) + len(response)) // 4

            # Step 4: AnalysisResult 생성
            elapsed = time.time() - start_time

            result = AnalysisResult.model_validate({
                "task_id": task_id,
                "file": file,
                "language": language,
                "agent": "CAnalyzerAgent",
                "issues": issues,
                "analysis_time_seconds": round(elapsed, 2),
                "llm_tokens_used": tokens_estimate,
            })

            logger.info(
                f"C 분석 완료: {file} → {len(result.issues)}개 이슈, "
                f"{result.analysis_time_seconds}초"
            )

            return result.model_dump()

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"C 분석 실패: {file}: {e}")
            return AnalysisResult(
                task_id=task_id,
                file=file,
                language=language,
                agent="CAnalyzerAgent",
                issues=[],
                analysis_time_seconds=round(elapsed, 2),
                llm_tokens_used=0,
                error=str(e),
            ).model_dump()

    _MAX_CONCURRENT_LLM = 3

    async def _run_two_pass(
        self,
        *,
        file: str,
        file_content: str,
        file_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """2-Pass 분석: Pre-Scanner → LLM 선별 → 함수별 개별 심층 분석.

        Pass 1: regex 스캔 + mini 모델로 위험 함수 선별
        Pass 2: 선별된 함수를 각각 개별 primary 모델 호출로 심층 분석
        """
        # Pass 1-a: Heuristic Pre-Scanner (regex, 비용 0)
        scan_result = self._heuristic_scanner.execute(file=file)
        findings = scan_result.data.get("findings", [])

        if not findings:
            logger.info(f"Pre-Scanner: 위험 패턴 없음 → 기존 Heuristic 분석: {file}")
            return await self._run_single_pass_heuristic(
                file=file, file_content=file_content, file_context=file_context,
            )

        # Pass 1-b: 함수별 패턴 요약 생성
        func_summary = self._build_function_findings_summary(findings)

        lines = file_content.splitlines()
        boundaries = find_function_boundaries(lines, "c")

        # Pass 1-c: mini 모델로 위험 함수 선별
        prescan_prompt = load_prompt(
            "c_prescan_fewshot",
            file_path=file,
            total_functions=str(len(boundaries)),
            total_findings=str(len(findings)),
            function_findings_summary=func_summary,
        )

        prescan_messages = [
            {
                "role": "system",
                "content": "당신은 C 코드 안전성 전문가입니다. 반드시 JSON 형식으로 응답하세요.",
            },
            {"role": "user", "content": prescan_prompt},
        ]

        # mini 모델로 빠르게 선별 (호출 후 원래 모델 복원)
        from mider.config.settings_loader import get_mini_model
        original_model = self.model
        original_fallback = self.fallback_model
        self.model = get_mini_model()
        self.fallback_model = None
        try:
            prescan_response = await self.call_llm(prescan_messages, json_mode=True)
        finally:
            self.model = original_model
            self.fallback_model = original_fallback

        prescan_result = json.loads(prescan_response)
        if not isinstance(prescan_result, dict):
            prescan_result = {"risky_functions": []}

        risky_entries = [
            f for f in prescan_result.get("risky_functions", [])
            if isinstance(f, dict) and "function_name" in f
        ]
        risky_functions = [f["function_name"] for f in risky_entries]

        if not risky_functions:
            logger.info(f"Pass 1: 위험 함수 없음 (LLM 판단) → Heuristic: {file}")
            self.rl.decision(
                "Pass 1 판정: 위험 함수 없음 → Heuristic fallback",
            )
            return await self._run_single_pass_heuristic(
                file=file, file_content=file_content, file_context=file_context,
            )

        logger.info(
            f"Pass 1 완료: {len(risky_functions)}개 위험 함수 선별 → "
            f"{risky_functions}"
        )
        self.rl.step(
            f"Pass 1 판정: {len(risky_functions)}개 위험 함수 선별",
        )
        for entry in risky_entries:
            fname = entry.get("function_name", "?")
            reason = entry.get("reason", "이유 없음")
            self.rl.scan(f"  [sky_blue2]{fname}[/sky_blue2]: {reason}")

        # Pass 2: 함수별 개별 LLM 호출
        structure_summary = build_structure_summary(file_content, file_context, "c")
        file_context_str = json.dumps(
            file_context, ensure_ascii=False, indent=2,
        ) if file_context else "컨텍스트 정보 없음"

        func_start_lines = self._map_function_boundaries(
            risky_functions, lines, boundaries,
        )

        sem = asyncio.Semaphore(self._MAX_CONCURRENT_LLM)
        total_funcs = len(risky_functions)

        async def _analyze_with_limit(
            idx: int, func_name: str, start_line: int,
        ) -> list[dict]:
            async with sem:
                self.rl.step(
                    f"Pass 2 [{idx}/{total_funcs}] [sky_blue2]{func_name}[/sky_blue2] 분석 시작"
                )
                return await self._analyze_single_function(
                    file=file,
                    file_content=file_content,
                    func_name=func_name,
                    start_line=start_line,
                    findings=findings,
                    structure_summary=structure_summary,
                    file_context_str=file_context_str,
                )

        tasks = []
        func_idx = 0
        for func_name in risky_functions:
            start_line = func_start_lines.get(func_name)
            if start_line is None:
                logger.warning(f"함수 경계 찾기 실패, 분석 건너뜀: {func_name}")
                continue
            func_idx += 1
            tasks.append(_analyze_with_limit(func_idx, func_name, start_line))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_issues: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.warning(f"함수 분석 실패: {result}")
                continue
            all_issues.extend(result)

        # 후처리: 동일 패턴 병합 + 노이즈 제거
        before_count = len(all_issues)
        all_issues = _deduplicate_issues(all_issues)
        if before_count != len(all_issues):
            self.rl.process(
                f"Dedup: {before_count}건 → {len(all_issues)}건 "
                f"({before_count - len(all_issues)}건 중복/노이즈 제거)"
            )

        # issue_id 재번호 (C-001부터 순차)
        for i, issue in enumerate(all_issues):
            issue["issue_id"] = f"C-{i + 1:03d}"

        logger.info(
            f"Pass 2 완료: {len(all_issues)}개 이슈 "
            f"({len(risky_functions)}개 함수 개별 분석)"
        )
        return all_issues

    async def _analyze_single_function(
        self,
        *,
        file: str,
        file_content: str,
        func_name: str,
        start_line: int,
        findings: list[dict[str, Any]],
        structure_summary: str,
        file_context_str: str,
    ) -> list[dict[str, Any]]:
        """단일 함수를 LLM으로 심층 분석한다."""
        error_blocks = extract_error_functions(file_content, [start_line], "c")
        if not error_blocks:
            return []

        error_functions_str = "\n\n".join(
            f"[{block.line_start}~{block.line_end}줄]\n{block.content}"
            for block in error_blocks
        )

        func_warnings_str = self._build_grouped_warnings(findings, [func_name])

        prompt = load_prompt(
            "c_analyzer_error_focused",
            clang_tidy_warnings=func_warnings_str,
            file_path=file,
            structure_summary=structure_summary,
            error_functions=error_functions_str,
            file_context=file_context_str,
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 C 언어 메모리 안전성 및 보안 분석 전문가입니다. "
                    "반드시 JSON 형식으로 응답하세요."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        response = await self.call_llm(messages, json_mode=True)
        tokens = (len(prompt) + len(response)) // 4
        llm_result = json.loads(response)

        if not isinstance(llm_result, dict):
            return []

        issues = llm_result.get("issues", [])
        logger.debug(f"함수 {func_name}: {len(issues)}개 이슈")

        # 함수별 결과 추론 로그
        if issues:
            severity_summary = {}
            for iss in issues:
                sev = iss.get("severity", "?").upper()
                severity_summary[sev] = severity_summary.get(sev, 0) + 1
            sev_str = " ".join(f"{k}:{v}" for k, v in severity_summary.items())
            self.rl.step(
                f"Pass 2 [[sky_blue2]{func_name}[/sky_blue2]]: {len(issues)}개 이슈 ({sev_str}, {tokens:,} tokens)"
            )
            for iss in issues:
                sev = iss.get("severity", "?").upper()
                title = iss.get("title", "")
                self.rl.scan(f"  [{sev}] {title}")
        else:
            self.rl.step(f"Pass 2 [[sky_blue2]{func_name}[/sky_blue2]]: 이슈 없음 ({tokens:,} tokens)")

        return issues

    async def _run_single_pass_heuristic(
        self,
        *,
        file: str,
        file_content: str,
        file_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """기존 Heuristic 단일 패스 분석."""
        file_content_optimized = optimize_file_content(
            file_content, file_context, "c",
        )
        prompt = load_prompt(
            "c_analyzer_heuristic",
            file_path=file,
            file_content_optimized=file_content_optimized,
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 C 언어 메모리 안전성 및 보안 분석 전문가입니다. "
                    "반드시 JSON 형식으로 응답하세요."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        response = await self.call_llm(messages, json_mode=True)
        llm_result = json.loads(response)
        if not isinstance(llm_result, dict):
            return []
        return llm_result.get("issues", [])

    def _build_function_findings_summary(
        self, findings: list[dict[str, Any]],
    ) -> str:
        """함수별 위험 패턴 요약을 생성한다."""
        func_findings: dict[str, list[dict[str, Any]]] = {}
        for f in findings:
            func = f.get("function") or "(global)"
            func_findings.setdefault(func, []).append(f)

        parts: list[str] = []
        for func, items in func_findings.items():
            parts.append(f"\n### 함수: {func} ({len(items)}개 패턴)")
            for item in items[:10]:  # 함수당 최대 10개
                parts.append(
                    f"- {item['pattern_id']} L{item['line']}: {item['content'][:80]}"
                )
            if len(items) > 10:
                parts.append(f"  ... 외 {len(items) - 10}개")

        return "\n".join(parts)

    _HIGH_PRIORITY_PATTERNS = {"UNINIT_VAR", "UNSAFE_FUNC", "MALLOC_NO_CHECK", "FORMAT_STRING"}

    def _build_grouped_warnings(
        self,
        findings: list[dict[str, Any]],
        risky_functions: list[str],
    ) -> str:
        """함수별로 그룹화된 warnings 문자열을 생성한다.

        HIGH 우선순위 패턴을 먼저 배치하고, 함수당 최대 15개로 제한하여
        LLM이 모든 함수의 warnings를 균등하게 처리하도록 한다.
        """
        func_warnings: dict[str, list[dict[str, Any]]] = {}
        for f in findings:
            func = f.get("function")
            if func in risky_functions:
                func_warnings.setdefault(func, []).append(f)

        parts: list[str] = []
        for func in risky_functions:
            items = func_warnings.get(func, [])
            if not items:
                continue

            # HIGH 우선순위 패턴을 먼저, 나머지를 뒤에
            high = [i for i in items if i["pattern_id"] in self._HIGH_PRIORITY_PATTERNS]
            rest = [i for i in items if i["pattern_id"] not in self._HIGH_PRIORITY_PATTERNS]
            sorted_items = high + rest

            parts.append(f"\n### 함수: {func} (총 {len(items)}개 경고, HIGH 우선 {len(high)}개)")
            for item in sorted_items[:15]:
                parts.append(
                    f"- L{item['line']} [{item['pattern_id']}] {item['description']}: "
                    f"{item['content'][:80]}"
                )
            if len(sorted_items) > 15:
                parts.append(f"  ... 외 {len(sorted_items) - 15}개")

        return "\n".join(parts)

    def _map_function_boundaries(
        self,
        function_names: list[str],
        lines: list[str],
        boundaries: list[tuple[int, int]],
    ) -> dict[str, int]:
        """함수명 → 시작 라인 번호 매핑을 반환한다."""
        from mider.tools.static_analysis.c_heuristic_scanner import _FUNC_NAME_PATTERN

        name_set = set(function_names)
        result: dict[str, int] = {}
        for start, end in boundaries:
            idx = start - 1
            func_line = lines[idx]
            m = _FUNC_NAME_PATTERN.match(func_line)
            if not m and idx + 1 < len(lines):
                combined = func_line.rstrip() + " " + lines[idx + 1].lstrip()
                m = _FUNC_NAME_PATTERN.match(combined)
            if m and m.group(1) in name_set:
                result[m.group(1)] = start

        return result

    def _run_clang_tidy(self, file: str) -> dict[str, Any] | None:
        """clang-tidy를 실행하여 결과를 반환한다.

        헤더 누락 등 컴파일 에러만 있고 유의미한 경고가 없으면
        None을 반환하여 Heuristic/2-Pass fallback을 유도한다.
        실행 실패 시에도 None을 반환한다.
        """
        try:
            result = self._clang_tidy_runner.execute(file=file)
            warnings = result.data.get("warnings", [])
            if not warnings:
                return None

            # 헤더 에러 분리
            header_errors = [w for w in warnings if _is_header_error(w)]
            non_header = [w for w in warnings if not _is_header_error(w)]

            if not header_errors:
                # 헤더 에러 없음 → 전체 경고가 유의미 (AST 정상)
                self.rl.scan(
                    f"clang-tidy: {len(non_header)}건 경고 (헤더 정상, 전부 유의미)"
                )
                return {"warnings": non_header} if non_header else None

            # 헤더 에러 있음 → Level 1은 저가치, Level 2만 유의미
            level2 = [w for w in non_header if _is_level2_warning(w)]
            level1 = [w for w in non_header if not _is_level2_warning(w)]

            self.rl.detect(
                f"clang-tidy: 헤더 에러 {len(header_errors)}건 "
                f"→ Level 1 저가치 {len(level1)}건 필터링"
            )

            if level2:
                self.rl.scan(f"clang-tidy: Level 2 유의미 {len(level2)}건")
                logger.info(
                    f"clang-tidy: 헤더 에러 {len(header_errors)}건, "
                    f"Level 1 저가치 {len(level1)}건 필터링, "
                    f"Level 2 유의미 {len(level2)}건"
                )
                return {"warnings": level2}

            # Level 2 = 0건 → Heuristic/2-Pass fallback
            self.rl.decision(
                "Decision: clang-tidy 유의미 경고 0건 → Heuristic/2-Pass fallback",
                reason=f"헤더 누락({len(header_errors)}건)으로 clang-analyzer 미동작, "
                       f"Level 1(bugprone 등) {len(level1)}건은 LLM 분석 가치 없음",
            )
            logger.info(
                f"clang-tidy: 헤더 누락으로 clang-analyzer 미동작, "
                f"Level 1 {len(level1)}건 저가치 → Heuristic/2-Pass fallback"
            )
            return None
        except Exception as e:
            logger.warning(f"clang-tidy 실행 실패, Heuristic 모드로 전환: {e}")
            return None

    def _build_messages(
        self,
        *,
        file: str,
        file_content: str,
        clang_data: dict[str, Any] | None,
        file_context: dict[str, Any] | None,
    ) -> tuple[str, list[dict[str, str]]]:
        """프롬프트 경로를 선택하고 LLM 메시지를 구성한다.

        Returns:
            (prompt_text, messages) 튜플
        """
        if clang_data:
            # Error-Focused 경로
            clang_warnings_str = json.dumps(
                clang_data["warnings"], ensure_ascii=False, indent=2,
            )
            file_context_str = json.dumps(
                file_context, ensure_ascii=False, indent=2,
            ) if file_context else "컨텍스트 정보 없음"

            # 에러 라인 추출
            error_lines = []
            for item in clang_data.get("warnings", []):
                if isinstance(item, dict) and "line" in item:
                    error_lines.append(item["line"])

            # 토큰 최적화
            structure_summary = build_structure_summary(
                file_content, file_context, "c",
            )
            error_blocks = extract_error_functions(
                file_content, error_lines, "c",
            )
            error_functions_str = "\n\n".join(
                f"[{block.line_start}~{block.line_end}줄]\n{block.content}"
                for block in error_blocks
            ) if error_blocks else optimize_file_content(
                file_content, file_context, "c",
            )

            prompt = load_prompt(
                "c_analyzer_error_focused",
                clang_tidy_warnings=clang_warnings_str,
                file_path=file,
                structure_summary=structure_summary,
                error_functions=error_functions_str,
                file_context=file_context_str,
            )
        else:
            # Heuristic 경로
            file_content_optimized = optimize_file_content(
                file_content, file_context, "c",
            )
            prompt = load_prompt(
                "c_analyzer_heuristic",
                file_path=file,
                file_content_optimized=file_content_optimized,
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 C 언어 메모리 안전성 및 보안 분석 전문가입니다. "
                    "반드시 JSON 형식으로 응답하세요."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        return prompt, messages
