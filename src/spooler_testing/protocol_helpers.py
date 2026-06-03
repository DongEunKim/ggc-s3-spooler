"""
FileTransferClient 프로토콜 검증 헬퍼 — 테스트 전용.

운영 코드는 `spooler.client_protocol.FileTransferClient` 프로토콜만 사용하며,
아래 런타임 검증/컨텍스트 매니저 유틸리티는 테스트에서만 쓰이므로
운영 패키지가 아닌 이 테스트 전용 패키지에 둔다 (배포 번들 제외).

⚠️  운영 코드에서 import 금지.
"""

import contextlib
from types import TracebackType

from spooler.client_protocol import FileTransferClient


def verify_client_protocol(client: object) -> FileTransferClient:
    """
    객체가 FileTransferClient 프로토콜을 구현하는지 런타임 검증한다.

    Raises:
        TypeError: 프로토콜을 구현하지 않는 경우
    """
    if not isinstance(client, FileTransferClient):
        raise TypeError(
            f"객체가 FileTransferClient 프로토콜을 구현하지 않습니다: {type(client).__name__}"
        )
    return client


class ClientContextManager:
    """
    FileTransferClient의 안전한 생명주기 관리를 위한 컨텍스트 매니저.

    Example:
        >>> with ClientContextManager(client) as conn:
        ...     conn.append_file("stream", "key", Path("file.txt"))
    """

    def __init__(self, client: FileTransferClient) -> None:
        self._client = verify_client_protocol(client)

    def __enter__(self) -> FileTransferClient:
        self._client.connect()
        return self._client

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        with contextlib.suppress(Exception):
            self._client.close()


def is_file_transfer_client(obj: object) -> bool:
    """객체가 FileTransferClient 프로토콜을 구현하는지 확인한다."""
    return isinstance(obj, FileTransferClient)


def ensure_protocol_compliance(client_class: type) -> None:
    """
    클래스가 FileTransferClient 프로토콜의 필수 메서드를 구현하는지 검사한다.

    Raises:
        TypeError: 프로토콜을 구현하지 않는 경우
    """
    required_methods = ['connect', 'close', 'append_file']

    for method_name in required_methods:
        if not hasattr(client_class, method_name):
            raise TypeError(
                f"클래스 {client_class.__name__}이 "
                f"필수 메서드 '{method_name}'을 구현하지 않았습니다"
            )

        method = getattr(client_class, method_name)
        if not callable(method):
            raise TypeError(
                f"클래스 {client_class.__name__}의 '{method_name}'이 호출 가능하지 않습니다"
            )
