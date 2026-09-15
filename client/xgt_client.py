"""LS XGT Dedicated TCP 클라이언트. 설정은 생성자로 주입받는다."""

from __future__ import annotations

import logging
import socket
import struct

from client.addr import parse_plc_addr
from client.errors import ClientError, to_client_error
from client.xgt_addr import (
    resolve_block,
    value_from_block_words,
)
from client.xgt_frame import (
    DATA_TYPE_BIT,
    DATA_TYPE_CONTINUOUS_BYTE,
    DATA_TYPE_WORD,
    HEADER_SIZE,
    build_bit_read,
    build_continuous_read,
    build_word_read,
    parse_read_ack,
    words_le,
)
from model.client_model import XgtSettings
from model.error_model import ClientErrorCode

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
            return [self._read_word(num)]
        # 짧은 연속 구간
        var = f"%DB{num * 2}"
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
        return words_le(data)

    def _read_word(self, word_num: int) -> int:
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
        return words[0]

    def _read_bit(self, word_num: int, bit: int) -> int:
        from client.xgt_addr import to_dx_var

        invoke = self._next_invoke()
        req = build_bit_read(
            invoke_id=invoke,
            var_name=to_dx_var(word_num, bit),
            cpu_info=self.settings.cpu_info,
        )
        raw = self._transact(req)
        _h, _t, data = parse_read_ack(raw, expected_type=DATA_TYPE_BIT)
        if not data:
            raise ClientError(ClientErrorCode.PROTOCOL_ERROR, "BIT 응답 Data 없음")
        return 1 if data[0] else 0

    def read_scan_samples(self, addresses: list[str]) -> list[tuple[str, int | None, str | None]]:
        """스캔용: 블록 모드면 1회 읽고 파싱, 아니면 점단위.

        반환: (addr, value, error_code|None)
        """
        if not addresses:
            return []
        block = resolve_block(
            self.settings.read_start, self.settings.read_word_count
        )
        if block is None:
            out: list[tuple[str, int | None, str | None]] = []
            for addr in addresses:
                try:
                    out.append((addr, int(self.read_device(addr, 1)[0]), None))
                except ClientError as e:
                    if e.code.requires_reconnect:
                        raise
                    code = (
                        e.code.value
                        if e.code
                        in (
                            ClientErrorCode.UNSUPPORTED_ADDR,
                            ClientErrorCode.ADDR_OUT_OF_RANGE,
                        )
                        else ClientErrorCode.ADDR_READ_FAILED.value
                    )
                    out.append((addr, None, code))
            return out

        try:
            words = self.read_block_words()
        except ClientError:
            raise

        out = []
        for addr in addresses:
            try:
                val = value_from_block_words(
                    words, start_word=block.start_word, addr=addr
                )
                out.append((addr, val, None))
            except ClientError as e:
                if e.code.requires_reconnect:
                    raise
                code = (
                    e.code.value
                    if e.code
                    in (
                        ClientErrorCode.UNSUPPORTED_ADDR,
                        ClientErrorCode.ADDR_OUT_OF_RANGE,
                    )
                    else ClientErrorCode.ADDR_READ_FAILED.value
                )
                out.append((addr, None, code))
        return out
