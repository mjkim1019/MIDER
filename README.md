# Mider

폐쇄망 소스코드 분석 CLI. JS/C/Pro\*C/SQL/XML 코드의 장애 유발 패턴을 사전 탐지합니다.

## 빠른 시작

### 1. 가상환경 생성 및 패키지 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어서 API 키를 입력합니다:

**Azure OpenAI (옵션 1)**
```env
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-12-01-preview
MIDER_MODEL=gpt-5
```

**OpenAI 직접 사용 (옵션 2)**
```env
MIDER_API_KEY=sk-your-key
MIDER_MODEL=gpt-5
```

### 3. 실행

```bash
# 가상환경 활성화 상태에서
mider -f path/to/file.c

# 여러 파일
mider -f file1.c file2.js query.sql

# 상세 로그
mider -f file.c -v

# 출력 디렉토리 지정
mider -f file.c -o ./reports

# SQL + Explain Plan
mider -f query.sql -e explain_plan.txt
```

### 4. 출력

결과는 `./output/` (또는 `-o`로 지정한 디렉토리)에 JSON으로 생성됩니다:

| 파일 | 내용 |
|------|------|
| `issue-list.json` | 발견된 이슈 목록 (severity, before/after) |
| `checklist.json` | 코드 리뷰 체크리스트 |
| `summary.json` | 심각도별 요약 + 배포 판정 |
| `deployment-checklist.json` | 배포 체크리스트 |

## 사용 방법

### 기본 사용 흐름

```
1. 분석할 파일 지정     →  mider -f <파일>
2. 자동 분석 실행       →  Phase 0~3 순차 실행
3. 결과 확인           →  터미널 출력 + JSON 파일
```

### 단일 파일 분석

```bash
# C 파일 분석
mider -f src/order_process.c

# Pro*C 파일 분석
mider -f src/batch_job.pc

# 상세 로그 확인 (각 Agent의 추론 과정 출력)
mider -f src/order_process.c -v
```

### 여러 파일 동시 분석

```bash
# 여러 파일을 한 번에 분석 (언어 자동 감지)
mider -f src/main.c src/util.c src/query.sql screen.xml handler.js

# glob 패턴 사용
mider -f src/*.pc
```

### SQL + Explain Plan 분석

```bash
# SQL 파일 + Explain Plan 텍스트 덤프를 함께 분석
mider -f query.sql -e explain_plan.txt
```

Explain Plan 파일은 Oracle의 `DBMS_XPLAN.DISPLAY` 출력 텍스트를 그대로 저장한 파일입니다.

### 결과 출력 디렉토리 지정

```bash
mider -f src/batch.pc -o ./reports/2026-03
```

### 출력 파일 해석

분석이 완료되면 `./output/` 디렉토리에 4개의 JSON 파일이 생성됩니다:

| 파일 | 내용 | 활용 |
|------|------|------|
| `*_issue-list.json` | 발견된 이슈 목록 | 각 이슈의 severity, 위치, Before/After 코드 확인 |
| `*_checklist.json` | 코드 리뷰 체크리스트 | 배포 전 확인 항목으로 활용 |
| `*_summary.json` | 심각도별 통계 + 배포 판정 | 배포 가능 여부 즉시 확인 |
| `*_deployment-checklist.json` | 배포 체크리스트 | 운영 배포 절차에 포함 |

> 파일명 접두사는 `{분석파일명}_{YYYYMMDDHHmm}_` 형식입니다.

### 터미널 출력 예시

