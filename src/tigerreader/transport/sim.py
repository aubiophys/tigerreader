"""In-process FLx800 simulator speaking the Appendix C serial protocol.

It models a fictional FLx800T (top probe, no incubator; a dual-probe unit with
incubator and shaker via ``SimConfig.tbi()``) closely enough for driver, CLI
and GUI development without hardware.  Timing is not simulated: responses are
produced instantly, and the temperature model follows ``time_scale``.
"""
from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from ..protocol.assay import (AssayDefinition, AssayType, Filter, FilterTable, ReadMode, SensitivityMode,
                              WellRect)
from ..protocol.data import build_block
from ..protocol.framing import ACK, DLE, ETX, NAK, SUB, CommError, ProtocolError

_NOARG = set("ACeHhJoPSwXx$*}E")
_FIXED = {"f": 1, "g": 2, "i": 1, "q": 1, "R": 1, "v": 111, "B": 8, "I": 11, ">": 1}
_ETX_TERM = {"m", "z", "c", "d"}

DEFAULT_TABLE = FilterTable(                       # a fictional, typical filter set
    excitation=[Filter(360, 40), Filter(485, 20), Filter(530, 25), Filter.plug()],
    emission=[Filter(460, 40), Filter(528, 20), Filter(580, 50), Filter(620, 40)],
)


@dataclass
class SimConfig:
    """A fictional FLx800T (top probe, no incubator)."""

    serial: str = "000000  "            # 8 chars, left-aligned, as the reader sends it
    part_number: str = "0000000"
    version: str = "0.00   "
    config_word: int = 0x0544           # FLx800 top probe, no incubator
    probe_sizes: tuple[int, int] = (45, 30)  # bottom, top (0.1 mm) as reported by 'P'
    features: set[int] = field(default_factory=lambda: {1001, 1003, 1004, 1008, 1010, 1017,
                                                        1021, 1022, 1028})
    supports_z: bool = True
    ambient_c: float = 23.5
    time_scale: float = 1.0             # >1 speeds up the incubator model
    pace: float = 0.0                   # 0 = data at once; >0 = release read data in real time x pace
                                        # (1.0 = as slow as the real reader, 0.05 = 20x faster)
    seed: int = 800

    @classmethod
    def tbi(cls) -> "SimConfig":
        """A dual-probe instrument with incubator and shaker (FLx800TBI), for testing those paths."""
        return cls(config_word=0x094C,
                   features={1001, 1003, 1004, 1005, 1008, 1009, 1010, 1017, 1021, 1022, 1028, 1031})


