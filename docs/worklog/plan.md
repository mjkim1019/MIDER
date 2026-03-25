# 작업 계획서

## 개요
언어별 LLM 전달 전략 통합 개선:
- **T31**: CAnalyzer 모든 경로에 regex 히트 추가 + 전체 함수 시그니처 전달
- **T33**: ProC 함수별 청킹 — 전체 코드를 함수 단위로 분할하여 개별 LLM 호출
- **T36**: Agent 표준 로그 개선 — 언어별 동작 차이를 표준 로그에서 확인 가능하도록

---

## T31: CAnalyzer 통합 개선 (T22 흡수)

### 배경
- clang-tidy 있으면 regex 안 돌림 → 탐지 누락
- ≤500줄이면 regex 안 돌림 → 탐지 누락
- 2-Pass에서 regex 히트 함수만 Pass 1에 전달 → 미히트 함수 블라인드 스팟

### 변경 후 C 분석 흐름
```
모든 파일: CHeuristicScanner 실행 (regex 6종)
  ├ >500줄 → 2-Pass (clang 경고 + regex → 전체 함수 시그니처 → 선별 → 함수별 LLM)
  ├ ≤500줄 + clang 있음 → Error-Focused (clang 경고 + regex findings 병합)
  └ ≤500줄 + clang 없음 → Heuristic (전체 코드 + regex findings)
```

### Subtask

#### T31.1: build_all_functions_summary() 구현 → `mider/tools/utility/token_optimizer.py`
- 전체 함수의 시그니처 + 위치 + 줄 수 요약 생성
- 출력: `[L142-L268] int c400_get_rcv(...) — 127줄`

#### T31.2: CHeuristicScanner 항상 실행 → `mider/agents/c_analyzer.py`
- `run()` 시작부에서 항상 `_heuristic_scanner.execute()` 호출
- clang-tidy 유무, 파일 크기와 무관하게 regex 결과 확보

#### T31.3: Error-Focused 경로에 regex 결과 병합 → `mider/agents/c_analyzer.py` + 프롬프트
- clang-tidy 경고 + regex findings를 함께 LLM에 전달
- `c_analyzer_error_focused.txt`에 `{scanner_findings}` 변수 추가
- 중복 제거: 같은 라인(±2) + 같은 카테고리 → clang-tidy 우선

#### T31.4: Heuristic 경로(≤500줄)에 regex 결과 추가 → `mider/agents/c_analyzer.py` + 프롬프트
- 전체 코드 + regex findings를 함께 전달
- `c_analyzer_heuristic.txt`에 `{scanner_findings}` 변수 추가

#### T31.5: 2-Pass 프롬프트에 전체 함수 시그니처 전달 → `mider/config/prompts/c_prescan_fewshot.txt`
- `{all_functions_summary}` 변수 추가 (전체 함수 목록)
- regex 미히트 함수도 선별 가능한 few-shot 예시 추가

#### T31.6: 단위 테스트
- `build_all_functions_summary()` 출력 검증
- 모든 경로에서 scanner_findings 포함 확인
- regex 미히트 함수 선별 시나리오

---

## T33: ProC 함수별 청킹

### 배경

**현재 ProC 분석 흐름:**
```
proc 에러 있음 OR SQLCA 누락 OR Scanner 히트 → Error-Focused
  → structure_summary + error_functions (에러 함수만 추출)
  → 에러 없는 함수는 LLM이 못 봄

위 조건 모두 없음 → Heuristic
  → ≤500줄: 전체 코드
  → >500줄: head 200 + tail 100 (중간 코드 누락)
```

**문제:**
1. Error-Focused에서 에러 없는 함수의 로직 결함 누락
2. Heuristic >500줄에서 중간 코드 완전 누락
3. 함수 간 커서 라이프사이클 추적 불가 (DECLARE → OPEN → FETCH → CLOSE가 다른 함수에 분산)

