"""스풀 디렉토리 공간 및 보존 기간 관리."""

import logging
import time
from collections.abc import Generator
from pathlib import Path

from .config import SpoolerConfig
from .filename_codec import is_spool_file

logger = logging.getLogger(__name__)


class SpoolCleaner:
    """
    두 가지 정책으로 스풀 디렉토리를 정리한다:
    1. 보존 기간 초과 파일 삭제 (공간 무관)
    2. 전체 용량 초과 시 오래된 파일부터 삭제
    """

    def __init__(self, config: SpoolerConfig) -> None:
        self._config = config

    def run_once(self) -> int:
        """정리 사이클 1회 실행. 삭제된 파일 수를 반환한다."""
        deleted = 0
        deleted += self._evict_expired()
        deleted += self._evict_over_quota()
        return deleted

    def _evict_expired(self) -> int:
        cutoff = time.time() - self._config.file_retention_hours * 3600
        deleted = 0
        for path in self._iter_spool_files():
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    logger.warning("보존기간 초과로 삭제: %s", path.name)
                    deleted += 1
            except OSError as exc:
                logger.error("파일 삭제 실패: %s — %s", path, exc)
        return deleted

    def _evict_over_quota(self) -> int:
        files = sorted(
            self._iter_spool_files(),
            key=lambda p: p.stat().st_mtime,
        )
        total = sum(p.stat().st_size for p in files)
        deleted = 0
        for path in files:
            if total <= self._config.max_spool_size_bytes:
                break
            try:
                size = path.stat().st_size
                path.unlink(missing_ok=True)
                total -= size
                logger.warning("용량 초과로 삭제: %s (%d bytes)", path.name, size)
                deleted += 1
            except OSError as exc:
                logger.error("파일 삭제 실패: %s — %s", path, exc)
        return deleted

    def _iter_spool_files(self) -> "Generator[Path, None, None]":
        spool_dir = self._config.spool_dir
        if not spool_dir.exists():
            return
        for path in spool_dir.iterdir():
            if path.is_file() and is_spool_file(path.name):
                yield path
