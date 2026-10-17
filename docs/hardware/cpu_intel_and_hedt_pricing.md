# Intel & HEDT CPU Pricing — Survey

> **Written**: 2026-08-02
> **Scope**: Pricing/spec notes on Intel desktop and workstation CPUs, plus the HEDT (Threadripper / Xeon-WS) tier, measured against the Ryzen 9 9950X pick in [cpu_motherboard_ram.md](cpu_motherboard_ram.md). This is a survey, not a decision — the 9950X pick stands, with one contender flagged for follow-up.

> **Note on prior research**: no Threadripper pricing was ever written into [cpu_motherboard_ram.md](cpu_motherboard_ram.md) — that doc only covers AM5 (9700X, 9950X, X3D variants). The Threadripper and Xeon-WS numbers below were gathered fresh for this comparison and are sourced, not recalled.

---

## Intel consumer CPUs — Micro Center pricing (2026-08-02)

All prices are Micro Center in-store, CPU only (no cooler, no board, no RAM).

| SKU | Cores | Threads | Socket | List | MC price | Discount |
|---|---|---|---|---|---|---|
| Core Ultra 5 250KF Plus | 18C (6P+12E) | 18 | LGA 1851 | $229.99 | **$189.99** | −17% |
| Core Ultra 5 250K Plus | 18C (6P+12E) | 18 | LGA 1851 | $249.99 | **$209.99** | −16% |
| Core Ultra 7 270K Plus | 24C (8P+16E) | 24 | LGA 1851 | $349.99 | **$279.99** | −20% |
| Core i7-14700K | 20C (8P+12E) | 28 | LGA 1700 | $499.99 | **$349.99** | −30% |
| Core i9-14900K | 24C (8P+16E) | 32 | LGA 1700 | $699.99 | **$439.99** | −37% |
| Core Ultra 9 285K | 24C (8P+16E) | 24 | LGA 1851 | $649.99 | **$469.99** | −28% |
| Core i9-14900KS | 24C (8P+16E) | 32 | LGA 1700 | $749.99 | **$669.99** | −11% |

Open-box also listed: 285K from $390.96, 14900K from $373.96.

**Critical spec note — Arrow Lake has no Hyper-Threading.** Every LGA 1851 part above is *cores = threads*. Micro Center's "Twenty Four-Core" on both the 285K and the 14900K hides a 24T vs 32T difference. Raptor Lake (LGA 1700) still has HT on its P-cores.

### Pricing observations

- **Intel is discounting hard across the board; AMD is not.** Every Intel SKU here is 11–37% off list. The 9950X bundle in [cpu_motherboard_ram.md](cpu_motherboard_ram.md) showed no comparable discounting. Two things driving it: LGA 1700 inventory clearance (the two deepest cuts, 14900K and 14700K, are both dead-socket parts) and aggressive Arrow Lake Refresh positioning against Zen 5.
- **CPUs are one of the few parts in this build *not* inflated by the memory shortage.** The DRAM/GDDR price pressure documented in [lessons_learned.md](lessons_learned.md) and [gpu.md](gpu.md) hit RAM and GPUs, not CPU silicon. CPU prices moved *down* over this research period while RAM and GPU moved up.
- **Discount depth tracks socket death, not value.** The 14900K's −37% is the largest cut here and also the worst long-term buy (see the Raptor Lake reliability flag below).
- **The Xeon tier is not discounted at all** — and Micro Center's Xeon 654 at $1,399.99 is *above* the ~$1,199 list price seen elsewhere. Worth cross-checking before buying, per the [lessons_learned.md](lessons_learned.md) rule about single-listing trust.

---

## The one genuinely interesting result: Core Ultra 7 270K Plus

At **$279.99**, the 270K Plus lands within **~1–1.2% of the Ryzen 9 9950X in multithreaded benchmarks** (Cinebench 2024 nT), while also beating the $469.99 Ultra 9 285K by ~8% and the older 265K by ~22%.

That matters because the reasoning in [cpu_motherboard_ram.md](cpu_motherboard_ram.md) was explicitly *"thread count, not per-core speed, is the relevant lever."* The 270K Plus has 24 threads to the 9950X's 32 — and still ties it on real multithreaded throughput, because 16 E-cores make up what no-SMT gives away. This is the [lessons_learned.md](lessons_learned.md) "spec-sheet comparisons can point the wrong way" rule applying to CPUs, not just GPUs: thread count alone did not predict the outcome.

### But the price gap is smaller than it looks

The $1,099.99 AMD figure is a **bundle** (CPU + X870E Creator ProArt + 32GB DDR5). Comparing it to a bare $279.99 CPU is not apples-to-apples.

| Path | Components | Est. total |
|---|---|---|
| AMD (documented, real bundle price) | 9950X + X870E Creator ProArt + 32GB | **$1,099.99** |
| Intel (estimated, à la carte) | 270K Plus $279.99 + Z890/W880 board ~$250–400 + 32GB DDR5 ~$300–450 | **~$830–1,130** |

