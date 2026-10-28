"""GUI tests on the (paced) simulator, headless (QT_QPA_PLATFORM=offscreen)."""
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from tigerreader.gui.main_window import MainWindow, configure_app  # noqa: E402
from tigerreader.gui.worker import SIM_FAST                 # noqa: E402
from tigerreader.readsetup import FilterSetSetup, ReadSetup  # noqa: E402
from tigerreader.rundata import RunData                     # noqa: E402


@pytest.fixture(autouse=True)
def _app_like_main(qapp):
    """Configure Qt as main() does (Fusion style), so the tests see the same look and sizes as the program.
    (On Windows the off-screen platform also needs the system fonts: see conftest.py.)"""
    configure_app(qapp)


@pytest.fixture
def win(qtbot, tmp_path, monkeypatch):
    from tigerreader.gui import worker as wmod
    monkeypatch.setitem(wmod.SIM_PORTS, SIM_FAST, 0.01)      # 69 s pass -> 0.7 s
    w = MainWindow(data_dir=tmp_path, ask=False)
    qtbot.addWidget(w)
    yield w
    w.close()


def _connect(qtbot, w):
    with qtbot.waitSignal(w.worker.connected, timeout=5000):
        w.connect_reader(SIM_FAST)
    qtbot.waitUntil(lambda: w.info is not None and not w.busy, timeout=5000)
    assert w.info.model == "FLx800T"


def _read(qtbot, w, setup, timeout=20000):
    w.setup_panel.set_setup(setup)
    with qtbot.waitSignal(w.worker.read_done, timeout=timeout) as sig:
        w.start_read()
    qtbot.waitUntil(lambda: not w.reading and not w.busy, timeout=5000)
    return sig.args


def test_connect_and_endpoint_read(qtbot, win):
    _connect(qtbot, win)
    assert "FLx800T" in win.info_text.toPlainText() and "Plug" in win.info_text.toPlainText()
    result, folder = _read(qtbot, win, ReadSetup(name="gui endpoint"))
    assert result.ok
    f = Path(folder)
    for name in ("run_header.txt", "raw_long.csv", "raw_FS1.tsv", "protocol.json", "trace.log"):
        assert (f / name).exists(), name
    assert "TX 53 | S" in (f / "trace.log").read_text()
    assert win.plate_view.model_.values[(1, 1)] > 0
    assert win.run_list.count() == 1
    rd = RunData.from_folder(f)
    assert len(rd.reads) == 1 and len(rd.reads[0].values) == 96


def test_kinetic_two_sets_live_and_reopen(qtbot, win):
    _connect(qtbot, win)
    s = ReadSetup(name="kin", kinetic=True, kinetic_reads=3, kinetic_interval=0,
                  filter_sets=[FilterSetSetup(), FilterSetSetup("360/40", "460/40")])
    blocks = []
    win.worker.block.connect(lambda *a: blocks.append(a))
    result, folder = _read(qtbot, win, s)
    assert result.ok and len(blocks) == 6 and len(win.run.reads) == 6
    assert win.fs_combo.count() == 2 and win.k_spin.maximum() == 3
    win.plate_view.selectRow(0)
    assert "mean" in win.stats_label.text()
    win.open_run(folder)
    assert win.run.n_filter_sets == 2 and win.run.n_kinetic(1) == 3


def test_abort_keeps_partial_data(qtbot, win, monkeypatch):
    from tigerreader.gui import worker as wmod
    monkeypatch.setitem(wmod.SIM_PORTS, SIM_FAST, 0.03)
    _connect(qtbot, win)
    win.setup_panel.set_setup(ReadSetup(name="long", kinetic=True, kinetic_reads=20, kinetic_interval=0))
    with qtbot.waitSignal(win.worker.read_done, timeout=30000) as sig:
        win.start_read()
        qtbot.waitUntil(lambda: win.run is not None and len(win.run.reads) >= 2, timeout=20000)
        assert win.act_abort.isEnabled()
        win.abort_read()
    result, folder = sig.args
    assert result.aborted and 2 <= len(list(result.iter_reads())) < 20
    assert "ABORTED" in (Path(folder) / "run_header.txt").read_text()
    qtbot.waitUntil(lambda: not win.busy, timeout=5000)
    assert win.act_read.isEnabled() and not win.act_abort.isEnabled()


