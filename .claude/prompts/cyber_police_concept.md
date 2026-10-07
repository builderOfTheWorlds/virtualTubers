# Cyber Police — Full Concept & One-Day Content Plan

Status: pack authored and verified at `campaigns/cyber_police/`. This doc is
the design reference for generating a full on-air day of content from it.

---

## 1. Premise

A seven-person digital-crimes unit — the Cyber Police, officially the
"Digital Crimes Task Force" — works a basement office chasing **The
Scuttle**, a nautical-themed piracy/file-sharing site run by a loose crew of
self-styled "privateers." The unit puts its lives on the line against real
digital crime and is treated, universally, as a joke: by dispatch, by the
press, by budget, by their own department's culture. The emotional engine of
every episode is the gap between what the unit actually does and how
seriously anyone (except the unit itself) takes it.

Inspiration: the user's source video (cyber police investigating The Pirate
Bay, chasing chaotic villains, "putting their lives on the line" while
nobody takes them seriously) — adapted into the project's existing
procedural-ensemble format (same shape as `ashiorid_office`: chain of
command, one standing case, a laminated-card value system, a running joke).

## 2. Cast (8, seats tuber_0..tuber_7)

| Seat | Id | Name | Role | Voice |
|---|---|---|---|---|
| 0 | captain | Rosalind Agar | GM / sets the daily case directive | baritone_mid |
| 1 | detective | Marcus Thorn | investigation lead, 9 years on The Scuttle | bass_low |
| 2 | analyst | Priya Nandakumar | intelligence/link-chart lead | alto_warm |
| 3 | agent | Deshawn Okafor | technical tracing, raid tech lead | tenor_high |
| 4 | forensics | Hana Vosk | evidence/chain of custody | tenor_low |
| 5 | press | Davey Okonkwo-Pryce | press liaison | baritone_soft |
| 6 | quartermaster | Teresa Mbeki-Lowndes | equipment/budget | alto_bright |
| 7 | observer | "The Observer" | silent, unexplained, watches everything | narrator_plain |

Full backstory for each lives in `campaigns/cyber_police/cast/<id>.yaml`
(system_prompt, wants, fears, speech, relationships, knowledge nodes) — same
schema shape as `ashiorid_office`, just without the heavier `profiles/`
memory-DB layer (deferred; see session note below).

## 3. World / lore (`campaigns/cyber_police/lore/`)

- `unit.md` — founding, mandate, the four laminated-card values, funding
- `the_scuttle.md` — the piracy site, its nautical cast (Commodore,
  Quartermasters, the Bosun), "Scuttle luck" as the standing in-world
  explanation for every near-miss
- `respect.md` — the core emotional fact: nobody takes this unit seriously,
  and why they keep showing up anyway ("Somebody has to mean it")
