"""healthcheck: 도구 상태 점검 모듈.

mider --check 명령으로 호출된다.
LLM 없이 ESLint, clang-tidy 등 모든 도구 바이너리의 동작을 검증한다.
"""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

logger = logging.getLogger(__name__)

# mider 패키지 루트 (mider/ 디렉토리)
_PACKAGE_DIR = Path(__file__).parent

# 테스트용 JavaScript 코드 (ESLint no-eval 룰 위반)
_TEST_JS = """\
var x = eval("1+1");
"""

# 테스트용 C 코드 (초기화되지 않은 변수 사용)
_TEST_C = """\
int main(void) {
    int x;
    int y = x + 1;
    return y;
}
"""


def run_healthcheck(console: Console) -> int:
    """전체 도구 상태를 점검하고 결과를 출력한다.

    Returns:
        0: 정적분석 도구 모두 정상
        1: 하나 이상의 도구 실패
    """
    results: list[tuple[str, str, str]] = []  # (status, name, detail)

    results.append(_check_node())
    results.append(_check_eslint())
    results.append(_check_clang_tidy())
    results.append(_check_configs())
    results.append(_check_chrome())
    results.append(_check_llm())

    # 결과 출력
    lines: list[str] = []
    pass_count = 0
    total = len(results)

    for status, name, detail in results:
        if status == "OK":
            lines.append(f"  [green]\\[OK][/]   {name:<18s} {detail}")
            pass_count += 1
        elif status == "SKIP":
            lines.append(f"  [yellow]\\[SKIP][/] {name:<18s} {detail}")
            pass_count += 1  # SKIP은 통과로 간주
        else:
            lines.append(f"  [red]\\[FAIL][/] {name:<18s} {detail}")

    content = "\n".join(lines)
    console.print(Panel(content, title="check", border_style="cyan"))

    # 요약
    fail_count = total - pass_count
    if fail_count == 0:
        console.print(
            f"\n result: [green bold]{pass_count}/{total} pass[/]"
        )
        return 0

    console.print(
        f"\n result: [red bold]{pass_count}/{total} pass[/] "
        f"({fail_count} fail)"
    )
    return 1


