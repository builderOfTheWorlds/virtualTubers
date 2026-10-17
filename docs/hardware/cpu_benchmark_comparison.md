# CPU Benchmark Comparison — Price vs. Performance

> **Written**: 2026-08-03
> **Scope**: Real benchmark data (not spec-sheet extrapolation) across every CPU seriously considered for this build — [cpu_motherboard_ram.md](cpu_motherboard_ram.md) (AMD mainstream), [cpu_intel_and_hedt_pricing.md](cpu_intel_and_hedt_pricing.md) (Intel mainstream + HEDT). Produced to answer "what does the 9950X3D2 pick actually cost in performance-per-dollar terms," per this project's own [lessons_learned.md](lessons_learned.md) rule: don't trust a spec-sheet comparison, find a real benchmark.

---

## Method

**PassMark CPU Mark** (cpubenchmark.net) was used as the single consistent source across every CPU here, rather than mixing benchmarks — Cinebench 2024 multi-core scores exist for most of the mainstream chips but are patchy or unavailable for the HEDT/Xeon-WS tier, while PassMark covers all of them on the same scale. PassMark CPU Mark is a synthetic composite, not a perfect proxy for this build's actual workload (Docker/Postgres/Kafka services, not rendering) — treat it as a reasonable relative-value signal, not a literal predictor of this build's throughput.

Prices are CPU-only (no cooler, board, or RAM), pulled from Micro Center listings and PassMark's own tracked pricing, current as of 2026-08-03 — re-verify before relying on them for a purchase, per this directory's standing "prices move week to week" caveat.

---

## Mainstream desktop tier

| CPU | Cores/Threads | Price (CPU only) | PassMark CPU Mark | $ per 1,000 pts |
|---|---|---|---|---|
| Core Ultra 7 270K Plus | 24C/24T | $279.99 | 68,571 | **$4.08** |
| Ryzen 9 9950X | 16C/32T | $489.99 | 65,720 | $7.46 |
| Core i9-14900K | 24C/32T | $439.99 | 58,260 | $7.55 |
| Ryzen 7 9700X | 8C/16T | $279.99 | 36,969 | $7.57 |
| Core i7-14700K | 20C/28T | $349.99 | 51,957 | $6.74 |
| Ryzen 9 9950X3D *(plain, asymmetric cache — not evaluated for this build)* | 16C/32T | ~$630 (unverified, old listing) | 70,111 | ~$8.99 |
| Core Ultra 9 285K | 24C/24T | $469.99 | 46,872 | $10.03 |
| **Ryzen 9 9950X3D2 *(this build's pick)*** | 16C/32T | $869.99 | 72,638 | **$11.98** |
| Core i9-14900KS | 24C/32T | $669.99 | 59,991 | $11.17 |

## HEDT / workstation tier

| CPU | Cores/Threads | Price (CPU only) | PassMark CPU Mark | $ per 1,000 pts |
|---|---|---|---|---|
| Threadripper 9960X | 24C/48T | $1,299.99 | 92,459 | $14.06 |
| Xeon 654 | 18C/36T | $1,399.99 (MC — above the ~$1,199 list seen elsewhere, per [cpu_intel_and_hedt_pricing.md](cpu_intel_and_hedt_pricing.md)) | 61,353 | $22.82 |
| Threadripper 9970X | 32C/64T | $1,899.99 (docs) / $2,299.99 (current listing — moved) | 107,172 | $17.73–21.46 |
| Xeon 658X | 24C/48T | $1,999.99 | 73,490 | $27.21 |

---

## Takeaways

- **Core Ultra 7 270K Plus is the clear value leader** at $4.08/1,000 points — nearly 2x better $/performance than the 9950X. This confirms, with hard numbers, what [cpu_intel_and_hedt_pricing.md](cpu_intel_and_hedt_pricing.md) flagged qualitatively as "the one genuinely interesting result." It does not change that doc's recommendation to keep the 9950X path, though — no ECC-capable board price was ever checked for the Intel path, and switching platforms re-opens the settled AM5 board/RAM decisions in [cpu_motherboard_ram.md](cpu_motherboard_ram.md).
- **This build's actual pick, the 9950X3D2, has the worst $/performance of the entire mainstream tier** ($11.98/1k, worse than even the 14900KS). That's expected and was a deliberate trade, not an oversight — see [cpu_motherboard_ram.md](cpu_motherboard_ram.md#final-pick-9950x3d2-not-the-plain-9950x) for why it was kept anyway (Postgres cache-locality behavior a synthetic benchmark doesn't capture, plus the RAM-capacity path that made buying CPU/board unbundled the only option regardless).
- **HEDT tier confirms the "wrong tool for this build" conclusion already in [cpu_intel_and_hedt_pricing.md](cpu_intel_and_hedt_pricing.md)** — even the cheapest Threadripper is worse value than every mainstream chip except the 9950X3D2/14900KS, because HEDT pricing is paying for PCIe lanes and RDIMM channels this build doesn't use, not per-core throughput. See [gpu.md](gpu.md#future-gpu-expansion-2-year-outlook) for the specific, narrower conditions under which that lane/RAM-channel headroom would actually start to matter (a third full-bandwidth PCIe device, or RAM needs beyond 256GB).
- **Gap found, not yet acted on**: the plain **Ryzen 9 9950X3D** (16C/32T, single V-Cache CCD, asymmetric — distinct from the dual-cache 9950X3D2 actually bought) sits in both price and score right between the plain 9950X and the 9950X3D2. It was never evaluated in [cpu_motherboard_ram.md](cpu_motherboard_ram.md) — that doc only ruled out the 8C/16T "plain X3D" chips (9800X3D/9850X3D) and separately considered the dual-cache 9950X3D2. Worth a look if the 9950X3D2's premium ever becomes a concern; its ~$630 price is from an older listing and needs re-verification before treating it as current.

## Sources

PassMark [cpubenchmark.net](https://www.cpubenchmark.net/) per-CPU pages: Ryzen 9 9950X (id=6211), Ryzen 9 9950X3D (id=6549), Ryzen 9 9950X3D2 (id=7115), Ryzen 7 9700X (id=6205), Core Ultra 7 270K Plus (id=7114), Core Ultra 9 285K (id=6296), Core i7-14700K (id=5719), Core i9-14900K (id=5717), Core i9-14900KS (id=5957), Threadripper 9960X (id=6808), Threadripper 9970X, Xeon 654 (id=7110), Xeon 658X (id=7147). Micro Center listings for CPU-only pricing.

## Related

- [cpu_motherboard_ram.md](cpu_motherboard_ram.md) — the actual CPU/board/RAM decision this table was built to sanity-check
- [cpu_intel_and_hedt_pricing.md](cpu_intel_and_hedt_pricing.md) — the qualitative Intel/HEDT survey this table adds hard numbers to
- [gpu.md](gpu.md) — the future-GPU-expansion analysis that uses the same "verify with real data, not spec sheets" method
- [lessons_learned.md](lessons_learned.md) — the verification habit this whole doc follows
