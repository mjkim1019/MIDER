# Mider 사용자 매뉴얼

> 폐쇄망 소스코드 분석 도구 — 장애 유발 패턴 사전 탐지

---

## 1. 폴더 구조

실행파일을 배포받으면 다음과 같은 폴더 구조를 확인할 수 있습니다:

```
mider/
├── mider.exe          ← 실행파일
├── .env               ← API 키 설정 파일 (직접 생성)
├── .env.example       ← .env 템플릿
├── input/             ← 분석할 소스 파일을 여기에 넣으세요
├── output/            ← 분석 결과가 여기에 생성됩니다
└── _internal/         ← 내부 라이브러리 (수정 금지)
```

| 폴더/파일 | 설명 |
|-----------|------|
| `mider.exe` | 프로그램 실행파일 |
| `.env` | LLM API 키 등 환경 변수 설정 |
| `input/` | 분석 대상 소스 파일 배치 폴더 |
| `output/` | 분석 결과 JSON 및 리포트 출력 폴더 |

---

## 2. 초기 설정

### 2.1 API 키 설정

분석에 사용할 LLM API 키를 설정해야 합니다.

1. `.env.example` 파일을 `.env`로 복사합니다.
2. `.env` 파일을 텍스트 편집기로 열어 API 키를 입력합니다.

**Azure OpenAI 사용 시 (권장):**

```
AZURE_OPENAI_API_KEY=실제-API-키-입력
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
```

**OpenAI 직접 사용 시:**

```
MIDER_API_KEY=sk-실제-API-키-입력
```

> API 키가 설정되지 않으면 프로그램이 시작되지 않습니다.

### 2.2 모델 변경 (선택)

기본 모델은 `settings.yaml`에 설정된 모델을 사용합니다. 변경이 필요하면:

```
MIDER_MODEL=gpt-5
```

---

## 3. 사용 방법

### 3.1 기본 사용법

1. `input/` 폴더에 분석할 소스 파일을 넣습니다.
2. 명령 프롬프트(cmd)를 열고 mider 폴더로 이동합니다.
3. 다음 명령을 실행합니다:

```bash
mider.exe -f 파일명1 파일명2
```

**예시:**

```bash
# 단일 파일 분석
mider.exe -f ordsb0100010t01.c

# 여러 파일 동시 분석
mider.exe -f ordsb0100010t01.c zinvbpre01140.pc zord_svc_f101.sql

# 파일명만 입력하면 input/ 폴더에서 자동으로 찾습니다
mider.exe -f app.js
```

### 3.2 지원 파일 형식

| 확장자 | 언어 | 분석 내용 |
|--------|------|-----------|
| `.js` | JavaScript | XSS, eval 사용, 미처리 예외, DOM 조작 취약점 |
| `.c`, `.h` | C | 버퍼 오버플로우, 메모리 누수, 초기화되지 않은 변수 |
| `.pc` | Pro*C | EXEC SQL 오류 처리, 커서 관리, 호스트 변수 불일치 |
| `.sql` | SQL | 문법 오류, 성능 저하 패턴, 인덱스 미사용 |
| `.xml` | XML (Proframe) | 인라인 JS 취약점, 중복 ID, 데이터 정의 오류 |

### 3.3 분석 완료

분석이 완료되면 `output/` 폴더에 결과 파일이 생성되고, 터미널에 요약이 출력됩니다:

```
Mider v1.0.0

[파일] 2개
  ordsb0100010t01.c      (C)
  zord_svc_f101.sql      (SQL)

[Phase 0] 파일 분류...        done (2.1s)
[Phase 1] 컨텍스트 수집...    done (3.5s)
[Phase 2] 코드 분석...        done (15.2s)
[Phase 3] 리포트 생성...      done (4.8s)

  CRITICAL  1    HIGH  2    MEDIUM  3    LOW  1

배포 판정: 위험 (Critical 1건)
  차단 이슈: C-001
```

