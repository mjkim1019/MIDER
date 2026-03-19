# 이슈 #006: clang-tidy 헤더 에러 시 Error-Focused 오진입

## 발견일
2026-03-17

## 발견 경위
실제 C 파일 분석 시 clang-tidy가 실행은 되지만 헤더 누락 에러만 반환하여, 유의미한 분석 없이 Error-Focused 경로에 진입하는 문제 확인.

## 문제 상황

### 현상
- clang-tidy 실행 → 44개 warning 반환
- 전부 **헤더 누락으로 인한 텍스트 수준 패턴** (bugprone-branch-clone 37개, bugprone-narrowing-conversions 7개)
- `clang-analyzer-*` (데이터 흐름 분석) 경고: **0개**
- `_run_clang_tidy()`가 warnings 있음 → `{"warnings": [...]}`반환 → Error-Focused 경로 진입
- LLM에 쓸모없는 헤더 에러만 전달 → **svc_cnt 미초기화 등 핵심 버그 놓침**

### 근본 원인
clang-tidy는 `#include`를 실제 컴파일처럼 처리하므로, 헤더 파일이 없으면 **fatal error로 파싱 중단**. 이후 코드의 데이터 흐름 분석이 전혀 이루어지지 않음.

폐쇄망 환경에서는 프로젝트 헤더 파일을 분석 환경에 제공하기 어려우므로, 대부분의 C 파일에서 이 문제가 발생.

### 영향
| 경로 | svc_cnt 탐지 | 문제 |
|------|-------------|------|
| Error-Focused (현재) | ✗ | 헤더 에러만 LLM에 전달, 핵심 버그 누락 |
| Heuristic/2-Pass (기대) | ✓ | regex 스캔으로 UNINIT_VAR 패턴 탐지 가능 |

## 해결 방향 (T27)

### `_run_clang_tidy()` 수정
1. warnings에서 **헤더 관련 에러**를 필터링
   - severity="error" + check=`clang-diagnostic-error`
   - 메시지에 `file not found`, `unknown type name` 포함
2. 유의미한 경고(bugprone-*, clang-analyzer-*)만 남김
3. 유의미한 경고 **0건이면 `None` 반환** → Heuristic/2-Pass fallback
4. 유의미한 경고 + 헤더 에러 혼재 시 → 유의미 경고만 남기고 Error-Focused 유지

### 수정 후 분기
```
clang-tidy 실행 → 전체 N건 중 헤더 에러 M건 필터링
→ 유의미 경고 K건 > 0 → Error-Focused (K건만 전달)
→ 유의미 경고 0건 + >500줄 → 2-Pass (regex + LLM 심층)
→ 유의미 경고 0건 + ≤500줄 → Heuristic (전체 코드 LLM 검증)
```

## 관련
- 이슈 #002: clang-tidy 헤더 미존재 시 정밀 분석 불가 (상위 문서)
- 이슈 #003: Pass 2 대형 함수 압도 문제
- T20: C Heuristic Pre-Scanner (2-Pass 분석)
- T22: clang-tidy + Heuristic 하이브리드 (미머지)
