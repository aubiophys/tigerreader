"""Transport wrapper that logs every byte (ASCII trace file + optional callback)."""
from __future__ import annotations

import time
from typing import Callable, Optional, TextIO

_NAMES = {0x06: "<ACK>", 0x15: "<NAK>", 0x03: "<ETX>", 0x10: "<DLE>", 0x1A: "<^Z>",
          0x0D: "<CR>", 0x0A: "<LF>", 0x1E: "<RS>"}


def printable(data: bytes) -> str:
    return "".join(_NAMES.get(b, chr(b) if 32 <= b < 127 else f"<{b:02X}>") for b in data)


class TracingTransport:
    def __init__(self, inner, sink: Optional[TextIO] = None,
                 callback: Optional[Callable[[str, bytes], None]] = None, flush_on: tuple = ()):
        self.inner = inner
        self.flush_on = (b"\x03", b"\n", b"\x1a", b"\x06", b"\x15") + tuple(flush_on)  # end of a log line
        self.sink = sink
        self.callback = callback
        self._t0 = time.monotonic()
        self._rx = bytearray()

    def _log(self, direction: str, data: bytes) -> None:
        if self.sink is not None:
            self.sink.write(f"{time.monotonic() - self._t0:10.3f} {direction} {data.hex(' ')} | {printable(data)}\n")
            self.sink.flush()
        if self.callback is not None:
            self.callback(direction, data)

    def open(self) -> None:
        self.inner.open()

    def close(self) -> None:
        self._flush_rx()
        self.inner.close()

    def _flush_rx(self) -> None:
        if self._rx:
            data, self._rx = bytes(self._rx), bytearray()
            self._log("RX", data)

    def flush_input(self) -> None:
        self.inner.flush_input()

    def write(self, data: bytes) -> None:
        self._flush_rx()
        self._log("TX", data)
        self.inner.write(data)

    def read_exact(self, n: int, timeout: float) -> bytes:
        try:
            data = self.inner.read_exact(n, timeout)
        except Exception:
            self._flush_rx()
            raise
        self._rx += data
        if len(self._rx) >= 80 or self._rx[-1:] in self.flush_on:
            self._flush_rx()
        return data

    def set_baudrate(self, baud: int) -> None:
        self.inner.set_baudrate(baud)
