# Stream Manager IPC — S3 파일 전송 가이드

> 출처: AWS Greengrass Developer Guide  
> - https://docs.aws.amazon.com/greengrass/v2/developerguide/work-with-streams.html  
> - https://docs.aws.amazon.com/greengrass/v2/developerguide/stream-export-configurations.html  
> - https://aws-greengrass.github.io/aws-greengrass-stream-manager-sdk-python/  
> SDK 버전: stream-manager 1.1.1  
> 문서 작성일: 2026-06-01 (SDK 소스 및 공식 문서 직접 확인)

---

## 1. 개요

Stream Manager를 통한 S3 파일 전송은 **태스크 기반(task-based)** 방식으로 동작한다.

```
[Greengrass Component]
    │
    │ append_message(stream_name, JSON(S3ExportTaskDefinition))
    │    ← 태스크 정의 (~100 bytes), 파일 내용이 아님
    ▼
[Stream Manager Component]
    │ input_url = "file:///local/path/to/file"
    │ bucket = "my-s3-bucket"
    │ key = "data/device-001/file.bin"
    ▼
[AWS S3]  ← SM이 파일을 직접 읽어 업로드 (멀티파트 자동 적용)
    │
    ▼
[Status Stream]  ← 업로드 완료/실패 결과 기록
```

### 핵심 특성

| 항목 | 내용 |
|------|------|
| 파일 크기 제한 | **없음** — SM이 S3 멀티파트 업로드 자동 처리 |
| S3 키 제어 | ✅ per-file 완전 제어 (`bucket` + `key` 직접 지정) |
| 직렬화 방식 | **JSON** — `Util.validate_and_serialize_to_json_bytes()` 사용 |
| 멀티파트 임계값 | `size_threshold_for_multipart_upload_bytes` (최솟값 5 MB) |
| 업로드 완료 감지 | 상태 스트림(status stream)으로 `StatusMessage` 수신 |

---

## 2. IAM 권한 요구사항

Greengrass Core Device의 Token Exchange Role에 다음 권한 필요:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts"
      ],
      "Resource": [
        "arn:aws:s3:::my-iot-data-bucket/*"
      ]
    }
  ]
}
```

> `s3:AbortMultipartUpload` 와 `s3:ListMultipartUploadParts` 는 멀티파트 업로드에 필수.

---

## 3. StreamManagerClient 연결

```python
from stream_manager import StreamManagerClient
from stream_manager.exceptions import StreamManagerException
import asyncio

# 기본 포트: 8088 (Greengrass가 SM_PORT 환경변수로 주입)
client = StreamManagerClient()
# 또는 명시적 지정:
client = StreamManagerClient(host="localhost", port=8088)

# 사용 후 반드시 close()
try:
    # 작업 수행
    pass
except StreamManagerException:
    pass  # 에러 처리
except (ConnectionError, asyncio.TimeoutError):
    pass  # 연결 에러 처리
finally:
    client.close()
```

> **주의**: Lambda 함수에서는 핸들러 외부에서 클라이언트를 인스턴스화할 것.  
> 핸들러 내부에서 인스턴스화 시 매 호출마다 연결을 생성하며, 반드시 `close()` 호출 필요.

---

## 4. 스트림 생성

### 4.1 상태 스트림 생성 (선택, 권장)

업로드 완료 감지를 위해 **export 스트림보다 먼저** 생성한다.

```python
from stream_manager.data import (
    MessageStreamDefinition,
    StrategyOnFull,
    Persistence,
)

client.create_message_stream(
    MessageStreamDefinition(
        name="my-status-stream",                   # 상태 스트림 이름
        strategy_on_full=StrategyOnFull.OverwriteOldestData,  # 상태 메시지가 꽉 차면 오래된 것 덮어씀
        persistence=Persistence.Memory,            # 재시작 시 유지 불필요
    )
)
```

### 4.2 S3 Export 스트림 생성

```python
from stream_manager.data import (
    MessageStreamDefinition,
    ExportDefinition,
    S3ExportTaskExecutorConfig,
    StatusConfig,
    StatusLevel,
    StrategyOnFull,
    Persistence,
)

