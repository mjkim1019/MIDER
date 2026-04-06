"""JSHeuristicScanner: JavaScript 코드 위험 패턴 정적 스캐너.

ESLint가 탐지하지 못하는 JS 런타임 장애 패턴을 regex 기반으로 탐지한다.
- 중첩 for문 var 루프 변수 재사용 (무한루프 / 루프 파괴)
- 비동기 콜백 내 var 루프 변수 참조 (클로저 스코프 버그)
"""

import logging
import re
from pathlib import Path
from typing import Any

from mider.tools.base_tool import BaseTool, ToolExecutionError, ToolResult
from mider.tools.utility.token_optimizer import find_function_boundaries

logger = logging.getLogger(__name__)

# ── for 루프 패턴 ──
# for (var i = 0; ...) 또는 for (var i in ...) 또는 for (var i of ...)
_FOR_VAR_DECL = re.compile(
    r"\bfor\s*\(\s*var\s+(\w+)\s*[=;]"
)
# for (i = 0; ...) — var 없이 재할당
_FOR_REASSIGN = re.compile(
    r"\bfor\s*\(\s*(\w+)\s*="
)

# ── 비동기 콜백 패턴 ──
_ASYNC_CALLBACK = re.compile(
    r"\b(setTimeout|setInterval|addEventListener|\.then|\.forEach)\s*\("
)

# ── 주석/문자열 제거 ──
_LINE_COMMENT = re.compile(r"//.*$")
_BLOCK_COMMENT_START = re.compile(r"/\*")
_BLOCK_COMMENT_END = re.compile(r"\*/")

# ── 함수명 추출 (JS) ──
_JS_FUNC_PATTERN = re.compile(
    r"(?:function\s+(\w+)|(\w+)\s*[:=]\s*function|(\w+)\s*[:=]\s*async\s+function)"
)


