#!/usr/bin/env python3
"""
GGC S3 Spooler 고부하 테스트 스크립트

테스트 시나리오:
1. 다중 파일 동시 생성/처리 (1000개 파일)
2. 대용량 파일 청크 분할 테스트 (100MB+ 파일)
3. 메모리 사용량 모니터링
4. 처리량 및 지연시간 측정
5. 정리 기능 부하 테스트
"""

import asyncio
import logging
import os
import random
import shutil
import tempfile
import time
import psutil
from pathlib import Path
from typing import Dict, List, Tuple

# 프로젝트 모듈 import
import sys
sys.path.insert(0, 'src')

from spooler.config import SpoolerConfig
from spooler.filename_codec import encode
from spooler.stream_client import MockStreamManagerClient
from spooler.watcher import SpoolWatcher
from spooler.cleaner import SpoolCleaner

logger = logging.getLogger(__name__)

class HighLoadTester:
    def __init__(self, spool_dir: Path):
        self.spool_dir = spool_dir
        self.config = SpoolerConfig(
            spool_dir=spool_dir,
            max_spool_size_mb=2048,  # 2GB for testing
            file_retention_hours=1,   # Short for testing
            poll_interval_seconds=2,
            incomplete_file_delay=0.0,  # 테스트용: 즉시 처리
        )
        self.client = MockStreamManagerClient()
        self.client.connect()

        self.stats = {
            'files_created': 0,
            'files_processed': 0,
            'bytes_processed': 0,
            'processing_times': [],
            'memory_usage': [],
            'errors': []
        }

    async def setup(self):
        """테스트 환경 설정"""
        self.spool_dir.mkdir(parents=True, exist_ok=True)

        # Clear any existing files
        for file in self.spool_dir.glob("*"):
            file.unlink()

        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s — %(message)s"
        )

    def create_test_file(self, size_kb: int, content_pattern: str = "test") -> Path:
        """테스트 파일 생성"""
        content = (content_pattern * (size_kb * 1024 // len(content_pattern)))[:size_kb * 1024]
        temp_file = Path(tempfile.mktemp())
        temp_file.write_bytes(content.encode('utf-8'))
        return temp_file

    async def test_concurrent_small_files(self, num_files: int = 1000):
        """시나리오 1: 다중 소형 파일 동시 처리"""
        logger.info(f"=== 시나리오 1: {num_files}개 소형 파일 동시 처리 ===")

        watcher = SpoolWatcher(config=self.config, client=self.client, loop=asyncio.get_event_loop())
        watcher.start()

        # process_loop 백그라운드 시작
        async def run_process_loop():
            try:
                await watcher.process_loop()
            except asyncio.CancelledError:
                pass

        process_task = asyncio.create_task(run_process_loop())

        start_time = time.time()

        # 파일 생성 작업들
        async def create_file_batch(batch_start: int, batch_size: int):
            for i in range(batch_start, min(batch_start + batch_size, num_files)):
                try:
                    # 1-10KB 랜덤 크기
                    size_kb = random.randint(1, 10)
                    temp_file = self.create_test_file(size_kb, f"file_{i}_")

                    # 스풀 파일명으로 인코딩
                    stream_id = random.choice(["telemetry", "sensor-data", "camera-feed"])
                    s3_key = f"data/device-{i % 10}/file_{i}.txt"
                    spool_name = encode(stream_id, s3_key)

                    # Atomic rename으로 스풀 디렉토리에 이동
                    target_path = self.spool_dir / spool_name
                    shutil.move(str(temp_file), str(target_path))

                    self.stats['files_created'] += 1
                    self.stats['bytes_processed'] += size_kb * 1024

                    if i % 100 == 0:
                        logger.info(f"생성된 파일: {i}/{num_files}")

                    # 메모리 사용량 기록
                    if i % 50 == 0:
                        memory_mb = psutil.Process().memory_info().rss / 1024 / 1024
                        self.stats['memory_usage'].append(memory_mb)

                    await asyncio.sleep(0.001)  # 1ms 간격

                except Exception as e:
                    self.stats['errors'].append(f"File {i}: {e}")
                    logger.error(f"파일 {i} 생성 오류: {e}")

        # 배치 단위로 동시 실행 (메모리 절약)
        batch_size = 50
        for batch_start in range(0, num_files, batch_size):
            await create_file_batch(batch_start, batch_size)

        # 파일 생성 완료 대기
        await asyncio.sleep(2)

        # 처리 완료까지 대기 (최대 60초)
        max_wait = 60
        wait_start = time.time()
        while len(self.client.sent) < self.stats['files_created'] and (time.time() - wait_start) < max_wait:
            await asyncio.sleep(0.5)
            logger.info(f"처리된 파일: {len(self.client.sent)}/{self.stats['files_created']}")

        end_time = time.time()
        processing_time = end_time - start_time

        # process_loop 중단
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            pass

        watcher.stop()

        self.stats['processing_times'].append(processing_time)

        logger.info(f"✅ 시나리오 1 완료")
        logger.info(f"   총 처리 시간: {processing_time:.2f}초")
        logger.info(f"   처리량: {num_files/processing_time:.1f} 파일/초")
        logger.info(f"   전송된 파일: {len(self.client.sent)}")

    async def test_large_file_chunks(self):
        """시나리오 2: 대용량 파일 청크 분할 테스트"""
        logger.info("=== 시나리오 2: 대용량 파일 청크 분할 ===")

        watcher = SpoolWatcher(config=self.config, client=self.client, loop=asyncio.get_event_loop())
        watcher.start()

        # process_loop 백그라운드 시작
        async def run_process_loop():
            try:
                await watcher.process_loop()
            except asyncio.CancelledError:
                pass

        process_task = asyncio.create_task(run_process_loop())

        # 100MB 파일 생성 (청크 분할 예상: 2개)
        file_size_mb = 100
        logger.info(f"100MB 파일 생성 중...")

        start_time = time.time()

        temp_file = self.create_test_file(file_size_mb * 1024, "LARGE_FILE_")
        spool_name = encode("large-data", "archive/large_file_100mb.bin")
        target_path = self.spool_dir / spool_name

        shutil.move(str(temp_file), str(target_path))

        # 처리 완료 대기
        initial_count = len(self.client.sent)
        while True:
            await asyncio.sleep(1)
            current_count = len(self.client.sent)
            if current_count > initial_count:
                # 청크가 전송되기 시작했으면 모든 청크 대기
                chunk_count = current_count - initial_count
                logger.info(f"청크 전송 중... ({chunk_count}개 청크)")
                await asyncio.sleep(3)  # 추가 청크 대기
                final_count = len(self.client.sent)
                if final_count == current_count:  # 더이상 증가하지 않으면 완료
                    break

        end_time = time.time()
        processing_time = end_time - start_time

        # process_loop 중단
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            pass

        watcher.stop()

        chunks_sent = len(self.client.sent) - initial_count
        throughput_mbps = file_size_mb / processing_time

        logger.info(f"✅ 시나리오 2 완료")
        logger.info(f"   파일 크기: {file_size_mb}MB")
        logger.info(f"   청크 수: {chunks_sent}개")
        logger.info(f"   처리 시간: {processing_time:.2f}초")
        logger.info(f"   처리량: {throughput_mbps:.1f}MB/초")

    async def test_cleanup_performance(self):
        """시나리오 3: 정리 기능 성능 테스트"""
        logger.info("=== 시나리오 3: 정리 기능 성능 테스트 ===")

        # 5000개 파일 생성 (정리 대상)
        num_test_files = 5000
        logger.info(f"{num_test_files}개 파일 생성하여 정리 성능 테스트...")

        for i in range(num_test_files):
            temp_file = self.create_test_file(1, f"cleanup_test_{i}_")
            spool_name = encode("cleanup-test", f"temp/file_{i}.txt")
            target_path = self.spool_dir / spool_name
            shutil.move(str(temp_file), str(target_path))

            # 일부 파일은 오래된 것으로 설정 (mtime 조작)
            if i < num_test_files // 2:
                old_time = time.time() - 7200  # 2시간 전
                os.utime(target_path, (old_time, old_time))

        logger.info(f"파일 생성 완료. 현재 파일 수: {len(list(self.spool_dir.glob('*')))}")

        # 정리 실행
        cleaner = SpoolCleaner(config=self.config)

        start_time = time.time()
        deleted_count = cleaner.run_once()
        end_time = time.time()

        cleanup_time = end_time - start_time
        remaining_files = len(list(self.spool_dir.glob('*')))

        logger.info(f"✅ 시나리오 3 완료")
        logger.info(f"   삭제된 파일: {deleted_count}개")
        logger.info(f"   정리 시간: {cleanup_time:.3f}초")
        logger.info(f"   남은 파일: {remaining_files}개")
        logger.info(f"   정리 성능: {deleted_count/cleanup_time:.0f} 파일/초")

    async def test_memory_stress(self):
        """시나리오 4: 메모리 스트레스 테스트"""
        logger.info("=== 시나리오 4: 메모리 스트레스 테스트 ===")

        watcher = SpoolWatcher(config=self.config, client=self.client, loop=asyncio.get_event_loop())
        watcher.start()

        # process_loop 백그라운드 시작
        async def run_process_loop():
            try:
                await watcher.process_loop()
            except asyncio.CancelledError:
                pass

        process_task = asyncio.create_task(run_process_loop())

        # 연속적으로 파일 생성하면서 메모리 모니터링
        process = psutil.Process()
        initial_memory = process.memory_info().rss / 1024 / 1024

        logger.info(f"초기 메모리: {initial_memory:.1f}MB")

        for batch in range(10):  # 10 배치
            logger.info(f"배치 {batch + 1}/10 처리 중...")

            # 각 배치마다 100개 파일 생성
            for i in range(100):
                size_kb = random.randint(10, 50)  # 10-50KB
                temp_file = self.create_test_file(size_kb, f"stress_{batch}_{i}_")

                stream_id = f"stress-{batch}"
                s3_key = f"stress/batch_{batch}/file_{i}.dat"
                spool_name = encode(stream_id, s3_key)

                target_path = self.spool_dir / spool_name
                shutil.move(str(temp_file), str(target_path))

            # 메모리 측정
            current_memory = process.memory_info().rss / 1024 / 1024
            self.stats['memory_usage'].append(current_memory)

            logger.info(f"   현재 메모리: {current_memory:.1f}MB")

            await asyncio.sleep(1)  # 처리 시간 확보

        # 최종 메모리 측정
        final_memory = process.memory_info().rss / 1024 / 1024
        memory_growth = final_memory - initial_memory

        # process_loop 중단
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            pass

        watcher.stop()

        logger.info(f"✅ 시나리오 4 완료")
        logger.info(f"   초기 메모리: {initial_memory:.1f}MB")
        logger.info(f"   최종 메모리: {final_memory:.1f}MB")
        logger.info(f"   메모리 증가: {memory_growth:.1f}MB")
        logger.info(f"   최대 메모리: {max(self.stats['memory_usage']):.1f}MB")

    def generate_report(self) -> Dict:
        """최종 성능 리포트 생성"""
        total_files = self.stats['files_created']
        total_bytes = self.stats['bytes_processed']
        total_sent = len(self.client.sent)
        avg_memory = sum(self.stats['memory_usage']) / len(self.stats['memory_usage']) if self.stats['memory_usage'] else 0

        return {
            'summary': {
                'total_files_created': total_files,
                'total_files_sent': total_sent,
                'success_rate': f"{(total_sent/total_files*100):.1f}%" if total_files > 0 else "0%",
                'total_bytes_processed': f"{total_bytes/1024/1024:.1f}MB",
                'average_memory_usage': f"{avg_memory:.1f}MB",
                'error_count': len(self.stats['errors'])
            },
            'performance': {
                'processing_times': self.stats['processing_times'],
                'memory_usage_trend': self.stats['memory_usage'],
            },
            'errors': self.stats['errors']
        }

async def main():
    """고부하 테스트 메인 실행"""
    print("🚀 GGC S3 Spooler 고부하 테스트 시작")

    # 임시 디렉토리 사용
    test_dir = Path(tempfile.mkdtemp(prefix="spooler_load_test_"))
    print(f"테스트 디렉토리: {test_dir}")

    try:
        tester = HighLoadTester(test_dir / "spool")
        await tester.setup()

        print("\n" + "="*60)

        # 시나리오별 실행
        await tester.test_concurrent_small_files(1000)
        await asyncio.sleep(2)

        await tester.test_large_file_chunks()
        await asyncio.sleep(2)

        await tester.test_cleanup_performance()
        await asyncio.sleep(1)

        await tester.test_memory_stress()

        print("\n" + "="*60)
        print("📊 최종 성능 리포트")
        print("="*60)

        report = tester.generate_report()

        for category, data in report.items():
            if category == 'summary':
                print(f"\n[종합 결과]")
                for key, value in data.items():
                    print(f"  {key}: {value}")
            elif category == 'performance' and data.get('processing_times'):
                print(f"\n[성능 지표]")
                times = data['processing_times']
                if times:
                    print(f"  평균 처리 시간: {sum(times)/len(times):.2f}초")
                    print(f"  최대 처리 시간: {max(times):.2f}초")
                    print(f"  최소 처리 시간: {min(times):.2f}초")
            elif category == 'errors' and data:
                print(f"\n[오류 목록] ({len(data)}개)")
                for error in data[:5]:  # 처음 5개만 표시
                    print(f"  {error}")
                if len(data) > 5:
                    print(f"  ... 및 {len(data)-5}개 추가")

        print("\n" + "="*60)
        print("✅ 고부하 테스트 완료")

    finally:
        # 정리
        if test_dir.exists():
            shutil.rmtree(test_dir)
            print(f"테스트 디렉토리 정리 완료: {test_dir}")

if __name__ == "__main__":
    asyncio.run(main())