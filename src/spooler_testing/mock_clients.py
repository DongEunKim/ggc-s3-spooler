"""
테스트/벤치마크 전용 Mock Stream Manager 클라이언트 — Pattern 2 전용.

운영 패키지(`spooler`)에서 분리된 테스트 더블 모음.
실제 Stream Manager(`spooler.stream_client.S3ExportUploader`)와
동일한 인터페이스(FileTransferClient 프로토콜)를 구현하여, 개발환경에서
Stream Manager 없이 파이프라인을 검증한다.

  - MockS3ExportUploader        : 결정론적 기본 Mock
  - MockS3SpoolerClient            : 위를 감싼 자동 삭제 워크플로우 Mock
  - RealisticMockS3ExportUploader: 지연/대역폭/오류 시뮬레이션 Mock
  - RealisticMockS3SpoolerClient   : 위를 감싼 현실적 성능 시뮬레이션 Mock

⚠️  운영 코드에서 import 금지. 이 모듈은 배포 번들에 포함되지 않는다.
"""

import logging
import random
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MockS3ExportUploader:
    """
    CAN Blackbox Pattern Mock — Pattern 2 전용.

    S3ExportUploader와 정확히 동일한 동작을 시뮬레이션한다.
    """

    def __init__(self, host: str, port: int, s3_bucket: str, status_stream_name: str = "") -> None:
        self._host = host
        self._port = port
        self._s3_bucket = s3_bucket
        self._status_stream_name = status_stream_name
        self._connected = False
        self._sequence_counter = 1000

        # Pattern 2 전송 기록: [(stream_name, s3_key, sequence_number, task_definition)]
        self.sent: list[tuple[str, str, int, str]] = []

    def connect(self) -> None:
        self._connected = True
        logger.info("Stream Manager (Pattern 2) 연결 완료: %s:%d", self._host, self._port)

    def close(self) -> None:
        self._connected = False

    def append_file(self, stream_name: str, s3_key: str, file_path: Path) -> int | None:
        """Pattern 2: S3ExportTaskDefinition 시뮬레이션"""
        if not self._connected:
            raise RuntimeError("Stream Manager에 연결되지 않았습니다. connect()를 먼저 호출하세요.")

        if not self._s3_bucket:
            raise ValueError("s3_bucket 설정이 필요합니다 (Pattern 2)")

        if not file_path.exists():
            raise FileNotFoundError(f"파일이 존재하지 않습니다: {file_path}")

        try:
            # Mock TaskDefinition 생성 (실제 구현체와 유사한 형태)
            task_definition = f"S3ExportTaskDefinition(input_url=file://{file_path.absolute()}, bucket={self._s3_bucket}, key={s3_key})"

            sequence_number = self._sequence_counter
            self._sequence_counter += 1

            self.sent.append((stream_name, s3_key, sequence_number, task_definition))

            logger.info(
                "Pattern 2 전송 요청: stream=%s bucket=%s key=%s size=%d seq=%d",
                stream_name,
                self._s3_bucket,
                s3_key,
                file_path.stat().st_size,
                sequence_number,
            )

            return sequence_number

        except Exception as exc:
            logger.error("Pattern 2 전송 실패: stream=%s key=%s — %s", stream_name, s3_key, exc)
            return None

    def check_upload_status(self, sequence_number: int, timeout_seconds: float = 30.0) -> bool:
        """Mock: 항상 성공 반환 (실제 구현체와 동일한 낙관적 동작)"""
        if not self._status_stream_name:
            logger.debug("상태 스트림 없음, 성공으로 가정 (seq=%d)", sequence_number)
            return True

        # Mock 상태 확인 (실제로는 복잡한 폴링 로직)
        logger.debug("Mock 상태 확인: seq=%d → 성공", sequence_number)
        return True