---

## 4. CLI 옵션

| 옵션 | 단축 | 설명 | 예시 |
|------|------|------|------|
| `--files` | `-f` | 분석할 파일 (필수, 1개 이상) | `-f app.js calc.c` |
| `--output` | `-o` | 결과 출력 디렉토리 | `-o ./reports` |
| `--model` | `-m` | LLM 모델 지정 | `-m gpt-5` |
| `--explain-plan` | `-e` | SQL Explain Plan 파일 경로 | `-e plan.txt` |
| `--verbose` | `-v` | 상세 로그 출력 | `-v` |
| `--version` | | 버전 정보 출력 | `--version` |

**사용 예시:**

```bash
# 기본 분석
mider.exe -f ordsb0100010t01.c

# 결과를 다른 폴더에 출력
mider.exe -f app.js -o C:\reports

# SQL 분석 + Explain Plan
mider.exe -f query.sql -e query_plan.txt

# 상세 로그와 함께 분석
mider.exe -f app.c -v

# 특정 모델 사용
mider.exe -f app.js -m gpt-5-mini
```

---

## 5. 출력 결과

분석 완료 후 `output/` 폴더에 다음 파일이 생성됩니다:

| 파일명 | 내용 |
|--------|------|
| `{파일명}_{일시}_issue-list.json` | 발견된 이슈 목록 (심각도, 위치, 수정 제안 포함) |
| `{파일명}_{일시}_checklist.json` | 코드 리뷰 체크리스트 |
| `{파일명}_{일시}_summary.json` | 심각도별 통계, 배포 위험도 판정 |
| `{파일명}_{일시}_deployment-checklist.json` | 배포 전 확인 체크리스트 |
| `{파일명}_{일시}_report.md` | 전체 분석 결과 Markdown 리포트 |

### 5.1 issue-list.json 구조

```json
{
  "total_issues": 3,
  "issues": [
    {
      "issue_id": "C-001",
      "severity": "critical",
      "title": "버퍼 오버플로우 위험",
      "file": "app.c",
      "location": { "line_start": 42, "line_end": 42 },
      "description": "strcpy() 사용 시 대상 버퍼 크기 미검증",
      "fix": {
        "before": "strcpy(dest, src);",
        "after": "strncpy(dest, src, sizeof(dest) - 1);"
      }
    }
  ]
}
```

### 5.2 summary.json 구조

```json
{
  "issue_summary": {
    "total_issues": 3,
    "by_severity": { "critical": 1, "high": 1, "medium": 1, "low": 0 }
  },
  "risk_assessment": {
    "deployment_risk": "CRITICAL",
    "deployment_allowed": false,
    "blocking_issues": ["C-001"],
    "risk_description": "Critical 이슈 1건 발견 — 수정 후 재분석 권장"
  }
}
```

---

## 6. 심각도 및 배포 판정 기준

### 6.1 심각도 등급

| 등급 | 설명 | 예시 |
|------|------|------|
| **Critical** | 즉시 수정 필요, 장애 직결 | 버퍼 오버플로우, SQL 인젝션, 메모리 미해제 |
| **High** | 우선 수정 권장, 장애 가능성 높음 | 미처리 예외, 커서 미닫힘, XSS 취약점 |
| **Medium** | 수정 권장, 잠재적 문제 | 비효율 쿼리, 하드코딩된 값, 불필요한 전역변수 |
| **Low** | 참고 사항, 코드 품질 개선 | 코딩 컨벤션 위반, 주석 부재, 네이밍 |

### 6.2 배포 판정

| 판정 | 조건 | 의미 |
|------|------|------|
| **가능 (LOW)** | Critical 0건, High 3건 미만 | 배포 진행 가능 |
| **주의 (MEDIUM)** | Critical 0건, High 3건 이상 | 검토 후 배포 판단 |
| **위험 (CRITICAL)** | Critical 1건 이상 | 수정 후 재분석 필요 |

