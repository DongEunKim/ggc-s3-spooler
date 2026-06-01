"""
파일 안정성 검증 — CAN Blackbox 패턴 구현.

파일 크기 추적을 통한 완성도 검증과 시간 기반 필터링을 결합한
하이브리드 안정성 검증 시스템을 제공한다.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)


class StabilityConfig(NamedTuple):
    """파일 안정성 검증 설정."""
    check_count: int = 3
    check_interval: float = 0.5
    timeout: float = 30.0  # 최대 대기 시간 (초)


class FileStabilityError(Exception):
    """파일 안정성 검증 실패 예외."""
    pass


async def is_file_stable_async(
    file_path: Path,
    config: StabilityConfig,
) -> bool:
    """
    비동기 파일 안정성 검증 — CAN Blackbox 패턴.

    파일 크기가 일정 시간동안 변하지 않으면 안정적으로 판단한다.

    Args:
        file_path: 검증할 파일 경로
        config: 안정성 검증 설정

    Returns:
        True if 파일이 안정적 (완전히 작성 완료)
        False if 아직 불안정 (작성 중이거나 오류)

    Raises:
        FileStabilityError: 파일 접근 오류 또는 타임아웃
    """
    if not file_path.exists():
        logger.debug("파일 없음: %s", file_path)
        return False

    start_time = time.time()
    previous_size = -1

    logger.debug(
        "파일 안정성 검증 시작: %s (체크 %d회, 간격 %.1fs)",
        file_path.name, config.check_count, config.check_interval
    )

    try:
        for i in range(config.check_count):
            # 타임아웃 검사
            elapsed = time.time() - start_time
            if elapsed > config.timeout:
                logger.warning(
                    "파일 안정성 검증 타임아웃: %s (%.1fs 초과)",
                    file_path.name, config.timeout
                )
                raise FileStabilityError(f"안정성 검증 타임아웃: {file_path.name}")

            # 현재 파일 크기 확인
            try:
                current_size = file_path.stat().st_size
            except OSError as e:
                logger.warning("파일 상태 확인 실패: %s — %s", file_path.name, e)
                return False

            # 첫 번째 체크가 아니고 크기가 변했으면 불안정
            if i > 0 and current_size != previous_size:
                logger.debug(
                    "파일 크기 변화 감지: %s (%d → %d bytes)",
                    file_path.name, previous_size, current_size
                )
                return False

            # 마지막 체크가 아니면 대기
            if i < config.check_count - 1:
                await asyncio.sleep(config.check_interval)

            previous_size = current_size

        logger.debug(
            "파일 안정성 검증 성공: %s (%d bytes, %.1fs)",
            file_path.name, previous_size, time.time() - start_time
        )
        return True

    except asyncio.CancelledError:
        logger.debug("파일 안정성 검증 취소: %s", file_path.name)
        raise
    except Exception as e:
        logger.error("파일 안정성 검증 오류: %s — %s", file_path.name, e)
        raise FileStabilityError(f"안정성 검증 실패: {file_path.name}") from e


def is_file_stable_sync(
    file_path: Path,
    config: StabilityConfig,
) -> bool:
    """
    동기식 파일 안정성 검증 — CAN Blackbox 원본 패턴.

    테스트 또는 동기식 컨텍스트에서 사용한다.

    Args:
        file_path: 검증할 파일 경로
        config: 안정성 검증 설정

    Returns:
        True if 파일이 안정적
        False if 불안정하거나 오류
    """
    if not file_path.exists():
        return False

    start_time = time.time()
    previous_size = -1

    try:
        for i in range(config.check_count):
            # 타임아웃 검사
            if time.time() - start_time > config.timeout:
                logger.warning(
                    "파일 안정성 검증 타임아웃: %s (%.1fs 초과)",
                    file_path.name, config.timeout
                )
                return False

            # 현재 파일 크기 확인
            try:
                current_size = file_path.stat().st_size
            except OSError:
                return False

            # 크기 변화 감지
            if i > 0 and current_size != previous_size:
                return False

            # 대기 (마지막 체크 제외)
            if i < config.check_count - 1:
                time.sleep(config.check_interval)

            previous_size = current_size

        return True

    except Exception:
        return False


async def wait_for_file_stability(
    file_path: Path,
    config: StabilityConfig,
    max_retries: int = 5,
    retry_delay: float = 1.0,
) -> bool:
    """
    파일이 안정될 때까지 대기 — 적응형 재시도.

    파일이 계속 변경되는 경우 일정 시간 대기 후 재검증한다.
    실시간성과 안정성의 균형을 맞춘 전략이다.

    Args:
        file_path: 검증할 파일 경로
        config: 안정성 검증 설정
        max_retries: 최대 재시도 횟수
        retry_delay: 재시도 간격 (초)

    Returns:
        True if 결국 안정화됨
        False if 최대 재시도 초과 또는 파일 없음
    """
    for attempt in range(max_retries + 1):
        try:
            if await is_file_stable_async(file_path, config):
                if attempt > 0:
                    logger.debug(
                        "파일 안정화 완료: %s (%d번째 시도)",
                        file_path.name, attempt + 1
                    )
                return True
        except FileStabilityError:
            # 검증 자체에 실패한 경우 포기
            return False

        # 마지막 시도가 아니면 재시도 대기
        if attempt < max_retries:
            logger.debug(
                "파일 아직 불안정, 재시도 대기: %s (%d/%d)",
                file_path.name, attempt + 1, max_retries + 1
            )
            await asyncio.sleep(retry_delay)

    logger.warning(
        "파일 안정화 실패: %s (최대 재시도 %d회 초과)",
        file_path.name, max_retries
    )
    return False