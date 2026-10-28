"""Write read results as plain ASCII files.

Run folder layout::

    runs/<YYYY-MM-DD_HHMMSS>_<ResultsID>/
        run_header.txt      key = value header
        run.json            same metadata, machine readable
        raw_long.csv        one row per well per read
        raw_FS<n>.tsv       matrix per filter set (…_K<kkk> per kinetic read)
        kinetic_FS<n>.csv   kinetic reads only: one row per read (time), one column per well;
                            overrange = empty cell (the status is in raw_long.csv)
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from . import __version__
from .driver.flx800 import InstrumentInfo, ReadResult
from .protocol.assay import ReadMode, rc_to_well


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "run"


def _jsonable(o):
    if isinstance(o, Enum):
        return o.name
    if is_dataclass(o):
        return {k: _jsonable(v) for k, v in asdict(o).items()}
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_jsonable(v) for v in o]
    return o


def write_run(result: ReadResult, out_dir: str | Path, results_id: str = "run",
              info: Optional[InstrumentInfo] = None, description: str = "", operator: str = "",
              plate_id: str = "", keep: Optional[set] = None) -> Path:
    """Write a run folder. *keep* = set of (row, col) to keep when only some wells of the read block
    were asked for (well list); the others are left out of raw_long.csv and blank in the matrices."""
    stamp = result.started.strftime("%Y-%m-%d_%H%M%S")
    folder = Path(out_dir) / f"{stamp}_{_safe(results_id)}"
    folder.mkdir(parents=True, exist_ok=False)

    reads = list(result.iter_reads())
    a0 = result.assays[0]
    kinetic = a0.read_mode == ReadMode.KINETIC

    # ---- header
    hdr: list[tuple[str, str]] = [
        ("Software", f"TigerReader {__version__}"),
        ("ResultsID", results_id),
        ("PlateID", plate_id),
        ("Description", description),
        ("Operator", operator),
        ("Started", result.started.isoformat(timespec="seconds")),
        ("Finished", result.finished.isoformat(timespec="seconds") if result.finished else ""),
        ("Status", "OK" if result.ok else ("ABORTED" if result.aborted else
                                            ";".join(s.code for s in result.statuses if not s.ok))),
    ]
    if info is not None:
        hdr += [("ReaderSerial", info.serial or ""),
                ("Basecode", f"{info.part_number} v{(info.version or '').strip()}"),
                ("ReaderConfig", info.config_text)]
    hdr += [("ReadMode", a0.read_mode.name), ("AssayType", a0.assay_type.name), ("Geometry", a0.geometry.name),
            ("Wells", f"{rc_to_well(a0.read_rect.first_row, a0.read_rect.first_col)}:"
                      f"{rc_to_well(a0.read_rect.last_row, a0.read_rect.last_col)}")]
    if keep is not None:
        hdr[-1] = ("Wells", ", ".join(rc_to_well(r, c) for r, c in sorted(keep)))
        hdr.append(("ReadArea", f"{rc_to_well(a0.read_rect.first_row, a0.read_rect.first_col)}:"
                                f"{rc_to_well(a0.read_rect.last_row, a0.read_rect.last_col)}"))
    if kinetic:
        hdr += [("KineticReads", str(a0.kinetic_reads)), ("KineticInterval_s", str(a0.kinetic_interval_s))]
    fs_all = [fs for a in result.assays for fs in a.filter_sets]
    for n, fs in enumerate(fs_all, 1):
        hdr.append((f"FilterSet{n}",
                    f"Ex={fs.excitation} Em={fs.emission} Optics={fs.optics.name} "
                    f"Sensitivity={result.sensitivities.get(n - 1, fs.sensitivity)}"
                    f"{' (auto)' if fs.sensitivity_mode else ''} Samples={fs.samples_per_well} "
                    f"DelayBefore_ms={fs.delay_before_ms} DelayBetween_ms={fs.delay_between_ms}"))
    (folder / "run_header.txt").write_text("".join(f"{k} = {v}\n" for k, v in hdr), encoding="ascii",
                                          errors="replace")

    meta = {"header": dict(hdr), "assays": _jsonable(result.assays), "info": _jsonable(info),
            "statuses": [s.code for s in result.statuses], "sensitivities": result.sensitivities}
    (folder / "run.json").write_text(json.dumps(meta, indent=2), encoding="ascii")

    # ---- long CSV
    with open(folder / "raw_long.csv", "w", newline="", encoding="ascii") as f:
        w = csv.writer(f)
        w.writerow(["results_id", "filter_set", "ex", "em", "optics", "sensitivity", "kinetic_read", "t_s",
                    "row", "col", "well", "value", "status", "temperature_c", "received_s"])
        for fs_i, k, a, fs, blk in reads:
            r0, c0 = a.read_rect.first_row, a.read_rect.first_col
            for ri, row in enumerate(blk.values):
                for ci, v in enumerate(row):
                    r, c = r0 + ri, c0 + ci
                    if keep is not None and (r, c) not in keep:
                        continue
                    w.writerow([results_id, fs_i + 1, fs.excitation, fs.emission, fs.optics.name,
                                result.sensitivities.get(fs_i, fs.sensitivity), k + 1, f"{blk.elapsed_s:.1f}",
                                r, c, rc_to_well(r, c), "" if v is None else v, "OVER" if v is None else "OK",
                                "" if blk.temperature_c is None else blk.temperature_c,
                                "" if blk.received_s is None else f"{blk.received_s:.1f}"])

    # ---- kinetic tables: one CSV per filter set, rows = kinetic reads, columns = wells
    if kinetic:
        by_fs: dict[int, list] = {}
        for fs_i, k, a, fs, blk in reads:
            by_fs.setdefault(fs_i, []).append((k, a, blk))
        for fs_i, items in by_fs.items():
            rr = items[0][1].read_rect
            wells = [(r, c) for r in range(rr.first_row, rr.last_row + 1) for c in range(rr.first_col, rr.last_col + 1)
                     if keep is None or (r, c) in keep]
            with open(folder / f"kinetic_FS{fs_i + 1}.csv", "w", newline="", encoding="ascii") as f:
                w = csv.writer(f)
                w.writerow(["kinetic_read", "t_s", "received_s"] + [rc_to_well(r, c) for r, c in wells])
                for k, a, blk in sorted(items, key=lambda x: x[0]):
                    vals = {(rr.first_row + ri, rr.first_col + ci): v
                            for ri, row in enumerate(blk.values) for ci, v in enumerate(row)}
                    rec = "" if blk.received_s is None else f"{blk.received_s:.1f}"
                    w.writerow([k + 1, f"{blk.elapsed_s:.1f}", rec]
                               + ["" if vals.get(x) is None else vals[x] for x in wells])

    # ---- matrices ('******' for overrange)
    for fs_i, k, a, fs, blk in reads:
        name = f"raw_FS{fs_i + 1}" + (f"_K{k + 1:03d}" if kinetic else "") + ".tsv"
        r0, c0 = a.read_rect.first_row, a.read_rect.first_col
        lines = [f"# FilterSet {fs_i + 1}  Ex {fs.excitation}  Em {fs.emission}  {fs.optics.name}  "
                 f"t={blk.elapsed_s:.1f}s" + (f"  received={blk.received_s:.1f}s" if blk.received_s is not None else "")
                 + (f"  T={blk.temperature_c}C" if blk.temperature_c else ""),
                 "\t" + "\t".join(str(c0 + i) for i in range(blk.n_cols))]
        for ri, row in enumerate(blk.values):
            lines.append(chr(ord("A") + r0 + ri - 1) + "\t" +
                         "\t".join("" if keep is not None and (r0 + ri, c0 + ci) not in keep
                                   else "******" if v is None else str(v) for ci, v in enumerate(row)))
        (folder / name).write_text("\n".join(lines) + "\n", encoding="ascii")
    return folder
