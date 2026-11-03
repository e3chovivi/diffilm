"""diffilm command line: compare / verify / crop."""

from __future__ import annotations

import argparse
import json
import sys

from .core import Gates, crop, evaluate, run_compare


def _gates(args) -> Gates:
    return Gates(
        activity_max=args.gate_activity,
        motion_s=args.gate_motion,
        energy_tol=args.gate_energy,
        peaks_lo=args.gate_peaks_lo,
        peaks_hi=args.gate_peaks_hi,
    )


def _gate_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--gate-activity", type=float, default=1.5,
                   help="mean per-second activity |ref-clone| in gray levels (default 1.5)")
    p.add_argument("--gate-motion", type=float, default=0.30,
                   help="max motion start/end error in seconds (default 0.30)")
    p.add_argument("--gate-energy", type=float, default=0.25,
                   help="energy mean ratio tolerance ± (default 0.25)")
    p.add_argument("--gate-peaks-lo", type=float, default=0.4,
                   help="min ref/clone peak-count ratio (default 0.4)")
    p.add_argument("--gate-peaks-hi", type=float, default=2.5,
                   help="max ref/clone peak-count ratio (default 2.5)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="diffilm",
        description="Video replica QC: activity curves, motion window, "
                    "rhythm energy — pass/fail gates with named causes.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("compare", help="measure a replica against its reference")
    pc.add_argument("--ref", required=True, help="reference video")
    pc.add_argument("--clone", required=True, help="replica video")
    pc.add_argument("--out", default="diffilm-out", help="report directory")
    pc.add_argument("--json", action="store_true", help="print report JSON to stdout")
    _gate_flags(pc)

    pv = sub.add_parser("verify", help="re-check gates against an existing report.json")
    pv.add_argument("--report", required=True, help="path to report.json")
    _gate_flags(pv)

    pr = sub.add_parser("crop", help="extract a frame (pair) for visual arbitration")
    pr.add_argument("--video", help="single video")
    pr.add_argument("--ref", help="reference video (with --clone: extract both)")
    pr.add_argument("--clone", help="replica video (with --ref: extract both)")
    pr.add_argument("--t", type=float, required=True, help="timestamp in seconds")
    pr.add_argument("--region", help="crop x,y,w,h in source pixels")
    pr.add_argument("--out", default="diffilm-out/crops", help="output directory")

    args = ap.parse_args(argv)

    if args.cmd == "compare":
        report = run_compare(args.ref, args.clone, args.out, _gates(args))
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            for c in report["checks"]:
                mark = "PASS" if c["ok"] else "FAIL"
                print(f"[{mark}] {c['name']}: {c['value']} (limit {c['limit']})"
                      + (f" — {c['cause']}" if c["cause"] else ""))
            verdict = "PASS" if report["pass"] else "FAIL"
            print(f"{verdict}  report: {args.out}/report.md")
        return 0 if report["pass"] else 1

    if args.cmd == "verify":
        with open(args.report, encoding="utf-8") as f:
            report = json.load(f)
        checks = evaluate(report, _gates(args))
        for c in checks:
            mark = "PASS" if c["ok"] else "FAIL"
            print(f"[{mark}] {c['name']}: {c['value']} (limit {c['limit']})"
                  + (f" — {c['cause']}" if c["cause"] else ""))
        ok = all(c["ok"] for c in checks)
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1

    if args.cmd == "crop":
        if args.video:
            path = crop(args.video, args.t, f"{args.out}/{args.video.split('/')[-1].rsplit('.', 1)[0]}_{args.t:g}s.png", args.region)
            print(path)
            return 0
        if args.ref and args.clone:
            a = crop(args.ref, args.t, f"{args.out}/ref_{args.t:g}s.png", args.region)
            b = crop(args.clone, args.t, f"{args.out}/clone_{args.t:g}s.png", args.region)
            print(a)
            print(b)
            return 0
        print("crop: give --video, or --ref and --clone", file=sys.stderr)
        return 2

    return 2


if __name__ == "__main__":
    sys.exit(main())
