"""Low-level framing for the FLx800 serial protocol.

Source: the computer-control appendix (Appendix C) of the FLx800 operator's manual.

* Commands are single ASCII characters, answered by ACK (0x06) or NAK (0x15).
* Numbers are fixed-width, zero-padded ASCII decimal, most significant digit first.
* Every command ends with a *status string*: 4 hexadecimal characters + ETX.
* Checksums are the 8-bit sum of the covered bytes, sent as 3 ASCII digits.
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import describe_status

ACK = 0x06
NAK = 0x15
ETX = 0x03
DLE = 0x10
RS = 0x1E
CR = 0x0D
LF = 0x0A
SUB = 0x1A  # ^Z, data terminator

STATUS_LEN = 5  # 4 hex chars + ETX


class FLx800Error(Exception):
    """Base class for all FLx800 errors."""


class CommError(FLx800Error):
    """Serial communication failure (timeout, port error)."""


class NakError(FLx800Error):
    """The reader answered a command character with NAK."""

    def __init__(self, command: str):
        super().__init__(f"reader rejected command {command!r} with NAK")
        self.command = command


class ProtocolError(FLx800Error):
    """Malformed response (bad framing, bad checksum, unexpected byte)."""


@dataclass(frozen=True)
class Status:
    """A 4-character hexadecimal status code returned by the reader."""

    code: str

    @property
    def ok(self) -> bool:
        return self.code == "0000"

    @property
    def fatal(self) -> bool:
        return self.code.upper().startswith("A")

    @property
    def value(self) -> int:
        return int(self.code, 16)

    def describe(self) -> str:
        return describe_status(self.code)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.code} ({self.describe()})"


class ReaderError(FLx800Error):
    """The reader reported a non-zero status code."""

    def __init__(self, status: Status, context: str = ""):
        msg = f"reader error {status.code}: {status.describe()}"
        if context:
            msg = f"{context}: {msg}"
        super().__init__(msg)
        self.status = status


def checksum8(data: bytes) -> int:
    """8-bit sum of *data* (0–255)."""
    return sum(data) & 0xFF


def checksum_field(data: bytes) -> bytes:
    """Checksum of *data* formatted as 3 ASCII digits ("000"–"255")."""
    return b"%03d" % checksum8(data)


def parse_status(raw: bytes) -> Status:
    """Parse a 5-byte status string (4 hex chars + ETX)."""
    if len(raw) != STATUS_LEN or raw[-1] != ETX:
        raise ProtocolError(f"bad status string {raw!r}")
    code = raw[:4].decode("ascii", errors="replace")
    if any(c not in "0123456789ABCDEFabcdef" for c in code):
        raise ProtocolError(f"non-hex status code {raw!r}")
    return Status(code.upper())


def num(value: int, width: int) -> bytes:
    """Zero-padded fixed-width ASCII decimal field; raises if it does not fit."""
    if value < 0:
        raise ValueError(f"negative value {value} for a {width}-digit field")
    s = b"%0*d" % (width, value)
    if len(s) != width:
        raise ValueError(f"value {value} does not fit in {width} digits")
    return s
