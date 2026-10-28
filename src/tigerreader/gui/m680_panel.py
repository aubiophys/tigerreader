"""Protocol editor for the Bio-Rad Model 680 (absorbance). Same interface as ``SetupPanel``."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QRadioButton, QSpinBox, QVBoxLayout, QWidget)

from ..m680.setup import AbsSetup
from ..m680.sim import Sim680Config
from .setup_panel import _fmt_time

MIX = [("L", "Low"), ("M", "Medium"), ("H", "High")]


class M680SetupPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.info = None
        self.filters = [w for w in Sim680Config().filters if w]      # until a reader is connected
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

        rm = QGroupBox("Absorbance read")
        f = QFormLayout(rm)
        self.meas = QComboBox()
        self.ref = QComboBox()
        self.ref.setToolTip("Optional second wavelength, stored as filter set 2 (raw; subtract it in your analysis)")
        f.addRow("Wavelength", self.meas)
        f.addRow("Reference", self.ref)
        self.speed = QComboBox()
        self.speed.addItem("Normal (15 s, 30 s with reference)", "normal")
        self.speed.addItem("Fast (6 s, 10 s with reference)", "fast")
        f.addRow("Speed", self.speed)
        mix = QHBoxLayout()
        self.mix_time = QSpinBox()
        self.mix_time.setRange(0, 999)
        self.mix_time.setSuffix(" s")
        self.mix_time.setToolTip("Mixing before each plate read (0 = no mixing)")
        self.mix_speed = QComboBox()
        for key, text in MIX:
            self.mix_speed.addItem(text, key)
        mix.addWidget(self.mix_time)
        mix.addWidget(self.mix_speed)
        mix.addStretch()
        f.addRow("Mixing", mix)
        self.wells = QLineEdit()
        self.wells.setPlaceholderText("whole plate  (or a block A1:D6, or a list A1, B2, C4)")
        self.wells.setToolTip("The Model 680 always reads the whole plate; only these wells are kept.")
        f.addRow("Wells", self.wells)
        self.endpoint = QRadioButton("Endpoint")
        self.kinetic = QRadioButton("Kinetic")
        self.endpoint.setChecked(True)
        g = QButtonGroup(self)
        g.addButton(self.endpoint)
        g.addButton(self.kinetic)
        row = QHBoxLayout()
        row.addWidget(self.endpoint)
        row.addWidget(self.kinetic)
        row.addStretch()
        f.addRow("Mode", row)
        kin = QHBoxLayout()
        kin.setContentsMargins(0, 0, 0, 0)
        self.kin_reads = QSpinBox()
        self.kin_reads.setRange(2, 1000)
        self.kin_reads.setValue(10)
        self.kin_reads.setSuffix(" reads")
        self.interval_s = QSpinBox()
        self.interval_s.setRange(0, 86400)
        self.interval_s.setValue(60)
        self.interval_s.setPrefix("every ")
        self.interval_s.setSuffix(" s")
        self.interval_s.setToolTip("Start to start; 0 = one read after the other. Timed by the program "
                                   "(the reader has no remote kinetic mode).")
        kin.addWidget(self.kin_reads)
        kin.addWidget(self.interval_s)
        kin.addStretch()
        self.kin_box = QWidget()
        self.kin_box.setLayout(kin)
        f.addRow("Kinetic", self.kin_box)
        lay.addWidget(rm)

        self.check = QLabel()
        self.check.setWordWrap(True)
        self.check.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.check)
        lay.addStretch()

        for w in (self.name, self.description, self.wells):
            w.textChanged.connect(self._changed)
        for w in (self.meas, self.ref, self.speed, self.mix_speed):
            w.currentIndexChanged.connect(self._changed)
        for w in (self.mix_time, self.kin_reads, self.interval_s):
            w.valueChanged.connect(self._changed)
        for w in (self.endpoint, self.kinetic):
            w.toggled.connect(self._changed)
        self.set_setup(AbsSetup())

    # ------------------------------------------------------------------ public (same as SetupPanel)
    def set_reader(self, info, table=None) -> None:
        """Offer the wavelengths installed in the connected reader."""
        self.info = info
        if info is not None and info.filters:
            self.filters = [w for _, w in sorted(info.filters.items()) if w]
        self.set_setup(self.setup())

    def setup(self) -> AbsSetup:
        return AbsSetup(name=self.name.text().strip() or "Untitled", description=self.description.text(),
                        meas_nm=self.meas.currentData(), ref_nm=self.ref.currentData(),
                        speed=self.speed.currentData(), mix_time_s=self.mix_time.value(),
                        mix_speed=self.mix_speed.currentData(), kinetic=self.kinetic.isChecked(),
                        kinetic_reads=self.kin_reads.value(), kinetic_interval_s=self.interval_s.value(),
                        wells=self.wells.text().strip())

    def set_setup(self, s: AbsSetup) -> None:
        self._loading = True
        try:
            self.name.setText(s.name)
            self.description.setText(s.description)
            nms = sorted(set(self.filters) | {s.meas_nm} | ({s.ref_nm} if s.ref_nm else set()))
            self.meas.clear()
            self.ref.clear()
            self.ref.addItem("none", None)
            for nm in nms:                       # a stored wavelength that is not installed stays visible
                mark = "" if nm in self.filters else "  (not installed)"
                self.meas.addItem(f"{nm} nm{mark}", nm)
                self.ref.addItem(f"{nm} nm{mark}", nm)
            self.meas.setCurrentIndex(self.meas.findData(s.meas_nm))
            self.ref.setCurrentIndex(max(0, self.ref.findData(s.ref_nm)) if s.ref_nm else 0)
            self.speed.setCurrentIndex(max(0, self.speed.findData(s.speed)))
            self.mix_time.setValue(int(s.mix_time_s))
            self.mix_speed.setCurrentIndex(max(0, self.mix_speed.findData(s.mix_speed)))
            self.wells.setText(s.wells)
            (self.kinetic if s.kinetic else self.endpoint).setChecked(True)
            self.kin_reads.setValue(int(s.kinetic_reads))
            self.interval_s.setValue(int(s.kinetic_interval_s))
        finally:
            self._loading = False
        self._changed()

    def problems(self) -> list:
        return self.setup().problems(None, self.info)

    def _changed(self, *_) -> None:
        if self._loading:
            return
        self.kin_box.setEnabled(self.kinetic.isChecked())
        s = self.setup()
        probs = s.problems(None, self.info)
        if probs:
            self.check.setText("<b style='color:#b00020'>Cannot read yet:</b><br>" +
                               "<br>".join(f"• {p}" for p in probs))
        else:
            txt = f"<b style='color:#1b7f3b'>Ready.</b> Estimated read time ≈ {_fmt_time(s.estimated_time_s())}"
            if s.read_area():
                txt += f"<br>Reads the whole plate and keeps {s.read_area()}"
            if s.kinetic and s.kinetic_interval_s < s.read_time_s():
                txt += (f"<br>One read takes about {s.read_time_s():.0f} s: reads follow one after the other "
                        "(interval shorter than a read).")
            if s.kinetic:
                txt += "<br>Abort stops after the plate read in progress (the reader has no abort command)."
            self.check.setText(txt)
        self.changed.emit()
