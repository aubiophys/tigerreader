# TigerReader — Developer Guide

## 1. Layout
```
src/tigerreader/
  protocol/        FLx800: framing, commands (safety classes), assay definition (v), data blocks, error codes
  driver/flx800.py FLx800 high-level driver (connect, read, kinetic, abort, filters, self-test)
  transport/       serial port (pyserial), FLx800 simulator, tracing wrapper, trace replay
  m680/            Model 680: protocol, driver, simulator, export, AbsSetup (protocol model), m680 command line
  readsetup.py     FLx800 protocol model (ReadSetup) and well-list parsing
  storage.py       SQLite parameter store (protocols of both readers, settings)
  export_ascii.py  FLx800 run folders;  rundata.py  run data for display (live or from a folder)
  report.py        run report (HTML → PDF/print);  cli.py  flx800 command line;  winshortcut.py  desktop shortcut
  gui/             main_window (both readers), worker (thread; ReaderWorker, M680Worker), setup_panel (FLx800),
                   m680_panel (Model 680), plate (heat map view)
tests/             pytest (headless; simulators only, no hardware needed)
```

## 2. Environment
* Miniconda with `environment.yml` (conda-forge; PySide6, pyqtgraph and pytest-qt come from conda-forge, which
  avoids Qt DLL problems with pip wheels inside conda on Windows), then `pip install -e .`.
* Or a venv: `pip install -e ".[gui,dev]"`.
* The core (drivers, command lines, exports) runs on Python 3.8+; the GUI needs 3.10+.
* Lint: `ruff check --select E,F,W src tests` (line length 120).

## 3. Tests
* `pytest -q` runs everything headless (`tests/conftest.py` selects Qt's off-screen platform; on Windows it also
  points Qt at the system fonts so that window sizes are measured correctly).
* **Simulators**: `transport/sim.py` (a fictional FLx800T; `SimConfig.tbi()` gives one with incubator, shaker and
  bottom probe) and `m680/sim.py` (a fictional Model 680 with incubator). `pace` makes them as slow as a real
  reader (1.0) or faster; the GUI offers them as ports.
* **Replay tests**: `transport/replay.py` replays a recorded serial trace (`--trace` files, or `trace.log` in a run
  folder) and checks every byte the driver sends. Traces recorded on your own readers make good regression tests;
  they contain the reader's serial number, so keep them out of public repositories.

## 4. Safety
* FLx800: `protocol/commands.py` classifies every command; the driver refuses commands that change stored settings
  (serial number, dispenser calibration, checksum test) and undocumented commands, and writes the filter table only
  in service mode. Every assay is validated before sending (ranges, Ex/Em band-pass overlap to protect the PMT,
  filters present in the reader's table).
* Model 680: `m680/protocol.py` `ALLOWED` / `FORBIDDEN`; `command()` refuses everything else before it reaches the
  wire. Adding a command means adding it to `ALLOWED` deliberately.

## 5. Adding another reader
Follow the Model 680 pattern: `<reader>/protocol.py` (encode/parse, allowed commands, errors), `driver.py`
(synchronous, thread-safe; abort between reads if the reader has no abort command), `sim.py` (simulator +
transport), `export.py` (run folder in the common layout: `run_header.txt`, `raw_long.csv`, `raw_FS<n>.tsv`,
`kinetic_FS<n>.csv`), `setup.py` (protocol model with `problems()`, `estimated_time_s()`, `selected_wells()`, JSON
with an `"instrument"` key), a GUI panel with the `SetupPanel` interface (`setup()`, `set_setup()`, `set_reader()`,
`check`, `changed`), a worker subclass of `ReaderWorker`, and entries in `INSTRUMENTS` (`gui/main_window.py`),
`storage.setup_from_json` and `RunData.from_folder`. Start with the command line and read-only commands, trace
everything, then add the GUI.

## 6. Contributing
Run `pytest -q` and ruff before a pull request; add a test for every behaviour change. Do not include traces, run
folders or screenshots that show a reader's serial number or your own set-up.
