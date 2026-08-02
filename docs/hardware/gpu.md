# GPU — Decision and Survey

> **Written**: 2026-08-02
> **Scope**: GPU selection for the dedicated build described in [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md). Supersedes that doc's original RTX 5060 Ti pick.
> Store: Micro Center, North Jersey. **Prices/stock are a snapshot** — GDDR pricing was moving week to week during this period from a broader memory shortage; re-verify before buying.

---

## Decision

| SKU | Card | VRAM | Price | Notes |
|---|---|---|---|---|
| **844035** | **Zotac RTX 5070 Ti SOLID SFF OC Triple Fan** | 16GB GDDR7 | **$929.99** (orig $1,099.99) | 896 GB/s, 8,960 CUDA cores, 300W. Confirmed 2 in stock at North Jersey. |

### Why this one, and not the alternatives seriously considered

- **RTX 5060 Ti (original plan)** — rejected for bandwidth: 448 GB/s vs the 5070 Ti's 896 GB/s. LLM token generation is memory-bandwidth-bound, so this was the lever that mattered most for Ollama serving.
- **RTX 5080** (Gigabyte WINDFORCE SFF OC, SKU 805440, $1,199.99, 25+ in stock) — same 16GB VRAM as the 5070 Ti, only ~7% more bandwidth (960 vs 896 GB/s), ~20-28% more compute (CUDA cores / AI TOPS, which matters more for image-gen than LLM decode). Real price premium for a gain that doesn't touch the actual bottleneck. Kept as the practical fallback given its much deeper stock.
- **AMD Radeon AI PRO R9700** (32GB GDDR6, $1,249-1,359 depending on AIB) — explored in real depth over "would double VRAM let me run bigger models." Answer, from a detailed hands-on review (llama.cpp + ComfyUI head-to-head, real benchmark commands and output, not marketing): **no** — the 5070 Ti won most real inference and image-gen tests despite having half the VRAM, because the R9700's bandwidth (644.6 GB/s) is 39% lower, and its ROCm software stack still has real rough edges (it lost to the generic Vulkan backend on one test; there's a live PyTorch-ROCm bug that makes first-run image gen take ~7 minutes). Bigger VRAM only reliably wins for workloads without a CPU-offload escape hatch — fine-tuning and 3D rendering were the reviewer's own suggested exceptions, not inference.
- **Intel Arc B580** ($299.99, 12GB, 456 GB/s) — good value in isolation, but priced against the 5060 Ti tier, not the 5070 Ti tier, and Ollama's own Intel support is thin (Level Zero, Linux-only, fewer quant options). The real Intel path is llama.cpp + IPEX-LLM directly, not Ollama. Considered as a cheap side-project box for learning that stack, not for production.
- **"Chinese cards"** — domestic chip makers (Moore Threads, Biren, Huawei Ascend) aren't practically purchasable in the US (export-restricted, Chinese-market-first, immature non-English tooling). Modded 48GB RTX 4090s from Chinese resellers are real but run ~$3,400 — more than a stock 5090 — for a hand-soldered, non-warrantied board. Neither beats the 5070 Ti pick.
- **RTX 5090 / RTX PRO Blackwell** — ruled out on budget alone; even the cheapest 5090 variant would have consumed nearly the entire GPU budget.

### Physical/thermal notes carried over from comparing the 5080 and 5070 Ti listings directly

- 5080 (Gigabyte WINDFORCE SFF): **triple-slot, 2.5-slot width (50mm), 126mm tall**, 850W PSU recommended.
- 5070 Ti (Zotac SOLID SFF): **dual-slot, 41.6mm width, 116.84mm tall**, 750W PSU recommended, ships with a dual 8-pin→16-pin adapter.
- The 5070 Ti's slimmer profile leaves more clearance for the reused LSI HBA card in an adjacent slot — worth a physical check once the motherboard and GPU are both in hand, same lesson as the case-clearance issue below.
- One data inconsistency caught on the Zotac listing: "PCIe Bandwidth: PCIe 5.0 x16" and "Interface: PCIe 4.0" appear on the same page, contradicting each other. Treated as a Micro Center template error (the 5070 Ti's GB203 die is native PCIe 5.0), not a real spec limitation — and wouldn't matter functionally either way, since PCIe generation isn't the bottleneck for this workload.

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

| SKU | Card | Price | Stock |
|---|---|---|---|
| **805440** | **Gigabyte WINDFORCE SFF OC Triple Fan** | **$1,199.99** (orig $1,399.99) | **25+** |
| 793182 | ASUS ROG Astral OC Triple Fan | $2,099.99 | 4 |
| 985283 | ASUS TUF Gaming OC Triple Fan | $1,899.99 (1 open box $1,529.96) | 2 |
| 947523 | ASUS ROG Astral White OC Triple Fan | $2,149.99 | 1 |
| 985135 | Zotac Solid Core OC Triple Fan | $1,249.99 (orig $1,269.99) | 25+ |
| 805143 | PNY OC Triple Fan | $1,289.99 | 3 |
| 804849 | ASUS Prime Triple Fan | $1,599.99 | 7 |
| 805341 | Gigabyte AERO SFF OC Triple Fan | $1,549.99 | 4 |
| 020024 | PNY Slim OC Dual Fan | $1,299.99 | 4 |
| 799817 | Gigabyte GAMING OC Triple Fan | $1,449.99 (orig $1,499.99) | 3 |
| 802975 | MSI GAMING TRIO OC Triple Fan | $1,609.99 (orig $1,649.99) | 7 |
| 803015 | MSI SHADOW 3X OC Triple Fan | $1,549.99 | 23 |
| 793190 | ASUS Prime OC Triple Fan | $1,699.99 | 4 |

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

- [cpu_motherboard_ram.md](cpu_motherboard_ram.md) — CPU/board pairing, including the PCIe-slot sizing that GPU width interacts with
- [case_chassis_rack.md](case_chassis_rack.md) — why the GPU's physical dimensions forced a case change
- [power_supply.md](power_supply.md) — PSU sizing against GPU + CPU draw
- [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md) — original hosting decision this build implements (GPU pick has since evolved from that doc's RTX 5060 Ti)
