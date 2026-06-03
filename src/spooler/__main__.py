"""
엔트리포인트 — `python -m spooler` 로 실행.

서브커맨드:
  (없음)     S3 Spooler 데몬 실행
  encode     스풀 파일명 인코딩 (FR-02 클라이언트 도구)
  decode     스풀 파일명 디코딩 (디버그용)
"""

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Callable

from .cleaner import SpoolCleaner
from .config import SpoolerConfig
from .filename_codec import decode, encode
from .stream_client import S3SpoolerClient
from .watcher import SpoolWatcher


def cmd_encode(args: argparse.Namespace) -> None:
    """FR-02: 스풀 파일명 인코딩 CLI."""
    try:
        result = encode(args.stream_id, args.s3_key)
        print(result)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_decode(args: argparse.Namespace) -> None:
    """스풀 파일명 디코딩 (디버그용)."""
    try:
        meta = decode(args.spool_filename)
        print(f"stream_id:     {meta.stream_id}")
        print(f"s3_key:        {meta.s3_key}")
        print(f"original_name: {meta.original_name}")
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)




def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GGC S3 Spooler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
서브커맨드 예시:
  python -m spooler encode telemetry "data/device-1/reading.json"
  python -m spooler decode "telemetry__data!device-1!reading.json"
  python -m spooler --spool-dir /var/spool/s3  (데몬 모드)
        """,
    )
    subparsers = parser.add_subparsers(dest="command")

    # encode 서브커맨드
    enc = subparsers.add_parser("encode", help="스풀 파일명 인코딩")
    enc.add_argument("stream_id", help="Stream Manager 스트림 이름")
    enc.add_argument("s3_key", help="S3 키 경로 (예: data/device-1/reading.json)")

    # decode 서브커맨드
    dec = subparsers.add_parser("decode", help="스풀 파일명 디코딩 (디버그)")
    dec.add_argument("spool_filename", help="디코딩할 스풀 파일명")


    # INI 설정 파일 지원
    parser.add_argument("--config", "-c", help="INI 설정 파일 경로 (기본: spooler.ini)")

    # 데몬 공유 인수 (INI/환경변수 설정을 오버라이드).
    # 기본값은 None(미지정) — 실제 기본값은 SpoolerConfig dataclass가 보유하며,
    # 우선순위 CLI > 환경변수 > INI > 기본값 이 from_args 에서 적용된다.
    parser.add_argument("--spool-dir", default=None)
    parser.add_argument("--max-size-mb", type=int, default=None)
    parser.add_argument("--retention-hours", type=int, default=None)
    parser.add_argument("--poll-interval", type=int, default=None)
    parser.add_argument("--sm-host", default=None)
    parser.add_argument("--sm-port", type=int, default=None)
    parser.add_argument("--log-level", default=None)
    parser.add_argument("--s3-bucket", default=None, help="A-07 Pattern 2 S3 버킷명")
    parser.add_argument("--status-stream-name", default=None, help="A-07 상태 스트림 이름")
    parser.add_argument(
        "--incomplete-file-delay",
        type=float,
        default=None,
        help="미완성 파일 보호 지연시간 (초)"
    )

    # 🔬 안정성 검증 파라미터 (고급 설정)
    parser.add_argument("--file-stability-wait", type=float, default=None,
                        help="시간 기반 사전 필터링 대기 (초)")
    parser.add_argument("--stability-check-interval", type=float, default=None,
                        help="크기 기반 안정성 체크 간격 (초)")
    parser.add_argument("--stability-check-count", type=int, default=None,
                        help="연속 크기 불변 확인 횟수")
    parser.add_argument("--max-stability-wait", type=float, default=None,
                        help="최대 안정성 대기 시간 (초)")
    parser.add_argument("--stability-max-retries", type=int, default=None,
                        help="안정성 검증 최대 재시도 횟수")
    parser.add_argument("--stability-retry-delay", type=float, default=None,
                        help="안정성 검증 재시도 지연 시간 (초)")

    return parser.parse_args()


async def cleaner_loop(cleaner: SpoolCleaner, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        deleted = cleaner.run_once()
        if deleted:
            logging.getLogger(__name__).info("정리 사이클: %d 파일 삭제", deleted)


async def main() -> None:
    args = parse_args()

    # 서브커맨드 처리 (데몬 모드가 아닌 경우)
    if args.command == "encode":
        cmd_encode(args)
        return
    if args.command == "decode":
        cmd_decode(args)
        return

    config = SpoolerConfig.from_args(args)

    # Pattern 2 전용 모드: s3_bucket 필수 검증
    try:
        config.validate_pattern2_requirements()
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("S3 Spooler 시작 (spool_dir=%s)", config.spool_dir)

    client = S3SpoolerClient(
        config.stream_manager_host,
        config.stream_manager_port,
        config.s3_bucket,
        config.status_stream_name
    )
    client.connect()

    loop = asyncio.get_event_loop()
    watcher = SpoolWatcher(config=config, client=client, loop=loop)
    cleaner = SpoolCleaner(config=config)

    watcher.start()

    stop_event = asyncio.Event()

    def _shutdown(sig: signal.Signals) -> None:
        logger.info("종료 신호 수신 (%s)", sig.name)
        stop_event.set()

    def create_shutdown_handler(sig: signal.Signals) -> "Callable[[], None]":
        def handler() -> None:
            _shutdown(sig)
        return handler

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, create_shutdown_handler(sig))

    try:
        await asyncio.gather(
            watcher.process_loop(),
            cleaner_loop(cleaner, config.poll_interval_seconds),
            stop_event.wait(),
        )
    finally:
        watcher.stop()
        client.close()
        logger.info("S3 Spooler 종료 완료")


if __name__ == "__main__":
    asyncio.run(main())
