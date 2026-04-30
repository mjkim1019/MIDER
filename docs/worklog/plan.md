# 작업 계획서

## 개요

다섯 개의 독립 피처:

- **피처 F (신규): PII 전처리 강화** ← **최우선 (SOC 반복 테스트 블로커)**. AICA 재시도/Connection error 유발하는 PII를 로컬에서 선탐지·선마스킹
- **피처 A (재설계): LLM-first 룰 시스템** — 구 피처 A+C+D 통합. Scanner Navigator 강등 + Skills few-shot으로 오탐 55%→15~20% 개선
- **피처 G (신규): 이슈 품질 관리** — "진짜 오류만" 보고. issue_type 분류(error/suggestion/info) + Header 의존성 해결(OOB 근본 해결)
- **피처 E (신규): Reporter 속도 개선** — Phase 3 20초 → 3~5초 단축
- **피처 B: 구조분석 캐싱 + 누적 학습** — SQLite 기반 이전 분석 컨텍스트 재활용 (중장기)

**핵심 흐름**: **F (PII 안정화) → A+G (오탐/품질 개선) + E (속도)** 병렬 → B (중장기 누적 학습)

---

## 우선순위 요약

| 순위 | Task | 피처 | 의존성 | 예상 임팩트 | 병렬 가능? |
|------|------|------|--------|-------------|-----------|
| **P0(최우선)** | **T71** | **F** | 없음 | **AICA 필터 차단/재시도 근본 해결 — SOC 반복 테스트 블로커** | 독립 |
| P0 | T64 | A | — | 모든 Skill/룰 외부화 기반 | 독립 |
| P0 | T65 | A | T64 | **오탐 55%→15~20%** (최대 임팩트) | **T72와 동시 진행** |
| P0 | T72 | G | — | **"진짜 오류만" 보고. 개선사항 분리** (이슈 수 50~70% 감소 예상) | **T65와 동시 진행** |
| P0 | T70 | E | 없음 | **Reporter 20s→3~5s** (UX) | T72와 reporter 부분 통합 |
| P1 | T57 | A | T65 | YAML 스코프 확정 | — |
| P1 | T58 | A | T57 | Scanner 리팩토링 | — |
| P1 | T73 | G | T72 | **OOB 오탐 근본 해결 (Header 의존성 인터랙션)** | — |
| P2 | T66 | A | T65, T58 | 문서화 | — |
| P2 | T68 | A | T65, T58, T66 | Orphan 정리 | — |
| P2 | T59 | A | T58 | 배포 (서명 + dev_mode) | — |
| P3 | T60~T63 | B | — | 누적 학습 | 피처 A와 병렬 OK |

**착수 순서 권장**:
1. **T71** (단독 최우선)
2. **T64** 기반 + **T70** 병렬
3. **T65 + T72** 동시 진행 (Skills에 issue_type 통합)
4. T57 → T58 → **T73** (OOB 근본 해결)
5. T66 → T68 → T59 (정리·배포)
6. 피처 B는 별도 일정 병행

---

## 피처 간 충돌 / 조정 가이드

**하드 충돌 없음** (모두 보완 관계). 같은 파일/영역을 건드리는 Task 조정:

| Task 쌍 | 영역 | 조정 방법 |
|---------|------|-----------|
| **T65 + T72** | 5개 Analyzer 프롬프트 + 스키마 | 같이 진행. Skill frontmatter에 `issue_type: error\|suggestion` 필드 추가하여 통합 |
| **T70 + T72** | `reporter.py` | T72의 error/suggestion 분리가 T70 템플릿 구성 요소로 통합. `# 🔴 오류` / `# 🟡 개선사항` 섹션 분리 |
| **T65.3 ProFrame Skills + T73** | OOB 헤더 의존 | Skills = 1차(프레임워크별, LEN_* 등), T73 = 2차(general fallback, 사내 매크로·구조체) |
| **T58 + T68** | Dead code 제거 | T58.5는 패턴 상수만, T68은 전수 검증. T58 완료 후 T68 수행 |
| **T73.4 + T60** | 캐시 인프라 | v1은 JSON, T60 완료 후 SQLite 마이그레이션 고려 |
| **T64 + T73** | 외부 파일 경로 해석 | 패턴은 유사(env > exe옆 > cache)하나 대상 다름(rules/skills vs headers). T73은 T64 패턴 참조 + 독립 구현 |
| **T65.4 (Navigator 강등) + issue_merger** | `issue_merger.py` | Navigator 강등이 먼저 → merger 변경 → T72 issue_type 필드 전파. 순차 |

---

## 피처 F (신규, 최우선): PII 전처리 강화

### 배경 — 실측 기반

SOC 반입 전 반복 테스트 중 AICA 호출이 다음 패턴으로 차단/재시도 발생:

```
[14:56:11] WARNING AICA PII 검출 (choices): PASSPORT(NT0000074)
           INFO    PII 검출 → 마스킹 후 재시도 (1/2)
[14:56:45] WARNING AICA PII/콘텐츠 필터 차단: [Error: Connection error.]
[14:56:49] WARNING AICA PII 검출 (choices): EMAIL(cyber@skbroadband.com),
                    EMAIL(cyber@sktelecom.com), PHONE(16002000)
[14:57:16] WARNING AICA PII/콘텐츠 필터 차단: [Error: Connection error.]
```

