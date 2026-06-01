# GGC S3 Spooler — Claude Code 운영 지침

## 프로젝트 개요

AWS Greengrass Component 형태의 S3 파일 스풀러.  
다른 프로세스가 스풀 디렉토리에 파일을 저장하면 Greengrass Stream Manager를 통해 S3로 전송하고,
전송 완료 후 파일을 삭제한다.

## 중요 제약사항

### 개발환경 vs. 실제 Greengrass 환경

**현재 개발환경은 실제 Greengrass Core Device가 아니다.**  
이 차이에서 오는 검증 제약을 반드시 인식하고 작업해야 한다.

| 항목 | 개발환경 (Mac/Linux 로컬) | 실제 Core Device |
|------|--------------------------|-----------------|
| Stream Manager | 없음 → `MockStreamManagerClient` 사용 | `stream_manager` SDK 실동작 |
| Greengrass 런타임 | 없음 | `/greengrass/v2` 설치됨 |
| recipe.yaml 적용 | 불가 | `greengrass-cli deployment create` |
| 컴포넌트 설정 주입 | 불가 | `{configuration:/key}` 치환 |
| 파일시스템 권한 | 일반 사용자 | ggc_user 계정 |

**테스트 원칙**:
- 단위 테스트·통합 테스트는 항상 Mock 클라이언트 사용
- `stream_manager` 패키지 import가 필요한 경로는 실디바이스 전용 마크(`@pytest.mark.greengrass`) 표시
- "로컬에서 통과" ≠ "Core Device에서 동작" — Stream Manager 연결, 스트림 이름, S3 권한은 디바이스에서 별도 검증 필요

### TGU 운영 환경 제약 (절대 준수)

**운영 환경**: 건설기계 탑재 TGU (Telematic Gateway Unit)

| 제약 | 내용 | 대응 |
|------|------|------|
| PyPI 차단 | 사이버보안 정책으로 인터넷 pip install 불가 | 번들 빌드 필수 (`make build`) |
| SDK 미탑재 | 기존 TGU에 stream-manager SDK 없음 | wheel을 아티팩트 zip에 포함 |
| 아키텍처 | ARM (aarch64 또는 armv7l, 미확정) | 플랫폼 프리셋 필수 지정 |
| Python 버전 | 미확정 (3.11 권장, cbor2 C 확장 ABI 의존) | TGU 확인 후 빌드 |

**빌드 규칙**:
- `make build` = ARM64 Linux + Python 3.11 번들 (TGU 기본)
- `make build-armv7l` = ARM32 구형 TGU
- `make build-local` = 개발용 전용, **TGU에 배포하면 안 됨**
- `--platform local` 빌드를 TGU에 배포하면 wheel 아키텍처 불일치로 설치 실패

**플랫폼 확인 미완료 시**:  
TGU CPU 아키텍처 및 Python 버전이 확인되지 않은 상태이다.  
[research/06-tgu-platform-constraints.md](docs/research/06-tgu-platform-constraints.md) 의 확인 절차를 먼저 수행한다.

### 네트워크 공급 확장 (미래 작업 — 기본 기능 완성 후)

코어 디바이스 하위 네트워크의 클라이언트 디바이스가 HTTPS 등 네트워크 수단으로 스풀러에 파일을 공급하는 구조로 확장할 예정이다.

**구현 시점**: 로컬 파일 기반 기본 기능(FR-01~FR-05)이 완전히 구현·검증된 후  
**아직 구현하지 말 것**: 네트워크 수신 서버, 인증 레이어, 멀티파트 업로드 API  
**설계 방향 (참고)**: 네트워크 수신 → 스풀 디렉토리 write → 기존 파이프라인 재사용 (수신 방식만 확장)

---

## 기술 스택

- **언어**: Python 3.11+ (Greengrass Core Device 권장 버전)
- **패키지 관리**: `pyproject.toml` (build backend: hatchling)
- **테스트**: pytest + pytest-asyncio
- **린팅**: ruff (linter + formatter)
- **타입 체크**: mypy
- **주요 의존성**: `stream_manager` (AWS Greengrass Stream Manager SDK), `watchdog`

## 디렉토리 구조

```
ggc-s3-spooler/
├── src/spooler/          # 메인 소스코드
├── tests/                # 테스트 코드
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── docs/                 # 기술 문서 (한글 위주)
│   └── reference/        # 참고문서
├── scripts/              # 배포/운영 스크립트
├── recipe.yaml           # Greengrass 컴포넌트 레시피
├── pyproject.toml
└── CLAUDE.md
```

## 파일명 인코딩 규칙

스풀 디렉토리에 저장되는 파일명은 다음 형식을 따른다:

```
{stream_id}__{s3_key_encoded}
```

- `stream_id`: Stream Manager 스트림 이름 (영숫자, 하이픈, 언더스코어)
- `s3_key_encoded`: S3 키 경로의 슬래시를 느낌표(!)로 치환한 형태

예시: `telemetry-stream__data!device-1!readings!sensor_2024.json`

## 에이전트 역할 정의

### 1. 기획 에이전트 (Plan Agent)
- 호출: `Agent(subagent_type="Plan", ...)`
- 역할: 기능 요구사항 → 구현 전략, 아키텍처 결정, 컴포넌트 분리
- 산출물: 구현 계획, 인터페이스 정의, 의존성 분석

### 2. 코딩 에이전트 (Coding Agent)
- 호출: `Agent(subagent_type="claude", ...)`
- 역할: 기획 에이전트 산출물 기반 코드 구현
- 제약: 기존 인터페이스 유지, 타입 힌트 필수, 단위 테스트 동반

