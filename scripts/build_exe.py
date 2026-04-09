"""Mider 실행파일 빌드 스크립트.

사용법:
    python scripts/build_exe.py

결과:
    dist/mider/ 디렉토리에 실행파일 + input/ + output/ + .env.example 생성
"""

import logging
import shutil
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# 프로젝트 루트 (이 스크립트 기준 한 단계 상위)
ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = ROOT / "mider.spec"
DIST_DIR = ROOT / "dist" / "mider"


def run_pyinstaller() -> None:
    """PyInstaller를 실행하여 실행파일을 빌드한다."""
    if not SPEC_FILE.exists():
        logger.error(f"spec 파일을 찾을 수 없습니다: {SPEC_FILE}")
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("Mider 실행파일 빌드 시작")
    logger.info("=" * 50)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        str(SPEC_FILE),
    ]

    logger.info(f"명령: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(ROOT))

    if result.returncode != 0:
        logger.error("PyInstaller 빌드 실패")
        sys.exit(result.returncode)

    logger.info("PyInstaller 빌드 완료")


def setup_dist_folders() -> None:
    """dist 폴더에 input/, output/ 폴더를 생성한다."""
    if not DIST_DIR.exists():
        logger.error(f"dist 디렉토리가 없습니다: {DIST_DIR}")
        sys.exit(1)

    input_dir = DIST_DIR / "input"
    output_dir = DIST_DIR / "output"

    input_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    logger.info(f"폴더 생성: {input_dir}")
    logger.info(f"폴더 생성: {output_dir}")


def copy_env_example() -> None:
    """프로젝트 루트의 .env.example을 dist 폴더에 복사한다."""
    src = ROOT / ".env.example"
    dst = DIST_DIR / ".env.example"

    if src.exists():
        shutil.copy2(str(src), str(dst))
        logger.info(f"복사: {src} → {dst}")
    else:
        logger.warning(f".env.example이 없습니다: {src}")


def main() -> None:
    """빌드 전체 프로세스를 실행한다."""
    run_pyinstaller()
    setup_dist_folders()
    copy_env_example()

    logger.info("")
    logger.info("=" * 50)
    logger.info("빌드 완료!")
    logger.info(f"  출력 경로: {DIST_DIR}")
    logger.info("")
    logger.info("배포 전 확인사항:")
    logger.info("  1. dist/mider/.env.example → .env로 복사 후 API 키 설정")
    logger.info("  2. dist/mider/input/ 폴더에 분석할 소스 파일 배치")
    logger.info("  3. dist/mider/mider.exe 실행")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
