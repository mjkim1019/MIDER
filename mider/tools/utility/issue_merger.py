"""IssueMerger: 전 계층 분석 결과를 통합, 중복 제거, 최종 Issue 생성.

설계서 V3 §7 기반.
- 원본 line 복원
- Proframe 노이즈 자동 제거
- 패턴 그룹 병합
- 교차 계층 중복 제거 (±3줄 + 동일 카테고리)
- severity 순 정렬
"""

from __future__ import annotations

import logging
import re
from typing import Any

from mider.models.proc_partition import Finding, PartitionResult

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# severity 우선순위
# ──────────────────────────────────────────

_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}

# source 우선순위 (높을수록 우선)
_SOURCE_RANK = {"hybrid": 3, "llm": 2, "static_analysis": 1}

# ──────────────────────────────────────────
# Proframe 노이즈 자동 제거 키워드
# (CAnalyzerAgent의 _REMOVE_KEYWORDS 확장)
# ──────────────────────────────────────────

# ──────────────────────────────────────────
# 오탐(false positive) 자동 감지 패턴
# LLM이 정적 finding을 검토한 결과 "오탐"으로 결론냈으나
# false_positive 필드를 명시하지 않은 경우를 잡기 위한 휴리스틱.
# 보수적으로: title/description의 명시적 부정 표현만 매칭.
# ──────────────────────────────────────────

_FP_TITLE_KEYWORDS: tuple[str, ...] = (
    "오탐", "오판", "잘못된 진단", "false positive",
    "오인 진단", "잘못된 분석",
)

_FP_DESC_PHRASES: tuple[str, ...] = (
    "불일치가 아닙니다", "불일치 아닙니다",
    "문제 없습니다", "문제가 없습니다",
    "정상 코드입니다", "정상 동작입니다",
    "오탐입니다", "오판입니다",
    "버그가 아닙니다", "버그 아닙니다",
    "false positive로 판단", "오탐으로 판단",
)


def _looks_like_false_positive(issue: dict[str, Any]) -> tuple[bool, str]:
    """이슈가 LLM이 결론내린 오탐일 가능성이 큰지 휴리스틱 판정.

    Returns:
        (is_fp, reason) — is_fp=True 시 reason은 매칭된 신호 설명.
    """
    # ① 명시적 false_positive 필드
    if issue.get("false_positive"):
        return True, "false_positive=true"

    title = (issue.get("title") or "")
    title_lower = title.lower()
    for kw in _FP_TITLE_KEYWORDS:
        if kw.lower() in title_lower:
            return True, f"title 키워드 '{kw}'"

    description = (issue.get("description") or "")
    desc_lower = description.lower()
    for phrase in _FP_DESC_PHRASES:
        if phrase.lower() in desc_lower:
            return True, f"description 부정 표현 '{phrase}'"

    # ③ before == after (실질적 수정 사항 없음)
    fix = issue.get("fix") or {}
    before = (fix.get("before") or "").strip()
    after = (fix.get("after") or "").strip()
    if before and before == after:
        return True, "fix.before == fix.after (수정 사항 없음)"

    return False, ""


_REMOVE_KEYWORDS: list[str] = [
    # 동시성/스레드 (Proframe 단일스레드)
    "스레드 안전", "동기화 부재", "경쟁 상태", "race condition",
    "mutex", "동시 접근", "스레드 안전성",
    "데이터 레이스", "동시성", "요청 간 공유", "멀티스레드",
    "race", "concurrent", "동기화 누락",
    # NULL 체크 (프레임워크 보장)
    "null 검증", "null 체크", "널 검증", "널 체크", "널 포인터",
    "유효성 검증 누락", "유효성 검사 누락",
    "방어적 프로그래밍", "null 역참조", "널 역참조",
    "포인터 검증", "ctx 유효성", "input 유효성",
    # 코드 스타일 제안 (실제 버그 아님)
    "안전 대안", "관례 개선", "memset_s",
    "가독성", "유지보수성", "네이밍",
    # 전역 변수 공유 (Proframe 프로세스별 독립)
    "전역 변수 공유", "전역 상태 공유", "전역 io 구조체",
    "요청 간 격리", "전역 버퍼",
    # 구조체 부분 초기화 (Proframe a000_init_proc/memset 보장)
    "부분적으로만 채", "부분 초기화", "미설정 필드",
    "정보 유출", "패딩 바이트",
    # 코드 스타일/미래 가능성
    "개선 권장", "표현 개선", "향후", "가독성 향상",
    # memset 전 선언 초기화
    "memset 이전 경로", "선언 시 초기화 누락",
    "선언 시 {0} 초기화",
]

# ──────────────────────────────────────────
# 패턴 그룹 병합 (기존 + Pro*C 확장)
# ──────────────────────────────────────────

