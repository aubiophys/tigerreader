"""Plate grid with a heat-map background (Model/View)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from ..protocol.assay import rc_to_well
from ..rundata import fmt_value

# viridis-like ramp (dark blue -> green -> yellow), readable with black/white text
_RAMP = [(68, 1, 84), (59, 82, 139), (33, 145, 140), (94, 201, 98), (253, 231, 37)]


def ramp(x: float) -> QColor:
    x = min(1.0, max(0.0, x)) * (len(_RAMP) - 1)
    i = min(int(x), len(_RAMP) - 2)
    f = x - i
    a, b = _RAMP[i], _RAMP[i + 1]
    return QColor(*(round(a[j] + (b[j] - a[j]) * f) for j in range(3)))


class PlateModel(QAbstractTableModel):
    def __init__(self, rows: int = 8, cols: int = 12):
        super().__init__()
        self.rows, self.cols = rows, cols
        self.values: dict[tuple[int, int], Optional[int]] = {}
        self.heat = True
        self._lo, self._hi = 0.0, 1.0

    def set_plate(self, rows: int, cols: int) -> None:
        self.beginResetModel()
        self.rows, self.cols = rows, cols
        self.values = {}
        self.endResetModel()

    def set_values(self, values: dict[tuple[int, int], Optional[int]]) -> None:
        self.beginResetModel()
        self.values = dict(values)
        nums = [v for v in self.values.values() if v is not None]
        self._lo, self._hi = (min(nums), max(nums)) if nums else (0.0, 1.0)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self.rows

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self.cols

    def well_value(self, index: QModelIndex):
        key = (index.row() + 1, index.column() + 1)
        return key in self.values, self.values.get(key)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        present, v = self.well_value(index)
        if role == Qt.DisplayRole:
            if not present:
                return ""
            return fmt_value(v)
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignCenter)
        if role == Qt.ToolTipRole:
            w = rc_to_well(index.row() + 1, index.column() + 1)
            return f"{w}: {'not read' if not present else 'overrange' if v is None else fmt_value(v)}"
        if not present or not self.heat:
            return None
        x = 1.0 if v is None else (v - self._lo) / (self._hi - self._lo) if self._hi > self._lo else 0.5
        if role == Qt.BackgroundRole:
            return ramp(x)
        if role == Qt.ForegroundRole:
            return QColor("black") if x > 0.55 else QColor("white")
        return None

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        return chr(ord("A") + section) if orientation == Qt.Vertical else str(section + 1)


class PlateView(QTableView):
    selection_changed = Signal(list)       # [(row, col), ...] 1-based

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = PlateModel()
        self.setModel(self.model_)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setCornerButtonEnabled(True)
        self.setAlternatingRowColors(False)
        self.selectionModel().selectionChanged.connect(self._emit_selection)
        self._apply_font()

    def set_plate(self, rows: int, cols: int) -> None:
        self.model_.set_plate(rows, cols)
        self._apply_font()

    def set_values(self, values) -> None:
        sel = self.selected_wells()
        self.model_.set_values(values)
        if sel:
            self._emit_selection()

    def _apply_font(self) -> None:
        f = QFont(self.font())
        f.setPointSize(7 if self.model_.cols > 12 else 9)
        self.setFont(f)

    def selected_wells(self) -> list[tuple[int, int]]:
        return sorted({(i.row() + 1, i.column() + 1) for i in self.selectionModel().selectedIndexes()})

    def _emit_selection(self, *_):
        self.selection_changed.emit(self.selected_wells())

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            self.copy_selection()
            return
        super().keyPressEvent(event)

    def copy_selection(self) -> None:
        """Copy the selected block (or the whole plate) as tab-separated text with headers."""
        wells = self.selected_wells() or [(r + 1, c + 1) for r in range(self.model_.rows)
                                          for c in range(self.model_.cols)]
        rows = sorted({r for r, _ in wells})
        cols = sorted({c for _, c in wells})
        lines = ["\t" + "\t".join(str(c) for c in cols)]
        for r in rows:
            cells = []
            for c in cols:
                cells.append(fmt_value(self.model_.values[(r, c)]) if (r, c) in self.model_.values else "")
            lines.append(chr(64 + r) + "\t" + "\t".join(cells))
        QGuiApplication.clipboard().setText("\n".join(lines) + "\n")