---

## 7. 환경 변수 설정

`.env` 파일에서 설정할 수 있는 환경 변수:

| 변수 | 필수 | 설명 |
|------|------|------|
| `AZURE_OPENAI_API_KEY` | O (Azure) | Azure OpenAI API 키 |
| `AZURE_OPENAI_ENDPOINT` | O (Azure) | Azure OpenAI 엔드포인트 URL |
| `MIDER_API_KEY` | O (OpenAI) | OpenAI API 키 |
| `MIDER_MODEL` | X | 사용할 LLM 모델명 (기본: settings.yaml 기준) |
| `MIDER_API_BASE` | X | API Base URL (프록시 사용 시) |

> Azure와 OpenAI 중 하나만 설정하면 됩니다.

---

## 8. FAQ / 문제 해결

### Q: "LLM API 키가 설정되지 않았습니다" 오류

**원인:** `.env` 파일이 없거나 API 키가 비어있습니다.

**해결:**
1. `.env.example`을 `.env`로 복사했는지 확인합니다.
2. `.env` 파일에 실제 API 키를 입력했는지 확인합니다.
3. `.env` 파일이 `mider.exe`와 같은 폴더에 있는지 확인합니다.

### Q: "파일을 찾을 수 없습니다" 오류

**원인:** 지정한 파일이 `input/` 폴더에 없습니다.

**해결:**
1. 분석할 파일을 `input/` 폴더에 넣었는지 확인합니다.
2. 파일명에 오타가 없는지 확인합니다.
3. 절대경로를 사용할 수도 있습니다: `mider.exe -f C:\src\app.c`

### Q: "LLM API 오류" 발생

**원인:** API 서버 연결 실패 또는 인증 오류입니다.

**해결:**
1. 네트워크 연결을 확인합니다 (폐쇄망에서는 Azure 엔드포인트 접근 가능 여부).
2. API 키가 유효한지 확인합니다.
3. Azure 엔드포인트 URL이 정확한지 확인합니다.

### Q: 분석 시간이 너무 오래 걸림

**원인:** 파일 크기가 크거나 LLM 응답이 느립니다.

**해결:**
1. 한 번에 분석하는 파일 수를 줄여보세요.
2. `--verbose` 옵션으로 어느 단계에서 지연되는지 확인합니다.
3. 더 빠른 모델을 지정합니다: `mider.exe -f app.c -m gpt-5-mini`

### Q: 출력 파일이 생성되지 않음

**원인:** 분석 중 오류 발생 또는 출력 디렉토리 권한 문제입니다.

**해결:**
1. `output/` 폴더가 존재하고 쓰기 권한이 있는지 확인합니다.
2. `--verbose` 옵션으로 오류 메시지를 확인합니다.
3. `--output` 옵션으로 다른 출력 경로를 지정해봅니다.

### Q: 종료 코드의 의미

| 코드 | 의미 |
|------|------|
| `0` | 정상 완료, Critical 이슈 없음 |
| `1` | 정상 완료, Critical 이슈 발견 (배포 위험) |
| `2` | 파일 오류 (파일 없음, 읽기 실패 등) |
| `3` | LLM API 오류 (키 없음, 연결 실패 등) |

---

## 9. 분석 리포트 활용

### 9.1 report.md

가장 읽기 쉬운 분석 결과입니다. 브라우저나 Markdown 뷰어로 열어보세요:
- 이슈 목록 (심각도별 정렬)
- Before/After 코드 비교
- 체크리스트
- 배포 판정 요약

### 9.2 CI/CD 연동

종료 코드를 활용하여 배포 파이프라인에 통합할 수 있습니다:

```bash
mider.exe -f *.c *.pc
if %ERRORLEVEL% EQU 1 (
    echo Critical 이슈 발견 - 배포 중단
    exit /b 1
)
```
