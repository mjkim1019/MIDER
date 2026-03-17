"""ProCAnalyzerAgent: Phase 2 - Pro*C 분석.

Oracle proc 프리컴파일러 + SQLExtractor + LLM 심층분석을 결합하여
Pro*C 파일의 데이터 무결성 위협 패턴을 탐지한다.
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
from mider.tools.static_analysis.proc_runner import ProcRunner
from mider.tools.utility.sql_extractor import SQLExtractor
from mider.tools.utility.token_optimizer import (
    build_structure_summary,
    extract_error_functions,
    optimize_file_content,
)

logger = logging.getLogger(__name__)


class ProCAnalyzerAgent(BaseAgent):
    """Phase 2: Pro*C 파일을 분석하는 Agent.

    proc 프리컴파일러 에러 + SQL 블록 추출 결과를 기반으로
    LLM이 심층 분석하여 Error-Focused 또는 Heuristic 경로로
    데이터 무결성 이슈를 탐지한다.
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
            line_count = len(file_content.splitlines())
            self.rl.scan(f"File: [sky_blue2]{Path(file).name}[/sky_blue2] ({line_count}줄)")

            # Step 2: proc 프리컴파일러 실행
            proc_errors = self._run_proc(file)
            if proc_errors:
                self.rl.scan(f"proc: {len(proc_errors)}건 에러")

            # Step 3: SQL 블록 추출
            sql_blocks = self._extract_sql_blocks(file)
            if sql_blocks:
                # EXEC SQL 함수명 표시
                sql_funcs = {b.get("function", "?") for b in sql_blocks if b.get("function")}
                self.rl.scan(
                    f"EXEC SQL: {len(sql_blocks)}개 블록 "
                    f"([sky_blue2]{', '.join(sorted(sql_funcs)[:5])}[/sky_blue2])"
                )

            # Step 4: Error-Focused / Heuristic 판정
            has_proc_errors = bool(proc_errors)
            has_missing_sqlca = any(
                not block.get("has_sqlca_check", True)
                for block in sql_blocks
            )
            use_error_focused = has_proc_errors or has_missing_sqlca
            if use_error_focused:
                self.rl.decision(
                    "Decision: Error-Focused path",
                    reason=f"proc errors={len(proc_errors or [])}, SQLCA 미검사={has_missing_sqlca}",
                )
            else:
                self.rl.decision("Decision: Heuristic path", reason="proc 에러 없음, SQLCA 정상")

            # Step 5: LLM 분석
            prompt, messages = self._build_messages(
                file=file,
                file_content=file_content,
                proc_errors=proc_errors,
                sql_blocks=sql_blocks,
                file_context=file_context,
                use_error_focused=use_error_focused,
            )

            response = await self.call_llm(messages, json_mode=True)
            llm_result = json.loads(response)

            if not isinstance(llm_result, dict):
                raise ValueError(f"LLM 응답이 dict가 아님: {type(llm_result)}")

            issues = llm_result.get("issues", [])

            # Step 6: AnalysisResult 생성
            elapsed = time.time() - start_time
            tokens_estimate = (len(prompt) + len(response)) // 4

            result = AnalysisResult.model_validate({
                "task_id": task_id,
                "file": file,
                "language": language,
                "agent": "ProCAnalyzerAgent",
                "issues": issues,
                "analysis_time_seconds": round(elapsed, 2),
                "llm_tokens_used": tokens_estimate,
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

    def _run_proc(self, file: str) -> list[dict[str, Any]]:
        """proc 프리컴파일러를 실행하여 에러 목록을 반환한다.

        실행 실패 시 빈 리스트를 반환한다.
        """
        try:
            result = self._proc_runner.execute(file=file)
            return result.data.get("errors", [])
        except Exception as e:
            logger.warning(f"proc 실행 실패, 에러 정보 없이 진행: {e}")
            return []

    def _extract_sql_blocks(self, file: str) -> list[dict[str, Any]]:
        """SQL 블록을 추출한다.

        추출 실패 시 빈 리스트를 반환한다.
        """
        try:
            result = self._sql_extractor.execute(file=file)
            return result.data.get("sql_blocks", [])
        except Exception as e:
            logger.warning(f"SQL 블록 추출 실패: {e}")
            return []

    def _build_messages(
        self,
        *,
        file: str,
        file_content: str,
        proc_errors: list[dict[str, Any]],
        sql_blocks: list[dict[str, Any]],
        file_context: dict[str, Any] | None,
        use_error_focused: bool,
    ) -> tuple[str, list[dict[str, str]]]:
        """프롬프트 경로를 선택하고 LLM 메시지를 구성한다.

        Returns:
            (prompt_text, messages) 튜플
        """
        sql_blocks_str = json.dumps(
            sql_blocks, ensure_ascii=False, indent=2,
        )

        if use_error_focused:
            # Error-Focused 경로
            proc_errors_str = json.dumps(
                proc_errors, ensure_ascii=False, indent=2,
            )
            file_context_str = json.dumps(
                file_context, ensure_ascii=False, indent=2,
            ) if file_context else "컨텍스트 정보 없음"

            # 에러 라인 추출
            error_lines = []
            for item in proc_errors:
                if isinstance(item, dict) and "line" in item:
                    error_lines.append(item["line"])
            for block in sql_blocks:
                if not block.get("has_sqlca_check", True) and "line" in block:
                    error_lines.append(block["line"])

            # 토큰 최적화
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

            prompt = load_prompt(
                "proc_analyzer_error_focused",
                proc_errors=proc_errors_str,
                sql_blocks=sql_blocks_str,
                file_path=file,
                structure_summary=structure_summary,
                error_functions=error_functions_str,
                file_context=file_context_str,
            )
        else:
            # Heuristic 경로
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
