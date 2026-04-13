"""SSO 인증 모듈 — Selenium 기반 브라우저 로그인으로 SSOSESSION/user_id 획득.

브라우저 로그인 → SSOSESSION 쿠키 추출 → /api/v1/auth로 user_id 추출.
세션 파일 캐싱(1시간 TTL) 및 만료 시 자동 재로그인을 지원한다.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

# 세션 기본 TTL: 1시간
DEFAULT_SESSION_TTL = timedelta(hours=1)


class SSOCredentials(NamedTuple):
    """SSO 인증 결과."""

    sso_session: str
    user_id: str
    name: str


class SSOAuthError(Exception):
    """SSO 인증 실패."""


class SSOAuthenticator:
    """Selenium 기반 SSO 인증 관리자.

    - 브라우저 로그인으로 SSOSESSION 쿠키 + user_id 획득
    - 세션 파일 캐싱 (JSON, TTL 기반)
    - force_login으로 캐시 무시 가능

    selenium이 설치되지 않은 환경에서는 ImportError를 raise한다.
    """

    def __init__(
        self,
        base_url: str,
        login_url: str | None = None,
        driver_path: str | None = None,
        session_file: str | None = None,
        session_ttl: timedelta = DEFAULT_SESSION_TTL,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._login_url = login_url or f"{self._base_url}?page=agent_login"
        self._driver_path = driver_path or os.environ.get("CHROME_DRIVER_PATH", "")
        self._session_ttl = session_ttl

        # 세션 파일 위치: 환경변수 > 인자 > 실행파일 옆
        if session_file:
            self._session_file = Path(session_file)
        else:
            import sys
            if getattr(sys, "frozen", False):
                base_dir = Path(sys.executable).parent
            else:
                base_dir = Path(__file__).resolve().parent.parent.parent
            self._session_file = base_dir / ".sso_session.json"

    def authenticate(self, force_login: bool = False) -> SSOCredentials:
        """SSO 인증을 수행하고 자격 증명을 반환한다.

        Args:
            force_login: True이면 캐시를 무시하고 브라우저 로그인

        Returns:
            SSOCredentials(sso_session, user_id, name)

        Raises:
            SSOAuthError: 인증 실패
        """
        if not force_login:
            cached = self._load_session()
            if cached:
                return cached

        return self._browser_login()

    def _load_session(self) -> SSOCredentials | None:
        """캐시된 세션 파일을 로드한다. TTL 초과 시 None."""
        if not self._session_file.exists():
            logger.debug("세션 파일 없음: %s", self._session_file)
            return None

        try:
            data = json.loads(self._session_file.read_text(encoding="utf-8"))
            issued_at = datetime.fromisoformat(data["issued_at"])
            elapsed = datetime.now() - issued_at
            remaining = self._session_ttl - elapsed

            logger.debug(
                "세션 파일 발견: 발급 %s, 경과 %s, 잔여 %s",
                data["issued_at"], elapsed, remaining,
            )

            if elapsed < self._session_ttl:
                mins_left = int(remaining.total_seconds() // 60)
                logger.info(
                    "기존 SSO 세션 재사용 (잔여 %d분, user_id=%s)",
                    mins_left, data["user_id"],
                )
                return SSOCredentials(
                    sso_session=data["sso_session"],
                    user_id=data["user_id"],
                    name=data.get("name", ""),
                )

            logger.info("SSO 세션 만료 (TTL 초과) — 재로그인 필요")
            return None

        except (KeyError, ValueError, json.JSONDecodeError) as e:
            logger.warning("세션 파일 파싱 실패: %s", e)
            return None

    def _save_session(self, credentials: SSOCredentials) -> None:
        """세션을 파일에 저장한다."""
        data = {
            "issued_at": datetime.now().isoformat(),
            "sso_session": credentials.sso_session,
            "user_id": credentials.user_id,
            "name": credentials.name,
        }
        self._session_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            self._session_file.chmod(0o600)
        except OSError:
            pass  # Windows에서는 chmod 미지원
        logger.debug("세션 저장: %s", self._session_file)

    def _browser_login(self) -> SSOCredentials:
        """Selenium 브라우저 로그인으로 SSOSESSION + user_id를 획득한다.

        Raises:
            SSOAuthError: 쿠키/user_id 추출 실패
            ImportError: selenium 미설치
        """
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service
        except ImportError:
            raise ImportError(
                "SSO 로그인에 selenium이 필요합니다.\n"
                "  pip install selenium\n"
                "또는 AICA_SSO_SESSION 환경변수를 직접 설정하세요."
            )

        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--ignore-certificate-errors")

        service_kwargs: dict = {}
        if self._driver_path:
            service_kwargs["executable_path"] = self._driver_path

        service = Service(**service_kwargs)
        driver = webdriver.Chrome(service=service, options=options)

        try:
            logger.info("SSO 로그인 페이지 접속: %s", self._login_url)
            driver.get(self._login_url)

            print("\n" + "=" * 60)
            print("브라우저에서 [ID/PW 로그인] 및 [2차 인증(SMS)]을 수행해 주세요.")
            print("메인 화면이 나타나면 아래 Enter를 눌러주세요.")
            print("=" * 60)
            input("\n인증 완료 후 Enter 키를 누르세요...")

            # SSOSESSION 쿠키 추출
            sso_session = self._extract_sso_cookie(driver)

            # /api/v1/auth로 user_id 추출
            user_id, name = self._extract_user_info(driver)

            credentials = SSOCredentials(
                sso_session=sso_session,
                user_id=user_id,
                name=name,
            )

            self._save_session(credentials)
            logger.info("SSO 로그인 완료: user_id=%s", user_id)

            return credentials

        finally:
            driver.quit()
            logger.debug("브라우저 종료")

    def _extract_sso_cookie(self, driver: object) -> str:
        """브라우저에서 SSOSESSION 쿠키를 추출한다."""
        all_cookies = driver.get_cookies()  # type: ignore[attr-defined]
        logger.debug("전체 쿠키 수: %d", len(all_cookies))

        for cookie in all_cookies:
            if cookie["name"] == "SSOSESSION":
                sso_session = cookie["value"]
                logger.info(
                    "SSOSESSION 추출 완료 (길이: %d자)", len(sso_session),
                )
                return sso_session

        raise SSOAuthError("SSOSESSION 쿠키를 찾을 수 없습니다.")

    def _extract_user_info(self, driver: object) -> tuple[str, str]:
        """브라우저 내에서 /api/v1/auth를 호출하여 user_id를 추출한다."""
        logger.debug("/api/v1/auth 호출 중...")
        t0 = time.time()

        auth_json = driver.execute_async_script(  # type: ignore[attr-defined]
            """
            const callback = arguments[arguments.length - 1];
            fetch("/api/v1/auth", {method: "POST"})
                .then(resp => resp.text())
                .then(text => callback(text))
                .catch(err => callback(JSON.stringify({error: err.message})));
            """
        )
        elapsed = time.time() - t0
        logger.debug("/api/v1/auth 응답 시간: %.2f초", elapsed)

        try:
            auth_data = json.loads(auth_json)
        except (json.JSONDecodeError, TypeError) as e:
            raise SSOAuthError(f"/api/v1/auth 응답 파싱 실패: {e}")

        if "error" in auth_data:
            raise SSOAuthError(f"/api/v1/auth 호출 실패: {auth_data['error']}")

        user_id = auth_data.get("user_id")
        if not user_id:
            raise SSOAuthError(
                f"응답에서 user_id를 찾을 수 없습니다 (keys: {list(auth_data.keys())})"
            )

        name = auth_data.get("name", "")
        logger.info("user_id 추출: %s / name: %s", user_id, name)

        return user_id, name

    def invalidate_session(self) -> None:
        """캐시된 세션 파일을 삭제한다 (만료 시 호출)."""
        if self._session_file.exists():
            self._session_file.unlink()
            logger.info("세션 파일 삭제: %s", self._session_file)
