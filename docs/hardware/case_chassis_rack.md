# Case / Chassis / Rack — Decision

> **Written**: 2026-08-02
> **Scope**: Enclosure for the dedicated build described in [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md).

---

## Decision

| Part | Pick | Price |
|---|---|---|
| Server chassis | 4U rackmount, 8-bay hot-swap (Rosewill or RackChoice) | ~$200-300 |
| Rack | 12-25U open-frame 4-post | ~$150-350 |
| Cooler | **Thermalright Peerless Assassin 120 SE** (SKU 959262) | **$49.99** |

This replaced an original plan to reuse `mafober`'s existing case for free — abandoned after direct measurement showed a real physical conflict. That measurement process is worth keeping on record since it's the reason this ended up a rack build at all.

## The case that got measured out: Antec Nine Hundred Two

`mafober`'s case (`Antec Nine Hundred Two`, ~15 years old, most fans no longer working) was the original plan for a free case. Antec's own spec sheet lists max GPU clearance at 343mm, **with the caveat that "some video cards may require removal of the adjacent hard drive cage"** — the same cage needed for the 6 reused PM883 drives (see [storage.md](storage.md)).

Measured directly rather than trusting the spec sheet:

| Measurement | Result |
|---|---|
| Old GTX 970 card length (for reference) | 9⅝" = 244.5mm |
| **Actual clearance with drive cage installed** | **11" = 279.4mm** |

Every RTX 5070 Ti variant checked ran **longer than 279.4mm** — even the shortest compact dual-fan option found (PNY Slim, 290mm) was ~11mm too long; "SFF"-branded cards weren't necessarily shorter (ZOTAC's "SOLID SFF," despite the name, is 304.4mm). At 300W, there doesn't appear to be a 5070 Ti variant under ~290mm — the cooler needs that much room to dissipate the heat.

Given the case is also 15 years old with failing fans, the decision was to replace it outright rather than reconfigure drive mounting to squeeze a shorter/dual-fan card in.

## Why rack instead of another tower

The user's own goal shifted the decision here: interest in gaining real rack-equipment experience for professional development, with available basement space to host one. This reframing also happens to solve the clearance problem better than any tower case would.

## Chassis: 4U, not 2U/3U

4U is the right tier — enough height for a full-size GPU, a standard tower air cooler, and hot-swap drive bays. 2U/3U chassis force low-profile coolers and often single-slot server-style GPUs, incompatible with the GPU picked in [gpu.md](gpu.md).

Candidates matching the spec (E-ATX/ATX, 6+ drives, full GPU, up to 350mm clearance — comfortably clears every 5070 Ti variant checked, unlike the Antec):

- **Rosewill 4U** — 8 hot-swap 3.5"/2.5" bays, E-ATX compatible, supports up to 4 dual-slot GPUs.
- **RackChoice 4U** — same shape: 8 hot-swap SATA/SAS bays, EATX/ATX/mATX support.

Both are sold through Newegg rather than Micro Center.

## Rack: sized for one server plus room to grow

A **12-25U open-frame 4-post rack** (open frame, not an enclosed cabinet, for airflow and cabling access) — sized for the one 4U server today, with room for a switch, patch panel, and UPS as the "real rack equipment" goal builds out, without jumping straight to a 42U cabinet that eats the whole basement on day one.

## Cooler: Thermalright Peerless Assassin 120 SE, not a premium option

The Ryzen 9 9950X ([cpu_motherboard_ram.md](cpu_motherboard_ram.md)) is a 170W-TDP chip (up to ~200-230W PPT under all-core boost) that needs a real dual-tower air cooler, not a budget single-tower — but this is also a well-documented case in independent hardware press (GamersNexus, Hardware Unboxed) where the cheap option wins outright:

