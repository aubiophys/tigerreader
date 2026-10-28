"""Desktop shortcut / launcher for Windows (the parts that can be checked off Windows)."""
from pathlib import Path, PureWindowsPath

from tigerreader import winshortcut as ws


def test_find_activate(tmp_path):
    base = tmp_path / "miniconda3"
    env = base / "envs" / "tigerreader"
    (base / "Scripts").mkdir(parents=True)
    env.mkdir(parents=True)
    (base / "Scripts" / "activate.bat").write_text("")
    assert ws.find_activate(env) == base / "Scripts" / "activate.bat"                       # <base>\envs\<name>
    assert ws.find_activate(env, str(base / "Scripts" / "conda.exe")) == base / "Scripts" / "activate.bat"
    assert ws.find_activate(tmp_path / "elsewhere") is None


def test_launcher_text():
    prefix = PureWindowsPath(r"C:\Users\me\miniconda3\envs\tigerreader")
    act = PureWindowsPath(r"C:\Users\me\miniconda3\Scripts\activate.bat")
    t = ws.launcher_text(prefix, act)
    assert t.startswith("@echo off")
    assert r'call "C:\Users\me\miniconda3\Scripts\activate.bat" "C:\Users\me\miniconda3\envs\tigerreader"' in t
    assert r'start "" "C:\Users\me\miniconda3\envs\tigerreader\pythonw.exe" -m tigerreader.gui' in t
    assert r'"C:\Users\me\miniconda3\envs\tigerreader\python.exe" -m tigerreader.gui' in t and "pause" in t   # debug


def test_shortcut_script_quotes_paths():
    ps = ws.shortcut_script(PureWindowsPath(r"C:\Users\o'brien\TigerReaderData\TigerReader.bat"),
                            PureWindowsPath(r"C:\Users\o'brien\TigerReaderData"), PureWindowsPath(r"C:\py\pythonw.exe"))
    assert "GetFolderPath('Desktop')" in ps and "'TigerReader.lnk'" in ps
    assert r"$s.TargetPath = 'C:\Users\o''brien\TigerReaderData\TigerReader.bat'" in ps
    assert "$s.WindowStyle = 7" in ps and r"'C:\py\pythonw.exe,0'" in ps


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ws, "default_data_dir", lambda: tmp_path / "TigerReaderData")
    assert ws.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "TigerReader.bat" in out and "-m tigerreader.gui" in out
    assert not (tmp_path / "TigerReaderData").exists()


def test_refuses_without_conda(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "default_data_dir", lambda: tmp_path / "d")
    monkeypatch.setattr(ws.sys, "prefix", str(tmp_path / "plainpython"))
    monkeypatch.delenv("CONDA_EXE", raising=False)
    assert ws.main([]) == 1 and not Path(tmp_path / "d").exists()
