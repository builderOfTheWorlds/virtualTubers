# ashiorid_office go-live runbook (Sat 2026-10-24 20:00 EDT, 168 h, no operator)

Compose shorthand used below (run from the repo root):

```bash
DC="docker compose -f docker-compose.yml -f docker-compose.office.yml -f docker-compose.table-office.yml"
TC=".venv/bin/python scripts/table_ctl.py"
WD=".venv/bin/python scripts/golive_watchdog.py"
```

Container names are `virtualtubers-worker-<service>-1`. Display / worker map:

| service | worker | display | stream key var (.env) | Twitch login (channels.yaml) |
|---|---|---|---|---|
| worker-roundtable | roundtable | :106 | ROUNDTABLE_STREAM_KEY | *null: fill in* |
| worker-gm | tuber_0 | :105 | TUBER0_STREAM_KEY | *null: fill in* |
| worker-manager | tuber_1 | :100 | **TUBER6**_STREAM_KEY | mastermanagerchief |
| worker-coder-native | tuber_2 | :102 | TUBER2_STREAM_KEY | ten_g_hands |
| worker-coder-aider | tuber_3 | :104 | **TUBER4**_STREAM_KEY | spam_of_frogs |
| worker-tester | tuber_4 | :101 | **TUBER5**_STREAM_KEY | braxton_caruso |
| worker-coder-opencode | tuber_5 | :103 | **TUBER3**_STREAM_KEY | burtyrenoldo |
| worker-coder | tuber_6 | :99 | **TUBER1**_STREAM_KEY | the_emperor_secus |
| worker-observer | tuber_7 | :107 | TUBER7_STREAM_KEY (**missing**, B9) | *null: fill in* |

Key var names are legacy-mapped (bold = not the tuber number). Confirm each login really belongs to its key
before relying on the watchdog's Twitch check (plan B11).

## 1. What the watchdog does (and never does)

`scripts/golive_watchdog.py`, one pass every 2 min via `office-watchdog.timer`. State
`~/.local/state/office-watchdog/state.json`, events `events.jsonl` (JSONL; `kind: alert` = needs a human).

| Check | Remediation (bounded) |
|---|---|
| Ollama loaded model | unload (`keep_alive:0`), alert |
| MemAvailable <8 GiB / <4 GiB | alert / restart largest-RSS worker (1 per 30 min, never vLLM) |
| vLLM `/v1/models` down 2 passes | `docker start vllm-agents` (3/h), only if nothing else holds :8092 |
| worker exited | `docker start`, backoff 60 s x2^n, max 3/h then alert only |
| ffmpeg missing / etimes resetting | alert; 3 bad passes -> restart that worker (1/h) |
| Twitch offline 3 passes, ffmpeg alive | restart that worker (1/h). Skipped if `lofigirl` probe reads offline |
| table not active/auto | `table_run_start {from: next}`, backoff 4 min x2^n. Not when `run.finished` |
| table stalled 30 min (no counters, no `table_line`) | alert, restart worker-gm (1 per 2 h) |
| tile_pane RSS >3 / >6 GiB | alert / restart roundtable (1 per 2 h) |
| root fs >93% | alert |

Never: `compose down`, stop/rm/kill, anything touching `vllm-hermes3-70b`, restarting vLLM, Ollama beyond unload.
Global cap 16 docker actions per rolling hour. Alerts only reach the event log: the sink
(`default_alert_sink`) is a TODO for a Hermes gateway message. **Nobody is paged**: read the log (section 3).

## 2. Go-live checklist (Sat, EDT)

**T-2h (18:00) host + model**
- [ ] `docker ps` has no `vllm-hermes3-70b`; `docker ps -a | grep vllm-agents`; start it: `docker start vllm-agents`, wait for `/v1/models` (probe below). One big model only.
- [ ] `curl -s localhost:11434/api/ps` -> `{"models":[]}`. `free -g` MemAvailable > 25 GiB before workers. `df -h /` < 90 %.
- [ ] `.claude/prompts/golive/channels.yaml`: no nulls left except channels the user wants skipped. `TUBER7_STREAM_KEY` set (user types it).
- [ ] Image current: `docker image inspect vtube-worker:latest` created after the last app commit, else rebuild.
- [ ] `block_clock.offset_s: 0` in `config/table/ashiorid_office.yaml` (soak offset removed), position file fresh.

