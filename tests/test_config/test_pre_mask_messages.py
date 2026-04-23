"""T71.2 _pre_mask_messages 단위 테스트.

AICA 전송 전 로컬 PII/Secret 선마스킹 파이프라인.
"""

from mider.config.llm_client import _pre_mask_messages


class TestPreMaskMessages:
    def test_empty_messages(self) -> None:
        assert _pre_mask_messages([]) == []

    def test_no_pii_unchanged(self) -> None:
        msgs = [{"role": "user", "content": "Analyze this code: int x = 10;"}]
        result = _pre_mask_messages(msgs)
        assert result[0]["content"] == msgs[0]["content"]

    def test_passport_masked(self) -> None:
        """실측 케이스: 여권번호 2자+7자."""
        msgs = [{"role": "user", "content": "char p[20] = \"NT0000074\";"}]
        result = _pre_mask_messages(msgs)
        # 원본이 아니라 마스킹된 상태로 포함
        assert "NT0000074" not in result[0]["content"]
        assert "*" in result[0]["content"]

    def test_skb_email_masked(self) -> None:
        """실측 케이스: SKB 이메일."""
        msgs = [{"role": "user", "content": "contact cyber@skbroadband.com"}]
        result = _pre_mask_messages(msgs)
        assert "cyber@skbroadband.com" not in result[0]["content"]

    def test_biz_phone_masked(self) -> None:
        """실측 케이스: 1600-2000 대표번호."""
        msgs = [{"role": "user", "content": "Support: 16002000"}]
        result = _pre_mask_messages(msgs)
        assert "16002000" not in result[0]["content"]

    def test_multiple_pii_all_masked(self) -> None:
        """여러 PII가 한 메시지에 있을 때 모두 마스킹."""
        msgs = [{
            "role": "user",
            "content": 'name="Kim Minju"; email="test@skb.com"; password="secret123"',
        }]
        result = _pre_mask_messages(msgs)
        content = result[0]["content"]
        assert "Kim Minju" not in content
        assert "test@skb.com" not in content
        # HARDCODED_SECRET는 password="secret123" 전체 매칭, 일부 마스킹
        assert "secret123" not in content

    def test_preserves_structure(self) -> None:
        """role 등 다른 필드는 유지."""
        msgs = [
            {"role": "system", "content": "You are analyzer."},
            {"role": "user", "content": "email: a@b.co"},
        ]
        result = _pre_mask_messages(msgs)
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert len(result) == 2

    def test_same_value_masked_once(self) -> None:
        """동일 값 반복 시 중복 마스킹 없이 1회 치환 (.replace는 전체 치환)."""
        msgs = [{
            "role": "user",
            "content": "Kim Minju is here. Kim Minju also appears.",
        }]
        result = _pre_mask_messages(msgs)
        # 둘 다 마스킹됨
        assert "Kim Minju" not in result[0]["content"]

    def test_secret_higher_priority(self) -> None:
        """AWS key 같은 critical secret은 반드시 마스킹."""
        msgs = [{"role": "user", "content": 'aws = "AKIAIOSFODNN7EXAMPLE"'}]
        result = _pre_mask_messages(msgs)
        assert "AKIAIOSFODNN7EXAMPLE" not in result[0]["content"]


class TestLengthPreservation:
    """_mask_center는 길이 보존 — AICA 위치 분석 호환성 확보."""

    def test_length_preserved_after_masking(self) -> None:
        original_content = "email: test@example.com — end"
        msgs = [{"role": "user", "content": original_content}]
        result = _pre_mask_messages(msgs)
        # 마스킹 후 길이 동일
        assert len(result[0]["content"]) == len(original_content)


class TestReviewFixes:
    """코드 리뷰에서 발견된 CRITICAL/HIGH 이슈 회귀 테스트 (2026-04-24)."""

    def test_secret_fully_masked_not_center(self) -> None:
        """CRITICAL: HARDCODED_SECRET은 _mask_center(가운데만)가 아닌 전체 '*'로 마스킹되어야 함.
        이전 버그: password="admin1234" → password=**admin1234" (값 노출)
        """
        msgs = [{"role": "user", "content": 'password = "admin1234"'}]
        result = _pre_mask_messages(msgs)
        # admin1234가 어떤 형태로든 남아있으면 안 됨 (부분 문자열 검사)
        assert "admin1234" not in result[0]["content"]
        assert "admin" not in result[0]["content"]
        # 전체 길이는 보존
        assert len(result[0]["content"]) == len('password = "admin1234"')

    def test_db_url_fully_masked(self) -> None:
        """CRITICAL: DB_URL의 자격증명이 노출되지 않아야 함."""
        content = "conn=postgres://user:superSecretPass@db.example.com:5432/app"
        msgs = [{"role": "user", "content": content}]
        result = _pre_mask_messages(msgs)
        assert "superSecretPass" not in result[0]["content"]
        assert "user:superSecret" not in result[0]["content"]

    def test_imsi_fully_masked(self) -> None:
        """CRITICAL: 통신 식별자(IMSI)도 strong mask."""
        msgs = [{"role": "user", "content": "imsi=450050123456789"}]
        result = _pre_mask_messages(msgs)
        assert "450050123456789" not in result[0]["content"]
        # 중간 숫자도 남으면 안 됨
        assert "450" not in result[0]["content"] or "50012" not in result[0]["content"]

    def test_overlapping_spans_both_masked(self) -> None:
        """CRITICAL: 겹치는 스팬에서 뒷순위 PII도 누락 없이 마스킹되어야 함.
        이전 버그: 'Kim Minju Lee' 순방향 'Kim Minju' + 역방향 'Minju Lee' 겹침
        """
        msgs = [{"role": "user", "content": "Kim Minju Lee"}]
        result = _pre_mask_messages(msgs)
        # Lee가 평문으로 남으면 안 됨
        assert "Lee" not in result[0]["content"]
        # Kim도 마스킹
        assert "Kim Minju" not in result[0]["content"]

    def test_aws_key_fully_masked(self) -> None:
        """Strong mask: AWS/GitHub/Google/Stripe/JWT."""
        msgs = [{"role": "user", "content": 'aws = "AKIAIOSFODNN7EXAMPLE"'}]
        result = _pre_mask_messages(msgs)
        assert "AKIA" not in result[0]["content"]
        assert "IOSFODNN" not in result[0]["content"]

    def test_email_fully_masked(self) -> None:
        """PII도 전체 마스킹 — 긴 값에 center mask는 사실상 노출(13자중 1자만 가림)."""
        msgs = [{"role": "user", "content": "contact cyber@skbroadband.com now"}]
        result = _pre_mask_messages(msgs)
        content = result[0]["content"]
        assert "cyber@skbroadband.com" not in content
        # 도메인 fragments도 노출되면 안 됨
        assert "skbroadband" not in content
        assert "cyber" not in content
        # 길이 보존
        assert len(content) == len("contact cyber@skbroadband.com now")

