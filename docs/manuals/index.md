# Mider 개발 매뉴얼 — 목차

> **반드시 이 목차를 먼저 읽고**, 필요한 챕터만 찾아가세요.

---

## 챕터 목록

### 1. [Agent 구현](agents.md)
- BaseAgent 패턴, Agent 생성 규칙
- LLM 호출 패턴 (OpenAI API)
- 프롬프트 관리 (config/prompts/)
- Phase별 Agent 역할
- Agent 간 데이터 전달 규칙

### 2. [Tool 구현](tools.md)
- Tool 인터페이스 규칙
- 정적 분석 Tool (eslint_runner, clang_tidy_runner, proc_runner)
- 검색 Tool (grep, glob, ast_grep_search)
- 유틸리티 Tool (sql_extractor, checklist_generator, dependency_resolver)
- Tool 에러 처리 패턴

### 3. [데이터 스키마](models.md)
- Pydantic v2 모델 규칙
- ExecutionPlan, FileContext, AnalysisResult
- IssueList, Checklist, Summary
- 스키마 간 키 매칭 규칙

### 4. [테스트](testing.md)
- pytest 구조 및 컨벤션
- Agent 테스트 패턴 (LLM mock)
- Tool 테스트 패턴
- 통합 테스트

### 5. [보안 체크](security.md)
- 메모리 안전성 (C: malloc/free, strcpy, buffer)
- XSS/Injection (JS: innerHTML, eval)
- 데이터 무결성 (Pro*C: SQLCA, INDICATOR)
- API 키/비밀 정보 관리

---

## 매뉴얼 사용 규칙

1. **항상 이 index.md를 먼저 읽는다**
2. 목차에서 해당 챕터를 확인한다
3. 필요한 챕터 파일만 Read tool로 읽는다
4. 한 번에 2개 이상 챕터를 읽지 않는다 (컨텍스트 절약)

## 자동 활성화 조건

| 조건 | 챕터 |
|------|------|
| agents/*.py 수정 | agents.md |
| tools/*.py 수정 | tools.md |
| models/*.py 수정 | models.md |
| tests/*.py 수정 | testing.md |
| malloc, strcpy, innerHTML 등 코드 패턴 감지 | security.md |
| BaseModel, pydantic 코드 패턴 감지 | models.md |
| openai, ChatCompletion 코드 패턴 감지 | agents.md |
