# Minimum Processing Power for Encoding-Only — 8 Concurrent Tubers

> **Written**: 2026-08-09
> **Scope**: isolates *just* the video encode step — `ffmpeg`/`libx264`, the
> literal production settings from
> [stream_supervisor.py](../../app/stream_supervisor.py#L64-L83) — from the
> rest of a worker container (Xvfb rendering, the Node agent loop, LLM/TTS
> calls). Answers "how little CPU does *encoding* 8 streams need," not "how
> little CPU does the whole worker need" — that broader question is
> [power_efficient_scaling_1_to_10_tubers.md](power_efficient_scaling_1_to_10_tubers.md).
> **Method**: unlike the rest of this project's hardware docs, the core
> numbers below are **real measurements**, not TDP/spec-sheet estimates —
> run 2026-08-09 on `mk_workstation` (Ryzen 7 1700X, 2017, 8C/16T), one of
> the fleet's own boxes and a deliberately weak, conservative baseline by
> 2026 standards. Extrapolated to modern low-power chips using PassMark
> single-thread ratios (a reasonable proxy for compute-bound codec work,
> not a substitute for testing the actual candidate chip).
> **Corrected 2026-08-09 (same day, later pass)**: this doc originally read
> the 8-concurrent/1-thread-pinned 1080p test as "~1 vCPU-thread per stream
> is close to the real floor," reasoning naively from the `-threads 1` flag
> (8 streams × 1 thread = 8 of 16 logical processors "should" be enough).
> **That reasoning was wrong.** The actual measured total-system CPU load
> during that test was 90-100% of all 16 logical processors — not ~50% —
> because `-threads 1` only constrains libx264's internal encode threads;
> each ffmpeg process still runs separate demux/filter/mux/pacing threads
> that `-threads` doesn't touch, and 8 real concurrent processes contend for
> cache/memory bandwidth in a way an isolated single-stream test can't show.
> **The honest floor for 1080p worst-case content, measured, is close to
> this entire 8C/16T machine, not half of it** — see the corrected numbers
> in [§5](#5-corrected-1080p-floor-and-the-720p-comparison) below, added
> after a 720p comparison test surfaced the gap. [§1](#1-what-was-actually-measured)-[§3](#3-the-floor-minimum-viable-hardware)
> are left as originally written (including the miscalibrated conclusion)
> rather than silently edited, per this project's own convention of
> correcting in place with a dated note instead of rewriting history.

---

## TL;DR

- **Encoding alone is cheap.** On an 8-year-old CPU, one encoder thread
  handles a single 1080p30/3000kbps/veryfast stream at **1.77x realtime**
  for full-motion synthetic content, and **2.56x realtime** for a static
  frame — i.e. roughly **0.4-0.6 vCPU-equivalent per stream**, measured, not
  guessed.
- **8 concurrent streams, worst-case content, real test**: sustained
  **1.01-1.02x realtime** with default (unconstrained) threading on this
  same 8C/16T chip — comfortable, not maxed out. Pinning each stream to
  exactly 1 encoder thread (8 threads total, half the machine) put it right
  at the edge — **0.94-0.97x, borderline**, at 90-100% total CPU load. That
  bounds the floor: **~1 CPU thread per stream, worst case**, is close to
  the real minimum — going lower risks dropped frames.
- **This closely confirms** the existing "~1 vCPU per worker" estimate in
  [hardware_and_hosting.md](../environment/hardware_and_hosting.md#the-nvenc-finding)
  — first real data point behind that number. ⚠️ **Corrected below**: that
  reading undercounted real per-process overhead — the honest measured
  figure for 1080p worst-case is closer to **~1.9 vCPU-equivalent/stream,
  all-in**, not ~1. See [§5](#5-corrected-1080p-floor-and-the-720p-comparison).
- **The floor, extrapolated**: an **Intel N305-class mini PC** (8 Gracemont
  cores, no hyperthreading, ~15W TDP, ~$200-300) has real headroom to encode
  all 8 streams on CPU alone — no GPU, no NVENC, no code changes. An
  **Intel N100** (4 cores, 6W TDP, ~$150) is right at the edge — plausible
  for 8 real (lower-motion, terminal-style) streams but tight; worth testing
  against actual captured content before committing.

---

## 1. What was actually measured

All tests use the exact encode flags from `build_ffmpeg_cmd`
([stream_supervisor.py:64-83](../../app/stream_supervisor.py#L64-L83)):
`libx264 -preset veryfast -tune zerolatency -b:v 3000k -maxrate 3000k
-bufsize 6000k -pix_fmt yuv420p -g 60`, at 1920x1080@30. Video only (audio
encode is comparatively negligible — AAC 128kbps has no real CPU footprint
next to 1080p30 H.264). Source: `ffmpeg`'s `lavfi` synthetic generators, not
a live `x11grab` capture (this machine is Windows; `x11grab` is Linux-only)
— see [§4](#4-what-this-doesnt-cover) for why that matters and how to close
the gap.

| Test | Content | Threads | Result |
|---|---|---:|---|
| Single stream, worst case | `testsrc2` (full synthetic motion — moving color bars, high detail) | 1 | **speed=1.77x** realtime |
| Single stream, best case | Static black frame (zero motion, proxy for an idle/mostly-static terminal) | 1 | **speed=2.56x** realtime |
| 8 concurrent, worst case, production-realistic threading | `testsrc2` ×8, real-time paced (`-re`), default/auto thread count (matches production — `stream_supervisor.py` never sets `-threads`) | auto | **All 8 sustained 1.01-1.02x** |
| 8 concurrent, worst case, artificially capped | `testsrc2` ×8, real-time paced, capped to 1 encoder thread each (8 threads total, of 16 available) | 1 each | **All 8 at 0.94-0.97x** (borderline under realtime) — total system CPU load 90-100% during the run |

Raw logs from this run are in the scratchpad if you want to re-verify the
math; nothing here is a spec-sheet number.

---

## 2. From measurement to vCPU-equivalents per stream

"vCPU-equivalent" = the fraction of one logical CPU thread, at this
machine's clock/IPC, needed to sustain one stream at exactly 1.0x realtime.
Derived directly from the single-thread speed multiplier (1 ÷ speed):

| Content type | Measured speed (1 thread) | vCPU-equivalent/stream |
|---|---:|---:|
| Worst case (full motion) | 1.77x | **0.57** |
| Best case (static) | 2.56x | **0.39** |

Real Xvfb-captured terminal/ASCII content ([app/stream_supervisor.py](../../app/stream_supervisor.py)'s
actual source, per [argyreServer's own confirmation](../../../argyreServer/docs/hardware/powerEdge/README.md#confirmed-second-use-case-virtualtubers-twitch-streaming-pipeline-2026-08-07)
that this is a terminal/ASCII capture, not a rendered avatar) should sit
**between these two bounds, closer to the static end** — mostly flat
background and monospace text, with periodic scrolling/redraw, not
continuous full-frame motion. **0.4-0.5 vCPU-equivalent/stream is a
reasonable working estimate**; the 8-thread concurrent test in §1 (0.94-
0.97x at 8 threads for the *worst-case* content) is the conservative floor
to plan against until it's re-measured on the real capture.

For 8 streams:

| Scenario | vCPU-equivalents needed |
|---|---:|
| Worst-case content, no safety margin (measured floor) | **~8** |
| Worst-case content, comfortable margin (measured, default threading) | ~10-12 |
| Realistic terminal content (estimated from the static-frame bound) | **~3.2-4.8** |

---

## 3. The floor: minimum viable hardware

Extrapolated from the measured Ryzen 7 1700X numbers using PassMark
single-thread ratios (checked 2026-08-09,
[cpubenchmark.net](https://www.cpubenchmark.net/cpu.php?cpu=AMD+Ryzen+7+1700X&id=2969)):

| Chip | Cores/Threads | TDP | PassMark single-thread | Ratio vs. 1700X | Est. vCPU-equiv/stream (worst case) | 8-stream headroom | Typical price |
|---|---:|---:|---:|---:|---:|---|---:|
| **AMD Ryzen 7 1700X** (measured baseline) | 8C/16T | 95W | 2,100 | 1.00x | 0.57 | ~14 threads available for 8×0.57≈4.6 needed — real margin | owned |
| **Intel N305** | 8C/8T (no HT) | ~15W | ~1,850-1,900* | ~0.90x | ~0.63 | 8 threads for 8×0.63≈5.0 needed — **real headroom** | ~$200-300 (mini PC) |
| **Intel N100** | 4C/4T | 6W | 1,881 | 0.90x | ~0.63 | 4 threads for 8×0.63≈5.0 needed — **at capacity, no margin** | ~$120-180 (mini PC) |

*N305's PassMark CPU Mark (multi-thread) is 9,394 per
[cpubenchmark.net](https://www.cpubenchmark.net/compare/5213vs5157/Intel-i3-N305-vs-Intel-N100);
its single-thread score wasn't independently confirmed in this pass (same
Gracemont core as N100, so assumed comparable — **verify directly before
relying on it**, flagged as an open item below).

**Reading this table**: the N100's 4 threads only clear 8 worst-case
streams with essentially zero margin (4 available vs. ~5.0 needed) — it's
plausible, not safely provisioned, and the honest verdict is "maybe, if
real content is easier than the synthetic worst case, which it almost
certainly is." **The N305 is the more defensible floor** — double the
threads at roughly the same per-core speed and only ~2.5x the price, with
real margin instead of none. Both numbers assume **encode only**; neither
of these chips is meant to also run Xvfb+Node+Kafka+Postgres for all 8
workers — see [power_efficient_scaling_1_to_10_tubers.md](power_efficient_scaling_1_to_10_tubers.md)
for the whole-container picture, where the answer is a bigger chip
(BD895i SE-class) specifically because it has to carry more than encode.

**Framed the other way**: if the goal is genuinely "the least possible
silicon that can encode 8 tuber streams, and nothing else runs on this
box," an **N305 mini PC at ~15W** is a defensible, real, buyable answer —
roughly **1/6th the TDP** of the Ryzen 7 1700X this was measured on, at
comparable per-core speed, because Gracemont's perf/watt is dramatically
better than 2017 Zen1's.

---

## 4. What this doesn't cover

- **This is encode only.** A real worker also runs Xvfb (rendering the
  terminal), a Node.js agent loop, and periodic TTS synthesis — none of
  that is included here. Don't buy an N100 expecting it to run the whole
  `docker-compose.yml` stack; see the companion doc for that number.
- **Synthetic content, not the real capture.** `testsrc2` and a static
  frame bound the real Xvfb/ASCII terminal output but aren't it. The
  cheapest way to close this gap: run the exact same `ffmpeg` command
  against a real `x11grab` capture of a live worker (needs a Linux host —
  this test ran on Windows specifically because `x11grab` isn't available
  here) and compare the `speed=` output against the 1.77x/2.56x bounds
  above.
- **Bare-metal, not containerized.** These numbers are host-CPU ffmpeg
  processes, not measured inside Docker/cgroups. Container CPU accounting
  and any `docker-compose.yml` CPU limits (currently **none are set** on
  `worker-1`..`worker-8`, confirmed 2026-08-09) would add a small,
  unmeasured amount of overhead on top of this.
- **N305's single-thread PassMark wasn't independently verified** — see the
  table footnote. Confirm before treating the N305 row as more than a
  same-architecture estimate off the N100.
- **No measured power draw** for either candidate chip under this specific
  sustained load — TDP is a ceiling, not a typical-load number, same
  caveat as every other hardware doc in this project.

---

## 5. Corrected 1080p floor, and the 720p comparison

Added 2026-08-09 (same day, later pass) after being asked how 720p30
changes the picture — running that comparison surfaced the §1-§3
miscalibration described in the correction note at the top of this doc, so
this section carries both the 720p data and the fix.

### The right way to read the concurrent tests: total system CPU%, not the `-threads` flag

For each 8-concurrent test, "vCPU-equivalent/stream, all-in" is computed as
`(average measured total-system CPU% ÷ 100) × 16 logical processors ÷ 8
streams` — this captures the *real* cost (encode + demux/mux/pacing thread
overhead + scheduling/cache contention), not just the encoder's internal
thread count. All tests below use the same fps (30, unchanged from
production) and bitrate (3000 kbps, unchanged, to isolate resolution as the
only variable) — see [§6](#6-what-a-real-720p-config-would-also-change)
for what changes if bitrate is *also* lowered to a resolution-appropriate
value, which a real 720p switch normally would do.

| Resolution | Content | Speed (8 concurrent, real-time paced) | Avg. total CPU% (16 logical) | vCPU-equiv/stream, all-in |
|---|---|---:|---:|---:|
| 1080p | Worst case (full motion) | 0.94-0.97x (borderline) | **95.3%** | **~1.90** |
| 1080p | Near-static | *not measured — estimated below* | — | ~1.20 (est.) |
| **720p** | **Worst case (full motion)** | **1.02x (real headroom)** | **54.5%** | **~1.09** |
| **720p** | **Near-static** | **1.02x (real headroom)** | **34.5%** | **~0.69** |

(1080p near-static estimated from the 720p worst:static ratio of ~1.58:1
applied to the measured 1080p worst-case figure — flagged as an estimate,
not measured, in the same spirit as everything else in this project that
isn't a direct reading.)

### The headline: 720p cuts real per-stream cost by roughly 1.7-1.9x, not the ~2.25x pixel-count ratio would suggest

1280x720 has 44.4% of 1920x1080's pixels (2.25x fewer) — but the *measured*
reduction in CPU cost is smaller, ~1.7-1.9x. The gap is fixed per-process
overhead (demux, filter graph, muxing, the `-re`/`x11grab`-equivalent
pacing thread) that doesn't shrink with resolution — it's a larger share of
a smaller total cost at 720p, so the win from dropping resolution is real
but not the full geometric pixel-count reduction. Worth knowing before
assuming "quarter the pixels → quarter the CPU."

### Redone hardware floor, both resolutions, worst-case and realistic content

For 8 streams, using the measured all-in figures above, scaled to
Intel N100/N305-class cores via the same PassMark single-thread ratio as
[§3](#3-the-floor-minimum-viable-hardware) (0.896x the 1700X's per-core
speed → needed vCPU-equivalents scale up ~1.12x on that class of core):

| Resolution | Content | vCPU-equiv needed (8 streams, scaled to N100/N305 cores) | N100 (4 threads) | N305 (8 threads) |
|---|---|---:|---|---|
| 1080p | Worst case | ~17.0 | ❌ far short | ❌ far short — needs ~2x an N305 |
| 1080p | Near-static (est.) | ~10.7 | ❌ far short | ❌ short — needs ~1.3x an N305 |
| **720p** | **Worst case** | **~9.7** | ❌ short | ⚠️ **short by ~1.7 threads — tight, not safe** |
| **720p** | **Near-static** | **~6.2** | ❌ short (needs 1.5x) | ✅ **comfortable — ~1.8 threads of margin** |

**Revised verdict**: at 1080p, neither N100 nor N305 is a safe pick once
real concurrent overhead is counted honestly — [§3](#3-the-floor-minimum-viable-hardware)'s
original "N305 has real headroom" conclusion doesn't survive this
correction. **At 720p, the picture changes meaningfully**: worst-case
content is still tight on an N305, but **realistic terminal/ASCII content
(the near-static row, which is what this project's actual Xvfb capture
looks like far more than full-motion `testsrc2`) fits an N305-class ~15W
chip with real margin.** The N100 doesn't clear 8 streams at either
resolution under this accounting — it was always closer to the edge than
[§3](#3-the-floor-minimum-viable-hardware) gave it credit for.

**Practical read**: dropping to 720p30 is a real, meaningful lever — it's
what actually makes an ~15W N305-class box a defensible answer to "the
least silicon that can encode 8 tubers," which 1080p30 does not support on
this size of chip once overhead is accounted for honestly. Above the N305
tier, any modern 6-8+ core desktop/mobile chip (the BD895i SE-class board
from earlier in this conversation, or the original 9700X-class pick) clears
either resolution with real margin regardless of this correction — the
tight numbers here only bind at the genuinely minimal end of the hardware
spectrum.

## 6. What a real 720p config would also change

This section only varied resolution (1920x1080 → 1280x720) to isolate its
effect — fps was already 30 in both
([config/worker.yaml:356](../../config/worker.yaml#L356)) so that wasn't a
variable. A real switch to 720p would normally also lower bitrate (Twitch's
own guidance targets roughly 2000-2500 kbps for 720p30, vs. 3000-6000 for
1080p) — not tested here, but bitrate has a much smaller effect on x264 CPU
cost than resolution does at `veryfast`/CBR-like settings (it mostly
changes quantizer/rate-control decisions, not the fixed per-frame
motion-estimation and transform work), so this would trim cost further but
not by anywhere near the resolution's own ~1.7-1.9x. Also unchanged by this
section: egress drops proportionally with bitrate if it's lowered (720p at
2000kbps ≈ 2.1 Mbps/stream vs. 1080p/3000kbps's 3.2 Mbps — a real win for
the egress side of [power_efficient_scaling_1_to_10_tubers.md](power_efficient_scaling_1_to_10_tubers.md),
not measured here since it's arithmetic, not a CPU question).

### Open items (this section)

- [ ] 1080p near-static concurrent case was estimated, not measured — worth
  a direct run to replace the estimate.
- [ ] None of this is against the real `x11grab` ASCII/terminal capture —
  same gap flagged in [§4](#4-what-this-doesnt-cover), now doubly relevant
  since the 720p verdict specifically hinges on real content behaving
  closer to "near-static" than "worst case."
- [ ] A real 720p+lower-bitrate config (not just resolution alone) hasn't
  been benchmarked — flagged, not assumed, per the note above.

---

## Related

- [power_efficient_scaling_1_to_10_tubers.md](power_efficient_scaling_1_to_10_tubers.md) — the whole-worker (not encode-only) picture this doc's numbers feed into; its §1 estimate is revised downward by the real measurement here
- [stream_supervisor.md](../stream_supervisor.md) — the ffmpeg broadcaster this doc benchmarks directly
- [../../app/stream_supervisor.py](../../app/stream_supervisor.py) — source of the exact encode flags used in every test above
