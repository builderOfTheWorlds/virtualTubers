# Full-Tuber Concurrent Capacity — Sweep Benchmark & Fleet Tool

> **Written**: 2026-08-09
> **Scope**: how many *complete* tuber pipelines — virtual desktop (Xvfb) +
> terminal render (xterm/tmux, same as `startup.sh`) + video encode + audio
> mixing — a given machine can sustain concurrently, and a reusable script
> to measure this on any machine in the fleet. Extends
> [encoding_only_minimum_cpu_8_tubers.md](encoding_only_minimum_cpu_8_tubers.md)
> (which isolated just the encode step) toward the whole pipeline.
> **Status**: the encode+audio-mix half is a real, measured concurrent
> sweep on `mk_workstation`. The virtual-desktop half (Xvfb/xterm/tmux) is
> **not yet measured** — blocked by a one-time root package install this
> session couldn't perform (no sudo password) — see [§4](#4-the-reusable-script)
> for the exact command and the tool that will complete this once it's run.

---

## TL;DR

- **Measured, real concurrent sweep** on `mk_workstation` (Ryzen 7 1700X,
  2017, 8C/16T) — encode + audio-mix only, current production flags:
  - **1080p30 / 3000 kbps**: clean through **N=8**, first stream starts
    lagging at **N=10**. Breaking point ≈ **8-9**.
  - **720p30 / 3000 kbps**: clean through **N=20**, degrading by **N=24**.
    Breaking point ≈ **20-22** — roughly **2.3-2.4x** more streams than
    1080p on the same chip, consistent with the per-stream ratios in
    [encoding_only_minimum_cpu_8_tubers.md §5](encoding_only_minimum_cpu_8_tubers.md#5-corrected-1080p-floor-and-the-720p-comparison).
- **This excludes Xvfb + xterm/tmux (virtual desktop/terminal render) and
  the Node.js agent loop** — real production components, not measured here
  because doing so needs root-installed packages this session couldn't get
  (details in [§4](#4-the-reusable-script)). **Reasoned estimate, not
  measured**: those add maybe 10-20% overhead on top of encode+audio (Xvfb
  and a terminal emulator are lightweight for text content; the agent loop
  is mostly I/O-bound waiting on network calls, not CPU-bound) — landing
  around **~7-8 real tubers at 1080p, ~17-19 at 720p** on this specific
  machine. Notably, **~7-8 at 1080p lines up with `docker-compose.yml`'s
  actual current worker count (8)** — a plausible coincidence, not proof.
- **A new script, [scripts/bench_full_tuber_capacity.sh](../../scripts/bench_full_tuber_capacity.sh),
  does the real (not estimated) version of this test** — real Xvfb, real
  xterm+tmux content, real PulseAudio null-sink mixing, sweeping N until it
  finds the breaking point automatically — and runs on **any machine in the
  fleet**, Linux-native or Windows-via-WSL, same script, same output
  format, so results are directly comparable across the fleet once run
  everywhere.

---

## 1. What was measured, and what's still a gap

| Component | Measured here? | How |
|---|---|---|
| Video encode (`libx264`, production flags) | ✅ Yes | Real concurrent sweep, `testsrc2` synthetic worst-case motion |
| Audio encode + mixing (`aac`, two-input mux) | ✅ Yes | Same sweep — every ffmpeg instance muxes a `sine` audio track alongside video, same as production muxes the PulseAudio capture alongside `x11grab` |
| Virtual desktop (Xvfb) | ❌ No | Needs `xvfb` package (root install) — see [§4](#4-the-reusable-script) |
| Terminal render (xterm + tmux, the actual content source) | ❌ No | Needs `xterm`/`tmux` packages (same blocker) |
| Real audio source (PulseAudio null sink + played narration, vs. this test's synthetic tone) | ❌ No | Needs `pulseaudio`/`pactl` (same blocker) |
| Node.js agent loop (message bus, LLM calls) | ❌ No | Out of scope for a CPU-sizing test — mostly I/O-bound, would need a real worker container running end-to-end |

The gap matters most for **§3**'s estimate below — everything in §2 is real,
measured data; §3 is reasoning, clearly labeled as such.

---

## 2. Sweep results (measured)

Method: `N` concurrent `ffmpeg` processes, each producing `testsrc2`
(worst-case full-motion synthetic video) + a `sine` audio tone, muxed
together and encoded with the exact production flags from
[stream_supervisor.py](../../app/stream_supervisor.py#L64-L83), real-time
paced (`-re`). Total CPU sampled via Windows `Get-Counter` during each
step. Machine: `mk_workstation`, Ryzen 7 1700X, 8C/16T, 95W TDP (2017).

### 1080p30 / 3000 kbps

| N | Mean speed | Min speed | Avg. total CPU% |
|---:|---:|---:|---:|
| 1 | 1.020x | 1.02x | 30.2% |
| 2 | 1.020x | 1.02x | 34.9% |
| 4 | 1.020x | 1.02x | 50.3% |
| 6 | 1.020x | 1.02x | 75.4%* |
| **8** | **1.020x** | **1.02x** | **63.5%** |
| **10** | **0.999x** | **0.974x** | **80.3%** — first stream starts lagging |
| 12 | 0.939x | 0.825x | 89.7% |
| 14 | 0.810x | 0.711x | 100% |
| 16 | 0.761x | 0.569x | 100% |

*N=6's 75.4% reading is higher than N=8's 63.5% — non-monotonic, almost
certainly sampling noise (background system activity during that
particular 16-second window) rather than a real effect. Flagged rather
than smoothed over.

### 720p30 / 3000 kbps

| N | Mean speed | Min speed | Avg. total CPU% |
|---:|---:|---:|---:|
| 8 | 1.020x | 1.02x | 57.0% |
| 12 | 1.020x | 1.02x | 57.9% |
| 16 | 1.020x | 1.02x | 65.1% |
| **20** | **1.020x** | **1.02x** | **73.5%** |
| **24** | **0.974x** | **0.57x** | 28.1%** — degrading |
| 28 | 0.960x | 0.544x | 76.6%** |

**The N=24/28 CPU% readings (28.1%, 76.6%) don't fit the trend and are
treated as measurement artifacts, not real steady-state figures — most
likely a "cold start" effect from launching 24-28 `ffmpeg` processes
nearly simultaneously (process-creation/filter-graph-init burst, possibly
Windows Defender scanning each new process launch) rather than genuine
sustained load. The **speed** figures at N=24/28 (clearly below 1.0x, with
min speed collapsing) are trusted — degradation is real — but the CPU%
number in those two rows should not be read as "only 28% loaded yet
failing," which wouldn't make physical sense. **Treat the 720p breaking
point as "between 20 and 24," not a precise number** — this project's own
convention (see [lessons_learned.md](../../../argyreServer/docs/hardware/ryzen_9/lessons_learned.md))
is to flag exactly this kind of noisy data rather than quietly average
past it.

---

## 3. Estimating the full picture (reasoned, not measured)

Xvfb, xterm, and tmux weren't in this sweep. Reasoning for why the gap is
probably modest, not dominant:

- **Xvfb** is a pure software framebuffer with no compositing/GPU work —
  for text content that redraws a few times a second (not video, not
  games), its own CPU cost per instance is typically small.
- **xterm** re-rendering monospace text is cheap — no complex layout,
  scaling, or shading, just glyph blits into the (virtual) framebuffer.
- **tmux** itself is negligible; it's a terminal multiplexer, not a
  renderer.
- **The Node.js agent loop** ([app/agent.py](../../app/agent.py) is
  actually Python, not Node — worth a note that the "Node 18" dependency
  referenced elsewhere in this project's docs is for the coding-backend
  workers specifically, not the base agent loop) spends most of its time
  waiting on the message bus and LLM API calls — network I/O, not CPU.

None of this is a substitute for measuring it. As a working estimate: **add
10-20% to the encode+audio-mix cost** to account for these components,
which on this machine's numbers puts real full-tuber capacity around:

| Resolution | Encode+audio ceiling (measured) | Estimated full-tuber ceiling |
|---|---:|---:|
| 1080p30/3000kbps | ~8-9 | **~7-8** |
| 720p30/3000kbps | ~20-22 | **~17-19** |

The 1080p estimate landing close to `docker-compose.yml`'s actual 8-worker
count is a notable, if unverified, coincidence — worth treating as a reason
to actually run the real test (§4) rather than as confirmation on its own.

---

## 4. The reusable script

[scripts/bench_full_tuber_capacity.sh](../../scripts/bench_full_tuber_capacity.sh)
drives the real components side by side — `Xvfb` → `xterm`+`tmux` (the
actual renderer pattern from `startup.sh`) → `ffmpeg x11grab` capture,
mixed with a `PulseAudio` null-sink audio source (the same `vout` pattern
`audio_player.py`/`stream_supervisor.py` use) — and sweeps `N` upward until
a step fails to sustain realtime, reporting the last passing `N` as that
machine's sustained capacity. Same script, same TSV output format, on every
fleet machine.

### One-time setup (per machine, needs root once — the script itself never calls `sudo`)

```bash
sudo apt-get update && sudo apt-get install -y \
    xvfb xterm tmux ffmpeg pulseaudio pulseaudio-utils
```

**This is the one blocker in this session** — a non-interactive agent can't
supply a sudo password, and shouldn't be asked to. Whoever has shell access
to a given machine runs this once; after that, the script runs unattended.

### Usage

```bash
./scripts/bench_full_tuber_capacity.sh                                  # default sweep, 1280x720
RES=1920x1080 BITRATE=3000 N_LIST="1 2 4 6 8 10 12" ./scripts/bench_full_tuber_capacity.sh
```

On a Windows box where WSL's Linux distro doesn't have `ffmpeg` installed
but the Windows install does, point at it directly through WSL interop
instead of installing a second copy (used for the §2 sweep above, for the
encode-only portion that doesn't need Xvfb):

```bash
FFMPEG=/mnt/c/Users/<you>/AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe \
  ./scripts/bench_full_tuber_capacity.sh
```

If any of `Xvfb`/`xterm`/`tmux`/`pulseaudio` are missing, the script
**falls back to proxy mode automatically** (synthetic `lavfi` sources, no
real virtual desktop) rather than failing outright — it prints which mode
it ran in, and proxy-mode numbers should be read as a **lower bound**, the
same caveat as §2-§3 above, until the real packages are installed.

### Rolling out across the fleet

Copy or `git pull` this script onto each fleet machine and run it once the
one-time setup above is done:

| Machine | OS | Notes |
|---|---|---|
| `mk_workstation` (this session) | Windows 10, via WSL2 Ubuntu | WSL needs the one-time install; `FFMPEG=` override works around the Windows/WSL split for the encode-only portion in the meantime |
| `d2000` | Windows, via WSL2 | Same pattern |
| `ashiorid` | Debian, native | Native Linux — no WSL split, install directly |
| `mafober` | Proxmox/Debian, native | **Deliberately skipped 2026-08-09** — SSH access confirmed working (root, key-based), but the box is live (CT 101/Portainer running Plex+Grafana+Prometheus+qBittorrent for the household), already memory-pressured (870MiB free, 762MiB in swap out of 11GiB at check time), and this benchmark's design intentionally saturates CPU to find the breaking point. Asked the user; chosen answer was to skip rather than risk visibly degrading live services on a box that's already documented as undersized and is being retired by the `argyreServer` fleet anyway — the marginal data point wasn't worth the risk. No packages were installed and no load was placed on the host; only read-only checks (`hostname`, `uptime`, `free -h`, `pct list`, tool presence) ran. |
| `argyreServer` boxes (once built) | Proxmox/Debian, native | Native Linux — not yet built, see `argyreServer/` |

Collect each machine's `bench_result_<hostname>_<timestamp>.tsv` output
file (written to the current directory when the script finishes) into one
place to compare fleet-wide capacity directly — same file format, same
methodology, everywhere.

---

## 5. Open items

- [ ] **Run the one-time `apt-get install` on `mk_workstation`'s WSL
  instance** (or any native Linux fleet box) and re-run the script in FULL
  mode — this is the single highest-value next step, since it replaces the
  §3 estimate with a real number.
- [ ] The N=24/28 720p CPU% readings need a re-run with a longer settle
  time before sampling starts, to rule out the process-launch-burst theory
  in [§2](#2-sweep-results-measured).
- [ ] `agent.py`'s real CPU footprint (message bus polling frequency, JSON
  parsing, any local processing) hasn't been profiled directly — the "I/O
  bound, small" reasoning in [§3](#3-estimating-the-full-picture-reasoned-not-measured)
  is plausible but unverified.
- [ ] No results yet from any other fleet machine — this doc only has
  `mk_workstation` data until the script is run elsewhere.

---

## Related

- [encoding_only_minimum_cpu_8_tubers.md](encoding_only_minimum_cpu_8_tubers.md) — the encode-only, single/small-N measurements this sweep builds on
- [power_efficient_scaling_1_to_10_tubers.md](power_efficient_scaling_1_to_10_tubers.md) — whole-worker CPU/RAM/power estimates this doc's real sweep data should eventually replace
- [../../scripts/bench_full_tuber_capacity.sh](../../scripts/bench_full_tuber_capacity.sh) — the reusable fleet-wide script
- [../../app/stream_supervisor.py](../../app/stream_supervisor.py) — source of the exact encode flags used in every test
- [../../startup.sh](../../startup.sh) — source of the real Xvfb/xterm/tmux boot sequence the script's FULL mode mirrors
