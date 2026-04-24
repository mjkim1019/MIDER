"""OrchestratorAgent: 전체 분석 워크플로우 제어.

Phase 0(분류) → Phase 1(컨텍스트) → Phase 2(분석) → Phase 3(리포트)를
순차 실행하며, 각 Phase의 입출력을 관리한다.
"""

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional, Protocol

from mider.agents.base_agent import BaseAgent
from mider.config.reasoning_logger import ReasoningLogger
from mider.config.settings_loader import (
    get_agent_fallback_model,
    get_agent_model,
)
from mider.agents.c_analyzer import CAnalyzerAgent
from mider.agents.context_collector import ContextCollectorAgent
from mider.agents.js_analyzer import JavaScriptAnalyzerAgent
from mider.agents.proc_analyzer import ProCAnalyzerAgent
from mider.agents.reporter import ReporterAgent
from mider.agents.sql_analyzer import SQLAnalyzerAgent
from mider.agents.task_classifier import TaskClassifierAgent
from mider.agents.xml_analyzer import XMLAnalyzerAgent
from mider.tools.file_io.file_reader import FileReader
from mider.tools.preprocessing.comment_remover import CommentRemover
from mider.tools.search.glob_tool import GlobTool

logger = logging.getLogger(__name__)


# 언어 → Analyzer 매핑
_LANGUAGE_AGENT_MAP: dict[str, type[BaseAgent]] = {
    "javascript": JavaScriptAnalyzerAgent,
    "c": CAnalyzerAgent,
    "proc": ProCAnalyzerAgent,
    "sql": SQLAnalyzerAgent,
    "xml": XMLAnalyzerAgent,
}


class ProgressCallback(Protocol):
    """Phase 진행 상태를 보고하는 콜백 프로토콜.

    Rich Progress Bar 등 외부 UI와 연동할 때 사용한다.
    """

    def __call__(
        self,
        phase: int,
        phase_name: str,
        current: int,
        total: int,
        message: str,
    ) -> None: ...


