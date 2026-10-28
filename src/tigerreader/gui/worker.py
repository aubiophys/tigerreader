"""The reader runs in its own thread; the window talks to it through Qt signals.

Every request is a small function queued to the worker thread (``submit``), so the
strictly half-duplex serial link is used by one thread only. The only exception is
``abort()``, which the driver allows from any thread.
"""
from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal, Slot

from .. import __version__
from ..driver.flx800 import FLx800
from ..export_ascii import write_run
from ..protocol.assay import ReadSeries
from ..readsetup import ReadSetup
from ..transport.trace import TracingTransport

SIM_FAST = "Simulator (20× faster)"
SIM_REAL = "Simulator (real speed)"
SIM_PORTS = {SIM_FAST: 0.05, SIM_REAL: 1.0}


class ReaderWorker(QObject):
    job = Signal(object, str)              # (function, label) -> run in the worker thread
    busy = Signal(bool, str)
    log = Signal(str)
    trace = Signal(str, bytes)
    connected = Signal(object, object)     # InstrumentInfo, FilterTable
    disconnected = Signal()
    carrier_ready = Signal()
    read_started = Signal(object, object)  # ReadSetup, list[AssayDefinition]
    block = Signal(int, int, object)       # definition index, block index in definition, DataBlock
    read_done = Signal(object, str)        # ReadResult, run folder
    status = Signal(str)
    selftest_done = Signal(str, str)
    failed = Signal(str, str)              # what, message

    def __init__(self, data_dir: Path):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.rdr: Optional[FLx800] = None
        self.table = None
        self._trace_path: Optional[Path] = None
        self._sink = None
        self.job.connect(self._run)

    # ------------------------------------------------------------------ plumbing
    def submit(self, fn: Callable[[], None], label: str) -> None:
        """Queue *fn* to run in the worker thread (call from the GUI thread)."""
        self.job.emit(fn, label)

    @Slot(object, str)
    def _run(self, fn, label: str) -> None:
        self.busy.emit(True, label)
        try:
            fn()
        except Exception as exc:  # report everything; the GUI must stay alive
            self.log.emit(f"{label}: {type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}")
            self.failed.emit(label, str(exc))
        finally:
            self.busy.emit(False, "")

    def _on_trace(self, direction: str, data: bytes) -> None:
        self.trace.emit(direction, data)

    def _traced(self, inner, flush_on: tuple = ()) -> TracingTransport:
        """Wrap *inner* so that every byte goes to a session trace file and to the GUI log."""
        traces = self.data_dir / "traces"
        traces.mkdir(parents=True, exist_ok=True)
        self._trace_path = traces / f"session_{datetime.now():%Y-%m-%d_%H%M%S}.log"
        self._sink = open(self._trace_path, "a", encoding="ascii", errors="replace")
        return TracingTransport(inner, self._sink, callback=self._on_trace, flush_on=flush_on)

    def _trace_pos(self) -> int:
        return self._trace_path.stat().st_size if self._trace_path and self._trace_path.exists() else 0

    def _save_trace(self, folder: Path, start: int) -> None:
        """Copy the part of the session trace written since *start* into the run folder (trace.log)."""
        if self._trace_path is not None and self._sink is not None:
            self._sink.flush()
            with open(self._trace_path, "rb") as f:
                f.seek(start)
                (folder / "trace.log").write_bytes(f.read())

    @property
    def is_connected(self) -> bool:
        return self.rdr is not None

    # ------------------------------------------------------------------ requests (worker thread)
    def connect_reader(self, port: str) -> None:
        self.disconnect_reader()
        if port in SIM_PORTS:
            from ..transport.sim import SimConfig, SimTransport, SimulatedFLx800
            inner = SimTransport(SimulatedFLx800(SimConfig(pace=SIM_PORTS[port])))
        else:
            from ..transport.serial_port import SerialTransport, find_ftdi_port
            if port.lower().startswith("auto"):
                port = find_ftdi_port()
            inner = SerialTransport(port)
        t = self._traced(inner)
        try:
            t.open()
            rdr = FLx800(t)
            info = rdr.connect()
            self.table = rdr.get_filter_table()
        except Exception:
            t.close()
            self._sink.close()
            self._sink = None
            raise
        self.rdr = rdr
        self.log.emit(f"Connected on {port} (TigerReader {__version__}). Trace: {self._trace_path}")
        self.connected.emit(info, self.table)

    def disconnect_reader(self) -> None:
        if self.rdr is None:
            return
        try:
            self.rdr.close()
        finally:
            self.rdr = None
            if self._sink is not None:
                self._sink.close()
                self._sink = None
            self.disconnected.emit()
            self.log.emit("Disconnected.")

    def _need(self) -> FLx800:
        if self.rdr is None:
            raise RuntimeError("not connected")
        return self.rdr

    def carrier(self, out: bool) -> None:
        r = self._need()
        r.carrier_out() if out else r.carrier_in()

    def prepare_read(self) -> None:
        """Present the carrier, then tell the GUI to ask for the plate."""
        self._need().carrier_out()
        self.carrier_ready.emit()

    def get_status(self) -> None:
        st = self._need().get_status()
        self.status.emit(f"{st.code} {st.describe()}")

    def refresh_filters(self) -> None:
        self.table = self._need().get_filter_table()
        self.connected.emit(self._need().info, self.table)

    def self_test(self) -> None:
        text, st = self._need().self_test()
        self.selftest_done.emit(text, f"{st.code} {st.describe()}")

    def run_read(self, setup: ReadSetup, results_id: str, description: str, runs_dir: Path) -> None:
        rdr = self._need()
        assay = setup.to_assay()
        defs = assay.definitions if isinstance(assay, ReadSeries) else [assay]
        self.read_started.emit(setup, defs)
        counters: dict[int, int] = {}
        offsets, n = [], 0
        for d in defs:
            offsets.append(n)
            n += len(d.filter_sets)
        n_auto = [len(d.serial_autosense_blocks()) for d in defs]

        def on_block(fs_offset: int, blk) -> None:
            di = offsets.index(fs_offset)
            j = counters.get(di, 0)
            counters[di] = j + 1
            if j >= n_auto[di]:
                self.block.emit(di, j - n_auto[di], blk)

        start = self._trace_pos()
        result = rdr.read(assay, table=self.table, on_block=on_block, fastest=setup.fastest)
        folder = write_run(result, runs_dir, results_id or setup.name, rdr.info, description=description,
                           keep=setup.selected_wells())
        (folder / "protocol.json").write_text(setup.to_json(), encoding="utf-8")
        self._save_trace(folder, start)
        self.read_done.emit(result, str(folder))

    def abort(self) -> None:
        """Called from the GUI thread while a read is running."""
        if self.rdr is not None:
            self.rdr.abort(soft=True)

    def shutdown(self) -> None:
        self.disconnect_reader()


