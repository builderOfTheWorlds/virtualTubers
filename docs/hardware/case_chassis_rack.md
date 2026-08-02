# Case / Chassis / Rack — Decision

> **Written**: 2026-08-02
> **Scope**: Enclosure for the dedicated build described in [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md).

---

## Decision

| Part | Pick | Price |
|---|---|---|
| Server chassis | 4U rackmount, 8-bay hot-swap (Rosewill or RackChoice) | ~$200-300 |
| Rack | 12-25U open-frame 4-post | ~$150-350 |
| Cooler | Air cooler (clearance not yet verified against final chassis pick) | ~$50 |

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

## Open item before ordering

GPU rack servers run deep (often 26-30"+ once PSU and cabling are accounted for), and rack depth is adjustable but has a max. **Confirm the specific chassis' depth against the rack's adjustable range before ordering both** — same lesson as the Antec measurement above, not yet done for this pairing.

## Considered and rejected: full enterprise server-grade equipment

Real enterprise gear (Dell PowerEdge, HPE ProLiant, Supermicro) was evaluated for the professional-experience angle — iDRAC/iLO, ECC RAM, redundant hot-swap PSUs. Rejected for the primary build:

- 1U/2U enterprise chassis are validated for datacenter GPUs (Tesla/A-series), not open-air consumer cards — wrong airflow direction, often no power routed to a GPU riser at all.
- Genuinely loud (small high-RPM fans, high static pressure) — a real 24/7 quality-of-life cost in a basement.
- Older, affordable used dual-Xeon/EPYC platforms commonly idle at 150-250W **before adding a GPU**, undoing the low-power-24/7 premise this whole build is optimized around.

Compromise, not yet acted on: a separate **$200-400 used enterprise box** (Dell R730/R740, HPE DL380) for CPU-only, non-GPU duty (Grafana, Prometheus, Gitea runner) — gets the real hands-on iDRAC/ECC/redundant-PSU experience without forcing a GPU into hardware never designed for one.

---

## Related

- [gpu.md](gpu.md) — the card whose physical dimensions drove this decision
- [storage.md](storage.md) — the drive cage that created the original conflict
- [power_supply.md](power_supply.md) — PSU sizing for the rack chassis