class SimulatedFLx800:
    def __init__(self, config: Optional[SimConfig] = None):
        self.cfg = config or SimConfig()
        self.table = FilterTable(list(DEFAULT_TABLE.excitation), list(DEFAULT_TABLE.emission))
        self.out = bytearray()
        self._cmd: Optional[str] = None
        self._args = bytearray()
        self.latched = "0000"
        self.quiet = True
        self.include_temp = False
        self.new_abort = False
        self.carrier_out = True
        self.setpoint = 0
        self._temp = self.cfg.ambient_c
        self._t_last = time.monotonic()
        self.assay: Optional[AssayDefinition] = None
        self.last_sensitivity = [100, 100]
        self.next_error: Optional[str] = None   # inject a status code into the next read
        self.log: list[str] = []
        self.baud_code = 0
        self.release: list[tuple[int, float]] = []   # (byte offset in out, monotonic release time)
        self._read_t0 = 0.0

    # ------------------------------------------------------------------ I/O
    def feed(self, data: bytes) -> None:
        for b in data:
            self._feed_byte(b)

    def _send(self, data: bytes | str) -> None:
        self.out += data.encode("ascii") if isinstance(data, str) else data

    def _hold(self, offset_s: float) -> None:
        """Paced mode: bytes sent from now on become readable *offset_s* (scaled) after 'S'."""
        if self.cfg.pace > 0:
            self.release.append((len(self.out), self._read_t0 + offset_s * self.cfg.pace))

    def available(self) -> int:
        """Number of bytes the host may read now (all of them unless paced)."""
        now = time.monotonic()
        while self.release and self.release[0][1] <= now:
            self.release.pop(0)
        return self.release[0][0] if self.release else len(self.out)

    def _status(self, code: str = "0000") -> None:
        self._send(code + "\x03")
        if code != "0000":
            self.latched = code

    def _feed_byte(self, b: int) -> None:
        ch = chr(b)
        if self._cmd is None:
            if ch in _NOARG and not (ch == "E" and 1024 not in self.cfg.features):
                self.log.append(ch)
                if ch not in "Xx":
                    self._send(bytes([ACK]))
                self._dispatch(ch, b"")
            elif ch in _FIXED or ch in _ETX_TERM:
                if ch == "z" and not self.cfg.supports_z:
                    self._send(bytes([NAK]))
                    return
                if ch in ("B", "I", ">"):
                    self._send(bytes([NAK]))  # not supported by the simulated unit
                    return
                self.log.append(ch)
                self._send(bytes([ACK]))
                self._cmd, self._args = ch, bytearray()
            else:
                self._send(bytes([NAK]))  # unexpected byte: clear buffer + NAK
            return
        # collecting arguments
        self._args.append(b)
        c = self._cmd
        if (c in _FIXED and len(self._args) == _FIXED[c]) or (c in _ETX_TERM and b == ETX):
            args = bytes(self._args[:-1]) if c in _ETX_TERM else bytes(self._args)
            self._cmd = None
            self._dispatch(c, args)

    # ------------------------------------------------------------------ helpers
    def temperature(self) -> float:
        now = time.monotonic()
        dt = (now - self._t_last) * self.cfg.time_scale
        self._t_last = now
        target = self.setpoint if self.setpoint else self.cfg.ambient_c
        self._temp = target + (self._temp - target) * math.exp(-dt / 120.0)
        return self._temp

    def _signal(self, fs, rc: tuple[int, int], k: int, fs_i: int, rng: random.Random) -> Optional[int]:
        r, c = rc
        lum = self.assay is not None and self.assay.assay_type == AssayType.LUMINESCENCE
        sens = fs.sensitivity if fs.sensitivity_mode == SensitivityMode.FIXED else self.last_sensitivity[fs_i]
        gain = (sens / 100.0) ** 3 if sens else 0.0
        # plate pattern: column 1 = 2-fold dilution series down the rows, column 12 = blank
        if c == 1:
            conc = 2.0 ** -(r - 1)
        elif c == 12:
            conc = 0.0
        else:
            conc = 0.02 * ((r * 7 + c * 3) % 11) / 10
        spectral = 1.0 if lum else max(0.2, 1.0 - abs(fs.emission.wavelength - fs.excitation.wavelength - 45) / 300)
        kinetic = 1.0 + 0.05 * k
        base = 150 if lum else 300
        v = (base + 60000 * conc * spectral * kinetic) * gain
        v *= 1.0 + rng.gauss(0, 0.015)
        if fs.optics == 0:  # bottom probe a bit less efficient in this model
            v *= 0.85
        if v > 99998:
            return None
        return max(0, int(round(v)))

    def _block(self, fs, rect: WellRect, k: int, fs_i: int, elapsed: float) -> bytes:
        rng = random.Random(hash((self.cfg.seed, k, fs_i, rect.first_row, rect.first_col)))
        vals = [[self._signal(fs, (r, c), k, fs_i, rng)
                 for c in range(rect.first_col, rect.last_col + 1)]
                for r in range(rect.first_row, rect.last_row + 1)]
        t = self.temperature() if self.include_temp else None
        return build_block(vals, elapsed, t)

    # ------------------------------------------------------------------ commands
    def _dispatch(self, c: str, args: bytes) -> None:  # noqa: C901 - a big switch is clearest here
        if c in "AJ":
            self.carrier_out = c == "J"
            self._status()
        elif c == "C":
            self._send(self.cfg.serial)
            self._status()
        elif c == "e":
            self._send(self.cfg.part_number + "  Version " + self.cfg.version)
            self._status()
        elif c == "}":
            self._send(bytes([self.cfg.config_word & 0xFF, self.cfg.config_word >> 8]))
        elif c == "P":
            self._send(f"{self.cfg.probe_sizes[0]:03d}{self.cfg.probe_sizes[1]:03d}1")
            self._status()
        elif c == "o":
            code, self.latched = self.latched, "0000"
            self._send(code + "\x03")
        elif c == "q":
            self.quiet = args == b"1"
            self._status()
        elif c == "i":
            self.include_temp = args == b"1"
            self._status()
        elif c == "R":
            self.baud_code = int(args)
            self._status()
        elif c == "z":
            code = int(args)
            avail = code in self.cfg.features
            if code == 1017 and avail:
                self.new_abort = True
            resp = "25,50,12,55,0000" if (code == 1031 and avail) else "0" * 16
            self._send(("1" if avail else "0") + resp)
            self._status()
        elif c == "w":
            self._send(self.table.encode())
            self._status()
        elif c == "m":
            try:
                self.table = FilterTable.decode(args)
                self._status()
            except (ProtocolError, ValueError):
                self._status("2100")
        elif c == "h":
            has = 1009 in self.cfg.features
            self._send(f"{int(round(self.temperature() * 10)):03d}" if has else "000")
            self._status()
        elif c == "H":
            self._send(f"{self.setpoint:02d}")
            self._status()
        elif c == "g":
            sp = int(args)
            if sp != 0 and not 25 <= sp <= 50:
                self._status("2100")
            else:
                self.temperature()
                self.setpoint = sp
                self._status()
        elif c == "f":
            i = int(args) - 1
            self._send(f"{self.last_sensitivity[i] if 0 <= i < 2 else 0:03d}")
            self._status()
        elif c == "v":
            self._set_assay(args)
        elif c == "$":
            if self.assay is None:
                self._send("0000.0")
                self._status("1600")
            else:
                t = float(math.ceil(self.assay.estimated_pass_s()))
                self._send(f"{t:06.1f}")
                self._status()
        elif c == "S":
            self._read_plate()
        elif c == "d":
            self._read_wellset(args)
        elif c == "c":
            self._status("2100")
        elif c in "Xx":
            code = "0100" if c == "X" else "0101"
            if self.release:   # paced read in progress: drop what the reader has not sent yet
                del self.out[self.available():]
                self.release.clear()
                self.carrier_out = True
            self._send(bytes([DLE if self.new_abort else SUB]) + code.encode())
        elif c == "*":
            self._send(f"SYSTEM SELF TEST\r\n{self.cfg.part_number}  Version {self.cfg.version.strip()}  "
                       f"{self.cfg.serial.strip()}\r\nSIMULATED TEST  PASS\r\nSYSTEM TEST PASS\r\n")
            self._status()
        else:  # pragma: no cover
            self._status("2100")

    def _set_assay(self, args: bytes) -> None:
        try:
            a = AssayDefinition.decode(args)
        except (ProtocolError, ValueError, KeyError):
            self._status("1600")
            return
        problems = a.validate()
        for i, fs in enumerate(a.filter_sets, 1):
            if not self.table.has(fs.excitation, emission=False) or not self.table.has(fs.emission, emission=True):
                self._status(f"0E0{i}")
                return
        if any("overlap" in p for p in problems):
            self._status("1F00")
            return
        if problems:
            self._status("2100")
            return
        if a.read_mode == ReadMode.DISPENSE_READ:
            self._status("2D08")
            return
        self.assay = a
        self.include_temp = False  # reset on every new definition (App. C)
        self._status()

    def _read_plate(self) -> None:
        if self.assay is None:
            self._status("1600")
            return
        if self.next_error and self.next_error.startswith("init:"):
            self._status(self.next_error[5:])
            self.next_error = None
            return
        self._status()  # initial status
        self.carrier_out = False
        self._read_t0 = time.monotonic()
        a = self.assay
        per_set = a.estimated_pass_s() / max(1, len(a.filter_sets))
        cycle = a.kinetic_interval_s or a.estimated_pass_s()
        for rect in a.serial_autosense_blocks():
            self._send(self._block(a.filter_sets[0], rect, 0, 0, 0.0))
        if a.serial_autosense_blocks():
            self._send(bytes([SUB]))
            self._status()
            self.carrier_out = True
            return
        for i, fs in enumerate(a.filter_sets):
            if fs.sensitivity_mode == SensitivityMode.AUTO_ONBOARD:
                self.last_sensitivity[i] = 87  # pretend the on-board search chose 87
        for k in range(a.n_kinetic):
            for i, fs in enumerate(a.filter_sets):
                if self.next_error and k == max(0, a.n_kinetic - 1) and i == len(a.filter_sets) - 1:
                    self._send(bytes([SUB]))
                    self._status(self.next_error)
                    self.next_error = None
                    self.carrier_out = True
                    return
                self._hold(k * cycle + (i + 1) * per_set)
                self._send(self._block(fs, a.read_rect, k, i, k * cycle))
        self._hold((a.n_kinetic - 1) * cycle + len(a.filter_sets) * per_set + 2.3)
        self._send(bytes([SUB]))
        self._status()
        self.carrier_out = True

    def _read_wellset(self, args: bytes) -> None:
        if self.assay is None or len(args) != 33:
            self._status("1600" if self.assay is None else "2100")
            return
        self._status()
        wells = [(int(args[i:i + 2]), int(args[i + 2:i + 4])) for i in range(0, 32, 4)]
        for i, fs in enumerate(self.assay.filter_sets):
            rng = random.Random(self.cfg.seed + i)
            vals = [[(self._signal(fs, w, 0, i, rng) if w[0] else 0) for w in wells]]
            self._send(build_block(vals, 0.0, self.temperature() if self.include_temp else None))
        self._send(bytes([SUB]))
        self._status()


