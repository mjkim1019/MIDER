# Mider 사용자 매뉴얼

> 폐쇄망 소스코드 분석 도구 — 장애 유발 패턴 사전 탐지

---

## 1. 서비스 개요 및 대상 사용자

### 1.1 서비스 개요

**Mider**는 폐쇄망 환경에서 소스코드의 장애 유발 패턴을 배포 전에 사전 탐지하는 CLI 도구입니다.

정적 분석 도구(ESLint, clang-tidy, Oracle proc)와 LLM(대규모 언어 모델)을 결합한 **하이브리드 분석** 방식으로, 단순 문법 오류뿐 아니라 메모리 누수, 버퍼 오버플로우, SQL 성능 저하 등 **실제 장애로 이어질 수 있는 패턴**을 탐지합니다.

분석 결과는 심각도별로 분류되며, **Before/After 코드 수정 제안**과 **배포 가능 여부 판정**을 함께 제공하여 개발자가 즉시 조치할 수 있도록 돕습니다.

| 항목 | 내용 |
|------|------|
| 서비스명 | Mider |
| 실행 형태 | CLI 단일 실행파일 (`mider.exe`) |
| 분석 방식 | 정적 분석 + LLM 하이브리드 (Multi-Agent) |
| 지원 언어 | JavaScript, C, Pro\*C, SQL, XML (Proframe) |
| 실행 환경 | Windows / Linux (폐쇄망 지원) |

### 1.2 대상 사용자

| 사용자 유형 | 활용 방식 |
|-------------|-----------|
| **주니어 개발자 / 신규 인력** | 시니어의 도움 없이 코드를 선(先)리뷰하여 자율적으로 품질 개선 |
| **시니어 개발자 / 운영자** | 반복적인 오류 패턴 지적에 소모하는 리뷰 시간 절감, 비즈니스 로직 검토에 집중 |
| **운영 담당자** | 배포 판정(가능/주의/위험) 결과로 배포 전 위험도를 객관적으로 판단 |

---ㅌㅈ

## 2. 주요 기능

### 2.1 하이브리드 분석 (정적 분석 + AI)

언어별 정적 분석 도구로 규칙 기반 패턴을 먼저 탐지한 뒤, LLM이 코드 맥락을 이해하여 정적 분석만으로는 발견하기 어려운 심층 이슈를 추가 탐지합니다.

| 언어 | 정적 분석 도구 | AI 심층 분석 항목 |
|------|----------------|-------------------|
| JavaScript | ESLint | XSS, eval 사용, 미처리 예외, DOM 조작 취약점, 메모리 누수 |
| C | clang-tidy + CHeuristicScanner | 버퍼 오버플로우, 포인터 안전성, 메모리 누수, 변수 미초기화 |
| Pro\*C | Oracle proc + ProCHeuristicScanner | EXEC SQL 오류 처리, SQLCA 체크 누락, 커서 관리, INDICATOR 누락 |
| SQL | sqlparse + AstGrepSearch | 인덱스 억제 패턴, Full Table Scan, 문법 오류, Explain Plan 해석 |
| XML | XMLParser | 인라인 JS 취약점, 중복 ID, 데이터 정의 오류 |

### 2.2 Before/After 코드 수정 제안

발견된 모든 이슈에 대해 **수정 전(Before) / 수정 후(After) 코드**를 제공하여, 개발자가 구체적인 수정 방향을 즉시 파악할 수 있습니다.

```
[CRITICAL] C-001  strcpy 버퍼 오버플로우 위험
  src/service/calc.c:234

  - Before:
    strcpy(dest, src);
  + After:
    strncpy(dest, src, sizeof(dest) - 1);
    dest[sizeof(dest) - 1] = '\0';
```

### 2.3 심각도 분류 및 배포 판정

