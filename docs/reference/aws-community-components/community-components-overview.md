# AWS Greengrass Community Components — 커뮤니티 컴포넌트 카탈로그

> 출처: AWS Labs GitHub, AWS Greengrass Software Catalog, Community Repositories  
> 보존 목적: GGC S3 Spooler와 유사한 커뮤니티 컴포넌트 현황 파악 및 설계 참조  
> 최종 검증: 2026-06-01 (GitHub 소스 직접 분석)

---

## 개요

AWS Greengrass 커뮤니티에는 파일 처리, S3 연동, 데이터 스풀링과 관련된 다양한 컴포넌트들이 존재한다. 이 문서는 GGC S3 Spooler와 유사하거나 관련된 컴포넌트들을 분류·정리하여 아키텍처 비교 및 향후 개발 방향 결정에 참조할 수 있도록 한다.

---

## 1. 직접 비교 가능 컴포넌트

### 1.1 AWS Greengrass Labs S3 File Uploader ⭐ **주요 비교 대상**

- **Repository**: [awslabs/aws-greengrass-labs-s3-file-uploader](https://github.com/awslabs/aws-greengrass-labs-s3-file-uploader)
- **기능**: 디렉토리 모니터링 + S3 업로드 + 성공 시 파일 삭제
- **핵심 아키텍처**: Greengrass Stream Manager 사용
- **유사도**: ⭐⭐⭐⭐⭐ (가장 직접적으로 비교 가능)
- **상세 분석**: [s3-file-uploader-analysis.md](s3-file-uploader-analysis.md)

### 1.2 AWS Greengrass Labs S3 File Downloader

- **Repository**: [awslabs/aws-greengrass-labs-s3-file-downloader](https://github.com/awslabs/aws-greengrass-labs-s3-file-downloader)
- **기능**: S3에서 로컬 디스크로 파일 다운로드 (반대 방향)
- **핵심 아키텍처**: S3 Transfer Manager 직접 사용
- **유사도**: ⭐⭐⭐ (방향은 반대지만 파일 전송 패턴 유사)
- **상세 분석**: [s3-file-downloader-analysis.md](s3-file-downloader-analysis.md)

---

## 2. 관련 스풀링/버퍼링 컴포넌트

### 2.1 AWS Greengrass Disk Spooler

- **Repository**: [aws-greengrass/aws-greengrass-disk-spooler](https://github.com/aws-greengrass/aws-greengrass-disk-spooler)
- **기능**: MQTT 메시지 디스크 스풀링 (IoT Core 연결 중단 시)
- **핵심 아키텍처**: 메시지 큐잉, 파일 기반 지속성
- **유사도**: ⭐⭐ (메시지 vs 파일 스풀링 차이)
- **상세 분석**: [disk-spooler-analysis.md](disk-spooler-analysis.md)

### 2.2 Stream Manager Component (공식)

- **Repository**: [aws-greengrass/aws-greengrass-stream-manager-sdk-python](https://github.com/aws-greengrass/aws-greengrass-stream-manager-sdk-python)
- **기능**: 로컬 데이터 스트림 → AWS 클라우드 (S3, Kinesis 등) 전송
- **핵심 아키텍처**: IPC 기반 메시지 전송, 로컬 버퍼링
- **유사도**: ⭐⭐⭐⭐ (GGC S3 Spooler의 기반 인프라)
- **참조**: [../aws-stream-manager-overview.md](../aws-stream-manager-overview.md) (기존 분석)

---

## 3. 도메인 특화 컴포넌트

### 3.1 CAN Blackbox Directory Uploader ⭐ **폴링 기반 접근법**

- **Repository**: [DongEunKim/can-blackbox-skeleton](https://github.com/DongEunKim/can-blackbox-skeleton)
- **기능**: CAN 데이터 BLF 파일의 안정적 S3 업로드
- **핵심 아키텍처**: 이중 프로세스 (로거+업로더), 폴링 기반 파일 감지, S3ExportTaskDefinition 활용
- **유사도**: ⭐⭐⭐⭐ (Stream Manager 기반이지만 완전히 다른 접근법)
- **특화 분야**: 자동차/산업 CAN 데이터 수집
- **상세 분석**: [can-blackbox-analysis.md](can-blackbox-analysis.md)

**주요 차별점**:
- **폴링 vs 실시간**: 5초 간격 스캔 vs GGC의 watchdog 이벤트
- **안정성 검증**: 파일 크기 추적 vs 시간 지연 휴리스틱  
- **프로세스 분리**: 독립적 로거/업로더 vs 통합 스풀러
- **태스크 기반**: S3ExportTaskDefinition JSON vs 직접 바이너리 전송

---

## 4. AWS 커뮤니티 카탈로그

### 3.1 AWS Greengrass Software Catalog

- **Repository**: [aws-greengrass/aws-greengrass-software-catalog](https://github.com/aws-greengrass/aws-greengrass-software-catalog)
- **목적**: 커뮤니티 개발 Greengrass 컴포넌트 인덱스
- **주요 컴포넌트들**:
  - Amazon Kinesis Video Streams (RTSP 카메라 연동)
  - Node-RED 배포/운영
  - LoRaWAN 프로토콜 지원
  - OSI PI Streaming Data Connector
  - Parsec Provider Component

### 4.2 AWS Labs 기타 컴포넌트

**파일/데이터 처리 관련**:
- [Certificate Rotator](https://github.com/awslabs/aws-greengrass-labs-certificate-rotator)
- [InfluxDB Publisher](https://github.com/awslabs/aws-greengrass-labs-telemetry-influxdbpublisher)
- [JupyterLab Component](https://github.com/awslabs/aws-greengrass-labs-jupyterlab)

**네트워크/연결 관련**:
- [Home Assistant Component](https://github.com/awslabs/aws-greengrass-labs-component-for-home-assistant)
- [The Things Stack LoRaWAN](https://github.com/awslabs/aws-greengrass-labs-component-for-the-things-stack-lorawan)
- [OpenThread Border Router](https://github.com/awslabs/aws-greengrass-labs-openthread-border-router)
- [Node-RED Docker Component](https://github.com/awslabs/aws-greengrass-labs-nodered-docker)

---

## 5. 핵심 차별점 요약

### 5.1 주요 컴포넌트 3-way 비교

| 항목 | AWS Labs S3 Uploader | CAN Blackbox Uploader | **GGC S3 Spooler** |
|------|---------------------|------------------------|---------------------|
| **파일 탐지 방식** | 주기적 폴더 스캔 (5초) | 폴링 + 안정성 검증 (5초) | 실시간 watchdog 이벤트 (~100ms) |
| **안정성 검증** | 최신 파일 제외 휴리스틱 | 파일 크기 추적 (N회 확인) | 시간 지연 (1초) |
| **프로세스 아키텍처** | 단일 프로세스 | 이중 프로세스 (로거+업로더) | 단일 통합 프로세스 |
| **업로드 방식** | 직접 바이너리 전송 | S3ExportTaskDefinition JSON | 직접 바이너리 + 청킹 |
| **라우팅 방식** | 설정 고정 (단일 스트림) | 설정 고정 (단일 버킷) | 파일명 기반 동적 라우팅 |
| **공간 관리** | 미지원 | 단순 FIFO (크기 기반) | 이중 정책 (retention + quota) |
| **도메인 특화** | 범용 파일 | CAN 데이터 특화 | 범용 파일 |
| **테스트 격리** | 기본 | Mock 클라이언트 완전 격리 | Mock Stream Manager |
| **설정 복잡도** | 낮음 (4개 파라미터) | 낮음 (INI 형식) | 중간 (YAML + CLI) |

---

## 6. 설계 인사이트

### 6.1 커뮤니티에서 학습할 패턴

#### AWS Labs 패턴
1. **파일 제외 로직**: "가장 최근 파일 제외"로 미완성 업로드 방지
2. **진행상태 추적**: S3 File Downloader의 MQTT 진행상태 퍼블리시 패턴
3. **설정 유연성**: 패턴 매칭 기반 파일 선택의 운영 편의성
4. **로깅 표준**: 표준화된 Greengrass 로그 디렉토리 사용 패턴

#### CAN Blackbox 패턴 🆕
1. **파일 크기 추적**: 연속 폴링에서 크기 불변 확인으로 확실한 완성도 보장
2. **프로세스 분리**: 로거/업로더 독립으로 장애 격리 및 개별 재시작 가능
3. **Mock 추상화**: 완전한 테스트 격리를 위한 UploadClient Protocol 패턴
4. **INI 설정**: 환경 무관, 명확한 섹션 기반 설정 구조
5. **TaskDefinition 활용**: S3ExportTaskDefinition으로 Stream Manager에 파일 처리 완전 위임

### 6.2 GGC S3 Spooler의 고유 가치

1. **Filename-as-metadata**: 별도 설정/DB 불필요한 우아한 라우팅 방식
2. **공간 인식**: 제한된 TGU 환경에서 필수적인 자동 공간 관리
3. **순서 보장**: 전역 mtime 기준 순차 처리로 데이터 순서 유지
4. **ARM 최적화**: TGU 특화 배포 파이프라인 및 의존성 관리

---

## 개정이력

| 버전 | 날짜 | 작성자 | 내용 |
|------|------|--------|------|
| 1.0 | 2026-06-01 | Claude Code | 초기 작성, AWS 커뮤니티 컴포넌트 카탈로그 정리 |
| 1.1 | 2026-06-01 | Claude Code | CAN Blackbox Directory Uploader 추가, 3-way 비교 확장 |