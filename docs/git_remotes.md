# Git Remotes & GitHub Mirror

This repo lives on the homelab **Gitea** instance, which auto-mirrors every push to
GitHub — set up 2026-07-05, so pushing to GitHub by hand is never needed:

| Remote | URL | Role |
|---|---|---|
| `origin` | `ssh://git@192.168.1.120:2222/gitea_admin/virtualTubers.git` | **Source of truth — push here** |
| `github` | `https://github.com/builderOfTheWorlds/virtualTubers` | Read-only mirror target (don't push) |

- A normal `git push` (to `origin`) lands on GitHub within seconds via Gitea's native
  push-mirror (`sync_on_commit: true`), with an 8-hour interval sync as fallback.
- The mirror credential is a fine-grained GitHub PAT stored inside Gitea, scoped to the
  mirrored repos only (Contents: read/write). **It expires 2026-10-03** — after that,
  mirroring silently fails with 403s until the token is regenerated and updated in
  Gitea (repo → Settings → Repository → Mirror Settings).
- Check mirror health: Gitea (`http://192.168.1.120:3300`) → repo → Settings →
  Repository → Mirror Settings (shows last-sync time and last error), or compare
  `git ls-remote origin main` vs `git ls-remote github main` — the hashes should match.
- To enable the same mirroring for another project, run `add_push_mirror.ps1` from
  `mafober/portainer/configs/gitea/` — full walkthrough (including the one-time GitHub
  PAT steps) in that folder's `github_push_mirror.md`.
