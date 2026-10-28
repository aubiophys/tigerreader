import csv

import pytest

from tigerreader import (AssayDefinition, AssayType, Filter, FilterSet, FLx800, Optics, ReadMode, ReadSeries,
                    SensitivityMode, ShakeMode, WellRect)
from tigerreader.export_ascii import write_run
from tigerreader.protocol.framing import FLx800Error, NakError, ReaderError
from tigerreader.transport.sim import SimConfig, SimulatedFLx800

FS = FilterSet(Filter(485, 20), Filter(528, 20), Optics.TOP, 10, 350, 10, SensitivityMode.FIXED, 100)


@pytest.fixture
def rdr(tmp_path):
    r = FLx800.open_sim(trace_path=tmp_path / "trace.log")
    yield r
    r.close()


@pytest.fixture
def tbi(tmp_path):
    r = FLx800.open_sim(sim=SimulatedFLx800(SimConfig.tbi()))
    yield r
    r.close()


def test_identify(rdr):
    i = rdr.info
    assert i.serial == "000000" and i.model == "FLx800T"
    assert i.part_number == "0000000" and i.version.strip() == "0.00"
    assert i.config_word == 0x0544 and not i.has_incubator
    assert i.bottom_probe_mm == 4.5 and i.top_probe_mm == 3.0
    assert i.z_supported and i.features[1017] and not i.features[1024] and not i.features[1009]
    assert "no incubator" in i.summary()


def test_identify_tbi(tbi):
    assert tbi.info.model == "FLx800TBI" and tbi.info.incubation_range == (25, 50, 12, 55)


def test_legacy_basecode_without_z():
    sim = SimulatedFLx800(SimConfig(supports_z=False))
    with FLx800.open_sim(sim=sim) as r:
        assert r.info.z_supported is False
        assert r.info.serial == "000000"


def test_filters_carrier_no_incubator(rdr):
    t = rdr.get_filter_table()
    assert str(t.excitation[1]) == "485/20" and t.excitation[3].is_plug
    rdr.carrier_out()
    rdr.carrier_in()
    with pytest.raises(FLx800Error, match="no incubator"):
        rdr.set_temperature(25)
    with pytest.raises(FLx800Error, match="no incubator"):
        rdr.get_temperature()


def test_temperature_on_incubated_model(tbi):
    rdr = tbi
    assert 15 < rdr.get_temperature() < 30
    rdr.set_temperature(37)
    assert rdr.get_setpoint() == 37
    with pytest.raises(ValueError):
        rdr.set_temperature(60)
    rdr.set_temperature(0)


def test_service_and_forbidden_commands(rdr):
    with pytest.raises(FLx800Error):
        rdr.set_filter_table(rdr.get_filter_table())  # needs service mode
    with pytest.raises(FLx800Error):
        rdr.transact("B", b"00000000")


def test_endpoint_read_and_ascii_export(tbi, tmp_path):
    rdr = tbi
    a = AssayDefinition(filter_sets=(FS,))
    res = rdr.read(a, table=rdr.get_filter_table(), include_temperature=True)
    assert res.ok
    (blocks,) = res.blocks
    assert len(blocks) == 1 and blocks[0].n_rows == 8 and blocks[0].n_cols == 12
    col1 = [row[0] for row in blocks[0].values]
    assert all(x is None or y is None or x > y for x, y in zip(col1, col1[1:]))  # dilution series
    assert blocks[0].temperature_c is not None
    folder = write_run(res, tmp_path / "runs", "endpoint test", rdr.info)
    rows = list(csv.DictReader(open(folder / "raw_long.csv")))
    assert len(rows) == 96 and rows[0]["well"] == "A1"
    assert (folder / "raw_FS1.tsv").exists() and "ReaderSerial = 000000" in (folder / "run_header.txt").read_text()


def test_kinetic_two_filtersets_partial(tbi):
    rdr = tbi
    fs2 = FilterSet(Filter(360, 40), Filter(460, 40), Optics.BOTTOM, sensitivity=80)
    a = AssayDefinition(filter_sets=(FS, fs2), read_mode=ReadMode.KINETIC, kinetic_reads=3, kinetic_interval_s=60,
                        wells=WellRect.parse("A1:D6"))
    res = rdr.read(a)
    reads = list(res.iter_reads())
    assert len(reads) == 6
    assert [(fs_i, k) for fs_i, k, *_ in reads] == [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2)]
    assert reads[-1][4].elapsed_s >= 120
    assert reads[0][4].n_rows == 4 and reads[0][4].n_cols == 6
    assert rdr.min_kinetic_interval() > 0


def test_series_of_five_filtersets(rdr):
    fsets = [FS, FilterSet(Filter(360, 40), Filter(460, 40)), FilterSet(Filter(530, 25), Filter(620, 40)),
             FilterSet(Filter(485, 20), Filter(580, 50)), FS]
    res = rdr.read(ReadSeries.split(AssayDefinition(filter_sets=(FS,)), fsets))
    assert res.ok and sorted({r[0] for r in res.iter_reads()}) == [0, 1, 2, 3, 4]


