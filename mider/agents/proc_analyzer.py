"""ProCAnalyzerAgent: Phase 2 - Pro*C 분석.

Oracle proc 프리컴파일러 + SQLExtractor + LLM 심층분석을 결합하여
Pro*C 파일의 데이터 무결성 위협 패턴을 탐지한다.

분석 경로:
  함수 ≥2 AND >500줄 → 2-Pass (mini 선별 → 함수별 LLM)
  그 외 → 기존 단일 LLM 호출 (Error-Focused / Heuristic)
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from mider.agents.base_agent import BaseAgent
from mider.config.prompt_loader import load_prompt
from mider.config.settings_loader import (
    get_agent_fallback_model,
    get_agent_model,
    get_agent_temperature,
    get_mini_model,
)
from mider.models.analysis_result import AnalysisResult
from mider.tools.file_io.file_reader import FileReader
from mider.tools.static_analysis.proc_heuristic_scanner import ProCHeuristicScanner
from mider.tools.static_analysis.proc_runner import ProcRunner
from mider.tools.utility.sql_extractor import SQLExtractor
from mider.tools.utility.token_optimizer import (
    build_all_functions_summary,
    build_cursor_lifecycle_map,
    build_structure_summary,
    extract_error_functions,
    extract_proc_global_context,
    find_function_boundaries,
    optimize_file_content,
)

logger = logging.getLogger(__name__)

# 병렬 LLM 호출 동시성 제한
_MAX_CONCURRENT_LLM = 3

# 함수 시그니처에서 함수명 추출
_FUNC_NAME_PATTERN = re.compile(
    r"^(?!\s*(?:if|else|for|while|switch|return|#|typedef|struct|union|enum)\b)"
    r"\s*(?:static\s+|extern\s+|inline\s+)*"
    r"(?:void|int|char|long|short|unsigned|float|double|size_t|ssize_t|\w+_t|\w+)\s*\*?\s+"
    r"(\w+)\s*\("
)


class ProCAnalyzerAgent(BaseAgent):
    """Phase 2: Pro*C 파일을 분석하는 Agent.

    proc 프리컴파일러 에러 + SQL 블록 추출 결과를 기반으로
    LLM이 심층 분석하여 데이터 무결성 이슈를 탐지한다.

    분석 경로:
    - 함수 ≥2 AND >500줄 → 2-Pass (함수별 청킹)
    - 그 외 → 기존 단일 LLM 호출 (Error-Focused / Heuristic)
    """

    def __init__(
        self,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        _name = "proc_analyzer"
        model = model or get_agent_model(_name)
        fallback_model = fallback_model or get_agent_fallback_model(_name)
        temperature = temperature if temperature is not None else get_agent_temperature(_name)
        super().__init__(
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
        )
        self._file_reader = FileReader()
        self._proc_runner = ProcRunner()
        self._sql_extractor = SQLExtractor()
        self._heuristic_scanner = ProCHeuristicScanner()

    async def run(
        self,
        *,
        task_id: str,
        file: str,
        language: str = "proc",
        file_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Pro*C 파일을 분석한다.

        Args:
            task_id: ExecutionPlan의 task_id
            file: 분석할 파일 경로
            language: 파일 언어 ("proc")
            file_context: Phase 1에서 수집한 파일 컨텍스트

        Returns:
            AnalysisResult 형식의 딕셔너리
        """
        start_time = time.time()
        logger.info(f"Pro*C 분석 시작: {file}")

        try:
            # Step 1: 파일 읽기
            read_result = self._file_reader.execute(path=file)
            file_content = read_result.data["content"]
            lines = file_content.splitlines()
            line_count = len(lines)
            filename = Path(file).name
            self.rl.scan(f"File: [sky_blue2]{filename}[/sky_blue2] ({line_count}줄)")

            # Step 2: proc 프리컴파일러 실행
            proc_errors = self._run_proc(file)
            if proc_errors:
                self.rl.scan(f"proc: {len(proc_errors)}건 에러")

            # Step 3: SQL 블록 추출
            sql_blocks = self._extract_sql_blocks(file)
            if sql_blocks:
                sql_funcs = {b.get("function", "?") for b in sql_blocks if b.get("function")}
                self.rl.scan(
                    f"EXEC SQL: {len(sql_blocks)}개 블록 "
                    f"([sky_blue2]{', '.join(sorted(sql_funcs)[:5])}[/sky_blue2])"
                )

            # Step 3.5: Heuristic Scanner (장애 유발 패턴 4종)
            scanner_findings = self._run_heuristic_scanner(file)
            if scanner_findings:
                for finding in scanner_findings:
                    self.rl.detect(
                        f"Scanner [{finding['pattern_id']}] L{finding['line']}: "
                        f"{finding['description'][:80]}"
                    )

            # 도구 실행 결과 표준 로그
            missing_sqlca = sum(
                1 for b in sql_blocks if not b.get("has_sqlca_check", True)
            )
            logger.info(
                f"ProC [{filename}] 도구: proc에러={len(proc_errors or [])}, "
                f"SQL블록={len(sql_blocks)}(SQLCA미검사={missing_sqlca}), "
                f"Scanner={len(scanner_findings or [])}건"
            )

            # Step 4: 분석 경로 결정
            boundaries = find_function_boundaries(lines, "proc")
            use_chunked = len(boundaries) >= 2 and line_count > 500

            if use_chunked:
                # 2-Pass 함수별 청킹
                self.rl.decision(
                    "Decision: 2-Pass 함수별 청킹",
                    reason=f"{len(boundaries)}개 함수, {line_count}줄(>500)",
                )
                logger.info(
                    f"ProC [{filename}] 경로: 2-Pass 함수별 청킹 | "
                    f"{len(boundaries)}개 함수, {line_count}줄"
                )
                issues = await self._run_function_chunked(
                    file=file,
                    file_content=file_content,
                    file_context=file_context,
                    proc_errors=proc_errors,
                    sql_blocks=sql_blocks,
                    scanner_findings=scanner_findings,
                    boundaries=boundaries,
                )
            else:
                # 기존 단일 LLM 호출
                has_proc_errors = bool(proc_errors)
                has_missing_sqlca = any(
                    not block.get("has_sqlca_check", True)
                    for block in sql_blocks
                )
                has_scanner_findings = bool(scanner_findings)
                use_error_focused = (
                    has_proc_errors or has_missing_sqlca or has_scanner_findings
                )
                reasons: list[str] = []
                if has_proc_errors:
                    reasons.append(f"proc errors={len(proc_errors or [])}")
                if has_missing_sqlca:
                    reasons.append("SQLCA 미검사")
                if has_scanner_findings:
                    reasons.append(f"Scanner {len(scanner_findings)}건")
                if use_error_focused:
                    self.rl.decision(
                        "Decision: Error-Focused path",
                        reason=", ".join(reasons),
                    )
                    logger.info(
                        f"ProC [{filename}] 경로: Error-Focused | "
                        f"{', '.join(reasons)}"
                    )
                else:
                    self.rl.decision("Decision: Heuristic path", reason="정적 오류 없음")
                    logger.info(f"ProC [{filename}] 경로: Heuristic | 정적 오류 없음")

                prompt, messages = self._build_messages(
                    file=file,
                    file_content=file_content,
                    proc_errors=proc_errors,
                    sql_blocks=sql_blocks,
                    file_context=file_context,
                    use_error_focused=use_error_focused,
                    scanner_findings=scanner_findings,
                )

                response = await self.call_llm(messages, json_mode=True)
                llm_result = json.loads(response)

                if not isinstance(llm_result, dict):
                    raise ValueError(f"LLM 응답이 dict가 아님: {type(llm_result)}")

                issues = llm_result.get("issues", [])

            # Step 5: AnalysisResult 생성
            elapsed = time.time() - start_time

            result = AnalysisResult.model_validate({
                "task_id": task_id,
                "file": file,
                "language": language,
                "agent": "ProCAnalyzerAgent",
                "issues": issues,
                "analysis_time_seconds": round(elapsed, 2),
                "llm_tokens_used": 0,
            })

            logger.info(
                f"Pro*C 분석 완료: {file} → {len(result.issues)}개 이슈, "
                f"{result.analysis_time_seconds}초"
            )

            return result.model_dump()

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Pro*C 분석 실패: {file}: {e}")
            return AnalysisResult(
                task_id=task_id,
                file=file,
                language=language,
                agent="ProCAnalyzerAgent",
                issues=[],
                analysis_time_seconds=round(elapsed, 2),
                llm_tokens_used=0,
                error=str(e),
            ).model_dump()

    # ──────────────────────────────────────────────
    # 2-Pass 함수별 청킹
    # ──────────────────────────────────────────────

    async def _run_function_chunked(
        self,
        *,
        file: str,
        file_content: str,
        file_context: dict[str, Any] | None,
        proc_errors: list[dict[str, Any]],
        sql_blocks: list[dict[str, Any]],
        scanner_findings: list[dict[str, Any]] | None,
        boundaries: list[tuple[int, int]],
    ) -> list[dict[str, Any]]:
        """2-Pass 함수별 청킹 분석.

        Pass 1: mini 모델로 위험 함수 선별
        Pass 2: 선별된 함수를 각각 개별 primary 모델 호출로 심층 분석
        """
        filename = Path(file).name
        lines = file_content.splitlines()

        # 공통 컨텍스트 생성
        global_context = extract_proc_global_context(file_content)
        cursor_map = build_cursor_lifecycle_map(file_content)
        all_funcs_summary = build_all_functions_summary(file_content, "proc")
        structure_summary = build_structure_summary(file_content, file_context, "proc")

        # 함수명 매핑
        func_names = self._extract_func_names(lines, boundaries)

        # ── Pass 1: 위험 함수 선별 ──
        findings_summary = self._build_function_findings_summary(
            proc_errors=proc_errors,
            sql_blocks=sql_blocks,
            scanner_findings=scanner_findings or [],
            boundaries=boundaries,
            func_names=func_names,
        )

        prescan_prompt = load_prompt(
            "proc_prescan",
            file_path=file,
            total_functions=str(len(boundaries)),
            total_findings=str(
                len(proc_errors or [])
                + len(scanner_findings or [])
                + sum(1 for b in sql_blocks if not b.get("has_sqlca_check", True))
            ),
            all_functions_summary=all_funcs_summary,
            function_findings_summary=findings_summary,
            cursor_lifecycle_map=cursor_map,
        )

        prescan_messages = [
            {
                "role": "system",
                "content": "당신은 Oracle Pro*C 코드 안전성 전문가입니다. 반드시 JSON 형식으로 응답하세요.",
            },
            {"role": "user", "content": prescan_prompt},
        ]

        # mini 모델로 선별
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
            entry for entry in prescan_result.get("risky_functions", [])
            if isinstance(entry, dict) and "function_name" in entry
        ]
        risky_functions = set(entry["function_name"] for entry in risky_entries)
        risky_reasons: dict[str, str] = {
            entry["function_name"]: entry.get("reason", "")
            for entry in risky_entries
        }

        logger.info(
            f"ProC [{filename}] Pass 1: {len(risky_functions)}개 위험 함수 선별"
            f"{' → ' + str(sorted(risky_functions)) if risky_functions else ''}"
        )
        self.rl.step(
            f"Pass 1 판정: {len(risky_functions)}개 위험 / "
            f"{len(boundaries)}개 전체 → 전체 함수 분석"
        )
        for entry in risky_entries:
            self.rl.scan(
                f"  ⚠ [sky_blue2]{entry.get('function_name', '?')}[/sky_blue2]: "
                f"{entry.get('reason', '')}"
            )

        # ── Pass 2: 전체 함수 개별 LLM 호출 (위험 함수는 중점 표시) ──
        # 전체 함수에 대해 시작 라인 매핑
        all_func_starts: dict[str, int] = {
            name: start for start, name in func_names.items()
        }

        sem = asyncio.Semaphore(_MAX_CONCURRENT_LLM)
        total_funcs = len(all_func_starts)
        done_count = 0
        next_milestone = 25

        async def _analyze_with_limit(
            idx: int, func_name: str, start_line: int,
        ) -> list[dict]:
            nonlocal done_count, next_milestone
            is_risky = func_name in risky_functions
            async with sem:
                risk_tag = "⚠ " if is_risky else ""
                self.rl.step(
                    f"Pass 2 [{idx}/{total_funcs}] "
                    f"{risk_tag}[sky_blue2]{func_name}[/sky_blue2] 분석"
                )
                result = await self._analyze_single_function(
                    file=file,
                    file_content=file_content,
                    func_name=func_name,
                    start_line=start_line,
                    global_context=global_context,
                    cursor_map=cursor_map,
                    structure_summary=structure_summary,
                    proc_errors=proc_errors,
                    sql_blocks=sql_blocks,
                    scanner_findings=scanner_findings or [],
                    boundaries=boundaries,
                    func_names=func_names,
                    is_risky=is_risky,
                    risky_reason=risky_reasons.get(func_name, ""),
                )
                done_count += 1
                pct = (done_count * 100) // total_funcs
                while pct >= next_milestone:
                    logger.info(
                        f"ProC [{filename}] 진행: {next_milestone}% "
                        f"({done_count}/{total_funcs} 함수)"
                    )
                    next_milestone += 25
                return result

        tasks = []
        func_idx = 0
        for func_name, start_line in all_func_starts.items():
            func_idx += 1
            tasks.append(_analyze_with_limit(func_idx, func_name, start_line))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_issues: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.warning(f"함수 분석 실패: {result}")
                continue
            all_issues.extend(result)

        # issue_id 재번호 (PC-001부터 순차)
        for i, issue in enumerate(all_issues):
            issue["issue_id"] = f"PC-{i + 1:03d}"

        logger.info(
            f"ProC [{filename}] Pass 2 완료: {len(all_issues)}개 이슈 "
            f"({total_funcs}개 함수 개별 분석)"
        )
        return all_issues

    async def _analyze_single_function(
        self,
        *,
        file: str,
        file_content: str,
        func_name: str,
        start_line: int,
        global_context: str,
        cursor_map: str,
        structure_summary: str,
        proc_errors: list[dict[str, Any]],
        sql_blocks: list[dict[str, Any]],
        scanner_findings: list[dict[str, Any]],
        boundaries: list[tuple[int, int]],
        func_names: dict[int, str],
        is_risky: bool = False,
        risky_reason: str = "",
    ) -> list[dict[str, Any]]:
        """단일 함수를 LLM으로 심층 분석한다."""
        # 함수 코드 추출
        error_blocks = extract_error_functions(file_content, [start_line], "proc")
        if not error_blocks:
            return []

        block = error_blocks[0]
        function_code = f"[{block.line_start}~{block.line_end}줄]\n{block.content}"

        # 이 함수에 해당하는 SQL 블록 필터
        func_sql = [
            b for b in sql_blocks
            if b.get("function") == func_name
        ]
        func_sql_str = json.dumps(
            func_sql, ensure_ascii=False, indent=2,
        ) if func_sql else "(없음)"

        # 이 함수에 해당하는 Scanner findings 필터
        func_scanner = [
            f for f in scanner_findings
            if block.line_start <= f.get("line", 0) <= block.line_end
        ]
        func_scanner_str = json.dumps(
            func_scanner, ensure_ascii=False, indent=2,
        ) if func_scanner else "(없음)"

        # 이 함수에 해당하는 proc 에러 필터
        func_proc_errors = [
            e for e in proc_errors
            if isinstance(e, dict)
            and block.line_start <= e.get("line", 0) <= block.line_end
        ]
        func_proc_str = json.dumps(
            func_proc_errors, ensure_ascii=False, indent=2,
        ) if func_proc_errors else "(없음)"

        # 위험 함수 중점 분석 태그
        risk_priority = ""
        if is_risky:
            risk_priority = (
                f"\n\n## ⚠ 중점 분석 대상\n"
                f"이 함수는 사전 선별에서 위험으로 판정되었습니다.\n"
                f"사유: {risky_reason}\n"
                f"일반 함수보다 더 꼼꼼하게 분석하세요."
            )

        prompt = load_prompt(
            "proc_analyzer_function",
            global_context=global_context,
            cursor_lifecycle_map=cursor_map,
            structure_summary=structure_summary,
            function_code=function_code,
            function_sql_blocks=func_sql_str,
            function_scanner_findings=func_scanner_str,
            function_proc_errors=func_proc_str,
            file_path=file,
        )

        # 중점 분석 태그를 프롬프트 상단에 삽입
        if risk_priority:
            prompt = risk_priority + "\n\n" + prompt

        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 Oracle Pro*C 프리컴파일러 및 임베디드 SQL 분석 전문가입니다. "
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

    # ──────────────────────────────────────────────
    # 도구 실행 헬퍼
    # ──────────────────────────────────────────────

    def _run_proc(self, file: str) -> list[dict[str, Any]]:
        """proc 프리컴파일러를 실행하여 에러 목록을 반환한다."""
        try:
            result = self._proc_runner.execute(file=file)
            return result.data.get("errors", [])
        except Exception as e:
            logger.warning(f"proc 실행 실패, 에러 정보 없이 진행: {e}")
            return []

    def _extract_sql_blocks(self, file: str) -> list[dict[str, Any]]:
        """SQL 블록을 추출한다."""
        try:
            result = self._sql_extractor.execute(file=file)
            return result.data.get("sql_blocks", [])
        except Exception as e:
            logger.warning(f"SQL 블록 추출 실패: {e}")
            return []

    def _run_heuristic_scanner(self, file: str) -> list[dict[str, Any]]:
        """Pro*C Heuristic Scanner를 실행하여 장애 유발 패턴을 반환한다."""
        try:
            result = self._heuristic_scanner.execute(file=file)
            return result.data.get("findings", [])
        except Exception as e:
            logger.warning(f"Pro*C Heuristic Scanner 실패: {e}")
            return []

    # ──────────────────────────────────────────────
    # 프롬프트 빌더
    # ──────────────────────────────────────────────

    def _build_messages(
        self,
        *,
        file: str,
        file_content: str,
        proc_errors: list[dict[str, Any]],
        sql_blocks: list[dict[str, Any]],
        file_context: dict[str, Any] | None,
        use_error_focused: bool,
        scanner_findings: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, str]]]:
        """프롬프트 경로를 선택하고 LLM 메시지를 구성한다."""
        sql_blocks_str = json.dumps(
            sql_blocks, ensure_ascii=False, indent=2,
        )

        if use_error_focused:
            proc_errors_str = json.dumps(
                proc_errors, ensure_ascii=False, indent=2,
            )
            file_context_str = json.dumps(
                file_context, ensure_ascii=False, indent=2,
            ) if file_context else "컨텍스트 정보 없음"

            error_lines = []
            for item in proc_errors:
                if isinstance(item, dict) and "line" in item:
                    error_lines.append(item["line"])
            for block in sql_blocks:
                if not block.get("has_sqlca_check", True) and "line" in block:
                    error_lines.append(block["line"])
            for finding in (scanner_findings or []):
                if finding.get("line"):
                    error_lines.append(finding["line"])

            structure_summary = build_structure_summary(
                file_content, file_context, "proc",
            )
            error_blocks = extract_error_functions(
                file_content, error_lines, "proc",
            )
            error_functions_str = "\n\n".join(
                f"[{block.line_start}~{block.line_end}줄]\n{block.content}"
                for block in error_blocks
            ) if error_blocks else optimize_file_content(
                file_content, file_context, "proc",
            )

            scanner_findings_str = json.dumps(
                scanner_findings or [], ensure_ascii=False, indent=2,
            )

            prompt = load_prompt(
                "proc_analyzer_error_focused",
                proc_errors=proc_errors_str,
                sql_blocks=sql_blocks_str,
                scanner_findings=scanner_findings_str,
                file_path=file,
                structure_summary=structure_summary,
                error_functions=error_functions_str,
                file_context=file_context_str,
            )
        else:
            file_content_optimized = optimize_file_content(
                file_content, file_context, "proc",
            )
            prompt = load_prompt(
                "proc_analyzer_heuristic",
                sql_blocks=sql_blocks_str,
                file_path=file,
                file_content_optimized=file_content_optimized,
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 Oracle Pro*C 프리컴파일러 및 임베디드 SQL 분석 전문가입니다. "
                    "반드시 JSON 형식으로 응답하세요."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        return prompt, messages

    # ──────────────────────────────────────────────
    # 유틸리티
    # ──────────────────────────────────────────────

    @staticmethod
    def _extract_func_names(
        lines: list[str],
        boundaries: list[tuple[int, int]],
    ) -> dict[int, str]:
        """함수 경계의 시작 라인에서 함수명을 추출한다."""
        result: dict[int, str] = {}
        for start, _end in boundaries:
            idx = start - 1
            m = _FUNC_NAME_PATTERN.match(lines[idx])
            if m:
                result[start] = m.group(1)
                continue
            if idx + 1 < len(lines):
                combined = lines[idx].rstrip() + " " + lines[idx + 1].lstrip()
                m = _FUNC_NAME_PATTERN.match(combined)
                if m:
                    result[start] = m.group(1)
        return result

    @staticmethod
    @staticmethod
    def _build_function_findings_summary(
        *,
        proc_errors: list[dict[str, Any]],
        sql_blocks: list[dict[str, Any]],
        scanner_findings: list[dict[str, Any]],
        boundaries: list[tuple[int, int]],
        func_names: dict[int, str],
    ) -> str:
        """함수별 위험 패턴 요약을 생성한다 (Pass 1 프롬프트용)."""
        # 함수별로 findings 그룹핑
        func_findings: dict[str, list[str]] = {}

        # proc 에러
        for err in proc_errors:
            if not isinstance(err, dict):
                continue
            line = err.get("line", 0)
            func = _find_enclosing_func(line, boundaries, func_names)
            if func:
                func_findings.setdefault(func, []).append(
                    f"proc에러 L{line}: {err.get('message', '')[:60]}"
                )

        # SQL 블록 SQLCA 미검사
        for block in sql_blocks:
            if not block.get("has_sqlca_check", True):
                func = block.get("function") or _find_enclosing_func(
                    block.get("line", 0), boundaries, func_names,
                )
                if func:
                    func_findings.setdefault(func, []).append(
                        f"SQLCA 미검사 L{block.get('line', '?')}: {block.get('sql', '')[:40]}"
                    )

        # Scanner findings
        for finding in scanner_findings:
            line = finding.get("line", 0)
            func = _find_enclosing_func(line, boundaries, func_names)
            if func:
                func_findings.setdefault(func, []).append(
                    f"Scanner [{finding.get('pattern_id', '?')}] L{line}: "
                    f"{finding.get('description', '')[:60]}"
                )

        if not func_findings:
            return "(함수별 패턴 없음)"

        parts: list[str] = []
        for func_name, findings in sorted(func_findings.items()):
            parts.append(f"### {func_name}")
            for f in findings:
                parts.append(f"- {f}")
            parts.append("")

        return "\n".join(parts)


def _find_enclosing_func(
    line_num: int,
    boundaries: list[tuple[int, int]],
    func_names: dict[int, str],
) -> str | None:
    """주어진 라인을 포함하는 함수명을 반환한다."""
    for start, end in boundaries:
        if start <= line_num <= end:
            return func_names.get(start)
    return None
