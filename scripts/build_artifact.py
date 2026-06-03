#!/usr/bin/env python3
"""
아티팩트 빌드 스크립트.

TGU 배포 요구사항:
  - PyPI 접근 불가 (사이버보안 정책)
  - stream-manager SDK 미설치 상태
  - 따라서 런타임 의존성 wheel을 zip에 포함하는 번들 빌드가 기본이다.

수행 작업:
  1. src/spooler/ 소스를 zip으로 패키징
  2. 타겟 플랫폼용 런타임 의존성 wheel 다운로드 및 zip에 포함
  3. SHA-256 다이제스트 계산
  4. recipe-template.yaml을 채워 최종 레시피 생성
  5. dist/ 하위에 빌드 산출물 저장

사용법:
  python scripts/build_artifact.py                           # TGU용 기본 빌드 (ARM64 Linux)
  python scripts/build_artifact.py --platform tgu-armv7l    # 구형 ARM32 TGU
  python scripts/build_artifact.py --platform local         # 현재 머신 플랫폼 (개발용)
  python scripts/build_artifact.py --version 1.2.0
  python scripts/build_artifact.py --dry-run

플랫폼 프리셋:
  tgu-arm64   manylinux2014_aarch64 + cp311  (기본값, 현대 TGU ARM64 Linux, Python 3.11)
  tgu-armv7l  linux_armv7l + cp311           (구형 TGU ARM32 Linux, Python 3.11)
  local       현재 머신 (개발/CI 테스트용, TGU 배포 불가)

⚠️  TGU Python 버전 및 아키텍처 확인 필요:
  docs/research/06-tgu-platform-constraints.md 의 확인 절차 참고
"""

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# TGU 배포 런타임 의존성 (pydantic 제외 — 현재 코드에서 미사용)
RUNTIME_DEPS = [
    "watchdog>=4.0.0,<6.0",     # 메이저 버전 제약 추가
    "stream-manager>=1.1.1,<2.0",  # AWS 정책에 맞춰 제약
    "cbor2==5.4.6",             # 순수 Python 버전 강제 (244KB → 20KB 절약)
]

# 플랫폼 프리셋: (pip --platform, pip --abi) 튜플
PLATFORM_PRESETS: dict[str, tuple[str, str]] = {
    "tgu-arm64": ("manylinux2014_aarch64", "cp311"),   # 기본: ARM64 Linux Python 3.11
    "tgu-armv7l": ("linux_armv7l", "cp311"),            # ARM32 Linux Python 3.11
    "local": ("", ""),                                   # 현재 머신
}


def read_version() -> str:
    spec = importlib.util.spec_from_file_location(
        "spooler_init", PROJECT_ROOT / "src" / "spooler" / "__init__.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return str(mod.__version__)


def read_deploy_config(env: str | None = None) -> dict:
    filename = f"deploy.{env}.yaml" if env else "deploy.yaml"
    deploy_yaml = PROJECT_ROOT / filename
    if not deploy_yaml.exists():
        print(f"[WARN] {filename} 없음 — S3 URI 플레이스홀더가 그대로 유지됩니다.")
        return {}
    try:
        import importlib.util as _iu
        if _iu.find_spec("yaml") is None:
            raise ImportError
        import yaml  # type: ignore[import]
        with deploy_yaml.open() as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        text = deploy_yaml.read_text()
        result: dict = {"component": {}}
        for line in text.splitlines():
            for key in ("name", "s3_bucket", "s3_prefix", "region"):
                if line.strip().startswith(f"{key}:"):
                    val = line.split(":", 1)[1].strip().strip('"')
                    result["component"][key] = val
        return result


def download_deps(deps_dir: Path, platform_preset: str) -> list[Path]:
    """
    타겟 플랫폼용 런타임 wheel을 다운로드한다.

    TGU는 PyPI 접근이 불가하므로 빌드 머신에서 미리 다운로드한다.
    플랫폼 프리셋에 따라 크로스-플랫폼 wheel을 지정한다.
    """
    plat, abi = PLATFORM_PRESETS.get(platform_preset, PLATFORM_PRESETS["tgu-arm64"])
    deps_dir.mkdir(parents=True, exist_ok=True)

    # 기존 캐시 삭제 후 새로 다운로드
    for old in deps_dir.glob("*.whl"):
        old.unlink()

    cmd = ["pip", "download", "--dest", str(deps_dir)]
    if plat:
        cmd += ["--platform", plat, "--abi", abi, "--python-version", "311",
                "--only-binary", ":all:"]
    cmd += RUNTIME_DEPS

    print(f"[BUILD] wheel 다운로드: 플랫폼={platform_preset or 'local'}")
    if plat:
        print(f"        target={plat} abi={abi} python=311")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] wheel 다운로드 실패:\n{result.stderr}", file=sys.stderr)
        if platform_preset != "local" and "no matching distribution" in result.stderr.lower():
            print(f"\n[HINT] 해결 방법:", file=sys.stderr)
            print(f"       1. TGU Python 버전 확인 (현재 --abi cp311 지정)", file=sys.stderr)
            print(f"       2. --platform local 로 빌드 후 TGU에서 직접 설치 시도", file=sys.stderr)
            print(f"       3. docs/research/06-tgu-platform-constraints.md 참고", file=sys.stderr)
        sys.exit(1)

    wheels = sorted(deps_dir.glob("*.whl"))
    for w in wheels:
        print(f"[BUILD]   + {w.name}")
    print(f"[BUILD] {len(wheels)}개 wheel 준비 완료")
    return wheels


