# Power Supply — Decision

> **Written**: 2026-08-02
> **Scope**: PSU for the dedicated build described in [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md).

---

## Decision

| Part | Pick | Price |
|---|---|---|
| PSU | **MSI MAG A1000GLS PCIE5**, 1000W 80+ Gold, fully modular, ATX 3.1 (SKU 948380) | **$129.99** (orig $159.99) |
| Spare | `mafober`'s existing Seasonic FOCUS GX-650 (kept, not primary) | $0 |

## Sizing rationale

Component draw once the rest of the build ([cpu_motherboard_ram.md](cpu_motherboard_ram.md), [gpu.md](gpu.md), [storage.md](storage.md)) is totaled:

| Component | Draw |
|---|---|
| Ryzen 9 9950X | 170W |
| RTX 5080 (Gigabyte WINDFORCE SFF OC) | 300W (recommends 850W PSU on its own) |
| LSI 9207-8i HBA | ~10-15W |
| 6x PM883 SSDs | ~15-20W combined |
| Motherboard, fans, misc | ~50W |

That's ~555-600W under simultaneous full CPU+GPU load — close enough to `mafober`'s existing 650W Seasonic FOCUS GX-650 that it isn't trusted as primary, given the margin needed for headroom and the PSU's efficiency curve. A fresh 850-1000W unit was sized instead, with the Seasonic kept as a spare rather than discarded.

Cross-check against the specific GPU's own recommendation: the RTX 5080 lists **850W recommended** on its own product page — the low end of the 850-1000W whole-system target is a tighter fit against that than the previously-considered RTX 5070 Ti (750W recommended) would have been. Leaned toward 1000W rather than 850W given this, since the GPU's own recommendation sits at the bottom of the original range.

### Updated 2026-08-03: CPU swap + second GPU

The CPU pick changed to the **9950X3D2** (200W, up from the plain 9950X's 170W) and a second GPU — a reused **EVGA GTX 970** (~145W), passed through to a Windows VM, see [gpu.md](gpu.md) — was added. Revised total:

| Component | Draw |
|---|---|
| Ryzen 9 9950X3D2 | 200W |
| RTX 5080 (Gigabyte WINDFORCE SFF OC) | 300W |
| GTX 970 (reused, VM passthrough) | ~145W |
| LSI 9207-8i HBA | ~10-15W |
| 6x PM883 SSDs | ~15-20W combined |
| Motherboard, fans, misc | ~50W |
| **Total** | **~720-730W** |

Against the 1000W unit already bought, that's **~270-280W (27-28%) headroom** — tighter than the original ~400W/40% margin, but still comfortably within a safe continuous-load range for a Gold-rated unit. No PSU change needed for this addition. This is a materially different situation from the "add a second RTX 5080" scenario discussed for future GPU expansion ([gpu.md](gpu.md)) — that hypothetical adds another ~300W and would actually require a PSU upgrade to 1200-1400W; the 970's ~145W does not.

## Why the MSI MAG A1000GLS PCIE5

Picked over a wide field of 850-1000W Gold-tier options surveyed directly at Micro Center (500W-1650W, every tier from PowerSpec budget units to Corsair HXi/Seasonic Titanium):

- **Externally verified**: Micro Center's in-store rating was only 4.9/5 off 6 units (too thin to trust). Cross-checked against Newegg's **4.5/5 across 7,144+ reviews** — the deepest, most trustworthy sample of any PSU considered.
- **Native 12V-2x6 connector confirmed** for RTX 50-series GPUs, rated for 600W delivery — no 8-pin→16-pin adapter needed for the RTX 5080 pick in [gpu.md](gpu.md).
- Measured clean: 12V rail holds 11.98-12.04V under load, 15.2mV ripple, ~46°C under full load. Built on a reputable CWT (Channel-Well Technologies) platform, tests out at Cybenetics Platinum-level despite the Gold label.
- **Cheaper than most of the 850W tier** it's being compared against (Corsair RM850x $149.99, ASUS TUF Gaming 850W $149.99, Seasonic FOCUS V4 GX-850 $159.99) while delivering more wattage — an easy win once the rating was verified.
- Real trade-off: no idle-fan-stop, so it spins continuously (audible above ~70% load), and uses mid-tier rather than premium Japanese capacitors. Neither is disqualifying for a server PSU that mostly runs at moderate, steady load.
- Runner-up if brand pedigree matters more than the last $37: Corsair RM1000x (SKU 769588, $166.99, 4.9/5, ATX 3.1) — the standard "safe pick" in PC building circles, extensively reviewed, but not meaningfully better hardware for this build.

---

## Related

- [cpu_motherboard_ram.md](cpu_motherboard_ram.md) — CPU draw
- [gpu.md](gpu.md) — GPU draw and its own PSU recommendation
- [storage.md](storage.md) — HBA/drive draw
- [case_chassis_rack.md](case_chassis_rack.md) — chassis this PSU mounts in
