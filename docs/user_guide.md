# TigerReader — User Guide

TigerReader controls two kinds of plate reader and saves what they measure:

* **BioTek FLx800** — fluorescence and luminescence
* **Bio-Rad Model 680** — absorbance

It acquires raw data only. Every read is saved as plain text files (CSV/TSV) that open in a spreadsheet or any
analysis program; blanking, curves and other analysis are done outside TigerReader.

---

## 1. Starting
* Double-click **TigerReader** on the desktop. (Made once with `tigerreader-shortcut`, see section 11.)
* Choose the reader: **BioTek FLx800** or **Bio-Rad Model 680**. The last choice is preselected.
* *File → Switch instrument…* changes the reader later; it asks first, and disconnects the current reader.
* Without the shortcut: Anaconda Prompt → `conda activate tigerreader` → `tigerreader`.

The window: **Protocols / Runs** on the left, the **protocol editor** in the middle, the **results** (plate and
curves) on the right, the **Instrument** panel and the **Log** at the edges (*View* hides and shows them).

## 2. Connecting
1. *Port*: choose the reader's COM port (FTDI USB adapters are marked ★; *Device Manager → Ports* shows which is
   which). *↻* refreshes the list. The port is remembered for each reader. Both readers can be connected to the
   same PC at the same time, each on its own port.
2. **Connect**. The Instrument panel shows the model, serial number and filters; the status bar says *Ready*.
3. **Disconnect** when done (closing TigerReader also disconnects).

The two **simulators** in the port list (20× faster, real speed) behave like the selected reader. Use them to try
protocols or learn the program without a plate.

Only one program can use a COM port at a time: close any other program that uses the reader's port.

## 3. Protocols
A protocol is a saved set of read settings. The *Protocols* tab lists the protocols of the selected reader.
* **New**, **Save** (asks before replacing), *Protocol → Save as…*, **Delete** (runs already saved are not affected).
* *Protocol → Export / Import protocol file (.json)* copies protocols between computers.
* Protocol names are shared by both readers: a name already used by the other reader's protocol is refused.
* Below the editor, TigerReader checks the settings and shows the **estimated read time**, or what is wrong
  (*Cannot read yet: …*). It will not start a read with a problem.

### 3.1 FLx800 protocol
| Field | Meaning |
|---|---|
| Detection | Fluorescence or Luminescence (luminescence puts *Plug* as excitation) |
| Plate | plate type (96-well = 8x12, and other formats the FLx800 supports) |
| Wells | empty = whole plate; a block `A1:D6`; or a list `A1, B2, C4:C6` (the reader reads the smallest block that covers the list; only the listed wells are kept) |
| Mode | Endpoint, or Kinetic: number of reads and interval — *Reader minimum* (fastest fixed interval), *Continuous* (no pause), or *Fixed interval* (seconds) |
| Filter sets | one row per Ex/Em pair (up to 6; more than 2 are read as a series). Optics: Top or Bottom, as fitted to the reader |
| Sensitivity | gain 25–255, or tick **Auto**: the reader chooses the gain so that the *Auto well* reads the *target* (default 90 000; lower it to leave room for brighter wells) |
| Samples, Delay, Between | measurements per well (default 10), delay before sampling a well (350 ms), delay between samples (1 ms) |

With two filter sets the reader reads the whole plate with set 1, then with set 2 (about one pass later).

### 3.2 Model 680 protocol
| Field | Meaning |
|---|---|
| Wavelength | measurement wavelength; only the filters installed in the reader are offered |
| Reference | optional second wavelength (e.g. 655 nm), saved as a second plate; subtract it in your analysis |
| Speed | Normal (15 s; 30 s with a reference) or Fast (6 s; 10 s) |
| Mixing | 0–999 s before each plate read; Low, Medium, High |
| Wells | the whole plate is always read; a block or list only selects the wells that are kept |
| Mode | Endpoint, or Kinetic: number of reads (2–1000) and interval **start to start** (0 = one read after the other). TigerReader times the reads; a read longer than the interval is followed at once by the next |

## 4. Reading a plate
1. Choose or make a protocol, then **Read plate ▶** (F5).
2. Enter a **Results ID** (names the run folder) and an optional description.
3. Load the plate when asked, **A1 at the back left**:
   * FLx800: the carrier comes out (*Present carrier*); put the plate on it and press OK.
   * Model 680: put the plate on the carrier and press OK; the carrier moves in by itself.
4. The plate appears as soon as each plate read arrives; the status bar shows progress and the time left.

**Abort ■** (Esc) stops a read and keeps everything received so far (the run is marked ABORTED). On the Model 680 it
stops after the plate read in progress (6–30 s), because the reader has no stop command; the reader's own
Start/Stop key stops it at once.

## 5. Looking at the results
* *Filter set* (FLx800) or *Wavelength* (Model 680) chooses the plate; *read k of N* steps through kinetic reads;
  *follow latest* jumps to each new read.
* Values: FLx800 in RFU/RLU (whole numbers); Model 680 in OD with 3 decimals. `*****` = overrange (FLx800: above the
  detector range — lower the sensitivity; Model 680: beyond ±3.5 OD). An empty cell = not read or not kept.
