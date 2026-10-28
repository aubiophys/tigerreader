"""Write a Model 680 run as plain ASCII files, in the same folder layout as FLx800 runs.
Absorbance in OD with 3 decimals; ``******`` / empty cell / ``OVER`` = beyond ±3.5 OD.

Filter set 1 = measurement wavelength, filter set 2 = reference wavelength (dual-wavelength reads).
The reference is stored raw; subtracting it is left to the analysis.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import List, Optional

from .. import __version__
from ..export_ascii import _safe
from ..protocol.assay import rc_to_well
from .driver import M680Info, M680Run
from .protocol import COLS, ROWS


def _sets(run: M680Run) -> List[tuple]:
    out = [(1, run.meas_nm, "measurement")]
    if run.ref_nm is not None:
        out.append((2, run.ref_nm, "reference"))
    return out


def _block(pr, fs: int):
    return pr.data.meas if fs == 1 else pr.data.ref


def _v(v: Optional[float]) -> str:
    return "" if v is None else f"{v:.3f}"


def write_run(run: M680Run, out_dir, results_id: str = "run", info: Optional[M680Info] = None,
              description: str = "", operator: str = "", plate_id: str = "", keep: Optional[set] = None) -> Path:
    folder = Path(out_dir) / f"{run.started.strftime('%Y-%m-%d_%H%M%S')}_{_safe(results_id)}"
    folder.mkdir(parents=True, exist_ok=False)
    wells = [(r, c) for r in range(1, ROWS + 1) for c in range(1, COLS + 1) if keep is None or (r, c) in keep]
    sets = _sets(run)

    hdr = [("Software", f"TigerReader {__version__}"), ("Instrument", "Bio-Rad Model 680"),
           ("ResultsID", results_id), ("PlateID", plate_id), ("Description", description), ("Operator", operator),
           ("Started", run.started.isoformat(timespec="seconds")),
           ("Finished", run.finished.isoformat(timespec="seconds") if run.finished else ""),
           ("Status", "OK" if run.ok else "ABORTED" if run.aborted else (run.error or "INCOMPLETE"))]
    if info is not None:
        hdr += [("ReaderSerial", info.serial), ("ReaderID", info.ident)]
    hdr += [("ReadMode", "KINETIC" if run.kinetic else "ENDPOINT"), ("AssayType", "ABSORBANCE"),
            ("Geometry", "8x12 (96 wells)"),
            ("Wells", "A1:H12" if keep is None else ", ".join(rc_to_well(r, c) for r, c in sorted(keep)))]
    if keep is not None:
        hdr.append(("ReadArea", "A1:H12"))
    hdr += [("Speed", run.speed), ("Mixing", f"{run.mix_time_s} s {run.mix_speed}" if run.mix_time_s else "none")]
    if run.kinetic:
        hdr += [("KineticReads", str(run.n_reads)), ("KineticInterval_s", f"{run.interval_s:g}"),
                ("KineticTiming", "host-timed (start to start)")]
    for fs, nm, role in sets:
        hdr.append((f"FilterSet{fs}", f"Wavelength={nm} Role={role}"))
    bad = [f"read {pr.index + 1}" for pr in run.reads if not pr.data.checksum_ok]
    if bad:
        hdr.append(("ChecksumWarning", ", ".join(bad)))
    (folder / "run_header.txt").write_text("".join(f"{k} = {v}\n" for k, v in hdr), encoding="ascii",
                                          errors="replace")
    meta = {"header": dict(hdr), "reader_times": [pr.data.reader_time for pr in run.reads],
            "checksums": [[{"sent": b.checksum, "check": b.checksum_rule} for b in (pr.data.meas, pr.data.ref) if b]
                          for pr in run.reads]}
    (folder / "run.json").write_text(json.dumps(meta, indent=2), encoding="ascii")
    # the reader's answers exactly as received (one line per received line), for checking and troubleshooting
    (folder / "reader_raw.txt").write_text(
        "".join(f"# read {pr.index + 1}\n" + "".join(f"{ln}\n" for ln in pr.data.raw) for pr in run.reads),
        encoding="ascii", errors="replace")

    with open(folder / "raw_long.csv", "w", newline="", encoding="ascii") as f:
        w = csv.writer(f)
        w.writerow(["results_id", "filter_set", "wavelength_nm", "role", "kinetic_read", "t_s", "row", "col",
                    "well", "value", "status", "received_s"])
        for pr in run.reads:
            for fs, nm, role in sets:
                vals = _block(pr, fs).values
                for r, c in wells:
                    v = vals[r - 1][c - 1]
                    w.writerow([results_id, fs, nm, role, pr.index + 1, f"{pr.t_s:.1f}", r, c, rc_to_well(r, c),
                                _v(v), "OVER" if v is None else "OK", f"{pr.received_s:.1f}"])

    if run.kinetic:
        for fs, nm, role in sets:
            with open(folder / f"kinetic_FS{fs}.csv", "w", newline="", encoding="ascii") as f:
                w = csv.writer(f)
                w.writerow(["kinetic_read", "t_s", "received_s"] + [rc_to_well(r, c) for r, c in wells])
                for pr in run.reads:
                    vals = _block(pr, fs).values
                    w.writerow([pr.index + 1, f"{pr.t_s:.1f}", f"{pr.received_s:.1f}"]
                               + [_v(vals[r - 1][c - 1]) for r, c in wells])

    for pr in run.reads:
        for fs, nm, role in sets:
            vals = _block(pr, fs).values
            lines = [f"# FilterSet {fs}  {nm} nm  {role}  t={pr.t_s:.1f}s  received={pr.received_s:.1f}s",
                     "\t" + "\t".join(str(c) for c in range(1, COLS + 1))]
            for r in range(1, ROWS + 1):
                lines.append(chr(64 + r) + "\t" + "\t".join(
                    "" if keep is not None and (r, c) not in keep else
                    "******" if vals[r - 1][c - 1] is None else f"{vals[r - 1][c - 1]:.3f}"
                    for c in range(1, COLS + 1)))
            name = f"raw_FS{fs}" + (f"_K{pr.index + 1:03d}" if run.kinetic else "") + ".tsv"
            (folder / name).write_text("\n".join(lines) + "\n", encoding="ascii")
    return folder
