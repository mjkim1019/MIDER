# Pro*C 파일 분석 파이프라인

> Pro*C 파일이 입력되었을 때 Mider가 분석을 수행하는 전체 프로세스

---

## 전체 흐름

```
main.py (진입점)
  ↓
OrchestratorAgent.run()
  ├─ Phase 0: 파일 분류 (TaskClassifierAgent)
  ├─ Phase 1: 컨텍스트 수집 (ContextCollectorAgent)
  ├─ Phase 2: Pro*C 분석 (ProCAnalyzerAgent)
  └─ Phase 3: 리포트 생성 (ReporterAgent)
```

---

## Phase 0: 파일 분류

**담당**: `TaskClassifierAgent` (`mider/agents/task_classifier.py`)

1. 파일 확장자(`.pc`)로 언어 감지 → `language: "proc"`
2. `DependencyResolver`로 `#include` / `EXEC SQL INCLUDE` 의존성 분석
3. `TaskPlanner`로 실행 순서 결정 (위상 정렬)
4. LLM으로 우선순위 조정 (파일 2개 이상일 때)

**출력**: `ExecutionPlan` — sub_tasks 리스트 (task_id, file, language, priority, metadata)

**라우팅**: `OrchestratorAgent`의 `_LANGUAGE_AGENT_MAP`에서 `"proc"` → `ProCAnalyzerAgent`로 매핑

---

## Phase 1: 컨텍스트 수집

**담당**: `ContextCollectorAgent` (`mider/agents/context_collector.py`)

각 Pro*C 파일에 대해 아래 정보를 추출:

| 수집 항목 | 설명 |
|-----------|------|
| `#include` / `EXEC SQL INCLUDE` | 헤더 파일 및 Oracle 내장(sqlca, oraca, sqlda) 참조 관계 |
| 함수 호출 관계 | 어떤 함수가 어떤 함수를 호출하는지 |
| 코드 패턴 | error_handling (WHENEVER SQLERROR, sqlca.sqlcode), logging, transaction (COMMIT/ROLLBACK), memory_management |

**출력**: `FileContext` — 의존성, 함수 호출, 패턴 정보

---

## Phase 2: Pro*C 분석 (핵심)

**담당**: `ProCAnalyzerAgent` (`mider/agents/proc_analyzer.py`)

### 아키텍처 개요

```
공통 파이프라인
  ├─ proc 프리컴파일러 → 에러 목록
  ├─ SQLExtractor → EXEC SQL 블록 추출 (SQLCA 검사 여부 포함)
  ├─ ProCHeuristicScanner → 도메인 특화 패턴 4종
  ├─ CHeuristicScanner → 범용 C 위험 패턴 6종
  ├─ 글로벌 컨텍스트 추출 (호스트 변수, include, typedef, 전역변수)
  └─ 커서 라이프사이클 맵 (DECLARE/OPEN/FETCH/CLOSE 추적)
       ↓
  Pass 1: 위험 함수 태깅 (mini 모델)
       ↓
  토큰 기반 분기
  ├─ ≤100K tokens → 전체 코드 단일 LLM 호출
  └─ >100K tokens → 스마트 그룹핑 (계층형/디스패치형/유틸)
```

### 2-1. 정적 분석 실행 (공통 파이프라인)

모든 Pro*C 파일에 대해 4개의 정적 분석을 **항상** 실행한다:

#### proc 프리컴파일러

- **도구**: `ProcRunner` (`mider/tools/static_analysis/proc_runner.py`)
- **동작**: Oracle proc 바이너리 실행 → 에러 파싱 (PCC-S/W/E 코드)
- **출력**: 에러 리스트 (line, message, code)

#### SQL 블록 추출

- **도구**: `SQLExtractor` (`mider/tools/utility/sql_extractor.py`)
- **동작**: `EXEC SQL` 구문 파싱 → SQL 블록 추출
- **출력**: sql_blocks 리스트 (sql, line, function, has_sqlca_check)
- **SQLCA 미검사**: `EXEC SQL` 후 `sqlca.sqlcode` 검사 없는 블록을 표시

#### ProC Heuristic Scanner (도메인 특화 4종)

- **도구**: `ProCHeuristicScanner` (`mider/tools/static_analysis/proc_heuristic_scanner.py`)
- **4개 탐지 패턴**:

| 패턴 ID | 설명 | 예시 |
|---------|------|------|
| FORMAT_STRUCT | %s에 구조체 전달 (Core Dump) | `PFM_DSP("...%s...", var.x.y[0])` |
| MEMSET_SIZEOF_MISMATCH | memset 변수/sizeof 타입 불일치 | `memset(&u_in, 0, sizeof(s_in_t))` |
| LOOP_INIT_MISSING | 루프 내 구조체 초기화 누락 | while 루프에서 memset 없이 재사용 |
| FCLOSE_MISSING | fopen/fclose 짝 불일치 | fopen 후 모든 경로에서 fclose 미호출 |

#### C Heuristic Scanner (범용 6종)

- **도구**: `CHeuristicScanner` (`mider/tools/static_analysis/c_heuristic_scanner.py`)
- **6개 탐지 패턴**: UNINIT_VAR, UNSAFE_FUNC, BOUNDED_FUNC, MALLOC_NO_CHECK, BUFFER_INDEX, FORMAT_STRING

두 Scanner의 결과는 병합하여 하나의 `scanner_findings` 리스트로 관리한다.

### 2-2. 글로벌 컨텍스트 + 커서 맵

#### 글로벌 컨텍스트

`extract_proc_global_context()` (`mider/tools/utility/token_optimizer.py`)

함수 밖의 구조 정보를 추출:
- `#include` / `EXEC SQL INCLUDE` 목록
- `EXEC SQL BEGIN/END DECLARE SECTION` 블록 (호스트 변수)
- `typedef` / `struct` 정의
- 전역 변수 선언

#### 커서 라이프사이클 맵

`build_cursor_lifecycle_map()` (`mider/tools/utility/token_optimizer.py`)

모든 커서의 DECLARE/OPEN/FETCH/CLOSE 위치와 해당 함수명을 추적:

```
cur_order:
  DECLARE → b10_init (L142)
  OPEN    → b20_select (L256)
  FETCH   → b20_select (L268)
  CLOSE   → b20_select (L290)
```

미발견 이벤트는 `⚠ 미발견`으로 표시하여 LLM이 커서 누수를 탐지할 수 있게 한다.

### 2-3. Pass 1: 위험 함수 태깅

함수가 2개 이상일 때만 실행한다.

- **프롬프트**: `proc_prescan.txt`
- **모델**: mini (gpt-5-mini) — 빠른 필터링 용도
- **입력**: 전체 함수 시그니처 요약 + 함수별 패턴 요약 + 커서 맵
- **출력**: `risky_functions` 리스트 (function_name, reason)

`build_all_functions_summary()`로 전체 함수의 위치/줄 수를 제공하고,
`_build_function_findings_summary()`로 함수별 proc 에러, SQLCA 미검사, Scanner 탐지를 요약한다.

위험 함수 태깅 결과는 `risky_annotation` 문자열로 Pass 2에 전달된다:

```
- ⚠ b20_select: SQLCA 미검사 L256, Scanner [UNINIT_VAR] L260
- ⚠ c400_get_rcv: proc에러 L310, FCLOSE_MISSING L340
```

### 2-4. 코드 전달 분기 (토큰 기반)

```
파일 코드 크기 확인 (문자 수 / 3 = 추정 토큰)
│
├─ 추정 토큰 + 3000(오버헤드) ≤ 100K
│  └─ 단일 호출 경로
│
└─ 추정 토큰 + 3000(오버헤드) > 100K
   └─ 스마트 그룹핑 경로
```

#### 경로 A: 전체 코드 단일 호출

- 프롬프트: `proc_analyzer.txt`
- 모델: primary (gpt-5)
- 단일 LLM 호출 — 전체 코드 + 글로벌 컨텍스트 + 커서 맵 + 위험 태깅 + proc 에러 + SQL 블록 + Scanner 결과

#### 경로 B: 스마트 그룹핑

`classify_proc_functions()` (`mider/tools/utility/token_optimizer.py`)로 함수를 4가지 카테고리로 분류:

| 분류 | 규칙 | 처리 |
|------|------|------|
| boilerplate | main, *_init_proc, *_exit_proc, 모듈명 함수 | 분석 제외 |
| hierarchical_groups | 숫자 접두사 형제 (b10+b20+b30) | 그룹별 LLM 호출 |
| dispatch | 동일 접두사+번호 (work_proc1~11) | 개별 LLM 호출 |
| utility_groups | z/s/rep 접두사 그룹 | 그룹별 LLM 호출 |

