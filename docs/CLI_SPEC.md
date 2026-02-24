# CLI 인터페이스 명세: Mider

> `mider` CLI 명령어, 옵션, 출력 형식 정의

---

## 1. 실행 방법

### 폐쇄망 환경 (실행파일)
```bash
# Windows
mider.exe --files src/calc.c src/order.pc

# Linux
./mider --files src/calc.c src/order.pc
```

### 개발 환경 (Python)
```bash
python main.py --files src/calc.c src/order.pc
```

---

## 2. 옵션

### 필수 옵션

| 옵션 | 단축 | 설명 | 예시 |
|------|------|------|------|
| `--files` | `-f` | 분석할 파일 경로 (1개 이상) | `--files src/calc.c src/order.pc` |

### 선택 옵션

| 옵션 | 단축 | 기본값 | 설명 |
|------|------|--------|------|
| `--output` | `-o` | `./output` | 결과 출력 디렉토리 |
| `--model` | `-m` | `gpt-4o` | LLM 모델명 |
| `--severity` | `-s` | `all` | 출력할 최소 심각도 (`critical`, `high`, `medium`, `low`, `all`) |
| `--format` | | `json` | 출력 형식 (`json`, `text`) |
| `--verbose` | `-v` | `false` | 상세 로그 출력 |
| `--no-static` | | `false` | 정적 분석 건너뛰기 (LLM만 사용) |
| `--timeout` | `-t` | `300` | 파일당 최대 분석 시간 (초) |
| `--version` | | | 버전 정보 출력 |

### 와일드카드 지원
```bash
# glob 패턴 지원
mider.exe --files "src/**/*.c" "src/**/*.pc"

# 단일 파일
mider.exe --files src/service/calc.c
```

### 실행 예시
```bash
# 기본 분석
mider.exe --files src/calc.c src/order.pc src/main.js

# Critical/High만 출력
mider.exe --files src/calc.c --severity high

# 다른 모델 사용
mider.exe --files src/calc.c --model gpt-4-turbo

# 상세 로그
mider.exe --files src/calc.c -v

# 출력 디렉토리 지정
mider.exe --files src/calc.c --output ./reports/2026-02-24
```

---

## 3. 터미널 출력 형식

### 분석 진행 중
```
Mider v0.1.0 — 소스코드 분석 시작

[대상 파일] 3개
  ├── src/service/calc.c (C)
  ├── src/batch/process.pc (Pro*C)
  └── src/db/orders.sql (SQL)

[Phase 0] 파일 분류 중...
  ✓ 완료 — ExecutionPlan 생성 (3 tasks)

[Phase 1] 컨텍스트 수집 중...
  ✓ 완료 — FileContext 생성

[Phase 2] 코드 분석 중... [2/3]
  ├── ✓ task_1: orders.sql (SQL) — 1 issues (0.8s)
  ├── ✓ task_2: calc.c (C) — 4 issues (8.5s)
  └──   task_3: process.pc (Pro*C) — 분석 중...
```

