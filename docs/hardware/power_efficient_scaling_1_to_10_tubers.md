# Power-Efficient Scaling — Running 1 to 10 Tubers

> **Written**: 2026-08-09
> **Scope**: how CPU/RAM/VRAM/power cost scales as concurrent tuber (worker) count
> goes from 1 to 10, and what the *most power-efficient* way to host that is —
> independent of the `argyreServer` fleet build, which optimizes for "absorb
> mafober + add VMs + reuse owned hardware," not minimum watts. Companion to
> [environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md)
> (the source of most numbers reconciled below) and
> [argyreServer/docs/hardware/](../../../argyreServer/docs/hardware/README.md)
> (the fleet actually being bought).
> **Caveat that applies to every number in this doc**: nothing here is a
> measured (Kill-A-Watt / `docker stats`) reading. Every figure is reconciled
> from engineering estimates already on file, several of which disagree with
> each other by ~2x in the source doc — see [§1](#1-reconciling-the-existing-per-worker-estimates)
> for how they were reconciled and where the uncertainty is. Get a real
> measurement before sizing a purchase off this doc alone.
> **Partially superseded 2026-08-09**: the encode-only portion of the
> ~2.1 vCPU/worker figure in [§1](#1-reconciling-the-existing-per-worker-estimates)
> is now backed by a real measurement, not just estimate reconciliation —
> see [encoding_only_minimum_cpu_8_tubers.md](encoding_only_minimum_cpu_8_tubers.md).
> The measured encode-only cost (~0.4-0.6 vCPU-equivalent/stream) runs
> lower than the ~1.0 vCPU/worker estimate used below, which would revise
> the current-code whole-worker total down to roughly **~1.5-1.7 vCPU/worker**
> (the ~1.1 vCPU/worker Xvfb+Node base from this section is unchanged — only
> the encode add-on moved). The scaling table in [§2](#2-scaling-table-1--10-tubers)
> hasn't been recalculated against this yet; treat it as conservative
> (slightly overstated) until it is.

---

## TL;DR

- One worker = one Docker container = one Xvfb display + one Node.js agent
  loop + one ffmpeg RTMP encode ([docker-compose.yml:27-235](../../docker-compose.yml#L27-L235)
  defines `worker-1`..`worker-8` today; 9/10 are the same pattern, just not
  yet added to the compose file).
- **The current code (`stream_supervisor.py`, still CPU `libx264`, no NVENC —
  confirmed live 2026-08-09, see [§1](#1-reconciling-the-existing-per-worker-estimates))
  costs roughly 2 vCPU/worker.** At 10 tubers that's ~20 vCPU just for
  encode+render — more core count than almost any single power-sipping CPU
  offers, and the CPU cost (not RAM, not power) is the real ceiling on how
  far you can scale before you either add cores or add a hardware encoder.
- **Wiring hardware video encode (NVENC on an Nvidia GPU, or a config-driven
  equivalent) is the single highest-leverage change for both scaling and
  power draw** — it's already a documented open item
  ([hardware_and_hosting.md § Open items](../environment/hardware_and_hosting.md#open-items)),
  not new work this doc is inventing. It roughly halves per-worker CPU need
  and is what makes 10 concurrent tubers realistic on a small, efficient box
  at all.
- **Most power-efficient real option at this scale**: a small x86 box with
  one GPU with hardware encode (NVENC-class), running everything (workers +
  Kafka/Postgres/Redis + Ollama) — this is close to the *original*
  `hardware_and_hosting.md` ~$1,325 / ~110-140W proposal, not the much larger
  `argyreServer` fleet (which is optimized for absorbing other workloads, not
  minimum watts — see [§3](#3-hosting-options-ranked-by-actual-power-efficiency)).
- **ARM is a legitimate option for the orchestration + encode tier**, more so
  than the project's own prior ARM rejection note suggested — see
  [§4](#4-arm-feasibility). The weak link is local LLM inference on cheap ARM
  boards, not framework/OS compatibility. This project's own `argyreServer`
  fleet has already independently picked an ARM device (ASUS Ascent GX10,
  NVIDIA GB10 Grace Blackwell) for its LLM role, which is a real existing
  precedent worth reusing rather than re-litigating.

---

## 1. Reconciling the existing per-worker estimates

[hardware_and_hosting.md](../environment/hardware_and_hosting.md) contains
two CPU estimates for the fleet that don't agree with each other at face
value:

| Quote | Reading |
|---|---|
| "roughly 1 vCPU per worker, ~6 vCPU fleet-wide" (software `libx264` encode) | The **encode step alone**, in isolation |
| "CPU requirement collapses from ~12–15 vCPU to ~4" (moving to NVENC) | Reads as **whole-container** cost, before/after |
| "~9 of 16 threads at 8 workers (encoding offloaded to NVENC)" | Reads as **whole-container, NVENC-offloaded**, tied to a specific N |

Verified directly against the current code
([stream_supervisor.py:64-83](../../app/stream_supervisor.py#L64-L83)) as
part of this doc: `build_ffmpeg_cmd` still hardcodes `-c:v libx264 -preset
veryfast` with `x11grab` capture — no NVENC, no GPU rendering, confirmed
2026-08-09. So "current code" below always means CPU software encode.

**Reconciliation used in this doc** (arithmetic, not a new measurement):

- Whole-container overhead with encode *offloaded* (Xvfb + Node agent loop +
  TTS/audio + misc), from the "~9 of 16 threads at 8 workers" line:
  **≈ 1.1 vCPU/worker**.
- The isolated encode-step estimate: **≈ 1.0 vCPU/worker**.
- Current code (base + encode, both on CPU): **≈ 2.1 vCPU/worker** — and at
  6-7 workers that lands on ≈12.6-14.7 vCPU, which matches the "~12-15
  vCPU fleet-wide" figure well. This is likely what that number actually
  meant, even though it's filed under the "why move to NVENC" heading
  without being tied to a worker count explicitly.
- The "~4 vCPU" post-NVENC figure doesn't reconcile against "~9 of 16
  threads at 8 workers" (off by >2x even accounting for a smaller N) — it
  reads like an earlier, rougher pass that the more specific sizing note
  superseded without an explicit correction. **Flagged here, not resolved**
  — worth a real `docker stats` pull once any worker is measured.

RAM and VRAM are simpler — both are stated for the whole stack at a specific
N, not derived:

- **RAM**: ~20-24 GB of 32 GB at 8 workers — this is the **whole host**
  (8 workers + Kafka + Postgres + Redis + message-logger + message-api +
  log-shipper + twitch-presence + Ollama), not just the workers. Kafka/
  Postgres/Ollama's model weights are roughly fixed regardless of worker
  count; only the Node/Xvfb/ffmpeg buffers actually scale per-worker. No
  breakdown of that split exists on file — treat "~2-2.5 GB/worker marginal,
  ~6-8 GB fixed infra floor" as a plausible split, not a sourced one.
- **VRAM** (if colocating Ollama on the same GPU that also does NVENC):
  ~6 GB fixed (loaded model) + ~190 MB/worker (NVENC session) — this part
  *is* per-worker and small, which is why VRAM was never the binding
  constraint in the source doc's own analysis.
- **Network egress**: 3.2 Mbps/worker at the current 3000 kbps setting
  (measured math, [hardware_and_hosting.md § The egress math](../environment/hardware_and_hosting.md#the-egress-math)) —
  linear, exact, not an estimate. 10 tubers = 32 Mbps sustained upload, still
  a non-issue against the ~274 Mbps up measured for this connection.

---

## 2. Scaling table, 1 → 10 tubers

| Tubers | vCPU (current code, CPU `libx264`) | vCPU (NVENC/HW-encode wired in) | Egress (Mbps) | Notes |
|---:|---:|---:|---:|---|
| 1 | ~2.1 | ~1.1 | 3.2 | Fits on almost anything |
| 2 | ~4.2 | ~2.2 | 6.4 | |
| 4 | ~8.4 | ~4.4 | 12.8 | Current code starts to want a real desktop CPU, not a SoC |
| 6 | ~12.6 | ~6.6 | 19.2 | Matches the doc's own "6 concurrent streams, continuous" baseline |
| 8 | ~16.8 | ~8.8 (≈"9 of 16" cited figure) | 25.6 | `docker-compose.yml`'s actual current worker count |
| 10 | ~21.0 | ~11.0 | 32.0 | Two workers beyond what's defined today — same pattern, just copy-paste in `docker-compose.yml` |

Reading this table: **current code needs a genuinely big CPU (16+ threads)
to sustain 8-10 tubers without falling behind real-time encode.** Wiring a
hardware encoder is what turns "10 tubers" from "needs a 16-core desktop
chip" into "needs about the thread count of a mid-range 6-core chip," and
NVENC's ~12-session/GPU ceiling ([hardware_and_hosting.md](../environment/hardware_and_hosting.md#the-nvenc-finding))
isn't binding until well past 10 anyway. This reframes the priority: **the
video-encode TODO is not a nice-to-have, it's the single change that most
determines what "efficient hardware for 10 tubers" even means.**

---

## 3. Hosting options, ranked by actual power efficiency

Ranked for *minimum sustained watts to run 1-10 tubers specifically* — not
"minimum watts for the whole homelab," which is the `argyreServer` fleet's
actual optimization target and pulls in unrelated goals (Plex, VDI, VM
hosting, absorbing `mafober`). Numbers pulled from what's already measured/
estimated elsewhere in this repo and `argyreServer`, cited per row.

| Option | Typical draw | $/yr (supply, PSE&G rate) | Fit for 1-10 tubers | Source |
|---|---:|---:|---|---|
| **Small x86 + one NVENC-capable GPU** (e.g. the original 9700X + RTX 5060 Ti proposal) | ~110-140 W | **~$185-235** | Best real option today: NVENC covers all 10 tubers with headroom, same box hosts Kafka/Postgres/Redis/Ollama, small form factor | [hardware_and_hosting.md § Proposed build](../environment/hardware_and_hosting.md#proposed-build--1325) |
| **x86 CPU-only, no GPU, current code** | ~60-90 W idle-typical (platform only, no encode load) | ~$102-140 | Fine for **1-3 tubers**; CPU-bound encode caps out well before 10 (see [§2](#2-scaling-table-1--10-tubers)) — cheapest entry, worst ceiling | [ryzen_9/general_build_energy_costs.md § Scenario A](../../../argyreServer/docs/hardware/ryzen_9/general_build_energy_costs.md) (Scenario A, no GPU, as a stand-in platform-idle figure) |
| **`argyreServer` PowerEdge R740 + Tesla M10** (as currently planned) | 331 W Typical | **~$567** | Handles 6+ tubers via M10 NVENC dies **once the code is wired for it** — but the box exists for Postgres/LXC/VMs/Plex too, so this cost isn't attributable to tubers alone; carrying that overhead just to run tubers is the least efficient option here | [argyreServer/.../energy_costs.md](../../../argyreServer/docs/hardware/powerEdge/energy_costs.md) |
| **ARM SBC/mini-PC tier** (RK3588-class, HW H.264 encode wired in) | ~15-30 W (board-level, per unit) | **~$25-50** | Best watts/tuber *if* the encode path is ported (see [§4](#4-arm-feasibility)) — not a drop-in today | [§4](#4-arm-feasibility) below |

**Bottom line**: the general-purpose `argyreServer` fleet is the right call
for the homelab as a whole (it's absorbing several other workloads this doc
doesn't touch), but it is not the power-efficient answer to "how do I run
1-10 tubers specifically" — it costs roughly 2.5-3x the small dedicated
box's electricity for that slice of the workload, mostly from the dual-Xeon
chassis floor that has nothing to do with tubers. If minimum power for
*this project specifically* is the actual goal, the small single-GPU x86
box (or the ARM path below, once wired) beats the R740 plan on watts, even
though the R740 plan is the one being built for other reasons.

---

## 4. ARM feasibility

The project's only existing ARM verdict is a rejection, written narrowly
about the NVIDIA DGX Spark as an LLM box, not a general ARM feasibility
study:

> "ARM64 breaks the x86 Docker stack (`vtube-worker` carries Node 18,
> OpenCode, aider, piper-tts — all awkward on ARM)"
> — [hardware_and_hosting.md § Rejected: NVIDIA DGX Spark](../environment/hardware_and_hosting.md#rejected-nvidia-dgx-spark)

Checked directly rather than taken on faith (2026-08-09):

| Dependency | ARM64 status |
|---|---|
| Node 18 | Official multi-arch Docker images (`node:18`) include `linux/arm64` — no emulation needed |
| ffmpeg + `libx264` | Compiles and runs natively on ARM64; widely used on Raspberry Pi/ODROID for years. CPU-bound software encode either way, same as the current x86 code path |
| Piper TTS | Ships **prebuilt `linux_aarch64` binaries directly from the project's own releases** ([rhasspy/piper releases](https://github.com/rhasspy/piper/releases)) — this is one of the *more* ARM-native pieces of the stack, not an awkward one |
| Xvfb | Standard X.Org package, packaged for ARM64 Debian/Ubuntu same as x86 |
| Ollama | Official ARM64 builds; works, see performance caveat below |
| aider / OpenCode | Python/npm-distributed CLI tools, no compiled x86-only binaries — install the same way on ARM64 Linux |

**Verdict: the "breaks the x86 Docker stack" framing does not hold up on a
direct check.** Every dependency listed has a real ARM64 path. What's
actually true, and probably what the original note was reaching for, is
narrower: **cheap ARM boards are much weaker at both video encode and LLM
inference than the x86+GPU alternative**, which is a hardware capability
question, not a compatibility one.

### Hardware options checked (2026-08-09)

| Board | Encode | LLM inference | Power | Price | Verdict |
|---|---|---|---:|---:|---|
| Raspberry Pi 5 | **No hardware encoder** — decode-only acceleration; software encode via 4 CPU cores caps around 1 stream at 1080p | Too weak to be worth running | <10 W | ~$80 | Not viable past 1 tuber |
| RK3588-class SBC/mini-PC | **Real hardware H.264/H.265 encoder, up to 8K30** on the VPU block | Weak: ~3-7 tok/s on a 7-14B model (real benchmarks, not vendor-claimed) | ~10-20 W | ~$150-250 | **Best fit for the encode/orchestration tier** — needs code work (see below), not for local LLM |
| Jetson Orin Nano (Super, 2026 price) | **No NVENC on this SKU** — software encode only, ~3x 1080p30 streams via CPU | Real GPU (1,024 CUDA cores) but only 8GB RAM shared CPU+GPU | ~15-25 W | $249 | Encode ceiling too close to Pi 5's to be worth the price jump for this workload |
| ASUS Ascent GX10 (already selected for this fleet's LLM role) | N/A — not an encode box | 128GB unified memory, real CUDA/Blackwell throughput | 240 W (whole unit) | $3,999.99 | Already the fleet's ARM answer for LLM — reuse it over the network rather than duplicating LLM elsewhere |

Sources: [RK3588 vs Raspberry Pi 5 comparison](https://ieeker.com/rk3588-vs-raspberry-pi-5/), [XDA — Raspberry Pi as a transcoder](https://www.xda-developers.com/modern-raspberry-pi-surprisingly-powerful-transcoder/), [Turing Pi — Ollama/llama.cpp on RK3588](https://turingpi.com/run-llm-locally-arm-rk3588-ollama-llama-cpp/), [NVIDIA Technical Blog — Jetson Orin Nano Super](https://developer.nvidia.com/blog/nvidia-jetson-orin-nano-developer-kit-gets-a-super-boost/), [edgeaistack.ai — Jetson Orin Nano power](https://edgeaistack.ai/blog/jetson-orin-nano-power-consumption/), [Piper releases](https://github.com/rhasspy/piper/releases).

### The actual recommendation: hybrid, not monolith

The worker architecture already assumes LLM inference can be remote —
`llm.provider: ollama` + `LLM_BASE_URL` is a network call today, pointed at
a completely separate box (`mk_workstation`'s 3080). That same pattern is
the right shape for an ARM deployment:

1. **Orchestration + encode tier on ARM** (RK3588-class board or a small
   cluster of them): Kafka/Postgres/Redis + N worker containers, each doing
   Xvfb + Node agent + ffmpeg. At the ~15-20 W/board range and real hardware
   H.264 encode, this is the most power-efficient way to run the tuber count
   itself — genuinely better watts/tuber than any x86 option in
   [§3](#3-hosting-options-ranked-by-actual-power-efficiency).
2. **LLM inference stays off-board**, served over the network — either the
   already-selected ASUS Ascent GX10 (itself ARM, so this isn't even a
   cross-architecture compromise) or a shared x86+GPU host. Don't try to run
   Ollama locally on the encode board; the benchmark data above (3-7 tok/s)
   makes that a bad trade at any tuber count above 1.
3. **Requires real code work first**: the codec has to become config-driven
   the same way the NVENC TODO already calls for
   ([hardware_and_hosting.md § Also needed](../environment/hardware_and_hosting.md#also-needed)),
   extended to cover `h264_v4l2m2m` / Rockchip MPP instead of (or alongside)
   `h264_nvenc`. This is the same shape of change either way — "ARM" doesn't
   add new engineering scope beyond what NVENC support already requires,
   it's the same abstraction serving a second hardware target.

### Open items

- [ ] No real ARM benchmark exists for **this specific ffmpeg command**
  (`x11grab` capture → hardware H.264 encode) on RK3588 — the sources above
  cover generic transcoding, not this exact capture-and-encode pipeline.
  Worth a cheap single-board test before committing.
- [ ] Consumer-accessible Ampere Altra-class boards (many-core ARM server
  chips, which would let one board host all 10 workers' base Xvfb/Node
  overhead without clustering) were not found at a clearly-priced, readily
  available tier during this pass — flagged as open rather than assumed
  unavailable.
- [ ] Docker orchestration across multiple small ARM boards (if one RK3588
  board can't hold all 10 workers' Xvfb+Node overhead — untested) would need
  Swarm/k3s, which is real added operational complexity this doc hasn't
  costed out.
- [ ] Get a real power measurement on at least one worker container under
  the current CPU-encode path — every vCPU/watt figure in this doc traces
  back to estimates, several of which (see [§1](#1-reconciling-the-existing-per-worker-estimates))
  already disagree with each other.

---

## 5. Real product evaluated: Ryzen HX mini-ITX boards (2026-08-09)

User-sourced spec sheet for two mini-ITX barebone boards (product names as
given: **BD895i SE** / **BD775i SE**) — evaluated specifically for the
orchestration+encode tier, with LLM generation assumed fully offloaded to
the already-selected [ASUS Ascent GX10](../../../argyreServer/docs/hardware/asus_ascent_gx10/README.md)
over the network (no local Ollama, so no VRAM/RAM budget for a model on
these boards at all).

| Spec | BD895i SE | BD775i SE |
|---|---|---|
| CPU | Ryzen 9 **8945HX**, 16C/32T, 2.5-5.4 GHz | Ryzen 7 **7745HX**, 8C/16T, 3.6-5.1 GHz |
| TDP | 100W (120W boost) | 100W |
| iGPU | Radeon 610M (RDNA2, 2 CU — weak for compute, has a VCN video-encode block) | Same |
| RAM | DDR5 SO-DIMM ×2, up to 96GB @ 5200MHz | Same |
| Storage | 2× M.2 NVMe (PCIe 4.0×4, up to 4TB each) | Same |
| Expansion | 1× PCIe 5.0 x16 (room for a discrete NVENC GPU later if ever wanted) | Same |
| Network | 2.5GbE | Same |
| Power | Needs an ATX PSU, >200W (barebone board, not an all-in-one — case/PSU/cooler/RAM/storage all sourced separately) | Same |

These are **x86, not ARM** — a mobile "HX" chip (laptop silicon) on a
desktop mini-ITX board, a genuinely different tier from the RK3588 SBC
option in [§4](#4-arm-feasibility): much more CPU, much higher power floor,
no ARM compatibility questions at all (it's the same x86_64 stack already
running today).

### Capacity math (thread-count reasoning, not a benchmark)

Using the same ~2.1 vCPU/worker (current CPU `libx264`) / ~1.1 vCPU/worker
(hardware-encode-offloaded) figures from [§1](#1-reconciling-the-existing-per-worker-estimates):

| Board | Threads | Workers @ CPU-only encode (current code, **zero code changes**) | Workers @ HW-encode offloaded |
|---|---:|---:|---:|
| BD895i SE (8945HX, 32T) | 32 | **~15** | ~29 |
| BD775i SE (7745HX, 16T) | 16 | **~7-8** | ~14 |

**The headline finding: a single BD895i SE covers the entire 1-10 tuber
target on the current, unmodified codebase** — no VCN wiring, no NVENC
detour, no code change at all. That's not true of anything else evaluated
in this doc; every other option either needed a hardware-encode change to
reach 10, or couldn't reach 10 at all. The BD775i SE is right at the edge
(7-8 max) — it would need the hardware-encode TODO done to comfortably
clear 10.

RAM is not a constraint at all here: 96GB against a whole-stack estimate of
roughly 10-15GB (Kafka+Postgres+Redis+10 workers, **lower than the ~20-24GB
figure used elsewhere in this doc because that figure included local
Ollama's model weights — irrelevant here since LLM is fully offloaded to
GX10**).

### The real caveat: encoder quality, and whether you even need it

AMD's VCN hardware H.264 encoder (`h264_vaapi` on Linux, `h264_amf` on
Windows) has **no artificial concurrent-session cap** (unlike older NVENC
consumer limits) — checked 2026-08-09, see
[Jellyfin's AMD hardware-acceleration doc](https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/amd/).
But it has a real, documented **quality** weakness: AMD's H.264 hardware
encoder has historically lacked B-frame support, which hurts compression
efficiency at a fixed bitrate compared to `libx264` or NVENC — worth a
side-by-side quality check against the current 3000 kbps setting
([config/worker.yaml:353](../../config/worker.yaml#L353)) before routing
encode through it. **Given the CPU-only column above already clears 10
tubers with real headroom, the simplest path on this board is to change
nothing** — keep software `libx264` and skip the hardware-encode question
entirely, since the thread count (not the encoder) was always the actual
constraint on smaller CPUs.

### Power reality check — why "3 boards" isn't automatically 3x more efficient

Sustained real-world draw isn't in vendor listings for this board (no
measurement on file, same caveat as everywhere else). Reasoning from the
100W CPU TDP and a mobile-chip idle floor:

| Scenario | Est. package/platform draw | Why |
|---|---:|---|
| 1 board idle, no workers | ~25-40W | Mobile-derived Zen4 idles low; board+RAM+NVMe adds a modest floor |
| **1 board running all 10 tubers** (≈66% of 32 threads) | **~120-150W** | HX chips run close to rated TDP under sustained multi-thread load — this is a 24/7-pinned workload, same "not bursty" pattern [hardware_and_hosting.md](../environment/hardware_and_hosting.md#why-not-cloud) already flags as the worst-fitting shape for assuming quiet/idle numbers apply |
| **3 boards, ~3-4 tubers each** (≈22% utilization each) | **~40-55W each → ~120-165W total** | Comparable *total* wattage to the single fully-loaded board — each board still pays its own idle floor, so splitting the load doesn't clearly win on raw watts |

**The 3-board case is not obviously more power-efficient than 1 board** —
it's roughly a wash on watts, because three idle floors add up. What 3
boards actually buys is **redundancy** (one board's failure/reboot doesn't
drop every stream), **thermal/component headroom** (each board runs cooler,
easier cooling in a small mini-ITX case), and **room to grow past 10 or run
other roles concurrently** (e.g. the `coder-native`/`coder-opencode`/
`coder-aider` A/B-test workers alongside tuber streaming without resource
contention). None of those are "more watts for the same job" — they're
different goals than pure power efficiency, worth being explicit about
before buying 3.

**If the goal is strictly "run 1-10 tubers for the least power/cost"**: one
BD895i SE (or even one BD775i SE with the hardware-encode TODO done) is
enough, with the current code, unmodified. **If the goal includes
redundancy, role separation, or headroom for other workloads**, 3 boards is
a reasonable, well-justified buy — just not for the "efficiency" reason
alone.

### Open items

- [ ] No measured power draw exists for this board family under sustained
  multi-thread load — the 120-150W single-board estimate above is TDP-
  derived, not measured.
- [ ] AMD VCN H.264 output quality at 3000 kbps, `h264_vaapi` on Linux, not
  benchmarked against the current `libx264 veryfast` baseline — needed only
  if the CPU-only headroom ever gets used up and hardware encode becomes
  necessary.
- [ ] These are barebone boards — full build cost (case, ATX PSU ≥200W,
  cooler rated for a 120W-boost mobile chip in a small mini-ITX chassis,
  RAM, NVMe) isn't priced here; board price alone understates the real
  per-unit cost, same lesson [lessons_learned.md](../../../argyreServer/docs/hardware/ryzen_9/lessons_learned.md)
  already documents for other builds in this fleet.

---

## Related

- [environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md) — source of the per-worker CPU/RAM/egress estimates reconciled in [§1](#1-reconciling-the-existing-per-worker-estimates)
- [stream_supervisor.md](../stream_supervisor.md) — the ffmpeg broadcaster this doc's encode-cost numbers are about
- [../../../argyreServer/docs/hardware/README.md](../../../argyreServer/docs/hardware/README.md) — the fleet actually being bought (general-purpose, not tuber-power-optimized)
- [../../../argyreServer/docs/hardware/asus_ascent_gx10/README.md](../../../argyreServer/docs/hardware/asus_ascent_gx10/README.md) — the ARM device already selected for this fleet's LLM role, reused as precedent in [§4](#4-arm-feasibility)