* *heat map* colours the wells from lowest (dark blue) to highest (yellow).
* Select wells (click, drag, Ctrl/Shift) to see n, mean, SD, CV, min, max and — for kinetic reads — their curves.
* **Copy** or Ctrl+C copies the selection (or the whole plate) as a table for Excel.
* *Runs* tab: double-click a run to show it again (from any reader).

## 6. Where the data are
Everything is under **`%USERPROFILE%\TigerReaderData`** (`C:\Users\<you>\TigerReaderData`):

| Path | Content |
|---|---|
| `runs\<date>_<time>_<ResultsID>\` | one folder per read (below); *File → Folder for runs…* changes the location |
| `traces\session_….log` | every byte sent and received in each session (for troubleshooting) |
| `selftests\` | FLx800 self-test reports |
| `tigerreader_params.db` | protocols and settings (no measurement data) |
| `TigerReader.bat` | the launcher used by the desktop shortcut |

**Run folder** (all plain text except the PDF):

| File | Content |
|---|---|
| `run_header.txt` | key = value: program version, reader model and serial number, Results ID, times, status (OK / ABORTED / error), plate, wells, read settings, one `FilterSet<n>` line per filter set or wavelength |
| `raw_long.csv` | one row per well per read: filter set, wavelengths, kinetic read, `t_s`, `received_s`, row, col, well, value, status (OK / OVER) |
| `raw_FS<n>.tsv` | the plate as a 8 × 12 table per filter set (`raw_FS1_K003.tsv` = kinetic read 3) |
| `kinetic_FS<n>.csv` | kinetic reads only: one row per read (time), one column per well — ready for curve fitting |
| `protocol.json`, `run.json` | the protocol used; the header in machine-readable form |
| `trace.log` | the serial traffic of this read |
| `reader_raw.txt` | Model 680: the reader's answers exactly as received |
| `report.pdf` | the report, if saved (section 7) |

Times: `t_s` = start of the read in seconds (FLx800: the reader's time stamp of the kinetic cycle, the same for both
filter sets of a cycle; Model 680: the program's clock). `received_s` = when the data arrived at the PC.

## 7. Reports
*File → Save run report as PDF…* (default `report.pdf` in the run folder) or *Print run report…* (Ctrl+P): the run
header and every plate read as tables. Tick *File → Save report after each read* to write `report.pdf` every time.

## 8. Instrument panel
* **FLx800**: *Get status*, *Read filter table*, *System self-test…* (about a minute; moves the carrier and wheels).
  *Carrier out / in* are in the toolbar.
* **Model 680**: filter wheel, usage counters and the **incubator**: status, set point (25.0–45.0 °C, at least 5 °C
  above the room), **On** / **Off**, ↻ to read the status again. *Instrument → Reset reader* clears a reader error
  (e.g. after the Start/Stop key) and reconnects.

## 9. Troubleshooting
| Problem | What to do |
|---|---|
| *Connect* fails or times out | Right port? Reader switched on, at its main menu? Cable plugged in (an RS-232-level adapter, not TTL)? Another program (for example a second TigerReader) using the port? Then Connect again |
| Model 680: first connect refused (8073 in the log) | Normal now and then; TigerReader retries once by itself |
| FLx800: interval too short (1700) | Use *Reader minimum* or a longer interval; one 96-well pass takes about 70 s per filter set |
| *Cannot read yet* | Read the reasons below the editor (e.g. a filter not in the reader, overlapping Ex/Em, a wavelength not installed) |
| Many `*****` (FLx800) | Lower the sensitivity, or use Auto with a lower target |
| Model 680 error 8075–8093 (lamp, filter, carrier, Stop key) | Fix the cause, then *Instrument → Reset reader* (clears the error and reconnects) |
| Checksum error | The program asks the reader to send the plate again; if it happens twice the read stops: check the cable |
| TigerReader does not start from the shortcut | Run `TigerReaderData\TigerReader.bat debug` in a Command Prompt: the console shows the error |
| Something else | Tick *show serial trace* in the Log, repeat, and keep `traces\` and the run folder. When sharing these files (for example in an issue), remember that they contain your reader's serial number |

## 10. Command line
The commands `flx800` and `m680` (Anaconda Prompt) do the same reads without the window, e.g.
`m680 --port COM6 read --wl 450 --ref 655 --out runs --id plate1`. `flx800 --help` and `m680 --help` list all
options; `--sim` uses the simulator.

## 11. Installing and updating
**Install** (Miniconda, Anaconda Prompt, in the TigerReader folder):
`conda env create -f environment.yml`, `conda activate tigerreader`, `pip install -e .`, `pytest -q`.

**Updating**: close TigerReader, replace the program folder with the new version (your protocols and runs in
`TigerReaderData` are not touched), then `conda activate tigerreader`, `pip install -e .` and `pytest -q` (all tests
must pass).

**Desktop shortcut** (Windows; once, or after the conda environment moved): Anaconda Prompt →
`conda activate tigerreader` → `tigerreader-shortcut`. `tigerreader-shortcut --dry-run` shows what it would write.

**Check after an update** (about 10 minutes):
1. `pytest -q`: all pass.
2. Start from the desktop shortcut; the reader choice appears.
3. Each reader: *Simulator (20× faster)* → Connect → read the default protocol → the plate appears.
4. Each connected reader: Connect, one endpoint read of a known plate; compare with an earlier run of that plate.
5. Open the runs from the *Runs* tab and save a report.
