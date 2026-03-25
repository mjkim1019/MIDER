# 작업 계획서

## 개요
언어별 LLM 전달 전략 통합 개선 — CAnalyzer 모든 경로에 regex 히트 추가, JS/ProC/XML 개선 설계, 주석 처리 전략 검토

## 배경 (현재 문제점)
1. **CAnalyzer**: clang-tidy 있으면 regex 안 돌림, ≤500줄이면 regex 안 돌림 → 탐지 누락
2. **JS**: >500줄이면 head 200 + tail 100만 전달 → 중간 코드 누락, 2-Pass 없음
3. **ProC**: >500줄이면 head+tail만 전달 → 중간 코드 누락, 함수별 청킹 없음
4. **XML**: 정적분석 도구 없이 파싱 결과만 → 코드 원본 미전달
5. **공통**: 주석이 그대로 포함되어 토큰 낭비 (3~20%)

---

## 구현 Task

### T31: CAnalyzer 통합 개선 (T22 흡수)

**목표**: 모든 경로(Error-Focused/Heuristic/2-Pass)에서 CHeuristicScanner를 항상 실행하고, 전체 함수 시그니처를 Pass 1에 전달

#### T31.1: build_all_functions_summary() 구현 → `mider/tools/utility/token_optimizer.py`
- 전체 함수의 시그니처 + 위치 + 줄 수 요약 생성
- 출력: `[L142-L268] int c400_get_rcv(...) — 127줄`
- `find_function_boundaries()` + `_extract_function_signatures()` 조합

#### T31.2: CHeuristicScanner 항상 실행 → `mider/agents/c_analyzer.py`
- `run()` 시작부에서 항상 `_heuristic_scanner.execute()` 호출
- clang-tidy 유무, 파일 크기와 무관하게 regex 결과 확보

#### T31.3: Error-Focused 경로에 regex 결과 병합 (기존 T22 흡수) → `mider/agents/c_analyzer.py`
- clang-tidy 경고 + regex findings를 함께 LLM에 전달
- `c_analyzer_error_focused.txt` 프롬프트에 `{scanner_findings}` 변수 추가
- 중복 제거: 같은 라인(±2) + 같은 카테고리 → clang-tidy 우선

#### T31.4: Heuristic 경로(≤500줄)에 regex 결과 추가 → `mider/agents/c_analyzer.py`
- 전체 코드 + regex findings를 함께 전달
- `c_analyzer_heuristic.txt` 프롬프트에 `{scanner_findings}` 변수 추가

#### T31.5: 2-Pass 프롬프트에 전체 함수 시그니처 전달 → `mider/config/prompts/c_prescan_fewshot.txt`
- `{all_functions_summary}` 변수 추가 (전체 함수 목록)
- regex 미히트 함수도 선별 가능한 few-shot 예시 추가
- findings 0건이어도 함수 수 기반 2-Pass 진입

#### T31.6: 단위 테스트
- `build_all_functions_summary()` 출력 검증
- 모든 경로에서 scanner_findings 포함 확인
- regex 미히트 함수 선별 시나리오

---

## 설계 검토 Task (구현 전 검토 필요)

### T32: JS 긴 파일 전략 설계 (검토)

**현재 문제**: >500줄이면 head 200 + tail 100만 전달 → 중간 코드 통째로 누락

#### T32.1: 대안 비교 분석
- **안 A**: C와 동일한 2-Pass 도입 (ESLint 없을 때)
  - 장점: 검증된 패턴 재사용
  - 단점: JS용 regex 패턴 세트 새로 만들어야 함 (XSS, 이벤트 리스너 누수 등)
- **안 B**: 함수 청킹 (모든 함수를 개별 LLM 호출)
  - 장점: 누락 없음
  - 단점: LLM 호출 수 증가, 비용 상승
- **안 C**: ESLint 항상 실행 강제 → Error-Focused 경로만 사용
  - 장점: 구현 간단
  - 단점: ESLint 바이너리 폐쇄망 배포 필요 (이미 포함)

