"""The Bio-Rad Model 680 in the main window (simulator, headless)."""
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from tigerreader.gui.main_window import MainWindow, configure_app  # noqa: E402
from tigerreader.gui.worker import SIM_FAST                 # noqa: E402
from tigerreader.m680.setup import AbsSetup                 # noqa: E402
from tigerreader.readsetup import ReadSetup                 # noqa: E402
from tigerreader.rundata import RunData                     # noqa: E402


@pytest.fixture(autouse=True)
def _app_like_main(qapp):
    configure_app(qapp)


@pytest.fixture
def win(qtbot, tmp_path, monkeypatch):
    from tigerreader.gui import worker as wmod
    monkeypatch.setitem(wmod.SIM_PORTS, SIM_FAST, 0.01)      # 15 s read -> 0.15 s
    w = MainWindow(data_dir=tmp_path, ask=False, instrument="m680")
    qtbot.addWidget(w)
    yield w
    w.close()


def _connect(qtbot, w):
    with qtbot.waitSignal(w.worker.connected, timeout=5000):
        w.connect_reader(SIM_FAST)
    qtbot.waitUntil(lambda: w.info is not None and not w.busy, timeout=5000)


def _read(qtbot, w, setup, timeout=20000):
    w.setup_panel.set_setup(setup)
    with qtbot.waitSignal(w.worker.read_done, timeout=timeout) as sig:
        w.start_read()
    qtbot.waitUntil(lambda: not w.reading and not w.busy, timeout=5000)
    return sig.args


def test_window_is_for_the_680(win):
    assert "Bio-Rad Model 680" in win.windowTitle()
    menu = [a for m in win.menuBar().actions() if m.text() == "&Instrument" for a in m.menu().actions()]
    assert win.act_read in menu and win.act_out not in menu and win.act_selftest not in menu
    assert not any(win.port.itemData(i) == "auto" for i in range(win.port.count()))


def test_connect_and_dual_endpoint_read(qtbot, win):
    _connect(qtbot, win)
    assert "Model 680" in win.info_text.toPlainText() and "655 nm" in win.info_text.toPlainText()
    assert "off" in win.inc_label.text()
    result, folder = _read(qtbot, win, AbsSetup(name="abs", meas_nm=450, ref_nm=655))
    assert result.ok
    f = Path(folder)
    for name in ("run_header.txt", "raw_long.csv", "raw_FS1.tsv", "raw_FS2.tsv", "reader_raw.txt",
                 "protocol.json", "trace.log"):
        assert (f / name).exists(), name
    assert "RPLATE" in (f / "trace.log").read_text()
    assert win.fs_combo.count() == 2 and "655 nm reference" in win.fs_combo.itemText(1)
    v = win.plate_view.model_.values[(1, 1)]
    assert isinstance(v, float)
    m = win.plate_view.model_
    assert m.data(m.index(0, 0)) == f"{v:.3f}" and m.data(m.index(7, 11)) == "*****"   # H12 = beyond 3.5 OD
    win.plate_view.selectRow(1)
    assert "mean 0." in win.stats_label.text()
    rd = RunData.from_folder(f)
    assert rd.n_filter_sets == 2 and len(rd.read(1, 0).values) == 96 and isinstance(rd.read(0, 0).values[(2, 2)], float)
    out = win.save_report()
    assert out.read_bytes().startswith(b"%PDF")
    assert win.run_list.count() == 1


def test_kinetic_well_list_and_abort(qtbot, win, monkeypatch):
    from tigerreader.gui import worker as wmod
    monkeypatch.setitem(wmod.SIM_PORTS, SIM_FAST, 0.03)      # fast read 6 s -> 0.18 s
    _connect(qtbot, win)
    win.setup_panel.set_setup(AbsSetup(name="kin", meas_nm=405, speed="fast", kinetic=True, kinetic_reads=20,
                                       kinetic_interval_s=0, wells="A1, B2, C4"))
    with qtbot.waitSignal(win.worker.read_done, timeout=30000) as sig:
        win.start_read()
        qtbot.waitUntil(lambda: win.run is not None and len(win.run.reads) >= 2, timeout=20000)
        assert win.act_abort.isEnabled()
        win.abort_read()
    run, folder = sig.args
    assert run.aborted and 2 <= len(run.reads) < 20
    hdr = (Path(folder) / "run_header.txt").read_text()
    assert "ABORTED" in hdr and "Wells = A1, B2, C4" in hdr
    assert set(win.plate_view.model_.values) == {(1, 1), (2, 2), (3, 4)}
    assert win.k_spin.maximum() == len(run.reads)
    qtbot.waitUntil(lambda: not win.busy, timeout=5000)
    assert win.act_read.isEnabled() and not win.act_abort.isEnabled()