### 근본 원인

현재 파이프라인:
1. Mider 로컬 `PIDScanner`: 주민/전화/카드/여권/운전면허/외국인등록 6종만
2. **원본 코드가 AICA로 전송됨** (로컬 선마스킹 없음)
3. AICA가 PII 검출 → error + detects → Mider가 마스킹 후 재시도
4. 2회 재시도 후에도 콘텐츠 필터 차단 → Connection error

**로컬 PIDScanner 결함 (실측)**:
- 여권번호 `[A-Z]\d{8}` (1자+8자) → `NT0000074` (2자+7자) **miss**
- 이메일 패턴 **없음** → `@skbroadband.com` **miss**
- 전화번호 `01[016789]|02|0[3-9]\d` → 대표번호 `1600-2000` **miss**

### 목표

- **로컬 선탐지 + 선마스킹 → AICA로 "깨끗한" 텍스트 전송**
- AICA PII 필터 차단/재시도 0건 (네트워크 이슈 제외)
- SOC 반복 테스트 시 예측 가능한 실행 시간

### 예상 효과

- AICA 재시도 횟수 0 → 분석 속도 안정화
- Connection error 감소 → 테스트 반복 효율 향상
- 통신사 식별자(IMSI/IMEI) 유출 위험 차단 (엔진 외 커버)
- Secret/API key 유출 차단 (PII 넘어선 보안)

### Task 목록

#### T71: PII 전처리 강화 — P0 (최우선)

- T71.1: **PIDScanner 패턴 확장** → `mider/tools/static_analysis/pid_scanner.py`
  - 여권번호: `\b[A-Z]\d{8}\b` → `\b[A-Z]{1,2}\d{7,8}\b` (2자+7자 케이스 커버)
  - 대표번호 (1XXX 비즈니스): 15XX/16XX/18XX 19개 프리픽스 + 4자리
    ```
    \b(?:1533|1544|1555|1566|1577|1588|1599|1600|1644|1661|1666|
        1670|1688|1800|1855|1877|1899)[-\s]?\d{4}\b
    ```
  - 이메일 (broad): `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b`
  - 안심번호 (0507): `\b0507[-\s]?\d{3,4}[-\s]?\d{4}\b`

- T71.2: **로컬 선마스킹 → AICA 전송 전** 파이프라인 구축
  - `LLMClient._chat_aica()` 호출 직전에 `PIDScanner` 실행
  - 탐지된 모든 `detect_str`를 `_mask_center()`로 마스킹
  - 구현 위치: `mider/config/llm_client.py` 또는 `mider/agents/base_agent.py`
  - AICA는 이미 마스킹된 텍스트 수신 → 재시도 없음

- T71.3: **AICA 에러 로그 분리**
  - 현재 "AICA PII/콘텐츠 필터 차단"으로 묶여 구분 불가
  - 분리: (a) PII 필터 (b) 콘텐츠 필터(PII 아닌 유해) (c) 네트워크/타임아웃
  - AICA 응답의 `status_code`, `reason`을 개별 로깅
  - 구현: `llm_client.py` `AICAError` 확장

- T71.4: **통신사 식별자 패턴 추가 (SKB 맥락)** → `pid_scanner.py`
  - IMSI: `\b450\d{12}\b` (한국 MCC 450 + 12자리)
  - IMEI: `\b\d{15}\b` + Luhn 체크섬 검증
  - ICCID: `\b89\d{17,19}\b` (89 프리픽스)
  - MSISDN: 기존 전화번호 패턴으로 커버됨

- T71.5: **Secret/API key 스캐너** → `mider/tools/preprocessing/secret_scanner.py` 신규
  - `detect-secrets` 패턴 포팅 (외부 의존성 추가 X, 정규식만)
  - 주요 패턴:
    - AWS Access Key: `\bAKIA[A-Z0-9]{16}\b`
    - GitHub PAT: `\b(?:ghp|github_pat)_[A-Za-z0-9_]{36,}\b`
    - JWT: `\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b`
    - Google API: `\bAIza[0-9A-Za-z_-]{35}\b`
    - Stripe: `\bsk_(?:live|test)_[0-9a-zA-Z]{24,}\b`
    - Hardcoded password: `(?i)\b(?:password|passwd|pwd|secret|api[_-]?key)\s*[:=]\s*["']([^"']{4,})["']`
    - DB URL: `(?i)\b(?:postgres|mysql|mongodb)://[^\s"']+`

- T71.6: **로마자 한글 이름 휴리스틱** → `pid_scanner.py`
  - 주요 성씨 50개 영문 표기 + 3~5자 영문 결합
    ```
    \b(?:Kim|Lee|Park|Choi|Jung|Jang|Cho|Kang|Lim|Han|Yoon|Jeon|Seo|
        Oh|Shin|Kwon|Hwang|Song|Ahn|Yu|Hong|Ko|Moon|Yang|Son|Bae|Baek|
        Jo|Hur|Nam|Ryu|No|Min|Seong|Sim|Yuk|Ha|Joo|Koo|Im|Na|Jin|
        Chae|Woo|Gil|Heo|Pyo|Yeo|Ma|Ryu)[\s._-][A-Z][a-z]{2,4}\b
    ```
  - 오탐 있으나 마스킹 쪽이 안전 (under-mask보다 over-mask 선호)

