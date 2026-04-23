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
