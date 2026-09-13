# Container reset + upgrade runbook — roundtable v1.1

> Status: **READY TO EXECUTE** (2026-09-13). Written before running anything so
> the steps and rollback are reviewable.
> Spec: `roundtable_stream_design.md` v1.1. Companion: `wp6_rehearsal_and_e2e_plan.md`.

---

## 0. Why a rebuild, not a restart

`Dockerfile` **COPY**s the code and layout configs into the image — it does not
bind-mount them:

```
COPY app/ /app/                 <- tile_pane.py, voice_registry.py live here
COPY config/panels/  /config/panels/    <- tile.yaml
COPY config/layouts/ /config/layouts/   <- roundtable.yaml
COPY startup.sh /startup.sh             <- the new V2 boot check (step 6.5)
```

Confirmed against the running stack:

```
/app/tile_pane.py       MISSING (needs rebuild)
/app/voice_registry.py  MISSING (needs rebuild)
/config/voices.yaml     MISSING (needs mount + recreate)
```

The live image is **13 days old** (`7812793677bb`). A `docker compose restart`
reuses it and would change nothing. This is a **rebuild + recreate**.

### What does NOT require the reset

The `.env` key rename (`CODER_STREAM_KEY` → `TUBER1_STREAM_KEY`, etc.) is
already live-safe and verified: every running container's actual `STREAM_KEY`
still hashes identically to what the renamed `.env` resolves to, because only the
key *name* changed on the supply side. **Nothing was interrupted by the rename.**
The reset is for the new *code*, not the renamed variables.

---

## 1. Pre-flight (do all of this BEFORE touching the stack)

| # | Check | Command / expectation |
|---|---|---|
| 1 | Record the rollback point | `docker images vtube-worker:latest --format '{{.ID}}'` → currently `7812793677bb` |
| 2 | Tag it so a rollback is trivial | `docker tag vtube-worker:latest vtube-worker:pre-roundtable` |
| 3 | `.env` backed up | `ls -t .env.bak.*` — a timestamped copy exists and is gitignored |
| 4 | Compose parses | `docker compose config --quiet` → exit 0 |
| 5 | Test suite at baseline | 1250 pass / 13 fail, the 13 all in `test_campaign_validator.py` |
| 6 | Registry sane on host | `python3 app/voice_registry.py --verify --voices-dir voices` → `0 failed` |
| 7 | Note what is currently live | 17 containers up ~15h; 6 workers on `vtube-worker:latest` |
| 8 | Episode library intact | `curl -s localhost:8090/replays` → `ashiorid`, `sample`, `sample_long` |

**Twitch note:** the six character channels are live. A recreate drops them for
the rebuild window (tens of seconds to a couple of minutes each). Do this in a
quiet slot, or accept the gap deliberately.

---

## 2. Build

```bash
cd /home/secus/codeProjects/virtualTubers
docker compose build worker-coder          # any worker target builds vtube-worker:latest
```

All seven worker services (including the new `worker-gm`) share
`image: vtube-worker:latest` with `pull_policy: never`, so one build serves all.

**Verify the new files actually landed in the image before recreating anything:**

```bash
docker run --rm --entrypoint sh vtube-worker:latest -c \
  'ls -1 /app/tile_pane.py /app/voice_registry.py \
         /config/panels/tile.yaml /config/layouts/roundtable.yaml && \
   grep -c "voice_registry.py --verify" /startup.sh'
```

Expect four paths listed and a non-zero grep count. If any is missing, STOP —
recreating on a stale image is how you get a confusing half-upgraded stack.

---

## 3. Recreate

Order matters: infra first, then message-api (it gains the registry mount), then
workers, then the GM last so its dependencies are already healthy.

```bash
# 1. message-api — picks up the config/voices.yaml mount + VOICE_REGISTRY_PATH
docker compose up -d --force-recreate message-api

# 2. the six character workers — new image, renamed env keys
docker compose up -d --force-recreate \
  worker-coder worker-coder-native worker-coder-opencode \
  worker-coder-aider worker-tester worker-manager

# 3. the NEW 7th container — GM / roundtable
docker compose up -d worker-gm
```

`docker compose up -d` alone would also work (compose recreates what changed),
but the explicit order makes a failure easy to localize.

---

## 4. Post-upgrade verification

Work down this list; each step gates the next.

### 4.1 Containers and image

```bash
docker ps --filter name=virtualtubers --format '{{.Names}}\t{{.Status}}'   # expect 18 now (was 17)
docker inspect virtualtubers-worker-gm-1 --format '{{.Config.Image}}'
```

`worker-gm` must be present and NOT restart-looping. A crash loop here is most
likely the Xvfb display or a missing mount — check `docker logs`.

### 4.2 The new code is actually in place

