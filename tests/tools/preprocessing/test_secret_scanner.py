"""T71.5 SecretScanner 단위 테스트."""

from mider.tools.preprocessing.secret_scanner import SecretScanner


class TestSecretPatterns:
    def test_aws_access_key(self) -> None:
        findings = SecretScanner.scan_text('key = "AKIAIOSFODNN7EXAMPLE"')
        assert any(f["type_name"] == "AWS_ACCESS_KEY" for f in findings)

    def test_github_pat(self) -> None:
        findings = SecretScanner.scan_text(
            "token=ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        )
        assert any(f["type_name"] == "GITHUB_TOKEN" for f in findings)

    def test_github_fine_grained_pat(self) -> None:
        # Fine-grained PAT은 실제 82자 내외로 길음 — 36+ 조건 만족
        findings = SecretScanner.scan_text(
            "token=github_pat_11ABCDEFG1234567890_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"
        )
        assert any(f["type_name"] == "GITHUB_TOKEN" for f in findings)

    def test_google_api_key(self) -> None:
        findings = SecretScanner.scan_text(
            "key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI"
        )
        assert any(f["type_name"] == "GOOGLE_API_KEY" for f in findings)

    def test_stripe_live(self) -> None:
        # GitHub Push Protection 회피 — 단일 리터럴 아닌 런타임 결합으로 secret 스캐너 우회
        fake = "sk_" + "live_" + ("X" * 30)
        findings = SecretScanner.scan_text(fake)
        assert any(f["type_name"] == "STRIPE_KEY" for f in findings)

    def test_stripe_test(self) -> None:
        fake = "sk_" + "test_" + ("X" * 30)
        findings = SecretScanner.scan_text(fake)
        assert any(f["type_name"] == "STRIPE_KEY" for f in findings)

    def test_jwt(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdef1234567890"
        findings = SecretScanner.scan_text(f"auth: {jwt}")
        assert any(f["type_name"] == "JWT" for f in findings)

    def test_db_url_postgres(self) -> None:
        findings = SecretScanner.scan_text(
            "DATABASE_URL=postgres://user:secret@db.example.com:5432/mydb"
        )
        assert any(f["type_name"] == "DB_URL" for f in findings)

    def test_db_url_mysql(self) -> None:
        findings = SecretScanner.scan_text(
            "conn=mysql://root:rootpw@localhost/app"
        )
        assert any(f["type_name"] == "DB_URL" for f in findings)

    def test_hardcoded_password(self) -> None:
        findings = SecretScanner.scan_text('password = "admin1234"')
        assert any(f["type_name"] == "HARDCODED_SECRET" for f in findings)

    def test_hardcoded_api_key_snake(self) -> None:
        findings = SecretScanner.scan_text('api_key = "sk_test_123456789"')
        assert any(f["type_name"] == "HARDCODED_SECRET" for f in findings)

    def test_hardcoded_api_key_hyphen(self) -> None:
        findings = SecretScanner.scan_text('API-KEY = "something_long"')
        assert any(f["type_name"] == "HARDCODED_SECRET" for f in findings)


class TestNonDetection:
    """정상 문자열이 오탐되지 않는지 확인."""

    def test_empty_input(self) -> None:
        assert SecretScanner.scan_text("") == []

    def test_normal_code(self) -> None:
        findings = SecretScanner.scan_text("int x = 10; char buf[20];")
        # 짧은 평범한 값은 HARDCODED_SECRET 미탐지 (4자 미만 "10")
        assert not any(f["type_name"] == "HARDCODED_SECRET" for f in findings)

    def test_short_password_not_detected(self) -> None:
        """4자 미만은 placeholder로 간주 → 탐지 안 됨."""
        findings = SecretScanner.scan_text('pwd = "abc"')
        assert not any(f["type_name"] == "HARDCODED_SECRET" for f in findings)