그룹별로 병렬 LLM 호출 (최대 3개 동시, `asyncio.Semaphore`):

```
그룹 1: 계층(b10+b20+b30) ─┐
그룹 2: c400_get_rcv ───────┼─→ 병렬 LLM 호출 (sem=3)
그룹 3: 유틸(z01+z02+z03) ─┘       ↓
                              결과 병합 → issue_id 재번호
```

각 그룹 호출에는 동일한 글로벌 컨텍스트, 커서 맵, 위험 태깅, proc 에러, SQL 블록, Scanner 결과가 전달된다.

그룹핑 분류 실패 시 전체 코드 단일 호출로 fallback한다.

### 2-5. 프롬프트 구성

`_build_unified_prompt()` 메서드에서 단일/그룹 모두 동일한 프롬프트 템플릿을 사용한다:

| 변수 | 설명 |
|------|------|
| `global_context` | 글로벌 컨텍스트 (호스트 변수, include, typedef) |
| `cursor_lifecycle_map` | 커서 DECLARE/OPEN/FETCH/CLOSE 위치 맵 |
| `risky_functions_annotation` | Pass 1 위험 함수 태깅 결과 |
| `scanner_findings` | ProC Scanner 4종 + C Scanner 6종 결과 JSON |
| `proc_errors` | proc 프리컴파일러 에러 JSON |
| `sql_blocks` | EXEC SQL 블록 목록 JSON |
| `code` | 분석 대상 코드 (전체 또는 그룹별) |
| `file_path` | 파일 경로 |

### 2-6. 후처리

#### Issue ID 부여

- 단일 호출: LLM이 직접 부여
- 그룹핑: 모든 그룹 결과 병합 후 `PC-001`, `PC-002`, ... 순차 재번호

---

## Phase 3: 리포트 생성

**담당**: `ReporterAgent` (`mider/agents/reporter.py`)

모든 Pro*C 파일의 분석 결과를 통합하여 최종 리포트 생성:

| 출력 파일 | 내용 |
|-----------|------|
| issue_list | 전체 이슈 목록 (상세 정보 포함) |
| summary | 심각도별/카테고리별/언어별 통계 |
| checklist | 배포 전 확인 체크리스트 |
| deployment_checklist | 위험도 평가 |

---

## Issue 스키마

각 이슈는 아래 구조로 저장 (`mider/models/analysis_result.py`):

```
Issue
├─ issue_id: "PC-001"
├─ category: memory_safety | null_safety | data_integrity | error_handling | security | performance | code_quality
├─ severity: critical | high | medium | low
├─ title: "SQLCA 미검사 — INSERT 후 에러 처리 없음" (한국어)
├─ description: "b20_select 함수에서 EXEC SQL INSERT 실행 후..." (한국어)
├─ location: {file, line_start, line_end}
├─ fix: {before, after, description}
├─ source: static_analysis | llm | hybrid
├─ static_tool: "proc" | null
└─ static_rule: "PCC-S-02201" | null
```

---

## 프롬프트 파일

| 프롬프트 | 용도 | 사용 경로 |
|---------|------|-----------|
| `proc_prescan.txt` | Pass 1 위험 함수 태깅 | 함수 2개 이상일 때 |
| `proc_analyzer.txt` | 통합 심층 분석 | 단일 호출 / 그룹별 호출 |

---

## 관련 파일 경로

| 구성 요소 | 파일 경로 |
|-----------|-----------|
| 진입점 | `mider/main.py` |
| 오케스트레이터 | `mider/agents/orchestrator.py` |
| Pro*C 분석 에이전트 | `mider/agents/proc_analyzer.py` |
| proc 프리컴파일러 도구 | `mider/tools/static_analysis/proc_runner.py` |
| ProC 휴리스틱 스캐너 | `mider/tools/static_analysis/proc_heuristic_scanner.py` |
| C 휴리스틱 스캐너 | `mider/tools/static_analysis/c_heuristic_scanner.py` |
| SQL 블록 추출 | `mider/tools/utility/sql_extractor.py` |
| 토큰 최적화 유틸 | `mider/tools/utility/token_optimizer.py` |
| 분석 결과 모델 | `mider/models/analysis_result.py` |
| 실행 계획 모델 | `mider/models/execution_plan.py` |
| 프롬프트 (Pass 1) | `mider/config/prompts/proc_prescan.txt` |
| 프롬프트 (분석) | `mider/config/prompts/proc_analyzer.txt` |
