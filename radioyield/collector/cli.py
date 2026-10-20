"""python -m radioyield.collector {run, ports, validate, stdf}

  run plans/<plan>.yaml --dut /dev/cu.usbmodemXXX --ref /dev/cu.usbmodemYYY --out out/rig/
  run plans/<plan>.yaml --fake --out out/sim/          (protocol test double, SIMULATED)
  ports                                                (find boards, query INFO, show roles)
  validate plans/*.yaml                                (check plans, print steps and time)
  stdf out/rig/<stem>.measurements.parquet             (re-export STDF if it failed at run time)
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from radioyield.collector import plan as PL
from radioyield.collector import runner as R
from radioyield.collector.transport import Board, BoardError, open_serial

ESPRESSIF_VIDS = {0x303A: "Espressif USB-Serial-JTAG", 0x10C4: "CP210x bridge", 0x1A86: "CH34x bridge",
                  0x0403: "FTDI bridge"}


def _boards(args, plan: PL.Plan, transcript):
    if args.fake:
        from radioyield.collector.fake import FakeAir, LinkModel

        link = LinkModel(distance_m=plan.setup.get("distance_m", 1.0))
        if plan.mode == "CONDUCTED":
            link.conducted_loss_db = (plan.attenuation_total_db or 0) + plan.setup.get("cable_loss_db", 0)
        air = FakeAir(link, seed=args.seed)
        dut = Board(air.board("DUT", 0x11, pwr_offset_qdbm=args.fake_pwr_offset), "DUT", transcript, simulated=True)
        ref = Board(air.board("REF", 0x22), "REF", transcript, simulated=True)
        return dut, ref
    if not args.dut or not args.ref:
        raise SystemExit("run: --dut and --ref are required (or --fake for the test double)")
    dut = Board(open_serial(args.dut, args.baud), "DUT", transcript)
    ref = Board(open_serial(args.ref, args.baud), "REF", transcript)
    return dut, ref


def cmd_run(args) -> int:
    try:
        plan = PL.load(args.plan)
    except PL.PlanError as e:
        print(e, file=sys.stderr)
        return 2
    for w in plan.warnings:
        print(f"plan warning: {w}")
    if args.unit:
        plan.unit_serial = args.unit
    if args.build:
        plan.build = args.build
    if args.distance is not None:
        plan.setup["distance_m"] = args.distance
    if args.position:
        plan.setup["position_label"] = args.position
    if args.orientation:
        plan.setup["orientation"] = args.orientation
    if args.obstacles:
        plan.setup["obstacles"] = args.obstacles
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    log_path = out / f"{R.run_stem(plan, now)}.serial.log"
    with open(log_path, "w") as transcript:
        dut = ref = None
        try:
            dut, ref = _boards(args, plan, transcript)
            res = R.run_plan(plan, dut, ref, out, now=now, run_id_seed=args.seed if args.fake else None)
        except BoardError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("interrupted; partial data written", file=sys.stderr)
            return 130
        finally:
            for b in (dut, ref):
                if b is not None:
                    b.close()
    print(f"\n{res.meta['data_origin']} run {res.stem}: {len(res.steps)} steps, {res.n_packets} packets logged")
    for k, p in res.files.items():
        print(f"  {k:13s} {p}")
    print(f"  {'serial log':13s} {log_path}")
    for w in res.warnings:
        print(f"WARNING: {w}")
    if res.stdf_error:
        print("\n*** STDF FILE NOT WRITTEN *** (data is safe in the Parquet files)", file=sys.stderr)
        return 3
    return 0


def cmd_ports(args) -> int:
    try:
        from serial.tools import list_ports
    except ImportError:
        print("pyserial is not installed (pip install 'radioyield[rig]')", file=sys.stderr)
        return 2
    found = 0
    for p in sorted(list_ports.comports(), key=lambda p: p.device):
        cand = p.vid in ESPRESSIF_VIDS or any(s in p.device for s in ("usbmodem", "usbserial", "ttyACM", "ttyUSB"))
        if not cand and not args.all:
            continue
        found += 1
        kind = ESPRESSIF_VIDS.get(p.vid, p.description or "?")
        line = f"{p.device:28s} {kind:26s} serial={p.serial_number or '-'}"
        try:
            b = Board(open_serial(p.device, args.baud), p.device)
            try:
                i = b.handshake(tries=2, timeout=1.5)
                line += f"  -> {i.role} fw={i.fw} mac={i.mac} tag={i.tag:04x} radios={','.join(i.radios)} label={i.label}"
            finally:
                b.close()
        except BoardError:
            line += "  -> no RadioYield INFO reply (not flashed, wrong baud, or busy)"
        except Exception as e:  # port busy / permission
            line += f"  -> cannot open: {e}"
        print(line)
    if not found:
        print("no candidate serial ports found (is the board plugged in with a data cable?)")
    return 0


def cmd_validate(args) -> int:
    rc = 0
    total = 0.0
    for f in args.plans:
        try:
            p = PL.load(f)
        except PL.PlanError as e:
            print(e)
            rc = 2
            continue
        secs = p.estimated_seconds()
        total += secs
        print(f"OK  {f}: {p.radio} {len(p.steps())} steps x {p.packets_per_step} pkts, "
              f"~{secs / 60:.1f} min, {p.data_origin}, build {p.build}")
        for w in p.warnings:
            print(f"    warning: {w}")
    if len(args.plans) > 1:
        print(f"total ~{total / 60:.1f} min of radio time (plus moving boards between positions)")
    return rc


def cmd_stdf(args) -> int:
    import pyarrow.parquet as pq

    t = pq.read_table(args.measurements)
    md = {k.decode(): v.decode() for k, v in (t.schema.metadata or {}).items()}
    t = t.replace_schema_metadata(None)
    from radioyield.schema import MEASUREMENT_SCHEMA

    t = t.cast(MEASUREMENT_SCHEMA)
    out = args.out or str(args.measurements).replace(".measurements.parquet", ".stdf")
    R.default_stdf_writer()(out, t, md)
    print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m radioyield.collector", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a sweep plan")
    r.add_argument("plan")
    r.add_argument("--dut", help="DUT serial port, e.g. /dev/cu.usbmodem1101")
    r.add_argument("--ref", help="REF serial port")
    r.add_argument("--out", default="out/rig")
    r.add_argument("--baud", type=int, default=115200, help="ignored by USB-Serial-JTAG; 921600 for UART builds")
    r.add_argument("--unit", help="override plan unit_serial (e.g. when swapping DUT boards)")
    r.add_argument("--build", help="override plan build label")
    r.add_argument("--distance", type=float, help="override setup.distance_m (path-loss walk)")
    r.add_argument("--position", help="override setup.position_label (e.g. 'wall', 'tape-8m')")
    r.add_argument("--orientation", help="override setup.orientation (e.g. for an orientation 'build')")
    r.add_argument("--obstacles", help="override setup.obstacles")
    r.add_argument("--fake", action="store_true", help="use the protocol test double; output is SIMULATED")
    r.add_argument("--fake-pwr-offset", type=int, default=0, help="test double only: DUT TX table offset (qdBm)")
    r.add_argument("--seed", type=int, default=1)
    r.set_defaults(fn=cmd_run)
    p = sub.add_parser("ports", help="list serial ports and identify RadioYield boards")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--all", action="store_true", help="probe every port, not just likely ones")
    p.set_defaults(fn=cmd_ports)
    v = sub.add_parser("validate", help="validate plans and estimate run time")
    v.add_argument("plans", nargs="+")
    v.set_defaults(fn=cmd_validate)
    s = sub.add_parser("stdf", help="write STDF from a saved measurements.parquet")
    s.add_argument("measurements")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_stdf)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
