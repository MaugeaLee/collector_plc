"""LS XGT Dedicated 프로토콜 프레임 조립·파싱 (연속 BYTE 읽기 중심).

Var Length / Header Length는 변수명 ASCII 실길이에 맞춘다.
(대승정밀 시트 예시의 0A/16 표기는 `%DB980000`=9B 기준으로는 off-by-one)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from client.errors import ClientError
from model.error_model import ClientErrorCode

# Company Header
HEADER_SIZE = 20
COMPANY_ID = b"LSIS-XGT\x00\x00"  # 10B
CPU_INFO_XGK = 0xA0
SOURCE_CLIENT = 0x33
SOURCE_SERVER = 0x11

# Application — Command / Data Type (와이어 LE 값)
CMD_READ = 0x0054
CMD_READ_ACK = 0x0055
DATA_TYPE_BIT = 0x0000
DATA_TYPE_WORD = 0x0002
DATA_TYPE_CONTINUOUS_BYTE = 0x0014

_HEADER_TAIL = struct.Struct("<HBBHHBB")  # PLC, CPU, Source, Invoke, Length, Pos, BCC
_APP_READ_PREFIX = struct.Struct("<HHHHH")  # Cmd, Type, Reserved, Block, VarLen
_APP_CONT_SUFFIX = struct.Struct("<H")  # Data Count
_APP_READ_ACK = struct.Struct("<HHHHHH")  # Cmd, Type, Reserved, Error, Block, DataCount


@dataclass(frozen=True, slots=True)
class XgtHeader:
    plc_info: int
    cpu_info: int
    source: int
    invoke_id: int
    length: int
    position: int
    bcc: int


@dataclass(frozen=True, slots=True)
class XgtContinuousReadResponse:
    header: XgtHeader
    error_status: int
    data: bytes


def build_header(
    *,
    invoke_id: int,
    app_length: int,
    cpu_info: int = CPU_INFO_XGK,
    source: int = SOURCE_CLIENT,
    plc_info: int = 0,
    position: int = 0,
    bcc: int = 0,
) -> bytes:
    """Company Header 20바이트."""
    if not 0 <= invoke_id <= 0xFFFF:
        raise ClientError(
            ClientErrorCode.INVALID_CONFIG,
            f"invoke_id 범위 초과: {invoke_id}",
        )
    if not 0 <= app_length <= 0xFFFF:
        raise ClientError(
            ClientErrorCode.INVALID_CONFIG,
            f"app_length 범위 초과: {app_length}",
        )
    return COMPANY_ID + _HEADER_TAIL.pack(
        plc_info,
        cpu_info,
        source,
        invoke_id,
        app_length,
        position,
        bcc,
    )


def build_continuous_read(
    *,
    invoke_id: int,
    var_name: str,
    byte_count: int,
    cpu_info: int = CPU_INFO_XGK,
) -> bytes:
    """연속 BYTE 읽기 요청 프레임 (Header + App).

    var_name/byte_count는 설정(read_start·read_word_count)에서 계산해 넘긴다.
    """
    if not var_name:
        raise ClientError(
            ClientErrorCode.INVALID_CONFIG,
            "var_name이 비어 있습니다",
        )
    var = var_name.encode("ascii")
    if not 0 < byte_count <= 0xFFFF:
        raise ClientError(
            ClientErrorCode.INVALID_CONFIG,
            f"byte_count 범위 초과: {byte_count}",
        )
    app = (
        _APP_READ_PREFIX.pack(
            CMD_READ,
            DATA_TYPE_CONTINUOUS_BYTE,
            0,
            1,
            len(var),
        )
        + var
        + _APP_CONT_SUFFIX.pack(byte_count)
    )
    return build_header(invoke_id=invoke_id, app_length=len(app), cpu_info=cpu_info) + app


def build_word_read(
    *,
    invoke_id: int,
    var_name: str,
    cpu_info: int = CPU_INFO_XGK,
) -> bytes:
    """개별 WORD 읽기 요청 (점단위). Data Count 필드 없음."""
    return _build_typed_read(
        invoke_id=invoke_id,
        var_name=var_name,
        data_type=DATA_TYPE_WORD,
        cpu_info=cpu_info,
    )


def build_bit_read(
    *,
    invoke_id: int,
    var_name: str,
    cpu_info: int = CPU_INFO_XGK,
) -> bytes:
    """개별 BIT 읽기 요청 (점단위)."""
    return _build_typed_read(
        invoke_id=invoke_id,
        var_name=var_name,
        data_type=DATA_TYPE_BIT,
        cpu_info=cpu_info,
    )


def _build_typed_read(
    *,
    invoke_id: int,
    var_name: str,
    data_type: int,
    cpu_info: int,
) -> bytes:
    if not var_name:
        raise ClientError(
            ClientErrorCode.INVALID_CONFIG,
            "var_name이 비어 있습니다",
        )
    var = var_name.encode("ascii")
    app = _APP_READ_PREFIX.pack(
        CMD_READ,
        data_type,
        0,
        1,
        len(var),
    ) + var
    return build_header(invoke_id=invoke_id, app_length=len(app), cpu_info=cpu_info) + app


def parse_header(raw: bytes) -> XgtHeader:
    """프레임 앞 20바이트를 Header로 파싱."""
    if len(raw) < HEADER_SIZE:
        raise ClientError(
            ClientErrorCode.PROTOCOL_ERROR,
            f"헤더 길이 부족: {len(raw)} < {HEADER_SIZE}",
        )
    if raw[:10] != COMPANY_ID:
        raise ClientError(
            ClientErrorCode.PROTOCOL_ERROR,
            f"Company ID 불일치: {raw[:10]!r}",
        )
    plc, cpu, source, invoke, length, pos, bcc = _HEADER_TAIL.unpack_from(raw, 10)
    return XgtHeader(
        plc_info=plc,
        cpu_info=cpu,
        source=source,
        invoke_id=invoke,
        length=length,
        position=pos,
        bcc=bcc,
    )


def parse_read_ack(
    raw: bytes,
    *,
    expected_type: int | None = None,
) -> tuple[XgtHeader, int, bytes]:
    """읽기 응답 → (header, data_type, data). Error≠0 이면 DEVICE_ERROR."""
    header = parse_header(raw)
    need = HEADER_SIZE + _APP_READ_ACK.size
    if len(raw) < need:
        raise ClientError(
            ClientErrorCode.PROTOCOL_ERROR,
            f"응답 App 접두 부족: {len(raw)} < {need}",
        )

    cmd, dtype, _reserved, err, _block, data_count = _APP_READ_ACK.unpack_from(
        raw, HEADER_SIZE
    )
    if cmd != CMD_READ_ACK:
        raise ClientError(
            ClientErrorCode.PROTOCOL_ERROR,
            f"Read ACK 아님: cmd=0x{cmd:04X}",
        )
    if expected_type is not None and dtype != expected_type:
        raise ClientError(
            ClientErrorCode.PROTOCOL_ERROR,
            f"Data Type 불일치: want=0x{expected_type:04X} got=0x{dtype:04X}",
        )
    if err != 0:
        raise ClientError(
            ClientErrorCode.DEVICE_ERROR,
            f"XGT Error Status=0x{err:04X}",
        )

    data_off = HEADER_SIZE + _APP_READ_ACK.size
    if len(raw) < data_off + data_count:
        raise ClientError(
            ClientErrorCode.PROTOCOL_ERROR,
            f"Data 길이 부족: have={len(raw) - data_off}, want={data_count}",
        )
    return header, dtype, raw[data_off : data_off + data_count]


def parse_continuous_read_response(raw: bytes) -> XgtContinuousReadResponse:
    """연속 BYTE 읽기 응답 파싱."""
    header, _dtype, data = parse_read_ack(
        raw, expected_type=DATA_TYPE_CONTINUOUS_BYTE
    )
    return XgtContinuousReadResponse(
        header=header,
        error_status=0,
        data=data,
    )


def words_le(data: bytes) -> list[int]:
    """응답 Data[]를 LE 워드 리스트로 변환."""
    if len(data) % 2:
        raise ClientError(
            ClientErrorCode.PROTOCOL_ERROR,
            f"홀수 길이 Data: {len(data)}",
        )
    return list(struct.unpack(f"<{len(data) // 2}H", data))
