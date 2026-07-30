# virtualTubers — Project-Specific Notes

> This file holds notes specific to this project only. Unlike `CLAUDE.md`, it is
> never synced from or to the master template — edit it freely.

## Deployment environments

This project moves between multiple runtime environments over time rather
than living permanently on one host — don't assume whichever one a past
session used is still current. **Update the "Currently active" line below
whenever the runtime stack moves**, so a future session doesn't waste time
rediscovering which host is actually live (or, worse, act against a stale
one that just happens to still have containers running).

**Currently active: mafober** (confirmed live 2026-07-28)

Gitea (source control mirror) lives on `mafober` (`192.168.1.120`)
regardless of which environment below is running the app's own stack — see
the README's
[Git Remotes & GitHub Mirror](README.md#git-remotes--github-mirror) section.

### mafober (Portainer-managed)

`CLAUDE.md`'s shared "Mafober Deployment Environment" section covers generic
host facts (IP, Portainer/Proxmox URLs, SSH) common to every project on this
box. What's specific to virtualTubers here is a Portainer git-stack quirk
that has caused real confusion (2026-07-28) and is easy to rediscover the
hard way:

| Item | Value |
|------|-------|
| Deployed via | Portainer stack (**stack id 25** as of 2026-07-28 — re-check if it's changed, see command below) |
| Repo checkout (build images / edit source here) | `/opt/virtualTubers` on the Portainer LXC (CT 101, `192.168.1.120`) |
| **Live bind-mount data** (`replays/`, `config/`, `voices/`) | `/data/compose/<stack-id>/` on CT 101 — **not** `/opt/virtualTubers/` |
| Kafka | `192.168.1.120:9092` |
| Postgres | `192.168.1.120:5432`, db `virtualtubers` |
| Redis | in-stack `redis` service |

**Gotcha: two directories exist, only one is live.** Portainer's git-based
stack deploy materializes `docker-compose.yml`'s relative bind-mount sources
(`./replays`, `./config`, `./voices`) into its own persistent stack
directory (`/data/compose/<stack-id>/...`) — separate from whatever plain
git checkout also happens to exist at `/opt/virtualTubers`. Editing
`app/`/`config/*.yaml`/`docker-compose.yml` and rebuilding images happens in
`/opt/virtualTubers`, same as any normal checkout. But a file meant to be
picked up by a *running* container at one of those bind-mount paths — a
hand-authored test episode especially, since `replays/` is gitignored
project-wide except `replays/coder/sample.json` (docs/replay_pane.md) — has
to be copied into `/data/compose/<stack-id>/...` directly, or it silently
never appears no matter how many times `/opt/virtualTubers` looks right.
Confirm a container's actual live source before trusting either directory:

```bash
docker inspect -f '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}' <container>
```

### d2000

| Item | Value |
|------|-------|
| Hostname | `d2000` |
| IP Address | `192.168.2.158` |
| OS | Windows, Docker Desktop |
| Stack management | Plain `docker compose` (no Portainer — bind mounts resolve exactly where `docker-compose.yml` says, no `/data/compose/<id>` indirection) |
| Repo checkout | `C:\Users\matt\PycharmProjects\virtualTubers` |
| Kafka | Runs on d2000, `192.168.2.158:9092` |
| Postgres | Runs on d2000, `192.168.2.158:5432` |
| Redis | Runs on d2000 (bundled `redis` service in `docker-compose.yml`) |

Full deploy workflow (env vars, build/redeploy steps) lives in the README's
[Deployment (Docker Compose on d2000)](README.md#deployment-docker-compose-on-d2000)
section — that's the source of truth, keep this note and that section in
sync if either changes.

### Production (not yet stood up)

No dedicated prod environment exists yet. When one is created, add its
connection details and any deployment-mechanism quirks (Portainer? plain
compose? something else?) here, in the same shape as the sections above —
and update "Currently active" once app traffic actually moves to it.
