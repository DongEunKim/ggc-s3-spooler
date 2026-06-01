# Latency Requirements Rationale

> 문서 목적: FR-01 검증 기준과 NFR-02 성능 목표의 정량적 근거  
> 작성일: 2026-05-31  
> 상태: 분석 기반 추정 (실디바이스 벤치마크로 검증 필요)

---

## 1. 문제 제기

최초 기능정의서에서 **FR-01**은 "30초 이내 전달" 을 검증 기준으로 명시하였으나,  
**NFR-02**는 "파일 생성 후 전송 시작까지 1초 이내" 를 요구한다.  
이 두 수치가 같은 파이프라인을 서로 다른 기준으로 표현하고 있어 일관성이 없으므로,  
각 단계의 레이턴시를 분해하여 합리적인 기준을 도출한다.

---

## 2. 단계별 레이턴시 분해

```
[클라이언트 파일 생성]
        │
        ▼ (A) watchdog 감지 레이턴시
[asyncio Queue push]
        │
        ▼ (B) 큐 처리 대기
[_transfer() 호출]
        │
        ▼ (C) run_in_executor 제출
[ThreadPoolExecutor 실행]
        │
        ▼ (D) Stream Manager SDK append_message()
[Stream Manager 수신 확인]
        │
        ▼ (E) 파일 삭제 (unlink)
[완료]
```

### 단계별 측정 기준

| 단계 | 메커니즘 | 예상 레이턴시 | 근거 |
|------|----------|--------------|------|
| (A) watchdog 감지 | Linux inotify 커널 이벤트 | **10~50 ms** | inotify 커널 이벤트 latency (ARM Linux 기준) |
| (B) asyncio 큐 처리 | `call_soon_threadsafe` + `Queue.get()` | **< 1 ms** | CPython event loop tick ~0.1ms |
| (C) executor 제출 | `run_in_executor(None, ...)` | **< 1 ms** | Thread pool submit overhead |
| (D-1) localhost TCP | localhost:8088 connect | **< 1 ms** | loopback 네트워크 RTT |
| (D-2) SDK 직렬화 | protobuf 직렬화 + send | 파일 크기 의존 | 아래 표 참조 |
| (E) unlink | OS 파일 삭제 syscall | **< 1 ms** | tmpfs/ext4 기준 |

### 파일 크기별 SDK 전송 시간 추정 (D-2)

loopback 환경에서 socket write 속도를 약 10 GB/s 로 가정 (실제 IPC 소켓 기준).

| 파일 크기 | 전송 시간 | 합계 (A+B+C+D+E) |
|----------|----------|-----------------|
| 1 KB | < 1 ms | **~60 ms** |
| 100 KB | ~0.01 ms | **~70 ms** |
| 1 MB | ~0.1 ms | **~80 ms** |
| 10 MB | ~1 ms | **~100 ms** |
| 64 MB | ~7 ms | **~120 ms** |

> **주의**: 위 수치는 이상적인 loopback 조건 기준이다.  
> 실제 Core Device (ARM Cortex-A 계열)에서는 CPU 제약으로 **2~5배 이상** 늘어날 수 있다.

---

## 3. 결론 및 수정 기준

### NFR-02 (성능 목표) — 변경 없음

"파일 생성 후 전송 시작까지 **1초 이내**"

분석 결과: 단계 (A)~(C)의 합이 50~100ms 이므로 달성 가능.  
단, 큐에 여전히 처리 중인 파일이 있을 경우 대기 시간이 추가될 수 있다.  
(→ 병렬 처리 의사결정 참고: [05-processing-model-rationale.md](05-processing-model-rationale.md))

### FR-01 검증 기준 — 수정

| 구분 | 기존 | 수정 |
|------|------|------|
| 소형 파일 (≤ 1 MB) | 30초 이내 | **1초 이내** |
| 대형 파일 (≤ 64 MB) | 30초 이내 | **10초 이내** |

"30초"는 Stream Manager → S3 업로드까지의 전체 경로를 포함한 수치로 추정되나,  
이 컴포넌트의 책임 범위는 "Stream Manager에 메시지 append 완료"까지이므로 위 기준으로 수정한다.

---

## 4. 검증 계획 (실디바이스)

아래 스크립트로 실 Core Device에서 측정:

```python
import time
import pathlib
from spooler.filename_codec import encode

SPOOL = pathlib.Path("/var/spool/s3-spooler")
SIZES = [1024, 100*1024, 1024*1024, 10*1024*1024]

for size in SIZES:
    data = b"x" * size
    name = encode("bench-stream", f"bench/{size}.bin")
    t0 = time.perf_counter()
    (SPOOL / name).write_bytes(data)
    # 파일이 사라질 때까지 대기 (= spooler가 처리 완료)
    while (SPOOL / name).exists():
        pass
    elapsed = time.perf_counter() - t0
    print(f"{size//1024:>8} KB: {elapsed*1000:.1f} ms")
```

> 이 문서는 실측값으로 업데이트되어야 한다. 현재 수치는 설계 기준값이다.
