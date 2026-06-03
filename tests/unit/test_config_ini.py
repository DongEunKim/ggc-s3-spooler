"""INI 설정 파일 기능 테스트."""

import argparse
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest

from spooler.config import SpoolerConfig


class TestSpoolerConfigINI:
    """SpoolerConfig INI 파일 지원 테스트."""

    def test_from_ini_nonexistent_file(self):
        """존재하지 않는 INI 파일 처리 — 기본값 사용."""
        config = SpoolerConfig.from_ini("nonexistent.ini")

        assert config.spool_dir == Path("/var/spool/s3-spooler/spool")
        assert config.max_spool_size_mb == 900
        assert config.log_level == "INFO"

    def test_from_ini_valid_file(self):
        """유효한 INI 파일 파싱."""
        ini_content = """
[spooler]
spool_dir = /custom/spool
log_level = DEBUG

[cleanup]
max_spool_size_mb = 1024
file_retention_hours = 48
poll_interval_seconds = 10

[stability]
file_stability_wait = 0.5
stability_check_interval = 0.5
stability_check_count = 5
max_stability_wait = 20.0

[stream_manager]
host = remote-host
port = 9088

[s3_export]
bucket = my-bucket
status_stream_name = status-stream
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False) as f:
            f.write(ini_content)
            f.flush()

            try:
                config = SpoolerConfig.from_ini(f.name)

                # [spooler] 섹션 검증
                assert config.spool_dir == Path("/custom/spool")
                assert config.log_level == "DEBUG"

                # [cleanup] 섹션 검증
                assert config.max_spool_size_mb == 1024
                assert config.file_retention_hours == 48
                assert config.poll_interval_seconds == 10

                # [stability] 섹션 검증 (하이브리드 안정성 필드)
                assert config.file_stability_wait == 0.5
                assert config.stability_check_interval == 0.5
                assert config.stability_check_count == 5
                assert config.max_stability_wait == 20.0

                # [stream_manager] 섹션 검증
                assert config.stream_manager_host == "remote-host"
                assert config.stream_manager_port == 9088

                # [s3_export] 섹션 검증
                assert config.s3_bucket == "my-bucket"
                assert config.status_stream_name == "status-stream"


            finally:
                Path(f.name).unlink()

    def test_from_ini_missing_required_sections(self):
        """필수 섹션 누락 시 오류 처리."""
        ini_content = """
# [spooler] 섹션 누락 - 이제 유일한 필수 섹션
[cleanup]
max_spool_size_mb = 1024
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False) as f:
            f.write(ini_content)
            f.flush()

            try:
                with pytest.raises(ValueError, match="필수 섹션 누락.*spooler"):
                    SpoolerConfig.from_ini(f.name)
            finally:
                Path(f.name).unlink()

    def test_from_ini_optional_sections(self):
        """선택적 섹션 누락 시 기본값 사용."""
        ini_content = """
[spooler]
spool_dir = /custom/spool

# [cleanup], [stability], [stream_manager], [s3_export], [internal] 섹션 누락
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False) as f:
            f.write(ini_content)
            f.flush()

            try:
                config = SpoolerConfig.from_ini(f.name)

                # 기본값 검증
                assert config.s3_bucket == ""
                assert config.status_stream_name == ""
                assert config.stability_check_count == 3  # 기본값
                assert config.file_stability_wait == 0.1  # 새 하이브리드 필드 기본값
                assert config.max_spool_size_mb == 900  # cleanup 섹션 없을 때 기본값

            finally:
                Path(f.name).unlink()

    def test_from_ini_malformed_file(self):
        """잘못된 INI 파일 처리."""
        ini_content = """
