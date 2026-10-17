# GPU — Decision and Survey

> **Written**: 2026-08-02 · **Updated**: 2026-08-02 — pivoted from the RTX 5070 Ti to the RTX 5080 tier. Micro Center raised the price on the 5070 Ti pick (844035), closing most of the gap to the 5080 tier, and the case-clearance constraint that had made the 5070 Ti's slim 2-slot width decisive was separately relaxed (confirmed room for a bulkier card). See "Why the 5080 now" below.
> **Scope**: GPU selection for the dedicated build described in [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md). Supersedes that doc's original RTX 5060 Ti pick.
> Store: Micro Center, North Jersey. **Prices/stock are a snapshot** — GDDR pricing was moving week to week during this period from a broader memory shortage; re-verify before buying.

---

## Decision

| SKU | Card | VRAM | Price | Notes |
|---|---|---|---|---|
| **805440** | **Gigabyte RTX 5080 WINDFORCE SFF OC Triple Fan** | 16GB GDDR7 | **$1,199.99** (orig $1,399.99) | 960 GB/s, 10,752 CUDA cores, 300W. Confirmed 25+ in stock at North Jersey. MC in-store rating 4.8/5, independently corroborated by Amazon (4.6★, 270+ reviews). |

Previous pick, superseded: **844035** — Zotac RTX 5070 Ti SOLID SFF OC, $929.99 at time of writing. Micro Center raised this price (new figure not confirmed — re-verify before treating it as dead), and once size stopped being a constraint the 5070 Ti's main advantage (slim 2-slot width) no longer mattered. See the full 5070 Ti catalog below — still valid reference if the 5080 pricing moves back out of range.

### Why the 5080 now, and not the 5070 Ti

- **The 5070 Ti vs 5080 math hasn't changed**: same 16GB VRAM, the 5080 offers only ~7% more bandwidth (960 vs 896 GB/s — the actual bottleneck for Ollama's memory-bandwidth-bound token generation) but ~20-28% more compute, which matters more for image-gen than LLM decode. That premium wasn't worth paying when the 5070 Ti was $929.99 against the 5080's $1,199.99 — a $270 gap for a mostly-irrelevant compute bump.
- **What changed**: Micro Center's price increase on 844035 narrowed that gap, and the case-clearance issue that had ruled out non-SFF cards (needed 2-slot width to clear a reused LSI HBA card) was separately resolved — confirmed room for a bulkier card. With the SFF requirement gone, the cheaper/better-reviewed cards across the whole 5080 catalog became fair game, and at near-parity pricing the 5080's extra headroom wins outright.

### Why 805440 specifically, out of the full RTX 5080 catalog

- **805440 (Gigabyte WINDFORCE SFF OC, $1,199.99)** — cheapest and best-stocked card in the entire 5080 lineup (25+ units). Its 4.8/5 in-store rating is independently corroborated by Amazon (4.6★ across 270+ reviews) — real owners report 60-63°C load temps, quiet operation, and ~13% OC headroom for a ~5% real-world gaming gain. Known gripes (startup coil whine, fan-bearing rattle, a lighter/plastic-leaning chassis with some sag risk) are minor and don't matter much for a headless serving box.
- **Zotac Solid Core OC (985135, $1,249.99)** — the strongest alternative. GamersNexus independently benchmarked it as the 5080 tier's best value: GPU 61°C / VRAM 64-65°C (beats Founders Edition), 31.1dBA, 3-6% faster than FE. No consumer review track record yet (too new) — the "best-measured, least-proven" option. Worth the $50 premium only if comfortable being an early adopter.
- **MSI SHADOW 3X OC (803015, $1,549.99)** — ruled out. Micro Center shows a 4.9/5 in-store rating, but independent Walmart reviews (4.3★, 20 reviews) tell a different story: owners call it "Ventus OC with a cheaper shroud," no metal backplate, not worth $1,549 against its own ~$999 MSRP tier. Takeaway: treat Micro Center's own in-store star ratings as directional, not gospel, especially on lower-review-volume SKUs — cross-check against an external source before trusting one.
- **Everything above ~$1,300** (Gaming OC, AERO SFF, the ASUS Prime → ROG Astral ladder up to $2,149.99) — no rating or thermal data found that justifies a $350-950 premium over 805440 for identical GB203 silicon.
- The rest of the alternatives-considered reasoning below (5060 Ti, AMD R9700, Intel Arc B580, Chinese cards, RTX 5090) is unchanged from the original 5070 Ti analysis — none of it was specific to the Ti vs. non-Ti choice.

