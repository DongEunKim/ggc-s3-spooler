# SDK Selection Rationale — Stream Manager vs AWS IoT Device SDK v2

> 문서 목적: 개발환경 및 배포 시 사용해야 하는 SDK 확인, 선택 근거  
> 작성일: 2026-05-31

---

## 1. 후보 SDK 비교

| 항목 | `stream-manager` (PyPI) | `awsiotsdk` (AWS IoT Device SDK v2) |
|------|------------------------|--------------------------------------|
| PyPI 패키지명 | `stream-manager` | `awsiotsdk` |
| 버전 (조사 기준) | 1.1.1 | 1.x |
| 목적 | Greengrass Stream Manager IPC | AWS IoT Core 직접 연결 (MQTT/Shadow/Jobs) |
| 통신 방식 | localhost TCP (기본 포트 8088) | TLS/MQTT over 인터넷 |
| AWS 인증 필요 여부 | **불필요** (Greengrass가 대신 처리) | 필요 (X.509 인증서) |
| Greengrass 런타임 의존 | **필요** (Stream Manager 컴포넌트 실행 중이어야 함) | 불필요 |
| 개발환경 사용 가능 여부 | 패키지 import 가능, 연결은 Mock 필요 | 가능 (독립 동작) |

---

## 2. 결론: `stream-manager` SDK 선택

이 컴포넌트의 역할은 **Greengrass Stream Manager 컴포넌트에 메시지를 전달**하는 것이다.  
Stream Manager가 S3 업로드를 담당하므로, 이 컴포넌트는 AWS 직접 통신이 불필요하다.

```
[S3 Spooler]  →  [stream-manager SDK]  →  [Stream Manager 컴포넌트]  →  [AWS S3]
                   (localhost:8088)           (Greengrass IPC)
```

`awsiotsdk`는 이 파이프라인에서 역할이 없다. **선택: `stream-manager`** ✓

---

## 3. 개발환경 설치 상태 확인

```bash
# 이미 설치됨 (pyproject.toml dependencies에 포함)
pip show stream-manager
# Name: stream-manager
# Version: 1.1.1

# 개발환경에서 import 가능 여부
python3 -c "from stream_manager import StreamManagerClient; print('OK')"
```

개발환경에서 `StreamManagerClient` 는 import 가능하지만,  
`client.connect()` 호출 시 `localhost:8088` 연결 실패 → `MockStreamManagerClient` 로 대체.

---

## 4. Core Device에 SDK가 탑재되어 있지 않은 경우

`stream-manager` SDK는 Greengrass 기본 설치에 포함되지 **않는다**.  
두 가지 설치 방법이 있다:

### 방법 A: 온라인 설치 (현재 recipe)

```yaml
Lifecycle:
  Install:
    Script: |
      pip3 install stream-manager>=1.1.1
```

디바이스에 인터넷 접근 + pip 사용 가능이 전제 조건이다.

### 방법 B: 번들 배포 (D-03 — 오프라인 지원)

아티팩트 zip에 의존성 wheel을 포함시켜 `pip install --no-index` 로 설치:

```yaml
Lifecycle:
  Install:
    Script: |
      pip3 install --no-index --find-links {artifacts:path}/deps/ \
        stream_manager-1.1.1-py3-none-any.whl \
        watchdog-6.0.0-py3-none-any.whl \
        pydantic-2.13.4-py3-none-any.whl \
        pydantic_settings-2.14.1-py3-none-any.whl
```

빌드 시 `pip download -d deps/ stream-manager watchdog pydantic pydantic-settings` 로  
wheels를 미리 다운로드하여 zip에 포함한다. (scripts/build_artifact.py D-03 구현 참고)

---

## 5. 참고: stream-manager SDK 주요 API