```
Mider v1.0.0

[파일] 2개
  src/order_process.c      (C)
  src/batch_job.pc         (Pro*C)

[Phase 0] 파일 분류...        done (1.2s)
[Phase 1] 컨텍스트 수집...     done (2.1s)
[Phase 2] 코드 분석...        [2/2] batch_job.pc
[Phase 2] 코드 분석...        done (15.3s)
[Phase 3] 리포트 생성...       done (3.4s)

┌──────────────────────────────────────────────┐
│ [CRITICAL] C-001  미초기화 변수 svc_cnt 사용    │
│   src/order_process.c:2383                    │
│                                              │
│   - Before:                                  │
│     long svc_cnt;                            │
│   + After:                                   │
│     long svc_cnt = 0;                        │
│                                              │
│   svc_cnt가 초기화 없이 배열 인덱스로 사용됨...    │
└──────────────────────────────────────────────┘

─────────────────────────────────────────────
  CRITICAL  1    HIGH  3    MEDIUM  5    LOW  2
─────────────────────────────────────────────

배포 판정: 불가 (Critical 1건)
  차단 이슈: C-001

출력 디렉토리: ./output
```

### 종료 코드 활용

CI/CD 파이프라인에서 종료 코드로 배포를 제어할 수 있습니다:

```bash
mider -f src/*.c -o ./reports
if [ $? -eq 1 ]; then
  echo "Critical 이슈 발견 — 배포 차단"
  exit 1
fi
```

| 코드 | 의미 |
|------|------|
| 0 | 정상 완료, Critical 없음 |
| 1 | 정상 완료, Critical 있음 (배포 불가) |
| 2 | 파일 오류 |
| 3 | LLM API 오류 |

---

## 아키텍처

Mider는 **OrchestratorAgent + 8개 Sub-Agent**로 구성된 Multi-Agent 시스템입니다.

### Phase 흐름

```
Phase 0 (분류) → Phase 1 (컨텍스트) → Phase 2 (분석) → Phase 3 (리포트)
```

| Phase | 담당 Agent | 설명 |
|-------|-----------|------|
| Phase 0 | TaskClassifierAgent | 파일 분류, 실행 계획 수립 |
| Phase 1 | ContextCollectorAgent | import/include 추출, 의존성 매핑 |
| Phase 2 | Analyzer Agents | 언어별 정적분석 + LLM 심층분석 |
| Phase 3 | ReporterAgent | 리포트 생성 (이슈/체크리스트/요약/배포) |

### Agent 목록

| Agent | 역할 | 대상 언어 |
|-------|------|-----------|
| OrchestratorAgent | 워크플로우 제어, Sub-Agent 조율 | - |
| TaskClassifierAgent | 파일 분류, 실행 계획 수립 | 전체 |
| ContextCollectorAgent | import/include 추출, 의존성 매핑 | 전체 |
| JavaScriptAnalyzerAgent | JS 정적분석 + LLM 심층분석 | `.js` |
| CAnalyzerAgent | C 정적분석 + LLM 심층분석 | `.c`, `.h` |
| ProCAnalyzerAgent | Pro\*C 정적분석 + LLM 심층분석 | `.pc` |
| SQLAnalyzerAgent | SQL 패턴분석 + Explain Plan + LLM | `.sql` |
| XMLAnalyzerAgent | Proframe XML 구조분석 + JS 핸들러 검증 | `.xml` |
| ReporterAgent | 리포트 생성 (4개 JSON) | - |

> 상세 설계는 [docs/TECH_SPEC.md](docs/TECH_SPEC.md) 참조

## 분석 전략

### 정적분석 + LLM 하이브리드

모든 Analyzer Agent는 정적분석 도구(ESLint, clang-tidy, proc)를 먼저 실행하고, 그 결과를 LLM 컨텍스트에 포함하여 심층분석합니다. 정적분석 바이너리가 없으면 LLM 휴리스틱 분석만 수행합니다.

### 2-Pass 분석 (C 대형 파일)

C 파일에 대형 함수가 포함된 경우, Pass 1에서 함수 단위로 분할하여 개별 분석한 뒤 Pass 2에서 파일 전체 맥락과 병합합니다.

### 스마트 그룹핑 (Pro\*C 대형 파일)

Pro\*C 파일은 함수 단위로 청킹하여, 토큰 제한 내에서 유사 함수를 그룹으로 묶어 LLM에 전달합니다.

### 인라인 JS 위임 (XML)

XMLAnalyzerAgent는 Proframe XML 내 인라인 JavaScript 핸들러를 추출하여 JS 분석 로직에 위임합니다.

