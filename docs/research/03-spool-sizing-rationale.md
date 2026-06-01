# Spool Sizing Rationale

> 문서 목적: FR-05 스풀 용량 한도 설정 근거  
> 작성일: 2026-05-31  
> 개정일: 2026-05-31 (v1.1 — 전용 파티션 전제로 재계산)  
> 전제 조건: **스풀러 전용 파티션 1 GB** (OS, Greengrass, Stream Manager와 별도)

---

## 개정이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| 1.0 | 2026-05-31 | 최초 작성 (1GB를 OS와 공유 가정, 400MB 도출) |
| 1.1 | 2026-05-31 | 전용 파티션으로 전제 수정, 재계산 (900MB 도출) |

---

## 1. 전제 조건 명확화

### v1.0 전제 (폐기)
> "1GB 전체 저장공간을 OS/Greengrass/Stream Manager와 공유"

### v1.1 전제 (현행)
> "1GB = **스풀러만을 위해 준비된 전용 빈 파티션**"
> OS, Greengrass Nucleus, Stream Manager, Python 런타임은 **별도 파티션에 설치됨**

이 전제가 바뀜에 따라 용량 계산이 전면 달라진다.

---

## 2. 전용 파티션 1GB 분배 계산

| 항목 | 크기 | 설명 |
|------|------|------|
| 파일시스템 메타데이터 | ~20 MB | ext4 기준 inode 테이블, 저널 등 |
| 안전 여유 (5%) | ~50 MB | 파티션 100% 도달 시 OS 파일 쓰기 실패 방지 |
| S3 Spooler 아티팩트 + deps | ~5 MB | zip 해제 후 설치 크기 (watchdog + stream-manager) |
| **스풀 데이터 가용 공간** | **~949 MB** | |

```
1,024 MB (파티션)
  - 20 MB  (파일시스템 오버헤드)
  - 50 MB  (안전 여유 5%)
  - 5 MB   (컴포넌트 아티팩트)
──────────────────────────
  ≈ 949 MB 가용
  → 권장 한도: 900 MB (추가 여유 ~50MB 확보)
```

**결론**: `max_spool_size_mb = 900` (v1.0의 400MB에서 상향)

---

## 3. Stream Manager 최대 크기 — 질문 분석

> "스트림 매니저의 최대 크기를 늘릴 수 없나?"

Stream Manager에는 **두 가지 크기 한도**가 존재하며 혼동하기 쉽다:

### 3.1 스트림 버퍼 크기 (max_size) — 변경 가능 ✅

스트림 생성 시 `MessageStreamDefinition.max_size` 로 설정.  
Stream Manager가 S3에 아직 올리지 못한 메시지를 임시 저장하는 로컬 버퍼 크기.

| 항목 | 내용 |
|------|------|
| 기본값 | 256 MB |
| 최대값 | 실질적 제한 없음 (파티션 여유 공간 이내) |
| 저장 위치 | Greengrass 설치 경로 (`/greengrass/v2/`) — **스풀 파티션과 별개** |
| 변경 방법 | 스트림 생성 시 `max_size=1_073_741_824` (1GB 예시) |

```python
client.create_message_stream(
    MessageStreamDefinition(
        name="my-stream",
        max_size=1_073_741_824,  # 1 GB (기본 256MB에서 증가)
        ...
    )
)
```

**영향**: 스풀 파티션에는 전혀 영향 없음. Greengrass 파티션 여유 공간 확인 필요.

### 3.2 단일 메시지 크기 (append_message 1회) — 변경 불가 ❌

`append_message(stream_name, data)` 에서 `data` 바이트의 최대 크기.

| 항목 | 내용 |
|------|------|
| 한도 | **64 MB** (하드코딩, 변경 불가) |
| 근거 | Stream Manager 내부 프로토콜 구현 고정값 |
| 우회 방법 | 청크 분할 전송 또는 `S3ExportTaskDefinition` 사용 (§4 참고) |

---

## 4. 64MB 초과 파일 처리 — Pattern 분석

Stream Manager Python SDK는 두 가지 전송 패턴을 지원한다:

### Pattern 1: 바이트 직접 전송 (현재 구현)