### 변경 후 흐름
```
모든 파일 (줄 수/에러 무관):
  Step 1: proc 프리컴파일러 실행 (에러 수집)
  Step 2: SQL 블록 추출 + 함수 매핑
  Step 3: Heuristic Scanner 실행 (패턴 4종)
  Step 4: 글로벌 컨텍스트 추출 (DECLARE SECTION, 전역 변수, 구조체)
  Step 5: 함수별 개별 LLM 호출
    각 함수에 전달:
    ├─ 글로벌 컨텍스트 (~50줄)
    ├─ 구조 요약 (전체 함수 시그니처 목록)
    ├─ 함수 본문 전체
    ├─ 해당 함수의 SQL 블록 + SQLCA 검사 여부
    ├─ 해당 함수의 Scanner findings
    └─ 해당 함수의 proc 에러
  Step 6: 결과 병합 + issue_id 재번호 + 중복 제거
```

### Subtask

### T33.1: 글로벌 컨텍스트 추출 함수 → `mider/tools/utility/token_optimizer.py`

`extract_proc_global_context(file_content: str) -> str` 구현:

추출 대상:
- `EXEC SQL BEGIN DECLARE SECTION` ~ `EXEC SQL END DECLARE SECTION` (호스트 변수)
- 함수 밖 전역 변수 선언 (기존 `_extract_globals()` 활용)
- `#include` / `EXEC SQL INCLUDE` 목록
- `typedef` / `struct` 정의 (함수 밖)

출력 예시:
```
[글로벌 컨텍스트]
#include "pfmcom.h"
EXEC SQL INCLUDE SQLCA;

EXEC SQL BEGIN DECLARE SECTION;
  char gs_input_h[100];
  long gl_ret_code;
EXEC SQL END DECLARE SECTION;

FILE *gf_fp_out;
char gc_proc_cd[2];
```

### T33.2: SQL 블록 함수 매핑 → `mider/tools/utility/sql_extractor.py`

현재 SQLExtractor는 각 SQL 블록의 `line` 번호만 반환하고 어느 함수에 속하는지 모름.

변경:
- `find_function_boundaries()` 활용하여 각 SQL 블록에 `function` 필드 추가
- `_find_enclosing_function(line, boundaries, func_names)` 패턴 (CHeuristicScanner와 동일)

출력 변경:
```json
{
  "id": 3,
  "sql": "SELECT ...",
  "line": 746,
  "function": "c110_open_gnrl_cursor",
  "has_sqlca_check": true,
  "host_variables": ["gs_input_h"],
  "indicator_variables": []
}
```

### T33.3: 함수별 청킹 분석 메서드 → `mider/agents/proc_analyzer.py`

`_run_function_chunked()` 메서드 신규 구현 (C의 `_run_two_pass()` + `_analyze_single_function()` 패턴 참조):

```python
async def _run_function_chunked(
    self, *, file, file_content, file_context,
    proc_errors, sql_blocks, scanner_findings,
) -> list[dict]:
    # 1. 글로벌 컨텍스트 추출
    global_context = extract_proc_global_context(file_content)

    # 2. 함수 경계 추출
    boundaries = find_function_boundaries(lines, "proc")

    # 3. 구조 요약 (전체 함수 시그니처)
    structure_summary = build_structure_summary(file_content, file_context, "proc")

    # 4. 함수별 데이터 분배
    #   - sql_blocks → 함수별로 필터
    #   - scanner_findings → 함수별로 필터
    #   - proc_errors → 함수별로 필터 (line 기준)

    # 5. asyncio.gather + Semaphore(3) 병렬 호출
    #   각 함수: global_context + structure + 함수 본문 + 해당 sql_blocks + findings + errors

    # 6. 결과 병합 + PC-001 재번호 + 중복 제거
```

**run() 메서드 변경:**
- 기존 Error-Focused/Heuristic 분기를 **함수별 청킹으로 통합**
- 모든 파일이 `_run_function_chunked()` 경로로 진입
- 단, 함수가 1개 이하인 짧은 파일은 기존 단일 LLM 호출 유지 (오버헤드 방지)

### T33.4: 함수별 분석 프롬프트 → `mider/config/prompts/proc_analyzer_function.txt` (신규)

기존 `proc_analyzer_error_focused.txt`를 기반으로 함수 단위 분석용 프롬프트 작성:

