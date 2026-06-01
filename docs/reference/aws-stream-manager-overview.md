# AWS Greengrass Stream Manager — 참고 요약

> 출처: AWS Greengrass Developer Guide + stream_manager 1.1.1 SDK 소스 직접 확인  
> 보존 목적: 이 프로젝트 설계 근거가 된 AWS 공식 문서 핵심 내용 요약  
> 최종 검증: 2026-06-01 (SDK 소스 직접 분석으로 검증)

---

## Stream Manager 개요

- AWS IoT Greengrass v2의 공식 컴포넌트 (`aws.greengrass.StreamManager`)
- 로컬 프로세스가 데이터 스트림에 메시지를 append하면 AWS 클라우드(S3, Kinesis 등)로 자동 전송
- 로컬 버퍼링을 통해 네트워크 불안정 상황에서도 데이터 손실 방지

---

## Python SDK 기본 사용법

```python
from stream_manager import StreamManagerClient

client = StreamManagerClient(host="localhost", port=8088)
sequence_number = client.append_message(stream_name="my-stream", data=b"payload")
client.close()
```

---

## 크기 제한 — 두 가지 구분 필수

> ⚠️ 아래 두 한도는 완전히 다른 개념이며 혼동하기 쉽다.

### 1. 단일 메시지 크기 (append_message data 파라미터) — 64MB

`append_message(stream_name, data=raw_bytes)` 에서 `data` 최대 크기.

- 한도: **64MB** (Stream Manager Component 내부 처리 한도)
- SDK 클라이언트 자체의 패킷 한도: `_MAX_PACKET_SIZE = 1 << 30 = 1GB` (Component가 먼저 제한)
- 이 한도는 **Pattern 1 (raw bytes 전송)에서만 적용**됨
- Pattern 2 (S3ExportTaskDefinition)에서는 메시지 크기 = ~110 bytes (태스크 정의)

### 2. S3 업로드 파일 크기 (Pattern 2) — 실질적 제한 없음

`S3ExportTaskExecutorConfig`의 `size_threshold_for_multipart_upload_bytes` 설정으로  
**Stream Manager가 대용량 파일을 자동으로 S3 멀티파트 업로드 처리**한다.

```python
S3ExportTaskExecutorConfig(
    identifier="my-s3-exporter",
    size_threshold_for_multipart_upload_bytes=5_242_880,  # 5MB 초과 시 멀티파트 자동 적용
    status_config=StatusConfig(...)
)
```

- 최솟값: 5MB (5,242,880 bytes)
- 이 값 이상의 파일: S3 멀티파트 업로드 → 실질적 크기 제한 없음
- 이 값 이하의 파일: 단일 PUT 업로드

---

## 전송 패턴 — Pattern 1 vs Pattern 2

### Pattern 1: 바이트 직접 전송

```python
data = file_path.read_bytes()          # 전체 파일을 메모리에 적재
seq = client.append_message(stream_name, data)
```

| 항목 | 내용 |
|------|------|
| 파일 크기 한도 | **64MB** |
| S3 키 제어 | ❌ 불가 — SM이 자동 생성 |
| 메모리 사용 | 파일 전체 메모리 적재 |
| 구현 복잡도 | 낮음 |

### Pattern 2: S3ExportTaskDefinition (권장)

```python
from stream_manager.data import S3ExportTaskDefinition
from stream_manager.util import Util  # ✅ JSON 직렬화 유틸리티

task = S3ExportTaskDefinition(
    input_url="file:///var/spool/s3/stream__key__file.bin",  # 로컬 파일 경로
    bucket="my-s3-bucket",                                   # S3 버킷 직접 지정
    key="data/device-001/sensor_2026-06-01.bin"              # S3 키 직접 지정
)
# ✅ 올바른 직렬화: JSON (공식 문서 확인)
# ❌ 잘못된 방식: cbor2.dumps(task.as_dict())  — SM이 태스크를 인식 못함
seq = client.append_message(
    stream_name,
    Util.validate_and_serialize_to_json_bytes(task)
)
# SM이 파일을 직접 읽어 S3에 업로드 (멀티파트 자동 적용)
```

| 항목 | 내용 |
|------|------|
| 파일 크기 한도 | **없음** (SM이 S3 멀티파트 자동 처리) |
| S3 키 제어 | ✅ per-file 완전 제어 (bucket + key 직접 지정) |
| 메모리 사용 | 파일을 메모리에 올리지 않음 (SM이 직접 읽기) |
| 구현 복잡도 | 중간 (파일 라이프사이클 관리 필요) |
| S3 키 Placeholder | `!{timestamp:yyyy/MM/dd}` 형식 지원 |

