"""PLC 주소 문자열 파싱·워드 내 비트 연산·다중 워드 디코드."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from client.errors import ClientError
from model.error_model import ClientErrorCode

# D100 / ZR10 / D100.5 / D800.A (워드.비트, 비트 0–15 — 10–15는 A–F)
_ADDR_RE = re.compile(r"^([A-Za-z]+)(\d+)(?:\.([0-9A-Fa-f]+))?$")
# D100~200 / D100~D200 / ZR10~ZR20 (포함 구간). ~ 앞뒤 공백 허용. 비트와 혼용 불가.
_RANGE_RE = re.compile(
    r"^([A-Za-z]+)(\d+)\s*~\s*(?:([A-Za-z]+))?(\d+)$"
)
WORD_BIT_MAX = 15

WordEndian = Literal["le", "be"]


@dataclass(frozen=True, slots=True)
class AddrSpan:
    """주소 구간. end 포함. bit 는 단일 워드일 때만."""

    kind: str
    start: int
    end: int
    bit: int | None
    raw: str

    @property
    def word_count(self) -> int:
        return self.end - self.start + 1


def _parse_bit_index(bit_raw: str, addr: str) -> int:
    """워드.비트 접미: 0–9(10진), A–F(16진, 비트 10–15)."""
    if bit_raw.isdecimal():
        bit = int(bit_raw, 10)
    elif len(bit_raw) == 1 and bit_raw.upper() in "ABCDEF":
        bit = int(bit_raw, 16)
    else:
        raise ClientError(
            ClientErrorCode.ADDR_FORMAT_INVALID,
            f"비트 인덱스는 0–9 또는 A–F: {addr}",
        )
    if bit < 0 or bit > WORD_BIT_MAX:
        raise ClientError(
            ClientErrorCode.ADDR_FORMAT_INVALID,
            f"비트 인덱스는 0–{WORD_BIT_MAX}: {addr}",
        )
    return bit


def parse_plc_addr(addr: str) -> tuple[str, int, int | None]:
    """'D100' / 'D100.5' / 'D800.A' / 'ZR10' → (디바이스, 번호, 비트|None).

    구간(D100~200 / D100~D200)은 parse_addr_span 을 쓴다.
    """
    span = parse_addr_span(addr)
    if span.end != span.start:
        raise ClientError(
            ClientErrorCode.ADDR_FORMAT_INVALID,
            f"단일 주소만 허용 (구간은 parse_addr_span): {addr}",
        )
    return span.kind, span.start, span.bit


def parse_addr_span(addr: str) -> AddrSpan:
    """단일·비트·구간 주소를 AddrSpan 으로 파싱한다.

    구간 예: D100~200, D100~D200, ZR10~20, ZR10~ZR20.
    공백은 무시 (D100~ 200, D100 ~ D200 등).
    시작·끝 디바이스가 다르면(D100~R200) ADDR_FORMAT_INVALID.
    """
    raw = addr.strip()
    # 오타성 공백 제거: "D100~ 200" / "D100 ~ D200" / "D100~D 200"
    compact = "".join(raw.split())
    m_range = _RANGE_RE.match(compact)
    if m_range:
        kind = m_range.group(1).upper()
        start = int(m_range.group(2))
        kind2_raw = m_range.group(3)
        end = int(m_range.group(4))
        if kind2_raw is not None:
            kind2 = kind2_raw.upper()
            if kind2 != kind:
                raise ClientError(
                    ClientErrorCode.ADDR_FORMAT_INVALID,
                    f"구간 주소 공간이 다름: {addr} "
                    f"(시작={kind}, 끝={kind2})",
                )
        if end < start:
            raise ClientError(
                ClientErrorCode.ADDR_FORMAT_INVALID,
                f"구간 끝번지가 시작보다 작음: {addr}",
            )
        return AddrSpan(kind=kind, start=start, end=end, bit=None, raw=raw)

    m = _ADDR_RE.match(compact)
    if not m:
        raise ClientError(
            ClientErrorCode.ADDR_FORMAT_INVALID,
            f"주소 형식 오류: {addr}",
        )
    kind = m.group(1).upper()
    num = int(m.group(2))
    bit_raw = m.group(3)
    bit = _parse_bit_index(bit_raw, raw) if bit_raw is not None else None
    return AddrSpan(kind=kind, start=num, end=num, bit=bit, raw=raw)


def as_unsigned16(value: int) -> int:
    """값을 16비트 unsigned(0–65535)로 정규화한다."""
    return int(value) & 0xFFFF


def as_signed16(value: int) -> int:
    """16비트 워드를 signed(-32768–32767)로 해석한다."""
    v = as_unsigned16(value)
    return v - 0x10000 if v >= 0x8000 else v


def extract_word_bit(word: int, bit: int) -> int:
    """16비트 워드에서 bit 위치(0–15)의 값을 0/1로 반환한다."""
    return (as_unsigned16(word) >> bit) & 1


def apply_word_bit(word: int, bit: int, value: int) -> int:
    """워드의 bit 위치를 value(0/1)로 바꾼 16비트 값을 반환한다."""
    w = as_unsigned16(word)
    if int(value):
        return w | (1 << bit)
    return w & ~(1 << bit)


def words_to_int(
    words: list[int],
    *,
    endian: WordEndian = "le",
) -> int:
    """연속 워드를 signed 정수 1개로 합성한다."""
    if not words:
        raise ClientError(
            ClientErrorCode.INVALID_CONFIG,
            "words_to_int: 빈 워드",
        )
    seq = list(words) if endian == "le" else list(reversed(words))
    value = 0
    for i, w in enumerate(seq):
        value |= as_unsigned16(w) << (16 * i)
    bits = 16 * len(words)
    sign = 1 << (bits - 1)
    mask = (1 << bits) - 1
    value &= mask
    if value & sign:
        value -= 1 << bits
    return value


def words_to_ascii(
    words: list[int],
    *,
    endian: WordEndian = "le",
) -> str:
    """워드당 2글자 ASCII. \\0 이후 truncate."""
    chars: list[str] = []
    for w in words:
        u = as_unsigned16(w)
        if endian == "le":
            pair = (u & 0xFF, (u >> 8) & 0xFF)
        else:
            pair = ((u >> 8) & 0xFF, u & 0xFF)
        for b in pair:
            if b == 0:
                return "".join(chars)
            chars.append(chr(b))
    return "".join(chars)
