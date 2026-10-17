# Hardware Research — Lessons Learned

> **Written**: 2026-08-02
> Methodology notes from researching and pricing the build documented in this directory. These are process lessons, not parts picks — see [README.md](README.md) for the actual build.

---

## Verify load-bearing numbers against reality, not just a spec sheet

- `mafober`'s Antec Nine Hundred Two case listed GPU clearance up to 343mm, with a vague caveat about needing to remove the drive cage. **Direct measurement with the drive cage actually installed** showed real clearance was 279.4mm — 64mm less than the headline spec, and short of every RTX 5070 Ti variant checked (even the shortest compact card found, 290mm, didn't fit). Trusting the spec sheet alone would have missed this.
- Micro Center's own listings were internally inconsistent more than once: the ASUS R9700 Turbo's detailed spec table said "16GB" while its title, part number, and every independent retailer (ASUS, Newegg, B&H) said "32GB." A separate Zotac RTX 5070 Ti page listed both "PCIe 5.0 x16" and "Interface: PCIe 4.0" on the same page. A 2026-08-03 pull of a Crucial 128GB RAM kit's own Micro Center page added a third example: "Memory Speed: PC5-52800" sat directly next to "Memory Speed (MHz): DDR5-5600" and the title's "PC5-44800" — three numbers on one page, only one of them consistent with the kit's actual rating.
- **Rule going forward**: when a specific number determines fit-or-doesn't-fit / works-or-doesn't-work, cross-check it against multiple independent sources (manufacturer page + 2+ retailers) or measure it directly — never take a single listing at face value, even the retailer actually selling it.

## Re-verify specs against the actual final part, not an earlier candidate

- [storage.md](storage.md)'s "AM5 motherboards only expose 4 native SATA ports" was true enough of the ASUS Creator ProArt — the board priced into the original CPU+mobo+RAM bundle — but the board actually purchased partway through this project, the Gigabyte X870E AORUS MASTER X3D ICE, has only **2** native SATA ports. The generic claim survived in the doc for a full day after the board pick itself had already changed, because nothing prompted a re-check of a spec that "obviously" hadn't changed.
- **Rule going forward**: whenever a specific-model decision changes mid-project (a different board, a different CPU, a different case candidate), treat every board/model-specific spec written before that change as unverified again — even ones that feel generic to the whole chipset tier ("AM5 boards have N SATA ports"). The failure mode isn't a bad source, it's citing a true fact about the *wrong specific part*.

## Retailer price gaps can be enormous, not just a location/tax difference

- The "store location can flip which listing is cheaper" lesson below was about tax rates at two locations of the *same* retailer. A separate, larger gap showed up comparing retailers directly: the same 128GB Crucial RAM kit (CP2K64G56C46U5) was **$1,799 on Amazon** vs. **$3,999.99 at Micro Center** — more than double — and the Micro Center listing was sold out with no shipping or pickup available at the local store regardless, making it doubly moot.
- **Rule going forward**: for any single expensive line item (this build's RAM was the biggest single component cost), check at least one retailer outside whatever store the rest of the build is being sourced from, especially for high-capacity/recently-released parts where per-unit retailer markup can vary wildly, not just by a typical 5-10%.

## Spec-sheet capacity comparisons can point the wrong way

- AMD Radeon AI PRO R9700 (32GB) vs RTX 5070 Ti (16GB) looked like an easy win for the R9700 on paper — double the VRAM for similar money. A real hands-on review with actual `llama.cpp` benchmark commands and output (not marketing copy) showed the opposite for real inference and image-gen workloads: the 5070 Ti won most tests despite half the VRAM, because memory bandwidth (896 vs 644.6 GB/s) and software maturity (ROCm lost to the generic Vulkan backend on one test) mattered more than raw capacity.
- **Rule going forward**: "bigger number on the spec sheet" doesn't reliably predict real performance, especially crossing vendor/software-ecosystem lines (CUDA vs ROCm vs SYCL). Find a real benchmark on a workload shape close to the actual use case before trusting the spec comparison.

## MoE models: total parameters govern memory, not active parameters

For mixture-of-experts models, every expert has to sit in memory regardless of how few activate per token — active-parameter count affects compute speed, not VRAM footprint. Easy to get backwards when eyeballing a model card (this is what made a 304B-total-parameter model an obvious non-starter for any consumer GPU, even though its active-parameter count is much smaller).

## GPU/DRAM pricing was genuinely volatile during this research, not a one-time lookup

A real, structural AI-driven memory shortage (not a rumor, not a short-lived spike) was moving GPU and RAM prices week to week, expected to persist through 2027-2028. Numbers gathered earlier in the *same* research session needed re-verification later — the AMD R9700 SKU list and stock differed materially between the Brooklyn and North Jersey Micro Center locations alone. **Don't treat a price/stock check as durable — re-verify close to the actual purchase date.**

## Store location can flip which listing is actually cheaper

North Jersey's 3.5% sales tax vs. NYC's 8.875% was large enough that a *higher* sticker price at the NJ store worked out *cheaper* after tax than a lower sticker price in Brooklyn. Always compute the after-tax total when comparing across locations, not the shelf price alone.

## Duplicate publishing isn't automatically disqualifying — read before dismissing

A hardware review found identically published on two unrelated-looking domains initially read as a content-farm red flag. Reading the full text showed genuine, credible hands-on benchmarking — real command lines, authentic tool output, a documented software bug with a working fix. The duplication was most likely just the author cross-publishing, not fabrication. Surface-level red flags are worth factoring into confidence, but the actual substance of the content should decide how much weight it gets.

---

## Related

- [README.md](README.md) — the build these lessons informed
- [case_chassis_rack.md](case_chassis_rack.md) — the clearance measurement in detail
- [gpu.md](gpu.md) — the R9700 vs 5070 Ti reversal in detail
- [storage.md](storage.md) — the native-SATA-port correction behind the "re-verify against the actual final part" lesson
- [cpu_motherboard_ram.md](cpu_motherboard_ram.md) — the Micro Center vs. Amazon RAM price gap, and the board swap that triggered the stale-spec lesson