이슈를 4단계 심각도(Critical / High / Medium / Low)로 분류하고, 심각도 집계 결과에 따라 **배포 가능 여부를 자동 판정**합니다. 자세한 판정 기준은 [10. 심각도 및 배포 판정 기준](#10-심각도-및-배포-판정-기준)을 참고하세요.

### 2.4 다중 출력 리포트

분석 결과를 용도별로 분리된 5개 파일로 출력합니다.

| 출력 파일 | 용도 |
|-----------|------|
| `issue-list.json` | 전체 이슈 상세 (위치, 수정 제안 포함) |
| `checklist.json` | Critical/High 이슈 기반 검증 체크리스트 |
| `summary.json` | 심각도 통계 + 배포 위험도 판정 |
| `deployment-checklist.json` | 파일 유형별 배포 절차 체크리스트 |
| `report.md` | 전체 분석 결과 Markdown 리포트 |

### 2.5 폐쇄망 제로 설정 실행

- 외부 인터넷 접속 없이 단일 실행파일(`mider.exe`)만으로 즉시 실행
- API 키, 설정 파일, 정적 분석 바이너리가 모두 실행파일 내부에 포함
- `pip install`, `npm install` 등 별도 설치 불필요

---

## 3. 대표 사용자 흐름 및 주요 사용 시나리오

### 3.1 분석 흐름

Mider의 분석은 4단계(Phase 0~3)로 자동 진행됩니다.

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Phase 0        │     │  Phase 1        │     │  Phase 2        │     │  Phase 3        │
│  파일 분류      │────→│  컨텍스트 수집  │────→│  코드 분석      │────→│  리포트 생성    │
│                 │     │                 │     │                 │     │                 │
│ · 언어 식별     │     │ · import/include│     │ · 정적 분석     │     │ · 이슈 목록     │
│ · 우선순위 결정 │     │   추적          │     │ · LLM 심층 분석 │     │ · 체크리스트    │
│ · 의존성 분석   │     │ · 함수 호출 매핑│     │ · 이슈 탐지     │     │ · 배포 판정     │
└─────────────────┘     └─────────────────┘     └─────────────────┘     └─────────────────┘
```

1. **Phase 0 — 파일 분류**: 입력 파일의 언어를 자동 식별하고, 의존성을 분석하여 분석 순서를 결정합니다.
2. **Phase 1 — 컨텍스트 수집**: import/include 관계, 함수 호출 패턴 등 코드 맥락 정보를 수집합니다.
3. **Phase 2 — 코드 분석**: 언어별 전문 분석기가 정적 분석 + LLM 심층 분석을 수행하여 이슈를 탐지합니다.
4. **Phase 3 — 리포트 생성**: 탐지된 이슈를 통합하고, 심각도별 정렬 + 배포 판정 + 체크리스트를 생성합니다.

### 3.2 주요 사용 시나리오

#### 시나리오 1: 배포 전 코드 리뷰 (가장 일반적)

개발자가 수정한 파일을 배포하기 전, 장애 위험 패턴을 사전에 점검합니다.

```
1. input/ 폴더에 수정한 파일을 넣는다
2. mider.exe 실행 → 파일명 입력
3. 분석 결과 확인:
   - "배포 판정: 가능" → 배포 진행
   - "배포 판정: 위험" → Critical 이슈 수정 후 재분석
```

#### 시나리오 2: 다건 파일 일괄 분석

하나의 업무에서 여러 파일을 동시에 수정한 경우, 쉼표로 구분하여 한 번에 분석합니다.

```
> ordsb0100010t01.c, zinvbpre01140.pc, zord_svc_f101.sql

→ 3개 파일을 각 언어별 전문 분석기로 동시 분석
→ 통합 리포트에서 전체 이슈를 심각도순으로 확인
```

#### 시나리오 3: SQL Explain Plan 연동 분석

SQL 파일을 분석할 때 Explain Plan 결과 파일이 있으면, 실행 계획 기반 성능 튜닝 포인트까지 분석합니다. SQL 파일이 포함되면 자동으로 Explain Plan 파일을 물어봅니다.

```
> zord_svc_f101.sql

ℹ SQL 파일이 포함되어 있습니다.
  Explain Plan 파일이 있으면 입력하세요 (없으면 Enter):
> explain_output.txt
```

Explain Plan 파일이 없으면 Enter를 누르면 됩니다. Explain Plan 없이도 SQL 분석은 정상 동작합니다.


| 종료 코드 | 의미 | 파이프라인 동작 |
|-----------|------|-----------------|
| `0` | 정상 완료, Critical 없음 | 배포 계속 |
| `1` | 정상 완료, Critical 발견 | 배포 중단 |
| `2` | 파일 오류 | 배포 중단 + 알림 |
| `3` | LLM API 오류 | 배포 중단 + 알림 |

---

## 4. 관련 문서

| 문서 | 경로 | 설명 |
|------|------|------|
| PRD | [docs/PRD.md](PRD.md) | 프로젝트 배경, 목표, 핵심 기능, 기대 효과 |
| 기술 설계서 | [docs/TECH_SPEC.md](TECH_SPEC.md) | Agent 상세 설계, 워크플로우, 프롬프트 구조 |
| CLI 명세 | [docs/CLI_SPEC.md](CLI_SPEC.md) | 실행 옵션, 터미널 출력 형식, 종료 코드 |
| 데이터 스키마 | [docs/DATA_SCHEMA.md](DATA_SCHEMA.md) | Pydantic 모델 정의, JSON 출력 스키마 |
| 시스템 아키텍처 | [docs/architecture/system_overview.md](architecture/system_overview.md) | 전체 시스템 구조, Agent/Tool 구성, 데이터 흐름 |
| 분석 워크플로우 | [docs/workflow-summary.md](workflow-summary.md) | Mermaid 다이어그램 기반 분석 흐름 시각화 |
| 오류 탐지 전략 | [docs/quality/error_detection_strategy.md](quality/error_detection_strategy.md) | 이중 필터링 전략 (Low 등급 억제) |

---

## 5. 배포 파일

배포받는 파일은 `mider.exe` **단일 실행파일** 하나입니다.

- API 키, 설정 파일, 리소스가 모두 실행파일 내부에 포함되어 있습니다.
- 별도 설치나 환경 설정 없이 바로 사용할 수 있습니다.

```
mider.exe    ← 이 파일 하나만 배포
```

---

## 6. 초기 설정

별도의 초기 설정이 필요 없습니다. API 키가 실행파일 내부에 포함되어 있습니다.

> API 키 변경이 필요한 경우 관리자에게 새 실행파일을 요청하세요.

---

## 7. 사용 방법

### 7.1 기본 사용법

1. 분석할 파일을 `mider.exe`와 같은 폴더의 `input/` 폴더에 넣습니다.
2. `mider.exe`를 **더블클릭**하여 실행합니다.
3. 분석할 파일명을 입력합니다 (여러 파일은 쉼표로 구분):

**단건 분석:**

```
Mider v1.0.0

분석하고자 하는 소스파일을 입력해주세요. 예시) ordsb0100010t01.c
> ordsb0100010t01.c

