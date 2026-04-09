# -*- mode: python ; coding: utf-8 -*-
"""Mider PyInstaller spec 파일.

빌드 명령:
    pyinstaller mider.spec

결과:
    dist/mider/ 디렉토리에 실행파일 + 리소스 생성
"""

import os
from pathlib import Path

block_cipher = None

# 프로젝트 루트 (이 spec 파일 기준)
ROOT = Path(SPECPATH)

# 번들 대상 데이터 파일
datas = []

# settings.yaml
settings_yaml = ROOT / "mider" / "config" / "settings.yaml"
if settings_yaml.exists():
    datas.append((str(settings_yaml), os.path.join("mider", "config")))

# 프롬프트 템플릿
prompts_dir = ROOT / "mider" / "config" / "prompts"
if prompts_dir.exists():
    for txt in prompts_dir.glob("*.txt"):
        datas.append((str(txt), os.path.join("mider", "config", "prompts")))

# lint 설정 파일
lint_dir = ROOT / "mider" / "resources" / "lint-configs"
if lint_dir.exists():
    for f in lint_dir.iterdir():
        if f.is_file():
            datas.append((str(f), os.path.join("mider", "resources", "lint-configs")))

# 바이너리 (ESLint node, clang-tidy, proc)
binaries_dir = ROOT / "mider" / "resources" / "binaries"
if binaries_dir.exists():
    for f in binaries_dir.iterdir():
        if f.is_file() and f.name != ".gitkeep":
            datas.append((str(f), os.path.join("mider", "resources", "binaries")))

a = Analysis(
    [str(ROOT / "mider" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "mider",
        "mider.agents",
        "mider.agents.orchestrator",
        "mider.config",
        "mider.config.settings_loader",
        "mider.config.logging_config",
        "mider.config.reasoning_logger",
        "mider.tools",
        "mider.models",
        "pydantic",
        "rich",
        "openai",
        "httpx",
        "sqlparse",
        "dotenv",
        "yaml",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "PIL",
        "cv2",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mider",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="mider",
)
