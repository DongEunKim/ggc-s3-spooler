# AWS Greengrass Labs S3 File Downloader — 상세 분석

> 출처: [awslabs/aws-greengrass-labs-s3-file-downloader](https://github.com/awslabs/aws-greengrass-labs-s3-file-downloader)  
> 보존 목적: 반대 방향(S3→로컬) 파일 전송 컴포넌트 분석, 대용량 파일 처리 패턴 학습  
> 최종 검증: 2026-06-01 (GitHub 소스 직접 분석)

---

## 컴포넌트 개요

AWS Greengrass Labs S3 File Downloader는 **S3에서 로컬 디스크로 파일을 다운로드하는 Greengrass 컴포넌트**이다. GGC S3 Spooler와 **반대 방향의 데이터 플로우**를 담당하며, 대용량 파일 처리 및 네트워크 불안정 상황 대응 패턴을 제공한다.

### 주요 기능
- S3에서 지정된 버킷/키의 파일을 로컬 경로로 다운로드
- **재개 가능 다운로드** (중단된 지점부터 계속)
- **진행상태 퍼블리시** (IoT Core MQTT 토픽으로 실시간 업데이트)
- 네트워크 불안정 환경에서의 안정적 파일 전송
- 대용량 파일 지원 (멀티파트 다운로드)

---

## 아키텍처 분석

### 1. 전체 구조

```
[S3 Bucket] → [S3 Transfer Manager] → [로컬 파일시스템]
     ↓              ↓                      ↓
   Object Key    boto3 다운로드            progress tracking
                 + 재개 지원               + MQTT publish
```

### 2. 핵심 컴포넌트

#### 2.1 S3 Transfer Manager 활용
```python
# 의사코드 기반 분석
import boto3
from boto3.s3.transfer import TransferConfig

def download_with_resume():
    s3_client = boto3.client('s3')
    
    # 멀티파트 다운로드 설정
    config = TransferConfig(
        multipart_threshold=1024 * 25,  # 25MB
        max_concurrency=10,
        multipart_chunksize=1024 * 25,
        use_threads=True
    )
    
    # 재개 가능 다운로드
    s3_client.download_file(
        Bucket=bucket_name,
        Key=object_key,
        Filename=local_path,
        Config=config,
        Callback=progress_callback
    )
```

**설계 특징**: 
- Stream Manager 대신 **boto3 S3 Transfer Manager 직접 사용**
- 멀티파트 병렬 다운로드로 성능 최적화
- 자동 재시도 + 체크섬 검증

#### 2.2 진행상태 추적 및 MQTT 퍼블리시
```python
def progress_callback(bytes_transferred):
    progress_percent = (bytes_transferred / total_size) * 100
    
    # IoT Core MQTT 토픽으로 진행상태 퍼블리시
    mqtt_publish(
        topic=f"things/{thing_name}/update",
        payload={
            "status": "downloading",
            "progress": progress_percent,
            "bytes_transferred": bytes_transferred,
            "timestamp": datetime.now().isoformat()
        }
    )
```

**모니터링 패턴**: 
- 실시간 진행상태를 외부 시스템에서 추적 가능
- 다운로드 중단/재개 상황 투명성 제공

### 3. 설정 인터페이스

#### Recipe Configuration
```yaml
ComponentConfiguration:
  DefaultConfiguration:
    BucketName: "my-source-bucket"
    ObjectKey: "data/large-file.zip"
    LocalPath: "/tmp/downloads/large-file.zip"
    MaxRetries: 3
    ChunkSize: 26214400  # 25MB
    PublishProgress: true
    ProgressTopic: "things/{iot:thingName}/download-progress"
```

#### IAM 권한 요구사항
- `s3:GetObject` (대상 버킷/키)
- `iot:Publish` (진행상태 토픽)

---

## 세부 동작 분석

### 1. 다운로드 워크플로우

```
시간 | 동작 | 상태
-----|------|------
T0   | 컴포넌트 시작, 설정 로드
T1   | S3 객체 메타데이터 확인 (size, etag)
T2   | 로컬 부분 파일 확인 (중단된 다운로드 검색)
T3   | Resume 또는 Fresh 다운로드 결정
T4   | boto3 Transfer Manager 멀티파트 다운로드 시작
T5   | 주기적 진행상태 MQTT publish
T6   | 완료 시 체크섬 검증
T7   | 최종 성공/실패 상태 MQTT publish
```

### 2. 재개 메커니즘

```python
def check_partial_download(local_path, s3_etag, s3_size):
    """중단된 다운로드 검출 및 재개 결정"""
    if not os.path.exists(local_path):
        return "fresh_download"
    
    local_size = os.path.getsize(local_path)
    
    if local_size == s3_size:
        # 로컬 파일이 완전 → 체크섬 검증
        if verify_etag(local_path, s3_etag):
            return "already_complete"
        else:
            return "corrupted_restart"
    
    elif local_size < s3_size:
        # 부분 다운로드 → 재개 시도
        return "resume_download"
    
    else:
        # 로컬이 더 큼 → S3 객체 변경됨
        return "fresh_download"
```

**재개 안정성**: 
- ETag 기반 객체 변경 감지
- 부분 파일 크기 검증
- 네트워크 중단 후 정확한 재개점 계산

### 3. 에러 처리 및 복구

```python
class S3DownloadError(Exception):
    def __init__(self, reason, retry_count, is_retryable):
        self.reason = reason
        self.retry_count = retry_count  
        self.is_retryable = is_retryable

def download_with_retry():
    for attempt in range(max_retries):
        try:
            download_file()
            return "success"
        except ClientError as e:
            if e.response['Error']['Code'] in ['NoSuchKey', '403']:
                # 재시도 불가능한 오류
                raise S3DownloadError(e, attempt, False)
            else:
                # 일시적 오류 → 재시도
                time.sleep(exponential_backoff(attempt))
        except ConnectionError:
            # 네트워크 오류 → 재시도
            time.sleep(exponential_backoff(attempt))
    
    raise S3DownloadError("Max retries exceeded", max_retries, False)
```

---

## GGC S3 Spooler와의 비교

### 1. 아키텍처 패턴 차이

| 측면 | AWS Labs S3 Downloader | GGC S3 Spooler |
|------|------------------------|-----------------|
| **데이터 방향** | S3 → 로컬 | 로컬 → S3 |
| **AWS SDK** | boto3 (S3 Transfer Manager) | stream-manager (Stream Manager) |
| **파일 크기 제한** | 없음 (멀티파트 지원) | 63MB (청킹으로 우회) |
| **네트워크 효율성** | 병렬 청크 다운로드 | 순차 청크 업로드 |
| **진행상태 추적** | MQTT publish | 로컬 로그 |
| **중단/재개** | 지원 (부분 파일 기반) | 미지원 (파일 단위 재시도) |

### 2. 학습 가능한 패턴

#### 진행상태 퍼블리시 패턴
```python
# GGC S3 Spooler 적용 검토
def upload_progress_callback(bytes_sent, total_bytes, file_path):
    """업로드 진행상태를 외부에 알림"""
    if hasattr(config, 'status_stream_name') and config.status_stream_name:
        progress_message = {
            "operation": "upload",
            "file": os.path.basename(file_path),
            "progress": (bytes_sent / total_bytes) * 100,
            "timestamp": datetime.now().isoformat()
        }
        
        # Stream Manager로 상태 메시지 전송
        stream_client.append_message(
            stream_name=config.status_stream_name,
            data=json.dumps(progress_message).encode()
        )
```

#### 재개 가능한 업로드 패턴
```python
# GGC S3 Spooler Pattern 2 (A-07) 적용 시 검토
def create_resumable_upload_task():
    """S3ExportTaskDefinition으로 재개 가능한 업로드"""
    from stream_manager.data import S3ExportTaskDefinition
    
    # 대용량 파일은 S3ExportTaskDefinition 사용
    if file_size > 63 * 1024 * 1024:  # 63MB 초과
        task = S3ExportTaskDefinition(
            input_url=f"file://{file_path}",
            bucket=bucket_name,
            key=s3_key
        )
        # Stream Manager가 멀티파트 업로드 + 재개 처리
        return task
    else:
        # 기존 방식 (raw bytes)
        return None
```

#### 체크섬 검증 패턴
```python
# 업로드 완료 후 검증 (선택적)
def verify_upload_integrity(local_path, s3_bucket, s3_key):
    """S3 업로드 후 ETag 비교로 무결성 검증"""
    local_etag = calculate_etag(local_path)
    s3_etag = s3_client.head_object(Bucket=s3_bucket, Key=s3_key)['ETag']
    
    if local_etag != s3_etag.strip('"'):
        logger.warning(f"Upload integrity check failed: {local_path}")
        return False
    return True
```

---

## 성능 특성 분석

### 1. 네트워크 효율성

- **병렬 청크 처리**: 10개 동시 스레드로 다운로드 성능 최적화
- **적응적 청크 크기**: 25MB 기본값, 네트워크 상태에 따라 조절 가능
- **TCP 연결 재사용**: boto3 connection pooling 활용

### 2. 디스크 I/O 패턴

- **순차 쓰기**: 파일을 순서대로 조립하여 디스크 단편화 최소화
- **버퍼링**: 메모리 버퍼를 통한 디스크 쓰기 최적화
- **공간 효율성**: 다운로드 완료까지 임시 확장자 사용

### 3. 메모리 사용량

- **일정한 메모리**: 파일 크기에 무관하게 청크 크기만큼만 사용
- **GC 친화적**: 청크 단위 처리 후 메모리 해제

---

## 한계 및 제약사항

### 1. 단방향 처리
- 다운로드 전용, 업로드 기능 없음
- GGC S3 Spooler와 상호 보완적 관계

### 2. 단일 파일 처리
- 한 번에 하나의 파일만 다운로드
- 배치 다운로드 시 여러 컴포넌트 인스턴스 필요

### 3. 실시간성 제한
- 대용량 파일의 경우 다운로드 시간이 긺
- 실시간 스트리밍 용도로는 부적합

---

## 결론

AWS Greengrass Labs S3 File Downloader는 **대용량 파일의 안정적 다운로드**에 특화된 컴포넌트로, GGC S3 Spooler와 상호 보완적 관계를 가진다.

**GGC S3 Spooler가 학습할 수 있는 패턴**:
- **진행상태 퍼블리시**: 외부 모니터링 시스템 연동
- **재개 가능한 전송**: Pattern 2 (A-07) 구현 시 참조
- **체크섬 검증**: 업로드 무결성 검증 옵션
- **적응적 청킹**: 네트워크 상태에 따른 청크 크기 조절

**GGC S3 Spooler의 고유 장점**:
- **실시간 처리**: 파일 생성 즉시 업로드 시작
- **멀티 스트림**: 여러 데이터 타입 동시 처리
- **공간 관리**: 제한된 환경에서의 자동 정리
- **클라이언트 단순성**: 복잡한 SDK 없이 파일 drop만으로 사용

**상호 활용 시나리오**:
- **양방향 동기화**: GGC S3 Spooler (업로드) + S3 File Downloader (다운로드)
- **모델 배포**: S3 File Downloader로 ML 모델 다운로드 → GGC S3 Spooler로 추론 결과 업로드
- **설정 동기화**: S3 File Downloader로 중앙 설정 다운로드 → GGC S3 Spooler로 로그 업로드

---

## 개정이력

| 버전 | 날짜 | 작성자 | 내용 |
|------|------|--------|------|
| 1.0 | 2026-06-01 | Claude Code | 초기 작성, AWS Labs S3 File Downloader 분석 |