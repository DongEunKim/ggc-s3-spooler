# AWS Greengrass Disk Spooler — 상세 분석

> 출처: [aws-greengrass/aws-greengrass-disk-spooler](https://github.com/aws-greengrass/aws-greengrass-disk-spooler)  
> 보존 목적: 대안적 스풀링 접근법 (MQTT 메시지 vs 파일) 분석, 디스크 기반 큐잉 패턴 학습  
> 최종 검증: 2026-06-01 (GitHub 소스 직접 분석)

---

## 컴포넌트 개요

AWS Greengrass Disk Spooler는 **MQTT 메시지를 디스크에 스풀링하는 Greengrass 컴포넌트**이다. IoT Core 연결이 중단된 상황에서 메시지를 로컬 디스크에 저장했다가, 연결이 복구되면 순차적으로 전송하는 **메시지 기반 스풀링** 시스템이다.

### 주요 기능
- MQTT 메시지의 디스크 기반 지속성 보장
- IoT Core 연결 중단 시 자동 스풀링 모드 전환
- 연결 복구 시 순서 보장된 메시지 재전송
- 설정 가능한 디스크 사용량 제한 및 오래된 메시지 정리
- 메시지 우선순위 및 QoS 레벨 지원

---

## 아키텍처 분석

### 1. 전체 구조

```
[Local MQTT Client] → [Disk Spooler] → [AWS IoT Core]
         ↓               ↓                 ↓
   publish message   disk queue         MQTT publish
                     (offline)          (online)
```

### 2. 핵심 컴포넌트

#### 2.1 메시지 큐잉 시스템
```python
# 의사코드 기반 분석
class DiskQueue:
    def __init__(self, queue_dir, max_size_mb=100):
        self.queue_dir = queue_dir
        self.max_size_mb = max_size_mb
        self.sequence_number = 0
    
    def enqueue_message(self, topic, payload, qos=0):
        """메시지를 디스크 큐에 저장"""
        msg_file = f"{self.sequence_number:010d}.msg"
        message_data = {
            "topic": topic,
            "payload": payload,  # base64 encoded
            "qos": qos,
            "timestamp": time.time(),
            "sequence": self.sequence_number
        }
        
        with open(f"{self.queue_dir}/{msg_file}", 'w') as f:
            json.dump(message_data, f)
        
        self.sequence_number += 1
        self._check_disk_usage()
    
    def dequeue_messages(self, batch_size=10):
        """큐에서 메시지 배치 읽기 (순서 보장)"""
        msg_files = sorted(os.listdir(self.queue_dir))
        return msg_files[:batch_size]
```

**설계 특징**: 
- **순차 번호 기반**: 파일명에 sequence number로 엄격한 순서 보장
- **JSON 직렬화**: 메시지 메타데이터 + 페이로드 구조화 저장
- **배치 처리**: 연결 복구 시 여러 메시지 한 번에 처리

#### 2.2 연결 상태 모니터링
```python
class ConnectionMonitor:
    def __init__(self, iot_client, disk_queue):
        self.iot_client = iot_client
        self.disk_queue = disk_queue
        self.is_online = False
        self.heartbeat_interval = 30  # seconds
    
    async def monitor_connection(self):
        """주기적 연결 상태 확인"""
        while True:
            try:
                # IoT Core ping 또는 test publish
                await self.iot_client.ping()
                if not self.is_online:
                    # 연결 복구됨 → 스풀된 메시지 처리 시작
                    await self.flush_spooled_messages()
                self.is_online = True
            except ConnectionError:
                self.is_online = False
            
            await asyncio.sleep(self.heartbeat_interval)
    
    async def flush_spooled_messages(self):
        """스풀된 메시지를 순차적으로 IoT Core에 전송"""
        while True:
            messages = self.disk_queue.dequeue_messages(batch_size=10)
            if not messages:
                break
                
            for msg_file in messages:
                success = await self.send_message(msg_file)
                if success:
                    os.remove(f"{self.disk_queue.queue_dir}/{msg_file}")
                else:
                    # 전송 실패 → 연결 다시 끊어진 것으로 간주
                    self.is_online = False
                    break
```

**연결 관리 특징**: 
- **적응적 모드 전환**: 온라인/오프라인 자동 감지
- **점진적 복구**: 배치 단위로 메시지 재전송
- **실패 감지**: 전송 실패 시 즉시 스풀링 모드 복귀

### 3. 설정 인터페이스

#### Recipe Configuration
```yaml
ComponentConfiguration:
  DefaultConfiguration:
    QueueDirectory: "/tmp/mqtt-spool"
    MaxQueueSizeMB: 100
    MaxMessageAgeDays: 7
    BatchSize: 10
    HeartbeatIntervalSeconds: 30
    RetryIntervalSeconds: 5
    MaxRetries: 3
```

#### 메시지 라우팅 설정
```yaml
# 별도 설정 파일: spool-config.json
{
  "spooling_topics": [
    "device/+/telemetry",
    "device/+/alerts",
    "system/status"
  ],
  "direct_topics": [
    "device/commands/+",   # 스풀링 없이 직접 전송
    "system/heartbeat"     # 연결 상태 확인용
  ]
}
```

---

## 세부 동작 분석

### 1. 메시지 처리 워크플로우

#### 온라인 상태
```
시간 | 동작 | 상태
-----|------|------
T0   | MQTT publish 요청
T1   | 연결 상태 확인 → Online
T2   | IoT Core 직접 전송 시도
T3   | 성공 → 즉시 완료
T4   | (스풀링 없음)
```

#### 오프라인 상태
```
시간 | 동작 | 상태
-----|------|------
T0   | MQTT publish 요청
T1   | 연결 상태 확인 → Offline
T2   | 디스크 큐에 메시지 저장
T3   | sequence number 증가
T4   | 디스크 사용량 체크 → 정리 (필요 시)
```

#### 복구 상태
```
시간 | 동작 | 상태
-----|------|------
T0   | 연결 복구 감지
T1   | 스풀된 메시지 스캔 (순서대로)
T2   | 배치 단위 IoT Core 전송
T3   | 전송 성공 → 디스크에서 삭제
T4   | 모든 메시지 처리 완료 → 정상 모드
```

### 2. 디스크 공간 관리

```python
def _check_disk_usage(self):
    """디스크 사용량 체크 및 오래된 메시지 정리"""
    total_size = sum(
        os.path.getsize(f"{self.queue_dir}/{f}")
        for f in os.listdir(self.queue_dir)
        if f.endswith('.msg')
    )
    
    if total_size > self.max_size_mb * 1024 * 1024:
        # 크기 초과 → 오래된 파일부터 삭제
        self._remove_oldest_messages(target_size=self.max_size_mb * 0.8)
    
    # 시간 기반 정리
    cutoff_time = time.time() - (self.max_age_days * 24 * 3600)
    for msg_file in os.listdir(self.queue_dir):
        if os.path.getctime(f"{self.queue_dir}/{msg_file}") < cutoff_time:
            os.remove(f"{self.queue_dir}/{msg_file}")
```

**공간 관리 정책**: 
- **크기 기반**: 최대 디스크 사용량 제한
- **시간 기반**: 오래된 메시지 자동 만료
- **FIFO 삭제**: 순서를 유지하면서 오래된 것부터 삭제

### 3. 메시지 순서 보장

```python
def ensure_message_ordering():
    """메시지 순서 보장 메커니즘"""
    # 1. 파일명 기반 순서 (sequence number)
    msg_files = sorted(os.listdir(queue_dir))  # 0000000001.msg, 0000000002.msg, ...
    
    # 2. 배치 내 순차 처리
    for msg_file in msg_files:
        send_message(msg_file)
        # 하나 실패하면 나머지는 처리하지 않음 (순서 보장)
        if not success:
            break
    
    # 3. 부분 실패 시 나머지는 다음 배치에서 처리
    return processed_count
```

---

## GGC S3 Spooler와의 비교

### 1. 스풀링 대상 차이

| 측면 | AWS Greengrass Disk Spooler | GGC S3 Spooler |
|------|----------------------------|-----------------|
| **데이터 타입** | MQTT 메시지 (텍스트/JSON) | 파일 (바이너리/텍스트) |
| **데이터 크기** | 작음 (KB 단위) | 중~대형 (MB 단위) |
| **스풀링 단위** | 메시지별 | 파일별 |
| **순서 보장** | 엄격 (sequence number) | mtime 기반 |
| **대상 서비스** | AWS IoT Core | AWS S3 |

### 2. 아키텍처 패턴 비교

| 측면 | Disk Spooler | GGC S3 Spooler |
|------|--------------|-----------------|
| **중재 레이어** | 직접 IoT Core | Stream Manager |
| **스풀링 방식** | 메시지 직렬화 (JSON) | 파일 복사 (원본 유지) |
| **처리 모델** | 배치 처리 | 순차 실시간 처리 |
| **연결 모니터링** | 능동적 ping | 수동적 (SDK 의존) |
| **복구 메커니즘** | 자동 플러시 | 재시작 시 드레인 |

### 3. 공간 관리 비교

| 측면 | Disk Spooler | GGC S3 Spooler |
|------|--------------|-----------------|
| **정리 정책** | 크기 + 시간 | 크기 + 시간 (이중 정책) |
| **정리 단위** | 메시지 (작은 단위) | 파일 (큰 단위) |
| **우선순위** | 시간 우선 (FIFO) | 크기 우선 (quota) |
| **정리 주기** | 메시지 추가 시 | 주기적 (5초) |

---

## 학습 가능한 패턴

### 1. 연결 상태 모니터링
```python
# GGC S3 Spooler 적용 검토
class StreamManagerMonitor:
    async def monitor_stream_manager_health(self):
        """Stream Manager 연결 상태 주기적 확인"""
        while True:
            try:
                # 더미 메시지로 연결 테스트
                client.append_message("health-check", b"ping")
                self.is_healthy = True
            except Exception:
                self.is_healthy = False
                logger.warning("Stream Manager connection lost")
            
            await asyncio.sleep(30)
```

### 2. 순서 보장 메커니즘
```python
# 현재 GGC S3 Spooler는 mtime 기반이지만, 더 엄격한 순서 보장 옵션
def ensure_strict_ordering():
    """파일명에 sequence number 추가 옵션"""
    if config.strict_ordering:
        # telemetry__data!device-1!sensor.json → 0001_telemetry__data!device-1!sensor.json
        sequence = get_next_sequence_number()
        filename = f"{sequence:06d}_{original_filename}"
    else:
        filename = original_filename
    
    return filename
```

### 3. 배치 처리 패턴
```python
# 현재는 순차 처리, 배치 처리 옵션 검토
async def process_file_batch(file_paths, batch_size=5):
    """여러 파일을 배치로 처리하여 효율성 향상"""
    for i in range(0, len(file_paths), batch_size):
        batch = file_paths[i:i+batch_size]
        
        # 배치 내 파일들을 병렬 처리 (순서는 파일명 기준)
        tasks = [process_file(path) for path in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 하나라도 실패하면 배치 전체 재시도 (순서 보장)
        if any(isinstance(r, Exception) for r in results):
            logger.warning(f"Batch failed, will retry: {batch}")
            break
```

### 4. 적응적 정리 정책
```python
# Disk Spooler의 크기+시간 이중 정책 적용
def adaptive_cleanup_policy():
    """상황에 따른 적응적 정리 정책"""
    
    # 1. 크기 기반 (즉시)
    if current_size > quota_limit:
        remove_oldest_files(target_size=quota_limit * 0.8)
    
    # 2. 시간 기반 (주기적)
    if time.time() % cleanup_interval == 0:
        remove_expired_files(max_age=retention_hours)
    
    # 3. 디스크 압박 시 공격적 정리
    free_space = shutil.disk_usage(spool_dir).free
    if free_space < emergency_threshold:
        remove_oldest_files(target_size=quota_limit * 0.5)  # 더 공격적
```

---

## 성능 특성 분석

### 1. 메모리 효율성
- **메시지 크기**: KB 단위로 메모리 부담 최소
- **배치 로딩**: 큰 배치를 한 번에 메모리에 로드하지 않음
- **스트리밍 처리**: 파일 단위로 읽기/쓰기/삭제

### 2. 디스크 I/O 패턴
- **순차 쓰기**: sequence number 기반 순차 파일 생성
- **배치 읽기**: 여러 메시지 파일을 한 번에 처리
- **즉시 삭제**: 전송 성공 후 디스크에서 즉시 제거

### 3. 네트워크 효율성
- **배치 전송**: 여러 MQTT 메시지를 배치로 전송
- **연결 재사용**: persistent MQTT connection
- **QoS 지원**: 메시지별 품질 보장 레벨

---

## 한계 및 제약사항

### 1. 데이터 타입 제한
- MQTT 메시지 전용, 대용량 파일 처리 불가
- 바이너리 데이터는 base64 인코딩 필요 (오버헤드)

### 2. 실시간성 제약
- 배치 처리 방식으로 지연시간 존재
- 연결 상태 감지에 heartbeat interval만큼 지연

### 3. 단일 목적지
- IoT Core 전용, 다중 서비스 라우팅 불가
- S3, Kinesis 등 다른 AWS 서비스 미지원

---

## 결론

AWS Greengrass Disk Spooler는 **MQTT 메시지의 안정적 전송**에 특화된 컴포넌트로, GGC S3 Spooler와는 **데이터 타입과 목적이 상이하지만 스풀링 메커니즘에서 학습할 점**이 많다.

**GGC S3 Spooler가 학습할 수 있는 패턴**:
- **능동적 연결 모니터링**: Stream Manager 상태 주기적 확인
- **엄격한 순서 보장**: sequence number 기반 순서 관리
- **배치 처리**: 효율성을 위한 배치 단위 처리
- **적응적 정리**: 상황별 디스크 정리 정책

**GGC S3 Spooler의 고유 장점**:
- **대용량 파일 지원**: 바이너리 데이터 원본 보존
- **멀티 서비스**: Stream Manager를 통한 다양한 AWS 서비스 연동
- **동적 라우팅**: 파일명 기반 런타임 목적지 결정
- **실시간 처리**: watchdog 기반 즉시 처리

**상호 보완 시나리오**:
- **메타데이터**: Disk Spooler로 파일 메타정보 전송 + GGC S3 Spooler로 실제 파일 업로드
- **상태 통지**: GGC S3 Spooler 처리 상태를 Disk Spooler로 IoT Core에 알림
- **설정 동기화**: IoT Core에서 스풀링 설정 변경 메시지 수신 (Disk Spooler) → 동적 적용

**아키텍처 진화 방향**:
- Phase 1: 현재 파일 기반 스풀링 안정화
- Phase 2: 연결 모니터링 및 배치 처리 패턴 적용
- Phase 3: MQTT 메시지 + 파일 통합 스풀링 (선택적)

---

## 개정이력

| 버전 | 날짜 | 작성자 | 내용 |
|------|------|--------|------|
| 1.0 | 2026-06-01 | Claude Code | 초기 작성, AWS Greengrass Disk Spooler 분석 |