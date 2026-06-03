#!/usr/bin/env python3
"""
GGC S3 Spooler 종합 고부하 성능 테스트 — 메트릭 기반 분석.

확장된 테스트 시나리오:
1. 기존 시나리오 (소형/대용량 파일, 정리, 메모리)
2. 멀티 프로세스 동시성 테스트
3. 처리량 포화점 측정
4. 지연시간 분포 분석 (P50/P90/P99)
5. 순간 고부하 테스트
6. 혼합 워크로드 테스트

실시간 메트릭 수집과 현실적 Mock 시뮬레이션을 통해
실제 TGU 환경에서의 성능을 예측한다.
"""

import argparse
import asyncio
import json
import logging
import multiprocessing
import os
import random
import shutil
import statistics
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

# 프로젝트 모듈 import
import sys
sys.path.insert(0, 'src')

from spooler.config import SpoolerConfig
from spooler.filename_codec import encode
from spooler.watcher import SpoolWatcher
from spooler.cleaner import SpoolCleaner
from spooler_testing.mock_clients import (
    MockS3SpoolerClient,
    RealisticMockS3SpoolerClient
)
from spooler_testing.metrics import get_metrics, reset_global_metrics, PerformanceMetrics

logger = logging.getLogger(__name__)


class EnhancedHighLoadTester:
    """
    종합 고부하 성능 테스트 — 메트릭 기반 분석.

    실시간 메트릭 수집과 현실적 Mock 시뮬레이션을 통해
    다양한 부하 조건에서의 스풀러 성능을 측정한다.
    """

    def __init__(self, spool_dir: Path, use_realistic_mock: bool = True,
                 simulation_params: Optional[Dict[str, Any]] = None):
        self.spool_dir = spool_dir
        self.use_realistic_mock = use_realistic_mock
        self.simulation_params = simulation_params or {
            'network_latency_range': (0.01, 0.05),  # 10-50ms (TGU 환경 가정)
            'bandwidth_limit_mbps': 10.0,  # 10MB/s (제한된 네트워크)
            'error_rate': 0.005,  # 0.5% 오류율
            'max_connection_delay': 3.0  # 연결 지연
        }

        self.config = SpoolerConfig(
            spool_dir=spool_dir,
            max_spool_size_mb=4096,  # 4GB for stress testing
            file_retention_hours=24,
            poll_interval_seconds=1,
            incomplete_file_delay=0.1,  # 100ms 지연
        )

        # 클라이언트 선택
        if use_realistic_mock:
            self.client = RealisticMockS3SpoolerClient(
                host="localhost", port=8088, s3_bucket="test-bucket",
                status_stream_name="status-stream", **self.simulation_params
            )
            logger.info("현실적 Mock 클라이언트 사용: %s", self.simulation_params)
        else:
            self.client = MockS3SpoolerClient(
                host="localhost", port=8088, s3_bucket="test-bucket"
            )
            logger.info("기본 Mock 클라이언트 사용")

        self.client.connect()

        # 테스트 결과 저장소
        self.test_results: Dict[str, Any] = {}

    async def setup(self) -> None:
        """테스트 환경 설정"""
        self.spool_dir.mkdir(parents=True, exist_ok=True)

        # 기존 파일 정리
        for file in self.spool_dir.glob("*"):
            if file.is_file():
                file.unlink()

        # 로깅 설정
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s — %(message)s"
        )

        # 메트릭 시스템 초기화
        reset_global_metrics()
        logger.info("테스트 환경 설정 완료")

    def create_test_file(self, size_kb: int, content_pattern: str = "test") -> Path:
        """테스트 파일 생성"""
        content = (content_pattern * (size_kb * 1024 // len(content_pattern) + 1))[:size_kb * 1024]
        temp_file = Path(tempfile.mktemp())
        temp_file.write_bytes(content.encode('utf-8'))
        return temp_file

    async def test_concurrent_small_files(self, num_files: int = 1000) -> Dict[str, Any]:
        """시나리오 1: 다중 소형 파일 동시 처리"""
        logger.info("=== 시나리오 1: %d개 소형 파일 동시 처리 ===", num_files)

        metrics = get_metrics()
        watcher = SpoolWatcher(config=self.config, client=self.client, loop=asyncio.get_event_loop())
        watcher.start()

        async def run_process_loop():
            try:
                await watcher.process_loop()
            except asyncio.CancelledError:
                pass

        process_task = asyncio.create_task(run_process_loop())
        start_time = time.time()

        # 파일 생성 (배치 단위)
        batch_size = 100
        for batch_start in range(0, num_files, batch_size):
            for i in range(batch_start, min(batch_start + batch_size, num_files)):
                try:
                    size_kb = random.randint(1, 50)  # 1-50KB
                    temp_file = self.create_test_file(size_kb, f"small_file_{i}_")

                    stream_id = random.choice(["telemetry", "sensor-data", "diagnostic"])
                    s3_key = f"batch_{batch_start//batch_size}/file_{i:06d}.dat"
                    spool_name = encode(stream_id, s3_key)

                    target_path = self.spool_dir / spool_name
                    shutil.move(str(temp_file), str(target_path))

                    await asyncio.sleep(0.001)  # 1ms 간격

                except Exception as e:
                    logger.error("파일 %d 생성 오류: %s", i, e)

            logger.info("배치 %d/%d 완료", (batch_start // batch_size) + 1, (num_files - 1) // batch_size + 1)
            await asyncio.sleep(0.5)  # 배치 간 간격

        # 처리 완료 대기 (최대 120초)
        max_wait = 120
        wait_start = time.time()
        initial_sent = len(self.client.sent)

        while (time.time() - wait_start) < max_wait:
            current_sent = len(self.client.sent)
            if current_sent >= num_files + initial_sent:
                break
            await asyncio.sleep(1)
            if (time.time() - wait_start) % 10 < 1:  # 10초마다 로그
                logger.info("처리 진행률: %d/%d", current_sent - initial_sent, num_files)

        end_time = time.time()
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            pass
        watcher.stop()

        processing_time = end_time - start_time
        files_processed = len(self.client.sent) - initial_sent

        # 메트릭 수집
        performance_report = metrics.get_performance_report()

        result = {
            'scenario': 'concurrent_small_files',
            'parameters': {'num_files': num_files, 'file_size_range': '1-50KB'},
            'results': {
                'total_processing_time': round(processing_time, 2),
                'files_processed': files_processed,
                'success_rate': round(files_processed / num_files * 100, 1),
                'throughput_files_per_second': round(files_processed / processing_time, 2),
                'average_file_size_kb': performance_report.get('histograms', {}).get('file_size_bytes', {}).get('mean', 0) / 1024 if performance_report.get('histograms', {}).get('file_size_bytes') else 0
            },
            'metrics': performance_report
        }

        logger.info("✅ 시나리오 1 완료")
        logger.info("   처리 시간: %.2fs, 처리량: %.1f파일/초",
                   processing_time, files_processed / processing_time)

        return result

    async def test_multi_process_concurrency(self, process_count: int = 4, files_per_process: int = 100) -> Dict[str, Any]:
        """시나리오 2: 멀티 프로세스 동시성 테스트"""
        logger.info("=== 시나리오 2: %d개 프로세스 동시 파일 생성 ===", process_count)

        metrics = get_metrics()
        watcher = SpoolWatcher(config=self.config, client=self.client, loop=asyncio.get_event_loop())
        watcher.start()

        async def run_process_loop():
            try:
                await watcher.process_loop()
            except asyncio.CancelledError:
                pass

        process_task = asyncio.create_task(run_process_loop())
        start_time = time.time()

        # 동시 프로세스 실행
        total_files = process_count * files_per_process

        def create_files_worker(process_id: int, files_count: int, spool_dir_str: str) -> Dict[str, Any]:
            """개별 프로세스 작업자"""
            worker_stats = {'files_created': 0, 'errors': 0, 'creation_time': 0}
            spool_dir = Path(spool_dir_str)

            worker_start = time.time()

            for i in range(files_count):
                try:
                    # 파일 크기와 내용
                    size_kb = random.randint(5, 25)
                    content = f"process_{process_id}_file_{i}_" * (size_kb * 1024 // 20)
                    content = content[:size_kb * 1024]

                    # 고유한 파일명 생성 (프로세스 ID 포함)
                    stream_id = f"proc-{process_id}"
                    s3_key = f"concurrent/proc_{process_id:02d}/file_{i:04d}.txt"
                    spool_name = encode(stream_id, s3_key)

                    # 임시 파일 생성 후 atomic move
                    temp_file = Path(tempfile.mktemp())
                    temp_file.write_text(content)

                    target_path = spool_dir / spool_name
                    shutil.move(str(temp_file), str(target_path))

                    worker_stats['files_created'] += 1
                    time.sleep(0.002)  # 2ms 간격

                except Exception as e:
                    worker_stats['errors'] += 1
                    print(f"Worker {process_id} 파일 {i} 오류: {e}")

            worker_stats['creation_time'] = time.time() - worker_start
            return worker_stats

        # ProcessPoolExecutor로 병렬 실행
        with ProcessPoolExecutor(max_workers=process_count) as executor:
            futures = [
                executor.submit(create_files_worker, proc_id, files_per_process, str(self.spool_dir))
                for proc_id in range(process_count)
            ]

            worker_results = [future.result() for future in futures]

        # 처리 완료 대기
        max_wait = 180  # 3분
        wait_start = time.time()
        initial_sent = len(self.client.sent)

        while (time.time() - wait_start) < max_wait:
            current_sent = len(self.client.sent)
            if current_sent >= total_files + initial_sent:
                break
            await asyncio.sleep(2)
            logger.info("동시성 처리 진행률: %d/%d", current_sent - initial_sent, total_files)

        end_time = time.time()
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            pass
        watcher.stop()

        # 결과 분석
        processing_time = end_time - start_time
        files_processed = len(self.client.sent) - initial_sent
        total_created = sum(w['files_created'] for w in worker_results)
        total_errors = sum(w['errors'] for w in worker_results)

        performance_report = metrics.get_performance_report()

        result = {
            'scenario': 'multi_process_concurrency',
            'parameters': {
                'process_count': process_count,
                'files_per_process': files_per_process,
                'total_files': total_files
            },
            'results': {
                'total_processing_time': round(processing_time, 2),
                'files_created': total_created,
                'files_processed': files_processed,
                'creation_errors': total_errors,
                'success_rate': round(files_processed / total_files * 100, 1),
                'concurrent_throughput': round(files_processed / processing_time, 2),
                'worker_results': worker_results
            },
            'metrics': performance_report
        }

        logger.info("✅ 시나리오 2 완료")
        logger.info("   생성: %d개, 처리: %d개, 오류: %d개", total_created, files_processed, total_errors)
        logger.info("   동시성 처리량: %.1f파일/초", files_processed / processing_time)

        return result

    async def test_throughput_saturation(self) -> Dict[str, Any]:
        """시나리오 3: 처리량 포화점 측정"""
        logger.info("=== 시나리오 3: 처리량 포화점 측정 ===")

        metrics = get_metrics()
        watcher = SpoolWatcher(config=self.config, client=self.client, loop=asyncio.get_event_loop())
        watcher.start()

        async def run_process_loop():
            try:
                await watcher.process_loop()
            except asyncio.CancelledError:
                pass

        process_task = asyncio.create_task(run_process_loop())

        # 점진적 부하 증가 테스트
        load_phases = [
            {'rate_per_second': 5, 'duration': 30, 'description': '저부하'},
            {'rate_per_second': 20, 'duration': 30, 'description': '중부하'},
            {'rate_per_second': 50, 'duration': 30, 'description': '고부하'},
            {'rate_per_second': 100, 'duration': 30, 'description': '초고부하'},
        ]

        phase_results = []
        start_time = time.time()

        for phase_idx, phase in enumerate(load_phases):
            logger.info("부하 단계 %d/%d: %s (%.1f파일/초, %d초)",
                       phase_idx + 1, len(load_phases), phase['description'],
                       phase['rate_per_second'], phase['duration'])

            phase_start = time.time()
            phase_initial_sent = len(self.client.sent)
            files_created_in_phase = 0

            # 지정된 속도로 파일 생성
            interval = 1.0 / phase['rate_per_second']
            phase_end_time = phase_start + phase['duration']

            while time.time() < phase_end_time:
                try:
                    # 파일 생성
                    size_kb = random.randint(10, 100)
                    temp_file = self.create_test_file(size_kb, f"saturation_phase{phase_idx}_")

                    stream_id = f"saturation-{phase_idx}"
                    s3_key = f"saturation/phase_{phase_idx}/file_{files_created_in_phase:06d}.bin"
                    spool_name = encode(stream_id, s3_key)

                    target_path = self.spool_dir / spool_name
                    shutil.move(str(temp_file), str(target_path))

                    files_created_in_phase += 1

                    await asyncio.sleep(interval)

                except Exception as e:
                    logger.error("포화 테스트 파일 생성 오류: %s", e)

            # 단계 완료 후 잠시 대기 (큐 처리 시간 확보)
            await asyncio.sleep(5)

            phase_end = time.time()
            phase_files_processed = len(self.client.sent) - phase_initial_sent
            phase_duration = phase_end - phase_start

            # 실시간 메트릭 수집
            queue_depth = metrics.get_gauge_value("file_queue_depth")
            processing_rate = phase_files_processed / phase_duration if phase_duration > 0 else 0

            phase_result = {
                'phase': phase_idx + 1,
                'description': phase['description'],
                'target_rate': phase['rate_per_second'],
                'actual_creation_rate': files_created_in_phase / phase_duration,
                'processing_rate': processing_rate,
                'files_created': files_created_in_phase,
                'files_processed': phase_files_processed,
                'queue_depth_final': queue_depth,
                'duration': round(phase_duration, 2),
                'saturation_ratio': min(processing_rate / phase['rate_per_second'], 1.0) if phase['rate_per_second'] > 0 else 0
            }

            phase_results.append(phase_result)

            logger.info("   단계 완료: 생성 %.1f파일/초, 처리 %.1f파일/초, 큐깊이 %.0f",
                       phase_result['actual_creation_rate'],
                       phase_result['processing_rate'],
                       queue_depth)

        end_time = time.time()
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            pass
        watcher.stop()

        # 포화점 분석
        saturation_point = None
        for phase in phase_results:
            if phase['saturation_ratio'] < 0.9:  # 90% 미만 처리율이면 포화점
                saturation_point = phase['target_rate']
                break

        performance_report = metrics.get_performance_report()
        total_duration = end_time - start_time

        result = {
            'scenario': 'throughput_saturation',
            'parameters': {'load_phases': load_phases},
            'results': {
                'total_duration': round(total_duration, 2),
                'phase_results': phase_results,
                'saturation_point_files_per_second': saturation_point,
                'max_sustainable_throughput': max((p['processing_rate'] for p in phase_results), default=0),
                'final_queue_depth': metrics.get_gauge_value("file_queue_depth")
            },
            'metrics': performance_report
        }

        logger.info("✅ 시나리오 3 완료")
        logger.info("   포화점: %s파일/초", saturation_point or "측정 범위 초과")
        logger.info("   최대 처리량: %.1f파일/초",
                   max((p['processing_rate'] for p in phase_results), default=0))

        return result

    async def test_latency_distribution(self, num_samples: int = 500) -> Dict[str, Any]:
        """시나리오 4: 지연시간 분포 분석"""
        logger.info("=== 시나리오 4: 지연시간 분포 분석 (%d샘플) ===", num_samples)

        metrics = get_metrics()
        watcher = SpoolWatcher(config=self.config, client=self.client, loop=asyncio.get_event_loop())
        watcher.start()

        async def run_process_loop():
            try:
                await watcher.process_loop()
            except asyncio.CancelledError:
                pass

        process_task = asyncio.create_task(run_process_loop())

        # 파일별 지연시간 추적
        file_timestamps: Dict[str, float] = {}
        latencies: List[float] = []

        start_time = time.time()

        # 파일 생성 (일정한 간격)
        for i in range(num_samples):
            try:
                creation_time = time.time()
                size_kb = random.randint(5, 100)
                temp_file = self.create_test_file(size_kb, f"latency_test_{i}_")

                stream_id = "latency-test"
                s3_key = f"latency/sample_{i:05d}.dat"
                spool_name = encode(stream_id, s3_key)

                # 생성 시간 기록
                file_timestamps[s3_key] = creation_time

                target_path = self.spool_dir / spool_name
                shutil.move(str(temp_file), str(target_path))

                await asyncio.sleep(0.1)  # 100ms 간격

            except Exception as e:
                logger.error("지연시간 테스트 파일 %d 생성 오류: %s", i, e)

        # 처리 완료 대기 및 지연시간 계산
        max_wait = 120
        wait_start = time.time()
        initial_sent = len(self.client.sent)

        while (time.time() - wait_start) < max_wait and len(latencies) < num_samples:
            current_sent = len(self.client.sent)
            processed_count = current_sent - initial_sent

            # 새로 처리된 파일들의 지연시간 계산
            if hasattr(self.client, 'detailed_sent'):
                # RealisticMock의 경우 상세 정보 활용
                for stream, s3_key, seq, task_def, sim_delay in self.client.detailed_sent[-processed_count:]:
                    if s3_key in file_timestamps:
                        creation_time = file_timestamps[s3_key]
                        completion_time = time.time()
                        latency = completion_time - creation_time
                        latencies.append(latency)
                        del file_timestamps[s3_key]

            await asyncio.sleep(1)

            if len(latencies) % 50 == 0 and len(latencies) > 0:
                logger.info("지연시간 분석 진행률: %d/%d", len(latencies), num_samples)

        end_time = time.time()
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            pass
        watcher.stop()

        # 지연시간 분포 계산
        if latencies:
            sorted_latencies = sorted(latencies)
            count = len(sorted_latencies)

            latency_stats = {
                'count': count,
                'mean': round(statistics.mean(sorted_latencies), 3),
                'median': round(statistics.median(sorted_latencies), 3),
                'std_dev': round(statistics.stdev(sorted_latencies), 3) if count > 1 else 0,
                'min': round(min(sorted_latencies), 3),
                'max': round(max(sorted_latencies), 3),
                'p50': round(sorted_latencies[int(count * 0.50)], 3),
                'p75': round(sorted_latencies[int(count * 0.75)], 3),
                'p90': round(sorted_latencies[int(count * 0.90)], 3),
                'p95': round(sorted_latencies[int(count * 0.95)], 3),
                'p99': round(sorted_latencies[int(count * 0.99)], 3) if count >= 100 else round(max(sorted_latencies), 3),
                'p999': round(sorted_latencies[int(count * 0.999)], 3) if count >= 1000 else round(max(sorted_latencies), 3)
            }
        else:
            latency_stats = {'error': 'No latency data collected'}

        performance_report = metrics.get_performance_report()
        total_duration = end_time - start_time

        result = {
            'scenario': 'latency_distribution',
            'parameters': {'num_samples': num_samples},
            'results': {
                'total_duration': round(total_duration, 2),
                'samples_processed': len(latencies),
                'latency_statistics': latency_stats,
                'raw_latencies': latencies[:100]  # 처음 100개만 저장 (메모리 절약)
            },
            'metrics': performance_report
        }

        logger.info("✅ 시나리오 4 완료")
        if latencies:
            logger.info("   지연시간: P50=%.3fs, P90=%.3fs, P99=%.3fs",
                       latency_stats['p50'], latency_stats['p90'], latency_stats['p99'])
        else:
            logger.warning("   지연시간 데이터 수집 실패")

        return result

    async def run_all_scenarios(self) -> Dict[str, Any]:
        """모든 시나리오 실행"""
        logger.info("🚀 GGC S3 Spooler 종합 성능 테스트 시작")
        logger.info("현실적 Mock: %s", self.use_realistic_mock)
        if self.use_realistic_mock:
            logger.info("시뮬레이션 파라미터: %s", self.simulation_params)

        await self.setup()

        all_results = {
            'test_info': {
                'timestamp': time.time(),
                'use_realistic_mock': self.use_realistic_mock,
                'simulation_params': self.simulation_params if self.use_realistic_mock else None
            },
            'scenarios': {}
        }

        # 시나리오 실행
        scenarios = [
            ('concurrent_small_files', lambda: self.test_concurrent_small_files(800)),
            ('multi_process_concurrency', lambda: self.test_multi_process_concurrency(4, 75)),
            ('throughput_saturation', lambda: self.test_throughput_saturation()),
            ('latency_distribution', lambda: self.test_latency_distribution(300)),
        ]

        for scenario_name, scenario_func in scenarios:
            try:
                logger.info("\n" + "="*60)
                result = await scenario_func()
                all_results['scenarios'][scenario_name] = result

                # 시나리오 간 휴식
                await asyncio.sleep(3)

            except Exception as e:
                logger.error("시나리오 '%s' 실행 오류: %s", scenario_name, e)
                all_results['scenarios'][scenario_name] = {
                    'error': str(e),
                    'scenario': scenario_name
                }

        # 종합 분석
        all_results['summary'] = self._generate_summary(all_results)

        logger.info("\n" + "="*60)
        logger.info("🎯 종합 성능 테스트 완료")

        return all_results

    def _generate_summary(self, all_results: Dict[str, Any]) -> Dict[str, Any]:
        """종합 성능 요약 생성"""
        summary = {
            'total_files_processed': 0,
            'peak_throughput': 0,
            'average_latency_p50': 0,
            'saturation_point': None,
            'scenario_count': len(all_results['scenarios']),
            'success_scenarios': 0
        }

        for scenario_name, result in all_results['scenarios'].items():
            if 'error' in result:
                continue

            summary['success_scenarios'] += 1

            # 처리된 파일 수 누적
            files_processed = result.get('results', {}).get('files_processed', 0)
            summary['total_files_processed'] += files_processed

            # 최대 처리량 추출
            throughput = result.get('results', {}).get('throughput_files_per_second', 0)
            if throughput > summary['peak_throughput']:
                summary['peak_throughput'] = throughput

            # 포화점 정보
            if scenario_name == 'throughput_saturation':
                summary['saturation_point'] = result.get('results', {}).get('saturation_point_files_per_second')

            # 평균 지연시간
            if scenario_name == 'latency_distribution':
                latency_p50 = result.get('results', {}).get('latency_statistics', {}).get('p50', 0)
                summary['average_latency_p50'] = latency_p50

        return summary

    def cleanup(self) -> None:
        """테스트 환경 정리"""
        if self.client:
            self.client.close()

        # 스풀 디렉토리 정리
        if self.spool_dir.exists():
            for file in self.spool_dir.glob("*"):
                if file.is_file():
                    file.unlink()


async def main():
    """메인 실행 함수"""
    parser = argparse.ArgumentParser(description='GGC S3 Spooler 종합 성능 테스트')
    parser.add_argument('--realistic', action='store_true',
                       help='현실적 Mock 시뮬레이션 사용 (네트워크 지연, 오류 포함)')
    parser.add_argument('--output', type=str, default='benchmark_results.json',
                       help='결과 저장 파일명')
    parser.add_argument('--spool-dir', type=str,
                       help='테스트용 스풀 디렉토리 (기본: 임시 디렉토리)')

    args = parser.parse_args()

    # 테스트 디렉토리 설정
    if args.spool_dir:
        test_dir = Path(args.spool_dir)
    else:
        test_dir = Path(tempfile.mkdtemp(prefix="enhanced_benchmark_"))

    logger.info("테스트 디렉토리: %s", test_dir)

    try:
        # 현실적 Mock 파라미터 (TGU 환경 시뮬레이션)
        realistic_params = {
            'network_latency_range': (0.02, 0.08),  # 20-80ms (불안정한 네트워크)
            'bandwidth_limit_mbps': 5.0,  # 5MB/s (제한된 대역폭)
            'error_rate': 0.01,  # 1% 오류율
            'max_connection_delay': 5.0  # 최대 5초 연결 지연
        }

        tester = EnhancedHighLoadTester(
            spool_dir=test_dir / "spool",
            use_realistic_mock=args.realistic,
            simulation_params=realistic_params if args.realistic else None
        )

        # 전체 테스트 실행
        results = await tester.run_all_scenarios()

        # 결과 저장
        results_file = Path(args.output)
        results_file.write_text(json.dumps(results, indent=2, ensure_ascii=False))

        logger.info("📊 테스트 결과 저장됨: %s", results_file)

        # 간단한 요약 출력
        summary = results['summary']
        logger.info("\n📈 성능 요약:")
        logger.info("  총 처리 파일: %d개", summary['total_files_processed'])
        logger.info("  최대 처리량: %.1f파일/초", summary['peak_throughput'])
        logger.info("  평균 지연시간 (P50): %.3f초", summary['average_latency_p50'])
        if summary['saturation_point']:
            logger.info("  처리량 포화점: %.1f파일/초", summary['saturation_point'])
        logger.info("  성공한 시나리오: %d/%d", summary['success_scenarios'], summary['scenario_count'])

    except Exception as e:
        logger.error("테스트 실행 중 오류: %s", e)
        raise

    finally:
        # 정리
        if hasattr(locals().get('tester'), 'cleanup'):
            tester.cleanup()

        if not args.spool_dir and test_dir.exists():
            shutil.rmtree(test_dir)
            logger.info("임시 테스트 디렉토리 정리 완료")


if __name__ == "__main__":
    asyncio.run(main())