#!/usr/bin/env python3
"""
AWS Greengrass 컴포넌트 배포 스크립트.

수행 작업:
  1. dist/ 에서 빌드 산출물 탐색
  2. 아티팩트 zip을 S3에 업로드
  3. 레시피에 최종 S3 URI와 다이제스트 삽입
  4. AWS IoT Greengrass에 컴포넌트 버전 등록
  5. (--deploy 옵션) 대상 Thing/ThingGroup에 배포 생성

사전 요구사항:
  - aws configure 또는 환경변수(AWS_ACCESS_KEY_ID 등) 설정
  - boto3 설치 (pip install boto3)
  - deploy.yaml 에 실제 버킷/ARN 설정 완료

사용법:
  python scripts/deploy_aws.py
  python scripts/deploy_aws.py --version 1.0.0
  python scripts/deploy_aws.py --deploy               # 컴포넌트 등록 + 디바이스 배포
  python scripts/deploy_aws.py --only-register        # S3 업로드 + 컴포넌트 등록만 (배포 없음)
  python scripts/deploy_aws.py --dry-run              # 실제 AWS 호출 없이 계획만 출력
"""

import argparse
import base64
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────

def load_deploy_config(dry_run: bool = False, env: str | None = None) -> dict:
    # D-02: 환경별 설정 파일 선택
    filename = f"deploy.{env}.yaml" if env else "deploy.yaml"
    deploy_yaml = PROJECT_ROOT / filename
    if not deploy_yaml.exists():
        print(f"[ERROR] {filename} 이 없습니다. deploy.yaml.example 을 복사하여 작성하세요.", file=sys.stderr)
        sys.exit(1)
    try:
        import yaml  # type: ignore[import]
        with deploy_yaml.open() as f:
            cfg = yaml.safe_load(f) or {}
    except ImportError:
        print("[ERROR] pyyaml 이 필요합니다: pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    if not dry_run:
        comp = cfg.get("component", {})
        for required_key in ("s3_bucket", "name", "region"):
            if not comp.get(required_key) or "REPLACE" in str(comp.get(required_key, "")):
                print(f"[ERROR] deploy.yaml 의 component.{required_key} 를 실제 값으로 채워주세요.", file=sys.stderr)
                sys.exit(1)
    return cfg


def find_manifest(version: str | None, component_name: str) -> dict:
    dist = PROJECT_ROOT / "dist"
    if version:
        manifest_path = dist / f"build-manifest-{version}.json"
    else:
        manifests = sorted(dist.glob("build-manifest-*.json"))
        if not manifests:
            print("[ERROR] dist/ 에 빌드 산출물이 없습니다. 먼저 'make build' 를 실행하세요.", file=sys.stderr)
            sys.exit(1)
        manifest_path = manifests[-1]  # 가장 최신 매니페스트

    if not manifest_path.exists():
        print(f"[ERROR] 매니페스트를 찾을 수 없습니다: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text())
    print(f"[DEPLOY] 빌드 매니페스트: {manifest_path.name}")
    print(f"[DEPLOY] 컴포넌트: {manifest['component_name']}  버전: {manifest['version']}")
    return manifest


def upload_to_s3(
    artifact_path: Path,
    component_name: str,
    version: str,
    cfg: dict,
    dry_run: bool,
) -> str:
    comp = cfg["component"]
    bucket = comp["s3_bucket"]
    prefix = comp.get("s3_prefix", "artifacts").rstrip("/")
    region = comp["region"]

    s3_key = f"{prefix}/{component_name}/{version}/{artifact_path.name}"
    s3_uri = f"s3://{bucket}/{s3_key}"

    if dry_run:
        print(f"[DRY-RUN] S3 업로드 생략: {s3_uri}")
        return s3_uri

    try:
        import boto3  # type: ignore[import]
    except ImportError:
        print("[ERROR] boto3 가 필요합니다: pip install boto3", file=sys.stderr)
        sys.exit(1)

    s3 = boto3.client("s3", region_name=region)
    print(f"[DEPLOY] S3 업로드: {artifact_path.name} → {s3_uri}")
    s3.upload_file(
        str(artifact_path),
        bucket,
        s3_key,
        ExtraArgs={"ServerSideEncryption": "AES256"},
    )
    print(f"[DEPLOY] 업로드 완료")
    return s3_uri


def render_final_recipe(
    recipe_template_path: Path,
    s3_uri: str,
    digest: str,
    component_name: str,
    version: str,
) -> str:
    """dist/recipes 의 레시피에서 S3 URI와 다이제스트를 최종값으로 교체한다."""
    text = recipe_template_path.read_text()
    return (
        text
        .replace("@@ARTIFACT_S3_URI@@", s3_uri)
        .replace("@@SHA256_DIGEST@@", digest)
        .replace("@@COMPONENT_NAME@@", component_name)
        .replace("@@COMPONENT_VERSION@@", version)
    )


def register_component(
    recipe_text: str,
    region: str,
    dry_run: bool,
) -> str:
    """greengrassv2:create_component_version — 레시피를 base64 인코딩하여 등록."""
    recipe_b64 = base64.b64encode(recipe_text.encode()).decode()

    if dry_run:
        print("[DRY-RUN] Greengrass 컴포넌트 등록 생략")
        return "arn:aws:greengrass:DRYRUN"

    try:
        import boto3  # type: ignore[import]
    except ImportError:
        print("[ERROR] boto3 가 필요합니다: pip install boto3", file=sys.stderr)
        sys.exit(1)

    gg = boto3.client("greengrassv2", region_name=region)
    print("[DEPLOY] Greengrass 컴포넌트 버전 등록 중...")
    resp = gg.create_component_version(inlineRecipe=recipe_b64)
    arn = resp["arn"]
    status = resp.get("status", {}).get("componentState", "UNKNOWN")
    print(f"[DEPLOY] 등록 완료: {arn}  (상태: {status})")
    return arn


def create_deployment(
    component_name: str,
    version: str,
    cfg: dict,
    region: str,
    dry_run: bool,
) -> None:
    depl = cfg.get("deployment", {})
    target_arn = depl.get("target_arn", "")
    if not target_arn or "REPLACE" in target_arn:
        print("[WARN] deploy.yaml 의 deployment.target_arn 이 설정되지 않아 배포를 건너뜁니다.")
        return

    deployment_name = depl.get("name", f"{component_name}-deployment")
    config_override = depl.get("config_override") or {}

    component_update: dict = {"componentVersion": version}
    if config_override:
        component_update["configurationUpdate"] = {
            "merge": json.dumps(config_override)
        }

    payload = {
        "targetArn": target_arn,
        "deploymentName": deployment_name,
        "components": {component_name: component_update},
    }

    if dry_run:
        print(f"[DRY-RUN] 배포 생성 생략:")
        print(f"          대상: {target_arn}")
        print(f"          컴포넌트: {component_name}={version}")
        return

    try:
        import boto3  # type: ignore[import]
    except ImportError:
        print("[ERROR] boto3 가 필요합니다: pip install boto3", file=sys.stderr)
        sys.exit(1)

    gg = boto3.client("greengrassv2", region_name=region)
    print(f"[DEPLOY] 배포 생성: {deployment_name} → {target_arn}")
    resp = gg.create_deployment(**payload)
    deployment_id = resp["deploymentId"]
    print(f"[DEPLOY] 배포 ID: {deployment_id}")
    return deployment_id


def poll_deployment_status(
    deployment_id: str,
    region: str,
    timeout_seconds: int = 300,
    poll_interval: int = 10,
) -> str:
    """D-04: 배포 완료까지 상태를 폴링한다. 최종 상태를 반환한다."""
    import time
    try:
        import boto3  # type: ignore[import]
    except ImportError:
        print("[ERROR] boto3 가 필요합니다: pip install boto3", file=sys.stderr)
        return "UNKNOWN"

    gg = boto3.client("greengrassv2", region_name=region)
    elapsed = 0
    print(f"[MONITOR] 배포 상태 모니터링 시작 (최대 {timeout_seconds}초, {poll_interval}초 간격)")

    terminal_states = {"COMPLETED", "FAILED", "CANCELED", "INACTIVE"}

    while elapsed < timeout_seconds:
        try:
            resp = gg.get_deployment(deploymentId=deployment_id)
            status = resp.get("deploymentStatus", "UNKNOWN")
            print(f"[MONITOR] {elapsed:>4}s  상태: {status}")

            if status in terminal_states:
                if status == "COMPLETED":
                    print(f"[MONITOR] 배포 성공 ✓")
                else:
                    print(f"[MONITOR] 배포 실패 ✗  상태: {status}")
                return status
        except Exception as exc:
            print(f"[MONITOR] 상태 조회 오류: {exc}")

        time.sleep(poll_interval)
        elapsed += poll_interval

    print(f"[MONITOR] 타임아웃 ({timeout_seconds}초) — 배포가 아직 완료되지 않았습니다.")
    print(f"          AWS IoT 콘솔에서 배포 ID {deployment_id} 를 직접 확인하세요.")
    return "TIMEOUT"


# ──────────────────────────────────────────────
# 엔트리포인트
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="AWS Greengrass 컴포넌트 배포")
    parser.add_argument("--version", help="배포할 버전 (미지정 시 최신 빌드)")
    parser.add_argument("--env", default=None,
                        help="D-02: 배포 환경 (staging/prod). deploy.{env}.yaml 사용")
    parser.add_argument("--deploy", action="store_true",
                        help="컴포넌트 등록 후 대상 디바이스에 즉시 배포")
    parser.add_argument("--only-register", action="store_true",
                        help="S3 업로드 + 컴포넌트 등록만 (배포 없음, 기본값)")
    parser.add_argument("--monitor", action="store_true",
                        help="D-04: 배포 후 완료까지 상태 모니터링 (--deploy 와 함께 사용)")
    parser.add_argument("--monitor-timeout", type=int, default=300,
                        help="D-04: 모니터링 타임아웃 초 (기본 300)")
    parser.add_argument("--dry-run", action="store_true",
                        help="AWS 호출 없이 계획만 출력")
    args = parser.parse_args()

    # D-02: 환경별 설정 파일 선택
    cfg = load_deploy_config(dry_run=args.dry_run, env=args.env)
    component_name = cfg["component"].get("name", "com.example.S3Spooler")
    region = cfg["component"].get("region", "ap-northeast-2")

    manifest = find_manifest(args.version, component_name)
    version = manifest["version"]
    digest = manifest["sha256"]

    artifact_path = PROJECT_ROOT / manifest["artifact"]
    recipe_path = PROJECT_ROOT / manifest["recipe"]

    if not artifact_path.exists():
        print(f"[ERROR] 아티팩트 파일 없음: {artifact_path}\n먼저 'make build' 를 실행하세요.", file=sys.stderr)
        sys.exit(1)

    # 1. S3 업로드
    s3_uri = upload_to_s3(artifact_path, component_name, version, cfg, args.dry_run)

    # 2. 최종 레시피 렌더링
    final_recipe = render_final_recipe(recipe_path, s3_uri, digest, component_name, version)

    # 3. Greengrass 컴포넌트 등록
    register_component(final_recipe, region, args.dry_run)

    # 4. 디바이스 배포 (--deploy 시에만)
    if args.deploy:
        deployment_id = create_deployment(component_name, version, cfg, region, args.dry_run)
        # D-04: --monitor 옵션 시 배포 완료까지 폴링
        if args.monitor and deployment_id and not args.dry_run:
            final_status = poll_deployment_status(
                deployment_id, region, timeout_seconds=args.monitor_timeout
            )
            if final_status not in ("COMPLETED", "DRYRUN"):
                sys.exit(1)
    else:
        print(f"\n[DEPLOY] 컴포넌트가 등록되었습니다.")
        print(f"         디바이스에 배포하려면: make deploy-full  또는  --deploy 옵션 추가")

    print(f"\n[DEPLOY] 완료 ✓  {component_name}=={version}")


if __name__ == "__main__":
    main()