class SimTransport:
    """Transport that talks to an in-process :class:`SimulatedFLx800` (thread-safe, so an abort
    can be sent from the GUI thread while a worker thread is reading)."""

    def __init__(self, sim: Optional[SimulatedFLx800] = None):
        self.sim = sim or SimulatedFLx800()
        self.is_open = False
        self._lock = threading.Lock()

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def flush_input(self) -> None:
        with self._lock:
            n = self.sim.available()
            del self.sim.out[:n]
            self.sim.release = [(pos - n, t) for pos, t in self.sim.release]

    def write(self, data: bytes) -> None:
        if not self.is_open:
            raise CommError("port not open")
        with self._lock:
            self.sim.feed(data)

    def read_exact(self, n: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                avail = self.sim.available()
                if avail >= n:
                    data = bytes(self.sim.out[:n])
                    del self.sim.out[:n]
                    self.sim.release = [(pos - n, t) for pos, t in self.sim.release]
                    return data
                pending = bool(self.sim.release)
                if not pending or time.monotonic() >= deadline:
                    got = bytes(self.sim.out[:avail])
                    del self.sim.out[:avail]
                    self.sim.release = [(pos - avail, t) for pos, t in self.sim.release]
                    raise CommError(f"timeout: wanted {n} bytes, got {len(got)}: {got!r}")
            time.sleep(0.005)

    def set_baudrate(self, baud: int) -> None:
        pass
