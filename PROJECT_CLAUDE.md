# virtualTubers — Project-Specific Notes

> This file holds notes specific to this project only. Unlike `CLAUDE.md`, it is
> never synced from or to the master template — edit it freely.

## Deployment target: d2000 (overrides CLAUDE.md's generic Mafober guidance)

`CLAUDE.md`'s shared "Mafober Deployment Environment" section describes the
default homelab deploy target for *new* projects on this machine — it does
**not** apply to virtualTubers. This project runs on **d2000** instead:

| Item | Value |
|------|-------|
| Hostname | `d2000` |
| IP Address | `192.168.2.158` |
| OS | Windows, Docker Desktop |
| Stack management | Plain `docker compose` (no Portainer) |
| Repo checkout | `C:\Users\matt\PycharmProjects\virtualTubers` |
| Kafka | Runs on d2000, `192.168.2.158:9092` |
| Postgres | Runs on d2000, `192.168.2.158:5432` |
| Redis | Runs on d2000 (bundled `redis` service in `docker-compose.yml`) |

Full deploy workflow (env vars, build/redeploy steps) lives in the README's
[Deployment (Docker Compose on d2000)](README.md#deployment-docker-compose-on-d2000)
section — that's the source of truth, keep this note and that section in sync
if either changes.

Gitea (source control mirror) is unaffected by this — it still lives on
`mafober` (`192.168.1.120`), per the README's
[Git Remotes & GitHub Mirror](README.md#git-remotes--github-mirror) section.
Only the app's runtime stack (workers, message-logger/api, Kafka, Postgres,
Redis) moved to d2000.
