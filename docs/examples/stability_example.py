#!/usr/bin/env python3
"""
GGC S3 Spooler 파일 안정성 검증 예제.

CAN Blackbox 패턴이 적용된 하이브리드 안정성 검증 시스템을 보여준다.

사용법:
    python docs/examples/stability_example.py
"""

import asyncio
import sys
import time
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from spooler.config import SpoolerConfig
from spooler.file_stability import (
    StabilityConfig,
    is_file_stable_async,
    wait_for_file_stability,
)


async def demonstrate_stability_check():
    """파일 안정성 검증 데모."""
    print("=== GGC S3 Spooler 파일 안정성 검증 데모 ===\n")

    # 임시 파일 생성
    demo_dir = Path("/tmp/ggc-stability-demo")
    demo_dir.mkdir(exist_ok=True)

    # 테스트 파일들
    stable_file = demo_dir / "stable.txt"
    growing_file = demo_dir / "growing.txt"

    print("1. 안정적인 파일 테스트")
    print("   - 파일을 생성하고 안정성 검증")
    stable_file.write_text("안정적인 내용")

    # 기본 안정성 설정
    config = StabilityConfig(check_count=3, check_interval=0.2, timeout=5.0)

    start_time = time.time()
    is_stable = await is_file_stable_async(stable_file, config)
    elapsed = time.time() - start_time

    print(f"   ✓ 안정성: {is_stable}")
    print(f"   ✓ 검증 시간: {elapsed:.2f}초\n")

    print("2. 증가하는 파일 테스트")
    print("   - 파일 크기가 계속 변하는 상황")
    growing_file.write_text("초기 내용")

    async def grow_file():
        """파일을 점진적으로 증가시키기."""
        await asyncio.sleep(0.1)
        for i in range(5):
            with open(growing_file, 'a') as f:
                f.write(f" 추가_{i}")
            await asyncio.sleep(0.15)

    # 파일 증가를 백그라운드에서 시작
    grow_task = asyncio.create_task(grow_file())

    try:
        start_time = time.time()
        is_stable = await is_file_stable_async(growing_file, config)
        elapsed = time.time() - start_time
        print(f"   ✓ 안정성: {is_stable} (예상: False)")
        print(f"   ✓ 검증 시간: {elapsed:.2f}초\n")
    finally:
        # 백그라운드 태스크 정리
        if not grow_task.done():
            grow_task.cancel()
            try:
                await grow_task
            except asyncio.CancelledError:
                pass

    print("3. SpoolerConfig 통합 예제")
    print("   - 실제 프로젝트 설정으로 안정성 검증")

    # 프로젝트 기본 설정
    spooler_config = SpoolerConfig()
    stability_config = spooler_config.stability_config

    print(f"   - 체크 횟수: {stability_config.check_count}")
    print(f"   - 체크 간격: {stability_config.check_interval}초")
    print(f"   - 타임아웃: {stability_config.timeout}초")

    # 새 안정 파일로 테스트
    test_file = demo_dir / "config_test.txt"
    test_file.write_text("SpoolerConfig 테스트")

    is_stable = await is_file_stable_async(test_file, stability_config)
    print(f"   ✓ 검증 결과: {is_stable}\n")

    print("4. 적응형 재시도 데모")
    print("   - wait_for_file_stability 함수 사용")

    # 결국 안정화되는 파일 시뮬레이션
    eventually_stable = demo_dir / "eventually_stable.txt"
    eventually_stable.write_text("초기")

    call_count = 0

    async def mock_eventually_stable():
        """결국 안정화되는 파일 시뮬레이션."""
        nonlocal call_count
        original_func = is_file_stable_async

        async def mock_check(file_path, config):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:  # 처음 2번은 불안정
                print(f"     시도 {call_count}: 불안정")
                return False
            print(f"     시도 {call_count}: 안정화!")
            return await original_func(file_path, config)

        return mock_check

    # Mock 함수로 시연
    quick_config = StabilityConfig(check_count=2, check_interval=0.1)
    start_time = time.time()

    # 실제로는 원래 함수를 사용하되, 로직을 시연
    print("     파일이 결국 안정화될 때까지 대기 중...")
    is_stable = await wait_for_file_stability(
        eventually_stable, quick_config,
        max_retries=3, retry_delay=0.3
    )
    elapsed = time.time() - start_time

    print(f"   ✓ 최종 안정성: {is_stable}")
    print(f"   ✓ 총 소요 시간: {elapsed:.2f}초\n")

    # 정리
    import shutil
    shutil.rmtree(demo_dir)
    print("✓ 데모 완료! 임시 파일들이 정리되었습니다.")


