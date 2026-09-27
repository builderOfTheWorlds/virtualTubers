# Usage

Everything below assumes the stack is already up (`docker compose up` — see
the Usage quick-start in [README.md](../README.md)). This is the detailed
reference: shelling into a container, sending inter-agent messages, pausing
a worker, running Rerun Theater (solo and duets), and local dev outside
Docker.

## Shelling into a running container

To poke around inside a running worker (check logs, inspect config, debug tmux panes), exec into it directly — no need to stop/restart anything. Since no `container_name` is pinned in `docker-compose.yml`, Compose auto-names containers `<project>-<service>-<n>`, where the project prefix is `virtualtubers-` (from the repo folder name):

```bash
docker exec -it virtualtubers-worker-coder-1 bash
```

Swap `worker-coder` for `worker-manager`, `worker-tester`, `message-logger`, `message-api`, or `log-shipper` as needed. Run `docker ps` first if you're unsure of the exact name/suffix on your host.

## Inter-agent messaging (Kafka)

Agents talk to each other over a Kafka topic (`vtuber.messages` by default) instead of a file — see `docs/message_bus.md`. Every message is durably logged to Postgres by the `message-logger` service (`docs/message_logger.md`).

To send a worker an instruction (or inject a test message), use the `message-api` HTTP service (`docs/message_api.md`), exposed on port `8090`:

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "task_assignment", "payload": {"task": "say hello"}}'
```

The `coder` worker's agent loop picks up the message, calls its configured LLM (`llm.provider` in `config/workers/coder.yaml`) with its system prompt and the task, and replies with `task_complete` — then hands the commit to the tester (`commit_notification`), whose `test_passed`/`bug_report` verdict flows on to the manager and, as a `manager_report`, back to the operator. The whole exchange is visible in each worker's console output and the tmux "agent chat"/Kafka feed pane — see [docs/agent.md](agent.md). To point a worker at Claude instead of Ollama, set that worker's `llm.provider: claude` and export `ANTHROPIC_API_KEY`.

For the full list of commands an operator can send (task assignment, direct chat, and manual/debug injections for every pipeline stage), see [docs/operator_commands.md](operator_commands.md).

## Turning a worker on/off (no redeploy)

Any worker can be paused and resumed without touching `docker-compose.yml`
or rebuilding the image — via `message-api`'s `/workers` endpoints
(see [docs/worker_control.md](worker_control.md) and
[docs/message_api.md](message_api.md)). "Off" stops both the agent
(no more task/message processing) and the Twitch stream (ffmpeg stops
pushing frames); the container itself stays up the whole time, ready to
resume instantly:

```bash
curl -X POST http://localhost:8090/workers/coder/disable   # agent pauses, stream goes offline
curl http://localhost:8090/workers/coder                   # {"worker_id": "coder", "enabled": false}
curl -X POST http://localhost:8090/workers/coder/enable    # resumes both, in place
```

The flag lives in the shared `redis` service and defaults to enabled — a
worker nobody has ever toggled, or a temporarily-unreachable Redis, both
behave as "on" rather than silently going dark.

**Is it actually running?** Each agent refreshes a Redis liveness key every
tick, and message-api turns those into one status row per worker (never a
503 — Redis down reads as `unknown`):

```bash
curl http://localhost:8090/workers/health          # every known worker
curl http://localhost:8090/workers/coder/health    # {"worker_id": "coder", "state": "alive", "age_s": 2.1, "local_override": false, "enabled": true, ...}
```

`state` is `alive`, `stale` (missed beats), `down` (no beat within its TTL)
or `unknown` (Redis unreachable). The control panel shows the same data as a
health column with a kill-switch badge.

**Emergency stop when Redis / message-api are down.** Because reads fail
open, the `/disable` toggle can't take a stream off air while Redis is
unreachable. The local kill switch can: it `docker exec`s a kill file into
each worker container, and while that file exists the worker is off
(ffmpeg stops within ~0.5 s, the agent pauses) without consulting Redis.
Run on the Docker host:

```bash
scripts/emergency_stop.sh              # all running worker-* containers
scripts/emergency_stop.sh coder gm     # by service suffix / service / container name
scripts/emergency_resume.sh coder      # remove it again (back under the Redis flag)
```

`scripts/emergency_stop.ps1` / `emergency_resume.ps1` do the same from a
Windows PC over SSH (default `secus@192.168.1.23`). The kill file survives
`docker restart` but not container re-creation (a redeploy). Details:
[docs/worker_control.md](worker_control.md).

## Rerun Theater — replaying past sessions, with voices

Rerun Theater re-performs saved (parsed, redacted) Claude Code dev sessions
as stream shows, and can narrate them out loud with two TTS voices per
airing — the boss and the coder — whose spoken lines are written fresh by
the local LLM on every airing and timed so speech and on-screen text finish
together. For a **solo** show, "the coder" is that worker's own distinct
persona voice (KODI-7, MAX-1, TESS-3, NYX-1, OKO-2, and ADA-3 each sound
different — see [CHANGELOG.md](../CHANGELOG.md)); "the boss" is a
shared voice every worker uses the same way.
Full pipeline docs: [docs/session_log_parser.md](session_log_parser.md)
→ [docs/revoice.md](revoice.md) → [docs/replay.md](replay.md) →
[docs/replay_pane.md](replay_pane.md) → (multi-worker)
[docs/duet_replay.md](duet_replay.md).

One-time setup:

```bash
# 1. Build the episode library from your session logs (on the machine that has them)
.venv/Scripts/python.exe scripts/build_replay_library.py \
  --logs "path/to/logs/claude/virtualTubers" --out replays

