# 작업 계획서

## 개요
clang-tidy 헤더 누락 시 Level 1 저가치 경고 필터링 — 헤더 에러가 있으면 Level 1(bugprone-*)은 저가치로 분류하고, Level 2(clang-analyzer-*)만 유의미로 판정. Level 2가 0건이면 Heuristic/2-Pass fallback.

## 완료된 Task
- T1~T27, T19: 전체 기능 구현 완료 (T27: 헤더 에러 필터링)

## 진행 예정 Task

### T28: clang-tidy Level 1 저가치 경고 필터링 (이슈 #002 확장)

#### T28.1: Level 1/Level 2 분류 로직 구현 → 대상: `mider/agents/c_analyzer.py`
- `_run_clang_tidy()` 수정: 헤더 에러가 1건 이상이면 Level 1 경고도 저가치로 분류
- Level 2 판정: check가 `clang-analyzer-` 접두사
- Level 1 판정: check가 `bugprone-`, `cert-`, `misc-` 등 나머지
- 헤더 에러 있음 + Level 2 0건 → None (fallback)
- 헤더 에러 있음 + Level 2 > 0 → Level 2만 Error-Focused에 전달
- 헤더 에러 없음 → 기존 동작 (전부 유의미)

#### T28.2: 이슈 #002 로그 업데이트 → 대상: `docs/issue-log/002-clang-tidy-header-limitation.md`
- Level 1 저가치 분류 해결 기록 추가
- 실제 사례: 2932줄 파일에서 45건 중 44건이 Level 1 → 48개 이슈 중 45건이 노이즈

#### T28.3: 추론 로그 개선 → 대상: `mider/agents/c_analyzer.py`
- scan 로그에 Level 1/Level 2 분류 결과 표시
- decision 근거에 "헤더 에러 N건 → Level 1 M건 저가치 → Level 2 K건" 표시

#### T28.4: 단위 테스트 → 대상: `tests/test_agents/test_c_analyzer.py`
- 헤더 에러 + Level 1만 → fallback 확인
- 헤더 에러 + Level 2 있음 → Level 2만 Error-Focused
- 헤더 에러 없음 → 기존 동작 (Level 1 포함)
- 기존 테스트 회귀 확인

---

### T15: Integration Test (depends: T28)
- T15.1~T15.4: (기존 계획 유지)

---

## 일정 요약
| Task | 의존성 | 상태 |
|------|--------|------|
| T1~T27, T19 | - | ✅ 완료 |
| T28 | T27 | **다음** — Level 1 저가치 필터링 |
| T15 | T28 | 대기 (마지막) |
