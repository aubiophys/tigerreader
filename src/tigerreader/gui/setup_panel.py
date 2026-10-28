"""Protocol (read setup) editor."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFormLayout, QGroupBox,
                               QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QRadioButton, QSpinBox,
                               QTableWidget, QVBoxLayout, QWidget)

from ..protocol.assay import GEOMETRIES, PLATE_CHOICES, FilterTable, rc_to_well
from ..readsetup import FilterSetSetup, ReadSetup
from ..transport.sim import DEFAULT_TABLE

COLS = ["Excitation", "Emission", "Optics", "Sensitivity", "Samples", "Delay (ms)", "Between (ms)"]


class SensitivityCell(QWidget):
    """Sensitivity 25-255 plus an Auto tick box (on-board auto-sensitivity)."""

    def __init__(self, value, on_change):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.spin = QSpinBox()
        self.spin.setRange(25, 255)
        self.auto = QCheckBox("Auto")
        self.auto.setToolTip("On-board auto-sensitivity: the reader picks the gain so that the auto-sensitivity "
                             "well reads the auto target")
        auto = str(value).lower() == "auto"
        self.spin.setValue(100 if auto else int(value))
        self.auto.setChecked(auto)
        self.spin.setEnabled(not auto)
        lay.addWidget(self.spin)
        lay.addWidget(self.auto)
        self.spin.valueChanged.connect(on_change)
        self.auto.toggled.connect(self.spin.setDisabled)
        self.auto.toggled.connect(on_change)

    def value(self):
        return "auto" if self.auto.isChecked() else self.spin.value()


def _fmt_time(s: float) -> str:
    s = int(round(s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min {sec:02d} s" if m else f"{sec} s"


class SetupPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.table: FilterTable = DEFAULT_TABLE
        self.info = None
        self._loading = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)

        gen = QGroupBox("Protocol")
        f = QFormLayout(gen)
        self.name = QLineEdit("New protocol")
        self.description = QLineEdit()
        f.addRow("Name", self.name)
        f.addRow("Description", self.description)
        lay.addWidget(gen)

        rm = QGroupBox("Read method")
        f = QFormLayout(rm)
        self.fluor = QRadioButton("Fluorescence")
        self.lum = QRadioButton("Luminescence")
        self.fluor.setChecked(True)
        g1 = QButtonGroup(self)
        g1.addButton(self.fluor)
        g1.addButton(self.lum)
        row = QHBoxLayout()
        row.addWidget(self.fluor)
        row.addWidget(self.lum)
        row.addStretch()
        f.addRow("Detection", row)

        self.plate = QComboBox()
        for key in PLATE_CHOICES:
            self.plate.addItem(GEOMETRIES[key].name, key)
        self.plate.setCurrentIndex(PLATE_CHOICES.index("8x12"))
        f.addRow("Plate", self.plate)
        self.wells = QLineEdit()
        self.wells.setPlaceholderText("whole plate  (or a block A1:D6, or a list A1, B2, C4)")
        self.wells.setToolTip("Empty = whole plate. A block (A1:D6) or a list of wells and blocks (A1, B2, C4:C6).\n"
                              "For a list, the reader reads the smallest block that covers the wells and only the\n"
                              "listed wells are kept.")
        f.addRow("Wells", self.wells)

        self.endpoint = QRadioButton("Endpoint")
        self.kinetic = QRadioButton("Kinetic")
        self.endpoint.setChecked(True)
        g2 = QButtonGroup(self)
        g2.addButton(self.endpoint)
        g2.addButton(self.kinetic)
        row = QHBoxLayout()
        row.addWidget(self.endpoint)
        row.addWidget(self.kinetic)
        row.addStretch()
        f.addRow("Mode", row)

        kin = QHBoxLayout()
        self.kin_reads = QSpinBox()
        self.kin_reads.setRange(2, 300)
        self.kin_reads.setValue(10)
        self.kin_reads.setSuffix(" reads")
        self.interval_mode = QComboBox()
        self.interval_mode.addItems(["Reader minimum", "Continuous (no pause)", "Fixed interval"])
        self.interval_s = QSpinBox()
        self.interval_s.setRange(1, 9999)
        self.interval_s.setValue(120)
        self.interval_s.setSuffix(" s")
        kin.addWidget(self.kin_reads)
        kin.addWidget(self.interval_mode)
        kin.addWidget(self.interval_s)
        self.kin_box = QWidget()
        self.kin_box.setLayout(kin)
        kin.setContentsMargins(0, 0, 0, 0)
        f.addRow("Kinetic", self.kin_box)
        lay.addWidget(rm)

        fsb = QGroupBox("Filter sets")
        v = QVBoxLayout(fsb)
        self.fs_table = QTableWidget(0, len(COLS))
        self.fs_table.setHorizontalHeaderLabels(COLS)
        self.fs_table.verticalHeader().setDefaultSectionSize(26)
        self.fs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.fs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.fs_table.setMinimumHeight(90)
        self.fs_table.setMaximumHeight(200)
        self.fs_table.horizontalHeaderItem(3).setToolTip('Gain 25-255, or tick Auto: the reader picks the gain '
                                                         'from the auto-sensitivity well')
        self.fs_table.horizontalHeaderItem(5).setToolTip('Delay before sampling each well')
        self.fs_table.horizontalHeaderItem(6).setToolTip('Delay between the samples of a well')
        v.addWidget(self.fs_table)
        row = QHBoxLayout()
        self.add_fs = QPushButton("Add filter set")
        self.del_fs = QPushButton("Remove")
        self.auto_well = QLineEdit("A1")
        self.auto_well.setMaximumWidth(50)
        self.auto_well.setToolTip("Well used by on-board auto-sensitivity (put the brightest sample here). "
                                  "Used by filter sets whose Sensitivity is set to Auto.")
        row.addWidget(self.add_fs)
        row.addWidget(self.del_fs)
        row.addStretch()
        row.addWidget(QLabel("Auto well"))
        row.addWidget(self.auto_well)
        self.auto_target = QSpinBox()
        self.auto_target.setRange(100, 90000)
        self.auto_target.setSingleStep(1000)
        self.auto_target.setValue(90000)
        self.auto_target.setToolTip("Auto-sensitivity target: the gain is chosen so that the auto well reads this "
                                    "value. Lower it to leave room for wells brighter than the auto well.")
        row.addWidget(QLabel("target"))
        row.addWidget(self.auto_target)
        v.addLayout(row)
        lay.addWidget(fsb)

        self.check = QLabel()
        self.check.setWordWrap(True)
        self.check.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.check)
        lay.addStretch()

        # signals
        for w in (self.name, self.description, self.wells, self.auto_well):
            w.textChanged.connect(self._changed)
        for w in (self.fluor, self.lum, self.endpoint, self.kinetic):
            w.toggled.connect(self._changed)
        self.plate.currentIndexChanged.connect(self._changed)
        self.kin_reads.valueChanged.connect(self._changed)
        self.auto_target.valueChanged.connect(self._changed)
        self.interval_mode.currentIndexChanged.connect(self._changed)
        self.interval_s.valueChanged.connect(self._changed)
        self.lum.toggled.connect(self._lum_toggled)
        self.add_fs.clicked.connect(self._add_clicked)
        self.del_fs.clicked.connect(self._del_clicked)

        self.set_setup(ReadSetup())

    # ------------------------------------------------------------------ filter-set rows
    def _filter_combo(self, emission: bool, value: str) -> QComboBox:
        cb = QComboBox()
        cb.setEditable(True)
        items = [str(f) for f in (self.table.emission if emission else self.table.excitation)]
        items += ["Open"] if emission and "Open" not in items else []
        items += ["Plug"] if not emission and "Plug" not in items else []
        cb.addItems(items)
        cb.setCurrentText(value)
        cb.currentTextChanged.connect(self._changed)
        return cb

    def _spin(self, lo: int, hi: int, value: int, step: int = 1) -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setValue(value)
        sp.valueChanged.connect(self._changed)
        return sp

    def _add_row(self, fs: FilterSetSetup) -> None:
        r = self.fs_table.rowCount()
        self.fs_table.insertRow(r)
        self.fs_table.setCellWidget(r, 0, self._filter_combo(False, fs.excitation))
        self.fs_table.setCellWidget(r, 1, self._filter_combo(True, fs.emission))
        opt = QComboBox()
        opt.addItems(["Top", "Bottom"])
        opt.setCurrentIndex(1 if fs.optics == "bottom" else 0)
        opt.currentIndexChanged.connect(self._changed)
        self.fs_table.setCellWidget(r, 2, opt)
        self.fs_table.setCellWidget(r, 3, SensitivityCell(fs.sensitivity, self._changed))
        self.fs_table.setCellWidget(r, 4, self._spin(1, 255, fs.samples))
        self.fs_table.setCellWidget(r, 5, self._spin(10, 2550, fs.delay_before_ms, 10))
        self.fs_table.setCellWidget(r, 6, self._spin(0, 255, fs.delay_between_ms))

    def _row(self, r: int) -> FilterSetSetup:
        w = self.fs_table.cellWidget
        return FilterSetSetup(excitation=w(r, 0).currentText().strip(), emission=w(r, 1).currentText().strip(),
                              optics="bottom" if w(r, 2).currentIndex() == 1 else "top",
                              sensitivity=w(r, 3).value(),
                              samples=w(r, 4).value(), delay_before_ms=w(r, 5).value(),
                              delay_between_ms=w(r, 6).value())

    def _add_clicked(self) -> None:
        if self.fs_table.rowCount() >= 6:
            return
        last = self._row(self.fs_table.rowCount() - 1) if self.fs_table.rowCount() else FilterSetSetup()
        self._add_row(last)
        self._changed()

    def _del_clicked(self) -> None:
        if self.fs_table.rowCount() <= 1:
            return
        r = self.fs_table.currentRow()
        self.fs_table.removeRow(r if r >= 0 else self.fs_table.rowCount() - 1)
        self._changed()

    def _lum_toggled(self, on: bool) -> None:
        if self._loading:
            return
        if on:      # luminescence: Plug in excitation, 580/50 or Open emission, higher gain
            for r in range(self.fs_table.rowCount()):
                self.fs_table.cellWidget(r, 0).setCurrentText("Plug")
        else:
            for r in range(self.fs_table.rowCount()):
                if self.fs_table.cellWidget(r, 0).currentText() == "Plug":
                    self.fs_table.cellWidget(r, 0).setCurrentText("485/20")
                    self.fs_table.cellWidget(r, 1).setCurrentText("528/20")

    # ------------------------------------------------------------------ public
    def set_reader(self, info, table: Optional[FilterTable]) -> None:
        """Use the connected reader's filter table and model for choices and checks."""
        self.info = info
        if table is not None:
            self.table = table
        self.set_setup(self.setup())

    def setup(self) -> ReadSetup:
        mode = self.interval_mode.currentIndex()
        interval = "min" if mode == 0 else 0 if mode == 1 else self.interval_s.value()
        return ReadSetup(
            name=self.name.text().strip() or "Untitled", description=self.description.text(),
            luminescence=self.lum.isChecked(), plate=self.plate.currentData(), wells=self.wells.text().strip(),
            kinetic=self.kinetic.isChecked(), kinetic_reads=self.kin_reads.value(), kinetic_interval=interval,
            auto_well=self.auto_well.text().strip() or "A1", auto_target=self.auto_target.value(),
            filter_sets=[self._row(r) for r in range(self.fs_table.rowCount())])

    def set_setup(self, s: ReadSetup) -> None:
        self._loading = True
        try:
            self.name.setText(s.name)
            self.description.setText(s.description)
            (self.lum if s.luminescence else self.fluor).setChecked(True)
            key = s.plate if s.plate in PLATE_CHOICES else "8x12"
            self.plate.setCurrentIndex(PLATE_CHOICES.index(key))
            self.wells.setText(s.wells)
            (self.kinetic if s.kinetic else self.endpoint).setChecked(True)
            self.kin_reads.setValue(int(s.kinetic_reads))
            iv = str(s.kinetic_interval).lower()
            self.interval_mode.setCurrentIndex(0 if iv in ("min", "fastest") else 1 if iv == "0" else 2)
            if iv not in ("min", "fastest", "0"):
                self.interval_s.setValue(int(s.kinetic_interval))
            self.auto_well.setText(s.auto_well)
            self.auto_target.setValue(int(s.auto_target))
            self.fs_table.setRowCount(0)
            for fs in s.filter_sets or [FilterSetSetup()]:
                self._add_row(fs)
        finally:
            self._loading = False
        self._changed()

    def problems(self) -> list[str]:
        return self.setup().problems(self.table, self.info)

    def _changed(self, *_) -> None:
        if self._loading:
            return
        kin = self.kinetic.isChecked()
        self.kin_box.setEnabled(kin)
        self.interval_s.setEnabled(kin and self.interval_mode.currentIndex() == 2)
        s = self.setup()
        probs = s.problems(self.table, self.info)
        if probs:
            self.check.setText("<b style='color:#b00020'>Cannot read yet:</b><br>" +
                               "<br>".join(f"• {p}" for p in probs))
        else:
            try:
                eta = _fmt_time(s.estimated_time_s())
            except Exception:  # pragma: no cover
                eta = "?"
            series = len(s.definitions()) > 1
            sel = s.selected_wells()
            if sel is not None:
                r = s.definitions()[0].read_rect
                eta += (f"<br>Reads block {rc_to_well(r.first_row, r.first_col)}:{rc_to_well(r.last_row, r.last_col)}"
                        f" ({r.n_rows * r.n_cols} wells) and keeps the {len(sel)} listed wells")
            self.check.setText(f"<b style='color:#1b7f3b'>Ready.</b> Estimated read time ≈ {eta}"
                               + (" (read as a series of definitions, 2 filter sets each)" if series else "")
                               + ("<br>Kinetic time stamps: both filter sets of a cycle carry the cycle's start "
                                  "time; set 2 is read one plate pass later." if kin and len(s.filter_sets) > 1
                                  else ""))
        self.changed.emit()