client.create_message_stream(
    MessageStreamDefinition(
        name="my-s3-stream",                        # 스트림 이름 (stream_id)
        max_size=268_435_456,                        # 스트림 버퍼 최대 크기: 256 MB (기본값)
        strategy_on_full=StrategyOnFull.OverwriteOldestData,
        persistence=Persistence.File,               # 재시작 후 버퍼 유지
        export_definition=ExportDefinition(
            s3_task_executor=[
                S3ExportTaskExecutorConfig(
                    identifier="MyS3Exporter",       # 필수. 이 export 설정의 고유 식별자
                    # 5 MB 초과 파일 → 자동 S3 멀티파트 업로드
                    # 5 MB 이하 파일 → 단일 PUT 업로드
                    # 최솟값: 5,242,880 bytes (5 MB)
                    size_threshold_for_multipart_upload_bytes=5_242_880,
                    priority=1,                      # 선택. 낮을수록 높은 우선순위
                    status_config=StatusConfig(
                        status_level=StatusLevel.INFO,         # INFO 이상 상태 기록
                        status_stream_name="my-status-stream"  # 위에서 생성한 상태 스트림
                    )
                )
            ]
        )
    )
)
```

#### S3ExportTaskExecutorConfig 전체 파라미터

| 파라미터 | 타입 | 필수 | 설명 |
|---------|------|------|------|
| `identifier` | str | **필수** | export 설정 고유 식별자. 영숫자, 공백, 쉼표, 마침표, 하이픈, 언더스코어. 1~255자 |
| `size_threshold_for_multipart_upload_bytes` | int | 선택 | 멀티파트 업로드 임계값(bytes). 이 값 초과 시 자동 멀티파트. **최솟값: 5,242,880 (5 MB)** |
| `priority` | int | 선택 | 업로드 우선순위. 낮을수록 높은 우선순위. 미지정 시 최저 우선순위 |
| `disabled` | bool | 선택 | export 활성화/비활성화. 기본값: `False` |
| `status_config` | StatusConfig | 선택 | 업로드 결과를 기록할 상태 스트림 설정 |

> ⚠️ **`s3_bucket`, `key_prefix` 파라미터 없음**: 버킷과 키는 스트림 수준이 아닌  
> 메시지마다 `S3ExportTaskDefinition`으로 지정한다.

#### StatusLevel 상수

| 값 | 기록 조건 |
|----|----------|
| `StatusLevel.ERROR` (0) | 오류만 |
| `StatusLevel.WARN` (1) | 경고 이상 |
| `StatusLevel.INFO` (2) | 정보 이상 (기본 권장) |
| `StatusLevel.DEBUG` (3) | 디버그 이상 |
| `StatusLevel.TRACE` (4) | 모든 상태 |

---

## 5. S3 업로드 태스크 등록 (append_message)

### 5.1 S3ExportTaskDefinition 파라미터

| 파라미터 | 타입 | 필수 | 설명 |
|---------|------|------|------|
| `input_url` | str | **필수** | 업로드할 로컬 파일 URL. `"file:///절대경로"` 형식. 파일은 Core Device 로컬 디스크에 있어야 함 |
| `bucket` | str | **필수** | 대상 S3 버킷 이름. 3~63자, 소문자·숫자·하이픈·마침표. **버킷은 사전에 존재해야 함** |
| `key` | str | **필수** | S3 오브젝트 키. 1~1024자. `!{timestamp:yyyy/MM/dd}` 형식의 Java DateTimeFormatter placeholder 지원 |
| `user_metadata` | dict | 선택 | S3 오브젝트 사용자 메타데이터. `x-amz-meta-` 접두사 불필요. 키는 대소문자 무관(S3에서 소문자로 저장). `$aws-gg-` 접두사는 예약됨 |

### 5.2 직렬화 방식 — JSON (cbor2 아님)

> ⚠️ **중요**: S3ExportTaskDefinition은 **JSON 직렬화**를 사용한다.  
> `cbor2.dumps()` 사용 시 SM이 태스크를 인식하지 못한다.

```python
from stream_manager import StreamManagerClient
from stream_manager.data import S3ExportTaskDefinition
from stream_manager.util import Util

