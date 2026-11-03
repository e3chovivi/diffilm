"""Core measurements: frames -> activity / energy / motion window -> gates."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np

GRAY_W, GRAY_H = 160, 90


# ----------------------------------------------------------------- extraction

def gray_frames(path: str, w: int = GRAY_W, h: int = GRAY_H) -> tuple[np.ndarray, float]:
    """Decode the video to a stack of grayscale frames plus its fps."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    rate = probe[0].strip().rstrip(",") if probe else ""
    num, _, den = rate.partition("/")
    try:
        fps = float(num) / float(den or 1)
        if fps <= 0:
            raise ValueError
    except ValueError:
        fps = 24.0
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={w}:{h},format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout
    n = len(raw) // (w * h)
    frames = np.frombuffer(raw[: n * w * h], np.uint8).reshape(n, h, w).astype(np.float32)
    return frames, fps


# ----------------------------------------------------------------- measurements

def activity(frames: np.ndarray) -> np.ndarray:
    """Per-frame mean absolute difference: the activity curve (what changed)."""
    if len(frames) < 2:
        return np.zeros(0, np.float32)
    return np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))


def energy(frames: np.ndarray, fps: float) -> dict:
    """Motion-energy summary: mean / max / peaks / per-second means."""
    a = activity(frames)
    if a.size == 0:
        return {"mean": 0.0, "max": 0.0, "peaks": [], "per_second": []}
    mean = float(a.mean())
    thr = mean + float(a.std())
    peaks = [
        round((i + 1) / fps, 2)
        for i in range(1, len(a) - 1)
        if a[i] > thr and a[i] >= a[i - 1] and a[i] > a[i + 1]
    ]
    secs = int(np.ceil(len(a) / fps)) or 1
    per_second = [
        round(float(a[int(s * fps): int((s + 1) * fps)].mean()), 3)
        if int(s * fps) < len(a) else 0.0
        for s in range(secs)
    ]
    return {"mean": round(mean, 3), "max": round(float(a.max()), 3),
            "peaks": peaks, "per_second": per_second}


def motion_window(a: np.ndarray, fps: float) -> dict:
    """First / last / peak moment of sustained motion (refilm-style)."""
    if a.size == 0:
        return {"start_s": None, "end_s": None, "peak_s": None}
    moving = a > max(0.02, 0.2 * float(a.max()))
    idx = np.where(moving)[0]
    if idx.size == 0:
        return {"start_s": None, "end_s": None, "peak_s": None}
    return {
        "start_s": round(float(idx[0]) / fps, 3),
        "end_s": round(float(idx[-1] + 1) / fps, 3),
        "peak_s": round(float(np.argmax(a)) / fps, 3),
    }


def per_second(a: np.ndarray, fps: float) -> list[float]:
    secs = int(np.ceil(len(a) / fps)) or 1
    return [
        round(float(a[int(s * fps): int((s + 1) * fps)].mean()), 3)
        if int(s * fps) < len(a) else 0.0
        for s in range(secs)
    ]


def align_offset(ref: np.ndarray, clone: np.ndarray, max_shift: int = 30) -> int:
    """Best clone lag (frames) by cross-correlating the two activity curves."""
    n = min(len(ref), len(clone))
    if n < 8:
        return 0
    r, c = ref[:n], clone[:n]
    r = (r - r.mean()) / (r.std() + 1e-9)
    c = (c - c.mean()) / (c.std() + 1e-9)
    best, best_score = 0, -np.inf
    for lag in range(-max_shift, max_shift + 1):
        if lag >= 0:
            a, b = r[lag:], c[: n - lag] if lag else c
        else:
            a, b = r[: n + lag], c[-lag:]
        if len(a) < n // 2:
            continue
        score = float(np.dot(a, b)) / len(a)
        if score > best_score:
            best_score, best = score, lag
    return best


def shift_curve(a: np.ndarray, lag: int) -> np.ndarray:
    out = np.zeros_like(a)
    if lag >= 0:
        out[: len(a) - lag] = a[lag:]
    else:
        out[-lag:] = a[: len(a) + lag]
    return out


# ----------------------------------------------------------------- gates

@dataclass
class Gates:
    activity_max: float = 1.5      # mean per-second |ref-clone| activity (gray levels)
    motion_s: float = 0.30         # max |start| / |end| error, seconds
    energy_tol: float = 0.25       # energy mean ratio tolerance (±)
    peaks_lo: float = 0.4          # peaks ref/clone ratio range
    peaks_hi: float = 2.5


def evaluate(report: dict, gates: Gates) -> list[dict]:
    """Re-check every gate against a report dict; each failure names its cause."""
    results = []
    act = report.get("activity_mean_abs_diff")
    if act is not None:
        results.append({
            "name": "activity_curve",
            "ok": act <= gates.activity_max,
            "value": round(act, 3),
            "limit": f"≤ {gates.activity_max}",
            "cause": (f"per-second activity mismatch {act:.3f} > {gates.activity_max}"
                      if act > gates.activity_max else ""),
        })
    tw = report.get("timing_error") or {}
    for key in ("start", "end"):
        v = tw.get(key)
        if v is None:
            continue
        ok = abs(v) <= gates.motion_s
        results.append({
            "name": f"motion_{key}",
            "ok": ok,
            "value": round(v, 3),
            "limit": f"± {gates.motion_s}s",
            "cause": (f"motion {key} off by {v:+.3f}s (limit ±{gates.motion_s}s)"
                      if not ok else ""),
        })
    en = report.get("energy") or {}
    rm, cm = en.get("ref_mean"), en.get("clone_mean")
    if rm and cm:
        ratio = (cm - rm) / rm
        ok = abs(ratio) <= gates.energy_tol
        results.append({
            "name": "energy_mean",
            "ok": ok,
            "value": round(ratio, 3),
            "limit": f"±{int(gates.energy_tol * 100)}%",
            "cause": (f"energy mean {cm:.2f} vs ref {rm:.2f} ({ratio:+.0%})"
                      if not ok else ""),
        })
    rp, cp = en.get("ref_peaks"), en.get("clone_peaks")
    if rp is not None and cp:
        ratio = rp / cp
        ok = gates.peaks_lo <= ratio <= gates.peaks_hi
        results.append({
            "name": "motion_peaks",
            "ok": ok,
            "value": round(ratio, 2),
            "limit": f"{gates.peaks_lo}–{gates.peaks_hi}",
            "cause": (f"peak count ref {rp} vs clone {cp} (ratio {ratio:.2f})"
                      if not ok else ""),
        })
    return results