```bash
docker exec virtualtubers-worker-gm-1 sh -c \
  'ls /app/tile_pane.py /app/voice_registry.py /config/voices.yaml'
```

### 4.3 V2 voice-registry check ran at boot

```bash
docker logs virtualtubers-worker-gm-1 2>&1 | grep -i "voice registry"
```

Expect `Voice registry OK: 9 voice(s) … 0 failed`. A WARNING here names the
offending voice — fix it before attempting any airing, because a bad voice makes
the director refuse the show on **every** channel.

### 4.4 The registry resolves inside the container (real paths, not rebased)

```bash
docker exec virtualtubers-worker-gm-1 python3 /app/voice_registry.py --verify
docker exec virtualtubers-message-api-1 python3 -c \
  "import sys; sys.path.insert(0,'/app'); import voice_registry; print(voice_registry.names())"
```

The GM sees the real `/data/voices/*.onnx`. message-api must list the same NAMES
while having **no** `/data/voices` mount — that asymmetry is the whole point of
the registry (§7.2) and is worth confirming once, live.

### 4.5 V1 rejects a bad voice through the real HTTP path

```bash
# a show header naming a voice that is not in the registry must 400, naming it
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  'http://localhost:8090/replays?name=v1_probe' \
  -H 'Content-Type: application/json' --data-binary @/tmp/bad_voice_episode.json
```

Expect `400`. This is the B1 regression guard exercised end to end rather than in
a unit test. (Build the fixture with a `show.persona.<slot>.voice` of
`not_a_real_voice`.)

### 4.6 The roundtable renders

`replay_pane` / `tile_pane` draw into tmux on an X display and log **nothing** to
`docker logs`, so verify on the rendered frame — this exact trap previously
caused a false "the duet is broken" report:

```bash
docker exec virtualtubers-worker-gm-1 sh -c \
  'ffmpeg -loglevel error -f x11grab -video_size 1920x1080 -i :105 -frames:v 1 -y /tmp/f.png'
docker cp virtualtubers-worker-gm-1:/tmp/f.png /tmp/gm_frame.png
```

Then LOOK at it. Expect 7 idle tiles (`tuber_0`…`tuber_6`, GM top-left with a
magenta border), the show-log pane across the bottom, and an htop strip. This is
also the §8.5 geometry confirmation with real content — the last unverified claim
in the design.

```bash
docker exec virtualtubers-worker-gm-1 tmux list-panes -t worker \
  -F '#{pane_index} #{pane_width}x#{pane_height} #{pane_title}'
```

Expect 10 panes; tiles ~58-61 cols × 18-19 rows. (Container tmux is 3.2a, where
`-p` works — the `-p`/`-l` problem is host-only.)

### 4.7 The six existing channels still work

```bash
# each worker still broadcasting
for w in coder coder-native coder-opencode coder-aider tester manager; do
  printf '%s ' "$w"
  docker exec virtualtubers-worker-$w-1 sh -c 'ps -eo etimes,args | grep "[f]fmpeg" | head -1' \
    | awk '{print "ffmpeg up " $1 "s"}'
done
```

Then confirm **externally** that Twitch is accepting — a live ffmpeg process is
not proof. Use the GQL `UseLive` check from the `virtualtubers-stream-ops` skill
against the channels listed in `.env`'s comments.

### 4.8 Backwards compatibility (the §7.4 contract)

Air one pre-existing episode that has **no** `show` header and still speaks as
`coder`/`tester`/etc. It must behave exactly as before the upgrade:

```bash
curl -s -X POST http://localhost:8090/messages -H 'Content-Type: application/json' \
  -d '{"to":"manager","type":"replay_request","payload":{"episode":"sample","voice":true}}'
```

This is the single most important post-upgrade check: it proves the v1.0 shipping
path survived. Verify on the frame, not the logs.

---

## 5. Known state after this upgrade

The roundtable will render **idle tiles only**. It cannot yet air a show, because
the director does not write the per-tile cue/request relay files —
`tile_pane.py` polls them and nothing produces them. That is **WP-6 task 1**
(`wp6_rehearsal_and_e2e_plan.md`), the last missing wire. Expect:

- 7 idle tiles that never advance, even during an airing on other channels.
- The six character channels airing normally (unchanged duet path).

That is the correct, expected state — not a bug from this upgrade.

---

## 6. Rollback

```bash
docker tag vtube-worker:pre-roundtable vtube-worker:latest
docker compose stop worker-gm && docker compose rm -f worker-gm
docker compose up -d --force-recreate \
  worker-coder worker-coder-native worker-coder-opencode \
  worker-coder-aider worker-tester worker-manager message-api
```

`.env` needs no rollback (values are unchanged and verified identical), but a
timestamped `.env.bak.*` exists if wanted. Nothing in this upgrade migrates
Postgres, so the episode library and `voiced_narration` are untouched — there is
no data rollback to perform.