So the realistic delta is roughly **$0–270**, not $820. Micro Center does run Intel bundles (flagged on the 14700K and 14900K listings), which would likely close it further — **that bundle price has not been checked and is the actionable next step.**

### Where Intel would actually win for this build

- **ECC memory.** Arrow Lake supports ECC UDIMM on **W880** chipset boards (up to 256GB dual-channel). For a 24/7 "main server" running Postgres, that is a real advantage — the X870E Creator ProArt path has no validated ECC. This is the strongest non-price argument for Intel here.
- **Memory speed.** DDR5-7200 native on Arrow Lake vs. AM5's practical ~6000 sweet spot, and no 4-DIMM speed-drop penalty as severe as AM5's (the constraint already flagged for the 64GB upgrade in [cpu_motherboard_ram.md](cpu_motherboard_ram.md)).

### Where Intel loses

- **Dead-end socket.** LGA 1851 is confirmed to carry **only** Arrow Lake and its refresh — Nova Lake moves to LGA 1954 (which Intel plans to support across three generations). AM5 has the longer remaining runway. Weight this lightly: a server box rarely gets a drop-in CPU upgrade.
- **PCIe lanes are a wash, not a win.** Arrow Lake has 24 CPU lanes; the 9950X has 28 (24 usable). This build needs GPU x16 + the LSI 9207-8i HBA (see [storage.md](storage.md)); on both platforms the HBA runs off chipset lanes. Neither platform is comfortable here — that's the HEDT argument, below.
- **Board cost is unverified.** A W880 board (needed for ECC) is a workstation-class part, likely pricier than a consumer Z890, which erodes the savings.

---

## Ruled out: Raptor Lake (14900K / 14900KS / 14700K)

