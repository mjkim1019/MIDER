"""PIDScanner 숫자 마스킹 단위 테스트.

6자리 이상 연속 숫자 마스킹 기준 변경을 검증한다.
"""

import pytest

from mider.tools.static_analysis.pid_scanner import PIDScanner


class TestMaskNumericLiterals:
    """_mask_numeric_literals 메서드 테스트."""

    def test_4_digits_unchanged(self) -> None:
        """4자리 숫자는 그대로 유지된다."""
        assert PIDScanner._mask_numeric_literals("1234") == "1234"

    def test_5_digits_unchanged(self) -> None:
        """5자리 숫자는 그대로 유지된다 (6자리 미만)."""
        assert PIDScanner._mask_numeric_literals("12345") == "12345"

    def test_6_digits_masked(self) -> None:
        """6자리 숫자는 첫자리+0으로 마스킹된다."""
        assert PIDScanner._mask_numeric_literals("123456") == "100000"

    def test_9_digits_masked(self) -> None:
        """9자리 숫자는 첫자리+0으로 마스킹된다."""
        assert PIDScanner._mask_numeric_literals("987654321") == "900000000"

    def test_10_digits_masked(self) -> None:
        """10자리 숫자도 마스킹된다."""
        assert PIDScanner._mask_numeric_literals("1234567890") == "1000000000"

    def test_already_masked_pattern_passes_through(self) -> None:
        """이미 첫자리+0 패턴인 숫자는 결과가 동일하다."""
        assert PIDScanner._mask_numeric_literals("1000000") == "1000000"

    def test_mixed_text_and_digits(self) -> None:
        """텍스트 + 숫자가 섞인 입력에서 6자리 이상만 마스킹된다."""
        assert PIDScanner._mask_numeric_literals("LEN[1000000]") == "LEN[1000000]"
        assert PIDScanner._mask_numeric_literals("PGM_ID[12345]") == "PGM_ID[12345]"
        assert PIDScanner._mask_numeric_literals("PGM_ID[123456]") == "PGM_ID[100000]"

    def test_hyphenated_groups_unchanged(self) -> None:
        """하이픈으로 구분된 4자리 이하 그룹은 마스킹되지 않는다."""
        assert PIDScanner._mask_numeric_literals("010-1234-5678") == "010-1234-5678"
        assert PIDScanner._mask_numeric_literals("1030-2300") == "1030-2300"


class TestIsMaskedFalsePositive:
    """_is_masked_false_positive 메서드 테스트."""

    def test_all_zeros_rest_is_false_positive(self) -> None:
        """첫자리+0 패턴은 오탐이다."""
        assert PIDScanner._is_masked_false_positive("100000000") is True

    def test_real_pid_is_not_false_positive(self) -> None:
        """실제 개인정보 패턴은 오탐이 아니다."""
        assert PIDScanner._is_masked_false_positive("740111-1234567") is False

    def test_short_groups_not_false_positive(self) -> None:
        """6자리 미만 숫자 그룹만 있으면 오탐이 아니다."""
        assert PIDScanner._is_masked_false_positive("010-1234-5678") is False
        assert PIDScanner._is_masked_false_positive("12345") is False

    def test_dual_masked_groups_is_false_positive(self) -> None:
        """두 그룹 모두 첫자리+0 패턴이면 오탐이다."""
        assert PIDScanner._is_masked_false_positive("800000-1000000") is True
