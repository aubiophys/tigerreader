"""High-level, synchronous FLx800 driver.

Usage::

    from tigerreader import FLx800
    with FLx800.open_serial("COM5", trace_path="trace.log") as rdr:
        print(rdr.info)
        table = rdr.get_filter_table()
        result = rdr.read(assay)

Thread-safety: every public method takes an internal lock, so a GUI worker
thread and a status poller can share one instance.  The protocol is strictly
half-duplex: one transaction at a time.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ..protocol.assay import AssayDefinition, FilterTable, ReadMode, ReadSeries, SensitivityMode
from ..protocol.commands import COMMANDS, FEATURES, INSTRUMENT_CONFIG, Safety
from ..protocol.data import DataBlock, ReadResponse, read_stream
from ..protocol.framing import (ACK, NAK, STATUS_LEN, CommError, FLx800Error, NakError, ProtocolError,
                                ReaderError, Status, num, parse_status)

BAUD_CODES = {9600: b"0", 19200: b"1", 38400: b"2"}


@dataclass
class InstrumentInfo:
    serial: Optional[str] = None
    part_number: Optional[str] = None
    version: Optional[str] = None
    config_word: Optional[int] = None
    bottom_probe_mm: Optional[float] = None
    top_probe_mm: Optional[float] = None
    current_probe: Optional[str] = None
    features: dict[int, bool] = field(default_factory=dict)
    feature_data: dict[int, str] = field(default_factory=dict)
    z_supported: Optional[bool] = None
    incubation_range: Optional[tuple[int, int, int, int]] = None

    @property
    def config_text(self) -> str:
        if self.config_word is None:
            return "unknown"
        return INSTRUMENT_CONFIG.get(self.config_word, f"unknown (0x{self.config_word:04X})")

    @property
    def has_incubator(self) -> bool:
        """Configuration word bit 0x0008 (0x..4C) = incubator; else fall back to feature 1009."""
        if self.config_word is not None:
            return bool(self.config_word & 0x0008)
        return self.features.get(1009, False)

    @property
    def probes(self) -> tuple[bool, bool]:
        """(has_top, has_bottom) from the '}' configuration word (unknown -> both allowed)."""
        if self.config_word is None:
            return True, True
        kind = (self.config_word >> 8) & 0x0C  # 0 bottom only, 4 top only, 8 dual
        return kind in (4, 8), kind in (0, 8)

    @property
    def has_shaker(self) -> bool:
        if self.features:
            return self.features.get(1005, False)
        return self.has_incubator  # all incubated models shake (operator's manual)

    @property
    def model(self) -> str:
        top, bottom = self.probes
        name = "FLx800" + ("T" if top else "") + ("B" if bottom else "") + ("I" if self.has_incubator else "")
        return name if self.config_word is not None else "FLx800 (configuration unknown)"

    def check_assay(self, a: AssayDefinition) -> list[str]:
        """Problems running *a* on this particular instrument (probe, shaker, …)."""
        p: list[str] = []
        top, bottom = self.probes
        for i, fs in enumerate(a.filter_sets, 1):
            if fs.optics == 0 and not bottom:
                p.append(f"filter set {i}: this {self.model} has no bottom probe - use top optics")
            if fs.optics == 1 and not top:
                p.append(f"filter set {i}: this {self.model} has no top probe - use bottom optics")
        if int(a.shake_mode) and not self.has_shaker:
            p.append(f"this {self.model} has no shaker - set shake mode to none")
        return p

    def summary(self) -> str:
        lines = [
            f"Serial number : {self.serial}",
            f"Basecode      : {self.part_number} v{(self.version or '').strip()}",
            f"Model         : {self.model}   (configuration {self.config_text}"
            + (f", 0x{self.config_word:04X})" if self.config_word is not None else ")"),
            "Probes        : " + ", ".join(
                [f"top {self.top_probe_mm} mm"] * self.probes[0]
                + [f"bottom {self.bottom_probe_mm} mm"] * self.probes[1]),
        ]
        if self.z_supported is False:
            lines.append("Features      : 'z' not supported (pre-2.00 basecode, legacy features only)")
        elif self.features:
            on = [f"{c} {FEATURES.get(c, '')}" for c, v in sorted(self.features.items()) if v]
            lines.append("Features      : " + "; ".join(on))
        if not self.has_incubator:
            lines.append("Incubation    : none (no incubator on this instrument)")
        if self.incubation_range:
            a, b, c, d = self.incubation_range
            lines.append(f"Incubation    : set point {a}-{b} °C, measurement {c}-{d} °C")
        return "\n".join(lines)


@dataclass
class ReadResult:
    """Data of one run (one or more assay definitions read as a series)."""

    assays: list[AssayDefinition]
    blocks: list[list[DataBlock]]              # per definition: blocks in arrival order
    statuses: list[Status]
    started: datetime
    finished: Optional[datetime] = None
    aborted: bool = False
    sensitivities: dict[int, int] = field(default_factory=dict)  # filter-set index -> chosen gain

    @property
    def ok(self) -> bool:
        return not self.aborted and all(s.ok for s in self.statuses)

    def iter_reads(self):
        """Yield (filter_set_index, kinetic_index, assay, filter_set, block) over the whole run."""
        fs_offset = 0
        for a, blks in zip(self.assays, self.blocks):
            n_auto = len(a.serial_autosense_blocks())
            nfs = len(a.filter_sets)
            for j, blk in enumerate(blks[n_auto:]):
                k, i = divmod(j, nfs)
                yield fs_offset + i, k, a, a.filter_sets[i], blk
            fs_offset += nfs


class FLx800:
    QUERY_FEATURES = (1001, 1003, 1004, 1005, 1007, 1008, 1009, 1010, 1017, 1021, 1022,
                      1024, 1025, 1028, 1031)

    def __init__(self, transport, service_mode: bool = False):
        self.t = transport
        self.service_mode = service_mode
        self.info = InstrumentInfo()
        self._lock = threading.RLock()
        self._abort = threading.Event()
        self._reading = False

    # ------------------------------------------------------------------ construction
    @classmethod
    def open_serial(cls, port: str, baudrate: int = 9600, trace_path: Optional[str | Path] = None,
                    identify: bool = True, **kw) -> "FLx800":
        from ..transport.serial_port import SerialTransport
        return cls._open(SerialTransport(port, baudrate), trace_path, identify, **kw)

    @classmethod
    def open_sim(cls, trace_path: Optional[str | Path] = None, identify: bool = True, sim=None, **kw) -> "FLx800":
        from ..transport.sim import SimTransport
        return cls._open(SimTransport(sim), trace_path, identify, **kw)

    @classmethod
    def _open(cls, transport, trace_path, identify, **kw) -> "FLx800":
        if trace_path is not None:
            from ..transport.trace import TracingTransport
            transport = TracingTransport(transport, open(trace_path, "a", encoding="ascii"))
        transport.open()
        rdr = cls(transport, **kw)
        if identify:
            rdr.connect()
        return rdr

    def close(self) -> None:
        sink = getattr(self.t, "sink", None)
        self.t.close()
        if sink is not None:
            sink.close()

    def __enter__(self) -> "FLx800":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ low level
    def _check_allowed(self, char: str) -> None:
        cmd = COMMANDS.get(char)
        if cmd is None or cmd.safety == Safety.FORBIDDEN:
            raise FLx800Error(f"command {char!r} is not allowed")
        if cmd.safety == Safety.SERVICE and not self.service_mode:
            raise FLx800Error(f"command {char!r} ({cmd.name}) requires service mode")

    def _timeout(self, char: str) -> float:
        return COMMANDS[char].timeout_s

    def _begin(self, char: str) -> None:
        """Flush, send the command character, expect ACK."""
        self._check_allowed(char)
        self.t.flush_input()
        self.t.write(char.encode("ascii"))
        r = self.t.read_exact(1, self._timeout(char))[0]
        if r == NAK:
            raise NakError(char)
        if r != ACK:
            raise ProtocolError(f"expected ACK after {char!r}, got 0x{r:02X}")

    def _status(self, char: str) -> Status:
        return parse_status(self.t.read_exact(STATUS_LEN, self._timeout(char)))

    def transact(self, char: str, args: bytes = b"", reply_len: int = 0, check: bool = True) -> tuple[bytes, Status]:
        """Generic fixed-length transaction: cmd → ACK → args → reply_len bytes → status."""
        with self._lock:
            self._begin(char)
            if args:
                self.t.write(args)
            data = self.t.read_exact(reply_len, self._timeout(char)) if reply_len else b""
            st = self._status(char)
        if check and not st.ok:
            raise ReaderError(st, COMMANDS[char].name)
        return data, st

    # ------------------------------------------------------------------ identification / status
    def connect(self) -> InstrumentInfo:
        """Clear latched errors and identify the instrument."""
        with self._lock:
            self.get_status()
            self.get_status()
            self.identify()
        return self.info

    def identify(self) -> InstrumentInfo:
        i = self.info
        i.part_number, i.version = self.get_basecode()
        try:
            i.config_word = self.get_configuration()
        except FLx800Error:
            i.config_word = None
        try:
            b, tp, cur = self.get_probe_configuration()
            i.bottom_probe_mm, i.top_probe_mm, i.current_probe = b, tp, cur
        except FLx800Error:
            pass
        i.z_supported = None
        for code in self.QUERY_FEATURES:
            try:
                ok, data = self.check_feature(code)
            except NakError:
                i.z_supported = False
                break
            i.z_supported = True
            i.features[code] = ok
            if ok:
                i.feature_data[code] = data
        if i.features.get(1031):
            try:
                a, b, c, d = (int(x) for x in i.feature_data[1031].split(",")[:4])
                i.incubation_range = (a, b, c, d)
            except ValueError:
                pass
        if i.features.get(1021, i.z_supported is False):
            try:
                i.serial = self.get_serial_number()
            except FLx800Error:
                pass
        return i

    def get_status(self) -> Status:
        """'o': latest error status (clears it on the reader where possible)."""
        _, st = self.transact("o", check=False)
        return st

    def get_basecode(self) -> tuple[str, str]:
        data, _ = self.transact("e", reply_len=24)
        txt = data.decode("ascii", "replace")
        return txt[:7], txt[17:24]

    def get_serial_number(self) -> str:
        data, _ = self.transact("C", reply_len=8)
        return data.decode("ascii").strip()  # 8 characters, left-aligned, space-padded

    def get_configuration(self) -> int:
        """'}': 16-bit configuration word (no status string follows)."""
        with self._lock:
            self._begin("}")
            raw = self.t.read_exact(2, self._timeout("}"))
        return raw[0] | (raw[1] << 8)

    def get_probe_configuration(self) -> tuple[float, float, str]:
        data, _ = self.transact("P", reply_len=7)
        return int(data[0:3]) / 10, int(data[3:6]) / 10, "top" if data[6:7] == b"1" else "bottom"

    def check_feature(self, code: int) -> tuple[bool, str]:
        data, _ = self.transact("z", num(code, 4) + b"\x03", reply_len=17, check=False)
        return data[:1] == b"1", data[1:].decode("ascii", "replace")

    def set_quiet(self, on: bool = True) -> None:
        self.transact("q", b"1" if on else b"0")

    def set_baudrate(self, baud: int) -> None:
        with self._lock:
            self.transact("R", BAUD_CODES[baud])
            time.sleep(0.5)
            self.t.set_baudrate(baud)

    # ------------------------------------------------------------------ carrier / filters
    def carrier_out(self) -> None:
        self.transact("J")

    def carrier_in(self) -> None:
        self.transact("A")

    def get_filter_table(self) -> FilterTable:
        data, _ = self.transact("w", reply_len=56)
        return FilterTable.decode(data)

    def set_filter_table(self, table: FilterTable) -> None:
        """'m' — writes NVRAM. Requires service mode; run self_test() afterwards."""
        self.transact("m", table.encode() + b"\x03")

    # ------------------------------------------------------------------ temperature
    def _require_incubator(self) -> None:
        if not self.has_incubator:
            raise FLx800Error(f"this {self.info.model} has no incubator "
                              f"(configuration {self.info.config_text}); temperature commands are not available")

    @property
    def has_incubator(self) -> bool:
        return self.info.has_incubator if (self.info.config_word is not None or self.info.features) else True

    def get_temperature(self) -> Optional[float]:
        self._require_incubator()
        data, st = self.transact("h", reply_len=3, check=False)
        v = int(data)
        if not st.ok:
            raise ReaderError(st, "get temperature")
        return None if v in (0, 111, 999) else v / 10

    def get_setpoint(self) -> int:
        self._require_incubator()
        data, _ = self.transact("H", reply_len=2)
        return int(data)

    def set_temperature(self, celsius: int) -> None:
        """Set incubator set point (0 = off; default range 25–50 °C)."""
        self._require_incubator()
        if celsius:
            rng = self.info.incubation_range
            lo, hi = (rng[0], rng[1]) if rng else (25, 50)
            if not lo <= celsius <= hi:
                raise ValueError(f"set point must be 0 (off) or {lo}-{hi} °C")
        self.transact("g", num(int(celsius), 2))

    def include_temperature(self, on: bool = True) -> None:
        self._require_incubator()
        self.transact("i", b"1" if on else b"0")

    # ------------------------------------------------------------------ reading
    def define(self, assay: AssayDefinition, table: Optional[FilterTable] = None) -> None:
        """Validate and send a 'v' assay definition."""
        problems = assay.validate(table) + self.info.check_assay(assay)
        if problems:
            raise ValueError("invalid assay definition:\n  " + "\n  ".join(problems))
        self.transact("v", assay.encode())

    def _check_kinetic_interval(self, a: AssayDefinition, table: Optional[FilterTable],
                                fastest: bool) -> AssayDefinition:
        try:
            tmin = self.min_kinetic_interval()
        except FLx800Error:
            return a  # '$' unavailable: let the reader decide (status 1700)
        need = int(math.ceil(tmin))
        if fastest and a.kinetic_interval_s != need:
            a = a.with_(kinetic_interval_s=need)
            self.define(a, table)
        elif 0 < a.kinetic_interval_s < tmin:  # 0 = read continuously, no pause (Appendix C)
            raise ValueError(f"kinetic interval {a.kinetic_interval_s} s is shorter than this read needs: "
                             f"minimum {tmin:.1f} s for {a.read_rect.n_rows * a.read_rect.n_cols} wells x "
                             f"{len(a.filter_sets)} filter set(s). Use an interval >= {need} s "
                             "(command line: --interval min; Python: read(..., fastest=True)).")
        return a

    def min_kinetic_interval(self) -> float:
        data, _ = self.transact("$", reply_len=6)
        return float(data)

    def get_sensitivity(self, filter_set: int) -> int:
        data, _ = self.transact("f", str(filter_set).encode(), reply_len=3)
        return int(data)

    def _read_stream(self, char: str, args: bytes = b"",
                     on_block: Optional[Callable[[DataBlock], None]] = None,
                     timeout: Optional[float] = None) -> ReadResponse:
        """Streamed read. *timeout* is the longest allowed gap between bytes."""
        with self._lock:
            self._begin(char)
            if args:
                self.t.write(args)
            timeout = timeout or self._timeout(char)

            def rd(n: int) -> bytes:
                return self.t.read_exact(n, timeout)

            self._reading = True
            try:
                return read_stream(rd, on_block)
            finally:
                self._reading = False

    def read_plate(self, on_block: Optional[Callable[[DataBlock], None]] = None,
                   assay: Optional[AssayDefinition] = None) -> ReadResponse:
        """'S' with the currently loaded definition (*assay* only sizes the timeout)."""
        return self._read_stream("S", on_block=on_block, timeout=self._gap_timeout(assay))

    def _gap_timeout(self, a: Optional[AssayDefinition]) -> float:
        """Longest silence to accept during a read: kinetic waits, shaking and a full plate pass."""
        t = self._timeout("S")
        if a is not None:
            t += 1.5 * a.estimated_pass_s()
            if a.read_mode == ReadMode.KINETIC:
                t += a.kinetic_interval_s
        return t

    def read(self, assay: AssayDefinition | ReadSeries, *, table: Optional[FilterTable] = None,
             include_temperature: bool = False,
             on_block: Optional[Callable[[int, DataBlock], None]] = None,
             before_next: Optional[Callable[[int], None]] = None,
             fastest: bool = False) -> ReadResult:
        """Run a complete read: one definition, or a series for >2 filter sets.

        ``before_next(i)`` is called before definition i>0 (e.g. to wait for the user
        when ``eject_between`` is set).  For kinetic reads the reader's minimum interval
        ('$') is checked first; ``fastest=True`` uses that minimum (rounded up to 1 s).
        """
        series = assay.definitions if isinstance(assay, ReadSeries) else [assay]
        self._abort.clear()
        result = ReadResult(assays=list(series), blocks=[], statuses=[], started=datetime.now())
        fs_offset = 0
        for idx, a in enumerate(series):
            if self._abort.is_set():
                result.aborted = True
                break
            if idx and before_next:
                before_next(idx)
            self.define(a, table)
            if a.read_mode == ReadMode.KINETIC:
                a = self._check_kinetic_interval(a, table, fastest)
                result.assays[idx] = a
            if include_temperature and self.has_incubator:
                self.include_temperature(True)
            blocks: list[DataBlock] = []
            result.blocks.append(blocks)

            t0 = time.monotonic()
            endpoint = a.read_mode != ReadMode.KINETIC

            def cb(blk: DataBlock, _off=fs_offset, _blocks=blocks, _t0=t0, _ep=endpoint) -> None:
                blk.received_s = time.monotonic() - _t0
                if _ep:
                    # The reader does not reset its clock for endpoint reads (an endpoint read after a
                    # kinetic run carries the kinetic run's last stamp), so the stamp is meaningless there.
                    blk.elapsed_s = 0.0
                _blocks.append(blk)
                if on_block:
                    on_block(_off, blk)

            resp = self.read_plate(cb, a)
            result.statuses.append(resp.final_status or resp.initial_status)
            if resp.aborted:
                result.aborted = True
                break
            if not resp.initial_status.ok:
                raise ReaderError(resp.initial_status, "read plate (initial status)")
            for i, fs in enumerate(a.filter_sets):
                if fs.sensitivity_mode == SensitivityMode.AUTO_ONBOARD:
                    try:
                        result.sensitivities[fs_offset + i] = self.get_sensitivity(i + 1)
                    except FLx800Error:
                        pass
                else:
                    result.sensitivities[fs_offset + i] = fs.sensitivity
            fs_offset += len(a.filter_sets)
            if resp.final_status is not None and not resp.final_status.ok:
                break
        result.finished = datetime.now()
        return result

    def read_wells(self, wells: list[tuple[int, int]]) -> ReadResponse:
        """'d': read up to 8 wells (row, col 1-based) with the loaded definition."""
        if not 1 <= len(wells) <= 8:
            raise ValueError("1-8 wells")
        w = list(wells) + [(0, 0)] * (8 - len(wells))
        args = b"".join(num(r, 2) + num(c, 2) for r, c in w) + b"0\x03"
        return self._read_stream("d", args)

    # ------------------------------------------------------------------ abort / test
    def abort(self, soft: bool = True) -> Optional[str]:
        """'x' (soft: keeps data already sent) or 'X' (halt). Safe to call from another thread."""
        self._abort.set()
        char = "x" if soft else "X"
        # Do not take the lock: a read may be in progress holding it.
        self.t.write(char.encode())
        if self._reading:
            return None  # the reading thread will see DLE/^Z + code
        try:
            raw = self.t.read_exact(5, self._timeout(char))
            return raw[1:].decode("ascii", "replace")
        except CommError:
            return None
        finally:
            time.sleep(0.05)
            self.t.flush_input()

    def self_test(self) -> tuple[str, Status]:
        """'*': run the system self-test; returns (report text, status)."""
        with self._lock:
            self._begin("*")
            buf = bytearray()
            deadline = time.monotonic() + self._timeout("*")
            while True:
                buf += self.t.read_exact(1, max(1.0, deadline - time.monotonic()))
                if buf[-1:] == b"\x03" and len(buf) >= 5:
                    break
        st = parse_status(bytes(buf[-5:]))
        return bytes(buf[:-5]).decode("ascii", "replace"), st
