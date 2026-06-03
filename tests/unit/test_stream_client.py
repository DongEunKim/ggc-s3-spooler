"""Pattern 2 (S3ExportTaskDefinition) StreamManagerClient 단위 테스트."""

from pathlib import Path

import pytest

from spooler_testing.mock_clients import MockAutoStreamManagerClient


@pytest.fixture
def pattern2_client() -> MockAutoStreamManagerClient:
    """Pattern 2 전용 클라이언트"""
    c = MockAutoStreamManagerClient("localhost", 8088, "test-bucket", "status")
    c.connect()
    return c


class TestAutoStreamManagerClient:
    def test_pattern2_requires_s3_bucket(self) -> None:
        """s3_bucket이 없으면 ValueError 발생"""
        with pytest.raises(ValueError, match="Pattern 2 전용 모드: s3_bucket이 필수입니다"):
            MockAutoStreamManagerClient("localhost", 8088, "", "")

    def test_pattern2_selection_with_s3_bucket(
        self, tmp_path: Path, pattern2_client: MockAutoStreamManagerClient
    ) -> None:
        """s3_bucket이 설정되면 Pattern 2 선택"""
        p = tmp_path / "test.bin"
        p.write_bytes(b"test data for pattern2")

        pattern2_client.append_file("stream", "key/test.bin", p)

        # Pattern 2: S3ExportTaskDefinition 메시지 (크기와 무관하게 단일 메시지)
        assert len(pattern2_client.sent) == 1
        _, _, chunk_idx, total_chunks, data = pattern2_client.sent[0]
        assert chunk_idx == 1
        assert total_chunks == 1
        # Pattern 2 메시지는 TaskDefinition JSON (실제 파일 내용 아님)
        assert b"S3ExportTaskDefinition" in data
        assert b"test-bucket" in data
        # 파일이 삭제되어야 함
        assert not p.exists()

    def test_pattern2_large_file_single_message(
        self, tmp_path: Path, pattern2_client: MockAutoStreamManagerClient
    ) -> None:
        """Pattern 2에서는 파일 크기와 무관하게 단일 메시지"""
        large_data = b"x" * (100 * 1024 * 1024)  # 100MB
        p = tmp_path / "large.bin"
        p.write_bytes(large_data)

        pattern2_client.append_file("stream", "key/large.bin", p)

        # Pattern 2: 파일 크기와 무관하게 단일 TaskDefinition 메시지
        assert len(pattern2_client.sent) == 1
        _, _, chunk_idx, total_chunks, data = pattern2_client.sent[0]
        assert chunk_idx == 1
        assert total_chunks == 1
        # TaskDefinition 메시지는 작음 (~100 bytes)
        assert len(data) < 1000
        assert not p.exists()

    def test_pattern2_maintains_s3_key_control(
        self, tmp_path: Path, pattern2_client: MockAutoStreamManagerClient
    ) -> None:
        """Pattern 2에서 s3_key가 정확히 전달되는지 확인"""
        p = tmp_path / "controlled.txt"
        p.write_text("controlled content")

        pattern2_client.append_file("my-stream", "exact/path/controlled.txt", p)

        assert len(pattern2_client.sent) == 1
        stream_name, s3_key, _, _, data = pattern2_client.sent[0]
        assert stream_name == "my-stream"
        assert s3_key == "exact/path/controlled.txt"
        # Mock TaskDefinition에 버킷과 키가 포함되어야 함
        task_data = data.decode()
        assert "test-bucket" in task_data
        assert "exact/path/controlled.txt" in task_data
        assert not p.exists()

    def test_pattern_verification(
        self, pattern2_client: MockAutoStreamManagerClient
    ) -> None:
        """선택된 패턴이 Pattern 2인지 확인"""
        assert pattern2_client.get_pattern() == "Pattern 2"
