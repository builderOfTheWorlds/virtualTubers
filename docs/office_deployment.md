# Office deployment (ashiorid_office show)

## Overview

The ashiorid_office campaign runs the 8 office roles (CEO … Party Member) as
live agents on the **same stream workers** as the dev-team show (build plan
E1). Office mode is a compose override plus one config directory:

| Piece | What it is |
|---|---|
| `docker-compose.office.yml` | Override layered on `docker-compose.yml`. Re-points every stream worker's `/config/worker.yaml` at `config/workers/office/`, switches `WORKER_ID` to the seat id, adds `worker-observer` (tuber_7), the Fraud-Stop clone volumes and the Gitea token env. |
| `config/workers/office/<role>.yaml` | One worker config per office role (8 files). |
| `config/workers/office/roundtable.yaml` | The office roundtable show: director + 8 seat tiles. |
| `./redeploy.sh --office` | Rebuild + force-recreate with the override applied. |

Without the override nothing changes: `docker compose up -d` and plain
`./redeploy.sh` still run the dev-team show from `config/workers/*.yaml`.

Related docs: handler behaviour and the `agent.office` schema in
[agent_handlers.md](agent_handlers.md#office-handlers-v130-ob-21), the stack
itself in [deployment.md](deployment.md), roles and lanes in
[office_roles.md](office_roles.md), the persona brief in
[brief_stub.md](brief_stub.md).

## Prerequisites

- The dev-team stack already deploys on argyre ([deployment.md](deployment.md)):
  `vtube-worker:latest` built, `.env` filled in.
- Docker Compose v2 (validated with v5.1.1). The override uses `extends`,
  YAML merge keys and nested `${VAR:-default}` interpolation.
- The Fraud-Stop repo on Gitea at `gitea_admin/fraud-stop`
  (`http://192.168.1.120:3300`), with `main` and the `loop-seed` tag pushed
  (build plan §7, "Repos in play").
- Two Gitea tokens in `.env` (build plan OP-1). The names are already in
  `.env.example`:
  - `GITEA_TOKEN_OFFICE` — `write:repository` + `write:issue`. Used by the 7
    speaking seats for pushes (via the `GIT_ASKPASS` helper in
    `app/git_client.py`), PRs, reviews, merges and issue comments.
  - `GITEA_TOKEN_OBSERVER` — read-only. Only the Party Member gets it.
- On the Ollama host (gx10, `http://192.168.1.23:11434`), these models
  installed: `gemma4:26b`, `gemma4:12b-it-q4_K_M`, `qwen3.8:27b`,
  `llama3.1:8b`. OB-07 found all four already installed.

Optional `.env` additions (the defaults are fine, and `.env.example` does not
list these yet):

| Variable | Default | Used by |
|---|---|---|
| `TUBER7_STREAM_KEY` | `tuber7` | worker-observer's channel |
| `OBSERVER_LAYOUT_PRESET` | `tuber_base` | worker-observer |
| `OBSERVER_AVATAR_PROVIDER` | (config's own) | worker-observer |
| `OFFICE_TWITCH_CHANNEL_MAP` | (unset: twitch-presence idles) | twitch-presence, office mode only (see "Twitch presence") |

## Service → seat mapping

| Service | Seat | Office role | Character | `agent.role` | LLM | Config |
|---|---|---|---|---|---|---|
| worker-gm | tuber_0 | CEO | Graham Ellery | `ceo` | gemma4:26b | `office/ceo.yaml` |
| worker-manager | tuber_1 | Tech Lead | Anselm Brody | `manager` | gemma4:26b | `office/tech_lead.yaml` |
| worker-coder-native | tuber_2 | Analyst | Maren Voss | `analyst` | gemma4:26b | `office/analyst.yaml` |
| worker-coder-aider | tuber_3 | Engineer | Theo Palliser | `coder` | gemma4:26b speaks, aider on qwen3.8:27b codes | `office/engineer.yaml` |
| worker-tester | tuber_4 | Tester | Owen Hask | `tester` | gemma4:12b-it-q4_K_M | `office/tester.yaml` |
| worker-coder-opencode | tuber_5 | Marketing | Julian Faire | `marketing` | gemma4:26b | `office/marketing.yaml` |
| worker-coder | tuber_6 | Office Manager | Nora Blakeley | `office_manager` | gemma4:12b-it-q4_K_M | `office/office_manager.yaml` |
| **worker-observer** (new) | tuber_7 | Party Member | A. Penhale | `observer` | llama3.1:8b (never called by office handlers) | `office/party_member.yaml` |
| worker-roundtable | — | show director | — | `roundtable` | gemma4:26b | `office/roundtable.yaml` |

Why this mapping:

- **worker-gm → CEO.** It already runs as `tuber_0`, the CEO's seat
  (`app/office/roles.py` SEAT). It is also the channel `redeploy.sh` grabs a
  verification frame from (display `:105`).
- **worker-manager → Tech Lead.** The TL reuses the manager handler family
  (E2).
- **worker-coder-aider → Engineer.** OB-07 picked the aider backend.
- **worker-tester → Tester.** It keeps the tester handler family.
- **The rest have no role-specific wiring.** The three remaining coder
  containers were assigned in service order.
- **Channels don't move between shows.** Each service keeps its own
  `STREAM_KEY`, `DISPLAY_NUM`, layout and avatar env. Only `WORKER_ID` (the
  bus address and liveness key) changes to the seat id.
- **worker-observer copies worker-gm.** It `extends` worker-gm's base
  definition: image, GPU, voice/recording/world-state mounts, and the
  Kafka/Redis/Postgres env. Then it overrides the identity:
  - display `:107` (99–104 are the characters, 105 the GM, 106 the
    roundtable)
  - its own `TUBER7_STREAM_KEY`

What each seat gets from the override:

| Seat(s) | Clone volume at `/data/repos/fraud-stop` | Token env |
|---|---|---|
| TL, Analyst, Engineer, Marketing, OM | yes, one volume each | `GITEA_TOKEN_OFFICE` |
| CEO | none (works through issues only) | `GITEA_TOKEN_OFFICE` |
| Tester | none; the Engineer's volume read-only at `/data/repos/tuber_3` | `GITEA_TOKEN_OFFICE` |
| Party Member | none | `GITEA_TOKEN_OBSERVER` only |

The TL container's dev-team backlog `GITEA_TOKEN` is blanked in office mode.

CPU: the office show runs **nine** stream workers. The base file's budget is
2.0 cores × 8 = 16 of about 20. The override caps each worker at 1.75, so
9 × 1.75 = 15.75 stays inside that budget (see the rationale on worker-coder's
`deploy` block in `docker-compose.yml`).

## One-time setup: the Fraud-Stop clones

Each writing seat has its own named volume so no two agents share a working
tree: `office-repo-tech-lead`, `-analyst`, `-engineer`, `-marketing` and
`-office-manager`. Compose prefixes the project name, e.g.
`virtualtubers_office-repo-engineer`.

**Clone them before the first office start.** On startup the Engineer's
`workspace_setup.ensure_workspace` seeds an *empty* workspace with the
dev-team sandbox and `git init`s it. After that it is not a Fraud-Stop clone.
To recover, see Troubleshooting.

```bash
cd /home/secus/codeProjects/virtualTubers
set -a; . ./.env; set +a          # exports GITEA_TOKEN_OFFICE into this shell
for seat in tech-lead analyst engineer marketing office-manager; do
  docker run --rm \
    -v "virtualtubers_office-repo-${seat}:/data/repos/fraud-stop" \
    -e GITEA_TOKEN_OFFICE \
    --entrypoint git vtube-worker:latest \
    -c credential.helper='!f() { echo username=gitea_admin; echo "password=$GITEA_TOKEN_OFFICE"; }; f' \
    clone http://192.168.1.120:3300/gitea_admin/fraud-stop.git /data/repos/fraud-stop
done
```

How this keeps the token safe:

- The credential helper is a `git -c` option placed *before* `clone`, so it is
  never written into the clone's `.git/config`.
- The token is expanded inside the container, not in your shell history.

The clone runs as root, the same user as the worker, so git's
"dubious ownership" check doesn't trigger.

## Usage

Validate the merged file (no containers touched):

```bash
docker compose -f docker-compose.yml -f docker-compose.office.yml config --quiet && echo OK
```

Start or switch to the office show:

```bash
./redeploy.sh --office            # pytest gate, rebuild, force-recreate, verify
# or, without a rebuild:
docker compose -f docker-compose.yml -f docker-compose.office.yml up -d --force-recreate \
  $(docker compose -f docker-compose.yml -f docker-compose.office.yml config --services | grep '^worker-')
```

`--office` exports
`COMPOSE_FILE=docker-compose.yml:docker-compose.office.yml`, so every compose
call in the script (infra-up, builds, the derived `worker-*` list, recreate)
sees the override.

Switch back to the dev-team show:

```bash
./redeploy.sh                     # warns while the office-only container still exists
docker rm -f virtualtubers-worker-observer-1
```

The clone volumes are kept. Remove them only if you want to discard the
office working trees; this cannot be undone:

```bash
docker volume rm virtualtubers_office-repo-{tech-lead,analyst,engineer,marketing,office-manager}
```

### Day runner (CEO, OB-30)

`config/workers/office/ceo.yaml` enables `agent.office.day_runner`
(docs/office_day_runner.md). `worker-gm` runs the office day from its idle
tick: `day_start` at 06:00, the directive, the phase edges, the 23:45 wrap-up,
`day_end` at 00:00, then the 00:00–06:00 hand-off to the playlist
(`office.playlist:day_runner_playlist`, docs/office_playlist.md). The override
gives `worker-gm`:

- **`office-day-runner:/data/office-state`**: a named volume for the
  restart-safe state file (`state_path: /data/office-state/day_runner.json`).
  It survives restarts and redeploys, so the CEO never issues a second
  directive on the same day. To restart the loop day from scratch, stop
  `worker-gm` and run `docker volume rm virtualtubers_office-day-runner`.
- **`MESSAGE_API_URL=http://message-api:8000`**: the playlist lists
  **approved** `office-*` replays from message-api. Drafts never air.
- **Optional: the sessionCorpus export.** Directive source 2 reads
  `corpus_export: /data/corpus/export.jsonl`. No host path exists for the
  export by default, so the mount is commented out in
  `docker-compose.office.yml`. To use it, set `OFFICE_CORPUS_DIR` in `.env`
  to the directory that holds `export.jsonl`, and uncomment
  `- ${OFFICE_CORPUS_DIR}:/data/corpus:ro`. Without the mount, the source
  logs one WARN and the directive comes from the open Fraud-Stop issue
  backlog.

The playlist's `replay_request` goes to `replay_target: roundtable` (the
office roundtable's bus id), with `playlist_options.cast` set to the
identity seat map `tuber_0..tuber_6` — the same cast the control panel's
office-mode Play sends. The roundtable only lights its tiles through the
duet director path, which needs `payload.cast`; `tuber_7` is left out
because the Party Member never speaks. Without `replay_target` the request
would go to the CEO worker, whose `tuber_base` layout has no replay pane.

At 23:45 the day runner broadcasts `wrap_up`; every seat with a superior
answers with one `status_report` (Analyst, Marketing, OM and the TL to the
CEO; Engineer and Tester to the TL). The CEO's own wrap-up line and each
report also go to the roundtable's live transcript.

### Week branches and the Sunday reset

Every office PR targets the current week's trunk `loop/<W>`
(`agent.office.base_branch: auto` on the lane writers): lane and Engineer
branches start from it, the TL merges into it, and the OM's branch GC never
touches it. `W` comes from the office clock (epoch 2026-09-27, America/New_York;
override with `agent.office.epoch` / `tz` or env `OFFICE_EPOCH` / `OFFICE_TZ`).
Set `base_branch` to a branch name to pin one instead.

`python -m office.weekly_reset` (docs/office_weekly_reset.md) rebuilds
`loop/<W>` from `loop-seed` on Sunday 00:00 and broadcasts
`character_refresh`. Each seat with a clone then clears its in-process office
state, `git fetch`es and checks out the new `loop/<W>`. The Tester (read-only
mount of the Engineer's clone) and the CEO / Party Member (no clone) only
clear state.

### Twitch presence

`twitch-presence` addresses greetings by worker id, and the office show
changes every worker id to a seat. The override therefore sets
`TWITCH_CHANNEL_MAP` from **`OFFICE_TWITCH_CHANNEL_MAP`** in `.env`: the same
channels, mapped to seats.

| Dev-team id | Seat | Service |
|---|---|---|
| `tuber_0` | `tuber_0` | worker-gm |
| `manager` | `tuber_1` | worker-manager |
| `coder-native` | `tuber_2` | worker-coder-native |
| `coder-aider` | `tuber_3` | worker-coder-aider |
| `tester` | `tuber_4` | worker-tester |
| `coder-opencode` | `tuber_5` | worker-coder-opencode |
| `coder` | `tuber_6` | worker-coder |
| (new channel) | `tuber_7` | worker-observer (queues a rerun, never greets — U6) |

Example: `OFFICE_TWITCH_CHANNEL_MAP=mycoderchannel:tuber_6,mymgrchannel:tuber_1`.
Unset, the service idles (no greetings) instead of greeting with the
dev-team ids.

Check a seat:

```bash
docker compose -f docker-compose.yml -f docker-compose.office.yml logs -f worker-manager
docker exec virtualtubers-worker-coder-aider-1 git -C /data/repos/fraud-stop status
```

## Configuration

Every office config copies the structure of its dev-team counterpart. These
blocks are office-specific:

- **`agent.role` / `agent.office_role`.** Per E2 and `app/office/roles.py`
  `HANDLER_ROLE`.
- **`agent.name` / `agent.system_prompt`.** Copied verbatim from
  `campaigns/ashiorid_office/cast/<role>.yaml`. The reused
  manager/coder/tester handlers and the any-role handlers narrate with
  `agent.system_prompt`. The office handlers build their brief from the cast
  and profile files instead (`pack_dir: /campaigns/ashiorid_office`, mounted
  read-only by the override).
- **`agent.manager_id: tuber_1`.** On the Engineer (it receives the
  backend-failure `clarification_request`) and on the Tester.
- **`agent.workspaces: {tuber_3: /data/repos/tuber_3}`.** On the Tester only.
- **`agent.office`.** Per the schema in agent_handlers.md:
  - `workspace`, `remote_url` and `base_branch: auto` (the week trunk
    `loop/<W>`) on the lane writers.
  - `live_transcript: true` on the seven speaking seats (not the Party
    Member).
  - `day_runner` on the CEO, including `replay_target: roundtable` and the
    playlist cast.
  - `gitea` on every seat, with `token_env` naming the env var.
  - `merge_prs` on the TL.
  - `chores` on the OM.
  - `observer` on the Party Member.
- **`remote_url` is the HTTP remote**
  (`http://192.168.1.120:3300/gitea_admin/fraud-stop.git`), not the U5 SSH
  remote. Pushes then authenticate through `GIT_ASKPASS` with
  `GITEA_TOKEN_OFFICE`, and no SSH key has to be mounted into nine
  containers. To use SSH instead, mount a key, set `GIT_SSH_COMMAND`, and
  change `remote_url` to
  `ssh://git@192.168.1.120:2222/gitea_admin/fraud-stop.git`.
- **`voice`.** `voice.speakers` is keyed `tuber_0..tuber_7`. Each value
  resolves the cast file's registry voice name through `config/voices.yaml`.
  `boss`/`coder` are kept for Rerun Theater replays.
- **`avatar.codec_avatar.character_params`.** Copied from the cast file's
  OB-20 `character_params`. The roundtable roster carries the same faces
  inside a marked `BEGIN/END OB-20` block. Geometry is the shared tuber_base
  rect.
- **`coding_backend`.** Engineer only: `aider`, `model: qwen3.8:27b`,
  `workspace: /data/repos/fraud-stop` (also `WORKSPACE_PATH` in the
  override), `timeout_s: 900`.

`tests/test_office_configs.py` checks every item above against its source of
truth. It also checks that no config or override line holds a secret value.
**Re-run it after editing a cast file**, because drift fails the test.

## Model serving (OB-07)

The allocation (`.claude/prompts/office_w0_benchmark.md` §3):

| Tier | Seats | Model |
|---|---|---|
| Plot voices | CEO, TL, Analyst, Marketing; the Engineer's speech | `gemma4:26b` |
| Support | Tester, OM | `gemma4:12b-it-q4_K_M` |
| Code | Engineer, through aider | `qwen3.8:27b` |
| Fallback / fast mode | any speaker | `llama3.1:8b` |

**Knobs the worker can't send yet.** `app/llm_client.py` `OllamaClient` sends
only `temperature` and `num_predict`. The configs still record `keep_alive:
"30m"`, `num_ctx: 8192`, `think: false` and `fallback_model: "llama3.1:8b"` in
`llm:` as the single source of truth for the follow-up that wires them in.
Nothing reads them today, so enforce them on the **Ollama host**:

```bash
# systemd override for ollama.service on gx10 (sudo systemctl edit ollama)
Environment="OLLAMA_KEEP_ALIVE=30m"        # keep models resident between turns
Environment="OLLAMA_NUM_PARALLEL=1"        # one request per model at a time (OB-07 measured no gain above 1)
Environment="OLLAMA_MAX_LOADED_MODELS=3"   # gemma4:26b + gemma4:12b + qwen3.8:27b co-resident (44.6 GB)
```

`think: false` has no server-side equivalent. OB-07 measured zero thinking
tokens with it off. Without it, check a gemma reply for leaked reasoning
before going live. `fallback_model` has no automatic switch: to use fast mode,
edit `llm.model`.

**Turn serialization.** OB-07 says spoken turns must be serialized. There is
**no turn-lock or scheduler knob** in the worker configs today. What the
configs do instead:

- **Phase changes cost one turn.** `narrate_phase_change` is `true` only on
  the CEO. A `phase_change` broadcast is then one LLM turn instead of seven.
  The Party Member never narrates.
- **The chain is sequential by protocol.** Each handler sends the next
  message only after its own LLM call: directive → functional_plan →
  technical_plan → task_assignment → test_request → verdict → status_report.
- **One concurrent burst remains.** A CEO `directive` fans out to the
  Analyst, Marketing, OM (and a TL ack) at once. That is 2–3 concurrent calls
  on gemma4:26b/12b, which queue on the server (about 7 s per queued line).
  A real turn scheduler belongs to the day runner (OB-30).
- **The aider job is the one allowed background job.** OB-07 allows one code
  job alongside a spoken turn; spoken lines slow to about 11–13 s while it
  decodes.

**Open: the qwen3.8:27b context clash (OB-07).** Another client pins
`qwen3.8:27b` at 262k ctx. Aider picks its own `num_ctx`, so each alternation
between that client and the Engineer can cost a 30–70 s reload. Decide one of:

- keep the other client off the model from 06:00 to 00:00, or
- create a dedicated tag (`ollama create qwen3.8-office -f <Modelfile with the
  same weights>`) and point `coding_backend.model` at it.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Engineer commits land in a sandbox tree, and pushes fail | The clone volume was empty on first start. Stop worker-coder-aider, `docker volume rm virtualtubers_office-repo-engineer`, re-clone (One-time setup), start again. |
| `event=no_token token_env=GITEA_TOKEN_OFFICE` in a seat's log | The token is missing from `.env`, or the container was not recreated after adding it. |
| The office voices are wrong on the roundtable | The show header's registry voice overrides `voice.speakers` per show. Check the header before the config. |
| Twitch arrivals greet the wrong worker, or nobody | In office mode twitch-presence reads `OFFICE_TWITCH_CHANNEL_MAP` (channel → seat id, see "Twitch presence"). Unset, it idles. Recreate `twitch-presence` after editing `.env`. |
| PRs target `main`, or `event=base_branch_fallback` in a seat's log | The seat's `agent.office.base_branch` is a branch name (explicit wins), or the week could not be derived (bad `OFFICE_EPOCH` / `OFFICE_TZ`, or a clock before the epoch). Use `auto`. |
| Lane PRs fail to open against `loop/<W>` | The week trunk isn't on Gitea yet: run `python -m office.weekly_reset --at <Sunday 00:00>` (or `--dry-run` first) so `loop/<W>` is pushed. |
| A seat stays on last week's branch after the reset | Look for `event=week_branch_checkout_failed` / `week_branch_fetch_failed` in its log (dirty tree, missing remote, token). `character_refresh` is logged as `event=character_refreshed ... checked_out=`. |
| No end-of-day reports at 23:45 | Check the CEO log for `day_runner event=wrap_up`, then each seat for `event=wrap_up_report_sent`. A `rank_violation handler=wrap_up` means the broadcast did not come from the clock / `tuber_0`. |
| Off-hours filler plays audio but no tile moves | The `replay_request` reached the roundtable without a `cast`: check `ceo.yaml` `playlist_options.cast`. |
| Control panel health view shows dev-team ids as down | The panel's worker list is dev-team shaped. The office mapping is OB-32. |
| Party Member produced text | Check that `party_member.yaml` still has `agent.office_role: party_member`. The `operator_message` and `viewer_joined` handlers skip the LLM for that role (U6). |

## Known gaps

- **The observer's gaze can't follow the speaker yet.**
  `agent.office.observer.stage_path` is unset because the roundtable's
  `stage.json` lives in that container's `TILE_RELAY_DIR`, which the
  observer can't see. Gaze rotates over the seats until OB-32 shares a relay
  volume.
- **Aider commits use the default email.** The coding backend's git author is
  `agent.name`, whose default email contains a space
  (`theo palliser@virtualtubers.local`). `agent.office.author_email` covers
  only the office lane commits.

## Changelog

- v1.0.0 (2026-09-28): Created (OB-22). 8 office worker configs plus the
  office roundtable, `docker-compose.office.yml` with `worker-observer`, and
  `redeploy.sh --office`.
- v1.1.0 (2026-09-28): CEO day runner wiring: the `office-day-runner` volume at
  `/data/office-state` and `MESSAGE_API_URL` on `worker-gm`, the optional corpus mount
  (`OFFICE_CORPUS_DIR`). Closed known gaps: the Party Member is silent in
  `operator_message` / `viewer_joined` (U6), and `.env.example` lists `TUBER7_STREAM_KEY`,
  `OBSERVER_LAYOUT_PRESET` and `OBSERVER_AVATAR_PROVIDER`.
- v1.2.0 (2026-09-28): Batch B integration. Closed known gaps: off-hours
  filler airs on the roundtable (`replay_target: roundtable` + seat cast),
  and the Twitch map has an office variant (`OFFICE_TWITCH_CHANNEL_MAP`,
  override on `twitch-presence`). New: office PRs land on the week trunk
  `loop/<W>` (`base_branch: auto`), the 23:45 `wrap_up` status reports,
  `character_refresh` checkout after the weekly reset, and the seven
  speaking seats' `live_transcript: true`. Troubleshooting rows added.
