"""스캔 포인트(주소+타입) 스키마."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, model_validator

from client.addr import parse_addr_span
from client.errors import ClientError


class ScanPointType(StrEnum):
    WORD = "word"
    BIT = "bit"
    INT = "int"
    ASCII = "ascii"


class ScanPoint(BaseModel):
    """스캔 한 점. addr는 D100 / D100.A / D100~200 / D100~D200 / ZR10~ZR20."""

    addr: str
    type: ScanPointType

    @model_validator(mode="after")
    def _addr_matches_type(self) -> ScanPoint:
        try:
            span = parse_addr_span(self.addr)
        except ClientError as e:
            # ADDR_FORMAT_INVALID 등 코드·메시지를 설정 검증 오류로 노출
            raise ValueError(f"{e.code.value}: {e.detail or e}") from e
        word_count = span.end - span.start + 1
        if self.type is ScanPointType.BIT:
            if span.bit is None or word_count != 1:
                raise ValueError(
                    f"type=bit 는 워드.비트 단일 주소만 허용: {self.addr!r}"
                )
        elif self.type is ScanPointType.WORD:
            if span.bit is not None or word_count != 1:
                raise ValueError(
                    f"type=word 는 단일 워드 주소만 허용: {self.addr!r}"
                )
        elif self.type is ScanPointType.INT:
            if span.bit is not None or word_count < 2:
                raise ValueError(
                    f"type=int 는 2워드 이상 구간(D100~101)만 허용: {self.addr!r}"
                )
        elif self.type is ScanPointType.ASCII:
            if span.bit is not None:
                raise ValueError(
                    f"type=ascii 는 비트 주소 불가: {self.addr!r}"
                )
            if word_count < 1:
                raise ValueError(f"type=ascii 구간이 비어 있음: {self.addr!r}")
        return self


def legacy_addr_to_scan_point(addr: str) -> ScanPoint:
    """레거시 주소 문자열 → ScanPoint (bit|word)."""
    span = parse_addr_span(addr)
    if span.bit is not None:
        return ScanPoint(addr=addr.strip(), type=ScanPointType.BIT)
    if span.end != span.start:
        raise ValueError(
            f"레거시 scan_addresses 는 구간/타입 없이 word·bit만 허용: {addr!r}. "
            "구간·int·ascii 는 scan[] 를 사용하세요"
        )
    return ScanPoint(addr=addr.strip(), type=ScanPointType.WORD)


def resolve_scan_points(
    *,
    scan: list[ScanPoint] | None,
    scan_addresses: list[str] | None,
) -> list[ScanPoint]:
    """scan 이 있으면 우선, 없으면 scan_addresses 를 word|bit 로 승격."""
    if scan:
        return list(scan)
    return [legacy_addr_to_scan_point(a) for a in (scan_addresses or [])]
