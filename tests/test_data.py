import pytest

from tigerreader.protocol.data import build_block, parse_block_body, read_stream
from tigerreader.protocol.framing import ProtocolError


def reader(buf: bytes):
    pos = [0]

    def rd(n):
        if pos[0] + n > len(buf):
            raise TimeoutError
        out = buf[pos[0]:pos[0] + n]
        pos[0] += n
        return out
    return rd


def manual_example_block() -> bytes:
    """A 2x3 block written by hand from the Appendix C description."""
    body = b"000:00:12.5" + b",00123,00456,*****\r\n" + b",99998,00000,00007\r\n" + b"372" + b"\x1a"
    return b"\r" + body + b"%03d" % (sum(body) % 256)


def test_parse_handwritten_stream():
    stream = b"0000\x03" + manual_example_block() + b"\x1a" + b"0000\x03"
    resp = read_stream(reader(stream))
    assert resp.ok
    (blk,) = resp.blocks
    assert blk.elapsed_s == 12.5
    assert blk.values == [[123, 456, None], [99998, 0, 7]]
    assert blk.temperature_c == 37.2


def test_build_block_roundtrip():
    vals = [[1, 2, 3], [None, 5, 6]]
    raw = build_block(vals, 3725.4)
    resp = read_stream(reader(b"0000\x03" + raw + raw + b"\x1a0000\x03"))
    assert len(resp.blocks) == 2 and resp.blocks[0].values == vals
    assert abs(resp.blocks[0].elapsed_s - 3725.4) < 1e-6


def test_checksum_error_detected():
    raw = bytearray(manual_example_block())
    raw[-1] = ord("0") if raw[-1] != ord("0") else ord("1")
    with pytest.raises(ProtocolError):
        read_stream(reader(b"0000\x03" + bytes(raw) + b"\x1a0000\x03"))


def test_initial_error_and_abort():
    resp = read_stream(reader(b"1600\x03"))
    assert not resp.ok and resp.initial_status.code == "1600" and not resp.blocks
    resp = read_stream(reader(b"0000\x03" + build_block([[1]]) + b"\x100101"))
    assert resp.aborted and resp.final_status.code == "0101" and len(resp.blocks) == 1
    resp = read_stream(reader(b"0000\x03" + b"\x1a" + b"0505\x03"))
    assert resp.final_status.code == "0505" and not resp.ok


def test_bad_row():
    with pytest.raises(ProtocolError):
        parse_block_body(b"000:00:00.0" + b"00123\r\n")
