# Hardware — Build Documentation Index

> **Written**: 2026-08-02
> This directory documents the detailed parts-level decisions for the dedicated box described at a hosting/strategy level in [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md). That doc's specific parts picks (Ryzen 7 9700X, RTX 5060 Ti, ~$1,325) are **superseded** by what's here — GDDR/DRAM price hikes and a scope change (this box became a general "main server," not just a campaign-platform-dedicated one) moved the build significantly since that doc was written.

## Docs in this directory

| Doc | Covers |
|---|---|
| [cpu_motherboard_ram.md](cpu_motherboard_ram.md) | Ryzen 9 9950X, ASUS X870E Creator ProArt, 64GB DDR5-6000 — and why the plan moved off the original 9700X |
| [gpu.md](gpu.md) | RTX 5070 Ti pick, full Micro Center catalog survey (100+ SKUs across NVIDIA/AMD/Intel), and the AMD R9700 detour that got reversed by real benchmark data |
| [storage.md](storage.md) | Reusing `mafober`'s 6x PM883 SSDs + LSI HBA, and the migration this actually requires |
| [case_chassis_rack.md](case_chassis_rack.md) | Why the free Antec case got measured out, and the pivot to a 4U rackmount chassis + open-frame rack |
| [power_supply.md](power_supply.md) | PSU sizing across the whole system |

## Current build summary

| Category | Pick | Price |
|---|---|---|
| CPU + Motherboard + RAM (32GB) | Ryzen 9 9950X + ASUS X870E Creator ProArt | $1,099.99 |
| RAM upgrade | +32GB matched kit → 64GB total | ~$450-500 |
| GPU | Zotac RTX 5070 Ti SOLID SFF OC Triple Fan 16GB | $929.99 |
| Storage (data) | Reused: 6x PM883 1.92TB + LSI 9207-8i HBA | $0 |
| Storage (boot) | New NVMe or reused mafober boot SSD | ~$0-60 |
| PSU | New 850-1000W 80+ Gold | ~$170 |
| Server chassis | 4U rackmount, 8-bay hot-swap | ~$200-300 |
| Rack | 12-25U open-frame 4-post | ~$150-350 |
| Cooler | Air cooler (clearance TBD) | ~$50 |
| **Estimated total** | | **~$3,150-3,450** |

Software/architecture side: staying on **Proxmox + LXC**, migrating CT 101's existing structure from `mafober` rather than rebuilding.

## Open items before ordering

- [ ] Re-verify the Zotac RTX 5070 Ti's current price/stock at the North Jersey store — confirmed once, but everything else in this build has shown prices moving week to week.
- [ ] Confirm the specific 4U chassis and rack depths match once exact models are chosen.
- [ ] Verify GPU slot-width clearance against the reused HBA once both the motherboard and GPU are physically in hand.
- [ ] Confirm the air cooler fits the 4U chassis' height clearance.
- [ ] Decide whether the virtualTubers Docker stack joins CT 101 or gets its own CT.
