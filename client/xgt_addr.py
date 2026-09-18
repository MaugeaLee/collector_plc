"""XGT 주소·연속 읽기 블록 해석 (D 번지 ↔ %DB/%DW/%DX)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from client.addr import as_unsigned16, parse_addr_span
from client.errors import ClientError
from client.scan_decode import decode_scan_words
from model.error_model import ClientErrorCode
from model.scan_model import ScanPoint


@dataclass(frozen=True, slots=True)
class XgtBlockSpec:
    """연속 BYTE 읽기 한 블록."""

    start_word: int
    word_count: int
    var_name: str
    byte_count: int


def _bit_suffix(bit: int) -> str:
    return format(bit, "X") if bit >= 10 else str(bit)


def to_db_var(word_num: int) -> str:
    """D 워드 번호 → 연속 BYTE 직접변수 `%DBxxxx` (워드×2)."""
    return f"%DB{word_num * 2}"


def to_dw_var(word_num: int) -> str:
    """D 워드 번호 → 개별 WORD `%DWxxxx`."""
    return f"%DW{word_num}"


def to_dx_var(word_num: int, bit: int) -> str:
    """D 워드.비트 → 개별 BIT `%DXxxxx.y`."""
    return f"%DX{word_num}.{_bit_suffix(bit)}"


def resolve_block(
    read_start: str | None,
    read_word_count: int | None,
) -> XgtBlockSpec | None:
    """설정 블록 필드 → XgtBlockSpec. 둘 다 비면 None(점단위)."""
    start = (read_start or "").strip()
    if not start or read_word_count is None or read_word_count == 0:
        return None
    if read_word_count < 0:
        raise ClientError(
            ClientErrorCode.INVALID_CONFIG,
            f"read_word_count 범위 오류: {read_word_count}",
        )
    span = parse_addr_span(start)
    if span.kind != "D" or span.bit is not None or span.end != span.start:
        raise ClientError(
            ClientErrorCode.INVALID_CONFIG,
            f"read_start는 D 워드만 허용: {read_start!r}",
        )
    byte_count = read_word_count * 2
    if byte_count > 0xFFFF:
        raise ClientError(
            ClientErrorCode.INVALID_CONFIG,
            f"연속 읽기 바이트 수 초과: {byte_count}",
        )
    return XgtBlockSpec(
        start_word=span.start,
        word_count=read_word_count,
        var_name=to_db_var(span.start),
        byte_count=byte_count,
    )


def addr_to_direct_var(addr: str) -> str:
    """점단위 읽기용 직접변수명."""
    span = parse_addr_span(addr)
    if span.kind != "D":
        raise ClientError(
            ClientErrorCode.UNSUPPORTED_ADDR,
            f"XGT는 현재 D 디바이스만 지원: {addr}",
        )
    if span.end != span.start:
        raise ClientError(
            ClientErrorCode.UNSUPPORTED_ADDR,
            f"직접변수는 단일 주소만: {addr}",
        )
    if span.bit is None:
        return to_dw_var(span.start)
    return to_dx_var(span.start, span.bit)


def _slice_block_words(
    words: list[int],
    *,
    start_word: int,
    span_start: int,
    span_end: int,
    addr: str,
) -> list[int]:
    """블록 버퍼에서 포함 구간 워드를 잘라 반환한다."""
    idx0 = span_start - start_word
    idx1 = span_end - start_word
    if idx0 < 0 or idx1 >= len(words):
        raise ClientError(
            ClientErrorCode.ADDR_OUT_OF_RANGE,
            f"{addr}가 연속 읽기 범위 D{start_word}~"
            f"D{start_word + len(words) - 1} 밖",
        )
    return [as_unsigned16(w) for w in words[idx0 : idx1 + 1]]


def value_from_block_words(
    words: list[int],
    *,
    start_word: int,
    point: ScanPoint,
    word_endian: Literal["le", "be"] = "le",
) -> int | str:
    """연속 읽기 워드 버퍼에서 scan 포인트 값을 꺼낸다."""
    span = parse_addr_span(point.addr)
    if span.kind != "D":
        raise ClientError(
            ClientErrorCode.UNSUPPORTED_ADDR,
            f"XGT는 현재 D 디바이스만 지원: {point.addr}",
        )
    sliced = _slice_block_words(
        words,
        start_word=start_word,
        span_start=span.start,
        span_end=span.end,
        addr=point.addr,
    )
    return decode_scan_words(sliced, point, endian=word_endian)
