# Go-live plan: ashiorid_office, Saturday 2026-10-24 night, 168h

Owner: Hermes (Opus orchestrator) + subagents (Sonnet 5.5 for code/judgement,
Haiku 5.5 for recon/mechanical). Started Wed 2026-10-21 01:00 EDT.
Cold resume: read this file top to bottom, then the Progress log.

## Definition of done
1. A week of scene contracts for ashiorid_office in the generator DB: one arc
   plan, 28 six-hour blocks (Sun..Sat), brief+tree per segment, audited.
2. The office live table plays them in auto mode, unattended, for the whole
   week, with ambient fill per D1, and survives GM/container restarts.
3. All office channels verified LIVE externally and on the rendered frame.
4. Watchdog + runbook so it runs with no operator.

## Decisions (user, Wed 01:30)
D1 Airtime fill: each 6h story block is tied to its real 6h wall-clock window.
   Block spine contracts play first, then the agents improvise plot-free
   AMBIENT office scenes until the window ends (quieter overnight).
D2 Week arc+segment planning model: hermes3-70b on vLLM (:8092). Must finish
   before the soak test (table model vllm-agents cannot share memory with it).
D3 Hermes main + auxiliary models moved to Anthropic (done 01:35; backup
   ~/.hermes/config.yaml.bak_golive_20261021). Ollama unloaded; keep it empty.
D4 On air: ALL agents - roundtable, GM (tuber_0), 6 characters (tuber_1..6),
   observer/Party Member (tuber_7).

## Orchestrator assumptions (flag to user, reversible)
A1 Go-live Sat 2026-10-24 20:00 EDT. 20:00-24:00 = prelude (ambient,
   evening phase). Story block 0 (Sunday 00-06) starts Sun 25 00:00 so the
   in-fiction weekday matches the real weekday. Block 27 ends Sat 31 24:00.
