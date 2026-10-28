"""Bio-Rad Model 680: protocol parsing, driver on the simulator, export and command line."""
import csv
import threading

import pytest

from tigerreader.m680 import M680Error, Model680
from tigerreader.m680.cli import main
from tigerreader.m680.export import write_run
from tigerreader.m680.protocol import (M680ProtocolError, block_checksum, command, parse_plate, parse_response_line,
                                  parse_row)
from tigerreader.m680.sim import Sim680Config, Sim680Transport, Simulated680


# ---------------------------------------------------------------- protocol
def test_command_encoding_and_forbidden():
    assert command("rplate", 2, 0, "L", 3, 7) == b"EIA.READER RPLATE 2 0 L 3 7\r"
    for bad in ("SSN", "RM", "DOWNLD", "DRPD", "DTSE"):
        with pytest.raises(ValueError, match="never sent"):
            command(bad)
    with pytest.raises(ValueError, match="not a command"):
        command("RWELL", 1, 1, 3)


def test_response_line():
    assert parse_response_line("ERE 0000 Model 680") == ("0000", "Model 680")
    assert parse_response_line("ERE 8073") == ("8073", "")
    with pytest.raises(M680ProtocolError):
        parse_response_line("EIA.READER ID")


def test_row_signs_and_overrange():
    row = " 0.101 0.102-0.003 0.104 0.105 0.106 0.107 0.108 0.109 0.110 1.234 *.***"
    v = parse_row(row)
    assert v[:3] == [0.101, 0.102, -0.003] and v[10] == 1.234 and v[11] is None
    with pytest.raises(M680ProtocolError):
        parse_row(" 0.101 0.102")


def test_plate_parse_and_checksum():
    """Checksum = 4 hex digits, 16-bit sum of the 8 rows incl. their CRs; filter lines carry wheel positions.
    A corrupted checksum is reported, not hidden."""
    rows = [" " + " ".join(f"{r * 0.1 + c * 0.001:.3f}" for c in range(1, 13)) for r in range(8)]
    cks = (sum("".join(rows).encode()) + 13 * 8) & 0xFFFF
    lines = ["BIO-RAD Model 680 Microplate READER", "01/02/2030 10:00:00", "Mes. filter:2", ".begin"] + rows + \
            [f"{cks:04X}", ".end"]
    pd = parse_plate(lines)
    assert pd.meas_filter == 2 and pd.reader_time == "01/02/2030 10:00:00" and pd.ref is None
    assert pd.meas.checksum == f"{cks:04X}" and pd.checksum_ok and block_checksum(rows) == cks
    assert pd.meas.values[0][0] == 0.001 and pd.meas.values[7][11] == 0.712
    bad = list(lines)
    bad[-2] = f"{(cks + 1) & 0xFFFF:04X}"
    assert not parse_plate(bad).checksum_ok and parse_plate(bad).meas.checksum_rule == "mismatch"


# ---------------------------------------------------------------- driver on the simulator
def test_connect_info_and_close_releases_remote():
    sim = Simulated680()
    rdr = Model680.open_sim(sim=sim)
    assert rdr.info.serial == "00000" and rdr.info.has_incubator
    assert rdr.info.filters[3] == 490 and rdr.info.filters[8] is None
    assert rdr.info.position(655) == 7 and sim.remote
    rdr.close()
    assert not sim.remote and sim.log[-1].upper().endswith("RL")


def test_first_aq_refused_is_retried_once():
    sim = Simulated680(Sim680Config(aq_fails_first=True))
    with Model680.open_sim(sim=sim) as rdr:
        assert rdr.remote and rdr.info.serial == "00000"
    assert [x for x in sim.log if x.endswith("AQ")] == ["EIA.READER AQ"] * 2


def test_needs_remote_mode():
    rdr = Model680(Sim680Transport())
    rdr.t.open()
    with pytest.raises(M680Error, match="8073"):
        rdr._transact("ID")


def test_endpoint_single_and_dual(tmp_path):
    with Model680.open_sim() as rdr:
        run = rdr.read(450)
        assert run.ok and len(run.reads) == 1
        d = run.reads[0].data
        assert d.meas_nm == 450 and d.ref is None and d.checksum_ok
        assert d.meas.values[7][11] is None and d.meas.values[0][11] < 0       # *.*** and a negative value
        run2 = rdr.read(450, ref_nm=655, speed="fast", mix_time_s=5, mix_speed="M")
        d2 = run2.reads[0].data
        assert d2.ref_nm == 655 and d2.ref is not None and d2.ref.values[7][0] < d2.meas.values[7][0]
        assert rdr.t.sim.log[-1] == "EIA.READER RPLATE 1 5 M 2 7"
        with pytest.raises(ValueError, match="no 560 nm filter"):
            rdr.read(560)
        with pytest.raises(ValueError, match="differ"):
            rdr.read(450, ref_nm=450)


def test_reader_error_is_reported():
    sim = Simulated680()
    with Model680.open_sim(sim=sim) as rdr:
        sim.next_error = "8077"
        run = rdr.read(450)
        assert not run.ok and "8077" in run.error and "reset" in run.error
        rdr.reset()
        assert rdr.remote and rdr.read(450).ok


def test_kinetic_timing_and_abort():
    sim = Simulated680(Sim680Config(pace=0.01))          # 6 s fast read -> 0.06 s
    with Model680.open_sim(sim=sim) as rdr:
        run = rdr.read(405, speed="fast", n_reads=3, interval_s=0.2)
        assert run.ok and [p.index for p in run.reads] == [0, 1, 2]
        starts = [p.t_s for p in run.reads]
        assert starts[1] >= 0.19 and starts[2] >= 0.39          # start to start
        got = []

        def on_plate(pr):
            got.append(pr.index)
            if pr.index == 1:
                threading.Timer(0.01, rdr.abort).start()
        run = rdr.read(405, speed="fast", n_reads=10, interval_s=0.3, on_plate=on_plate)
        assert run.aborted and not run.ok and len(run.reads) == 2


