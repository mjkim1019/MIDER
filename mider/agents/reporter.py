"""ReporterAgent: Phase 3 - 분석 결과 통합 리포트 생성.

Phase 2의 모든 AnalysisResult를 통합하여
IssueList, Checklist, Summary 3개 JSON 리포트를 생성한다.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from mider.agents.base_agent import BaseAgent
from mider.config.prompt_loader import load_prompt
from mider.config.settings_loader import (
    get_agent_fallback_model,
    get_agent_model,
    get_agent_temperature,
)
from mider.models.analysis_result import AnalysisResult
from mider.models.report import (
    AnalysisMetadata,
    Checklist,
    ChecklistItem,
    DeploymentChecklist,
    DeploymentChecklistSection,
    IssueList,
    IssueListItem,
    IssueSummary,
    RiskAssessment,
    Summary,
)
from mider.tools.utility.checklist_generator import ChecklistGenerator
from mider.tools.utility.deployment_checklist import DeploymentChecklistGenerator

logger = logging.getLogger(__name__)

# 심각도 정렬 우선순위
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class ReporterAgent(BaseAgent):
    """Phase 3: 분석 결과를 통합하여 4개 리포트를 생성하는 Agent.

    AnalysisResult 리스트를 받아 IssueList, Checklist, Summary,
    DeploymentChecklist를 생성한다. LLM은 risk_description 한국어 생성에 사용한다.
    """

    def __init__(
        self,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        _name = "reporter"
        model = model or get_agent_model(_name)
        fallback_model = fallback_model or get_agent_fallback_model(_name)
        temperature = temperature if temperature is not None else get_agent_temperature(_name)
        super().__init__(
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
        )
        self._checklist_generator = ChecklistGenerator()
        self._deployment_checklist_generator = DeploymentChecklistGenerator()

    async def run(
        self,
        *,
        analysis_results: list[dict[str, Any]],
        session_id: str,
        total_files: int,
        total_lines: int,
        analysis_duration_seconds: float,
        file_paths: list[str] | None = None,
        file_first_lines: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """분석 결과를 통합하여 4개 리포트를 생성한다.

        Args:
            analysis_results: Phase 2 AnalysisResult dict 리스트
            session_id: 세션 식별자
            total_files: 분석한 총 파일 수
            total_lines: 분석한 총 라인 수
            analysis_duration_seconds: 전체 분석 소요 시간 (초)
            file_paths: 분석 대상 파일 경로 리스트 (배포 체크리스트용)
            file_first_lines: 파일별 첫 줄 내용 (C 파일 TP/Module 판별용)

        Returns:
            {"issue_list": ..., "checklist": ..., "summary": ...,
             "deployment_checklist": ...}
        """
        start_time = time.time()
        step_times: dict[str, float] = {}
        logger.info(f"리포트 생성 시작: {len(analysis_results)}개 분석 결과")

        try:
            generated_at = datetime.now(timezone.utc)

            # Step 1: 모든 이슈를 통합하고 심각도별 정렬
            t = time.time()
            all_issues = self._collect_all_issues(analysis_results)
            sorted_issues = self._sort_issues(all_issues)
            step_times["collect_and_sort_issues"] = time.time() - t

            # Step 2: IssueList 생성
            t = time.time()
            issue_list = self._build_issue_list(
                sorted_issues, generated_at, session_id,
            )
            step_times["build_issue_list"] = time.time() - t

            # Step 3: Checklist 생성 (ChecklistGenerator Tool 사용)
            t = time.time()
            checklist = self._build_checklist(
                analysis_results, generated_at, session_id,
            )
            step_times["build_checklist"] = time.time() - t

            # 분석 에러 감지 (에러 발생 시 분석불가 판정)
            analysis_errors = [
                r for r in analysis_results if r.get("error")
            ]

            # Step 4: Summary 생성 (LLM으로 risk_description 생성)
            total_llm_tokens = sum(
                r.get("llm_tokens_used", 0) for r in analysis_results
            )
            t = time.time()
            summary = await self._build_summary(
                sorted_issues=sorted_issues,
                generated_at=generated_at,
                session_id=session_id,
                total_files=total_files,
                total_lines=total_lines,
                analysis_duration_seconds=analysis_duration_seconds,
                total_llm_tokens=total_llm_tokens,
                analysis_errors=analysis_errors,
                analysis_results=analysis_results,
            )
            step_times["build_summary_with_llm"] = time.time() - t

            # Step 5: 배포 체크리스트 생성
            t = time.time()
            deployment_checklist = self._build_deployment_checklist(
                file_paths=file_paths or [],
                file_first_lines=file_first_lines or {},
                generated_at=generated_at,
                session_id=session_id,
            )
            step_times["build_deployment_checklist"] = time.time() - t

            elapsed = time.time() - start_time

            # T70.1: Reporter 단계별 소요 시간 breakdown 로깅
            self._log_profile_breakdown(step_times, elapsed)

            logger.info(
                f"리포트 생성 완료: {issue_list.total_issues}개 이슈, "
                f"{checklist.total_checks}개 체크항목, "
                f"{deployment_checklist.total_items}개 배포항목, "
                f"{elapsed:.2f}초"
            )

            return {
                "issue_list": issue_list.model_dump(mode="json"),
                "checklist": checklist.model_dump(mode="json"),
                "summary": summary.model_dump(mode="json"),
                "deployment_checklist": deployment_checklist.model_dump(mode="json"),
            }

        except Exception as e:
            logger.error(f"리포트 생성 실패: {e}")
            raise

    def _log_profile_breakdown(
        self,
        step_times: dict[str, float],
        total: float,
    ) -> None:
        """Reporter 각 단계 소요 시간을 stdlib logger + ReasoningLogger에 출력한다.

        T70.1 프로파일링용. stdlib logger는 항상, ReasoningLogger는 verbose 모드에서만.
        """
        logger.info("Reporter 단계별 소요 시간 breakdown:")
        for step, duration in step_times.items():
            pct = (duration / total * 100) if total > 0 else 0
            logger.info(f"  - {step}: {duration:.2f}s ({pct:.1f}%)")
        logger.info(f"  - TOTAL: {total:.2f}s")

        if self.rl.enabled:
            self.rl.scan(f"Reporter 단계별 시간 (총 {total:.2f}s):")
            for step, duration in step_times.items():
                pct = (duration / total * 100) if total > 0 else 0
                self.rl.scan(f"    {step}: {duration:.2f}s ({pct:.1f}%)")

    def _collect_all_issues(
        self,
        analysis_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """모든 AnalysisResult에서 이슈를 통합한다 (Medium 이상만)."""
        all_issues: list[dict[str, Any]] = []

        for result in analysis_results:
            file_path = result.get("file", "")
            language = result.get("language", "")

            for issue in result.get("issues", []):
                severity = issue.get("severity", "low").lower()
                # Medium 이상의 이슈만 수집 (low 제외)
                if severity == "low":
                    continue

                issue_item = {
                    "issue_id": issue.get("issue_id", ""),
                    "file": file_path,
                    "language": language,
                    "category": issue.get("category", "code_quality"),
                    "severity": severity,
                    "title": issue.get("title", ""),
                    "description": issue.get("description", ""),
                    "location": issue.get("location", {
                        "file": file_path,
                        "line_start": 0,
                        "line_end": 0,
                    }),
                    "fix": issue.get("fix", {
                        "before": "",
                        "after": "",
                        "description": "",
                    }),
                    "source": issue.get("source", "llm"),
                }
                all_issues.append(issue_item)

        return all_issues

    def _sort_issues(
        self,
        issues: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """이슈를 심각도순(critical→low), 같은 심각도 내에서는 파일명순 정렬."""
        return sorted(
            issues,
            key=lambda x: (
                _SEVERITY_ORDER.get(x.get("severity", "low"), 3),
                x.get("file", ""),
            ),
        )

    def _build_issue_list(
        self,
        sorted_issues: list[dict[str, Any]],
        generated_at: datetime,
        session_id: str,
    ) -> IssueList:
        """IssueList 모델을 생성한다."""
        by_severity: dict[str, int] = {
            "critical": 0, "high": 0, "medium": 0, "low": 0,
        }
        for issue in sorted_issues:
            severity = issue.get("severity", "low")
            if severity in by_severity:
                by_severity[severity] += 1

        issue_items = [
            IssueListItem.model_validate(issue) for issue in sorted_issues
        ]

        return IssueList(
            generated_at=generated_at,
            session_id=session_id,
            total_issues=len(sorted_issues),
            by_severity=by_severity,
            issues=issue_items,
        )

    def _build_checklist(
        self,
        analysis_results: list[dict[str, Any]],
        generated_at: datetime,
        session_id: str,
    ) -> Checklist:
        """ChecklistGenerator Tool로 체크리스트를 생성한다."""
        tool_result = self._checklist_generator.execute(
            analysis_results=analysis_results,
        )

        items = [
            ChecklistItem.model_validate(item)
            for item in tool_result.data.get("items", [])
        ]

        return Checklist(
            generated_at=generated_at,
            session_id=session_id,
            total_checks=len(items),
            items=items,
        )

    async def _build_summary(
        self,
        *,
        sorted_issues: list[dict[str, Any]],
        generated_at: datetime,
        session_id: str,
        total_files: int,
        total_lines: int,
        analysis_duration_seconds: float,
        total_llm_tokens: int,
        analysis_errors: list[dict[str, Any]] | None = None,
        analysis_results: list[dict[str, Any]] | None = None,
    ) -> Summary:
        """Summary 모델을 생성한다. LLM으로 risk_description을 작성한다."""
        # 통계 집계
        by_severity: dict[str, int] = {
            "critical": 0, "high": 0, "medium": 0, "low": 0,
        }
        by_category: dict[str, int] = {}
        by_language: dict[str, int] = {}
        by_file: dict[str, int] = {}

        for issue in sorted_issues:
            severity = issue.get("severity", "low")
            if severity in by_severity:
                by_severity[severity] += 1

            category = issue.get("category", "code_quality")
            by_category[category] = by_category.get(category, 0) + 1

            language = issue.get("language", "")
            if language:
                by_language[language] = by_language.get(language, 0) + 1

            file_path = issue.get("file", "")
            if file_path:
                by_file[file_path] = by_file.get(file_path, 0) + 1

        # RiskAssessment 결정 (전체 통합 판정)
        critical_count = by_severity.get("critical", 0)
        high_count = by_severity.get("high", 0)
        risk_assessment = self._determine_risk(
            critical_count, high_count, sorted_issues,
            analysis_errors=analysis_errors,
        )

        # 파일별 개별 판정 (analysis_results의 모든 파일에 대해 계산)
        risk_assessment["by_file"] = self._build_per_file_risks(
            sorted_issues=sorted_issues,
            analysis_results=analysis_results or [],
            analysis_errors=analysis_errors or [],
        )

        allowed = risk_assessment["deployment_allowed"]
        risk = risk_assessment["deployment_risk"]

        if risk == "UNABLE_TO_ANALYZE":
            self.rl.decision(
                "Decision: 분석불가",
                reason="분석 중 오류 발생 → 배포 판정 불가",
            )
            # risk_description은 _determine_risk에서 이미 생성됨, LLM 호출 스킵
        elif allowed:
            # T70.6.1: 배포 허용 (MEDIUM + LOW) — LLM 호출 skip, 템플릿 사용
            self.rl.decision(
                f"Decision: 배포 가능 ({risk}) — LLM skip",
                reason=f"deployment_allowed=True → 템플릿 사용 (T70.6.1)",
            )
            risk_assessment["risk_description"] = self._default_risk_description(
                by_severity, risk_assessment["deployment_risk"],
            )
            logger.info(
                f"Reporter LLM 호출 skip: {risk} 위험도 → 템플릿 사용"
            )
        else:
            status = "가능" if allowed else "차단"
            if critical_count > 0:
                block_reason = "(CRITICAL>0 차단)"
            elif high_count >= 3:
                block_reason = "(HIGH>=3 차단)"
            else:
                block_reason = ""
            self.rl.decision(
                f"Decision: 배포 {status} ({risk})",
                reason=f"CRITICAL={critical_count}, HIGH={high_count} {block_reason}".rstrip(),
            )

            # LLM으로 risk_description 생성
            risk_description = await self._generate_risk_description(
                by_severity, risk_assessment["deployment_risk"], generated_at,
            )
            risk_assessment["risk_description"] = risk_description

        metadata = AnalysisMetadata(
            session_id=session_id,
            analyzed_at=generated_at,
            total_files=total_files,
            total_lines=total_lines,
            analysis_duration_seconds=round(analysis_duration_seconds, 2),
            total_llm_tokens=total_llm_tokens,
        )

        issue_summary = IssueSummary(
            total=len(sorted_issues),
            by_severity=by_severity,
            by_category=by_category,
            by_language=by_language,
            by_file=by_file,
        )

        return Summary(
            analysis_metadata=metadata,
            issue_summary=issue_summary,
            risk_assessment=RiskAssessment.model_validate(risk_assessment),
        )

    @staticmethod
    def _build_per_file_risks(
        *,
        sorted_issues: list[dict[str, Any]],
        analysis_results: list[dict[str, Any]],
        analysis_errors: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """파일별로 개별 배포 판정을 계산한다.

        같은 판정 규칙(_determine_risk)을 파일 단위로 적용:
        - critical>0           → CRITICAL, 배포 불가
        - high>=3              → HIGH,     배포 가능 (강력 권고)
        - high>=1              → MEDIUM,   배포 가능
        - 그 외                → LOW,      배포 가능
        - 분석 에러 발생 파일  → UNABLE_TO_ANALYZE, 배포 불가

        파일 목록은 analysis_results 기반 (이슈 0건도 LOW로 보고).
        analysis_results가 비면 sorted_issues의 file 필드로 fallback.
        """
        # 분석 에러 파일
        error_files: dict[str, str] = {
            r.get("file", ""): (r.get("error") or "")
            for r in analysis_errors if r.get("file")
        }

        # 분석된 모든 파일
        if analysis_results:
            all_files = [r.get("file", "") for r in analysis_results if r.get("file")]
        else:
            all_files = sorted({i.get("file", "") for i in sorted_issues if i.get("file")})

        # 파일별 이슈 그룹핑
        issues_by_file: dict[str, list[dict[str, Any]]] = {}
        for issue in sorted_issues:
            fp = issue.get("file", "")
            if fp:
                issues_by_file.setdefault(fp, []).append(issue)

        per_file: list[dict[str, Any]] = []
        for file_path in all_files:
            if file_path in error_files:
                per_file.append({
                    "file": file_path,
                    "deployment_risk": "UNABLE_TO_ANALYZE",
                    "deployment_allowed": False,
                    "critical_count": 0,
                    "high_count": 0,
                    "medium_count": 0,
                    "blocking_issues": [],
                })
                continue

            file_issues = issues_by_file.get(file_path, [])
            crit = sum(1 for i in file_issues if i.get("severity") == "critical")
            high = sum(1 for i in file_issues if i.get("severity") == "high")
            med = sum(1 for i in file_issues if i.get("severity") == "medium")

            blocking: list[str] = []
            if crit > 0:
                risk = "CRITICAL"
                allowed = False
                blocking = [
                    i.get("issue_id", "") for i in file_issues
                    if i.get("severity") == "critical" and i.get("issue_id")
                ]
            elif high >= 3:
                risk = "HIGH"
                allowed = True
            elif high >= 1:
                risk = "MEDIUM"
                allowed = True
            else:
                risk = "LOW"
                allowed = True

            per_file.append({
                "file": file_path,
                "deployment_risk": risk,
                "deployment_allowed": allowed,
                "critical_count": crit,
                "high_count": high,
                "medium_count": med,
                "blocking_issues": blocking,
            })

        # 파일명 정렬
        per_file.sort(key=lambda x: x["file"])
        return per_file

    def _determine_risk(
        self,
        critical_count: int,
        high_count: int,
        sorted_issues: list[dict[str, Any]],
        analysis_errors: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """RiskAssessment 필드를 결정한다 (risk_description 제외)."""
        # 분석 에러가 있으면 분석불가 판정
        if analysis_errors:
            error_files = [r.get("file", "unknown") for r in analysis_errors]
            error_details = [r.get("error", "") for r in analysis_errors]
            logger.warning(
                f"분석 에러 발견 ({len(analysis_errors)}건) → 분석불가 판정: "
                f"{error_files}"
            )
            return {
                "deployment_risk": "UNABLE_TO_ANALYZE",
                "deployment_allowed": False,
                "blocking_issues": [],
                "risk_description": (
                    f"분석 중 오류가 발생하여 배포 판정을 내릴 수 없습니다. "
                    f"오류 파일 {len(analysis_errors)}건: "
                    + "; ".join(
                        f"{f} ({e[:80]})"
                        for f, e in zip(error_files, error_details)
                    )
                ),
                "by_file": [],
            }

        blocking_issues: list[str] = []

        # T70.6.2: CRITICAL만 배포 차단. HIGH 탐지 신뢰도가 확보되기 전까지
        # 오탐으로 인한 가짜 차단을 방지하기 위한 보수적 정책.
        if critical_count > 0:
            deployment_risk = "CRITICAL"
            deployment_allowed = False
            blocking_issues = [
                issue.get("issue_id", "") for issue in sorted_issues
                if issue.get("severity") == "critical"
            ]
        elif high_count >= 3:
            deployment_risk = "HIGH"
            deployment_allowed = True  # T70.6.2: 배포 허용, 강력 수정 권고만
        elif high_count >= 1:
            deployment_risk = "MEDIUM"
            deployment_allowed = True
        else:
            deployment_risk = "LOW"
            deployment_allowed = True

        return {
            "deployment_risk": deployment_risk,
            "deployment_allowed": deployment_allowed,
            "blocking_issues": blocking_issues,
            "risk_description": "",
        }

    async def _generate_risk_description(
        self,
        by_severity: dict[str, int],
        deployment_risk: str,
        generated_at: datetime,
    ) -> str:
        """LLM으로 한국어 배포 위험 설명을 생성한다.

        LLM 실패 시 기본 메시지로 graceful degradation한다.
        """
        response = ""
        try:
            prompt = load_prompt(
                "reporter",
                analysis_results=json.dumps(
                    {
                        "by_severity": by_severity,
                        "deployment_risk": deployment_risk,
                    },
                    ensure_ascii=False,
                ),
                generated_at=generated_at.isoformat(),
                session_id="risk_description_generation",
            )

            messages = [
                {
                    "role": "system",
                    "content": (
                        "당신은 소스코드 분석 결과 리포트 작성 전문가입니다. "
                        "반드시 JSON 형식으로 응답하세요."
                    ),
                },
                {"role": "user", "content": prompt},
            ]

            prompt_tokens = len(prompt) // 4
            self.rl.prompt(f"Prompt: reporter (~{prompt_tokens:,} tokens)")
            self.rl.llm_request(f"LLM 호출: {self.model} 요청 중...")

            # T70.1: LLM 호출 시간 측정
            llm_start = time.time()
            response = await self.call_llm(messages, json_mode=True)
            llm_elapsed = time.time() - llm_start

            response_tokens = len(response) // 4
            logger.info(
                f"Reporter LLM 호출: {llm_elapsed:.2f}s, "
                f"~{prompt_tokens:,} prompt tokens, "
                f"~{response_tokens:,} response tokens"
            )
            self.rl.llm_response(
                f"LLM 응답: {llm_elapsed:.2f}s, "
                f"~{prompt_tokens:,} prompt + ~{response_tokens:,} response tokens"
            )
            result = json.loads(response)

            if not isinstance(result, dict):
                raise ValueError(f"LLM 응답이 dict가 아님: {type(result)}")

            # risk_description 추출
            summary_data = result.get("summary", {})
            risk_data = summary_data.get("risk_assessment", {})
            description = risk_data.get("risk_description", "")

            if description:
                return description

        except Exception as e:
            logger.warning(
                "LLM risk_description 생성 실패, 기본 메시지 사용: %s (응답 처음 300자: %s)",
                e, response[:300] if response else "(빈 응답)",
            )

        # Graceful degradation: 기본 메시지
        return self._default_risk_description(by_severity, deployment_risk)

    def _build_deployment_checklist(
        self,
        *,
        file_paths: list[str],
        file_first_lines: dict[str, str],
        generated_at: datetime,
        session_id: str,
    ) -> DeploymentChecklist:
        """배포 체크리스트를 생성한다."""
        tool_result = self._deployment_checklist_generator.execute(
            file_paths=file_paths,
            file_first_lines=file_first_lines,
        )

        sections = [
            DeploymentChecklistSection.model_validate(s)
            for s in tool_result.data.get("sections", [])
        ]

        return DeploymentChecklist(
            generated_at=generated_at,
            session_id=session_id,
            total_items=tool_result.data.get("total_items", 0),
            sections=sections,
        )

    def _default_risk_description(
        self,
        by_severity: dict[str, int],
        deployment_risk: str,
    ) -> str:
        """LLM 실패 시 기본 위험 설명을 생성한다."""
        critical = by_severity.get("critical", 0)
        high = by_severity.get("high", 0)
        medium = by_severity.get("medium", 0)
        low = by_severity.get("low", 0)
        total = critical + high + medium + low

        if deployment_risk == "CRITICAL":
            return (
                f"Critical 이슈 {critical}건 발견. "
                f"즉시 수정 필요. 배포 차단 권고."
            )
        elif deployment_risk == "HIGH":
            return (
                f"High 이슈 {high}건 발견. "
                f"배포 가능하나 강력 수정 권고 (조건부 장애 가능성)."
            )
        elif deployment_risk == "MEDIUM":
            return (
                f"총 {total}건 이슈 발견 (High {high}건). "
                f"배포 가능하나 수정 권고."
            )
        else:
            return f"총 {total}건 이슈 발견. 심각한 문제 없음."