_DEDUP_GROUPS: list[tuple[str, list[str]]] = [
    # 기존 C 그룹
    ("strncpy 널 종료", ["strncpy", "널 종료", "null 종료", "strlcpy"]),
    ("ix 미초기화", ["ix 변수", "ix 미초기화", "ix 선언", "지역 변수 ix"]),
    # Pro*C 확장 그룹
    ("SQLCA 미검사", [
        "sqlca", "sqlca 에러", "sqlca 체크 누락", "sqlca_missing",
        "sqlca 검사", "에러 체크 누락",
    ]),
    ("INDICATOR 누락", [
        "indicator", "인디케이터 누락", "indicator 변수 누락",
        "indicator_missing", "null 값 수신",
    ]),
    ("커서 미해제", [
        "cursor close 누락", "커서 close", "자원 누수",
        "cursor_close_missing", "커서 미해제",
    ]),
]

# ──────────────────────────────────────────
# 교차 계층 중복 판정 기준
# ──────────────────────────────────────────

_LINE_PROXIMITY = 3  # ±3줄 이내면 동일 위치로 판정


class IssueMerger:
    """전 계층 분석 결과를 통합하여 최종 Issue 리스트를 생성한다."""

    def __init__(self) -> None:
        self._issue_counter = 0

    def merge(
        self,
        llm_issues: list[dict[str, Any]],
        static_findings: list[Finding],
        file_path: str,
        partition: PartitionResult | None = None,
    ) -> list[dict[str, Any]]:
        """LLM이 생성한 Issue와 정적 Finding을 통합한다.

        Args:
            llm_issues: LLM Reviewer가 생성한 Issue dict 리스트
            static_findings: 정적/교차 분석 Finding (LLM 실패 시 fallback)
            file_path: 대상 파일 경로
            partition: PartitionResult (line 복원용)

        Returns:
            최종 Issue dict 리스트 (중복 제거, 정렬 완료)
        """
        self._issue_counter = 0

        # 1단계: LLM issues에서 false_positive 제거
        issues = self._filter_false_positives(llm_issues)

        # 2단계: Proframe 노이즈 제거
        issues = self._remove_proframe_noise(issues)

        # 3단계: 패턴 그룹 병합
        issues = self._merge_pattern_groups(issues)

        # 4단계: 교차 계층 중복 제거
        issues = self._deduplicate_cross_layer(issues)

        # 5단계: issue_id 재부여 + severity 정렬
        issues = self._finalize(issues, file_path)

        return issues

    # ──────────────────────────────────────────
    # Step 1: false positive 제거
    # ──────────────────────────────────────────

    @staticmethod
    def _filter_false_positives(
        issues: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """LLM이 오탐으로 결론낸 이슈를 제거한다.

        세 가지 신호 중 하나라도 매칭되면 제외:
        ① false_positive=True 필드 (명시적)
        ② title의 오탐 키워드 ("오탐", "false positive" 등)
        ③ description의 부정 결론 표현 ("불일치 아닙니다" 등)
        ④ fix.before == fix.after (실질 수정 없음)
        """
        kept: list[dict[str, Any]] = []
        for issue in issues:
            is_fp, reason = _looks_like_false_positive(issue)
            if is_fp:
                logger.info(
                    f"오탐 자동 제거: {issue.get('issue_id', '?')} "
                    f"[{reason}] title={issue.get('title', '')[:60]}"
                )
                continue
            kept.append(issue)
        return kept

    # ──────────────────────────────────────────
    # Step 2: Proframe 노이즈 제거
    # ──────────────────────────────────────────

    @staticmethod
    def _remove_proframe_noise(
        issues: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Proframe 환경 특성상 무의미한 이슈를 제거한다."""
        filtered: list[dict[str, Any]] = []
        for issue in issues:
            title_lower = issue.get("title", "").lower()
            desc_lower = issue.get("description", "").lower()
            combined = title_lower + " " + desc_lower
            if any(kw in combined for kw in _REMOVE_KEYWORDS):
                continue
            filtered.append(issue)
        return filtered

    # ──────────────────────────────────────────
    # Step 3: 패턴 그룹 병합
    # ──────────────────────────────────────────

    @staticmethod
    def _merge_pattern_groups(
        issues: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """동일 패턴 그룹의 이슈를 대표 1건으로 병합한다."""
        group_map: dict[str, list[dict[str, Any]]] = {}
        ungrouped: list[dict[str, Any]] = []

        for issue in issues:
            title_lower = issue.get("title", "").lower()
            desc_lower = issue.get("description", "").lower()
            combined = title_lower + " " + desc_lower
            matched_group = None
            for group_name, keywords in _DEDUP_GROUPS:
                if any(kw in combined for kw in keywords):
                    # 같은 함수 내에서만 그룹핑
                    func = _extract_function_from_issue(issue)
                    matched_group = f"{group_name}:{func}"
                    break
            if matched_group:
                group_map.setdefault(matched_group, []).append(issue)
            else:
                ungrouped.append(issue)

        merged: list[dict[str, Any]] = []
        for group_issues in group_map.values():
            if not group_issues:
                continue
            # severity 최고 우선
            group_issues.sort(
                key=lambda x: _SEVERITY_RANK.get(x.get("severity", "low"), 0),
                reverse=True,
            )
            representative = group_issues[0].copy()
            if len(group_issues) > 1:
                representative["description"] += (
                    f" (외 {len(group_issues) - 1}곳 동일 패턴)"
                )
            merged.append(representative)

        return merged + ungrouped

    # ──────────────────────────────────────────
    # Step 4: 교차 계층 중복 제거
    # ──────────────────────────────────────────

    @staticmethod
    def _deduplicate_cross_layer(
        issues: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """위치(±3줄) + 카테고리 동일한 이슈를 병합한다.

        우선순위: hybrid > llm > static_analysis → severity 높은 것 → 설명 긴 것
        """
        if not issues:
            return issues

        # 위치 + 카테고리로 클러스터링
        clusters: list[list[dict[str, Any]]] = []

        for issue in issues:
            line_start = _get_line_start(issue)
            category = issue.get("category", "")
            merged = False

            for cluster in clusters:
                rep = cluster[0]
                rep_line = _get_line_start(rep)
                rep_cat = rep.get("category", "")

                if rep_cat == category and abs(rep_line - line_start) <= _LINE_PROXIMITY:
                    cluster.append(issue)
                    merged = True
                    break

            if not merged:
                clusters.append([issue])

        # 각 클러스터에서 가장 우선순위 높은 것 선택
        result: list[dict[str, Any]] = []
        for cluster in clusters:
            if len(cluster) == 1:
                result.append(cluster[0])
                continue

            # 정렬: source → severity → description 길이
            cluster.sort(
                key=lambda x: (
                    -_SOURCE_RANK.get(x.get("source", ""), 0),
                    -_SEVERITY_RANK.get(x.get("severity", "low"), 0),
                    -len(x.get("description", "")),
                ),
            )
            winner = cluster[0].copy()
            if len(cluster) > 1:
                # 병합된 다른 도구 정보 기록
                other_tools = {
                    c.get("static_tool", "")
                    for c in cluster[1:]
                    if c.get("static_tool")
                }
                if other_tools:
                    winner.setdefault("metadata", {})["merged_from"] = list(other_tools)
            result.append(winner)

        return result

    # ──────────────────────────────────────────
    # Step 5: 최종 정리
    # ──────────────────────────────────────────

    def _finalize(
        self,
        issues: list[dict[str, Any]],
        file_path: str,
    ) -> list[dict[str, Any]]:
        """issue_id 재부여, severity 정렬."""
        # severity 순 정렬
        issues.sort(
            key=lambda x: _SEVERITY_RANK.get(x.get("severity", "low"), 0),
            reverse=True,
        )

        # issue_id 재부여
        for i, issue in enumerate(issues, 1):
            issue["issue_id"] = f"PC-{i:03d}"

            # location에 file 보장
            loc = issue.get("location", {})
            if isinstance(loc, dict) and not loc.get("file"):
                loc["file"] = file_path
                issue["location"] = loc

            # false_positive 필드 제거 (최종 출력에는 불필요)
            issue.pop("false_positive", None)

            # confidence 필드 제거 (Issue 모델에 없음)
            issue.pop("confidence", None)

            # metadata 필드 제거 (Issue 모델에 없음)
            issue.pop("metadata", None)

        return issues

    def merge_fallback(
        self,
        static_findings: list[Finding],
        file_path: str,
    ) -> list[dict[str, Any]]:
        """LLM 실패 시 정적 Finding만으로 Issue를 생성한다."""
        issues: list[dict[str, Any]] = []
        for f in static_findings:
            issues.append({
                "issue_id": "",
                "category": f.category,
                "severity": f.severity,
                "title": f.title,
                "description": f.description,
                "location": {
                    "file": file_path,
                    "line_start": f.origin_line_start,
                    "line_end": f.origin_line_end,
                },
                "fix": {
                    "before": f.raw_match[:100] if f.raw_match else "",
                    "after": "(수동 검토 필요)",
                    "description": f.description,
                },
                "source": "static_analysis",
                "static_tool": f.tool,
                "static_rule": f.rule_id,
            })

        # 노이즈 제거 + 그룹 병합 + 정렬
        issues = self._remove_proframe_noise(issues)
        issues = self._merge_pattern_groups(issues)
        issues = self._finalize(issues, file_path)
        return issues


# ──────────────────────────────────────────
# 헬퍼 함수
# ──────────────────────────────────────────


def _get_line_start(issue: dict[str, Any]) -> int:
    """Issue dict에서 line_start를 추출한다."""
    loc = issue.get("location", {})
    if isinstance(loc, dict):
        return loc.get("line_start", 0)
    return 0


def _extract_function_from_issue(issue: dict[str, Any]) -> str:
    """Issue의 description이나 title에서 함수명을 추출한다."""
    desc = issue.get("description", "")
    # "함수 xxx의" 패턴
    m = re.search(r"함수\s+(\w+)", desc)
    if m:
        return m.group(1)
    return "(unknown)"
