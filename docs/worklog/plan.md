# 작업 계획서

## 개요
XML 중복 ID 스코프 개선 — 데이터 정의 요소(`<w2:column>` 등)를 컴포넌트 ID 중복 검사에서 제외하여 false positive 방지 (이슈 #005 Phase 3)

## 완료된 Task
- T1~T14, T16, T17: Project Scaffold → Data Models → Base Infrastructure → Tools → Prompts → Agents → CLI → 토큰 최적화 → 배포 체크리스트
- T18: SQL 성능개선 강화
- T20: C Heuristic Pre-Scanner (2-Pass 분석)
- T21: Pass 2 함수별 개별 LLM 호출
- T22: clang-tidy + Heuristic 하이브리드 분석 (브랜치 미머지)
- T23: SQL 분석 파이프라인 검증 및 테스트
- T24: Explain Plan 튜닝 포인트 → 정적 이슈 자동 생성
- T19: Proframe XML 지원

## 진행 예정 Task

### T25: XML 중복 ID 스코프 개선 (이슈 #005 Phase 3)

#### T25.1: `_extract_component_ids`에서 데이터 정의 요소 제외 → 대상: `mider/tools/static_analysis/xml_parser.py`
- `column`, `columnInfo`, `data` 태그를 컴포넌트 ID 수집에서 제외
- `dataList`, `dataMap` ID는 유지 (document-level에서 `$w.getById()`로 접근)
- 제외 대상 태그 세트를 상수로 정의 (`_DATA_DEFINITION_TAGS`)

#### T25.2: 테스트 수정 및 추가 → 대상: `tests/test_tools/test_xml_parser.py`
- 서로 다른 dataList에 동일 column id가 있어도 중복 미탐지 테스트
- body UI 컴포넌트 중복은 여전히 탐지하는 회귀 테스트
- 기존 `test_extract_all_ids` 수정 (column ID 제외 반영)
- 기존 `test_no_duplicates` 유지 (SAMPLE_WEBSQUARE_XML에 중복 없음)

#### T25.3: 이슈 로그 업데이트 → 대상: `docs/issue-log/005-xml-script-extraction-missing.md`
- Phase 3 해결 완료 기록

---

### T15: Integration Test (depends: T25)
- (기존 계획 유지)

---

## 일정 요약
| Task | 의존성 | 상태 |
|------|--------|------|
| T1~T24, T19 | - | ✅ 완료 |
| T25 | T19 | **다음** — 이슈 #005 Phase 3 |
| T15 | T25 | 대기 (마지막) |
