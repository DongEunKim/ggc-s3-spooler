#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# 로컬 Greengrass Core Device 수동 배포 스크립트
#
# 실제 Core Device에서 실행한다. S3 없이 로컬 파일로 배포하므로
# 개발 중 빠른 반복 테스트에 적합하다.
#
# 사용법:
#   ./scripts/deploy_local.sh                # dist/ 의 최신 빌드 배포
#   ./scripts/deploy_local.sh 1.0.0          # 특정 버전 배포
#   ./scripts/deploy_local.sh --remove       # 컴포넌트 제거
#   ./scripts/deploy_local.sh --status       # 배포 상태 확인
#
# 사전 요구사항:
#   - Greengrass Nucleus 실행 중 (/greengrass/v2)
#   - greengrass-cli 설치됨
#   - 이 스크립트는 root 또는 sudo 권한 필요
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${PROJECT_ROOT}/dist"
GGC_CLI="/greengrass/v2/bin/greengrass-cli"
COMPONENT_NAME="com.example.S3Spooler"

# deploy.yaml 에서 컴포넌트 이름 오버라이드 (있으면)
if command -v python3 &>/dev/null && [[ -f "${PROJECT_ROOT}/deploy.yaml" ]]; then
    _name=$(python3 -c "
import sys
try:
    import yaml
    c = yaml.safe_load(open('${PROJECT_ROOT}/deploy.yaml'))
    print(c.get('component',{}).get('name',''))
except Exception:
    pass
" 2>/dev/null || true)
    [[ -n "${_name}" ]] && COMPONENT_NAME="${_name}"
fi

# ──────────────────────────────────────────────
# 상태 확인
# ──────────────────────────────────────────────
cmd_status() {
    echo "[STATUS] 컴포넌트 목록 조회..."
    sudo "${GGC_CLI}" component list
}

# ──────────────────────────────────────────────
# 컴포넌트 제거
# ──────────────────────────────────────────────
cmd_remove() {
    echo "[REMOVE] ${COMPONENT_NAME} 제거 중..."
    sudo "${GGC_CLI}" deployment create --remove "${COMPONENT_NAME}"
    echo "[REMOVE] 제거 요청 완료. 'sudo ${GGC_CLI} deployment status' 로 확인하세요."
}

# ──────────────────────────────────────────────
# 배포
# ──────────────────────────────────────────────
cmd_deploy() {
    local version="${1:-}"

    # 최신 매니페스트 탐색
    if [[ -n "${version}" ]]; then
        manifest="${DIST_DIR}/build-manifest-${version}.json"
    else
        manifest=$(ls -t "${DIST_DIR}"/build-manifest-*.json 2>/dev/null | head -1 || true)
    fi

    if [[ -z "${manifest}" || ! -f "${manifest}" ]]; then
        echo "[ERROR] 빌드 산출물이 없습니다."
        echo "        먼저 개발 머신에서 'make build' 를 실행하고 dist/ 를 이 디바이스에 복사하세요."
        exit 1
    fi

    version=$(python3 -c "import json,sys; print(json.load(open('${manifest}'))['version'])")
    artifact_rel=$(python3 -c "import json,sys; print(json.load(open('${manifest}'))['artifact'])")
    recipe_rel=$(python3 -c "import json,sys; print(json.load(open('${manifest}'))['recipe'])")

    artifact_path="${PROJECT_ROOT}/${artifact_rel}"
    recipe_path="${PROJECT_ROOT}/${recipe_rel}"
    recipe_dir=$(dirname "${recipe_path}")
    artifact_dir=$(dirname "${artifact_path}")

    if [[ ! -f "${artifact_path}" ]]; then
        echo "[ERROR] 아티팩트 파일 없음: ${artifact_path}"
        exit 1
    fi

    echo "[DEPLOY] 로컬 배포 시작"
    echo "         컴포넌트: ${COMPONENT_NAME}  버전: ${version}"
    echo "         레시피:   ${recipe_path}"
    echo "         아티팩트: ${artifact_path}"
    echo ""

    # greengrass-cli 는 로컬 파일 배포 시 S3 URI 대신 artifacts 디렉토리 경로를 사용
    sudo "${GGC_CLI}" deployment create \
        --recipeDir  "${recipe_dir}" \
        --artifactDir "${artifact_dir}" \
        --merge "${COMPONENT_NAME}=${version}"

    echo ""
    echo "[DEPLOY] 배포 요청 완료."
    echo "         배포 상태 확인: sudo ${GGC_CLI} deployment status"
    echo "         컴포넌트 로그:  sudo journalctl -u greengrass -f"
    echo "                        또는 /greengrass/v2/logs/${COMPONENT_NAME}.log"
}

# ──────────────────────────────────────────────
# CLI 파싱
# ──────────────────────────────────────────────
if [[ ! -x "${GGC_CLI}" ]]; then
    echo "[WARN] greengrass-cli 를 찾을 수 없습니다: ${GGC_CLI}"
    echo "       이 스크립트는 실제 Greengrass Core Device에서 실행해야 합니다."
    exit 1
fi

case "${1:-deploy}" in
    --status|status)   cmd_status ;;
    --remove|remove)   cmd_remove ;;
    --help|-h)
        sed -n '/^# ─/,/^# ─/p' "$0" | grep -v '^#.*─' | sed 's/^# \{0,2\}//'
        ;;
    *)
        cmd_deploy "${1:-}"
        ;;
esac
