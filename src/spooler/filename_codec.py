"""스풀 파일명 인코딩/디코딩 — 파일명 만으로 stream, s3 경로, 원본 파일명 식별."""

import re
from dataclasses import dataclass
from pathlib import Path

SEP = "__"

# stream_name 명명 규칙 (클라이언트 계약)
# 형식: {process_name}[-{sequence}]
# - 소문자 영문자로 시작
# - 소문자·숫자·하이픈·언더스코어 포함 가능
# - 금지: 대문자, 이중 언더스코어, 공백, 특수문자
STREAM_ID_PATTERN = re.compile(r"^[a-z][a-z0-9\-_]*$")
STREAM_ID_MAX_LEN = 64


@dataclass(frozen=True)
class SpoolFileMeta:
    stream_id: str
    s3_key: str  # S3 전체 키 (prefix + original_filename 포함)
    original_name: str  # S3에 저장될 실제 파일명 (s3_key의 마지막 세그먼트)


def encode(stream_id: str, s3_key: str) -> str:
    """
    S3 키와 스트림 ID를 스풀 파일명으로 인코딩한다.

    반환값은 실제 파일이 스풀 디렉토리에 기록될 때의 파일명이 된다.

    예) stream_id="telemetry", s3_key="data/device-1/sensor.json"
        → "telemetry__data!device-1!sensor.json"
    """
    if not stream_id:
        raise ValueError("stream_id는 빈 문자열일 수 없습니다.")
    if len(stream_id) > STREAM_ID_MAX_LEN:
        raise ValueError(
            f"stream_id는 {STREAM_ID_MAX_LEN}자 이하여야 합니다. (현재: {len(stream_id)}자)"
        )
    if not STREAM_ID_PATTERN.match(stream_id):
        raise ValueError(
            f"stream_id '{stream_id}'는 명명 규칙을 위반합니다. "
            "규칙: 소문자로 시작하고, 소문자·숫자·하이픈·언더스코어만 포함"
        )
    if SEP in stream_id:
        raise ValueError(f"stream_id에 구분자({SEP!r})를 포함할 수 없습니다.")
    original_name = Path(s3_key).name
    if not original_name:
        raise ValueError("s3_key의 마지막 세그먼트(파일명)가 비어 있습니다.")
    # 슬래시를 느낌표로 치환 (가독성과 디버깅 편의성)
    s3_key_encoded = s3_key.replace("/", "!")
    return f"{stream_id}{SEP}{s3_key_encoded}"


def decode(spool_filename: str) -> SpoolFileMeta:
    """스풀 파일명에서 메타데이터를 복원한다."""
    parts = spool_filename.split(SEP, 1)
    if len(parts) != 2:
        raise ValueError(
            f"유효하지 않은 스풀 파일명: {spool_filename!r} "
            f"(기대 형식: stream_id{SEP}s3_key_encoded)"
        )
    stream_id, s3_key_encoded = parts
    # 느낌표를 슬래시로 복원
    s3_key = s3_key_encoded.replace("!", "/")
    # original_name을 s3_key에서 추출
    original_name = Path(s3_key).name
    return SpoolFileMeta(stream_id=stream_id, s3_key=s3_key, original_name=original_name)


def is_spool_file(filename: str) -> bool:
    """파일명이 스풀 파일 형식인지 확인한다."""
    return filename.count(SEP) >= 1