**T-1h (19:00) stack up**
- [ ] `$DC up -d` (never `--force-recreate`; see pitfalls). `$DC ps` shows 9 workers Up.
- [ ] `$TC status`: `mode: auto`, `run.active: true`, `block.window: prelude`, `block.phase: evening`, `ambient_pool` > 0.
- [ ] Per worker, ffmpeg age: `docker exec <c> sh -c 'ps -eo etimes,args | grep "[f]fmpeg"'` and `global_header` (`tr "\0" " " </proc/$(pgrep -o -x ffmpeg)/cmdline | grep -c global_header` >= 1).
- [ ] **Rendered frame of every display** (:99-:107, probe below), looked at, not inferred from logs.
- [ ] Tile RSS baseline (probe below): all tile_pane well under 1 GiB.

**T-30m (19:30) external + watchdog**
- [ ] Every channel with a login live externally (Twitch probe below). Fix any offline one before going on.
- [ ] `$WD --dry-run`: expect `planned actions (0)` and every container `running`. Anything planned = fix the cause first.
- [ ] Install + start (user decision, this is the step the build did NOT do):
  `cp .claude/prompts/golive/office-watchdog.{service,timer} ~/.config/systemd/user/ && systemctl --user daemon-reload && loginctl enable-linger $USER && systemctl --user enable --now office-watchdog.timer`
- [ ] After 5 min: `systemctl --user list-timers office-watchdog.timer`; `tail -3 ~/.local/state/office-watchdog/events.jsonl` shows `kind: pass`, `open_alerts: []`.

**T0 (20:00)** prelude plays ambient evening scenes until Sun 00:00 (block 0). Nothing to press.

**T+15m / T+1h**
- [ ] Frame + Twitch live re-check on 2 channels incl. roundtable; `$TC status` shows scenes advancing (`ambient_played` grows, `stuck.total` 0).
- [ ] No `alert` in events since the timer started; `MemAvailable` steady; ffmpeg etimes growing (not resetting).

## 3. Daily checks (2 min)

```bash
jq -c 'select(.level=="alert" or .level=="error" or .kind=="action")' ~/.local/state/office-watchdog/events.jsonl | tail -20
jq '{passes,last_pass,alerts:(.alerts|keys),actions:(.actions|length)}' ~/.local/state/office-watchdog/state.json
systemctl --user list-timers office-watchdog.timer      # NEXT/LAST moving; journalctl --user -u office-watchdog -n 20
$TC status ; df -h / ; free -g
```
Alert quick map: `ffmpeg:*`/`twitch:*` stream path (check frame + key); `container:*:budget` worker keeps dying (`docker logs`);
`table:stalled|stuck_streak|no_reply` GM/vLLM trouble; `tiles:*` leak regression; `memory:*` host pressure; `disk:full`
(recordings cap 5 GB, rotate); `vllm:*` model server; `vllm:port_occupied` the 70b is on :8092 (do not start vllm-agents on top).

## 4. Reading table status

`GET :8082/table/status` is an **HTML partial**, not JSON. Use `$TC status` (JSON via message-api, same protocol the
watchdog uses). Fields:

- `mode` auto/manual, `run.active` (should be true), `run.halted`, `run.finished` (week over), `run.played`.
- `block.index` / `of`: block 0..27 = Sun 00:00..Sat 24:00 in 6 h steps; `-1` prelude (before Sun 00:00), `>= of` after the week.
  `block.window` prelude|block|after, `block.phase` off_hours|morning|workday|evening (local hour).
- `block.spine_remaining` contracts left in this block's window, `carry_over` unplayed spine from earlier blocks (plays first).
- `block.ambient_pool` / `ambient_pool_phase` scenes available (0 = it will replay or play nothing), `last_ambient_id`,
  `ambient_played`, `spine_played`, `next_kind` spine|ambient, `playing`.
- `block.stuck`: `consecutive` / `limit` (3), `total` (only ever grows; each is a scene the GM could not resolve),
  `cooldown_s` > 0 means ambient-only cooldown after a streak.
- Healthy: `stuck.total` flat or creeping by a few; a growing `consecutive` or a total jumping by tens = model/vLLM problem.

## 5. Pause / stop / resume

- **Maintenance (watchdog must not undo it):** `touch ~/.local/state/office-watchdog/PAUSE` (passes become no-ops), do the work, `rm` it.
  Do this BEFORE any `docker stop` or `table_ctl.py run-stop`, otherwise the next pass restarts/resumes it.
