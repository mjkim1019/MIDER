# 작업 계획서

## 개요
Version 1.0.0 릴리스 정리 — 미사용 파일 제거, README 리라이트, 시스템 아키텍처 문서화, 버전 범프, 브랜치 정리, 릴리스 태그

---

## Task 목록

### T40: 미사용 파일 정리
- T40.1: 미사용 ProC 프롬프트 삭제 → `config/prompts/proc_analyzer_error_focused.txt`, `proc_analyzer_heuristic.txt`
- T40.2: 프롬프트 개수 테스트 수정 (15개 → 13개) → 관련 테스트 파일

### T41: README v1 리라이트
- T41.1: XML(.xml) 지원 언어 추가 → `README.md`
- T41.2: 모델명 gpt-4o → gpt-5 업데이트 → `README.md`
- T41.3: 아키텍처 섹션 추가 (Multi-Agent, Phase 흐름, Agent/Tool 목록) → `README.md`
- T41.4: 분석 전략 섹션 추가 (2-Pass, 스마트 그룹핑, 하이브리드) → `README.md`

### T43: 시스템 아키텍처 문서 (docs/architecture/)
- T43.1: `system_overview.md` — 전체 시스템 구조 (Multi-Agent, Phase 흐름, 데이터 파이프라인) → `docs/architecture/`
- T43.2: `js_analysis_pipeline.md` — JS 분석 파이프라인 (ESLint + 전체코드 단일호출) → `docs/architecture/`
- T43.3: `proc_analysis_pipeline.md` — ProC 분석 파이프라인 (스마트 그룹핑 + 전체코드) → `docs/architecture/`
- T43.4: `sql_analysis_pipeline.md` — SQL 분석 파이프라인 (sqlparse + Explain Plan + LLM) → `docs/architecture/`
- T43.5: `xml_analysis_pipeline.md` — XML 분석 파이프라인 (인라인 JS 위임 + 구조 검증) → `docs/architecture/`
- T43.6: 기존 `c_analysis_pipeline.md` 최신화 (모델명 등) → `docs/architecture/`

### T42: 버전 1.0.0 릴리스 (depends: T40, T41, T43)
- T42.1: 버전 범프 0.1.0 → 1.0.0 → `mider/__init__.py`, `pyproject.toml`
- T42.2: 머지된 로컬 브랜치 29개 삭제
- T42.3: 미머지 로컬 브랜치 9개 검토 및 정리
- T42.4: 원격 머지된 브랜치 정리
- T42.5: v1.0.0 Git 태그 + GitHub Release

---

## 설계 결정

| 결정 | 이유 |
|------|------|
| 미사용 프롬프트 삭제 | T33에서 proc_analyzer.txt로 통합 완료, 코드에서 참조 0건 |
| README 리라이트 | XML 지원 누락, 모델명 불일치, 아키텍처 개요 없음 |
| 아키텍처 문서 분리 | README는 Quick Start 중심, 상세 파이프라인은 docs/architecture/에서 관리 |
| 언어별 파이프라인 문서 | C 문서만 있고 JS/ProC/SQL/XML 없음 — v1 기준 전체 커버 필요 |
| 1.0.0 버전 | T1~T36 핵심 기능 완료, 773 테스트 통과, 통합 테스트 완료 |
| 브랜치 정리 | 38개 로컬 브랜치 누적 — main만 유지하여 클린 상태 |

## 의존성

| Task | 의존 | 비고 |
|------|------|------|
| T40 | 없음 | 파일 정리 |
| T41 | 없음 | 문서 |
| T43 | 없음 | 문서 |
| T42 | T40, T41, T43 | 릴리스 (정리 완료 후) |

---

## AICA API 전환

### 개요
Azure OpenAI/OpenAI API를 SKT AICA 사내 API로 전환한다.
AICA는 `http://aica.sktelecom.com:3000/api/agent/v1/chats`로 POST 호출하며, SSO 세션은 별도 작업에서 처리한다.

### Task 목록

#### T47: LLM Client AICA API 전환
- T47.1: `llm_client.py` — openai SDK 제거, httpx 기반 AICA API 클라이언트 구현 → `mider/config/llm_client.py`
- T47.2: 모델명 매핑 (gpt-5 → GPT5_2 등) + settings.yaml 업데이트 → `mider/config/settings.yaml`
- T47.3: 단위 테스트 수정 → `tests/test_config/test_llm_client.py`

#### T48: 환경 변수 및 CLI 업데이트 (depends: T47)
- T48.1: `main.py` — validate_api_key() AICA 방식으로 변경 → `mider/main.py`
- T48.2: `.env.example` — AICA 환경 변수로 변경 → `.env.example`
- T48.3: `settings.yaml` api 섹션 업데이트 → `mider/config/settings.yaml`

#### T49: CI/빌드/문서 업데이트 (depends: T48)
- T49.1: `build-windows-exe.yml` — secrets 이름 변경 → `.github/workflows/build-windows-exe.yml`
- T49.2: `build_exe.py` — 안내 메시지 업데이트 → `scripts/build_exe.py`
- T49.3: `USER_MANUAL.md` — API 관련 내용 업데이트 → `docs/USER_MANUAL.md`
- T49.4: 기존 테스트 호환성 확인 → `tests/`

### 설계 결정

| 결정 | 이유 |
|------|------|
| openai SDK → httpx 직접 호출 | AICA API는 OpenAI 호환 형식이 아닌 자체 API 형식 사용 |
| SSO 세션 미구현 | 별도 작업에서 처리 예정, SSOSESSION 쿠키 전달 인터페이스만 준비 |
| model_cd 매핑 테이블 | settings.yaml 모델명(gpt-5)을 AICA model_cd(GPT5_2)로 변환 필요 |
| AICA_API_KEY + AICA_ENDPOINT 환경 변수 | 기존 Azure/OpenAI 키 3종 → 단일 키로 단순화 |

### 의존성

| Task | 의존 | 비고 |
|------|------|------|
| T47 | 없음 | LLM Client 핵심 변경 |
| T48 | T47 | 환경 변수는 Client 변경 후 |
| T49 | T48 | CI/문서는 마지막 |