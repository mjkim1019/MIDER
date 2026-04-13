# 작업 계획서

## 개요
인터랙티브 모드 Explain Plan 프롬프트 — SQL 파일 감지 시 Explain Plan 파일 경로를 자동으로 질문하여 `--explain-plan` CLI 플래그 없이 사용 가능하게 한다.

---

## Task 목록

### T56: 인터랙티브 Explain Plan 프롬프트
- T56.1: `prompt_for_explain_plan()` 함수 추가 + `main()` 연동 → `mider/main.py`
  - 입력된 파일 목록에 `.sql` 파일이 포함되어 있는지 확인
  - 있으면 "Explain Plan 파일이 있으면 입력하세요 (없으면 Enter):" 프롬프트
  - 입력된 경로를 `resolve_input_files()`와 동일한 방식으로 해석
  - Enter만 누르면 `None` (explain plan 없이 분석)
  - `main()`에서 인터랙티브 모드일 때 `explain_plan` 변수에 연결
- T56.2: 단위 테스트 → `tests/test_cli/test_main.py`
  - SQL 포함 → 프롬프트 호출 확인
  - SQL 미포함 → 프롬프트 미호출 확인
  - Enter(빈 입력) → None 반환 확인

---

## 설계 결정

| 결정 | 이유 |
|------|------|
| **`--explain-plan` CLI 옵션 유지** | 스크립트/CI 호출 시 필요. 인터랙티브에서만 자동 질문 추가 |
| **SQL 파일 감지는 확장자(`.sql`) 기반** | 파일 내용 파싱 없이 빠르게 판단 가능 |
| **Enter로 건너뛰기** | explain plan은 선택 사항 — 없어도 SQL 분석은 동작 |
| **파일 경로 해석은 기존 로직 재사용** | `resolve_input_files()`와 동일하게 절대/상대/workspace 검색 |

## 의존성

| Task | 의존 | 비고 |
|------|------|------|
| T56 | 없음 | main.py 인터랙티브 UX 변경, 분석 로직 변경 없음 |
