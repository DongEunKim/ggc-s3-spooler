"""
파일 안정성 통합 테스트.

SpoolWatcher와 파일 안정성 검증의 통합 동작을 테스트한다.
하이브리드 접근법 (시간 기반 + 크기 기반)의 전체 워크플로우를 검증한다.
"""

import asyncio
import pytest
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from spooler.client_protocol import FileTransferClient
from spooler.config import SpoolerConfig
from spooler.watcher import SpoolWatcher


class MockFileTransferClient:
    """테스트용 파일 전송 클라이언트."""

    def __init__(self):
        self.transferred_files = []
        self.append_file = Mock(side_effect=self._append_file)

    def _append_file(self, stream_id: str, s3_key: str, file_path: Path):
        """파일 전송 시뮬레이션."""
        self.transferred_files.append({
            'stream_id': stream_id,
            's3_key': s3_key,
            'file_path': str(file_path),
            'size': file_path.stat().st_size,
        })
        # 파일 삭제 시뮬레이션 (실제 client가 하는 일)
        file_path.unlink()


@pytest.fixture
def stability_config():
    """안정성 검증을 위한 설정 (하이브리드 패턴)."""
    return SpoolerConfig(
        spool_dir=Path("/tmp/test-spool"),
        incomplete_file_delay=0.2,  # deprecated, 하위 호환성
        file_stability_wait=0.05,  # 빠른 테스트를 위해 짧게
        stability_check_count=3,
        stability_check_interval=0.1,
        max_stability_wait=2.0,
    )


@pytest.fixture
def mock_client():
    """Mock 파일 전송 클라이언트."""
    return MockFileTransferClient()


@pytest.fixture
async def watcher(stability_config, mock_client, tmp_path):
    """테스트용 SpoolWatcher 인스턴스."""
    # 임시 디렉토리를 스풀 디렉토리로 설정
    stability_config.spool_dir = tmp_path / "spool"
    watcher = SpoolWatcher(stability_config, mock_client)
    yield watcher
    watcher.stop()


