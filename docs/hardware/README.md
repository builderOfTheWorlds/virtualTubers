# Hardware — Build Documentation Index

> **Written**: 2026-08-02 · **Updated**: 2026-08-03 — CPU/board/RAM final pick changed, a second GPU was added, and two spec corrections landed. See [Current build summary](#current-build-summary) for the current state.
> This directory documents the detailed parts-level decisions for the dedicated box described at a hosting/strategy level in [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md). That doc's specific parts picks (Ryzen 7 9700X, RTX 5060 Ti, ~$1,325) are **superseded** by what's here — GDDR/DRAM price hikes and a scope change (this box became a general "main server," not just a campaign-platform-dedicated one) moved the build significantly since that doc was written.

## Docs in this directory

| Doc | Covers |
|---|---|
| [cpu_motherboard_ram.md](cpu_motherboard_ram.md) | Final pick: Ryzen 9 **9950X3D2**, Gigabyte **X870E AORUS MASTER X3D ICE**, **128GB** DDR5-5600 — why the plan moved off both the original 9700X and the originally-bundled plain 9950X/Creator ProArt/64GB path |
| [gpu.md](gpu.md) | RTX 5080 pick, full Micro Center catalog survey (100+ SKUs across NVIDIA/AMD/Intel), the AMD R9700 detour reversed by real benchmark data, a **second GPU** (reused GTX 970) added for VM passthrough, and a 2-year GPU-expansion outlook |
| [cpu_benchmark_comparison.md](cpu_benchmark_comparison.md) | **New** — real PassMark price/performance table across every CPU considered in this build, quantifying what the 9950X3D2 pick actually costs vs. plain-9950X and Intel alternatives |
| [storage.md](storage.md) | Reusing `mafober`'s 6x PM883 SSDs + LSI HBA, the migration this actually requires, and a corrected native-SATA-port count |
| [case_chassis_rack.md](case_chassis_rack.md) | Why the free Antec case got measured out, the pivot to a 4U rackmount chassis + open-frame rack, and the raised-stakes clearance check now that there are 3 full-length cards, not 2 |
| [power_supply.md](power_supply.md) | PSU sizing across the whole system, updated for the CPU swap and second GPU |
| [lessons_learned.md](lessons_learned.md) | Methodology notes — verification habits worth keeping for future hardware research |
| [cpu_intel_and_hedt_pricing.md](cpu_intel_and_hedt_pricing.md) | Intel Arrow Lake/Raptor Lake pricing vs. the 9950X pick, plus Threadripper/Xeon-WS HEDT survey — 9950X-family pick stands |
| [power_efficient_scaling_1_to_10_tubers.md](power_efficient_scaling_1_to_10_tubers.md) | **New (2026-08-09)** — per-worker CPU/RAM/power scaling from 1-10 concurrent tubers, hosting options ranked by watts (not by what the `argyreServer` fleet is optimized for), and an ARM feasibility check that overturns this project's earlier "ARM breaks the Docker stack" assumption |
| [encoding_only_minimum_cpu_8_tubers.md](encoding_only_minimum_cpu_8_tubers.md) | **New (2026-08-09), corrected same day** — isolates just the ffmpeg encode step via real measured benchmarks; original 1080p "N305 has headroom" read undercounted real per-process overhead (corrected in §5) — at 1080p even N305 is short, but a 720p30 comparison (also §5) shows that resolution drop is what actually makes a ~15W N305-class box viable for realistic (non-worst-case) content |
| [full_tuber_capacity_benchmark.md](full_tuber_capacity_benchmark.md) | **New (2026-08-09)** — real concurrent sweep finding `mk_workstation`'s (Ryzen 7 1700X) breaking point for encode+audio-mix (~8-9 tubers at 1080p30, ~20-22 at 720p30), plus [scripts/bench_full_tuber_capacity.sh](../../scripts/bench_full_tuber_capacity.sh) — a fleet-portable script that adds the still-unmeasured Xvfb/terminal-render piece once a one-time root package install is done on each machine |

## Current build summary

| Category | Pick | Price |
|---|---|---|
| CPU | AMD Ryzen 9 **9950X3D2** (16C/32T, dual V-Cache, 200W) | $869.99 |
| Motherboard | Gigabyte **X870E AORUS MASTER X3D ICE** | $549.99 |
| RAM | Crucial Pro 128GB (2x64GB) DDR5-5600 CL46 (Amazon) | $1,799 |
| GPU (primary) | Gigabyte RTX 5080 WINDFORCE SFF OC Triple Fan 16GB | $1,199.99 |
| GPU (secondary, VM passthrough) | Reused: EVGA GTX 970 4GB | $0 |
| Storage (data) | Reused: 6x PM883 1.92TB + LSI 9207-8i HBA | $0 |
| Storage (boot) | Inland TN320 256GB NVMe | $59.99 |
| PSU | MSI MAG A1000GLS PCIE5 1000W 80+ Gold | $129.99 |
| Server chassis | 4U rackmount, 8-bay hot-swap (Rosewill/RackChoice) — **or** the SilverStone RM52 5U that appeared in the actual shopping cart; not yet confirmed which | ~$200-300 or $579.99 |
| Rack | 12-25U open-frame 4-post | ~$150-350 |
| Cooler | Thermalright Peerless Assassin 120 SE | $49.99 |
| **Estimated total** | | **~$5,010-5,540** |

This is a substantial jump from the 2026-08-02 estimate (~$3,340-3,690), driven almost entirely by three 2026-08-03 decisions: buying CPU/board/RAM unbundled to reach 128GB (see [cpu_motherboard_ram.md](cpu_motherboard_ram.md)), keeping the 9950X3D2 instead of the plain 9950X once that bundle discount no longer applied, and sourcing the RAM at $1,799 (Amazon) after Micro Center's own listing for the same kit came back at $3,999.99 and out of stock. The second GPU (GTX 970) added **$0**, since it's reused hardware.

GPU line originally increased from $929.99 (Zotac RTX 5070 Ti) after Micro Center raised that card's price and the case-clearance constraint was separately relaxed — see [gpu.md](gpu.md) for the full pivot reasoning. Storage (boot), PSU, and Cooler remain locked-in specific SKUs — see [storage.md](storage.md), [power_supply.md](power_supply.md), and [case_chassis_rack.md](case_chassis_rack.md).

Software/architecture side: staying on **Proxmox + LXC** for the existing container stack, migrating CT 101's existing structure from `mafober` rather than rebuilding — plus **full KVM/QEMU VMs** (at least one Windows 11, one Linux, both GUI, both general-purpose/non-GPU-intensive use) running alongside the LXC containers on the same Proxmox host. Proxmox natively supports both side by side; no architecture change needed. CPU/RAM headroom for this was checked against the existing service-sizing numbers in [cpu_motherboard_ram.md](cpu_motherboard_ram.md) and [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md) — comfortable margin on both (roughly half the RAM pool and 15-35% of CPU threads still free after the VMs). GPU acceleration for the VMs is handled by the second GPU (GTX 970) above, passed through to the Windows VM specifically, so it doesn't contend with the primary 5080's Ollama/Plex role.

## Open items before ordering

- [ ] Re-verify the Gigabyte RTX 5080 WINDFORCE SFF OC's current price/stock at the North Jersey store — confirmed once, but everything else in this build has shown prices moving week to week.
- [ ] **Confirm which chassis is actually being bought** — this doc's researched pick (Rosewill/RackChoice 4U) vs. the SilverStone RM52 5U that showed up in the real shopping cart. Not the same decision; needs resolving before the next two items can be checked.
- [ ] Confirm the specific chassis and rack depths match once the exact model above is settled.
- [ ] **Verify clearance for three full-length cards** (RTX 5080 + GTX 970 + LSI HBA), not two — airflow between the two GPUs specifically, and whether either GPU's cooler blocks physical access to the HBA's slot. Higher-stakes version of the original GPU-vs-HBA clearance check, see [case_chassis_rack.md](case_chassis_rack.md).
- [ ] Re-verify the cooler's 155mm height against the specific chassis' published clearance once it's chosen (Peerless Assassin 120 SE picked partly for height margin, but not yet checked against an actual chassis spec sheet).
- [ ] Re-confirm the boot NVMe's correct CPU-direct M.2 slot number against the Gigabyte AORUS MASTER X3D ICE's actual manual — the original M.2_1 guidance in [storage.md](storage.md) was confirmed against the ASUS Creator ProArt candidate, not the board actually bought.
- [ ] Decide whether the virtualTubers Docker stack joins CT 101 or gets its own CT.
- [ ] Set up GPU passthrough for both the primary 5080 (into CT 101, per [storage.md](storage.md)) and the GTX 970 (into the Windows VM) — including the known Code 43 workaround for the 970 (see [gpu.md](gpu.md)).