class JSHeuristicScanner(BaseTool):
    """JavaScript 코드 위험 패턴 스캐너.

    전체 파일을 스캔하여 ESLint가 놓치는 런타임 장애 패턴을 탐지한다.
    """

    def execute(self, *, file: str) -> ToolResult:
        """JS 파일을 스캔하여 위험 패턴을 반환한다.

        Args:
            file: 분석할 JavaScript 파일 경로

        Returns:
            ToolResult (data: findings, total_findings)
        """
        file_path = Path(file)
        if not file_path.exists():
            raise ToolExecutionError(
                "js_heuristic_scanner", f"file not found: {file}"
            )

        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()

        # 주석 제거된 라인 생성
        clean_lines = self._strip_comments(lines)

        # 함수 경계 + 이름
        func_boundaries = find_function_boundaries(lines, "javascript")
        func_names = self._extract_func_names(lines, func_boundaries)

        # 패턴 스캔
        findings: list[dict[str, Any]] = []
        findings.extend(
            self._scan_nested_var_loop(clean_lines, func_boundaries, func_names)
        )
        findings.extend(
            self._scan_var_closure_loop(clean_lines, func_boundaries, func_names)
        )

        logger.debug(
            f"JS 휴리스틱 스캔 완료: {file} → {len(findings)} findings"
        )

        return ToolResult(
            success=True,
            data={
                "findings": findings,
                "total_findings": len(findings),
            },
        )

    # ── 패턴 1: 중첩 for문 var 변수 재사용 ──

    def _scan_nested_var_loop(
        self,
        clean_lines: list[str],
        boundaries: list[tuple[int, int]],
        func_names: dict[tuple[int, int], str],
    ) -> list[dict[str, Any]]:
        """중첩 for문에서 동일 var 루프 변수를 재사용하는 패턴을 탐지한다.

        var는 함수 스코프이므로 내부 for문의 var i가 외부 i를 덮어써서
        무한루프 또는 외부 루프 조기 종료를 유발한다.
        """
        findings: list[dict[str, Any]] = []

        # for 블록을 스택으로 추적: [(변수명, 시작라인, depth)]
        loop_stack: list[tuple[str, int, int]] = []
        brace_depth = 0

        for i, line in enumerate(clean_lines):
            line_num = i + 1

            # 중괄호 depth 추적
            open_count = line.count("{")
            close_count = line.count("}")

            # for (var <변수>) 매칭
            var_match = _FOR_VAR_DECL.search(line)
            reassign_match = _FOR_REASSIGN.search(line) if not var_match else None

            if var_match:
                var_name = var_match.group(1)
                # 스택에 같은 변수가 이미 있으면 → 위험
                for stack_var, stack_line, _depth in loop_stack:
                    if stack_var == var_name:
                        func_name = self._find_enclosing_function(
                            line_num, boundaries, func_names,
                        )
                        findings.append({
                            "pattern_id": "NESTED_VAR_LOOP",
                            "severity": "critical",
                            "description": (
                                f"중첩 for문에서 var {var_name} 재선언 "
                                f"(외부 루프 L{stack_line}과 동일 변수) — "
                                f"var는 함수 스코프이므로 외부 루프 변수를 덮어씀"
                            ),
                            "line": line_num,
                            "outer_line": stack_line,
                            "content": line.strip(),
                            "match": var_match.group(0),
                            "function": func_name,
                        })
                        break
                loop_stack.append((var_name, line_num, brace_depth))

            elif reassign_match:
                var_name = reassign_match.group(1)
                # let/const/var 키워드 없이 재할당하는 경우
                # 스택에 같은 변수가 var로 선언되어 있으면 → 위험
                for stack_var, stack_line, _depth in loop_stack:
                    if stack_var == var_name:
                        func_name = self._find_enclosing_function(
                            line_num, boundaries, func_names,
                        )
                        findings.append({
                            "pattern_id": "NESTED_VAR_LOOP",
                            "severity": "critical",
                            "description": (
                                f"중첩 for문에서 {var_name} 재할당 "
                                f"(외부 루프 L{stack_line}의 var {var_name}를 직접 변경) — "
                                f"외부 루프 제어 파괴"
                            ),
                            "line": line_num,
                            "outer_line": stack_line,
                            "content": line.strip(),
                            "match": reassign_match.group(0),
                            "function": func_name,
                        })
                        break

            # depth 업데이트
            brace_depth += open_count - close_count

            # 스택에서 닫힌 블록의 루프 제거
            while loop_stack and brace_depth <= loop_stack[-1][2]:
                loop_stack.pop()

        return findings

    # ── 패턴 2: 비동기 콜백 내 var 루프 변수 참조 ──

    def _scan_var_closure_loop(
        self,
        clean_lines: list[str],
        boundaries: list[tuple[int, int]],
        func_names: dict[tuple[int, int], str],
    ) -> list[dict[str, Any]]:
        """for(var ...) 루프 내 비동기 콜백에서 루프 변수를 참조하는 패턴을 탐지한다.

        var는 함수 스코프이므로 콜백 실행 시 루프 변수는 최종값을 가진다.
        """
        findings: list[dict[str, Any]] = []

        # 활성 var 루프 추적: [(변수명, 시작라인, depth)]
        loop_stack: list[tuple[str, int, int]] = []
        brace_depth = 0

        for i, line in enumerate(clean_lines):
            line_num = i + 1

            open_count = line.count("{")
            close_count = line.count("}")

            # for (var <변수>) 진입
            var_match = _FOR_VAR_DECL.search(line)
            if var_match:
                loop_stack.append((var_match.group(1), line_num, brace_depth))

            # 비동기 콜백 내 루프 변수 참조 체크
            if loop_stack and _ASYNC_CALLBACK.search(line):
                for var_name, loop_line, _depth in loop_stack:
                    # 콜백 인자나 본문에서 루프 변수 사용 여부 (간이 체크)
                    # 같은 줄 또는 다음 몇 줄에서 변수 참조 확인
                    search_end = min(i + 5, len(clean_lines))
                    for j in range(i, search_end):
                        if re.search(rf"\b{re.escape(var_name)}\b", clean_lines[j]):
                            # function 키워드나 => 가 콜백에 있는지 확인
                            callback_range = "\n".join(clean_lines[i:search_end])
                            if "function" in callback_range or "=>" in callback_range:
                                func_name = self._find_enclosing_function(
                                    line_num, boundaries, func_names,
                                )
                                findings.append({
                                    "pattern_id": "VAR_CLOSURE_LOOP",
                                    "severity": "high",
                                    "description": (
                                        f"for(var {var_name}) 루프(L{loop_line}) 내 "
                                        f"비동기 콜백에서 {var_name} 참조 — "
                                        f"콜백 실행 시 {var_name}은 루프 최종값"
                                    ),
                                    "line": line_num,
                                    "outer_line": loop_line,
                                    "content": line.strip(),
                                    "match": _ASYNC_CALLBACK.search(line).group(0),
                                    "function": func_name,
                                })
                                break
                    break  # 첫 번째 매칭만

            brace_depth += open_count - close_count

            while loop_stack and brace_depth <= loop_stack[-1][2]:
                loop_stack.pop()

        return findings

    # ── 공통 유틸리티 ──

    @staticmethod
    def _strip_comments(lines: list[str]) -> list[str]:
        """주석과 문자열 리터럴을 제거한 라인 목록을 반환한다."""
        result: list[str] = []
        in_block_comment = False

        for line in lines:
            if in_block_comment:
                if _BLOCK_COMMENT_END.search(line):
                    in_block_comment = False
                    # */ 이후 코드만 남김
                    idx = line.index("*/") + 2
                    line = line[idx:]
                else:
                    result.append("")
                    continue

            if _BLOCK_COMMENT_START.search(line) and not _BLOCK_COMMENT_END.search(line):
                in_block_comment = True
                idx = line.index("/*")
                line = line[:idx]

            # 한 줄 주석 제거
            line = _LINE_COMMENT.sub("", line)
            # 한 줄 블록 주석 제거
            line = re.sub(r"/\*.*?\*/", "", line)
            # 문자열 리터럴 제거
            line = re.sub(r'"[^"]*"', '""', line)
            line = re.sub(r"'[^']*'", "''", line)

            result.append(line)

        return result

    @staticmethod
    def _extract_func_names(
        lines: list[str],
        boundaries: list[tuple[int, int]],
    ) -> dict[tuple[int, int], str]:
        """함수 경계별 함수명을 추출한다."""
        names: dict[tuple[int, int], str] = {}
        for start, end in boundaries:
            idx = start - 1
            if idx >= len(lines):
                continue
            m = _JS_FUNC_PATTERN.search(lines[idx])
            if m:
                names[(start, end)] = m.group(1) or m.group(2) or m.group(3)
        return names

    @staticmethod
    def _find_enclosing_function(
        line_num: int,
        boundaries: list[tuple[int, int]],
        func_names: dict[tuple[int, int], str],
    ) -> str | None:
        """라인이 속한 함수명을 반환한다."""
        for start, end in boundaries:
            if start <= line_num <= end:
                return func_names.get((start, end))
        return None
