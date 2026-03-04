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
from mider.models.analysis_result import AnalysisResult
from mider.models.report import (
    AnalysisMetadata,
    Checklist,
    ChecklistItem,
    IssueList,
    IssueListItem,
    IssueSummary,
    RiskAssessment,
    Summary,
)
from mider.tools.utility.checklist_generator import ChecklistGenerator

logger = logging.getLogger(__name__)

# 심각도 정렬 우선순위
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class ReporterAgent(BaseAgent):
    """Phase 3: 분석 결과를 통합하여 3개 리포트를 생성하는 Agent.

    AnalysisResult 리스트를 받아 IssueList, Checklist, Summary를
    생성한다. LLM은 risk_description 한국어 생성에 사용한다.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        fallback_model: str | None = "gpt-4o",
        temperature: float = 0.3,
    ) -> None:
        super().__init__(
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
        )
        self._checklist_generator = ChecklistGenerator()

    async def run(
        self,
        *,
        analysis_results: list[dict[str, Any]],
        session_id: str,
        total_files: int,
        total_lines: int,
        analysis_duration_seconds: float,
    ) -> dict[str, Any]:
        """분석 결과를 통합하여 3개 리포트를 생성한다.

        Args:
            analysis_results: Phase 2 AnalysisResult dict 리스트
            session_id: 세션 식별자
            total_files: 분석한 총 파일 수
            total_lines: 분석한 총 라인 수
            analysis_duration_seconds: 전체 분석 소요 시간 (초)

        Returns:
            {"issue_list": ..., "checklist": ..., "summary": ...}
        """
        start_time = time.time()
        logger.info(f"리포트 생성 시작: {len(analysis_results)}개 분석 결과")

        try:
            generated_at = datetime.now(timezone.utc)

            # Step 1: 모든 이슈를 통합하고 심각도별 정렬
            all_issues = self._collect_all_issues(analysis_results)
            sorted_issues = self._sort_issues(all_issues)

            # Step 2: IssueList 생성
            issue_list = self._build_issue_list(
                sorted_issues, generated_at, session_id,
            )

            # Step 3: Checklist 생성 (ChecklistGenerator Tool 사용)
            checklist = self._build_checklist(
                analysis_results, generated_at, session_id,
            )

            # Step 4: Summary 생성 (LLM으로 risk_description 생성)
            total_llm_tokens = sum(
                r.get("llm_tokens_used", 0) for r in analysis_results
            )
            summary = await self._build_summary(
                sorted_issues=sorted_issues,
                generated_at=generated_at,
                session_id=session_id,
                total_files=total_files,
                total_lines=total_lines,
                analysis_duration_seconds=analysis_duration_seconds,
                total_llm_tokens=total_llm_tokens,
            )

            elapsed = time.time() - start_time
            logger.info(
                f"리포트 생성 완료: {issue_list.total_issues}개 이슈, "
                f"{checklist.total_checks}개 체크항목, {elapsed:.2f}초"
            )

            return {
                "issue_list": issue_list.model_dump(mode="json"),
                "checklist": checklist.model_dump(mode="json"),
                "summary": summary.model_dump(mode="json"),
            }

        except Exception as e:
            logger.error(f"리포트 생성 실패: {e}")
            raise

    def _collect_all_issues(
        self,
        analysis_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """모든 AnalysisResult에서 이슈를 통합한다."""
        all_issues: list[dict[str, Any]] = []

        for result in analysis_results:
            file_path = result.get("file", "")
            language = result.get("language", "")

            for issue in result.get("issues", []):
                issue_item = {
                    "issue_id": issue.get("issue_id", ""),
                    "file": file_path,
                    "language": language,
                    "category": issue.get("category", "code_quality"),
                    "severity": issue.get("severity", "low"),
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

        # RiskAssessment 결정
        critical_count = by_severity.get("critical", 0)
        high_count = by_severity.get("high", 0)
        risk_assessment = self._determine_risk(
            critical_count, high_count, sorted_issues,
        )

        # LLM으로 risk_description 생성
        risk_description = await self._generate_risk_description(
            by_severity, risk_assessment["deployment_risk"],
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

    def _determine_risk(
        self,
        critical_count: int,
        high_count: int,
        sorted_issues: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """RiskAssessment 필드를 결정한다 (risk_description 제외)."""
        blocking_issues: list[str] = []

        if critical_count > 0:
            deployment_risk = "CRITICAL"
            deployment_allowed = False
            blocking_issues = [
                issue["issue_id"] for issue in sorted_issues
                if issue.get("severity") in ("critical", "high")
            ]
        elif high_count >= 3:
            deployment_risk = "HIGH"
            deployment_allowed = False
            blocking_issues = [
                issue["issue_id"] for issue in sorted_issues
                if issue.get("severity") == "high"
            ]
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
    ) -> str:
        """LLM으로 한국어 배포 위험 설명을 생성한다.

        LLM 실패 시 기본 메시지로 graceful degradation한다.
        """
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
                generated_at=datetime.now(timezone.utc).isoformat(),
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

            response = await self.call_llm(messages, json_mode=True)
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
            logger.warning(f"LLM risk_description 생성 실패, 기본 메시지 사용: {e}")

        # Graceful degradation: 기본 메시지
        return self._default_risk_description(by_severity, deployment_risk)

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
                f"조건부 장애 가능성 있음. 배포 전 수정 권고."
            )
        elif deployment_risk == "MEDIUM":
            return (
                f"총 {total}건 이슈 발견 (High {high}건). "
                f"배포 가능하나 수정 권고."
            )
        else:
            return f"총 {total}건 이슈 발견. 심각한 문제 없음."
