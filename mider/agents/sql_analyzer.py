"""SQLAnalyzerAgent: Phase 2 - SQL 분석.

문법 검증(sqlparse) + 정적 패턴 분석(AstGrepSearch) + Explain Plan 해석 +
LLM 심층분석을 결합하여 SQL 파일의 성능 저하 및 장애 유발 패턴을 탐지한다.
"""

import json
import logging
import time
from typing import Any

from mider.agents.base_agent import BaseAgent
from mider.config.prompt_loader import load_prompt
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


class SQLAnalyzerAgent(BaseAgent):
    """Phase 2: SQL 파일을 분석하는 Agent.

    문법 검증 + 정적 패턴 검색 + Explain Plan 해석 후 LLM이 심층 분석하여
    Error-Focused 또는 Heuristic 경로로 이슈를 탐지한다.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        fallback_model: str | None = "gpt-4o",
        temperature: float = 0.0,
    ) -> None:
        super().__init__(
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
        )
        self._file_reader = FileReader()
        self._ast_grep = AstGrepSearch()
        self._syntax_checker = SQLSyntaxChecker()
        self._explain_plan_parser = ExplainPlanParser()

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

            # Step 2: SQL 문법 검증
            syntax_result = self._check_syntax(file)

            # Step 3: 정적 패턴 검색
            static_patterns = self._search_patterns(file)

            # Step 4: Explain Plan 파싱 (옵션)
            explain_plan_data = self._parse_explain_plan(explain_plan_file)

            # Step 5: LLM 분석
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

            issues = llm_result.get("issues", [])

            # LLM 응답의 source 필드 정규화
            for issue in issues:
                if issue.get("source") not in _VALID_SOURCES:
                    issue["source"] = "llm"

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
    def _format_explain_plan(
        explain_plan_data: dict[str, Any] | None,
    ) -> str:
        """Explain Plan 데이터를 LLM 프롬프트용 텍스트로 변환한다."""
        if not explain_plan_data:
            return ""

        parts: list[str] = []

        # Compact 테이블 형식 (우선 사용)
        formatted_table = explain_plan_data.get("formatted_table", "")
        if formatted_table:
            parts.append(f"[Explain Plan]\n{formatted_table}")
        else:
            # Fallback: 원본 텍스트 (대형 파일은 truncate)
            raw_text = explain_plan_data.get("raw_text", "")
            if raw_text:
                if len(raw_text) > 8000:
                    raw_text = raw_text[:8000] + "\n... (truncated)"
                parts.append(f"[원본 Explain Plan]\n{raw_text}")

        # 튜닝 포인트 요약
        tuning_points = explain_plan_data.get("tuning_points", [])
        if tuning_points:
            parts.append("\n[자동 탐지된 튜닝 포인트]")
            for tp in tuning_points:
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
