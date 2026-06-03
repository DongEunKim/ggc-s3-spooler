"""
Stream Manager 클라이언트 래퍼 — Pattern 2 전용 (S3ExportTaskDefinition).

Pattern 2 특징:
  - S3ExportTaskDefinition을 사용한 S3 직접 업로드
  - per-file S3 키 완전 제어 가능
  - 파일 크기 제한 없음 (SM이 S3 멀티파트 업로드 처리)
  - 상태 스트림을 통한 업로드 완료 확인

CAN Blackbox Pattern 적용:
  - 모든 클라이언트는 FileTransferClient 프로토콜 구현

테스트 더블(Mock)은 운영 배포 비오염을 위해 `spooler_testing.mock_clients`로 분리되어 있다.
"""

import contextlib
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)



class S3ExportStreamManagerClient:
    """
    Pattern 2: S3ExportTaskDefinition 기반 Stream Manager 클라이언트.

    per-file S3 키 제어와 파일 크기 제한 없는 전송을 제공한다.
    상태 스트림을 통해 업로드 완료를 확인할 수 있다.

    CAN Blackbox Pattern: FileTransferClient 프로토콜 구현
    """

    def __init__(self, host: str, port: int, s3_bucket: str, status_stream_name: str = "") -> None:
        self._host = host
        self._port = port
        self._s3_bucket = s3_bucket
        self._status_stream_name = status_stream_name
        self._client: Any = None

    def connect(self) -> None:
        from stream_manager import StreamManagerClient as _Client

        self._client = _Client(host=self._host, port=self._port)
        logger.info("Stream Manager (Pattern 2) 연결 완료: %s:%d", self._host, self._port)

    def close(self) -> None:
        if self._client:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None

    def append_file(self, stream_name: str, s3_key: str, file_path: Path) -> int | None:
        """
        Pattern 2: S3ExportTaskDefinition으로 파일을 S3에 직접 업로드한다.

        Returns:
            message sequence number (상태 추적용), 또는 None (실패 시)
        """
        if self._client is None:
            raise RuntimeError("Stream Manager에 연결되지 않았습니다. connect()를 먼저 호출하세요.")

        if not self._s3_bucket:
            raise ValueError("s3_bucket 설정이 필요합니다 (Pattern 2)")

        try:
            from stream_manager.data import S3ExportTaskDefinition
            from stream_manager.util import Util

            task = S3ExportTaskDefinition(
                input_url=f"file://{file_path.absolute()}",
                bucket=self._s3_bucket,
                key=s3_key
            )

            # JSON 직렬화하여 스트림에 전송
            task_bytes = Util.validate_and_serialize_to_json_bytes(task)
            sequence_number = self._client.append_message(stream_name, task_bytes)

            logger.info(
                "Pattern 2 전송 요청: stream=%s bucket=%s key=%s size=%d seq=%d",
                stream_name,
                self._s3_bucket,
                s3_key,
                file_path.stat().st_size,
                sequence_number,
            )

            return int(sequence_number)

        except Exception as exc:
            logger.error("Pattern 2 전송 실패: stream=%s key=%s — %s", stream_name, s3_key, exc)
            return None

    def check_upload_status(self, sequence_number: int, timeout_seconds: float = 30.0) -> bool:
        """
        상태 스트림을 확인하여 업로드 완료 여부를 반환한다.

        Args:
            sequence_number: append_file()에서 반환된 시퀀스 번호
            timeout_seconds: 최대 대기 시간

        Returns:
            True if upload succeeded, False if failed or timeout
        """
        if not self._status_stream_name or self._client is None:
            # 상태 확인 불가 → 낙관적 성공 가정
            logger.debug("상태 스트림 없음, 성공으로 가정 (seq=%d)", sequence_number)
            return True

        try:
            from stream_manager.data import ReadMessagesOptions

            start_time = time.time()

            while (time.time() - start_time) < timeout_seconds:
                try:
                    # 상태 스트림에서 메시지 읽기
                    messages = self._client.read_messages(
                        self._status_stream_name,
                        ReadMessagesOptions(
                            desired_start_sequence_number=0,  # 처음부터 읽기
                            read_timeout_millis=1000,
                            min_message_count=1,
                            max_message_count=10
                        )
                    )

                    for message in messages:
                        # StatusMessage 파싱하여 해당 시퀀스 번호 확인
                        if self._check_status_message(message, sequence_number):
                            return True

                except Exception as e:
                    logger.debug("상태 스트림 읽기 오류: %s", e)

                time.sleep(0.5)  # 500ms 간격으로 폴링

            logger.warning("업로드 상태 확인 타임아웃 (seq=%d)", sequence_number)
            return False

        except Exception as exc:
            logger.error("상태 확인 실패 (seq=%d): %s", sequence_number, exc)
            return False

    def _check_status_message(self, message: object, target_sequence: int) -> bool:
        """StatusMessage를 파싱하여 대상 시퀀스의 성공 여부를 확인한다."""
        try:
            from stream_manager.util import Util
            from stream_manager.data import StatusMessage, Status

            status_msg = Util.deserialize_json_bytes_to_obj(message.payload, StatusMessage)
            if status_msg.status_context and status_msg.status_context.sequence_number == target_sequence:
                return status_msg.status == Status.Success
        except Exception:
            pass
        return False




class AutoStreamManagerClient:
    """
    Pattern 2 전용 Stream Manager 클라이언트.

    S3ExportTaskDefinition을 사용하여 파일 크기 제한 없는 전송과
    per-file S3 키 제어를 제공합니다.

    CAN Blackbox Pattern: FileTransferClient 프로토콜 구현
    """

    def __init__(
        self, host: str, port: int, s3_bucket: str, status_stream_name: str = ""
    ) -> None:
        if not s3_bucket:
            raise ValueError("Pattern 2 전용 모드: s3_bucket이 필수입니다")

        self._host = host
        self._port = port
        self._s3_bucket = s3_bucket
        self._status_stream_name = status_stream_name

        # Pattern 2 전용
        self._client = S3ExportStreamManagerClient(host, port, s3_bucket, status_stream_name)
        logger.info("Pattern 2 전용 모드: bucket=%s", s3_bucket)

    def connect(self) -> None:
        self._client.connect()

    def close(self) -> None:
        self._client.close()

    def append_file(self, stream_name: str, s3_key: str, file_path: Path) -> None:
        """
        Pattern 2: S3ExportTaskDefinition으로 파일을 S3에 직접 업로드합니다.

        파일 크기 제한 없이 전송하며, per-file S3 키 제어가 가능합니다.
        업로드 완료 확인 후 파일을 삭제합니다.
        """
        sequence_number = self._client.append_file(stream_name, s3_key, file_path)
        if sequence_number is not None:
            # 업로드 상태 확인
            success = self._client.check_upload_status(sequence_number)
            if success:
                # S3 업로드 성공 시 파일 삭제
                file_path.unlink(missing_ok=True)
                logger.info(
                    "S3 업로드 및 삭제 완료: %s → s3://%s/%s",
                    file_path.name,
                    self._s3_bucket,
                    s3_key
                )
            else:
                logger.error("S3 업로드 실패: %s", file_path.name)
                raise RuntimeError(f"S3 업로드 실패: {file_path.name}")
        else:
            logger.error("S3 업로드 요청 실패: %s", file_path.name)
            raise RuntimeError(f"S3 업로드 요청 실패: {file_path.name}")