- **Height: 155mm** — clears the typical 4U rackmount clearance ceiling (~160-165mm) with real margin, addressing the open item below even before the exact chassis is picked.
- **Thermal performance**: handles the 9950X's full TDP without throttling, benchmarking within 1-2°C of the $180 Noctua NH-D15 — a premium flagship cooler at roughly 3.6x the price.
- **24/7 duty**: S-FDB fluid-dynamic-bearing fans rated 50,000+ hours; no reported issues under sustained (non-gaming-burst) load.
- Ruled out the 140mm variant (Peerless Assassin 140, $59.99) specifically for height — at 158mm it's too close to the 4U ceiling to risk before the exact chassis is confirmed. The premium Noctua NH-D15 G2 ($179.99) was also passed on: since this box lives in a basement rack rather than near living space, the extra quietness isn't worth the price jump for a 1-2°C thermal gain.

## Open item before ordering

GPU rack servers run deep (often 26-30"+ once PSU and cabling are accounted for), and rack depth is adjustable but has a max. **Confirm the specific chassis' depth against the rack's adjustable range before ordering both** — same lesson as the Antec measurement above, not yet done for this pairing. The cooler's 155mm height is now confirmed and should clear most 4U chassis, but re-verify against the specific chassis' published clearance once chosen.

**Raised stakes as of 2026-08-03**: the build now has **three full-length PCIe cards** (RTX 5080 + a second GPU, an EVGA GTX 970 reused for VM passthrough, + the LSI HBA — see [gpu.md](gpu.md) and [storage.md](storage.md)), not two. The clearance/airflow check above needs to cover all three cards in adjacent slots, not just GPU-vs-HBA — specifically whether the 5080's cooler (triple-fan, even if "SFF"-branded) leaves enough gap for the 970 next to it without starving either card's airflow, and whether either card's cooler physically blocks reach to the third (chipset) slot for the HBA. Still not done.

**Case pick may have diverged**: this doc's candidates (Rosewill 4U, RackChoice 4U) were the researched recommendation, but the actual shopping cart for this build included a **SilverStone RM52 (5U, ATX, ~$579.99)** instead — a different chassis than either candidate above. If that's the one being bought, it needs the same depth/clearance verification from scratch (bay count, GPU clearance with the drive cage installed, and now 3-card width) rather than assuming it matches the Rosewill/RackChoice research below. Not yet confirmed which chassis is final.

No networking-driven slot pressure to add to this check: the motherboard has onboard 10GbE/5GbE/WiFi7 (see [cpu_motherboard_ram.md](cpu_motherboard_ram.md)), so there's no future NIC card competing for rack/case space or a PCIe slot.

## Considered and rejected: full enterprise server-grade equipment

Real enterprise gear (Dell PowerEdge, HPE ProLiant, Supermicro) was evaluated for the professional-experience angle — iDRAC/iLO, ECC RAM, redundant hot-swap PSUs. Rejected for the primary build:

- 1U/2U enterprise chassis are validated for datacenter GPUs (Tesla/A-series), not open-air consumer cards — wrong airflow direction, often no power routed to a GPU riser at all.
- Genuinely loud (small high-RPM fans, high static pressure) — a real 24/7 quality-of-life cost in a basement.
- Older, affordable used dual-Xeon/EPYC platforms commonly idle at 150-250W **before adding a GPU**, undoing the low-power-24/7 premise this whole build is optimized around.

Compromise, not yet acted on: a separate **$200-400 used enterprise box** (Dell R730/R740, HPE DL380) for CPU-only, non-GPU duty (Grafana, Prometheus, Gitea runner) — gets the real hands-on iDRAC/ECC/redundant-PSU experience without forcing a GPU into hardware never designed for one.

---

## Related

- [gpu.md](gpu.md) — the cards whose physical dimensions drove this decision, now two GPUs plus the HBA
- [storage.md](storage.md) — the drive cage that created the original conflict
- [power_supply.md](power_supply.md) — PSU sizing for the rack chassis, including the second GPU's power draw
- [cpu_motherboard_ram.md](cpu_motherboard_ram.md) — onboard networking that removes any NIC-driven slot/case pressure