def test_wavelength_not_installed_is_refused(qtbot, win):
    _connect(qtbot, win)
    win.setup_panel.set_setup(AbsSetup(meas_nm=560))
    assert "Cannot read" in win.setup_panel.check.text() and "560" in win.setup_panel.check.text()
    win.start_read()
    assert not win.reading


def test_setup_panel_roundtrip(win):
    s = AbsSetup(name="rt", description="d", meas_nm=490, ref_nm=630, speed="fast", mix_time_s=12, mix_speed="M",
                 kinetic=True, kinetic_reads=7, kinetic_interval_s=45, wells="A1:B4")
    win.setup_panel.set_setup(s)
    assert win.setup_panel.setup() == s


def test_protocols_are_kept_per_instrument(qtbot, win, tmp_path):
    win.setup_panel.set_setup(AbsSetup(name="Abs1", meas_nm=405))
    win.save_protocol()
    assert win.protocol_list.count() == 1
    flx = MainWindow(data_dir=tmp_path, ask=False)                        # same parameter database
    qtbot.addWidget(flx)
    assert flx.protocol_list.count() == 0
    flx.setup_panel.set_setup(ReadSetup(name="Abs1"))
    flx.save_protocol()                                                    # refused: the name is the 680's
    assert flx.protocol_list.count() == 0 and flx.store.load("Abs1").meas_nm == 405
    flx.close()


def test_incubator(qtbot, win):
    _connect(qtbot, win)
    win.inc_set.setValue(30.5)
    with qtbot.waitSignal(win.worker.incubator, timeout=5000):
        win.inc_on.click()
    qtbot.waitUntil(lambda: "on" in win.inc_label.text(), timeout=5000)
    assert "30.5" in win.inc_label.text()


def test_switch_instrument(qtbot, tmp_path):
    w = MainWindow(data_dir=tmp_path, ask=False)
    qtbot.addWidget(w)
    new = w.switch_instrument()
    qtbot.addWidget(new)
    assert new.m680 and "Model 680" in new.windowTitle() and not w.isVisible()
    new.close()


def test_switch_instrument_always_asks(qtbot, tmp_path):
    """Switching asks first, also when not connected; answering No keeps the window."""
    w = MainWindow(data_dir=tmp_path, ask=False)
    qtbot.addWidget(w)
    w.show()
    asked = []
    w._question = lambda title, text: asked.append(text) or False
    assert w.info is None and w.switch_instrument() is None
    assert asked == ["Switch to the Bio-Rad Model 680?"] and w.isVisible() and not w.m680
    w.close()


def test_window_fits_screen_m680(qtbot, tmp_path):
    """The 680 window also fits a 1680x1050 screen (taskbar ~40 px)."""
    w = MainWindow(data_dir=tmp_path, ask=False, instrument="m680")
    qtbot.addWidget(w)
    w.show()
    hint = w.minimumSizeHint()
    assert hint.width() <= 1680 and hint.height() <= 1010, hint
    w.close()


def test_reset_after_reader_error(qtbot, win):
    """A reader error (here 8093, Stop key) ends the read; Instrument > Reset reader clears it (RS) and reconnects."""
    _connect(qtbot, win)
    sim = win.worker.rdr.t.inner.sim
    sim.next_error = "8093"
    run, folder = _read(qtbot, win, AbsSetup(name="err"))
    assert not run.ok and "8093" in run.error and "reset" in run.error
    assert win.act_reset.isEnabled()
    with qtbot.waitSignal(win.worker.connected, timeout=5000):
        win.act_reset.trigger()
    qtbot.waitUntil(lambda: not win.busy, timeout=5000)
    assert "EIA.READER RS" in sim.log
    run, folder = _read(qtbot, win, AbsSetup(name="after reset"))
    assert run.ok