#### T32.2: 설계 결정 문서 작성 → `docs/worklog/context.md`

### T33: ProC 함수별 청킹 설계 (검토)

**사용자 요청**: proc 에러 조건/줄 수와 무관하게 전체 코드 전송. 함수별 청킹해서 개별 LLM 호출.

#### T33.1: ProC 코드 구조 특성 분석
- EXEC SQL 블록이 함수 내부에 산재 → 함수 단위 청킹이 적합한지 확인
- C의 2-Pass와 다른 점: SQL 블록 컨텍스트가 함수 간에 공유될 수 있음
- 함수별 청킹 시 SQLCA 검사 누락 탐지가 가능한지 확인

#### T33.2: 전체 코드 전송 방식 설계
- 모든 파일: 구조 요약 + 함수별 개별 LLM 호출
- SQL 블록은 항상 컨텍스트로 첨부
- ProCHeuristicScanner 결과도 함수별로 분배

#### T33.3: 설계 결정 문서 작성 → `docs/worklog/context.md`

### T34: XML 분석 강화 검토

**조사 결과**:
- ESLint → 부적합 (HTML/JS용, WebSquare 커스텀 네임스페이스 미지원)
- lxml + XSD → 가능하나 WebSquare XSD 스키마 필요 (확보 여부 미확인)
- 현실적 대안: 파싱 데이터 + 전체 코드 함께 전달

#### T34.1: 전체 코드 전달 효과 검토
- 현재: 파싱 결과(data_lists, events, component_ids)만 → LLM이 코드 원본 못 봄
- 개선: 파싱 결과 + XML 원본도 함께 전달 → LLM이 바인딩/속성 오류 직접 확인
- 토큰 비용 추정 (일반적 WebSquare XML 크기 기준)

#### T34.2: `<script>` 태그 추출 추가 (이슈 #005)
- XML 내 CDATA 인라인 JS 추출 → 실제 버그 발생 지점
- ESLint로 인라인 JS 린팅 가능 여부 확인

#### T34.3: 설계 결정 문서 작성 → `docs/worklog/context.md`

### T35: 주석 처리 전략 검토

**조사 결과**:
- 토큰 절감: 3~20% (파일별 편차)
- 라인번호 깨짐: **CRITICAL** — 제거하면 LLM이 보고하는 line_start가 원본과 불일치
- 유용한 주석 유실: 비즈니스 로직 설명, TODO, 비활성화 코드

#### T35.1: 전략 비교
| 전략 | 토큰 절감 | 라인번호 | 컨텍스트 보존 | 구현 난이도 |
|------|-----------|----------|--------------|------------|
| 현행 (유지) | 0% | ✅ 정확 | ✅ 완전 | 없음 |
| 전체 제거 + 라인 매핑 | 3~20% | ⚠️ 매핑 필요 | ❌ 유실 | 높음 |
| 선택적 제거 (헤더만) | 1~5% | ✅ 정확 (빈줄 대체) | ✅ 대부분 보존 | 중간 |
| 주석 → 1줄 요약 압축 | 5~15% | ✅ 정확 | ⚠️ 부분 보존 | 중간 |

#### T35.2: 설계 결정 문서 작성 → `docs/worklog/context.md`

---

## 일정 요약

| Task | 유형 | 의존성 | 상태 |
|------|------|--------|------|
| T1~T30 | - | - | ✅ 완료 |
| **T31** | **구현** | T20, T21 | **다음** — CAnalyzer 통합 개선 |
| T32 | 설계 검토 | T31 | 대기 — JS 긴 파일 전략 |
| T33 | 설계 검토 | T31 | 대기 — ProC 함수별 청킹 |
| T34 | 설계 검토 | - | 대기 — XML 분석 강화 |
| T35 | 설계 검토 | - | 대기 — 주석 처리 전략 |
| T15 | 구현 | T31~T35 | 대기 (마지막) — Integration Test |
