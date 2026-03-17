# 작업 계획서

## 개요
clang-tidy 헤더 누락 fallback — clang-tidy가 실행되지만 헤더 에러만 나오고 유의미한 경고가 없는 경우, Heuristic/2-Pass로 자동 fallback하여 분석 커버리지 확보.

## 완료된 Task
- T1~T26, T19: 전체 기능 구현 완료

## 진행 예정 Task

### T27: clang-tidy 헤더 누락 시 Heuristic/2-Pass fallback

#### T27.1: clang-tidy 결과에서 헤더 에러 필터링 → 대상: `mider/agents/c_analyzer.py`
- `_run_clang_tidy()` 수정: warnings에서 헤더 누락 에러 분리
- 헤더 에러 패턴: `file not found`, `unknown type name` 등 컴파일 fatal error
- 유의미한 경고(bugprone-*, clang-analyzer-*)만 남김
- 유의미한 경고 0건이면 `None` 반환 → Heuristic/2-Pass fallback
- 헤더 에러 존재 시 reasoning log에 detect 로그 출력

#### T27.2: 추론 로그 추가 → 대상: `mider/agents/c_analyzer.py`
- clang-tidy 실행 결과 scan 로그 (전체 N건, 헤더 에러 M건, 유의미 K건)
- 헤더 에러만 있을 때 decision 로그 ("clang-tidy 헤더 에러만 → Heuristic fallback")
- Error-Focused / Heuristic / 2-Pass 경로 선택 decision 로그

#### T27.3: 단위 테스트 → 대상: `tests/test_agents/test_c_analyzer.py`
- 헤더 에러만 있는 경우 → fallback 확인
- 유의미한 경고 + 헤더 에러 혼재 → Error-Focused 유지 (유의미 경고만 전달)
- 기존 테스트 회귀 확인

---

### T15: Integration Test (depends: T27)
- T15.1~T15.4: (기존 계획 유지)

---

## 일정 요약
| Task | 의존성 | 상태 |
|------|--------|------|
| T1~T26, T19 | - | ✅ 완료 |
| T27 | T20, T26 | **다음** — 헤더 누락 fallback |
| T15 | T27 | 대기 (마지막) |
