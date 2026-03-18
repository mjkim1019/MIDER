# 작업 계획서

## 개요
Pro*C 전용 Heuristic Scanner + 2-Pass 전략 — 실제 장애 유발 패턴 3종(포맷 문자열 타입 불일치, memset sizeof 불일치, 루프 초기화 누락)을 regex로 사전 스캔하고 LLM에 집중 분석 요청.

## 실제 장애 사례 (탐지 대상)

| 패턴 | 파일 | 장애 | 현재 탐지 |
|------|------|------|-----------|
| `%s`에 구조체 전달 | zordbs0401882.pc:1174 | Core Dump + 문자 미발송 | ✗ |
| memset sizeof 불일치 | zordms03s0200.c:272 | 구조체 일부만 초기화 → 이전 데이터 잔류 | ✗ |
| 루프 내 초기화 누락 | zinvbreps8030.pc:2915 | 이전 데이터 누적 → 금액 오표기 | ✗ |
| fclose 누락 | zordbs0401549.pc:238 | 파일 핸들 릭 | ✗ (이슈 #008) |

## 진행 예정 Task

### T30: Pro*C Heuristic Scanner 구현

#### T30.1: ProCHeuristicScanner Tool → 대상: `mider/tools/static_analysis/proc_heuristic_scanner.py`
- `CHeuristicScanner` 구조 재사용 (BaseTool 상속)
- 패턴 4종:
  1. **FORMAT_STRUCT**: `PFM_DSP/printf`의 `%s`에 배열 인덱스만 전달 (`.멤버` 접근 없음)
     - regex: `(PFM_DSP|PFM_ERR|printf|sprintf)\s*\(.*%s.*,\s*\w+\.\w+\.\w+\[\d+\]\s*[,)]`
  2. **MEMSET_SIZEOF_MISMATCH**: memset 변수명과 sizeof 타입명의 핵심 부분 불일치
     - regex: `memset\s*\(&?\s*(\w+)\s*,.*sizeof\s*\((\w+)\)`
     - 검증: 변수명에서 추출한 핵심 이름 ≠ sizeof 타입에서 추출한 핵심 이름
  3. **LOOP_INIT_MISSING**: while/for 루프 내 구조체 사용하지만 INIT2VCHAR/memset 없음
     - 루프 시작~끝 범위에서 구조체 쓰기는 있지만 초기화 호출 없음
  4. **FCLOSE_MISSING**: fopen이 있지만 대응 fclose 없음
     - 전체 파일에서 fopen 호출 수 > fclose 호출 수

#### T30.2: ProCAnalyzerAgent에 Scanner 연동 → 대상: `mider/agents/proc_analyzer.py`
- Scanner 결과를 Error-Focused 프롬프트에 전달
- Scanner findings > 0이면 Error-Focused 경로 강제 진입
- 추론 로그: Scanner 결과 표시

#### T30.3: Pro*C 프롬프트에 장애 사례 Few-shot 추가 → 대상: `mider/config/prompts/proc_analyzer_error_focused.txt`
- 3개 장애 사례를 few-shot 예시로 추가
- Scanner가 전달한 의심 위치를 LLM이 판정하도록 유도

#### T30.4: 단위 테스트 → 대상: `tests/test_tools/test_proc_heuristic_scanner.py`
- 각 패턴별 탐지/미탐지 테스트
- 실제 장애 코드 패턴으로 검증

#### T30.5: 통합 테스트 → 대상: `tests/test_agents/test_proc_analyzer.py`
- Scanner 결과가 Error-Focused 경로로 유도되는지 확인
- Scanner + LLM 결과 합산 확인

---

## 일정 요약
| Task | 의존성 | 상태 |
|------|--------|------|
| T1~T29, T19 | - | ✅ 완료 |
| T30 | T29 | **다음** — Pro*C Heuristic Scanner |
| T15 | T30 | 대기 (마지막) |
