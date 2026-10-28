"""pyserial-based transport: 9600 (19200/38400) baud, 8 data bits, no parity, 2 stop bits (FLx800).

The Model 680 uses 1 stop bit (``stopbits=1``) and possibly RTS/CTS handshake (``rtscts=True``)."""
from __future__ import annotations

import time

from ..protocol.framing import CommError


class SerialTransport:
    def __init__(self, port: str, baudrate: int = 9600, stopbits: int = 2, rtscts: bool = False):
        self.port = port
        self.baudrate = baudrate
        self.stopbits = stopbits
        self.rtscts = rtscts
        self._ser = None

    def open(self) -> None:
        try:
            import serial  # pyserial
        except ImportError as exc:  # pragma: no cover
            raise CommError("pyserial is not installed (pip install pyserial)") from exc
        try:
            self._ser = serial.Serial(
                port=self.port, baudrate=self.baudrate, bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE if self.stopbits == 1 else serial.STOPBITS_TWO,
                timeout=0.1, write_timeout=5, rtscts=self.rtscts, xonxoff=False, dsrdtr=False,
            )
            # Assert DTR/RTS (the readers' cables expect them).
            self._ser.dtr = True
            self._ser.rts = True
        except Exception as exc:  # noqa: BLE001
            raise CommError(f"cannot open {self.port}: {exc}") from exc

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    def _s(self):
        if self._ser is None:
            raise CommError("port not open")
        return self._ser

    def flush_input(self) -> None:
        s = self._s()
        s.reset_input_buffer()

    def write(self, data: bytes) -> None:
        s = self._s()
        try:
            s.write(data)
            s.flush()
        except Exception as exc:  # noqa: BLE001
            raise CommError(f"write failed: {exc}") from exc

    def read_exact(self, n: int, timeout: float) -> bytes:
        s = self._s()
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while len(buf) < n:
            chunk = s.read(n - len(buf))
            if chunk:
                buf += chunk
                deadline = max(deadline, time.monotonic() + min(timeout, 2.0))  # data is flowing
            elif time.monotonic() > deadline:
                raise CommError(f"timeout: wanted {n} bytes, got {len(buf)}: {bytes(buf)!r}")
        return bytes(buf)

    def set_baudrate(self, baud: int) -> None:
        self.baudrate = baud
        if self._ser is not None:
            self._ser.baudrate = baud


FTDI_VID = 0x0403


def list_ports() -> list[tuple[str, str, bool]]:
    """[(device, description, is_ftdi)] of available serial ports."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:  # pragma: no cover
        return []
    out = []
    for p in lp.comports():
        desc = p.description or ""
        if p.vid is not None:
            desc += f" [{p.vid:04X}:{p.pid or 0:04X}{' ' + p.serial_number if p.serial_number else ''}]"
        out.append((p.device, desc, p.vid == FTDI_VID))
    return out


def find_ftdi_port() -> str:
    """Return the single FTDI USB-serial port, for ``--port auto``."""
    ftdi = [dev for dev, _, is_ftdi in list_ports() if is_ftdi]
    # macOS lists both /dev/tty.* and /dev/cu.*; the cu device is the right one for outgoing use.
    cu = [d for d in ftdi if "/dev/cu." in d]
    ftdi = cu or ftdi
    if len(ftdi) != 1:
        raise CommError(f"--port auto needs exactly one FTDI adapter, found {len(ftdi)}: {ftdi or 'none'}"
                        " (see 'flx800 ports')")
    return ftdi[0]
