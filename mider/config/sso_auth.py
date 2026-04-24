"""SSO 인증 모듈 — Chrome CDP 기반 브라우저 로그인으로 SSOSESSION/user_id 획득.

Chrome DevTools Protocol(CDP)을 사용하여 Selenium/ChromeDriver 없이 직접
브라우저를 제어한다. 크롬 버전 불일치 문제를 원천 차단한다.

브라우저 로그인 → Network.getAllCookies로 SSOSESSION 추출 →
Runtime.evaluate로 /api/v1/auth 호출하여 user_id 추출.
세션 파일 캐싱(1시간 TTL) 및 만료 시 자동 재로그인을 지원한다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import atexit

import httpx

logger = logging.getLogger(__name__)

# 모듈 레벨에서 Chrome 프로세스 추적 (atexit 정리용)
_active_chrome_proc: subprocess.Popen | None = None

# 세션 기본 TTL: 1시간
DEFAULT_SESSION_TTL = timedelta(hours=1)

# Chrome CDP 기본 포트
DEFAULT_CDP_PORT = 9222


def _kill_cdp_chrome(port: int) -> None:
    """CDP 포트에 연결된 기존 Chrome 프로세스를 종료한다."""
    try:
        resp = httpx.get(
            f"http://127.0.0.1:{port}/json/version",
            timeout=2,
            verify=False,
        )
        if resp.status_code == 200:
            logger.info("CDP 포트(%d)에 기존 Chrome 감지 → 종료 시도", port)
            # Browser.close CDP 명령으로 깔끔하게 종료
            try:
                ws_url = _get_cdp_ws_url(port, timeout=3)
                import websocket as ws_module
                ws = ws_module.create_connection(ws_url, timeout=5)
                _cdp_send(ws, "Browser.close")
                ws.close()
            except Exception:
                pass
            # 프로세스가 남아있을 수 있으므로 잠시 대기
            time.sleep(1)
    except Exception:
        pass  # 기존 Chrome 없음 — 정상


def _cleanup_chrome() -> None:
    """atexit 핸들러: 활성 Chrome 프로세스를 종료한다."""
    global _active_chrome_proc
    if _active_chrome_proc is not None:
        try:
            _active_chrome_proc.terminate()
            _active_chrome_proc.wait(timeout=5)
            logger.debug("atexit: Chrome 프로세스 종료")
        except Exception:
            try:
                _active_chrome_proc.kill()
            except Exception:
                pass
        _active_chrome_proc = None


atexit.register(_cleanup_chrome)

# Chrome 실행 파일 후보 경로
_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


class SSOCredentials(NamedTuple):
    """SSO 인증 결과."""

    sso_session: str
    user_id: str
    name: str


class SSOAuthError(Exception):
    """SSO 인증 실패."""


def _find_chrome() -> str:
    """설치된 Chrome 실행 파일 경로를 반환한다.

    탐색 순서:
    1. CHROME_PATH 환경변수
    2. 파일 시스템 후보 경로
    3. Windows 레지스트리

    Raises:
        FileNotFoundError: Chrome을 찾을 수 없을 때
    """
    # 환경변수 우선
    env_path = os.environ.get("CHROME_PATH", "")
    if env_path and os.path.isfile(env_path):
        logger.debug("Chrome (환경변수): %s", env_path)
        return env_path

    # 후보 경로 탐색
    for path in _CHROME_CANDIDATES:
        if os.path.isfile(path):
            logger.debug("Chrome 발견: %s", path)
            return path

    # Windows 레지스트리 탐색
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        )
        path, _ = winreg.QueryValueEx(key, "")
        winreg.CloseKey(key)
        if os.path.isfile(path):
            logger.debug("Chrome (레지스트리): %s", path)
            return path
    except Exception as e:
        logger.debug("레지스트리 탐색 실패: %s", e)

    raise FileNotFoundError(
        "Chrome 실행 파일을 찾을 수 없습니다.\n"
        "Chrome이 설치되어 있는지 확인하거나, CHROME_PATH 환경변수를 설정하세요."
    )


def _cdp_send(ws: object, method: str, params: dict | None = None) -> dict:
    """CDP 명령을 WebSocket으로 전송하고 응답을 반환한다.

    Args:
        ws: websocket.WebSocket 인스턴스
        method: CDP 메서드명 (예: "Network.getAllCookies")
        params: CDP 파라미터

    Returns:
        CDP 응답 dict

    Raises:
        TimeoutError: 응답을 100회 루프 내에 받지 못했을 때
    """
    cmd_id = int(str(uuid.uuid4().int)[:8])
    payload = {"id": cmd_id, "method": method, "params": params or {}}
    logger.debug("CDP → %s %s", method, params or "")
    ws.send(json.dumps(payload))  # type: ignore[attr-defined]

    for _ in range(100):
        raw = ws.recv()  # type: ignore[attr-defined]
        resp = json.loads(raw)
        if resp.get("id") == cmd_id:
            logger.debug("CDP ← %s: %s", method, str(resp)[:200])
            return resp

    raise TimeoutError(f"CDP 명령 응답 없음: {method}")


def _get_cdp_ws_url(port: int, timeout: int = 15) -> str:
    """CDP HTTP API에서 첫 번째 탭의 WebSocket URL을 가져온다.

    Args:
        port: CDP 포트
        timeout: 최대 대기 시간(초)

    Returns:
        WebSocket debugger URL

    Raises:
        ConnectionError: 타임아웃 내에 연결 실패
    """
    deadline = time.time() + timeout
    last_err: Exception | None = None

    while time.time() < deadline:
        try:
            resp = httpx.get(
                f"http://127.0.0.1:{port}/json",
                timeout=2,
                verify=False,
            )
            tabs = resp.json()
            # type=="page"인 탭을 우선 선택
            for tab in tabs:
                if tab.get("type") == "page" and "webSocketDebuggerUrl" in tab:
                    ws_url = tab["webSocketDebuggerUrl"]
                    logger.debug("CDP WS URL: %s", ws_url)
                    return ws_url
            # page 탭이 없으면 첫 번째 항목 사용
            if tabs and "webSocketDebuggerUrl" in tabs[0]:
                return tabs[0]["webSocketDebuggerUrl"]
        except Exception as e:
            last_err = e
        time.sleep(0.5)

    raise ConnectionError(
        f"CDP 포트({port})에 연결할 수 없습니다 (timeout={timeout}s). "
        f"마지막 오류: {last_err}"
    )


class SSOAuthenticator:
    """Chrome CDP 기반 SSO 인증 관리자.

    - Chrome을 CDP 모드로 실행하여 SSOSESSION 쿠키 + user_id 획득
    - 세션 파일 캐싱 (JSON, TTL 기반)
    - force_login으로 캐시 무시 가능
    - Selenium/ChromeDriver 불필요
    """

    def __init__(
        self,
        base_url: str,
        login_url: str | None = None,
        session_file: str | None = None,
        session_ttl: timedelta = DEFAULT_SESSION_TTL,
        cdp_port: int = DEFAULT_CDP_PORT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._login_url = login_url or f"{self._base_url}?page=agent_login"
        self._session_ttl = session_ttl
        self._cdp_port = cdp_port

        # 세션 파일 위치: 인자 > 실행파일 옆
        if session_file:
            self._session_file = Path(session_file)
        else:
            if getattr(sys, "frozen", False):
                base_dir = Path(sys.executable).parent
            else:
                base_dir = Path(__file__).resolve().parent.parent.parent
            self._session_file = base_dir / ".sso_session.json"

        # CDP 전용 Chrome 프로필 디렉토리 (메인 Chrome과 충돌 방지)
        self._chrome_profile_dir = str(self._session_file.parent / ".chrome_cdp_profile")

    def authenticate(self, force_login: bool = False) -> SSOCredentials:
        """SSO 인증을 수행하고 자격 증명을 반환한다.

        Args:
            force_login: True이면 캐시를 무시하고 브라우저 로그인

        Returns:
            SSOCredentials(sso_session, user_id, name)

        Raises:
            SSOAuthError: 인증 실패
            FileNotFoundError: Chrome을 찾을 수 없을 때
        """
        if not force_login:
            cached = self._load_session()
            if cached:
                return cached

        # force_login=True → Chrome 쿠키 잔존으로 인한 auto-login을 방지하기 위해
        # 프로필을 초기화하고 완전히 새로운 로그인 페이지에서 시작하도록 한다.
        self.clear_chrome_profile()

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
        """Chrome CDP로 SSOSESSION + user_id를 획득한다.

        1. Chrome을 --remote-debugging-port로 실행
        2. CDP WebSocket 연결
        3. 사용자 로그인 대기
        4. Network.getAllCookies로 SSOSESSION 추출
        5. Runtime.evaluate로 /api/v1/auth 호출하여 user_id 추출

        Raises:
            SSOAuthError: 쿠키/user_id 추출 실패
            FileNotFoundError: Chrome을 찾을 수 없을 때
        """
        try:
            import websocket as ws_module
        except ImportError:
            raise ImportError(
                "SSO 로그인에 websocket-client가 필요합니다.\n"
                "  pip install websocket-client\n"
                "또는 AICA_SSO_SESSION 환경변수를 직접 설정하세요."
            )

        global _active_chrome_proc

        chrome_exe = _find_chrome()

        # 이전 실행에서 남은 Chrome CDP 프로세스 정리
        _kill_cdp_chrome(self._cdp_port)

        chrome_args = [
            chrome_exe,
            f"--remote-debugging-port={self._cdp_port}",
            f"--user-data-dir={self._chrome_profile_dir}",
            "--remote-allow-origins=*",
            "--start-maximized",
            "--ignore-certificate-errors",
            "--no-first-run",
            "--no-default-browser-check",
            self._login_url,
        ]

        logger.info("Chrome CDP 모드로 실행 (포트: %d)", self._cdp_port)
        chrome_proc = subprocess.Popen(chrome_args)
        _active_chrome_proc = chrome_proc

        ws = None
        try:
            # CDP WebSocket 연결 (Chrome 시작까지 최대 15초 대기)
            ws_url = _get_cdp_ws_url(self._cdp_port, timeout=15)
            ws = ws_module.create_connection(ws_url, timeout=10)

            # 페이지 이벤트 활성화
            _cdp_send(ws, "Page.enable")

            print("\n" + "=" * 60)
            print("브라우저에서 [ID/PW 로그인] 및 [2차 인증(SMS)]을 수행해 주세요.")
            print("메인 화면이 나타나면 아래 Enter를 눌러주세요.")
            print("=" * 60)
            input("\n인증 완료 후 Enter 키를 누르세요...")

            # SSOSESSION 쿠키 추출
            sso_session = self._extract_sso_cookie(ws)

            # /api/v1/auth로 user_id 추출
            user_id, name = self._extract_user_info(ws)

            credentials = SSOCredentials(
                sso_session=sso_session,
                user_id=user_id,
                name=name,
            )

            self._save_session(credentials)
            logger.info("SSO 로그인 완료: user_id=%s", user_id)

            return credentials

        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass
            chrome_proc.terminate()
            chrome_proc.wait()
            _active_chrome_proc = None
            logger.debug("브라우저 종료")

    def _extract_sso_cookie(self, ws: object) -> str:
        """CDP Network.getAllCookies로 SSOSESSION 쿠키를 추출한다."""
        resp = _cdp_send(ws, "Network.getAllCookies")
        all_cookies = resp.get("result", {}).get("cookies", [])
        logger.debug("전체 쿠키 수: %d", len(all_cookies))

        for cookie in all_cookies:
            if cookie["name"] == "SSOSESSION":
                sso_session = cookie["value"]
                logger.info(
                    "SSOSESSION 추출 완료 (길이: %d자)", len(sso_session),
                )
                return sso_session

        raise SSOAuthError("SSOSESSION 쿠키를 찾을 수 없습니다.")

    def _extract_user_info(self, ws: object) -> tuple[str, str]:
        """user_id를 3단계 fallback으로 추출한다.

        1순위: 응답전문(API + HTML)에서 정규식 검색 (V 시작 제외)
        2순위: /api/v1/auth JSON 파싱 (기존 방식)
        3순위: 사용자 수동 입력
        """
        # /api/v1/auth 응답 + 페이지 HTML 수집 (1순위, 2순위 공용)
        auth_json = ""
        try:
            js_code = """
                (async () => {
                    const resp = await fetch("/api/v1/auth", {method: "POST"});
                    return await resp.text();
                })()
            """
            logger.debug("/api/v1/auth 호출 중...")
            t0 = time.time()
            eval_resp = _cdp_send(ws, "Runtime.evaluate", {
                "expression": js_code,
                "awaitPromise": True,
                "returnByValue": True,
            })
            auth_json = eval_resp.get("result", {}).get("result", {}).get("value", "")
            logger.debug("/api/v1/auth 응답 시간: %.2f초", time.time() - t0)
        except Exception as e:
            logger.debug("/api/v1/auth 호출 실패: %s", e)

        page_html = ""
        try:
            html_resp = _cdp_send(ws, "Runtime.evaluate", {
                "expression": "document.documentElement.outerHTML",
                "returnByValue": True,
            })
            page_html = html_resp.get("result", {}).get("result", {}).get("value", "")
        except Exception:
            pass

        # ── 1순위: 응답전문에서 user_id 정규식 검색 (V 시작 제외) ──
        logger.info("[user_id] 1순위: 응답전문에서 user_id 검색 (V 시작 제외)")
        combined_text = auth_json + "\n" + page_html
        matches = re.findall(
            r'(?i)["\']?(?:user_id|userid)["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            combined_text,
        )
        found_ids: list[str] = []
        for m in matches:
            uid = m.strip().upper()
            if uid and uid not in found_ids:
                found_ids.append(uid)

        for uid in found_ids:
            if not uid.startswith("V"):
                logger.info("[user_id] 1순위 성공: %s (응답전문 검색)", uid)
                return uid, ""

        logger.info("[user_id] 1순위 실패 → 2순위 시도")

        # ── 2순위: /api/v1/auth JSON 파싱 ──
        logger.info("[user_id] 2순위: /api/v1/auth JSON 파싱")
        if auth_json:
            try:
                auth_data = json.loads(auth_json)
                user_id = auth_data.get("user_id")
                name = auth_data.get("name", "")
                if user_id:
                    logger.info("[user_id] 2순위 성공: %s / name: %s", user_id, name)
                    return user_id, name
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug("2순위 실패 (파싱 오류): %s", e)

        logger.info("[user_id] 2순위 실패 → 3순위 (수동 입력)")

        # ── 3순위: 사용자 수동 입력 ──
        print("\n[user_id] 자동 추출에 모두 실패했습니다.")
        print("로그인할 때 입력한 아이디를 직접 입력해 주세요.")
        while True:
            manual_id = input("login_id 입력(UX000): ").strip().upper()
            if manual_id:
                logger.info("[user_id] 3순위 수동 입력: %s", manual_id)
                return manual_id, ""
            print("아이디를 입력해 주세요.")

    def invalidate_session(self) -> None:
        """캐시된 세션 파일을 삭제한다 (만료 시 호출)."""
        if self._session_file.exists():
            self._session_file.unlink()
            logger.info("세션 파일 삭제: %s", self._session_file)

    def clear_chrome_profile(self) -> None:
        """Chrome CDP 프로필 디렉토리를 삭제한다.

        이전 세션의 쿠키/저장된 자격증명을 완전히 제거하여
        Chrome이 새 로그인 페이지를 표시하도록 강제한다.
        사용자의 개인 Chrome 프로필과는 무관한 mider 전용 디렉토리
        (`.chrome_cdp_profile/`)만 삭제한다.
        """
        profile_path = Path(self._chrome_profile_dir)
        if not profile_path.exists():
            return
        try:
            import shutil
            shutil.rmtree(profile_path)
            logger.info("Chrome CDP 프로필 삭제: %s", profile_path)
        except Exception as e:
            logger.warning(
                "Chrome CDP 프로필 삭제 실패 (다음 실행에서 재시도됨): %s - %s",
                profile_path, e,
            )
