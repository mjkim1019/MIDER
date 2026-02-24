# CLI 인터페이스 명세: Mider

> `mider` CLI 명령어, 옵션, 출력 형식 정의

---

## 1. 설치 및 실행

### 폐쇄망 환경 (실행파일)
```bash
# Windows
mider.exe analyze --files src/calc.c src/order.pc

# Linux
./mider analyze --files src/calc.c src/order.pc
```

### 개발 환경 (Python)
```bash
python cli.py analyze --files src/calc.c src/order.pc
```

---

## 2. 명령어 구조

```
mider <command> [options]
```

| 명령어 | 설명 |
|--------|------|
| `analyze` | 파일 분석 실행 |
| `resume` | 중단된 세션 재개 |
| `sessions` | 세션 목록 조회 |
| `version` | 버전 정보 출력 |

---

## 3. `analyze` — 파일 분석

### 사용법
```bash
mider analyze --files <file1> [file2 ...] [options]
```

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

### 와일드카드 지원
```bash
# glob 패턴 지원
mider analyze --files "src/**/*.c" "src/**/*.pc"

# 단일 파일
mider analyze --files src/service/calc.c
```

### 실행 예시
```bash
# 기본 분석
mider analyze --files src/calc.c src/order.pc src/main.js

# Critical/High만 출력
mider analyze --files src/calc.c --severity high

# 다른 모델 사용
mider analyze --files src/calc.c --model gpt-4-turbo

# 상세 로그
mider analyze --files src/calc.c -v

# 출력 디렉토리 지정
mider analyze --files src/calc.c --output ./reports/2026-02-24
```

---

## 4. `resume` — 세션 재개

### 사용법
```bash
mider resume --session <session_id>
```

### 옵션

| 옵션 | 단축 | 설명 |
|------|------|------|
| `--session` | `-s` | 재개할 세션 ID |

### 실행 예시
```bash
mider resume --session 20260224_153000
```

### 동작
1. `./sessions/session_{session_id}.json` 파일 로드
2. `current_phase`와 `completed_tasks` 확인
3. 중단된 Phase/Task부터 이어서 분석 재개
4. 기존 결과에 추가로 누적

---

## 5. `sessions` — 세션 목록

### 사용법
```bash
mider sessions [options]
```

### 옵션

| 옵션 | 단축 | 기본값 | 설명 |
|------|------|--------|------|
| `--limit` | `-l` | `10` | 표시할 세션 수 |
| `--status` | | `all` | 필터 (`running`, `paused`, `completed`, `failed`, `all`) |

### 출력 예시
```
 Session ID          Status     Phase    Files  Issues  Created
──────────────────────────────────────────────────────────────────
 20260224_153000     completed  phase_3  5      7       2026-02-24 15:30
 20260224_100000     paused     phase_2  3      2       2026-02-24 10:00
 20260223_140000     failed     phase_2  4      0       2026-02-23 14:00
```

---

## 6. 터미널 출력 형식

### 분석 진행 중
```
🔍 Mider v0.1.0 — 소스코드 분석 시작

📂 대상 파일: 3개
   ├── src/service/calc.c (C)
   ├── src/batch/process.pc (Pro*C)
   └── src/db/orders.sql (SQL)

⏳ Phase 0: 파일 분류 중...
   ✓ Phase 0 완료 — ExecutionPlan 생성 (3 tasks)

⏳ Phase 1: 컨텍스트 수집 중...
   ✓ Phase 1 완료 — FileContext 생성

⏳ Phase 2: 코드 분석 중... [2/3]
   ├── ✓ task_1: orders.sql (SQL) — 1 issues (0.8s)
   ├── ✓ task_2: calc.c (C) — 4 issues (8.5s)
   └── ⏳ task_3: process.pc (Pro*C) — 분석 중...
```

### 분석 완료
```
   ✓ Phase 2 완료 — 3개 파일 분석 (17.3s)

⏳ Phase 3: 리포트 생성 중...
   ✓ Phase 3 완료

════════════════════════════════════════════════════
📊 분석 결과 요약
════════════════════════════════════════════════════

  심각도        건수
  ─────────────────
  🔴 Critical   2
  🟠 High       3
  🟡 Medium     1
  🟢 Low        1
  ─────────────────
  합계          7

🚫 배포 판정: 불가 (Critical 2건)
   └── C-001: strcpy 버퍼 오버플로우 위험 (calc.c:234)
   └── C-003: NULL 포인터 역참조 위험 (calc.c:189)

⏱️  예상 수정 시간: 90분 (Critical: 30분, High: 45분)

📁 출력 파일:
   ├── ./output/issue-list.json
   ├── ./output/checklist.json
   └── ./output/summary.json

💾 세션 저장: 20260224_153000
   └── Resume: mider resume --session 20260224_153000
```

### 중단 시
```
⚠️  분석 중단됨 (Ctrl+C 감지)

💾 세션 저장: 20260224_153000
   ├── 현재 Phase: phase_2
   ├── 완료된 Task: 2/3
   └── Resume: mider resume --session 20260224_153000
```

---

## 7. 종료 코드

| 코드 | 의미 |
|------|------|
| `0` | 정상 완료, Critical 이슈 없음 |
| `1` | 정상 완료, Critical 이슈 있음 (배포 불가) |
| `2` | 실행 오류 (파일 없음, 권한 없음 등) |
| `3` | LLM API 오류 (연결 실패, 토큰 초과 등) |
| `130` | 사용자 중단 (Ctrl+C) |

---

## 8. 환경 변수

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `MIDER_API_KEY` | (필수) | OpenAI API 키 |
| `MIDER_API_BASE` | `https://api.openai.com/v1` | API Base URL (폐쇄망 프록시) |
| `MIDER_MODEL` | `gpt-4o` | 기본 LLM 모델 |
| `MIDER_OUTPUT_DIR` | `./output` | 기본 출력 디렉토리 |
| `MIDER_SESSION_DIR` | `./sessions` | 세션 저장 디렉토리 |
| `MIDER_LOG_LEVEL` | `INFO` | 로그 레벨 (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `MIDER_TIMEOUT` | `300` | 파일당 분석 타임아웃 (초) |
