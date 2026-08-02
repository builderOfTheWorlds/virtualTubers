# Storage — Decision

> **Written**: 2026-08-02
> **Scope**: Storage for the dedicated build described in [../environment/hardware_and_hosting.md](../environment/hardware_and_hosting.md).

---

## Decision

| Component | Source | Cost |
|---|---|---|
| Data pool | **Reused from `mafober`**: 6x Samsung PM883 1.92TB 2.5" SATA enterprise SSDs | $0 |
| HBA | **Reused from `mafober`**: LSI 9207-8i (IT mode, SFF-8087 → SATA breakout) | $0 |
| Boot drive | New NVMe (~500GB-1TB), or reuse `mafober`'s existing boot SSD | ~$0-60 |

11.52TB raw / ~7.68TB usable in RAIDZ2 — inherited from `mafober`'s existing `tank_0` pool, at zero additional hardware cost.

## Why reuse, and what it actually means

Pulling these drives isn't a simple storage upgrade — `tank_0` **is** `mafober`'s Plex media, qBittorrent downloads, Grafana data, Postgres, Kafka, Gitea, the public reverse-proxy config, and SFTP access (full detail: [../environment/mafober_summary.md](../environment/mafober_summary.md)). There is no version of this where the drives move and `mafober` keeps functioning as-is — taking the pool means **retiring `mafober`'s current role and migrating everything on it** to the new box.

## Why the HBA is not optional

The AM5 motherboards evaluated (see [cpu_motherboard_ram.md](cpu_motherboard_ram.md)) only expose 4 native SATA ports. Six drives need the LSI 9207-8i's SFF-8087 breakout cables — without it, this build physically cannot connect more than 4 of the 6 drives.

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

- [cpu_motherboard_ram.md](cpu_motherboard_ram.md) — motherboard the HBA needs a free slot in
- [case_chassis_rack.md](case_chassis_rack.md) — chassis needs to physically fit 6-7 2.5" drives
- [../environment/mafober_summary.md](../environment/mafober_summary.md) — full detail on what's currently running on the pool being migrated