A2 Spine still unplayed when its window ends carries over (plays before the
   next block's spine; ambient dropped). Never loop spine.
A3 A stuck scene never halts the unattended run: cool down, fall back to
   ambient, log loudly.
A4 Ambient promotion into the pack (Postgres pack_scenes) stays a user
   approval step; staged under .claude/prompts/office_ambient_stage/.

## Timeline
  Wed 21  gen: hermes3-70b week arc + 28 segments | code: block clock, ambient,
          ambient pool authoring (same vLLM)       | tile_pane leak, config fixes
  Thu 22  gen audit + regen bad segments; watchdog; stream readiness;
          swap vLLM hermes3-70b -> vllm-agents; full office stack up
  Fri 23  SOAK 12h+ unattended on the real contracts (clock offset to "now")
  Sat 24  soak fixes; 18:00 final checks; 20:00 GO LIVE; watchdog on

## Blockers found in recon (Wed 01:00-01:40)
B1 Content: 21 playable office scenes (~1h). Table had no filler -> D1.
B2 Full-week arc failed because vLLM died mid-run (exit 137), not content.
B3 worker-gm runs the D&D table config, not the office one.
B4 Office config: no auto_pace/auto_duration, adjudicate max_tokens 256
   (needs 512), start_mode manual (no auto-resume after restart).
B5 3 consecutive stuck scenes HALT the table until an operator acts.
B6 worker-observer (tuber_7) exited 137, 8 days ago.
B7 MEMORY LEAK: worker-roundtable at 34 GiB after 3.8 days; tile_pane.py
   procs 4-10 GiB RSS each (tuber_3 10.4 GiB). A 7-day run would OOM the host.
B8 hermes3-70b at util 0.70 (~85 GiB) only fits with stream workers stopped.
   All virtualtubers-worker-* stopped Wed 01:40 for the generation window.

## Workstreams
WS1 (Sonnet) Week contracts on hermes3-70b via the generator API (arc+segment
    only). Driver as a systemd user unit; logs persisted to file.
WS2 (Sonnet) Table: block clock + ambient contracts + unattended hardening.
WS3 (Sonnet) Ambient pool: ~150 plot-free office scenes by day_phase, authored
    on hermes3-70b, gated, staged (not promoted).
WS4 (Sonnet) tile_pane.py memory leak: reproduce, fix, prove flat RSS.
WS5 (Haiku) Stream readiness recon (keys/channels, observer crash, overlay).
Later: WS6 watchdog, WS7 soak + frame/Twitch verification.

## Seam: ambient office scene (shared by WS2 and WS3)
Standard pack ambient scene YAML: id, title, ambient: true, prompt, lore: [],
source: {...}, PLUS office keys (pack.py ignores unknown keys):
  day_phase: off_hours | morning | workday | evening
  intensity: quiet | normal      (off_hours scenes are quiet)
  seats: [cast ids]              (who is present; 2-4 normal, 1-2 quiet)
Cast ids: ceo, tech_lead, analyst, engineer, tester, marketing,
office_manager, party_member.
Day phases by local hour: off_hours 00-06, morning 06-09, workday 09-17,
evening 17-24.

## Hard constraints
- One 70b-class model loaded at a time on argyre GB10 (OOM crash history).
- Every generator run goes through the generator API (job ids).
- Verify on air via rendered frame + external Twitch check.

## Progress log
- Wed 01:00 recon (2 Haiku subagents) done; findings above.
- Wed 01:35 Hermes off Ollama; Ollama unloaded; workers stopped;
  vllm-hermes3-70b starting with VLLM_GPU_MEM_UTIL=0.70.
- Wed 01:50 dispatched WS1-WS4 (Sonnet, deleg_76ea9518) and WS5 (Haiku,
  deleg_202d128f). systemd units to expect: office-golive-gen (WS1),
  office-ambient-author (WS3). Delegation model switched per batch with
  `hermes config set delegation.model ...` (currently haiku).
- Wed 02:00 WS5 done. New blockers:
  B9  TUBER7_STREAM_KEY missing in .env -> observer streams to key 'tuber7',
      ffmpeg crash-loop. USER must add the key (secret; agent never types it).
  B10 Twitch logins for roundtable + GM channels are not in .env comments;
      needed for the external live check (ask user, non-secret).
  B11 Key var names are legacy-role mapped (TUBER1 -> worker-coder = tuber_6
      Office Manager -> the_emperor_secus, etc.). Confirm intended mapping.
  Notes: image vtube-worker:latest is current with HEAD (rebuild needed only
  after WS2/WS4 land). Dev-team stack and office overlay must never run
  together (same names/displays/keys). Disk 86% used, recordings capped 5 GB.
  Live check: raw GQL user(login){stream{createdAt}} primary, decapi second.
  WS5 echoed 6+4-char key fragments in its transcript
  (~/.hermes/cache/delegation/live/deleg_202d128f/task-0.log).
- Wed 03:05 WS1-WS4 done; committed 24b45e0 (code+tests+drivers; user's
  CLAUDE.md/todo.md edits left uncommitted). Suite 4415 passed.
  WS1: config 14 ACTIVE, run ashiorid_office_20261021_054820_9f48, arc 28/28
       verified (artifact 798). Segments running in unit office-golive-gen,
       chunk of 4 = ~43 min -> ETA all 28 ~07:30 Wed.
       Quality issues to audit: sunday-morning says pilot already live (wrong);
       first briefs all kind 'ambient', prompts restate synopsis, sibling
       leaves repeat; first-standup is the only pinned spine scene, so every
       morning pins it; several off-hours blocks end with 'Office Manager
       arrives early' (arrival-loop risk).
  WS2: block clock + ambient + unattended in. Operator steps: set
       week_run_id in config/table/ashiorid_office.yaml, export fixture
       (scripts/export_table_fixture.py <run>), rebuild configs. Empty ambient
       pool = silent table (logs 'no ambient scene to play'). GM container needs
       GENERATOR_POSTGRES_PASSWORD in .env for the pack_scenes read (or use
       ambient.dir on /data/campaigns). Perf note: Arbiter makes all 6 seats
       think every round even in 2-seat ambient scenes (app/turns.py).
  WS3: was 401-blocked. Cause: the vLLM container took VLLM_API_KEY from the
       Hermes shell env (overrides the deployment .env) -> keys differ.
       Restarted office-ambient-author with AMBIENT_LLM_KEY from the container
       env; accepting ~1.5-2 scenes/min -> 150 by ~04:30. Fix the deployment
       key mismatch next vLLM start (run compose with `env -u VLLM_API_KEY`).
  WS4: leak = unpumped pygame/SDL events on SHAPE-clipped windows (~1 KB/frame).
       Fixed in tile_avatar + (orchestrator) codec_avatar.py for solo heads.
       Needs image rebuild + live RSS check at t0/+1h/+6h.
       Open: TILE_BREATH_SCALE is 5.0 (commit 5f06abe set it deliberately?);
       skill says 0 avoids NVENC freeze. Ask user.
- Wed 03:10 WS6 watchdog+runbook dispatched (Sonnet, deleg_c9ba73b8).
  NEXT (after 07:30): WS7 contract audit + targeted rebrief (Sonnet);
  then switchover: stop vllm-hermes3-70b, start vllm-agents
  (`env -u VLLM_API_KEY docker compose up -d`), rebuild vtube-worker, export
  fixture, set week_run_id, promote ambient (USER approval), bring up office
  stack, soak with block_clock.offset_s.
- Wed 03:20 WS6 done, committed b85e17e (84 tests). Units NOT installed;
  install at T-30m Sat per .claude/prompts/golive/RUNBOOK.md. Notes:
  /table/status on :8082 is HTML; watchdog uses table_ctl's message-api
  protocol. Alert sink is a no-op (event log only) - wire a Hermes gateway
  delivery if the user wants pings. `touch ~/.local/state/office-watchdog/PAUSE`
  before any manual stop or the watchdog undoes it within 2 min.
  Open: virtualtubers-tts-gpu-1 exited 2 weeks ago - confirm workers don't
  need it. Memory now 113/121 used, 7 GiB available (hermes3-70b at 0.70 +
  stack); do NOT start anything else until generation ends.
  Ambient author: 32 accepted by 03:14 (~2/min).
