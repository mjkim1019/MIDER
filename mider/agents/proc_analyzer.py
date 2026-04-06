"""ProCAnalyzerAgent: Phase 2 - Pro*C 분석.

V3 파이프라인 (기본):
  Phase 0: ProCPartitioner → PartitionResult + SymbolGraph
  Phase 1: EmbeddedSQLStaticAnalyzer → Finding[] (8규칙)
  Phase 2: ProCCrossChecker → Finding[] (7규칙)
  Phase 3: ProCLLMReviewer → Issue[] (focused LLM review)
  Phase 4: IssueMerger → Final Issue[] (중복 제거, 정렬)

V1 파이프라인 (fallback):
  공통 파이프라인 (proc + SQL + Scanner + 커서맵 + 글로벌컨텍스트)
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
    get_proc_grouping_config,
)
from mider.models.analysis_result import AnalysisResult
from mider.tools.file_io.file_reader import FileReader
from mider.tools.static_analysis.c_heuristic_scanner import CHeuristicScanner
from mider.tools.static_analysis.proc_heuristic_scanner import ProCHeuristicScanner
from mider.tools.static_analysis.proc_runner import ProcRunner
from mider.tools.utility.sql_extractor import SQLExtractor
from mider.tools.static_analysis.embedded_sql_analyzer import EmbeddedSQLStaticAnalyzer
from mider.tools.static_analysis.proc_clang_tidy_runner import ProCClangTidyRunner
from mider.tools.static_analysis.proc_cross_checker import ProCCrossChecker
from mider.tools.utility.issue_merger import IssueMerger
from mider.tools.utility.proc_llm_reviewer import ProCLLMReviewer
from mider.tools.utility.proc_partitioner import ProCPartitioner
from mider.tools.utility.proc_symbol_graph import ProCSymbolGraphBuilder
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
_MAX_CONCURRENT_LLM = 5
# 그룹 간 요청 간격 (초) — rate limit 완화용 stagger
_GROUP_STAGGER_SECONDS = 3.0

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
        self._stats: dict[str, Any] = {}

        # V3 컴포넌트
        self._partitioner = ProCPartitioner()
        self._graph_builder = ProCSymbolGraphBuilder()
        self._sql_static_analyzer = EmbeddedSQLStaticAnalyzer()
        self._cross_checker = ProCCrossChecker()
        self._llm_reviewer = ProCLLMReviewer(
            model=model, fallback_model=fallback_model, temperature=temperature,
        )
        self._issue_merger = IssueMerger()
        self._proc_clang_tidy = ProCClangTidyRunner()

    async def run(
        self,
        *,
        task_id: str,
        file: str,
        language: str = "proc",
        file_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Pro*C 파일을 분석한다.

        V3 파이프라인을 기본으로 사용하고, 실패 시 V1으로 fallback한다.
        """
        start_time = time.time()
        logger.info(f"Pro*C 분석 시작: {file}")

        try:
            # V3 파이프라인 시도
            result = await self._run_v3_pipeline(
                task_id=task_id, file=file, language=language,
            )
            elapsed = time.time() - start_time
            result["analysis_time_seconds"] = round(elapsed, 2)
            logger.info(
                f"Pro*C V3 분석 완료: {file} → {len(result.get('issues', []))}개 이슈, "
                f"{elapsed:.1f}초"
            )
            return result

        except Exception as v3_err:
            logger.warning(f"V3 파이프라인 실패, V1 fallback 시도: {v3_err}")
            try:
                return await self._run_v1_pipeline(
                    task_id=task_id, file=file, language=language,
                )
            except Exception as v1_err:
                elapsed = time.time() - start_time
                logger.error(f"Pro*C 분석 실패 (V1+V3): {file}: {v1_err}")
                return AnalysisResult(
                    task_id=task_id,
                    file=file,
                    language=language,
                    agent="ProCAnalyzerAgent",
                    issues=[],
                    analysis_time_seconds=round(elapsed, 2),
                    llm_tokens_used=0,
                    error=f"V3: {v3_err} | V1: {v1_err}",
                ).model_dump()

    # ──────────────────────────────────────────────
    # V3 파이프라인
    # ──────────────────────────────────────────────

    async def _run_v3_pipeline(
        self,
        *,
        task_id: str,
        file: str,
        language: str = "proc",
    ) -> dict[str, Any]:
        """V3 분리 검사 파이프라인: Partitioner → Static → Cross → LLM → Merger."""
        pipeline_start = time.time()
        filename = Path(file).name

        # ── Phase 0: Partitioner ──
        self.rl.step("V3 Phase 0: Partitioner")
        partition = self._partitioner.partition(file)
        self.rl.scan(
            f"Partition: {len(partition.functions)}개 함수, "
            f"{len(partition.sql_blocks)}개 SQL블록, "
            f"{len(partition.host_variables)}개 호스트변수, "
            f"{len(partition.cursor_map)}개 커서"
        )
        logger.info(
            f"ProC V3 [{filename}] Phase 0: "
            f"함수={len(partition.functions)}, "
            f"SQL={len(partition.sql_blocks)}, "
            f"호스트변수={len(partition.host_variables)}, "
            f"커서={len(partition.cursor_map)}"
        )
        partition_ms = int((time.time() - pipeline_start) * 1000)

        # ── Phase 0: SymbolGraph ──
        graph_start = time.time()
        graph = self._graph_builder.build(partition)
        self.rl.scan(
            f"SymbolGraph: {len(graph.nodes)}개 노드, {len(graph.edges)}개 엣지"
        )
        graph_ms = int((time.time() - graph_start) * 1000)

        # ── Phase 1: 정적 분석 (EmbeddedSQLStaticAnalyzer + clang-tidy) ──
        static_start = time.time()
        self.rl.step("V3 Phase 1: 정적 분석 (8규칙 + clang-tidy)")
        static_findings = self._sql_static_analyzer.analyze(
            sql_blocks=partition.sql_blocks,
            host_variables=partition.host_variables,
            cursor_map=partition.cursor_map,
            transaction_points=partition.transaction_points,
            global_context=partition.global_context,
        )

        # clang-tidy (EXEC SQL 제거 → 순수 C → stub header → clang-tidy)
        ct_findings = self._proc_clang_tidy.analyze(file=file, source_file=file)
        if ct_findings:
            static_findings.extend(ct_findings)
            for f in ct_findings:
                self.rl.detect(
                    f"[clang-tidy:{f.rule_id}] L{f.origin_line_start}: {f.title}"
                )
            logger.info(
                f"ProC V3 [{filename}] clang-tidy: {len(ct_findings)}개 finding"
            )

        if static_findings:
            for f in static_findings:
                if not f.rule_id.startswith("CT-"):  # clang-tidy는 위에서 이미 출력
                    self.rl.detect(
                        f"[{f.rule_id}] L{f.origin_line_start}: {f.title}"
                    )
        logger.info(
            f"ProC V3 [{filename}] Phase 1: {len(static_findings)}개 정적 finding"
        )
        static_ms = int((time.time() - static_start) * 1000)

        # ── Phase 2: 교차 검사 (ProCCrossChecker) ──
        cross_start = time.time()
        self.rl.step("V3 Phase 2: 교차 검사 (7규칙)")
        cross_findings = self._cross_checker.check(graph, partition)
        if cross_findings:
            for f in cross_findings:
                self.rl.detect(
                    f"[{f.rule_id}] L{f.origin_line_start}: {f.title}"
                )
        logger.info(
            f"ProC V3 [{filename}] Phase 2: {len(cross_findings)}개 교차 finding"
        )
        cross_ms = int((time.time() - cross_start) * 1000)

        all_findings = static_findings + cross_findings
        total_before_llm = len(all_findings)
        self.rl.scan(
            f"정적+교차 합계: {total_before_llm}개 finding → LLM 검토 전달"
        )

        # ── Phase 3: LLM Reviewer ──
        llm_start = time.time()
        self.rl.step(f"V3 Phase 3: LLM 검토 ({total_before_llm}개 finding)")

        # LLM Reviewer에 rl 전달
        self._llm_reviewer.rl = self.rl

        try:
            llm_result = await self._llm_reviewer.review(
                findings=all_findings,
                partition=partition,
                file_path=file,
            )
            llm_issues = llm_result["issues"]
            tokens_used = llm_result["tokens_used"]
        except Exception as llm_err:
            logger.warning(f"LLM 검토 실패, 정적 findings만 사용: {llm_err}")
            llm_issues = self._llm_reviewer.convert_findings_to_issues(
                all_findings, file,
            )
            tokens_used = 0

        llm_ms = int((time.time() - llm_start) * 1000)

        # ── Phase 4: IssueMerger ──
        merge_start = time.time()
        self.rl.step("V3 Phase 4: Issue 병합")
        final_issues = self._issue_merger.merge(
            llm_issues=llm_issues,
            static_findings=all_findings,
            file_path=file,
            partition=partition,
        )
        merge_ms = int((time.time() - merge_start) * 1000)

        self.rl.scan(
            f"최종: {len(final_issues)}개 이슈 "
            f"(LLM 전: {total_before_llm}, LLM 후: {len(llm_issues)}, "
            f"병합 후: {len(final_issues)})"
        )

        # ── _stats 수집 (CLI 요약 출력용) ──
        elapsed = time.time() - pipeline_start
        self._stats = {
            "delivery_mode": "v3_pipeline",
            "total_lines": partition.total_lines,
            "total_tokens": tokens_used,
            "total_groups": 0,
            "group_stats": [],
            "analysis_time_seconds": round(elapsed, 2),
            "v3_phase_ms": {
                "partition": partition_ms,
                "graph": graph_ms,
                "static": static_ms,
                "cross": cross_ms,
                "llm": llm_ms,
                "merge": merge_ms,
            },
            "v3_findings": {
                "static": len(static_findings),
                "clang_tidy": len(ct_findings),
                "cross": len(cross_findings),
                "llm_output": len(llm_issues),
                "final": len(final_issues),
            },
        }

        # ── 결과 생성 ──
        result = AnalysisResult.model_validate({
            "task_id": task_id,
            "file": file,
            "language": language,
            "agent": "ProCAnalyzerAgent",
            "issues": final_issues,
            "analysis_time_seconds": round(elapsed, 2),
            "llm_tokens_used": tokens_used,
        })
        return result.model_dump()

    # ──────────────────────────────────────────────
    # V1 파이프라인 (fallback)
    # ──────────────────────────────────────────────

    async def _run_v1_pipeline(
        self,
        *,
        task_id: str,
        file: str,
        language: str = "proc",
    ) -> dict[str, Any]:
        """V1 파이프라인 (기존 로직)."""
        start_time = time.time()
        logger.info(f"Pro*C V1 fallback 분석 시작: {file}")

        try:
            # ── 공통 파이프라인 ──
            read_result = self._file_reader.execute(path=file)
            file_content = read_result.data["content"]
            lines = file_content.splitlines()
            line_count = len(lines)
            filename = Path(file).name
            self.rl.scan(f"[V1] File: [sky_blue2]{filename}[/sky_blue2] ({line_count}줄)")

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
                f"ProC V1 [{filename}] 도구: proc에러={len(proc_errors or [])}, "
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
            self._stats = {
                "delivery_mode": mode,
                "total_lines": line_count,
                "total_tokens": 0,
                "total_groups": 0,
                "group_stats": [],
            }

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
                logger.info(f"ProC V1 [{filename}] 경로: 단일 호출 | {line_count}줄")
                issues = await self._run_single_call(**common_kwargs)
            else:
                self.rl.decision(
                    "Decision: 스마트 그룹핑",
                    reason=f"{line_count}줄, 토큰 초과 → 함수 그룹핑",
                )
                logger.info(
                    f"ProC V1 [{filename}] 경로: 스마트 그룹핑 | "
                    f"{len(boundaries)}개 함수, {line_count}줄"
                )
                issues = await self._run_grouped_call(
                    **common_kwargs,
                    boundaries=boundaries,
                    func_names=func_names,
                )

            # ── source 필드 보정 ──
            _VALID_SOURCES = {"static_analysis", "llm", "hybrid"}
            for issue in issues:
                if issue.get("source") not in _VALID_SOURCES:
                    issue["source"] = "llm"

            # ── 결과 생성 ──
            elapsed = time.time() - start_time
            result = AnalysisResult.model_validate({
                "task_id": task_id,
                "file": file,
                "language": language,
                "agent": "ProCAnalyzerAgent",
                "issues": issues,
                "analysis_time_seconds": round(elapsed, 2),
                "llm_tokens_used": self._stats.get("total_tokens", 0),
            })

            logger.info(
                f"Pro*C V1 분석 완료: {file} → {len(result.issues)}개 이슈, "
                f"{result.analysis_time_seconds}초"
            )
            return result.model_dump()

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Pro*C V1 분석 실패: {file}: {e}")
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

        system_content = (
            "당신은 Oracle Pro*C 프리컴파일러 및 임베디드 SQL 분석 전문가입니다. "
            "반드시 JSON 형식으로 응답하세요."
        )
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ]

        response = await self.call_llm(messages, json_mode=True)

        # 토큰 추정 (입력 + 출력)
        self._stats["total_tokens"] = (
            self._estimate_tokens(system_content)
            + self._estimate_tokens(prompt)
            + self._estimate_tokens(response)
        )

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

        _target, hard_cap = get_proc_grouping_config()
        classification = classify_proc_functions(
            file_content, boundaries, func_names,
            hard_cap_lines=hard_cap,
        )
        dispatch_groups = classification["dispatch_groups"]
        logger.info(
            f"ProC [{filename}] 그룹핑: "
            f"boilerplate={len(classification['boilerplate'])}, "
            f"디스패치={len(dispatch_groups)}그룹({len(classification['dispatch'])}함수), "
            f"유틸={len(classification['utility_groups'])}그룹"
        )

        # 그룹 목록 생성
        groups: list[dict[str, Any]] = []

        # 디스패치 그룹 (줄 수 기반 묶음)
        for dg in dispatch_groups:
            code = self._extract_group_code(lines, dg, boundaries, func_names)
            grp_ranges = self._get_group_line_ranges(dg, boundaries, func_names)
            lc = sum(e - s + 1 for s, e in grp_ranges)
            if len(dg) == 1:
                label = dg[0]
            else:
                preview = "+".join(dg[:3])
                label = f"dispatch({preview}{'...' if len(dg) > 3 else ''})"
            groups.append({
                "label": label,
                "code": code,
                "func_names_list": dg,
                "line_ranges": grp_ranges,
                "line_count": lc,
            })

        # 유틸 그룹
        for util_group in classification["utility_groups"]:
            code = self._extract_group_code(lines, util_group, boundaries, func_names)
            grp_ranges = self._get_group_line_ranges(util_group, boundaries, func_names)
            groups.append({
                "label": f"유틸({'+'.join(util_group[:3])}...)",
                "code": code,
                "func_names_list": util_group,
                "line_ranges": grp_ranges,
                "line_count": sum(e - s + 1 for s, e in grp_ranges),
            })

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
                group_start = time.time()
                # 그룹별 컨텍스트 필터링
                grp_ranges = group["line_ranges"]
                grp_fnames = group["func_names_list"]

                filtered_proc_errors = self._filter_by_line_ranges(
                    proc_errors, grp_ranges,
                )
                filtered_sql_blocks = self._filter_sql_blocks_by_group(
                    sql_blocks, grp_fnames, grp_ranges,
                )
                filtered_scanner = self._filter_by_line_ranges(
                    scanner_findings or [], grp_ranges,
                )
                filtered_cursor = self._filter_cursor_map(
                    cursor_map, grp_fnames,
                )
                filtered_risky = self._filter_risky_annotation(
                    risky_annotation, grp_fnames,
                )

                line_count = group["line_count"]

                # ● 그룹 시작 로그 (verbose: rl.step, 항상: logger.info)
                self.rl.step(
                    f"그룹 [{idx}/{total_groups}] "
                    f"[sky_blue2]{group['label']}[/sky_blue2] "
                    f"({line_count}줄) | "
                    f"필터링: proc_errors {len(proc_errors)}→{len(filtered_proc_errors)}, "
                    f"sql_blocks {len(sql_blocks)}→{len(filtered_sql_blocks)}, "
                    f"scanner {len(scanner_findings or [])}→{len(filtered_scanner)}"
                )
                logger.info(
                    f"그룹 [{idx}/{total_groups}] {group['label']} "
                    f"({line_count}줄)"
                )

                prompt = self._build_unified_prompt(
                    file=file,
                    code=group["code"],
                    global_context=global_context,
                    cursor_map=filtered_cursor,
                    risky_annotation=filtered_risky,
                    proc_errors=filtered_proc_errors,
                    sql_blocks=filtered_sql_blocks,
                    scanner_findings=filtered_scanner,
                )

                system_content = (
                    "당신은 Oracle Pro*C 프리컴파일러 및 임베디드 SQL 분석 전문가입니다. "
                    "반드시 JSON 형식으로 응답하세요."
                )
                messages = [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": prompt},
                ]

                # ── 토큰 비율 분석 (1 token ≈ 3 chars, 한글+코드 혼합) ──
                est = self._estimate_tokens
                code_tokens = est(group["code"])
                system_tokens = est(system_content)
                user_prompt_tokens = est(prompt)
                total_tokens = system_tokens + user_prompt_tokens

                global_ctx_tokens = est(global_context)
                cursor_tokens = est(filtered_cursor)
                risky_tokens = est(filtered_risky)
                pe_str = json.dumps(
                    filtered_proc_errors or [], ensure_ascii=False, indent=2,
                ) if filtered_proc_errors else "(없음)"
                sql_str = json.dumps(
                    filtered_sql_blocks, ensure_ascii=False, indent=2,
                )
                sc_str = json.dumps(
                    filtered_scanner or [], ensure_ascii=False, indent=2,
                ) if filtered_scanner else "(없음)"
                findings_tokens = est(pe_str) + est(sql_str) + est(sc_str)
                template_tokens = max(
                    0,
                    total_tokens - code_tokens - global_ctx_tokens
                    - cursor_tokens - risky_tokens - findings_tokens,
                )

                code_pct = (code_tokens / total_tokens * 100) if total_tokens else 0
                non_code = total_tokens - code_tokens
                non_code_pct = 100.0 - code_pct

                self.rl.scan(
                    f"└ 입력 비율: 총 {total_tokens:,} tokens | "
                    f"소스코드 {code_tokens:,} ({code_pct:.1f}%) | "
                    f"비소스 {non_code:,} ({non_code_pct:.1f}%)"
                )
                # 세부 내역
                def _pct(t: int) -> str:
                    return f"{t / total_tokens * 100:5.1f}" if total_tokens else "  0.0"

                self.rl.scan(
                    f"   소스코드(code)                {code_tokens:>7,} ({_pct(code_tokens)}%)"
                )
                self.rl.scan(
                    f"   시스템/프롬프트 템플릿        {template_tokens:>7,} ({_pct(template_tokens)}%)"
                )
                self.rl.scan(
                    f"   global_context                {global_ctx_tokens:>7,} ({_pct(global_ctx_tokens)}%)"
                )
                self.rl.scan(
                    f"   cursor_map                    {cursor_tokens:>7,} ({_pct(cursor_tokens)}%)"
                )
                self.rl.scan(
                    f"   risky_annotation              {risky_tokens:>7,} ({_pct(risky_tokens)}%)"
                )
                self.rl.scan(
                    f"   proc_errors/sql_blocks/scanner{findings_tokens:>7,} ({_pct(findings_tokens)}%)"
                )

                response = await self.call_llm(messages, json_mode=True)

                # 그룹별 메트릭 수집
                output_tokens = self._estimate_tokens(response)
                group_total_tokens = total_tokens + output_tokens
                group_elapsed = time.time() - group_start
                logger.debug(
                    f"[{idx}/{total_groups}] LLM 응답 수신: "
                    f"{group['label']} ({line_count}줄), "
                    f"tokens≈{group_total_tokens:,}, {group_elapsed:.1f}초"
                )

                try:
                    llm_result = json.loads(response)
                except (json.JSONDecodeError, TypeError):
                    llm_result = {"issues": []}

                async with progress_lock:
                    self._stats["group_stats"].append({
                        "tokens": group_total_tokens,
                        "lines": line_count,
                        "elapsed_seconds": round(group_elapsed, 2),
                    })
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

        # staggered launch: 각 그룹을 _GROUP_STAGGER_SECONDS 간격으로 시작
        async def _staggered_launch() -> list[asyncio.Task]:
            launched: list[asyncio.Task] = []
            for i, group in enumerate(groups):
                task = asyncio.create_task(_analyze_group(i + 1, group))
                launched.append(task)
                if i < len(groups) - 1:
                    await asyncio.sleep(_GROUP_STAGGER_SECONDS)
            return launched

        launched_tasks = await _staggered_launch()
        results = await asyncio.gather(*launched_tasks, return_exceptions=True)

        all_issues: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.warning(f"그룹 분석 실패: {result}")
                continue
            all_issues.extend(result)

        # issue_id 재번호
        for i, issue in enumerate(all_issues):
            issue["issue_id"] = f"PC-{i + 1:03d}"

        # 메트릭 집계
        self._stats["total_groups"] = total_groups
        self._stats["total_tokens"] = sum(
            g["tokens"] for g in self._stats["group_stats"]
        )

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
    # 토큰 추정 / 그룹 유틸
    # ──────────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """토큰 수를 추정한다 (1 token ≈ 3 chars, 한글+코드 혼합 기준)."""
        return max(1, len(text) // 3) if text else 0

    @staticmethod
    def _count_group_lines(
        group_func_names: list[str],
        boundaries: list[tuple[int, int]],
        func_names: dict[int, str],
    ) -> int:
        """그룹 함수들의 총 줄 수를 반환한다."""
        name_set = set(group_func_names)
        return sum(
            end - start + 1
            for start, end in boundaries
            if func_names.get(start) in name_set
        )

    # ──────────────────────────────────────────────
    # 그룹별 컨텍스트 필터링
    # ──────────────────────────────────────────────

    @staticmethod
    def _get_group_line_ranges(
        group_func_names: list[str],
        boundaries: list[tuple[int, int]],
        func_names: dict[int, str],
    ) -> list[tuple[int, int]]:
        """그룹 함수들의 (start, end) 라인 범위 리스트를 반환한다."""
        name_set = set(group_func_names)
        return [
            (start, end)
            for start, end in boundaries
            if func_names.get(start) in name_set
        ]

    @staticmethod
    def _filter_by_line_ranges(
        items: list[dict[str, Any]],
        line_ranges: list[tuple[int, int]],
        line_key: str = "line",
    ) -> list[dict[str, Any]]:
        """항목의 라인 번호가 그룹 범위에 속하는 것만 필터링한다."""
        filtered: list[dict[str, Any]] = []
        for item in items:
            line = item.get(line_key, 0)
            if any(start <= line <= end for start, end in line_ranges):
                filtered.append(item)
        return filtered

    @staticmethod
    def _filter_sql_blocks_by_group(
        sql_blocks: list[dict[str, Any]],
        group_func_names: list[str],
        line_ranges: list[tuple[int, int]],
    ) -> list[dict[str, Any]]:
        """SQL 블록을 함수명 또는 라인 범위로 필터링한다."""
        name_set = set(group_func_names)
        filtered: list[dict[str, Any]] = []
        for block in sql_blocks:
            func = block.get("function", "")
            line = block.get("line", 0)
            if func in name_set:
                filtered.append(block)
            elif any(start <= line <= end for start, end in line_ranges):
                filtered.append(block)
        return filtered

    @staticmethod
    def _filter_cursor_map(
        cursor_map: str,
        group_func_names: list[str],
    ) -> str:
        """그룹 함수에서 사용하는 커서만 필터링한다."""
        if not cursor_map:
            return cursor_map
        name_set = set(group_func_names)
        filtered_lines: list[str] = []
        for line in cursor_map.splitlines():
            if any(fn in line for fn in name_set):
                filtered_lines.append(line)
        return "\n".join(filtered_lines) if filtered_lines else cursor_map

    @staticmethod
    def _filter_risky_annotation(
        risky_annotation: str,
        group_func_names: list[str],
    ) -> str:
        """그룹 함수에 해당하는 위험 annotation만 필터링한다."""
        if not risky_annotation:
            return risky_annotation
        name_set = set(group_func_names)
        filtered_lines: list[str] = []
        for line in risky_annotation.splitlines():
            if any(fn in line for fn in name_set):
                filtered_lines.append(line)
        return "\n".join(filtered_lines) if filtered_lines else risky_annotation

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
