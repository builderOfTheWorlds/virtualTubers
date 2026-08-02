# CPU, Motherboard & RAM — Decision

> **Written**: 2026-08-02
> **Scope**: Compute platform for the dedicated build described in [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md). Supersedes that doc's original Ryzen 7 9700X pick.

---

## Decision

| Part | Pick | Price |
|---|---|---|
| CPU | AMD Ryzen 9 **9950X** (16C/32T, 170W) | bundled |
| Motherboard | ASUS **X870E Creator ProArt** | bundled |
| RAM (bundle) | 32GB DDR5-6000 | bundled |
| Bundle total | 9950X + X870E Creator ProArt + 32GB | **$1,099.99** |
| RAM upgrade | +1× matched 32GB kit → **64GB total** | ~$450-500 |

Micro Center's CPU+mobo+RAM bundle pricing was used throughout — bundles reliably beat buying the three parts separately by $150-350, even after the memory-shortage price hikes.

## Why 9950X, not the originally-planned 9700X

The original hardware doc sized the build around an 8C/16T 9700X for a narrow, fixed workload (6-8 workers + Kafka/Postgres/Redis + Ollama). That calculus changed once the box was reframed as a **general "main server"** — absorbing services currently split across `mafober` and other boxes, with more applications expected to land on it over time.

Thread count, not per-core speed, is the relevant lever for "more concurrent services," so the 9950X's 16C/32T (double the 9700X) was the right upgrade — not a 3D-cache chip (see below), which doesn't add threads.

| | 9700X | 9950X |
|---|---|---|
| Cores/Threads | 8C/16T | 16C/32T |
| TDP | 65W | 170W |
| Bundle price (32GB) | ~$700-730 | $899.99 (B850 board) / $1,099.99 (X870E board) |

Tradeoff accepted: TDP triples (65W → 170W), which raises the ongoing electricity cost estimated in the original hosting doc (~$15/month at 9700X-era power draw no longer holds once this box is running significantly more services regardless of which CPU is picked).

## Why X870E over B850

Two things pushed the pricier X870E Creator ProArt bundle ($1,099.99) over the cheaper B850 Creator ProArt ($899.99):

1. **PCIe slot count** — the box needs room for both the GPU and the reused LSI 9207-8i HBA card (see [storage.md](storage.md)) in separate full-length slots simultaneously. X870E boards typically carry 2-3 full-length PCIe slots; B850 boards are tighter.
2. Headroom for whatever else ends up needing a PCIe slot as the "main server" role grows (10GbE NIC, additional storage controller, etc. — see the future rack/networking notes in [case_chassis_rack.md](case_chassis_rack.md)).

## Ruled out: X3D / X3D2 variants

3D V-Cache chips (9800X3D, 9850X3D, 9950X3D2) were evaluated specifically because the box now runs a mixed bag of services rather than one narrow workload.

- **Plain X3D chips (9800X3D, 9850X3D)** — still only 8C/16T, same as the 9700X. The cache boost helps gaming/single-thread-latency workloads, not "more concurrent Docker services," which is a threads problem. Also carry an asymmetric-CCD design (one CCD has the cache, the other runs faster with no cache), adding scheduling complexity with no clear win for a pile of unrelated services. Passed on both.
- **9950X3D2** (dual V-Cache, no CCD asymmetry, 16C/32T, 200W, $899 MSRP) — genuinely interesting: 13% faster than plain 9950X in Blender (a real multi-threaded compute proxy, not just gaming), and cache-aware scheduling has shown real PostgreSQL throughput/latency gains — directly relevant since Postgres is in this stack. But the cheapest bundle available was **$1,349.99** (X870E only, no cheaper B850 pairing), a $450 premium over the plain 9950X for a ~10-15% gain, plus TDP climbs to 200W. Passed given the build was already over its original budget line from GPU/RAM price hikes; flagged as a legitimate upgrade if budget stops being the constraint.

## RAM sizing

Bundled 32GB (matches the tight sizing the original doc calculated for just the worker/Kafka/Postgres/Redis/Ollama stack) was extended to **64GB** via a second matched kit, since the "main server" role adds services (Plex, Grafana, Prometheus, Gitea runner, etc. — see [storage.md](storage.md) for what's migrating over from `mafober`) on top of the original sizing.

Buy the second kit **matched** to the bundle's kit rather than mixing brands/timings — and note a 4-DIMM-populated AM5 board forces a memory speed drop, a constraint already flagged in the original hosting doc.

---

## Related

- [gpu.md](gpu.md) — GPU pick, including the PCIe-slot-width interaction with the HBA
- [storage.md](storage.md) — the HBA this board needs to have a free slot for
- [case_chassis_rack.md](case_chassis_rack.md) — physical chassis this board goes into
- [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md) — original hosting decision (CPU pick has since evolved from that doc's 9700X)
