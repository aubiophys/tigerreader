"""Synchronous driver for the Bio-Rad Model 680 absorbance reader.

Usage::

    from tigerreader.m680 import Model680
    with Model680.open_serial("COM6", trace_path="trace.log") as rdr:
        print(rdr.info.summary())
        run = rdr.read(meas_nm=450, ref_nm=655)

Remote mode: ``AQ`` on connect (locks the keypad), ``RL`` on close. The reader has no remote kinetic mode
and no abort command: kinetic reads are timed here, and an abort takes effect between plate reads.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..protocol.framing import CommError
from .protocol import (MIX_SPEEDS, SPEEDS, M680Error, M680ProtocolError, PlateData, command, parse_plate,
                       parse_response_line, read_time_s)

LINE_TIMEOUT = 3.0        # simple commands answer at once
MULTI_LINE = {"MR": 3, "FSTATUS": 1}   # lines that follow "ERE 0000" on their own line


@dataclass
class M680Info:
    ident: str = ""
    serial: str = ""
    filters: Dict[int, Optional[int]] = field(default_factory=dict)   # position 1-8 -> nm (None = empty)
    has_incubator: bool = False
    maintenance: Dict[str, str] = field(default_factory=dict)

    @property
    def model(self) -> str:
        return "Bio-Rad Model 680" + (" (incubator)" if self.has_incubator else "")

    def position(self, nm: int) -> int:
        for pos, w in sorted(self.filters.items()):
            if w == nm:
                return pos
        installed = ", ".join(str(w) for _, w in sorted(self.filters.items()) if w)
        raise ValueError(f"no {nm} nm filter in the reader (installed: {installed or 'none'})")

    def summary(self) -> str:
        lines = [f"Instrument    : {self.ident or 'Model 680'}",
                 f"Serial number : {self.serial}",
                 "Filters       : " + "  ".join(f"{p}:{w if w else 'empty'}" for p, w in sorted(self.filters.items())),
                 f"Incubator     : {'yes' if self.has_incubator else 'no'}"]
        if self.maintenance:
            lines.append("Counters      : " + ", ".join(f"{k} {v}" for k, v in self.maintenance.items()))
        return "\n".join(lines)


@dataclass
class PlateRead:
    index: int                 # 0-based read number (kinetic)
    t_s: float                 # start of this read, seconds after the run started
    received_s: float          # data received, seconds after the run started
    data: PlateData


@dataclass
class M680Run:
    started: datetime
    meas_nm: int
    ref_nm: Optional[int]
    speed: str
    mix_time_s: int
    mix_speed: str
    n_reads: int = 1
    interval_s: float = 0.0
    reads: List[PlateRead] = field(default_factory=list)
    finished: Optional[datetime] = None
    aborted: bool = False
    error: str = ""

    @property
    def kinetic(self) -> bool:
        return self.n_reads > 1

    @property
    def ok(self) -> bool:
        return not self.aborted and not self.error and len(self.reads) == self.n_reads


class Model680:
    def __init__(self, transport):
        self.t = transport
        self.info = M680Info()
        self.remote = False
        self._lock = threading.RLock()
        self._abort = threading.Event()

    # ------------------------------------------------------------------ opening
    @classmethod
    def open_serial(cls, port: str, trace_path: Optional[str | Path] = None, rtscts: bool = False,
                    identify: bool = True) -> "Model680":
        from ..transport.serial_port import SerialTransport
        return cls._open(SerialTransport(port, 9600, stopbits=1, rtscts=rtscts), trace_path, identify)

    @classmethod
    def open_sim(cls, trace_path: Optional[str | Path] = None, sim=None, identify: bool = True) -> "Model680":
        from .sim import Sim680Transport
        return cls._open(Sim680Transport(sim), trace_path, identify)

    @classmethod
    def _open(cls, transport, trace_path, identify) -> "Model680":
        if trace_path is not None:
            from ..transport.trace import TracingTransport
            transport = TracingTransport(transport, open(trace_path, "a", encoding="ascii"), flush_on=(b"\r",))
        transport.open()
        rdr = cls(transport)
        if identify:
            try:
                rdr.connect()
            except Exception:
                transport.close()
                raise
        return rdr

    def __enter__(self) -> "Model680":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self.remote:
                try:
                    self._transact("RL")
                except Exception:  # noqa: BLE001 - closing anyway
                    pass
                self.remote = False
            self.t.close()

    # ------------------------------------------------------------------ low level
    def _readline(self, timeout: float) -> str:
        buf = bytearray()
        while True:
            b = self.t.read_exact(1, timeout)
            if b == b"\r":
                return buf.decode("ascii", errors="replace")
            if b != b"\n":
                buf += b

    def _transact(self, cmd: str, *args, timeout: float = LINE_TIMEOUT, extra_lines: int = 0) -> List[str]:
        """Send one command; return [data of the ERE line] + *extra_lines* following lines."""
        with self._lock:
            self.t.flush_input()
            self.t.write(command(cmd, *args))
            line = self._readline(timeout)
            while not line.strip():                       # tolerate blank lines before the answer
                line = self._readline(timeout)
            code, data = parse_response_line(line)
            if code != "0000":
                raise M680Error(code, cmd)
            out = [data]
            out += [self._readline(LINE_TIMEOUT) for _ in range(extra_lines)]
            return out

    # ------------------------------------------------------------------ commands
    def connect(self) -> M680Info:
        try:
            self._transact("AQ")
        except M680Error as exc:
            # The first AQ after opening the port can be answered 8073; retry once.
            if exc.code != "8073":
                raise
            time.sleep(0.3)
            self._transact("AQ")
        self.remote = True
        info = M680Info()
        info.ident = self._transact("ID")[0]
        info.serial = self._transact("GSN")[0]
        info.filters = self._parse_filters(self._transact("FSTATUS", extra_lines=1))
        info.has_incubator = self.incubator()["present"]
        for ln in self._transact("MR", extra_lines=3)[1:]:
            k, _, v = ln.partition(":")
            info.maintenance[k.strip()] = v.strip()
        self.info = info
        return info

    @staticmethod
    def _parse_filters(lines: List[str]) -> Dict[int, Optional[int]]:
        words = " ".join(lines).split()
        if len(words) != 8:
            raise M680ProtocolError(f"filter status must list 8 positions: {words}")
        return {i + 1: (int(w) if w.isdigit() else None) for i, w in enumerate(words)}

    def reset(self) -> None:
        """RS: reset to the power-up state (recovery from errors 8075-8093). Leaves remote mode."""
        self._transact("RS", timeout=60)
        self.remote = False
        self.connect()

    def incubator(self) -> dict:
        w = self._transact("ISTATUS")[0].split()
        if len(w) < 2:
            raise M680ProtocolError(f"incubator status: {w}")
        f = [float(x) if x.replace(".", "", 1).isdigit() else None for x in w[2:4]] + [None, None]
        return {"present": w[0] == "1", "on": w[1] == "1", "setpoint_c": f[0], "temperature_c": f[1]}

    def set_incubator(self, temp_c: Optional[float]) -> None:
        """Switch the incubator on at *temp_c* (25.0–45.0 °C, 0.1 steps) or off (None)."""
        if not self.info.has_incubator:
            raise ValueError("this Model 680 has no incubator")
        if temp_c is None:
            self._transact("INCU", 0)
            return
        if not 25.0 <= temp_c <= 45.0:
            raise ValueError("incubator set point must be 25.0–45.0 °C (and at least 5 °C above room temperature)")
        self._transact("WINC", f"{temp_c:.1f}")
        self._transact("INCU", 1)

    def _rplate_args(self, meas_nm: int, ref_nm: Optional[int], speed: str, mix_time_s: int, mix_speed: str):
        if speed not in SPEEDS:
            raise ValueError(f"speed must be one of {list(SPEEDS)}")
        if not 0 <= int(mix_time_s) <= 999:
            raise ValueError("mixing time must be 0–999 s")
        if mix_speed.upper() not in MIX_SPEEDS:
            raise ValueError("mixing speed must be L, M or H")
        if ref_nm is not None and ref_nm == meas_nm:
            raise ValueError("the reference wavelength must differ from the measurement wavelength")
        args = [SPEEDS[speed], int(mix_time_s), mix_speed.upper(), self.info.position(meas_nm)]
        if ref_nm is not None:
            args.append(self.info.position(ref_nm))
        return args

    def read_plate(self, meas_nm: int, ref_nm: Optional[int] = None, speed: str = "normal", mix_time_s: int = 0,
                   mix_speed: str = "L") -> PlateData:
        """One RPLATE: the whole 96-well plate at one (or two) wavelengths."""
        args = self._rplate_args(meas_nm, ref_nm, speed, mix_time_s, mix_speed)
        timeout = 2 * read_time_s(speed, ref_nm is not None) + int(mix_time_s) + 30
        n_blocks = 2 if ref_nm is not None else 1
        with self._lock:
            data = self._plate_answer("RPLATE", args, timeout, n_blocks)
            if not data.checksum_ok:                      # transmission error: ask the reader to send it again
                data = self._plate_answer("RTPLATE", [], LINE_TIMEOUT * 4, n_blocks)
                if not data.checksum_ok:
                    raise M680ProtocolError("plate data checksum wrong twice (RPLATE and RTPLATE)")
        # the filter lines carry wheel positions; make sure the reader used the filters asked for
        expected = [(data.meas_filter, args[3], meas_nm), (data.ref_filter, args[4] if ref_nm else None, ref_nm)]
        for got, pos, nm in expected:
            if got not in (pos, nm):
                raise M680ProtocolError(f"reader reports filter {got}, expected position {pos} ({nm} nm)")
        data.meas_nm, data.ref_nm = meas_nm, ref_nm
        return data

    def _plate_answer(self, cmd: str, args, timeout: float, n_blocks: int) -> PlateData:
        first = self._transact(cmd, *args, timeout=timeout)
        lines = [first[0]]
        ends = 0
        while ends < n_blocks:
            ln = self._readline(LINE_TIMEOUT)
            lines.append(ln)
            if ln.strip().lower() in (".end", "end"):
                ends += 1
        return parse_plate(lines)

    def abort(self) -> None:
        """Stop a run after the plate read in progress (the reader has no abort command)."""
        self._abort.set()

    def read(self, meas_nm: int, ref_nm: Optional[int] = None, speed: str = "normal", mix_time_s: int = 0,
             mix_speed: str = "L", n_reads: int = 1, interval_s: float = 0.0,
             on_plate: Optional[Callable[[PlateRead], None]] = None) -> M680Run:
        """Endpoint (n_reads=1) or host-timed kinetic run. *interval_s* is start-to-start; a read that is
        longer than the interval is followed at once by the next one."""
        self._rplate_args(meas_nm, ref_nm, speed, mix_time_s, mix_speed)       # validate before starting
        if n_reads < 1:
            raise ValueError("number of reads must be at least 1")
        self._abort.clear()
        run = M680Run(datetime.now(), meas_nm, ref_nm, speed, int(mix_time_s), mix_speed.upper(), n_reads,
                      float(interval_s))
        t0 = time.monotonic()
        try:
            for k in range(n_reads):
                if k:
                    wait = t0 + k * interval_s - time.monotonic()
                    if self._abort.wait(max(0.0, wait)):
                        run.aborted = True
                        break
                elif self._abort.is_set():
                    run.aborted = True
                    break
                start = time.monotonic() - t0
                data = self.read_plate(meas_nm, ref_nm, speed, mix_time_s, mix_speed)
                pr = PlateRead(k, start, time.monotonic() - t0, data)
                run.reads.append(pr)
                if on_plate is not None:
                    on_plate(pr)
                if self._abort.is_set() and k < n_reads - 1:
                    run.aborted = True
                    break
        except (M680Error, CommError, M680ProtocolError) as exc:
            run.error = str(exc)
        run.finished = datetime.now()
        return run
