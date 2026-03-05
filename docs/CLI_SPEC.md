# CLI 인터페이스 명세: Mider

---

## 1. 실행 방법

```bash
# 폐쇄망 (Windows)
mider.exe --files src/calc.c src/order.pc

# 폐쇄망 (Linux)
./mider --files src/calc.c src/order.pc

# 개발 환경
python main.py --files src/calc.c src/order.pc
```

---

## 2. 옵션

| 옵션 | 단축 | 기본값 | 설명 |
|------|------|--------|------|
| `--files` | `-f` | **(필수)** | 분석할 파일 경로 (1개 이상, glob 지원) |
| `--output` | `-o` | `./output` | 결과 출력 디렉토리 |
| `--model` | `-m` | `gpt-4o` | LLM 모델명 |
| `--explain-plan` | `-e` | `None` | Explain Plan 결과 파일 경로 (SQL 분석 시 사용) |
| `--verbose` | `-v` | `false` | 상세 로그 출력 |
| `--version` | | | 버전 정보 출력 |

```bash
# 기본 사용
mider.exe --files src/calc.c src/order.pc src/main.js

# glob 패턴
mider.exe --files "src/**/*.c" "src/**/*.pc"

# 출력 디렉토리 지정
mider.exe --files src/calc.c -o ./reports/0224

# SQL + Explain Plan
mider.exe --files src/db/orders.sql --explain-plan explain_output.txt
```

---

## 3. 터미널 출력

### 진행 상황
```
Mider v0.1.0

[파일] 3개
  src/service/calc.c      (C)
  src/batch/process.pc    (Pro*C)
  src/db/orders.sql       (SQL)

[Phase 0] 파일 분류...        done (0.2s)
[Phase 1] 컨텍스트 수집...    done (1.1s)
[Phase 2] 코드 분석...        [2/3]
  ✓ orders.sql     1 issues   0.8s
  ✓ calc.c         4 issues   8.5s
    process.pc     분석 중...
```

### 분석 결과 (Before/After)
```
[Phase 2] 코드 분석...        done (17.3s)
[Phase 3] 리포트 생성...      done (2.1s)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[CRITICAL] C-001  strcpy 버퍼 오버플로우 위험
  src/service/calc.c:234

  - Before:
    strcpy(dest, src);
  + After:
    strncpy(dest, src, sizeof(dest) - 1);
    dest[sizeof(dest) - 1] = '\0';

  strcpy()는 버퍼 크기를 검증하지 않아 오버플로우 발생 가능.
  strncpy()로 교체하고 NULL 종료 문자를 보장하세요.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[CRITICAL] C-003  NULL 포인터 역참조 위험
  src/service/calc.c:189

  - Before:
    result = ptr->value;
  + After:
    if (ptr != NULL) {
        result = ptr->value;
    }

  malloc 반환값을 검증하지 않고 사용. NULL 체크를 추가하세요.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[HIGH] PC-001  SQLCA 에러 체크 누락
  src/batch/process.pc:89

  - Before:
    EXEC SQL UPDATE ORDERS SET STATUS = :status
             WHERE ORDER_ID = :id;
  + After:
    EXEC SQL UPDATE ORDERS SET STATUS = :status
             WHERE ORDER_ID = :id;
    if (sqlca.sqlcode != 0) {
        EXEC SQL ROLLBACK;
        return sqlca.sqlcode;
    }

  EXEC SQL 후 SQLCA 에러 체크가 누락되어 데이터 무결성 손상 가능.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[MEDIUM] SQL-001  인덱스 억제 패턴
  src/db/orders.sql:15

  - Before:
    WHERE YEAR(created_at) = 2026
  + After:
    WHERE created_at >= '2026-01-01'
      AND created_at < '2027-01-01'

  WHERE 절 함수 사용으로 인덱스 무시. 범위 조건으로 변경하세요.

... (총 7건)
```

### 요약
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  CRITICAL  2    HIGH  3    MEDIUM  1    LOW  1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

배포 판정: 불가 (Critical 2건)

출력: ./output/issue-list.json
      ./output/checklist.json
      ./output/summary.json
```

---

## 4. 출력 파일

| 파일 | 설명 |
|------|------|
| `issue-list.json` | 전체 이슈 (severity 순, Before/After 포함) |
| `checklist.json` | Critical/High 검증 체크리스트 |
| `summary.json` | 통계 요약, 배포 판정 |
| `deployment-checklist.json` | 배포 체크리스트 (섹션별 배포 절차) |

> 스키마 상세: `docs/DATA_SCHEMA.md`

---

## 5. 종료 코드

| 코드 | 의미 |
|------|------|
| `0` | 정상 완료, Critical 없음 |
| `1` | 정상 완료, Critical 있음 (배포 불가) |
| `2` | 실행 오류 (파일 없음, 권한 없음) |
| `3` | LLM API 오류 |

---

## 6. 환경 변수

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `MIDER_API_KEY` | (필수) | OpenAI API 키 |
| `MIDER_API_BASE` | `https://api.openai.com/v1` | API Base URL (폐쇄망 프록시) |
| `MIDER_MODEL` | `gpt-4o` | 기본 LLM 모델 |
| `MIDER_LOG_LEVEL` | `INFO` | 로그 레벨 |

---

## 7. 2차 PoC 예정

- `--resume`: 세션 재개
- `--severity`: 심각도 필터
- `--no-static`: 정적 분석 건너뛰기
- `--timeout`: 파일당 타임아웃
- 세션 자동 저장/복구
