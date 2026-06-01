# CAN Blackbox Directory Uploader — 상세 분석

> 출처: [DongEunKim/can-blackbox-skeleton](https://github.com/DongEunKim/can-blackbox-skeleton)  
> 보존 목적: 폴링 기반 파일 스풀링 아키텍처 분석 및 GGC S3 Spooler 비교 참조  
> 최종 검증: 2026-06-01 (GitHub 소스 직접 분석)

---

## 컴포넌트 개요

CAN Blackbox Directory Uploader는 **자동차/산업용 CAN 데이터 수집에 특화된 파일 스풀링 시스템**이다. AWS Greengrass Stream Manager를 활용하되, GGC S3 Spooler와는 완전히 다른 아키텍처 접근법을 취한다.

### 주요 기능
- **CAN 데이터 특화**: CAN 버스 → BLF(Binary Logging Format) 파일 → S3 파이프라인
- **이중 프로세스 설계**: 독립적인 로거(`can_logger`) + 업로더(`directory_uploader`) 프로세스
- **폴링 기반 감지**: 5초 간격 디렉토리 스캔 (설정 가능)
- **파일 안정성 검증**: 연속 폴링에서 파일 크기 불변 확인
- **태스크 기반 업로드**: `S3ExportTaskDefinition` JSON으로 Stream Manager 활용
- **Mock 테스트 지원**: 완전한 격리 테스트 환경 제공

---

## 아키텍처 분석

### 1. 전체 구조

```
[CAN Hardware] → [can_logger] → [BLF Files] → [directory_uploader] → [Stream Manager] → [S3]
       ↓              ↓              ↓                ↓                      ↓
   CAN 메시지     독립 프로세스     로컬 저장      폴링 기반 감지        Task Definition
                 (systemd)      (10MB 단위)      (5초 간격)           JSON 전송
```

### 2. 핵심 컴포넌트

#### 2.1 Directory Uploader (599줄 Python)
```python
class DirectoryUploader:
    def __init__(self, watch_dir, client, storage_limit_mb):
        self.watch_dir = watch_dir
        self.client = client  # UploadClient 또는 MockUploadClient
        self.known_files = {}  # 파일별 안정성 추적
        self.min_stable_polls = 2  # 안정성 확인 최소 횟수
        
    def _scan_new_files(self):
        """폴링 기반 새 파일 감지 + 안정성 검증"""
        for blf_file in glob.glob(f"{self.watch_dir}/*.blf"):
            current_size = os.path.getsize(blf_file)
            
            if blf_file in self.known_files:
                # 기존 파일 크기 추적
                if self.known_files[blf_file]['size'] == current_size:
                    self.known_files[blf_file]['stable_count'] += 1
                else:
                    # 크기 변경됨 → 아직 쓰여지고 있음
                    self.known_files[blf_file] = {
                        'size': current_size, 'stable_count': 0
                    }
            else:
                # 새 파일 발견
                if current_size > 0:  # 0바이트 파일 제외
                    self.known_files[blf_file] = {
                        'size': current_size, 'stable_count': 0
                    }
                    
            # 안정성 확인된 파일 처리
            if (self.known_files[blf_file]['stable_count'] >= 
                self.min_stable_polls):
                self._on_new_file(blf_file)
                del self.known_files[blf_file]
```

**설계 철학**: 
- 파일 크기가 연속 N회 폴링에서 불변이면 "완성된 파일"로 판단
- 시간 기반 지연보다 **데이터 무결성 우선**

#### 2.2 S3ExportTaskDefinition 활용 패턴
```python
def _on_new_file(self, file_path):
    """S3 업로드 태스크 생성"""
    task_definition = {
        "inputUrl": f"file://{os.path.abspath(file_path)}",
        "bucket": self.config.s3_bucket,
        "key": f"{self.config.s3_prefix}/{os.path.basename(file_path)}"
    }
    
    # Stream Manager에 태스크 전송 (파일 내용이 아닌 JSON)
    self.stream_manager_client.append_message(
        stream_name="s3-export-stream",
        data=json.dumps(task_definition).encode()
    )
    
    # Status Stream에서 완료 대기
    self._monitor_upload_status(file_path, task_id)
```

**핵심 차이점**: 
- GGC S3 Spooler: 파일 내용을 직접 바이너리로 전송
- CAN Blackbox: 파일 경로가 포함된 JSON 태스크만 전송, Stream Manager가 파일 읽기

#### 2.3 Status Stream 모니터링
```python
def _monitor_upload_status(self, file_path, task_id):
    """업로드 상태 모니터링 및 파일 생명주기 관리"""
    timeout = 300  # 5분 타임아웃
    
    while timeout > 0:
        status_messages = self.stream_manager_client.read_messages(
            stream_name="status-stream"
        )
        
        for message in status_messages:
            if message['taskId'] == task_id:
                if message['status'] == 'Success':
                    os.remove(file_path)  # 성공 시 삭제
                    return
                elif message['status'] in ['Failed', 'Canceled']:
                    # 실패 시 파일 보존, 재시도 가능
                    return
        
        time.sleep(5)
        timeout -= 5
```

### 3. 설정 인터페이스

#### INI 기반 설정 (환경 무관)
```ini
[logger]
backend = python  # 또는 c

[can]
interfaces = vcan0,vcan1
auto_reconnect = true

[logging]
output_dir = /data/can_logs
rotation_size_mb = 10
file_prefix = vehicle_

[storage]
max_total_mb = 500  # 자동 정리 트리거

[watcher]
poll_interval_seconds = 5
min_stable_polls = 2

[stream_manager]
use_mock = false
s3_bucket = my-can-data-bucket
s3_prefix = vehicle-001/logs
export_stream_name = can-s3-export
status_stream_name = can-upload-status
```

#### Mock vs Real 모드
```python
# 테스트용 Mock 모드
if config.use_mock:
    client = MockUploadClient(local_copy_dir="/tmp/mock_s3")
else:
    client = UploadClient(stream_manager_client)
```

**Mock 모드 장점**: 
- Stream Manager 없이도 완전한 기능 테스트
- 로컬 디렉토리에 파일 복사로 업로드 시뮬레이션
- CI/CD 파이프라인에서 외부 의존성 제거

---

## 세부 동작 분석

### 1. 파일 처리 워크플로우

```
시간 | 동작 | 상태
-----|------|------
T0   | can_logger가 CAN 메시지 수집 시작
T1   | 10MB 도달 시 vehicle_20241201_001.blf 파일 생성
T2   | directory_uploader 폴링 (5초 후)
T3   | 새 파일 발견, 크기 기록 (stable_count=0)
T8   | 2번째 폴링, 크기 동일 확인 (stable_count=1)
T13  | 3번째 폴링, 크기 동일 확인 (stable_count=2)
T13  | min_stable_polls 도달 → 업로드 태스크 생성
T14  | S3ExportTaskDefinition JSON → Stream Manager
T15+ | Stream Manager가 파일 읽기 + S3 업로드 (비동기)
T20+ | Status Stream에서 Success 확인 → 파일 삭제
```

### 2. 공간 관리 정책

```python
def _trim_storage(self):
    """간단한 FIFO 정리 정책"""
    total_size = sum(os.path.getsize(f) for f in self.get_all_files())
    
    if total_size > self.storage_limit_bytes:
        # 가장 오래된 파일부터 삭제
        files_by_age = sorted(self.get_all_files(), 
                            key=lambda f: os.path.getmtime(f))
        
        for file_path in files_by_age:
            os.remove(file_path)
            total_size -= os.path.getsize(file_path)
            if total_size <= self.storage_limit_bytes * 0.8:  # 20% 여유
                break
```

**단순화된 정책**: 
- 크기 기반 정리만 지원 (시간 기반 retention 없음)
- FIFO 순서로 삭제 (GGC S3 Spooler의 이중 정책과 비교)

### 3. 이중 프로세스 아키텍처

```bash
# systemd 서비스 분리
blf-can-logger.service    # CAN 메시지 → BLF 파일
blf-uploader.service      # BLF 파일 → S3 업로드

# 장점: 독립적 재시작 가능
sudo systemctl restart blf-can-logger    # 업로드에 영향 없음
sudo systemctl restart blf-uploader      # 로깅에 영향 없음
```

---

## GGC S3 Spooler와의 비교

### 1. 아키텍처 패턴 차이

| 측면 | CAN Blackbox Directory Uploader | GGC S3 Spooler |
|------|----------------------------------|-----------------|
| **프로세스 구조** | 이중 프로세스 (로거 + 업로더) | 단일 통합 프로세스 |
| **파일 감지** | 폴링 (5초 간격) | 실시간 watchdog 이벤트 |
| **안정성 검증** | 파일 크기 추적 (N회 확인) | 시간 지연 (1초) |
| **업로드 방식** | S3ExportTaskDefinition JSON | 직접 바이너리 전송 |
| **라우팅** | 단일 버킷/프리픽스 | 파일명 기반 동적 라우팅 |
| **공간 관리** | 단순 FIFO (크기 기반만) | 이중 정책 (retention + quota) |
| **도메인 특화** | CAN 데이터 전용 | 범용 파일 스풀러 |

### 2. 성능 특성 비교

#### 지연시간 (Latency)
- **CAN Blackbox**: 5-15초 (폴링 간격 + 안정성 확인)
- **GGC S3 Spooler**: ~1-2초 (실시간 이벤트 + 1초 지연)

#### 안정성 (Reliability)
- **CAN Blackbox**: 매우 높음 (파일 크기 추적으로 확실한 완성도 보장)
- **GGC S3 Spooler**: 높음 (시간 지연 기반 휴리스틱)

#### 확장성 (Scalability)
- **CAN Blackbox**: 단일 디렉토리, 순차 처리
- **GGC S3 Spooler**: 멀티 클라이언트, 동시 멀티 스트림

#### 테스트 용이성
- **CAN Blackbox**: 우수 (완전한 Mock 격리)
- **GGC S3 Spooler**: 보통 (MockStreamManagerClient)

### 3. 운영 복잡도

#### 설정 관리
- **CAN Blackbox**: 낮음 (INI 파일, 명확한 섹션)
- **GGC S3 Spooler**: 중간 (YAML recipe + CLI 파라미터)

#### 장애 격리
- **CAN Blackbox**: 우수 (프로세스별 독립 재시작)
- **GGC S3 Spooler**: 보통 (통합 프로세스)

#### 배포 복잡도
- **CAN Blackbox**: 높음 (2개 systemd 서비스 + 의존성 관리)
- **GGC S3 Spooler**: 보통 (단일 Greengrass 컴포넌트)

---

## 학습 가능한 패턴

### 1. 채택할 만한 패턴

#### 파일 크기 추적 기반 안정성 검증
```python
# GGC S3 Spooler 개선 검토
class StabilityTracker:
    def __init__(self, min_stable_checks=3, check_interval=0.5):
        self.files = {}
        self.min_stable_checks = min_stable_checks
        self.check_interval = check_interval
    
    def is_file_stable(self, path):
        """CAN Blackbox 패턴 적용"""
        current_size = path.stat().st_size
        
        if path not in self.files:
            self.files[path] = {'size': current_size, 'count': 0}
            return False
        
        if self.files[path]['size'] == current_size:
            self.files[path]['count'] += 1
            return self.files[path]['count'] >= self.min_stable_checks
        else:
            # 크기 변경됨 → 재시작
            self.files[path] = {'size': current_size, 'count': 0}
            return False
```

#### Mock 클라이언트 추상화 패턴
```python
# GGC S3 Spooler 테스트 개선
class UploadClientProtocol(Protocol):
    def upload_file(self, stream_id: str, s3_key: str, file_path: Path) -> bool:
        ...

class StreamManagerUploadClient:
    def upload_file(self, stream_id, s3_key, file_path):
        # 기존 Stream Manager 로직
        pass

class MockUploadClient:
    def __init__(self, copy_dir):
        self.copy_dir = Path(copy_dir)
    
    def upload_file(self, stream_id, s3_key, file_path):
        # 파일을 로컬 디렉토리에 복사만
        dest = self.copy_dir / stream_id / s3_key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, dest)
        return True
```

#### INI 기반 설정 간소화
```ini
# GGC S3 Spooler 설정 간소화 검토
[spooler]
spool_dir = /var/spool/s3-spooler/spool
incomplete_file_delay = 1.0
default_stream = telemetry

[streams]
telemetry = telemetry-bucket
alerts = alerts-bucket
diagnostics = diagnostics-bucket

[advanced]
max_spool_size_mb = 900
file_retention_hours = 24
poll_interval_seconds = 5
```

### 2. GGC S3 Spooler 고유 장점 유지

#### 실시간 이벤트 기반 처리
- CAN Blackbox의 5초 폴링은 실시간 요구사항에서 불리
- watchdog 이벤트의 ~100ms 응답성은 유지해야 할 핵심 장점

#### 동적 멀티 스트림 라우팅
- filename-as-metadata 패턴은 CAN Blackbox 대비 확장성에서 우수
- 단일 설정으로 여러 데이터 타입 처리하는 운영 효율성

#### 통합 프로세스의 단순성
- 이중 프로세스는 격리에는 좋지만 배포/운영 복잡도 증가
- Greengrass 컴포넌트로서의 단일 패키지 배포 우위

---

## 결론

CAN Blackbox Directory Uploader는 **도메인 특화와 안정성에 중점을 둔 폴링 기반 접근법**으로, GGC S3 Spooler와는 상호 보완적인 설계 철학을 보여준다.

**CAN Blackbox의 핵심 기여**:
- **파일 크기 추적**: 시간 기반보다 확실한 완성도 검증
- **프로세스 분리**: 관심사 분리를 통한 장애 격리
- **Mock 추상화**: 완전한 테스트 격리 패턴
- **설정 단순성**: INI 형식의 명확한 구조화

**GGC S3 Spooler의 지속 우위**:
- **실시간성**: 폴링 대비 10배+ 빠른 반응성
- **동적 라우팅**: 설정 없이 파일명 기반 멀티 스트림 처리
- **공간 인식**: 이중 정책으로 정교한 디스크 관리
- **범용성**: 도메인에 구속되지 않는 유연한 파일 처리

**상호 학습 방향**:
- CAN Blackbox → GGC: 파일 크기 추적, Mock 패턴, 설정 간소화
- GGC → CAN Blackbox: 실시간 이벤트, 동적 라우팅, 공간 관리

이 분석을 통해 파일 스풀링 시스템 설계에서 **실시간성 vs 안정성**, **통합 vs 분리**, **범용성 vs 특화**의 트레이드오프를 명확히 이해할 수 있다.

---

## 개정이력

| 버전 | 날짜 | 작성자 | 내용 |
|------|------|--------|------|
| 1.0 | 2026-06-01 | Claude Code | 초기 작성, CAN Blackbox Directory Uploader 상세 분석 |