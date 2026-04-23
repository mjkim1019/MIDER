"""SecretScanner: 소스코드/메시지 내 API 키·Secret·하드코딩 자격증명 탐지.

PII는 아니지만 유출 시 PII보다 더 치명적인 secret을 AICA 전송 전에 로컬 마스킹한다.
detect-secrets / gitleaks 패턴 중 자주 쓰이는 것만 정규식으로 포팅 (외부 의존성 0).

T71.5: PII 전처리 강화의 보안 확장.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _SecretPattern:
    pattern: re.Pattern
    type_name: str
    severity: str


# 각 패턴은 원본 텍스트에서 매칭 (prefix·포맷이 핵심이라 masking 불필요)
_PATTERNS: list[_SecretPattern] = [
    # AWS Access Key ID: AKIA + 16자 대문자/숫자
    _SecretPattern(
        pattern=re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
        type_name="AWS_ACCESS_KEY",
        severity="critical",
    ),
    # GitHub Personal Access Token: ghp_ 또는 github_pat_ 프리픽스
    _SecretPattern(
        pattern=re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{36,}\b"),
        type_name="GITHUB_TOKEN",
        severity="critical",
    ),
    # Google API Key: AIza + 35자
    _SecretPattern(
        pattern=re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        type_name="GOOGLE_API_KEY",
        severity="critical",
    ),
    # Stripe Live/Test Key: sk_live_/sk_test_ + 24자+
    _SecretPattern(
        pattern=re.compile(r"\bsk_(?:live|test)_[0-9a-zA-Z]{24,}\b"),
        type_name="STRIPE_KEY",
        severity="critical",
    ),
    # JWT (3-part base64 with dots): eyJ... . eyJ... . signature
    _SecretPattern(
        pattern=re.compile(
            r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
        type_name="JWT",
        severity="high",
    ),
    # DB Connection URL: postgres/mysql/mongodb + 자격증명 포함
    _SecretPattern(
        pattern=re.compile(
            r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://"
            r"[^\s\"']+"
        ),
        type_name="DB_URL",
        severity="critical",
    ),
    # Hardcoded password/secret/api_key = "..."
    # 값 길이 4자 이상 (빈 문자열·짧은 placeholder 제외)
    _SecretPattern(
        pattern=re.compile(
            r"(?i)\b(?:password|passwd|pwd|pass|secret|api[_-]?key|token)\s*[:=]\s*"
            r"[\"']([^\"']{4,})[\"']"
        ),
        type_name="HARDCODED_SECRET",
        severity="critical",
    ),
    # Base64 blob: 40자+ base64 형식 (API 응답·토큰에 흔함)
    # 오탐 많으므로 severity=medium, AICA가 콘텐츠 필터로 2차 방어
    _SecretPattern(
        pattern=re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
        type_name="BASE64_BLOB",
        severity="medium",
    ),
]


class SecretScanner:
    """Secret·API key 탐지 스캐너.

    PIDScanner와 유사한 구조지만 별도 파일로 분리:
    - PII ≠ Secret (성격·법적 분류 다름)
    - Secret은 PII보다 보고 severity·우선순위 높음
    """

    @classmethod
    def scan_text(cls, text: str) -> list[dict[str, Any]]:
        """텍스트에서 secret 후보를 탐지한다.

        Returns:
            [{type_name, severity, start, end, value}, ...]
        """
        if not text:
            return []

        findings: list[dict[str, Any]] = []
        seen_ranges: set[tuple[int, int]] = set()

        for secret_pattern in _PATTERNS:
            for m in secret_pattern.pattern.finditer(text):
                span = (m.start(), m.end())
                if span in seen_ranges:
                    continue
                seen_ranges.add(span)
                findings.append({
                    "type_name": secret_pattern.type_name,
                    "severity": secret_pattern.severity,
                    "start": m.start(),
                    "end": m.end(),
                    "value": m.group(0),
                })
        return findings
