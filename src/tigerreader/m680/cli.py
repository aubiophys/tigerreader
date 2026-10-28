"""Command line for the Bio-Rad Model 680.

    m680 --port COM6 --trace session.log info
    m680 --sim read --wl 450 --ref 655 --out ./runs --id test1
    m680 --port COM6 read --wl 405 --kinetic 10 --interval 30 --speed fast --wells "A1:A6, C4"
    m680 --port COM6 incubator --set 37        (or --off)
"""
from __future__ import annotations

import argparse
import sys

from .. import __version__
from ..protocol.framing import FLx800Error
from .driver import Model680, PlateRead


def _open(args) -> Model680:
    if args.sim:
        return Model680.open_sim(trace_path=args.trace)
    if not args.port:
        sys.exit("error: give --port COMx (see 'flx800 ports') or --sim")
    return Model680.open_serial(args.port, trace_path=args.trace, rtscts=args.rtscts)


def _matrix(vals) -> str:
    out = ["     " + "".join(f"{c:>7d}" for c in range(1, 13))]
    for r, row in enumerate(vals, 1):
        out.append(f"  {chr(64 + r)}  " + "".join(f"{'*.***' if v is None else f'{v:.3f}':>7}" for v in row))
    return "\n".join(out)


def cmd_read(rdr: Model680, args) -> int:
    keep = None
    if args.wells:
        from ..readsetup import parse_wells
        keep = parse_wells(args.wells, 8, 12)
    if not args.no_wait:
        input("Load the plate, then press Enter ")
    print(f"TigerReader {__version__}: reading {args.wl} nm" + (f" (reference {args.ref} nm)" if args.ref else "")
          + (f", {args.kinetic} reads every {args.interval:g} s" if args.kinetic > 1 else ""))

    def show(pr: PlateRead) -> None:
        warn = "" if pr.data.checksum_ok else "  CHECKSUM?"
        print(f"  read {pr.index + 1}: started {pr.t_s:.1f} s, data at {pr.received_s:.1f} s{warn}")

    try:
        run = rdr.read(args.wl, args.ref, args.speed, args.mix, args.mix_speed, max(1, args.kinetic),
                       args.interval, on_plate=show)
    except KeyboardInterrupt:
        print("stopped (the plate read in progress finishes on the reader)")
        return 2
    if args.out:
        from .export import write_run
        print("saved:", write_run(run, args.out, args.id, rdr.info, description=args.description or "", keep=keep))
    if run.reads:
        last = run.reads[-1]
        print(f"\n{args.wl} nm, read {last.index + 1}:\n" + _matrix(last.data.meas.values))
        if last.data.ref is not None:
            print(f"\n{args.ref} nm (reference):\n" + _matrix(last.data.ref.values))
    print("\nstatus:", "OK" if run.ok else run.error or "ABORTED")
    return 0 if run.ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="m680", description="Bio-Rad Model 680 absorbance reader control (TigerReader)")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--port", help="serial port: COM6, /dev/cu.usbserial-XXXX, /dev/ttyUSB0 (see 'flx800 ports')")
    p.add_argument("--sim", action="store_true", help="use the built-in simulator")
    p.add_argument("--trace", help="append a byte-level trace to this file")
    p.add_argument("--rtscts", action="store_true", help="use RTS/CTS hardware handshake (if the reader needs it)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="identify the reader: ID, serial number, filters, incubator, counters")
    sub.add_parser("reset", help="reset the reader (recovers from errors 8075-8093)")
    i = sub.add_parser("incubator", help="show the incubator; --set T or --off")
    g = i.add_mutually_exclusive_group()
    g.add_argument("--set", type=float, help="set point 25.0-45.0 °C (switches it on)")
    g.add_argument("--off", action="store_true")
    r = sub.add_parser("read", help="read the plate (endpoint, or --kinetic N)")
    r.add_argument("--wl", type=int, required=True, help="measurement wavelength in nm (an installed filter)")
    r.add_argument("--ref", type=int, help="reference wavelength in nm (dual-wavelength read)")
    r.add_argument("--speed", choices=["fast", "normal"], default="normal")
    r.add_argument("--mix", type=int, default=0, help="mixing time before each read, 0-999 s")
    r.add_argument("--mix-speed", choices=["L", "M", "H"], default="L")
    r.add_argument("--kinetic", type=int, default=1, help="number of reads (host-timed)")
    r.add_argument("--interval", type=float, default=0.0, help="seconds from start to start (0 = back to back)")
    r.add_argument("--wells", help="keep only these wells, e.g. 'A1:D6' or 'A1, B2, C4' (the whole plate is read)")
    r.add_argument("--no-wait", action="store_true", help="do not wait for Enter before reading")
    r.add_argument("--out", help="output directory for ASCII run folders")
    r.add_argument("--id", default="run", help="Results ID")
    r.add_argument("--description")
    args = p.parse_args(argv)
    try:
        with _open(args) as rdr:
            if args.cmd == "info":
                print(rdr.info.summary())
            elif args.cmd == "reset":
                rdr.reset()
                print("reset done")
            elif args.cmd == "incubator":
                if not rdr.info.has_incubator:
                    print("This Model 680 has no incubator.")
                    return 1
                if args.set is not None or args.off:
                    rdr.set_incubator(None if args.off else args.set)
                s = rdr.incubator()
                print(f"incubator {'on' if s['on'] else 'off'}: set point {s['setpoint_c']} °C, "
                      f"temperature {s['temperature_c']} °C")
            elif args.cmd == "read":
                return cmd_read(rdr, args)
    except (FLx800Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
