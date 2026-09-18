"""ScanPoint 워드 버퍼 디코드 (XGT/MC/Modbus 공통)."""

from __future__ import annotations

from typing import Literal

from client.addr import (
    as_signed16,
    as_unsigned16,
    extract_word_bit,
    parse_addr_span,
    words_to_ascii,
    words_to_int,
)
from client.errors import ClientError
from model.error_model import ClientErrorCode
from model.scan_model import ScanPoint, ScanPointType

# Modbus Function 03/04 관례상 한 요청 최대 레지스터 수
MODBUS_MAX_REGISTERS = 125

WordEndian = Literal["le", "be"]


def soft_addr_error_code(err: ClientError) -> str:
    """번지 soft-fail 시 와이어에 남길 코드."""
    if err.code in (
        ClientErrorCode.UNSUPPORTED_ADDR,
        ClientErrorCode.ADDR_OUT_OF_RANGE,
        ClientErrorCode.ADDR_FORMAT_INVALID,
    ):
        return err.code.value
    return ClientErrorCode.ADDR_READ_FAILED.value


def decode_scan_words(
    words: list[int],
    point: ScanPoint,
    *,
    endian: WordEndian = "le",
) -> int | str:
    """이미 읽은 unsigned 워드 리스트를 ScanPoint 타입에 맞게 디코드한다."""
    span = parse_addr_span(point.addr)
    if len(words) != span.word_count:
        raise ClientError(
            ClientErrorCode.PROTOCOL_ERROR,
            f"워드 수 불일치: want={span.word_count} got={len(words)} addr={point.addr}",
        )
    raw = [as_unsigned16(w) for w in words]
    if point.type is ScanPointType.BIT:
        if span.bit is None:
            raise ClientError(
                ClientErrorCode.ADDR_FORMAT_INVALID,
                f"type=bit 인데 비트 없음: {point.addr}",
            )
        return extract_word_bit(raw[0], span.bit)
    if point.type is ScanPointType.WORD:
        return as_signed16(raw[0])
    if point.type in (ScanPointType.INT, ScanPointType.LONG_WORD):
        return words_to_int(raw, endian=endian)
    if point.type is ScanPointType.ASCII:
        return words_to_ascii(raw, endian=endian)
    raise ClientError(
        ClientErrorCode.UNSUPPORTED_ADDR,
        f"지원하지 않는 scan type: {point.type}",
    )
