"""FileContext 스키마 단위 테스트."""

import pytest
from pydantic import ValidationError

from mider.models.execution_plan import DependencyGraph
from mider.models.file_context import (
    CallInfo,
    FileContext,
    ImportInfo,
    PatternInfo,
    SingleFileContext,
)


class TestImportInfo:
    def test_valid(self):
        info = ImportInfo(
            statement='#include "utils.h"',
            resolved_path="/app/src/common/utils.h",
            is_external=False,
        )
        assert info.resolved_path == "/app/src/common/utils.h"

    def test_optional_resolved_path(self):
        info = ImportInfo(
            statement="#include <stdio.h>",
            is_external=True,
        )
        assert info.resolved_path is None


class TestCallInfo:
    def test_valid(self):
        call = CallInfo(
            function_name="execSQL",
            line=45,
        )
        assert call.target_file is None

    def test_with_target(self):
        call = CallInfo(
            function_name="log_error",
            line=78,
            target_file="/app/src/common/utils.h",
        )
        assert call.target_file is not None


class TestPatternInfo:
    def test_valid(self):
        pattern = PatternInfo(
            pattern_type="error_handling",
            description="if-return error handling",
            line=50,
        )
        assert pattern.pattern_type == "error_handling"

    def test_invalid_pattern_type(self):
        with pytest.raises(ValidationError):
            PatternInfo(
                pattern_type="unknown",  # type: ignore[arg-type]
                description="test",
                line=1,
            )


class TestSingleFileContext:
    def test_valid(self):
        ctx = SingleFileContext(
            file="/app/src/calc.c",
            language="c",
            imports=[
                ImportInfo(
                    statement="#include <stdio.h>",
                    is_external=True,
                )
            ],
            calls=[CallInfo(function_name="printf", line=10)],
            patterns=[
                PatternInfo(
                    pattern_type="memory_management",
                    description="malloc without free",
                    line=120,
                )
            ],
        )
        assert len(ctx.imports) == 1
        assert len(ctx.calls) == 1
        assert len(ctx.patterns) == 1

    def test_empty_lists(self):
        ctx = SingleFileContext(
            file="/app/test.js",
            language="javascript",
        )
        assert ctx.imports == []
        assert ctx.calls == []
        assert ctx.patterns == []


class TestFileContext:
    def test_valid(self):
        fc = FileContext(
            file_contexts=[
                SingleFileContext(
                    file="/app/calc.c",
                    language="c",
                )
            ],
            dependencies=DependencyGraph(),
            common_patterns={"error_handling": 3, "memory_management": 1},
        )
        assert fc.common_patterns["error_handling"] == 3

    def test_json_roundtrip(self):
        fc = FileContext(
            file_contexts=[
                SingleFileContext(
                    file="/app/calc.c",
                    language="c",
                    imports=[
                        ImportInfo(
                            statement="#include <stdio.h>",
                            is_external=True,
                        )
                    ],
                )
            ],
            dependencies=DependencyGraph(),
            common_patterns={"error_handling": 2},
        )
        json_str = fc.model_dump_json()
        restored = FileContext.model_validate_json(json_str)
        assert restored.file_contexts[0].file == "/app/calc.c"
        assert len(restored.file_contexts[0].imports) == 1
