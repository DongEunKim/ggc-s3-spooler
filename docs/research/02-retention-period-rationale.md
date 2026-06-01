# Retention Period Rationale

> 문서 목적: FR-04 파일 보존 기간(24시간) 설정 근거  
> 작성일: 2026-05-31  
> 상태: 운영 패턴 분석 기반 추정

---

## 1. 보존 기간의 의미

스풀러는 "전송 완료 전까지 파일을 보존"하는 역할을 한다.  
Stream Manager 전송 실패(네트워크 단절, 컴포넌트 재시작)가 발생했을 때  
얼마나 오래된 파일까지 재전송을 시도할지 결정하는 파라미터이다.

보존 기간이 **너무 짧으면**: 네트워크 복구 후 이미 데이터가 삭제되어 손실 발생  
보존 기간이 **너무 길면**: 스풀 용량 소진 → 신규 파일도 삭제되는 역설 발생

---

## 2. 운영 시나리오별 단절 시간 분석

| 시나리오 | 단절 시간 | 빈도 |
|----------|-----------|------|
| Greengrass 컴포넌트 재시작 | < 1분 | 잦음 (배포, 업데이트) |
| Greengrass Nucleus 재시작 | 1~5분 | 가끔 (OS 재부팅) |
| 계획된 유지보수 | 1~8시간 | 월 1~2회 |
| 네트워크 일시 단절 | 수분~수시간 | 환경 의존 |
| 장기 네트워크 장애 | 수시간~수일 | 드묾 |

**95th percentile 단절 시간**: 8시간 이내  
→ 24시간은 이를 3배 커버하는 안전 마진

---

## 3. 스풀 용량과의 충돌 분석

보존 기간 동안 쌓일 수 있는 최대 데이터 크기:

| 파일 생성 속도 | 파일 크기 | 24시간 누적 | 스풀 한도 (400MB) 대비 |
|--------------|----------|------------|----------------------|
| 1 파일/초 | 1 KB | 84 MB | 21% — **안전** |
| 1 파일/분 | 100 KB | 144 MB | 36% — **안전** |
| 1 파일/분 | 1 MB | 1,440 MB | **360% — 용량 초과** |
| 1 파일/10분 | 1 MB | 144 MB | 36% — **안전** |
| 1 파일/시 | 10 MB | 240 MB | 60% — 주의 필요 |

**결론**: 1MB 이상 파일을 분당 1개 이상 생성하는 경우 24시간 보존은 용량 한도를 초과한다.  
이 경우 FR-05의 용량 초과 삭제 정책이 먼저 발동하여 보존 기간보다 일찍 파일이 삭제된다.  
즉, 두 정책은 **선택이 아니라 상호 보완 관계**이며, 먼저 발동하는 조건이 우선한다.

---

## 4. 결론

- **기본값 24시간**: 대부분의 계획된 유지보수 시나리오를 커버
- 중요 데이터 장기 보존이 필요한 경우: `file_retention_hours` 를 72~168(7일)로 증가
- 용량 한도를 함께 증가시켜야 함 (FR-05 연계)
- 보존 기간은 **스풀 용량 한도와 함께** 설계되어야 하며, 두 파라미터는 독립적이지 않다

### 파라미터 조합 권장

| 용도 | `file_retention_hours` | `max_spool_size_mb` |
|------|----------------------|---------------------|
| 소형 파일 (< 100KB), 고빈도 | 24 | 256 |
| 중형 파일 (100KB~1MB), 저빈도 | 24 | 400 |
| 대형 파일 (1MB~64MB), 저빈도 | 8 | 400 |
| 중요 데이터 장기 보존 | 72 | 400 |

---

## 5. 검증 방법

```bash
# 보존 기간 테스트: mtime을 과거로 설정하여 삭제 확인
python3 -c "
import time, os, pathlib
from spooler.filename_codec import encode
from spooler.config import SpoolerConfig
from spooler.cleaner import SpoolCleaner

spool = pathlib.Path('/tmp/test-spool')
spool.mkdir(exist_ok=True)
cfg = SpoolerConfig(spool_dir=spool, file_retention_hours=1)

# 2시간 전 파일 생성
name = encode('s', 'test/old.txt')
p = spool / name
p.write_bytes(b'old data')
old_time = time.time() - 7200  # 2시간 전
os.utime(p, (old_time, old_time))

deleted = SpoolCleaner(cfg).run_once()
print(f'삭제된 파일 수: {deleted}')  # 1 기대
assert deleted == 1
print('PASS')
"
```