The 14900K at $439.99 with 24C/**32T** looks like the best threads-per-dollar on the list. **Do not buy it for a 24/7 server.**

13th/14th-gen Intel i9 parts carry the well-documented Vmin-shift / oxidation degradation problem — instability that develops over time under sustained load. Microcode 0x12B mitigates it, but always-on server duty is precisely the load profile that exposed it. Compounding: LGA 1700 is fully end-of-life, so there is no upgrade path off a chip that degrades. The steep discount is inventory clearance on a dead socket, not a bargain.

The 14700K ($349.99, 20C/28T) is a lesser version of the same problem — the degradation issue was concentrated in i9 parts, but the dead socket applies regardless.

---

## HEDT tier — Threadripper vs. Xeon-WS

Both are the "many PCIe lanes + ECC RDIMM + huge core count" tier. Prices are current retail, gathered 2026-08-02.

| Platform | CPU | Cores/Threads | CPU price | Board | Memory |
|---|---|---|---|---|---|
| AMD sTR5 (TRX50) | Threadripper 9960X | 24C/48T | $1,299.99 | TRX50 ~$700–1,100 | RDIMM required |
| AMD sTR5 (TRX50) | Threadripper 9970X | 32C/64T | $1,899.99 | TRX50 ~$700–1,100 | RDIMM required |
| Intel LGA 4710 (W890) | Xeon 654 (Granite Rapids-WS) | 18C/36T* | $1,399.99 (MC) | W890 ~$1,500–2,000 | RDIMM required |
| Intel LGA 4710 (W890) | Xeon 658X (Granite Rapids-WS) | 24C/48T | $1,999.99 (MC) | W890 ~$1,500–2,000 | RDIMM required |

\* 658X's 24C/48T is confirmed; the 654's thread count is inferred from Granite Rapids-WS having SMT — verify before relying on it.

**Verdict: both are out of scope for this build, by a wide margin.**

- Micro Center's Threadripper 9960X **3-in-1 bundle** (9960X + ASUS TRX50-SAGE Pro WS + 128GB DDR5-5600 ECC RDIMM) is **$5,299.99** (from $6,649.97 list). That single line item exceeds the entire **~$3,340–3,690** build total in [README.md](README.md).
- The Intel Xeon-WS path is worse on price: CPU + board alone is **~$3,400–4,000 before any RAM**, because W890 boards are $1,500–2,000. Intel's HEDT tier does not undercut Threadripper here — it's the more expensive of the two.
- What HEDT buys — 80–128 PCIe lanes, 8-channel RDIMM, 48–64 threads — this build does not need. It needs two PCIe cards (GPU + HBA) and 64GB. Paying a $4,000+ platform premium for lanes and capacity that go unused is exactly the wrong trade.
- Both platforms also carry higher idle power, which cuts against the low-power-24/7 premise already established in [case_chassis_rack.md](case_chassis_rack.md) for rejecting used dual-Xeon/EPYC boxes.

Revisit HEDT only if requirements change materially — multiple GPUs, 256GB+ RAM, or a real need for lane count.

---

## Summary

| Question | Answer |
|---|---|
| Does anything Intel beat the 9950X pick? | Not decisively. The 270K Plus ties it on multithreaded at a lower CPU price, but the platform delta is ~$0–270, not the ~$820 the CPU sticker suggests. |
| Best Intel value on the list | **Core Ultra 7 270K Plus, $279.99** — ~1% behind a 9950X multithreaded. |
| Best Intel-only argument | **ECC UDIMM support** via a W880 board — genuinely relevant for a 24/7 Postgres host. |
| Avoid | 14900K / 14900KS / 14700K — degradation risk on 24/7 duty + dead socket, discounts notwithstanding. |
| HEDT (Threadripper / Xeon-WS) | Out of scope. Cheapest realistic Threadripper platform (~$5,300 bundle) exceeds the whole build budget; Xeon-WS is worse still. |
| **Recommendation** | **Keep the 9950X.** The switch isn't justified on price alone, and re-speccing board + RAM re-opens settled decisions. Price the Intel bundle before finalizing only if ECC becomes a hard requirement. |

## Open items

- [ ] Get Micro Center's actual **Intel bundle** price for 270K Plus + Z890/W880 + 32GB — the à la carte estimate above is the weakest number in this doc.
- [ ] If ECC is wanted: price a specific W880 board and confirm which ECC UDIMM kits are on its QVL.
- [ ] Cross-check the Xeon 654's $1,399.99 Micro Center price against its ~$1,199 list elsewhere (single-listing discrepancy, per [lessons_learned.md](lessons_learned.md)).
- [ ] Re-verify all prices close to purchase date — CPU prices moved noticeably during this research window, same as GPU/RAM.

---

## Sources

- [Intel Core Ultra 7 270K Plus — Intel product specifications](https://www.intel.com/content/www/us/en/products/sku/245692/intel-core-ultra-7-processor-270k-plus-36m-cache-up-to-5-50-ghz/specifications.html)
- [Intel Core Ultra 7 270K Plus review — Tom's Hardware (productivity benchmarks)](https://www.tomshardware.com/pc-components/cpus/intel-core-ultra-7-270k-plus-review/3)
- [270K Plus / 250K Plus benchmarked vs 285K and 9950X3D — Neowin](https://www.neowin.net/reviews/review-intel-core-ultra-7-270k-plusultra-5-250k-plus-benchmarked-vs-285k-and-9950x3d/)
- [Intel Core Ultra 5 250K Plus review — Tom's Hardware](https://www.tomshardware.com/pc-components/cpus/intel-core-ultra-5-250k-plus-review)
- [ASUS Pro WS W880-ACE SE tech specs (ECC UDIMM support)](https://www.asus.com/motherboards-components/motherboards/workstation/pro-ws-w880-ace-se/techspec/)
- [Intel Nova Lake-S: LGA 1954 replaces LGA 1851 — Igor's Lab](https://www.igorslab.de/en/intel-nova-lake-s-the-next-socket-change-is-imminent-lga-1954-replaces-lga-1851/)
- [Leaked Intel document confirms multi-generation LGA 1954 socket plans — OC3D](https://overclock3d.net/news/cpu_mainboard/leaked-intel-document-confirms-multi-generation-lga-1954-cpu-socket-plans/)
- [AMD Threadripper 9970X — Micro Center](https://www.microcenter.com/product/698408/amd-ryzen-threadripper-9970x-shimada-peak-4ghz-32-core-str5-boxed-processor-heatsink-not-included)
- [Threadripper 9960X + TRX50 Aero D + 128GB build bundle — Micro Center](https://www.microcenter.com/product/5007200/amd-ryzen-threadripper-9960x,-gigabyte-trx50-aero-d-str5,-kingston-fury-renegade-pro-128gb-quad-channel-ddr5-5600-kit,-computer-build-bundle)
- [Intel Xeon 658X review — StorageReview](https://www.storagereview.com/review/intel-xeon-658x-review-24-cores-into-intels-new-workstation-platform)
- [Intel Xeon 600 Granite Rapids-WS pricing leak — Tom's Hardware](https://www.tomshardware.com/pc-components/cpus/intels-upcoming-xeon-granite-rapids-workstation-lineup-leaks-poised-to-challenge-amd-threadripper-with-usd8-300-86-core-flagship-retailer-lists-prices-ahead-of-ces-launch-starts-at-usd540)
- [ASUS W890 workstation motherboard, LGA 4710 — Wccftech](https://wccftech.com/asus-next-gen-w890-workstation-motherboard-for-intel-granite-rapids-ws-cpus-lga-4710-socket/)

## Related

- [cpu_motherboard_ram.md](cpu_motherboard_ram.md) — the AMD 9950X decision this survey was measured against
- [README.md](README.md) — full build summary and budget
- [lessons_learned.md](lessons_learned.md) — the verification rules applied here
- [storage.md](storage.md) — the LSI HBA driving the PCIe-lane discussion