```
당신은 Oracle Pro*C 분석 전문가입니다.
아래 함수 하나를 심층 분석하세요.

## 글로벌 컨텍스트 (호스트 변수, 전역 선언)
{global_context}

## 전체 파일 구조 요약
{structure_summary}

## 분석 대상 함수
{function_code}

## 이 함수의 SQL 블록
{function_sql_blocks}

## 이 함수의 Scanner 탐지 결과
{function_scanner_findings}

## 이 함수의 proc 에러
{function_proc_errors}

## 분석 절차
(기존 6단계 체크리스트 유지)
```

### T33.5: 단위 테스트

#### `tests/test_tools/test_token_optimizer.py` 추가
- `extract_proc_global_context()` 테스트
  - DECLARE SECTION 추출
  - 전역 변수 추출
  - 빈 파일 / DECLARE SECTION 없는 파일

#### `tests/test_tools/test_sql_extractor.py` 추가
- SQL 블록 함수 매핑 테스트
  - 함수 내부 SQL → function 필드 정확히 매핑
  - 함수 밖 SQL → function=None

#### `tests/test_agents/test_proc_analyzer.py` 추가
- 함수별 청킹 E2E 테스트
  - 모든 함수가 개별 LLM 호출되는지 확인
  - 글로벌 컨텍스트가 각 호출에 포함되는지 확인
  - issue_id 재번호 검증
  - 함수 1개 이하 → 기존 단일 호출 fallback

---

## T36: Agent 표준 로그 개선 — 언어별 동작 차이 가시화

### 배경

현재 Analyzer Agent의 분석 경로 선택(Error-Focused/Heuristic/2-Pass), 도구 실행 결과,
후처리 과정이 `ReasoningLogger`에만 기록되고 Python 표준 `logging`에는 안 남는다.

**문제점:**
1. `verbose=False`(기본값)이면 ReasoningLogger 출력이 전부 무시됨
2. 로그 파일 분석 시 "어떤 경로로 분석했는지" 확인 불가
3. 도구(ESLint, clang-tidy, proc, scanner 등) 성공 시 표준 로그 없음
4. 후처리(dedup, merge) 결과가 표준 로그에 없음

### 변경 원칙
- ReasoningLogger는 그대로 유지 (verbose CLI 출력용)
- 핵심 정보를 `logger.info()`로 **병행 출력**
- 로그 형식: `"{Agent} [{파일명}] {내용}"` — 에이전트별 구분 가능

### Subtask

#### T36.1: 분석 경로 선택 로그 추가 (5개 Analyzer)
대상 파일: `mider/agents/{js,c,proc,sql,xml}_analyzer.py`

각 Analyzer의 `run()` 메서드에서 분석 경로를 선택하는 지점에 `logger.info()` 추가:

```python
# 현재: rl.decision만 호출
self.rl.decision("Decision: Error-Focused path", reason="...")

# 추가: 표준 로그에도 기록
logger.info(f"C [{filename}] 경로: Error-Focused | clang-tidy {w_count}건 경고")
logger.info(f"C [{filename}] 경로: 2-Pass | clang-tidy 없음, {line_count}줄(>500)")
logger.info(f"JS [{filename}] 경로: Heuristic | ESLint 경고 없음")
logger.info(f"ProC [{filename}] 경로: Error-Focused | proc errors=3, SQLCA 미검사, Scanner 2건")
logger.info(f"SQL [{filename}] 경로: Error-Focused | syntax errors=1, explain plan 있음")
logger.info(f"XML [{filename}] 경로: Error-Focused | duplicate_ids=2, missing_handlers=1")
```

#### T36.2: 도구 실행 결과 로그 추가 (5개 Analyzer)
대상 파일: 동일

성공 시에도 도구 실행 결과를 표준 로그에 기록:

| Agent | 추가할 로그 |
|-------|-----------|
| JS | `"JS [{f}] ESLint: errors={n}, warnings={n}"` |
| C | `"C [{f}] Scanner: {n}건 findings ({패턴별 수})"` |
| C | `"C [{f}] clang-tidy: {n}건 유의미 / {n}건 필터링"` |
| ProC | `"ProC [{f}] proc: {n}건 에러"` |
| ProC | `"ProC [{f}] SQL블록: {n}개, Scanner: {n}건"` |
| SQL | `"SQL [{f}] 문법에러: {n}건, 패턴: {n}건, 튜닝포인트: {n}건"` |
| XML | `"XML [{f}] parse: {n} dataList, {n} events, {n} dup ID"` |
| XML | `"XML [{f}] JS검증: {missing}/{total} 핸들러 누락"` |