def build_zip(
    version: str,
    component_name: str,
    output_dir: Path,
    platform_preset: str,
) -> Path:
    src = PROJECT_ROOT / "src" / "spooler"
    artifact_dir = output_dir / "artifacts" / component_name / version
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # 플랫폼 정보를 파일명에 포함 (여러 플랫폼 빌드 공존 가능)
    platform_tag = "" if platform_preset == "tgu-arm64" else f"-{platform_preset}"
    zip_name = f"ggc-s3-spooler-{version}{platform_tag}.zip"
    zip_path = artifact_dir / zip_name

    deps_dir = output_dir / "deps" / platform_preset
    wheels = download_deps(deps_dir, platform_preset)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 소스 코드: spooler/xxx.py
        # src = src/spooler 만 globbing → 형제 패키지 src/spooler_testing 는 자동 제외.
        for py_file in sorted(src.rglob("*.py")):
            arcname = py_file.relative_to(src.parent)
            zf.write(py_file, arcname)

        # CAN Blackbox 패턴: INI 설정 파일
        ini_file = PROJECT_ROOT / "spooler.ini"
        if ini_file.exists():
            zf.write(ini_file, "spooler.ini")
            print(f"[BUILD]   + spooler.ini")
        else:
            print(f"[WARN] spooler.ini 파일이 없습니다 — 기본값이 사용됩니다.")

        # 의존성 wheel: deps/xxx.whl
        for wheel in wheels:
            zf.write(wheel, f"deps/{wheel.name}")

    # 가드: 테스트/벤치마크 코드가 번들에 누출되지 않았는지 검증 (재오염 방지).
    # deps/ wheel 파일명은 검사 대상에서 제외한다.
    forbidden = ("mock", "testing", "benchmark", "metrics", "psutil")
    with zipfile.ZipFile(zip_path) as zf:
        leaked = [
            name for name in zf.namelist()
            if not name.startswith("deps/")
            and any(token in name.lower() for token in forbidden)
        ]
    if leaked:
        zip_path.unlink(missing_ok=True)
        print(
            f"[ERROR] 빌드 가드 실패 — 테스트/벤치마크 코드가 번들에 포함됨:\n"
            f"        {leaked}\n"
            f"        해당 코드는 src/spooler_testing/ 로 분리되어야 합니다.",
            file=sys.stderr,
        )
        sys.exit(1)

    size_kb = zip_path.stat().st_size // 1024
    print(f"[BUILD] 아티팩트: {zip_path.relative_to(PROJECT_ROOT)}  ({size_kb} KB)")
    return zip_path


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def render_recipe(
    version: str,
    component_name: str,
    zip_path: Path,
    digest: str,
    deploy_cfg: dict,
    output_dir: Path,
) -> Path:
    template_path = PROJECT_ROOT / "recipe-template.yaml"
    if not template_path.exists():
        print("[ERROR] recipe-template.yaml 를 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(1)

    comp = deploy_cfg.get("component", {})
    s3_bucket = comp.get("s3_bucket", "REPLACE_WITH_YOUR_BUCKET")
    s3_prefix = comp.get("s3_prefix", "artifacts").rstrip("/")
    s3_uri = f"s3://{s3_bucket}/{s3_prefix}/{component_name}/{version}/{zip_path.name}"

    recipe_text = (
        template_path.read_text()
        .replace("@@COMPONENT_NAME@@", component_name)
        .replace("@@COMPONENT_VERSION@@", version)
        .replace("@@ARTIFACT_S3_URI@@", s3_uri)
        .replace("@@SHA256_DIGEST@@", digest)
    )

    recipe_dir = output_dir / "recipes"
    recipe_dir.mkdir(parents=True, exist_ok=True)
    recipe_path = recipe_dir / f"{component_name}-{version}.yaml"
    recipe_path.write_text(recipe_text)
    print(f"[BUILD] 레시피:    {recipe_path.relative_to(PROJECT_ROOT)}")
    return recipe_path


def write_manifest(
    version: str,
    component_name: str,
    zip_path: Path,
    recipe_path: Path,
    digest: str,
    platform_preset: str,
    output_dir: Path,
) -> Path:
    manifest = {
        "component_name": component_name,
        "version": version,
        "platform": platform_preset,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "artifact": str(zip_path.relative_to(PROJECT_ROOT)),
        "recipe": str(recipe_path.relative_to(PROJECT_ROOT)),
        "sha256": digest,
    }
    manifest_path = output_dir / f"build-manifest-{version}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[BUILD] 매니페스트: {manifest_path.relative_to(PROJECT_ROOT)}")
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GGC S3 Spooler 아티팩트 빌드",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
플랫폼 프리셋:
  tgu-arm64   ARM64 Linux + Python 3.11 (기본값, 현대 TGU)
  tgu-armv7l  ARM32 Linux + Python 3.11 (구형 TGU)
  local       현재 머신 (개발 테스트 전용, TGU 배포 불가)

