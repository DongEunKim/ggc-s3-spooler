#!/usr/bin/env python3
"""
버전 범프 스크립트.

단일 진실 원천: src/spooler/__init__.py 의 __version__
pyproject.toml 의 version 필드도 동기화한다.

사용법:
  python scripts/bump_version.py 1.2.0
  python scripts/bump_version.py --show           # 현재 버전 출력
  python scripts/bump_version.py --patch          # 패치 버전 자동 증가 (1.0.0 → 1.0.1)
  python scripts/bump_version.py --minor          # 마이너 버전 자동 증가 (1.0.0 → 1.1.0)
  python scripts/bump_version.py --major          # 메이저 버전 자동 증가 (1.0.0 → 2.0.0)
"""

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
INIT_FILE = PROJECT_ROOT / "src" / "spooler" / "__init__.py"
PYPROJECT_FILE = PROJECT_ROOT / "pyproject.toml"


def current_version() -> str:
    text = INIT_FILE.read_text()
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not m:
        print("[ERROR] __version__ 을 src/spooler/__init__.py 에서 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(1)
    return m.group(1)


def bump_part(version: str, part: str) -> str:
    parts = version.split(".")
    if len(parts) != 3:
        print(f"[ERROR] 버전 형식이 X.Y.Z 여야 합니다: {version}", file=sys.stderr)
        sys.exit(1)
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def validate_semver(version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"[ERROR] 유효하지 않은 버전 형식: {version!r}  (기대: X.Y.Z)", file=sys.stderr)
        sys.exit(1)


def update_init(new_version: str) -> None:
    text = INIT_FILE.read_text()
    updated = re.sub(
        r'^(__version__\s*=\s*)["\'][^"\']+["\']',
        f'\\g<1>"{new_version}"',
        text,
        flags=re.MULTILINE,
    )
    INIT_FILE.write_text(updated)
    print(f"[BUMP] {INIT_FILE.relative_to(PROJECT_ROOT)}: __version__ = \"{new_version}\"")


def update_pyproject(new_version: str) -> None:
    text = PYPROJECT_FILE.read_text()
    updated = re.sub(
        r'^(version\s*=\s*)["\'][^"\']+["\']',
        f'\\g<1>"{new_version}"',
        text,
        flags=re.MULTILINE,
    )
    PYPROJECT_FILE.write_text(updated)
    print(f"[BUMP] {PYPROJECT_FILE.relative_to(PROJECT_ROOT)}: version = \"{new_version}\"")


def main() -> None:
    parser = argparse.ArgumentParser(description="버전 범프 유틸리티")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("version", nargs="?", help="목표 버전 (예: 1.2.0)")
    group.add_argument("--show", action="store_true", help="현재 버전 출력")
    group.add_argument("--patch", action="store_true", help="패치 버전 증가")
    group.add_argument("--minor", action="store_true", help="마이너 버전 증가")
    group.add_argument("--major", action="store_true", help="메이저 버전 증가")
    args = parser.parse_args()

    cur = current_version()

    if args.show:
        print(cur)
        return

    if args.patch:
        new_version = bump_part(cur, "patch")
    elif args.minor:
        new_version = bump_part(cur, "minor")
    elif args.major:
        new_version = bump_part(cur, "major")
    elif args.version:
        new_version = args.version
    else:
        parser.print_help()
        sys.exit(0)

    validate_semver(new_version)
    print(f"[BUMP] {cur} → {new_version}")
    update_init(new_version)
    update_pyproject(new_version)
    print(f"\n[BUMP] 완료. 다음 단계:")
    print(f"         git add src/spooler/__init__.py pyproject.toml")
    print(f"         git commit -m 'chore: bump version to {new_version}'")
    print(f"         git tag v{new_version}")
    print(f"         git push origin main v{new_version}")


if __name__ == "__main__":
    main()
