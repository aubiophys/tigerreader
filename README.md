# TigerReader

Data acquisition software for two microplate readers connected over RS-232:

* **BioTek FLx800** — fluorescence and luminescence (endpoint and kinetic reads, up to six filter sets, partial
  plates and well lists, on-board auto-sensitivity, incubator and shaker where fitted)
* **Bio-Rad Model 680** — absorbance (single or dual wavelength, mixing, endpoint and program-timed kinetic reads,
  incubator)

TigerReader controls the reader and records exactly what it measures. Every read is saved as plain text files
(CSV/TSV plus a key = value header), ready for any analysis program; it does not analyse data itself.

It has a graphical interface (PySide6), command-line tools (`flx800`, `m680`), a Python API, and built-in
simulators of both readers, so everything can be tried without hardware.

## Status
Version 0.7.x is usable for routine acquisition on both readers. It has been checked on hardware with 96-well
plates; the FLx800's other plate formats are implemented from the manual and tested on the simulator.

## About this program
This program was developed for the sole purpose of controlling acquisition on both instruments through their
RS-232 ports with Python. The code was written by Claude (Anthropic) from the instruments' published manuals; the
communication conventions and the error messages are, where possible, verbatim from the manuals to make
troubleshooting easier.

Two open-source projects for other BioTek readers helped with the first orientation:
[PyLabRobot](https://github.com/PyLabRobot/pylabrobot) (its BioTek backends use the same family of single-character
commands, ACK/ETX framing and status strings as the FLx800) and [el800-control](https://pypi.org/project/el800-control/)
(serial settings and data framing of the EL800). No code was taken from either project.

A few details that the manuals do not describe, or describe differently, follow the readers' actual replies:
the Model 680 sends its data checksum as four hexadecimal digits (the 16-bit sum of the eight data rows including
their carriage returns) and reports filter-wheel positions rather than wavelengths in its data header; its first
remote-mode request after the port opens is occasionally refused and is retried once. For the FLx800, read-time
estimates and timeouts come from a per-well timing model, and the reader's time stamp is ignored for endpoint reads.

> **Disclaimer.** TigerReader is an independent project. It is not affiliated with, endorsed by or supported by
> the manufacturers of these instruments. Product names are trademarks of their owners and are used only to
> identify the supported hardware. For research use only; check results against your own reference method before
> relying on them. See the license for the (absent) warranty.

## Requirements
* Windows 10/11 (macOS and Linux work for development and the simulators)
* Python 3.10 or newer for the GUI (3.8+ for the drivers and command lines); [Miniconda](https://docs.conda.io)
  recommended
* An RS-232 connection to the reader, e.g. a USB-to-RS-232 adapter (RS-232 levels, not TTL)

## Install
```bat
conda env create -f environment.yml
conda activate tigerreader
pip install -e .
pytest -q
tigerreader-shortcut        (Windows: optional desktop shortcut)
tigerreader
```
The GUI packages come from conda-forge (see `environment.yml`); with plain pip use `pip install -e ".[gui,dev]"`.

## Quick start
1. Start `tigerreader` and choose the reader.
2. Choose *Simulator (20× faster)* as the port and press **Connect** to try it without hardware, or choose the
   reader's COM port.
3. Make or choose a protocol, then **Read plate**.

Command line, for example:
```bash
flx800 --sim read --ex 485/20 --em 528/20 --sens 100 --out runs --id test
m680 --sim read --wl 450 --ref 655 --out runs --id test
```

## Documentation
* [User guide](docs/user_guide.md) — connecting, protocols, reading, run files, reports, troubleshooting
* [Developer guide](docs/developer_guide.md) — code layout, tests, simulators, safety, adding a reader

## Safety
The drivers only send the commands they need. Commands that change a reader's stored settings (serial number,
filter tables, stored protocols, maintenance counters, calibration) are refused before they reach the wire.

## License
MIT — see [LICENSE](LICENSE).
