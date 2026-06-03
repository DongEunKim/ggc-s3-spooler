"""
스풀 디렉토리 감시 — CAN Blackbox Pattern 적용.

새 파일 감지 시 Stream Manager로 전송한다.
FileTransferClient 프로토콜을 통해 Mock과 실제 구현체를 동일하게 처리한다.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .client_protocol import FileTransferClient
from .config import SpoolerConfig
from .file_stability import wait_for_file_stability
from .filename_codec import SpoolFileMeta, decode, is_spool_file

logger = logging.getLogger(__name__)


class SpoolEventHandler(FileSystemEventHandler):
    """
    watchdog 이벤트 핸들러.
    watchdog 콜백은 별도 스레드에서 실행되므로 asyncio 루프에 태스크를 제출한다.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: "asyncio.Queue[Path]",
    ) -> None:
        super().__init__()
        self._loop = loop
        self._queue = queue

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if is_spool_file(path.name):
            self._loop.call_soon_threadsafe(self._queue.put_nowait, path)


class SpoolWatcher:
    """
    스풀 디렉토리를 감시하고 새 파일을 Stream Manager로 전송한다.
    전송 완료 후 파일을 삭제한다.

    CAN Blackbox Pattern: FileTransferClient 프로토콜 사용으로
    Mock과 실제 클라이언트를 구분 없이 처리한다.
    """

    def __init__(
        self,
        config: SpoolerConfig,
        client: FileTransferClient,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._loop = loop or asyncio.get_event_loop()
        self._queue: asyncio.Queue[Path] = asyncio.Queue()
        self._observer: Any = None

    def start(self) -> None:
        self._config.spool_dir.mkdir(parents=True, exist_ok=True)
        handler = SpoolEventHandler(loop=self._loop, queue=self._queue)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._config.spool_dir), recursive=False)
        self._observer.start()
        logger.info("스풀 디렉토리 감시 시작: %s", self._config.spool_dir)

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None

    async def process_loop(self) -> None:
        """
        큐에서 파일 경로를 꺼내 전송한다.
        기동 시 스풀 디렉토리에 이미 있던 파일도 처리한다.
        """
        await self._drain_existing()
        while True:
            path = await self._queue.get()
            await self._transfer(path)

    async def _drain_existing(self) -> None:
        """기동 시 스풀 디렉토리의 기존 파일을 처리한다."""
        spool_dir = self._config.spool_dir
        if not spool_dir.exists():
            return
        for path in sorted(spool_dir.iterdir(), key=lambda p: p.stat().st_mtime):
            if path.is_file() and is_spool_file(path.name):
                await self._transfer(path)

    async def _transfer(self, path: Path) -> None:
        """
        파일 전송 — 하이브리드 안정성 검증.

        시간 기반 사전 필터링 + 크기 기반 안정성 검증을 통해
        실시간성과 신뢰성을 균형있게 보장한다.
        """
        if not path.exists():
            return

        # Phase 1: 시간 기반 사전 필터링 (Fast Path)
        if not await self._is_file_ready_by_time(path):
            return

        # Phase 2: 크기 기반 안정성 검증 (Thorough Path)
        if not await self._is_file_stable(path):
            return

        # Phase 3: 파일명 검증 및 전송
        try:
            meta = decode(path.name)
        except ValueError as exc:
            logger.warning("파일명 디코딩 실패, 건너뜀: %s — %s", path.name, exc)
            return

        await self._transfer_to_stream(path, meta)

    async def _is_file_ready_by_time(self, path: Path) -> bool:
        """시간 기반 파일 준비 상태 확인 — AWS Labs 패턴."""
        try:
            mtime = path.stat().st_mtime
            age = time.time() - mtime

            if age < self._config.incomplete_file_delay:
                logger.debug(
                    "파일이 너무 최신: %s (%.1fs < %.1fs)",
                    path.name, age, self._config.incomplete_file_delay
                )
                return False
            return True
        except OSError as e:
            logger.warning("파일 상태 확인 실패: %s — %s", path.name, e)
            return False

    async def _is_file_stable(self, path: Path) -> bool:
        """크기 기반 파일 안정성 검증 — CAN Blackbox 패턴."""
        try:
            is_stable = await wait_for_file_stability(
                path,
                self._config.stability_config,
                max_retries=self._config.stability_max_retries,
                retry_delay=self._config.stability_retry_delay,
            )

            if not is_stable:
                logger.debug("파일 안정성 검증 실패: %s (작성 중이거나 오류)", path.name)
                return False

            return True
        except Exception as e:
            logger.warning("파일 안정성 검증 오류: %s — %s", path.name, e)
            # 안정성 검증 실패 시 시간 기반 fallback
            logger.debug("시간 기반 fallback 적용: %s", path.name)
            return True

    async def _transfer_to_stream(self, path: Path, meta: SpoolFileMeta) -> None:
        """Stream Manager로 파일 전송."""
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.append_file(meta.stream_id, meta.s3_key, path),
            )
            # 파일 삭제는 stream client에서 처리
            bucket = getattr(self._client, "_s3_bucket", "?")
            logger.info("전송 완료: %s → s3://%s/%s", path.name, bucket, meta.s3_key)
        except Exception as exc:
            # Stream Manager 스트림 미등록 오류 감지
            exc_str = str(exc).lower()
            unregistered_keywords = ("stream", "not found", "does not exist", "unknown")
            if any(keyword in exc_str for keyword in unregistered_keywords):
                # 미등록 스트림으로 판단되는 오류
                logger.error(
                    "미등록 스트림 오류: %s (stream: %s) - 파일 삭제",
                    path.name, meta.stream_id
                )
                try:
                    path.unlink(missing_ok=True)
                    logger.warning("미등록 스트림 파일 삭제 완료: %s", path.name)
                except OSError as delete_exc:
                    logger.error("파일 삭제 실패: %s — %s", path.name, delete_exc)
            else:
                # 기타 전송 오류는 재시도를 위해 파일 유지
                logger.error("전송 실패: %s — %s (재시도 대기)", path.name, exc)
