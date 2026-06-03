"""
파일 안정성 모듈 테스트.

CAN Blackbox 패턴의 파일 크기 추적 안정성 검증을 테스트한다.
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from spooler.file_stability import (
    FileStabilityError,
    StabilityConfig,
    is_file_stable_async,
    wait_for_file_stability,
)


class TestStabilityConfig:
    """StabilityConfig 테스트."""

    def test_default_values(self):
        """기본값 검증."""
        config = StabilityConfig()
        assert config.check_count == 3
        assert config.check_interval == 0.5
        assert config.timeout == 30.0

    def test_custom_values(self):
        """사용자 정의 값 검증."""
        config = StabilityConfig(
            check_count=5,
            check_interval=0.2,
            timeout=10.0,
        )
        assert config.check_count == 5
        assert config.check_interval == 0.2
        assert config.timeout == 10.0


class TestAsyncStabilityCheck:
    """비동기식 안정성 검증 테스트."""

    @pytest.mark.asyncio
    async def test_nonexistent_file(self, tmp_path):
        """존재하지 않는 파일 처리."""
        config = StabilityConfig(check_count=2, check_interval=0.1)
        nonexistent = tmp_path / "nonexistent.txt"

        result = await is_file_stable_async(nonexistent, config)
        assert result is False

    @pytest.mark.asyncio
    async def test_stable_file(self, tmp_path):
        """안정한 파일 검증."""
        config = StabilityConfig(check_count=3, check_interval=0.1)
        test_file = tmp_path / "stable.txt"
        test_file.write_text("stable content")

        result = await is_file_stable_async(test_file, config)
        assert result is True

    @pytest.mark.asyncio
    async def test_growing_file_detection(self, tmp_path):
        """크기 변화 감지."""
        config = StabilityConfig(check_count=4, check_interval=0.1)
        test_file = tmp_path / "growing.txt"
        test_file.write_text("initial")

        async def grow_file():
            await asyncio.sleep(0.15)  # 첫 번째와 두 번째 체크 사이
            with open(test_file, 'a') as f:
                f.write(" additional")

        # 파일 증가 태스크를 백그라운드에서 실행
        grow_task = asyncio.create_task(grow_file())

        try:
            result = await is_file_stable_async(test_file, config)
            assert result is False
        finally:
            await grow_task

    @pytest.mark.asyncio
    async def test_timeout_handling(self, tmp_path):
        """타임아웃 예외 처리."""
        config = StabilityConfig(check_count=50, check_interval=0.1, timeout=0.2)
        test_file = tmp_path / "timeout.txt"
        test_file.write_text("content")

        with pytest.raises(FileStabilityError, match="안정성 검증"):
            await is_file_stable_async(test_file, config)

    @pytest.mark.asyncio
    async def test_cancellation_handling(self, tmp_path):
        """태스크 취소 처리."""
        config = StabilityConfig(check_count=10, check_interval=0.5)
        test_file = tmp_path / "cancel.txt"
        test_file.write_text("content")

        async def cancel_after_delay():
            await asyncio.sleep(0.1)
            task.cancel()

        task = asyncio.create_task(is_file_stable_async(test_file, config))
        cancel_task = asyncio.create_task(cancel_after_delay())

        with pytest.raises(asyncio.CancelledError):
            await asyncio.gather(task, cancel_task)

    @pytest.mark.asyncio
    async def test_file_access_error(self, tmp_path):
        """파일 접근 오류 처리."""
        config = StabilityConfig(check_count=3, check_interval=0.1)
        test_file = tmp_path / "access_error.txt"
        test_file.write_text("content")

        # 파일을 삭제해서 stat 에러를 발생시킴
        test_file.unlink()

        result = await is_file_stable_async(test_file, config)
        assert result is False


class TestWaitForFileStability:
    """파일 안정화 대기 테스트."""

    @pytest.mark.asyncio
    async def test_immediately_stable(self, tmp_path):
        """즉시 안정한 파일."""
        config = StabilityConfig(check_count=2, check_interval=0.1)
        test_file = tmp_path / "stable.txt"
        test_file.write_text("stable content")

        result = await wait_for_file_stability(test_file, config, max_retries=3)
        assert result is True

    @pytest.mark.asyncio
    async def test_eventually_stable(self, tmp_path):
        """결국 안정화되는 파일."""
        config = StabilityConfig(check_count=2, check_interval=0.1)
        test_file = tmp_path / "eventually_stable.txt"
        test_file.write_text("initial")

        call_count = 0
        original_is_stable = is_file_stable_async

        async def mock_is_stable(file_path, config):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:  # 처음 2번은 불안정
                return False
            return await original_is_stable(file_path, config)

        with patch('spooler.file_stability.is_file_stable_async', side_effect=mock_is_stable):
            result = await wait_for_file_stability(
                test_file, config, max_retries=5, retry_delay=0.05
            )
            assert result is True
            assert call_count == 3

    @pytest.mark.asyncio
    async def test_never_stable(self, tmp_path):
        """계속 불안정한 파일."""
        config = StabilityConfig(check_count=2, check_interval=0.1)
        test_file = tmp_path / "unstable.txt"
        test_file.write_text("initial")

        with patch('spooler.file_stability.is_file_stable_async', return_value=False):
            result = await wait_for_file_stability(
                test_file, config, max_retries=3, retry_delay=0.05
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_stability_error_handling(self, tmp_path):
        """안정성 검증 오류 처리."""
        config = StabilityConfig(check_count=2, check_interval=0.1)
        test_file = tmp_path / "error.txt"
        test_file.write_text("content")

        with patch(
            'spooler.file_stability.is_file_stable_async',
            side_effect=FileStabilityError("Test error")
        ):
            result = await wait_for_file_stability(test_file, config, max_retries=2)
            assert result is False


class TestIntegrationScenarios:
    """통합 시나리오 테스트."""

    @pytest.mark.asyncio
    async def test_realistic_file_writing_scenario(self, tmp_path):
        """실제적인 파일 작성 시나리오."""
        config = StabilityConfig(check_count=3, check_interval=0.1)
        test_file = tmp_path / "realistic.txt"

        async def simulate_file_writing():
            """파일이 점진적으로 작성되는 상황을 시뮬레이션."""
            test_file.write_text("chunk1")
            await asyncio.sleep(0.05)

            with open(test_file, 'a') as f:
                f.write("chunk2")
            await asyncio.sleep(0.05)

            with open(test_file, 'a') as f:
                f.write("chunk3")
            # 이후 파일이 안정화됨

        # 파일 작성을 백그라운드에서 시작
        write_task = asyncio.create_task(simulate_file_writing())

        # 안정화 대기
        await asyncio.sleep(0.02)  # 파일 작성이 시작된 후
        result = await wait_for_file_stability(
            test_file, config, max_retries=10, retry_delay=0.1
        )

        await write_task
        assert result is True
        assert test_file.read_text() == "chunk1chunk2chunk3"

    @pytest.mark.asyncio
    async def test_concurrent_stability_checks(self, tmp_path):
        """동시 안정성 검증 처리."""
        config = StabilityConfig(check_count=3, check_interval=0.1)

        files = []
        for i in range(5):
            file_path = tmp_path / f"concurrent_{i}.txt"
            file_path.write_text(f"content_{i}")
            files.append(file_path)

        # 모든 파일을 동시에 검증
        tasks = [
            wait_for_file_stability(file_path, config, max_retries=2)
            for file_path in files
        ]

        results = await asyncio.gather(*tasks)
        assert all(results)  # 모든 파일이 안정적이어야 함