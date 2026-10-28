"""Run report (HTML content, PDF output)."""
import os

import pytest

from tigerreader import FLx800
from tigerreader.export_ascii import write_run
from tigerreader.readsetup import FilterSetSetup, ReadSetup
from tigerreader.report import report_html
from tigerreader.rundata import RunData


def _run(tmp_path, setup):
    rdr = FLx800.open_sim()
    res = rdr.read(setup.to_assay(), fastest=setup.fastest)
    return RunData.from_folder(write_run(res, tmp_path, setup.name, rdr.info, keep=setup.selected_wells()))


def test_report_html_has_header_and_every_read(tmp_path):
    s = ReadSetup(name="rep", kinetic=True, kinetic_reads=2, kinetic_interval=0,
                  filter_sets=[FilterSetSetup(), FilterSetSetup("360/40", "460/40")])
    rd = _run(tmp_path, s)
    h = report_html(rd)
    assert "<h2>rep</h2>" in h and "ReaderSerial" in h and "000000" in h
    assert h.count("<h4>") == 4                                     # 2 filter sets x 2 kinetic reads
    assert "read 2 of 2" in h and "360/40 → 460/40" in h
    v = rd.read(0, 0).values[(1, 1)]
    assert f"<td align='right'>{v}</td>" in h


def test_report_keeps_only_listed_wells(tmp_path):
    rd = _run(tmp_path, ReadSetup(name="list", wells="A1, B2"))
    h = report_html(rd)
    assert h.count("<td align='right'>") == 2 and "Wells</b></td><td>A1, B2" in h


def test_report_pdf(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from tigerreader.report import write_pdf
    rd = _run(tmp_path, ReadSetup(name="pdf", plate="16x24"))
    out = write_pdf(rd, tmp_path / "r.pdf")
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 5000


def test_kinetic_tables(tmp_path):
    """One CSV per filter set; rows = kinetic reads, columns = wells (listed wells only)."""
    import csv
    s = ReadSetup(name="kt", kinetic=True, kinetic_reads=3, kinetic_interval=0, wells="A1, B2, C4",
                  filter_sets=[FilterSetSetup(), FilterSetSetup("360/40", "460/40")])
    rd = _run(tmp_path, s)
    rows = list(csv.reader(open(rd.folder / "kinetic_FS2.csv")))
    assert rows[0] == ["kinetic_read", "t_s", "received_s", "A1", "B2", "C4"]
    assert [r[0] for r in rows[1:]] == ["1", "2", "3"]
    assert int(rows[3][3]) == rd.read(1, 2).values[(1, 1)]
    assert (rd.folder / "kinetic_FS1.csv").exists()
    endpoint = _run(tmp_path / "ep", ReadSetup(name="ep"))
    assert not list(endpoint.folder.glob("kinetic_*.csv"))

