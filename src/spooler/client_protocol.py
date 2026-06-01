"""
FileTransferClient Protocol — CAN Blackbox Pattern Implementation

이 모듈은 CAN Blackbox 패턴의 핵심인 Protocol 기반 인터페이스를 정의한다.
Protocol을 통해 Mock과 실제 구현체 간의 완전한 격리를 달성하며,
런타임에 타입 체크와 동적 검증을 제공한다.

CAN Blackbox 패턴의 주요 특징:
- Protocol 기반 인터페이스로 Mock과 Real 구현체 완전 격리
- 런타임 타입 체크 (isinstance(obj, Protocol) 지원)
- Mock의 동작이 실제 구현체와 정확히 일치하도록 보장
- 테스트와 프로덕션 코드의 동일한 인터페이스 사용
"""

import contextlib
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class FileTransferClient(Protocol):
    """
    파일 전송 클라이언트 프로토콜.

    이 프로토콜을 구현하는 모든 클라이언트는 동일한 인터페이스를 제공해야 한다.
    실제 Stream Manager 클라이언트와 Mock 클라이언트 모두 이 프로토콜을 준수한다.

    CAN Blackbox 패턴의 핵심: Mock이 실제 구현체와 정확히 같은 동작을 보장
    """

    def connect(self) -> None:
        """
        클라이언트 연결을 초기화한다.

        실제 구현체: Stream Manager 서버에 TCP 연결
        Mock 구현체: 연결 상태 시뮬레이션 (no-op)
        """
        ...

    def close(self) -> None:
        """
        클라이언트 연결을 종료한다.

        실제 구현체: TCP 연결 해제, 리소스 정리
        Mock 구현체: 상태 리셋 (no-op)
        """
        ...

    def append_file(self, stream_name: str, s3_key: str, file_path: Path) -> None:
        """
        파일을 스트림에 전송하고 전송 완료 후 파일을 삭제한다.

        Args:
            stream_name: 대상 스트림 이름 (Stream Manager에 미리 생성되어야 함)
            s3_key: S3 키 경로 (Pattern 1: 라우팅용, Pattern 2: 실제 S3 키)
            file_path: 전송할 파일의 경로

        Raises:
            RuntimeError: 클라이언트가 연결되지 않은 경우
            FileNotFoundError: 파일이 존재하지 않는 경우
            ValueError: 스트림 이름이나 S3 키가 잘못된 경우

        Behavior:
            - 파일을 읽어서 스트림에 전송
            - 전송 완료 후 파일 삭제 (file_path.unlink())
            - 큰 파일은 클라이언트 구현에 따라 청크 분할 처리
        """
        ...


def verify_client_protocol(client: object) -> FileTransferClient:
    """
    객체가 FileTransferClient 프로토콜을 구현하는지 런타임 검증한다.

    CAN Blackbox 패턴: 런타임에 Mock과 Real 구현체의 프로토콜 준수 보장

    Args:
        client: 검증할 객체

    Returns:
        FileTransferClient 프로토콜을 구현하는 객체

    Raises:
        TypeError: 프로토콜을 구현하지 않는 경우

    Example:
        >>> client = SomeStreamClient()
        >>> verified = verify_client_protocol(client)
        >>> verified.append_file("stream", "key", Path("file.txt"))
    """
    if not isinstance(client, FileTransferClient):
        raise TypeError(
            f"객체가 FileTransferClient 프로토콜을 구현하지 않습니다: {type(client).__name__}"
        )
    return client


class ClientContextManager:
    """
    FileTransferClient의 안전한 생명주기 관리를 위한 컨텍스트 매니저.

    CAN Blackbox 패턴: Mock과 Real 클라이언트 모두 동일한 생명주기 보장

    Example:
        >>> with ClientContextManager(client) as conn:
        ...     conn.append_file("stream", "key", Path("file.txt"))
    """

    def __init__(self, client: FileTransferClient) -> None:
        self._client = verify_client_protocol(client)

    def __enter__(self) -> FileTransferClient:
        self._client.connect()
        return self._client

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore
        with contextlib.suppress(Exception):
            self._client.close()


# 타입 검사를 위한 유틸리티 함수들
def is_file_transfer_client(obj: object) -> bool:
    """객체가 FileTransferClient 프로토콜을 구현하는지 확인"""
    return isinstance(obj, FileTransferClient)


def ensure_protocol_compliance(client_class: type) -> None:
    """
    클래스가 컴파일 타임에 FileTransferClient 프로토콜을 구현하는지 검사.

    이 함수는 주로 테스트에서 사용되어 새로운 클라이언트 구현체가
    프로토콜을 올바르게 구현했는지 확인한다.

    Args:
        client_class: 검사할 클래스 타입

    Raises:
        TypeError: 프로토콜을 구현하지 않는 경우
    """
    required_methods = ['connect', 'close', 'append_file']

    for method_name in required_methods:
        if not hasattr(client_class, method_name):
            raise TypeError(
                f"클래스 {client_class.__name__}이 필수 메서드 '{method_name}'을 구현하지 않았습니다"
            )

        method = getattr(client_class, method_name)
        if not callable(method):
            raise TypeError(
                f"클래스 {client_class.__name__}의 '{method_name}'이 호출 가능하지 않습니다"
            )