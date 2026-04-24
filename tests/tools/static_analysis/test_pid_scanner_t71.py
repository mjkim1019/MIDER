"""T71 PIDScanner 확장 패턴 회귀 테스트.

T71.1: 여권 1~2자, 대표번호 1XXX, 이메일 broad, 안심번호 0507
T71.4: IMSI/IMEI/ICCID + Luhn
T71.6: 로마자 한글 이름 (순방향/역방향)
"""

import pytest

from mider.tools.static_analysis.pid_scanner import PIDScanner, _passes_luhn


class TestPassportExpansion:
    """T71.1: 여권번호 1~2자 알파벳 + 7~8자 숫자 확장."""

    def test_1letter_8digits_detected(self) -> None:
        findings = PIDScanner.scan_text("여권번호: M12345678")
        assert any(f["type_name"] == "여권번호" for f in findings)

    def test_2letter_7digits_detected(self) -> None:
        """실측 케이스: NT0000074 — 외교관 여권 2자+7자."""
        findings = PIDScanner.scan_text("PASSPORT(NT0000074)")
        assert any(f["type_name"] == "여권번호" and f["value"] == "NT0000074" for f in findings)

    def test_2letter_8digits_detected(self) -> None:
        findings = PIDScanner.scan_text("AB12345678")
        assert any(f["type_name"] == "여권번호" for f in findings)

    def test_3letters_not_detected(self) -> None:
        """3자 알파벳은 탐지 안 됨."""
        findings = PIDScanner.scan_text("ABC1234567")
        assert not any(f["type_name"] == "여권번호" for f in findings)


class TestBizPhone:
    """T71.1: 대표번호 (15XX/16XX/18XX)."""

    def test_biz_with_dash(self) -> None:
        findings = PIDScanner.scan_text("문의 1600-2000")
        assert any(f["type_name"] == "대표번호" and "1600-2000" in f["value"] for f in findings)

    def test_biz_without_dash(self) -> None:
        """실측 케이스: 대시 없이 8자리 연속."""
        findings = PIDScanner.scan_text("Support 16002000 ext")
        assert any(f["type_name"] == "대표번호" and f["value"] == "16002000" for f in findings)

    def test_1577_detected(self) -> None:
        findings = PIDScanner.scan_text("1577-1234")
        assert any(f["type_name"] == "대표번호" for f in findings)

    def test_1234_not_detected(self) -> None:
        """지원 프리픽스 아닌 1234는 탐지 안 됨."""
        findings = PIDScanner.scan_text("key 1234-5678")
        assert not any(f["type_name"] == "대표번호" for f in findings)


class TestSafetyPhone:
    """T71.1: 0507 안심번호."""

    def test_0507_with_dashes(self) -> None:
        findings = PIDScanner.scan_text("안심번호 0507-1234-5678")
        assert any(f["type_name"] == "안심번호" for f in findings)

    def test_0507_3_4_4_format(self) -> None:
        """0507-XXX-XXXX 변형 (중간 3자리)."""
        findings = PIDScanner.scan_text("0507-123-4567")
        assert any(f["type_name"] == "안심번호" for f in findings)


class TestEmailBroad:
    """T71.1: 이메일 broad (도메인 화이트리스트 없이)."""

    def test_skb_domain(self) -> None:
        """실측 케이스: cyber@skbroadband.com."""
        findings = PIDScanner.scan_text("Contact cyber@skbroadband.com")
        assert any(f["type_name"] == "이메일" and f["value"] == "cyber@skbroadband.com" for f in findings)

    def test_skt_domain(self) -> None:
        """실측 케이스: cyber@sktelecom.com."""
        findings = PIDScanner.scan_text("cyber@sktelecom.com")
        assert any(f["type_name"] == "이메일" for f in findings)

    def test_international_domain(self) -> None:
        findings = PIDScanner.scan_text("user@example.io")
        assert any(f["type_name"] == "이메일" for f in findings)

    def test_no_at_not_detected(self) -> None:
        findings = PIDScanner.scan_text("user.example.com")
        assert not any(f["type_name"] == "이메일" for f in findings)


class TestTelecomIdentifiers:
    """T71.4: IMSI/IMEI/ICCID + Luhn."""

    def test_imsi_korea_mcc_450(self) -> None:
        """IMSI: 한국 MCC 450 + 12자리."""
        findings = PIDScanner.scan_text("subscriber=450050123456789")
        assert any(f["type_name"] == "IMSI" for f in findings)

    def test_imsi_non_korean_not_detected(self) -> None:
        """MCC 450 아닌 15자리는 IMSI로 탐지 안 됨."""
        findings = PIDScanner.scan_text("foreign=123450123456789")
        assert not any(f["type_name"] == "IMSI" for f in findings)

    def test_iccid_19digits(self) -> None:
        """ICCID: 89 프리픽스 + 17자리 = 19자리 총."""
        findings = PIDScanner.scan_text("sim=8998201234567890123")
        assert any(f["type_name"] == "ICCID" for f in findings)

    def test_imei_valid_luhn(self) -> None:
        """IMEI: Luhn 유효한 15자리."""
        # 490154203237518 is a valid IMEI (Luhn passes)
        findings = PIDScanner.scan_text("device=490154203237518")
        assert any(f["type_name"] == "IMEI" for f in findings)

    def test_imei_invalid_luhn_rejected(self) -> None:
        """IMEI: Luhn 실패 15자리는 탐지 안 됨 (FP 방지)."""
        findings = PIDScanner.scan_text("num=355555555555555")
        assert not any(f["type_name"] == "IMEI" for f in findings)


