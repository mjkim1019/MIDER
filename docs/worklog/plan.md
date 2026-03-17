# 작업 계획서

## 개요
C 분석 이슈 후처리 중복 제거 — 함수별 개별 LLM 호출로 발생하는 동일 패턴 이슈(strncpy 중복, 스레드 안전성 등)를 합산 후 자동 병합. 추론 로그 색상 개선.

## 완료된 Task
- T1~T28, T19: 전체 기능 구현 완료

## 진행 예정 Task

### T29: C 분석 이슈 후처리 중복 제거

#### T29.1: 이슈 중복 제거 로직 → 대상: `mider/agents/c_analyzer.py`
- `_deduplicate_issues()` 메서드 추가
- 삽입 위치: `all_issues` 합산 후, `issue_id` 재번호 전
- 중복 판정 기준:
  - 동일 title 키워드 (strncpy, 스레드, NULL 체크 등)
  - 동일 category + 유사 description (코사인 유사도 대신 키워드 매칭)
- 병합 방식: 대표 1건 유지 + description에 "외 N곳 동일 패턴" 추가
- 스레드 안전성 이슈 자동 제외 (Proframe 단일스레드)

#### T29.2: 이슈 로그 작성 → 대상: `docs/issue-log/007-c-duplicate-issues.md`
- 문제: 함수별 개별 LLM이 동일 패턴을 독립 보고 → 60건 중 45건 노이즈
- 해결: 후처리 중복 제거

#### T29.3: 추론 로그 색상 수정 → 대상: `mider/config/reasoning_logger.py`
- `prompt()` 메서드: `Pass 2 [N/M] func 분석 시작` → 흰색 bold
- spinner 제거 후 시작/완료 로그 색상 정리

#### T29.4: 단위 테스트 → 대상: `tests/test_agents/test_c_analyzer.py`
- 동일 패턴 이슈 병합 검증
- 스레드 안전성 이슈 제거 검증
- 기존 테스트 회귀

---

## 일정 요약
| Task | 의존성 | 상태 |
|------|--------|------|
| T1~T28, T19 | - | ✅ 완료 |
| T29 | T28 | **다음** — 이슈 중복 제거 |
| T15 | T29 | 대기 (마지막) |