- T71.7: **단위 테스트 + 실측 검증**
  - 실측 케이스 (PASSPORT(NT0000074) / EMAIL(@skbroadband.com) / PHONE(1600-2000)) 재현 테스트 — 로컬에서 전부 탐지되어야 함
  - 통신사 식별자 샘플 테스트 (IMSI/IMEI/ICCID)
  - Secret 패턴 회귀 테스트
  - 실제 소스코드 1건 돌려서 AICA 재시도 0 확인

### 피처 F 독립성

- **의존성 없음** — 다른 피처와 무관하게 즉시 착수 가능
- **피처 A/E와 병렬 가능** — 작업 영역이 다름 (F: PII 전처리 / A: 룰 시스템 / E: Reporter)
- **피처 A보다 먼저 완료 권장** — F 완료 후 AICA 호출 안정화된 상태에서 A 테스트하는 게 디버깅 용이

---

## 피처 A (재설계): LLM-first 룰 시스템

### 배경 — 재설계 결정

초기 계획(피처 A/C/D 3개 병렬)을 논의한 결과 근본 문제 발견:

- **Heuristic Scanner가 "Detection(판단)"과 "Navigation(탐색)" 2개 역할을 섞음**
- Scanner 단독 이슈 보고 → 맥락 없는 FP 양산 (ordsb0100010t01.c 오탐률 55%)
- T67의 프롬프트 땜빵으로 누를 수는 있으나 **체질적 해결 아님**

**결론**: Scanner는 Navigator로 강등. 모든 이슈 판단을 **LLM + Skills(정답/오답 few-shot)**로 수행.

### 새 아키텍처

```
파일
  └→ Scanner(Navigator) ── "이 함수/라인 살펴봐라" 힌트만 제공
                              ↓
       LLM + Skills 예시(정답/오답 few-shot) ── 판단 ── 보고
       ※ Scanner findings 단독으로 이슈 보고 금지
```

- **YAML 룰** = navigation 키워드만 (severity/post_check 등 판단 필드 제거)
- **Skills (Markdown)** = 판단의 1차 소스. 정답/오답 예시 + 억제 규칙
- **ProFrame 오탐** = 별도 Task 아닌 Skill 4개(`PROFRAME_*.md`)로 표현

### 예상 효과

| 단계 | 예상 FP율 (ordsb0100010t01.c) |
|------|------------------------------|
| 현재 | 55% |
| 프롬프트만 강화 (구 T67) | 30-35% |
| 재설계 + ProFrame Skills 4개 | 15-20% |
| 운영 피드백 수개월 축적 | 10% 이하 가능 |

### Task 목록 (실행 순서)

#### T64: 외부 리소스 경로 레이어 (기반) — P0

- T64.1: `resource_path.py` 신설 → `mider/config/resource_path.py`
  - 3단계 우선순위: 환경변수(`MIDER_RULES_PATH` 등) > exe옆(`mider_rules/`, `mider_skills/`) > 번들 fallback
  - `get_rule_path(name)`, `get_prompt_path(name)`, `get_skill_path(pattern_id)` 제공
- T64.2: `prompt_loader.py` 리팩토링 — resource_path 사용
- T64.3: `rule_loader.py`(T57.2) / `skill_loader.py`(T65.2) 모두 resource_path 통합
- T64.4: `mider.spec` 업데이트 — `config/rules/`, `config/skills/` 번들 포함, 외부 overlay 주석
- T64.5: `scripts/export_default_resources.py` — exe에서 기본 리소스(룰/프롬프트/스킬) 내보내기
- T64.6: 단위 테스트 (bundled/external fallback, 환경변수 우선순위)

#### T65: Skill 포맷 + 로더 + Navigator 강등 (핵심) — P0

**오탐 개선의 핵심 Task. 기존 T67(ProFrame)을 여기로 흡수.**

- T65.1: Skill 파일 포맷 정의 → `mider/config/skills/_SCHEMA.md`
  ```markdown
  ---
  id: UNSAFE_FUNC
  language: c
  enabled: true
  severity: high
  framework: null  # optional (예: proframe)
  ---
  # 설명
  ## 정답 예시 (탐지해야 함)
  ## 오답 예시 (억제)
  ## 억제 규칙 (선택, post_check 자동 변환 대상)
  ```
- T65.2: `SkillLoader` 구현 → `mider/tools/static_analysis/skill_loader.py`
  - `load_skill(pattern_id)`, `load_all_skills(language)`, frontmatter 파싱
  - 로드 실패 시 graceful degradation (경고 로그, few-shot 없이 진행)
