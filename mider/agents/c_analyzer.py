"""CAnalyzerAgent: Phase 2 - C 언어 분석.

clang-tidy 정적분석 + LLM 심층분석을 결합하여
C 파일의 메모리 안전성 및 장애 유발 패턴을 탐지한다.
"""

import json
import logging
import time
from typing import Any

from mider.agents.base_agent import BaseAgent
from mider.config.prompt_loader import load_prompt
from mider.models.analysis_result import AnalysisResult
from mider.tools.file_io.file_reader import FileReader
from mider.tools.static_analysis.clang_tidy_runner import ClangTidyRunner

logger = logging.getLogger(__name__)


class CAnalyzerAgent(BaseAgent):
    """Phase 2: C 파일을 분석하는 Agent.

    clang-tidy 정적분석 결과를 기반으로 LLM이 심층 분석하여
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
        self._clang_tidy_runner = ClangTidyRunner()

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

            # Step 2: clang-tidy 정적분석
            clang_data = self._run_clang_tidy(file)

            # Step 3: LLM 분석
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

            # Step 4: AnalysisResult 생성
            elapsed = time.time() - start_time
            tokens_estimate = (len(prompt) + len(response)) // 4

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

    def _run_clang_tidy(self, file: str) -> dict[str, Any] | None:
        """clang-tidy를 실행하여 결과를 반환한다.

        실행 실패 시 None을 반환한다 (Heuristic 모드로 전환).
        """
        try:
            result = self._clang_tidy_runner.execute(file=file)
            warnings = result.data.get("warnings", [])
            if warnings:
                return {"warnings": warnings}
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
            prompt = load_prompt(
                "c_analyzer_error_focused",
                clang_tidy_warnings=clang_warnings_str,
                file_path=file,
                file_content=file_content,
                file_context=file_context_str,
            )
        else:
            # Heuristic 경로
            prompt = load_prompt(
                "c_analyzer_heuristic",
                file_path=file,
                file_content=file_content,
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
