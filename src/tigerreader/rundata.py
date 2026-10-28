"""Plate data for display: built live from read blocks, or loaded from a saved run folder."""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .protocol.assay import AssayDefinition, geometry, rc_to_well, well_to_rc


@dataclass
class PlateRead:
    filter_set: int                    # 0-based over the whole run
    kinetic: int                       # 0-based kinetic read index
    label: str                         # "485/20 → 528/20 S100"
    t_s: float                         # reader time stamp
    received_s: Optional[float]
    values: dict[tuple[int, int], Optional[float]]  # (row, col) 1-based -> value (None = overrange);
                                                     # int for the FLx800 (RFU/RLU), float for the Model 680 (OD)


@dataclass
class RunData:
    rows: int = 8
    cols: int = 12
    title: str = ""
    folder: Optional[Path] = None
    reads: list[PlateRead] = field(default_factory=list)
    fs_labels: dict[int, str] = field(default_factory=dict)
    header: dict[str, str] = field(default_factory=dict)      # run_header.txt (saved runs)

    # ------------------------------------------------------------------ building
    @classmethod
    def for_definitions(cls, defs: list[AssayDefinition], title: str = "") -> "RunData":
        g = defs[0].geometry
        rd = cls(g.rows, g.cols, title)
        n = 0
        for a in defs:
            for fs in a.filter_sets:
                rd.fs_labels[n] = fs_label(fs)
                n += 1
        return rd

    def add_block(self, fs_offset: int, blk, assay: AssayDefinition, index_in_def: int,
                  keep: Optional[set] = None) -> PlateRead:
        """Add a data block. *index_in_def* = arrival index within its definition (0, 1, ...).
        *keep* = wells to keep (well list); None keeps the whole block."""
        nfs = len(assay.filter_sets)
        k, i = divmod(index_in_def, nfs)
        r0, c0 = assay.read_rect.first_row, assay.read_rect.first_col
        vals = {(r0 + ri, c0 + ci): v for ri, row in enumerate(blk.values) for ci, v in enumerate(row)
                if keep is None or (r0 + ri, c0 + ci) in keep}
        fs = fs_offset + i
        pr = PlateRead(fs, k, self.fs_labels.get(fs, f"FS{fs + 1}"), blk.elapsed_s, blk.received_s, vals)
        self.reads.append(pr)
        return pr

    @classmethod
    def from_folder(cls, folder: str | Path) -> "RunData":
        folder = Path(folder)
        header = {}
        hp = folder / "run_header.txt"
        if hp.exists():
            for line in hp.read_text(encoding="ascii", errors="replace").splitlines():
                k, _, v = line.partition(" = ")
                header[k.strip()] = v.strip()
        rows, cols = 8, 12
        geo = header.get("Geometry", "")
        for key in ("16x24", "8x12", "6x8", "4x6", "3x4", "2x3"):
            if geo.startswith(key):
                g = geometry(key)
                rows, cols = g.rows, g.cols
                break
        rd = cls(rows, cols, f"{header.get('ResultsID', folder.name)} — {header.get('Started', '')}", folder)
        rd.header = header
        by_key: dict[tuple[int, int], PlateRead] = {}
        with open(folder / "raw_long.csv", newline="", encoding="ascii") as f:
            for row in csv.DictReader(f):
                fs, k = int(row["filter_set"]) - 1, int(row["kinetic_read"]) - 1
                pr = by_key.get((fs, k))
                if pr is None:
                    if "wavelength_nm" in row:                       # Model 680 run (absorbance)
                        label = f"{row['wavelength_nm']} nm" + (" reference" if row["role"] == "reference" else "")
                    else:
                        label = f"{row['ex']} → {row['em']} S{row['sensitivity']}"
                    rec = row.get("received_s") or ""
                    pr = PlateRead(fs, k, label, float(row["t_s"] or 0), float(rec) if rec else None, {})
                    by_key[(fs, k)] = pr
                    rd.reads.append(pr)
                    rd.fs_labels[fs] = label
                v = row["value"]
                pr.values[(int(row["row"]), int(row["col"]))] = (None if v in ("", None) else
                                                                 float(v) if "." in v else int(v))
        return rd

    # ------------------------------------------------------------------ access
    @property
    def n_filter_sets(self) -> int:
        return len(self.fs_labels) or (max((r.filter_set for r in self.reads), default=-1) + 1)

    def n_kinetic(self, fs: int) -> int:
        return sum(1 for r in self.reads if r.filter_set == fs)

    def read(self, fs: int, k: int) -> Optional[PlateRead]:
        for r in self.reads:
            if r.filter_set == fs and r.kinetic == k:
                return r
        return None

    def series(self, fs: int, well: tuple[int, int]) -> tuple[list[float], list[Optional[int]]]:
        """(times, values) of one well over the kinetic reads of a filter set."""
        rs = sorted((r for r in self.reads if r.filter_set == fs), key=lambda r: r.kinetic)
        return [r.t_s for r in rs], [r.values.get(well) for r in rs]


def fs_label(fs) -> str:
    sens = "auto" if fs.sensitivity_mode else str(fs.sensitivity)
    return f"{fs.excitation} → {fs.emission} S{sens}"


def stats(values: list[Optional[int]]) -> dict:
    """n, mean, SD, CV % of the numeric values (overrange counted separately)."""
    v = [x for x in values if x is not None]
    over = sum(1 for x in values if x is None)
    if not v:
        return {"n": 0, "over": over}
    m = sum(v) / len(v)
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) if len(v) > 1 else 0.0
    return {"n": len(v), "over": over, "mean": m, "sd": sd, "cv": (100 * sd / m) if m else float("nan"),
            "min": min(v), "max": max(v)}


def fmt_value(v) -> str:
    """Display text of a well value: OD with 3 decimals (Model 680), counts as integers (FLx800)."""
    if v is None:
        return "*****"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def wells_text(wells) -> str:
    return ", ".join(rc_to_well(r, c) for r, c in sorted(wells))


__all__ = ["PlateRead", "RunData", "stats", "fs_label", "fmt_value", "wells_text", "well_to_rc"]
