#!/usr/bin/env python3
"""
자동 매뉴얼 로더 Hook (PreToolUse: Edit, Write)
파일 경로, 코드 패턴을 감지하여 관련 매뉴얼을 안내한다.
"""
import sys
import json
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 경로 기반 매뉴얼 매핑
PATH_RULES = {
    "/agents/": "agents",
    "/tools/": "tools",
    "/models/": "models",
    "/tests/": "testing",
    "test_": "testing",
    "/config/prompts/": "agents",
}

# 코드 패턴 기반 매뉴얼 매핑 (CLAUDE.md와 동기화)
PATTERN_RULES = {
    # C / 메모리 안전성 → security
    "malloc": "security",
    "free(": "security",
    "strcpy": "security",
    "sprintf": "security",
    "buffer": "security",
    # Pro*C / SQL → agents (ProC/SQL 분석 규칙)
    "EXEC SQL": "agents",
    "WHENEVER": "agents",
    "sqlca": "agents",
    "INDICATOR": "agents",
    # JavaScript / 보안 → security
    "innerHTML": "security",
    "dangerouslySetInnerHTML": "security",
    "eval(": "security",
    "document.write": "security",
    # Pydantic / 스키마 → models
    "BaseModel": "models",
    "Field(": "models",
    "model_validate": "models",
    "pydantic": "models",
    "AnalysisResult": "models",
    "ExecutionPlan": "models",
    # LLM / OpenAI → agents
    "openai": "agents",
    "ChatCompletion": "agents",
    "system_prompt": "agents",
    # 테스트 → testing
    "pytest": "testing",
    "@pytest.fixture": "testing",
    "assert ": "testing",
    "def test_": "testing",
}

MANUAL_NAMES = {
    "agents": "docs/manuals/agents.md",
    "tools": "docs/manuals/tools.md",
    "models": "docs/manuals/models.md",
    "testing": "docs/manuals/testing.md",
    "security": "docs/manuals/security.md",
}


def detect_manuals(file_path: str) -> list[str]:
    manuals = set()

    # 1. 경로 기반 감지
    for pattern, manual_key in PATH_RULES.items():
        if pattern in file_path:
            manuals.add(manual_key)

    # 2. 파일 내용 기반 감지 (코드 패턴)
    abs_path = file_path if os.path.isabs(file_path) else os.path.join(PROJECT_ROOT, file_path)
    if os.path.exists(abs_path):
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(10000)  # 앞 10KB만 읽기
            for pattern, manual_key in PATTERN_RULES.items():
                if pattern in content:
                    manuals.add(manual_key)
        except (OSError, PermissionError):
            pass

    return [MANUAL_NAMES[k] for k in manuals if k in MANUAL_NAMES]


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        return

    manuals = detect_manuals(file_path)

    if manuals:
        print("[자동 매뉴얼] 이 파일을 수정하기 전, 아래 매뉴얼을 확인하세요.")
        print("  1. 먼저 docs/manuals/index.md (목차)를 읽으세요.")
        print("  2. 그다음 해당 챕터로 이동:")
        for m in manuals:
            print(f"     - {m}")
        print()


if __name__ == "__main__":
    main()
