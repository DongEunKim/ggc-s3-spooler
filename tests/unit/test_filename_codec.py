"""filename_codec 단위 테스트."""

import pytest

from spooler.filename_codec import decode, encode, is_spool_file


class TestEncode:
    def test_basic(self) -> None:
        result = encode("telemetry", "data/device-1/sensor.json")
        parts = result.split("__")
        assert len(parts) == 2
        assert parts[0] == "telemetry"
        assert parts[1] == "data!device-1!sensor.json"

    def test_roundtrip(self) -> None:
        stream_id = "my-stream"
        s3_key = "prefix/subdir/file.csv"
        spool_name = encode(stream_id, s3_key)
        meta = decode(spool_name)
        assert meta.stream_id == stream_id
        assert meta.s3_key == s3_key
        assert meta.original_name == "file.csv"

    def test_stream_id_with_separator_raises(self) -> None:
        with pytest.raises(ValueError, match="구분자"):
            encode("bad__id", "key/file.txt")

    def test_empty_s3_key_name_raises(self) -> None:
        # Path("/").name == '' → ValueError 발생
        with pytest.raises(ValueError):
            encode("stream", "/")

    def test_stream_id_empty_raises(self) -> None:
        """빈 stream_id → ValueError."""
        with pytest.raises(ValueError, match="빈 문자열"):
            encode("", "key/file.txt")

    def test_stream_id_uppercase_raises(self) -> None:
        """대문자로 시작 → ValueError."""
        with pytest.raises(ValueError, match="명명 규칙"):
            encode("Telemetry", "key/file.txt")

    def test_stream_id_starts_with_digit_raises(self) -> None:
        """숫자로 시작 → ValueError."""
        with pytest.raises(ValueError, match="명명 규칙"):
            encode("1-sensor", "key/file.txt")

    def test_stream_id_starts_with_hyphen_raises(self) -> None:
        """하이픈으로 시작 → ValueError."""
        with pytest.raises(ValueError, match="명명 규칙"):
            encode("-stream", "key/file.txt")

    def test_stream_id_starts_with_underscore_raises(self) -> None:
        """언더스코어로 시작 → ValueError."""
        with pytest.raises(ValueError, match="명명 규칙"):
            encode("_stream", "key/file.txt")

    def test_stream_id_too_long_raises(self) -> None:
        """65자 초과 → ValueError."""
        long_id = "a" * 65
        with pytest.raises(ValueError, match="이하여야"):
            encode(long_id, "key/file.txt")

    def test_stream_id_space_raises(self) -> None:
        """공백 포함 → ValueError."""
        with pytest.raises(ValueError, match="명명 규칙"):
            encode("my stream", "key/file.txt")

    def test_valid_stream_ids(self) -> None:
        """유효한 stream_id들 인코딩."""
        valid_ids = ["telemetry", "device-001", "sensor_data", "camera-front-1"]
        for stream_id in valid_ids:
            result = encode(stream_id, "path/file.txt")
            assert result.startswith(stream_id + "__")


class TestDecode:
    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="유효하지 않은"):
            decode("no-separator-here.txt")

    def test_unicode_s3_key(self) -> None:
        s3_key = "폴더/파일.json"
        spool_name = encode("stream1", s3_key)
        meta = decode(spool_name)
        assert meta.s3_key == s3_key


class TestIsSpoolFile:
    def test_valid(self) -> None:
        name = encode("stream", "path/file.txt")
        assert is_spool_file(name) is True

    def test_plain_file(self) -> None:
        assert is_spool_file("plain_file.txt") is False