- Stop the watchdog: `systemctl --user disable --now office-watchdog.timer`.
- Stop the table (workers keep streaming): `$TC run-stop` (scene finishes) / `$TC run-stop --now`. Resume: `$TC run --from next`.
  Skip a scene: `$TC stop`.
- Stop the broadcast: PAUSE first, then `$DC stop <services>` (not `down`). Resume: `$DC up -d --no-deps <services>`, `rm PAUSE`.
- Generation window (vLLM 70b needs the memory): PAUSE, stop workers + vllm-agents, load the 70b; reverse before unpausing.

## 6. Swap a channel's stream key

1. Edit `.env` yourself (secret, never paste it into chat/logs): the var from the table above.
2. Recreate only that service (`stop` + `rm -f`, then `up`; `--force-recreate` can leave orphans):
   `PAUSE; $DC stop worker-X && $DC rm -f worker-X && $DC up -d --no-deps worker-X; rm PAUSE`
3. New login? Edit `channels.yaml` (re-read every pass, no restart). One publisher per key: stop the worker before testing a key by hand.
4. Verify: ffmpeg etimes growing, frame of its display, Twitch probe for the login.

## 7. Rollback

- **Roundtable split-capture misbehaves** (ffmpeg crash loop, no video): `ROUNDTABLE_STREAM_SPLIT_CAPTURE=0 $DC stop worker-roundtable && $DC rm -f worker-roundtable && ROUNDTABLE_STREAM_SPLIT_CAPTURE=0 $DC up -d --no-deps worker-roundtable`. Persist it in `.env` if it must survive a recreate.
- **Tile memory leak regression:** the watchdog restarts roundtable at >6 GiB (max 1 per 2 h). If it recurs every couple of hours the fix regressed: tell the dev (`TileAvatar.tick` must call `pygame.event.clear()`); until then the watchdog's cap is the only guard, so check `tiles:*` alerts more often.
- **Bad GM/table build:** `$DC up -d --no-deps worker-gm` after reverting config; `$TC run --from next`.
- **Watchdog itself misbehaving:** `systemctl --user disable --now office-watchdog.timer`, read `events.jsonl`.
- Pitfalls: `docker exec ... sh -c 'pkill -f x'` kills its own shell; `compose stop` + `rm -f` then `up -d`.

## 8. Known-good probes

```bash
# vLLM (key read at runtime; never echo it)
K=$(docker exec vllm-agents printenv VLLM_API_KEY); curl -s -H "Authorization: Bearer $K" localhost:8092/v1/models | jq -r '.data[].id'   # table-agents
# Ollama must be empty
curl -s localhost:11434/api/ps
# Rendered frame (ground truth), display from the table above
docker exec virtualtubers-worker-roundtable-1 sh -c 'ffmpeg -loglevel error -f x11grab -video_size 1920x1080 -i :106 -frames:v 1 -y /tmp/f.png' && docker cp virtualtubers-worker-roundtable-1:/tmp/f.png /tmp/frame.png
# ffmpeg uptime in a worker
docker exec virtualtubers-worker-manager-1 sh -c 'ps -eo etimes,args | grep "[f]fmpeg"'
# Twitch, raw GQL (non-null stream = live). Check lofigirl first: if it reads offline the probe is untrustworthy.
for L in lofigirl mastermanagerchief ten_g_hands; do curl -s -H 'Client-ID: kimne78kx3ncx6brgo4mv6wki5h1ko' -H 'Content-Type: application/json' -d "{\"query\":\"query{user(login:\\\"$L\\\"){stream{createdAt}}}\"}" https://gql.twitch.tv/gql | jq -c --arg l $L '{login:$l,live:(.data.user.stream!=null)}'; done
# Tile RSS (KiB, per tile)
docker exec virtualtubers-worker-roundtable-1 ps -eo rss,args | grep '[t]ile_pane'
# Table + memory + disk
$TC status ; grep MemAvailable /proc/meminfo ; df -h /
# Watchdog, read-only
$WD --dry-run
```

Limits worth knowing: the watchdog cannot see *what is on screen* (use frames), cannot tell a wrong-but-live key, and with
no gateway sink it cannot reach you; a human has to read the log. Twitch `decapi.me` uptime lags; trust GQL + `lofigirl` probe.