# 2. Upload the episodes to the server. message-api validates each one
#    (shape, leak audit, and a real dry-run render) before storing it in
#    Postgres, which is where the workers read them from.
for f in replays/*.json; do
  echo -n "$(basename "$f" .json): "
  curl -sS -X POST http://localhost:8090/replays \
    -H 'Content-Type: application/json' --data-binary @"$f"
  echo
done
```

`replays/` is a local staging directory on the machine that builds it —
nothing is copied to the deploy host and there is no `/data/replays`
mount. Adding an episode later is step 2 on its own; `curl
http://localhost:8090/replays` lists what's in the (airable) library.

**Drafts.** Add `?status=draft` to the upload to hold an episode for review
instead of airing it at once. A draft is validated and stored but can
never be requested or randomly picked until it is approved — in the control
panel's "Drafts awaiting review" table, or:

```bash
curl http://localhost:8090/replays?status=draft               # the review queue
curl -X POST http://localhost:8090/replays/<name>/approve     # make it airable
curl -X DELETE http://localhost:8090/replays/<name>           # reject it
```

The offline generator can post drafts itself when `AUTO_SUBMIT_DRAFTS=true`
is set on `3layer-generator` (off by default; docs/draft_submitter.md). See
[docs/episode_store.md](episode_store.md),
[docs/episode_validator.md](episode_validator.md), and the `/replays`
routes in [docs/message_api.md](message_api.md).

The Piper voice models (coder + boss) don't need a manual download/sync —
`./install.sh` fetches them straight into `voices/` on the deployment host
(see [Deploy / redeploy](deployment.md#deploy--redeploy-after-a-code-change)), which
is already the bind-mount source for `/data/voices`. Only needed manually for
local preview off the host: `.venv/Scripts/python.exe scripts/download_voices.py --out voices`.

Then enable it per worker (config-only, plus one image rebuild for the
`piper-tts` dependency):

```yaml
# config/workers/<role>.yaml
voice:
  provider: piper          # "null" keeps replays silent
```

Set `LAYOUT_PRESET=replay` on that worker (e.g. `TUBER1_LAYOUT_PRESET=replay`
in `.env`) so its editor pane becomes the theater, and
request a show:

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "replay_request",
       "payload": {"episode": "2026-07-02_04-27-00_6ecdde82"}}'
```

The pane prints "preparing tonight's episode…" while the LLM writes the
dialogue and TTS renders it, then performs the show — boss messages in the
boss's voice, narration and work commentary in the coder's, audio going out
on the stream via the same PulseAudio sink ffmpeg already captures. Long
command outputs get proportionally longer narration, so the avatar always
has something to say over the scroll. Add `"voice": false` to the payload
for a silent airing; voice failures (LLM/TTS/player down) automatically
degrade to a silent show rather than cancelling it. Local preview without
the stack:

```bash
python app/replay.py replays/<episode>.json --voice-config config/workers/coder.yaml
```

## Duets (multiple workers, same episode)

Add `payload.cast` to a `replay_request` to have several workers perform
the SAME episode together, each on its own Twitch channel, each voicing a
different speaker — full protocol reference: [docs/duet_replay.md](duet_replay.md).

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "replay_request",
       "payload": {"episode": "2026-07-02_04-27-00_6ecdde82",
                    "cast": {"boss": "manager", "coder": "coder"}}}'
```

- `coder` (the worker addressed) becomes the **director**: it prepares and
  Postgres-persists the airing once, invites `manager`, and paces both
  streams scene-by-scene with cues.
- Every stream shows the **whole episode's visuals**, but only speaks its
  own cast lines: the coder's stream plays the `coder` speaker's audio and
  shows the avatar "listening" during boss lines; the manager's stream is
  the mirror image.
- **Duets never degrade to solo** — if the director can't reach the
  narration store or Kafka, voice prep fails, or `manager` never confirms
  ready in time, the whole airing refuses outright (an `operator_reply`
  error, when the director could still reach Kafka at all) rather than
  airing solo or partially.
- Deployment: every cast worker needs `LAYOUT_PRESET=replay`, the
  `POSTGRES_*` env vars, and reachable Kafka. All six coder-role workers
  (`coder`/`manager`/`tester` plus the three A/B coding-backend workers
  `coder-native`/`coder-opencode`/`coder-aider`) are wired for this in
  `docker-compose.yml` — the three A/B workers currently default to
  `replay` already; `coder`/`manager`/`tester` need their
  `*_LAYOUT_PRESET` stack env set to `replay` to enable it.
- **Voice gotcha**: the worker you address (`to`) becomes the director,
  and it voices *every* cast member from its own `voice.speakers` config —
  not each worker's own. Always address the request to whichever worker is
  cast as `"coder"` (as in the example above); addressing it to `manager`
  instead makes the `"coder"` lines come out in the manager's voice. See
  [docs/duet_replay.md](duet_replay.md#voice-resolution-the-directors-config-decides-every-speakers-audio).

## Local development outside Docker

To run a single worker outside Docker for quick iteration on `app/agent.py` or `app/avatar.py`:

> **Always use the project's `.venv` for local development — never install packages into or run scripts against the global/system Python on this machine.** Create it once with `python -m venv .venv`, then activate it before installing dependencies or running anything.

```bash
python -m venv .venv          # first time only
.venv\Scripts\activate         # Windows (use `source .venv/bin/activate` on macOS/Linux)
pip install -r requirements.txt
python3 app/avatar.py --config config/workers/coder.yaml
```
