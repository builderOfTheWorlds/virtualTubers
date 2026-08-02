# Power Supply — Decision

> **Written**: 2026-08-02
> **Scope**: PSU for the dedicated build described in [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md).

---

## Decision

| Part | Pick | Price |
|---|---|---|
| PSU | New 850-1000W 80+ Gold | ~$170 |
| Spare | `mafober`'s existing Seasonic FOCUS GX-650 (kept, not primary) | $0 |

## Sizing rationale

Component draw once the rest of the build ([cpu_motherboard_ram.md](cpu_motherboard_ram.md), [gpu.md](gpu.md), [storage.md](storage.md)) is totaled:

| Component | Draw |
|---|---|
| Ryzen 9 9950X | 170W |
| RTX 5070 Ti (Zotac SOLID SFF) | 300W (recommends 750W PSU on its own) |
| LSI 9207-8i HBA | ~10-15W |
| 6x PM883 SSDs | ~15-20W combined |
| Motherboard, fans, misc | ~50W |

That's ~555-600W under simultaneous full CPU+GPU load — close enough to `mafober`'s existing 650W Seasonic FOCUS GX-650 that it isn't trusted as primary, given the margin needed for headroom and the PSU's efficiency curve. A fresh 850-1000W unit was sized instead, with the Seasonic kept as a spare rather than discarded.

Cross-check against the specific GPU's own recommendation: the Zotac RTX 5070 Ti lists **750W recommended** on its own product page (the RTX 5080 alternative considered in [gpu.md](gpu.md) lists 850W) — the whole-system 850-1000W target leaves comfortable margin either way, more so with the 5070 Ti than the 5080 would have.

---

## Related

- [cpu_motherboard_ram.md](cpu_motherboard_ram.md) — CPU draw
- [gpu.md](gpu.md) — GPU draw and its own PSU recommendation
- [storage.md](storage.md) — HBA/drive draw
- [case_chassis_rack.md](case_chassis_rack.md) — chassis this PSU mounts in