client = StreamManagerClient()

# 기본 사용법
s3_task = S3ExportTaskDefinition(
    input_url="file:///var/spool/s3-spooler/sensor_2026-06-01.bin",
    bucket="my-iot-data-bucket",
    key="raw/device-001/sensor_2026-06-01.bin"
)
# ✅ 올바른 직렬화: Util.validate_and_serialize_to_json_bytes()
# ❌ 잘못된 방식: cbor2.dumps(s3_task.as_dict())
sequence_number = client.append_message(
    stream_name="my-s3-stream",
    data=Util.validate_and_serialize_to_json_bytes(s3_task)
)
# sequence_number: 업로드 태스크 ID (상태 스트림에서 추적에 사용)
```

### 5.3 타임스탬프 placeholder 사용 예

```python
# key에서 !{timestamp:...} placeholder 사용
# → SM이 업로드 시점의 시간으로 치환
s3_task = S3ExportTaskDefinition(
    input_url="file:///var/spool/s3-spooler/telemetry.bin",
    bucket="my-iot-data-bucket",
    key="telemetry/!{timestamp:yyyy}/!{timestamp:MM}/!{timestamp:dd}/data.bin"
    # 실제 키 예: "telemetry/2026/06/01/data.bin"
)
```

### 5.4 사용자 메타데이터 추가

```python
s3_task = S3ExportTaskDefinition(
    input_url="file:///var/spool/s3-spooler/alarm.json",
    bucket="my-iot-data-bucket",
    key="alarms/device-001/alarm_001.json",
    user_metadata={
        "device-id": "device-001",     # S3에서 x-amz-meta-device-id 로 저장
        "event-type": "critical",
    }
)
```

---

## 6. 업로드 완료 감지 — 상태 스트림 읽기

```python
import time
from stream_manager import StreamManagerClient
from stream_manager.data import (
    ReadMessagesOptions,
    Status,
    StatusMessage,
)
from stream_manager.util import Util

client = StreamManagerClient()

def wait_for_upload(status_stream_name: str, target_sequence_number: int, timeout_seconds: int = 300) -> bool:
    """
    상태 스트림에서 특정 sequence_number의 업로드 완료를 대기한다.
    
    Returns:
        True = Success, False = Failure/Canceled
    """
    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            messages = client.read_messages(
                status_stream_name,
                options=ReadMessagesOptions(
                    min_message_count=1,
                    max_message_count=10,
                    read_timeout_millis=1000  # 1초 대기
                )
            )
            for message in messages:
                # ✅ 올바른 역직렬화: Util.deserialize_json_bytes_to_obj()
                status_msg = Util.deserialize_json_bytes_to_obj(message.payload, StatusMessage)
                
                # sequence_number로 해당 태스크 필터링
                if (status_msg.status_context and
                        status_msg.status_context.sequence_number == target_sequence_number):
                    
                    if status_msg.status == Status.Success:
                        return True
                    elif status_msg.status in (Status.Failure, Status.Canceled):
                        # status_msg.message 에 실패 이유 포함
                        return False
                    # Status.InProgress, Status.Warning: 계속 대기
        except Exception:
            pass  # 메시지 없음 또는 타임아웃 — 계속 대기
        time.sleep(5)
    return False  # 타임아웃
