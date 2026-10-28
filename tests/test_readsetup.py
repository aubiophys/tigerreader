"""ReadSetup (GUI protocol) and parameter store."""

from tigerreader import FLx800
from tigerreader.protocol.assay import AssayType, ReadMode, ReadSeries, SensitivityMode
from tigerreader.readsetup import FilterSetSetup, ReadSetup, describe
from tigerreader.storage import ParamStore


def test_default_read_settings():
    a = ReadSetup().to_assay()
    fs = a.filter_sets[0]
    assert (fs.samples_per_well, fs.delay_before_ms, fs.delay_between_ms, fs.sensitivity) == (10, 350, 1, 100)
    assert a.read_mode == ReadMode.ENDPOINT and a.geometry.rows == 8


def test_kinetic_luminescence_series_and_json():
    s = ReadSetup(name="x", luminescence=True, kinetic=True, kinetic_reads=5, kinetic_interval=0,
                  wells="A1:D6", filter_sets=[FilterSetSetup("Plug", "580/50", sensitivity=150)])
    a = s.to_assay()
    assert a.assay_type == AssayType.LUMINESCENCE and a.kinetic_interval_s == 0 and a.read_rect.last_col == 6
    assert ReadSetup.from_json(s.to_json()) == s
    s3 = ReadSetup(filter_sets=[FilterSetSetup(), FilterSetSetup("360/40", "460/40"),
                                FilterSetSetup("530/25", "620/40", sensitivity="auto")])
    series = s3.to_assay()
    assert isinstance(series, ReadSeries) and len(series.definitions) == 2
    assert series.definitions[1].filter_sets[0].sensitivity_mode == SensitivityMode.AUTO_ONBOARD
    assert "kinetic 5× continuous" in describe(s)


def test_problems_use_reader_table_and_model():
    rdr = FLx800.open_sim()
    table = rdr.get_filter_table()
    assert ReadSetup().problems(table, rdr.info) == []
    bad = ReadSetup(filter_sets=[FilterSetSetup("485/20", "530/20", optics="bottom")])
    probs = bad.problems(table, rdr.info)
    assert any("530/20 not in reader filter table" in p for p in probs)
    assert any("no bottom probe" in p for p in probs)
    assert ReadSetup(plate="nonsense").problems()          # unknown plate -> message, no exception


def test_estimated_time():
    s = ReadSetup(kinetic=True, kinetic_reads=3, kinetic_interval=120)
    assert 300 < s.estimated_time_s() < 320                 # 2 x 120 s + one 69 s pass


def test_param_store(tmp_path):
    st = ParamStore(tmp_path / "p.db")
    st.save(ReadSetup(name="Example"))
    assert st.protocol_names() == ["Example"] and st.load("Example").filter_sets[0].excitation == "485/20"
    assert st.unique_name("Example") == "Example (2)"
    st.set("port", "COM5")
    st.close()
    st = ParamStore(tmp_path / "p.db")
    assert st.get("port") == "COM5" and st.load("Example") is not None
    st.delete("Example")
    assert st.protocol_names() == []


def test_well_list_reads_covering_block():
    """A list of wells is read as the smallest covering block; only the listed wells are kept."""
    from tigerreader.readsetup import parse_wells
    assert parse_wells("A1, b2;C4", 8, 12) == {(1, 1), (2, 2), (3, 4)}
    assert parse_wells("A1:A3 D5", 8, 12) == {(1, 1), (1, 2), (1, 3), (4, 5)}
    s = ReadSetup(wells="A1, B2, C4")
    r = s.to_assay().read_rect
    assert (r.first_row, r.first_col, r.last_row, r.last_col) == (1, 1, 3, 4)
    assert s.selected_wells() == {(1, 1), (2, 2), (3, 4)}
    assert ReadSetup(wells="A1:D6").selected_wells() is None          # a block keeps everything
    assert ReadSetup(wells="B2").selected_wells() is None             # a single well is its own block
    assert ReadSetup(wells="").well_set() is None
    assert any("outside this 8x12 plate" in p for p in ReadSetup(wells="A1, I1").problems())
    assert any("cannot read 'A1x'" in p for p in ReadSetup(wells="A1x").problems())


def test_well_list_run_files(tmp_path):
    from tigerreader.export_ascii import write_run
    from tigerreader.rundata import RunData
    rdr = FLx800.open_sim()
    s = ReadSetup(wells="A1, B2, C4")
    res = rdr.read(s.to_assay())
    folder = write_run(res, tmp_path, "list", rdr.info, keep=s.selected_wells())
    hdr = (folder / "run_header.txt").read_text()
    assert "Wells = A1, B2, C4" in hdr and "ReadArea = A1:C4" in hdr
    rd = RunData.from_folder(folder)
    assert set(rd.reads[0].values) == {(1, 1), (2, 2), (3, 4)}
    rows = (folder / "raw_FS1.tsv").read_text().splitlines()
    assert rows[2].split("\t")[1] != "" and rows[2].split("\t")[2] == ""       # A1 kept, A2 blank

