"""XGT 주소·연속 읽기 블록 해석 (D 번지 ↔ %DB/%DW/%DX)."""

from __future__ import annotations

from dataclasses import dataclass

from client.addr import extract_word_bit, parse_plc_addr
from client.errors import ClientError
from model.error_model import ClientErrorCode


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
    kind, num, bit = parse_plc_addr(start)
    if kind != "D" or bit is not None:
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
        start_word=num,
        word_count=read_word_count,
        var_name=to_db_var(num),
        byte_count=byte_count,
    )


def addr_to_direct_var(addr: str) -> str:
    """점단위 읽기용 직접변수명."""
    kind, num, bit = parse_plc_addr(addr)
    if kind != "D":
        raise ClientError(
            ClientErrorCode.UNSUPPORTED_ADDR,
            f"XGT는 현재 D 디바이스만 지원: {addr}",
        )
    if bit is None:
        return to_dw_var(num)
    return to_dx_var(num, bit)


def value_from_block_words(
    words: list[int],
    *,
    start_word: int,
    addr: str,
) -> int:
    """연속 읽기 워드 버퍼에서 scan 주소 값을 꺼낸다."""
    kind, num, bit = parse_plc_addr(addr)
    if kind != "D":
        raise ClientError(
            ClientErrorCode.UNSUPPORTED_ADDR,
            f"XGT는 현재 D 디바이스만 지원: {addr}",
        )
    idx = num - start_word
    if idx < 0 or idx >= len(words):
        raise ClientError(
            ClientErrorCode.ADDR_OUT_OF_RANGE,
            f"{addr}가 연속 읽기 범위 D{start_word}~"
            f"D{start_word + len(words) - 1} 밖",
        )
    word = int(words[idx]) & 0xFFFF
    if bit is None:
        return word
    return extract_word_bit(word, bit)