### 분석 완료 — 이슈 상세 (Before/After)
```
  ✓ Phase 2 완료 — 3개 파일 분석 (17.3s)

[Phase 3] 리포트 생성 중...
  ✓ 완료

════════════════════════════════════════════════════════════
  분석 결과
════════════════════════════════════════════════════════════

[CRITICAL] C-001  strcpy 버퍼 오버플로우 위험
  파일: src/service/calc.c:234
  ──────────────────────────────────────────────────────
  Before:
    strcpy(dest, src);
  After:
    strncpy(dest, src, sizeof(dest) - 1);
    dest[sizeof(dest) - 1] = '\0';
  ──────────────────────────────────────────────────────
  설명: strcpy()는 버퍼 크기를 검증하지 않아 오버플로우가
        발생할 수 있습니다. strncpy()로 교체하고
        NULL 종료 문자를 보장하세요.

[CRITICAL] C-003  NULL 포인터 역참조 위험
  파일: src/service/calc.c:189
  ──────────────────────────────────────────────────────
  Before:
    result = ptr->value;
  After:
    if (ptr != NULL) {
        result = ptr->value;
    }
  ──────────────────────────────────────────────────────
  설명: malloc 반환값을 검증하지 않고 사용합니다.
        NULL 체크를 추가하세요.

[HIGH] PC-001  SQLCA 에러 체크 누락
  파일: src/batch/process.pc:89
  ──────────────────────────────────────────────────────
  Before:
    EXEC SQL UPDATE ORDERS SET STATUS = :status
             WHERE ORDER_ID = :id;
  After:
    EXEC SQL UPDATE ORDERS SET STATUS = :status
             WHERE ORDER_ID = :id;
    if (sqlca.sqlcode != 0) {
        EXEC SQL ROLLBACK;
        return sqlca.sqlcode;
    }
  ──────────────────────────────────────────────────────
  설명: EXEC SQL 실행 후 SQLCA 에러 체크가 누락되어
        데이터 무결성이 손상될 수 있습니다.

[MEDIUM] SQL-001  인덱스 억제 패턴
  파일: src/db/orders.sql:15
  ──────────────────────────────────────────────────────
  Before:
    WHERE YEAR(created_at) = 2026
  After:
    WHERE created_at >= '2026-01-01'
      AND created_at < '2027-01-01'
  ──────────────────────────────────────────────────────
  설명: WHERE 절에 함수 사용으로 인덱스가 무시됩니다.
        범위 조건으로 변경하세요.

... (총 7건)

════════════════════════════════════════════════════════════
  요약
════════════════════════════════════════════════════════════

  심각도        건수
  ─────────────────
  CRITICAL      2
  HIGH          3
  MEDIUM        1
  LOW           1
  ─────────────────
  합계          7

  배포 판정: 불가 (Critical 2건 해결 필요)
  예상 수정 시간: 90분 (Critical: 30분, High: 45분)

[출력 파일]
  ├── ./output/issue-list.json
  ├── ./output/checklist.json
  └── ./output/summary.json
```

---

## 4. 출력 파일 (output/)

| 파일 | 설명 |
|------|------|
| `issue-list.json` | 전체 이슈 목록 (severity 순, Before/After 코드 포함) |
| `checklist.json` | Critical/High 이슈 검증 체크리스트 |
| `summary.json` | 통계 요약, 배포 판정, 예상 수정 시간 |

> 스키마 상세는 `docs/DATA_SCHEMA.md` 참조

---

## 5. 종료 코드

| 코드 | 의미 |
|------|------|
| `0` | 정상 완료, Critical 이슈 없음 |
| `1` | 정상 완료, Critical 이슈 있음 (배포 불가) |
| `2` | 실행 오류 (파일 없음, 권한 없음 등) |
| `3` | LLM API 오류 (연결 실패, 토큰 초과 등) |

---

## 6. 환경 변수

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `MIDER_API_KEY` | (필수) | OpenAI API 키 |
| `MIDER_API_BASE` | `https://api.openai.com/v1` | API Base URL (폐쇄망 프록시) |
| `MIDER_MODEL` | `gpt-4o` | 기본 LLM 모델 |
| `MIDER_OUTPUT_DIR` | `./output` | 기본 출력 디렉토리 |
| `MIDER_LOG_LEVEL` | `INFO` | 로그 레벨 (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `MIDER_TIMEOUT` | `300` | 파일당 분석 타임아웃 (초) |

---

## 7. 2차 PoC 예정 기능

| 기능 | 설명 |
|------|------|
| `--resume <session_id>` | 중단된 세션 재개 |
| `--sessions` | 세션 목록 조회 |
| 자동 체크포인트 | Phase/Task 단위 세션 저장 |
| Ctrl+C 중단 복구 | 중단 시 자동 세션 저장 후 재개 |
