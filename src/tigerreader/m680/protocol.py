"""Bio-Rad Model 680 remote-control protocol (instruction manual, "Command Language for Remote control mode").

* 9600 baud, 8 data bits, no parity, 1 stop bit; ASCII, case blind.
* Command:  ``EIA.READER <command>[ <args>...]<CR>``
* Response: ``ERE <4-digit error code>[ <data>...]<CR>`` (some responses continue on further lines).
* Plate data: 8 rows of 12 absorbance values (3 decimals); ``*.***`` = beyond ±3.500 OD; a minus sign
  replaces the separating space for negative values; each block ends with a checksum line and ``.end``.
  The checksum is 4 hex digits = 16-bit sum of the 8 rows including their CRs, and the filter lines give
  wheel positions (``Mes. filter:3``), not wavelengths.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..protocol.framing import FLx800Error

DEVICE = "EIA.READER"
CR = b"\r"

# Commands the driver may send. Everything else is refused before it reaches the wire.
ALLOWED = {"AQ", "RL", "RS", "ID", "GSN", "MR", "FSTATUS", "ISTATUS", "INCU", "WINC", "RPLATE", "RTPLATE"}
# Documented commands that change the reader's stored state (never sent).
FORBIDDEN = {
    "SSN": "sets the serial number",
    "RM": "resets the maintenance counters",
    "DOWNLD": "overwrites a stored protocol",
    "DRPD": "overwrites stored plate data",
    "DTSE": "overwrites the TSE assay parameters",
}

# Error codes, verbatim from the instruction manual's error-code table: (Error, Cause); "Recovery" for NEEDS_RESET.
ERRORS = {
    "0000": ("No error", ""),
    "8071": ("Invalid Command", "The received data is not a command for the instrument"),
    "8072": ("Parameter out of range", "The received parameter is out of range"),
    "8073": ("Device Not In Remote Mode", "The instrument is not in remote mode"),
    "8074": ("Device busy", "The instrument is doing a job."),
    "8075": ("Filter wheel", "Filter wheel jammed."),
    "8077": ("Light bulb burned out", "Insufficient light level"),
    "8083": ("Incubator error", "Incubator over heat."),
    "8084": ("Plate carrier error-1", "Plate carrier home sensor closed status error"),
    "8085": ("Plate carrier error-2", "Plate carrier home sensor open status error"),
    "8086": ("Plate carrier error-3", "Plate carrier jammed before reading"),
    "8087": ("Plate carrier Position", "Plate carrier jammed after reading"),
    "8088": ("Light level error", "There is too big difference of light between channels"),
    "8089": ("Filter missing", "Light is exceeding allowable range"),
    "8090": ("Disagreement of security status", "The security status from P.C was disagreement."),
    "8091": ("RS232C error", "RS232C hardware error"),
    "8092": ("A/D converter error", "Malfunction of A/D converter."),
    "8093": ("Measurement stop by stop key", 'The user input "stop" key during measurement.'),
}
# Errors whose recovery is the "RS" (reset) command.
NEEDS_RESET = {"8075", "8077", "8084", "8085", "8086", "8087", "8088", "8089", "8092", "8093"}

SPEEDS = {"fast": 1, "normal": 2}
MIX_SPEEDS = ("L", "M", "H")
ROWS, COLS = 8, 12


class M680Error(FLx800Error):
    """The Model 680 answered with a non-zero error code."""

    def __init__(self, code: str, context: str = ""):
        self.code = code
        error, cause = ERRORS.get(code, ("Unknown error", ""))
        text = f"Model 680 error {code} {error}" + (f": {cause}" if cause else "")
        if code in NEEDS_RESET:
            text += ' Recovery: "reset" command.'
        super().__init__(f"{context}: {text}" if context else text)


class M680ProtocolError(FLx800Error):
    """A response that does not follow the manual's format."""


def command(cmd: str, *args) -> bytes:
    """Encode one command line; refuses anything not in ALLOWED."""
    name = cmd.upper()
    if name in FORBIDDEN:
        raise ValueError(f"{name} is never sent: it {FORBIDDEN[name]}")
    if name not in ALLOWED:
        raise ValueError(f"{name} is not a command this program sends")
    return " ".join([DEVICE, name] + [str(a) for a in args]).encode("ascii") + CR


