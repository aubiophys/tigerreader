from tigerreader.cli import main


def test_cli_info_and_read(tmp_path, capsys):
    assert main(["--sim", "info"]) == 0
    assert "000000" in capsys.readouterr().out
    out = tmp_path / "runs"
    rc = main(["--sim", "--trace", str(tmp_path / "t.log"), "read", "--no-carrier", "--kinetic", "3",
               "--interval", "10", "--wells", "A1:B3", "--out", str(out), "--id", "kin"])
    assert rc == 0
    (folder,) = out.iterdir()
    names = sorted(p.name for p in folder.iterdir())
    assert "raw_FS1_K003.tsv" in names and "raw_long.csv" in names
    assert main(["--sim", "read", "--no-carrier", "--ex", "485/20", "--em", "495/20"]) == 1  # overlap refused


def test_ports_and_auto(monkeypatch, capsys):
    import tigerreader.transport.serial_port as sp
    from tigerreader.protocol.framing import CommError

    monkeypatch.setattr(sp, "list_ports", lambda: [("/dev/tty.usbserial-A1", "FT232R [0403:6001]", True),
                                                   ("/dev/cu.usbserial-A1", "FT232R [0403:6001]", True),
                                                   ("/dev/cu.Bluetooth", "bt", False)])
    assert sp.find_ftdi_port() == "/dev/cu.usbserial-A1"
    monkeypatch.setattr(sp, "list_ports", lambda: [])
    import pytest
    with pytest.raises(CommError):
        sp.find_ftdi_port()