#### T36.3: 후처리 로그 추가
대상 파일: `mider/agents/c_analyzer.py`, `mider/agents/sql_analyzer.py`

| 위치 | 추가할 로그 |
|------|-----------|
| C `_deduplicate_issues()` 후 | `"C [{f}] Dedup: {before}건 → {after}건 ({removed}건 제거)"` |
| SQL `_merge_issues()` 후 | `"SQL [{f}] 병합: LLM {n}건 + 정적 {n}건 → 최종 {n}건"` |

#### T36.4: 단위 테스트
대상 파일: `tests/test_agents/test_analyzer_logging.py` (신규)

pytest `caplog` fixture로 로그 메시지 출력 검증:
- 각 Agent가 분석 경로 로그를 출력하는지
- 도구 실행 결과 로그가 포함되는지
- 후처리 로그가 포함되는지

---

## 설계 결정 사항

| 결정 | 이유 |
|------|------|
| Error-Focused/Heuristic 분기 제거 → 함수별 청킹 통합 (T33) | 사용자 요청: "proc에러 조건과 관계없이" |
| 함수 1개 이하 파일은 기존 단일 LLM 호출 (T33) | 함수 1개를 청킹할 이유 없음, 오버헤드 방지 |
| 글로벌 컨텍스트를 매 함수 호출에 첨부 (T33) | 커서 라이프사이클 추적 위해 호스트 변수/커서 선언 필요 |
| SQL 블록 함수 매핑을 SQLExtractor에 추가 (T33) | 함수별 청크에 해당 SQL만 전달하기 위해 |
| C의 asyncio.gather + Semaphore(3) 패턴 재사용 (T33) | 검증된 병렬 호출 패턴, rate limit 보호 |
| 새 프롬프트 파일 (proc_analyzer_function.txt) (T33) | 기존 프롬프트는 전체 파일 기준 → 함수 단위 분석 지시 필요 |
| ReasoningLogger + 표준 logging 병행 (T36) | verbose CLI + 로그 파일 양쪽에서 확인 가능 |
| 로그 형식에 Agent명 + 파일명 포함 (T36) | 동시 분석 시 어떤 Agent의 로그인지 즉시 구분 |

## 의존성

| Task | 의존성 | 비고 |
|------|--------|------|
| T33.1 | 없음 | token_optimizer.py 신규 함수 |
| T33.2 | 없음 | sql_extractor.py 확장 |
| T33.3 | T33.1, T33.2 | proc_analyzer.py 핵심 변경 |
| T33.4 | T33.3 | 프롬프트 신규 |
| T33.5 | T33.1~T33.4 | 테스트 |
| T36.1 | 없음 | 5개 Analyzer 로그 추가 |
| T36.2 | 없음 | 5개 Analyzer 로그 추가 |
| T36.3 | 없음 | C, SQL 후처리 로그 |
| T36.4 | T36.1~T36.3 | 테스트 |

## 예상 토큰 비용 변화 (T33)

| 파일 유형 | 현재 (단일 호출) | 변경 후 (함수별) | 비고 |
|-----------|-----------------|-----------------|------|
| 839줄 / 6함수 | ~7K (전체) | ~6×(1.5K+0.5K글로벌) = ~12K | 1.7배 증가, 정밀도 향상 |
| 3018줄 / 15함수 | ~7K (head+tail) | ~15×(2K+0.5K글로벌) = ~37K | 5배 증가, 누락 0% |

비용 증가하지만 **중간 코드 누락 0%** + **함수별 정밀 분석** 달성.

---

## 일정 요약

| Task | 의존성 | 상태 |
|------|--------|------|
| T1~T30 | - | ✅ 완료 |
| **T31** | **T20, T21** | **진행 중** — CAnalyzer 통합 개선 |
| **T33** | **없음** | **대기** — ProC 함수별 청킹 |
| **T36** | **없음** | **다음** — Agent 표준 로그 개선 |
| T32 | T31 | 대기 — JS 긴 파일 전략 |
| T34 | - | 대기 — XML 분석 강화 |
| T35 | - | 대기 — 주석 처리 전략 |
| T15 | T31~T36 | 대기 (마지막) — Integration Test |
