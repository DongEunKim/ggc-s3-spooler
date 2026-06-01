# AWS Greengrass Labs S3 File Uploader — 상세 분석

> 출처: [awslabs/aws-greengrass-labs-s3-file-uploader](https://github.com/awslabs/aws-greengrass-labs-s3-file-uploader)  
> 보존 목적: GGC S3 Spooler와 가장 유사한 커뮤니티 컴포넌트 아키텍처 분석  
> 최종 검증: 2026-06-01 (GitHub 소스 직접 분석)

---

## 컴포넌트 개요

AWS Greengrass Labs S3 File Uploader는 **로컬 디렉토리를 모니터링하여 파일을 S3로 업로드하는 Greengrass 컴포넌트**이다. GGC S3 Spooler와 가장 직접적으로 비교할 수 있는 참조 구현체로, Stream Manager를 활용한 파일 업로드 패턴의 대표적 사례이다.

### 주요 기능
- 지정된 디렉토리의 파일을 패턴 매칭으로 선택
- Greengrass Stream Manager를 통한 S3 업로드
- 업로드 성공 시 로컬 파일 삭제
- 설정 가능한 스캔 간격 및 파일 패턴

---

## 아키텍처 분석

### 1. 전체 구조

```
[로컬 디렉토리] → [File Scanner] → [Stream Manager] → [S3 Bucket]
      ↓              ↓                     ↓
  *.ext 패턴    주기적 스캔(5초)        upload 완료 시
    파일들        파일 목록 수집          로컬 파일 삭제
```

### 2. 핵심 컴포넌트

#### 2.1 File Scanner Logic
```python
# 의사코드 기반 분석
def scan_and_upload():
    files = glob.glob(f"{PathName}")
    files.sort(key=lambda x: os.path.getmtime(x))
    
    # 핵심 로직: 가장 최근 파일은 제외 (미완성 업로드 방지)
    if len(files) > 1:
        files_to_upload = files[:-1]  # 마지막 파일 제외
    else:
        files_to_upload = []
    
    for file_path in files_to_upload:
        upload_to_stream_manager(file_path)
```

**설계 의도**: 현재 쓰여지고 있는 파일의 불완전한 업로드 방지

#### 2.2 Stream Manager 연동
```python
def upload_to_stream_manager(file_path):
    with open(file_path, 'rb') as f:
        data = f.read()
    
    # S3 객체 키 생성
    object_key = f"{ObjectKeyPrefix}/{os.path.basename(file_path)}"
    
    # Stream Manager로 전송
    sequence_number = stream_client.append_message(
        stream_name=STREAM_NAME,
        data=data
    )
    
    # 성공 시 로컬 파일 삭제
    os.remove(file_path)
```

### 3. 설정 인터페이스

#### Recipe Configuration
```yaml
ComponentConfiguration:
  DefaultConfiguration:
    PathName: "/local/path/to/monitor/*.ext"
    BucketName: "my-target-bucket" 
    ObjectKeyPrefix: "uploaded-files"
    Interval: 5  # 스캔 간격 (초)
```

#### Stream Configuration (별도 설정 필요)
- Stream Manager에서 해당 스트림이 사전 생성되어야 함
- S3Export 설정으로 BucketName과 연결
- Stream의 retention, size policy 등은 별도 관리

---

## 세부 동작 분석

### 1. 파일 처리 워크플로우

```
시간 | 동작 | 상태
-----|------|------
T0   | 스캔 주기 도래 (5초마다)
T1   | glob.glob()로 패턴 매칭 파일 수집
T2   | mtime 기준 정렬 (오래된 순)
T3   | 마지막 파일(최신) 제외하여 업로드 대상 선정
T4   | 각 파일별로 순차 처리:
     |   - 파일 읽기 (전체 메모리 로드)
     |   - Stream Manager append_message()
     |   - 성공 시 os.remove()
T5   | 다음 스캔까지 대기
```

### 2. 에러 처리 패턴

```python
try:
    sequence_number = stream_client.append_message(stream_name, data)
    os.remove(file_path)
    logger.info(f"Successfully uploaded and deleted {file_path}")
except Exception as e:
    logger.error(f"Failed to upload {file_path}: {e}")
    # 파일 삭제하지 않음 → 다음 스캔에서 재시도
```

**실패 처리 전략**: 
- 실패한 파일은 삭제하지 않음
- 다음 스캔 주기에서 자동 재시도
- at-least-once 배송 보장 (중복 업로드 가능)

### 3. 동시성 및 경쟁 조건

**파일 경쟁 조건 처리**:
- 최신 파일 제외 로직으로 write-in-progress 파일 업로드 방지
- 단일 스레드 순차 처리로 파일 레벨 경쟁 조건 회피
- 외부 프로세스의 동시 파일 생성/삭제는 고려되지 않음

---

## GGC S3 Spooler와의 비교

### 1. 아키텍처 패턴 차이

| 측면 | AWS Labs S3 File Uploader | GGC S3 Spooler |
|------|---------------------------|-----------------|
| **파일 탐지** | 주기적 스캔 (폴링) | 실시간 이벤트 (watchdog) |
| **파일 선택** | 패턴 매칭 + 최신 제외 | 파일명 디코딩 + 완전 파일만 |
| **라우팅 설정** | 컴포넌트 설정 (정적) | 파일명 인코딩 (동적) |
| **스트림 관리** | 단일 스트림 (고정) | 멀티 스트림 (filename 기반) |
| **메모리 사용** | 전체 파일 메모리 로드 | 청킹 지원 (63MB+) |
| **공간 관리** | 없음 | retention + quota 정책 |

### 2. 처리 성능 특성

#### 지연시간 (Latency)
- **AWS Labs**: 최대 스캔 간격만큼 지연 (기본 5초)
- **GGC S3 Spooler**: 거의 즉시 (~수백ms)

#### 처리량 (Throughput)
- **AWS Labs**: 메모리 제약에 의한 파일 크기 제한
- **GGC S3 Spooler**: 청킹으로 대용량 파일 지원

#### 리소스 사용
- **AWS Labs**: 주기적 CPU 스파이크, 파일 크기만큼 메모리
- **GGC S3 Spooler**: 이벤트 기반 일정 사용량, 일정 메모리

### 3. 운영 복잡도

#### 설정 관리
- **AWS Labs**: 간단 (4개 파라미터), Stream 별도 설정
- **GGC S3 Spooler**: 중간 복잡도, 통합 설정 + registry

#### 클라이언트 통합
- **AWS Labs**: 매우 간단 (파일 drop만)
- **GGC S3 Spooler**: 중간 (파일명 인코딩 필요)

#### 디버깅/모니터링
- **AWS Labs**: 기본 로깅, 진행상태 추적 없음
- **GGC S3 Spooler**: 상세 로깅, 큐 상태 추적

---

## 학습 포인트

### 1. 채택할 만한 패턴

#### 최신 파일 제외 로직
```python
# GGC S3 Spooler 적용 검토
def should_process_file(file_path):
    """업로드 중인 파일인지 휴리스틱 검사"""
    # 1. 매우 최근 생성된 파일은 잠깐 대기
    if time.time() - os.path.getmtime(file_path) < 1.0:
        return False
    
    # 2. 파일이 현재 열려있는지 검사 (Linux lsof 활용)
    # 구현 복잡도 vs 효과 검토 필요
    
    return True
```

#### 간단한 설정 인터페이스
```yaml
# GGC S3 Spooler recipe 간소화 검토
ComponentConfiguration:
  DefaultConfiguration:
    spool_dir: "/tmp/s3-spooler/spool"
    # stream_registry 대신 단일 기본 스트림 설정?
    default_stream: "telemetry"
    default_bucket: "my-telemetry-bucket"
```

### 2. 개선이 필요한 영역

#### 공간 관리 부재
AWS Labs 컴포넌트는 실패한 파일이 디스크에 계속 누적될 수 있다. GGC S3 Spooler의 retention/quota 정책이 우수함.

#### 멀티 스트림 미지원
단일 스트림 고정으로 여러 데이터 타입 처리 시 컴포넌트 다중 배포 필요. GGC S3 Spooler의 동적 라우팅이 우수함.

#### 대용량 파일 제한
메모리 전체 로드 방식은 임베디드 환경에서 문제. GGC S3 Spooler의 청킹이 우수함.

---

## 결론

AWS Greengrass Labs S3 File Uploader는 **단순하고 직관적인 파일 업로드 솔루션**으로, 기본적인 파일 업로드 요구사항을 Stream Manager 패턴으로 해결하는 좋은 참조 구현체이다.

**GGC S3 Spooler가 제공하는 고급 기능들**:
- 실시간 처리 (vs 주기적 폴링)
- 동적 멀티 스트림 라우팅 (vs 단일 스트림)
- 자동 공간 관리 (vs 공간 관리 없음)
- 대용량 파일 지원 (vs 메모리 제한)
- TGU 특화 배포 (vs 표준 환경)

**상호 보완적 학습 영역**:
- AWS Labs: 설정 간소성, 미완성 파일 처리 휴리스틱
- GGC S3 Spooler: 성능, 확장성, 운영 안정성

---

## 개정이력

| 버전 | 날짜 | 작성자 | 내용 |
|------|------|--------|------|
| 1.0 | 2026-06-01 | Claude Code | 초기 작성, AWS Labs S3 File Uploader 상세 분석 |