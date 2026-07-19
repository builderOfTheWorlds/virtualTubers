# Duet test payloads — worker1.json .. worker6.json

Ready-to-curl POST bodies for `POST /messages` (message-api, port 8090),
one per cast size. All target the same hand-authored fixture episode
(`sample`, `narration: "reuse"`) so results are comparable across files.
`coder` is always the director.

```bash
curl -X POST http://192.168.1.120:8090/messages \
  -H "Content-Type: application/json" \
  -d @scripts/worker2.json
```

| File | Cast size | Director | Real speakers (own audio) | Non-speaking followers |
|---|---|---|---|---|
| worker1.json | 1 | coder | coder (solo, no `cast` field) | — |
| worker2.json | 2 | coder | coder, manager (boss) | — |
| worker3.json | 3 | coder | coder, manager (boss), tester | — |
| worker4.json | 4 | coder | coder, manager (boss), tester, coder-native | — |
| worker5.json | 5 | coder | coder, manager (boss), tester, coder-native, coder-opencode | — |
| worker6.json | 6 | coder | coder, manager (boss), tester, coder-native, coder-opencode, coder-aider | — |

The "Non-speaking followers" column is kept for historical comparison but
no longer applies to any row — see below.

Real (parsed) episode scripts still only ever produce two speakers
(`"boss"`/`"coder"` — see `docs/duet_replay.md` "Ownership & uncast-speaker
defaulting"), since a recorded session is inherently one human and one
assistant. That limitation has been lifted for hand-authored scripts —
landed, see `docs/revoice.md`'s changelog: `plan_scenes` now honors an
optional per-event `"speaker"` override, so a fixture like
`replays/sample.json` can tag up to 6 distinct personas with real
dialogue. worker3-6 now cast `tester`, `coder-native`, `coder-opencode`,
and `coder-aider` under their own real speaker names instead of the old
synthetic `extra1..4` placeholders, and each one owns and speaks its own
scene against `sample.json` — no more idle listeners. This still exercises
invite/ready/cue fan-out to N followers, just now with N distinct voices
too.

## Fixed 2026-07-19: worker4/5/6 now have what they need

`coder-native`, `coder-opencode`, and `coder-aider` used to be missing
`LAYOUT_PRESET` override env, `POSTGRES_*`, and the replay/voices volume
mounts that `coder`/`manager`/`tester` already had — `docker-compose.yml`
now wires all three the same way (`CODER_NATIVE_LAYOUT_PRESET` /
`CODER_OPENCODE_LAYOUT_PRESET` / `CODER_AIDER_LAYOUT_PRESET`,
`POSTGRES_HOST`/`PORT`/`DB`/`USER`/`PASSWORD`, and the
`/opt/virtualTubers/replays:/data/replays:ro` +
`/opt/virtualTubers/voices:/data/voices:ro` mounts).

All three now **default to `replay`** in `docker-compose.yml` (no stack
env needs to be set) — set `CODER_NATIVE_LAYOUT_PRESET`/
`CODER_OPENCODE_LAYOUT_PRESET`/`CODER_AIDER_LAYOUT_PRESET` to `coder` in
the Portainer stack env if you want one of them back to its normal
editor pane instead.

**Still required before worker4/5/6 will actually pass**, since this was
a compose-file change: rebuild `vtube-worker:latest` (no new dependency,
same image as before) and redeploy the stack — same two-step deploy as
any other compose change (README's
[Deploy / redeploy](../README.md#deploy--redeploy-after-a-code-change)).
Until that redeploy happens, those three workers still boot with the
old image/env and will invite-then-timeout exactly as before
(`reason: "ready_timeout"`, `REPLAY_READY_TIMEOUT_S`, per
`docs/duet_replay.md` "Refusal rule").
