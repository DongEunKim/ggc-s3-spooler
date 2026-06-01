#!/usr/bin/env python3
"""
간단한 실제 환경 테스트 — SpoolWatcher 기본 동작 검증
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

async def simple_test():
    """기본 동작 테스트"""
    print("🔍 SpoolWatcher 기본 동작 테스트")

    # 테스트 디렉토리 설정
    test_dir = Path(tempfile.mkdtemp(prefix="spooler_simple_test_"))
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

        # 1초 대기 후 파일 생성
        await asyncio.sleep(1)
        print("📄 테스트 파일 생성...")

        # 테스트 파일 3개 생성
        for i in range(3):
            # 콘텐츠 준비
            content = f"Test file {i} content - timestamp: {time.time()}"

            # 임시 파일 생성
            temp_file = test_dir / f"temp_{i}.txt"
            temp_file.write_text(content)

            # 스풀 파일명으로 변환
            spool_name = encode(f"test-stream-{i}", f"data/test_{i}.txt")
            target_path = spool_dir / spool_name

            # Atomic rename (권장 방식)
            temp_file.rename(target_path)
            print(f"  ✅ 파일 {i+1}: {spool_name}")

        # 처리 대기
        print("⏳ 파일 처리 대기...")
        max_wait = 10  # 최대 10초 대기
        waited = 0

        while waited < max_wait:
            processed_count = len(client.sent)
            print(f"  처리된 파일: {processed_count}/3 (대기: {waited:.1f}초)")

            if processed_count >= 3:
                break

            await asyncio.sleep(0.5)
            waited += 0.5

        # 결과 분석
        final_count = len(client.sent)
        print(f"\n📊 테스트 결과:")
        print(f"  생성된 파일: 3개")
        print(f"  처리된 파일: {final_count}개")

        if final_count > 0:
            print("\n✅ 처리된 파일 상세:")
            for i, (stream_name, s3_key, chunk_idx, total_chunks, data) in enumerate(client.sent):
                print(f"  {i+1}. stream={stream_name}, s3_key={s3_key}, size={len(data)} bytes")

        # 성공/실패 판정
        if final_count == 3:
            print("\n🎉 테스트 성공! SpoolWatcher가 정상 동작합니다.")
            success = True
        else:
            print(f"\n❌ 테스트 실패: 3개 파일 중 {final_count}개만 처리됨")
            success = False

            # 디버깅 정보
            remaining_files = list(spool_dir.glob("*"))
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
    result = asyncio.run(simple_test())
    exit(0 if result else 1)