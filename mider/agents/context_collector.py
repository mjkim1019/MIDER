"""ContextCollectorAgent: Phase 1 - 파일 컨텍스트 수집.

ExecutionPlan을 입력받아 각 파일의 import/include 관계,
함수 호출 관계, 코드 패턴을 수집하여 FileContext를 생성한다.
"""

import json
import logging
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from mider.agents.base_agent import BaseAgent
from mider.config.prompt_loader import load_prompt
from mider.config.settings_loader import (
    get_agent_fallback_model,
    get_agent_model,
    get_agent_temperature,
)
from mider.models.execution_plan import DependencyGraph
from mider.models.file_context import FileContext
from mider.tools.file_io.file_reader import FileReader

logger = logging.getLogger(__name__)

# 언어별 import/include 정규표현식
_JS_IMPORT_RE = re.compile(
    r"(?:import\s+.*?from\s+['\"](.+?)['\"]|require\s*\(\s*['\"](.+?)['\"]\s*\))"
)
_C_INCLUDE_RE = re.compile(r'#include\s+[<"](.+?)[>"]')
_PROC_INCLUDE_RE = re.compile(
    r"(?:EXEC\s+SQL\s+INCLUDE\s+(\w+)|#include\s+[<\"](.+?)[>\"])",
    re.IGNORECASE,
)

# 함수 호출 추출 패턴 (간이)
_FUNCTION_CALL_RE = re.compile(r"\b(\w{2,})\s*\(")

# 함수 호출 시 무시할 키워드
_CALL_SKIP_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "return", "sizeof", "typeof",
    "catch", "throw", "case", "default", "else", "do",
    "int", "void", "char", "float", "double", "long", "short",
    "unsigned", "signed", "struct", "enum", "union", "typedef",
    "const", "static", "extern", "register", "volatile",
    "EXEC", "SQL", "BEGIN", "END", "DECLARE", "SECTION",
    "SELECT", "INSERT", "UPDATE", "DELETE", "FROM", "WHERE",
    "OPEN", "CLOSE", "FETCH", "COMMIT", "ROLLBACK", "WHENEVER",
    "INCLUDE", "INTO", "VALUES", "SET", "ORDER", "GROUP", "HAVING",
    "function", "class", "var", "let", "import", "export",
    "async", "await", "new", "delete", "require",
})

# 패턴 유형별 정규표현식 (언어별)
_PATTERN_REGEXES: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "javascript": [
        ("error_handling", re.compile(
            r"(?:try\s*\{|\.catch\s*\(|if\s*\(.*(?:err|error|null|undefined))"
        )),
        ("logging", re.compile(r"console\.(?:log|warn|error|info)\s*\(")),
    ],
    "c": [
        ("error_handling", re.compile(
            r"(?:if\s*\(.*(?:NULL|== -1|< 0)|goto\s+\w+)"
        )),
        ("logging", re.compile(r"(?:printf|fprintf|syslog|log_error)\s*\(")),
        ("memory_management", re.compile(r"\b(?:malloc|calloc|realloc|free)\s*\(")),
    ],
    "proc": [
        ("error_handling", re.compile(
            r"(?:WHENEVER\s+SQLERROR|sqlca\.sqlcode)", re.IGNORECASE
        )),
        ("logging", re.compile(r"(?:printf|fprintf|syslog|log_error)\s*\(")),
        ("transaction", re.compile(
            r"EXEC\s+SQL\s+(?:COMMIT|ROLLBACK)", re.IGNORECASE
        )),
        ("memory_management", re.compile(r"\b(?:malloc|calloc|realloc|free)\s*\(")),
    ],
    "sql": [
        ("transaction", re.compile(
            r"\b(?:COMMIT|ROLLBACK|BEGIN\s+TRANSACTION)\b", re.IGNORECASE
        )),
    ],
    "xml": [],  # XML은 _detect_patterns_xml에서 별도 처리
}


