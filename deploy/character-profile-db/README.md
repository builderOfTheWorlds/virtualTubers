# character-profile-db

The Postgres database for the character generator and updater, with the
pgvector extension. It runs as a Portainer stack in CT 101 on mafober
(192.168.1.120, port 5433). It holds the `character_profile` database, owned
by its own `character_profile` role: baselines, backstories, experiences,
daily summaries, memory fragments, and the `character_jobs` /
`character_artifacts` tables. Design: `.claude/prompts/character_generator_updater_v4.md` §6.

Two existing databases are separate and unchanged: the 3-layer generator's
`generator-postgres` (alpine, no pgvector) and the stack-wide `virtualtubers`
database on :5432.

## Prerequisites

- A root shell on mafober, the Proxmox VE host (192.168.1.117).
- CT 101 (the Portainer LXC, 192.168.1.120) running, and the parent dataset
  `tank_0/utilities` present.
- Portainer at https://192.168.1.120:9443.
- Host port 5433 free on CT 101 and reachable from the machines that connect
  (gx10 at 192.168.1.23, the dev PC). When checked on 2026-09-24, unused ports
  on 192.168.1.120 were filtered from the LAN, so you may need a firewall
  rule. If 5433 is taken, set `CHARACTER_DB_PORT` in the stack and pass
  `DB_PORT=<port>` to `install.sh --verify`.

## Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | The stack. `db` runs `pgvector/pgvector:0.8.6-pg16-bookworm`. `init` is a one-shot service that creates the role, the database and the `vector` extension. It's idempotent and re-runs on every stack update. |
| `.env.example` | The stack environment to paste into Portainer. |
| `scripts/install.sh` | Run on mafober **before** deploying. It creates the ZFS dataset (16K records), sets its owner to 999:999 and bind-mounts it into CT 101. `--verify` checks everything after the deploy. |
| `scripts/uninstall.sh` | Removes the bind mount and keeps the data. `--purge-data` also destroys the dataset, after you type its name. |
| `tests/run_tests.sh` | Tests both scripts off-host with mocked `pct`/`zfs` (51 checks). |

## Install

1. Copy the package to mafober and do a dry run:
   ```bash
   scp -r deploy/character-profile-db root@192.168.1.117:/root/
   ssh root@192.168.1.117 'bash /root/character-profile-db/scripts/install.sh --dry-run'
   ```
2. Run it on mafober:
   ```bash
   bash /root/character-profile-db/scripts/install.sh
   ```
   - Exit 0: the mount is live inside CT 101, so continue.
   - Exit 3: the mount is configured but not live yet. At a quiet time run
     `install.sh --reboot-ct`, which asks you to type `reboot 101`. The
     reboot restarts **every** container in CT 101: Portainer, Gitea, the
     :5432 Postgres, Plex, qBittorrent, Grafana and Prometheus.

   Don't deploy the stack until the mount is live. Otherwise Postgres writes
   into CT 101's own filesystem instead of the dataset (see
   `projectManager/mafober_summary.md`, lessons 1, 2 and 8).
3. In Portainer, go to Stacks → Add stack and:
   - Name it `character-profile-db`.
   - Paste `docker-compose.yml` into the web editor.
   - Under Environment variables → Advanced mode, paste `.env.example` with
     real passwords.
   - Deploy.
4. Back on mafober:
   ```bash
   bash /root/character-profile-db/scripts/install.sh --verify
   ```
   This checks five things: the mount is live, `db` is healthy, `init` exited
   0, the database is owned by `character_profile`, and pgvector is installed.
   It also checks that the data files are on the dataset, then prints the
   connection settings.

## Connect

```bash
psql "host=192.168.1.120 port=5433 dbname=character_profile user=character_profile"
```

Services read `CHARACTER_DB_HOST`, `CHARACTER_DB_PORT`, `CHARACTER_DB_NAME`,
`CHARACTER_DB_USER` and `CHARACTER_DB_PASSWORD`.

**Rotating the app password:** change `CHARACTER_DB_PASSWORD` in the stack
environment, then Update the stack. `init` re-applies it.

## Uninstall

1. Delete the stack in Portainer. `uninstall.sh` refuses to run while either
   of its containers still exists.
2. Run one of these on mafober (add `--dry-run` to preview):
   ```bash
   bash /root/character-profile-db/scripts/uninstall.sh              # removes the mount, keeps the data
   bash /root/character-profile-db/scripts/uninstall.sh --purge-data # also destroys the dataset + snapshots
   ```
   `--purge-data` refuses while the old mount is still live inside CT 101,
   because a removed mount stays until the CT reboots. It then asks you to
   type the dataset name.

## Backups

**Not set up yet.** Plan v4 §5 makes backups part of the nightly
`daily-maintenance` job: a `pg_dump -Fc` of `character_profile` plus ZFS
snapshots of the dataset. Set that up, and do one restore drill, before the
pilot stores anything. Memory fragments are LLM output that can't be
regenerated.

## Tests

```bash
bash deploy/character-profile-db/tests/run_tests.sh
```

The tests put mocks for `pct`, `zfs`, `id`, `stat`, `chown` and `timeout` on
`PATH`, so they need no root or Proxmox access and change nothing on the real
system. Git-bash on Windows works.

## Notes

- The image is pinned to `0.8.6-pg16-bookworm` (amd64 and arm64). Moving to
  another Debian codename changes glibc's collation rules, so REINDEX the text
  indexes if you ever do. Never reuse a data directory from an alpine (musl)
  Postgres.
- CT 101 is privileged, so UID 999 inside the container is UID 999 on the
  host. That's why the dataset is owned by `999:999`.

## References

- `projectManager/mafober_summary.md`: mafober hardware, ZFS layout, CT 101,
  and the lessons learned.
- `projectManager/docs/install_sh_guide.md`: the install-script conventions
  these scripts follow.
