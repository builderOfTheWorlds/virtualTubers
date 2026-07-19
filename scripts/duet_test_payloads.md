# Duet test payloads — worker1.json .. worker6.json

Ready-to-curl POST bodies for `POST /messages` (message-api, port 8090),
one per cast size. All target the same known-working episode
(`2026-07-02_04-27-00_6ecdde82`, `narration: "reuse"`) so results are
comparable across files. `coder` is always the director.

```bash
curl -X POST http://192.168.1.120:8090/messages \
  -H "Content-Type: application/json" \
  -d @scripts/worker2.json
```

| File | Cast size | Director | Real speakers (own audio) | Non-speaking followers |
|---|---|---|---|---|
| worker1.json | 1 | coder | coder (solo, no `cast` field) | — |
| worker2.json | 2 | coder | coder, manager (boss) | — |
| worker3.json | 3 | coder | coder, manager (boss) | tester |
| worker4.json | 4 | coder | coder, manager (boss) | tester, coder-native |
| worker5.json | 5 | coder | coder, manager (boss) | tester, coder-native, coder-opencode |
| worker6.json | 6 | coder | coder, manager (boss) | tester, coder-native, coder-opencode, coder-aider |

v1 episode scripts only ever produce two speakers (`"boss"`/`"coder"` —
see `docs/duet_replay.md` "Ownership & uncast-speaker defaulting"), so
worker3-6 invite extra followers under synthetic cast keys
(`extra1`, `extra2`, ...) that don't match any real scene speaker. Those
workers still join the duet, load the airing, render full visuals, and
follow every cue — they just never own a scene, so they sit "listening"
the entire episode instead of speaking. This is intentional: it's testing
that invite/ready/cue fan-out scales to N followers, not N distinct
voices.

## Known gap: worker4/5/6 will refuse as shipped

`coder-native`, `coder-opencode`, and `coder-aider` are **not**
duet-capable in the current `docker-compose.yml` (checked 2026-07-18).
Compared to `coder`/`manager`/`tester`, each of those three is missing:

- `LAYOUT_PRESET` override — no env var is even wired up for them (no
  `CODER_NATIVE_LAYOUT_PRESET` etc.), so there's no way to put them in
  `replay` mode without adding one.
- `POSTGRES_HOST`/`PORT`/`DB`/`USER`/`PASSWORD` — required for
  `narration_store.available()` to be `True`; a follower can't
  `load_airing` without it.
- The `/opt/virtualTubers/replays:/data/replays:ro` and
  `/opt/virtualTubers/voices:/data/voices:ro` volume mounts.

Until those three env/mount blocks are extended to match `worker-coder`'s
(see `docker-compose.yml` lines ~55-134 for the current blocks, ~3-52 for
what to copy from), `worker4.json`/`worker5.json`/`worker6.json` will
invite those followers but they'll never publish `replay_ready` — the
director will hit `REPLAY_READY_TIMEOUT_S` (60s) and refuse the whole
airing with `reason: "ready_timeout"` (per `docs/duet_replay.md`
"Refusal rule"). `worker3.json` (tester only) works today since tester
already has all three.

**TODO**: add `LAYOUT_PRESET`/`POSTGRES_*`/replay-library+voices mounts
to the `worker-coder-native`, `worker-coder-opencode`, and
`worker-coder-aider` service blocks in `docker-compose.yml`, matching
`worker-coder`'s block, before worker4-6 tests can pass.