```python
from stream_manager import StreamManagerClient
from stream_manager.data import (
    MessageStreamDefinition,
    S3ExportTaskExecutorConfig,
    ExportDefinition,
    StrategyOnFull,
)

# 연결
client = StreamManagerClient(host="localhost", port=8088)

# 메시지 전송 (스트림이 미리 생성되어 있어야 함)
sequence_number = client.append_message(
    stream_name="my-stream",
    data=b"binary payload"
)

# 스트림 생성 (필요 시)
client.create_message_stream(
    MessageStreamDefinition(
        name="my-stream",
        strategy_on_full=StrategyOnFull.OverwriteOldestData,
        export_definition=ExportDefinition(
            s3_task_executor=[
                S3ExportTaskExecutorConfig(
                    identifier="S3Export",
                    s3_bucket="my-bucket",
                    key_prefix="data/",
                )
            ]
        ),
    )
)

client.close()
```

---

## 6. 커뮤니티 컴포넌트 분석 결과 (2026-06-01 추가)

AWS 커뮤니티에서 유사한 파일 업로드 컴포넌트들이 어떤 SDK를 선택했는지 분석한 결과, **우리의 `stream-manager` 선택이 표준적 패턴임을 확인**했다.

### 6.1 AWS Labs S3 File Uploader
- **사용 SDK**: `stream-manager` (동일)
- **사용 패턴**: 
  ```python
  client = StreamManagerClient(host="localhost", port=8088)
  client.append_message(stream_name, data)
  ```
- **특징**: 파일을 메모리에 전체 로드 후 `append_message()` 호출

### 6.2 AWS Labs S3 File Downloader  
- **사용 SDK**: `boto3` (S3 Transfer Manager)
- **이유**: Stream Manager는 업로드 전용, 다운로드는 S3 SDK 직접 사용
- **패턴**: 
  ```python
  s3_client = boto3.client('s3')
  s3_client.download_file(bucket, key, local_path)
  ```

### 6.3 AWS Greengrass Disk Spooler
- **사용 SDK**: `awsiotsdk` (IoT Core 직접 연결)
- **이유**: MQTT 메시지 스풀링이 목적 (S3와 무관)
- **패턴**: IoT Core MQTT publish

### 6.4 커뮤니티 패턴 결론

| 목적 | 커뮤니티 표준 SDK | 우리 선택 | 일치도 |
|------|-------------------|----------|--------|
| **S3 파일 업로드** | `stream-manager` | `stream-manager` | ✅ 일치 |
| S3 파일 다운로드 | `boto3` | N/A | N/A |
| MQTT 메시지 | `awsiotsdk` | N/A | N/A |

**검증 결과**: S3 파일 업로드를 위한 `stream-manager` SDK 선택은 AWS 커뮤니티 표준 패턴과 완전히 일치한다.

### 6.5 상호 학습 포인트

#### 우리가 커뮤니티에서 학습한 패턴
- **파일 크기 제한**: 커뮤니티도 동일한 64MB 제한 인지하고 있음
- **에러 처리**: 실패 시 파일을 삭제하지 않고 재시도하는 패턴 공통
- **순차 처리**: 대부분 단일 스레드 순차 처리로 복잡성 회피

#### 우리가 커뮤니티보다 발전시킨 부분
- **청킹 지원**: 64MB 초과 파일 자동 분할 (커뮤니티 미지원)
- **동적 라우팅**: 파일명 기반 멀티 스트림 (커뮤니티는 단일 스트림)
- **공간 관리**: retention + quota 정책 (커뮤니티 미지원)

**참조 문서**: [../reference/aws-community-components/comparison-matrix.md](../reference/aws-community-components/comparison-matrix.md)

---

## 7. 참고 링크 (보존)

- stream-manager PyPI: https://pypi.org/project/stream-manager/
- Greengrass Stream Manager 개발 가이드: https://docs.aws.amazon.com/greengrass/v2/developerguide/work-with-streams.html
- AWS IoT Device SDK v2 for Python: https://github.com/aws/aws-iot-device-sdk-python-v2 (이 프로젝트에서 미사용)