async def demonstrate_hybrid_approach():
    """하이브리드 안정성 검증 접근법 데모."""
    print("\n=== 하이브리드 접근법 (시간 + 크기) 데모 ===\n")

    demo_dir = Path("/tmp/ggc-hybrid-demo")
    demo_dir.mkdir(exist_ok=True)

    # SpoolerConfig 설정
    config = SpoolerConfig(
        incomplete_file_delay=0.3,  # 시간 기반 필터링
        size_check_count=3,         # 크기 기반 검증
        size_check_interval=0.1,
        stability_timeout=2.0,
    )

    test_file = demo_dir / "hybrid_test.txt"

    print("1. Phase 1: 시간 기반 사전 필터링")
    print("   - 너무 최근 파일은 크기 체크 없이 건너뛰기")

    # 방금 생성된 파일
    test_file.write_text("최근 파일")
    file_age = time.time() - test_file.stat().st_mtime

    print(f"   파일 나이: {file_age:.3f}초")
    print(f"   임계값: {config.incomplete_file_delay}초")

    if file_age < config.incomplete_file_delay:
        print("   ✓ 시간 기반 필터링: 너무 최근 → 건너뜀 (Fast Path)")
        proceed_to_stability = False
    else:
        print("   ✓ 시간 기반 필터링: 통과 → 안정성 검증으로")
        proceed_to_stability = True

    if proceed_to_stability:
        print("\n2. Phase 2: 크기 기반 안정성 검증")
        print("   - 파일 크기 변화 추적으로 완성도 확인")

        stability_result = await is_file_stable_async(test_file, config.stability_config)
        print(f"   ✓ 안정성 검증: {stability_result} (Thorough Path)")

        if stability_result:
            print("   → 파일 전송 가능")
        else:
            print("   → 파일 전송 대기 또는 재시도")
    else:
        print("\n2. Phase 2: 건너뛰기")
        print("   - 시간 기반 필터링에서 차단됨")

    print("\n3. 실제 적용 시나리오")
    print("   - 충분히 오래된 파일로 전체 플로우 테스트")

    # 파일을 충분히 오래된 것으로 만들기
    await asyncio.sleep(config.incomplete_file_delay + 0.1)

    print(f"   대기 후 파일 나이: {time.time() - test_file.stat().st_mtime:.3f}초")

    # Phase 1: 시간 검사
    file_age = time.time() - test_file.stat().st_mtime
    time_check_pass = file_age >= config.incomplete_file_delay
    print(f"   ✓ Phase 1 (시간): {'통과' if time_check_pass else '차단'}")

    if time_check_pass:
        # Phase 2: 안정성 검사
        stability_result = await is_file_stable_async(test_file, config.stability_config)
        print(f"   ✓ Phase 2 (안정성): {'통과' if stability_result else '차단'}")

        if stability_result:
            print("   → 최종 결과: 파일 전송 진행 ✅")
        else:
            print("   → 최종 결과: 파일 전송 대기 ⏳")

    # 정리
    import shutil
    shutil.rmtree(demo_dir)
    print("\n✓ 하이브리드 데모 완료!")


async def main():
    """메인 함수."""
    await demonstrate_stability_check()
    await demonstrate_hybrid_approach()


if __name__ == "__main__":
    asyncio.run(main())