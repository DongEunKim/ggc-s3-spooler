"""
Pattern 2 전용 클라이언트 프로토콜 테스트.

FileTransferClient 프로토콜 준수와 Mock 격리를 검증한다:
- 모든 클라이언트가 FileTransferClient 프로토콜을 올바르게 구현하는지 확인
- Mock 클라이언트가 실제 클라이언트와 동일한 동작을 수행하는지 검증
- 런타임 타입 체크와 프로토콜 검증 기능 테스트
"""

import pytest
from pathlib import Path

from spooler.client_protocol import (
    FileTransferClient,
    verify_client_protocol,
    ClientContextManager,
    is_file_transfer_client,
    ensure_protocol_compliance
)
from spooler.stream_client import (
    S3ExportStreamManagerClient,
    AutoStreamManagerClient
)
from spooler_testing.mock_clients import (
    MockAutoStreamManagerClient,
    MockS3ExportStreamManagerClient,
)


class TestProtocolCompliance:
    """모든 클라이언트 클래스가 FileTransferClient 프로토콜을 구현하는지 검증"""

    @pytest.mark.parametrize("client_class", [
        MockAutoStreamManagerClient,
        MockS3ExportStreamManagerClient,
        S3ExportStreamManagerClient,
        AutoStreamManagerClient
    ])
    def test_client_implements_protocol(self, client_class: type) -> None:
        """각 클라이언트 클래스가 필수 메서드를 구현하는지 확인"""
        ensure_protocol_compliance(client_class)

    @pytest.mark.parametrize("client_class", [
        MockAutoStreamManagerClient,
        MockS3ExportStreamManagerClient,
    ])
    def test_mock_clients_are_protocol_instances(self, client_class: type) -> None:
        """Mock 클라이언트 인스턴스가 런타임에 프로토콜을 준수하는지 확인"""
        client = client_class("localhost", 8088, "test-bucket")
        assert isinstance(client, FileTransferClient)
        assert is_file_transfer_client(client)

    def test_protocol_verification_function(self) -> None:
        """verify_client_protocol 함수가 올바르게 동작하는지 확인"""
        client = MockAutoStreamManagerClient("localhost", 8088, "test-bucket")
        verified_client = verify_client_protocol(client)
        assert verified_client is client
        assert isinstance(verified_client, FileTransferClient)

    def test_protocol_verification_rejects_invalid_object(self) -> None:
        """프로토콜을 구현하지 않는 객체를 올바르게 거부하는지 확인"""
        invalid_client = object()
        with pytest.raises(TypeError, match="FileTransferClient 프로토콜을 구현하지 않습니다"):
            verify_client_protocol(invalid_client)

    def test_ensure_protocol_compliance_rejects_invalid_class(self) -> None:
        """프로토콜을 구현하지 않는 클래스를 올바르게 거부하는지 확인"""
        class InvalidClient:
            def connect(self) -> None: pass
            # close와 append_file 메서드 누락

        with pytest.raises(TypeError, match="필수 메서드 'close'을 구현하지 않았습니다"):
            ensure_protocol_compliance(InvalidClient)


class TestClientContextManager:
    """ClientContextManager가 안전한 클라이언트 생명주기를 제공하는지 검증"""

    def test_context_manager_with_mock_client(self) -> None:
        """Mock 클라이언트와 함께 컨텍스트 매니저 사용"""
        client = MockS3ExportStreamManagerClient("localhost", 8088, "test-bucket")

        with ClientContextManager(client) as managed_client:
            assert isinstance(managed_client, FileTransferClient)
            assert client._connected is True

        assert client._connected is False

    def test_context_manager_handles_exceptions(self) -> None:
        """컨텍스트 매니저가 예외 상황에서도 정리를 수행하는지 확인"""
        client = MockS3ExportStreamManagerClient("localhost", 8088, "test-bucket")

        try:
            with ClientContextManager(client):
                assert client._connected is True
                raise ValueError("테스트 예외")
        except ValueError:
            pass

        # 예외가 발생해도 close()가 호출되어야 함
        assert client._connected is False

    def test_context_manager_rejects_invalid_client(self) -> None:
        """컨텍스트 매니저가 잘못된 클라이언트를 거부하는지 확인"""
        invalid_client = object()
        with pytest.raises(TypeError):
            ClientContextManager(invalid_client)


class TestPattern2ClientBehavior:
    """Pattern 2 클라이언트들의 동작 검증"""

    def test_auto_client_pattern2_selection(self, tmp_path: Path) -> None:
        """MockAutoStreamManagerClient가 Pattern 2를 사용하는지 확인"""
        client = MockAutoStreamManagerClient("localhost", 8088, "test-bucket")
        client.connect()

        assert client.get_pattern() == "Pattern 2"

        test_file = tmp_path / "pattern2_test.txt"
        test_file.write_text("pattern2 data")

        client.append_file("stream", "key/path.txt", test_file)

        # Pattern 2: TaskDefinition이 기록되어야 함
        sent_data = client.sent
        assert len(sent_data) == 1
        stream, key, chunk_idx, total_chunks, task_def_bytes = sent_data[0]
        assert chunk_idx == 1
        assert total_chunks == 1
        task_def = task_def_bytes.decode()
        assert "S3ExportTaskDefinition" in task_def
        assert "test-bucket" in task_def

    def test_s3export_client_behavior(self, tmp_path: Path) -> None:
        """MockS3ExportStreamManagerClient의 기본 동작 확인"""
        client = MockS3ExportStreamManagerClient("localhost", 8088, "test-bucket")
        client.connect()

        test_file = tmp_path / "s3export_test.txt"
        test_file.write_text("s3export data")

        seq_num = client.append_file("stream", "key.txt", test_file)
        assert seq_num is not None

        # 상태 확인 기능 테스트
        success = client.check_upload_status(seq_num)
        assert isinstance(success, bool)

        # 파일이 삭제되지 않음 (수동 삭제 필요)
        assert test_file.exists()

    def test_protocol_polymorphic_usage(self, tmp_path: Path) -> None:
        """프로토콜을 통해 다형성 사용이 가능한지 확인"""
        clients: list[FileTransferClient] = [
            MockAutoStreamManagerClient("localhost", 8088, "test-bucket"),
            MockS3ExportStreamManagerClient("localhost", 8088, "test-bucket")
        ]

        for i, client in enumerate(clients):
            # 프로토콜을 통해 동일한 인터페이스 사용
            with ClientContextManager(client):
                test_file = tmp_path / f"poly_test_{i}.txt"
                test_file.write_text("polymorphic test")

                client.append_file("poly-stream", f"poly/key_{i}.txt", test_file)

    def test_pattern2_requires_bucket(self) -> None:
        """Pattern 2 클라이언트들이 s3_bucket을 필수로 요구하는지 확인"""
        with pytest.raises(ValueError, match="Pattern 2 전용 모드: s3_bucket이 필수입니다"):
            MockAutoStreamManagerClient("localhost", 8088, "")

        # S3ExportStreamManagerClient는 빈 버킷도 허용 (기본 설정)
        client = MockS3ExportStreamManagerClient("localhost", 8088, "")
        assert client is not None