"""Replay a recorded trace (``--trace`` log) as a transport, for regression tests.

Each line of a trace looks like::

      2.703 TX 77 | w
      2.812 RX 06 | <ACK>

Writes are compared with the recorded TX bytes (``strict=True``) or only by command
letter (``strict=False``); reads return the recorded RX bytes in order.
"""
from __future__ import annotations

from pathlib import Path

from ..protocol.framing import CommError


def parse_trace(text: str) -> list[tuple[str, bytes]]:
    events: list[tuple[str, bytes]] = []
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or parts[1] not in ("TX", "RX"):
            continue
        hexpart = parts[2].split("|")[0]
        events.append((parts[1], bytes.fromhex(hexpart)))
    return events


class ReplayTransport:
    def __init__(self, events: list[tuple[str, bytes]], strict: bool = True):
        self.events = list(events)
        self.strict = strict
        self.pos = 0
        self._rx = bytearray()
        self.tx_log: list[bytes] = []

    @classmethod
    def from_file(cls, path: str | Path, strict: bool = True, start: int = 0) -> "ReplayTransport":
        ev = parse_trace(Path(path).read_text(encoding="ascii", errors="replace"))
        return cls(ev[start:], strict)

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def flush_input(self) -> None:
        self._rx.clear()

    def set_baudrate(self, baud: int) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.tx_log.append(data)
        # skip any unread RX that preceded this write in the recording
        while self.pos < len(self.events) and self.events[self.pos][0] == "RX":
            self.pos += 1
        if self.pos >= len(self.events):
            raise CommError(f"replay exhausted: unexpected write {data!r}")
        d, expected = self.events[self.pos]
        same = expected == data if self.strict else expected[:1] == data[:1]
        if not same:
            raise CommError(f"replay mismatch at event {self.pos}: wrote {data!r}, recorded {expected!r}")
        self.pos += 1
        while self.pos < len(self.events) and self.events[self.pos][0] == "RX":
            self._rx += self.events[self.pos][1]
            self.pos += 1

    def read_exact(self, n: int, timeout: float) -> bytes:
        if len(self._rx) < n:
            got = bytes(self._rx)
            self._rx.clear()
            raise CommError(f"replay: wanted {n} bytes, have {len(got)}: {got!r}")
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out