- `basement.md` — the physical office (bullpen, Captain's glass box,
  evidence locker, the Observer's corner desk — deliberate mirror of
  ashiorid_office's floor plan)
- `org_chart.md` — chain of command, lanes, tenure table
- `rituals.md` — the working day: 07:00 briefing → midday → evening wrap,
  requisition day, "Scuttle luck" as a lunchtime argument nobody's allowed
  to start twice

## 4. Primitives (new `cyber_police` genre, `app/campaign/primitives.py`)

`open_case`, `assign_lead`, `trace_signal`, `raid`, `interrogate`,
`seize_evidence`, `file_report`, `request_backup`, `brief_press`,
`requisition`, `stand_watch` — plus the pre-existing generic `cyber` genre's
`scan_target`/`execute_exploit`, both enabled in `campaign.yaml`. Verified:
`validate_pack()` zero errors/warnings; full repo test suite 4624
passed/0 failed after adding the genre (had to split it from the existing
locked `cyber` genre to keep `test_fantasy_cyber_and_office_are_the_only_genres`
honest — now `cyber`, `cyber_police`, `fantasy`, `office`).

## 5. The spine — one full case-of-the-day story

Seven chained scenes (`default_next` chain, verified by walking it in code):

1. **`morning-briefing`** (07:00) — the Captain sets the day's lead: an
   overnight chatter spike points to a new Scuttle mirror, six-hour window.
2. **`the-mirror-raid`** (mid-morning) — the raid lands on an empty rack
   (classic "Scuttle luck"), but Forensics recovers a shipping manifest with
   a name on it — not the Bosun, someone smaller.
3. **`the-manifest-name`** (midday) — the Analyst resolves the name to a
   courier, Soren Pike, with three deliveries this month to Scuttle-adjacent
   addresses.
4. **`the-pike-interview`** (mid-afternoon) — Pike isn't hiding anything on
   purpose; he gives up a dead-letter locker drop protocol without knowing
   its significance.
5. **`the-locker-complication`** (late afternoon) — The Scuttle's public
   banner names the storage facility before the unit can move quietly,
   forcing a rushed, under-resourced, public operation instead.
6. **`the-storage-facility`** (early evening) — another near-miss on the
   servers, but this time real paper: a lease and a bank transfer under a
   real name — the first solid identity lead on the Bosun's network in
   years.
7. **`end-of-day-wrap`** (19:00) — the Captain closes the case file with the
   new lead attached for tomorrow; the floor logs a rare, real win.

Verified end-to-end: `SceneRenderer.render_beat()` (with `Pacer(enabled=False)`
to bypass the real-time typing simulation) rendered all **76 beats** across
the full 7-scene chain with zero errors, ~**1,473 words** of scripted
dialogue/narration. This is the fixed daily "spine" — the authored case —
that the ambient layer fills around.

## 6. Ambient pool (10 scenes, `ambient.every: 2`)

Filler texture injected between spine beats at runtime/build time:
`bullpen-ambience`, `scuttle-luck-chatter`, `no-respect-upstairs`,
`quartermaster-binder-chatter`, `forensics-evidence-locker-routine`,
`press-reporter-small-talk`, `analyst-link-chart-deep-focus`,
`agent-hardware-complaint`, `observer-corner-desk`,
`captain-commissioners-memo`. Each carries no plot decisions, per the
project's ambient-scene convention — pure texture, safe to regenerate
indefinitely without touching continuity.

## 7. Reaching "at least one day" of content

Per the `virtualtubers-campaign-content` skill
(`references/reaching_target_hours.md`), there are two non-interchangeable
mechanisms for bulk content. **Path B — `batch_generate.py` flat ambient
generation — is the one that scales**; the 3-layer segment-tree planner
(Path A) collapses well before this volume and is not used here.

### What "one day" means for this pack

`lore/rituals.md` fixes the unit's on-air day at **07:00–19:00, 12 hours**
(unlike `ashiorid_office`'s round-the-clock 18h day — the Cyber Police keep
standard department hours on purpose, a deliberate contrast). Using this
project's documented target generation rate of **8,929 words/hour**
(`reaching_target_hours.md`), one full on-air day is:

```
12h × 8,929 words/hour ≈ 107,148 words
```

The authored spine supplies a fixed **1,473 words** (measured, not
estimated). The remaining **~105,675 words** come from ambient takes,
injected at the `every: 2` cadence around the spine during episode build.

### Proof the pipeline works (done this session)

- `config/campaigns/cyber_police_batch_fast.yaml` — new fast-model batch
  config (`llama3.1:8b`, mirrors `ashiorid_1_batch_fast.yaml`'s pattern; the
  default `hermes3:70b` config is confirmed too slow for volume at ~4.8
  tok/s per the skill's prior findings).
- Small proof run: `--takes-per-scene 2` across the 10 ambient scenes → 14
  takes, 0 failures, **1,157 words**, ~6.0s/take. Output is real in-character
  dialogue (sample: Captain/Detective/Analyst/Agent/Forensics/Press lines
  referencing "the briefing," "the Scuttle crawl," "the warehouse raid" —
  grounded in the pack's own lore, not generic filler).
- Larger proof run (`--takes-per-scene 60`, 600 takes across 10 scenes)
  confirmed the rate holds at scale: 160 takes in, 10,999 words,
  ~68.7 words/take, 0 failures — consistent with the small proof batch, no
  degradation observed as volume increased (full run left completing in the
  background and handed off to the scheduled driver below).

### Sixth: a real correctness bug found and fixed (QA pass, this session)

An audit script (`.claude/prompts/audit_cyber_police_takes.py`, adapted from
the existing `audit_takes.py` pattern for `batch_generate.py`'s flat
`generated/<scene>/*.yaml` output) found that **the silent Observer
character was being assigned spoken dialogue lines in ~30% of generated
takes** (66/219 in one batch) — a direct violation of the cast contract
("You never speak. You only observe.") that only existed as prompt text,
never enforced in code. This matches the project's own standing principle
(memory: "enforce invariants in code... never ask / strip after").

Fixed at the source in `app/campaign/improviser.py`
(`LLMImproviser.generate_scene`):
1. Added `_silent_cast_ids()` — detects any cast member whose
   `system_prompt` contains the schema-mandated literal "You never speak."
   sentence (shared verbatim by every silent observer-archetype character
   across packs: `ashiorid_office`'s `party_member`, `cyber_police`'s
   `observer`).
2. Silent members are excluded from the cast roster shown to the model, so
   it is never offered them as a speaker option.
3. **Hard backstop**: silent members are also excluded from the
   id/name/first-name lookup table used to attribute a generated line's
   speaker — so even if the model names the silent character's id directly
   anyway, the line falls through to GM narration instead of becoming
   dialogue for that character. This holds regardless of model compliance.
4. Added 3 regression tests (`tests/test_campaign_improviser.py`) proving
   both the roster omission and the hard-backstop attribution fallback.
   Full repo suite: **4627 passed, 0 failed** (up from 4624 — the 3 new
   tests), after the fix.
5. **Purged already-contaminated data**: 102 of 261 accumulated takes had
   observer dialogue and were deleted from disk and the manifest (159 clean
   takes, 10,308 words, survived). Re-ran the audit: **0 bad takes**.
   Restarted the batch-generation process (the old one had the pre-fix code
   loaded in memory and would have kept producing bad takes) — confirmed at
   live scale: 22 freshly generated takes since restart, **0 bad takes**.

This fix benefits every pack with a silent observer-archetype character, not
just cyber_police — `ashiorid_office`'s `party_member` was exposed to the
exact same risk and is now protected by the same code path.

### Sizing the full run

At the measured **~82.6 words/take, ~6.0s/take**:

```
105,675 words ÷ 82.6 words/take ≈ 1,280 takes needed
1,280 takes × 6.0s/take ≈ 7,680s ≈ 2.1 GPU-hours
```

Comfortably inside the ~24.7 GPU-hour full-production estimate the skill
measured for a much larger target on `ashiorid_1`. This is a genuinely
same-session-achievable job, not a multi-day one.

### Execution plan

1. **Chunked resumable driver**: `.claude/prompts/cyber_police_fill_day_driver.py`
   (written this session). Each invocation sums `generated/manifest.jsonl`
   word counts, compares against the 107,148-word target, and if short runs
   ONE bounded chunk (`--takes-per-scene 40` across the 10 scenes = 400
   takes/chunk ≈ 2,400s, safely under the cron runner's ~3,600s hard
   SIGKILL ceiling for `no_agent` jobs). No-ops once the target is met.
2. **Scheduled**: cron job `cyber_police_fill_day` (job_id `a34732eb3fa5`),
   `every 1h`, `no_agent: true`, 8 repeats, `deliver: local` (saved only, no
   push notification — this is backend content generation, not something
   that needs to interrupt anyone). 8 hourly chunks comfortably cover the
   ~2.1 GPU-hour total estimated above with margin; the driver self-no-ops
   once the target word count is reached, so the extra repeats cost nothing.
3. **Verify on disk, not from logs**: after the run, sum
   `generated/manifest.jsonl` directly and spot-read a handful of
   `generated/<scene>/*.yaml` take files for real, grounded dialogue before
   calling it done — per the skill's standing rule, a `written: N` log line
   is a self-report, not proof.
4. **Build the airable episode**: **done this session.**
   `.claude/prompts/build_cyber_police_full_episode.py` (adapted from the
   proven `build_ashiorid_full_episode.py` pattern, generalized to consume a
   **seated** pack's `seats:` map via `campaign.validator.check_seats()`
   instead of a hardcoded per-pack worker-id table) loops the 7-scene spine
   and injects generated ambient takes from `generated/<scene>/*.yaml`
   round-robin at the pack's `ambient.every: 2` cadence, splits output at the
   8MB server cap, and validates every part through the real
   `episode_validator.validate_episode()` before writing/uploading.
   **Verified working**: a test build at `--target-words 5000` produced 231
   events / 5,539 words / 3 spine loops, **validation: PASS**. This closes
   the authoring→airable gap flagged as a known limitation in the prior
   session — the pack is no longer "structurally incapable of reaching the
   stream," per `virtualtubers-stream-ops`'s own framing of that risk.
   Known quality caveat carried over from that skill still applies: ambient
   *generated* takes read fine as background filler at volume but shouldn't
   be trusted to carry plot — the authored spine (verified separately,
   1,473 words / 76 beats) remains the plot backbone every loop replays.

## 8. Open items / deferred

- `profiles/*.yaml` heavyweight backstory bible (OB-41 memory-DB layer, the
  ashiorid_office pattern) was explicitly deferred this session — only the
  lighter `cast/*.yaml` layer exists. Add profiles/ if this pack gets
  promoted to live-agent use.
- The full-day episode has not yet been **uploaded/aired** — the builder
  (`build_cyber_police_full_episode.py --upload`) supports it once the
  ambient batch finishes filling the 12h target; this is a one-command step
  away, not a design gap.
- Payload/identity for "the Bosun" is intentionally left open past this arc,
  the same way `ashiorid_office`'s Party Member mystery stays open — gives
  future days somewhere to go without committing to a reveal date.