- T65.3: 초기 Skill 작성 (우선순위 순) → `mider/config/skills/`
  - C 기본 3개: `UNSAFE_FUNC.md`, `UNINIT_VAR.md`, `MEMSET_SIZE_MISMATCH.md`
  - **ProFrame 4개 (구 T67 흡수)**:
    - `PROFRAME_A000_INIT.md` — a000_init_proc 전역 초기화 보장
    - `PROFRAME_DBIO_RECCNT.md` — DBIO reccnt ≤ AS_* 보장
    - `PROFRAME_LEN_CONSTANT.md` — LEN_* 자동생성 = 필드크기 일치
    - `PROFRAME_CTX_ALLOCATED.md` — ctx malloc+memset 보장
  - JS/SQL 3개: `XSS_INNERHTML.md`, `EVAL_USAGE.md`, `SQL_INJECTION_RISK.md`
- T65.4: **Scanner Navigator 강등** (구조 변경)
  - `ScannerFinding` → `NavigationHint` 타입으로 이름/의미 변경
  - Scanner 단독 이슈 보고 경로 제거 — 모든 이슈는 LLM 판단 후 보고
  - 단, complex state 분석(JS loop_stack, ProC fopen/fclose 짝)은 Scanner 코드에 유지 (힌트 생성만)
- T65.5: 5개 Analyzer `_build_messages()`에 few-shot 자동 주입
  - NavigationHint에서 pattern_id 추출 → 관련 Skill 로드 → 프롬프트 말미 `## 룰 참고 예시`로 주입
  - 토큰 예산 관리: severity=high 우선, 해당 파일 언어 매칭, 초과 시 오답 예시부터 제외(정답 유지)
- T65.6: `_REMOVE_KEYWORDS` 보강 (안전망)
  - `c_analyzer.py`, `issue_merger.py`에 "null 검사", "null 안전", "입력 포인터" 등 누락 키워드 추가
- T65.7: 오답 예시 → post_check 자동 변환 (선택 기능, 간단 케이스만)
- T65.8: 단위 테스트 + 통합 테스트 + 실측 검증
  - ordsb0100010t01.c 재분석 → 오탐률 before/after 비교
  - 진짜 버그 3건(C-001, C-002, C-004)은 여전히 탐지되어야 함

#### T57: 룰 YAML 스키마 + 로더 (스코프 축소) — P1

**기존 계획 대비 대폭 축소**: severity/post_check 등 판단 필드 제거. Navigation 키워드 저장소로만 활용.

- T57.1: `Rule` 모델 정의 → `mider/models/rule.py`
  - 필드: id, description, type(regex|ast_pattern), pattern, language, scope(function|file)
  - **제거된 필드**: severity, post_check (Skills로 이동)
- T57.2: `RuleLoader` 구현 → `mider/config/rule_loader.py`
  - `load(language)` — 내장 + 외부 병합 로드 (resource_path 사용)
  - 병합 전략: 같은 id → 외부 우선, 새 id → 추가
- T57.3: 기본 룰 YAML 생성 → `mider/config/rules/`
  - 기존 Scanner/AstGrep 패턴을 YAML로 이관 (navigation용으로 단순화)
- T57.4: 단위 테스트

#### T58: Scanner 리팩토링 (하드코딩 → YAML 로드) — P1

- T58.1: `CHeuristicScanner` 리팩토링 — `_PATTERNS` 제거, `RuleLoader.load("c")` 사용
- T58.2: `JSHeuristicScanner` 리팩토링 — 패턴만 YAML, loop_stack 상태머신 코드 유지
- T58.3: `ProCHeuristicScanner` 리팩토링 — 패턴만 YAML, 루프/파일 추적 코드 유지
- T58.4: `AstGrepSearch` 리팩토링 — `LANGUAGE_PATTERNS` 제거
- T58.5: 기존 테스트 호환성 확인 + 신규 단위 테스트 + **dead pattern 상수 완전 제거 확인** (주석 처리 아닌 삭제, git grep으로 참조 0 검증)

#### T66: 문서화 — P2

- T66.1: 아키텍처 문서 → `docs/architecture/llm_first_rules.md`
- T66.2: `USER_MANUAL.md` 업데이트
  - "커스텀 Skill 작성하기" 섹션 (오탐 억제 가이드)
  - "커스텀 룰 추가하기" 섹션 (navigation 키워드)
  - `scripts/export_default_resources.py` / `MIDER_DEV_MODE` 사용법
- T66.3: 이슈 로그 → `docs/issue-log/010-llm-first-rule-redesign.md` (작성 완료)

#### T68: Orphan 파일/코드 정리 (신규) — P2

**피처 A 재설계로 발생할 dead code/테스트/문서를 체계적으로 제거**

- T68.1: dead import / 미사용 상수 전수 스캔
  - 도구: `vulture` (dead code 탐지), `ruff --select F401,F841` (미사용 import/변수)
  - 대상: mider/ 전체
- T68.2: 구 Scanner 테스트 fixture 정리
  - Scanner 단독 출력을 이슈로 검증하던 테스트 → NavigationHint 검증으로 변경 또는 제거
  - 구 `_PATTERNS` 등 상수를 직접 참조하던 유닛 테스트 제거