[spooler
# 닫는 브래킷 누락
spool_dir = /custom/spool
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False) as f:
            f.write(ini_content)
            f.flush()

            try:
                with pytest.raises(ValueError, match="INI 파일 파싱 오류"):
                    SpoolerConfig.from_ini(f.name)
            finally:
                Path(f.name).unlink()

    def test_from_ini_invalid_data_types(self):
        """잘못된 데이터 타입 처리."""
        ini_content = """
[spooler]
spool_dir = /custom/spool

[cleanup]
max_spool_size_mb = invalid_number

[stability]
file_stability_wait = 0.1

[stream_manager]
host = localhost
port = 8088
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False) as f:
            f.write(ini_content)
            f.flush()

            try:
                with pytest.raises(ValueError, match="INI 파일 파싱 오류"):
                    SpoolerConfig.from_ini(f.name)
            finally:
                Path(f.name).unlink()


class TestSpoolerConfigCLIOverride:
    """CLI 오버라이드 기능 테스트."""

    def test_cli_overrides_ini(self):
        """CLI 파라미터가 INI 설정을 오버라이드."""
        # Mock argparse.Namespace
        class MockArgs:
            def __init__(self):
                self.config = None
                self.spool_dir = "/cli/spool"
                self.max_size_mb = 2048
                self.retention_hours = 72
                self.poll_interval = 15
                self.sm_host = "cli-host"
                self.sm_port = 7088
                self.log_level = "WARNING"
                self.s3_bucket = "cli-bucket"
                self.status_stream_name = "cli-status"
                self.incomplete_file_delay = 3.0

        # INI 파일 생성
        ini_content = """
[spooler]
spool_dir = /ini/spool
log_level = DEBUG

[cleanup]
max_spool_size_mb = 1024
file_retention_hours = 48
poll_interval_seconds = 10

[stability]
file_stability_wait = 0.5
stability_check_count = 5
stability_check_interval = 1.0
max_stability_wait = 15.0

[stream_manager]
host = ini-host
port = 9088

[s3_export]
bucket = ini-bucket
status_stream_name = ini-status
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False) as f:
            f.write(ini_content)
            f.flush()

            try:
                args = MockArgs()
                config = SpoolerConfig.from_args(args, ini_path=f.name)

                # CLI 값이 우선되는지 검증
                assert config.spool_dir == Path("/cli/spool")
                assert config.max_spool_size_mb == 2048
                assert config.file_retention_hours == 72
                assert config.poll_interval_seconds == 15
                assert config.stream_manager_host == "cli-host"
                assert config.stream_manager_port == 7088
                assert config.log_level == "WARNING"
                assert config.s3_bucket == "cli-bucket"
                assert config.status_stream_name == "cli-status"
                assert config.incomplete_file_delay == 3.0

                # INI에만 있는 값은 INI에서 가져오는지 검증 (하이브리드 안정성 필드)
                assert config.stability_check_count == 5
                assert config.stability_check_interval == 1.0
                assert config.file_stability_wait == 0.5
                assert config.max_stability_wait == 15.0

            finally:
                Path(f.name).unlink()

    def test_cli_without_ini(self):
        """INI 파일 없이 CLI만 사용."""
        class MockArgs:
            def __init__(self):
                self.config = None
                self.spool_dir = "/cli/spool"
                self.max_size_mb = 2048
                self.retention_hours = 72
                self.poll_interval = 15
                self.sm_host = "cli-host"
                self.sm_port = 7088
                self.log_level = "WARNING"
                self.s3_bucket = "cli-bucket"
                self.status_stream_name = "cli-status"
                self.incomplete_file_delay = 3.0

        # INI 파일 없음을 보장
        with patch('pathlib.Path.exists', return_value=False):
            args = MockArgs()
            config = SpoolerConfig.from_args(args)

            # CLI 값 검증
            assert config.spool_dir == Path("/cli/spool")
            assert config.max_spool_size_mb == 2048
            assert config.stream_manager_host == "cli-host"

            # 기본값 검증 (하이브리드 안정성 필드)
            assert config.stability_check_count == 3
            assert config.stability_check_interval == 0.2
            assert config.file_stability_wait == 0.1
            assert config.max_stability_wait == 10.0

    def test_default_ini_file_detection(self):
        """기본 INI 파일 위치 자동 감지."""
        class MockArgs:
            def __init__(self):
                self.config = None
                self.spool_dir = "/cli/spool"
                self.max_size_mb = 2048
                self.retention_hours = 72
                self.poll_interval = 15
                self.sm_host = "cli-host"
                self.sm_port = 7088
                self.log_level = "WARNING"
                self.s3_bucket = ""
                self.status_stream_name = ""
                self.incomplete_file_delay = 3.0

        # spooler.ini 존재를 시뮬레이션
        def mock_exists(self):
            return str(self) == "spooler.ini"

        with patch.object(Path, 'exists', mock_exists):
            with patch.object(SpoolerConfig, 'from_ini') as mock_from_ini:
                mock_from_ini.return_value = SpoolerConfig(stability_check_count=7)

                args = MockArgs()
                config = SpoolerConfig.from_args(args)

                # from_ini가 spooler.ini로 호출되었는지 확인
                mock_from_ini.assert_called_once_with("spooler.ini")


