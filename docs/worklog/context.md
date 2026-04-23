# 맥락 노트

## 설계 결정
- Bottom-up 구현 순서: 기반 → 스키마 → 인프라 → Tool → Agent → CLI → 통합
- T4/T6/T7/T8 병렬 구현: 모두 T3(Base Infrastructure)만 의존하므로 독립적
- T11/T12 병렬 구현: Phase 2 Analyzer와 Phase 3 Reporter는 스키마가 확정되어 있으므로 병렬 가능
- LSP Tool (T7)은 1차 PoC에서 선택적 기능 — 바이너리 없을 시 graceful degradation
- ContextCollectorAgent는 Tool 기반 추출 + LLM 보정 하이브리드 방식 채택 (TaskClassifierAgent 패턴 동일)

## 토큰 최적화 설계 결정 (Structure + Function Window)
- **Error-Focused 경로**: `{file_content}` (파일 전체) → `{structure_summary}` + `{error_functions}` (에러 포함 함수 전체)
- **에러 포함 함수 전체 추출**: ±N줄이 아닌 함수 단위 — 함수는 논리적 완결 단위이므로 LLM이 더 정확하게 분석 가능
- **구조 요약**: Phase 1 file_context의 imports/calls/patterns + ast-grep 함수 시그니처 + 전역변수
- **Heuristic 경로**: 에러 위치를 모르므로 파일 크기 기반 분기 — ≤500줄 전체, >500줄 head(200)+tail(100)+구조요약
- **SQL 특화**: SQL은 함수가 아닌 SQL 문(SELECT/INSERT/UPDATE/DELETE) 단위로 추출
- **구현 위치**: 4개 Analyzer의 `_build_messages()` + 8개 프롬프트 템플릿 변수 변경

## T57~T59 설계 결정 (정적 분석 룰 외부화)
- **방식 선정: ②+③ 하이브리드**: regex 룰(YAML) + ast-grep 패턴(YAML) 통합. ① 하드코딩의 높은 변경 비용 해결
- **YAML 선택 이유**: JSON보다 가독성 좋음, 주석 지원, 멀티라인 regex 자연스러움
- **룰 로드 전략**: 내장(패키지 내 config/rules/) + 외부(base_dir/rules/) 2단계. 외부 우선 merge
- **Scanner 리팩토링 범위**: 패턴 정의만 YAML로 이동, 분석 로직(loop_stack, open/close 추적)은 코드 유지
  - CHeuristicScanner: `_PATTERNS` 6종 + `_MEMSET_SIZEOF_PATTERN` → YAML 이관, `_scan_patterns()` 로직 유지
  - JSHeuristicScanner: `_FOR_VAR_DECL` 등 4종 → YAML, `_scan_nested_var_loop()` 상태머신 유지
  - ProCHeuristicScanner: `_FORMAT_STRUCT_RE` 등 5종 → YAML, `execute()` 내 루프/파일 추적 유지
  - AstGrepSearch: `LANGUAGE_PATTERNS` dict → YAML, `execute()` 로직 유지
- **post_check 필드**: memset 타입 비교 같은 regex 이후 추가 검증은 코드에서 처리. YAML에는 "어떤 post_check를 쓸지" 이름만 지정
- **PyInstaller 번들**: `datas=[('mider/config/rules', 'mider/config/rules')]` 추가
- **사용자 커스텀 룰**: exe 옆 `rules/c_rules.yaml` 놓으면 내장 룰에 병합됨. 운영팀이 재빌드 없이 패턴 추가 가능
- **regex 컴파일 캐싱**: YAML 로드 시 `re.compile()` 1회 수행, Scanner 인스턴스 생명주기 동안 재사용

## T60~T62 설계 결정 (구조분석 캐싱 1단계: SQLite)
- **SQLite 선택 이유**: Python 내장(sqlite3), 외부 의존성 0, 폐쇄망 즉시 적용, 파일 기반 이동 용이
- **벡터 DB는 2단계**: ChromaDB + sentence-transformers는 ~200MB 의존성. 폐쇄망 배포 검증 필요 → T63에서 선택적 확장
- **저장 대상**: Phase 1 구조분석(FileContext) + Phase 2 분석결과(AnalysisResult). Phase 0 ExecutionPlan은 저장 불필요 (매번 재생성)
- **캐시 키**: file_path + file_hash(SHA256). hash 변경 = 코드 변경 → 이전 분석 결과는 "참고"로만 사용
- **hash 동일 + 이전 분석 존재 시**: 기본 동작은 LLM skip (캐시 활용). `--force-reanalyze`로 강제 재분석
- **LLM 프롬프트 주입 방식**: `{previous_findings}` 섹션을 프롬프트에 추가. 이전 이슈 목록을 "참고용"으로 전달
  - LLM에게: "이전 이슈가 해결되었는지 확인 + 새 이슈 탐지" 지시
  - 이전 이슈를 맹목적으로 복사하지 않도록 "코드에서 직접 확인" 강조