def _check_node() -> tuple[str, str, str]:
    """node.exe 존재 및 버전을 확인한다."""
    node_path = _PACKAGE_DIR / "resources" / "binaries" / "node.exe"
    if not node_path.exists():
        node_path = _PACKAGE_DIR / "resources" / "binaries" / "node"
    if not node_path.exists():
        return ("FAIL", "node", f"binary not found: {node_path}")

    try:
        result = subprocess.run(
            [str(node_path), "--version"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        version = result.stdout.strip()
        return ("OK", "node", version)
    except Exception as e:
        return ("FAIL", "node", f"exec fail: {e}")


def _check_eslint() -> tuple[str, str, str]:
    """ESLint 설치 및 실제 린트 동작을 확인한다."""
    node_path = _PACKAGE_DIR / "resources" / "binaries" / "node.exe"
    if not node_path.exists():
        node_path = _PACKAGE_DIR / "resources" / "binaries" / "node"
    if not node_path.exists():
        return ("FAIL", "ESLint", "node binary not found")

    # eslint.js 탐색
    eslint_js = _find_eslint_js()
    if not eslint_js:
        return ("FAIL", "ESLint", "eslint module not found (node_modules/)")

    # 버전 확인
    try:
        pkg_json = eslint_js.parent.parent / "package.json"
        if pkg_json.exists():
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            version = data.get("version", "unknown")
        else:
            version = "unknown"
    except Exception:
        version = "unknown"

    # 실제 린트 테스트
    config_path = _PACKAGE_DIR / "resources" / "lint-configs" / ".eslintrc.json"
    if not config_path.exists():
        return ("FAIL", "ESLint", f"v{version} - .eslintrc.json not found")

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".js", mode="w", delete=False, encoding="utf-8",
        ) as f:
            f.write(_TEST_JS)
            test_file = f.name

        result = subprocess.run(
            [
                str(node_path), str(eslint_js),
                "--no-eslintrc",
                "--config", str(config_path),
                "--format", "json",
                "--no-color",
                test_file,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )

        # returncode 1 = lint errors found (정상)
        if result.stdout.strip():
            parsed = json.loads(result.stdout)
            msg_count = sum(
                len(f.get("messages", [])) for f in parsed
            )
            return ("OK", "ESLint", f"v{version}  (test lint {msg_count} detected)")

        return ("FAIL", "ESLint", f"v{version} - no output: {result.stderr[:200]}")
    except Exception as e:
        return ("FAIL", "ESLint", f"v{version} - exec fail: {e}")
    finally:
        try:
            Path(test_file).unlink(missing_ok=True)
        except Exception:
            pass


def _find_eslint_js() -> Path | None:
    """ESLint 실행 파일 경로를 탐색한다."""
    candidates = [
        _PACKAGE_DIR / "resources" / "binaries" / "node_modules" / "eslint" / "bin" / "eslint.js",
        _PACKAGE_DIR / "resources" / "binaries" / "node_modules" / ".bin" / "eslint",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _check_clang_tidy() -> tuple[str, str, str]:
    """clang-tidy 설치 및 실제 린트 동작을 확인한다."""
    ct_path = _PACKAGE_DIR / "resources" / "binaries" / "clang-tidy.exe"
    if not ct_path.exists():
        ct_path = _PACKAGE_DIR / "resources" / "binaries" / "clang-tidy"
    if not ct_path.exists():
        return ("FAIL", "clang-tidy", f"binary not found: {ct_path}")

    # 버전 확인
    try:
        result = subprocess.run(
            [str(ct_path), "--version"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        version = "unknown"
        for line in result.stdout.splitlines():
            if "version" in line.lower():
                parts = line.split()
                for part in parts:
                    if part[0].isdigit():
                        version = part
                        break
                if version != "unknown":
                    break
    except Exception as e:
        return ("FAIL", "clang-tidy", f"exec fail: {e}")

    # 실제 린트 테스트
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".c", mode="w", delete=False, encoding="utf-8",
        ) as f:
            f.write(_TEST_C)
            test_file = f.name

        result = subprocess.run(
            [
                str(ct_path),
                "--checks=-*,clang-analyzer-*",
                test_file,
                "--",
                "-std=c99",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )

        combined = f"{result.stdout}\n{result.stderr}"
        warning_count = combined.count("warning:")
        return ("OK", "clang-tidy", f"v{version}  (test lint {warning_count} detected)")
    except Exception as e:
        return ("FAIL", "clang-tidy", f"v{version} - exec fail: {e}")
    finally:
        try:
            Path(test_file).unlink(missing_ok=True)
        except Exception:
            pass


def _check_configs() -> tuple[str, str, str]:
    """설정 파일 존재 여부를 확인한다."""
    files = {
        "settings.yaml": _PACKAGE_DIR / "config" / "settings.yaml",
        ".eslintrc.json": _PACKAGE_DIR / "resources" / "lint-configs" / ".eslintrc.json",
        ".clang-tidy": _PACKAGE_DIR / "resources" / "lint-configs" / ".clang-tidy",
    }

    missing = [name for name, path in files.items() if not path.exists()]

    if missing:
        return ("FAIL", "config", f"not found: {', '.join(missing)}")

    found = ", ".join(files.keys())
    return ("OK", "config", found)


def _check_chrome() -> tuple[str, str, str]:
    """Chrome 브라우저 및 websocket-client 사용 가능 여부를 확인한다."""
    # websocket-client 모듈 확인
    try:
        import websocket  # noqa: F401
    except ImportError:
        return ("FAIL", "Chrome CDP", "websocket-client 모듈 없음 (pip install websocket-client)")

    # Chrome 실행 파일 탐색
    try:
        from mider.config.sso_auth import _find_chrome
        chrome_path = _find_chrome()
        return ("OK", "Chrome CDP", f"Chrome: {chrome_path}")
    except FileNotFoundError:
        return ("SKIP", "Chrome CDP", "Chrome not found - SSO login unavailable")


def _check_llm() -> tuple[str, str, str]:
    """LLM API 키 설정 여부를 확인한다 (연결 테스트는 하지 않음)."""
    # AICA
    aica_key = os.environ.get("AICA_API_KEY", "")
    aica_endpoint = os.environ.get("AICA_ENDPOINT", "")
    if aica_key and aica_endpoint:
        return ("OK", "LLM API", "AICA configured")

    # Azure OpenAI
    azure_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    if azure_key and azure_endpoint:
        return ("OK", "LLM API", "Azure OpenAI configured")

    # OpenAI
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        return ("OK", "LLM API", "OpenAI API key configured")

    return ("SKIP", "LLM API", "API key not set - static analysis only")
