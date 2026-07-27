# Hardware & Hosting — Decisions and Analysis

> **Written**: 2026-07-26
> **Scope**: where virtualTubers runs, why, and what the LLM layer costs.
> Companion to [mafober_summary.md](mafober_summary.md) (that file covers one
> host in depth; this one covers the fleet and the hosting decisions).

---

## Decisions at a glance

| Question | Decision |
|---|---|
| Cloud host (AWS EC2)? | **No** — ~$1,100/month, egress-dominated |
| NVIDIA DGX Spark? | **No** — lower bandwidth than a consumer card, ARM, 10x price |
| Where does it run? | Dedicated Linux box, to be built (~$1,325) |
| Video encoding | **Switch to NVENC** — currently software x264, on hosts that own GPUs |
| Upload bandwidth | **Non-issue** — 274 Mbps up, needs ~19 |
| LLM for high-volume/low-stakes | **Local** (Ollama on GPU) |
| LLM for character/story quality | **Anthropic API**, model TBD by A/B test |

---

## Fleet inventory

| Box | Year | CPU | Cores | RAM | GPU / encoder | Role |
|---|---|---|---|---|---|---|
| **d2000** | 2022 | Ryzen 7 **7800X3D** | 8C/16T | 32 GB DDR5 | **RTX 3080 10 GB** | Primary workstation + gaming. Currently runs the whole stack plus Kafka/Postgres/Redis. **Off-limits long-term.** |
| **mk_workstation** | 2017 | Ryzen 7 1700X | 8C/16T | 32 GB | **RTX 3080 10 GB** | Daily driver + gaming. Currently the Ollama host (`100.37.208.112`). **Off-limits long-term.** |
| **ashiorid** | 2014 | Intel i5-4590 | 4C/4T | 8 GB | Intel HD 4600 (QuickSync) | 24/7 Debian. WireGuard site-to-site, HomeAssistant, qBittorrent, Portainer. Disk **94% full**. |
| **mafober** | 2012 | Intel **i3-2120** | 2C/4T | 12 GB | Sandy Bridge iGPU | 24/7 Proxmox. Plex, Grafana, Prometheus, Portainer LXC. Too weak for streaming; already flagged undersized. |
| *(storage)* | 2014 | Intel i3-4160 | 2C/4T | 8 GB DDR3 | Intel HD 4400 (QuickSync) | Unused. Useful as a RAM donor for ashiorid, or a zero-risk test bench. |
| *(shelf)* | 2014 | — | — | — | **EVGA GTX 970 4 GB** | Maxwell NVENC. Driver EOL. Emergency spare only. |

**Both daily drivers own an RTX 3080.** Freeing them for gaming is the driver
behind building a dedicated box.

---

## Why not cloud

Six workers stream 1080p30 at 3000 kbps continuously
([stream_supervisor.py](../../app/stream_supervisor.py), `build_ffmpeg_cmd`).
That is a 24/7 CPU-saturated video encoder with sustained outbound traffic —
the worst-fitting workload shape for cloud pricing.

### The egress math

```
3000 kbps video + 128 kbps audio + ~3% RTMP/TCP overhead ≈ 3.2 Mbps per stream
6 streams                                                 = 19.2 Mbps sustained
19.2 Mbps × 2,628,000 s/month ÷ 8                         ≈ 6,307 GB ≈ 6.3 TB
(6,307 − 100 free) × $0.09/GB                             = $559/month
```

| Line item | Monthly |
|---|---|
| c7i.4xlarge on-demand (16 vCPU, 32 GiB) | $521 |
| Data transfer out (6.3 TB) | **$559** |
| EBS gp3 ~200 GB | ~$16 |
| **Total** | **≈ $1,096** |

A 1-year Compute Savings Plan cuts the instance ~28% (→ ~$375). **Egress does
not discount.** Even with a free server this workload costs $559/month.

### Why the "our dev boxes are cheap" intuition doesn't transfer

| | Typical dev box | virtualTubers |
|---|---|---|
| Duty cycle | Stopped nights/weekends (~220 hr/mo) | 24/7 (730 hr/mo) |
| CPU profile | Idle ~5% | Pinned ~100% |
| Egress | A few GB/month | **6,300 GB/month** |

