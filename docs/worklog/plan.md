# 작업 계획서

## 개요
ProC Analyzer 전면 재설계 — 전체 코드 전달 + 스마트 그룹핑:
- **통일 아키텍처**: 파일 크기 무관하게 동일한 데이터 파이프라인
- **전체 코드 우선**: 토큰 한계 내 파일은 전체 코드 단일 LLM 호출
- **스마트 그룹핑**: 초과 파일은 함수 패턴 기반 자동 그룹핑
- **위험 함수 태깅**: Pass 1(mini)으로 위험 함수 중점 표시

---

## 배경

### 현재 문제
1. Error-Focused가 Scanner/proc에러 **있는 함수만** 코드 추출 → Scanner가 못 잡는 버그 누락
2. 함수별 개별 분석은 cross-function 맥락 손실 (Lost-in-the-Middle)
3. 8000줄 파일은 128K 토큰 초과로 단일 호출 불가

### 샘플 데이터 (24개 .pc 파일)
| 구간 | 파일 수 | 토큰 범위 | 단일 호출? |
|------|---------|-----------|-----------|
| ≤2000줄 | 18개 | ~5K~43K | OK |
| 2000~5000줄 | 4개 | ~40K~75K | OK |
| 5000줄+ | 2개 | ~131K~176K | OVER |

→ 24개 중 22개는 전체 코드 단일 호출 가능, 2개만 그룹핑 필요

### ProC 함수 패턴 (실제 파일 분석)
```
공통 보일러플레이트 (경량 처리):
  main() → 모듈명() → init_proc() → exit_proc()

비즈니스 로직:
  계층형 (b00→b10/b20/b30): 순차 호출, 커서/변수 공유 → 형제 그룹핑
  디스패치형 (work_proc1~11): if/else 분기, 독립적 → 개별 분석
  유틸/z계열 (z00+z99, s03+s10): 접두사별 그룹핑
```

---

## 변경 후 흐름

```
모든 ProC 파일 공통 파이프라인:
  Step 1: proc 프리컴파일러 (에러 수집)
  Step 2: SQL 블록 추출 + 함수 매핑
  Step 3: Heuristic Scanner (패턴 4종)
  Step 4: 글로벌 컨텍스트 추출
  Step 5: 커서 라이프사이클 맵 생성
  Step 6: Pass 1 — gpt-5-mini로 위험 함수 태깅

  코드 전달 분기:
  ├─ ≤100K tokens → 단일 호출
  │   전체 코드 + 모든 컨텍스트 + 위험 함수 표시
  │
  └─ >100K tokens → 스마트 그룹핑
      ├─ 계층형 (b10+b20+b30) → 형제 그룹 호출
      ├─ 디스패치형 (work_proc1~11) → 개별 호출
      └─ 유틸 (z00+z99) → 접두사 그룹 호출
      각 그룹에 글로벌 컨텍스트 + 커서 맵 + 위험 표시 첨부

  Step 7: 결과 병합 + issue_id 재번호
```

---

## T33: ProC 분석 재설계

### T33.1: 프롬프트 통합 → `proc_analyzer.txt` (신규)

기존 `error_focused` + `heuristic` 2개를 **단일 프롬프트**로 통합.
전체 코드를 전달하므로 분기 불필요.

변수: `{global_context}`, `{cursor_lifecycle_map}`, `{risky_functions_annotation}`,
      `{scanner_findings}`, `{proc_errors}`, `{sql_blocks}`, `{code}`, `{file_path}`

→ 단일 호출/그룹핑 모두 이 프롬프트 사용. `{code}`에 전체 코드 또는 그룹 코드.

### T33.2: 함수 패턴 분류기 → `token_optimizer.py`

`classify_proc_functions()` 구현:
- 보일러플레이트 식별 (main, init_proc, exit_proc)
- 계층형 그룹핑 (숫자 접두사 첫 자리 기준)
- 디스패치형 식별 (main_proc 내 if/else 분기 대상)
- 유틸/z계열 그룹핑 (z, s, rep 접두사)

### T33.3: 토큰 기반 전달 분기 → `proc_analyzer.py`

`_decide_delivery_mode()`: `len(file_content) // 3 + 3000 ≤ 100000` → "single" / "grouped"

### T33.4: 단일 호출 경로 → `proc_analyzer.py`

`_run_single_call()`: 전체 코드 + 모든 컨텍스트를 1회 LLM 호출

### T33.5: 그룹핑 호출 경로 → `proc_analyzer.py`

`_run_grouped_call()`: classify → 그룹별 코드 추출 → 병렬 LLM 호출 (Semaphore 3)

### T33.6: `run()` 리팩토링 → `proc_analyzer.py`

Error-Focused/Heuristic 분기 제거 → 통일 파이프라인 + 전달 분기

### T33.7: 단위 테스트

- classify_proc_functions: 4패턴 분류 검증
- _decide_delivery_mode: 토큰 기준 분기
- 단일 호출 E2E + 그룹핑 호출 E2E
- 기존 글로벌 컨텍스트/커서 맵/SQL 매핑 테스트 유지

---

## 재사용 자산 (기존 T33 구현)

| 유틸리티 | 용도 |
|----------|------|
| `extract_proc_global_context()` | DECLARE SECTION, 전역변수 |
| `build_cursor_lifecycle_map()` | 커서 맵 |
| `build_all_functions_summary()` | 함수 시그니처 요약 |
| `find_function_boundaries()` | 함수 경계 |
| SQL 블록 `function` 필드 | SQL→함수 매핑 |
| `proc_prescan.txt` | Pass 1 선별 프롬프트 |

---

## 설계 결정

| 결정 | 이유 |
|------|------|
| 전체 코드 단일 호출 우선 | Scanner 한계 → LLM이 직접 전체 코드에서 버그 탐지. 정확성 최우선 |
| 100K 토큰 기준 분기 | 128K 한계에서 프롬프트+응답 여유분 확보 |
| 계층형 그룹핑 | b10+b20+b30은 커서/변수 흐름 공유 — 분리 시 크로스함수 버그 누락 |
| 디스패치형 개별 분석 | work_proc1~11은 독립적 — 그룹핑 시 attention 분산만 증가 |
| Error-Focused/Heuristic 제거 | 전체 코드 전달이므로 경로 분기 불필요 |
| Pass 1 위험 태깅 유지 | 선별이 아닌 중점 표시 — 전체 분석하되 위험 함수에 집중 |
| 프롬프트 1개 통합 | 코드 전달 방식만 다르고 분석 절차 동일 |

## 의존성

| Subtask | 의존 | 비고 |
|---------|------|------|
| T33.1 | 없음 | 프롬프트 |
| T33.2 | 없음 | 유틸 |
| T33.3 | T33.2 | 분류기 필요 |
| T33.4 | T33.1 | 프롬프트 필요 |
| T33.5 | T33.1, T33.2 | 프롬프트+분류기 |
| T33.6 | T33.3~T33.5 | 전체 통합 |
| T33.7 | T33.1~T33.6 | 테스트 |
