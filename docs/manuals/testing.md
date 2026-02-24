# 챕터 4: 테스트 매뉴얼

---

## 4.1 테스트 구조

```
tests/
├── conftest.py              # 공통 fixture
├── test_agents/
│   ├── test_orchestrator.py
│   ├── test_task_classifier.py
│   ├── test_context_collector.py
│   ├── test_js_analyzer.py
│   ├── test_c_analyzer.py
│   ├── test_proc_analyzer.py
│   ├── test_sql_analyzer.py
│   └── test_reporter.py
├── test_tools/
│   ├── test_eslint_runner.py
│   ├── test_clang_tidy_runner.py
│   ├── test_proc_runner.py
│   └── test_sql_extractor.py
└── test_models/
    ├── test_execution_plan.py
    └── test_analysis_result.py
```

## 4.2 테스트 실행

```bash
# 전체 테스트
pytest tests/ -v

# 특정 Agent 테스트
pytest tests/test_agents/test_orchestrator.py -v

# 특정 테스트 함수
pytest tests/test_agents/test_orchestrator.py::test_phase_flow -v
```

## 4.3 Agent 테스트 패턴 (LLM Mock)

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.fixture
def mock_llm_response():
    """LLM 응답 mock"""
    return {
        "issues": [
            {
                "issue_id": "C-001",
                "category": "memory_safety",
                "severity": "critical",
                "title": "strcpy 버퍼 오버플로우",
                ...
            }
        ]
    }

@patch("agents.c_analyzer.AsyncOpenAI")
async def test_c_analyzer(mock_openai, mock_llm_response):
    mock_openai.return_value.chat.completions.create = AsyncMock(
        return_value=mock_llm_response
    )
    agent = CAnalyzerAgent(model="gpt-4o")
    result = await agent.run(file_path="test.c", file_context={})
    assert len(result.issues) > 0
    assert result.issues[0].severity == "critical"
```

## 4.4 Tool 테스트 패턴

```python
def test_eslint_runner_with_errors(tmp_path):
    """ESLint가 에러를 정상 반환하는지"""
    js_file = tmp_path / "test.js"
    js_file.write_text("var x = undeclaredVar;")
    result = eslint_runner.execute(file=str(js_file))
    assert result.success
    assert len(result.data["errors"]) > 0

def test_eslint_runner_file_not_found():
    """존재하지 않는 파일 → ToolExecutionError"""
    with pytest.raises(ToolExecutionError):
        eslint_runner.execute(file="/nonexistent.js")
```

## 4.5 테스트 컨벤션

- 테스트 함수명: `test_{기능}_{조건}` (예: `test_classify_js_file`)
- fixture 활용: conftest.py에 공통 샘플 데이터
- LLM 호출은 반드시 mock 처리
- 정적 분석 Tool은 실제 바이너리 있을 때만 실행 (`@pytest.mark.skipif`)
