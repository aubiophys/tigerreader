"""A user-level read protocol ("read setup") that the GUI edits, stores and turns into reader definitions.

It holds only reader settings (no data reduction yet). It is stored as JSON in the parameter
database (``storage.ParamStore``).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Optional, Tuple, Union

from .protocol.assay import (AssayDefinition, AssayType, Filter, FilterSet, Optics, ReadMode, ReadSeries,
                             SensitivityMode, WellRect, geometry, rc_to_well, well_to_rc)

Interval = Union[str, int]   # "min" = reader minimum, 0 = continuous, N = seconds
Well = Tuple[int, int]       # (row, col), 1-based

_WELL = re.compile(r"^([A-Pa-p])([0-9]{1,2})$")


def parse_wells(text: str, rows: int, cols: int) -> set[Well]:
    """Wells from 'A1:D6', 'A1, B2, C4' or a mix ('A1:A6, C4'). Raises ValueError with a plain message."""
    wells: set[Well] = set()
    for item in re.split(r"[,;\s]+", text.strip()):
        if not item:
            continue
        ends = item.replace("-", ":").split(":")
        if len(ends) > 2 or not all(_WELL.match(e) for e in ends):
            raise ValueError(f"wells: cannot read {item!r} (use A1, A1:D6 or A1, B2, C4)")
        (r1, c1), (r2, c2) = well_to_rc(ends[0]), well_to_rc(ends[-1])
        for r, c in ((r1, c1), (r2, c2)):
            if not (1 <= r <= rows and 1 <= c <= cols):
                raise ValueError(f"wells: {rc_to_well(r, c)} is outside this {rows}x{cols} plate")
        wells |= {(r, c) for r in range(min(r1, r2), max(r1, r2) + 1) for c in range(min(c1, c2), max(c1, c2) + 1)}
    return wells


def bounding_rect(wells: set[Well]) -> WellRect:
    rows = [r for r, _ in wells]
    cols = [c for _, c in wells]
    return WellRect(min(rows), min(cols), max(rows), max(cols))


def wells_text(wells) -> str:
    return ", ".join(rc_to_well(r, c) for r, c in sorted(wells))


@dataclass
class FilterSetSetup:
    excitation: str = "485/20"       # "485/20", "Plug" or "Open"
    emission: str = "528/20"
    optics: str = "top"              # "top" | "bottom"
    sensitivity: Union[int, str] = 100   # 25-255, or "auto" (on-board auto-sensitivity)
    samples: int = 10                # defaults: 10 samples, 350 ms before, 1 ms between
    delay_before_ms: int = 350
    delay_between_ms: int = 1

    def to_filter_set(self) -> FilterSet:
        auto = str(self.sensitivity).lower() == "auto"
        return FilterSet(Filter.parse(self.excitation), Filter.parse(self.emission),
                         Optics.BOTTOM if self.optics == "bottom" else Optics.TOP,
                         int(self.samples), int(self.delay_before_ms), int(self.delay_between_ms),
                         SensitivityMode.AUTO_ONBOARD if auto else SensitivityMode.FIXED,
                         0 if auto else int(self.sensitivity))


@dataclass
class ReadSetup:
    name: str = "New protocol"
    description: str = ""
    luminescence: bool = False
    plate: str = "8x12"                       # key of GEOMETRIES (or an alias)
    wells: str = ""                           # "" = whole plate, "A1:H6", or a list "A1, B2, C4"
    kinetic: bool = False
    kinetic_reads: int = 10
    kinetic_interval: Interval = "min"
    auto_well: str = "A1"                     # well used by on-board auto-sensitivity
    auto_target: int = 90000                  # reading the auto well is brought to (100-90000)
    filter_sets: list[FilterSetSetup] = field(default_factory=lambda: [FilterSetSetup()])

    # ------------------------------------------------------------------ conversion
    def well_set(self) -> Optional[set[Well]]:
        """The wells to keep (None = whole plate)."""
        if not self.wells.strip():
            return None
        g = geometry(self.plate)
        return parse_wells(self.wells, g.rows, g.cols)

    def selected_wells(self) -> Optional[set[Well]]:
        """The listed wells when they do not fill their covering block (None = keep everything read).

        The reader reads the smallest block that covers the listed wells; only the listed wells are kept in
        the display and the run files."""
        ws = self.well_set()
        if ws is None:
            return None
        r = bounding_rect(ws)
        return None if len(ws) == r.n_rows * r.n_cols else ws

    @property
    def fastest(self) -> bool:
        return self.kinetic and str(self.kinetic_interval).lower() in ("min", "fastest")

    def to_assay(self) -> Union[AssayDefinition, ReadSeries]:
        """Reader definition(s). More than 2 filter sets become a read series."""
        if not self.filter_sets:
            raise ValueError("at least one filter set is needed")
        fsets = [f.to_filter_set() for f in self.filter_sets]
        interval = 0 if self.fastest else int(self.kinetic_interval) if self.kinetic else 10
        base = AssayDefinition(
            filter_sets=tuple(fsets[:2]),
            read_mode=ReadMode.KINETIC if self.kinetic else ReadMode.ENDPOINT,
            assay_type=AssayType.LUMINESCENCE if self.luminescence else AssayType.FLUORESCENCE,
            geometry=geometry(self.plate),
            wells=bounding_rect(self.well_set()) if self.wells.strip() else None,
            onboard_autosense_well=well_to_rc(self.auto_well or "A1"),
            onboard_autosense_max=int(self.auto_target),
            kinetic_reads=int(self.kinetic_reads) if self.kinetic else 10,
            kinetic_interval_s=interval,
        )
        return ReadSeries.split(base, fsets) if len(fsets) > 2 else base

    def definitions(self) -> list[AssayDefinition]:
        a = self.to_assay()
        return a.definitions if isinstance(a, ReadSeries) else [a]

    def problems(self, table=None, info=None) -> list[str]:
        """Everything that would stop this read, in plain words (empty = OK)."""
        try:
            defs = self.definitions()
        except (ValueError, KeyError) as exc:
            return [str(exc.args[0] if exc.args else exc)]
        out: list[str] = []
        for d in defs:
            out += d.validate(table)
            if info is not None:
                out += info.check_assay(d)
        return list(dict.fromkeys(out))

    def estimated_time_s(self) -> float:
        """Rough duration of the whole read (one pass per kinetic read)."""
        defs = self.definitions()
        passes = sum(d.estimated_pass_s() for d in defs)
        if not self.kinetic:
            return passes
        k = int(self.kinetic_reads)
        if self.fastest or not int(self.kinetic_interval):
            return passes * k
        return max(passes, int(self.kinetic_interval)) * (k - 1) + passes

    # ------------------------------------------------------------------ storage
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ReadSetup":
        d = dict(d)
        d["filter_sets"] = [FilterSetSetup(**f) for f in d.get("filter_sets", [])]
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "ReadSetup":
        return cls.from_dict(json.loads(text))


def describe(setup: ReadSetup) -> str:
    """One-line summary, e.g. 'Fluorescence · 8x12 · 485/20→528/20 S100 · endpoint'."""
    fs = ", ".join(f"{f.excitation}→{f.emission} S{f.sensitivity}" for f in setup.filter_sets)
    kin = (f"kinetic {setup.kinetic_reads}× " + ("min" if setup.fastest else
                                                  "continuous" if str(setup.kinetic_interval) == "0"
                                                  else f"{setup.kinetic_interval} s")) if setup.kinetic else "endpoint"
    return " · ".join(x for x in ["Luminescence" if setup.luminescence else "Fluorescence", setup.plate,
                                  setup.wells or None, fs, kin] if x)


def optional_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
