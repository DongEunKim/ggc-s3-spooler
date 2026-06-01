"""런타임 설정 관리 — INI 파일, 환경변수 및 CLI 인수로 오버라이드 가능."""

from __future__ import annotations

import argparse
import configparser
import dataclasses
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SpoolerConfig:
    spool_dir: Path = Path("/var/spool/s3-spooler/spool")
    max_spool_size_mb: int = 900  # 전용 1GB 파티션 기준 (research/03 v1.1)
    file_retention_hours: int = 120  # 5일 (건설기계 주말 휴무 고려)
    poll_interval_seconds: int = 5
    stream_manager_host: str = "localhost"
    stream_manager_port: int = 8088
    log_level: str = "INFO"

    # 🆕 AWS Labs 패턴: 미완성 파일 보호 (초 단위)
    incomplete_file_delay: float = 1.0


    # A-07 Pattern 2: S3ExportTaskDefinition 설정
    # S3 버킷명 (Pattern 2 전송 대상)
    s3_bucket: str = ""
    # 업로드 완료 감지용 상태 스트림 이름
    # 빈 문자열이면 Pattern 1 사용 (append_message 직접 전송)
    status_stream_name: str = ""

    # 🆕 CAN Blackbox 패턴: 하이브리드 파일 안정성 검증 설정
    # 시간 기반 사전 필터링 대기 시간 (초)
    file_stability_wait: float = 0.1
    # 크기 기반 안정성 체크 간격 (초)
    stability_check_interval: float = 0.2
    # 연속 크기 불변 확인 횟수
    stability_check_count: int = 3
    # 최대 안정성 대기 시간 (초)
    max_stability_wait: float = 10.0
    # 안정성 검증 재시도 횟수
    stability_max_retries: int = 3
    # 재시도 간격 (초)
    stability_retry_delay: float = 0.5

    @property
    def max_spool_size_bytes(self) -> int:
        return self.max_spool_size_mb * 1024 * 1024

    def validate_pattern2_requirements(self) -> None:
        """Pattern 2 필수 설정을 검증합니다."""
        if not self.s3_bucket:
            raise ValueError(
                "Pattern 2 전용 모드: s3_bucket 설정이 필수입니다. "
                "환경변수 S3_SPOOLER_S3_BUCKET 또는 CLI --s3-bucket을 설정하세요."
            )

        if not self.status_stream_name:
            logger = logging.getLogger(__name__)
            logger.warning("status_stream_name 없음: S3 업로드 상태 확인이 제한될 수 있습니다.")

    @property
    def stability_config(self) -> "StabilityConfig":
        """파일 안정성 검증 설정을 반환한다."""
        from .file_stability import StabilityConfig
        return StabilityConfig(
            check_count=self.stability_check_count,
            check_interval=self.stability_check_interval,
            timeout=self.max_stability_wait,
        )

    @classmethod
    def from_ini(cls, ini_path: Path | str = "spooler.ini") -> SpoolerConfig:
        """INI 파일에서 설정을 로드합니다."""
        config = configparser.ConfigParser()
        ini_path = Path(ini_path)

        if not ini_path.exists():
            logging.getLogger(__name__).info("INI 파일 없음 (%s), 기본값 사용", ini_path)
            return cls()

        try:
            config.read(ini_path, encoding="utf-8")

            # 필수 섹션 검증
            required_sections = ["spooler"]
            missing_sections = [s for s in required_sections if s not in config]
            if missing_sections:
                raise ValueError(f"필수 섹션 누락: {missing_sections}")

            # CAN Blackbox 패턴: 섹션별 설정 파싱
            return cls(
                # [spooler] 섹션
                spool_dir=Path(config.get(
                    "spooler", "spool_dir", fallback="/var/spool/s3-spooler/spool"
                )),
                log_level=config.get("spooler", "log_level", fallback="INFO"),

                # [cleanup] 섹션 (선택적)
                max_spool_size_mb=config.getint("cleanup", "max_spool_size_mb", fallback=900) if config.has_section("cleanup") else 900,
                file_retention_hours=config.getint("cleanup", "file_retention_hours", fallback=24) if config.has_section("cleanup") else 24,
                poll_interval_seconds=config.getint("cleanup", "poll_interval_seconds", fallback=5) if config.has_section("cleanup") else 5,

                # [stability] 섹션 - 하이브리드 안정성 검증 (선택적)
                file_stability_wait=config.getfloat(
                    "stability", "file_stability_wait", fallback=0.1
                ) if config.has_section("stability") else 0.1,
                stability_check_interval=config.getfloat(
                    "stability", "stability_check_interval", fallback=0.2
                ) if config.has_section("stability") else 0.2,
                stability_check_count=config.getint("stability", "stability_check_count", fallback=3) if config.has_section("stability") else 3,
                max_stability_wait=config.getfloat("stability", "max_stability_wait", fallback=10.0) if config.has_section("stability") else 10.0,

                # [stream_manager] 섹션 (선택적)
                stream_manager_host=config.get("stream_manager", "host", fallback="localhost") if config.has_section("stream_manager") else "localhost",
                stream_manager_port=config.getint("stream_manager", "port", fallback=8088) if config.has_section("stream_manager") else 8088,

                # 기존 설정 (하위 호환성)
                incomplete_file_delay=1.0,  # deprecated

                # [s3_export] 섹션 (선택적)
                s3_bucket=(
                    config.get("s3_export", "bucket", fallback="")
                    if config.has_section("s3_export") else ""
                ),
                status_stream_name=(
                    config.get("s3_export", "status_stream_name", fallback="")
                    if config.has_section("s3_export") else ""
                ),

            )

        except Exception as e:
            raise ValueError(f"INI 파일 파싱 오류 ({ini_path}): {e}") from e

    @classmethod
    def from_env_override(cls, base_config: 'SpoolerConfig') -> 'SpoolerConfig':
        """환경변수로 핵심 설정을 오버라이드합니다."""
        env_mapping = {
            'S3_SPOOLER_S3_BUCKET': 's3_bucket',
            'S3_SPOOLER_RETENTION_HOURS': 'file_retention_hours',
            'S3_SPOOLER_MAX_SIZE_MB': 'max_spool_size_mb',
            'S3_SPOOLER_SM_HOST': 'stream_manager_host',
            'S3_SPOOLER_SM_PORT': 'stream_manager_port',
            'S3_SPOOLER_STATUS_STREAM': 'status_stream_name',
            'S3_SPOOLER_LOG_LEVEL': 'log_level',
        }

        overrides = {}
        for env_var, config_attr in env_mapping.items():
            value = os.environ.get(env_var)
            if value is not None:
                overrides[config_attr] = cls._convert_env_value(config_attr, value)

        return dataclasses.replace(base_config, **overrides)

    @staticmethod
    def _convert_env_value(attr_name: str, value: str) -> Any:
        """환경변수 값을 적절한 타입으로 변환합니다."""
        if attr_name.endswith('_mb') or attr_name.endswith('_port'):
            return int(value)
        elif attr_name.endswith('_hours'):
            return int(value)
        elif attr_name.endswith('_delay') or attr_name.endswith('_interval'):
            return float(value)
        return value

    @classmethod
    def from_args(
        cls, args: argparse.Namespace, ini_path: Path | str | None = None
    ) -> SpoolerConfig:
        """CLI 인수에서 설정을 로드합니다.

        우선순위: CLI > 환경변수 > INI > 기본값
        """
        # 1. INI 파일에서 기본값 로드 (있으면)
        if ini_path is not None:
            base_config = cls.from_ini(ini_path)
        elif hasattr(args, "config") and args.config:
            base_config = cls.from_ini(args.config)
        else:
            # 기본 INI 파일 위치들 시도 - 시스템 경로 우선
            for default_path in ["/etc/ggc-s3-spooler/spooler.ini", "spooler.ini"]:
                if Path(default_path).exists():
                    base_config = cls.from_ini(default_path)
                    break
            else:
                base_config = cls()

        # 2. 환경변수로 오버라이드
        env_config = cls.from_env_override(base_config)

        # 3. CLI 인수로 최종 오버라이드 (우선순위 최고)
        spool_dir = Path(args.spool_dir)
        return cls(
            spool_dir=spool_dir,
            max_spool_size_mb=args.max_size_mb,
            file_retention_hours=args.retention_hours,
            poll_interval_seconds=args.poll_interval,
            stream_manager_host=args.sm_host,
            stream_manager_port=args.sm_port,
            log_level=args.log_level,
            s3_bucket=getattr(args, "s3_bucket", env_config.s3_bucket),
            status_stream_name=getattr(args, "status_stream_name", env_config.status_stream_name),
            incomplete_file_delay=getattr(args, "incomplete_file_delay", env_config.incomplete_file_delay),
            # 하이브리드 안정성 검증 설정 (CLI에서 오버라이드 불가 - INI 전용)
            file_stability_wait=env_config.file_stability_wait,
            stability_check_interval=env_config.stability_check_interval,
            stability_check_count=env_config.stability_check_count,
            max_stability_wait=env_config.max_stability_wait,
        )