class ContextCollectorAgent(BaseAgent):
    """Phase 1: 파일 컨텍스트를 수집하는 Agent.

    ExecutionPlan의 파일 목록에서 import/include, 함수 호출,
    코드 패턴을 Tool로 추출하고 LLM으로 보정한다.
    """

    def __init__(
        self,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        _name = "context_collector"
        model = model or get_agent_model(_name)
        fallback_model = fallback_model or get_agent_fallback_model(_name)
        temperature = temperature if temperature is not None else get_agent_temperature(_name)
        super().__init__(
            model=model,
            fallback_model=fallback_model,
            temperature=temperature,
        )
        self._file_reader = FileReader()

    async def run(
        self,
        *,
        execution_plan: dict[str, Any],
        cleaned_contents: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """ExecutionPlan에서 파일 컨텍스트를 수집한다.

        Args:
            execution_plan: ExecutionPlan.model_dump() 형식의 딕셔너리
            cleaned_contents: 주석 제거된 파일 내용 (파일 경로 → 내용).
                              제공된 경우 FileReader 대신 우선 사용한다.

        Returns:
            FileContext 형식의 딕셔너리
        """
        sub_tasks = execution_plan.get("sub_tasks", [])
        dependencies = execution_plan.get("dependencies", {})

        if not sub_tasks:
            logger.warning("분석 대상 파일이 없습니다.")
            return FileContext(
                file_contexts=[],
                dependencies=DependencyGraph(),
                common_patterns={},
            ).model_dump()

        logger.info(f"Phase 1 시작: {len(sub_tasks)}개 파일 컨텍스트 수집")

        # 분석 대상 파일 세트 (resolved_path 매칭용)
        all_files = {task["file"] for task in sub_tasks}

        # Step 1: Tool 기반 컨텍스트 수집 (파일별)
        file_contexts: list[dict[str, Any]] = []
        for task in sub_tasks:
            ctx = self._collect_single_file(
                task["file"], task["language"], all_files,
                cleaned_contents=cleaned_contents,
            )
            file_contexts.append(ctx)

        # Step 2: 공통 패턴 집계
        common_patterns = self._aggregate_patterns(file_contexts)

        # Step 3: LLM 보정 (단일 파일이면 skip — Tool 결과만으로 충분)
        tool_result = {
            "file_contexts": file_contexts,
            "dependencies": dependencies,
            "common_patterns": common_patterns,
        }

        # Scan 결과 로그
        for ctx in file_contexts:
            imports_count = len(ctx.get("imports", []))
            calls_count = len(ctx.get("calls", []))
            patterns = ctx.get("patterns", [])
            pattern_summary = ", ".join(
                f"{p['pattern_type']}: {p.get('description', '')[:30]}"
                for p in patterns[:3]
            )
            fname = Path(ctx.get("file", "?")).name
            self.rl.scan(
                f"Scan [{fname}]: imports={imports_count}, calls={calls_count}, "
                f"patterns=[{pattern_summary}]"
            )

        if len(sub_tasks) > 1:
            self.rl.llm_request(f"LLM 컨텍스트 보정: {self.model} 요청 중...")
            refined = await self._refine_with_llm(
                execution_plan=execution_plan,
                tool_result=tool_result,
                cleaned_contents=cleaned_contents,
            )
            self.rl.llm_response("LLM 컨텍스트 보정 완료")
        else:
            logger.debug("단일 파일 — LLM 컨텍스트 보정 건너뜀")
            self.rl.decision(
                "Decision: LLM 컨텍스트 보정 skip",
                reason="단일 파일이므로 교차 참조 보정 불필요",
            )
            refined = tool_result

        # Step 4: FileContext 스키마 검증
        file_context = FileContext.model_validate(refined)

        logger.info(
            f"Phase 1 완료: {len(file_context.file_contexts)}개 파일 컨텍스트"
        )

        return file_context.model_dump()

    # ──────────────────────────────────────────────
    # 단일 파일 컨텍스트 수집
    # ──────────────────────────────────────────────

    def _collect_single_file(
        self,
        file_path: str,
        language: str,
        all_files: set[str],
        *,
        cleaned_contents: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """단일 파일의 컨텍스트를 Tool로 수집한다."""
        try:
            if cleaned_contents and file_path in cleaned_contents:
                content = cleaned_contents[file_path]
            else:
                read_result = self._file_reader.execute(path=file_path)
                content = read_result.data["content"]
        except Exception as e:
            logger.warning(f"파일 읽기 실패: {file_path}: {e}")
            return {
                "file": file_path,
                "language": language,
                "imports": [],
                "calls": [],
                "patterns": [],
            }

        imports = self._extract_imports(content, file_path, language, all_files)
        if language == "xml":
            calls = self._extract_xml_event_calls(content)
            patterns = self._detect_xml_patterns(content)
        else:
            calls = self._extract_calls(content, language)
            patterns = self._detect_patterns(content, language)

        return {
            "file": file_path,
            "language": language,
            "imports": imports,
            "calls": calls,
            "patterns": patterns,
        }

    # ──────────────────────────────────────────────
    # Import/Include 추출
    # ──────────────────────────────────────────────

    def _extract_imports(
        self,
        content: str,
        file_path: str,
        language: str,
        all_files: set[str],
    ) -> list[dict[str, Any]]:
        """파일 내용에서 import/include 구문을 추출한다."""
        if language in ("sql", "xml"):
            return []

        imports: list[dict[str, Any]] = []

        if language == "javascript":
            for match in _JS_IMPORT_RE.finditer(content):
                ref = match.group(1) or match.group(2)
                resolved = self._resolve_path(ref, file_path, all_files)
                is_external = not ref.startswith(".")
                imports.append({
                    "statement": match.group().strip(),
                    "resolved_path": resolved,
                    "is_external": is_external,
                })

        elif language == "c":
            for line in content.splitlines():
                m = _C_INCLUDE_RE.search(line)
                if m:
                    ref = m.group(1)
                    resolved = self._resolve_path(ref, file_path, all_files)
                    is_external = "<" in line and ">" in line
                    imports.append({
                        "statement": line.strip(),
                        "resolved_path": resolved,
                        "is_external": is_external,
                    })

        elif language == "proc":
            for line in content.splitlines():
                m = _PROC_INCLUDE_RE.search(line)
                if m:
                    ref = m.group(1) or m.group(2)
                    resolved = self._resolve_path(ref, file_path, all_files)
                    # EXEC SQL INCLUDE sqlca/oraca → Oracle 내장
                    is_oracle_builtin = bool(m.group(1)) and ref.lower() in {
                        "sqlca", "oraca", "sqlda",
                    }
                    is_external = is_oracle_builtin or ("<" in line and ">" in line)
                    imports.append({
                        "statement": line.strip(),
                        "resolved_path": resolved,
                        "is_external": is_external,
                    })

        return imports

    @staticmethod
    def _resolve_path(
        ref: str,
        source_file: str,
        all_files: set[str],
    ) -> str | None:
        """참조를 분석 대상 파일에서 매칭한다."""
        source_dir = Path(source_file).parent

        # 상대 경로 시도
        for ext in ["", ".js", ".c", ".h", ".pc", ".sql"]:
            candidate = str((source_dir / (ref + ext)).resolve())
            if candidate in all_files:
                return candidate

        # 파일명만으로 매칭
        ref_name = Path(ref).name
        for f in all_files:
            if Path(f).name == ref_name or Path(f).stem == Path(ref_name).stem:
                return f

        return None

    # ──────────────────────────────────────────────
    # 함수 호출 추출
    # ──────────────────────────────────────────────

    @staticmethod
    def _extract_calls(
        content: str,
        language: str,
    ) -> list[dict[str, Any]]:
        """파일에서 함수 호출을 추출한다.

        간이 정규표현식으로 추출하며, LLM이 target_file을 보정한다.
        """
        if language == "sql":
            return []

        calls: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()

        for line_num, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            # 주석 건너뛰기 (블록 주석 내부의 "* " 라인 포함)
            if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("* "):
                continue
            # 전처리기 지시문 건너뛰기
            if stripped.startswith("#"):
                continue

            for m in _FUNCTION_CALL_RE.finditer(line):
                func_name = m.group(1)
                if func_name in _CALL_SKIP_KEYWORDS:
                    continue
                key = (func_name, line_num)
                if key not in seen:
                    seen.add(key)
                    calls.append({
                        "function_name": func_name,
                        "line": line_num,
                        "target_file": None,
                    })

        return calls

    # ──────────────────────────────────────────────
    # 코드 패턴 탐지
    # ──────────────────────────────────────────────

    @staticmethod
    def _detect_patterns(
        content: str,
        language: str,
    ) -> list[dict[str, Any]]:
        """파일에서 코드 패턴을 탐지한다."""
        regexes = _PATTERN_REGEXES.get(language, [])
        if not regexes:
            return []

        patterns: list[dict[str, Any]] = []

        for line_num, line in enumerate(content.splitlines(), start=1):
            for pattern_type, regex in regexes:
                if regex.search(line):
                    patterns.append({
                        "pattern_type": pattern_type,
                        "description": f"{pattern_type} 패턴: {line.strip()[:80]}",
                        "line": line_num,
                    })

        return patterns

    # ──────────────────────────────────────────────
    # 공통 패턴 집계
    # ──────────────────────────────────────────────

    @staticmethod
    def _aggregate_patterns(
        file_contexts: list[dict[str, Any]],
    ) -> dict[str, int]:
        """전체 파일의 패턴 유형별 빈도를 집계한다."""
        counter: Counter[str] = Counter()
        for ctx in file_contexts:
            for pattern in ctx.get("patterns", []):
                counter[pattern["pattern_type"]] += 1

        all_types = ["error_handling", "logging", "transaction", "memory_management"]
        return {t: counter.get(t, 0) for t in all_types}

    # ──────────────────────────────────────────────
    # LLM 보정
    # ──────────────────────────────────────────────

    async def _refine_with_llm(
        self,
        *,
        execution_plan: dict[str, Any],
        tool_result: dict[str, Any],
        cleaned_contents: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """LLM으로 Tool 결과를 보정한다.

        LLM 실패 시 Tool 결과를 그대로 반환한다 (graceful degradation).
        """
        file_contents = self._read_file_contents(
            [task["file"] for task in execution_plan.get("sub_tasks", [])],
            cleaned_contents=cleaned_contents,
        )
        if not file_contents:
            logger.warning("모든 파일 읽기 실패, LLM 보정 건너뜀")
            return tool_result

        plan_str = json.dumps(execution_plan, ensure_ascii=False, indent=2, default=str)
        contents_str = "\n\n".join(
            f"### {path}\n```\n{content}\n```"
            for path, content in file_contents.items()
        )

        try:
            prompt = load_prompt(
                "context_collector",
                execution_plan=plan_str,
                file_contents=contents_str,
            )

            messages = [
                {
                    "role": "system",
                    "content": (
                        "당신은 소스코드 컨텍스트 수집 전문가입니다. "
                        "파일의 import/include 관계, 함수 호출, 코드 패턴을 분석합니다. "
                        "반드시 JSON 형식으로 응답하세요."
                    ),
                },
                {"role": "user", "content": prompt},
            ]

            response = await self.call_llm(messages, json_mode=True)
            llm_result = json.loads(response)

            if not isinstance(llm_result, dict):
                logger.warning(f"LLM 응답이 dict가 아님: {type(llm_result)}")
                return tool_result

            return self._merge_results(tool_result, llm_result)

        except Exception as e:
            logger.warning(
                f"LLM 컨텍스트 보정 실패, Tool 결과를 사용합니다: {e}"
            )
            return tool_result

    def _merge_results(
        self,
        tool_result: dict[str, Any],
        llm_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Tool 결과와 LLM 결과를 병합한다.

        imports/dependencies는 Tool 결과 우선, calls/patterns는 LLM으로 보강.
        """
        llm_contexts = llm_result.get("file_contexts", [])
        if not llm_contexts:
            return tool_result

        llm_map: dict[str, dict[str, Any]] = {
            ctx.get("file", ""): ctx for ctx in llm_contexts if ctx.get("file")
        }

        merged_contexts: list[dict[str, Any]] = []
        for tool_ctx in tool_result.get("file_contexts", []):
            file_path = tool_ctx["file"]
            llm_ctx = llm_map.get(file_path, {})

            merged = {
                "file": file_path,
                "language": tool_ctx["language"],
                "imports": tool_ctx["imports"],
                "calls": self._merge_calls(
                    tool_ctx.get("calls", []),
                    llm_ctx.get("calls", []),
                ),
                "patterns": self._merge_patterns(
                    tool_ctx.get("patterns", []),
                    llm_ctx.get("patterns", []),
                ),
            }
            merged_contexts.append(merged)

        return {
            "file_contexts": merged_contexts,
            "dependencies": tool_result["dependencies"],
            # common_patterns는 Tool 결과 우선 (LLM이 빈도를 할루시네이션할 수 있음)
            "common_patterns": tool_result.get("common_patterns", {}),
        }

    @staticmethod
    def _merge_calls(
        tool_calls: list[dict[str, Any]],
        llm_calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Tool과 LLM의 함수 호출 목록을 병합한다."""
        tool_map: dict[tuple[str, int], dict[str, Any]] = {}
        for call in tool_calls:
            key = (call["function_name"], call["line"])
            tool_map[key] = call

        for call in llm_calls:
            func = call.get("function_name", "")
            line = call.get("line", 0)
            if not func or not line:
                continue
            key = (func, line)
            if key in tool_map:
                if call.get("target_file") and not tool_map[key].get("target_file"):
                    tool_map[key]["target_file"] = call["target_file"]
            else:
                tool_map[key] = call

        return list(tool_map.values())

    @staticmethod
    def _merge_patterns(
        tool_patterns: list[dict[str, Any]],
        llm_patterns: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Tool과 LLM의 패턴 목록을 병합한다."""
        valid_types = {"error_handling", "logging", "transaction", "memory_management"}
        seen: set[tuple[str, int]] = set()
        merged: list[dict[str, Any]] = []

        for p in tool_patterns:
            key = (p["pattern_type"], p["line"])
            if key not in seen:
                seen.add(key)
                merged.append(p)

        for p in llm_patterns:
            pt = p.get("pattern_type", "")
            line = p.get("line", 0)
            if pt in valid_types and line:
                key = (pt, line)
                if key not in seen:
                    seen.add(key)
                    merged.append(p)

        return merged

    # ──────────────────────────────────────────────
    # XML 전용 추출
    # ──────────────────────────────────────────────

    @staticmethod
    def _extract_xml_event_calls(content: str) -> list[dict[str, Any]]:
        """XML에서 이벤트 핸들러 함수 호출을 추출한다."""
        calls: list[dict[str, Any]] = []
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return calls

        event_re = re.compile(r"\{.*\}on\w+$|^ev:on\w+$|^on\w+$")
        func_re = re.compile(r"scwin\.(\w+)")

        for elem in root.iter():
            for attr_name, attr_value in elem.attrib.items():
                if event_re.match(attr_name):
                    for m in func_re.finditer(attr_value):
                        calls.append({
                            "function_name": m.group(1),
                            "line": 0,
                            "target_file": None,
                        })
        return calls

    @staticmethod
    def _detect_xml_patterns(content: str) -> list[dict[str, Any]]:
        """XML에서 구조 패턴을 탐지한다."""
        patterns: list[dict[str, Any]] = []
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return patterns

        # 이벤트 바인딩 패턴
        event_re = re.compile(r"\{.*\}on\w+$|^ev:on\w+$|^on\w+$")
        event_count = 0
        for elem in root.iter():
            for attr_name in elem.attrib:
                if event_re.match(attr_name):
                    event_count += 1

        if event_count > 0:
            patterns.append({
                "pattern_type": "event_binding",
                "description": f"이벤트 바인딩 {event_count}개 (핸들러 검증 필요)",
                "line": 0,
            })

        return patterns

    # ──────────────────────────────────────────────
    # 파일 읽기 유틸
    # ──────────────────────────────────────────────

    def _read_file_contents(
        self,
        files: list[str],
        *,
        cleaned_contents: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """파일 내용을 읽어서 딕셔너리로 반환한다."""
        contents: dict[str, str] = {}
        for file_path in files:
            try:
                if cleaned_contents and file_path in cleaned_contents:
                    content = cleaned_contents[file_path]
                else:
                    result = self._file_reader.execute(path=file_path)
                    content = result.data["content"]
                lines = content.split("\n")
                if len(lines) > 500:
                    head = "\n".join(lines[:250])
                    tail = "\n".join(lines[-250:])
                    content = (
                        f"{head}\n\n"
                        f"... ({len(lines) - 500} lines omitted) ...\n\n"
                        f"{tail}"
                    )
                contents[file_path] = content
            except Exception as e:
                logger.warning(f"파일 읽기 실패, 건너뜀: {file_path}: {e}")
        return contents