def test_incubator():
    sim = Simulated680()
    with Model680.open_sim(sim=sim) as rdr:
        assert rdr.incubator()["on"] is False
        rdr.set_incubator(30.5)
        s = rdr.incubator()
        assert s["on"] and s["setpoint_c"] == 30.5
        rdr.set_incubator(None)
        assert not rdr.incubator()["on"]
        with pytest.raises(ValueError):
            rdr.set_incubator(50)


# ---------------------------------------------------------------- export and command line
def test_export_kinetic_with_well_list(tmp_path):
    with Model680.open_sim() as rdr:
        run = rdr.read(450, ref_nm=655, n_reads=2)
        folder = write_run(run, tmp_path, "k", rdr.info, keep={(1, 1), (8, 12)})
    names = sorted(p.name for p in folder.iterdir())
    assert {"raw_FS1_K002.tsv", "raw_FS2_K001.tsv", "kinetic_FS1.csv", "kinetic_FS2.csv", "raw_long.csv"} <= set(names)
    rows = list(csv.DictReader(open(folder / "raw_long.csv")))
    assert len(rows) == 2 * 2 * 2                                   # reads x wavelengths x wells
    h12 = [r for r in rows if r["well"] == "H12"][0]
    assert h12["value"] == "" and h12["status"] == "OVER"
    hdr = (folder / "run_header.txt").read_text()
    assert "Wells = A1, H12" in hdr and "FilterSet2 = Wavelength=655 Role=reference" in hdr
    raw = (folder / "reader_raw.txt").read_text().splitlines()
    assert raw[0] == "# read 1" and raw.count(".begin") == 4 and "Ref. filter:7" in raw
    kin = list(csv.reader(open(folder / "kinetic_FS1.csv")))
    assert kin[0] == ["kinetic_read", "t_s", "received_s", "A1", "H12"] and len(kin) == 3


def test_cli(tmp_path, capsys):
    assert main(["--sim", "info"]) == 0
    assert "490" in capsys.readouterr().out
    out = tmp_path / "runs"
    assert main(["--sim", "--trace", str(tmp_path / "t.log"), "read", "--wl", "450", "--ref", "655",
                 "--no-wait", "--out", str(out), "--id", "cli"]) == 0
    assert "*.***" in capsys.readouterr().out
    (folder,) = out.iterdir()
    assert (folder / "raw_FS2.tsv").exists()
    trace = (tmp_path / "t.log").read_text()
    assert "EIA.READER AQ" in trace and "RPLATE" in trace
    assert main(["--sim", "read", "--wl", "560", "--no-wait"]) == 1
    assert main(["--sim", "incubator", "--set", "37"]) == 0


def test_serial_settings(monkeypatch):
    import serial

    from tigerreader.transport.serial_port import SerialTransport
    seen = {}

    class Fake:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(serial, "Serial", Fake)
    SerialTransport("COM6", stopbits=1, rtscts=True).open()
    assert seen["stopbits"] == serial.STOPBITS_ONE and seen["rtscts"] is True
    SerialTransport("COM5").open()                                    # FLx800 unchanged
    assert seen["stopbits"] == serial.STOPBITS_TWO and seen["rtscts"] is False


# ---------------------------------------------------------------- absorbance protocol and storage
def test_abs_setup_checks_and_time():
    from tigerreader.m680.setup import AbsSetup
    from tigerreader.m680.sim import Simulated680
    with Model680.open_sim(sim=Simulated680()) as rdr:
        info = rdr.info
    s = AbsSetup(meas_nm=450, ref_nm=655, speed="normal", mix_time_s=5)
    assert s.problems(None, info) == [] and s.estimated_time_s() == 35
    k = AbsSetup(meas_nm=405, speed="fast", kinetic=True, kinetic_reads=3, kinetic_interval_s=2)
    assert k.estimated_time_s() == 18                                    # reads longer than the interval
    assert "560 nm" in " ".join(AbsSetup(meas_nm=560).problems(None, info))
    assert AbsSetup(meas_nm=450, ref_nm=450).problems()
    assert AbsSetup(wells="A1, B2").selected_wells() == {(1, 1), (2, 2)}
    assert AbsSetup(wells="A1:H12").selected_wells() is None
    assert AbsSetup(wells="Z9").problems()


def test_protocol_store_per_instrument():
    from tigerreader.m680.setup import AbsSetup
    from tigerreader.readsetup import ReadSetup
    from tigerreader.storage import ParamStore
    st = ParamStore()
    st.save(ReadSetup(name="F"))
    st.save(AbsSetup(name="A", meas_nm=490))
    assert [n for n, *_ in st.protocols("flx800")] == ["F"] and [n for n, *_ in st.protocols("m680")] == ["A"]
    assert isinstance(st.load("A"), AbsSetup) and st.load("A").meas_nm == 490 and isinstance(st.load("F"), ReadSetup)
    with pytest.raises(ValueError):
        st.save(ReadSetup(name="A"))                                     # never overwrite the other reader's protocol
    assert st.unique_name("A") == "A (2)"


def test_bad_checksum_is_resent_with_rtplate():
    sim = Simulated680(Sim680Config(bad_checksum_once=True))
    with Model680.open_sim(sim=sim) as rdr:
        run = rdr.read(450, ref_nm=655)
        assert run.ok and run.reads[0].data.checksum_ok
        assert sim.log[-1] == "EIA.READER RTPLATE"
