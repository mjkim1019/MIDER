"""이슈 후처리 필터.

LLM이 반환한 이슈 중 프로젝트 컨벤션상 false positive인 것들을 제거한다.
현재 지원 필터:
- 경계 검증을 보장하는 프로젝트 자체 함수(예: mpfm*) 호출의 반환값이
  인덱스로 사용되는 "배열 인덱스 경계값 미검증" 류 이슈 제외
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# "경계값 미검증" 계열 이슈로 판정할 키워드 (title/description 소문자 매칭)
_BOUNDS_KEYWORDS: tuple[str, ...] = (
    "인덱스 경계",
    "배열 경계",
    "경계값",
    "경계 미검증",
    "경계 미검사",
    "array bounds",
    "array bound",
    "out of bounds",
    "index bounds",
    "bounds check",
    "경계 검증",
)


def format_safe_prefixes_for_prompt(prefixes: list[str]) -> str:
    """접두사 리스트를 프롬프트 표시용 문자열로 변환한다.

    예: ["mpfm"] → "mpfm*"
        [] → "(없음)"
    """
    if not prefixes:
        return "(없음)"
    return ", ".join(f"{p}*" for p in prefixes)


def _has_bounds_keyword(issue: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(issue.get("title", "") or ""),
            str(issue.get("description", "") or ""),
        ]
    ).lower()
    return any(kw.lower() in text for kw in _BOUNDS_KEYWORDS)


def _mentions_safe_function(issue: dict[str, Any], prefixes: list[str]) -> bool:
    fix = issue.get("fix") or {}
    raw_code = issue.get("raw_code") or issue.get("raw_match") or ""
    haystack = " ".join(
        [
            str(issue.get("title", "") or ""),
            str(issue.get("description", "") or ""),
            str(fix.get("before", "") or ""),
            str(fix.get("after", "") or ""),
            str(raw_code or ""),
        ]
    )
    for prefix in prefixes:
        # 함수 호출 형태만 매칭: mpfm<word>( — 다른 식별자에 접두사 부분 포함 가능성 차단
        pattern = re.compile(rf"\b{re.escape(prefix)}\w*\s*\(", re.IGNORECASE)
        if pattern.search(haystack):
            return True
    return False


def filter_safe_function_bounds_issues(
    issues: list[dict[str, Any]],
    safe_prefixes: list[str],
) -> tuple[list[dict[str, Any]], int]:
    """신뢰 함수 호출의 경계값 미검증 이슈를 제거한다.

    Args:
        issues: LLM 출력 이슈 리스트.
        safe_prefixes: 신뢰 함수 접두사 리스트 (빈 리스트면 필터 미적용).

    Returns:
        (필터링된 이슈 리스트, 제거된 건수)
    """
    if not safe_prefixes or not issues:
        return issues, 0

    kept: list[dict[str, Any]] = []
    removed = 0
    for issue in issues:
        if _has_bounds_keyword(issue) and _mentions_safe_function(issue, safe_prefixes):
            logger.info(
                "Issue filter drop: 신뢰 함수 경계 검증 이슈 제외 "
                "[%s] %s",
                issue.get("issue_id", "?"),
                str(issue.get("title", ""))[:80],
            )
            removed += 1
            continue
        kept.append(issue)
    return kept, removed