```

### 6.1 ReadMessagesOptions 파라미터

| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `desired_start_sequence_number` | int | 0 | 읽기 시작 시퀀스 번호 |
| `min_message_count` | int | 1 | 최소 읽을 메시지 수. 미충족 시 `NotEnoughMessagesException` |
| `max_message_count` | int | 1 | 최대 읽을 메시지 수 |
| `read_timeout_millis` | int | 0 | `min_message_count` 충족 대기 시간(ms). 0이면 즉시 반환 |

### 6.2 StatusMessage 구조

| 필드 | 타입 | 설명 |
|------|------|------|
| `status` | Status | `Success` / `Failure` / `InProgress` / `Warning` / `Canceled` |
| `status_context` | StatusContext | 관련 스트림/태스크 정보 |
| `status_context.sequence_number` | int | append_message가 반환한 시퀀스 번호 (태스크 ID) |
| `status_context.s3_export_task_definition` | S3ExportTaskDefinition | 해당 태스크의 원본 정의 (input_url, bucket, key 포함) |
| `status_context.stream_name` | str | 태스크가 속한 스트림 이름 |
| `message` | str | 실패 시 오류 메시지 |
| `timestamp_epoch_ms` | int | 상태 생성 시각 (epoch milliseconds) |

#### Status 코드 의미

| Status | 의미 | 다음 행동 |
|--------|------|-----------|
| `Success` | 업로드 완료 | 스풀 파일 삭제 가능 |
| `Failure` | 오류 발생 (버킷 미존재 등) | 문제 해결 후 태스크 재등록 |
| `Canceled` | 스트림/export 삭제 또는 TTL 만료로 취소 | 태스크 재등록 필요 |
| `InProgress` | 업로드 진행 중 | 계속 대기 |
| `Warning` | 부분 실패 (partial upload 정리 실패 등) | 계속 대기 (업로드는 진행 중) |

---

## 7. 전체 예제 — 파일 업로드 + 완료 감지 + 삭제

```python
import time
from pathlib import Path
from stream_manager import StreamManagerClient
from stream_manager.data import (
    ExportDefinition,
    MessageStreamDefinition,
    Persistence,
    ReadMessagesOptions,
    S3ExportTaskDefinition,
    S3ExportTaskExecutorConfig,
    Status,
    StatusConfig,
    StatusLevel,
    StatusMessage,
    StrategyOnFull,
)
from stream_manager.util import Util
from stream_manager.exceptions import StreamManagerException

STREAM_NAME = "telemetry-stream"
STATUS_STREAM_NAME = "telemetry-status-stream"
S3_BUCKET = "my-iot-data-bucket"

def setup_streams(client: StreamManagerClient) -> None:
    """스트림이 없으면 생성한다. 이미 있으면 예외를 무시한다."""
    # 1. 상태 스트림 먼저 생성
    try:
        client.create_message_stream(MessageStreamDefinition(
            name=STATUS_STREAM_NAME,
            strategy_on_full=StrategyOnFull.OverwriteOldestData,
            persistence=Persistence.Memory,
        ))
    except StreamManagerException:
        pass  # 이미 존재

    # 2. S3 Export 스트림 생성
    try:
        client.create_message_stream(MessageStreamDefinition(
            name=STREAM_NAME,
            max_size=268_435_456,           # 256 MB 버퍼
            strategy_on_full=StrategyOnFull.OverwriteOldestData,
            persistence=Persistence.File,
            export_definition=ExportDefinition(
                s3_task_executor=[
                    S3ExportTaskExecutorConfig(
                        identifier="TelemetryS3Exporter",
                        size_threshold_for_multipart_upload_bytes=5_242_880,  # 5 MB
                        status_config=StatusConfig(
                            status_level=StatusLevel.INFO,
                            status_stream_name=STATUS_STREAM_NAME,
                        )
                    )
                ]
            )
        ))
    except StreamManagerException:
        pass  # 이미 존재