- T68.3: 프롬프트 템플릿 내 Skill 이관 섹션 삭제
  - `c_analyzer_error_focused.txt`, `c_analyzer_heuristic.txt`, `c_prescan_fewshot.txt`에서 Skills로 이관된 예시/규칙 섹션 제거
- T68.4: 문서 업데이트
  - `docs/TECH_SPEC.md`, `docs/DATA_SCHEMA.md`에서 구 Scanner 역할 설명 → Navigator로 갱신
  - 구 T67 ProFrame 관련 주석/TODO 제거
- T68.5: docs/worklog 내 완료 계획 아카이브
  - 필요 시 `docs/archive/` 디렉토리 생성 후 이동
- T68.6: git grep 기반 deprecated 심볼 참조 0 확인
  - `ScannerFinding`, 구 `_PATTERNS` 이름 등 grep → 0건 검증

#### T59: PyInstaller + 서명 + dev_mode (배포) — P2

**기존 계획에 서명 + dev_mode 추가** (SOC 반입 및 반복 테스트 대응)

- T59.1: `mider.spec` 데이터 파일 번들 (rules + skills + 공개키)
- T59.2: `settings.yaml` rules_dir / skills_dir 설정 추가
- T59.3: 서명 시스템 구현
  - Ed25519 공개키 exe 번들, 개인키는 운영자(당신)만 보유
  - Skill/Rule 파일 로드 시 서명 검증 (`*.sig` 동일 디렉토리)
  - 검증 실패 시: 번들 기본값 사용 + 경고 로그
- T59.4: `MIDER_DEV_MODE=1` 환경변수 — 서명 검증 skip (SOC 테스트용)
- T59.5: `scripts/sign_resources.py` — 개인키 서명 스크립트
- T59.6: 단위 테스트 (서명 검증, dev_mode 우회)

### 피처 A 의존성 그래프

```
T64 (외부 경로)
  └→ T65 (Skills + Navigator 강등) ← 오탐 개선 핵심
        └→ T57 (YAML 축소) ─→ T58 (Scanner) ─→ T66 (문서)
                                           └→ T68 (Orphan 정리) ─→ T59 (서명 + 배포)
```

---

## 피처 G (신규): 이슈 품질 관리 — "진짜 오류만"

### 배경

현재 Mider는 LLM에게 "버그 찾아줘"라고 하면 LLM이 "개선 가능한 모든 것"으로 해석 → LLM이 medium severity로 매긴 항목들이 개선 제안으로 오염되어 진짜 오류를 파묻음.

**원칙: medium severity 자체는 제거하지 않는다.**
Scanner 출처 medium에는 반드시 보고해야 할 보안/정합성 이슈가 포함됨:
- `pid_scanner`: 하드코딩된 비밀번호/주민번호/계좌번호/카드번호
- `secret_scanner`: API 키, 토큰 누출
- `embedded_sql_analyzer`: SQL 인젝션 패턴 (Pro*C `EXEC SQL`)
- `proc_cross_checker`: EXEC SQL 변수 미선언/타입 불일치
- `explain_plan_parser`: 풀스캔/카테시안 곱

노이즈의 본질은 severity 값이 아니라 **issue type 구분 부재**임. severity는 영향도(직교 축)로 계속 활용한다.

두 개의 독립 문제:

1. **Issue type 혼동**: 개선사항(strcpy→strlcpy 권장, 매직 넘버, 함수 길이)이 "오류"로 분류됨 — 주로 LLM 출처
2. **OOB 오탐**: LLM이 헤더 정보 없이 "아마 OOB 가능성"으로 안전빵 보고. 근본 원인은 LLM 억측이 아니라 **분석 컨텍스트의 정보 부족** — 헤더 파일 없으면 buffer size 상수/구조체 크기 판단 불가

### Task 목록

#### T72.0: 단기 우회 — LLM 출처 medium suggestion 필터 — P0 (즉시)

**T72 본 작업이 끝날 때까지 사용자 노출되는 LLM medium suggestion 폭주를 일단 막는다.**

- 대상: `source=="llm"` AND `severity=="medium"` 이슈 (scanner 출처는 그대로 보존)
- 위치: `agents/reporter.py` primary 출력 + 터미널 요약 (markdown_report_formatter도 동일)
- 구현 핵심: 한 줄 필터 (`if issue.source == "llm" and issue.severity == "medium": skip`)
- JSON 보고서에는 그대로 남김 (감사 추적/디버깅용)
- 회귀 방지: scanner 출처 medium(PID/SECRET/SQL/explain plan)이 모두 보존되는지 단위 테스트 추가
- **T72 본 작업 완료 시 이 임시 필터는 제거** — issue_type 필드로 대체 (TODO 주석 명시)

#### T72: 이슈 타입 분류 강화 — P0 (T65와 동시 진행)

**"진짜 오류"와 "개선사항"을 구조적으로 분리**

- T72.1: `AnalysisResult.Issue` 스키마에 `issue_type` 필드 추가
  - 값: `error` | `suggestion` | `info`
  - 기존 `severity`는 유지 (직교 축)
