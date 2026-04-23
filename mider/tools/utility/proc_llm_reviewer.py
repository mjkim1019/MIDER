"""ProCLLMReviewer: 정적/교차 분석 결과를 LLM이 최종 검토한다.

설계서 V3 §6 기반.
- 역할: FP 제거, 심각도 조정, fix 제안 생성, 추가 이슈 탐지
- Case 1: 0 findings → skip
- Case 2: 1~20 findings → 단일 LLM 호출
- Case 3: 21+ findings → 함수별 그룹핑, 병렬 호출 (max 5)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mider.agents.base_agent import BaseAgent
from mider.config.settings_loader import (
    get_agent_fallback_model,
    get_agent_model,
    get_agent_temperature,
    get_safe_function_prefixes,
)
from mider.models.analysis_result import CodeFix, Issue, Location
from mider.models.proc_partition import (
    Finding,
    PartitionResult,
)
from mider.tools.utility.issue_filter import format_safe_prefixes_for_prompt

logger = logging.getLogger(__name__)

# 병렬 LLM 호출 제한
_MAX_CONCURRENT_LLM = 5
_GROUP_STAGGER_SECONDS = 3.0
# 함수당 최대 findings
_MAX_FINDINGS_PER_GROUP = 15
# 그룹핑 기준
_GROUP_THRESHOLD = 20

_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}

_SYSTEM_PROMPT = (
    "당신은 Oracle Pro*C 코드 안전성 전문가입니다.\n"
    "정적 분석 결과를 검토하여 false positive를 제거하고, "
    "심각도를 조정하며, 수정 제안을 생성합니다.\n"
    "반드시 JSON 형식으로 응답하세요."
)

_PROFRAME_NOTES = """## Proframe 환경 면제 사항
- INDICATOR 변수 누락: NVL() 사용 시 false positive
- sizeof(type_name) vs sizeof(var_name): Proframe 표준 패턴
- swgf_snprintf 반환값 미검사: 정상 (Proframe wrapper)
- 프레임워크 변수 (INPUT, NGMHEADER, ctx): NULL 체크 불필요
- 전역 변수 thread safety: 단일 프로세스 환경이므로 불필요
"""


def _build_safe_function_note() -> str:
    """신뢰 함수 접두사 안내 블록을 생성한다 (runtime-evaluated for config updates)."""
    prefixes_display = format_safe_prefixes_for_prompt(get_safe_function_prefixes())
    return (
        "## 프로젝트 신뢰 함수 (경계 검증 보장)\n"
        f"다음 접두사의 프로젝트 자체 함수는 반환값의 경계를 내부에서 보장합니다: "
        f"{prefixes_display}\n"
        "이 함수들의 반환값이 배열 인덱스/루프 한계값으로 사용되는 경우를 "
        "\"배열 인덱스 경계값 미검증\" 류로 **보고하지 마세요**.\n"
        "예시 (이슈 아님): `totcnt = mpfmdbio_reccnt(); for(i=0;i<totcnt;i++) arr[i]=...;`\n"
    )

_REVIEW_INSTRUCTIONS = """## 지시사항
1. 각 finding을 판정하세요: true positive / false positive
2. true positive인 경우 심각도를 검증/조정하세요
3. 각 이슈에 대해 before/after 수정 제안을 생성하세요
4. 정적 분석이 놓친 의미적(semantic) 이슈가 있으면 추가하세요
5. 아래 JSON 형식으로 응답하세요:

```json
{
  "issues": [
    {
      "issue_id": "PC-001",
      "category": "data_integrity",
      "severity": "high",
      "title": "이슈 제목 (한국어)",
      "description": "상세 설명 (한국어)",
      "location": {"file": "...", "line_start": 123, "line_end": 125},
      "fix": {"before": "수정 전 코드", "after": "수정 후 코드", "description": "수정 설명"},
      "source": "hybrid",
      "static_tool": "embedded_sql_static",
      "static_rule": "SQL_SQLCA_MISSING",
      "confidence": 0.92,
      "false_positive": false
    }
  ]
}
```

