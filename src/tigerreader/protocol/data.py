"""Parser for streamed read data ('S', 'd', 'c' responses).

Format (Appendix C, READ PLATE 'S'):

    initial status (4 hex + ETX)
    repeat per kinetic interval, per filter set / auto-sense block:
        CR  "hhh:mm:ss.s"  { ( ",ddddd" )* CR LF }*  [ttt]  ^Z  ccc
    ^Z
    final status (4 hex + ETX)

Values "00000"-"99998"; "*****" = overrange.  The checksum covers the bytes
after the CR start code up to and including the ^Z.  An abort produces
DLE (new protocol) or ^Z (old protocol) followed by the status code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .framing import CR, DLE, ETX, LF, SUB, ProtocolError, Status, checksum8, parse_status

OVERRANGE = None  # value used in matrices for "*****"


@dataclass
class DataBlock:
    """One filter-set (or auto-sense) block of a plate read."""

    elapsed_s: float                   # reader's time stamp (kinetic: start of that kinetic cycle)
    values: list[list[Optional[int]]]  # rows x cols, None = overrange
    temperature_c: Optional[float] = None
    raw: bytes = b""
    received_s: Optional[float] = None  # host clock: seconds from 'S' until the block arrived

    @property
    def n_rows(self) -> int:
        return len(self.values)

    @property
    def n_cols(self) -> int:
        return len(self.values[0]) if self.values else 0


@dataclass
class ReadResponse:
    initial_status: Status
    blocks: list[DataBlock] = field(default_factory=list)
    final_status: Optional[Status] = None
    aborted: bool = False

    @property
    def ok(self) -> bool:
        return self.initial_status.ok and self.final_status is not None and self.final_status.ok


def parse_timestamp(ts: bytes) -> float:
    """'hhh:mm:ss.s' -> seconds."""
    try:
        h, m, s = ts.decode("ascii").split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception as exc:  # noqa: BLE001
        raise ProtocolError(f"bad timestamp {ts!r}") from exc


def parse_temperature(t: bytes) -> Optional[float]:
    """3-digit temperature (tenths of °C); 000 = none/error, 111 = below range, 999 = above."""
    v = int(t)
    if v in (0, 111, 999):
        return None
    return v / 10.0


def parse_block_body(body: bytes) -> DataBlock:
    """Parse the bytes between the CR start code and ^Z (exclusive)."""
    if len(body) < 11:
        raise ProtocolError("data block too short")
    elapsed = parse_timestamp(body[:11])
    rest = body[11:]
    lines = rest.split(bytes([CR, LF]))
    trailer = lines.pop()  # text after the last CR LF: temperature or empty
    rows: list[list[Optional[int]]] = []
    for line in lines:
        if not line.startswith(b","):
            raise ProtocolError(f"row does not start with ',': {line[:20]!r}")
        cells = line[1:].split(b",")
        row: list[Optional[int]] = []
        for c in cells:
            if c == b"*****":
                row.append(OVERRANGE)
            elif len(c) == 5 and c.isdigit():
                row.append(int(c))
            else:
                raise ProtocolError(f"bad data value {c!r}")
        rows.append(row)
    temp = None
    if trailer:
        if len(trailer) != 3 or not trailer.isdigit():
            raise ProtocolError(f"unexpected trailer {trailer!r}")
        temp = parse_temperature(trailer)
    return DataBlock(elapsed, rows, temp, raw=body)


ByteReader = Callable[[int], bytes]  # read exactly n bytes (raise on timeout)


def read_stream(read: ByteReader, on_block: Optional[Callable[[DataBlock], None]] = None,
                max_block: int = 65536) -> ReadResponse:
    """Consume a streamed read response from *read* (ACK already consumed)."""
    first = read(1)
    if first[0] in (DLE, SUB):
        return _abort(read, first)
    initial = parse_status(first + read(4))
    resp = ReadResponse(initial_status=initial)
    if not initial.ok:
        return resp
    while True:
        b = read(1)[0]
        if b == CR:
            body = bytearray()
            while True:
                c = read(1)[0]
                if c == SUB:
                    break
                body.append(c)
                if len(body) > max_block:
                    raise ProtocolError("data block exceeds maximum size")
            ck = read(3)
            expected = checksum8(bytes(body) + bytes([SUB]))
            if not ck.isdigit() or int(ck) != expected:
                raise ProtocolError(f"block checksum mismatch: got {ck!r}, expected {expected:03d}")
            blk = parse_block_body(bytes(body))
            resp.blocks.append(blk)
            if on_block:
                on_block(blk)
        elif b == SUB:
            resp.final_status = parse_status(read(5))
            return resp
        elif b == DLE:
            ab = _abort(read, bytes([b]))
            resp.final_status, resp.aborted = ab.final_status, True
            return resp
        else:
            raise ProtocolError(f"unexpected byte 0x{b:02X} in data stream")


def _abort(read: ByteReader, lead: bytes) -> ReadResponse:
    code = read(4)
    st = Status(code.decode("ascii", "replace").upper())
    # An ETX may follow; callers flush the input afterwards.
    return ReadResponse(initial_status=st, final_status=st, aborted=True)


def build_block(values: list[list[Optional[int]]], elapsed_s: float = 0.0,
                temperature_c: Optional[float] = None) -> bytes:
    """Encode one block (CR … ^Z ccc). Used by the simulator and tests."""
    h = int(elapsed_s // 3600)
    m = int(elapsed_s % 3600 // 60)
    s = elapsed_s % 60
    body = bytearray(f"{h:03d}:{m:02d}:{s:04.1f}".encode())
    for row in values:
        for v in row:
            body += b",*****" if v is None else b",%05d" % min(max(int(v), 0), 99998)
        body += bytes([CR, LF])
    if temperature_c is not None:
        body += b"%03d" % int(round(temperature_c * 10))
    body.append(SUB)
    return bytes([CR]) + bytes(body) + b"%03d" % checksum8(bytes(body))


ETX_B = bytes([ETX])
