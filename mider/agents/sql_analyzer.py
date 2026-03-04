"""SQLAnalyzerAgent: Phase 2 - SQL 분석.

정적 패턴 분석(AstGrepSearch) + LLM 심층분석을 결합하여
SQL 파일의 성능 저하 및 장애 유발 패턴을 탐지한다.
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

logger = logging.getLogger(__name__)

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

    AstGrepSearch로 정적 패턴을 검색한 뒤 LLM이 심층 분석하여
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

    async def run(
        self,
        *,
        task_id: str,
        file: str,
        language: str = "sql",
        file_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """SQL 파일을 분석한다.

        Args:
            task_id: ExecutionPlan의 task_id
            file: 분석할 파일 경로
            language: 파일 언어 ("sql")
            file_context: Phase 1에서 수집한 파일 컨텍스트

        Returns:
            AnalysisResult 형식의 딕셔너리
        """
        start_time = time.time()
        logger.info(f"SQL 분석 시작: {file}")

        try:
            # Step 1: 파일 읽기
            read_result = self._file_reader.execute(path=file)
            file_content = read_result.data["content"]

            # Step 2: 정적 패턴 검색
            static_patterns = self._search_patterns(file)

            # Step 3: LLM 분석
            prompt, messages = self._build_messages(
                file=file,
                file_content=file_content,
                static_patterns=static_patterns,
                file_context=file_context,
            )

            response = await self.call_llm(messages, json_mode=True)
            llm_result = json.loads(response)

            if not isinstance(llm_result, dict):
                raise ValueError(f"LLM 응답이 dict가 아님: {type(llm_result)}")

            issues = llm_result.get("issues", [])

            # Step 4: AnalysisResult 생성
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

    def _search_patterns(self, file: str) -> list[dict[str, Any]]:
        """SQL 파일에서 정적 패턴을 검색한다.

        각 패턴별로 AstGrepSearch를 실행하여 매치 결과를 수집한다.
        """
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

    def _build_messages(
        self,
        *,
        file: str,
        file_content: str,
        static_patterns: list[dict[str, Any]],
        file_context: dict[str, Any] | None,
    ) -> tuple[str, list[dict[str, str]]]:
        """프롬프트 경로를 선택하고 LLM 메시지를 구성한다.

        Returns:
            (prompt_text, messages) 튜플
        """
        if static_patterns:
            # Error-Focused 경로
            patterns_str = json.dumps(
                static_patterns, ensure_ascii=False, indent=2,
            )
            file_context_str = json.dumps(
                file_context, ensure_ascii=False, indent=2,
            ) if file_context else "컨텍스트 정보 없음"

            prompt = load_prompt(
                "sql_analyzer_error_focused",
                static_patterns=patterns_str,
                file_path=file,
                file_content=file_content,
                file_context=file_context_str,
            )
        else:
            # Heuristic 경로
            prompt = load_prompt(
                "sql_analyzer_heuristic",
                file_path=file,
                file_content=file_content,
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
