# GGC S3 Spooler

AWS Greengrass Component로 동작하는 S3 파일 스풀러.  
다른 프로세스가 지정된 스풀 디렉토리에 파일을 저장하면, Greengrass Stream Manager를 통해 S3 버킷으로 전송하고 완료 후 삭제한다.

## 주요 기능

| 기능 | 설명 |
|------|------|
| 파일 감시 | `watchdog` 기반 실시간 파일 생성 감지 |
| 파일명 라우팅 | 파일명 인코딩으로 스트림·S3 경로 구분 |
| **하이브리드 안정성 검증** | **시간+크기 기반으로 파일 쓰기 완성도 확인** |
| **INI 설정 지원** | **CAN Blackbox 스타일 섹션별 설정 파일** |
| 자동 정리 | 보존 기간 초과 및 용량 초과 파일 자동 삭제 |
| 멀티 클라이언트 | 여러 프로세스의 동시 요청 지원 |
| Greengrass 통합 | Stream Manager S3 Export 스트림 연동 |

## 빠른 시작

```bash
# 가상환경 생성 및 개발 의존성 설치
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 테스트 실행
pytest tests/unit/ -v
```

## 파일명 인코딩 규칙

클라이언트 프로세스는 아래 형식으로 파일명을 지정하여 스풀 디렉토리에 저장한다:

```
{stream_id}__{s3_key_base64url}__{original_filename}
```

Python 유틸리티 사용 예:
```python
from spooler.filename_codec import encode

spool_name = encode("telemetry-stream", "data/device-1/reading.json")
# → "telemetry-stream__ZGF0YSFkZXZpY2UtMSFyZWFkaW5nLmpzb24"
```

## INI 설정 사용법

CAN Blackbox 스타일 섹션별 설정으로 운영 환경에 맞는 튜닝이 가능하다:

```ini
# spooler.ini
[spooler]
spool_dir = /custom/spool/path
log_level = DEBUG

[stability]
file_stability_wait = 0.2
max_stability_wait = 30.0

[cleanup]
max_spool_size_mb = 1500
file_retention_hours = 48
```

```bash
# INI 설정 파일 지정하여 실행
python -m spooler --config spooler.ini

# CLI로 개별 설정 오버라이드
python -m spooler --config spooler.ini --log-level INFO --spool-dir /tmp/test
```

## 문서

사양 문서는 번호 순서대로 읽는다.

| 번호 | 문서 | 대상 독자 |
|------|------|-----------|
| 01 | [기능정의서](docs/01-기능정의서.md) | 전체 |
| 02 | [시스템사양서](docs/02-시스템사양서.md) | 개발자, 운영자 |
| 03 | [아키텍처설계서](docs/03-아키텍처설계서.md) | 개발자 |
| 04 | [인터페이스설계서](docs/04-인터페이스설계서.md) | 클라이언트 개발자, 개발자 |
| 05 | [상세설계사양서](docs/05-상세설계사양서.md) | 개발자 |
| 06 | [배포가이드](docs/06-배포가이드.md) | 운영자, DevOps |
| 07 | [클라이언트 통합 가이드](docs/07-클라이언트-통합-가이드.md) | 클라이언트 프로세스 개발자 |
| 08 | [S3·스트림 관리 가이드](docs/08-S3-스트림-관리-가이드.md) | S3 관리자, 스트림 설정 담당자 |

## 라이선스

내부 사용 전용