- T72.2: 5개 Analyzer 프롬프트에 분류 기준 + 예시 추가
  ```
  - error: 구체적 입력으로 런타임 실패/보안 취약/데이터 손실 재현 가능
  - suggestion: 동작은 정상, 개선 여지 (스타일, 모범 사례, 방어적 프로그래밍)
  - info: 참고용 (deprecated API, 구식 관용구)
  
  애매하면 suggestion. error는 확신 있는 것만.
  ```
- T72.3: **Skill frontmatter에 `issue_type` 필드 추가** (T65와 통합)
  - Skill마다 "이 패턴은 error" / "이 패턴은 suggestion" 명시
  - 예: `UNSAFE_FUNC.md` → `issue_type: error`, `STRCPY_STYLE.md` → `issue_type: suggestion`
- T72.4: Reporter 분리 출력 (T70과 통합)
  - Primary 섹션: `issue_type=error`만
  - Secondary 섹션: `issue_type=suggestion` (옵션)
  - `info`는 JSON에만, CLI 출력 안 함
- T72.5: CLI 옵션 `--include-suggestions` 추가 (기본 off)
- T72.6: `issue_merger.py` 수정 — `issue_type` 필드 보존·전파
- T72.7: 단위 테스트 + 실측 (보고 이슈 수 before/after 비교)

#### T73: Header 의존성 해결 + 인터랙션 — P1 (depends: T72)

**OOB 오탐의 근본 원인(헤더 정보 부재)을 기본 헤더 번들 + 사용자 인터랙션으로 해결**

- T73.0: **ProFrame 공통 헤더 구조 추출 + 번들** (신규, P1 최우선)
  - 대상 헤더 7종: `pfmcom.h`, `pfmutil.h`, `pfmerr.h`, `pfmcbuf.h`, `pfmdbio.h`, `pfmioframe.h`, `pfmlframe.h` (원본 총 2,351줄)
  - **원칙**: 원본 번들 금지 (기밀/토큰 낭비/noise). 구조만 추출하여 산출물 2종 생성
  - 산출물 2종:
    1. `mider/resources/headers/pfm.slim.h` — 주석/히스토리/구현 제거, 구조체/typedef/상수/프로토타입만. clang-tidy `-I`로 주입
    2. `mider/resources/headers/pfm_symbols.yaml` — LLM 프롬프트 주입용. 심볼별 (name, kind, value, type, source) 구조화
  - T73.0.1: `scripts/extract_pfm_symbols.py` — **libclang 기반** AST 추출 스크립트 (7종 헤더 batch include 파싱, 매크로 값 자동 평가)
  - T73.0.2: 산출물 생성 로직 (슬림 .h + YAML 동시 출력)
  - T73.0.3: 커버리지 검증 테스트 — 샘플 C 파일(ORDSB0100010T01 등)의 `pfm_*` 참조 심볼 ↔ 추출물 교집합/차집합 0 검증
  - T73.0.4: `.gitignore` negation — 원본 `pfm*.h` 제외 유지 + 산출물(`pfm.slim.h`, `pfm_symbols.yaml`)만 커밋 허용
  - T73.0.5: 버전 해시 주석 + CI 검증 — 슬림 .h 상단에 `// source: pfmcom.h@<sha256>` 기록, 원본 변경 시 CI 실패
  - T73.0.6: `include_resolver`가 pfm 심볼을 기본 해결함 → T73.3 인터랙션 트리거는 pfm 외 사내 커스텀 헤더만 발동

- T73.1: Include 파서 → `mider/tools/preprocessing/include_resolver.py` 신규
  - `#include "..."` / `#include <...>` 추출
  - 해결 가능 여부 체크 (기본 번들 pfm / 파일 제공됨 / 표준 경로 / 미해결)
- T73.2: 심볼 의존성 추적
  - 분석 대상 연산(버퍼 크기 상수, 구조체 정의, 함수 시그니처)이 미해결 헤더에 의존하는지 판정
  - 1차: LLM 1-shot 호출로 "이 코드가 참조하는 외부 심볼 목록" 추출
  - 2차: 추출된 심볼이 어느 헤더에서 오는지 매칭
- T73.3: 인터랙션 프롬프트 (CLI) — **트리거 조건 모두 만족 시에만**
  - (1) 미해결 include 존재 AND
  - (2) 분석 대상 연산(memcpy/strcpy/배열 접근/구조체 참조)에 그 헤더 심볼이 쓰임 AND
  - (3) 해당 연산이 OOB/타입/크기 관련 이슈 판단 대상
  
  ```
  ⚠️  'zordmb0100020.c' 분석에 다음 헤더가 필요합니다:
      - cust_dbio.h    (버퍼 크기 상수: CUST_NAME_LEN, CUST_ID_LEN 참조)
      - proframe.h     (구조체: DBIO_IN_TYPE, DBIO_OUT_TYPE 참조)
  
    [1] 헤더 파일 경로 입력
    [2] 이 분석 skip (OOB/크기 관련 이슈 보고 안 함)
    [3] 이번 세션 동안 전부 skip
  선택: _
  ```