class MockS3SpoolerClient:
    """
    CAN Blackbox Pattern Mock — S3SpoolerClient 완전 시뮬레이션.

    Pattern 2 전용 Stream Manager Mock 클라이언트.

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
        self._client = MockS3ExportUploader(host, port, s3_bucket, status_stream_name)
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

    @property
    def sent(self) -> list[Any]:
        """테스트용: 전송된 메시지 목록"""
        # Pattern 2에서는 기존 테스트 호환성을 위해 형식 변환
        # Pattern 2: (stream, key, seq_num, task_def) -> (stream, key, 1, 1, task_def.encode())
        return [
            (s, k, 1, 1, task_def.encode() if isinstance(task_def, str) else task_def)
            for s, k, seq, task_def in self._client.sent
        ]

    def get_pattern(self) -> str:
        """테스트용: 선택된 패턴 확인"""
        return "Pattern 2"


class RealisticMockS3ExportUploader:
    """
    실제 Stream Manager 동작을 정확히 시뮬레이션하는 Mock 클라이언트.

    네트워크 지연, 대역폭 제한, 간헐적 오류를 시뮬레이션하여
    실제 운영 환경에서의 성능 특성을 모사한다.
    """

    def __init__(self, host: str, port: int, s3_bucket: str, status_stream_name: str = "",
                 **simulation_params: Any) -> None:
        self._host = host
        self._port = port
        self._s3_bucket = s3_bucket
        self._status_stream_name = status_stream_name
        self._connected = False
        self._sequence_counter = 1000

        # 시뮬레이션 파라미터 설정
        self._network_latency_range = simulation_params.get('network_latency_range', (0.005, 0.02))  # 5-20ms
        self._bandwidth_limit_mbps = simulation_params.get('bandwidth_limit_mbps', 50.0)  # 50MB/s
        self._error_rate = simulation_params.get('error_rate', 0.001)  # 0.1% 실패율
        self._max_connection_delay = simulation_params.get('max_connection_delay', 2.0)  # 최대 연결 지연

        # 오류 시나리오 유형
        self._error_scenarios = [
            'ConnectionTimeout',
            'StreamNotFoundException',
            'TemporaryServerError',
            'NetworkError',
            'S3AccessDenied'
        ]

        # 전송 기록: [(stream_name, s3_key, sequence_number, task_definition, simulated_delay)]
        self.sent: list[tuple[str, str, int, str, float]] = []

        logger.info(
            "현실적 Mock 클라이언트 초기화: 지연=%s, 대역폭=%sMB/s, 오류율=%.3f%%",
            self._network_latency_range, self._bandwidth_limit_mbps, self._error_rate * 100
        )

    def connect(self) -> None:
        """연결 시뮬레이션 (초기 연결 지연 포함)"""
        # 연결 설정 지연 시뮬레이션
        connection_delay = random.uniform(0.1, self._max_connection_delay)
        time.sleep(connection_delay)

        self._connected = True
        logger.info("Stream Manager (현실적 Mock) 연결 완료: %s:%d (지연: %.3fs)",
                   self._host, self._port, connection_delay)

    def close(self) -> None:
        self._connected = False

    def append_file(self, stream_name: str, s3_key: str, file_path: Path) -> int | None:
        """현실적 파일 전송 시뮬레이션"""
        if not self._connected:
            raise RuntimeError("Stream Manager에 연결되지 않았습니다. connect()를 먼저 호출하세요.")

        if not self._s3_bucket:
            raise ValueError("s3_bucket 설정이 필요합니다 (Pattern 2)")

        if not file_path.exists():
            raise FileNotFoundError(f"파일이 존재하지 않습니다: {file_path}")

        # 간헐적 오류 시뮬레이션
        if random.random() < self._error_rate:
            error_type = random.choice(self._error_scenarios)
            error_msg = self._generate_simulated_error(error_type, stream_name, s3_key)
            raise RuntimeError(error_msg)

        try:
            file_size = file_path.stat().st_size

            # 네트워크 지연 시뮬레이션
            network_latency = random.uniform(*self._network_latency_range)

            # 대역폭 제한 기반 전송 시간 시뮬레이션
            bandwidth_bytes_per_sec = self._bandwidth_limit_mbps * 1024 * 1024
            transfer_time = file_size / bandwidth_bytes_per_sec if file_size > 0 else 0

            # 총 시뮬레이션 지연 (네트워크 지연 + 전송 시간)
            total_delay = network_latency + transfer_time
            time.sleep(total_delay)

            # Mock TaskDefinition 생성
            task_definition = (f"S3ExportTaskDefinition(input_url=file://{file_path.absolute()}, "
                             f"bucket={self._s3_bucket}, key={s3_key})")

            sequence_number = self._sequence_counter
            self._sequence_counter += 1

            self.sent.append((stream_name, s3_key, sequence_number, task_definition, total_delay))

            logger.info(
                "현실적 전송 시뮬레이션: stream=%s bucket=%s key=%s size=%d seq=%d delay=%.3fs",
                stream_name, self._s3_bucket, s3_key, file_size, sequence_number, total_delay
            )

            return sequence_number

        except Exception as exc:
            logger.error("현실적 전송 시뮬레이션 실패: stream=%s key=%s — %s", stream_name, s3_key, exc)
            return None

    def check_upload_status(self, sequence_number: int, timeout_seconds: float = 30.0) -> bool:
        """현실적 상태 확인 시뮬레이션"""
        if not self._status_stream_name:
            logger.debug("상태 스트림 없음, 성공으로 가정 (seq=%d)", sequence_number)
            return True

        # 상태 확인 지연 시뮬레이션 (폴링 오버헤드)
        status_check_delay = random.uniform(0.1, 0.5)
        time.sleep(status_check_delay)

        # 95% 성공률 (간헐적 S3 업로드 실패 시뮬레이션)
        success_rate = 0.95
        success = random.random() < success_rate

        logger.debug("현실적 상태 확인: seq=%d → %s (지연: %.3fs)",
                    sequence_number, "성공" if success else "실패", status_check_delay)
        return success

    def _generate_simulated_error(self, error_type: str, stream_name: str, s3_key: str) -> str:
        """다양한 현실적 오류 메시지 생성"""
        error_messages = {
            'ConnectionTimeout': f"Connection timed out while connecting to Stream Manager at {self._host}:{self._port}",
            'StreamNotFoundException': f"Stream '{stream_name}' does not exist or is not configured",
            'TemporaryServerError': "Internal server error (503): Stream Manager temporary unavailable",
            'NetworkError': "Network unreachable: Failed to reach Stream Manager endpoint",
            'S3AccessDenied': f"Access denied: Unable to upload to S3 bucket '{self._s3_bucket}' key '{s3_key}'"
        }
        return error_messages.get(error_type, f"Unknown error during {error_type}")


class RealisticMockS3SpoolerClient:
    """
    현실적 S3SpoolerClient Mock — 실제 성능 특성 시뮬레이션.

    네트워크 조건, 오류 상황, 대역폭 제한 등을 반영하여
    실제 TGU 환경에서의 성능을 예측할 수 있도록 한다.
    """

    def __init__(self, host: str, port: int, s3_bucket: str, status_stream_name: str = "",
                 **simulation_params: Any) -> None:
        if not s3_bucket:
            raise ValueError("Pattern 2 전용 모드: s3_bucket이 필수입니다")

        self._host = host
        self._port = port
        self._s3_bucket = s3_bucket
        self._status_stream_name = status_stream_name

        # 시뮬레이션 파라미터 전달
        self._simulation_params = simulation_params

        # 현실적 Pattern 2 클라이언트 생성
        self._client = RealisticMockS3ExportUploader(
            host, port, s3_bucket, status_stream_name, **simulation_params
        )
        logger.info("현실적 Pattern 2 Mock 모드: bucket=%s", s3_bucket)

    def connect(self) -> None:
        self._client.connect()

    def close(self) -> None:
        self._client.close()

    def append_file(self, stream_name: str, s3_key: str, file_path: Path) -> None:
        """현실적 파일 전송 시뮬레이션 (완전한 워크플로우)"""
        sequence_number = self._client.append_file(stream_name, s3_key, file_path)
        if sequence_number is not None:
            # 업로드 상태 확인
            success = self._client.check_upload_status(sequence_number)
            if success:
                # S3 업로드 성공 시 파일 삭제
                file_path.unlink(missing_ok=True)
                logger.info(
                    "현실적 S3 업로드 및 삭제 완료: %s → s3://%s/%s",
                    file_path.name, self._s3_bucket, s3_key
                )
            else:
                logger.error("현실적 S3 업로드 실패: %s", file_path.name)
                raise RuntimeError(f"S3 업로드 실패 (상태 확인): {file_path.name}")
        else:
            logger.error("현실적 S3 업로드 요청 실패: %s", file_path.name)
            raise RuntimeError(f"S3 업로드 요청 실패: {file_path.name}")

    @property
    def sent(self) -> list[Any]:
        """테스트용: 전송된 메시지 목록 (호환성 유지)"""
        # 기존 테스트 호환성을 위한 형식 변환
        return [
            (s, k, 1, 1, task_def.encode() if isinstance(task_def, str) else task_def)
            for s, k, seq, task_def, delay in self._client.sent
        ]

    @property
    def detailed_sent(self) -> list[tuple[str, str, int, str, float]]:
        """현실적 시뮬레이션 전용: 상세 전송 기록 (지연 시간 포함)"""
        return self._client.sent

    def get_pattern(self) -> str:
        """테스트용: 선택된 패턴 확인"""
        return "Pattern 2 (Realistic)"

    def get_simulation_stats(self) -> dict:
        """시뮬레이션 통계 요약"""
        if not self._client.sent:
            return {'total_transfers': 0}

        delays = [delay for _, _, _, _, delay in self._client.sent]
        return {
            'total_transfers': len(self._client.sent),
            'total_delay_seconds': sum(delays),
            'avg_delay_seconds': sum(delays) / len(delays),
            'min_delay_seconds': min(delays),
            'max_delay_seconds': max(delays),
            'simulation_params': self._simulation_params
        }
