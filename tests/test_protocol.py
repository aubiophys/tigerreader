import pytest

from tigerreader.protocol.assay import (GEOMETRIES, AssayDefinition, AssayType, Filter, FilterSet, FilterTable, Optics,
                                   ReadMode, ReadSeries, SensitivityMode, SeriesPosition, ShakeMode, ShakeSpeed,
                                   WellRect)
from tigerreader.protocol.errors import describe_status
from tigerreader.protocol.framing import ProtocolError, Status, checksum8, checksum_field, num, parse_status

FS1 = FilterSet(Filter(485, 20), Filter(528, 20), Optics.TOP, 10, 350, 10, SensitivityMode.FIXED, 100)


def test_status_parse_and_describe():
    st = parse_status(b"0000\x03")
    assert st.ok and not st.fatal
    st = parse_status(b"0e01\x03")
    assert st.code == "0E01"
    assert st.describe() == "filter specified for Filterset 1 not available in instrument's filter table"
    assert Status("A400").fatal
    with pytest.raises(ProtocolError):
        parse_status(b"0000")
    assert describe_status("1505") == "Incubator zone failed temperature range: Zones 1 and 3"
    assert describe_status("0203") == "Emissions filter wheel motor position sensor error"


def test_num_and_checksum():
    assert num(7, 3) == b"007"
    with pytest.raises(ValueError):
        num(1000, 3)
    assert checksum8(b"\xff\x02") == 1
    assert checksum_field(b"ABC") == b"%03d" % ((65 + 66 + 67) % 256)


def test_filter_codec():
    assert Filter.parse("plug").encode() == b"0000000"
    assert Filter.parse("open").encode() == b"0001000"
    assert Filter.parse("485/20").encode() == b"0485020"
    t = FilterTable([Filter(360, 40), Filter(485, 20), Filter(530, 25), Filter.plug()],
                    [Filter(460, 40), Filter(528, 20), Filter(580, 50), Filter(620, 40)])
    raw = t.encode()
    assert len(raw) == 56
    assert FilterTable.decode(raw) == t
    assert t.has(Filter(485, 20), emission=False) and not t.has(Filter(485, 30), emission=False)
    assert t.has(Filter.plug(), emission=False)


def test_assay_layout_matches_appendix_c():
    a = AssayDefinition(filter_sets=(FS1,), geometry=GEOMETRIES["8x12"])
    raw = a.encode()
    assert len(raw) == 111
    d = raw[:108]
    # 1-based positions from Appendix C
    pos = lambda i, j: d[i - 1:j]  # noqa: E731
    assert pos(1, 1) == b"1"            # endpoint
    assert pos(2, 2) == b"2"            # fluorescence
    assert pos(3, 4) == b"02"           # 96 wells
    assert pos(5, 12) == b"01010812"    # A1..H12
    assert pos(13, 13) == b"1"          # 1 filter set
    assert pos(14, 14) == b"1"          # top probe
    assert pos(15, 18) == b"0485" and pos(19, 21) == b"020"
    assert pos(22, 25) == b"0528" and pos(26, 28) == b"020"
    assert pos(29, 31) == b"010"        # samples
    assert pos(32, 34) == b"035"        # 350 ms in 10 ms units
    assert pos(35, 37) == b"010"        # 10 ms
    assert pos(38, 38) == b"0"          # fixed sensitivity
    assert pos(39, 41) == b"100"
    assert pos(46, 50) == b"90000"
    assert pos(58, 61) == b"0100"       # no shake, medium, single read, no eject
    assert pos(62, 77) == b"0" * 16     # unused auto-sense blocks
    assert pos(78, 105) == b"0" * 28    # unused filter set 2
    assert pos(106, 108) == b"000"
    assert raw[108:] == b"%03d" % (sum(d) % 256)


def test_assay_roundtrip_two_filtersets_kinetic():
    fs2 = FilterSet(Filter(360, 40), Filter(460, 40), Optics.BOTTOM, 20, 100, 0, SensitivityMode.FIXED, 75)
    a = AssayDefinition(filter_sets=(FS1, fs2), read_mode=ReadMode.KINETIC, kinetic_reads=5,
                        kinetic_interval_s=60, wells=WellRect.parse("B2:G11"),
                        shake_mode=ShakeMode.BEFORE_EACH_KINETIC_READ,
                        shake_speed=ShakeSpeed.FAST, shake_time_s=5)
    b = AssayDefinition.decode(a.encode())
    assert b.filter_sets == a.filter_sets
    assert b.read_rect == WellRect(2, 2, 7, 11)
    assert (b.kinetic_reads, b.kinetic_interval_s, b.shake_time_s) == (5, 60, 5)
    bad = bytearray(a.encode())
    bad[0] = ord("2") if bad[0] == ord("1") else ord("1")
    with pytest.raises(ProtocolError):
        AssayDefinition.decode(bytes(bad))


def test_validation_catches_unsafe_or_invalid():
    overlap = FilterSet(Filter(485, 20), Filter(495, 20))
    probs = AssayDefinition(filter_sets=(overlap,)).validate()
    assert any("overlap" in p for p in probs)
    open_fl = FilterSet(Filter.open(), Filter(528, 20))
    assert AssayDefinition(filter_sets=(open_fl,)).validate()
    lum_ok = FilterSet(Filter.plug(), Filter.open(), sensitivity=150)
    assert AssayDefinition(filter_sets=(lum_ok,), assay_type=AssayType.LUMINESCENCE).validate() == []
    assert AssayDefinition(filter_sets=(FS1,), wells=WellRect.parse("A1:I12")).validate()
    bad_sens = FilterSet(Filter(485, 20), Filter(528, 20), sensitivity=10)
    assert AssayDefinition(filter_sets=(bad_sens,)).validate()


def test_read_series_split():
    f = [FS1] * 5
    s = ReadSeries.split(AssayDefinition(filter_sets=(FS1,)), f)
    assert [len(d.filter_sets) for d in s.definitions] == [2, 2, 1]
    assert [d.series_position for d in s.definitions] == [SeriesPosition.FIRST, SeriesPosition.NEXT,
                                                          SeriesPosition.FINAL]