---

## S3 Export 스트림 생성 — Pattern 2 기준 (올바른 코드)

> ⚠️ **중요**: `S3ExportTaskExecutorConfig`에는 `s3_bucket`, `key_prefix` 파라미터가 없다.  
> 버킷과 키는 스트림 수준이 아니라 **메시지마다** `S3ExportTaskDefinition`으로 지정한다.

```python
from stream_manager import StreamManagerClient
from stream_manager.data import (
    MessageStreamDefinition,
    S3ExportTaskExecutorConfig,
    S3ExportTaskDefinition,
    StatusConfig,
    StatusLevel,
    ExportDefinition,
    StrategyOnFull,
    Persistence,
)
from stream_manager.util import Util

client = StreamManagerClient(host="localhost", port=8088)

# 1. (선택) 업로드 완료 상태를 수신할 상태 스트림 생성
client.create_message_stream(
    MessageStreamDefinition(name="telemetry-status-stream")
)

# 2. S3 Export 스트림 생성
client.create_message_stream(
    MessageStreamDefinition(
        name="telemetry-stream",
        max_size=268_435_456,              # 스트림 버퍼 256MB (Greengrass 파티션에 저장)
        strategy_on_full=StrategyOnFull.OverwriteOldestData,
        persistence=Persistence.File,
        export_definition=ExportDefinition(
            s3_task_executor=[
                S3ExportTaskExecutorConfig(
                    identifier="TelemetryS3Exporter",
                    size_threshold_for_multipart_upload_bytes=5_242_880,  # 5MB 이상 멀티파트
                    status_config=StatusConfig(
                        status_level=StatusLevel.ERROR,          # ERROR 이상 상태만 보고
                        status_stream_name="telemetry-status-stream"
                    )
                )
            ]
        ),
    )
)

# 3. 파일 업로드 태스크 등록 (per-file S3 키 제어)
task = S3ExportTaskDefinition(
    input_url="file:///var/spool/s3-spooler/telemetry__key__reading.bin",
    bucket="my-iot-data-bucket",
    key="raw/device-001/2026/06/01/reading.bin"
)
seq = client.append_message(
    "telemetry-stream",
    Util.validate_and_serialize_to_json_bytes(task)  # ✅ JSON 직렬화 (공식 방식)
)
# → SM이 파일을 읽어 S3에 업로드, 완료 시 status-stream에 결과 기록

client.close()
```

---

## 업로드 완료 감지 — StatusMessage 읽기

```python
from stream_manager.data import StatusMessage, Status, ReadMessagesOptions
from stream_manager.util import Util  # ✅ JSON 역직렬화

messages = client.read_messages(
    "telemetry-status-stream",
    options=ReadMessagesOptions(min_message_count=1, read_timeout_millis=1000)
)
for msg in messages:
    # ✅ 올바른 역직렬화: Util.deserialize_json_bytes_to_obj()
    # ❌ 잘못된 방식: StatusMessage.from_dict(cbor2.loads(msg.payload))
    status_msg = Util.deserialize_json_bytes_to_obj(msg.payload, StatusMessage)
    if status_msg.status == Status.Success:
        seq = status_msg.status_context.sequence_number
        # seq에 해당하는 스풀 파일 삭제
    elif status_msg.status in (Status.Failure, Status.Canceled):
        # 재시도 처리 또는 오류 로그
        pass
```

---

## 스트림 버퍼 크기 (`max_size`) — 별도 설정

`MessageStreamDefinition.max_size`: SM이 S3 업로드 전 로컬에 버퍼링하는 크기.

- 기본값: 256MB
- 저장 위치: `/greengrass/v2/work/aws.greengrass.StreamManager/` (Greengrass 파티션)
- **스풀 파티션(전용 1GB)과 독립** — 별도 관리
- 네트워크 단절 시 더 많은 데이터를 보관하려면 증가 가능

---

## 포트 기본값

- Stream Manager 기본 포트: **8088**

---

## 참고 링크

- Stream Manager 개발 가이드: https://docs.aws.amazon.com/greengrass/v2/developerguide/stream-manager-component.html
- Python SDK 레퍼런스: https://docs.aws.amazon.com/greengrass/v2/developerguide/work-with-streams.html
- stream_manager PyPI: https://pypi.org/project/stream-manager/