> 각 파이프라인 상세는 [docs/architecture/](docs/architecture/) 참조

## 탐지 가능한 에러

### JavaScript (.js)

| 카테고리 | 탐지 항목 | 도구 |
|---------|----------|------|
| code_quality | `var` 스코프 오류 (이중 루프 재사용, 클로저 캡처, 호이스팅) | LLM |
| memory_safety | `addEventListener` 미해제, `setInterval` 미정리, DOM 참조 누수 | LLM |
| security | `innerHTML` XSS, `eval()`, `document.write()` | LLM |
| null_safety | 중첩 객체 optional chaining 누락, API 응답 null 체크 | LLM |
| error_handling | 빈 catch 블록, Promise `.catch()` 누락, async try-catch 누락 | LLM |
| performance | 루프 내 DOM 접근, N+1 패턴 | LLM |
| (ESLint 규칙) | `.eslintrc.json`에 정의된 모든 규칙 | ESLint |

### C (.c, .h)

| 카테고리 | 탐지 항목 | 도구 |
|---------|----------|------|
| memory_safety | 버퍼 오버플로우 (`strcpy`, `sprintf`, 배열 인덱스 초과) | Scanner + LLM |
| memory_safety | 메모리 누수 (malloc 후 free 누락, 에러 경로 미해제) | LLM |
| memory_safety | Use-After-Free, Double-Free | LLM |
| null_safety | malloc/calloc 반환 NULL 미검증 | Scanner + LLM |
| null_safety | 함수 반환/매개변수 포인터 NULL 미검증 | LLM |
| (Scanner 6종) | UNINIT_VAR, UNSAFE_FUNC, BOUNDED_FUNC, MALLOC_NO_CHECK, BUFFER_INDEX, FORMAT_STRING | CHeuristicScanner |
| (clang-tidy) | bugprone-\*, clang-analyzer-\* 체크 | clang-tidy |

### Pro\*C (.pc)

| 카테고리 | 탐지 항목 | 도구 |
|---------|----------|------|
| data_integrity | EXEC SQL 후 `sqlca.sqlcode` 미검사 | SQLExtractor + LLM |
| data_integrity | INDICATOR 변수 누락 (NULL 가능 컬럼) | LLM |
| data_integrity | 커서 DECLARE/OPEN/FETCH/CLOSE 불완전 | 커서맵 + LLM |
| data_integrity | 트랜잭션 COMMIT/ROLLBACK 위치 부적절, 부분 커밋 | LLM |
| memory_safety | `%s`에 구조체 전달 → Core Dump | ProCScanner + LLM |
| memory_safety | memset/sizeof 타입 불일치 | ProCScanner + LLM |
| memory_safety | 루프 내 구조체 초기화 누락 → 이전 데이터 잔류 | ProCScanner + LLM |
| memory_safety | fopen/fclose 짝 불일치 → 파일 핸들 누수 | ProCScanner + LLM |
| memory_safety | 미초기화 변수, 버퍼 오버플로우 | CScanner + LLM |
| (proc 에러) | PCC 에러 코드 (문법 오류, 호스트 변수 오류) | proc |

### SQL (.sql)

| 카테고리 | 탐지 항목 | 도구 |
|---------|----------|------|
| performance | 인덱스 억제 — WHERE 절 함수/연산, 암시적 형변환 | 정적패턴 + LLM |
| performance | Full Table Scan | ExplainPlan + LLM |
| performance | Cartesian Product (JOIN 조건 누락) | ExplainPlan + LLM |
| performance | N+1 쿼리, 상관 서브쿼리, UNION vs UNION ALL | LLM |
| performance | SELECT \*, LIKE 선행 와일드카드, OR 조건 | 정적패턴 + LLM |
| data_integrity | UPDATE/DELETE WHERE 절 누락 | sqlparse |
| data_integrity | INSERT 컬럼 목록 미명시, 트랜잭션 범위 | LLM |
| (문법 검증) | 괄호 불일치, 따옴표 미닫힘, FROM 절 누락 | sqlparse |

### Proframe XML (.xml)