class TestConfigIntegration:
    """설정 시스템 통합 테스트."""

    def test_max_spool_size_bytes_property(self):
        """max_spool_size_bytes 속성 계산."""
        config = SpoolerConfig(max_spool_size_mb=1024)
        assert config.max_spool_size_bytes == 1024 * 1024 * 1024

    def test_new_stability_fields_defaults(self):
        """새 안정성 필드 기본값 검증 (하이브리드 패턴)."""
        config = SpoolerConfig()
        assert config.stability_check_count == 3
        assert config.stability_check_interval == 0.2
        assert config.file_stability_wait == 0.1
        assert config.max_stability_wait == 10.0
        assert config.incomplete_file_delay == 1.0  # deprecated, 하위 호환성


class TestEnvOverridePrecedence:
    """우선순위 CLI > 환경변수 > INI > 기본값 검증 (센티넬 기반)."""

    @staticmethod
    def _daemon_args(**overrides: object) -> argparse.Namespace:
        """실제 argparse 출력처럼 모든 데몬 인수를 None 으로 채운 Namespace."""
        base: dict[str, object] = {
            "config": None,
            "spool_dir": None,
            "max_size_mb": None,
            "retention_hours": None,
            "poll_interval": None,
            "sm_host": None,
            "sm_port": None,
            "log_level": None,
            "s3_bucket": None,
            "status_stream_name": None,
            "incomplete_file_delay": None,
            "file_stability_wait": None,
            "stability_check_interval": None,
            "stability_check_count": None,
            "max_stability_wait": None,
            "stability_max_retries": None,
            "stability_retry_delay": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_env_used_when_cli_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI 미지정 시 환경변수가 적용된다 (이전엔 무시되던 버그)."""
        monkeypatch.setenv("S3_SPOOLER_S3_BUCKET", "env-bucket")
        with patch.object(Path, "exists", return_value=False):
            config = SpoolerConfig.from_args(self._daemon_args())
        assert config.s3_bucket == "env-bucket"

    def test_cli_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI 인수가 환경변수보다 우선한다."""
        monkeypatch.setenv("S3_SPOOLER_S3_BUCKET", "env-bucket")
        with patch.object(Path, "exists", return_value=False):
            config = SpoolerConfig.from_args(self._daemon_args(s3_bucket="cli-bucket"))
        assert config.s3_bucket == "cli-bucket"

    def test_default_when_neither(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI·환경변수·INI 모두 없으면 dataclass 기본값을 사용한다."""
        monkeypatch.delenv("S3_SPOOLER_S3_BUCKET", raising=False)
        with patch.object(Path, "exists", return_value=False):
            config = SpoolerConfig.from_args(self._daemon_args())
        assert config.s3_bucket == ""
        assert config.spool_dir == Path("/var/spool/s3-spooler/spool")