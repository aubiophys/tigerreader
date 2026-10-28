"""Command-line interface.

    flx800 ports
    flx800 --port COM5 info
    flx800 --sim read --ex 485/20 --em 528/20 --sens 100 --out ./runs --id test1
    flx800 --port COM5 --trace trace.log read --lum --em 580/50 --sens 150 --wells A1:D12
"""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .driver.flx800 import FLx800
from .protocol.assay import (PLATE_CHOICES, AssayDefinition, AssayType, Filter, FilterSet, Optics, ReadMode,
                             ReadSeries, SensitivityMode, ShakeMode, ShakeSpeed, WellRect, geometry, well_to_rc)
from .protocol.framing import FLx800Error


def _open(args) -> FLx800:
    kw = dict(trace_path=args.trace, service_mode=getattr(args, "service", False))
    if args.sim:
        return FLx800.open_sim(**kw)
    if not args.port:
        sys.exit("error: give --port COMx | /dev/cu.usbserial-xxx | auto (see 'flx800 ports') or --sim")
    port = args.port
    if port.lower() == "auto":
        from .transport.serial_port import find_ftdi_port
        port = find_ftdi_port()
        print(f"using FTDI adapter on {port}", file=sys.stderr)
    return FLx800.open_serial(port, args.baud, **kw)


def _filter_sets(args) -> list[FilterSet]:
    lum = args.lum
    exs = args.ex or (["plug"] if lum else ["485/20"])
    ems = args.em or (["open"] if lum else ["528/20"])
    if len(exs) != len(ems):
        sys.exit("error: give the same number of --ex and --em values (one per filter set)")
    sens = args.sens or [150 if lum else 100]
    sens = sens + [sens[-1]] * (len(exs) - len(sens))
    optics = Optics.BOTTOM if args.optics == "bottom" else Optics.TOP
    out = []
    for ex, em, s in zip(exs, ems, sens):
        mode = SensitivityMode.FIXED
        if str(s).lower() == "auto":
            mode, s = SensitivityMode.AUTO_ONBOARD, 0
        out.append(FilterSet(Filter.parse(ex), Filter.parse(em), optics, args.samples, args.delay_before,
                             args.delay_between, mode, int(s)))
    return out


