# Mider

폐쇄망 소스코드 분석 CLI. JS/C/Pro*C/SQL 코드의 장애 유발 패턴을 사전 탐지합니다.

## 빠른 시작

### 1. 가상환경 생성 및 패키지 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어서 API 키를 입력합니다:

**Azure OpenAI (옵션 1)**
```env
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-12-01-preview
MIDER_MODEL=gpt-4o
```

**OpenAI 직접 사용 (옵션 2)**
```env
MIDER_API_KEY=sk-your-key
MIDER_MODEL=gpt-4o
```

### 3. 실행

```bash
# 가상환경 활성화 상태에서
mider -f path/to/file.c

# 여러 파일
mider -f file1.c file2.js query.sql

# 상세 로그
mider -f file.c -v

# 출력 디렉토리 지정
mider -f file.c -o ./reports

# SQL + Explain Plan
mider -f query.sql -e explain_plan.txt
```

### 4. 출력

결과는 `./output/` (또는 `-o`로 지정한 디렉토리)에 JSON으로 생성됩니다:

| 파일 | 내용 |
|------|------|
| `issue-list.json` | 발견된 이슈 목록 (severity, before/after) |
| `checklist.json` | 코드 리뷰 체크리스트 |
| `summary.json` | 심각도별 요약 + 배포 판정 |
| `deployment-checklist.json` | 배포 체크리스트 |

## 개발

### 테스트 실행

```bash
source .venv/bin/activate
pip install -e .
pip install pytest pytest-asyncio

# 전체 테스트
pytest

# 특정 테스트
pytest tests/test_agents/test_sql_analyzer.py -v

# 상세 출력
pytest -v --tb=short
```

### 수동 테스트 (샘플 파일)

```bash
# C 파일 분석
mider -f tests/fixtures/sample_skb/ordsb0100010t01.c -v

# 모델 지정
mider -f tests/fixtures/sample_skb/ordsb0100010t01.c -m gpt-4o-mini
```

### CLI 옵션

| 옵션 | 축약 | 설명 | 기본값 |
|------|------|------|--------|
| `--files` | `-f` | 분석할 파일 (필수, 복수 가능) | - |
| `--output` | `-o` | 출력 디렉토리 | `./output` |
| `--model` | `-m` | LLM 모델명 | `gpt-4o` |
| `--explain-plan` | `-e` | Explain Plan 파일 (SQL용) | - |
| `--verbose` | `-v` | 상세 로그 | `false` |
| `--version` | | 버전 출력 | - |

### 종료 코드

| 코드 | 의미 |
|------|------|
| 0 | 정상 완료, Critical 없음 |
| 1 | 정상 완료, Critical 있음 (배포 불가) |
| 2 | 파일 오류 |
| 3 | LLM API 오류 |

## 지원 언어

| 확장자 | 언어 | 분석 도구 |
|--------|------|-----------|
| `.js` | JavaScript | ESLint + LLM |
| `.c`, `.h` | C | clang-tidy + LLM |
| `.pc` | Pro*C | Oracle proc + LLM |
| `.sql` | SQL | sqlparse + Explain Plan + LLM |
