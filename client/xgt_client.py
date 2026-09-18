"""LS XGT Dedicated TCP 클라이언트. 설정은 생성자로 주입받는다."""

from __future__ import annotations

import logging
import socket
import struct
from typing import Literal

from client.addr import (
    as_signed16,
    as_unsigned16,
    extract_word_bit,
    parse_addr_span,
    parse_plc_addr,
)
from client.errors import ClientError, to_client_error
from client.scan_decode import decode_scan_words, soft_addr_error_code
from client.xgt_addr import (
    resolve_block,
    value_from_block_words,
)
from client.xgt_frame import (
    DATA_TYPE_CONTINUOUS_BYTE,
    DATA_TYPE_WORD,
    HEADER_SIZE,
    build_continuous_read,
    build_word_read,
    parse_read_ack,
    words_le,
)
from model.client_model import XgtSettings
from model.error_model import ClientErrorCode
from model.scan_model import ScanPoint, ScanPointType

logger = logging.getLogger(__name__)


class XgtClient:
    """BaseClient와 동일하게 connect/close/_target/read_device 를 제공한다."""

    def __init__(self, settings: XgtSettings):
        self.settings = settings
        self.device_id = settings.device_id
        self._sock: socket.socket | None = None
        self._invoke_id = 0

    def _target(self) -> str:
        return f"{self.settings.host}:{self.settings.port}"

    @property
    def word_endian(self) -> Literal["le", "be"]:
        return getattr(self.settings, "word_endian", "le")

    def connect(self) -> bool:
        self.close()
        try:
            sock = socket.create_connection(
                (self.settings.host, self.settings.port),
                timeout=self.settings.timeout,
            )
            sock.settimeout(self.settings.timeout)
            self._sock = sock
        except Exception as e:
            raise to_client_error(
                e, default=ClientErrorCode.CONNECT_FAILED
            ) from e
        return True

    def close(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is None:
            return
        try:
            sock.close()
        except Exception as e:
            logger.debug("xgt close failed: %s", e)

    def _next_invoke(self) -> int:
        self._invoke_id = (self._invoke_id + 1) & 0xFFFF
        if self._invoke_id == 0:
            self._invoke_id = 1
        return self._invoke_id

    def _require_sock(self) -> socket.socket:
        if self._sock is None:
            raise ClientError(
                ClientErrorCode.CONNECTION_CLOSED,
                f"소켓 없음: {self._target()}",
            )
        return self._sock

    def _recv_exact(self, n: int) -> bytes:
        sock = self._require_sock()
        buf = bytearray()
        try:
            while len(buf) < n:
                chunk = sock.recv(n - len(buf))
                if not chunk:
                    raise ClientError(
                        ClientErrorCode.CONNECTION_CLOSED,
                        f"수신 중 연결 종료: {self._target()}",
                    )
                buf.extend(chunk)
        except ClientError:
            raise
        except Exception as e:
            raise to_client_error(e, default=ClientErrorCode.READ_FAILED) from e
        return bytes(buf)

    def _transact(self, request: bytes) -> bytes:
        sock = self._require_sock()
        try:
            sock.sendall(request)
        except Exception as e:
            raise to_client_error(e, default=ClientErrorCode.READ_FAILED) from e
        header = self._recv_exact(HEADER_SIZE)
        app_len = struct.unpack_from("<H", header, 16)[0]
        app = self._recv_exact(app_len)
        return header + app

    def read_block_words(self) -> list[int]:
        """read_start/read_word_count 연속 읽기 → LE 워드 리스트."""
        block = resolve_block(
            self.settings.read_start, self.settings.read_word_count
        )
        if block is None:
            raise ClientError(
                ClientErrorCode.INVALID_CONFIG,
                "연속 읽기 블록이 설정되지 않음",
            )
        invoke = self._next_invoke()
        req = build_continuous_read(
            invoke_id=invoke,
            var_name=block.var_name,
            byte_count=block.byte_count,
            cpu_info=self.settings.cpu_info,
        )
        raw = self._transact(req)
        _header, _dtype, data = parse_read_ack(
            raw, expected_type=DATA_TYPE_CONTINUOUS_BYTE
        )
        words = words_le(data)
        if len(words) != block.word_count:
            raise ClientError(
                ClientErrorCode.PROTOCOL_ERROR,
                f"워드 수 불일치: want={block.word_count} got={len(words)}",
            )
        return words

    def _read_words_raw(self, start_word: int, count: int) -> list[int]:
        """연속 count 워드를 unsigned 로 읽는다."""
        if count < 1:
            raise ClientError(
                ClientErrorCode.INVALID_CONFIG,
                f"count 범위 오류: {count}",
            )
        if count == 1:
            return [as_unsigned16(self._read_word_raw(start_word))]
        var = f"%DB{start_word * 2}"
        invoke = self._next_invoke()
        req = build_continuous_read(
            invoke_id=invoke,
            var_name=var,
            byte_count=count * 2,
            cpu_info=self.settings.cpu_info,
        )
        raw = self._transact(req)
        _h, _t, data = parse_read_ack(
            raw, expected_type=DATA_TYPE_CONTINUOUS_BYTE
        )
        return [as_unsigned16(w) for w in words_le(data)]

    def read_device(self, addr: str, count: int = 1) -> list[int]:
        """점단위 읽기. count>1 이면 연속 워드(시작 addr 기준) — 비트 addr는 count=1만."""
        kind, num, bit = parse_plc_addr(addr)
        if kind != "D":
            raise ClientError(
                ClientErrorCode.UNSUPPORTED_ADDR,
                f"XGT는 현재 D 디바이스만 지원: {addr}",
            )
        if bit is not None:
            if count != 1:
                raise ClientError(
                    ClientErrorCode.UNSUPPORTED_ADDR,
                    f"비트 주소는 count=1만 허용: {addr}",
                )
            return [self._read_bit(num, bit)]
        if count < 1:
            raise ClientError(
                ClientErrorCode.INVALID_CONFIG,
                f"count 범위 오류: {count}",
            )
        if count == 1:
            return [as_signed16(self._read_word_raw(num))]
        return [as_signed16(w) for w in self._read_words_raw(num, count)]

    def _read_word_raw(self, word_num: int) -> int:
        invoke = self._next_invoke()
        req = build_word_read(
            invoke_id=invoke,
            var_name=f"%DW{word_num}",
            cpu_info=self.settings.cpu_info,
        )
        raw = self._transact(req)
        _h, _t, data = parse_read_ack(raw, expected_type=DATA_TYPE_WORD)
        words = words_le(data)
        if not words:
            raise ClientError(ClientErrorCode.PROTOCOL_ERROR, "WORD 응답 Data 없음")
        return as_unsigned16(words[0])

    def _read_bit(self, word_num: int, bit: int) -> int:
        # XGT 전용 프로토콜: D 디바이스는 Bit 개별 접근 불가(Byte/Word만).
        # %DX… 요청은 성공처럼 보여도 항상 0이 될 수 있어 %DW 읽기 후 비트 추출.
        return extract_word_bit(self._read_word_raw(word_num), bit)

    def read_scan_point(self, point: ScanPoint) -> int | str:
        """점단위로 ScanPoint 한 건을 읽어 디코드한다."""
        span = parse_addr_span(point.addr)
        if span.kind != "D":
            raise ClientError(
                ClientErrorCode.UNSUPPORTED_ADDR,
                f"XGT는 현재 D 디바이스만 지원: {point.addr}",
            )
        if point.type is ScanPointType.BIT:
            assert span.bit is not None
            return self._read_bit(span.start, span.bit)
        raw = self._read_words_raw(span.start, span.word_count)
        return decode_scan_words(raw, point, endian=self.word_endian)

    def read_scan_samples(
        self, points: list[ScanPoint]
    ) -> list[tuple[str, ScanPointType, int | str | None, str | None]]:
        """스캔용: 블록 모드면 1회 읽고 파싱, 아니면 점단위.

        반환: (addr, type, value, error_code|None)
        """
        if not points:
            return []
        block = resolve_block(
            self.settings.read_start, self.settings.read_word_count
        )
        if block is None:
            out: list[
                tuple[str, ScanPointType, int | str | None, str | None]
            ] = []
            for point in points:
                try:
                    out.append(
                        (
                            point.addr,
                            point.type,
                            self.read_scan_point(point),
                            None,
                        )
                    )
                except ClientError as e:
                    if e.code.requires_reconnect:
                        raise
                    out.append(
                        (
                            point.addr,
                            point.type,
                            None,
                            soft_addr_error_code(e),
                        )
                    )
            return out

        words = self.read_block_words()
        out = []
        for point in points:
            try:
                val = value_from_block_words(
                    words,
                    start_word=block.start_word,
                    point=point,
                    word_endian=self.word_endian,
                )
                out.append((point.addr, point.type, val, None))
            except ClientError as e:
                if e.code.requires_reconnect:
                    raise
                out.append(
                    (point.addr, point.type, None, soft_addr_error_code(e))
                )
        return out
