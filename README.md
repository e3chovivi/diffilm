# diffilm

**Video replica QC.** Point it at your reference and your replica; it answers with
numbers — how much of the frame moves each second, when motion starts and ends,
whether the rhythm energy matches — and **fails with named causes**, not vibes.

Built for the "agent replicates a video as code" loop: measure → get told exactly
what is off → fix → re-measure. Works on any two videos (different assets, colors
and copy are fine — diffilm measures *motion*, not pixels).

## Install

```bash
pip install .          # from a checkout; provides the `diffilm` command
# requires ffmpeg/ffprobe on PATH, numpy
```

## Usage

```bash
# full comparison → report.json + report.md, exit 1 when a gate fails
diffilm compare --ref reference.mp4 --clone replica.mp4 --out ./qc

# re-check gates later (CI-friendly)
diffilm verify --report ./qc/report.json

# extract matched full-res crops at a timestamp, for eyeball arbitration
diffilm crop --ref reference.mp4 --clone replica.mp4 --t 4.0 --out ./qc/crops
```

Example output (real run — a 13s promo replica vs its reference):

```
[PASS] activity_curve: 1.013 (limit ≤ 1.5)
[PASS] motion_start: -0.1 (limit ± 0.3s)
[PASS] motion_end: -0.1 (limit ± 0.3s)
[PASS] energy_mean: 0.143 (limit ±25%)
[PASS] motion_peaks: 0.88 (limit 0.4–2.5)
PASS  report: ./qc/report.md
```

## Reference vs replica, side by side

Left = original, right = the code replica, at five key moments:

![reference vs replica](assets/compare.png)

Same run, the numbers behind the picture:

| metric | reference | replica |
| --- | --- | --- |
| activity curve (mean \|ref−clone\|) | — | **1.013** (gate ≤ 1.5) |
| motion window | 1.53s → 10.17s | 1.43s → 10.07s |
| timing error (start / end) | — | **−0.1s / −0.1s** (gate ±0.3s) |
| energy mean | 3.70 | **4.23** (+14%, gate ±25%) |
| motion peaks | 21 | **24** (gate 0.4–2.5×) |
| alignment lag | — | 0.067s |


## What it measures

| metric | meaning |
| --- | --- |
| **activity curve** | per-second fraction of the frame that changed — the shape of motion over time (ref vs clone, auto-aligned) |
| **motion window** | first / last / peak moment of sustained motion |
| **timing error** | clone minus reference on motion start / end / peak |
| **energy mean & peaks** | grayscale frame-diff energy: overall busyness and how many distinct motion bursts |

## Gates (all tunable)

| flag | default | checks |
| --- | --- | --- |
| `--gate-activity` | 1.5 | mean per-second activity difference (gray levels) |
| `--gate-motion` | 0.30s | motion start/end error |
| `--gate-energy` | ±25% | energy mean ratio |
| `--gate-peaks-lo/hi` | 0.4–2.5 | motion peak-count ratio |

**Fidelity levels in spirit:** diffilm never requires pixel identity. A replica with
different album art, colors or fonts can pass; a replica that is static where the
reference moves cannot.

## Why not just refilm?

[refilm](https://www.npmjs.com/package/refilm) is excellent for cloning *CSS/DOM
animations* — it can spec per-element easing because it records the DOM. diffilm
targets *video-vs-video* replication (canvas/code replicas of real footage) and
adds what refilm lacks: **rhythm/energy gates and per-second activity shape**.
The two are complementary; use refilm when you own the clone's DOM, diffilm when
you only have two MP4s.

## License

MIT