- T73.4: 세션 캐시 → `.mider_cache/header_decisions.json` (T60 완료 후 SQLite 마이그레이션)
  - 같은 include 결정 재질문 안 함
  - 프로젝트 루트의 `.mider_headers.yaml`에 영구 저장 옵션
- T73.5: Analyzer LLM 프롬프트에 조건부 지시 추가
  - 헤더 미제공 시: "이 헤더에 정의된 심볼에 대해 **크기/OOB/구조체 레이아웃 이슈 보고 금지**. 다른 종류의 이슈(로직 오류)는 정상 분석"
- T73.6: CLI 모드 플래그
  - `--headers <path>`: 헤더 디렉토리 지정
  - `--assume-headers-missing`: 전체 skip (CI/배치용)
  - `--no-interactive`: 질문 건너뛰고 자동 skip
- T73.7: 단위 테스트 + 실측 검증
  - 헤더 제공 vs 미제공 시 OOB 이슈 탐지 정확도 비교
  - 세션 캐시 동작 확인

### 피처 G 의존성

```
T72 (issue_type 분류) ─→ T73 (Header 해결)
   ↕ T65 (Skills frontmatter)
   ↕ T70 (Reporter 분리 출력)
```

- T72는 T65와 **동시 진행** (프롬프트/스키마 공유)
- T73은 T72 완료 후 (issue_type 필드 전제)
- T73의 Skills 보완 역할: ProFrame LEN_* 같은 framework-specific 케이스는 T65 Skills, 일반 사내 매크로·구조체는 T73

### 예상 효과

- **T72**: 보고 이슈 수 50~70% 감소 (suggestion을 별도 분리)
- **T73**: OOB 오탐 추가 감소. 헤더 제공 시 정확한 OOB 탐지, 미제공 시 허위 보고 0
- **T72+T73 결합**: 사용자가 받는 "진짜 오류 목록"이 크게 정제됨

---

## 피처 B: 구조분석 캐싱 + 누적 학습

### 현재 문제

- Phase 1(ContextCollector)가 매번 처음부터 구조분석 수행
- Phase 2 LLM에게 이전 이슈 컨텍스트 없음
- 같은 파일 재분석해도 이전 결과 활용 불가

### 2단계 전략

```
1단계: SQLite 기반 구조 캐싱 (의존성 0, 폐쇄망 즉시 적용)
2단계: 벡터 DB 유사 검색 (선택적, T63)
```

### Task 목록

#### T60: StructureStore 인프라 (SQLite) — P3

- T60.1: 구조 캐싱 스키마 정의 → `mider/models/structure_store.py`
  - `FileStructure`: file_path, file_hash(SHA256), functions[], globals[], imports[], patterns[], indexed_at
  - `AnalysisSnapshot`: file_path, file_hash, timestamp, issues[], severity_counts, model_used
- T60.2: StructureStore 구현 → `mider/tools/utility/structure_store.py`
  - `store_structure()`, `store_analysis()`, `get_previous()`, `get_history()`
  - DB 경로: `base_dir/.mider_cache/structure.db`
- T60.3: 단위 테스트

#### T61: 파이프라인 연동 — P3

- T61.1: Phase 1 → StructureStore 자동 저장 (orchestrator)
- T61.2: Phase 2 → 이전 분석 컨텍스트 프롬프트 주입 (각 Analyzer)
  - `{previous_findings}` 섹션 추가
  - hash 동일 + 이전 분석 있으면 LLM skip 옵션 (`--force-reanalyze` 예외)
- T61.3: Phase 3 → 이력 비교 보고 (delta: new/resolved/unchanged)
- T61.4: 단위 테스트

#### T62: CLI + 관리 — P3

- T62.1: `mider cache status/clear/show` 명령
- T62.2: `settings.yaml` cache 섹션
- T62.3: `--no-cache` CLI 옵션
- T62.4: 단위 테스트

#### T63: 벡터 DB 확장 (2단계, 선택) — P3

- T63.1: ChromaDB + sentence-transformers 통합
- T63.2: 유사 코드 이슈 검색 → Phase 2 프롬프트 주입
- T63.3: AICA 임베딩 API 옵션
- T63.4: 단위 테스트

---

## 피처 E (신규): Reporter 속도 개선

### 현재 문제

- Phase 3 (Reporter)가 **약 20초** 소요
- 원인: LLM이 템플릿화 가능한 부분(이슈 테이블, severity 카운트, 배포 체크리스트)까지 생성
- 원인: 단일 LLM 호출로 Summary + RiskAssessment + Narrative 모두 처리

### 목표

- Reporter 실행 시간: **20초 → 3~5초** (75% 단축)
- 품질 저하 없이 성능 개선

### 개선 전략 (임팩트 순)

| 전략 | 예상 효과 | 품질 영향 |
|------|-----------|----------|
| (a) 템플릿 우선, LLM은 내러티브만 | -10~13초 | 없음 (규칙 기반 생성) |
| (b) Reporter 모델 gpt-5 → gpt-5-mini | -5~7초 | 최소 (요약 작업) |
| (c) 병렬 LLM 호출 (Summary/Risk/Narrative 분리) | -3~5초 | 없음 |
| (d) 출력 토큰 제한 (max paragraphs) | -1~2초 | 사소 |
| (e) LLM skip (low severity only 또는 0 issues) | 해당 케이스 -19초 | 없음 |

