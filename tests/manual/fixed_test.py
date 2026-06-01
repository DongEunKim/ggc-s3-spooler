#!/usr/bin/env python3
"""
수정된 실제 환경 테스트 — SpoolWatcher process_loop 포함
"""

import asyncio
import tempfile
import time
from pathlib import Path

import sys
sys.path.insert(0, 'src')

from spooler.config import SpoolerConfig
from spooler.filename_codec import encode
from spooler.stream_client import MockStreamManagerClient
from spooler.watcher import SpoolWatcher

async def fixed_test():
    """process_loop 포함 테스트"""
    print("🔍 SpoolWatcher 수정된 동작 테스트")

    # 테스트 디렉토리 설정
    test_dir = Path(tempfile.mkdtemp(prefix="spooler_fixed_test_"))
    spool_dir = test_dir / "spool"

    print(f"📁 테스트 디렉토리: {test_dir}")

    try:
        # 설정
        config = SpoolerConfig(
            spool_dir=spool_dir,
            incomplete_file_delay=0.0,  # 테스트용: 즉시 처리
        )

        client = MockStreamManagerClient()
        client.connect()

        # Watcher 시작
        watcher = SpoolWatcher(config=config, client=client, loop=asyncio.get_event_loop())
        print("🎯 Watcher 시작...")
        watcher.start()

        # 테스트 파일들 미리 생성 (process_loop 시작 전)
        print("📄 테스트 파일 생성...")
        test_files = []

        for i in range(3):
            # 콘텐츠 준비
            content = f"Test file {i} content - timestamp: {time.time()}"

            # 임시 파일 생성
            temp_file = test_dir / f"temp_{i}.txt"
            temp_file.write_text(content)

            # 스풀 파일명으로 변환
            spool_name = encode(f"test-stream-{i}", f"data/test_{i}.txt")
            target_path = spool_dir / spool_name

            # Atomic rename
            temp_file.rename(target_path)
            test_files.append(target_path)
            print(f"  ✅ 파일 {i+1}: {spool_name}")

        # process_loop 시작 (기존 파일 처리 + 새 파일 감시)
        print("⚙️  process_loop 시작...")

        async def run_process_loop():
            try:
                await watcher.process_loop()
            except asyncio.CancelledError:
                pass

        process_task = asyncio.create_task(run_process_loop())

        # 처리 대기 (기존 파일은 _drain_existing에서 처리됨)
        print("⏳ 기존 파일 처리 대기...")
        await asyncio.sleep(1)  # drain_existing 완료 대기

        initial_count = len(client.sent)
        print(f"  기존 파일 처리 완료: {initial_count}개")

        # 새 파일 추가 (watchdog 이벤트 테스트)
        print("📄 추가 파일 생성 (watchdog 이벤트 테스트)...")
        for i in range(2):  # 2개 추가
            content = f"New file {i} - timestamp: {time.time()}"
            temp_file = test_dir / f"new_temp_{i}.txt"
            temp_file.write_text(content)

            spool_name = encode(f"new-stream-{i}", f"new/file_{i}.txt")
            target_path = spool_dir / spool_name

            temp_file.rename(target_path)
            print(f"  ✅ 새 파일 {i+1}: {spool_name}")

        # 새 파일 처리 대기
        print("⏳ 새 파일 처리 대기...")
        max_wait = 5
        waited = 0

        while waited < max_wait:
            current_count = len(client.sent)
            print(f"  총 처리된 파일: {current_count}/5 (대기: {waited:.1f}초)")

            if current_count >= 5:
                break

            await asyncio.sleep(0.5)
            waited += 0.5

        # process_loop 중단
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            pass

        # 결과 분석
        final_count = len(client.sent)
        print(f"\n📊 테스트 결과:")
        print(f"  생성된 파일: 5개 (기존 3개 + 새 파일 2개)")
        print(f"  처리된 파일: {final_count}개")

        if final_count > 0:
            print("\n✅ 처리된 파일 상세:")
            for i, (stream_name, s3_key, chunk_idx, total_chunks, data) in enumerate(client.sent):
                print(f"  {i+1}. stream={stream_name}, s3_key={s3_key}, size={len(data)} bytes")

        # 성능 측정
        if final_count >= 3:
            print(f"\n⚡ 성능:")
            print(f"  기존 파일 처리: {initial_count}개 (즉시 처리됨)")
            print(f"  전체 처리 시간: ~{waited:.1f}초")
            print(f"  처리량: {final_count/max(waited, 0.1):.1f} 파일/초")

        # 성공/실패 판정
        if final_count >= 3:  # 최소 기존 파일은 처리되어야 함
            print("\n🎉 테스트 성공! SpoolWatcher가 동작합니다.")
            success = True

            if final_count == 5:
                print("✨ 완벽! 모든 파일이 처리되었습니다.")
            else:
                print(f"⚠️  일부 파일만 처리됨 ({final_count}/5)")
        else:
            print(f"\n❌ 테스트 실패: 기본 기능도 동작하지 않음")
            success = False

        # 디버깅 정보
        remaining_files = list(spool_dir.glob("*"))
        if remaining_files:
            print(f"📋 남은 파일: {len(remaining_files)}개")
            for f in remaining_files:
                stat = f.stat()
                age = time.time() - stat.st_mtime
                print(f"  - {f.name} (생성: {age:.1f}초 전)")

        watcher.stop()
        return success

    finally:
        # 정리
        import shutil
        if test_dir.exists():
            shutil.rmtree(test_dir)
            print(f"🗑️  테스트 디렉토리 정리: {test_dir}")

if __name__ == "__main__":
    result = asyncio.run(fixed_test())
    exit(0 if result else 1)