def parse_response_line(line: str) -> Tuple[str, str]:
    """``'ERE 0000 Model 680'`` -> ('0000', 'Model 680')."""
    m = re.match(r"^\s*ERE\s+(\d{4})(?:\s(.*))?$", line)
    if not m:
        raise M680ProtocolError(f"expected 'ERE <code>', got {line!r}")
    return m.group(1), (m.group(2) or "").strip()


_VALUE = re.compile(r"([ -]?)(\*\.\*\*\*|\d+\.\d+)")


def parse_row(line: str) -> List[Optional[float]]:
    """One row of 12 absorbance values; ``*.***`` -> None (beyond ±3.500 OD)."""
    vals: List[Optional[float]] = []
    pos = 0
    for m in _VALUE.finditer(line):
        if line[pos:m.start()].strip():
            raise M680ProtocolError(f"unexpected text in data row {line!r}")
        pos = m.end()
        sign, num = m.groups()
        vals.append(None if num.startswith("*") else (-float(num) if sign == "-" else float(num)))
    if line[pos:].strip() or len(vals) != COLS:
        raise M680ProtocolError(f"data row must hold {COLS} values: {line!r}")
    return vals


def block_checksum(rows: List[str]) -> int:
    """16-bit sum of the 8 data rows including each row's CR, sent as 4 hex digits (e.g. ``1A2B``)."""
    return (sum("".join(rows).encode("ascii")) + 13 * len(rows)) & 0xFFFF


def checksum_line(rows: List[str]) -> str:
    return f"{block_checksum(rows):04X}"


@dataclass
class Block:
    values: List[List[Optional[float]]]     # 8 x 12
    checksum: Optional[str]                  # as sent (4 hex digits)
    checksum_rule: str                       # "ok", "mismatch" or "missing"


@dataclass
class PlateData:
    reader_time: str                         # reader clock, "dd/mm/yyyy hh:mm:ss" as sent
    meas_filter: Optional[int]               # number on the "Mes. filter:" line (wheel position on the reader)
    ref_filter: Optional[int]
    meas: Block
    ref: Optional[Block]
    raw: List[str] = field(default_factory=list)   # the answer as received (saved in reader_raw.txt)
    meas_nm: Optional[int] = None            # filled in by the driver from the reader's filter wheel
    ref_nm: Optional[int] = None

    @property
    def checksum_ok(self) -> bool:
        return all(b.checksum_rule == "ok" for b in (self.meas, self.ref) if b)


def _num(text: str) -> Optional[int]:
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else None


def parse_plate(lines: List[str]) -> PlateData:
    """Parse the lines of an RPLATE/RTPLATE answer after the ERE code (header line first)."""
    reader_time, meas_f, ref_f = "", None, None
    blocks: List[Block] = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        low = s.lower()
        if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}\s+\d", s):
            reader_time = s
        elif low.startswith("mes. filter") or low.startswith("mes.filter"):
            meas_f = _num(s.split(":", 1)[-1])
        elif low.startswith("ref. filter") or low.startswith("ref.filter"):
            ref_f = _num(s.split(":", 1)[-1])
        elif low in (".begin", "begin"):
            rows = lines[i + 1:i + 1 + ROWS]
            if len(rows) < ROWS:
                raise M680ProtocolError("plate data block is shorter than 8 rows")
            values = [parse_row(r) for r in rows]
            j = i + 1 + ROWS
            cks, rule = None, "missing"
            if j < len(lines) and re.match(r"^[0-9A-Fa-f]{4}$", lines[j].strip()):
                cks = lines[j].strip().upper()
                rule = "ok" if int(cks, 16) == block_checksum(rows) else "mismatch"
                j += 1
            if j < len(lines) and lines[j].strip().lower() in (".end", "end"):
                j += 1
            blocks.append(Block(values, cks, rule))
            i = j
            continue
        i += 1
    if not blocks:
        raise M680ProtocolError("no plate data block in the answer")
    return PlateData(reader_time, meas_f, ref_f, blocks[0], blocks[1] if len(blocks) > 1 else None, list(lines))


def read_time_s(speed: str, dual: bool) -> float:
    """Plate read time from the specifications: fast 6/10 s, normal 15/30 s (single/dual wavelength)."""
    return {("fast", False): 6, ("fast", True): 10, ("normal", False): 15, ("normal", True): 30}[(speed, dual)]