def cmd_read(rdr: FLx800, args) -> int:
    try:
        geom = geometry(args.geometry)
    except KeyError as exc:
        sys.exit(f"error: {exc.args[0]}")
    fastest = str(args.interval).lower() in ("min", "fastest")
    interval = 0 if fastest else int(args.interval)
    fsets = _filter_sets(args)
    kinetic = args.kinetic and args.kinetic > 1
    base = AssayDefinition(
        filter_sets=tuple(fsets[:2]),
        read_mode=ReadMode.KINETIC if kinetic else ReadMode.ENDPOINT,
        assay_type=AssayType.LUMINESCENCE if args.lum else AssayType.FLUORESCENCE,
        geometry=geom,
        wells=WellRect.parse(args.wells) if args.wells else None,
        onboard_autosense_well=well_to_rc(args.auto_well) if args.auto_well else (1, 1),
        kinetic_reads=args.kinetic if kinetic else 10,
        kinetic_interval_s=interval if kinetic else 10,
        shake_mode=ShakeMode(args.shake_mode), shake_speed=ShakeSpeed(args.shake_speed),
        shake_time_s=args.shake_time,
    )
    series = ReadSeries.split(base, fsets) if len(fsets) > 2 else base
    table = rdr.get_filter_table()
    if args.temp is not None:
        rdr.set_temperature(args.temp)
    if not args.no_carrier:
        print("Place the plate on the carrier…")
        rdr.carrier_out()
        input("Press Enter when the plate is loaded ")

    def show(off, blk):
        print(f"  block: t={blk.elapsed_s:.1f}s  {blk.n_rows}x{blk.n_cols} wells"
              + (f"  T={blk.temperature_c} C" if blk.temperature_c else ""))

    try:
        defs = series.definitions if isinstance(series, ReadSeries) else [series]
        print(f"TigerReader {__version__}: one plate pass takes about {sum(d.estimated_pass_s() for d in defs):.0f} s")
        if kinetic:
            print(f"kinetic: {args.kinetic} reads, interval "
                  + ("= reader minimum" if fastest else "0 = continuous, no pause" if interval == 0
                     else f"{interval} s"))
        res = rdr.read(series, table=table, include_temperature=args.include_temp, on_block=show,
                       fastest=fastest)
    except KeyboardInterrupt:
        print("aborting…")
        rdr.abort(soft=True)
        return 2
    if args.out:
        from .export_ascii import write_run
        folder = write_run(res, args.out, args.id, rdr.info, description=args.description or "")
        print(f"saved: {folder}")
    for fs_i, k, a, fs, blk in res.iter_reads():
        if k == 0 or k == a.n_kinetic - 1:
            r0, c0 = a.read_rect.first_row, a.read_rect.first_col
            print(f"\nFilter set {fs_i + 1} (Ex {fs.excitation}, Em {fs.emission}, "
                  f"sens {res.sensitivities.get(fs_i)}) read {k + 1}:")
            print("     " + "".join(f"{c0 + i:>7d}" for i in range(blk.n_cols)))
            for ri, row in enumerate(blk.values):
                print(f"  {chr(64 + r0 + ri)}  " + "".join(f"{'*****' if v is None else v:>7}" for v in row))
    print("\nstatus:", "OK" if res.ok else [s.code + " " + s.describe() for s in res.statuses])
    return 0 if res.ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="flx800", description="BioTek FLx800 control (TigerReader)")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--port", help="serial port: COM5, /dev/cu.usbserial-XXXX, /dev/ttyUSB0, or 'auto' (FTDI)")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--sim", action="store_true", help="use the built-in simulator")
    p.add_argument("--trace", help="append a byte-level trace to this file")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports", help="list serial ports")
    sub.add_parser("info", help="identify the reader")
    sub.add_parser("status", help="get (and clear) the current status")
    sub.add_parser("filters", help="show the reader's filter table")
    c = sub.add_parser("carrier", help="move the plate carrier")
    c.add_argument("direction", choices=["in", "out"])
    t = sub.add_parser("temp", help="read temperature / set incubator")
    t.add_argument("--set", type=int, help="set point in °C (0 = off)")
    sub.add_parser("selftest", help="run the reader self-test")

    r = sub.add_parser("read", help="read a plate (quick read, no protocol)")
    r.add_argument("--ex", action="append", help="excitation per filter set: 485/20, plug, open (repeat)")
    r.add_argument("--em", action="append", help="emission per filter set (repeat)")
    r.add_argument("--sens", action="append", help="sensitivity per filter set (25-255 or 'auto')")
    r.add_argument("--auto-well", help="well for on-board auto-sensitivity (e.g. A1)")
    r.add_argument("--optics", choices=["top", "bottom"], default="top")
    r.add_argument("--lum", action="store_true", help="luminescence (default fluorescence)")
    r.add_argument("--samples", type=int, default=10)
    r.add_argument("--delay-before", type=int, default=350, help="ms, multiple of 10")
    r.add_argument("--delay-between", type=int, default=1, help="ms between samples (default 1)")
    r.add_argument("--geometry", "--plate", default="8x12",
                   help="plate: " + ", ".join(PLATE_CHOICES) + " (aliases 6,12,24,48,96,96H,96M,384)")
    r.add_argument("--wells", help="partial plate, e.g. A1:D6")
    r.add_argument("--kinetic", type=int, default=0, help="number of kinetic reads (>=2)")
    r.add_argument("--interval", default="min",
                   help="kinetic interval in s; 'min' = shortest fixed interval the reader allows (default); "
                        "0 = read continuously without pauses")
    r.add_argument("--shake-mode", type=int, default=0, choices=[0, 1, 2])
    r.add_argument("--shake-speed", type=int, default=1, choices=[0, 1, 2, 3])
    r.add_argument("--shake-time", type=int, default=0)
    r.add_argument("--temp", type=int, help="set incubator before reading (°C, 0 = off; incubator models only)")
    r.add_argument("--include-temp", action="store_true", help="record temperature with each block")
    r.add_argument("--no-carrier", action="store_true", help="do not present the carrier / wait for Enter")
    r.add_argument("--out", help="output directory for ASCII run folders")
    r.add_argument("--id", default="run", help="Results ID")
    r.add_argument("--description")

    args = p.parse_args(argv)
    if args.cmd == "ports":
        from .transport.serial_port import list_ports
        ports = list_ports()
        for dev, desc, ftdi in ports:
            print(f"{'*' if ftdi else ' '} {dev:28s} {desc}")
        if not ports:
            print("no serial ports found")
        elif any(f for *_, f in ports):
            print("* = FTDI adapter (usable with --port auto)")
        return 0
    try:
        with _open(args) as rdr:
            if args.cmd == "info":
                print(rdr.info.summary())
            elif args.cmd == "temp" and not rdr.has_incubator:
                print(f"This {rdr.info.model} has no incubator: no temperature reading or control.")
                return 1
            elif args.cmd == "status":
                st = rdr.get_status()
                print(st.code, st.describe())
            elif args.cmd == "filters":
                tb = rdr.get_filter_table()
                for i in range(4):
                    print(f"  position {i + 1}:  Ex {str(tb.excitation[i]):>8}   Em {str(tb.emission[i]):>8}")
            elif args.cmd == "carrier":
                rdr.carrier_out() if args.direction == "out" else rdr.carrier_in()
            elif args.cmd == "temp":
                if args.set is not None:
                    rdr.set_temperature(args.set)
                print(f"temperature: {rdr.get_temperature()} °C   set point: {rdr.get_setpoint()} °C")
            elif args.cmd == "selftest":
                text, st = rdr.self_test()
                print(text)
                print("status:", st.code, st.describe())
            elif args.cmd == "read":
                return cmd_read(rdr, args)
    except (FLx800Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        if "1700" in str(exc):
            print("hint: the kinetic interval is shorter than one plate pass. Use --interval min (shortest fixed "
                  "interval, the default), --interval 0 (continuous), or a longer --interval.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