- **이력 비교 보고**: summary.json에 `delta: {new: 2, resolved: 1, unchanged: 3}` 추가
- **DB 경로**: `base_dir/.mider_cache/structure.db` — .gitignore에 추가
- **max_history**: 파일당 최근 10회 분석만 유지 (오래된 건 자동 삭제)

## T63 설계 고려사항 (벡터 DB 2단계)
- **임베딩 모델 선택지**:
  1. `sentence-transformers/all-MiniLM-L6-v2` (90MB, CPU로 충분, 폐쇄망 오프라인 가능)
  2. AICA 임베딩 API (있다면 — 확인 필요)
  3. TF-IDF + cosine similarity (임베딩 모델 없이, 코드 토큰 기반)
- **유사 코드 검색 활용 시나리오**:
  - "이 함수와 유사한 패턴의 다른 함수에서 어떤 이슈가 발견되었는가?"
  - "memset 패턴이 있는 모든 함수에서 공통적으로 발견된 이슈는?"
- **실용성 의문**: 같은 프로젝트 내에서 "유사 코드"가 충분히 많아야 의미 있음. 단일 실행 시 효과 제한적
- **권장**: T60~T62 (SQLite 캐싱)만 우선 구현, 실사용 데이터 축적 후 T63 필요성 재평가

## 참조 문서
- docs/TECH_SPEC.md: 2차 PoC 예정 RAG (섹션 4.2)
- docs/DATA_SCHEMA.md: AnalysisResult, FileContext 스키마
- mider/tools/static_analysis/c_heuristic_scanner.py: CHeuristicScanner `_PATTERNS` (리팩토링 대상)
- mider/tools/static_analysis/js_heuristic_scanner.py: JSHeuristicScanner 패턴 (리팩토링 대상)
- mider/tools/static_analysis/proc_heuristic_scanner.py: ProCHeuristicScanner 패턴 (리팩토링 대상)
- mider/tools/search/ast_grep_search.py: AstGrepSearch `LANGUAGE_PATTERNS` (리팩토링 대상)

## 주의사항
- Scanner 리팩토링 시 기존 테스트 전수 통과 필수 — 패턴 동작이 달라지면 안 됨
- YAML regex는 Python `re.compile()` 호환이어야 함 (PCRE 아님)
- JS Scanner의 상태 기반 분석(loop_stack, brace_depth)은 YAML로 표현 불가 → 코드 유지
- ProC Scanner의 루프 추적, fopen/fclose 짝 검사도 코드 유지
- StructureStore의 SQLite는 concurrent write 시 locking — 단일 프로세스이므로 문제 없음
- `.mider_cache/` 디렉토리를 .gitignore에 추가 필수

## T64~T66 설계 결정 (Skills 기반 정답/오답 예시 관리)

- **왜 Skills인가**: YAML은 "무엇을 탐지하는가"만 표현. "왜 이게 버그이고 왜 저건 아닌가"의 맥락은 LLM이 판단해야 함 → 정답/오답 예시를 few-shot으로 주입하는 게 가장 효과적
- **포맷 선정 — Markdown + frontmatter**: Claude Code `skills.md` 스타일 차용. 개발자/운영자가 읽기 쉽고, 코드 블록이 자연스럽게 포함됨. YAML-only는 멀티라인 코드 예시 불편.
- **파일 1개 = 패턴 1개**: `skills/UNSAFE_FUNC.md` 처럼 pattern_id 1:1. 탐색 O(1), git blame으로 이력 추적 용이.
- **LLM few-shot 주입 시점**: Analyzer의 `_build_messages()` 마지막에 `## 룰 참고 예시` 섹션으로 추가. 탐지된 pattern_id만 관련 skill 선택적 주입 (토큰 절약).
- **토큰 예산 초과 대응**: severity=high 우선 + 해당 파일 언어와 매칭되는 것만. 초과 시 "오답 예시"가 먼저 제외됨 (정답은 유지 — LLM에게 "탐지하라"는 지시가 더 중요).
- **오답 예시 → post_check 자동 변환**: skill 파일에 "억제 규칙" 섹션이 있으면 정규식/조건 추출 시도. 복잡한 로직은 명시적 Python 함수 작성 필요 (skill은 주로 문서화 역할).
- **피처 A/B와의 분리**: T64(외부 리소스 경로)는 T59(PyInstaller 번들)를 일반화한 레이어. 프롬프트/룰/스킬 모두 동일한 경로 해석 로직 사용 → 중복 제거.