| 카테고리 | 탐지 항목 | 도구 |
|---------|----------|------|
| code_quality | 중복 컴포넌트 ID → `$w.getById()` 오작동 | XMLParser |
| error_handling | 이벤트 핸들러 함수가 JS 파일에 미정의 | JS 교차검증 |
| data_integrity | dataList 컬럼-컴포넌트 바인딩 불일치, dataType 불일치 | LLM |
| security | 사용자 입력 유효성 검사 누락, hidden 필드 민감 정보 | LLM |
| performance | 미사용 dataList, 과도한 이벤트 바인딩 | LLM |
| (인라인 JS) | `<script>` CDATA 내 JS 코드 → JS Analyzer 위임 분석 | ESLint + LLM |

## 개발

### 테스트 실행

```bash
source .venv/bin/activate
pip install -e .
pip install pytest pytest-asyncio

# 전체 테스트
pytest

# 특정 테스트
pytest tests/test_agents/test_sql_analyzer.py -v

# 상세 출력
pytest -v --tb=short
```

### 수동 테스트 (샘플 파일)

```bash
# C 파일 분석
mider -f tests/fixtures/sample_skb/ordsb0100010t01.c -v

# 모델 지정
mider -f tests/fixtures/sample_skb/ordsb0100010t01.c -m gpt-5-mini
```

### CLI 옵션

| 옵션 | 축약 | 설명 | 기본값 |
|------|------|------|--------|
| `--files` | `-f` | 분석할 파일 (필수, 복수 가능) | - |
| `--output` | `-o` | 출력 디렉토리 | `./output` |
| `--model` | `-m` | LLM 모델명 | `gpt-5` |
| `--explain-plan` | `-e` | Explain Plan 파일 (SQL용) | - |
| `--verbose` | `-v` | 상세 로그 | `false` |
| `--version` | | 버전 출력 | - |

## 지원 언어

| 확장자 | 언어 | 분석 도구 |
|--------|------|-----------|
| `.js` | JavaScript | ESLint + LLM |
| `.c`, `.h` | C | clang-tidy + LLM |
| `.pc` | Pro*C | Oracle proc + LLM |
| `.sql` | SQL | sqlparse + Explain Plan + LLM |
| `.xml` | Proframe XML | XMLParser + LLM |

## 폐쇄망 배포 준비

폐쇄망에서는 외부 도구 설치가 불가능하므로, **배포 전에 정적 분석 바이너리를 `mider/resources/binaries/`에 복사**해야 합니다.

바이너리가 없으면 해당 도구는 skip되고 LLM 휴리스틱 분석만 수행됩니다.

### 필요 바이너리

| 바이너리 | 대상 언어 | 설치 출처 |
|----------|-----------|-----------|
| `clang-tidy` | C (`.c`, `.h`) | LLVM (`brew install llvm` 또는 OS 패키지) |
| `node` + `eslint` | JavaScript (`.js`) | Node.js + `npm install eslint` |
| `proc` | Pro*C (`.pc`) | Oracle Pro*C Precompiler (Oracle Client) |

### 준비 방법

배포 패키지를 만드는 머신(인터넷 가능 환경)에서:

```bash
# 1. clang-tidy
brew install llvm  # macOS
# apt install clang-tidy  # Ubuntu
cp $(which clang-tidy) mider/resources/binaries/clang-tidy

# 2. node + eslint
cp $(which node) mider/resources/binaries/node
npm install eslint --prefix mider/resources/binaries/

# 3. Oracle proc (Oracle Client 설치 필요)
cp $(which proc) mider/resources/binaries/proc
```

> **주의**: 바이너리는 **대상 폐쇄망 서버의 OS/아키텍처와 동일한 환경**에서 복사해야 합니다.
> (예: 폐쇄망이 RHEL 8 x86_64이면 같은 OS에서 빌드/복사)

### ESLint 설정 파일

ESLint 룰셋은 `mider/resources/lint-configs/.eslintrc.json`에 미리 포함되어 있습니다.
프로젝트에 맞게 수정하려면 해당 파일을 편집하세요.