def upload_file_to_s3(
    client: StreamManagerClient,
    local_file: Path,
    s3_key: str,
) -> bool:
    """
    로컬 파일을 S3에 업로드하고 완료를 확인한 뒤 로컬 파일을 삭제한다.
    파일 크기와 무관하게 동작 (SM이 멀티파트 자동 처리).
    Returns: True = 성공, False = 실패
    """
    # S3ExportTaskDefinition 등록
    task = S3ExportTaskDefinition(
        input_url=f"file://{local_file.absolute()}",
        bucket=S3_BUCKET,
        key=s3_key,
    )
    seq = client.append_message(
        stream_name=STREAM_NAME,
        data=Util.validate_and_serialize_to_json_bytes(task),
    )

    # 상태 스트림에서 완료 대기
    deadline = time.time() + 300  # 5분 타임아웃
    while time.time() < deadline:
        try:
            messages = client.read_messages(
                STATUS_STREAM_NAME,
                options=ReadMessagesOptions(min_message_count=1, read_timeout_millis=1000)
            )
            for msg in messages:
                status_msg = Util.deserialize_json_bytes_to_obj(msg.payload, StatusMessage)
                ctx = status_msg.status_context
                if ctx and ctx.sequence_number == seq:
                    if status_msg.status == Status.Success:
                        local_file.unlink(missing_ok=True)  # 스풀 파일 삭제
                        return True
                    elif status_msg.status in (Status.Failure, Status.Canceled):
                        return False
        except StreamManagerException:
            pass
        time.sleep(5)
    return False  # 타임아웃


# --- 사용 예 ---
client = StreamManagerClient()
try:
    setup_streams(client)
    success = upload_file_to_s3(
        client,
        local_file=Path("/var/spool/s3-spooler/telemetry__key__reading.bin"),
        s3_key="raw/device-001/2026/06/01/reading.bin",
    )
    print("업로드 성공" if success else "업로드 실패")
finally:
    client.close()
```

---

## 8. 파일 라이프사이클 — 공식 문서 명시 사항

> 출처: AWS 공식 문서 "Manage input data" 섹션

1. 로컬 프로세스가 Core Device 디렉토리에 파일을 생성
2. Greengrass 컴포넌트가 디렉토리를 스캔하고 새 파일 발견 시 `append_message`로 태스크 등록
3. Stream Manager가 append된 순서대로 파일을 읽어 S3에 업로드
   - **대상 버킷은 사전에 존재해야 함**
   - 지정된 키의 오브젝트가 없으면 SM이 자동 생성
4. Greengrass 컴포넌트가 상태 스트림을 읽어 업로드 완료 확인
5. **업로드 완료 후 컴포넌트가 직접 입력 파일 삭제** — SM이 자동 삭제하지 않음

> ⚠️ SM은 `input_url`의 로컬 파일을 자동으로 삭제하지 않는다.  
> 컴포넌트가 `Status.Success` 수신 후 직접 삭제해야 한다.

---

## 9. 오류 재시도 정책

- SM은 실패한 export를 **최대 5분 간격**으로 계속 재시도
- 재시도 횟수 제한 없음
- `Status.Warning`: 오류가 발생했지만 태스크 실행에는 영향 없음 (예: 부분 업로드 정리 실패)
- 버킷 미존재 등 치명적 오류 → `Status.Failure` → 문제 해결 후 태스크 재등록 필요

---

## 10. SDK 참고 링크

| 문서 | URL |
|------|-----|
| StreamManagerClient (Python) | https://aws-greengrass.github.io/aws-greengrass-stream-manager-sdk-python/_apidoc/stream_manager.streammanagerclient.html |
| S3ExportTaskExecutorConfig (Python) | https://aws-greengrass.github.io/aws-greengrass-stream-manager-sdk-python/_apidoc/stream_manager.data.html#stream_manager.data.S3ExportTaskExecutorConfig |
| S3ExportTaskDefinition (Python) | https://aws-greengrass.github.io/aws-greengrass-stream-manager-sdk-python/_apidoc/stream_manager.data.html#stream_manager.data.S3ExportTaskDefinition |
| StatusMessage (Python) | https://aws-greengrass.github.io/aws-greengrass-stream-manager-sdk-python/_apidoc/stream_manager.data.html#stream_manager.data.StatusMessage |
| work-with-streams | https://docs.aws.amazon.com/greengrass/v2/developerguide/work-with-streams.html |
| stream-export-configurations | https://docs.aws.amazon.com/greengrass/v2/developerguide/stream-export-configurations.html |