```python
# 파일 내용을 바이트로 읽어서 전송
data = file_path.read_bytes()  # 메모리에 적재
client.append_message(stream_name, data)  # 64MB 한도
```

- **S3 키 제어**: ❌ 불가 — Stream Manager가 자동 생성 (`{key_prefix}{seq_num}`)
- **파일 크기 한도**: 64MB
- **구현 복잡도**: 낮음 (현재 코드)

### Pattern 2: S3ExportTaskDefinition (향후 개선 A-07)

```python
from stream_manager.data import S3ExportTaskDefinition
import cbor2

task = S3ExportTaskDefinition(
    input_url=f"file://{file_path}",   # 로컬 파일 경로
    bucket="my-s3-bucket",             # S3 버킷 직접 지정
    key="data/device/file.bin"         # S3 키 직접 지정
)
# 73바이트의 태스크 정의만 전송 (파일 내용 아님)
client.append_message(stream_name, cbor2.dumps(task.as_dict()))
# → Stream Manager가 파일을 직접 읽어 S3에 업로드 (메모리 제한 없음)
```

- **S3 키 제어**: ✅ 완전한 per-file 키 제어
- **파일 크기 한도**: 사실상 없음 (Stream Manager가 멀티파트 업로드 처리)
- **구현 복잡도**: 높음 — 파일 라이프사이클 관리 필요
  - Stream Manager는 업로드 완료 후 소스 파일을 자동 삭제하지 않음
  - 언제 스풀 파일을 삭제할지 별도 추적 메커니즘 필요

> **현재 선택 (Phase 1)**: Pattern 1 + 청크 분할로 64MB 한도 우회  
> **향후 (A-07)**: Pattern 2로 마이그레이션 — per-file S3 키 제어 + 무제한 파일 크기

---

## 5. 청크 분할 전송 — 설계 결정

> "스트림 매니저의 최대 크기를 초과해서 생성된 파일의 처리방안"
> → 사용자 결정: **청크 분할 전송**

### 5.1 청크 크기

| 항목 | 값 | 근거 |
|------|-----|------|
| 최대 청크 크기 | **63 MB** = 66,060,288 bytes | 64MB 한도에서 1MB 여유 (SDK 프레이밍 오버헤드 감안) |

### 5.2 S3 오브젝트 명명 — 현재 제한

Pattern 1에서는 S3 키를 직접 제어할 수 없다.  
청크 파일은 스트림의 `key_prefix` + 자동 생성 시퀀스 번호로 S3에 저장된다.

**청크 식별**: 수신측은 Stream Manager 시퀀스 번호로 청크 순서를 파악.  
또는: 다운스트림에서 `s3_key` 기반 재조립 로직 구현 필요.

### 5.3 메모리 사용

청크 분할 시 한 번에 최대 63MB가 메모리에 적재된다.  
TGU 메모리가 충분하지 않은 경우 Pattern 2(스트리밍)로 전환 필요.

---

## 6. 업데이트된 사양

| 파라미터 | 구버전 (v1.0) | 신버전 (v1.1) | 변경 이유 |
|----------|--------------|--------------|-----------|
| `max_spool_size_mb` | 400 | **900** | 전용 파티션으로 전제 수정 |
| Stream Manager `max_size` | 256MB (기본) | 필요 시 증가 가능 | 스풀 파티션과 독립 |
| 단일 파일 최대 크기 | 64MB | 청크 분할로 우회 | FR-07 추가 |

---

## 7. 디바이스 실측 가이드

```bash
# 전용 스풀 파티션 마운트포인트 확인
df -h /var/spool  # 또는 실제 마운트 경로

# 가용 공간의 90%를 스풀 한도로 설정
FREE_MB=$(df --output=avail -m /var/spool | tail -1)
SAFE_SPOOL_MB=$((FREE_MB * 90 / 100))
echo "권장 max_spool_size_mb: ${SAFE_SPOOL_MB}"

# Greengrass 파티션 확인 (Stream Manager 버퍼 위치)
df -h /greengrass/v2
du -sh /greengrass/v2/work/aws.greengrass.StreamManager/  2>/dev/null || echo "경로 확인 필요"
```