### Task 목록

#### T70: Reporter 속도 개선 — P0 (피처 A와 병렬)

- T70.1: Reporter 프로파일링 — 현재 20초의 병목 식별
  - LLM 호출 수, 호출별 시간, 출력 토큰 수 측정
  - 로그: ReasoningLogger에 Reporter 단계별 시간 기록
- T70.2: 템플릿 기반 결정적 섹션 분리
  - 이슈 테이블/severity 카운트/파일 그룹핑/배포 체크리스트 → 코드 조립
  - 기존 T17 DeploymentChecklist 로직 활용
  - `reporter.py`에 `_build_deterministic_sections()` 신설
- T70.3: LLM 호출 범위 축소 — "Executive Summary" 내러티브만
  - 프롬프트: "2~3 paragraphs, bullet points only" 명시
  - 출력 토큰 max 400 제한
- T70.4: Reporter 모델 다운그레이드 옵션
  - `settings.yaml` 에 reporter 전용 모델 설정 (기본 gpt-5-mini)
  - RiskAssessment는 gpt-5 유지 (품질 영향 큰 경우)
- T70.5: 병렬 LLM 호출
  - Executive Summary / RiskAssessment / 권고안을 `asyncio.gather`로 병렬
- T70.6: LLM skip 조건 추가
  - 이슈 0건 또는 low severity only → LLM 호출 skip, 템플릿만 출력
- T70.7: 단위 테스트 + 실측 (before/after 시간 측정 + 품질 회귀 확인)

### 피처 E는 독립

- 피처 A/B와 의존성 없음
- 언제든 착수 가능 — P0으로 T64/T65와 병렬 진행 권장 (빠른 가시적 성과)

---

## 전체 의존성 요약

```
피처 F (PII 전처리, 최우선, 독립)
 T71 ──────────────────────────────────────→ (다른 피처 모두의 안정화 기반)

피처 A (LLM-first 룰)                  피처 G (이슈 품질)         피처 E (Reporter)
 T64 ─→ T65 ─→ T57 ─→ T58 ─→ T66      T72 ─→ T73                T70
         ↕ 동시                 ↘             (depends T72)      ↕ 통합
       (T72와 coord)           T68 → T59                         (T72와 coord)
         ↕ 통합
        T70

피처 B (구조 캐싱, 중장기)
 T60 ─→ T61 ─→ T62 ─→ [T63 선택]
```

### 핵심 의존성 / 동시 진행 규칙

- **T71 먼저** — SOC 반복 테스트 안정화. 나머지 피처 테스트의 전제
- **T65 ⇔ T72 동시 진행** — 프롬프트/스키마 공유, Skill frontmatter에 `issue_type` 필드 포함
- **T70 ⇔ T72 reporter 통합** — 템플릿 섹션에 error/suggestion 분리 구조 포함
- **T65.3 Skills (framework-specific) ↔ T73 (general header)** — 역할 분담, 보완 관계
- **T73은 T72 다음** — `issue_type` 필드 전제
- **피처 B는 독립적** — 언제든 병행 가능 (T73.4 캐시 → T60 SQLite 마이그레이션 경로 존재)
- **구 피처 C(Skills) = T65로 흡수**
- **구 피처 D(ProFrame) = T65.3의 Skill 4개로 흡수**
- **T63 벡터 DB**는 선택 확장

---

## YAML 룰 포맷 (축소 스코프)

```yaml
# config/rules/c_rules.yaml
version: "1.0"
language: c

rules:
  - id: UNSAFE_FUNC
    description: "위험 함수 사용 위치 (Navigator: LLM에 전달)"
    type: regex
    pattern: '\b(strcpy|sprintf|strcat|gets|scanf|vsprintf)\s*\('
    scope: file
    # severity / post_check 제거됨 — 판단은 Skills(UNSAFE_FUNC.md)로 이동

  - id: MEMSET_PATTERN
    description: "memset 호출 위치"
    type: regex
    pattern: '\bmemset\s*\(\s*&?\s*(\w+)\s*,'
    scope: file
```

## Skill 포맷 (판단 1차 소스)

```markdown
---
id: UNSAFE_FUNC
language: c
enabled: true
severity: high
framework: null
---

# UNSAFE_FUNC — 경계 미검증 위험 함수 사용

## 정답 예시 (탐지해야 함)
```c
char dest[10];
strcpy(dest, user_input);  // ❌ 오버플로우
```

## 오답 예시 (억제)
```c
strcpy(buf, "OK");              // ✅ 리터럴 — 안전
SKB_SAFE_STRCPY(dst, src, len); // ✅ 사내 안전 매크로
```

## 억제 규칙 (선택)
- 두 번째 인자가 문자열 리터럴이면 제외
- 함수명이 `SKB_SAFE_*` 접두사면 제외
```

## StructureStore 저장 구조

```
.mider_cache/
└── structure.db (SQLite)
    ├── file_structures
    └── analysis_snapshots
```
