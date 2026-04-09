"""Mider 단일 실행파일 빌드 스크립트.

사용법:
    python scripts/build_exe.py

사전 조건:
    프로젝트 루트에 .env 파일이 존재해야 합니다 (API 키 포함).
    .env 파일은 실행파일 내부에 번들링되어 외부에 노출되지 않습니다.

결과:
    dist/mider (단일 실행파일) 생성
"""

import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# 프로젝트 루트 (이 스크립트 기준 한 단계 상위)
ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = ROOT / "mider.spec"
ENV_FILE = ROOT / ".env"
DIST_EXE = ROOT / "dist" / "mider"


def check_env_file() -> None:
    """.env 파일 존재 여부를 확인한다."""
    if not ENV_FILE.exists():
        logger.error(f".env 파일을 찾을 수 없습니다: {ENV_FILE}")
        logger.error("빌드 전에 .env 파일을 생성하고 API 키를 설정하세요.")
        logger.error(f"  cp {ROOT / '.env.example'} {ENV_FILE}")
        sys.exit(1)

    logger.info(f".env 파일 확인: {ENV_FILE}")


def run_pyinstaller() -> None:
    """PyInstaller를 실행하여 단일 실행파일을 빌드한다."""
    if not SPEC_FILE.exists():
        logger.error(f"spec 파일을 찾을 수 없습니다: {SPEC_FILE}")
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("Mider 단일 실행파일 빌드 시작")
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


def main() -> None:
    """빌드 전체 프로세스를 실행한다."""
    check_env_file()
    run_pyinstaller()

    logger.info("")
    logger.info("=" * 50)
    logger.info("빌드 완료!")
    logger.info(f"  실행파일: {DIST_EXE}")
    logger.info("")
    logger.info("사용법:")
    logger.info(f"  {DIST_EXE} -f <분석할 파일들>")
    logger.info("")
    logger.info("참고:")
    logger.info("  - API 키가 실행파일 내부에 포함되어 있습니다.")
    logger.info("  - 실행파일 하나만 배포하면 됩니다.")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
