# 챕터 2: Tool 구현 매뉴얼

> 상세 설계: `docs/TECH_SPEC.md` | 프로젝트 구조: `tools/`

---

## 2.1 Tool 인터페이스

모든 Tool은 아래 패턴을 따른다:

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel

class ToolResult(BaseModel):
    success: bool
    data: dict
    error: str | None = None

class BaseTool(ABC):
    """모든 Tool의 기본 클래스"""

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        pass
```

## 2.2 Tool 분류 및 위치

```
tools/
├── file_io/
│   └── file_reader.py         # 파일 읽기
├── search/
│   ├── grep.py                # 패턴 검색
│   ├── glob.py                # 파일 검색
│   └── ast_grep_search.py     # AST 기반 검색
├── static_analysis/
│   ├── eslint_runner.py       # ESLint (JavaScript)
│   ├── clang_tidy_runner.py   # clang-tidy (C)
│   └── proc_runner.py         # Oracle proc (Pro*C)
├── lsp/
│   └── lsp_client.py          # Language Server Protocol
└── utility/
    ├── sql_extractor.py       # EXEC SQL 블록 추출
    ├── checklist_generator.py # 체크리스트 생성
    ├── task_planner.py        # 실행 계획 생성
    └── dependency_resolver.py # 의존성 해석
```

## 2.3 정적 분석 Tool 규칙

### eslint_runner
- 입력: file_path, config (.eslintrc.json)
- 출력: errors[], warnings[]
- `resources/binaries/` 내 portable node + eslint 사용

### clang_tidy_runner
- 입력: file_path, checks ("-*,clang-analyzer-*,bugprone-*")
- 출력: warnings[]
- `resources/binaries/` 내 portable clang-tidy 사용

### proc_runner
- 입력: file_path, include_dirs
- 출력: errors[], success
- `resources/binaries/` 내 portable proc 사용

## 2.4 Tool 에러 처리

```python
class ToolExecutionError(Exception):
    """Tool 실행 실패 시 raise"""
    def __init__(self, tool_name: str, message: str):
        self.tool_name = tool_name
        super().__init__(f"[{tool_name}] {message}")
```

- 바이너리 없음 → ToolExecutionError("binary not found")
- 파일 없음 → ToolExecutionError("file not found")
- 타임아웃 → ToolExecutionError("timeout")
- Agent에서 try-except로 잡아 AnalysisResult.error에 기록
