# 챕터 3: 데이터 스키마 매뉴얼

> 전체 스키마: `docs/DATA_SCHEMA.md`

---

## 3.1 Pydantic v2 규칙

```python
from pydantic import BaseModel
from typing import Literal, Optional
from datetime import datetime

# 모든 모델은 BaseModel 상속
class MyModel(BaseModel):
    field: str
    optional_field: Optional[str] = None
    literal_field: Literal["a", "b"]
```

### 파일 구성
- `models/__init__.py` — 모든 모델 re-export
- 1 스키마 = 1 파일
- 파일명: snake_case (예: `execution_plan.py`)

### 금지 패턴
- `dict` 타입 직접 사용 금지 → 명시적 필드 정의
- `Any` 타입 최소화
- 기본값이 있는 필드는 뒤에 배치

## 3.2 핵심 스키마 요약

| 스키마 | Phase | 생성 Agent | 소비 Agent |
|--------|-------|-----------|-----------|
| ExecutionPlan | 0→1 | TaskClassifier | Orchestrator, ContextCollector |
| FileContext | 1→2 | ContextCollector | JS/C/ProC/SQL Analyzer |
| AnalysisResult | 2→3 | Analyzers | Reporter |
| IssueList | 3 (출력) | Reporter | (파일 출력) |
| Checklist | 3 (출력) | Reporter | (파일 출력) |
| Summary | 3 (출력) | Reporter | (파일 출력) |

## 3.3 키 매칭 규칙

```
ExecutionPlan.sub_tasks[].task_id  ←→  AnalysisResult.task_id
ExecutionPlan.sub_tasks[].file     ←→  FileContext.file_contexts[].file
AnalysisResult.issues[].issue_id   ←→  Checklist.items[].related_issues[]
```

## 3.4 Issue ID 규칙

- JavaScript: `JS-001`, `JS-002`...
- C: `C-001`, `C-002`...
- Pro*C: `PC-001`, `PC-002`...
- SQL: `SQL-001`, `SQL-002`...

## 3.5 Severity 기준

| 심각도 | 기준 | 예시 |
|--------|------|------|
| critical | 즉시 크래시, 데이터 손상, 보안 취약점 | 버퍼 오버플로우, NULL 역참조 |
| high | 특정 조건에서 장애 | SQLCA 체크 누락, 커서 누수 |
| medium | 성능 저하 | Full Table Scan, 인덱스 억제 |
| low | 코드 품질 | 미사용 변수, 컨벤션 위반 |
