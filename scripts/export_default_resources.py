#!/usr/bin/env python3
"""기본 리소스(프롬프트/룰/Skill)를 번들에서 외부 디렉토리로 내보내는 스크립트.

배포된 Mider exe에 번들로 들어있는 기본 리소스를 실행파일 옆으로 복사해서
사용자가 커스터마이징할 수 있게 만드는 유틸리티.

## 사용법

개발 환경 (mider 패키지 설치된 상태):
    python scripts/export_default_resources.py [--output DIR]

PyInstaller 번들된 exe 옆에서 실행:
    python export_default_resources.py

## 출력

기본적으로 현재 작업 디렉토리에 다음 3개 디렉토리를 생성:
- mider_prompts/   — *.txt 프롬프트 파일
- mider_rules/     — *.yaml 룰 파일 (존재하는 경우)
- mider_skills/    — *.md Skill 파일 (존재하는 경우)

커스터마이징 후 런타임에 환경변수(`MIDER_PROMPTS_PATH` 등)로 지정하거나
exe 옆 디렉토리에 그대로 두면 자동 인식된다.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# 개발 환경에서 직접 실행 시 mider 패키지 경로 추가
# (PyInstaller frozen 환경은 번들에 mider가 포함되어 자동 import 가능)
if not getattr(sys, "frozen", False):
    _REPO_ROOT = Path(__file__).resolve().parent.parent
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from mider.config.resource_path import (
    BUNDLED_PROMPTS_DIR,
    BUNDLED_RULES_DIR,
    BUNDLED_SKILLS_DIR,
    EXE_PROMPTS_DIRNAME,
    EXE_RULES_DIRNAME,
    EXE_SKILLS_DIRNAME,
)


def _copy_resources(src: Path, dst: Path, extension: str) -> int:
    """src 디렉토리의 extension 파일을 dst로 복사한다.

    Returns:
        복사된 파일 개수.
    """
    if not src.exists():
        print(f"  (skip) 번들 디렉토리 없음: {src}")
        return 0

    dst.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in src.glob(f"*.{extension}"):
        target = dst / f.name
        shutil.copy2(f, target)
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mider 기본 리소스를 외부 디렉토리로 내보낸다.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path.cwd(),
        help="리소스를 내보낼 기준 디렉토리 (기본: 현재 작업 디렉토리)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="대상 디렉토리가 이미 존재해도 덮어쓴다",
    )
    args = parser.parse_args()

    output_base: Path = args.output.resolve()
    output_base.mkdir(parents=True, exist_ok=True)

    resources = [
        (BUNDLED_PROMPTS_DIR, output_base / EXE_PROMPTS_DIRNAME, "txt", "프롬프트"),
        (BUNDLED_RULES_DIR, output_base / EXE_RULES_DIRNAME, "yaml", "룰"),
        (BUNDLED_SKILLS_DIR, output_base / EXE_SKILLS_DIRNAME, "md", "Skill"),
    ]

    print(f"기본 리소스 export 시작 → {output_base}")
    total = 0
    for src, dst, ext, label in resources:
        if dst.exists() and not args.force and any(dst.iterdir()):
            print(f"  (skip) {label}: {dst} 이미 존재 (--force로 덮어쓰기)")
            continue
        count = _copy_resources(src, dst, ext)
        print(f"  {label}: {count}개 복사 → {dst}")
        total += count

    print(f"완료: 총 {total}개 파일 export")
    print()
    print("커스터마이징 방법:")
    print(f"  1. {output_base} 안의 파일을 수정")
    print(f"  2. 환경변수로 직접 지정: MIDER_PROMPTS_PATH={output_base / EXE_PROMPTS_DIRNAME}")
    print(f"  3. 또는 exe 옆에 두면 자동 인식 (PyInstaller 배포 환경)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
