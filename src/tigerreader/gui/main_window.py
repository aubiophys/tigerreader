"""TigerReader main window, for the BioTek FLx800 or the Bio-Rad Model 680."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSpinBox, QSplitter, QTabWidget, QTextEdit, QToolBar,
                               QSizePolicy, QVBoxLayout, QWidget, QApplication)

from .. import __version__
from ..m680.setup import AbsSetup
from ..readsetup import ReadSetup
from ..rundata import PlateRead, RunData, fmt_value, stats, wells_text
from ..storage import DB_NAME, ParamStore, default_data_dir, instrument_of, setup_from_json
from ..transport.trace import printable
from .m680_panel import M680SetupPanel
from .plate import PlateView
from .setup_panel import SetupPanel, _fmt_time
from .worker import SIM_FAST, SIM_REAL, M680Worker, ReaderWorker

INSTRUMENTS = {"flx800": "BioTek FLx800", "m680": "Bio-Rad Model 680"}
_WINDOWS: list = []        # windows opened by "Switch instrument" (kept alive here)

try:  # optional: live kinetic curves
    import pyqtgraph as pg
except Exception:  # pragma: no cover
    pg = None


def instrument_html(info, table) -> str:
    from ..protocol.commands import FEATURES
    top, bottom = info.probes
    rows = [("Model", info.model), ("Serial", info.serial),
            ("Basecode", f"{info.part_number} v{(info.version or '').strip()}"),
            ("Config", f"0x{info.config_word:04X}" if info.config_word is not None else "?"),
            ("Probes", " + ".join(["top"] * top + ["bottom"] * bottom)),
            ("Incubator", "yes" if info.has_incubator else "no"), ("Shaker", "yes" if info.has_shaker else "no")]
    h = "<table cellspacing=2>" + "".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in rows) + "</table>"
    h += "<p><b>Filter table</b><table cellspacing=2><tr><td></td>" + "".join(f"<td>{i}</td>" for i in range(1, 5))
    h += "</tr><tr><td>Ex</td>" + "".join(f"<td>{f}</td>" for f in table.excitation)
    h += "</tr><tr><td>Em</td>" + "".join(f"<td>{f}</td>" for f in table.emission) + "</tr></table></p>"
    feats = [f"{c} {FEATURES.get(c, '')}" for c, v in sorted(info.features.items()) if v]
    if feats:
        h += "<p><b>Features</b><br>" + "<br>".join(feats) + "</p>"
    return h


def m680_html(info) -> str:
    rows = [("Model", info.ident or "Model 680"), ("Serial", info.serial),
            ("Incubator", "yes" if info.has_incubator else "no")]
    rows += [(k, v) for k, v in info.maintenance.items()]
    h = "<table cellspacing=2>" + "".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in rows) + "</table>"
    h += "<p><b>Filter wheel</b><table cellspacing=2>" + "".join(
        f"<tr><td>{p}</td><td>{f'{w} nm' if w else 'empty'}</td></tr>" for p, w in sorted(info.filters.items()))
    return h + "</table></p>"


def _state(result) -> str:
    """OK / ABORTED / the error, for an FLx800 ReadResult or a Model 680 M680Run."""
    if result.ok:
        return "OK"
    if result.aborted:
        return "ABORTED"
    if hasattr(result, "statuses"):
        return "; ".join(f"{s.code} {s.describe()}" for s in result.statuses if not s.ok)
    return result.error or "INCOMPLETE"


class RunDialog(QDialog):
    """Results ID + description before a read."""

    def __init__(self, setup, eta: str, parent=None, ok_text: str = "Present carrier"):
        super().__init__(parent)
        self.setWindowTitle("Read plate")
        f = QFormLayout(self)
        self.results_id = QLineEdit(f"{setup.name}")
        self.description = QLineEdit(setup.description)
        f.addRow(QLabel(f"<b>{setup.name}</b><br>Estimated time ≈ {eta}"))
        f.addRow("Results ID", self.results_id)
        f.addRow("Description", self.description)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(ok_text)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Optional[Path] = None, ask: bool = True, instrument: str = "flx800"):
        super().__init__()
        self.ask = ask                       # False in automated tests: no modal questions
        if instrument not in INSTRUMENTS:
            raise ValueError(f"unknown instrument {instrument!r}")
        self.instrument = instrument
        self.m680 = instrument == "m680"     # Bio-Rad Model 680 (absorbance) instead of the FLx800
        self.settings = QSettings("TigerReader", "TigerReader")
        if data_dir is None:
            data_dir = self.settings.value("data_dir") or default_data_dir()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = ParamStore(self.data_dir / DB_NAME)
        self.runs_dir = Path(self.store.get("runs_dir", str(self.data_dir / "runs")))
        self.info = None
        self.reading = False
        self.busy = False
        self.run: Optional[RunData] = None
        self.defs: list = []
        self.def_offsets: list[int] = []
        self.keep: Optional[set] = None
        self.total_reads = 0
        self.t_read0 = 0.0
        self.last_folder: Optional[str] = None
        self.current_protocol: Optional[str] = None

        self.setWindowTitle(f"TigerReader {__version__} — {INSTRUMENTS[instrument]}")

        # worker thread
        self.thread = QThread(self)
        self.worker = (M680Worker if self.m680 else ReaderWorker)(self.data_dir)
        self.worker.moveToThread(self.thread)
        self.thread.start()
        w = self.worker
        w.busy.connect(self._on_busy)
        w.log.connect(self.log)
        w.trace.connect(self._on_trace)
        w.connected.connect(self._on_connected)
        w.disconnected.connect(self._on_disconnected)
        w.carrier_ready.connect(self._on_carrier_ready)
        w.read_started.connect(self._on_read_started)
        w.block.connect(self._on_block)
        w.read_done.connect(self._on_read_done)
        w.status.connect(lambda s: self.log(f"Reader status: {s}"))
        w.selftest_done.connect(self._on_selftest)
        w.failed.connect(self._on_failed)
        if self.m680:
            w.plate.connect(self._on_plate)
            w.incubator.connect(self._on_incubator)

        self._build_actions()
        self._build_ui()
        self._refresh_ports()
        self._refresh_protocols()
        self._refresh_runs()
        self._update_enabled()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(500)
        self._fit_to_screen(self.settings.value("geometry"))

    def _fit_to_screen(self, saved=None) -> None:
        """Restore the saved size if it fits the screen; otherwise use ~90 % of the available area."""
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry()
        if saved is not None and self.restoreGeometry(saved) and avail.contains(self.frameGeometry()):
            return
        w, h = min(1500, int(avail.width() * 0.92)), min(950, int(avail.height() * 0.92))
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)

    # ================================================================== UI construction
    def _build_actions(self) -> None:
        A = lambda text, slot, key=None: self._action(text, slot, key)  # noqa: E731
        self.act_connect = A("Connect", self.connect_reader)
        self.act_disconnect = A("Disconnect", self.disconnect_reader)
        self.act_out = A("Carrier out", lambda: self._submit(lambda: self.worker.carrier(True), "Carrier out"))
        self.act_in = A("Carrier in", lambda: self._submit(lambda: self.worker.carrier(False), "Carrier in"))
        self.act_read = A("Read plate ▶", self.start_read, "F5")
        self.act_abort = A("Abort ■", self.abort_read, "Esc")
        self.act_status = A("Get status", lambda: self._submit(self.worker.get_status, "Status"))
        self.act_filters = A("Read filter table", lambda: self._submit(self.worker.refresh_filters, "Filter table"))
        self.act_selftest = A("System self-test…", self.self_test)
        self.act_reset = A("Reset reader", lambda: self._submit(self.worker.reset, "Reset"))    # Model 680
        self.act_open_run = A("Open run folder…", self.open_run_dialog, QKeySequence.Open)
        self.act_new = A("New protocol", self.new_protocol, QKeySequence.New)
        self.act_save = A("Save protocol", self.save_protocol, QKeySequence.Save)
        self.act_save_as = A("Save protocol as…", self.save_protocol_as)
        self.act_delete = A("Delete protocol", self.delete_protocol)
        self.act_import_json = A("Import protocol file (.json)…", self.import_json)
        self.act_export_json = A("Export protocol file (.json)…", self.export_json)
        self.act_runs_dir = A("Folder for runs…", self.choose_runs_dir)
        self.act_show_folder = A("Show run in file browser", self.show_run_folder)
        self.act_report_pdf = A("Save run report as PDF…", self.save_report_dialog)
        self.act_report_print = A("Print run report…", self.print_report, QKeySequence.Print)
        self.act_auto_report = QAction("Save report after each read", self)
        self.act_auto_report.setCheckable(True)
        self.act_auto_report.setChecked(self.store.get("auto_report", "0") == "1")
        self.act_auto_report.toggled.connect(lambda on: self.store.set("auto_report", "1" if on else "0"))
        self.act_switch = A("Switch instrument…", self.switch_instrument)
        self.act_quit = A("Quit", self.close, QKeySequence.Quit)
        self.act_about = A("About", self.about)

        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.act_open_run, self.act_show_folder, self.act_runs_dir, None, self.act_report_pdf,
                  self.act_report_print, self.act_auto_report, None, self.act_switch, self.act_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Protocol")
        for a in (self.act_new, self.act_save, self.act_save_as, self.act_delete, None,
                  self.act_import_json, self.act_export_json):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Instrument")
        flx = () if self.m680 else (None, self.act_out, self.act_in)          # the 680's carrier moves by itself
        tail = (None, self.act_reset) if self.m680 else (None, self.act_status, self.act_filters, self.act_selftest)
        for a in (self.act_connect, self.act_disconnect) + flx + (None, self.act_read, self.act_abort) + tail:
            m.addSeparator() if a is None else m.addAction(a)
        self.view_menu = mb.addMenu("&View")        # filled in _build_ui (panels)
        mb.addMenu("&Help").addAction(self.act_about)

    def _action(self, text, slot, key=None) -> QAction:
        a = QAction(text, self)
        a.triggered.connect(lambda *_: slot())
        if key is not None:
            a.setShortcut(key)
        return a

    def _build_ui(self) -> None:
        tb = QToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addWidget(QLabel(" Port "))
        self.port = QComboBox()
        self.port.setMinimumWidth(230)
        tb.addWidget(self.port)
        rp = QPushButton("↻")
        rp.setToolTip("Refresh the list of serial ports")
        rp.setMaximumWidth(30)
        rp.clicked.connect(self._refresh_ports)
        tb.addWidget(rp)
        for a in (self.act_connect, self.act_disconnect):
            tb.addAction(a)
        tb.addSeparator()
        if not self.m680:
            for a in (self.act_out, self.act_in):
                tb.addAction(a)
            tb.addSeparator()
        tb.addAction(self.act_read)
        tb.addAction(self.act_abort)
        tb.addSeparator()
        tb.addAction(self.act_open_run)

        # ---- left: protocols + runs, and the protocol editor
        left_tabs = QTabWidget()
        pw = QWidget()
        pl = QVBoxLayout(pw)
        pl.setContentsMargins(2, 2, 2, 2)
        self.protocol_list = QListWidget()
        self.protocol_list.itemSelectionChanged.connect(self._protocol_selected)
        pl.addWidget(self.protocol_list)
        row = QHBoxLayout()
        for text, slot in (("New", self.new_protocol), ("Save", self.save_protocol), ("Delete", self.delete_protocol)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        pl.addLayout(row)
        left_tabs.addTab(pw, "Protocols")
        rw = QWidget()
        rl = QVBoxLayout(rw)
        rl.setContentsMargins(2, 2, 2, 2)
        self.run_list = QListWidget()
        self.run_list.itemDoubleClicked.connect(lambda it: self.open_run(it.data(Qt.UserRole)))
        rl.addWidget(self.run_list)
        b = QPushButton("Open selected run")
        b.clicked.connect(lambda: self.run_list.currentItem() and
                          self.open_run(self.run_list.currentItem().data(Qt.UserRole)))
        rl.addWidget(b)
        left_tabs.addTab(rw, "Runs")

        self.setup_panel = M680SetupPanel() if self.m680 else SetupPanel()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.setup_panel)
        scroll.setMinimumWidth(380)

        # ---- centre: results
        res = QWidget()
        rlay = QVBoxLayout(res)
        rlay.setContentsMargins(4, 4, 4, 4)
        self.run_title = QLabel("No data yet — connect, choose a protocol and press <b>Read plate</b>.")
        self.run_title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.run_title.setWordWrap(True)
        rlay.addWidget(self.run_title)
        bar = QHBoxLayout()
        self.fs_combo = QComboBox()
        self.fs_combo.setMinimumWidth(150)
        self.fs_combo.currentIndexChanged.connect(self._show_current)
        self.k_spin = QSpinBox()
        self.k_spin.setMinimum(1)
        self.k_spin.setPrefix("read ")
        self.k_spin.valueChanged.connect(self._show_current)
        self.k_total = QLabel("of 1")
        self.follow = QCheckBox("follow latest")
        self.follow.setChecked(True)
        self.heat = QCheckBox("heat map")
        self.heat.setChecked(True)
        self.heat.toggled.connect(self._toggle_heat)
        self.t_label = QLabel("")
        self.t_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # long text must not widen the window
        copy = QPushButton("Copy")
        copy.setToolTip("Copy the selected wells (or the whole plate) as tab-separated text")
        bar.addWidget(QLabel("Wavelength" if self.m680 else "Filter set"))
        bar.addWidget(self.fs_combo)
        bar.addWidget(self.k_spin)
        bar.addWidget(self.k_total)
        bar.addWidget(self.follow)
        bar.addWidget(self.t_label, 1)
        bar.addWidget(self.heat)
        bar.addWidget(copy)
        rlay.addLayout(bar)
        self.plate_view = PlateView()
        copy.clicked.connect(self.plate_view.copy_selection)
        self.plate_view.selection_changed.connect(self._selection_changed)
        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(self.plate_view)
        lower = QWidget()
        ll = QVBoxLayout(lower)
        ll.setContentsMargins(0, 0, 0, 0)
        self.stats_label = QLabel("Select wells to see statistics and kinetic curves.")
        self.stats_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.stats_label.setWordWrap(True)
        ll.addWidget(self.stats_label)
        if pg is not None:
            pg.setConfigOptions(antialias=True, background="w", foreground="k")
            self.plot = pg.PlotWidget()
            self.plot.setLabel("bottom", "time (s)" if self.m680 else "time (s, reader time stamp)")
            self.plot.setLabel("left", "OD" if self.m680 else "RFU / RLU")
            self.plot.showGrid(x=True, y=True, alpha=0.3)
            self.plot.addLegend(offset=(-10, 10))
            self.plot.setMinimumHeight(80)
            ll.addWidget(self.plot)
        else:
            self.plot = None
            ll.addWidget(QLabel("(install pyqtgraph for kinetic curves)"))
        vsplit.addWidget(lower)
        vsplit.setSizes([520, 220])
        rlay.addWidget(vsplit)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left_tabs)
        split.addWidget(scroll)
        split.addWidget(res)
        split.setSizes([260, 600, 640])
        split.setStretchFactor(2, 1)
        self.setCentralWidget(split)

        # ---- right dock: instrument
        inst = QWidget()
        il = QVBoxLayout(inst)
        self.conn_label = QLabel("<b style='color:#888'>● Not connected</b>")
        il.addWidget(self.conn_label)
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        mono = QFont("Consolas" if sys.platform.startswith("win") else "Monospace")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(9)
        self.info_text.setFont(mono)
        il.addWidget(self.info_text)
        if self.m680:
            il.addWidget(self._incubator_box())
        else:
            for a in (self.act_status, self.act_filters, self.act_selftest):
                b = QPushButton(a.text())
                b.clicked.connect(a.trigger)
                a.changed.connect(lambda b=b, a=a: b.setEnabled(a.isEnabled()))
                il.addWidget(b)
        dock = QDockWidget("Instrument", self)
        dock.setObjectName("instrument_dock")
        dock.setWidget(inst)
        dock.setMinimumWidth(200)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        # ---- bottom dock: log + trace
        lw = QWidget()
        lv = QVBoxLayout(lw)
        lv.setContentsMargins(2, 2, 2, 2)
        self.show_trace = QCheckBox("show serial trace (TX/RX bytes)")
        lv.addWidget(self.show_trace)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(5000)
        self.log_text.setFont(mono)
        lv.addWidget(self.log_text)
        ldock = QDockWidget("Log", self)
        ldock.setObjectName("log_dock")
        ldock.setWidget(lw)
        self.addDockWidget(Qt.BottomDockWidgetArea, ldock)
        self.resizeDocks([ldock], [110], Qt.Vertical)
        for d in (dock, ldock):                       # panels can be hidden and shown again
            self.view_menu.addAction(d.toggleViewAction())

        # ---- status bar
        self.state_label = QLabel("Not connected")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setVisible(False)
        self.statusBar().addWidget(self.state_label, 1)
        self.statusBar().addPermanentWidget(self.progress)
        self.runs_label = QLabel(f"Runs: {self.runs_dir}")
        self.statusBar().addPermanentWidget(self.runs_label)

    def _incubator_box(self) -> QWidget:
        """Model 680 incubator: status, set point, On/Off (25.0-45.0 °C, at least 5 °C above the room)."""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        self.inc_label = QLabel("Incubator: —")
        v.addWidget(self.inc_label)
        row = QHBoxLayout()
        self.inc_set = QDoubleSpinBox()
        self.inc_set.setRange(25.0, 45.0)
        self.inc_set.setDecimals(1)
        self.inc_set.setSingleStep(0.5)
        self.inc_set.setValue(37.0)
        self.inc_set.setSuffix(" °C")
        self.inc_on = QPushButton("On")
        self.inc_off = QPushButton("Off")
        self.inc_on.clicked.connect(lambda: self._submit(
            lambda t=self.inc_set.value(): self.worker.set_incubator(t), "Incubator"))
        self.inc_off.clicked.connect(lambda: self._submit(lambda: self.worker.set_incubator(None), "Incubator"))
        refresh = QPushButton("↻")
        refresh.setMaximumWidth(30)
        refresh.setToolTip("Read the incubator status again")
        refresh.clicked.connect(lambda: self._submit(self.worker.get_incubator, "Incubator status"))
        self.inc_buttons = (self.inc_set, self.inc_on, self.inc_off, refresh)
        for w in self.inc_buttons:
            row.addWidget(w)
        v.addLayout(row)
        return box

    def _on_incubator(self, s: dict) -> None:
        self.inc_label.setText(f"Incubator: <b>{'on' if s['on'] else 'off'}</b>, set point {s['setpoint_c']} °C, "
                               f"temperature {s['temperature_c']} °C")

    # ================================================================== helpers
    def log(self, text: str) -> None:
        self.log_text.appendPlainText(f"{datetime.now():%H:%M:%S}  {text}")

    def _on_trace(self, direction: str, data: bytes) -> None:
        if self.show_trace.isChecked():
            self.log_text.appendPlainText(f"{datetime.now():%H:%M:%S.%f}"[:-3] + f"  {direction} {printable(data)}")

    def _submit(self, fn, label: str) -> None:
        if self.busy and label != "Abort":
            self.log(f"{label}: busy, please wait")
            return
        self.worker.submit(fn, label)

    def _question(self, title: str, text: str) -> bool:
        if not self.ask:
            return True
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def _info(self, title: str, text: str, warn: bool = False) -> None:
        self.log(f"{title}: {text}")
        if self.ask:
            (QMessageBox.warning if warn else QMessageBox.information)(self, title, text)

    def _update_enabled(self) -> None:
        con = self.worker.is_connected or self.info is not None
        idle = not self.busy
        self.act_connect.setEnabled(idle and not con)
        self.port.setEnabled(idle and not con)
        self.act_disconnect.setEnabled(idle and con)
        for a in (self.act_out, self.act_in, self.act_status, self.act_filters, self.act_selftest, self.act_reset):
            a.setEnabled(idle and con)
        self.act_read.setEnabled(idle and con)
        self.act_abort.setEnabled(self.reading)
        self.act_switch.setEnabled(not self.reading)
        if self.m680:
            has_inc = self.info is not None and self.info.has_incubator
            for w in self.inc_buttons:
                w.setEnabled(idle and con and has_inc)

    def _on_busy(self, on: bool, label: str) -> None:
        self.busy = on
        if on:
            self.state_label.setText(f"{label}…")
        elif not self.reading:
            self.state_label.setText("Ready" if self.info else "Not connected")
        self._update_enabled()

    def _on_failed(self, what: str, msg: str) -> None:
        if what.startswith("Read"):
            self.reading = False
            self.progress.setVisible(False)
        self._update_enabled()
        self._info(f"{what} failed", msg, warn=True)

    # ================================================================== connection
    def _refresh_ports(self) -> None:
        from ..transport.serial_port import list_ports
        cur = self.port.currentData() or self.store.get(self._port_key, "")
        self.port.clear()
        ports = list_ports()
        for dev, desc, ftdi in ports:
            self.port.addItem(f"{dev}  {'★ ' if ftdi else ''}{desc}".strip(), dev)
        if any(f for _, _, f in ports) and not self.m680:     # 'auto' needs a single FTDI cable: FLx800 only
            self.port.addItem("Auto (FTDI cable)", "auto")
        self.port.addItem(SIM_FAST, SIM_FAST)
        self.port.addItem(SIM_REAL, SIM_REAL)
        idx = max(0, self.port.findData(cur))
        if self.port.findData(cur) < 0 and ports:
            idx = 0
        self.port.setCurrentIndex(idx)

    @property
    def _port_key(self) -> str:
        return "m680_port" if self.m680 else "port"

    def connect_reader(self, port: Optional[str] = None) -> None:
        port = port or self.port.currentData()
        self.store.set(self._port_key, port)
        self._submit(lambda: self.worker.connect_reader(port), f"Connect {port}")

    def disconnect_reader(self) -> None:
        self._submit(self.worker.disconnect_reader, "Disconnect")

    def _on_connected(self, info, table) -> None:
        self.info = info
        self.conn_label.setText(f"<b style='color:#1b7f3b'>● Connected</b> — {info.model}, SN {info.serial}")
        self.info_text.setHtml(m680_html(info) if self.m680 else instrument_html(info, table))
        self.setup_panel.set_reader(info, table)
        self.state_label.setText("Ready")
        self._update_enabled()

    def _on_disconnected(self) -> None:
        self.info = None
        self.conn_label.setText("<b style='color:#888'>● Not connected</b>")
        self.state_label.setText("Not connected")
        self._update_enabled()

    # ================================================================== reading
    def start_read(self) -> None:
        if self.busy or self.reading:
            return
        setup = self.setup_panel.setup()
        probs = setup.problems(self.worker.table, self.info)
        if probs:
            self._info("Cannot read", "\n".join(probs), warn=True)
            return
        eta = _fmt_time(setup.estimated_time_s())
        rid, desc = setup.name, setup.description
        if self.ask:
            dlg = RunDialog(setup, eta, self, ok_text="Read" if self.m680 else "Present carrier")
            if dlg.exec() != QDialog.Accepted:
                return
            rid, desc = dlg.results_id.text().strip() or setup.name, dlg.description.text()
        self._pending = (setup, rid, desc)
        if self.m680:                    # no carrier command: the 680's carrier moves by itself during the read
            self._on_carrier_ready()
        else:
            self._submit(self.worker.prepare_read, "Carrier out")

    def _on_carrier_ready(self) -> None:
        setup, rid, desc = self._pending
        if self.ask:
            text = ("Place the plate on the carrier (A1 at the back left), then press OK to read."
                    + (" The carrier moves in by itself." if self.m680 else ""))
            box = QMessageBox(QMessageBox.Information, "Load the plate", text,
                              QMessageBox.Ok | QMessageBox.Cancel, self)
            if box.exec() != QMessageBox.Ok:
                if not self.m680:
                    self.worker.submit(lambda: self.worker.carrier(False), "Carrier in")
                return
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.reading = True
        self._update_enabled()
        # submit directly: the carrier job that triggered this is still marked busy
        self.worker.submit(lambda: self.worker.run_read(setup, rid, desc, self.runs_dir), "Read")

    def abort_read(self) -> None:
        if self.reading:
            self.log("Abort requested — data already received will be kept.")
            self.worker.abort()

    def _on_read_started(self, setup, defs) -> None:
        if self.m680:
            self._start_m680_run(setup)
            return
        self.defs = list(defs)
        self.def_offsets = []
        n = 0
        for d in self.defs:
            self.def_offsets.append(n)
            n += len(d.filter_sets)
        self.total_reads = sum(len(d.filter_sets) * d.n_kinetic for d in self.defs)
        self.keep = setup.selected_wells()
        self.run = RunData.for_definitions(self.defs, f"{setup.name} — reading…")
        self.t_read0 = time.monotonic()
        self.eta_s = setup.estimated_time_s()
        self.progress.setRange(0, self.total_reads)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._show_run(self.run)
        self.log(f"Read started: {setup.name} ({self.total_reads} plate read(s), ≈ {_fmt_time(self.eta_s)}).")

    def _on_block(self, di: int, j: int, blk) -> None:
        if self.run is None:
            return
        pr = self.run.add_block(self.def_offsets[di], blk, self.defs[di], j, keep=self.keep)
        self._new_read(pr)

    def _start_m680_run(self, setup: AbsSetup) -> None:
        sets = [(0, setup.meas_nm, "measurement")] + ([(1, setup.ref_nm, "reference")] if setup.ref_nm else [])
        self.m680_sets = sets
        self.keep = setup.selected_wells()
        self.run = RunData(8, 12, f"{setup.name} — reading…")
        self.run.fs_labels = {fs: f"{nm} nm" + (" reference" if fs else "") for fs, nm, role in sets}
        self.total_reads = setup.n_reads * len(sets)
        self.t_read0 = time.monotonic()
        self.eta_s = setup.estimated_time_s()
        self.progress.setRange(0, self.total_reads)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._show_run(self.run)
        self.log(f"Read started: {setup.name} ({setup.n_reads} plate read(s), ≈ {_fmt_time(self.eta_s)}).")

    def _on_plate(self, p) -> None:
        """A Model 680 plate read (m680.driver.PlateRead): one or two plates (measurement, reference)."""
        if self.run is None:
            return
        for fs, nm, role in self.m680_sets:
            block = p.data.meas if fs == 0 else p.data.ref
            vals = {(r + 1, c + 1): v for r, row in enumerate(block.values) for c, v in enumerate(row)
                    if self.keep is None or (r + 1, c + 1) in self.keep}
            self.run.reads.append(PlateRead(fs, p.index, self.run.fs_labels[fs], p.t_s, p.received_s, vals))
        self._new_read(self.run.reads[-len(self.m680_sets)])

    def _new_read(self, pr) -> None:
        self.progress.setValue(len(self.run.reads))
        self._fill_fs_combo()
        if self.follow.isChecked():
            self.fs_combo.setCurrentIndex(pr.filter_set)
            self.k_spin.setMaximum(max(1, self.run.n_kinetic(pr.filter_set)))
            self.k_spin.setValue(pr.kinetic + 1)
        self._show_current()

    def _on_read_done(self, result, folder: str) -> None:
        self.reading = False
        self.progress.setVisible(False)
        self.last_folder = folder
        if self.run is not None:
            self.run.folder = Path(folder)
            self.run.title = f"{Path(folder).name}"
        state = _state(result)
        self.run_title.setText(f"<b>{Path(folder).name}</b> — {state} — saved in {folder}")
        self.log(f"Read finished ({state}). Saved: {folder}")
        self.state_label.setText(f"Last read: {state}")
        if self.act_auto_report.isChecked():
            try:
                self.save_report()
            except Exception as exc:  # the data are saved already; a report problem must not hide that
                self._info("Report", f"Run saved, but the report could not be written: {exc}", warn=True)
        self._refresh_runs()
        self._update_enabled()

    def _tick(self) -> None:
        if self.reading:
            el = time.monotonic() - self.t_read0
            left = max(0.0, self.eta_s - el)
            self.state_label.setText(f"Reading… {len(self.run.reads) if self.run else 0}/{self.total_reads} "
                                     f"plate read(s) · elapsed {_fmt_time(el)} · ≈ {_fmt_time(left)} left")

    # ================================================================== results display
    def _show_run(self, run: RunData) -> None:
        self.run = run
        self.plate_view.set_plate(run.rows, run.cols)
        self.run_title.setText(f"<b>{run.title}</b>" + (f" — {run.folder}" if run.folder else ""))
        self._fill_fs_combo()
        self._show_current()

    def _fill_fs_combo(self) -> None:
        if self.run is None:
            return
        n = self.run.n_filter_sets
        if self.fs_combo.count() != n:
            self.fs_combo.blockSignals(True)
            cur = self.fs_combo.currentIndex()
            self.fs_combo.clear()
            for i in range(n):
                self.fs_combo.addItem(f"{i + 1}: {self.run.fs_labels.get(i, '')}")
            self.fs_combo.setCurrentIndex(max(0, min(cur, n - 1)))
            self.fs_combo.blockSignals(False)

    def _show_current(self, *_) -> None:
        if self.run is None:
            return
        fs = max(0, self.fs_combo.currentIndex())
        nk = max(1, self.run.n_kinetic(fs))
        self.k_spin.blockSignals(True)
        self.k_spin.setMaximum(nk)
        self.k_spin.blockSignals(False)
        self.k_total.setText(f"of {nk}")
        pr = self.run.read(fs, self.k_spin.value() - 1)
        self.plate_view.set_values(pr.values if pr else {})
        if pr:
            rec = f", received {pr.received_s:.1f} s after start" if pr.received_s is not None else ""
            self.t_label.setText(f"t = {pr.t_s:.1f} s{rec}")
            self.t_label.setToolTip(self.t_label.text())
        else:
            self.t_label.setText("")
        self._selection_changed(self.plate_view.selected_wells())

    def _toggle_heat(self, on: bool) -> None:
        self.plate_view.model_.heat = on
        self._show_current()

    def _selection_changed(self, wells: list) -> None:
        if self.run is None or not wells:
            self.stats_label.setText("Select wells to see statistics and kinetic curves (Ctrl+C copies).")
            if self.plot is not None:
                self.plot.clear()
            return
        fs = max(0, self.fs_combo.currentIndex())
        pr = self.run.read(fs, self.k_spin.value() - 1)
        vals = [pr.values.get(w) for w in wells if pr and w in pr.values]
        s = stats(vals)
        if s["n"]:
            d = 4 if isinstance(s["min"], float) else 1                     # OD (Model 680) or counts (FLx800)
            self.stats_label.setText(
                f"<b>{len(wells)} well(s)</b> {wells_text(wells) if len(wells) <= 8 else ''} — "
                f"mean {s['mean']:.{d}f}, SD {s['sd']:.{d}f}, CV {s['cv']:.2f} %, min {fmt_value(s['min'])}, "
                f"max {fmt_value(s['max'])}"
                + (f", {s['over']} overrange" if s["over"] else ""))
        else:
            self.stats_label.setText(f"{len(wells)} well(s): no data")
        if self.plot is not None:
            self.plot.clear()
            if self.run.n_kinetic(fs) > 1:
                for i, w in enumerate(wells[:12]):
                    t, v = self.run.series(fs, w)
                    pts = [(a, b) for a, b in zip(t, v) if b is not None]
                    if pts:
                        self.plot.plot([p[0] for p in pts], [p[1] for p in pts], pen=pg.intColor(i, 12, maxValue=200),
                                       symbol="o", symbolSize=5, name=wells_text([w]))

    # ================================================================== runs
    def _refresh_runs(self) -> None:
        self.run_list.clear()
        if not self.runs_dir.exists():
            return
        for d in sorted((p for p in self.runs_dir.iterdir() if (p / "raw_long.csv").exists()), reverse=True)[:300]:
            it = QListWidgetItem(d.name)
            it.setData(Qt.UserRole, str(d))
            self.run_list.addItem(it)

    def open_run_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Open run folder", str(self.runs_dir))
        if d:
            self.open_run(d)

    def open_run(self, folder: str) -> None:
        try:
            rd = RunData.from_folder(folder)
        except Exception as exc:
            self._info("Open run", f"Could not read {folder}: {exc}", warn=True)
            return
        self.last_folder = folder
        self.follow.setChecked(False)
        self.fs_combo.clear()
        self._show_run(rd)
        self.log(f"Opened run {folder}")

    # ------------------------------------------------------------------ reports
    def save_report(self, path: Optional[str] = None) -> Optional[Path]:
        """Save the report of the run shown last (default: report.pdf in its run folder)."""
        if not self.last_folder:
            self._info("Report", "Open or read a run first.")
            return None
        from ..report import write_pdf
        rd = RunData.from_folder(self.last_folder)
        out = write_pdf(rd, path or Path(self.last_folder) / "report.pdf")
        self.log(f"Report saved: {out}")
        return out

    def save_report_dialog(self) -> None:
        if not self.last_folder:
            self._info("Report", "Open or read a run first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save run report", str(Path(self.last_folder) / "report.pdf"),
                                              "PDF (*.pdf)")
        if path:
            self.save_report(path)

    def print_report(self) -> None:
        if not self.last_folder:
            self._info("Report", "Open or read a run first.")
            return
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        from ..report import print_report
        printer = QPrinter(QPrinter.HighResolution)
        if QPrintDialog(printer, self).exec() == QDialog.Accepted:
            print_report(RunData.from_folder(self.last_folder), printer)
            self.log(f"Report printed: {self.last_folder}")

    def show_run_folder(self) -> None:
        if self.last_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_folder))

    def choose_runs_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder for runs", str(self.runs_dir))
        if d:
            self.runs_dir = Path(d)
            self.store.set("runs_dir", d)
            self.runs_label.setText(f"Runs: {self.runs_dir}")
            self._refresh_runs()

    # ================================================================== protocols
    def _refresh_protocols(self, select: Optional[str] = None) -> None:
        self.protocol_list.blockSignals(True)
        self.protocol_list.clear()
        for name, source, modified in self.store.protocols(self.instrument):
            it = QListWidgetItem(name)
            it.setToolTip(f"modified {modified}")
            self.protocol_list.addItem(it)
            if name == select:
                self.protocol_list.setCurrentItem(it)
        self.protocol_list.blockSignals(False)

    def _protocol_selected(self) -> None:
        it = self.protocol_list.currentItem()
        if it is None:
            return
        s = self.store.load(it.text())
        if s is not None and instrument_of(s) == self.instrument:
            self.current_protocol = s.name
            self.setup_panel.set_setup(s)

    def new_protocol(self) -> None:
        self.current_protocol = None
        self.protocol_list.clearSelection()
        cls = AbsSetup if self.m680 else ReadSetup
        self.setup_panel.set_setup(cls(name=self.store.unique_name("New protocol")))

    def save_protocol(self) -> None:
        s = self.setup_panel.setup()
        if s.name != self.current_protocol and s.name in self.store.protocol_names():
            if not self._question("Save protocol", f"Replace the existing protocol “{s.name}”?"):
                return
        try:
            self.store.save(s)
        except ValueError as exc:                 # the name belongs to the other instrument's protocol
            self._info("Save protocol", f"Not saved: {exc}. Choose another name.", warn=True)
            return
        self.current_protocol = s.name
        self._refresh_protocols(select=s.name)
        self.log(f"Protocol saved: {s.name}")

    def save_protocol_as(self) -> None:
        s = self.setup_panel.setup()
        s.name = self.store.unique_name(s.name)
        self.setup_panel.name.setText(s.name)
        self.store.save(s)
        self.current_protocol = s.name
        self._refresh_protocols(select=s.name)

    def delete_protocol(self) -> None:
        it = self.protocol_list.currentItem()
        if it is None:
            return
        if self._question("Delete protocol", f"Delete “{it.text()}”? (Runs already saved are not affected.)"):
            self.store.delete(it.text())
            self._refresh_protocols()

    def import_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import protocol", str(self.data_dir), "Protocol (*.json)")
        if path:
            s = setup_from_json(Path(path).read_text(encoding="utf-8"))
            if instrument_of(s) != self.instrument:
                self._info("Import protocol", f"This is a {INSTRUMENTS[instrument_of(s)]} protocol; switch to that "
                                              "instrument to import it.", warn=True)
                return
            s.name = self.store.unique_name(s.name)
            self.store.save(s)
            self._refresh_protocols(select=s.name)
            self.setup_panel.set_setup(s)

    def export_json(self) -> None:
        s = self.setup_panel.setup()
        path, _ = QFileDialog.getSaveFileName(self, "Export protocol", str(self.data_dir / f"{s.name}.json"),
                                              "Protocol (*.json)")
        if path:
            Path(path).write_text(s.to_json(), encoding="utf-8")

    # ================================================================== instrument
    def self_test(self) -> None:
        if self._question("System self-test", "Run the reader's system self-test? It takes about a minute "
                                              "and moves the carrier and filter wheels."):
            self._submit(self.worker.self_test, "Self-test")

    def _on_selftest(self, text: str, status: str) -> None:
        self.log(f"Self-test finished: {status}\n{text}")
        out = self.data_dir / "selftests"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"selftest_{datetime.now():%Y-%m-%d_%H%M%S}.txt").write_text(text + f"\nStatus: {status}\n",
                                                                           encoding="utf-8")
        self._info("Self-test", f"Status: {status}\n\n{text[:1500]}")

    def about(self) -> None:
        QMessageBox.about(self, "About", f"<b>TigerReader {__version__}</b><br>Plate-reader acquisition for the "
                                         "BioTek FLx800 and the Bio-Rad Model 680."
                                         f"<br>Instrument: {INSTRUMENTS[self.instrument]}"
                                         f"<br>Data folder: {self.data_dir}<br>Runs: {self.runs_dir}<br>"
                                         "Research use only.")

    def switch_instrument(self) -> None:
        """Close this window and open one for the other reader (asks first)."""
        if self.reading:
            return
        other = "flx800" if self.m680 else "m680"
        text = (f"Disconnect from the {INSTRUMENTS[self.instrument]} and switch to the {INSTRUMENTS[other]}?"
                if self.info is not None else f"Switch to the {INSTRUMENTS[other]}?")
        if not self._question("Switch instrument", text):
            return
        self.settings.setValue("instrument", other)
        new = MainWindow(data_dir=self.data_dir, ask=self.ask, instrument=other)
        _WINDOWS.append(new)
        new.show()
        self.close()
        return new

    # ================================================================== shutdown
    def closeEvent(self, event) -> None:
        if self.reading and not self._question("Quit", "A read is running. Abort it and quit?"):
            event.ignore()
            return
        if self.reading:
            self.worker.abort()
        self.settings.setValue("geometry", self.saveGeometry())
        self.thread.quit()
        self.thread.wait(15000)
        self.worker.shutdown()          # the worker thread has stopped: safe to close the port here
        self.store.close()
        event.accept()


def configure_app(app) -> None:
    """Application-wide settings, shared by main() and the tests (same look and sizes everywhere)."""
    app.setApplicationName("TigerReader")
    app.setStyle("Fusion")


def choose_instrument(settings: QSettings) -> Optional[str]:
    """Ask which reader to use (the last choice is preselected); None = cancelled."""
    keys = list(INSTRUMENTS)
    last = settings.value("instrument", "flx800")
    item, ok = QInputDialog.getItem(None, "TigerReader", "Which plate reader?", [INSTRUMENTS[k] for k in keys],
                                    keys.index(last) if last in keys else 0, False)
    if not ok:
        return None
    key = keys[[INSTRUMENTS[k] for k in keys].index(item)]
    settings.setValue("instrument", key)
    return key


def main(argv=None) -> int:
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    configure_app(app)
    instrument = choose_instrument(QSettings("TigerReader", "TigerReader"))
    if instrument is None:
        return 0
    w = MainWindow(instrument=instrument)
    w.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = ["MainWindow", "main"]
