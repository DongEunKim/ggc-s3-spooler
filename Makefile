.PHONY: help test lint type-check build bump-patch bump-minor bump-major release \
        deploy-aws deploy-full deploy-local clean version

PYTHON  := .venv/bin/python
PYTEST  := .venv/bin/pytest
RUFF    := .venv/bin/ruff
MYPY    := .venv/bin/mypy

# ──────────────────────────────────────────────────────────
# 기본 타겟
# ──────────────────────────────────────────────────────────

help: ## 사용 가능한 명령 목록
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

version: ## 현재 컴포넌트 버전 출력
	@$(PYTHON) scripts/bump_version.py --show

# ──────────────────────────────────────────────────────────
# 개발: 테스트 / 품질
# ──────────────────────────────────────────────────────────

test: ## 단위 + 통합 테스트 실행 (Greengrass 전용 제외)
	$(PYTEST) tests/ -v

test-all: ## Greengrass 전용 테스트 포함 실행 (실 디바이스 필요)
	$(PYTEST) tests/ -v --greengrass

test-cov: ## 커버리지 리포트 생성 (htmlcov/)
	$(PYTEST) tests/ --cov=spooler --cov-report=html --cov-report=term-missing

lint: ## ruff 린트 검사
	$(RUFF) check src/ tests/

lint-fix: ## ruff 자동 수정
	$(RUFF) check --fix src/ tests/
	$(RUFF) format src/ tests/

type-check: ## mypy 타입 검사
	$(MYPY) src/spooler/

check: lint type-check test ## 린트 + 타입 + 테스트 (CI용)

# ──────────────────────────────────────────────────────────
# 버전 관리
# ──────────────────────────────────────────────────────────

bump-patch: ## 패치 버전 증가 (1.0.0 → 1.0.1)
	$(PYTHON) scripts/bump_version.py --patch

bump-minor: ## 마이너 버전 증가 (1.0.0 → 1.1.0)
	$(PYTHON) scripts/bump_version.py --minor

bump-major: ## 메이저 버전 증가 (1.0.0 → 2.0.0)
	$(PYTHON) scripts/bump_version.py --major

# 특정 버전 직접 지정: make release VERSION=1.2.0
release: ## 버전 범프 + 빌드 + git 태그 (VERSION= 필수)
ifndef VERSION
	$(error VERSION 을 지정하세요. 예: make release VERSION=1.2.0)
endif
	$(PYTHON) scripts/bump_version.py $(VERSION)
	$(MAKE) build
	@echo ""
	@echo "  다음 단계 (git 태그 + push):"
	@echo "    git add src/spooler/__init__.py pyproject.toml"
	@echo "    git commit -m 'chore: bump version to $(VERSION)'"
	@echo "    git tag v$(VERSION)"
	@echo "    git push origin main v$(VERSION)"

# ──────────────────────────────────────────────────────────
# 빌드
# ──────────────────────────────────────────────────────────

# ⚠️  TGU는 PyPI 접근 불가 — 항상 번들(wheel 포함) 빌드를 사용해야 한다.
build: ## TGU 번들 빌드 — ARM64 Linux + Python 3.11 (프로덕션 기본)
	$(PYTHON) scripts/build_artifact.py --platform tgu-arm64

build-arm64: ## TGU 번들 빌드 — ARM64 (= make build 와 동일)
	$(PYTHON) scripts/build_artifact.py --platform tgu-arm64

build-armv7l: ## TGU 번들 빌드 — ARM32 구형 TGU (Python 3.11)
	$(PYTHON) scripts/build_artifact.py --platform tgu-armv7l

build-local: ## 로컬 머신 플랫폼 빌드 (개발/CI 테스트 전용, TGU 배포 불가)
	$(PYTHON) scripts/build_artifact.py --platform local

build-dry: ## 빌드 계획만 출력 (파일 생성 없음)
	$(PYTHON) scripts/build_artifact.py --platform tgu-arm64 --dry-run

# ──────────────────────────────────────────────────────────
# 배포 — 기본 (deploy.yaml)
# ──────────────────────────────────────────────────────────

deploy-aws: ## S3 업로드 + Greengrass 컴포넌트 등록 (배포 없음)
	$(PYTHON) scripts/deploy_aws.py --only-register

deploy-full: ## S3 업로드 + 컴포넌트 등록 + 디바이스 배포
	$(PYTHON) scripts/deploy_aws.py --deploy

deploy-watch: ## D-04: 디바이스 배포 + 완료까지 상태 모니터링
	$(PYTHON) scripts/deploy_aws.py --deploy --monitor

deploy-dry: ## 배포 계획만 출력 (실제 AWS 호출 없음)
	$(PYTHON) scripts/deploy_aws.py --deploy --dry-run

deploy-local: ## 로컬 Core Device에 직접 배포 (디바이스에서 실행)
	bash scripts/deploy_local.sh

# ──────────────────────────────────────────────────────────
# 배포 — D-02 다중 환경
# ──────────────────────────────────────────────────────────

deploy-staging: ## D-02: 스테이징 환경에 배포 (deploy.staging.yaml 필요)
	$(PYTHON) scripts/deploy_aws.py --env staging --deploy --monitor

deploy-prod: ## D-02: 프로덕션 환경에 배포 (deploy.prod.yaml 필요) ⚠️  신중하게
	$(PYTHON) scripts/deploy_aws.py --env prod --deploy --monitor

# ──────────────────────────────────────────────────────────
# 정리
# ──────────────────────────────────────────────────────────

clean: ## 빌드 산출물 삭제 (dist/, __pycache__, .coverage 등)
	rm -rf dist/ htmlcov/ .coverage .pytest_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@echo "[CLEAN] 완료"