- source: "hybrid" (정적 finding을 LLM이 보강), "llm" (LLM이 새로 탐지)
- false_positive: true인 항목은 결과에서 제외됩니다
- category: memory_safety, null_safety, data_integrity, error_handling, security, performance, code_quality
"""


class ProCLLMReviewer(BaseAgent):
    """정적/교차 분석 Finding을 LLM으로 최종 검토하여 Issue를 생성한다."""

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
        self._stats: dict[str, Any] = {}

    async def run(self, **kwargs: Any) -> dict:
        """BaseAgent 인터페이스 구현."""
        return await self.review(**kwargs)

    async def review(
        self,
        *,
        findings: list[Finding],
        partition: PartitionResult,
        file_path: str,
    ) -> dict[str, Any]:
        """Finding 리스트를 LLM으로 검토하여 Issue 리스트를 반환한다.

        Returns:
            {"issues": list[dict], "tokens_used": int, "stats": dict}
        """
        self._stats = {"tokens_used": 0, "llm_calls": 0}

        # Case 1: findings 없으면 skip
        if not findings:
            logger.info("Finding 없음 — LLM 호출 건너뜀")
            return {"issues": [], "tokens_used": 0, "stats": self._stats}

        # Finding 정렬: severity desc, 정적 > 교차
        sorted_findings = self._sort_findings(findings)

        # Case 2 vs Case 3
        if len(sorted_findings) <= _GROUP_THRESHOLD:
            raw_issues = await self._review_single(sorted_findings, partition, file_path)
        else:
            raw_issues = await self._review_grouped(sorted_findings, partition, file_path)

        # false positive 제거
        issues = [i for i in raw_issues if not i.get("false_positive", False)]

        return {
            "issues": issues,
            "tokens_used": self._stats["tokens_used"],
            "stats": self._stats,
        }

    # ──────────────────────────────────────────
    # Case 2: 단일 호출
    # ──────────────────────────────────────────

    async def _review_single(
        self,
        findings: list[Finding],
        partition: PartitionResult,
        file_path: str,
    ) -> list[dict[str, Any]]:
        """1~20개 findings를 단일 LLM 호출로 검토."""
        prompt = self._build_review_prompt(findings, partition, file_path)
        return await self._call_and_parse(prompt)

    # ──────────────────────────────────────────
    # Case 3: 그룹핑 병렬 호출
    # ──────────────────────────────────────────

    async def _review_grouped(
        self,
        findings: list[Finding],
        partition: PartitionResult,
        file_path: str,
    ) -> list[dict[str, Any]]:
        """21+ findings를 함수별로 그룹핑 후 병렬 LLM 호출."""
        groups = self._group_by_function(findings)
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_LLM)
        all_issues: list[dict[str, Any]] = []

        async def process_group(
            group_findings: list[Finding], delay: float,
        ) -> list[dict[str, Any]]:
            if delay > 0:
                await asyncio.sleep(delay)
            async with semaphore:
                prompt = self._build_review_prompt(
                    group_findings, partition, file_path,
                )
                return await self._call_and_parse(prompt)

        tasks = []
        for i, group in enumerate(groups):
            delay = i * _GROUP_STAGGER_SECONDS
            tasks.append(process_group(group, delay))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                all_issues.extend(result)
            elif isinstance(result, Exception):
                logger.warning(f"그룹 LLM 호출 실패: {result}")

        return all_issues

    # ──────────────────────────────────────────
    # LLM 호출 + 파싱
    # ──────────────────────────────────────────

    async def _call_and_parse(
        self, prompt: str,
    ) -> list[dict[str, Any]]:
        """LLM 호출 후 JSON 파싱."""
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            response = await self.call_llm(messages, json_mode=True)
            self._stats["llm_calls"] += 1
            self._stats["tokens_used"] += (
                self._estimate_tokens(prompt)
                + self._estimate_tokens(response)
            )
        except Exception as e:
            logger.warning(f"LLM 호출 실패, 정적 findings 반환: {e}")
            return []

        try:
            result = json.loads(response)
        except (json.JSONDecodeError, TypeError):
            stripped_resp = response.strip()
            if stripped_resp.startswith("[Error:"):
                logger.warning("Pro*C LLM 오류 응답: %s", stripped_resp)
            else:
                logger.warning("LLM 응답 JSON 파싱 실패 (처음 300자): %s", response[:300])
            return []

        if not isinstance(result, dict):
            return []

        issues = result.get("issues", [])
        if not isinstance(issues, list):
            return []

        # source 필드 보정
        valid_sources = {"static_analysis", "llm", "hybrid"}
        for issue in issues:
            if isinstance(issue, dict):
                if issue.get("source") not in valid_sources:
                    issue["source"] = "llm"

        return issues

    # ──────────────────────────────────────────
    # 프롬프트 구축
    # ──────────────────────────────────────────

    def _build_review_prompt(
        self,
        findings: list[Finding],
        partition: PartitionResult,
        file_path: str,
    ) -> str:
        """검토 프롬프트를 구축한다."""
        sections: list[str] = []

        # 1. 정적 분석 결과
        sections.append("## 정적 분석 결과")
        findings_data = []
        for f in findings:
            findings_data.append({
                "finding_id": f.finding_id,
                "rule_id": f.rule_id,
                "severity": f.severity,
                "category": f.category,
                "title": f.title,
                "description": f.description,
                "line_start": f"L{f.origin_line_start}",
                "line_end": f"L{f.origin_line_end}",
                "function": f.function_name,
                "source_layer": f.source_layer,
                "tool": f.tool,
            })
        sections.append(json.dumps(findings_data, ensure_ascii=False, indent=2))

        # 2. 관련 코드 스니펫
        sections.append("\n## 관련 코드 스니펫")
        code_snippets = self._extract_code_snippets(findings, partition)
        for snippet in code_snippets:
            sections.append(snippet)

        # 3. 컨텍스트 정보
        sections.append("\n## 컨텍스트 정보")

        # 관련 호스트 변수
        related_hvars = self._get_related_host_vars(findings, partition)
        if related_hvars:
            sections.append(f"### 호스트 변수\n{related_hvars}")

        # 관련 커서
        related_cursors = self._get_related_cursors(findings, partition)
        if related_cursors:
            sections.append(f"### 커서\n{related_cursors}")

        # 글로벌 컨텍스트 요약
        gc = partition.global_context
        if gc.includes:
            inc_list = ", ".join(i.statement for i in gc.includes[:10])
            sections.append(f"### Include\n{inc_list}")

        # 4. Proframe 면제 + 신뢰 함수 + 지시사항
        sections.append(f"\n{_PROFRAME_NOTES}")
        sections.append(f"\n{_build_safe_function_note()}")
        sections.append(f"\n{_REVIEW_INSTRUCTIONS}")

        # 파일 경로
        sections.insert(0, f"## 대상 파일\n{file_path}\n")

        return "\n".join(sections)

    def _extract_code_snippets(
        self,
        findings: list[Finding],
        partition: PartitionResult,
    ) -> list[str]:
        """Finding별 관련 코드 스니펫을 추출한다."""
        snippets: list[str] = []
        lines = partition.file_content.splitlines()
        seen_ranges: set[tuple[int, int]] = set()

        for f in findings:
            # 주변 ±5줄 컨텍스트
            start = max(1, f.origin_line_start - 5)
            end = min(len(lines), f.origin_line_end + 5)
            key = (start, end)
            if key in seen_ranges:
                continue
            seen_ranges.add(key)

            code_lines = []
            for i in range(start - 1, end):
                marker = ">>>" if f.origin_line_start <= (i + 1) <= f.origin_line_end else "   "
                code_lines.append(f"{marker} {i + 1:5d} | {lines[i]}")

            snippets.append(
                f"### {f.finding_id}: {f.title} "
                f"(L{f.origin_line_start}~L{f.origin_line_end})\n"
                f"```c\n{''.join(line + chr(10) for line in code_lines)}```"
            )

        return snippets

    def _get_related_host_vars(
        self,
        findings: list[Finding],
        partition: PartitionResult,
    ) -> str:
        """Finding과 관련된 호스트 변수 정보를 요약한다."""
        # 관련 함수 수집
        funcs = {f.function_name for f in findings if f.function_name}
        if not funcs:
            return ""

        related = [
            hv for hv in partition.host_variables
            if hv.declared_in_function in funcs or hv.declared_in_function is None
        ]
        if not related:
            return ""

        lines = []
        for hv in related[:20]:
            ind = f" (indicator: {hv.indicator_name})" if hv.indicator_name else ""
            lines.append(
                f"- {hv.name}: {hv.declared_type} "
                f"(L{hv.declared_line}, func={hv.declared_in_function or 'global'}){ind}"
            )
        return "\n".join(lines)

    def _get_related_cursors(
        self,
        findings: list[Finding],
        partition: PartitionResult,
    ) -> str:
        """Finding과 관련된 커서 정보를 요약한다."""
        funcs = {f.function_name for f in findings if f.function_name}
        if not funcs:
            return ""

        lines = []
        for cursor in partition.cursor_map:
            cursor_funcs = {
                e.function_name for e in cursor.events if e.function_name
            }
            if cursor_funcs & funcs:
                events_str = " → ".join(
                    f"{e.event_type}(L{e.line})" for e in cursor.events
                )
                missing = cursor.missing_events
                status = "완전" if cursor.is_complete else f"누락: {', '.join(missing)}"
                lines.append(
                    f"- {cursor.cursor_name}: {events_str} [{status}]"
                )
        return "\n".join(lines) if lines else ""

    # ──────────────────────────────────────────
    # 유틸리티
    # ──────────────────────────────────────────

    @staticmethod
    def _sort_findings(findings: list[Finding]) -> list[Finding]:
        """severity desc, 정적 > 교차 순으로 정렬."""
        layer_rank = {"static": 2, "cross": 1, "llm": 0}
        return sorted(
            findings,
            key=lambda f: (
                -_SEVERITY_RANK.get(f.severity, 0),
                -layer_rank.get(f.source_layer, 0),
            ),
        )

    @staticmethod
    def _group_by_function(findings: list[Finding]) -> list[list[Finding]]:
        """함수별로 그룹핑. 함수 없는 것은 별도 그룹."""
        groups: dict[str, list[Finding]] = {}
        for f in findings:
            key = f.function_name or "(global)"
            groups.setdefault(key, []).append(f)

        result = []
        for group in groups.values():
            # 그룹당 최대 findings 수 제한
            result.append(group[:_MAX_FINDINGS_PER_GROUP])
        return result

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """토큰 수 추정 (한글+코드 혼합)."""
        return len(text) // 3

    def convert_findings_to_issues(
        self,
        findings: list[Finding],
        file_path: str,
    ) -> list[dict[str, Any]]:
        """LLM 호출 없이 Finding을 Issue dict로 변환한다 (Fallback 용)."""
        issues = []
        for i, f in enumerate(findings, 1):
            issues.append({
                "issue_id": f"PC-{i:03d}",
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
        return issues
