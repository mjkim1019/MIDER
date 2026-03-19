import os
import tempfile
from pathlib import Path

import pytest
from mider.tools.static_analysis.stub_header_generator import StubHeaderGenerator

@pytest.fixture
def stub_gen():
    return StubHeaderGenerator()

def test_parse_includes(stub_gen):
    content = """
#include <stdio.h>
#include <stdlib.h>
#include "my_header.h"
#include "util/types.h"
#include "my_header.h"
    """
    headers = stub_gen._parse_includes(content)
    assert headers == ["my_header.h", "util/types.h"]

def test_create_stub(stub_gen):
    with tempfile.TemporaryDirectory() as tmp_dir:
        stubs_dir = Path(tmp_dir) / "stubs"
        
        # 기본 헤더
        stub_gen._create_stub("common.h", stubs_dir)
        common_h = stubs_dir / "common.h"
        assert common_h.exists()
        content = common_h.read_text("utf-8")
        assert "STUB_COMMON_H" in content
        assert "typedef unsigned char      UINT8;" in content
        
        # 서브디렉토리 포함 헤더
        stub_gen._create_stub("util/types.h", stubs_dir)
        types_h = stubs_dir / "util" / "types.h"
        assert types_h.exists()
        content_types = types_h.read_text("utf-8")
        assert "STUB_UTIL_TYPES_H" in content_types

def test_generate_and_cleanup(stub_gen):
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        source_file = tmp_path / "test.c"
        stubs_dir = tmp_path / "stubs"
        
        source_file.write_text('#include "test1.h"\n#include "test2.h"\n', encoding="utf-8")
        
        generated = stub_gen.generate(str(source_file), stubs_dir)
        
        assert len(generated) == 2
        assert stubs_dir.exists()
        assert (stubs_dir / "test1.h").exists()
        assert (stubs_dir / "test2.h").exists()
        
        stub_gen.cleanup(stubs_dir)
        assert not stubs_dir.exists()