Also worth knowing: **burstable T-class instances are a trap under sustained
load.** A `t3.xlarge` (4 vCPU, 40% baseline) pinned at 100% bills surplus credits
at $0.05/vCPU-hour — $0.286/hr total, versus $0.178/hr for a `c7i.xlarge` with
faster dedicated cores. Burstable costs *more* and performs worse when you never
idle.

---

## The NVENC finding

[stream_supervisor.py:72](../../app/stream_supervisor.py#L72) hardcodes:

```python
"-c:v", "libx264",
"-preset", "veryfast",
```

That is **software encoding** — roughly 1 vCPU per worker, ~6 vCPU fleet-wide —
on hosts that own RTX 3080s. Moving to `h264_nvenc`:

- NVENC is a **separate fixed-function block**. It does not consume CUDA cores,
  so encoding does not slow Ollama inference. Only VRAM is shared
  (~100–200 MB per session).
- CPU requirement collapses from ~12–15 vCPU to ~4.

**Session limit — corrected.** NVIDIA's
[Video Encode and Decode GPU Support Matrix](https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new)
currently lists **12 concurrent sessions per GPU** for GeForce cards
("Unrestricted" on professional/datacenter parts). Widely-circulated figures of
3, 5, and 8 are all superseded — NVIDIA raises this silently and only updates
the matrix. **Verify against that page before relying on the number.**

At 12/GPU, NVENC is not the binding constraint; RAM is (~13 workers on 32 GB).

### Also needed

- `-preset veryfast` / `-tune zerolatency` are libx264-specific. NVENC uses
  `-preset p1..p7`, `-tune ll`, `-rc cbr`.
- NVIDIA Container Toolkit + a `deploy.resources.reservations.devices` block.
- **Make the codec config-driven** (`h264_nvenc` / `h264_qsv` / `libx264`) so a
  GPU-less host falls back cleanly and a broken driver update is survivable.

### Dead config

All six worker YAMLs set `stream.fps: 30`, but
[stream_supervisor.py:69](../../app/stream_supervisor.py#L69) hardcodes
`"-framerate", "30"` and `build_ffmpeg_cmd` takes no fps argument. **Nothing
reads that key.** It would need wiring before 1080p60 is reachable.

---

## Upload bandwidth — resolved

Speed test (2026-07-25, Newark server): **~920 Mbps down / ~274 Mbps up.**

| | Mbps | % of 274 |
|---|---|---|
| 6 streams @ 3000 kbps (current) | 19.2 | 7% |
| 6 streams @ 6000 kbps (Twitch practical max) | 37 | 13% |

Not a constraint. The ceiling is **Twitch's per-stream bitrate cap**, not the
pipe. Two free wins available: raise `video_bitrate_kbps` from 3000 to ~6000,
and consider 1080p60 (needs the `fps` wiring above).

---

## Proposed build — ~$1,325

| Part | Pick | Price |
|---|---|---|
| CPU + mobo + RAM bundle | Ryzen 7 **9700X** (8C/16T, **65 W**) + B650 + 32 GB DDR5-6000 | ~$550 |
| GPU | **RTX 5060 Ti 16 GB** | ~$450 |
| Storage | 2 TB NVMe | ~$120 |
| PSU | 650 W 80+ Gold | ~$80 |
| Cooler | Thermalright Peerless Assassin (**air, not AIO** — no pump to fail) | ~$35 |
| Case | Airflow-focused | ~$90 |

Micro Center bundles CPU+mobo+RAM at a significant discount; verify in store.

**Runs at ~110–140 W → ~$15/month.** Pays back against the EC2 figure in ~5 weeks.

Hosts everything: 6 workers, Kafka, Postgres, Redis, message-logger,
message-api, log-shipper, twitch-presence, and Ollama. Both RTX 3080s go back to
gaming duty.

### Sizing notes

- **~9 of 16 threads** at 8 workers (encoding offloaded to NVENC).
- **~20–24 GB of 32** RAM — the tighter constraint. Upgrade path is a 2×32 kit,
  *not* two more sticks (4 populated DIMMs on AM5 forces a speed drop).
- VRAM at 8 workers: ~6 GB Ollama + ~1.5 GB NVENC = **~7.6 of 16**.

### Alternatives considered

- **Reuse the shelf GTX 970** (~$875 total): viable for encoding — its weak
  quality-per-bit no longer matters now that bandwidth is free. But **4 GB
  cannot host Ollama**, so mk_workstation stays pinned, and the Maxwell driver
  branch (580) is end-of-life. Stopgap only.
- **Used RTX 3090 24 GB** (~$800): **936 GB/s** bandwidth vs the 5060 Ti's 448,
  plus 24 GB. Better for inference-heavy use; downsides are 350 W, no warranty,
  Ampere-generation NVENC. Worth reconsidering if the LLM work grows.

### Not viable

**mafober** (i3-2120, 2 cores) — has a real job, cannot encode.
**ashiorid** (i5-4590, 4 threads, 8 GB, 94% full disk) — has QuickSync and could
carry 1–2 workers after a free RAM upgrade from the storage box, but is a
stepping stone, not a destination. It is also an OptiPlex 9020 **SFF**:
low-profile slots and a ~240 W proprietary PSU with no PCIe power, so the GTX 970
physically will not fit.

---

## Rejected: NVIDIA DGX Spark

| | RTX 5060 Ti 16 GB | DGX Spark (GB10) |
|---|---|---|
| Memory | 16 GB GDDR7 | 128 GB LPDDR5X unified |
| **Bandwidth** | **448 GB/s** | **273 GB/s** |
| Price | ~$450 | **$3,999–$4,795** |
| CPU | x86 host | 20-core **ARM** (Grace) |

LLM generation is memory-bandwidth-bound, so the Spark is **~39% slower** on any
model that fits both. Its 128 GB only helps for models above 16 GB — but a 70b-q4
at 273 GB/s generates ~5 tok/s, meaning a 200-token line takes **~40 seconds**.
Capacity and bandwidth are mismatched for real-time dialogue.

Additionally: ARM64 breaks the x86 Docker stack (`vtube-worker` carries Node 18,
OpenCode, aider, piper-tts — all awkward on ARM), and NVENC concurrency on this
part is unverified.

It is a prototyping box for validating large-model workflows before deploying to
datacenter GPUs. Wrong tool here.

---

## LLM hosting

### Current

Ollama on mk_workstation's RTX 3080 (`100.37.208.112`), running
`qwen2.5:7b-instruct-q4_K_M`. Per-worker config selects the provider
(`llm.provider: ollama | claude`), so **backends can be mixed per worker with no
architecture change** — this is the key enabler for any hybrid.

### What fits on a 10 GB RTX 3080

Budget: 10 GB − ~0.7 GB desktop − ~0.5 GB CUDA ≈ **8.8 GB usable**.
Bandwidth **760 GB/s** — notably *faster* than the 5060 Ti for anything that fits.

| Model | Weights | Max context (q8 KV) | Speed | Verdict |
|---|---|---|---|---|
| `qwen2.5:3b-q4` | 2.0 GB | full 32K | ~180 tok/s | OK for minor roles |
| **`qwen2.5:7b-q4_K_M`** | **4.7 GB** | full 32K | ~90–110 tok/s | **Practical floor. Current.** |
| `qwen2.5:14b-q3_K_M` | ~7.0 GB | ~19K | ~65 tok/s | **Biggest that fits — worth testing** |
| `qwen2.5:14b-q4_K_M` | 9.0 GB | — | — | Won't fit (documented OOM) |

Two levers worth more than shrinking the model:

- **`OLLAMA_KV_CACHE_TYPE=q8_0`** roughly halves KV memory. On a 14b that is
  192 KB/token → 96 KB — the difference between ~9K and ~19K context.
- **Prefer a bigger model at lower quant** over a smaller model at higher quant,
  down to about q3. Below q3 quality falls off sharply.

On a 16 GB card, one model can serve many personas: set
`OLLAMA_NUM_PARALLEL=8` for shared weights plus per-slot KV (~230 MB/slot at 4K
on a 7b). Separate 7b copies per worker would cap at **2**.

### Anthropic API pricing (cached 2026-06-24 — re-verify before relying on it)

| Model | Input $/Mtok | Output $/Mtok | Context |
|---|---|---|---|
| Opus 5 (`claude-opus-5`) | $5.00 | $25.00 | 1M |
| Sonnet 5 (`claude-sonnet-5`) | $3.00 (**$2.00 intro to 2026-08-31**) | $15.00 (**$10.00 intro**) | 1M |
| Haiku 4.5 (`claude-haiku-4-5`) | $1.00 | $5.00 | 200K |

Prompt caching: reads ~0.1×, writes 1.25× (5-min TTL). Batch API: 50% off.

**A Claude Pro subscription does not include API access.** The API is separate
billing via [console.anthropic.com](https://console.anthropic.com). **Set a
monthly spend limit** — a worker stuck airing back-to-back would reach ~$2,600/mo
on Opus, and `handle_viewer_joined` queues a rerun per viewer join
(`PRESENCE_COOLDOWN_S` throttles greetings, not queuing).

### Cost of the current replay workload — measured

From the actual 45-episode library: **1,385 scenes**, median 22 scenes/episode
(mean 30.8), ~29K prompt characters per episode ≈ **8,400 input / 2,500 output
tokens per airing**.

| Model | Per airing | @ 1,800 airings/mo |
|---|---|---|
| Opus 5 | $0.10 | ~$190 |
| Sonnet 5 | $0.06 | ~$110 |
| Haiku 4.5 | $0.02 | ~$38 |

**With narration reuse enabled** ([narration_store.py](../../app/narration_store.py)):
45 episodes × one generation = **$4.73 one-time**. The reuse setting swings this
by three orders of magnitude.

### Cost of a D&D campaign — architecture dominates

Live turn-by-turn improv accumulates context quadratically: **$276–$1,200/month**
depending on model, and requires a long-context model for the GM.

**Scripted generation** (outline → two-pass expansion → perform) replaces long
context with a small curated story bible, and costs collapse:

| Config | Per 3-hr session | **Per month (1/day)** |
|---|---|---|
| All local (`7b` on a 3080) | $0 | **$0** — ~5 min to generate |
| All Haiku 4.5 | ~$0.20 | **~$6** |
| **Draft on Opus 5, voice pass on Haiku** | ~$0.47 | **~$14** |
| All Sonnet 5 | ~$0.60 | **~$18** |
| All Opus 5 | ~$1.05 | **~$32** |

**The architecture change is worth ~40x — far more than any model choice.** It
also makes Opus 5 affordable for story logic, which was never true live.

These are estimates from token modeling, not measurements. The replay figures
above *are* measured.

---

## Open items

- [ ] Verify the NVENC session limit on NVIDIA's support matrix before sizing
      past 8 workers
- [ ] Make the ffmpeg codec config-driven; wire `stream.fps`
- [ ] Raise `video_bitrate_kbps` 3000 → ~6000 (free quality, 13% of uplink)
- [ ] Decide 5060 Ti vs used RTX 3090 based on how heavy the LLM work becomes
- [ ] A/B local `qwen2.5` against Haiku 4.5 on real character dialogue, judged
      through the Piper pipeline — the one open question no benchmark answers
- [ ] Donate the storage box's 2×4 GB DDR3 to ashiorid (free 8 GB → 16 GB)
- [ ] Set an API spend cap before wiring any Claude-backed worker
- [ ] Add prompt caching to `ClaudeClient.complete()` — it currently sends no
      `cache_control`, which is the 4x cost difference on any long-context use
- [ ] `llm_client.py` defaults to `claude-opus-4-8`; current is `claude-opus-5`

---

## Related

- [mafober_summary.md](mafober_summary.md) — Proxmox host detail
- [../campaign_platform_build.md](../campaign_platform_build.md) — the platform work this hosts
- [../stream_supervisor.md](../stream_supervisor.md) — the ffmpeg broadcaster
- [../tts_client.md](../tts_client.md) — TTS providers and `TTS_BASE_URL` offload