def test_luminescence_and_onboard_autosense(rdr):
    lum = FilterSet(Filter.plug(), Filter(580, 50), sensitivity=150)
    assert rdr.read(AssayDefinition(filter_sets=(lum,), assay_type=AssayType.LUMINESCENCE)).ok
    auto = FilterSet(Filter(485, 20), Filter(528, 20), sensitivity_mode=SensitivityMode.AUTO_ONBOARD, sensitivity=0)
    res = rdr.read(AssayDefinition(filter_sets=(auto,), onboard_autosense_well=(1, 1)))
    assert res.sensitivities[0] == 87


def test_reader_errors(rdr):
    missing = FilterSet(Filter(590, 20), Filter(645, 40))
    with pytest.raises(ValueError):  # caught by our validator against the reader table
        rdr.read(AssayDefinition(filter_sets=(missing,)), table=rdr.get_filter_table())
    with pytest.raises(ReaderError) as ei:  # without the table, the reader rejects it (0E01)
        rdr.read(AssayDefinition(filter_sets=(missing,)))
    assert ei.value.status.code == "0E01"
    rdr.t.inner.sim.next_error = "0505"
    res = rdr.read(AssayDefinition(filter_sets=(FS,)))
    assert not res.ok and res.statuses[-1].code == "0505"
    assert rdr.get_status().code == "0505"   # latched …
    assert rdr.get_status().code == "0000"   # … and cleared


def test_unexpected_nak(rdr):
    rdr.t.inner.sim.cfg.features.discard(1024)
    with pytest.raises(NakError):
        rdr.transact("E")


def test_wellset_and_selftest(rdr):
    rdr.define(AssayDefinition(filter_sets=(FS,)))
    resp = rdr.read_wells([(1, 1), (8, 12)])
    assert resp.ok and len(resp.blocks[0].values[0]) == 8
    text, st = rdr.self_test()
    assert st.ok and "SYSTEM TEST PASS" in text


def test_trace_written(rdr, tmp_path):
    rdr.get_status()
    log = (tmp_path / "trace.log").read_text()
    assert " TX 6f | o" in log and "<ACK>" in log


def test_bottom_optics_refused_on_top_only_model(rdr):
    fsb = FilterSet(Filter(485, 20), Filter(528, 20), Optics.BOTTOM)
    with pytest.raises(ValueError, match="no bottom probe"):
        rdr.read(AssayDefinition(filter_sets=(fsb,)))
    with pytest.raises(ValueError, match="no shaker"):
        rdr.read(AssayDefinition(filter_sets=(FS,), shake_mode=ShakeMode.BEFORE_READ))


def test_kinetic_interval_checked_against_reader_minimum(rdr):
    """Reader error 1700: 2 filter sets x 96 wells cannot be read every 60 s."""
    fs2 = FilterSet(Filter(360, 40), Filter(460, 40))
    a = AssayDefinition(filter_sets=(FS, fs2), read_mode=ReadMode.KINETIC, kinetic_reads=10, kinetic_interval_s=60)
    with pytest.raises(ValueError, match="minimum"):
        rdr.read(a)
    res = rdr.read(a, fastest=True)
    assert res.ok and res.assays[0].kinetic_interval_s > 60
    assert len(list(res.iter_reads())) == 20


def test_geometries():
    from tigerreader.protocol.assay import PLATE_CHOICES, geometry
    assert len(PLATE_CHOICES) == 8
    assert geometry("96").code == 2 and geometry("8x12 (H)").code == 7 and geometry("8x12M").code == 10
    assert geometry("384").rows == 16 and geometry("2x3").code == 8


def test_kinetic_interval_zero_is_continuous(rdr):
    """Appendix C: interval 0 = read repeatedly with no pause; must not be refused by the '$' check."""
    a = AssayDefinition(filter_sets=(FS,), read_mode=ReadMode.KINETIC, kinetic_reads=3, kinetic_interval_s=0)
    res = rdr.read(a)
    assert res.ok and len(list(res.iter_reads())) == 3
    with pytest.raises(ValueError, match="minimum"):
        rdr.read(a.with_(kinetic_interval_s=60))   # 1 set x 96 wells every 60 s -> 1700 on the reader


def test_paced_simulator_and_abort():
    """Paced mode releases data over time; an abort from another thread stops the read."""
    import threading
    import time as _t
    sim = SimulatedFLx800(SimConfig(pace=0.01))           # 69 s pass -> ~0.7 s
    rdr = FLx800.open_sim(sim=sim)
    a = AssayDefinition(filter_sets=(FS,), read_mode=ReadMode.KINETIC, kinetic_reads=3, kinetic_interval_s=0)
    t0 = _t.monotonic()
    res = rdr.read(a)
    assert res.ok and len(list(res.iter_reads())) == 3 and _t.monotonic() - t0 > 1.5
    a10 = a.with_(kinetic_reads=10)
    threading.Timer(1.0, rdr.abort).start()
    res = rdr.read(a10)
    n = len(list(res.iter_reads()))
    assert res.aborted and 0 < n < 10
    rdr.get_status()                                       # link still usable after the abort
    res = rdr.read(AssayDefinition(filter_sets=(FS,)))
    assert res.ok
