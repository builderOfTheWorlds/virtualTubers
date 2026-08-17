# virtualTubers — Project-Specific Notes

> This file holds notes specific to this project only. Unlike `CLAUDE.md`, it is
> never synced from or to the master template — edit it freely.

## Campaign module — in progress (started 2026-08-16)

A generic campaign layer (`app/campaign/`, `campaigns/`, `tools/qwen_worker/`) is
being built alongside the existing dev-team show. Waves 1–3 are done and green;
Wave 4 (`agent.py` integration, configs, compose, per-module docs) is not.

**If you are picking that work up, read
[docs/campaign_module_status.md](docs/campaign_module_status.md) first** — it
covers the architecture decisions, the local-model worker harness and how to
drive it, the seams left for the deferred weekly-loop/chat-voting work, and the
review checklist for generated code.

Nothing in that module is committed yet.

## Deployment target: argyre, via Portainer (moved off d2000 — 2026-08-16)

This project previously ran on **d2000** via plain `docker compose`. As of
2026-08-16 it has moved to **argyre**, this machine, and is now managed
through **Portainer** (a stack running locally on argyre itself, not the
mafober one referenced in `CLAUDE.md`'s shared boilerplate — that section
still doesn't apply to virtualTubers).

| Item | Value |
|------|-------|
| Hostname | `argyre` (a.k.a. `argyreServer`; actual hostname `gx10-35a4`) |
| IP Address | `192.168.2.170` |
| OS | Ubuntu 24.04 LTS |
| Stack management | Portainer (`portainer/portainer-ce`, local container, ports 8000/9443) — stack name `virtualtubers` |
| Repo checkout | `/home/secus/codeProjects/virtualTubers` |
| Kafka | Bundled container in the stack (`kafka:9092`, internal) |
| Redis | Bundled container in the stack (`redis:6379`, internal) |
| Postgres | External — `192.168.1.120:5432` (mafober), per `.env` `POSTGRES_HOST` |

Full deploy workflow (env vars, build/redeploy steps) lives in
[docs/deployment.md](docs/deployment.md) — that doc and
[README.md](README.md)'s "Deployment" section still describe the old d2000/
plain-compose setup as of this edit and need updating to match; flagged to
the user, not yet done here.

Gitea (source control mirror) is unaffected by this — it still lives on
`mafober` (`192.168.1.120`), per [docs/git_remotes.md](docs/git_remotes.md).