⚠️  TGU Python 버전이 3.11이 아닌 경우 cbor2 C 확장 호환성 문제 발생 가능.
    docs/research/06-tgu-platform-constraints.md 참고.
        """,
    )
    parser.add_argument("--version", help="빌드할 버전 (미지정 시 __init__.py에서 읽음)")
    parser.add_argument(
        "--platform",
        default="tgu-arm64",
        choices=list(PLATFORM_PRESETS.keys()),
        help="타겟 플랫폼 프리셋 (기본: tgu-arm64)",
    )
    parser.add_argument("--env", default=None,
                        help="배포 환경 (staging/prod). deploy.{env}.yaml 사용")
    parser.add_argument("--dry-run", action="store_true",
                        help="파일 생성 없이 계획만 출력")
    args = parser.parse_args()

    version = args.version or read_version()
    deploy_cfg = read_deploy_config(env=args.env)
    component_name = deploy_cfg.get("component", {}).get("name", "com.example.S3Spooler")
    output_dir = PROJECT_ROOT / "dist"

    print(f"[BUILD] 컴포넌트: {component_name}  버전: {version}")
    print(f"[BUILD] 타겟 플랫폼: {args.platform}")
    if args.platform == "local":
        print("[WARN]  'local' 플랫폼은 TGU 배포에 사용할 수 없습니다 (아키텍처 불일치 가능).")

    if args.dry_run:
        print("[BUILD] --dry-run 모드: 파일을 생성하지 않습니다.")
        return

    zip_path = build_zip(version, component_name, output_dir, platform_preset=args.platform)
    digest = sha256_of(zip_path)
    print(f"[BUILD] SHA-256: {digest}")
    recipe_path = render_recipe(version, component_name, zip_path, digest, deploy_cfg, output_dir)
    write_manifest(version, component_name, zip_path, recipe_path, digest, args.platform, output_dir)
    print(f"\n[BUILD] 완료 — dist/ 에 번들 아티팩트가 생성되었습니다.")
    print(f"         다음 단계: make deploy-aws  또는  make deploy-local")


if __name__ == "__main__":
    main()
