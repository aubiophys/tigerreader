"""In-process Bio-Rad Model 680 simulator (remote-control command language), for tests and development.

Defaults: a fictional unit with incubator; filters 405, 450, 490, 540, 595, 630, 655 nm, position 8 empty.
Plate values: a smooth gradient with a little noise; H12 is beyond 3.5 OD (*.***) and A12 is slightly negative,
so both special cases appear in every read. ``pace`` scales the real read times (0 = instant).
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from ..protocol.framing import CommError
from .protocol import COLS, MIX_SPEEDS, ROWS, checksum_line, read_time_s


@dataclass
class Sim680Config:
    filters: List[Optional[int]] = field(default_factory=lambda: [405, 450, 490, 540, 595, 630, 655, None])
    serial: str = "00000"
    has_incubator: bool = True
    ambient_c: float = 23.0
    pace: float = 0.0
    bad_checksum_once: bool = False      # corrupt the next plate's first checksum (RTPLATE then sends it right)
    seed: int = 680
    aq_fails_first: bool = False        # answer the first AQ with 8073 (as the reader sometimes does)


def _fmt(v: Optional[float]) -> str:
    if v is None or abs(v) > 3.5:
        return " *.***"
    return f"-{-v:.3f}" if v < 0 else f" {v:.3f}"


class Simulated680:
    def __init__(self, config: Optional[Sim680Config] = None):
        self.cfg = config or Sim680Config()
        self.remote = False
        self.inc_on = False
        self.setpoint = 37.0
        self.next_error: Optional[str] = None    # inject an error code into the next RPLATE
        self.log: List[str] = []
        self.plates = 0
        self._rng = random.Random(self.cfg.seed)
        self._last_plate: Optional[str] = None

    # ------------------------------------------------------------------ plate model
    def _value(self, r: int, c: int, nm: int) -> Optional[float]:
        if (r, c) == (8, 12):
            return None
        if (r, c) == (1, 12):
            return -0.004
        if nm >= 600:                                     # typical reference wavelength: plate background only
            return round(0.040 + 0.001 * r + self._rng.gauss(0, 0.002), 3)
        base = 0.045 + 0.30 * (r - 1) + 0.02 * (c - 1)
        return round(base * (1.0 + (450 - nm) / 2000) + self._rng.gauss(0, 0.002), 3)

    def _block(self, nm: int) -> List[str]:
        rows = ["".join(_fmt(self._value(r, c, nm)) for c in range(1, COLS + 1)) for r in range(1, ROWS + 1)]
        return [".begin"] + rows + [checksum_line(rows), ".end"]

    # ------------------------------------------------------------------ commands
    def handle(self, line: str):
        """Return (response text, seconds the reader needs before answering)."""
        words = line.strip().split()
        self.log.append(line.strip())
        if len(words) < 2 or words[0].upper() != "EIA.READER":
            return "ERE 8071\r", 0.0
        cmd, args = words[1].upper(), words[2:]
        if cmd != "AQ" and not self.remote:
            return "ERE 8073\r", 0.0
        if cmd == "AQ" and self.cfg.aq_fails_first:
            self.cfg.aq_fails_first = False
            return "ERE 8073\r", 0.0
        if cmd == "AQ":
            self.remote = True
            return "ERE 0000\r", 0.0
        if cmd == "RL":
            self.remote = False
            return "ERE 0000\r", 0.0
        if cmd == "RS":
            self.remote = False
            return "ERE 0000\r", 2.0
        if cmd == "ID":
            return "ERE 0000 Model 680\r", 0.0
        if cmd == "GSN":
            return f"ERE 0000 {self.cfg.serial:5.5s}\r", 0.0
        if cmd == "MR":
            return f"ERE 0000\rOn/Off:0123\rHours :0456\rPlates:{self.plates:04d}\r", 0.0
        if cmd == "FSTATUS":
            return "ERE 0000\r" + " ".join(str(f) if f else "***" for f in self.cfg.filters) + "\r", 0.0
        if cmd == "ISTATUS":
            t = self.setpoint if self.inc_on else self.cfg.ambient_c
            return (f"ERE 0000 {int(self.cfg.has_incubator)} {int(self.inc_on)} {self.setpoint:.1f} {t:.1f}\r"), 0.0
        if cmd == "INCU":
            if not args or args[0] not in ("0", "1"):
                return "ERE 8072\r", 0.0
            self.inc_on = args[0] == "1"
            return "ERE 0000\r", 0.0
        if cmd == "WINC":
            try:
                t = float(args[0])
            except (IndexError, ValueError):
                return "ERE 8072\r", 0.0
            if not 25.0 <= t <= 45.0:
                return "ERE 8072\r", 0.0
            self.setpoint = t
            return "ERE 0000\r", 0.0
        if cmd == "RPLATE":
            return self._rplate(args)
        if cmd == "RTPLATE":
            return (self._last_plate or "ERE 8072\r"), 0.0
        return "ERE 8071\r", 0.0

    def _rplate(self, args: List[str]):
        try:
            speed, mix_t, mix_s, wp = int(args[0]), int(args[1]), args[2].upper(), int(args[3])
            ref = int(args[4]) if len(args) > 4 else None
        except (IndexError, ValueError):
            return "ERE 8072\r", 0.0
        if speed not in (1, 2) or not 0 <= mix_t <= 999 or mix_s not in MIX_SPEEDS:
            return "ERE 8072\r", 0.0
        nms = []
        for p in [wp] + ([ref] if ref else []):
            if not 1 <= p <= 8:
                return "ERE 8072\r", 0.0
            if self.cfg.filters[p - 1] is None:
                return "ERE 8089\r", 0.0
            nms.append(self.cfg.filters[p - 1])
        if self.next_error:
            code, self.next_error = self.next_error, None
            return f"ERE {code}\r", 0.0
        self.plates += 1
        lines = ["ERE 0000 BIO-RAD Model 680 Microplate READER", datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                 f"Mes. filter:{wp}"] + ([f"Ref. filter:{ref}"] if ref else [])    # wheel positions
        for k, nm in enumerate(nms):
            lines += (["" ] if k else []) + self._block(nm)                     # blank line between blocks
        text = "\r".join(lines) + "\r"
        self._last_plate = text
        if self.cfg.bad_checksum_once:
            self.cfg.bad_checksum_once = False
            i = lines.index(".end") - 1
            text = "\r".join(lines[:i] + ["0000"] + lines[i + 1:]) + "\r"
        dur = read_time_s("fast" if speed == 1 else "normal", ref is not None) + mix_t
        return text, dur


class Sim680Transport:
    """Transport for :class:`Simulated680`: answers arrive after the simulated read time × pace."""

    def __init__(self, sim: Optional[Simulated680] = None):
        self.sim = sim or Simulated680()
        self.is_open = False
        self._in = bytearray()
        self._out = bytearray()
        self._ready_at = 0.0
        self._lock = threading.Lock()

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def flush_input(self) -> None:
        with self._lock:
            if time.monotonic() >= self._ready_at:
                self._out.clear()

    def set_baudrate(self, baud: int) -> None:
        pass

    def write(self, data: bytes) -> None:
        if not self.is_open:
            raise CommError("port not open")
        with self._lock:
            self._in += data
            while b"\r" in self._in:
                line, _, rest = bytes(self._in).partition(b"\r")
                self._in = bytearray(rest)
                text, dur = self.sim.handle(line.decode("ascii", errors="replace"))
                self._out += text.encode("ascii")
                self._ready_at = time.monotonic() + dur * self.sim.cfg.pace

    def read_exact(self, n: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if time.monotonic() >= self._ready_at and len(self._out) >= n:
                    out = bytes(self._out[:n])
                    del self._out[:n]
                    return out
            if time.monotonic() > deadline:
                raise CommError(f"timeout: wanted {n} bytes")
            time.sleep(0.005)