분석을 시작합니다...
```

**다건 분석 (쉼표로 구분):**

```
Mider v1.0.0

분석하고자 하는 소스파일을 입력해주세요. 예시) ordsb0100010t01.c
> ordsb0100010t01.c, zinvbpre01140.pc, zord_svc_f101.sql

ℹ SQL 파일이 포함되어 있습니다.
  Explain Plan 파일이 있으면 입력하세요 (없으면 Enter):
>

분석을 시작합니다...
```

### 7.2 지원 파일 형식

| 확장자 | 언어 | 분석 내용 |
|--------|------|-----------|
| `.js` | JavaScript | XSS, eval 사용, 미처리 예외, DOM 조작 취약점 |
| `.c`, `.h` | C | 버퍼 오버플로우, 메모리 누수, 초기화되지 않은 변수 |
| `.pc` | Pro*C | EXEC SQL 오류 처리, 커서 관리, 호스트 변수 불일치 |
| `.sql` | SQL | 문법 오류, 성능 저하 패턴, 인덱스 미사용 |
| `.xml` | XML (Proframe) | 인라인 JS 취약점, 중복 ID, 데이터 정의 오류 |

### 7.3 분석 완료

분석이 완료되면 `output/` 폴더(기본값: 실행 위치의 `./output`)에 결과 파일이 생성되고, 터미널에 요약이 출력됩니다:

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

## 8. 사용 예시

### 8.1 단건 분석

```
Mider v1.0.0

분석하고자 하는 소스파일을 입력해주세요. 예시) ordsb0100010t01.c
> ordsb0100010t01.c

분석을 시작합니다...

[파일] 1개
  ordsb0100010t01.c      (C)

[Phase 0] 파일 분류...        done (1.2s)
[Phase 1] 컨텍스트 수집...    done (2.3s)
[Phase 2] 코드 분석...        done (12.1s)
[Phase 3] 리포트 생성...      done (3.5s)

  CRITICAL  0    HIGH  1    MEDIUM  2    LOW  1

배포 판정: 가능 (LOW)
```

### 8.2 다건 분석

```
Mider v1.0.0

분석하고자 하는 소스파일을 입력해주세요. 예시) ordsb0100010t01.c
> ordsb0100010t01.c, zinvbpre01140.pc, zord_svc_f101.sql

ℹ SQL 파일이 포함되어 있습니다.
  Explain Plan 파일이 있으면 입력하세요 (없으면 Enter):
>

[파일] 3개
  ordsb0100010t01.c      (C)
  zinvbpre01140.pc       (Pro*C)
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

## 9. 출력 결과

