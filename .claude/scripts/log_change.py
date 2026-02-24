#!/usr/bin/env python3
"""
수정 기록 Hook (PostToolUse: Edit, Write)
파일 변경 시 docs/quality/changelog.md에 자동 기록한다.
"""
import sys
import json
import os
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHANGELOG = os.path.join(PROJECT_ROOT, "docs", "quality", "changelog.md")


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        return

    # 시스템 파일은 기록 제외
    skip_patterns = ["docs/quality/changelog.md", "docs/worklog/", ".claude/"]
    if any(p in file_path for p in skip_patterns):
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 브랜치명 가져오기
    try:
        import subprocess
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        branch = "unknown"

    entry = f"- `{timestamp}` [{branch}] **{tool_name}**: `{file_path}`\n"

    os.makedirs(os.path.dirname(CHANGELOG), exist_ok=True)

    # 파일이 없으면 헤더 추가
    if not os.path.exists(CHANGELOG):
        with open(CHANGELOG, "w", encoding="utf-8") as f:
            f.write("# 수정 기록 (Changelog)\n\n")
            f.write("> 자동 생성됨. Hook(PostToolUse)이 파일 수정 시마다 기록합니다.\n\n")

    with open(CHANGELOG, "a", encoding="utf-8") as f:
        f.write(entry)


if __name__ == "__main__":
    main()
