#!/usr/bin/env python3
"""
SpoolWatcher 디버그 테스트 — incomplete_file_delay 문제 확인
"""

import asyncio
import logging
import tempfile
import time
from pathlib import Path

import sys
sys.path.insert(0, 'src')

from spooler.config import SpoolerConfig
from spooler.filename_codec import encode
from spooler.stream_client import MockStreamManagerClient
from spooler.watcher import SpoolWatcher

# 디버그 로깅 활성화
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)8s] %(name)s — %(message)s"
)

async def debug_test():
    """incomplete_file_delay 문제 디버깅"""
    print("🔍 SpoolWatcher incomplete_file_delay 디버깅")

    # 테스트 디렉토리 설정
    test_dir = Path(tempfile.mkdtemp(prefix="spooler_debug_test_"))
    spool_dir = test_dir / "spool"

    print(f"📁 테스트 디렉토리: {test_dir}")

    try:
        # 설정 - 매우 짧은 delay
        config = SpoolerConfig(
            spool_dir=spool_dir,
            incomplete_file_delay=0.1,  # 0.1초
        )

        client = MockStreamManagerClient()
        client.connect()

        # Watcher 시작
        watcher = SpoolWatcher(config=config, client=client, loop=asyncio.get_event_loop())
        print("🎯 Watcher 시작...")
        watcher.start()

        # 테스트 파일 1개 생성
        print("📄 테스트 파일 생성...")
        content = f"Test content - timestamp: {time.time()}"

        # 임시 파일 생성
        temp_file = test_dir / "temp.txt"
        temp_file.write_text(content)

        # 스풀 파일명으로 변환
        spool_name = encode("debug-stream", "data/debug.txt")
        target_path = spool_dir / spool_name

        print(f"  파일명: {spool_name}")
        file_creation_time = time.time()

        # Atomic rename
        temp_file.rename(target_path)
        print(f"  ✅ 파일 생성 완료 (시간: {file_creation_time:.3f})")

        # 즉시 _drain_existing 호출 (process_loop 시작 전 테스트)
        print("⚙️  _drain_existing 즉시 호출...")
        drain_start_time = time.time()
        await watcher._drain_existing()
        drain_end_time = time.time()

        processed_immediately = len(client.sent)
        print(f"  즉시 처리 결과: {processed_immediately}개")
        print(f"  drain_existing 소요시간: {drain_end_time - drain_start_time:.3f}초")

        if processed_immediately == 0:
            file_age = drain_start_time - file_creation_time
            print(f"  🐛 파일이 처리되지 않음! 파일 age: {file_age:.3f}초")
            print(f"  🐛 incomplete_file_delay: {config.incomplete_file_delay}초")

            # 추가 대기 후 재시도
            print("⏳ delay 대기 후 재시도...")
            await asyncio.sleep(0.15)  # incomplete_file_delay보다 길게 대기

            retry_start_time = time.time()
            await watcher._drain_existing()

            processed_after_wait = len(client.sent)
            print(f"  대기 후 처리 결과: {processed_after_wait}개")

            if processed_after_wait > 0:
                retry_file_age = retry_start_time - file_creation_time
                print(f"  ✅ 대기 후 성공! 파일 age: {retry_file_age:.3f}초")
            else:
                print(f"  ❌ 여전히 처리되지 않음")

                # 파일 상태 확인
                if target_path.exists():
                    stat = target_path.stat()
                    current_age = time.time() - stat.st_mtime
                    print(f"  📋 파일 존재, 현재 age: {current_age:.3f}초")
                    print(f"  📋 파일 mtime: {stat.st_mtime:.3f}")
                    print(f"  📋 현재 시간: {time.time():.3f}")
                else:
                    print(f"  📋 파일이 사라짐")

        # process_loop 테스트
        print("\n⚙️  process_loop 시작...")

        # 새 파일 생성 (process_loop 감지 테스트)
        new_content = f"New test content - timestamp: {time.time()}"
        new_temp_file = test_dir / "new_temp.txt"
        new_temp_file.write_text(new_content)

        new_spool_name = encode("new-stream", "data/new.txt")
        new_target_path = spool_dir / new_spool_name

        # process_loop 백그라운드 실행
        async def run_process_loop():
            try:
                await watcher.process_loop()
            except asyncio.CancelledError:
                pass

        process_task = asyncio.create_task(run_process_loop())

        # 1초 대기 후 새 파일 생성
        await asyncio.sleep(1)
        print("📄 새 파일 생성 (process_loop 중)...")
        new_temp_file.rename(new_target_path)

        # 2초 더 대기
        await asyncio.sleep(2)

        # process_loop 중단
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            pass

        # 최종 결과
        final_count = len(client.sent)
        print(f"\n📊 최종 결과:")
        print(f"  생성된 파일: 2개")
        print(f"  처리된 파일: {final_count}개")

        if final_count > 0:
            print("\n✅ 처리된 파일 상세:")
            for i, (stream_name, s3_key, chunk_idx, total_chunks, data) in enumerate(client.sent):
                print(f"  {i+1}. stream={stream_name}, s3_key={s3_key}, size={len(data)} bytes")

        # 남은 파일 확인
        remaining_files = list(spool_dir.glob("*"))
        if remaining_files:
            print(f"\n📋 남은 파일: {len(remaining_files)}개")
            for f in remaining_files:
                stat = f.stat()
                age = time.time() - stat.st_mtime
                print(f"  - {f.name} (age: {age:.3f}초, mtime: {stat.st_mtime:.3f})")

        watcher.stop()

        # 결론
        if final_count >= 1:
            print("\n🎉 부분 성공: 일부 파일이 처리됨")
            if processed_immediately == 0 and final_count > 0:
                print("🐛 확인됨: incomplete_file_delay가 즉시 처리를 차단함")
        else:
            print("\n❌ 실패: 파일이 전혀 처리되지 않음")

        return final_count > 0

    finally:
        # 정리
        import shutil
        if test_dir.exists():
            shutil.rmtree(test_dir)
            print(f"🗑️  테스트 디렉토리 정리: {test_dir}")

if __name__ == "__main__":
    result = asyncio.run(debug_test())
    exit(0 if result else 1)