"""PIDScanner: 소스코드 내 개인정보(PID) 탐지 Tool.

개선안:
  AS-IS: 숫자 리터럴(LEN[1000000], PGM_ID[010125])이 PID에 오탐되어 소스코드 검사 불가
  TO-BE:
    STEP 1 (전처리): 6자리 이상 연속 숫자 → 첫 자리 유지 + 나머지 0으로 치환
    STEP 2 (후처리): 탐지 결과 중 '첫자리+000...' 패턴 항목 제외 (오탐 필터)

탐지 엔진: 순수 Python 정규식 (OS/플랫폼 무관, .so 불필요)
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


# ── post_check 콜러블 ─────────────────────────────────────────────────────────


def _passes_luhn(value: str) -> bool:
    """Luhn 알고리즘으로 숫자열 체크섬을 검증한다.

    IMEI는 15자리 숫자 중 마지막이 Luhn 체크섬 → 유효 IMEI만 유지 (오탐 감소)
    """
    digits = [int(c) for c in value if c.isdigit()]
    if len(digits) < 2:
        return False

    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ── 정규식 탐지 패턴 정의 ──────────────────────────────────────────────────────
# 각 항목: (컴파일된 패턴, 타입 코드, 한국어 이름, 심각도, scan_on, post_check)
#
# 설계 원칙:
#   - 전처리 후의 마스킹된 텍스트에서 실행 (6자리+ 숫자 그룹은 첫자리+0으로 치환됨)
#   - 탐지 위치는 원본 텍스트에서 후처리로 실제 개인정보 여부를 최종 판별
#   - prefix가 의미 있는 패턴(대표번호/IMSI/ICCID 등)은 scan_on="original"

@dataclass
class _PIDPattern:
    pattern: re.Pattern
    type_code: int
    type_name: str
    severity: str
    # "masked": 6자리+ 숫자 마스킹된 텍스트에 매칭 (주민/카드/운전면허 등 digit-count만 중요한 패턴)
    # "original": 원본 텍스트에 매칭 (대표번호 1XXX/0507/이메일/IMSI 등 prefix가 의미 있는 패턴)
    scan_on: str = "masked"
    # 정규식 매칭 후 추가 검증 콜러블 (True 반환 시 유지). None이면 검증 없음
    # 예: IMEI는 Luhn 체크섬으로 오탐 감소
    post_check: Callable[[str], bool] | None = None


_PATTERNS: list[_PIDPattern] = [
    # 주민등록번호: YYMMDD-SXXXXXX
    # 마스킹 후에도 \d{6}-\d{7} 형식 유지 → 탐지 가능
    # 후처리에서 원본값으로 실제 생년월일 포함 여부 판별
    _PIDPattern(
        pattern=re.compile(r"\b\d{6}[-]\d{7}\b"),
        type_code=0x400,
        type_name="주민등록번호",
        severity="critical",
    ),
    # 전화번호: 010/011/016~019, 02, 0XX 형식
    # 각 구분 그룹이 4자리 이하 → 마스킹 안 됨 → 원형 그대로 탐지
    _PIDPattern(
        pattern=re.compile(
            r"\b(?:01[016789]|02|0[3-9]\d)"   # 국번
            r"[-\s]?\d{3,4}"                   # 중간번호
            r"[-\s]?\d{4}\b"                   # 끝번호
        ),
        type_code=0x200,
        type_name="전화번호",
        severity="high",
    ),
    # 카드번호: XXXX-XXXX-XXXX-XXXX (4자리 그룹 → 마스킹 안 됨)
    _PIDPattern(
        pattern=re.compile(r"\b\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}\b"),
        type_code=0x010,
        type_name="카드번호",
        severity="high",
    ),
    # 여권번호: 영문 1~2자 + 숫자 7~8자리 (예: M12345678, NT0000074)
    # T71.1: 기존 1자+8자 패턴을 2자 변형까지 확장 (외교관 NT/관용 MT 등)
    _PIDPattern(
        pattern=re.compile(r"\b[A-Z]{1,2}\d{7,8}\b"),
        type_code=0x100,
        type_name="여권번호",
        severity="high",
    ),
    # 운전면허번호: XX-XX-XXXXXX-XX (예: 11-04-123456-01)
    _PIDPattern(
        pattern=re.compile(r"\b\d{2}[-]\d{2}[-]\d{6}[-]\d{2}\b"),
        type_code=0x004,
        type_name="운전면허번호",
        severity="high",
    ),
    # 외국인등록번호: YYMMDD-5XXXXXX ~ YYMMDD-8XXXXXX
    _PIDPattern(
        pattern=re.compile(r"\b\d{6}[-][5-8]\d{6}\b"),
        type_code=0x008,
        type_name="외국인등록번호",
        severity="high",
    ),

    # ── T71.1 신규 ─────────────────────────────────────────────────────────────
    # 대표번호 (15XX/16XX/18XX): 대시 유무 모두. 프리픽스 보존 필수 → original 스캔
    _PIDPattern(
        pattern=re.compile(
            r"\b(?:1533|1544|1555|1566|1577|1588|1599|1600|1644|1661|1666|"
            r"1670|1688|1800|1855|1877|1899)[-\s]?\d{4}\b"
        ),
        type_code=0x040,
        type_name="대표번호",
        severity="medium",
        scan_on="original",
    ),
    # 안심번호 (0507-XXX(X)-XXXX): SKB 가상번호 서비스. 프리픽스 보존 필수
    _PIDPattern(
        pattern=re.compile(r"\b0507[-\s]?\d{3,4}[-\s]?\d{4}\b"),
        type_code=0x080,
        type_name="안심번호",
        severity="high",
        scan_on="original",
    ),
    # 이메일 (broad): 도메인 화이트리스트 없이 전체 탐지
    # 오탐 허용 (예: LLM 프롬프트 내 샘플 이메일) — over-mask 선호 원칙
    _PIDPattern(
        pattern=re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        type_code=0x020,
        type_name="이메일",
        severity="medium",
        scan_on="original",
    ),

    # ── T71.4 통신사 식별자 (SKB 맥락) ─────────────────────────────────────────
    # IMSI (International Mobile Subscriber Identity): 한국 MCC 450 + 12자리
    # 15자리 중 "450" 프리픽스가 매우 구체적이라 FP 낮음
    _PIDPattern(
        pattern=re.compile(r"\b450\d{12}\b"),
        type_code=0x1000,
        type_name="IMSI",
        severity="critical",
        scan_on="original",
    ),
    # ICCID (SIM card ID): "89" 국제 프리픽스 + 17~19자리
    _PIDPattern(
        pattern=re.compile(r"\b89\d{17,19}\b"),
        type_code=0x4000,
        type_name="ICCID",
        severity="critical",
        scan_on="original",
    ),
    # IMEI (Mobile Equipment Identity): 15자리 숫자 + Luhn 체크섬
    # 15자리는 광범위하므로 Luhn 검증으로 FP 감소
    _PIDPattern(
        pattern=re.compile(r"\b\d{15}\b"),
        type_code=0x2000,
        type_name="IMEI",
        severity="critical",
        scan_on="original",
        post_check=_passes_luhn,
    ),
]


def _run_regex_scan(
    masked_text: str, original_text: str,
) -> list[tuple[int, int, _PIDPattern]]:
    """패턴별 scan_on 지정에 따라 masked/original 텍스트에 정규식을 적용한다.

    Returns:
        [(시작위치, 끝위치, 패턴정보), ...] — 원본 텍스트와 위치 동일 (마스킹은 길이 보존)
    """
    candidates: list[tuple[int, int, _PIDPattern]] = []
    seen_ranges: set[tuple[int, int]] = set()

    for pid_pattern in _PATTERNS:
        target = original_text if pid_pattern.scan_on == "original" else masked_text
        for m in pid_pattern.pattern.finditer(target):
            span = (m.start(), m.end())
            # 동일 위치 중복 탐지 방지 (더 구체적인 패턴 우선)
            if span not in seen_ranges:
                seen_ranges.add(span)
                candidates.append((m.start(), m.end(), pid_pattern))

    return candidates


class PIDScanner(BaseTool):
    """소스코드 내 개인정보(PID) 탐지 Tool.

    전처리(6자리 이상 숫자 마스킹) → 정규식 탐지 → 후처리(오탐 필터링) 파이프라인으로
    소스코드 숫자 리터럴에 의한 오탐을 제거하고 실제 개인정보만 검출한다.
    """

    def execute(self, *, file: str) -> ToolResult:
        """소스코드 파일에서 개인정보를 탐지한다.

        Args:
            file: 분석할 파일 경로

        Returns:
            ToolResult(data={findings, total_findings})
        """
        file_path = Path(file)
        if not file_path.exists():
            return ToolResult(
                success=False,
                error=f"파일 없음: {file}",
                data={"findings": [], "total_findings": 0},
            )

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"파일 읽기 실패: {e}",
                data={"findings": [], "total_findings": 0},
            )

        # STEP 1: 전처리 — 6자리 이상 연속 숫자 마스킹
        masked = self._mask_numeric_literals(content)

        # 정규식 탐지 (패턴별 scan_on에 따라 masked/original 분기)
        candidates = _run_regex_scan(masked, content)

        # STEP 2: 후처리 — 원본 텍스트 기준으로 오탐 필터링
        lines = content.splitlines()
        findings: list[dict[str, Any]] = []

        for start, end, pid_pattern in candidates:
            # 원본 텍스트에서 실제 값 추출
            # (마스킹은 동일 길이로 치환하므로 위치가 그대로 대응됨)
            original_value = content[start:end]

            if self._is_masked_false_positive(original_value):
                logger.debug(f"오탐 제외 (마스킹 패턴): '{original_value[:30]}'")
                continue
            if pid_pattern.post_check is not None and not pid_pattern.post_check(original_value):
                logger.debug(f"오탐 제외 ({pid_pattern.type_name} post_check): '{original_value[:30]}'")
                continue

            line_num, col_num = self._position_to_line_col(content, start)
            code_snippet = lines[line_num - 1].strip()[:120] if line_num <= len(lines) else ""

            findings.append({
                "pattern_id": f"PID_{pid_pattern.type_name.replace(' ', '_')}",
                "severity": pid_pattern.severity,
                "type_name": pid_pattern.type_name,
                "line": line_num,
                "col": col_num,
                "code": code_snippet,
                "detected_value": self._mask_for_display(original_value),
                "description": (
                    f"{pid_pattern.type_name}이(가) 소스코드에 하드코딩되어 있습니다. "
                    f"개인정보보호법 위반 및 보안 취약점이 될 수 있습니다."
                ),
            })

        logger.debug(f"PID 스캔 완료: {file} → {len(findings)} findings")

        return ToolResult(
            success=True,
            data={"findings": findings, "total_findings": len(findings)},
        )

    # ── STEP 1: 전처리 ──────────────────────────────────────────────────────────

    @staticmethod
    def _mask_numeric_literals(text: str) -> str:
        """6자리 이상 연속 숫자의 첫 자리만 유지하고 나머지를 0으로 치환한다.

        예:
            1000000000 → 1000000000  (이미 첫자리+0 패턴 — 통과)
            123456789  → 100000000
            123456     → 100000
            12345      → 12345       (5자리 이하 유지)
            1234       → 1234        (4자리 이하 유지)
        """
        def _replace(m: re.Match) -> str:
            d = m.group(0)
            if len(d) >= 6:
                return d[0] + "0" * (len(d) - 1)
            return d

        return re.sub(r"\d+", _replace, text)

    # ── STEP 2: 후처리 ──────────────────────────────────────────────────────────

    @staticmethod
    def _is_masked_false_positive(original_value: str) -> bool:
        """탐지된 원본 문자열이 숫자 리터럴 오탐인지 판별한다.

        판별 기준:
          1. 원본값에서 6자리 이상 연속 숫자 그룹 추출
          2. 모든 그룹이 '첫 자리 + 나머지 전부 0' 패턴 → 오탐
          3. 하나라도 0이 아닌 숫자가 나머지에 포함 → 실제 개인정보

        예:
          "100000000"       → True  오탐 (1+00000000)
          "010-1234-5678"   → False 실제 PII (6자리+ 그룹 없음)
          "800000-1000000"  → True  오탐 (두 그룹 모두 첫자리+0)
          "740111-1234567"  → False 실제 PII (740111의 나머지에 비-0 포함)
        """
        long_groups = re.findall(r"\d{6,}", original_value)

        if not long_groups:
            # 6자리 이상 숫자 없음 → 마스킹 대상 아님 → 오탐 아님
            return False

        for group in long_groups:
            rest = group[1:]
            if rest and not all(c == "0" for c in rest):
                return False  # 비-0 숫자 존재 → 실제 개인정보

        return True  # 전부 마스킹 패턴 → 오탐

    # ── 유틸리티 ────────────────────────────────────────────────────────────────

    @staticmethod
    def _position_to_line_col(text: str, char_pos: int) -> tuple[int, int]:
        """문자 위치(0-based)를 (줄 번호, 열 번호)(1-based)로 변환한다."""
        before = text[:char_pos]
        split = before.split("\n")
        return len(split), len(split[-1]) + 1

    @staticmethod
    def _mask_for_display(text: str) -> str:
        """탐지된 개인정보 일부를 마스킹하여 리포트에 안전하게 표시한다."""
        if len(text) <= 4:
            return "*" * len(text)
        visible = max(2, len(text) // 4)
        return text[:visible] + "*" * (len(text) - visible)

    # ── 텍스트 스캐닝 인터페이스 (LLM 전송 전 선탐지용) ─────────────────────────

    @classmethod
    def scan_text(cls, text: str) -> list[dict[str, Any]]:
        """주어진 텍스트에서 PII 후보를 탐지한다 (파일 I/O 없이).

        T71.2 (LLMClient 선마스킹) 파이프라인에서 사용한다.

        Returns:
            [{type_name, severity, start, end, value}, ...]
            — value는 원본 문자열 (마스킹 전)
        """
        if not text:
            return []

        masked = cls._mask_numeric_literals(text)
        candidates = _run_regex_scan(masked, text)

        findings: list[dict[str, Any]] = []
        for start, end, pid_pattern in candidates:
            original_value = text[start:end]
            if cls._is_masked_false_positive(original_value):
                continue
            if pid_pattern.post_check is not None and not pid_pattern.post_check(original_value):
                # post_check(예: Luhn)를 통과하지 못하면 제외
                continue
            findings.append({
                "type_name": pid_pattern.type_name,
                "severity": pid_pattern.severity,
                "start": start,
                "end": end,
                "value": original_value,
            })
        return findings
