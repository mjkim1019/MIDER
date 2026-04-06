"""SQLAnalyzerAgent: Phase 2 - SQL 분석.

문법 검증(sqlparse) + 정적 패턴 분석(AstGrepSearch) + Explain Plan 해석 +
LLM 심층분석을 결합하여 SQL 파일의 성능 저하 및 장애 유발 패턴을 탐지한다.
"""

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
)
from mider.models.analysis_result import AnalysisResult
from mider.tools.file_io.file_reader import FileReader
from mider.tools.search.ast_grep_search import AstGrepSearch
from mider.tools.static_analysis.sql_syntax_checker import SQLSyntaxChecker
from mider.tools.utility.explain_plan_parser import ExplainPlanParser

logger = logging.getLogger(__name__)

# LLM 응답에서 허용되는 source 값
_VALID_SOURCES = frozenset({"static_analysis", "llm", "hybrid"})

# SQL 정적 패턴 검색 대상
_SQL_PATTERNS = [
    "select_star",
    "function_in_where",
    "like_wildcard",
    "subquery",
    "or_condition",
]

# LLM context window 안전 임계값 (추정 토큰 수)
_TOKEN_WARNING_THRESHOLD = 100_000


class SQLAnalyzerAgent(BaseAgent):
    """Phase 2: SQL 파일을 분석하는 Agent.

    문법 검증 + 정적 패턴 검색 + Explain Plan 해석 후 LLM이 심층 분석하여
    Error-Focused 또는 Heuristic 경로로 이슈를 탐지한다.
    """

    def __init__(
        self,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        _name = "sql_analyzer"
        model = model or get_agent_model(_name)
        fallback_model = fallback_model or get_agent_fallback_model(_name)
        temperature = temperature if temperature is not None else get_agent_temperature(_name)
        super().__init__(
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
        )
        self._file_reader = FileReader()
        self._ast_grep = AstGrepSearch()
        self._syntax_checker = SQLSyntaxChecker()
        self._explain_plan_parser = ExplainPlanParser()
        self._stats: dict[str, Any] = {}

    async def run(
        self,
        *,
        task_id: str,
        file: str,
        language: str = "sql",
        file_context: dict[str, Any] | None = None,
        explain_plan_file: str | None = None,
    ) -> dict[str, Any]:
        """SQL 파일을 분석한다.

        Args:
            task_id: ExecutionPlan의 task_id
            file: 분석할 파일 경로
            language: 파일 언어 ("sql")
            file_context: Phase 1에서 수집한 파일 컨텍스트
            explain_plan_file: Explain Plan 결과 파일 경로 (선택적)

        Returns:
            AnalysisResult 형식의 딕셔너리
        """
        start_time = time.time()
        logger.info(f"SQL 분석 시작: {file}")

        try:
            # Step 1: 파일 읽기
            read_result = self._file_reader.execute(path=file)
            file_content = read_result.data["content"]
            line_count = read_result.data.get("line_count", 0)
            file_size = read_result.data.get("file_size", 0)
            token_estimate = len(file_content) // 4
            filename = Path(file).name
            self.rl.scan(f"File: [sky_blue2]{filename}[/sky_blue2] ({line_count}줄, ~{token_estimate:,} tokens)")

            logger.info(
                f"SQL 파일 크기: {file} → {line_count}줄, "
                f"{file_size:,}bytes, ~{token_estimate:,}토큰"
            )
            if token_estimate > _TOKEN_WARNING_THRESHOLD:
                logger.warning(
                    f"SQL 파일이 매우 큼: ~{token_estimate:,}토큰 "
                    f"(LLM context 초과 가능성)"
                )

            # Step 2: SQL 문법 검증
            syntax_result = self._check_syntax(file)
            syntax_errors = syntax_result.get("syntax_errors", [])
            if syntax_errors:
                self.rl.detect(f"SQL 문법 에러: {len(syntax_errors)}건")

            # Step 3: 정적 패턴 검색
            static_patterns = self._search_patterns(file)
            if static_patterns:
                # 테이블명 추출 (패턴에서)
                tables = set()
                for p in static_patterns:
                    match_text = p.get("match", "")
                    # FROM/INTO/UPDATE 뒤의 테이블명 추출
                    for t in re.findall(r"(?:FROM|INTO|UPDATE|JOIN)\s+(\w+)", match_text, re.IGNORECASE):
                        tables.add(t)
                if tables:
                    table_str = ", ".join(f"[sky_blue2]{t}[/sky_blue2]" for t in sorted(tables)[:5])
                    self.rl.scan(f"테이블: {table_str}")

            # Step 4: Explain Plan 파싱 (옵션)
            explain_plan_data = self._parse_explain_plan(explain_plan_file)
            if explain_plan_data:
                tuning_points = explain_plan_data.get("tuning_points", [])
                if tuning_points:
                    self.rl.detect(f"Explain Plan: {len(tuning_points)}건 튜닝 포인트")

            # 도구 실행 결과 표준 로그
            tuning_count = len((explain_plan_data or {}).get("tuning_points", []))
            logger.info(
                f"SQL [{filename}] 도구: 문법에러={len(syntax_errors)}, "
                f"패턴={len(static_patterns)}, 튜닝포인트={tuning_count}"
            )

            # Step 5: LLM 분석
            has_errors = bool(syntax_errors or (explain_plan_data and explain_plan_data.get("tuning_points")))
            if has_errors:
                self.rl.decision("Decision: Error-Focused path",
                                 reason=f"syntax errors={len(syntax_errors)}, explain plan={bool(explain_plan_data)}")
                logger.info(
                    f"SQL [{filename}] 경로: Error-Focused | "
                    f"syntax errors={len(syntax_errors)}, 튜닝포인트={tuning_count}"
                )
            else:
                self.rl.decision("Decision: Heuristic path", reason="문법 에러 없음")
                logger.info(f"SQL [{filename}] 경로: Heuristic | 문법 에러 없음")

            prompt, messages = self._build_messages(
                file=file,
                file_content=file_content,
                syntax_errors=syntax_result.get("syntax_errors", []),
                syntax_warnings=syntax_result.get("warnings", []),
                static_patterns=static_patterns,
                file_context=file_context,
                explain_plan_data=explain_plan_data,
            )

            response = await self.call_llm(messages, json_mode=True)
            llm_result = json.loads(response)

            if not isinstance(llm_result, dict):
                raise ValueError(f"LLM 응답이 dict가 아님: {type(llm_result)}")

            llm_issues = llm_result.get("issues", [])

            # LLM 응답의 source 필드 정규화
            for issue in llm_issues:
                if issue.get("source") not in _VALID_SOURCES:
                    issue["source"] = "llm"

            # Step 5.5: Explain Plan 정적 이슈 생성 + LLM 이슈 병합
            static_issues = self._generate_static_issues(
                explain_plan_data, file,
            )
            issues = self._merge_issues(llm_issues, static_issues)

            if static_issues:
                logger.info(
                    f"SQL [{filename}] 병합: LLM {len(llm_issues)}건 + "
                    f"정적 {len(static_issues)}건 → 최종 {len(issues)}건"
                )

            # Step 6: AnalysisResult 생성
            elapsed = time.time() - start_time
            tokens_estimate = (len(prompt) + len(response)) // 4

            result = AnalysisResult.model_validate({
                "task_id": task_id,
                "file": file,
                "language": language,
                "agent": "SQLAnalyzerAgent",
                "issues": issues,
                "analysis_time_seconds": round(elapsed, 2),
                "llm_tokens_used": tokens_estimate,
            })

            # 분석 요약 메트릭
            self._stats = {
                "delivery_mode": "single",
                "total_lines": line_count,
                "total_tokens": tokens_estimate,
                "total_groups": 0,
                "group_stats": [],
            }

            logger.info(
                f"SQL 분석 완료: {file} → {len(result.issues)}개 이슈, "
                f"{result.analysis_time_seconds}초"
            )

            return result.model_dump()

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"SQL 분석 실패: {file}: {e}")
            return AnalysisResult(
                task_id=task_id,
                file=file,
                language=language,
                agent="SQLAnalyzerAgent",
                issues=[],
                analysis_time_seconds=round(elapsed, 2),
                llm_tokens_used=0,
                error=str(e),
            ).model_dump()

    def _check_syntax(self, file: str) -> dict[str, Any]:
        """SQL 문법을 검증한다."""
        try:
            result = self._syntax_checker.execute(file=file)
            return result.data
        except Exception as e:
            logger.warning(f"SQL 문법 검증 실패: {file}: {e}")
            return {"syntax_errors": [], "warnings": []}

    def _search_patterns(self, file: str) -> list[dict[str, Any]]:
        """SQL 파일에서 정적 패턴을 검색한다."""
        all_matches: list[dict[str, Any]] = []

        for pattern_name in _SQL_PATTERNS:
            try:
                result = self._ast_grep.execute(
                    pattern=pattern_name,
                    file=file,
                    language="sql",
                )
                matches = result.data.get("matches", [])
                for match in matches:
                    all_matches.append({
                        "pattern": pattern_name,
                        "line": match.get("line", 0),
                        "content": match.get("content", ""),
                    })
            except Exception as e:
                logger.warning(f"패턴 검색 실패 ({pattern_name}): {e}")

        return all_matches

    def _parse_explain_plan(
        self,
        explain_plan_file: str | None,
    ) -> dict[str, Any] | None:
        """Explain Plan 파일을 파싱한다."""
        if not explain_plan_file:
            return None

        try:
            result = self._explain_plan_parser.execute(file=explain_plan_file)
            if result.success and (result.data.get("steps") or result.data.get("raw_text")):
                return result.data
            return None
        except Exception as e:
            logger.warning(f"Explain Plan 파싱 실패: {explain_plan_file}: {e}")
            return None

    def _build_messages(
        self,
        *,
        file: str,
        file_content: str,
        syntax_errors: list[dict[str, Any]],
        syntax_warnings: list[dict[str, Any]],
        static_patterns: list[dict[str, Any]],
        file_context: dict[str, Any] | None,
        explain_plan_data: dict[str, Any] | None,
    ) -> tuple[str, list[dict[str, str]]]:
        """프롬프트 경로를 선택하고 LLM 메시지를 구성한다."""
        has_errors = bool(syntax_errors or static_patterns)

        # Explain Plan 텍스트 준비
        explain_plan_str = self._format_explain_plan(explain_plan_data)

        if has_errors:
            # Error-Focused 경로
            patterns_str = json.dumps(
                static_patterns, ensure_ascii=False, indent=2,
            )
            syntax_errors_str = json.dumps(
                syntax_errors + syntax_warnings, ensure_ascii=False, indent=2,
            ) if (syntax_errors or syntax_warnings) else ""

            file_context_str = json.dumps(
                file_context, ensure_ascii=False, indent=2,
            ) if file_context else "컨텍스트 정보 없음"

            prompt = load_prompt(
                "sql_analyzer_error_focused",
                static_patterns=patterns_str,
                file_path=file,
                file_content=file_content,
                file_context=file_context_str,
                syntax_errors=syntax_errors_str,
                explain_plan=explain_plan_str,
            )
        else:
            # Heuristic 경로
            prompt = load_prompt(
                "sql_analyzer_heuristic",
                file_path=file,
                file_content=file_content,
                explain_plan=explain_plan_str,
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 Oracle SQL 성능 최적화 및 품질 분석 전문가(DBA)입니다. "
                    "반드시 JSON 형식으로 응답하세요."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        return prompt, messages

    @staticmethod
    def _generate_static_issues(
        explain_plan_data: dict[str, Any] | None,
        file: str,
    ) -> list[dict[str, Any]]:
        """HIGH/CRITICAL 튜닝 포인트를 정적 이슈로 변환한다.

        LLM 비결정성과 무관하게 핵심 튜닝 포인트가 항상 이슈로 보고되도록 한다.
        """
        if not explain_plan_data:
            return []

        tuning_points = explain_plan_data.get("tuning_points", [])
        if not tuning_points:
            return []

        issues: list[dict[str, Any]] = []
        idx = 1

        for tp in tuning_points:
            severity = tp.get("severity", "")
            if severity not in ("critical", "high"):
                continue

            operation = tp.get("operation", "")
            obj = tp.get("object", "")
            cost = tp.get("cost", "")
            suggestion = tp.get("suggestion", "")
            upper_op = operation.upper()

            # CARTESIAN JOIN → critical
            if "CARTESIAN" in upper_op:
                issues.append({
                    "issue_id": f"SQL-S{idx:03d}",
                    "category": "performance",
                    "severity": "critical",
                    "title": "MERGE JOIN CARTESIAN 발생 — JOIN 조건 누락 가능",
                    "description": (
                        f"Explain Plan에서 {operation}"
                        f"{f' ({obj})' if obj else ''}"
                        f"{f' Cost={cost}' if cost else ''}이 탐지되었습니다. "
                        f"JOIN 조건이 누락되었거나 불필요한 Cartesian Product가 "
                        f"발생하여 대량 데이터에서 심각한 성능 저하를 유발합니다."
                    ),
                    "location": {
                        "file": file,
                        "line_start": 0,
                        "line_end": 0,
                    },
                    "fix": {
                        "before": operation,
                        "after": "JOIN 조건을 추가하여 Cartesian Product를 방지",
                        "description": "JOIN 조건을 명확히 추가하여 Cartesian Product를 방지합니다.",
                    },
                    "source": "static_analysis",
                })
                idx += 1

            # PK 인덱스 고비용 RANGE SCAN → high
            elif "INDEX" in upper_op and "RANGE SCAN" in upper_op and "_PK" in (obj or "").upper():
                # suggestion에서 컬럼 힌트 추출
                hint = ""
                if "/*+" in suggestion and "*/" in suggestion:
                    hint_start = suggestion.index("/*+")
                    hint_end = suggestion.index("*/", hint_start) + 2
                    hint = suggestion[hint_start:hint_end]

                issues.append({
                    "issue_id": f"SQL-S{idx:03d}",
                    "category": "performance",
                    "severity": "high",
                    "title": f"PK 인덱스 비효율 — {obj} Cost={cost}",
                    "description": (
                        f"Explain Plan에서 {obj} PK 인덱스가 INDEX RANGE SCAN에 "
                        f"사용되지만 Cost={cost}으로 높습니다. "
                        f"조인 컬럼이 PK 선두 컬럼이 아니라 비효율적입니다. "
                        f"{suggestion}"
                    ),
                    "location": {
                        "file": file,
                        "line_start": 0,
                        "line_end": 0,
                    },
                    "fix": {
                        "before": f"INDEX RANGE SCAN ({obj})",
                        "after": hint or f"조인 컬럼 기반 인덱스 힌트 추가 필요",
                        "description": suggestion,
                    },
                    "source": "static_analysis",
                })
                idx += 1

            # TABLE ACCESS FULL + 높은 Cost → high
            elif "TABLE ACCESS FULL" in upper_op:
                issues.append({
                    "issue_id": f"SQL-S{idx:03d}",
                    "category": "performance",
                    "severity": "high",
                    "title": f"Full Table Scan — {obj}"
                            f"{f' Cost={cost}' if cost else ''}",
                    "description": (
                        f"Explain Plan에서 {obj} 테이블에 대해 TABLE ACCESS FULL이 "
                        f"발생합니다. WHERE 조건 컬럼에 인덱스가 없거나 "
                        f"인덱스 억제 패턴이 사용되고 있습니다. {suggestion}"
                    ),
                    "location": {
                        "file": file,
                        "line_start": 0,
                        "line_end": 0,
                    },
                    "fix": {
                        "before": f"TABLE ACCESS FULL ({obj})",
                        "after": f"/*+ INDEX({obj.lower()} (조건_컬럼)) */",
                        "description": "WHERE 조건 컬럼 기반 인덱스 힌트를 추가합니다.",
                    },
                    "source": "static_analysis",
                })
                idx += 1

        # 같은 object에 대한 중복 제거 (첫 번째만 유지)
        seen_objects: set[str] = set()
        unique_issues: list[dict[str, Any]] = []
        for issue in issues:
            obj_key = issue.get("fix", {}).get("before", "")
            if obj_key in seen_objects:
                continue
            seen_objects.add(obj_key)
            unique_issues.append(issue)

        return unique_issues

    @staticmethod
    def _merge_issues(
        llm_issues: list[dict[str, Any]],
        static_issues: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """LLM 이슈와 정적 이슈를 병합한다.

        같은 object에 대한 이슈가 양쪽에 있으면 LLM 이슈를 우선한다.
        LLM이 놓친 정적 이슈만 추가하여 탐지 누락을 방지한다.
        """
        if not static_issues:
            return llm_issues

        if not llm_issues:
            merged = static_issues
        else:
            # LLM 이슈의 텍스트에서 object 이름 추출
            llm_text = " ".join(
                json.dumps(issue, ensure_ascii=False).lower()
                for issue in llm_issues
            )

            merged = list(llm_issues)
            for static_issue in static_issues:
                # 정적 이슈의 object 이름이 LLM 이슈에 포함되는지 확인
                obj = static_issue.get("fix", {}).get("before", "")
                # object 이름에서 핵심 키워드 추출 (테이블명, 인덱스명)
                raw_keywords = [
                    w for w in obj.replace("(", " ").replace(")", " ").split()
                    if len(w) > 3 and w.upper() not in (
                        "INDEX", "RANGE", "SCAN", "TABLE", "ACCESS", "FULL",
                        "MERGE", "JOIN", "CARTESIAN",
                    )
                ]
                # 인덱스 접미사 제거하여 테이블명도 매칭
                obj_keywords: list[str] = []
                for kw in raw_keywords:
                    obj_keywords.append(kw)
                    # _PK, _N1 등 접미사 제거하여 베이스 테이블명 추가
                    base = re.sub(r"_(?:PK|N\d+|U\d+|IX\d*)$", "", kw, flags=re.IGNORECASE)
                    if base != kw and len(base) > 3:
                        obj_keywords.append(base)

                # 키워드 중 하나라도 LLM 이슈에 있으면 중복으로 판단
                is_duplicate = any(
                    kw.lower() in llm_text for kw in obj_keywords
                )

                if not is_duplicate:
                    merged.append(static_issue)

        # issue_id 재번호 (SQL-001부터 순차)
        for i, issue in enumerate(merged, 1):
            issue["issue_id"] = f"SQL-{i:03d}"

        return merged

    @staticmethod
    def _format_explain_plan(
        explain_plan_data: dict[str, Any] | None,
    ) -> str:
        """Explain Plan 데이터를 LLM 프롬프트용 텍스트로 변환한다.

        대형 Explain Plan(step 100개 이상)은 고비용 step만 필터링하여
        LLM이 핵심 튜닝 포인트에 집중할 수 있도록 한다.
        """
        if not explain_plan_data:
            return ""

        parts: list[str] = []
        steps = explain_plan_data.get("steps", [])

        # 대형 Explain Plan: 고비용 step만 필터링 (Cost≥50 또는 TABLE ACCESS)
        if len(steps) > 100:
            high_cost_steps = [
                s for s in steps
                if (isinstance(s.get("cost"), int) and s["cost"] >= 50)
                or "TABLE ACCESS" in s.get("operation", "").upper()
                or "MERGE JOIN" in s.get("operation", "").upper()
                or "CARTESIAN" in s.get("operation", "").upper()
            ]
            parts.append(
                f"[Explain Plan — 전체 {len(steps)}개 step 중 "
                f"고비용/핵심 {len(high_cost_steps)}개]"
            )
            parts.append("Id | Operation | Object | Cost | Rows")
            parts.append("---|-----------|--------|------|-----")
            for s in high_cost_steps:
                sid = s.get("id", "")
                op = s.get("operation", "")
                name = s.get("name", "")
                cost = s.get("cost", "")
                rows = s.get("rows", "")
                parts.append(f"{sid} | {op} | {name} | {cost} | {rows}")

            # Predicate 정보 (고비용 step만)
            pred_lines: list[str] = []
            for s in high_cost_steps:
                preds = s.get("predicates", [])
                if preds:
                    for pred in preds:
                        p = pred[:200] + "..." if len(pred) > 200 else pred
                        pred_lines.append(f"  {s.get('id', '?')} - {p}")
            if pred_lines:
                parts.append("")
                parts.append("Predicate Information (고비용 step):")
                parts.extend(pred_lines)
        else:
            # 소형 Explain Plan: 전체 테이블 사용
            formatted_table = explain_plan_data.get("formatted_table", "")
            if formatted_table:
                parts.append(f"[Explain Plan]\n{formatted_table}")
            else:
                raw_text = explain_plan_data.get("raw_text", "")
                if raw_text:
                    if len(raw_text) > 8000:
                        raw_text = raw_text[:8000] + "\n... (truncated)"
                    parts.append(f"[원본 Explain Plan]\n{raw_text}")

        # 튜닝 포인트 요약 (severity 순 정렬, 상위 20개)
        tuning_points = explain_plan_data.get("tuning_points", [])
        if tuning_points:
            severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            sorted_tp = sorted(
                tuning_points,
                key=lambda x: (
                    severity_rank.get(x.get("severity", "low"), 4),
                    -(x.get("cost", 0) if isinstance(x.get("cost"), int) else 0),
                ),
            )
            max_tp = 20
            shown_tp = sorted_tp[:max_tp]

            parts.append(
                f"\n[자동 탐지된 튜닝 포인트 — "
                f"심각도 순 상위 {len(shown_tp)}/{len(tuning_points)}개]"
            )
            for tp in shown_tp:
                op = tp.get("operation", "")
                obj = tp.get("object", "")
                cost = tp.get("cost", "")
                rows = tp.get("rows", "")
                sev = tp.get("severity", "")
                suggestion = tp.get("suggestion", "")
                parts.append(
                    f"- [{sev.upper()}] {op}"
                    f"{f' ({obj})' if obj else ''}"
                    f"{f' Cost={cost}' if cost else ''}"
                    f"{f' Rows={rows}' if rows else ''}"
                    f": {suggestion}"
                )

        return "\n".join(parts)
