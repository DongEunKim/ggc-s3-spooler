"""
테스트/벤치마크 전용 패키지 — 운영 배포 대상이 아니다.

이 패키지는 단위·통합·성능 시험에서만 사용하는 Mock 클라이언트와
성능 메트릭 수집기를 포함한다. 운영 패키지(`spooler`)와 물리적으로 분리되어
빌드 아티팩트(`scripts/build_artifact.py`는 `src/spooler/`만 번들링)와
wheel(`pyproject.toml`의 `packages = ["src/spooler"]`)에서 자동 제외된다.

⚠️  운영 코드(`src/spooler/`)에서 이 패키지를 import 하지 말 것.
    psutil 등 TGU에 미탑재된 의존성을 가지므로 실디바이스 기동을 깨뜨린다.
"""