### Why not the other tiers (unchanged from the original 5070 Ti analysis)

- **RTX 5060 Ti (original plan)** — rejected for bandwidth: 448 GB/s vs. 896-960 GB/s in the 5070 Ti/5080 tier. LLM token generation is memory-bandwidth-bound, so this was the lever that mattered most for Ollama serving.
- **AMD Radeon AI PRO R9700** (32GB GDDR6, $1,249-1,359 depending on AIB) — explored in real depth over "would double VRAM let me run bigger models." Answer, from a detailed hands-on review (llama.cpp + ComfyUI head-to-head, real benchmark commands and output, not marketing): **no** — the 5070 Ti won most real inference and image-gen tests despite having half the VRAM, because the R9700's bandwidth (644.6 GB/s) is well below even the 5070 Ti, and its ROCm software stack still has real rough edges (it lost to the generic Vulkan backend on one test; there's a live PyTorch-ROCm bug that makes first-run image gen take ~7 minutes). Bigger VRAM only reliably wins for workloads without a CPU-offload escape hatch — fine-tuning and 3D rendering were the reviewer's own suggested exceptions, not inference. The 5080's bandwidth lead over the R9700 is even larger, so this conclusion only strengthens with the pivot.
- **Intel Arc B580** ($299.99, 12GB, 456 GB/s) — good value in isolation, but priced against the 5060 Ti tier, not this one, and Ollama's own Intel support is thin (Level Zero, Linux-only, fewer quant options). The real Intel path is llama.cpp + IPEX-LLM directly, not Ollama. Considered as a cheap side-project box for learning that stack, not for production.
- **"Chinese cards"** — domestic chip makers (Moore Threads, Biren, Huawei Ascend) aren't practically purchasable in the US (export-restricted, Chinese-market-first, immature non-English tooling). Modded 48GB RTX 4090s from Chinese resellers are real but run ~$3,400 — more than a stock 5090 — for a hand-soldered, non-warrantied board. Neither beats the 5080 pick.
- **RTX 5090 / RTX PRO Blackwell** — ruled out on budget alone; even the cheapest 5090 variant would have consumed nearly the entire GPU budget.

### Physical/thermal notes (no longer decision-relevant, kept for build reference)

- Size was originally a hard constraint — needed a 2-slot card to clear a reused LSI HBA card in an adjacent slot. That constraint was later relaxed (confirmed room in the case), which is part of why the 5080 tier opened up. Worth a physical check once the motherboard and GPU are both in hand regardless.
- 5080 (Gigabyte WINDFORCE SFF, 805440): marketed as an SFF-class cooler; exact slot width/mm not confirmed in this pass — verify against Gigabyte's spec page before finalizing case layout. 850W PSU recommended.
- 5070 Ti (Zotac SOLID SFF, 844035): **dual-slot, 41.6mm width, 116.84mm tall**, 750W PSU recommended, ships with a dual 8-pin→16-pin adapter. Kept for reference since 844035 is still the fallback if 5080 pricing moves back out of range.
- One data inconsistency caught on the Zotac 5070 Ti listing: "PCIe Bandwidth: PCIe 5.0 x16" and "Interface: PCIe 4.0" appear on the same page, contradicting each other. Treated as a Micro Center template error (the GB203 die is native PCIe 5.0), not a real spec limitation — and wouldn't matter functionally either way, since PCIe generation isn't the bottleneck for this workload.

---

## Second GPU: GTX 970, added for VM passthrough (2026-08-03)

> **Decision**: reuse the existing spare **EVGA GTX 970 4GB** (see the fleet table in [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md)) as a dedicated passthrough GPU for a Windows VM, rather than buying anything new.

### Why

