# 작업 계획서

## 개요
SSO 인증 연동 + 배포 환경 파일 탐색 개선 — Selenium SSO 로그인 자동화, AICA 응답 파싱 수정, workspace 하위 디렉토리 재귀 검색으로 파일명만 입력하면 자동 탐색.

---

## Task 목록

### T50: SSO 인증 모듈 구현
- T50.1: `mider/config/sso_auth.py` — SSOAuthenticator 클래스 구현 → `mider/config/sso_auth.py`
  - Selenium 브라우저 로그인 (ChromeDriver)
  - SSOSESSION 쿠키 추출
  - `/api/v1/auth` 호출로 user_id 추출
  - 세션 파일 캐싱 (JSON, 1시간 TTL)
  - 세션 로드/저장/만료 판단
  - force_login 옵션 (캐시 무시)
- T50.2: 단위 테스트 → `tests/test_config/test_sso_auth.py`

### T51: LLM Client AICA 응답 파싱 수정 + SSO 연동 (depends: T50)
- T51.1: `_chat_aica()` 응답 파싱 수정 → `mider/config/llm_client.py`
  - **BUG FIX**: `token.data` → `choices[0].message.content` (OpenAI 호환 형식)
  - payload에 `app_env: "prd"` 필드 추가
- T51.2: SSO user_id 연동 → `mider/config/llm_client.py`
  - SSO에서 추출한 실제 user_id를 payload에 전달
  - `--sso` 모드: SSOAuthenticator에서 sso_session + user_id 획득
  - 비-SSO 모드: 기존 `AICA_SSO_SESSION` + `AICA_USER_ID` 환경변수 유지 (하위 호환)
- T51.3: SSO 만료 감지 + 자동 재인증 → `mider/config/llm_client.py`
  - HTML 응답(SSO 리다이렉트) 감지 시 재로그인
  - 재인증 후 1회 자동 재시도
- T51.4: 단위 테스트 수정 → `tests/test_config/test_llm_client.py`

### T52: CLI 및 설정 업데이트 (depends: T51)
- T52.1: `main.py` — `--sso` CLI 옵션 추가 → `mider/main.py`
  - `validate_api_key()` SSO 모드 분기 추가
  - SSO 로그인 플로우 CLI 안내 메시지
- T52.2: `settings.yaml` — SSO 설정 섹션 추가 → `mider/config/settings.yaml`
  - chromedriver 경로, 세션 TTL, 로그인 URL 등
- T52.3: `requirements.txt` — selenium 의존성 추가 → `requirements.txt`
- T52.4: `.env.example` + `docs/USER_MANUAL.md` SSO 관련 업데이트

### T53: 파일 탐색 개선 — workspace 하위 재귀 검색 추가 (depends: 없음)
- T53.1: `resolve_input_files()` 수정 → `mider/main.py`
  - `base_dir` 하위 전체 재귀 검색(`rglob`) 추가
  - 동일 파일명 여러 개 발견 시 목록 표시 + 에러 처리
  - 검색 순서: 절대경로 → CWD 상대경로 → base_dir 하위 재귀 검색
- T53.2: `USER_MANUAL.md` 파일 입력 방법 안내 업데이트 → `docs/USER_MANUAL.md`
- T53.3: 단위 테스트 수정 → `tests/test_main.py`

---

## 설계 결정

| 결정 | 이유 |
|------|------|
| **응답 파싱 `choices[0].message.content`로 수정** | 실제 AICA 응답이 OpenAI 호환 형식임을 확인 — 현재 `token.data` 파싱은 잘못됨 |
| **SSO user_id를 payload에 전달** | AICA API가 `user_id` 필드를 요구 — SSO `/api/v1/auth`에서 추출한 실제 사번 사용 |
| **`app_env: "prd"` 추가** | 데모 스크립트에서 확인된 필수 payload 필드 — 현재 llm_client.py에 누락 |
| selenium은 optional dependency | selenium 미설치 환경에서도 기존 환경변수 방식으로 동작해야 함 |
| 세션 파일 위치: 프로젝트 루트 `.sso_session.json` | 사용자 접근 용이, `.gitignore`에 추가 |
| 1시간 TTL (데모 스크립트와 동일) | SSO 서버 토큰 만료 정책에 맞춤 |
| HTML 응답으로 세션 만료 감지 | AICA 서버가 만료 시 SSO 리다이렉트 HTML을 반환하는 동작 활용 |
| `--sso` CLI 플래그로 SSO 모드 활성화 | 기존 환경변수 방식과의 하위 호환 유지 |
| chromedriver 경로는 settings.yaml + 환경변수 override | OS별 경로 차이 대응 |
| **`base_dir` rglob 검색 추가 (input 폴더 제외)** | workspace 재귀 검색으로 복사 불필요 — input 폴더는 사용하지 않음 |
| **동일 파일명 다수 발견 시 에러** | 자동 선택하면 잘못된 파일 분석 위험 — 사용자에게 목록 보여주고 명시적 경로 입력 유도 |

## 의존성

| Task | 의존 | 비고 |
|------|------|------|
| T50 | 없음 | SSO 모듈 독립 구현 |
| T51 | T50 | 응답 파싱 수정 + SSO 연동 |
| T52 | T51 | CLI는 Client 연동 완료 후 |
| T53 | 없음 | 파일 탐색 개선 (SSO와 독립) |
