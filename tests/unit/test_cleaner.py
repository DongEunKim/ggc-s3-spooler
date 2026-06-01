"""SpoolCleaner 단위 테스트."""

import time
from pathlib import Path

import pytest

from spooler.cleaner import SpoolCleaner
from spooler.config import SpoolerConfig
from spooler.filename_codec import encode


@pytest.fixture
def spool_dir(tmp_path: Path) -> Path:
    d = tmp_path / "spool"
    d.mkdir()
    return d


@pytest.fixture
def config(spool_dir: Path) -> SpoolerConfig:
    return SpoolerConfig(
        spool_dir=spool_dir,
        max_spool_size_mb=1,
        file_retention_hours=1,
    )


def make_spool_file(spool_dir: Path, stream: str, key: str, content: bytes = b"x") -> Path:
    name = encode(stream, key)
    p = spool_dir / name
    p.write_bytes(content)
    return p


class TestEvictExpired:
    def test_old_file_deleted(self, config: SpoolerConfig, spool_dir: Path) -> None:
        p = make_spool_file(spool_dir, "s", "k/f.txt")
        # mtime을 2시간 전으로 조정
        old_time = time.time() - 7200
        import os

        os.utime(p, (old_time, old_time))
        cleaner = SpoolCleaner(config)
        assert cleaner.run_once() == 1
        assert not p.exists()

    def test_fresh_file_kept(self, config: SpoolerConfig, spool_dir: Path) -> None:
        p = make_spool_file(spool_dir, "s", "k/f.txt")
        cleaner = SpoolCleaner(config)
        cleaner.run_once()
        assert p.exists()


class TestEvictOverQuota:
    def test_oldest_deleted_when_over_quota(self, config: SpoolerConfig, spool_dir: Path) -> None:
        # 1MB 한도, 600KB 파일 2개 = 1200KB > 1MB
        data = b"0" * (600 * 1024)
        p1 = make_spool_file(spool_dir, "s", "k/old.bin", data)
        time.sleep(0.01)
        p2 = make_spool_file(spool_dir, "s", "k/new.bin", data)

        import os

        os.utime(p1, (time.time() - 100, time.time() - 100))

        cleaner = SpoolCleaner(config)
        cleaner.run_once()

        assert not p1.exists(), "오래된 파일이 삭제되어야 한다"
        assert p2.exists(), "새 파일은 유지되어야 한다"