def run_compare(ref_path: str, clone_path: str, out_dir: str,
                gates: Gates | None = None, fps: int = 15) -> dict:
    """Full pipeline: decode both, measure, align, gate, write report files."""
    gates = gates or Gates()
    ref_frames, ref_fps = gray_frames(ref_path)
    clone_frames, clone_fps = gray_frames(clone_path)
    a_ref, a_clone = activity(ref_frames), activity(clone_frames)
    lag = align_offset(a_ref, a_clone)
    aligned = shift_curve(a_clone, lag)
    aligned = aligned[: len(a_ref)]
    ref_ps = per_second(a_ref, ref_fps)
    clone_ps = per_second(aligned, clone_fps)
    n = max(len(ref_ps), len(clone_ps))
    ref_ps += [0.0] * (n - len(ref_ps))
    clone_ps += [0.0] * (n - len(clone_ps))
    sum_abs = float(np.abs(np.array(ref_ps) - np.array(clone_ps)).sum())
    mean_abs = sum_abs / max(len(ref_ps), 1)

    mw_ref, mw_clone = motion_window(a_ref, ref_fps), motion_window(a_clone, clone_fps)
    timing = {}
    if mw_ref["start_s"] is not None and mw_clone["start_s"] is not None:
        timing["start"] = round(mw_clone["start_s"] - mw_ref["start_s"], 3)
    if mw_ref["end_s"] is not None and mw_clone["end_s"] is not None:
        timing["end"] = round(mw_clone["end_s"] - mw_ref["end_s"], 3)
    if mw_ref["peak_s"] is not None and mw_clone["peak_s"] is not None:
        timing["peak"] = round(mw_clone["peak_s"] - mw_ref["peak_s"], 3)

    e_ref, e_clone = energy(ref_frames, ref_fps), energy(clone_frames, clone_fps)
    report = {
        "schema": 1,
        "kind": "diffilm-compare",
        "ref": ref_path,
        "clone": clone_path,
        "alignment_lag_frames": lag,
        "alignment_lag_s": round(lag / ref_fps, 3),
        "activity_per_second": {"ref": ref_ps, "clone": clone_ps},
        "activity_sum_abs_diff": round(sum_abs, 3),
        "activity_mean_abs_diff": round(mean_abs, 3),
        "motion_window": {"ref": mw_ref, "clone": mw_clone},
        "timing_error": timing,
        "energy": {
            "ref_mean": e_ref["mean"], "clone_mean": e_clone["mean"],
            "ref_peaks": len(e_ref["peaks"]), "clone_peaks": len(e_clone["peaks"]),
            "ref_per_second": e_ref["per_second"],
            "clone_per_second": e_clone["per_second"],
        },
    }
    report["checks"] = evaluate(report, gates)
    report["pass"] = all(c["ok"] for c in report["checks"])

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    (out / "report.md").write_text(render_md(report))
    return report


def render_md(r: dict) -> str:
    lines = [f"# diffilm compare — {'PASS' if r['pass'] else 'FAIL'}", ""]
    lines.append(f"- alignment lag: **{r['alignment_lag_s']}s** "
                 f"({r['alignment_lag_frames']} frames)")
    lines.append(f"- activity mean |ref-clone|: **{r.get('activity_mean_abs_diff')}** "
                 f"(sum {r['activity_sum_abs_diff']})")
    tw, mw = r["timing_error"], r["motion_window"]
    lines.append(f"- motion window ref {mw['ref']} vs clone {mw['clone']}")
    if tw:
        lines.append(f"- timing error: {tw}")
    en = r["energy"]
    lines.append(f"- energy mean ref {en['ref_mean']} / clone {en['clone_mean']} · "
                 f"peaks ref {en['ref_peaks']} / clone {en['clone_peaks']}")
    lines += ["", "## checks", ""]
    for c in r["checks"]:
        mark = "PASS" if c["ok"] else "FAIL"
        lines.append(f"- [{mark}] **{c['name']}** {c['value']} (limit {c['limit']})"
                     + (f" — {c['cause']}" if c["cause"] else ""))
    lines += ["", "## per-second activity", "", "| s | ref | clone |", "|---|---|---|"]
    for i, (a, b) in enumerate(zip(r["activity_per_second"]["ref"],
                                   r["activity_per_second"]["clone"])):
        lines.append(f"| {i} | {a} | {b} |")
    lines.append("")
    return "\n".join(lines)


def crop(video: str, t: float, out: str, region: str | None = None) -> str:
    """Extract one frame at t (optionally cropped x,y,w,h) as PNG for arbitration."""
    args = ["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", video, "-frames:v", "1"]
    if region:
        # region is x,y,w,h — the crop filter wants w:h:x:y
        x, y, w, h = (int(v) for v in region.split(","))
        args += ["-vf", f"crop={w}:{h}:{x}:{y}"]
    args.append(out)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(args, check=True)
    return out
