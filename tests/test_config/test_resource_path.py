"""resource_path 단위 테스트."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from mider.config.resource_path import (
    BUNDLED_PROMPTS_DIR,
    BUNDLED_RULES_DIR,
    BUNDLED_SKILLS_DIR,
    ENV_PROMPTS,
    ENV_RULES,
    ENV_SKILLS,
    EXE_PROMPTS_DIRNAME,
    EXE_RULES_DIRNAME,
    EXE_SKILLS_DIRNAME,
    get_prompt_path,
    get_prompts_dir,
    get_rule_path,
    get_rules_dir,
    get_skill_path,
    get_skills_dir,
)


class TestBundledDefaults:
    """번들 기본 경로가 올바르게 설정되어 있다."""

    def test_prompts_dir_is_under_config(self):
        assert BUNDLED_PROMPTS_DIR.name == "prompts"
        assert BUNDLED_PROMPTS_DIR.parent.name == "config"

    def test_rules_dir_is_under_config(self):
        assert BUNDLED_RULES_DIR.name == "rules"
        assert BUNDLED_RULES_DIR.parent.name == "config"

    def test_skills_dir_is_under_config(self):
        assert BUNDLED_SKILLS_DIR.name == "skills"
        assert BUNDLED_SKILLS_DIR.parent.name == "config"


class TestEnvVarOverride:
    """환경변수가 최우선이다."""

    def test_prompt_env_override(self, tmp_path, monkeypatch):
        override = tmp_path / "reporter.txt"
        override.write_text("env override")
        monkeypatch.setenv(ENV_PROMPTS, str(tmp_path))

        path = get_prompt_path("reporter")
        assert path == override

    def test_rule_env_override(self, tmp_path, monkeypatch):
        override = tmp_path / "c_rules.yaml"
        override.write_text("version: '1.0'")
        monkeypatch.setenv(ENV_RULES, str(tmp_path))

        path = get_rule_path("c_rules")
        assert path == override

    def test_skill_env_override(self, tmp_path, monkeypatch):
        override = tmp_path / "UNSAFE_FUNC.md"
        override.write_text("# Skill")
        monkeypatch.setenv(ENV_SKILLS, str(tmp_path))

        path = get_skill_path("UNSAFE_FUNC")
        assert path == override

    def test_env_set_but_file_missing_falls_back(self, tmp_path, monkeypatch):
        """환경변수 디렉토리에 파일이 없으면 번들로 fallback한다."""
        # tmp_path는 비어있음
        monkeypatch.setenv(ENV_PROMPTS, str(tmp_path))

        path = get_prompt_path("reporter")
        # 실제 번들에 reporter.txt가 존재
        assert path == BUNDLED_PROMPTS_DIR / "reporter.txt"


class TestExeDirFallback:
    """PyInstaller frozen 환경에서 exe 옆 디렉토리를 2순위로 탐색한다."""

    def test_exe_dir_used_when_frozen(self, tmp_path, monkeypatch):
        # sys.frozen + sys.executable 설정으로 frozen 환경 시뮬레이션
        fake_exe = tmp_path / "mider"
        fake_exe.write_text("")
        (tmp_path / EXE_PROMPTS_DIRNAME).mkdir()
        override = tmp_path / EXE_PROMPTS_DIRNAME / "reporter.txt"
        override.write_text("exe override")

        monkeypatch.delenv(ENV_PROMPTS, raising=False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(fake_exe))

        path = get_prompt_path("reporter")
        assert path == override

    def test_not_frozen_skips_exe_dir(self, tmp_path, monkeypatch):
        """frozen=False이면 exe 옆 경로를 건너뛰고 번들로 직행."""
        monkeypatch.delenv(ENV_PROMPTS, raising=False)
        # sys.frozen은 기본적으로 존재하지 않음 (개발 환경)
        monkeypatch.delattr(sys, "frozen", raising=False)

        path = get_prompt_path("reporter")
        assert path == BUNDLED_PROMPTS_DIR / "reporter.txt"


class TestPriorityOrder:
    """환경변수 > exe 옆 > 번들 순서를 준수한다."""

    def test_env_beats_exe_dir(self, tmp_path, monkeypatch):
        # 환경변수 경로
        env_dir = tmp_path / "env"
        env_dir.mkdir()
        (env_dir / "reporter.txt").write_text("env wins")

        # exe 옆 경로
        exe_base = tmp_path / "exe"
        exe_base.mkdir()
        (exe_base / EXE_PROMPTS_DIRNAME).mkdir()
        (exe_base / EXE_PROMPTS_DIRNAME / "reporter.txt").write_text("exe loses")

        monkeypatch.setenv(ENV_PROMPTS, str(env_dir))
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe_base / "mider"))

        path = get_prompt_path("reporter")
        assert path.read_text() == "env wins"


class TestDirectoryResolution:
    """디렉토리 경로 해석."""

    def test_prompts_dir_default(self, monkeypatch):
        monkeypatch.delenv(ENV_PROMPTS, raising=False)
        monkeypatch.delattr(sys, "frozen", raising=False)

        assert get_prompts_dir() == BUNDLED_PROMPTS_DIR

    def test_prompts_dir_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_PROMPTS, str(tmp_path))
        assert get_prompts_dir() == tmp_path

    def test_env_dir_not_a_directory_falls_back(self, tmp_path, monkeypatch):
        """환경변수가 디렉토리가 아니면 fallback한다."""
        fake_file = tmp_path / "not_a_dir"
        fake_file.write_text("")
        monkeypatch.setenv(ENV_PROMPTS, str(fake_file))
        monkeypatch.delattr(sys, "frozen", raising=False)

        assert get_prompts_dir() == BUNDLED_PROMPTS_DIR

    def test_rules_and_skills_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_RULES, str(tmp_path))
        monkeypatch.setenv(ENV_SKILLS, str(tmp_path))
        assert get_rules_dir() == tmp_path
        assert get_skills_dir() == tmp_path


class TestFileExtensions:
    """리소스 유형별 확장자 매핑."""

    def test_prompt_uses_txt(self, tmp_path, monkeypatch):
        (tmp_path / "x.txt").write_text("ok")
        monkeypatch.setenv(ENV_PROMPTS, str(tmp_path))
        assert get_prompt_path("x").suffix == ".txt"

    def test_rule_uses_yaml(self, tmp_path, monkeypatch):
        (tmp_path / "x.yaml").write_text("ok")
        monkeypatch.setenv(ENV_RULES, str(tmp_path))
        assert get_rule_path("x").suffix == ".yaml"

    def test_skill_uses_md(self, tmp_path, monkeypatch):
        (tmp_path / "x.md").write_text("ok")
        monkeypatch.setenv(ENV_SKILLS, str(tmp_path))
        assert get_skill_path("x").suffix == ".md"