## T64 경로 해석 우선순위 (3단계)

1. 환경변수 `MIDER_RULES_PATH`, `MIDER_PROMPTS_PATH`, `MIDER_SKILLS_PATH` (최우선 — CI/테스트 편의)
2. exe 옆 `mider_rules/`, `mider_prompts/`, `mider_rules/skills/` (운영팀이 드래그&드롭으로 배치)
3. 번들 내장 (PyInstaller `sys._MEIPASS`) — fallback, 항상 존재 보장

- **병합 전략**: 같은 pattern_id 발견 시 외부 우선(override), 새 id는 추가(extend). 번들 룰을 절대 덮어쓰지 않음(파일만 overlay).
- **exe 실행 시 경로 감지**: `sys.frozen` + `sys._MEIPASS` 확인. 개발 모드(소스 실행)에서는 `mider/config/rules/` 사용.

## 참조 문서 (T64~T66 추가)
- mider.spec: PyInstaller 번들 설정 — T64.4 수정 대상
- mider/config/prompt_loader.py: 기존 프롬프트 로더 — T64.2 리팩토링 대상 (resource_path 사용)
- mider/agents/*.py: 5개 Analyzer의 `_build_messages()` — T65.4 few-shot 주입 대상
- docs/USER_MANUAL.md: 커스텀 룰/Skill 추가 가이드 섹션 추가 — T66.2

## 주의사항 (T64~T66 추가)
- PyInstaller onefile 모드에서 `__file__` vs `sys._MEIPASS` 차이 주의 — resource_path.py에서 명확히 분기
- Skill 파일 로드 실패(포맷 오류, 파일 없음) → graceful degradation: 경고만 로그, few-shot 없이 분석 계속
- `python-frontmatter` 패키지 도입 고려 — 또는 수동 YAML 파싱(의존성 최소화 우선)
- Skill 주입 시 LLM 토큰 예산 관리 — 기존 `token_optimizer` 연동 필요
- 오답 예시의 post_check 자동 변환은 간단 케이스(리터럴/접두사 매칭)만 지원. 복잡한 AST 조건은 수동 코드 작성.

## 피처 A 재설계 (2026-04-23) — LLM-first 룰 시스템

### 핵심 인사이트

Scanner가 2개 역할을 섞고 있음:
- **(A) Detection**: "이건 버그다"라고 판단 — LLM이 더 잘함 (맥락 인식)
- **(B) Navigation**: "이 함수/라인 LLM에 보여줘야 한다" — 여전히 필요 (토큰 관리)

T67 오탐률 55% 문제의 근본 원인은 Scanner의 Detection 역할이 LLM 판단을 우회하거나 앵커링시킨 것. 프롬프트 땜빵(기존 T67)이 아닌 **구조적 해결** 필요.

### 재설계 핵심 변경

1. **Scanner → Navigator 강등**
   - `ScannerFinding` → `NavigationHint` 타입으로 명확히 구분
   - Scanner 단독 이슈 보고 경로 제거 — 모든 이슈는 LLM 판단 후 보고
   - complex state 분석(JS loop_stack, ProC fopen/fclose 짝)은 Scanner 코드에 유지하되 힌트 생성만

2. **판단 로직 → Skills Markdown few-shot**
   - 정답 예시(탐지) + 오답 예시(억제) + 억제 규칙(선택)
   - LLM은 프롬프트의 "~하지 마라" 문장보다 구체 코드 예시에 훨씬 강하게 반응

3. **YAML 스코프 축소**
   - severity/post_check 등 판단 필드 **제거** (Skills로 이동)
   - YAML은 navigation 키워드(어떤 함수/라인을 LLM에 보여줄지) 저장소로만

4. **ProFrame 오탐 = Skill 4개로 표현**
   - 구 T67 별도 Task 아닌 T65.3 Skill 작성으로 흡수
   - `PROFRAME_A000_INIT.md`, `PROFRAME_DBIO_RECCNT.md`, `PROFRAME_LEN_CONSTANT.md`, `PROFRAME_CTX_ALLOCATED.md`

5. **T59 서명 + dev_mode 추가**
   - Ed25519 서명: 개인키(운영자) + 공개키(exe 번들). Skill/Rule 변조 방지
   - 사용자는 읽을 수 있지만 수정 불가 → SOC 감사 통과 + 투명성
   - `MIDER_DEV_MODE=1`: 서명 검증 skip (테스트 단계 반복 수정용)

### 예상 효과

| 단계 | 예상 FP율 (ordsb0100010t01.c) |
|------|------------------------------|
| 현재 | 55% |
| 프롬프트만 강화 (구 T67) | 30-35% |
| 재설계 + ProFrame Skills 4개 | 15-20% |
| 운영 피드백 수개월 축적 | 10% 이하 가능 |

### 착수 순서

T64 (경로 레이어) → **T65 (Skills + Navigator 강등)** → T57 (YAML 축소) → T58 (Scanner 리팩토링) → T66 (문서) → T59 (서명 + 배포)

- T65가 오탐 개선 핵심 Task. ProFrame Skills 우선 작성으로 빠른 실측 검증 가능
- T57 이전에 T65가 먼저 가는 이유: Navigator 강등 후 YAML 스코프 확정되어야 의미 있음

### 주의사항 (재설계 관련)

- **Skill 커버리지 갭**: 예시가 없는 프레임워크/패턴은 FP 남음 → ProFrame은 초기 4개 필수
- **과잉 억제 위험**: 오답 예시를 너무 많이 넣으면 진짜 버그를 놓침 (반대 실패). 회귀 테스트로 감시 필요
- **LLM 비결정성**: 같은 코드도 실행마다 ±10% 편차. temperature 고정 + seed + few-shot으로 완화
- **토큰 예산**: Skill 주입 시 severity=high 우선 + 파일 언어 매칭. 초과 시 오답 예시부터 제외(정답 유지)
- **기존 T67 계획 폐기**: 프롬프트 하드코딩 대신 Skill 파일로 구현. 체질적 해결이 목적

## T68 설계 결정 (Orphan 파일/코드 정리)

- **필요성**: 피처 A 재설계가 만들 dead code를 체계적으로 제거하지 않으면 다음 개발자(또는 미래의 자신)가 혼란. 특히 Scanner의 구 역할 흔적이 코드/테스트/문서에 남음.
- **검증 도구**:
  - `vulture`: 미사용 함수/클래스/변수 탐지 (false positive 있으므로 수동 확인 필요)
  - `ruff --select F401,F841`: 미사용 import/로컬 변수 — CI에 통합 가능
  - `git grep <symbol>`: deprecated 심볼 참조 검증 (ScannerFinding, 구 _PATTERNS 이름 등)
- **제거 대상 예시**:
  - 코드: `_PATTERNS`, `_MEMSET_SIZEOF_PATTERN`, `_FOR_VAR_DECL`, `_LANGUAGE_PATTERNS` 등 구 상수
  - 타입: `ScannerFinding` (NavigationHint로 통합)
  - 프롬프트 섹션: Skills로 이관된 예시/규칙 (c_analyzer_*.txt 내)
  - 테스트: Scanner 단독 이슈 검증 테스트
- **원칙**: 주석 처리가 아닌 **완전 삭제**. 필요 시 `docs/archive/`에 이동 (구 계획 문서 등)
- **실행 시점**: 피처 A 주요 Task(T65, T58, T66) 완료 후. 너무 이른 정리는 롤백 비용↑

## T70 설계 결정 (Reporter 속도 개선)

- **현재 원인 분석**:
  - 단일 LLM 호출로 Summary + RiskAssessment + Narrative + 결정적 섹션까지 생성 → 긴 출력 토큰
  - LLM 레이턴시의 주범은 출력 토큰(autoregressive decode). 입력은 prefill로 상대적 저렴
  - Reporter 모델이 gpt-5 전체 사용 — 요약 작업엔 overkill
- **해결 전략 (임팩트 순)**:
  1. **템플릿 우선, LLM은 내러티브만**: 이슈 테이블/카운트/체크리스트는 코드로 조립. 기존 T17 DeploymentChecklist 로직 재사용 → **10~13초 절감**
  2. **모델 다운그레이드**: Reporter 전용으로 gpt-5-mini. RiskAssessment만 gpt-5 유지 옵션 → 5~7초
  3. **병렬 호출**: Executive Summary / RiskAssessment / 권고안 분리 후 asyncio.gather → 3~5초
  4. **출력 토큰 제한**: "max 2~3 paragraphs" 명시 → 1~2초
  5. **LLM skip**: 이슈 0건 또는 low severity only → 템플릿만 → 해당 케이스 -19초
- **목표**: 20초 → 3~5초 (75% 단축)
- **품질 회귀 방지**: 기존 Reporter 출력 샘플 vs 신 Reporter 출력을 수동 검토. JSON 필드 누락/형식 차이 없는지 확인
- **독립성**: 피처 A/B와 의존성 없음 → 언제든 착수. P0으로 T64/T65와 병렬 진행하면 빠른 가시적 성과 + 사용자 워크플로우 개선

## 참조 문서 (T68, T70 추가)
- mider/agents/reporter.py: T70 리팩토링 대상
- mider/tools/utility/deployment_checklist.py (T17): T70.2 템플릿 재사용
- mider/config/settings.yaml: T70.4 Reporter 전용 모델 설정 추가
- docs/issue-log/010-llm-first-rule-redesign.md: T68 배경 (재설계 문서)

## T71 설계 결정 (PII 전처리 강화, 피처 F)

### 실측 근거

AICA 로그에서 다음 케이스가 로컬 PIDScanner를 통과해 AICA에서 차단:

1. **PASSPORT(NT0000074)** — 2자 알파벳 + 7자 숫자
   - 현재 로컬 패턴: `\b[A-Z]\d{8}\b` (1자+8자) → miss
   - 수정: `\b[A-Z]{1,2}\d{7,8}\b`

2. **EMAIL(cyber@skbroadband.com / @sktelecom.com)** — SKB/SKT 도메인
   - 현재 로컬 패턴: 이메일 패턴 **아예 없음** → miss
   - 수정: broad 패턴 추가 (`[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`)

3. **PHONE(16002000)** — 1600-2000 대표번호 (대시 없이)
   - 현재 로컬 패턴: `01[016789]|02|0[3-9]\d` (01X/02/0X) → 1XXX miss
   - 수정: 15XX/16XX/18XX 19개 프리픽스 추가

### 핵심 설계

**로컬 선탐지 + 선마스킹 → AICA 깨끗한 텍스트 전송**
- 현재: 원본 → AICA → PII 검출 → 재시도 → 콘텐츠 필터 재차단 → Connection error
- 변경: 원본 → 로컬 PIDScanner → `_mask_center()` 마스킹 → AICA (재시도 없음)
- 구현 위치 후보: `llm_client.py` (`_chat_aica()` 진입 시) 또는 `base_agent.py` (LLM 호출 래퍼)

### 추가 커버리지 (SKB/보안 맥락)

1. **통신사 식별자**: Mider는 SKB 통신사 코드 분석 → IMSI/IMEI/ICCID가 테스트 데이터로 존재 가능성 높음
   - IMSI: 한국 MCC 450 (`\b450\d{12}\b`)
   - IMEI: 15자리 + Luhn 체크
   - ICCID: 89 프리픽스 (`\b89\d{17,19}\b`)

2. **Secret/API key**: PII 정의엔 없지만 유출 시 더 치명적
   - AWS/GitHub/JWT/Google/Stripe + Hardcoded password + DB URL
   - 별도 파일 `mider/tools/preprocessing/secret_scanner.py` 분리 (PII와 성격 구분)

3. **로마자 한글 이름**: KMA 형태소 분석기는 한글만 처리 → 영문 표기 이름(개발자 아이디, 커밋 author) miss
   - 주요 성씨 50개 + 3~5자 영문 결합 휴리스틱

### 설계 원칙

- **Over-mask 선호**: 마스킹이 과해 LLM 정확도가 조금 떨어지는 쪽 > PII가 AICA로 유출되는 쪽
- **오탐 허용**: 이메일 broad 패턴, 로마자 이름 휴리스틱은 오탐 있음 → 기능 분석에 영향 없는 선에서 허용
- **AICA 엔진 신뢰는 유지**: 로컬이 1차 방어, AICA가 2차. 로컬이 놓친 건 AICA가 잡음. 로컬 패턴이 과도하게 복잡해질 필요 없음
- **로그 투명성**: 어떤 PII가 로컬에서 마스킹됐는지 debug 로그로 추적 가능해야 함 (운영 중 오탐 디버깅)

### 주의사항

- **`_mask_center()` 길이 보존 필요**: AICA가 문맥 분석하는데 길이가 바뀌면 위치 정보 깨짐. 현재 함수는 길이 유지 (확인됨)
- **테스트 fixture 우회 불가**: `tests/fixtures/sample_skb/`의 실제 PII는 로컬 마스킹으로 보호됨. 커밋 금지 원칙은 그대로
- **Luhn 체크 false positive**: IMEI 15자리가 Luhn pass해도 실제 IMEI 아닐 수 있음 → 과도 탐지 허용 (over-mask 원칙)
- **regex 컴파일 비용**: 신규 패턴 추가 시 모듈 로드 타임에 `re.compile()` 1회. 실행 시 비용 없음

### 예상 효과

- AICA 재시도 횟수 0 (네트워크 이슈 제외) → 분석 속도 안정화
- SOC 반복 테스트 예측 가능성 ↑
- 통신사 식별자/Secret 유출 리스크 근본 차단

### 착수 우선순위

- **최우선 (P0 중에서도 최상위)** — SOC 반복 테스트의 블로커
- 피처 A/B/E와 병렬 가능하나, F 완료 후 AICA 안정화된 상태에서 다른 피처 테스트가 디버깅 용이
- 의존성 없음 → 즉시 착수 가능

## 참조 문서 (T71 추가)

- mider/tools/static_analysis/pid_scanner.py: T71.1/T71.4/T71.6 패턴 확장
- mider/config/llm_client.py: T71.2 선마스킹 통합 + T71.3 에러 로그 분리 (_mask_pii_in_messages, _mask_center 재사용)
- mider/tools/preprocessing/secret_scanner.py (신규): T71.5
- mider/agents/base_agent.py: T71.2 대안 구현 위치
- tests/tools/static_analysis/test_pid_scanner.py: T71.7 회귀 테스트 확장

## T72 설계 결정 (이슈 타입 분류 강화, 피처 G)

### 핵심 인사이트

현재 LLM에게 "버그 찾아줘"라고 하면 "개선 가능한 모든 것"으로 해석 → 진짜 오류가 개선 제안 더미 속에 파묻힘.

**구조적 해결**: 
- `issue_type` 필드를 스키마에 추가 → 메타데이터 레벨에서 강제 분리
- `severity`와는 **직교 축** (severity=심각도, issue_type=오류/개선 종류)
- 예: `{issue_type: error, severity: critical}` vs `{issue_type: suggestion, severity: low}`

### 분류 기준

| issue_type | 정의 | 예시 |
|-----------|------|------|
| `error` | 구체적 입력으로 런타임 실패/보안 취약/데이터 손실 재현 가능 | null deref, 확정 OOB, SQL injection, use-after-free |
| `suggestion` | 동작은 정상, 개선 여지 | strcpy→strlcpy, 매직 넘버, 함수 분리 |
| `info` | 참고용 | deprecated API, 구식 관용구 |

**원칙**: 애매하면 suggestion. error는 확신 있는 것만.

### Skills와의 통합 (T65 coordination)

Skill frontmatter에 `issue_type` 필드 추가:
```yaml
---
id: UNSAFE_FUNC
language: c
issue_type: error   # NEW
severity: high
---
```

Skill마다 "이 패턴은 error" / "이 패턴은 suggestion" 명시 → LLM이 Skill 참조 시 자동 분류.

### Reporter 통합 (T70 coordination)

T70의 템플릿 우선 Reporter에서 섹션 분리:
- `# 🔴 오류 (error)` — Primary 섹션
- `# 🟡 개선 제안 (suggestion)` — Secondary 섹션 (`--include-suggestions` 옵션)
- `info`는 JSON만

### 예상 효과

- 보고 이슈 수 50~70% 감소 (suggestion 분리)
- 사용자가 받는 "오류 목록"의 신호 대 잡음비 대폭 향상
- T71/T65 오탐 개선 효과와 누적되어 최종 FP율 10% 이하 가능

## T73 설계 결정 (Header 의존성 해결, 피처 G)

### 핵심 인사이트

OOB 오탐의 근본 원인은 **LLM 억측이 아니라 분석 컨텍스트의 정보 부족**:
- `char buf[CUST_NAME_LEN]` — CUST_NAME_LEN이 헤더에 정의됨
- 헤더 미제공 시 LLM은 크기 판단 불가 → 안전빵 "OOB 가능성" 보고
- 해결: **정보를 공급**하거나 **판단을 중지**시키거나

### 트리거 조건 (3 AND)

헤더 요청 인터랙션은 다음 모든 조건 만족 시에만 발생 (무분별한 질문 방지):

1. **미해결 include 존재**: `#include "xxx.h"` 있는데 파일 미제공
2. **분석 대상 연산에 그 헤더 심볼 사용**:
   - 버퍼 크기 상수 참조 (`char buf[LEN_X]`, `memcpy(dst, src, LEN_X)`)
   - 구조체 정의 참조 (`struct Foo`)
   - 함수 시그니처 참조 (DBIO API 등)
3. **해당 연산이 OOB/타입/크기 관련 이슈 판단 대상**

### 3가지 해결 경로

사용자 선택지:
1. **헤더 제공**: 경로 입력 → 파일 추가 포함 → 정확한 분석
2. **분석 skip**: OOB/크기 관련 이슈는 보고 안 함 (LLM 프롬프트에 조건부 지시)
3. **세션 전체 skip**: 이번 실행 동안 추가 질문 없이 모두 skip

### 캐싱 전략

- **세션 캐시**: `.mider_cache/header_decisions.json` — 같은 include 결정 재질문 안 함
- **영구 캐시**: 프로젝트 루트 `.mider_headers.yaml` (옵션) — 팀 공유 가능
- **T60 SQLite 완료 후**: JSON → SQLite 마이그레이션 (T60 인프라 재사용)

### Skills와의 역할 분담 (T65.3 coordination)

| 레이어 | 담당 | 구현 |
|-------|------|------|
| 1차 (framework-specific) | ProFrame LEN_*, DBIO reccnt 등 알려진 규칙 | T65.3 Skills (`PROFRAME_LEN_CONSTANT.md` 등) |
| 2차 (general fallback) | 사내 매크로, 구조체 등 일반 케이스 | T73 인터랙션 |

Skills가 1차 방어로 대부분 걸러내고, 걸러지지 않은 케이스만 T73이 사용자 입력으로 해결.

### CLI 모드별 동작

- **기본 (인터랙티브)**: 트리거 조건 만족 시 질문
- **`--headers <path>`**: 헤더 디렉토리 지정, 질문 없이 자동 해결
- **`--assume-headers-missing`**: 전체 skip (CI/배치)
- **`--no-interactive`**: 질문 건너뛰고 자동 skip

### 구현 복잡도

- T72 대비 인프라 부담 큼 (include 파서, 심볼 추적, 인터랙션, 캐시)
- T72 먼저 → 프롬프트 기반 OOB 억제로 70% 효과 → 남은 케이스에 T73 집중
- T73.2 심볼 추적은 LLM 1-shot으로 실용적 구현 가능 (정적 분석 풀 구현 대신)

### 주의사항

- **사용자 피로도**: 트리거 조건 엄격히 (3 AND). 세션 캐시로 반복 질문 방지
- **SOC 배치 실행**: `--assume-headers-missing` 또는 `--no-interactive`로 블로킹 회피
- **헤더 경로 해석**: T64 resource_path 패턴 참조하되 독립 구현 (결합도 낮게)
- **T72 의존**: `issue_type` 필드가 있어야 "헤더 미해결 시 OOB 이슈만 skip"을 정확히 필터링 가능

## 참조 문서 (T72, T73 추가)

- mider/models/analysis_result.py: T72.1 issue_type 필드 추가
- mider/agents/*.py (5개 Analyzer): T72.2 프롬프트 분류 기준
- mider/config/prompts/*.txt: T72.2 프롬프트 수정
- mider/config/skills/_SCHEMA.md: T72.3 Skill frontmatter 업데이트
- mider/agents/reporter.py: T72.4 분리 출력 (T70과 통합)
- mider/tools/issue_merger.py: T72.6 issue_type 보존
- mider/tools/preprocessing/include_resolver.py (신규): T73.1
- mider/main.py: T73.3 인터랙션 + T73.6 CLI 플래그
- .mider_cache/header_decisions.json (런타임 생성): T73.4

## 변경 이력
| 날짜 | 내용 | 이유 |
|------|------|------|
| 2026-04-23 | T57~T63 계획 수립 (룰 외부화 + 구조 캐싱) | 사용자 요청: 데이터 드리븐 정적 분석 + 구조분석 캐싱 누적 학습 |
| 2026-04-23 | T64~T66 신규 계획 (Skills 예시 + PyInstaller 외부 오버라이드) | 사용자 요청: 정답/오답 예시를 skills 형태로 관리, exe가 외부 프롬프트 런타임 읽기 |
| 2026-04-23 | T67 신규 계획 (ProFrame 오탐 억제) | 실측 분석: ordsb0100010t01.c 오탐률 55%. ProFrame 프레임워크 보장 규칙을 프롬프트/필터/fewshot에 반영 |
| 2026-04-23 | **피처 A 재설계**: A+C+D 통합 → LLM-first 룰 시스템. Scanner Navigator 강등, Skills를 판단 1차 소스로. T59에 서명+dev_mode 추가. 구 T67은 T65.3의 ProFrame Skill 4개로 흡수. 착수 순서 T64→T65→T57→T58→T66→T59로 재정렬 | Scanner의 Detection/Navigation 2개 역할 혼합이 오탐 55% 근본 원인. 프롬프트 땜빵 아닌 구조적 해결. SOC 반복 테스트 + 사용자 변조 방지 요구 |
| 2026-04-23 | **T68 (Orphan 정리) 신규 추가**: 피처 A 재설계로 발생할 dead code/테스트/문서 체계적 제거. vulture/ruff/git grep 도구 활용. 실행 시점은 T65/T58/T66 완료 후 | 재설계 과정에서 기존 Scanner 패턴 상수/타입/프롬프트 섹션이 orphan 될 것. 방치 시 코드 혼란 | 
| 2026-04-23 | **T70 (Reporter 속도 개선, 피처 E) 신규 추가**: Reporter 20초 → 3~5초. 템플릿 우선 + 모델 다운그레이드 + 병렬 호출 + 출력 토큰 제한. 피처 A와 병렬 P0 | Reporter LLM 호출에 템플릿화 가능한 결정적 섹션까지 맡기면서 지연. 사용자 체감 큰 UX 개선 |
| 2026-04-23 | **우선순위 재정렬**: P0 (T64/T65/T70) → P1 (T57/T58) → P2 (T66/T68/T59) → P3 (T60~T63). T70은 독립이라 T64/T65와 병렬 착수 권장 | FP 개선과 UX 속도 개선이 가장 큰 사용자 가치. 병렬 가능한 작업 구분 |
| 2026-04-23 | **T71 (PII 전처리 강화, 피처 F) 신규 추가 + 최우선 배치**: 실측 로그에서 PASSPORT(NT0000074)/EMAIL(@skbroadband.com)/PHONE(1600-2000) 3건이 로컬 miss → AICA 필터 재시도/Connection error 발생. 여권 1~2자 패턴, 대표번호 1XXX, 이메일 broad, 0507 안심번호, IMSI/IMEI/ICCID, Secret 스캐너, 로마자 이름 휴리스틱 추가. 로컬 선탐지+선마스킹으로 AICA에 깨끗한 텍스트 전송 | SOC 반복 테스트의 블로커. AICA 호출 안정화가 다른 피처 테스트의 전제. 의존성 없어 즉시 착수 가능 |
| 2026-04-23 | **피처 G (이슈 품질 관리) 신규 추가 — T72 + T73**: T72(이슈 타입 분류, P0, T65와 동시)는 issue_type 필드(error/suggestion/info)로 개선사항을 오류 분류에서 제거. T73(Header 의존성 해결, P1, depends T72)은 헤더 미제공 시 OOB 판단 불가 문제를 사용자 인터랙션으로 해결. 피처 간 조정 가이드 7개 항목 추가 (T65+T72, T70+T72, T65.3+T73, T58+T68, T73.4+T60, T64+T73, T65.4+issue_merger) | "오류 탐지 너무 많음 + 개선사항 섞임" + "OOB 오탐"의 사용자 피드백. 메타데이터 레벨에서 구조적 분리 + 헤더 정보 부재 근본 해결 |
| 2026-04-23 | **T71 완료** (모든 Subtask 통과): PIDScanner 6종 → 10종 확장 + IMSI/IMEI/ICCID + Luhn + 로마자 이름 순/역방향 + Secret 스캐너 8종 + 선마스킹 파이프라인 + AICA 에러 범주 분리(PII/CONTENT/SESSION/NETWORK/OTHER) + nested error 포맷 버그 수정 | SOC 반입 전 AICA 필터 차단 근본 해결. 실측 3건 (PASSPORT/EMAIL/PHONE 1600) 로컬 탐지 확인. 신규 테스트 58개 추가, 기존 테스트 전부 통과 |
