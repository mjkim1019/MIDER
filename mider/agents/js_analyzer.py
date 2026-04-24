"""JavaScriptAnalyzerAgent: Phase 2 - JavaScript 분석.

ESLint 정적분석 + LLM 심층분석을 결합하여
JavaScript 파일의 장애 유발 패턴을 탐지한다.
"""

import asyncio
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
    get_js_grouping_config,
)
from mider.models.analysis_result import AnalysisResult
from mider.tools.file_io.file_reader import FileReader
from mider.tools.static_analysis.eslint_runner import ESLintRunner
from mider.tools.static_analysis.js_heuristic_scanner import JSHeuristicScanner
from mider.tools.utility.token_optimizer import (
    build_structure_summary,
    split_js_into_chunks,
)

logger = logging.getLogger(__name__)


class JavaScriptAnalyzerAgent(BaseAgent):
    """Phase 2: JavaScript 파일을 분석하는 Agent.

    ESLint 정적분석 결과와 파일 전체 코드를 LLM에 전달하여
    장애 유발 패턴을 탐지한다.

    대형 파일(group_hard_cap_lines 초과)은 함수 경계 기준으로 청크 분할 후
    각 청크를 개별 LLM 호출로 분석한다.
    """

    _MAX_CONCURRENT_LLM = 5

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
        llm_error: str | None = None
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

            # Step 3: LLM 분석 — 대형 파일은 청크 분할
            target_lines, hard_cap_lines = get_js_grouping_config()

            if line_count > hard_cap_lines:
                self.rl.decision(
                    f"Decision: chunked ({line_count}줄 > {hard_cap_lines}줄 상한)",
                    reason=f"{line_count}줄, 청크 목표 {target_lines}줄",
                )
                logger.info(
                    f"JS [{filename}] {line_count}줄 > {hard_cap_lines}줄 → 청크 분석"
                )
                issues, tokens_estimate, chunk_fails = await self._run_chunked(
                    file=file,
                    file_content=file_content,
                    eslint_data=eslint_data,
                    scanner_findings=scanner_findings or [],
                    file_context=file_context,
                    target_lines=target_lines,
                    hard_cap_lines=hard_cap_lines,
                )
                if chunk_fails > 0 and not issues:
                    llm_error = f"JS 청크 LLM 전체 실패 ({chunk_fails}건)"
            else:
                prompt, messages = self._build_messages(
                    file=file,
                    file_content=file_content,
                    eslint_data=eslint_data,
                    scanner_findings=scanner_findings,
                    file_context=file_context,
                )

                response = await self.call_llm(messages, json_mode=True)
                try:
                    llm_result = json.loads(response)
                except (json.JSONDecodeError, TypeError):
                    stripped_resp = response.strip()
                    if stripped_resp.startswith("[Error:"):
                        logger.warning("JS single LLM 오류 응답: %s", stripped_resp)
                        llm_error = stripped_resp
                    else:
                        logger.warning(
                            "JS single LLM 응답 JSON 파싱 실패 (처음 300자): %s",
                            response[:300],
                        )
                        llm_error = f"LLM 응답 파싱 실패: {response}"
                    issues = []
                    tokens_estimate = 0
                else:
                    if not isinstance(llm_result, dict):
                        raise ValueError(f"LLM 응답이 dict가 아님: {type(llm_result)}")
                    issues = llm_result.get("issues", [])
                    tokens_estimate = (len(prompt) + len(response)) // 4

            # Step 4: AnalysisResult 생성
            # Low 등급 원천 차단 필터링
            issues = [
                issue for issue in issues
                if issue.get("severity", "low").lower() != "low"
            ]

            elapsed = time.time() - start_time

            result = AnalysisResult.model_validate({
                "task_id": task_id,
                "file": file,
                "language": language,
                "agent": "JavaScriptAnalyzerAgent",
                "issues": issues,
                "analysis_time_seconds": round(elapsed, 2),
                "llm_tokens_used": tokens_estimate,
                "error": llm_error,
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

    # ── 청크 분할 분석 (대형 파일) ──

    async def _run_chunked(
        self,
        *,
        file: str,
        file_content: str,
        eslint_data: dict[str, Any] | None,
        scanner_findings: list[dict[str, Any]],
        file_context: dict[str, Any] | None,
        target_lines: int,
        hard_cap_lines: int,
    ) -> tuple[list[dict[str, Any]], int, int]:
        """대형 JS 파일을 청크 단위로 분할하여 분석한다.

        Args:
            file: 파일 경로
            file_content: 파일 전체 내용
            eslint_data: ESLint 결과 (전체 파일 기준)
            scanner_findings: Scanner 결과 (전체 파일 기준)
            file_context: Phase 1 컨텍스트
            target_lines: 목표 청크 크기
            hard_cap_lines: 허용 상한

        Returns:
            (issues, tokens_estimate, fail_count) 튜플
        """
        filename = Path(file).name

        chunks = split_js_into_chunks(file_content, target_lines, hard_cap_lines)
        total_chunks = len(chunks)
        logger.info(f"JS [{filename}] 청크 분할: {total_chunks}개 그룹")

        # 전체 파일 구조 요약 (각 청크에 컨텍스트로 제공)
        structure_summary = build_structure_summary(
            file_content, file_context, "javascript",
        )

        sem = asyncio.Semaphore(self._MAX_CONCURRENT_LLM)
        total_tokens = 0

        async def _analyze_chunk(
            idx: int,
            chunk_code: str,
            start_line: int,
            end_line: int,
        ) -> list[dict[str, Any]]:
            nonlocal total_tokens

            async with sem:
                self.rl.step(
                    f"Chunk [{idx}/{total_chunks}] "
                    f"L{start_line}~L{end_line} 분석 시작"
                )

                # ESLint/Scanner 결과를 청크 범위로 필터링
                chunk_eslint = self._filter_eslint_for_range(
                    eslint_data, start_line, end_line,
                )
                # Scanner 결과 필터링 + 라인번호를 청크 상대값으로 변환
                chunk_scanner = [
                    {**f, "line": f.get("line", 0) - start_line + 1}
                    for f in scanner_findings
                    if start_line <= f.get("line", 0) <= end_line
                ]

                # file_context에 전체 파일 구조 요약 첨부
                chunk_context = dict(file_context) if file_context else {}
                chunk_context["structure_summary"] = structure_summary
                chunk_context["chunk_range"] = (
                    f"L{start_line}~L{end_line} "
                    f"({end_line - start_line + 1}줄, "
                    f"전체 {len(file_content.splitlines())}줄 중)"
                )

                prompt, messages = self._build_messages(
                    file=file,
                    file_content=chunk_code,
                    eslint_data=chunk_eslint,
                    scanner_findings=chunk_scanner or None,
                    file_context=chunk_context,
                )

                response = await self.call_llm(messages, json_mode=True)
                total_tokens += (len(prompt) + len(response)) // 4

                try:
                    llm_result = json.loads(response)
                except (json.JSONDecodeError, TypeError):
                    stripped_resp = response.strip()
                    if stripped_resp.startswith("[Error:"):
                        logger.warning(
                            "JS 청크 [%d/%d] LLM 오류 응답: %s",
                            idx, total_chunks, stripped_resp,
                        )
                    else:
                        logger.warning(
                            "JS 청크 [%d/%d] LLM 응답 JSON 파싱 실패 (처음 300자): %s",
                            idx, total_chunks, response[:300],
                        )
                    return []
                if not isinstance(llm_result, dict):
                    return []

                issues = llm_result.get("issues", [])

                # 청크 상대 라인번호 → 원본 파일 라인번호로 변환
                for issue in issues:
                    loc = issue.get("location", {})
                    if loc.get("line_start"):
                        loc["line_start"] += start_line - 1
                    if loc.get("line_end"):
                        loc["line_end"] += start_line - 1

                if issues:
                    self.rl.step(
                        f"Chunk [{idx}/{total_chunks}] "
                        f"L{start_line}~L{end_line}: {len(issues)}개 이슈"
                    )
                else:
                    self.rl.step(
                        f"Chunk [{idx}/{total_chunks}] "
                        f"L{start_line}~L{end_line}: 이슈 없음"
                    )

                return issues

        tasks = [
            _analyze_chunk(idx, code, s, e)
            for idx, (code, s, e) in enumerate(chunks, 1)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_issues: list[dict[str, Any]] = []
        fail_count = 0
        for result in results:
            if isinstance(result, BaseException):
                logger.warning(f"청크 분석 실패: {result}")
                fail_count += 1
                continue
            all_issues.extend(result)

        # 중복 제거
        before_count = len(all_issues)
        all_issues = self._deduplicate_issues(all_issues)
        if before_count != len(all_issues):
            removed = before_count - len(all_issues)
            self.rl.process(
                f"Dedup: {before_count}건 → {len(all_issues)}건 "
                f"({removed}건 중복 제거)"
            )

        # issue_id 재번호
        for i, issue in enumerate(all_issues):
            issue["issue_id"] = f"JS-{i + 1:03d}"

        logger.info(
            f"JS [{filename}] 청크 분석 완료: {len(all_issues)}개 이슈 "
            f"({total_chunks}개 청크, 실패 {fail_count}건)"
        )
        return all_issues, total_tokens, fail_count

    @staticmethod
    def _filter_eslint_for_range(
        eslint_data: dict[str, Any] | None,
        start_line: int,
        end_line: int,
    ) -> dict[str, Any] | None:
        """ESLint 결과에서 특정 줄 범위의 항목만 필터링한다.

        라인번호를 청크 상대값으로 변환한다.
        """
        if not eslint_data:
            return None

        filtered: dict[str, list] = {"errors": [], "warnings": []}
        for key in ("errors", "warnings"):
            for item in eslint_data.get(key, []):
                line = item.get("line", 0)
                if start_line <= line <= end_line:
                    remapped = dict(item)
                    remapped["line"] = line - start_line + 1
                    end_l = remapped.get("end_line")
                    if end_l:
                        remapped["end_line"] = end_l - start_line + 1
                    filtered[key].append(remapped)

        if filtered["errors"] or filtered["warnings"]:
            return filtered
        return None

    @staticmethod
    def _deduplicate_issues(issues: list[dict]) -> list[dict]:
        """동일 위치+카테고리+제목 이슈 중복 제거."""
        seen: set[tuple] = set()
        deduped: list[dict] = []
        for issue in issues:
            loc = issue.get("location", {})
            key = (
                loc.get("line_start"),
                loc.get("line_end"),
                issue.get("category"),
                issue.get("title"),
            )
            if key not in seen:
                seen.add(key)
                deduped.append(issue)
        return deduped
