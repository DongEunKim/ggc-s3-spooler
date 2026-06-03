"""파일 감시 → Mock 전송 파이프라인 통합 테스트 (Pattern 2 전용)."""

from pathlib import Path

import pytest

from spooler.config import SpoolerConfig
from spooler.filename_codec import encode
from spooler.watcher import SpoolWatcher
from spooler_testing.mock_clients import MockS3SpoolerClient


@pytest.fixture
def spool_dir(tmp_path: Path) -> Path:
    d = tmp_path / "spool"
    d.mkdir()
    return d


@pytest.fixture
def mock_client() -> MockS3SpoolerClient:
    return MockS3SpoolerClient("localhost", 8088, "test-bucket")


@pytest.fixture
def config(spool_dir: Path) -> SpoolerConfig:
    return SpoolerConfig(spool_dir=spool_dir, incomplete_file_delay=0.0)  # 테스트용: 즉시 처리


@pytest.mark.asyncio
async def test_drain_existing_files(
    config: SpoolerConfig,
    mock_client: MockS3SpoolerClient,
    spool_dir: Path,
) -> None:
    """기동 시 이미 존재하는 스풀 파일을 처리한다."""
    content = b"hello world"
    name = encode("test-stream", "prefix/file.txt")
    (spool_dir / name).write_bytes(content)

    # Pattern 2 Mock 클라이언트 연결
    mock_client.connect()

    watcher = SpoolWatcher(config=config, client=mock_client)
    watcher.start()
    await watcher._drain_existing()
    watcher.stop()

    mock_client.close()

    # Pattern 2는 TaskDefinition 메시지를 전송
    assert len(mock_client.sent) == 1
    stream_name, s3_key, chunk_idx, total_chunks, data = mock_client.sent[0]
    assert stream_name == "test-stream"
    assert s3_key == "prefix/file.txt"
    assert chunk_idx == 1
    assert total_chunks == 1
    # Pattern 2에서는 TaskDefinition JSON이 전송됨
    task_def = data.decode()
    assert "S3ExportTaskDefinition" in task_def
    assert "test-bucket" in task_def
    assert not (spool_dir / name).exists(), "전송 완료 후 파일이 삭제되어야 한다"


@pytest.mark.asyncio
async def test_invalid_filename_skipped(
    config: SpoolerConfig,
    mock_client: MockS3SpoolerClient,
    spool_dir: Path,
) -> None:
    """스풀 형식이 아닌 파일은 건너뛴다."""
    (spool_dir / "plain_file.txt").write_bytes(b"data")

    watcher = SpoolWatcher(config=config, client=mock_client)
    watcher.start()
    await watcher._drain_existing()
    watcher.stop()

    assert len(mock_client.sent) == 0
    assert (spool_dir / "plain_file.txt").exists(), "일반 파일은 삭제되면 안 된다"


@pytest.mark.greengrass
async def test_real_stream_manager_connection() -> None:
    """실제 Stream Manager에 연결한다 — Greengrass Core Device 전용."""
    from spooler.stream_client import S3SpoolerClient

    client = S3SpoolerClient("localhost", 8088, "test-bucket")
    client.connect()
    client.close()
