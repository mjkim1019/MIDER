"""ChecklistGenerator: 이슈 기반 체크리스트 자동 생성 Tool.

AnalysisResult 리스트에서 critical/high 이슈를 추출하여
검증 명령어가 포함된 체크리스트를 생성한다.
"""

import logging
import shlex
from typing import Any

from mider.tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# 카테고리별 검증 명령어 템플릿 (shlex.quote로 이스케이프된 값 삽입)
_VERIFICATION_COMMANDS: dict[str, str] = {
    "memory_safety": "grep -n {pattern} {file}",
    "null_safety": "grep -n {pattern} {file}",
    "data_integrity": "grep -n 'EXEC SQL' {file}",
    "error_handling": "grep -n {pattern} {file}",
    "security": "grep -n {pattern} {file}",
    "performance": "grep -n {pattern} {file}",
    "code_quality": "grep -n {pattern} {file}",
}

# 카테고리별 기대 결과 템플릿
_EXPECTED_RESULTS: dict[str, str] = {
    "memory_safety": "매칭 결과 없음 (0건)",
    "null_safety": "모든 NULL 체크 완료",
    "data_integrity": "모든 EXEC SQL 후 SQLCA 체크 존재",
    "error_handling": "모든 예외 경로 처리 완료",
    "security": "매칭 결과 없음 (0건)",
    "performance": "인덱스 억제 패턴 제거 완료",
    "code_quality": "코드 품질 개선 완료",
}

# 이슈 제목에서 검증 패턴을 추론하는 키워드 매핑
_PATTERN_KEYWORDS: dict[str, str] = {
    "strcpy": "strcpy",
    "sprintf": "sprintf",
    "malloc": "malloc",
    "free": "free",
    "innerHTML": "innerHTML",
    "eval": "eval(",
    "document.write": "document.write",
    "XSS": "innerHTML\\|eval\\|document.write",
    "SQLCA": "EXEC SQL",
    "INDICATOR": "INDICATOR",
    "SELECT *": "SELECT \\*",
    "커서": "OPEN\\|CLOSE",
    "인덱스": "YEAR\\|MONTH\\|UPPER\\|LOWER",
}


# 카테고리 한국어 매핑
_CATEGORY_KR_MAP: dict[str, str] = {
    "memory_safety": "메모리 안전성",
    "null_safety": "NULL 안전성",
    "data_integrity": "데이터 무결성",
    "error_handling": "에러 처리",
    "security": "보안",
    "performance": "성능",
    "code_quality": "코드 품질",
}


def _infer_pattern(issue_title: str, issue_description: str) -> str:
    """이슈 제목/설명에서 검증할 패턴을 추론한다."""
    combined = f"{issue_title} {issue_description}"
    for keyword, pattern in _PATTERN_KEYWORDS.items():
        if keyword.lower() in combined.lower():
            return pattern
    return "TODO"


class ChecklistGenerator(BaseTool):
    """이슈 기반 체크리스트를 자동 생성하는 Tool."""

    def execute(
        self,
        *,
        analysis_results: list[dict[str, Any]],
        **kwargs: Any,
    ) -> ToolResult:
        """AnalysisResult 리스트에서 체크리스트를 생성한다.

        Args:
            analysis_results: AnalysisResult dict 리스트 (JSON 파싱된 형태)

        Returns:
            ToolResult (data: items, total_checks)
        """
        items: list[dict[str, Any]] = []
        check_id = 1

        # 카테고리별로 이슈 그룹핑 (같은 카테고리+파일은 하나의 체크항목)
        grouped: dict[str, list[dict[str, Any]]] = {}

        for result in analysis_results:
            file_path = result.get("file", "")
            for issue in result.get("issues", []):
                severity = issue.get("severity", "low")
                if severity not in ("critical", "high"):
                    continue

                category = issue.get("category", "code_quality")
                group_key = f"{category}:{file_path}"

                if group_key not in grouped:
                    grouped[group_key] = []
                grouped[group_key].append(issue)

        for group_key, issues in grouped.items():
            category, file_path = group_key.split(":", 1)

            issue_ids = [i["issue_id"] for i in issues]
            severity = "critical" if any(
                i["severity"] == "critical" for i in issues
            ) else "high"

            # 첫 번째 이슈의 제목/설명으로 패턴 추론
            first_issue = issues[0]
            pattern = _infer_pattern(
                first_issue.get("title", ""),
                first_issue.get("description", ""),
            )

            # 검증 명령어 생성 (shell injection 방지)
            safe_file = shlex.quote(file_path)
            safe_pattern = shlex.quote(pattern)
            cmd_template = _VERIFICATION_COMMANDS.get(
                category, "grep -n {pattern} {file}"
            )
            verification_command = cmd_template.format(
                pattern=safe_pattern, file=safe_file
            )

            expected_result = _EXPECTED_RESULTS.get(
                category, "이슈 수정 완료"
            )

            category_kr = _CATEGORY_KR_MAP.get(category, category)

            description = (
                f"모든 {category_kr} 이슈 수정 완료 ({file_path})"
            )

            items.append({
                "id": f"CHECK-{check_id}",
                "category": category,
                "severity": severity,
                "description": description,
                "related_issues": issue_ids,
                "verification_command": verification_command,
                "expected_result": expected_result,
            })
            check_id += 1

        # critical 먼저, 그 다음 high 순으로 정렬
        items.sort(key=lambda x: 0 if x["severity"] == "critical" else 1)

        logger.debug(f"체크리스트 생성 완료: {len(items)}개 항목")

        return ToolResult(
            success=True,
            data={
                "items": items,
                "total_checks": len(items),
            },
        )