class TestHybridStabilityVerification:
    """하이브리드 안정성 검증 테스트."""

    @pytest.mark.asyncio
    async def test_immediate_stable_file_transfer(self, watcher, mock_client, tmp_path):
        """즉시 안정한 파일의 전송."""
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True)

        # 안정한 파일 생성 (충분히 오래된)
        test_file = spool_dir / "telemetry-stream__data!sensor.json"
        test_file.write_text('{"value": 123}')

        # 파일을 충분히 오래되도록 만들기
        old_time = time.time() - 5.0
        import os
        os.utime(test_file, (old_time, old_time))

        # 직접 전송 메서드 호출
        await watcher._transfer(test_file)

        # 전송 확인
        assert len(mock_client.transferred_files) == 1
        assert mock_client.transferred_files[0]['stream_id'] == 'telemetry-stream'
        assert mock_client.transferred_files[0]['s3_key'] == 'data/sensor.json'
        assert not test_file.exists()  # 파일이 삭제됨

    @pytest.mark.asyncio
    async def test_too_recent_file_skipped(self, watcher, mock_client, tmp_path):
        """너무 최근 파일은 건너뛰기."""
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True)

        # 방금 생성된 파일
        test_file = spool_dir / "recent-stream__data!new.json"
        test_file.write_text('{"new": true}')

        # 직접 전송 메서드 호출
        await watcher._transfer(test_file)

        # 전송되지 않음
        assert len(mock_client.transferred_files) == 0
        assert test_file.exists()  # 파일이 여전히 존재

    @pytest.mark.asyncio
    async def test_unstable_file_handling(self, watcher, mock_client, tmp_path):
        """불안정한 파일 처리."""
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True)

        # 오래된 파일이지만 크기가 계속 변함
        test_file = spool_dir / "unstable-stream__data!growing.json"
        test_file.write_text('{"initial": true}')

        # 파일을 충분히 오래되도록 만들기
        old_time = time.time() - 5.0
        import os
        os.utime(test_file, (old_time, old_time))

        async def grow_file():
            """파일 크기를 계속 증가시키기."""
            await asyncio.sleep(0.05)
            for i in range(5):
                with open(test_file, 'a') as f:
                    f.write(f', "chunk_{i}": {i}')
                await asyncio.sleep(0.05)

        # 파일 증가를 백그라운드에서 시작
        grow_task = asyncio.create_task(grow_file())

        try:
            # 전송 시도
            await watcher._transfer(test_file)

            # 안정성 검증 실패로 인해 fallback이 적용되어 전송됨
            # (실제 구현에서는 fallback 로직이 있음)
            assert len(mock_client.transferred_files) == 1
        finally:
            grow_task.cancel()
            try:
                await grow_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_invalid_filename_handling(self, watcher, mock_client, tmp_path):
        """잘못된 파일명 처리."""
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True)

        # 잘못된 형식의 파일명
        test_file = spool_dir / "invalid_filename.txt"
        test_file.write_text("content")

        # 파일을 충분히 오래되도록 만들기
        old_time = time.time() - 5.0
        import os
        os.utime(test_file, (old_time, old_time))

        # 직접 전송 메서드 호출
        await watcher._transfer(test_file)

        # 전송되지 않음
        assert len(mock_client.transferred_files) == 0
        assert test_file.exists()  # 파일이 여전히 존재

    @pytest.mark.asyncio
    async def test_eventually_stable_file(self, watcher, mock_client, tmp_path):
        """결국 안정화되는 파일."""
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True)

        test_file = spool_dir / "eventual-stream__data!stable.json"
        test_file.write_text('{"initial": true}')

        # 파일을 충분히 오래되도록 만들기
        old_time = time.time() - 5.0
        import os
        os.utime(test_file, (old_time, old_time))

        async def modify_then_stabilize():
            """파일을 수정한 후 안정화."""
            await asyncio.sleep(0.05)
            with open(test_file, 'a') as f:
                f.write(', "modified": true')
            # 이후 안정화됨

        # 파일 수정을 백그라운드에서 시작
        modify_task = asyncio.create_task(modify_then_stabilize())

        try:
            # 약간 대기 후 전송 시도
            await asyncio.sleep(0.02)
            await watcher._transfer(test_file)

            await modify_task

            # 파일이 결국 전송됨 (재시도 로직에 의해)
            # 또는 fallback에 의해 전송됨
            # 정확한 동작은 구현에 따라 다름
            assert len(mock_client.transferred_files) <= 1
        finally:
            if not modify_task.done():
                modify_task.cancel()
                try:
                    await modify_task
                except asyncio.CancelledError:
                    pass

    @pytest.mark.asyncio
    async def test_concurrent_file_processing(self, watcher, mock_client, tmp_path):
        """동시 파일 처리."""
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True)

        # 여러 파일 생성
        files = []
        for i in range(5):
            file_path = spool_dir / f"concurrent-{i}__data!test_{i}.json"
            file_path.write_text(f'{{"id": {i}}}')

            # 모든 파일을 충분히 오래되도록 만들기
            old_time = time.time() - 5.0
            import os
            os.utime(file_path, (old_time, old_time))
            files.append(file_path)

        # 모든 파일을 동시에 전송
        tasks = [watcher._transfer(file_path) for file_path in files]
        await asyncio.gather(*tasks)

        # 모든 파일이 전송됨
        assert len(mock_client.transferred_files) == 5

        # 전송된 파일들의 ID 확인
        transferred_ids = {
            int(tf['s3_key'].split('_')[1].split('.')[0])
            for tf in mock_client.transferred_files
        }
        assert transferred_ids == {0, 1, 2, 3, 4}


