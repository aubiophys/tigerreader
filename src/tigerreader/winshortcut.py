"""Desktop shortcut for TigerReader on Windows (conda install).

Run once in Anaconda Prompt, with the environment that has TigerReader installed:

    conda activate tigerreader
    tigerreader-shortcut

It writes ``%USERPROFILE%\\TigerReaderData\\TigerReader.bat`` (activates this conda environment and starts the GUI
without a console window; kept in the data folder, so extracting a new zip never touches it) and a "TigerReader"
shortcut on the desktop that runs it. ``TigerReader.bat debug`` starts the GUI with a console window and keeps it
open, for when the program does not start. ``tigerreader-shortcut --dry-run`` only shows what would be written.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .storage import default_data_dir


def find_activate(prefix: Path, conda_exe: Optional[str] = None) -> Optional[Path]:
    """Conda's ``activate.bat`` for the environment at *prefix* (via CONDA_EXE, else the base install above envs)."""
    candidates = []
    if conda_exe:
        candidates.append(Path(conda_exe).parent / "activate.bat")                 # <base>\Scripts\conda.exe
    if prefix.parent.name.lower() == "envs":
        candidates.append(prefix.parent.parent / "Scripts" / "activate.bat")       # <base>\envs\<name>
    candidates.append(prefix / "Scripts" / "activate.bat")                         # the base environment itself
    return next((c for c in candidates if c.is_file()), None)


def launcher_text(prefix: Path, activate: Path) -> str:
    """The .bat that starts TigerReader from this environment (CRLF line ends added when written)."""
    py, pyw = prefix / "python.exe", prefix / "pythonw.exe"
    return "\n".join([
        "@echo off",
        f"rem TigerReader launcher, written by tigerreader-shortcut (TigerReader {__version__}).",
        "rem Run it with the word debug to see error messages in a console window.",
        f'call "{activate}" "{prefix}"',
        'if /i "%~1"=="debug" (',
        f'  "{py}" -m tigerreader.gui',
        "  pause",
        "  exit /b",
        ")",
        f'start "" "{pyw}" -m tigerreader.gui',
        "",
    ])


def _ps_quote(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def shortcut_script(target: Path, workdir: Path, icon: Path, name: str = "TigerReader") -> str:
    """PowerShell that puts the shortcut on the user's desktop (also a redirected one) and prints its path."""
    return "; ".join([
        "$d = [Environment]::GetFolderPath('Desktop')",
        f"$p = Join-Path $d {_ps_quote(name + '.lnk')}",
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($p)",
        f"$s.TargetPath = {_ps_quote(target)}",
        f"$s.WorkingDirectory = {_ps_quote(workdir)}",
        "$s.WindowStyle = 7",                               # minimised: the .bat's console does not flash up
        f"$s.IconLocation = {_ps_quote(str(icon) + ',0')}",
        "$s.Description = 'TigerReader plate-reader acquisition'",
        "$s.Save()",
        "Write-Output $p",
    ])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tigerreader-shortcut", description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true", help="show the launcher and the shortcut, write nothing")
    args = p.parse_args(argv)
    prefix = Path(sys.prefix)
    activate = find_activate(prefix, os.environ.get("CONDA_EXE"))
    data = default_data_dir()
    bat = data / "TigerReader.bat"
    if activate is None and not args.dry_run:
        print(f"error: conda's activate.bat not found for {prefix}. Run this in Anaconda Prompt after "
              "'conda activate <environment>'.", file=sys.stderr)
        return 1
    text = launcher_text(prefix, activate or Path(r"<conda>\Scripts\activate.bat"))
    if args.dry_run:
        print(f"launcher {bat}:\n{text}\nshortcut: Desktop\\TigerReader.lnk -> {bat}")
        return 0
    if sys.platform != "win32":
        print("error: desktop shortcuts are made on Windows only (use --dry-run to see the launcher)", file=sys.stderr)
        return 1
    data.mkdir(parents=True, exist_ok=True)
    bat.write_text(text.replace("\n", "\r\n"), encoding="ascii", errors="replace", newline="")
    ps = shortcut_script(bat, data, prefix / "pythonw.exe")
    res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print(f"launcher written: {bat}\nerror: the shortcut could not be made: {res.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"launcher written: {bat}\nshortcut made:    {res.stdout.strip()}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