class OrchestratorAgent(BaseAgent):
    """전체 분석 파이프라인을 조율하는 Agent.

    Phase 0 → 1 → 2 → 3 순차 실행하며 각 단계의 결과를
    다음 단계에 전달한다.

    Attributes:
        session_id: 분석 세션 식별자
        progress_callback: 진행률 콜백 (Optional)
    """

    def __init__(
        self,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float = 0.3,
        progress_callback: Optional[ProgressCallback] = None,
        reasoning_logger: Optional[ReasoningLogger] = None,
    ) -> None:
        _name = "orchestrator"
        model = model or get_agent_model(_name)
        fallback_model = fallback_model or get_agent_fallback_model(_name)
        super().__init__(
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
        )
        self.session_id: str = uuid.uuid4().hex[:12]
        self.progress_callback = progress_callback
        if reasoning_logger is not None:
            self.rl = reasoning_logger
        self._explain_plan_file: str | None = None

        # Tools
        self._glob_tool = GlobTool()
        self._file_reader = FileReader()
        self._comment_remover = CommentRemover()

        # Sub-Agents (lazy init in run)
        self._task_classifier: Optional[TaskClassifierAgent] = None
        self._context_collector: Optional[ContextCollectorAgent] = None
        self._reporter: Optional[ReporterAgent] = None
        self._analyzers: dict[str, BaseAgent] = {}

    async def run(
        self,
        *,
        files: list[str],
        explain_plan_file: str | None = None,
    ) -> dict[str, Any]:
        """전체 분석 파이프라인을 실행한다.

        Args:
            files: 분석 대상 파일 경로 리스트 (glob 패턴 포함 가능)
            explain_plan_file: Explain Plan 결과 파일 경로 (SQL 분석 시 사용)

        Returns:
            {"issue_list": ..., "checklist": ..., "summary": ...,
             "execution_plan": ..., "session_id": ..., "errors": ...}
        """
        self._explain_plan_file = explain_plan_file
        pipeline_start = time.time()
        logger.info(f"분석 시작: session={self.session_id}, 입력 {len(files)}건")

        # Step 0: 입력 파일 검증
        valid_files, file_errors = self._validate_and_expand_files(files)

        if file_errors:
            for err in file_errors:
                logger.warning(f"파일 검증 경고: {err}")

        if not valid_files:
            logger.error("분석 가능한 파일이 없습니다.")
            return self._empty_result(file_errors)

        self._report_progress(0, "입력 검증", 1, 1, f"{len(valid_files)}개 파일 확인")

        # Sub-Agent 초기화 (이미 설정된 경우 유지 — 테스트 시 mock 주입용)
        if self._task_classifier is None:
            self._task_classifier = TaskClassifierAgent()
            self._task_classifier.rl = self.rl
        if self._context_collector is None:
            self._context_collector = ContextCollectorAgent()
            self._context_collector.rl = self.rl
        if self._reporter is None:
            self._reporter = ReporterAgent()
            self._reporter.rl = self.rl

        # Phase 0: Task Classification
        execution_plan = await self._run_phase0(valid_files)

        # Phase 0.5: 주석 제거 전처리 (개인정보 보호)
        cleaned_contents = self._preprocess_comments(execution_plan)

        # Phase 1: Context Collection
        file_context = await self._run_phase1(execution_plan, cleaned_contents)

        # Phase 2: Sequential Analysis
        analysis_results, total_lines = await self._run_phase2(
            execution_plan, file_context, cleaned_contents,
        )

        # Phase 3: Report Generation
        pipeline_elapsed = time.time() - pipeline_start
        file_first_lines = self._collect_first_lines(valid_files)
        report = await self._run_phase3(
            analysis_results=analysis_results,
            total_files=len(valid_files),
            total_lines=total_lines,
            analysis_duration_seconds=pipeline_elapsed,
            file_paths=valid_files,
            file_first_lines=file_first_lines,
        )

        total_elapsed = time.time() - pipeline_start
        logger.info(
            f"분석 완료: session={self.session_id}, "
            f"{total_elapsed:.2f}초 소요"
        )

        # LLM 분석 실패 파일 수집
        analysis_errors = [
            {"file": r.get("file", ""), "error": r.get("error", "")}
            for r in analysis_results
            if r.get("error")
        ]

        return {
            "session_id": self.session_id,
            "execution_plan": execution_plan,
            "issue_list": report["issue_list"],
            "checklist": report["checklist"],
            "summary": report["summary"],
            "deployment_checklist": report["deployment_checklist"],
            "errors": file_errors,
            "analysis_errors": analysis_errors,
        }

    # ──────────────────────────────────────────────
    # Phase 실행
    # ──────────────────────────────────────────────

    async def _run_phase0(
        self,
        valid_files: list[str],
    ) -> dict[str, Any]:
        """Phase 0: TaskClassifierAgent로 파일 분류 및 실행 계획 수립."""
        self._report_progress(0, "파일 분류", 0, 1, "실행 계획 수립 중")
        self.rl.phase_header(0, "TaskClassifierAgent")

        # 입력 정보 로그
        lang_summary = ", ".join(
            f"{Path(f).suffix}: {Path(f).name}" for f in valid_files[:5]
        )
        self.rl.scan(f"입력: {len(valid_files)}개 파일 ({lang_summary})")

        execution_plan = await self._call_agent(
            self._task_classifier,  # type: ignore[arg-type]
            files=valid_files,
        )

        sub_tasks = execution_plan.get("sub_tasks", [])
        est = execution_plan.get("estimated_time_seconds", 0)
        logger.info(f"Phase 0 완료: {len(sub_tasks)}개 태스크, 예상 {est}초")

        self.rl.result(f"Result: {len(sub_tasks)} tasks, 예상 {est}초")
        self._report_progress(0, "파일 분류", 1, 1, f"{len(sub_tasks)}개 태스크 계획")

        return execution_plan

    async def _run_phase1(
        self,
        execution_plan: dict[str, Any],
        cleaned_contents: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Phase 1: ContextCollectorAgent로 파일 컨텍스트 수집."""
        self._report_progress(1, "컨텍스트 수집", 0, 1, "의존성/패턴 분석 중")
        self.rl.phase_header(1, "ContextCollectorAgent")

        file_context = await self._call_agent(
            self._context_collector,  # type: ignore[arg-type]
            execution_plan=execution_plan,
            cleaned_contents=cleaned_contents,
        )

        contexts = file_context.get("file_contexts", [])
        logger.info(f"Phase 1 완료: {len(contexts)}개 파일 컨텍스트")

        self.rl.result(f"Result: {len(contexts)} file contexts collected")
        self._report_progress(1, "컨텍스트 수집", 1, 1, f"{len(contexts)}개 컨텍스트")

        return file_context

    async def _run_phase2(
        self,
        execution_plan: dict[str, Any],
        file_context: dict[str, Any],
        cleaned_contents: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Phase 2: 언어별 Analyzer 순차 호출.

        Returns:
            (analysis_results, total_lines)
        """
        sub_tasks = execution_plan.get("sub_tasks", [])
        total_tasks = len(sub_tasks)
        analysis_results: list[dict[str, Any]] = []
        total_lines = 0

        # 파일별 FileContext 매핑
        context_map = self._build_context_map(file_context)

        self._report_progress(2, "코드 분석", 0, total_tasks, "분석 시작")

        for idx, task in enumerate(sub_tasks):
            try:
                file_path = task["file"]
                language = task["language"]
                task_id = task["task_id"]
            except (KeyError, TypeError) as e:
                logger.error(f"Sub-task 데이터 오류: {e}")
                analysis_results.append({
                    "task_id": task.get("task_id", f"unknown_{idx}") if isinstance(task, dict) else f"unknown_{idx}",
                    "file": task.get("file", "unknown") if isinstance(task, dict) else "unknown",
                    "language": task.get("language", "unknown") if isinstance(task, dict) else "unknown",
                    "agent": "OrchestratorAgent",
                    "issues": [],
                    "analysis_time_seconds": 0.0,
                    "llm_tokens_used": 0,
                    "error": f"Sub-task 데이터 오류: {e}",
                })
                continue

            self._report_progress(
                2, "코드 분석", idx, total_tasks,
                f"{language}: {Path(file_path).name}",
            )

            # 파일 라인 수 집계
            line_count = task.get("metadata", {}).get("line_count", 0)
            total_lines += line_count

            # Phase 2 헤더 (Analyzer 이름 포함)
            agent_cls = _LANGUAGE_AGENT_MAP.get(language)
            agent_name = getattr(agent_cls, "__name__", language.upper() + "Analyzer")
            self.rl.phase_header(2, agent_name)
            self.rl.scan(f"File: {Path(file_path).name} ({line_count}줄, {language})")

            # 디버그 로그: 파일별 시작
            from mider.config.debug_logger import is_enabled as _dbg_on, start_file, end_file, log_info
            if _dbg_on():
                start_file(Path(file_path).name)
                log_info("Phase2", f"언어={language}, 라인={line_count}, agent={agent_name}")

            # 언어별 Analyzer 호출
            result = await self._analyze_single_file(
                task_id=task_id,
                file=file_path,
                language=language,
                file_context=context_map.get(file_path),
                file_content=cleaned_contents.get(file_path) if cleaned_contents else None,
            )

            analysis_results.append(result)

            issues_count = len(result.get("issues", []))
            error = result.get("error")
            if error:
                logger.warning(f"분석 에러: {file_path}: {error}")
                self.rl.detect(f"Error: {error}")
                if _dbg_on():
                    from mider.config.debug_logger import log_error
                    log_error("Phase2", f"분석 에러: {error}")
            else:
                logger.info(
                    f"Phase 2 [{idx + 1}/{total_tasks}]: "
                    f"{file_path} → {issues_count}개 이슈"
                )
                elapsed = result.get("analysis_time_seconds", 0)
                self.rl.result(
                    f"Result: {issues_count} issues, {elapsed}초",
                    issues=result.get("issues", []),
                )

            # 디버그 로그: 파일별 종료
            if _dbg_on():
                log_info("Phase2", f"분석 완료: {issues_count}개 이슈")
                end_file()

        self._report_progress(
            2, "코드 분석", total_tasks, total_tasks, "분석 완료",
        )

        total_issues = sum(len(r.get("issues", [])) for r in analysis_results)
        logger.info(f"Phase 2 완료: {total_tasks}개 파일, {total_issues}개 이슈")

        return analysis_results, total_lines

    def _preprocess_comments(
        self,
        execution_plan: dict[str, Any],
    ) -> dict[str, str]:
        """Phase 0.5: 모든 대상 파일에서 주석을 제거한다.

        개인정보(이름, 사번) 보호를 위해 LLM에 전달하기 전
        주석을 사전 제거한다. 줄번호는 보존된다.
        """
        self._report_progress(0, "주석 제거", 0, 1, "전처리 중")
        self.rl.phase_header(0, "CommentRemover")

        sub_tasks = execution_plan.get("sub_tasks", [])
        cleaned: dict[str, str] = {}
        total_removed = 0

        for task in sub_tasks:
            file_path = task.get("file", "")
            language = task.get("language", "")
            if not file_path or not language:
                continue

            try:
                read_result = self._file_reader.execute(path=file_path)
                original = read_result.data["content"]
                result = self._comment_remover.execute(
                    content=original, language=language,
                )
                cleaned[file_path] = result.data["content"]
                total_removed += result.data.get("removed_count", 0)
            except Exception as e:
                logger.warning(f"주석 제거 실패, 원본 유지: {file_path}: {e}")
                # 실패 시 원본 사용 (FileReader에서 다시 읽히도록 cleaned에 넣지 않음)

        self.rl.result(f"Result: {len(cleaned)}개 파일, {total_removed}건 주석 제거")
        self._report_progress(0, "주석 제거", 1, 1, f"{total_removed}건 제거 완료")

        logger.info(f"주석 제거 완료: {len(cleaned)}개 파일, {total_removed}건")
        return cleaned

    async def _run_phase3(
        self,
        *,
        analysis_results: list[dict[str, Any]],
        total_files: int,
        total_lines: int,
        analysis_duration_seconds: float,
        file_paths: list[str],
        file_first_lines: dict[str, str],
    ) -> dict[str, Any]:
        """Phase 3: ReporterAgent로 통합 리포트 생성."""
        self._report_progress(3, "리포트 생성", 0, 1, "리포트 생성 중")
        self.rl.phase_header(3, "ReporterAgent")

        # 이슈 집계 로그 (Medium 이상만)
        all_issues = [
            i for r in analysis_results for i in r.get("issues", [])
            if i.get("severity", "low").lower() != "low"
        ]
        severity_counts = {}
        for issue in all_issues:
            sev = issue.get("severity", "unknown")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
        sev_str = " ".join(f"{k.upper()}:{v}" for k, v in severity_counts.items())
        self.rl.scan(f"Input: {total_files} files, {len(all_issues)} issues (Medium+ only: {sev_str})")

        report = await self._call_agent(
            self._reporter,  # type: ignore[arg-type]
            analysis_results=analysis_results,
            session_id=self.session_id,
            total_files=total_files,
            total_lines=total_lines,
            analysis_duration_seconds=analysis_duration_seconds,
            file_paths=file_paths,
            file_first_lines=file_first_lines,
        )

        logger.info("Phase 3 완료: 리포트 생성")
        self.rl.result("Result: issue-list + checklist + summary + deployment-checklist")
        self._report_progress(3, "리포트 생성", 1, 1, "완료")

        return report

    # ──────────────────────────────────────────────
    # Sub-Agent 호출
    # ──────────────────────────────────────────────

    async def _call_agent(
        self,
        agent: BaseAgent,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Sub-Agent의 run()을 호출하고 결과를 반환한다.

        Agent 내부에서 이미 에러 처리를 하므로 여기서는
        예외를 그대로 전파한다.
        """
        agent_name = type(agent).__name__
        logger.debug(f"Agent 호출: {agent_name}")

        start = time.time()
        result = await agent.run(**kwargs)
        elapsed = time.time() - start

        logger.debug(f"Agent 완료: {agent_name} ({elapsed:.2f}초)")
        return result

    async def _analyze_single_file(
        self,
        *,
        task_id: str,
        file: str,
        language: str,
        file_context: dict[str, Any] | None,
        file_content: str | None = None,
    ) -> dict[str, Any]:
        """단일 파일을 적절한 Analyzer로 분석한다."""
        agent_cls = _LANGUAGE_AGENT_MAP.get(language)
        if agent_cls is None:
            logger.warning(f"지원하지 않는 언어: {language}, 건너뜀: {file}")
            return {
                "task_id": task_id,
                "file": file,
                "language": language,
                "agent": "OrchestratorAgent",
                "issues": [],
                "analysis_time_seconds": 0.0,
                "llm_tokens_used": 0,
                "error": f"지원하지 않는 언어: {language}",
            }

        # Analyzer 캐싱: 같은 언어는 같은 인스턴스 재사용
        if language not in self._analyzers:
            analyzer = agent_cls()
            analyzer.rl = self.rl
            self._analyzers[language] = analyzer
        analyzer = self._analyzers[language]

        try:
            # SQL 분석 시 Explain Plan 파일 전달
            extra_kwargs: dict[str, Any] = {}
            if language == "sql" and self._explain_plan_file:
                extra_kwargs["explain_plan_file"] = self._explain_plan_file

            return await self._call_agent(
                analyzer,
                task_id=task_id,
                file=file,
                language=language,
                file_context=file_context,
                file_content=file_content,
                **extra_kwargs,
            )
        except Exception as e:
            logger.error(f"Analyzer 실행 실패: {file}: {e}")
            return {
                "task_id": task_id,
                "file": file,
                "language": language,
                "agent": type(analyzer).__name__,
                "issues": [],
                "analysis_time_seconds": 0.0,
                "llm_tokens_used": 0,
                "error": str(e),
            }

    # ──────────────────────────────────────────────
    # 입력 검증 도구
    # ──────────────────────────────────────────────

    def _validate_and_expand_files(
        self,
        files: list[str],
    ) -> tuple[list[str], list[str]]:
        """파일 경로를 검증하고 glob 패턴을 확장한다.

        Returns:
            (valid_files, errors) 튜플
        """
        expanded: list[str] = []
        errors: list[str] = []

        for file_pattern in files:
            # glob 패턴 감지 (* 또는 ? 포함)
            if "*" in file_pattern or "?" in file_pattern:
                matched = self._glob_expand(file_pattern)
                if not matched:
                    errors.append(f"패턴과 매칭되는 파일 없음: {file_pattern}")
                else:
                    expanded.extend(matched)
            else:
                expanded.append(file_pattern)

        # 중복 제거 (순서 유지)
        seen: set[str] = set()
        unique_files: list[str] = []
        for f in expanded:
            resolved = str(Path(f).resolve())
            if resolved not in seen:
                seen.add(resolved)
                unique_files.append(resolved)

        # 존재 여부 및 읽기 권한 검증
        valid_files, validation_errors = self._validate_files(unique_files)
        errors.extend(validation_errors)

        return valid_files, errors

    def _glob_expand(self, pattern: str) -> list[str]:
        """glob 패턴을 확장하여 파일 목록을 반환한다."""
        try:
            # 패턴에서 루트와 glob 부분 분리
            pattern_path = Path(pattern)

            # 절대경로 패턴인 경우 루트를 "/"로 설정
            if pattern_path.is_absolute():
                parts = pattern_path.parts
                # glob 시작 위치 찾기
                root_parts: list[str] = []
                glob_parts: list[str] = []
                in_glob = False
                for part in parts:
                    if "*" in part or "?" in part:
                        in_glob = True
                    if in_glob:
                        glob_parts.append(part)
                    else:
                        root_parts.append(part)
                root = str(Path(*root_parts)) if root_parts else "/"
                glob_pattern = str(Path(*glob_parts)) if glob_parts else ""
            else:
                root = "."
                glob_pattern = pattern

            if not glob_pattern:
                return []

            result = self._glob_tool.execute(
                pattern=glob_pattern, root=root,
            )
            return result.data.get("matched_files", [])

        except Exception as e:
            logger.warning(f"Glob 확장 실패: {pattern}: {e}")
            return []

    def _validate_files(
        self,
        file_paths: list[str],
    ) -> tuple[list[str], list[str]]:
        """파일 존재 여부 및 읽기 권한을 검증한다.

        Returns:
            (valid_files, errors) 튜플
        """
        valid: list[str] = []
        errors: list[str] = []

        for file_path in file_paths:
            path = Path(file_path)

            if not path.exists():
                errors.append(f"파일 없음: {file_path}")
                continue

            if not path.is_file():
                errors.append(f"파일이 아님: {file_path}")
                continue

            if not os.access(file_path, os.R_OK):
                errors.append(f"읽기 권한 없음: {file_path}")
                continue

            # 지원하는 확장자 확인
            ext = path.suffix.lower()
            supported = {".js", ".c", ".h", ".pc", ".sql", ".xml"}
            if ext not in supported:
                errors.append(f"지원하지 않는 확장자: {file_path} ({ext})")
                continue

            valid.append(file_path)

        return valid, errors

    # ──────────────────────────────────────────────
    # 유틸리티
    # ──────────────────────────────────────────────

    @staticmethod
    def _build_context_map(
        file_context: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """FileContext에서 파일 경로 → SingleFileContext 매핑을 생성한다.

        절대경로로 정규화하여 _validate_and_expand_files에서 resolve()한
        경로와 매칭되도록 한다.
        """
        context_map: dict[str, dict[str, Any]] = {}
        for ctx in file_context.get("file_contexts", []):
            file_path = ctx.get("file", "")
            if file_path:
                # 절대경로 정규화 (검증 단계에서 resolve()된 경로와 매칭)
                resolved = str(Path(file_path).resolve())
                context_map[resolved] = ctx
                # 원본 경로도 등록 (resolve 전 경로로 접근하는 경우 대비)
                if resolved != file_path:
                    context_map[file_path] = ctx
        return context_map

    def _collect_first_lines(
        self,
        file_paths: list[str],
    ) -> dict[str, str]:
        """파일별 첫 줄을 수집한다 (C 파일 TP/Module 판별용)."""
        first_lines: dict[str, str] = {}
        for fp in file_paths:
            ext = Path(fp).suffix.lower()
            if ext not in (".c", ".h"):
                continue
            try:
                result = self._file_reader.execute(path=fp)
                content = result.data.get("content", "")
                first_lines[fp] = content.split("\n", 1)[0] if content else ""
            except Exception as e:
                logger.debug(f"첫 줄 읽기 실패: {fp}: {e}")
        return first_lines

    def _report_progress(
        self,
        phase: int,
        phase_name: str,
        current: int,
        total: int,
        message: str,
    ) -> None:
        """진행률 콜백을 호출한다."""
        if self.progress_callback is not None:
            try:
                self.progress_callback(
                    phase=phase,
                    phase_name=phase_name,
                    current=current,
                    total=total,
                    message=message,
                )
            except Exception as e:
                logger.debug(f"Progress 콜백 에러 (무시): {e}")

    def _empty_result(
        self,
        errors: list[str],
    ) -> dict[str, Any]:
        """분석 대상 파일이 없을 때의 빈 결과."""
        return {
            "session_id": self.session_id,
            "execution_plan": {
                "sub_tasks": [],
                "dependencies": {"edges": [], "has_circular": False, "warnings": []},
                "total_files": 0,
                "estimated_time_seconds": 0,
            },
            "issue_list": {
                "generated_at": None,
                "session_id": self.session_id,
                "total_issues": 0,
                "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                "issues": [],
            },
            "checklist": {
                "generated_at": None,
                "session_id": self.session_id,
                "total_checks": 0,
                "items": [],
            },
            "summary": {
                "analysis_metadata": {
                    "session_id": self.session_id,
                    "analyzed_at": None,
                    "total_files": 0,
                    "total_lines": 0,
                    "analysis_duration_seconds": 0.0,
                    "total_llm_tokens": 0,
                },
                "issue_summary": {
                    "total": 0,
                    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                    "by_category": {},
                    "by_language": {},
                    "by_file": {},
                },
                "risk_assessment": {
                    "deployment_risk": "LOW",
                    "deployment_allowed": True,
                    "blocking_issues": [],
                    "risk_description": "분석 대상 파일이 없습니다.",
                },
            },
            "deployment_checklist": {
                "generated_at": None,
                "session_id": self.session_id,
                "total_items": 0,
                "sections": [],
            },
            "errors": errors,
        }
