"""
FileTransferClient Protocol — CAN Blackbox Pattern Implementation

이 모듈은 CAN Blackbox 패턴의 핵심인 Protocol 기반 인터페이스를 정의한다.
Protocol을 통해 Mock과 실제 구현체 간의 완전한 격리를 달성하며,
런타임에 타입 체크 (isinstance(obj, Protocol)) 를 지원한다.

프로토콜 검증/컨텍스트 매니저 등 테스트 전용 헬퍼는
`spooler_testing.protocol_helpers` 로 분리되어 있다 (배포 번들 제외).
"""

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

