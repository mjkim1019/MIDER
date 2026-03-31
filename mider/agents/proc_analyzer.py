"""ProCAnalyzerAgent: Phase 2 - Pro*C 분석.

Oracle proc 프리컴파일러 + SQLExtractor + LLM 심층분석을 결합하여
Pro*C 파일의 데이터 무결성 위협 패턴을 탐지한다.

분석 경로 (통일 아키텍처):
  모든 파일: 공통 파이프라인 (proc + SQL + Scanner + 커서맵 + 글로벌컨텍스트)
  → Pass 1: mini로 위험 함수 태깅
  → ≤100K tokens: 전체 코드 단일 LLM 호출
  → >100K tokens: 스마트 그룹핑 (계층형/디스패치형/유틸)
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
from mider.tools.static_analysis.c_heuristic_scanner import CHeuristicScanner
from mider.tools.static_analysis.proc_heuristic_scanner import ProCHeuristicScanner
from mider.tools.static_analysis.proc_runner import ProcRunner
from mider.tools.utility.sql_extractor import SQLExtractor
from mider.tools.utility.token_optimizer import (
    build_all_functions_summary,
    build_cursor_lifecycle_map,
    classify_proc_functions,
    extract_error_functions,
    extract_proc_global_context,
    find_function_boundaries,
)

logger = logging.getLogger(__name__)

# 병렬 LLM 호출 동시성 제한
_MAX_CONCURRENT_LLM = 3

# 토큰 한계 (128K에서 프롬프트+응답 여유분 확보)
_TOKEN_LIMIT = 100_000

# 함수명 추출 패턴
_FUNC_NAME_PATTERN = re.compile(
    r"^(?!\s*(?:if|else|for|while|switch|return|#|typedef|struct|union|enum)\b)"
    r"\s*(?:static\s+|extern\s+|inline\s+)*"
    r"(?:void|int|char|long|short|unsigned|float|double|size_t|ssize_t|\w+_t|\w+)\s*\*?\s+"
    r"(\w+)\s*\("
)


class ProCAnalyzerAgent(BaseAgent):
    """Phase 2: Pro*C 파일을 분석하는 Agent.

    통일 아키텍처:
    - 공통 파이프라인 (proc + SQL + Scanner + 커서맵 + 글로벌컨텍스트)
    - Pass 1: mini로 위험 함수 태깅
    - 코드 전달: 전체 코드 단일 호출 / 스마트 그룹핑 (토큰 기반 분기)
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
        self._c_scanner = CHeuristicScanner()

    async def run(
        self,
        *,
        task_id: str,
        file: str,
        language: str = "proc",
        file_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Pro*C 파일을 분석한다."""
        start_time = time.time()
        logger.info(f"Pro*C 분석 시작: {file}")

        try:
            # ── 공통 파이프라인 ──
            read_result = self._file_reader.execute(path=file)
            file_content = read_result.data["content"]
            lines = file_content.splitlines()
            line_count = len(lines)
            filename = Path(file).name
            self.rl.scan(f"File: [sky_blue2]{filename}[/sky_blue2] ({line_count}줄)")

            proc_errors = self._run_proc(file)
            if proc_errors:
                self.rl.scan(f"proc: {len(proc_errors)}건 에러")

            sql_blocks = self._extract_sql_blocks(file)
            if sql_blocks:
                sql_funcs = {b.get("function", "?") for b in sql_blocks if b.get("function")}
                self.rl.scan(
                    f"EXEC SQL: {len(sql_blocks)}개 블록 "
                    f"([sky_blue2]{', '.join(sorted(sql_funcs)[:5])}[/sky_blue2])"
                )

            # ProC Scanner (도메인 특화 4종) + C Scanner (범용 6종) 병합
            proc_scanner_findings = self._run_heuristic_scanner(file)
            c_scanner_findings = self._run_c_scanner(file)
            scanner_findings = (proc_scanner_findings or []) + (c_scanner_findings or [])
            if scanner_findings:
                for finding in scanner_findings:
                    self.rl.detect(
                        f"Scanner [{finding['pattern_id']}] L{finding['line']}: "
                        f"{finding['description'][:80]}"
                    )

            missing_sqlca = sum(
                1 for b in sql_blocks if not b.get("has_sqlca_check", True)
            )
            logger.info(
                f"ProC [{filename}] 도구: proc에러={len(proc_errors or [])}, "
                f"SQL블록={len(sql_blocks)}(SQLCA미검사={missing_sqlca}), "
                f"Scanner={len(scanner_findings)}건 "
                f"(ProC={len(proc_scanner_findings or [])}, C={len(c_scanner_findings or [])})"
            )

            # 글로벌 컨텍스트 + 커서 맵
            global_context = extract_proc_global_context(file_content)
            cursor_map = build_cursor_lifecycle_map(file_content)
            boundaries = find_function_boundaries(lines, "proc")
            func_names = self._extract_func_names(lines, boundaries)

            # ── Pass 1: 위험 함수 태깅 (함수 ≥2일 때만) ──
            risky_annotation = "(위험 함수 없음)"
            if len(boundaries) >= 2:
                risky_annotation = await self._pass1_risk_tagging(
                    file=file,
                    file_content=file_content,
                    proc_errors=proc_errors,
                    sql_blocks=sql_blocks,
                    scanner_findings=scanner_findings,
                    boundaries=boundaries,
                    func_names=func_names,
                    cursor_map=cursor_map,
                )

            # ── 코드 전달 분기 ──
            mode = self._decide_delivery_mode(file_content)

            common_kwargs = dict(
                file=file,
                file_content=file_content,
                global_context=global_context,
                cursor_map=cursor_map,
                risky_annotation=risky_annotation,
                proc_errors=proc_errors,
                sql_blocks=sql_blocks,
                scanner_findings=scanner_findings,
            )

            if mode == "single":
                self.rl.decision(
                    "Decision: 전체 코드 단일 호출",
                    reason=f"{line_count}줄, 토큰 한계 내",
                )
                logger.info(f"ProC [{filename}] 경로: 단일 호출 | {line_count}줄")
                issues = await self._run_single_call(**common_kwargs)
            else:
                self.rl.decision(
                    "Decision: 스마트 그룹핑",
                    reason=f"{line_count}줄, 토큰 초과 → 함수 그룹핑",
                )
                logger.info(
                    f"ProC [{filename}] 경로: 스마트 그룹핑 | "
                    f"{len(boundaries)}개 함수, {line_count}줄"
                )
                issues = await self._run_grouped_call(
                    **common_kwargs,
                    boundaries=boundaries,
                    func_names=func_names,
                )

            # ── 결과 생성 ──
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
    # Pass 1: 위험 함수 태깅
    # ──────────────────────────────────────────────

    async def _pass1_risk_tagging(
        self,
        *,
        file: str,
        file_content: str,
        proc_errors: list[dict[str, Any]],
        sql_blocks: list[dict[str, Any]],
        scanner_findings: list[dict[str, Any]] | None,
        boundaries: list[tuple[int, int]],
        func_names: dict[int, str],
        cursor_map: str,
    ) -> str:
        """mini 모델로 위험 함수를 태깅하고 annotation 문자열을 반환한다."""
        filename = Path(file).name
        all_funcs_summary = build_all_functions_summary(file_content, "proc")

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

        original_model = self.model
        original_fallback = self.fallback_model
        self.model = get_mini_model()
        self.fallback_model = None
        try:
            prescan_response = await self.call_llm(prescan_messages, json_mode=True)
        finally:
            self.model = original_model
            self.fallback_model = original_fallback

        try:
            prescan_result = json.loads(prescan_response)
        except (json.JSONDecodeError, TypeError):
            prescan_result = {"risky_functions": []}
        if not isinstance(prescan_result, dict):
            prescan_result = {"risky_functions": []}

        risky_entries = [
            entry for entry in prescan_result.get("risky_functions", [])
            if isinstance(entry, dict) and "function_name" in entry
        ]

        logger.info(
            f"ProC [{filename}] Pass 1: {len(risky_entries)}개 위험 함수 태깅"
        )
        self.rl.step(f"Pass 1: {len(risky_entries)}개 위험 함수 태깅")
        for entry in risky_entries:
            self.rl.scan(
                f"  ⚠ [sky_blue2]{entry.get('function_name', '?')}[/sky_blue2]: "
                f"{entry.get('reason', '')}"
            )

        if not risky_entries:
            return "(위험 함수 없음 — 전체 코드를 균등하게 분석하세요)"

        # annotation 문자열 생성
        lines_out: list[str] = []
        for entry in risky_entries:
            lines_out.append(
                f"- ⚠ {entry['function_name']}: {entry.get('reason', '')}"
            )
        return "\n".join(lines_out)

    # ──────────────────────────────────────────────
    # 코드 전달 분기
    # ──────────────────────────────────────────────

    @staticmethod
    def _decide_delivery_mode(file_content: str) -> str:
        """코드 전달 방식을 결정한다.

        Returns:
            "single" (전체 코드 단일 호출) 또는 "grouped" (스마트 그룹핑)
        """
        estimated_tokens = len(file_content) // 3  # 보수적 추정 (한글+코드 혼합)
        prompt_overhead = 3000
        if estimated_tokens + prompt_overhead <= _TOKEN_LIMIT:
            return "single"
        return "grouped"

    # ──────────────────────────────────────────────
    # 단일 호출 경로
    # ──────────────────────────────────────────────

    async def _run_single_call(
        self,
        *,
        file: str,
        file_content: str,
        global_context: str,
        cursor_map: str,
        risky_annotation: str,
        proc_errors: list[dict[str, Any]],
        sql_blocks: list[dict[str, Any]],
        scanner_findings: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """전체 코드를 단일 LLM 호출로 분석한다."""
        prompt = self._build_unified_prompt(
            file=file,
            code=file_content,
            global_context=global_context,
            cursor_map=cursor_map,
            risky_annotation=risky_annotation,
            proc_errors=proc_errors,
            sql_blocks=sql_blocks,
            scanner_findings=scanner_findings,
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

        response = await self.call_llm(messages, json_mode=True)
        try:
            llm_result = json.loads(response)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(llm_result, dict):
            return []
        return llm_result.get("issues", [])

    # ──────────────────────────────────────────────
    # 그룹핑 호출 경로
    # ──────────────────────────────────────────────

    async def _run_grouped_call(
        self,
        *,
        file: str,
        file_content: str,
        global_context: str,
        cursor_map: str,
        risky_annotation: str,
        proc_errors: list[dict[str, Any]],
        sql_blocks: list[dict[str, Any]],
        scanner_findings: list[dict[str, Any]] | None,
        boundaries: list[tuple[int, int]],
        func_names: dict[int, str],
    ) -> list[dict[str, Any]]:
        """스마트 그룹핑으로 분석한다."""
        filename = Path(file).name
        lines = file_content.splitlines()

        classification = classify_proc_functions(
            file_content, boundaries, func_names,
        )
        logger.info(
            f"ProC [{filename}] 그룹핑: "
            f"boilerplate={len(classification['boilerplate'])}, "
            f"계층={len(classification['hierarchical_groups'])}그룹, "
            f"디스패치={len(classification['dispatch'])}, "
            f"유틸={len(classification['utility_groups'])}그룹"
        )

        # 그룹 목록 생성
        groups: list[dict[str, Any]] = []

        # 계층형 그룹
        for hier_group in classification["hierarchical_groups"]:
            code = self._extract_group_code(lines, hier_group, boundaries, func_names)
            groups.append({"label": f"계층({'+'.join(hier_group[:3])}...)", "code": code})

        # 디스패치형 개별
        for func_name in classification["dispatch"]:
            code = self._extract_func_code(lines, func_name, boundaries, func_names)
            if code:
                groups.append({"label": func_name, "code": code})

        # 유틸 그룹
        for util_group in classification["utility_groups"]:
            code = self._extract_group_code(lines, util_group, boundaries, func_names)
            groups.append({"label": f"유틸({'+'.join(util_group[:3])}...)", "code": code})

        if not groups:
            # 분류 실패 시 전체 코드 fallback
            logger.warning(f"ProC [{filename}] 그룹핑 실패 → 전체 코드 fallback")
            return await self._run_single_call(
                file=file,
                file_content=file_content,
                global_context=global_context,
                cursor_map=cursor_map,
                risky_annotation=risky_annotation,
                proc_errors=proc_errors,
                sql_blocks=sql_blocks,
                scanner_findings=scanner_findings,
            )

        # 병렬 LLM 호출
        sem = asyncio.Semaphore(_MAX_CONCURRENT_LLM)
        progress_lock = asyncio.Lock()
        total_groups = len(groups)
        done_count = 0
        next_milestone = 25

        async def _analyze_group(idx: int, group: dict) -> list[dict]:
            nonlocal done_count, next_milestone
            async with sem:
                self.rl.step(
                    f"그룹 [{idx}/{total_groups}] "
                    f"[sky_blue2]{group['label']}[/sky_blue2] 분석"
                )
                prompt = self._build_unified_prompt(
                    file=file,
                    code=group["code"],
                    global_context=global_context,
                    cursor_map=cursor_map,
                    risky_annotation=risky_annotation,
                    proc_errors=proc_errors,
                    sql_blocks=sql_blocks,
                    scanner_findings=scanner_findings,
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

                response = await self.call_llm(messages, json_mode=True)
                try:
                    llm_result = json.loads(response)
                except (json.JSONDecodeError, TypeError):
                    llm_result = {"issues": []}

                async with progress_lock:
                    done_count += 1
                    pct = (done_count * 100) // total_groups
                    while pct >= next_milestone:
                        logger.info(
                            f"ProC [{filename}] 진행: {next_milestone}% "
                            f"({done_count}/{total_groups} 그룹)"
                        )
                        next_milestone += 25

                if not isinstance(llm_result, dict):
                    return []
                return llm_result.get("issues", [])

        tasks = [
            _analyze_group(i + 1, group)
            for i, group in enumerate(groups)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_issues: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.warning(f"그룹 분석 실패: {result}")
                continue
            all_issues.extend(result)

        # issue_id 재번호
        for i, issue in enumerate(all_issues):
            issue["issue_id"] = f"PC-{i + 1:03d}"

        logger.info(
            f"ProC [{filename}] 그룹핑 완료: {len(all_issues)}개 이슈 "
            f"({total_groups}개 그룹)"
        )
        return all_issues

    # ──────────────────────────────────────────────
    # 프롬프트 빌더 (통합)
    # ──────────────────────────────────────────────

    @staticmethod
    def _build_unified_prompt(
        *,
        file: str,
        code: str,
        global_context: str,
        cursor_map: str,
        risky_annotation: str,
        proc_errors: list[dict[str, Any]],
        sql_blocks: list[dict[str, Any]],
        scanner_findings: list[dict[str, Any]] | None,
    ) -> str:
        """통합 프롬프트를 구성한다."""
        proc_errors_str = json.dumps(
            proc_errors or [], ensure_ascii=False, indent=2,
        ) if proc_errors else "(없음)"
        sql_blocks_str = json.dumps(
            sql_blocks, ensure_ascii=False, indent=2,
        )
        scanner_str = json.dumps(
            scanner_findings or [], ensure_ascii=False, indent=2,
        ) if scanner_findings else "(없음)"

        return load_prompt(
            "proc_analyzer",
            global_context=global_context,
            cursor_lifecycle_map=cursor_map,
            risky_functions_annotation=risky_annotation,
            scanner_findings=scanner_str,
            proc_errors=proc_errors_str,
            sql_blocks=sql_blocks_str,
            code=code,
            file_path=file,
        )

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

    def _run_c_scanner(self, file: str) -> list[dict[str, Any]]:
        """C Heuristic Scanner를 실행하여 범용 C 위험 패턴을 반환한다."""
        try:
            result = self._c_scanner.execute(file=file)
            return result.data.get("findings", [])
        except Exception as e:
            logger.warning(f"C Heuristic Scanner 실패: {e}")
            return []

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
    def _extract_func_code(
        lines: list[str],
        func_name: str,
        boundaries: list[tuple[int, int]],
        func_names: dict[int, str],
    ) -> str | None:
        """함수명으로 코드를 추출한다."""
        for start, end in boundaries:
            if func_names.get(start) == func_name:
                return "\n".join(lines[start - 1:end])
        return None

    @staticmethod
    def _extract_group_code(
        lines: list[str],
        group_func_names: list[str],
        boundaries: list[tuple[int, int]],
        func_names: dict[int, str],
    ) -> str:
        """함수 그룹의 코드를 연결하여 추출한다."""
        parts: list[str] = []
        name_set = set(group_func_names)
        for start, end in boundaries:
            if func_names.get(start) in name_set:
                parts.append(f"/* ── {func_names[start]} (L{start}~L{end}) ── */")
                parts.append("\n".join(lines[start - 1:end]))
                parts.append("")
        return "\n".join(parts) if parts else "(코드 추출 실패)"

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
        func_findings: dict[str, list[str]] = {}

        for err in proc_errors:
            if not isinstance(err, dict):
                continue
            line = err.get("line", 0)
            func = _find_enclosing_func(line, boundaries, func_names)
            if func:
                func_findings.setdefault(func, []).append(
                    f"proc에러 L{line}: {err.get('message', '')[:60]}"
                )

        for block in sql_blocks:
            if not block.get("has_sqlca_check", True):
                func = block.get("function") or _find_enclosing_func(
                    block.get("line", 0), boundaries, func_names,
                )
                if func:
                    func_findings.setdefault(func, []).append(
                        f"SQLCA 미검사 L{block.get('line', '?')}: {block.get('sql', '')[:40]}"
                    )

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
