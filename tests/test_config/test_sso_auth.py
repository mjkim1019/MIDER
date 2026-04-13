"""SSOAuthenticator 단위 테스트."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mider.config.sso_auth import (
    DEFAULT_SESSION_TTL,
    SSOAuthenticator,
    SSOAuthError,
    SSOCredentials,
)


@pytest.fixture
def tmp_session_file(tmp_path: Path) -> Path:
    """임시 세션 파일 경로."""
    return tmp_path / ".sso_session.json"


@pytest.fixture
def authenticator(tmp_session_file: Path) -> SSOAuthenticator:
    """기본 SSOAuthenticator 인스턴스."""
    return SSOAuthenticator(
        base_url="http://test.example.com:3000",
        session_file=str(tmp_session_file),
    )


def _write_session(
    path: Path,
    sso_session: str = "test-sso-token",
    user_id: str = "testuser",
    name: str = "테스트유저",
    issued_at: datetime | None = None,
) -> None:
    """헬퍼: 세션 파일을 생성한다."""
    data = {
        "issued_at": (issued_at or datetime.now()).isoformat(),
        "sso_session": sso_session,
        "user_id": user_id,
        "name": name,
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ── 생성자 테스트 ──


class TestSSOAuthenticatorInit:
    """SSOAuthenticator 초기화 테스트."""

    def test_default_login_url(self, tmp_session_file: Path) -> None:
        auth = SSOAuthenticator(
            base_url="http://test.example.com:3000",
            session_file=str(tmp_session_file),
        )
        assert auth._login_url == "http://test.example.com:3000?page=agent_login"

    def test_custom_login_url(self, tmp_session_file: Path) -> None:
        auth = SSOAuthenticator(
            base_url="http://test.example.com:3000",
            login_url="http://custom.example.com/login",
            session_file=str(tmp_session_file),
        )
        assert auth._login_url == "http://custom.example.com/login"

    def test_base_url_trailing_slash_stripped(self, tmp_session_file: Path) -> None:
        auth = SSOAuthenticator(
            base_url="http://test.example.com:3000/",
            session_file=str(tmp_session_file),
        )
        assert auth._base_url == "http://test.example.com:3000"

    def test_driver_path_from_env(self, tmp_session_file: Path) -> None:
        with patch.dict("os.environ", {"CHROME_DRIVER_PATH": "/usr/bin/chromedriver"}):
            auth = SSOAuthenticator(
                base_url="http://test.example.com:3000",
                session_file=str(tmp_session_file),
            )
            assert auth._driver_path == "/usr/bin/chromedriver"

    def test_driver_path_explicit_overrides_env(self, tmp_session_file: Path) -> None:
        with patch.dict("os.environ", {"CHROME_DRIVER_PATH": "/env/path"}):
            auth = SSOAuthenticator(
                base_url="http://test.example.com:3000",
                driver_path="/explicit/path",
                session_file=str(tmp_session_file),
            )
            assert auth._driver_path == "/explicit/path"


# ── 세션 로드 테스트 ──


class TestLoadSession:
    """_load_session() 테스트."""

    def test_no_file_returns_none(
        self, authenticator: SSOAuthenticator,
    ) -> None:
        assert authenticator._load_session() is None

    def test_valid_session_returns_credentials(
        self, authenticator: SSOAuthenticator, tmp_session_file: Path,
    ) -> None:
        _write_session(tmp_session_file)
        result = authenticator._load_session()

        assert result is not None
        assert result.sso_session == "test-sso-token"
        assert result.user_id == "testuser"
        assert result.name == "테스트유저"

    def test_expired_session_returns_none(
        self, authenticator: SSOAuthenticator, tmp_session_file: Path,
    ) -> None:
        expired_time = datetime.now() - timedelta(hours=2)
        _write_session(tmp_session_file, issued_at=expired_time)

        assert authenticator._load_session() is None

    def test_almost_expired_session_still_valid(
        self, authenticator: SSOAuthenticator, tmp_session_file: Path,
    ) -> None:
        almost_expired = datetime.now() - timedelta(minutes=59)
        _write_session(tmp_session_file, issued_at=almost_expired)

        result = authenticator._load_session()
        assert result is not None
        assert result.user_id == "testuser"

    def test_corrupted_json_returns_none(
        self, authenticator: SSOAuthenticator, tmp_session_file: Path,
    ) -> None:
        tmp_session_file.write_text("not json", encoding="utf-8")
        assert authenticator._load_session() is None

    def test_missing_fields_returns_none(
        self, authenticator: SSOAuthenticator, tmp_session_file: Path,
    ) -> None:
        tmp_session_file.write_text('{"issued_at": "2026-01-01T00:00:00"}', encoding="utf-8")
        assert authenticator._load_session() is None


# ── 세션 저장 테스트 ──


class TestSaveSession:
    """_save_session() 테스트."""

    def test_save_creates_file(
        self, authenticator: SSOAuthenticator, tmp_session_file: Path,
    ) -> None:
        creds = SSOCredentials("sso-abc", "user123", "김테스트")
        authenticator._save_session(creds)

        assert tmp_session_file.exists()
        data = json.loads(tmp_session_file.read_text(encoding="utf-8"))
        assert data["sso_session"] == "sso-abc"
        assert data["user_id"] == "user123"
        assert data["name"] == "김테스트"
        assert "issued_at" in data

    def test_save_overwrites_existing(
        self, authenticator: SSOAuthenticator, tmp_session_file: Path,
    ) -> None:
        _write_session(tmp_session_file, user_id="old_user")
        authenticator._save_session(SSOCredentials("new-sso", "new_user", ""))

        data = json.loads(tmp_session_file.read_text(encoding="utf-8"))
        assert data["user_id"] == "new_user"


# ── authenticate 테스트 ──


class TestAuthenticate:
    """authenticate() 테스트."""

    def test_returns_cached_session(
        self, authenticator: SSOAuthenticator, tmp_session_file: Path,
    ) -> None:
        _write_session(tmp_session_file, user_id="cached_user")
        result = authenticator.authenticate()

        assert result.user_id == "cached_user"

    def test_force_login_ignores_cache(
        self, authenticator: SSOAuthenticator, tmp_session_file: Path,
    ) -> None:
        _write_session(tmp_session_file, user_id="cached_user")

        with patch.object(authenticator, "_browser_login") as mock_login:
            mock_login.return_value = SSOCredentials("new-sso", "new_user", "")
            result = authenticator.authenticate(force_login=True)

        assert result.user_id == "new_user"
        mock_login.assert_called_once()

    def test_calls_browser_login_when_no_cache(
        self, authenticator: SSOAuthenticator,
    ) -> None:
        with patch.object(authenticator, "_browser_login") as mock_login:
            mock_login.return_value = SSOCredentials("sso-tok", "user1", "")
            result = authenticator.authenticate()

        assert result.user_id == "user1"
        mock_login.assert_called_once()


# ── 쿠키 추출 테스트 ──


class TestExtractSSOCookie:
    """_extract_sso_cookie() 테스트."""

    def test_extracts_ssosession(self, authenticator: SSOAuthenticator) -> None:
        driver = MagicMock()
        driver.get_cookies.return_value = [
            {"name": "OTHER", "value": "abc"},
            {"name": "SSOSESSION", "value": "sso-token-123"},
        ]
        result = authenticator._extract_sso_cookie(driver)
        assert result == "sso-token-123"

    def test_raises_when_no_ssosession(self, authenticator: SSOAuthenticator) -> None:
        driver = MagicMock()
        driver.get_cookies.return_value = [
            {"name": "OTHER", "value": "abc"},
        ]
        with pytest.raises(SSOAuthError, match="SSOSESSION 쿠키를 찾을 수 없습니다"):
            authenticator._extract_sso_cookie(driver)


# ── user_info 추출 테스트 ──


class TestExtractUserInfo:
    """_extract_user_info() 테스트."""

    def test_extracts_user_id_and_name(self, authenticator: SSOAuthenticator) -> None:
        driver = MagicMock()
        driver.execute_async_script.return_value = json.dumps({
            "user_id": "a11401",
            "name": "김민주",
        })
        user_id, name = authenticator._extract_user_info(driver)
        assert user_id == "a11401"
        assert name == "김민주"

    def test_raises_when_no_user_id(self, authenticator: SSOAuthenticator) -> None:
        driver = MagicMock()
        driver.execute_async_script.return_value = json.dumps({"name": "테스트"})

        with pytest.raises(SSOAuthError, match="user_id를 찾을 수 없습니다"):
            authenticator._extract_user_info(driver)

    def test_raises_on_error_response(self, authenticator: SSOAuthenticator) -> None:
        driver = MagicMock()
        driver.execute_async_script.return_value = json.dumps({
            "error": "Network error",
        })
        with pytest.raises(SSOAuthError, match="호출 실패"):
            authenticator._extract_user_info(driver)

    def test_raises_on_invalid_json(self, authenticator: SSOAuthenticator) -> None:
        driver = MagicMock()
        driver.execute_async_script.return_value = "not json"

        with pytest.raises(SSOAuthError, match="파싱 실패"):
            authenticator._extract_user_info(driver)


# ── invalidate_session 테스트 ──


class TestInvalidateSession:
    """invalidate_session() 테스트."""

    def test_deletes_existing_file(
        self, authenticator: SSOAuthenticator, tmp_session_file: Path,
    ) -> None:
        _write_session(tmp_session_file)
        assert tmp_session_file.exists()

        authenticator.invalidate_session()
        assert not tmp_session_file.exists()

    def test_no_error_when_file_missing(
        self, authenticator: SSOAuthenticator,
    ) -> None:
        authenticator.invalidate_session()