### 3. 품질평가 에이전트 (Review Agent)
- 호출: `/code-review` 스킬
- 역할: 버그, 보안 취약점, 성능 문제 탐지
- 체크리스트: 파일 경쟁 조건, 공간 관리 엣지케이스, Stream Manager 오류 처리

### 4. 문서 에이전트 (Doc Agent)
- 호출: `Agent(subagent_type="claude", ...)`  
- 역할: 코드 변경 → 관련 기술문서 개정 + 개정이력 추가
- 규칙: 문서 제목 영문, 내용 한글, 개정이력 테이블 유지

## 워크플로우 규칙

1. **기능 추가/변경 시**: Plan → Code → Review → Doc 순서
2. **버그 수정 시**: Code → Review → Doc (간이)
3. **문서 수정 시**: Doc만
4. 각 단계 완료 후 **에이전트 업그레이드 포인트** 제안 필수

## 코딩 컨벤션

- 모든 public 함수에 타입 힌트 필수
- 예외 처리: Greengrass SDK 오류는 반드시 구조화된 로깅
- 설정값은 `config.py`의 dataclass로 관리, 환경변수로 오버라이드 가능
- 비동기: `asyncio` 사용 (watchdog 콜백은 스레드 → asyncio 큐 브릿지)

## 개발 환경 설정

### 설정 파일 위치 (개발/배포 일관성)

개발과 배포 환경의 일관성을 위해 **시스템 표준 경로**를 사용한다:

```bash
# 개발 환경 설정 (최초 1회)
sudo mkdir -p /etc/ggc-s3-spooler/
sudo cp spooler.ini /etc/ggc-s3-spooler/
sudo chmod 644 /etc/ggc-s3-spooler/spooler.ini
```

### 설정 탐색 순서

`SpoolerConfig`는 다음 순서로 설정을 탐색한다:

1. **CLI 인수** (최고 우선순위)
2. **환경변수** 오버라이드
3. **시스템 설정**: `/etc/ggc-s3-spooler/spooler.ini`
4. **프로젝트 로컬**: `./spooler.ini` (개발 편의용)
5. **기본값**: `SpoolerConfig` dataclass 기본값

### 환경변수 오버라이드

주요 설정값은 환경변수로 오버라이드 가능:

```bash
export S3_SPOOLER_S3_BUCKET=my-test-bucket
export S3_SPOOLER_LOG_LEVEL=DEBUG
export S3_SPOOLER_SM_HOST=localhost
export S3_SPOOLER_SM_PORT=8088
```

## 테스트 실행

```bash
# 가상환경 활성화
source .venv/bin/activate

# 단위 테스트
pytest tests/unit/ -v

# 전체 테스트 (Stream Manager 모킹 포함)
pytest tests/ -v

# 커버리지
pytest --cov=spooler --cov-report=html tests/
```

## 빌드 및 배포

단계별 명령은 `make help` 로 확인한다. 상세 절차는 `docs/06-배포가이드.md` 참고.

```bash
# 버전 확인
make version

# 아티팩트 빌드 (dist/ 하위에 zip + recipe + manifest 생성)
make build

# 버전 범프 → 빌드 → git 태그 (한 번에)
make release VERSION=1.1.0

# AWS 배포: S3 업로드 + 컴포넌트 등록 (디바이스 배포 없음)
make deploy-aws

# AWS 배포 + 디바이스 즉시 배포 (deploy.yaml target_arn 필요)
make deploy-full

# 로컬 Core Device 수동 배포 (디바이스에서 실행)
make deploy-local
```

### 배포 설정 파일

- `deploy.yaml` — 실제 S3 버킷, 리전, 대상 ARN 설정 (`.gitignore` 제외)
- `deploy.yaml.example` — 설정 템플릿 (버전관리 포함)

### 자동 배포 (GitHub Actions)

- `v*.*.*` 태그 push → `.github/workflows/release.yml` 자동 실행
- GitHub Secrets 필요: `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `GGC_ARTIFACT_S3_BUCKET`, `GGC_DEPLOYMENT_TARGET_ARN`
- `production` Environment Protection Rules로 배포 승인 제어 가능

### 버전 관리 단일 진실 원천

`src/spooler/__init__.py` 의 `__version__` 이 유일한 버전 기준점.  
`pyproject.toml`, recipe, 아티팩트 파일명 모두 이 값에서 파생된다.  
`make bump-patch / bump-minor / bump-major` 로 자동 동기화.

## 참고 문서 위치

- `docs/01-기능정의서.md` — 기능 요구사항
- `docs/02-시스템사양서.md` — 성능·환경 사양
- `docs/03-아키텍처설계서.md` — 시스템 아키텍처
- `docs/04-인터페이스설계서.md` — 파일 인코딩 규칙, API 인터페이스
- `docs/05-상세설계사양서.md` — 모듈별 상세 설계
- `docs/06-배포가이드.md` — 빌드·배포·롤백 절차
- `docs/07-클라이언트-통합-가이드.md` — 클라이언트 프로세스 개발자용
- `docs/08-S3-스트림-관리-가이드.md` — S3/스트림 관리자용
- `docs/research/` — 정량 사양 결정 근거 문서
- `docs/reference/` — AWS 공식 문서 발췌 및 참고 코드 보존
  - `aws-community-components/` — AWS 커뮤니티 컴포넌트 분석 (2026-06-01 추가)
    - `community-components-overview.md` — 유사 컴포넌트 카탈로그 및 분류
    - `s3-file-uploader-analysis.md` — AWS Labs S3 File Uploader 상세 분석 (주요 비교 대상)
    - `can-blackbox-analysis.md` — CAN Blackbox Directory Uploader 상세 분석 (폴링 기반 도메인 특화)
    - `comparison-matrix.md` — GGC S3 Spooler vs 커뮤니티 컴포넌트 종합 비교 분석표
