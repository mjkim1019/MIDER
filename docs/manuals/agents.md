# 챕터 1: Agent 구현 매뉴얼

> 상세 설계: `docs/TECH_SPEC.md` | 스키마: `docs/DATA_SCHEMA.md`

---

## 1.1 BaseAgent 패턴

모든 Agent는 아래 인터페이스를 따른다:

```python
from abc import ABC, abstractmethod
from models.analysis_result import AnalysisResult

class BaseAgent(ABC):
    """모든 Agent의 기본 클래스"""

    def __init__(self, model: str, fallback_model: str | None = None, temperature: float = 0.0):
        self.model = model
        self.fallback_model = fallback_model
        self.temperature = temperature

    @abstractmethod
    async def run(self, **kwargs) -> dict:
        """Agent 실행. 하위 클래스에서 구현"""
        pass

    async def call_llm(self, messages: list[dict], json_mode: bool = True) -> str:
        """LLM API 호출 (재시도 포함)"""
        # 최대 3회 재시도
        # 실패 시 fallback_model 사용
        # json_mode=True이면 response_format={"type": "json_object"}
        pass
```

## 1.2 Agent 생성 규칙

- 파일 위치: `agents/{agent_name}.py`
- 클래스명: PascalCase (예: `OrchestratorAgent`)
- 1 Agent = 1 파일
- 생성자에서 model, fallback_model, temperature 설정
- `run()` 메서드가 유일한 공개 인터페이스

## 1.3 LLM 호출 패턴

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key=os.environ["MIDER_API_KEY"],
    base_url=os.environ.get("MIDER_API_BASE", "https://api.openai.com/v1"),
)

response = await client.chat.completions.create(
    model=self.model,
    messages=messages,
    temperature=self.temperature,
    response_format={"type": "json_object"},  # Structured Output
)
```

### 재시도 로직
```python
for attempt in range(3):
    try:
        return await self._call(messages)
    except Exception as e:
        if attempt == 2 and self.fallback_model:
            self.model = self.fallback_model
            return await self._call(messages)
        raise
```

## 1.4 프롬프트 관리

- 프롬프트 파일 위치: `config/prompts/{agent_name}_{variant}.txt`
- 예: `config/prompts/js_analyzer_error_focused.txt`
- 프롬프트 로딩: 파일에서 읽어서 f-string 변수 치환
- **프롬프트를 코드에 하드코딩하지 않는다**

## 1.5 Phase별 Agent 역할

| Phase | Agent | 입력 | 출력 |
|-------|-------|------|------|
| 0 | TaskClassifierAgent | 파일 목록 | ExecutionPlan |
| 1 | ContextCollectorAgent | ExecutionPlan | FileContext |
| 2 | JS/C/ProC/SQL Analyzer | 파일 + FileContext | AnalysisResult |
| 3 | ReporterAgent | List[AnalysisResult] | IssueList, Checklist, Summary |

## 1.6 Agent 간 데이터 전달

- 모든 Agent 출력은 Pydantic 모델로 직렬화 (JSON)
- OrchestratorAgent가 중간 결과를 메모리에 보관하고 다음 Agent에 전달
- Agent는 자신의 입출력 스키마만 알면 된다 (다른 Agent 내부 로직 불필요)
