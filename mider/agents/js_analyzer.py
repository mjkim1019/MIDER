"""JavaScriptAnalyzerAgent: Phase 2 - JavaScript 분석.

ESLint 정적분석 + LLM 심층분석을 결합하여
JavaScript 파일의 장애 유발 패턴을 탐지한다.
"""

import json
import logging
import time
from typing import Any

from mider.agents.base_agent import BaseAgent
from mider.config.prompt_loader import load_prompt
from mider.models.analysis_result import AnalysisResult
from mider.tools.file_io.file_reader import FileReader
from mider.tools.static_analysis.eslint_runner import ESLintRunner

logger = logging.getLogger(__name__)


class JavaScriptAnalyzerAgent(BaseAgent):
    """Phase 2: JavaScript 파일을 분석하는 Agent.

    ESLint 정적분석 결과를 기반으로 LLM이 심층 분석하여
    Error-Focused 또는 Heuristic 경로로 이슈를 탐지한다.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        fallback_model: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        super().__init__(
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
        )
        self._file_reader = FileReader()
        self._eslint_runner = ESLintRunner()

    async def run(
        self,
        *,
        task_id: str,
        file: str,
        language: str = "javascript",
        file_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """JavaScript 파일을 분석한다.

        Args:
            task_id: ExecutionPlan의 task_id
            file: 분석할 파일 경로
            language: 파일 언어 ("javascript")
            file_context: Phase 1에서 수집한 파일 컨텍스트

        Returns:
            AnalysisResult 형식의 딕셔너리
        """
        start_time = time.time()
        logger.info(f"JS 분석 시작: {file}")

        try:
            # Step 1: 파일 읽기
            read_result = self._file_reader.execute(path=file)
            file_content = read_result.data["content"]

            # Step 2: ESLint 정적분석
            eslint_data = self._run_eslint(file)

            # Step 3: LLM 분석
            prompt, messages = self._build_messages(
                file=file,
                file_content=file_content,
                eslint_data=eslint_data,
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
                "agent": "JavaScriptAnalyzerAgent",
                "issues": issues,
                "analysis_time_seconds": round(elapsed, 2),
                "llm_tokens_used": tokens_estimate,
            })

            logger.info(
                f"JS 분석 완료: {file} → {len(result.issues)}개 이슈, "
                f"{result.analysis_time_seconds}초"
            )

            return result.model_dump()

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"JS 분석 실패: {file}: {e}")
            return AnalysisResult(
                task_id=task_id,
                file=file,
                language=language,
                agent="JavaScriptAnalyzerAgent",
                issues=[],
                analysis_time_seconds=round(elapsed, 2),
                llm_tokens_used=0,
                error=str(e),
            ).model_dump()

    def _run_eslint(self, file: str) -> dict[str, Any] | None:
        """ESLint를 실행하여 결과를 반환한다.

        실행 실패 시 None을 반환한다 (Heuristic 모드로 전환).
        """
        try:
            result = self._eslint_runner.execute(file=file)
            errors = result.data.get("errors", [])
            warnings = result.data.get("warnings", [])
            if errors or warnings:
                return {"errors": errors, "warnings": warnings}
            return None
        except Exception as e:
            logger.warning(f"ESLint 실행 실패, Heuristic 모드로 전환: {e}")
            return None

    def _build_messages(
        self,
        *,
        file: str,
        file_content: str,
        eslint_data: dict[str, Any] | None,
        file_context: dict[str, Any] | None,
    ) -> tuple[str, list[dict[str, str]]]:
        """프롬프트 경로를 선택하고 LLM 메시지를 구성한다.

        Returns:
            (prompt_text, messages) 튜플
        """
        if eslint_data:
            # Error-Focused 경로
            eslint_errors_str = json.dumps(
                eslint_data, ensure_ascii=False, indent=2,
            )
            file_context_str = json.dumps(
                file_context, ensure_ascii=False, indent=2,
            ) if file_context else "컨텍스트 정보 없음"
            prompt = load_prompt(
                "js_analyzer_error_focused",
                eslint_errors=eslint_errors_str,
                file_path=file,
                file_content=file_content,
                file_context=file_context_str,
            )
        else:
            # Heuristic 경로
            prompt = load_prompt(
                "js_analyzer_heuristic",
                file_path=file,
                file_content=file_content,
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 JavaScript/TypeScript 보안 및 품질 분석 전문가입니다. "
                    "반드시 JSON 형식으로 응답하세요."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        return prompt, messages
