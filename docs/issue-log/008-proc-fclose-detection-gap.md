# 이슈 #008: Pro*C 분석에서 fclose 누락 미탐지

## 발견일
2026-03-18

## 발견 경위
zordbs0401549.pc에서 의도적으로 `fclose(gfp_wp1)` 제거 후 Mider 분석 → 미탐지 확인.

## 문제 상황

### 테스트 케이스
- 원본: `fopen` → `fwrite` × N → `fclose` (정상)
- 버그: `fopen` → `fwrite` × N → **fclose 누락** (파일 핸들 릭)

### 탐지 결과
- EXEC SQL 관련 이슈 4건 탐지 (SQLCA 미검사, ROLLBACK 누락 등)
- **fclose 누락: 미탐지**

### 근본 원인
ProCAnalyzerAgent는 **EXEC SQL 블록 중심**으로 분석:
1. `SQLExtractor`가 EXEC SQL 블록만 추출
2. `ProcRunner`가 proc 프리컴파일러 에러만 수집
3. LLM 프롬프트가 SQLCA/트랜잭션/커서에 집중

일반 C 파일 I/O (`fopen`/`fclose`/`fwrite`)는 분석 범위 밖.

## 영향
- Pro*C 배치 프로그램에서 파일 핸들 릭 → 장시간 배치 시 `Too many open files` 크래시
- SAM 파일 쓰기 후 미닫기 → 데이터 flush 안 됨 → 불완전 파일

## 해결 방안

### 방안 1: Pro*C 프롬프트에 파일 I/O 검사 추가 (단기)
- `proc_analyzer_error_focused.txt` / `proc_analyzer_heuristic.txt`에 지시 추가
- "fopen이 있으면 대응하는 fclose가 있는지 확인"
- LLM이 전체 코드를 볼 수 있으므로 매칭 가능

### 방안 2: 정적 검사 도구 추가 (중기)
- `fopen`/`fclose` 짝 매칭을 regex/AST로 사전 검사
- 불일치 발견 시 Error-Focused 프롬프트에 경고로 전달

## 관련
- ProCAnalyzerAgent (`mider/agents/proc_analyzer.py`)
- SQLExtractor (`mider/tools/utility/sql_extractor.py`)
- 프롬프트: `mider/config/prompts/proc_analyzer_*.txt`
