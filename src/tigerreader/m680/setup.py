"""Absorbance read protocol for the Bio-Rad Model 680 (what the GUI edits and stores).

Stored as JSON in the parameter database like the FLx800 ``ReadSetup``; ``"instrument": "m680"`` tells them
apart. The reader always reads the whole plate; a well list only selects the wells that are kept.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Optional

from ..readsetup import bounding_rect, parse_wells
from .protocol import read_time_s

INSTRUMENT = "m680"


@dataclass
class AbsSetup:
    name: str = "New protocol"
    description: str = ""
    meas_nm: int = 450
    ref_nm: Optional[int] = None              # None = single wavelength
    speed: str = "normal"                     # "normal" | "fast"
    mix_time_s: int = 0
    mix_speed: str = "L"                      # L, M, H
    kinetic: bool = False
    kinetic_reads: int = 10
    kinetic_interval_s: int = 60              # start to start; 0 = back to back
    wells: str = ""                           # "" = whole plate; block or list = wells kept
    instrument: str = INSTRUMENT

    @property
    def n_reads(self) -> int:
        return int(self.kinetic_reads) if self.kinetic else 1

    def selected_wells(self) -> Optional[set]:
        """Wells to keep (None = all 96). The reader always reads the whole plate."""
        if not self.wells.strip():
            return None
        ws = parse_wells(self.wells, 8, 12)
        return None if len(ws) == 96 else ws

    def read_area(self) -> Optional[str]:
        ws = self.selected_wells()
        if ws is None:
            return None
        r = bounding_rect(ws)
        return f"{len(ws)} wells in {chr(64 + r.first_row)}{r.first_col}:{chr(64 + r.last_row)}{r.last_col}"

    def problems(self, table=None, info=None) -> list:
        """Everything that would stop this read, in plain words (empty = OK). *table* is unused (FLx800 only)."""
        out = []
        try:
            self.selected_wells()
        except ValueError as exc:
            out.append(str(exc))
        if self.ref_nm is not None and self.ref_nm == self.meas_nm:
            out.append("the reference wavelength must differ from the measurement wavelength")
        if self.speed not in ("normal", "fast"):
            out.append("speed must be normal or fast")
        if not 0 <= int(self.mix_time_s) <= 999:
            out.append("mixing time must be 0-999 s")
        if self.mix_speed not in ("L", "M", "H"):
            out.append("mixing speed must be L, M or H")
        if self.kinetic and not 2 <= int(self.kinetic_reads) <= 1000:
            out.append("kinetic: 2-1000 reads")
        if int(self.kinetic_interval_s) < 0:
            out.append("kinetic interval must be 0 s or more")
        if info is not None and getattr(info, "filters", None):
            installed = {w for w in info.filters.values() if w}
            for label, nm in (("measurement", self.meas_nm), ("reference", self.ref_nm)):
                if nm is not None and nm not in installed:
                    out.append(f"{label} wavelength {nm} nm: no such filter in the reader "
                               f"(installed: {', '.join(str(w) for w in sorted(installed))})")
        return out

    def read_time_s(self) -> float:
        return read_time_s(self.speed, self.ref_nm is not None) + int(self.mix_time_s)

    def estimated_time_s(self) -> float:
        one = self.read_time_s()
        if not self.kinetic:
            return one
        return max(one, int(self.kinetic_interval_s)) * (int(self.kinetic_reads) - 1) + one

    # ------------------------------------------------------------------ storage
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AbsSetup":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "AbsSetup":
        return cls.from_dict(json.loads(text))


def describe(s: AbsSetup) -> str:
    wl = f"{s.meas_nm} nm" + (f" / ref {s.ref_nm} nm" if s.ref_nm else "")
    kin = f"kinetic {s.kinetic_reads}× every {s.kinetic_interval_s} s" if s.kinetic else "endpoint"
    return " · ".join(x for x in ["Absorbance", wl, s.speed, s.wells or None, kin] if x)