class TestLuhnAlgorithm:
    """T71.4: Luhn 체크섬 함수."""

    def test_valid_luhn(self) -> None:
        # 유효 Luhn 예시
        assert _passes_luhn("490154203237518") is True
        assert _passes_luhn("4532015112830366") is True  # 카드번호 예시

    def test_invalid_luhn(self) -> None:
        assert _passes_luhn("355555555555555") is False
        assert _passes_luhn("999999999999999") is False
        assert _passes_luhn("12345678901234") is False

    def test_short_input(self) -> None:
        assert _passes_luhn("1") is False
        assert _passes_luhn("") is False


class TestRomanizedKoreanName:
    """T71.6: 로마자 한글 이름 휴리스틱."""

    def test_surname_first_space(self) -> None:
        findings = PIDScanner.scan_text("Kim Minju")
        assert any(f["type_name"] == "로마자이름" and f["value"] == "Kim Minju" for f in findings)

    def test_surname_first_dot(self) -> None:
        findings = PIDScanner.scan_text("Kim.Minju")
        assert any(f["type_name"] == "로마자이름" for f in findings)

    def test_reverse_order(self) -> None:
        """역방향: 이름-성."""
        findings = PIDScanner.scan_text("// author: Chulsoo Lee")
        assert any(f["type_name"] == "로마자이름" and "Chulsoo Lee" in f["value"] for f in findings)

    def test_triple_name(self) -> None:
        """Triple name: Park Jihye Kim."""
        findings = PIDScanner.scan_text("Park Jihye Kim")
        assert any(f["type_name"] == "로마자이름" for f in findings)

    def test_non_surname_not_detected(self) -> None:
        findings = PIDScanner.scan_text("Abc Def")
        assert not any(f["type_name"] == "로마자이름" for f in findings)


class TestRomanizedNameFPFix:
    """코드 리뷰 반영 — 2자/영단어 성씨 제거로 식별자 오탐 방지 (2026-04-24)."""

    def test_min_size_not_matched(self) -> None:
        """Min, Ma 등 2자 성씨는 제외 → Min_Size 식별자 미탐지."""
        findings = PIDScanner.scan_text("#define MIN_SIZE 10")
        assert not any(f["type_name"] == "로마자이름" for f in findings)

    def test_no_error_not_matched(self) -> None:
        findings = PIDScanner.scan_text("int No_Error = 0;")
        assert not any(f["type_name"] == "로마자이름" for f in findings)

    def test_moon_phase_not_matched(self) -> None:
        """Moon은 영단어라 제외."""
        findings = PIDScanner.scan_text("moon_phase = Moon.Phase")
        assert not any(f["type_name"] == "로마자이름" for f in findings)

    def test_underscore_separator_not_matched(self) -> None:
        """snake_case 구분자(_)는 식별자 충돌 방지 차원에서 제외."""
        findings = PIDScanner.scan_text("const kim_minju_id")
        assert not any(f["type_name"] == "로마자이름" for f in findings)

    def test_distinct_surname_still_works(self) -> None:
        """3자+ 구분 성씨는 정상 탐지 유지."""
        findings = PIDScanner.scan_text("Kim Minju submitted PR")
        assert any(f["type_name"] == "로마자이름" and f["value"] == "Kim Minju" for f in findings)


class TestForeignRegistrationOrder:
    """코드 리뷰 반영 — 외국인등록번호 vs 주민번호 dedup 순서 수정 (2026-04-24)."""

    def test_foreign_rrn_classified_correctly(self) -> None:
        """성별코드 5-8이면 외국인등록번호로 분류 (주민번호가 아님)."""
        findings = PIDScanner.scan_text("외국인: 850101-5234567")
        types = {f["type_name"] for f in findings}
        assert "외국인등록번호" in types
        assert "주민등록번호" not in types

    def test_korean_rrn_still_classified_as_korean(self) -> None:
        """성별코드 1-4는 주민번호로 분류."""
        findings = PIDScanner.scan_text("주민: 850101-1234567")
        assert any(f["type_name"] == "주민등록번호" for f in findings)


class TestBackwardCompatibility:
    """기존 6개 패턴이 계속 동작하는지 확인."""

    def test_rrn_detected(self) -> None:
        findings = PIDScanner.scan_text("주민번호 740111-1234567")
        assert any(f["type_name"] == "주민등록번호" for f in findings)

    def test_phone_detected(self) -> None:
        findings = PIDScanner.scan_text("연락처 010-1234-5678")
        assert any(f["type_name"] == "전화번호" for f in findings)

    def test_card_detected(self) -> None:
        findings = PIDScanner.scan_text("1234-5678-9012-3456")
        assert any(f["type_name"] == "카드번호" for f in findings)

    def test_driver_license_detected(self) -> None:
        findings = PIDScanner.scan_text("11-04-123456-01")
        assert any(f["type_name"] == "운전면허번호" for f in findings)