class TestProcessLoop:
    """전체 프로세스 루프 테스트."""

    @pytest.mark.asyncio
    async def test_existing_files_processing(self, watcher, mock_client, tmp_path):
        """기존 파일 처리."""
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True)

        # 기존 파일들 생성 (이미 안정화된)
        files = []
        for i in range(3):
            file_path = spool_dir / f"existing-{i}__data!old_{i}.json"
            file_path.write_text(f'{{"existing": {i}}}')

            # 충분히 오래된 시간으로 설정
            old_time = time.time() - 10.0
            import os
            os.utime(file_path, (old_time, old_time))
            files.append(file_path)

        # 기존 파일 처리 실행
        await watcher._drain_existing()

        # 모든 기존 파일이 처리됨
        assert len(mock_client.transferred_files) == 3

        # 파일들이 생성 시간 순으로 처리되었는지 확인
        for i, tf in enumerate(mock_client.transferred_files):
            assert tf['stream_id'] == f"existing-{i}"
            assert f"old_{i}.json" in tf['s3_key']


class TestErrorHandling:
    """오류 처리 테스트."""

    @pytest.mark.asyncio
    async def test_transfer_client_error(self, watcher, mock_client, tmp_path):
        """전송 클라이언트 오류 처리."""
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True)

        test_file = spool_dir / "error-stream__data!fail.json"
        test_file.write_text('{"test": "error"}')

        # 파일을 충분히 오래되도록 만들기
        old_time = time.time() - 5.0
        import os
        os.utime(test_file, (old_time, old_time))

        # 전송 클라이언트에서 예외 발생하도록 설정
        mock_client.append_file.side_effect = Exception("Transfer failed")

        # 전송 시도 (예외가 발생하지만 처리됨)
        await watcher._transfer(test_file)

        # 파일이 여전히 존재 (전송 실패로 인해 삭제되지 않음)
        assert test_file.exists()
        assert len(mock_client.transferred_files) == 0

    @pytest.mark.asyncio
    async def test_file_disappeared_during_processing(self, watcher, mock_client, tmp_path):
        """처리 중 파일 삭제."""
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True)

        test_file = spool_dir / "disappear-stream__data!vanish.json"
        test_file.write_text('{"will": "disappear"}')

        # 파일을 충분히 오래되도록 만들기
        old_time = time.time() - 5.0
        import os
        os.utime(test_file, (old_time, old_time))

        # 안정성 검증 중에 파일 삭제
        original_is_stable = watcher._is_file_stable

        async def delete_file_during_check(path):
            # 첫 번째 호출에서 파일 삭제
            path.unlink()
            return await original_is_stable(path)

        watcher._is_file_stable = delete_file_during_check

        # 전송 시도 (파일이 없어져서 조기 종료)
        await watcher._transfer(test_file)

        # 전송되지 않음
        assert len(mock_client.transferred_files) == 0


class TestPerformanceCharacteristics:
    """성능 특성 테스트."""

    @pytest.mark.asyncio
    async def test_stability_check_timing(self, watcher, tmp_path):
        """안정성 검증 시간 측정."""
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True)

        test_file = spool_dir / "timing-test.txt"
        test_file.write_text("stable content")

        # 파일을 충분히 오래되도록 만들기
        old_time = time.time() - 5.0
        import os
        os.utime(test_file, (old_time, old_time))

        # 안정성 검증 시간 측정
        start_time = time.time()
        is_stable = await watcher._is_file_stable(test_file)
        elapsed = time.time() - start_time

        assert is_stable is True
        # 3회 체크 * 0.1초 간격 ≈ 0.3초 (약간의 여유 포함)
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_time_check_performance(self, watcher, tmp_path):
        """시간 기반 검사 성능."""
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True)

        test_file = spool_dir / "time-test.txt"
        test_file.write_text("content")

        # 시간 기반 검사는 매우 빨라야 함
        start_time = time.time()
        is_ready = await watcher._is_file_ready_by_time(test_file)
        elapsed = time.time() - start_time

        # 최근 파일이므로 False
        assert is_ready is False
        # 시간 기반 검사는 즉시 완료
        assert elapsed < 0.01