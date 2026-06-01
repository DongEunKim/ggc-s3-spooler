# Manual Test Scripts

이 디렉토리는 수동 테스트 및 개발 중 검증 목적으로 작성된 스크립트들을 포함합니다.

## 파일 목록

### benchmark_high_load.py
- **목적**: 고부하 상황에서의 성능 테스트
- **기능**: 
  - 다중 파일 동시 생성/처리 (1000개 파일)
  - 대용량 파일 청크 분할 테스트 (100MB+)
  - 메모리 사용량 모니터링
  - 처리량 및 지연시간 측정

### debug_test.py
- **목적**: SpoolWatcher의 incomplete_file_delay 문제 디버깅
- **기능**: 파일 처리 지연 시간 관련 디버깅 및 검증

### fixed_test.py
- **목적**: 수정된 실제 환경에서의 SpoolWatcher 테스트
- **기능**: process_loop 포함한 전체 워크플로우 검증

### simple_test.py
- **목적**: SpoolWatcher 기본 동작 검증
- **기능**: 간단한 실제 환경 테스트 시나리오

## 사용법

각 스크립트는 독립적으로 실행 가능하며, 프로젝트 루트에서 다음과 같이 실행합니다:

```bash
# 가상환경 활성화
source .venv/bin/activate

# 스크립트 실행
python tests/manual/simple_test.py
```

## 주의사항

- 이 스크립트들은 개발/디버깅 목적으로 작성되었으며 자동화된 테스트 스위트에 포함되지 않습니다
- 실행 전 임시 디렉토리 및 테스트 환경 설정이 필요할 수 있습니다
- Stream Manager 모킹이 활성화된 상태에서 실행해야 합니다