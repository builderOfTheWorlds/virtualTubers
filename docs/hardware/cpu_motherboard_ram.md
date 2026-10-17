# CPU, Motherboard & RAM — Decision

> **Written**: 2026-08-02 · **Updated**: 2026-08-03 — final pick diverged from the bundle path below. See [Final decision](#final-decision-2026-08-03) for what was actually bought.
> **Scope**: Compute platform for the dedicated build described in [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md). Supersedes that doc's original Ryzen 7 9700X pick.

---

## Final decision (2026-08-03)

| Part | Pick | Price | Source |
|---|---|---|---|
| CPU | AMD Ryzen 9 **9950X3D2** (16C/32T, dual V-Cache, 200W) | $869.99 | Micro Center, SKU 017368 |
| Motherboard | Gigabyte **X870E AORUS MASTER X3D ICE** | $549.99 | Micro Center, SKU 927608 |
| RAM | Crucial Pro **128GB (2x64GB) DDR5-5600 CL46** UDIMM (CP2K64G56C46U5) | $1,799 | Amazon — see [why not Micro Center](#ram-sourcing-micro-center-vs-amazon) |
| **Total** | | **$3,218.97** | |

This supersedes the bundle-path numbers below (kept for the reasoning trail — the CPU-only and mobo-only prices above are what actually got bought, not the $1,099.99 bundle). Two things changed the plan from the original bundle recommendation:

1. **RAM capacity requirement grew to 128GB**, driven by adding VM workloads (Windows + Linux GUI VMs) on top of the container stack. A 128GB target doesn't compose well with a bundle that locks in a fixed 32GB kit — you'd either mismatch DIMMs or throw away the bundled RAM. Buying CPU, board, and RAM as separate line items (losing the ~$150-350 bundle discount) was the more economical path once the target moved past what one matched-kit upgrade could reach cleanly.
2. **The 9950X3D2 was kept over the plain 9950X** specifically because, once CPU+mobo were being bought unbundled anyway, the ~$900 premium (see [Final pick: 9950X3D2](#final-pick-9950x3d2-not-the-plain-9950x) below) was evaluated on its own terms rather than against a bundle discount that no longer applied.

Micro Center's CPU+mobo+RAM bundle pricing (below) was the starting point for this research — bundles reliably beat buying the three parts separately by $150-350, even after the memory-shortage price hikes — but the final purchase didn't end up using it, for the reasons above.

### Original bundle-path pricing (superseded)

| Part | Pick | Price |
|---|---|---|
| CPU | AMD Ryzen 9 **9950X** (16C/32T, 170W) | bundled |
| Motherboard | ASUS **X870E Creator ProArt** | bundled |
| RAM (bundle) | 32GB DDR5-6000 | bundled |
| Bundle total | 9950X + X870E Creator ProArt + 32GB | **$1,099.99** |
| RAM upgrade | +1× matched 32GB kit → **64GB total** | ~$450-500 |

## Why 9950X, not the originally-planned 9700X

The original hardware doc sized the build around an 8C/16T 9700X for a narrow, fixed workload (6-8 workers + Kafka/Postgres/Redis + Ollama). That calculus changed once the box was reframed as a **general "main server"** — absorbing services currently split across `mafober` and other boxes, with more applications expected to land on it over time.

Thread count, not per-core speed, is the relevant lever for "more concurrent services," so the 9950X's 16C/32T (double the 9700X) was the right upgrade — not a 3D-cache chip (see below), which doesn't add threads.

| | 9700X | 9950X |
|---|---|---|
| Cores/Threads | 8C/16T | 16C/32T |
| TDP | 65W | 170W |
| Bundle price (32GB) | ~$700-730 | $899.99 (B850 board) / $1,099.99 (X870E board) |

Tradeoff accepted: TDP triples (65W → 170W), which raises the ongoing electricity cost estimated in the original hosting doc (~$15/month at 9700X-era power draw no longer holds once this box is running significantly more services regardless of which CPU is picked).

## Why X870E over B850

Two things pushed the pricier X870E Creator ProArt bundle ($1,099.99) over the cheaper B850 Creator ProArt ($899.99):

1. **PCIe slot count** — the box needs room for both the GPU and the reused LSI 9207-8i HBA card (see [storage.md](storage.md)) in separate full-length slots simultaneously. X870E boards typically carry 2-3 full-length PCIe slots; B850 boards are tighter.
2. Headroom for whatever else ends up needing a PCIe slot as the "main server" role grows (10GbE NIC, additional storage controller, etc. — see the future rack/networking notes in [case_chassis_rack.md](case_chassis_rack.md)).

This reasoning held at the chipset-tier level (X870E over B850) even after the specific board model changed — see below.

## Why AORUS MASTER X3D ICE, not Creator ProArt

The ASUS Creator ProArt was the board priced into the original bundle, but the final purchase used the Gigabyte **X870E AORUS MASTER X3D ICE** instead — a board swap driven by buying CPU/board/RAM unbundled (see [Final decision](#final-decision-2026-08-03)) rather than any problem with the Creator ProArt itself. Verified specs on the actual board bought:

| Spec | AORUS MASTER X3D ICE |
|---|---|
| PCIe slots | 3 full-length: **PCIEX16** (CPU-direct, PCIe 5.0 x16, drops to x8 once slot 2 is populated), **PCIEX8** (shares the same CPU lanes as slot 1, runs x8), **PCIEX4** (chipset-fed, PCIe 4.0 x4) |
| M.2 slots | 5 total: 2x PCIe 5.0, 3x PCIe 4.0 |
| Native SATA 6Gb/s | **2 ports** — see the correction in [storage.md](storage.md); this is *fewer* than the 4 ports this doc originally assumed generically for "AM5 motherboards" (that assumption was based on the Creator ProArt candidate, not this actual board) |
| Onboard networking | Realtek **10GbE** + Realtek **5GbE** (dual LAN), MediaTek **WiFi 7** (MT7927) + Bluetooth 5.4 |

Two things this resolves cleanly:

- **No add-in NIC needed, ever.** The "headroom for a future 10GbE NIC" reasoning above (point 2) is fully satisfied by onboard networking on this board — the 3 full-length slots were never going to have a NIC competing for one of them.
- **The 3 full-length slots ended up fully committed anyway** — GPU (RTX 5080) + second GPU (GTX 970, see [gpu.md](gpu.md)) + the LSI HBA, one per slot. No slot is spare, but nothing (network or otherwise) was left needing one.

The 2-native-SATA-port number is a real, if minor, downgrade from what this doc assumed when comparing chipset tiers — it doesn't change the X870E-over-B850 conclusion, but it makes the LSI HBA (see [storage.md](storage.md)) more firmly mandatory, not less, since only 2 of the 6 reused drives could connect without it.

## Final pick: 9950X3D2, not the plain 9950X

3D V-Cache chips (9800X3D, 9850X3D, 9950X3D2) were evaluated specifically because the box now runs a mixed bag of services rather than one narrow workload.

- **Plain X3D chips (9800X3D, 9850X3D)** — still only 8C/16T, same as the 9700X. The cache boost helps gaming/single-thread-latency workloads, not "more concurrent Docker services," which is a threads problem. Also carry an asymmetric-CCD design (one CCD has the cache, the other runs faster with no cache), adding scheduling complexity with no clear win for a pile of unrelated services. Passed on both.
- **9950X3D2** (dual V-Cache, no CCD asymmetry, 16C/32T, 200W) — genuinely interesting: 13% faster than plain 9950X in Blender (a real multi-threaded compute proxy, not just gaming), and cache-aware scheduling has shown real PostgreSQL throughput/latency gains — directly relevant since Postgres is in this stack. Originally passed on (2026-08-02) when compared bundle-to-bundle: the cheapest X3D2 bundle was $1,349.99 against the plain 9950X's $1,099.99 bundle, a $450 premium for a ~10-15% gain.
- **Reversed 2026-08-03**: once the RAM-capacity requirement (see [Final decision](#final-decision-2026-08-03)) forced buying CPU/board/RAM unbundled anyway, the bundle discount that made the X3D2 look like a $450 premium no longer applied to either option. Compared à la carte, the CPU-only price gap is $869.99 (9950X3D2) vs. $489.99 (plain 9950X, Micro Center CPU-only) — a real ~$380 premium, kept deliberately for the Postgres cache-locality benefit given the box already runs Postgres 24/7. Verified against real benchmark data (PassMark CPU Mark), not just the vendor's own Blender figure — see [cpu_benchmark_comparison.md](cpu_benchmark_comparison.md) for the full price/performance table across every CPU considered in this build, including this one. By that measure the 9950X3D2 is actually the **worst** $/performance pick in the mainstream tier ($11.98 per 1,000 CPU Mark points) — the purchase is deliberately not optimizing for that metric, trading it for the cache-locality and RAM-path reasons above.

**New gap flagged, not yet acted on**: the plain **Ryzen 9 9950X3D** (single V-Cache CCD, asymmetric design, 16C/32T — distinct from the dual-cache 9950X3D2 above) sits in price and PassMark score between the plain 9950X and the 9950X3D2, and was never evaluated here. Worth a look if the 9950X3D2's premium ever becomes a concern; its ~$630 price is from an older listing and needs re-verification before treating it as current.

## RAM sizing — revised to 128GB (2026-08-03)

The 64GB target below (bundle's 32GB + a matched 32GB upgrade kit) was superseded once VM workloads entered scope: at least one Windows and one Linux GUI VM (general-purpose use — VS Code, docs, browsing — not GPU-heavy), on top of the existing container stack. Actual headroom math, using the original sizing this doc already established plus what's known about the services being folded in:

| Consumer | RAM |
|---|---|
| virtualTubers stack (8 workers + Kafka/Postgres/Redis) | ~20-24GB (original sizing) |
| `mafober` stack (Plex, Grafana, Prometheus, Portainer/Docker, qBittorrent) | fits in 12GB *total* on its current 2012-era box — generously ~10-12GB on the new host |
| 2 GUI VMs (Windows 8-16GB, Linux 4-8GB) | ~12-24GB |
| **Total committed** | **~42-60GB** |

Against 128GB, that leaves roughly **68-86GB (over half) still free** even before accounting for any conservatism in the above. 64GB would have been tight once the VMs were added; 128GB was bought instead for comfortable headroom, not because the arithmetic demanded exactly 128.

### Final RAM pick

**Crucial Pro 128GB (2x64GB) DDR5-5600 CL46 UDIMM, CP2K64G56C46U5** — bought via Amazon at $1,799 (see [RAM sourcing](#ram-sourcing-micro-center-vs-amazon) below for why not Micro Center).

- **Only populates 2 of the board's 4 DIMM slots**, leaving room to add a second matched 2x64GB kit later for the full 256GB the board and platform support — see below — and sidestepping the 4-DIMM speed-drop penalty entirely (that penalty only applies once all 4 slots are populated).
- **Runs at DDR5-5600 CL46, not the 6000 MHz sweet spot** the 32GB-kit survey below assumed. This is a density trade-off, not a compatibility problem — 64GB-per-stick modules top out lower than 32GB sticks across every vendor at this point in the memory-shortage cycle (see [lessons_learned.md](lessons_learned.md)), not something specific to Crucial.
- **Max memory confirmed**: 256GB on both the AORUS MASTER X3D ICE (4x64GB, per Gigabyte's own spec page) and the AM5/9950X3D2 platform generally (same socket/memory controller as the plain 9950X3D, confirmed at 256GB; the X3D2-specific ceiling wasn't found documented anywhere but is very likely identical).

### RAM sourcing: Micro Center vs. Amazon

This exact kit (CP2K64G56C46U5) is also listed at Micro Center, SKU 807107 — but at **$3,999.99**, more than double the $1,799 Amazon price, and shown **sold out with no shipping or pickup available** at the North Jersey store regardless. This is the single largest price/availability gap found anywhere in this build's research — see the new entry in [lessons_learned.md](lessons_learned.md). Buy from Amazon.

Two additional single-listing inconsistencies caught on that same Micro Center page, neither disqualifying, both logged per this project's verify-don't-trust-one-listing habit:

- The spec table lists "Memory Speed: PC5-52800" directly next to "Memory Speed (MHz): DDR5-5600" and the title's "PC5-44800" — PC5-52800 doesn't correspond to 5600 MHz (5600×8=44,800) or any of the kit's other speed options. Straightforward internal contradiction on Micro Center's own page.
- "Memory Profiles" lists only "Intel XMP," though Crucial's own product page and general retail listings say the module supports both XMP 3.0 and AMD EXPO. Likely an incomplete field on Micro Center's table, not a real EXPO limitation — worth a quick BIOS check once the kit is in hand.

---

## RAM Pricing & Options (Historical survey — 32GB/48GB/64GB tiers, superseded by the 128GB pick above)

> **Updated**: 2026-08-02

### Bundle Kit (included in CPU+Mobo bundle)
The CPU/mobo bundle includes **32GB DDR5-6000** (specifications vary by bundle variant — confirm exact model at purchase).

### Matched Upgrade Kits (32GB) — For 64GB total

Pick a kit with matching **speed (6000 MHz), CAS latency (CL36 or tighter)**, and **brand** to the bundled kit to avoid stability issues on a 4-DIMM board:

| Kit | Spec | Price | Notes |
|---|---|---|---|
| KLEVV BOLT V | 32GB DDR5-6000 CL30 | $389.99 | Best value; tighter latency |
| G.Skill Flare X5 | 32GB DDR5-6000 CL36 | $419.99 | Budget-friendly, widely available |
| Corsair VENGEANCE RGB | 32GB DDR5-6000 CL36 | $464.99 | RGB, solid performance |
| TeamGroup T-FORCE DELTA RGB ECO | 32GB DDR5-6000 CL38 | $459.99 | Value RGB option |
| G.Skill Flare X5 (CL28) | 32GB DDR5-6000 CL28 | $499.99 | Premium tier, tighter latency |
| Corsair VENGEANCE RGB (CL30) | 32GB DDR5-6000 CL30 | $599.99 | Premium RGB, tighter latency |

**Recommendation**: KLEVV BOLT V ($389.99) or G.Skill Flare X5 ($419.99) for best value; verify the bundled kit's brand/latency first and match it.

### 48GB "Middle Ground" Kits — Alternative to 64GB

Skip adding a second 32GB stick entirely and jump to a single 48GB kit (two 24GB DIMM sticks) to avoid the 4-DIMM memory-speed penalty:

| Kit | Spec | Price | Stock | Notes |
|---|---|---|---|---|
| Corsair VENGEANCE RGB | 48GB DDR5-8400 CL40 | $955.99 | 1 | High-speed option, avoids speed penalty |
| Corsair VENGEANCE RGB | 48GB DDR5-8800 CL42 | $960.99 | 1 | Highest speed available in this capacity |

**Trade-off**: 48GB sits between 32GB and 64GB; avoids the 4-DIMM speed downgrade but leaves 16GB unused on the board. Viable if you want more than 32GB without the stability/speed risk of 4 DIMMs.

### 64GB Kit Options — Single purchase alternative

If buying 64GB outright instead of upgrading later:

| Kit | Spec | Price | Notes |
|---|---|---|---|
| Corsair VENGEANCE RGB | 64GB DDR5-6000 CL30 | $1,499.99 | Dual-rank, mature option |
| G.Skill Trident Z5 RGB | 64GB DDR5-6400 CL32 | $1,049.99 | Higher speed, lower latency, open box: $892.96 |
| Crucial Pro | 64GB DDR5-6000 CL40 | $999.99 | Value tier |

### Supply & Considerations

- All above options show **25+ in stock** or limited quantities — confirm availability before purchasing.
- X870E Creator ProArt board supports **EXPO profiles** (AMD memory tuning); most DDR5-6000 kits are EXPO-rated, allowing stable 6000 MHz operation at rated timings.
- **4-DIMM penalty**: Populating all 4 DIMM slots may force a downgrade from 6000 MHz to 5600 MHz depending on the board's memory controller. Test after installation; some boards allow tuning back to 6000 MHz via BIOS (EXPO or manual).

---

## Related

- [gpu.md](gpu.md) — GPU pick, the second GPU (GTX 970) added for VM passthrough, and the PCIe-slot interaction with the HBA
- [storage.md](storage.md) — the HBA this board needs a free slot for, and the corrected native-SATA-port count
- [case_chassis_rack.md](case_chassis_rack.md) — physical chassis this board goes into
- [cpu_benchmark_comparison.md](cpu_benchmark_comparison.md) — PassMark price/performance table across every CPU considered for this build
- [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md) — original hosting decision (CPU pick has since evolved from that doc's 9700X)
