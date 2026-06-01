# TGU Platform Constraints & Verification Guide

> 문서 목적: TGU 운영 환경 제약 근거, 플랫폼 정보 확인 절차, 빌드 플랫폼 선택 근거  
> 작성일: 2026-05-31  
> 상태: **미확인 항목 존재 — 확인 절차 수행 필요**

---

## 1. 운영 제약 배경

| 제약 | 근거 | 영향 |
|------|------|------|
| PyPI 접근 불가 | 사이버보안 정책 (건설기계 탑재 장비) | `pip install` 온라인 불가 |
| stream-manager SDK 미탑재 | 기존 TGU 출하 시 포함 안 됨 | 번들 배포 필수 |
| ARM 아키텍처 | 건설기계 TGU 일반적 CPU | 크로스 플랫폼 wheel 필요 |
| Python 버전 미확정 | 운영 중 TGU 다수 운용 중 | cbor2 C 확장 ABI 의존성 |

---

## 2. 플랫폼 의존성 분석

번들 zip에 포함되는 wheel 파일의 플랫폼 의존성:

| 패키지 | wheel 타입 | 플랫폼 의존 | 비고 |
|--------|-----------|------------|------|
| `stream-manager` | `py2.py3-none-any` | **없음** | 어떤 Python·OS에서도 동작 |
| `watchdog` (Linux) | `py3-none-manylinux2014_aarch64` | OS=Linux만 | Pure Python, glibc 무관 |
| `cbor2 5.x` | `cp311-cp311-manylinux_2_17_aarch64` | **Python 3.11 + glibc≥2.17** | C 확장, ABI 민감 |

### cbor2가 핵심 위험 요소

`cbor2` 5.x 버전은 C 확장을 제공하며 Python 버전(ABI)에 강하게 의존한다:
- `cp311` wheel = Python 3.11 전용
- TGU가 Python 3.9를 사용한다면 `cp39` wheel을 다운로드해야 함
- Python 버전이 다르면 `pip install --no-index` 시 "No matching distribution" 오류 발생

**해결 방안**:  
cbor2 순수 Python 버전(5.4.6)은 `py3-none-any` 형태로도 존재한다.  
build_artifact.py에서 `cbor2>=5.4.6,<6.0` 버전이면서 `--no-binary cbor2`로 소스를 취하면  
Python 버전 독립적으로 설치 가능하다. (단, 빌드 도구 필요)

또는: `cbor2<5.5`를 지정하면 항상 pure-Python 버전을 받을 수 있다 (확인 필요).

---

## 3. 확인이 필요한 TGU 사양

### 3.1 확인 스크립트 (TGU에서 실행)

아래 스크립트를 TGU에서 실행하여 결과를 개발팀에 공유한다:

```bash
#!/bin/bash
# tgu_platform_check.sh
# TGU에서 실행하여 플랫폼 정보를 수집한다.

echo "=== TGU 플랫폼 정보 ==="
echo ""
echo "1. CPU 아키텍처:"
uname -m
echo ""
echo "2. OS 정보:"
cat /etc/os-release 2>/dev/null || uname -a
echo ""
echo "3. Python 버전:"
python3 --version 2>/dev/null || python --version 2>/dev/null
echo ""
echo "4. Python 경로:"
which python3 2>/dev/null || which python 2>/dev/null
echo ""
echo "5. pip 버전:"
pip3 --version 2>/dev/null || pip --version 2>/dev/null
echo ""
echo "6. glibc 버전:"
ldd --version 2>/dev/null | head -1
echo ""
echo "7. 저장공간:"
df -h / | tail -1
echo ""
echo "8. Greengrass 설치 확인:"
ls /greengrass/v2/bin/greengrass-cli 2>/dev/null && echo "설치됨" || echo "없음"
echo ""
echo "9. 기존 Python 패키지:"
pip3 list 2>/dev/null | grep -E "watchdog|stream.manager|cbor2|pydantic|greengrass" || echo "(없음)"
echo ""
echo "=== 확인 완료 ==="
```

### 3.2 확인 결과 해석

| 확인 결과 | 빌드 명령 |
|-----------|-----------|
| `uname -m` = `aarch64` + Python 3.11 | `make build` (기본) |
| `uname -m` = `armv7l` + Python 3.11 | `make build-armv7l` |
| `uname -m` = `aarch64` + Python 3.9 | Python 버전 지정 필요 (아래 참고) |

### 3.3 Python 버전이 3.11이 아닌 경우 대응

`build_artifact.py`의 `PLATFORM_PRESETS`에 해당 버전 프리셋 추가 필요:

```python
# 예시: Python 3.9 ARM64
PLATFORM_PRESETS["tgu-arm64-py39"] = ("manylinux2014_aarch64", "cp39")
```

그리고 `download_deps`에서 `--python-version` 인수도 변경:
```python
cmd += ["--platform", plat, "--abi", abi, "--python-version", "39", ...]
```

---

## 4. cbor2 순수 Python 대안 (Python 버전 독립 방안)

cbor2의 C 확장 의존성을 완전히 제거하는 방법:

```bash
# 순수 Python cbor2 5.4.6 다운로드
pip download --no-binary cbor2 "cbor2>=5.4.6,<5.5" --dest deps/
```

이렇게 하면 `cbor2-5.4.6.tar.gz` (소스)가 다운로드된다.  
단, TGU에서 소스 빌드가 가능해야 한다 (gcc, Python-dev 헤더 필요).

**더 간단한 방법**: cbor2 5.4.6의 pure-Python 버전은 PyPI에 별도 존재한다.  
`pip download cbor2==5.4.6 --platform any --python-version 3` 시도 필요 (확인 필요).

---

## 5. 현재 빌드 기본값 근거

기본 플랫폼 프리셋 `tgu-arm64`의 근거:
- 현대 ARM64(aarch64)는 건설기계 TGU에서 일반적인 아키텍처
- Python 3.11은 Greengrass v2 권장 버전
- `manylinux2014` = glibc ≥ 2.17 요구, 2013년 이후 Linux 배포판에서 지원

---

## 6. 액션 아이템

| 항목 | 담당 | 상태 |
|------|------|------|
| TGU에서 platform_check.sh 실행 및 결과 공유 | 운영팀/하드웨어팀 | ⚠️ **미완료** |
| Python 버전 확인 후 cbor2 wheel 재빌드 (필요 시) | 개발팀 | 대기 중 |
| glibc 버전 확인 (manylinux2014 호환성) | 운영팀 | 대기 중 |
| pip3 동작 확인 (버전, `--no-index` 지원 여부) | 운영팀 | ⚠️ **미완료** |