분석 완료 후 `output/` 폴더(기본: `./output`, `-o` 옵션으로 변경 가능)에 다음 파일이 생성됩니다:

| 파일명 | 내용 |
|--------|------|
| `{파일명}_{일시}_issue-list.json` | 발견된 이슈 목록 (심각도, 위치, 수정 제안 포함) |
| `{파일명}_{일시}_checklist.json` | 코드 리뷰 체크리스트 |
| `{파일명}_{일시}_summary.json` | 심각도별 통계, 배포 위험도 판정 |
| `{파일명}_{일시}_deployment-checklist.json` | 배포 전 확인 체크리스트 |
| `{파일명}_{일시}_report.md` | 전체 분석 결과 Markdown 리포트 |

### 9.1 issue-list.json 구조

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

### 9.2 summary.json 구조

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

## 10. 심각도 및 배포 판정 기준

### 10.1 심각도 등급

| 등급 | 설명 | 예시 |
|------|------|------|
| **Critical** | 즉시 수정 필요, 장애 직결 | 버퍼 오버플로우, SQL 인젝션, 메모리 미해제 |
| **High** | 우선 수정 권장, 장애 가능성 높음 | 미처리 예외, 커서 미닫힘, XSS 취약점 |
| **Medium** | 수정 권장, 잠재적 문제 | 비효율 쿼리, 하드코딩된 값, 불필요한 전역변수 |
| **Low** | 참고 사항, 코드 품질 개선 | 코딩 컨벤션 위반, 주석 부재, 네이밍 |

### 10.2 배포 판정

| 판정 | 조건 | 의미 |
|------|------|------|
| **가능 (LOW)** | Critical 0건, High 3건 미만 | 배포 진행 가능 |
| **주의 (MEDIUM)** | Critical 0건, High 3건 이상 | 검토 후 배포 판단 |
| **위험 (CRITICAL)** | Critical 1건 이상 | 수정 후 재분석 필요 |

---

## 11. 환경 변수 (관리자용)

API 키 등 환경 변수는 빌드 시점에 `.env` 파일을 통해 실행파일 내부에 포함됩니다.
일반 사용자는 이 섹션을 신경 쓸 필요가 없습니다.

| 변수 | 필수 | 설명 |
|------|------|------|
| `AZURE_OPENAI_API_KEY` | O (Azure) | Azure OpenAI API 키 |
| `AZURE_OPENAI_ENDPOINT` | O (Azure) | Azure OpenAI 엔드포인트 URL |
| `MIDER_API_KEY` | O (OpenAI) | OpenAI API 키 |
| `MIDER_MODEL` | X | 사용할 LLM 모델명 (기본: settings.yaml 기준) |
| `MIDER_API_BASE` | X | API Base URL (프록시 사용 시) |

> 키 변경 시 `.env` 수정 후 실행파일을 다시 빌드해야 합니다.

---

## 12. FAQ / 문제 해결

### Q: "LLM API 키가 설정되지 않았습니다" 오류

**원인:** 실행파일에 API 키가 포함되지 않았거나 키가 만료되었습니다.

**해결:**
1. 관리자에게 최신 실행파일을 요청하세요.
2. 관리자: `.env` 파일에 유효한 API 키가 설정되어 있는지 확인 후 다시 빌드하세요.

### Q: "파일을 찾을 수 없습니다" 오류

**원인:** 지정한 파일 경로가 잘못되었습니다.

**해결:**
1. 파일명에 오타가 없는지 확인합니다.
2. `input/` 폴더에 파일이 있는지 확인합니다.
3. 파일명만 입력하세요 (경로 불필요): `mider.exe -f app.c`

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
1. `--verbose` 옵션으로 오류 메시지를 확인합니다.
2. `--output` 옵션으로 다른 출력 경로를 지정해봅니다: `mider.exe -f app.c -o .\결과`

### Q: 종료 코드의 의미

| 코드 | 의미 |
|------|------|
| `0` | 정상 완료, Critical 이슈 없음 |
| `1` | 정상 완료, Critical 이슈 발견 (배포 위험) |
| `2` | 파일 오류 (파일 없음, 읽기 실패 등) |
| `3` | LLM API 오류 (키 없음, 연결 실패 등) |

---

## 13. 분석 리포트 활용

### 13.1 report.md

가장 읽기 쉬운 분석 결과입니다. 브라우저나 Markdown 뷰어로 열어보세요:
- 이슈 목록 (심각도별 정렬)
- Before/After 코드 비교
- 체크리스트
- 배포 판정 요약

