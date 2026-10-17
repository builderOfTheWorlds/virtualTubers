# Storage — Decision

> **Written**: 2026-08-02
> **Scope**: Storage for the dedicated build described in [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md).

---

## Decision

| Component | Source | Cost |
|---|---|---|
| Data pool | **Reused from `mafober`**: 6x Samsung PM883 1.92TB 2.5" SATA enterprise SSDs | $0 |
| HBA | **Reused from `mafober`**: LSI 9207-8i (IT mode, SFF-8087 → SATA breakout) | $0 |
| Boot drive | **Inland TN320 256GB NVMe** PCIe Gen3 x4, M.2 2280 (SKU 529750) | **$59.99** (orig $199.99) |

11.52TB raw / ~7.68TB usable in RAIDZ2 — inherited from `mafober`'s existing `tank_0` pool, at zero additional hardware cost.

### Why the Inland TN320 256GB for boot

256GB is plenty since this drive only holds Proxmox VE + OS — all real data (Plex media, Postgres, Kafka, container volumes, etc.) lives on the reused `tank_0` pool above, not on the boot drive. PCIe Gen3 speed is fine for a boot role; nothing here is throughput-sensitive the way the data pool is. 150TBW endurance and a 6-year parts/labor warranty are comfortably more than a boot-only drive will ever need. At $59.99 (marked down from $199.99, 4.6/5 across 88 reviews), it lands at the low end of the ~$0-60 range this line was already budgeted at.

Install it in **M.2_1** specifically, not a chipset-shared M.2 slot. This guidance was originally confirmed against the ASUS ProArt X870E-CREATOR WIFI's spec sheet — since the final board pick moved to the Gigabyte X870E AORUS MASTER X3D ICE (see [cpu_motherboard_ram.md](cpu_motherboard_ram.md)), the *principle* still holds (put boot storage on a CPU-direct M.2 slot, not one sharing lanes with the HBA/GPU slots) but the specific M.2_1 lane-sharing map needs re-confirming against the Gigabyte board's own manual before install — not yet done. The Gigabyte board has 5 M.2 slots total (2x PCIe 5.0, 3x PCIe 4.0), so a CPU-direct option should exist; which physical slot number that is on this board is unverified.

A SATA boot drive was considered and rejected: **the board's native SATA ports go entirely unused in this build already** (all 6 data drives route through the HBA, not the board's native ports) — and a SATA boot drive would either consume one of the chassis' 8 hot-swap bays meant for the data pool/spares, or need a separate internal mount most 4U chassis don't have. M.2 sidesteps both problems for free. (Original text here said "4 native SATA ports go unused" — corrected below to 2; doesn't change this conclusion either way, since the boot drive was never going onto a native SATA port regardless of the exact count.)

## Why reuse, and what it actually means

Pulling these drives isn't a simple storage upgrade — `tank_0` **is** `mafober`'s Plex media, qBittorrent downloads, Grafana data, Postgres, Kafka, Gitea, the public reverse-proxy config, and SFTP access (full detail: [../environment/mafober_summary.md](../environment/mafober_summary.md)). There is no version of this where the drives move and `mafober` keeps functioning as-is — taking the pool means **retiring `mafober`'s current role and migrating everything on it** to the new box.

## Why the HBA is not optional

> **Corrected 2026-08-03**: this section originally said the AM5 motherboards evaluated exposed 4 native SATA ports — that figure came from the ASUS Creator ProArt candidate. The board actually bought, the Gigabyte **X870E AORUS MASTER X3D ICE**, has only **2 native SATA 6Gb/s ports**, verified against Gigabyte's own spec listing (see [cpu_motherboard_ram.md](cpu_motherboard_ram.md)). This makes the HBA more firmly required, not less — the question was never "do we need the HBA," but the margin is now 2-of-6 native rather than 4-of-6.

Six drives need the LSI 9207-8i's SFF-8087 breakout cables — without it, this build physically cannot connect more than 2 of the 6 drives natively. The HBA also now shares its 3-full-length-PCIe-slot layout with *two* GPUs, not one — the RTX 5080 and a second card (an EVGA GTX 970, reused for VM passthrough — see [gpu.md](gpu.md)) — leaving the HBA the third and last slot, the chipset-fed PCIe 4.0 x4 one. That's still comfortably more bandwidth than the HBA needs (its own native interface is only PCIe 2.0 x8, and 6 SATA SSDs don't get close to saturating even that).

This also means the lesson in [lessons_learned.md](lessons_learned.md) about verifying specs generalized across a category applies here directly: "4 native SATA ports on AM5 boards" was true enough of the original candidate board, but not of the board that actually got bought once the plan changed mid-project — always re-check board-specific specs against the *actual* final SKU, not an earlier candidate in the same chipset tier.

## Migration outline

1. `zpool export tank_0` on `mafober` before pulling drives.
2. Physically move the 6 PM883s + the HBA to the new box. Confirm the X870E board has a free full-length PCIe slot for the HBA alongside the GPU (see [gpu.md](gpu.md) for the GPU's physical footprint).
3. Fresh Proxmox VE install on the new hardware, then `zpool import tank_0` — dataset structure and paths carry over unchanged.
4. Recreate CT 101 (Portainer/Docker host) with the same bind-mount table, privileged/AppArmor/`keyctl`/`nesting`/`fuse` settings, and UID conventions (1000 for linuxserver images, 472 for Grafana) documented in [../environment/mafober_summary.md](../environment/mafober_summary.md) — this is copy-work from that doc, not redesign.
5. **New step `mafober` never needed**: GPU passthrough into CT 101 for Ollama/NVENC/Plex hardware transcode — bind-mount the NVIDIA device nodes into the privileged LXC (or use `nvidia-container-toolkit`). No precedent for this in the existing docs.
6. Re-point the public reverse-proxy (`mafober-nj.duckdns.org`) and re-register the Gitea Actions runner against the new host's IP once stable.

## Open question

Whether the virtualTubers Docker stack (Kafka/Postgres/Redis/8 workers) joins CT 101 alongside Plex/Grafana/etc., or gets its own CT — not yet decided.

---

## Related

- [cpu_motherboard_ram.md](cpu_motherboard_ram.md) — motherboard the HBA needs a free slot in, and the corrected native-SATA-port count
- [gpu.md](gpu.md) — the two GPUs now sharing the same 3-slot PCIe layout as the HBA
- [case_chassis_rack.md](case_chassis_rack.md) — chassis needs to physically fit 6-7 2.5" drives plus two GPUs
- [../environment/mafober_summary.md](../environment/mafober_summary.md) — full detail on what's currently running on the pool being migrated
