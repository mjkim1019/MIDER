"""CAnalyzerAgent: Phase 2 - C 언어 분석.

clang-tidy 정적분석 + LLM 심층분석을 결합하여
C 파일의 메모리 안전성 및 장애 유발 패턴을 탐지한다.

clang-tidy 없고 대형 파일(>500줄)이면 2-Pass 전략:
  Pass 1: Heuristic Pre-Scanner → gpt-4o-mini로 위험 함수 선별
  Pass 2: 선별된 함수 → gpt-4o로 심층 분석
"""

import json
import logging
import time
from typing import Any

from mider.agents.base_agent import BaseAgent
from mider.config.prompt_loader import load_prompt
from mider.models.analysis_result import AnalysisResult
from mider.tools.file_io.file_reader import FileReader
from mider.tools.static_analysis.c_heuristic_scanner import CHeuristicScanner
from mider.tools.static_analysis.clang_tidy_runner import ClangTidyRunner
from mider.tools.utility.token_optimizer import (
    build_structure_summary,
    extract_error_functions,
    optimize_file_content,
    find_function_boundaries,
)

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
        self._heuristic_scanner = CHeuristicScanner()

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
            line_count = len(file_content.splitlines())

            # Step 2: clang-tidy 정적분석
            clang_data = self._run_clang_tidy(file)

            # Step 3: 분석 경로 선택
            tokens_estimate = 0
            if not clang_data and line_count > 500:
                # 2-Pass 전략: clang-tidy 없고 대형 파일
                issues = await self._run_two_pass(
                    file=file,
                    file_content=file_content,
                    file_context=file_context,
                )
            else:
                # 기존 경로: clang-tidy 있음 or 500줄 이하
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
                tokens_estimate = (len(prompt) + len(response)) // 4

            # Step 4: AnalysisResult 생성
            elapsed = time.time() - start_time

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

    async def _run_two_pass(
        self,
        *,
        file: str,
        file_content: str,
        file_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """2-Pass 분석: Pre-Scanner → LLM 선별 → 심층 분석.

        Pass 1: regex 스캔 + gpt-4o-mini로 위험 함수 선별
        Pass 2: 선별된 함수 전체 코드 + gpt-4o로 심층 분석
        """
        # Pass 1-a: Heuristic Pre-Scanner (regex, 비용 0)
        scan_result = self._heuristic_scanner.execute(file=file)
        findings = scan_result.data.get("findings", [])
        functions_at_risk = scan_result.data.get("functions_at_risk", [])

        if not findings:
            logger.info(f"Pre-Scanner: 위험 패턴 없음 → 기존 Heuristic 분석: {file}")
            return await self._run_single_pass_heuristic(
                file=file, file_content=file_content, file_context=file_context,
            )

        # Pass 1-b: 함수별 패턴 요약 생성
        func_summary = self._build_function_findings_summary(findings)

        lines = file_content.splitlines()
        boundaries = find_function_boundaries(lines, "c")

        # Pass 1-c: gpt-4o-mini로 위험 함수 선별
        prescan_prompt = load_prompt(
            "c_prescan_fewshot",
            file_path=file,
            total_functions=str(len(boundaries)),
            total_findings=str(len(findings)),
            function_findings_summary=func_summary,
        )

        prescan_messages = [
            {
                "role": "system",
                "content": "당신은 C 코드 안전성 전문가입니다. 반드시 JSON 형식으로 응답하세요.",
            },
            {"role": "user", "content": prescan_prompt},
        ]

        # gpt-4o-mini로 빠르게 선별 (호출 후 원래 모델 복원)
        original_model = self.model
        original_fallback = self.fallback_model
        self.model = "gpt-4o-mini"
        self.fallback_model = None
        try:
            prescan_response = await self.call_llm(prescan_messages, json_mode=True)
        finally:
            self.model = original_model
            self.fallback_model = original_fallback

        prescan_result = json.loads(prescan_response)
        if not isinstance(prescan_result, dict):
            prescan_result = {"risky_functions": []}

        risky_functions = [
            f["function_name"]
            for f in prescan_result.get("risky_functions", [])
            if isinstance(f, dict) and "function_name" in f
        ]

        if not risky_functions:
            # LLM이 위험 함수 없다고 판단 → 기존 Heuristic 경로
            logger.info(f"Pass 1: 위험 함수 없음 (LLM 판단) → Heuristic: {file}")
            return await self._run_single_pass_heuristic(
                file=file, file_content=file_content, file_context=file_context,
            )

        logger.info(
            f"Pass 1 완료: {len(risky_functions)}개 위험 함수 선별 → "
            f"{risky_functions}"
        )

        # Pass 2: 위험 함수 전체 코드 추출 → gpt-4o 심층 분석
        risky_lines = self._get_lines_for_functions(
            risky_functions, lines, boundaries,
        )

        error_blocks = extract_error_functions(file_content, risky_lines, "c")
        error_functions_str = "\n\n".join(
            f"[{block.line_start}~{block.line_end}줄]\n{block.content}"
            for block in error_blocks
        ) if error_blocks else optimize_file_content(file_content, file_context, "c")

        structure_summary = build_structure_summary(file_content, file_context, "c")

        # 스캔 findings를 clang-tidy warnings 형식으로 변환
        scan_warnings_str = json.dumps(
            [
                {
                    "line": f["line"],
                    "message": f"{f['pattern_id']}: {f['description']}",
                    "content": f["content"],
                }
                for f in findings
                if f.get("function") in risky_functions
            ],
            ensure_ascii=False, indent=2,
        )

        file_context_str = json.dumps(
            file_context, ensure_ascii=False, indent=2,
        ) if file_context else "컨텍스트 정보 없음"

        prompt = load_prompt(
            "c_analyzer_error_focused",
            clang_tidy_warnings=scan_warnings_str,
            file_path=file,
            structure_summary=structure_summary,
            error_functions=error_functions_str,
            file_context=file_context_str,
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

        response = await self.call_llm(messages, json_mode=True)
        llm_result = json.loads(response)

        if not isinstance(llm_result, dict):
            return []

        logger.info(f"Pass 2 완료: {len(llm_result.get('issues', []))}개 이슈")
        return llm_result.get("issues", [])

    async def _run_single_pass_heuristic(
        self,
        *,
        file: str,
        file_content: str,
        file_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """기존 Heuristic 단일 패스 분석."""
        file_content_optimized = optimize_file_content(
            file_content, file_context, "c",
        )
        prompt = load_prompt(
            "c_analyzer_heuristic",
            file_path=file,
            file_content_optimized=file_content_optimized,
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
        response = await self.call_llm(messages, json_mode=True)
        llm_result = json.loads(response)
        if not isinstance(llm_result, dict):
            return []
        return llm_result.get("issues", [])

    def _build_function_findings_summary(
        self, findings: list[dict[str, Any]],
    ) -> str:
        """함수별 위험 패턴 요약을 생성한다."""
        func_findings: dict[str, list[dict[str, Any]]] = {}
        for f in findings:
            func = f.get("function") or "(global)"
            func_findings.setdefault(func, []).append(f)

        parts: list[str] = []
        for func, items in func_findings.items():
            parts.append(f"\n### 함수: {func} ({len(items)}개 패턴)")
            for item in items[:10]:  # 함수당 최대 10개
                parts.append(
                    f"- {item['pattern_id']} L{item['line']}: {item['content'][:80]}"
                )
            if len(items) > 10:
                parts.append(f"  ... 외 {len(items) - 10}개")

        return "\n".join(parts)

    def _get_lines_for_functions(
        self,
        function_names: list[str],
        lines: list[str],
        boundaries: list[tuple[int, int]],
    ) -> list[int]:
        """함수명 목록에 해당하는 라인 번호를 반환한다."""
        from mider.tools.static_analysis.c_heuristic_scanner import _FUNC_NAME_PATTERN

        target_lines: list[int] = []
        for start, end in boundaries:
            idx = start - 1
            func_line = lines[idx]
            m = _FUNC_NAME_PATTERN.match(func_line)
            if not m and idx + 1 < len(lines):
                combined = func_line.rstrip() + " " + lines[idx + 1].lstrip()
                m = _FUNC_NAME_PATTERN.match(combined)
            if m and m.group(1) in function_names:
                target_lines.append(start)

        return target_lines

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

            # 에러 라인 추출
            error_lines = []
            for item in clang_data.get("warnings", []):
                if isinstance(item, dict) and "line" in item:
                    error_lines.append(item["line"])

            # 토큰 최적화
            structure_summary = build_structure_summary(
                file_content, file_context, "c",
            )
            error_blocks = extract_error_functions(
                file_content, error_lines, "c",
            )
            error_functions_str = "\n\n".join(
                f"[{block.line_start}~{block.line_end}줄]\n{block.content}"
                for block in error_blocks
            ) if error_blocks else optimize_file_content(
                file_content, file_context, "c",
            )

            prompt = load_prompt(
                "c_analyzer_error_focused",
                clang_tidy_warnings=clang_warnings_str,
                file_path=file,
                structure_summary=structure_summary,
                error_functions=error_functions_str,
                file_context=file_context_str,
            )
        else:
            # Heuristic 경로
            file_content_optimized = optimize_file_content(
                file_content, file_context, "c",
            )
            prompt = load_prompt(
                "c_analyzer_heuristic",
                file_path=file,
                file_content_optimized=file_content_optimized,
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