class M680Worker(ReaderWorker):
    """The same thread plumbing for the Bio-Rad Model 680. The 680 has no carrier command (its carrier
    moves by itself), no status/filter-table/self-test commands, and no abort: Abort acts between plate reads."""

    plate = Signal(object)                 # m680.driver.PlateRead, as each plate read arrives
    incubator = Signal(object)             # dict from Model680.incubator()

    def connect_reader(self, port: str) -> None:
        from ..m680.driver import Model680
        self.disconnect_reader()
        if port in SIM_PORTS:
            from ..m680.sim import Sim680Config, Sim680Transport, Simulated680
            inner = Sim680Transport(Simulated680(Sim680Config(pace=SIM_PORTS[port])))
        else:
            from ..transport.serial_port import SerialTransport
            inner = SerialTransport(port, 9600, stopbits=1)           # 8N1, no handshake
        t = self._traced(inner, flush_on=(b"\r",))
        try:
            t.open()
            rdr = Model680(t)
            info = rdr.connect()
        except Exception:
            t.close()
            self._sink.close()
            self._sink = None
            raise
        self.rdr = rdr
        self.log.emit(f"Connected on {port} (TigerReader {__version__}). Trace: {self._trace_path}")
        self.connected.emit(info, None)
        self.get_incubator()

    def reset(self) -> None:
        """RS: clear a reader error (8075-8093, e.g. the Stop key); the driver reconnects (remote mode)."""
        rdr = self._need()
        rdr.reset()
        self.log.emit("Reader reset.")
        self.connected.emit(rdr.info, None)
        self.get_incubator()

    def get_incubator(self) -> None:
        if self._need().info.has_incubator:
            self.incubator.emit(self._need().incubator())

    def set_incubator(self, temp_c) -> None:
        """Set point in °C, or None = off."""
        self._need().set_incubator(temp_c)
        self.log.emit("Incubator off." if temp_c is None else f"Incubator on, set point {temp_c:.1f} °C.")
        self.get_incubator()

    def run_read(self, setup, results_id: str, description: str, runs_dir: Path) -> None:
        from ..m680.export import write_run as write_m680
        rdr = self._need()
        self.read_started.emit(setup, None)
        start = self._trace_pos()
        run = rdr.read(setup.meas_nm, setup.ref_nm, setup.speed, setup.mix_time_s, setup.mix_speed,
                       setup.n_reads, setup.kinetic_interval_s if setup.kinetic else 0,
                       on_plate=self.plate.emit)
        folder = write_m680(run, runs_dir, results_id or setup.name, rdr.info, description=description,
                            keep=setup.selected_wells())
        (folder / "protocol.json").write_text(setup.to_json(), encoding="utf-8")
        self._save_trace(folder, start)
        self.read_done.emit(run, str(folder))

    def abort(self) -> None:
        if self.rdr is not None:
            self.rdr.abort()


def dump_setup(setup: ReadSetup) -> str:
    return json.dumps(setup.to_dict(), indent=2)
