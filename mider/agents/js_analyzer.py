"""JavaScriptAnalyzerAgent: Phase 2 - JavaScript 분석.

ESLint 정적분석 + LLM 심층분석을 결합하여
JavaScript 파일의 장애 유발 패턴을 탐지한다.
"""

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
from mider.tools.static_analysis.eslint_runner import ESLintRunner
from mider.tools.static_analysis.js_heuristic_scanner import JSHeuristicScanner

logger = logging.getLogger(__name__)


class JavaScriptAnalyzerAgent(BaseAgent):
    """Phase 2: JavaScript 파일을 분석하는 Agent.

    ESLint 정적분석 결과와 파일 전체 코드를 LLM에 전달하여
    장애 유발 패턴을 탐지한다.
    """

    def __init__(
        self,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        _name = "js_analyzer"
        model = model or get_agent_model(_name)
        fallback_model = fallback_model or get_agent_fallback_model(_name)
        temperature = temperature if temperature is not None else get_agent_temperature(_name)
        super().__init__(
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
        )
        self._file_reader = FileReader()
        self._eslint_runner = ESLintRunner()
        self._heuristic_scanner = JSHeuristicScanner()

    async def run(
        self,
        *,
        task_id: str,
        file: str,
        language: str = "javascript",
        file_context: dict[str, Any] | None = None,
        file_content: str | None = None,
    ) -> dict[str, Any]:
        """JavaScript 파일을 분석한다.

        Args:
            task_id: ExecutionPlan의 task_id
            file: 분석할 파일 경로
            language: 파일 언어 ("javascript")
            file_context: Phase 1에서 수집한 파일 컨텍스트
            file_content: 주석 제거된 파일 내용 (None이면 직접 읽음)

        Returns:
            AnalysisResult 형식의 딕셔너리
        """
        start_time = time.time()
        logger.info(f"JS 분석 시작: {file}")

        try:
            # Step 1: 파일 읽기
            if file_content is None:
                read_result = self._file_reader.execute(path=file)
                file_content = read_result.data["content"]
            line_count = len(file_content.splitlines())
            filename = Path(file).name
            self.rl.scan(f"File: [sky_blue2]{filename}[/sky_blue2] ({line_count}줄)")

            # Step 2: ESLint 정적분석
            eslint_data = self._run_eslint(file)
            if eslint_data:
                err_count = len(eslint_data.get("errors", []))
                warn_count = len(eslint_data.get("warnings", []))
                self.rl.scan(f"ESLint: errors={err_count}, warnings={warn_count}")
                logger.info(
                    f"JS [{filename}] ESLint: errors={err_count}, warnings={warn_count}"
                )
            else:
                logger.info(f"JS [{filename}] ESLint: 없음 (코드만 분석)")

            # Step 2.5: Heuristic Scanner (regex, 비용 0)
            scanner_findings = self._run_heuristic_scanner(file)
            if scanner_findings:
                for finding in scanner_findings:
                    self.rl.detect(
                        f"Scanner [{finding['pattern_id']}] L{finding['line']}: "
                        f"{finding['description'][:80]}"
                    )
                logger.info(
                    f"JS [{filename}] Scanner: {len(scanner_findings)}건"
                )

            # Step 3: LLM 분석 (전체 코드 + ESLint + Scanner 결과)
            prompt, messages = self._build_messages(
                file=file,
                file_content=file_content,
                eslint_data=eslint_data,
                scanner_findings=scanner_findings,
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

        실행 실패 시 None을 반환한다.
        """
        try:
            result = self._eslint_runner.execute(file=file)
            if result.data.get("skipped"):
                return None
            errors = result.data.get("errors", [])
            warnings = result.data.get("warnings", [])
            if errors or warnings:
                return {"errors": errors, "warnings": warnings}
            return None
        except Exception as e:
            logger.warning(f"ESLint 실행 실패: {e}")
            return None

    def _run_heuristic_scanner(self, file: str) -> list[dict[str, Any]]:
        """Heuristic Scanner를 실행하여 findings를 반환한다."""
        try:
            result = self._heuristic_scanner.execute(file=file)
            return result.data.get("findings", [])
        except Exception as e:
            logger.warning(f"JS Heuristic Scanner 실행 실패: {e}")
            return []

    def _build_messages(
        self,
        *,
        file: str,
        file_content: str,
        eslint_data: dict[str, Any] | None,
        scanner_findings: list[dict[str, Any]] | None = None,
        file_context: dict[str, Any] | None,
    ) -> tuple[str, list[dict[str, str]]]:
        """LLM 메시지를 구성한다.

        파일 전체 코드 + ESLint + Scanner 결과를 단일 프롬프트로 전달한다.

        Returns:
            (prompt_text, messages) 튜플
        """
        eslint_results_str = json.dumps(
            eslint_data, ensure_ascii=False, indent=2,
        ) if eslint_data else "ESLint 결과 없음"

        scanner_str = json.dumps(
            scanner_findings, ensure_ascii=False, indent=2,
        ) if scanner_findings else "Scanner 결과 없음"

        file_context_str = json.dumps(
            file_context, ensure_ascii=False, indent=2,
        ) if file_context else "컨텍스트 정보 없음"

        prompt = load_prompt(
            "js_analyzer",
            file_path=file,
            file_content=file_content,
            eslint_results=eslint_results_str,
            scanner_findings=scanner_str,
            file_context=file_context_str,
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
