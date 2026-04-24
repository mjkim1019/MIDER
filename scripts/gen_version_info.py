"""PyInstaller Windows version resource 생성 스크립트.

mider/__init__.py의 __version__을 읽어 version_info.txt를 생성한다.
실행: python scripts/gen_version_info.py
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT_PY = ROOT / "mider" / "__init__.py"
OUTPUT = ROOT / "version_info.txt"


def read_version() -> tuple[int, int, int, int]:
    text = INIT_PY.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        raise RuntimeError(f"__version__ not found in {INIT_PY}")
    parts = match.group(1).split(".")
    nums = [int(re.match(r"\d+", p).group()) for p in parts if re.match(r"\d+", p)]
    while len(nums) < 4:
        nums.append(0)
    return tuple(nums[:4])  # type: ignore[return-value]


def render(version: tuple[int, int, int, int]) -> str:
    version_str = ".".join(str(n) for n in version[:3])
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version},
    prodvers={version},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'SKT'),
         StringStruct('FileDescription', 'Mider - 폐쇄망 소스코드 분석 CLI'),
         StringStruct('FileVersion', '{version_str}'),
         StringStruct('InternalName', 'mider'),
         StringStruct('LegalCopyright', 'Copyright (c) SKT'),
         StringStruct('OriginalFilename', 'mider.exe'),
         StringStruct('ProductName', 'Mider'),
         StringStruct('ProductVersion', '{version_str}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main() -> None:
    version = read_version()
    OUTPUT.write_text(render(version), encoding="utf-8")
    print(f"wrote {OUTPUT} (version={'.'.join(str(n) for n in version[:3])})")


if __name__ == "__main__":
    main()