def test_invalid_setup_is_refused(qtbot, win):
    _connect(qtbot, win)
    win.setup_panel.set_setup(ReadSetup(filter_sets=[FilterSetSetup("485/20", "530/20", optics="bottom")]))
    assert "Cannot read" in win.setup_panel.check.text()
    assert "no bottom probe" in win.setup_panel.check.text()
    win.start_read()
    assert not win.reading


def test_protocols_save_load_delete(qtbot, win):
    win.setup_panel.set_setup(ReadSetup(name="Example", wells="A1:D6", filter_sets=[FilterSetSetup(sensitivity=80)]))
    win.save_protocol()
    win.new_protocol()
    assert win.setup_panel.name.text() != "Example"
    win.protocol_list.setCurrentRow(0)
    s = win.setup_panel.setup()
    assert s.name == "Example" and s.wells == "A1:D6" and s.filter_sets[0].sensitivity == 80
    win.delete_protocol()
    assert win.protocol_list.count() == 0


def test_setup_panel_roundtrip(qtbot, win):
    s = ReadSetup(name="rt", luminescence=True, plate="16x24", wells="A1:B4", kinetic=True, kinetic_reads=7,
                  kinetic_interval=300, filter_sets=[FilterSetSetup("Plug", "580/50", sensitivity="auto",
                                                                    samples=20, delay_before_ms=500,
                                                                    delay_between_ms=5)])
    win.setup_panel.set_setup(s)
    assert win.setup_panel.setup() == s


def test_auto_sensitivity_well_editable(qtbot, win):
    """The auto-sensitivity well can be changed whether or not a filter set is set to Auto."""
    p = win.setup_panel
    assert p.auto_well.isEnabled()
    qtbot.keyClicks(p.auto_well, "\b\bB2")
    p.fs_table.cellWidget(0, 3).auto.setChecked(True)                                # -> "Auto" (tick box)
    s = p.setup()
    assert s.auto_well == "B2" and s.filter_sets[0].sensitivity == "auto"
    assert s.to_assay().onboard_autosense_well == (2, 2)


def test_well_list_read(qtbot, win):
    """F1 in the GUI: only the listed wells are shown and saved."""
    _connect(qtbot, win)
    result, folder = _read(qtbot, win, ReadSetup(name="list", wells="A1, B2, C4"))
    assert result.ok
    assert set(win.plate_view.model_.values) == {(1, 1), (2, 2), (3, 4)}
    assert "Wells = A1, B2, C4" in (Path(folder) / "run_header.txt").read_text()


def test_auto_tick_box_and_target(qtbot, win):
    """Auto is a tick box (the gain box is disabled while ticked) and the auto target is settable."""
    p = win.setup_panel
    cell = p.fs_table.cellWidget(0, 3)
    cell.auto.setChecked(True)
    assert not cell.spin.isEnabled()
    p.auto_target.setValue(50000)
    a = p.setup().to_assay()
    assert a.filter_sets[0].sensitivity_mode == 1 and a.onboard_autosense_max == 50000
    cell.auto.setChecked(False)
    cell.spin.setValue(80)
    assert p.setup().filter_sets[0].sensitivity == 80


@pytest.mark.parametrize("size", [(1680, 1010)])
def test_window_fits_screen(qtbot, tmp_path, size):
    """The window must fit a 1680x1050 screen at 100 % scaling (taskbar ~40 px)."""
    w = MainWindow(data_dir=tmp_path, ask=False)
    qtbot.addWidget(w)
    w.show()
    hint = w.minimumSizeHint()
    assert hint.width() <= size[0] and hint.height() <= size[1], hint
    w.resize(*size)
    assert w.width() <= size[0] and w.height() <= size[1]
    w.close()


def test_report_after_read(qtbot, win):
    """File > Save run report as PDF, default report.pdf in the run folder."""
    _connect(qtbot, win)
    result, folder = _read(qtbot, win, ReadSetup(name="report"))
    out = win.save_report()
    assert out == Path(folder) / "report.pdf" and out.read_bytes().startswith(b"%PDF")


def test_auto_report_after_read(qtbot, win):
    """With *Save report after each read* ticked, report.pdf is written when the read finishes."""
    _connect(qtbot, win)
    win.act_auto_report.setChecked(True)
    result, folder = _read(qtbot, win, ReadSetup(name="auto"))
    assert (Path(folder) / "report.pdf").read_bytes().startswith(b"%PDF")
    assert win.store.get("auto_report") == "1"
    win.act_auto_report.setChecked(False)
    result, folder = _read(qtbot, win, ReadSetup(name="manual"))
    assert not (Path(folder) / "report.pdf").exists()