The build added a requirement for at least one Windows and one Linux GUI VM (general-purpose use — VS Code, docs, browsing, connected to over LAN via RDP — explicitly not gaming or GPU compute, which stays on the user's separate gaming machines). The single RTX 5080 is already committed via passthrough to Ollama inference + Plex NVENC transcode on the host ([storage.md](storage.md)); handing it to a VM instead would take it away from both while the VM holds it. A second, separate card sidesteps that conflict entirely and costs nothing, since it's already-owned spare hardware.

### Why it fits the platform cleanly

- **Slot**: the AORUS MASTER X3D ICE's second full-length slot (**PCIEX8**) takes the 970, running x8 electrical alongside the 5080 (which also drops to x8 once slot 2 is populated) — see [cpu_motherboard_ram.md](cpu_motherboard_ram.md#why-aorus-master-x3d-ice-not-creator-proart) for the confirmed slot layout. x8 is far more bandwidth than a 2014 Maxwell card can use, and the 5080 dropping from x16 to x8 doesn't matter for Ollama (VRAM-bandwidth-bound, not host-PCIe-bound — same reasoning as the rest of this doc). The LSI HBA takes the third slot (PCIEX4), completing a 3-card, 3-slot layout with nothing left spare.
- **Power**: the 970's ~145W TDP adds comfortably to the existing ~555-600W single-GPU draw already sized in [power_supply.md](power_supply.md) — nowhere near the PSU headroom problem a second 5080 (~300W) would have caused.
- **Driver support is healthier than this project's own fleet notes assumed.** [hardware_and_hosting.md](../environment/hardware_and_hosting.md) previously flagged the 970 as "Driver EOL, emergency spare only." Verified: NVIDIA's last Game Ready driver for Maxwell shipped October 2025, but the architecture then moves to **quarterly security updates through October 2028** — well past any reasonable planning horizon for a VS Code/docs/browsing VM, which doesn't need Game Ready features anyway. One real constraint: NVIDIA is dropping Windows 10 driver builds after October 2026, so the Windows VM needs to be **Windows 11**.
- **Known, solved passthrough gotcha**: consumer GeForce drivers historically detect they're running in a VM and refuse to initialize ("Code 43"). Standard, well-documented Proxmox fix: hide the hypervisor bit from the guest (`args: -cpu host,kvm=off`, CPU type `host`) and enable the PCI-E checkbox on the passthrough device. Routine at this point, not a real blocker — budget setup time for it.
- **Bonus**: assigning the 970 to the Windows VM also gives that VM its own GPU for RDP's hardware video-encode path (RemoteFX-style), which should make video/scroll-heavy content noticeably smoother than pure CPU-based RDP encoding would be.

### Trade-off

Using all 3 of the board's full-length slots (5080 + 970 + HBA) means any future PCIe card — a NIC, another storage controller — has nothing to go into. In practice this isn't a real cost here: onboard 10GbE/5GbE/WiFi7 on this board (see [cpu_motherboard_ram.md](cpu_motherboard_ram.md)) already covers the only future PCIe device this build had been planning for. Chassis clearance for **three** full-length cards (not two) is now the open item in [case_chassis_rack.md](case_chassis_rack.md) — higher stakes than the original two-card check.

## Future GPU expansion (2-year outlook)

If more GPU power is needed down the road (bigger/more parallel model serving), the choice between "add a matched second 5080" vs. "add a future higher-tier card" vs. "replace with a single higher-VRAM card" was analyzed against real llama.cpp/Ollama multi-GPU behavior, not assumption:

- **Ollama/llama.cpp split one model across GPUs by layer** (GPU 0 runs layers 0-20, GPU 1 runs 21-40, etc.), not true tensor-parallel. Mismatched cards technically work but **run at the pace of the slower GPU** — pairing a future flagship with the current 5080 to split one large model would cap the new card down to 5080 speed for anything touching both, on top of real CUDA/driver-version friction between generations.
- **A matched second 5080** is the proven low-friction path to pool VRAM (32GB combined) for one model too big for 16GB — same architecture, same driver stack, and the board's x8/x8 split already clears the community-recommended x8 PCIe Gen 5 minimum for multi-GPU AI work. Real cost: paired 5080s want **1200-1400W**, well above the current 1000W PSU. If this is the real plan, buy the pair together rather than staggering the purchase — the 5080 may be hard to source new by the time a second one is wanted 2 years out.
- **A future flagship card is still a good buy — just not paired with the 5080 for one model.** Run it as an independent second GPU for its own separate workload instead; that gets its full generational uplift rather than throttling it to 5080 speed inside a split model.
- **Replacing with a single higher-VRAM card** is architecturally the cleanest option if the market offers one affordably by then (no interconnect bottleneck, no 1200-1400W PSU, no multi-GPU software complexity) — but not guaranteed, since consumer VRAM per tier has moved slowly generation to generation (the 5080 is still 16GB, same tier previous generations sat at). The real high-VRAM jump in this doc's own catalog survey is the RTX PRO Blackwell tier at $5,900-$15,000 — a different budget conversation.
- **Naming note for future searches**: NVIDIA's generations run 10→20→30→40→50 (Blackwell); the successor to the 5080 will most likely be branded **60-series** ("6080"), not "70-series."

---

## Full catalog survey

Everything seen at Micro Center during this search, for reference.

### NVIDIA GeForce RTX 5090 (32GB GDDR7)

| SKU | Card | Price | Stock |
|---|---|---|---|
| 805390 | Gigabyte Gaming OC Triple Fan | $3,799.99 (orig $3,899.99) | 3 |
| 800078 | ASUS TUF Gaming OC Triple Fan | $4,399.99 | Sold out |
| 805234 | MSI VENTUS 3X OC Triple Fan | $4,299.99 | Sold out |
| 800060 | ASUS ROG Astral OC Triple Fan | $4,829.99 | 4 |
| 048272 | ASUS ProArt OC Dual-Fan | $4,499.99 | Sold out |
| 805333 | Gigabyte MASTER ICE OC Triple Fan | $4,099.99 | 1 |
| 905257 | ASUS ROG Astral White OC Quad Fan | $4,929.99 | 1 |
| 805309 | MSI SUPRIM LIQUID SOC (AIO) | $4,099.99 | 2 |
| 985168 | Zotac SOLID OC Triple Fan | $3,499.99 | Sold out |
| 805283 | MSI GAMING TRIO OC Triple Fan | $4,399.99 | 3 |
| 985101 | Zotac AMP Extreme Infinity OC Triple Fan | $3,899.99 | 2 |
| 805416 | Gigabyte Aorus Master Triple Fan | $4,099.99 | Sold out |
| 936237 | ASUS ROG Astral BTF OC Triple Fan | $4,929.99 | Sold out |
| 985176 | Zotac ARCTICSTORM AIO Liquid Cooled | $4,099.99 | 2 |

### NVIDIA GeForce RTX 5080 (16GB GDDR7, 960 GB/s, 10,752 CUDA cores)

Refreshed from a live Micro Center catalog pull, which also exposed MC's own in-store rating per SKU (see [Decision](#why-805440-specifically-out-of-the-full-rtx-5080-catalog) above for external corroboration/contradiction of a few of these).

| SKU | Card | Price | MC Rating | Stock |
|---|---|---|---|---|
| **805440** | **Gigabyte WINDFORCE SFF OC Triple Fan** ⭐ picked | **$1,199.99** (orig $1,399.99) | 4.8 | **25+** |
| 985135 | Zotac Solid Core OC Triple Fan | $1,249.99 (orig $1,269.99) | 4.7 | 25+ |
| 805143 | PNY OC Triple Fan | $1,289.99 | 4.8 | 2 |
| 020024 | PNY Slim OC Dual Fan | $1,299.99 | 4.3 | 4 |
| 805135 | PNY EPIC-X RGB OC Triple Fan | $1,319.99 | 4.7 | 5 |
| 018580 | MSI INSPIRE 3X Black OC Triple Fan | $1,429.99 (PSU-bundle discount available) | 5.0 | 3 |
| 799817 | Gigabyte GAMING OC Triple Fan | $1,499.99 | 4.7 | 2 |
| 803015 | MSI SHADOW 3X OC Triple Fan | $1,549.99 (PSU-bundle discount available) | 4.9 | 22 |
| 805341 | Gigabyte AERO SFF OC Triple Fan | $1,549.99 | 4.7 | 3 |
| 804849 | ASUS Prime Triple Fan | $1,599.99 | 4.8 | 6 |
| 802975 | MSI GAMING TRIO OC Triple Fan | $1,649.99 (PSU-bundle discount available) | 4.9 | 7 |
| 793190 | ASUS Prime OC Triple Fan | $1,699.99 | 4.6 | 4 |
| 044743 | ASUS Prime EVO OC Triple-Fan | $1,699.99 (1 open box $1,349.96) | — | 2 |
| 793315 | ASUS TUF Gaming OC Triple Fan | $1,899.99 | 4.7 | 1 |
| 926717 | ASUS Noctua Edition OC Triple Fan | $1,899.99 | 5.0 | 1 |
| 936104 | ASUS ProArt OC Triple Fan | $1,899.99 (1 open box $1,444.96) | 4.3 | 2 |
| 793182 | ASUS ROG Astral OC Triple Fan | $2,099.99 | 4.7 | 4 |
| 947523 | ASUS ROG Astral White OC Triple Fan | $2,149.99 | 4.8 | 5 |

*Note: MSI GAMING TRIO's discount lapsed between the original survey and this refresh ($1,609.99 → $1,649.99, full price). ASUS TUF Gaming OC's SKU also changed on this pull (985283 → 793315) with the open-box unit no longer listed — treat as normal live-catalog churn, not a data error.*

### NVIDIA GeForce RTX 5070 Ti (16GB GDDR7, 896 GB/s, 8,960 CUDA cores)

| SKU | Card | Price | Stock |
|---|---|---|---|
| 800102 | ASUS PRIME OC Triple Fan | $1,009.99 | 4 |
| **844035** | **Zotac SOLID SFF OC Triple Fan** ⭐ picked | **$929.99** (orig $1,099.99) | **2 (NJ)** |
| 019240 | MSI VENTUS 3X PZ Black OC Triple Fan | $1,029.99 | 1 |
| 045278 | MSI FRIEREN EDITION OC Triple-Fan | $1,179.99 | 7 |
| 805176 | PNY ARGB EPIC-X OC Triple Fan | $979.99 | 2 |
| 815118 | MSI VENTUS 3X OC Triple Fan | $999.99 (orig $1,009.99) | 2 |
| 813956 | Gigabyte Gaming OC Triple Fan | $1,099.99 | 4 |
| 814020 | Gigabyte EAGLE ICE SFF OC Triple Fan | $1,059.99 | 3 |
| 814046 | Gigabyte AERO OC Triple Fan | $1,159.99 (1 open box $1,043.96) | 2 |
| 937425 | MSI MLG EDITION OC Dual Fan | $1,079.99 (orig $1,129.99) | 7 |
| 938118 | Zotac Solid Core OC Triple Fan | $1,099.99 | 18 |
| 814053 | Gigabyte EAGLE SFF OC Triple Fan | $1,059.99 | 3 |
| 985101 | Zotac SOLID SFF OC Triple-Fan (2nd SKU) | $1,069.99 | 10 |

### NVIDIA GeForce RTX 5070 (12GB GDDR7, 672 GB/s, 6,144 CUDA cores)

| SKU | Card | Price | Stock |
|---|---|---|---|
| 813519 | ASUS Prime Triple Fan | $819.99 | 1 |
| 813980 | Gigabyte GAMING OC Triple Fan | $679.99 | 4 |
| 826172 | MSI SHADOW 2X OC Dual Fan | $789.99 | 11 |
| 826164 | MSI SHADOW 3X OC Triple Fan | $659.99 | 3 |
| 813493 | ASUS TUF Gaming OC Triple Fan | $949.99 (1 open box $719.96) | 4 |
| 922179 | PNY Triple Fan | $619.99 | 8 |
| 805168 | PNY OC Triple Fan | $629.99 | 17 |
| 813972 | Gigabyte WINDFORCE SFF OC Triple Fan | $579.99 (orig $649.99) | 25+ |

### NVIDIA GeForce RTX 5060 Ti (8GB or 16GB, GDDR7)

| SKU | Card | VRAM | Price | Stock |
|---|---|---|---|---|
| 985283 | Zotac Twin Edge OC Dual Fan | 8GB | $329.99 (orig $429.99) | 25+ |
| 019232 | MSI VENTUS 2X BLACK PLUS OC Dual Fan | 16GB | $549.99 (orig $609.99) | 2 |
| 854885 | MSI Shadow 2X Plus OC Dual Fan | 8GB | $329.99 (orig $429.99) | 6 |
| 847616 | ASUS Prime Triple Fan | 16GB | $809.99 | 2 |
| 991604 | ASUS Dual OC Dual Fan | 16GB | $799.99 | 6 |
| 914002 | Zotac Twin Edge OC Dual Fan | 16GB | $649.99 | 4 |
| 849893 | ASUS Prime OC Triple Fan | 16GB | $829.99 | 5 |
| 985291 | Zotac Twin Edge White OC Dual Fan | 8GB | $429.99 | 15 |
| 855205 | PNY EPIC-X RGB OC Triple Fan | 8GB | $449.99 | 1 |

*Original hardware-doc target — upgraded to the 5070 Ti for bandwidth.*

### NVIDIA GeForce RTX 5060 / 5050 (budget tier)

| SKU | Card | VRAM | Price |
|---|---|---|---|
| 856021 | PNY 5060 OC Dual Fan | 8GB | $319.99 (orig $339.99) |
| 872002 | ASUS 5060 Dual OC Dual Fan | 8GB | $469.99 |
| 865436 | ASUS 5060 Prime Triple Fan | 8GB | $489.99 |
| 869545 | Gigabyte 5060 WINDFORCE OC Dual Fan | 8GB | $349.99 |
| 898510 | MSI 5050 SHADOW 2X OC Dual Fan | 8GB | $399.99 |
| 895599 | Zotac 5050 Solo Single Fan | 8GB | $299.99 |
| 895003 | Gigabyte 5050 Low Profile OC Dual Fan | 8GB | $299.99 |

### NVIDIA RTX 30-series (legacy stock)

| SKU | Card | VRAM | Price |
|---|---|---|---|
| 985580 | Zotac RTX 3050 Twin Edge OC Dual Fan | 6GB | $199.99 |
| 799973 | Gigabyte RTX 3050 WINDFORCE V2 OC Dual Fan | 6GB | $209.99 |
| 061218 | MSI RTX 3060 VENTUS 2X OC Dual-Fan | 12GB | $339.99 |

### NVIDIA RTX PRO (Blackwell workstation/AI)

| SKU | Card | VRAM | Price |
|---|---|---|---|
| 044594 | RTX PRO 6000 Blackwell Workstation Dual-Fan (OEM) | 96GB | $10,999.99 (orig $14,999.99) |
| 043893 | PNY RTX PRO 6000 Blackwell Max-Q Dual-Fan | 96GB | $10,999.99 (orig $14,999.99) |
| 044610 | RTX PRO 5000 Blackwell Single-Fan (OEM) | 72GB | $8,699.99 (orig $11,999.99) |
| 044628 | RTX PRO 5000 Blackwell Single-Fan (OEM) | 48GB | $5,899.99 (orig $8,999.99) |
| 919951 | PNY RTX PRO 2000 Blackwell Single-Fan | 16GB | $999.99 (orig $1,199.99) |

*Way outside budget except the Pro 2000, which loses to the 5070 Ti on raw compute for similar money.*

### AMD Radeon RX 9070 XT / 9070 / 9070 GRE (16GB/12GB GDDR6)

| SKU | Card | VRAM | Price | Stock |
|---|---|---|---|---|
| 796706 | PowerColor Reaper Triple Fan | 16GB | $669.99 (orig $799.99) | 25+ |
| 813238 | ASRock Steel Legend Triple Fan | 16GB | $749.99 (1 open box $674.96) | 9 |
| 795070 | ASUS Prime OC Triple Fan | 16GB | $799.99 | 25+ |
| 930487 | ASRock Challenger Triple Fan | 16GB | $719.99 | 25+ |
| 799841 | Gigabyte Gaming OC Triple Fan | 16GB | $739.99 (1 open box $665.96) | 6 |
| 815423 | Sapphire PULSE Triple Fan | 16GB | $749.99 (orig $809.99) | 3 |
| 795062 | ASUS TUF Gaming OC Triple Fan | 16GB | $849.99 | 1 |
| 917856 | ASRock Steel Legend Dark OC Triple Fan | 16GB | $749.99 | 25+ |
| 998419 | ASUS Prime White OC Triple Fan | 16GB | $819.99 | 7 |
| 800177 | PowerColor Red Devil OC Triple Fan | 16GB | $849.99 | 1 |
| 922641 | Yeston Sakura Atlantis OC Triple Fan | 16GB | $799.99 | 11 |
| 843532 | PowerColor Devil Spectral White OC Triple Fan | 16GB | $899.99 (1 open box $809.96) | 17 |
| 796748 | PowerColor Reaper Triple Fan (9070, non-XT) | 16GB | $599.99 (orig $619.99) | 19 |
| 799833 | Gigabyte Gaming OC Triple Fan (9070, non-XT) | 16GB | $669.99 | 10 |
| 043356 | PowerColor GRE Reaper Triple-Fan | 12GB | $549.99 | 2 |

### AMD Radeon RX 9060 XT (16GB/8GB GDDR6)

| SKU | Card | VRAM | Price |
|---|---|---|---|
| 872010 | PowerColor Reaper Dual Fan | 16GB | $439.99 (orig $469.99) |
| 986794 | PowerColor Reaper Dual Fan | 16GB | $469.99 |
| 918995 | ASUS Dual Fan | 16GB | $489.99 |
| 863720 | PowerColor Reaper Dual Fan | 8GB | $339.99 |
| 907097 | PowerColor Hellhound Spectral White OC Triple Fan | 16GB | $509.99 |
| 936096 | ASUS Dual White Edition Dual Fan | 16GB | $499.99 |
| 956003 | Gigabyte Gaming ICE OC Triple Fan | 16GB | $469.99 |
| 869578 | ASUS Prime OC Triple Fan | 16GB | $529.99 (1 open box $476.96) |

### AMD Radeon AI PRO R9700 (32GB GDDR6, 640 GB/s, RDNA4) — seriously considered, see decision above

| SKU | Card | Price | Stock | Location note |
|---|---|---|---|---|
| 938654 | ASRock Creator Single-Fan | $1,249.99 (orig $1,459.99) | 1 | Brooklyn — not at NJ |
| 942201 | XFX Single Fan | $1,299.99 (orig $1,499.99) | 1 | Available at NJ |
| 907139 | Gigabyte TOP Single-Fan | $1,359.99 (orig $1,459.99) | 2 | Brooklyn — not at NJ |
| 918326 | ASUS Turbo Single Fan | $1,359.99 (orig $1,479.99) | 2 | Available at NJ. MC's spec table wrongly listed 16GB — confirmed 32GB via ASUS/Newegg/B&H |
| 967562 | Sapphire Single-Fan | $1,249.99 (orig $1,479.99) | 2 | Brooklyn — not at NJ |

Between the two NJ-available options, ASUS Turbo was favored over XFX on thermal grounds (larger cooler: 2-slot/111.76mm vs XFX's 1.5-slot/100mm; ASUS has published thermal data averaging 60.35°C under ROCm load) before the whole R9700 direction was reversed in favor of the 5070 Ti.

### Intel Arc

| SKU | Card | VRAM | Price | Stock |
|---|---|---|---|---|
| 787648 | ASRock B580 Challenger OC Dual Fan | 12GB | $299.99 | 11 |

*Only Arc card in stock at this store. Candidate for a cheap side-project box (llama.cpp + IPEX-LLM), not the production platform.*

### Legacy / other

| SKU | Card | VRAM | Price |
|---|---|---|---|
| 576496 | ASRock RX 7600 Challenger OC Dual Fan | 8GB | $279.99 (orig $289.99) |
| 987347 | Gigabyte GT 710 Single Fan | 2GB DDR3 | $64.99 |

---

## Related

- [cpu_motherboard_ram.md](cpu_motherboard_ram.md) — CPU/board pairing, including the confirmed 3-slot PCIe layout both GPUs and the HBA share
- [case_chassis_rack.md](case_chassis_rack.md) — why the GPU's physical dimensions forced a case change, and the now-3-card clearance check
- [power_supply.md](power_supply.md) — PSU sizing against GPU + CPU draw, including the second GPU's power addition
- [storage.md](storage.md) — the HBA sharing the same 3-slot layout as both GPUs
- [cpu_benchmark_comparison.md](cpu_benchmark_comparison.md) — CPU price/performance table (separate from this doc's GPU survey, but produced in the same research pass)
- [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md) — original hosting decision this build implements (GPU pick has since evolved from that doc's RTX 5060 Ti; fleet table's GTX 970 entry updated to reflect its new role and corrected driver-support timeline)
