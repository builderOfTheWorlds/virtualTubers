# Ashiorid Inc. Office Campaign — Plan v0.2

Date: 2026-09-27. Status: plan only, nothing built. v0.2 includes the user's answers to the v0.1
questions (see §9).

## 1. Concept

Ashiorid is a financial-software company. Its eight employees believe they are real people.
The show is a full campaign arc, built the same way as the fantasy Ashiorid arc:
- 3-layer generation
- ring structure
- a spine plus ambient scenes
- a weekly loop that resets at Sunday 00:00
- memory fragments

Only the background changes: an office instead of a magical world.

The work the characters do comes from real coding sessions (Claude Code and others), which a new
corpus utility gathers from several machines. Sessions are used in two ways:
1. **Replay/source material:** the CEO's directives are drawn from the user prompts in those
   sessions.
2. **Live mode:** live role agents delegate to each other on the bus. The office campaign is the
   first test of the live-agent infrastructure.

## 2. Roster and chain of command

| Slot | Role | Reports to | Directs | Function |
|---|---|---|---|---|
| tuber_0 | CEO | (Party Member, silently) | Tech Lead, Analyst, Marketing, Office Manager | In-fiction GM. Sets the day's scenario and issues high-level directives, playing the "user" role from source sessions |
| tuber_1 | Tech Lead | CEO | Engineer, Tester | Knows the codebase at a high level. Longest tenure; has seen 2 CEOs. Combines the directive and the functional plan into a technical plan |
| tuber_2 | Analyst | CEO (peer of Tech Lead) | none | Turns requirements into a functional plan |
| tuber_3 | Engineer | Tech Lead | Tester (test requests) | Builds the code |
| tuber_4 | Tester | Tech Lead, Engineer | none | Tests the built code |
| tuber_5 | Marketing | CEO | none | Works out how to sell what's being built |
| tuber_6 | Office Manager (she) | CEO | none | Cleaning, coffee, snacks, HR, garbage collection |
| tuber_7 | Party Member | ? | nobody (v1) | Silent observer. Rumoured government spy. Never speaks |

- Office Manager replaces "Operations". Git commits and deploys go to the Tech Lead / Engineer
  chain, not to her.
- All 7 speaking roles and the Party Member have `retains_fragments: true` (user answer Q6).
- The Party Member's hidden truth is left undefined on purpose for now. v1 behaviour: observe only.

## 3. Company lore (to be drafted in WS-A)

- **Company:** Ashiorid. The name deliberately echoes the fantasy pack. A possible truth-layer hook:
  the office is the next "simulation layer" of the same world (`docs/weeklyLoopBrainstorm.md`
  genre-switch idea). This is a truth-layer idea only, and it needs sign-off.
- **Product (proposal; pick one or replace):**
  - a) **Ledgerline**: a reconciliation and close platform for mid-size finance teams. It matches
    bank feeds to the general ledger and flags anomalies. Good daily-work surface: parsers,
    matching rules, reports, audits.
  - b) **Ashiorid Vault**: a treasury and cash-forecasting SaaS.
  - c) **Tallymark**: a consumer budgeting app with a B2B payments API.
  -UserSuggstion: Fraud-Stop, a enterprise grade SaaS that banks can process transactions through to determine if they are fraduulent.
- Lore files:
  - `company.md`: founding, product, the 2 previous CEOs
  - `product.md`
  - `office.md`: floor plan, kitchen, server closet, the Party Member's desk
  - `org_chart.md`
  - `rituals.md`: 06:00 standup, lunch, Friday demo, 00:00 lights-out
  - `rumours.md`
  - `clients.md`: a few recurring customers or prospects for Marketing and for plot

## 4. Clock: an 18/6 day inside the weekly loop

The generator already cuts the week into 6 h segments, 4 per day from Sunday 00:00
(`utilities/3LayersWeeklyGeneration/config/generation.yaml:41-42`). The 06:00–00:00 work day lines
up exactly with these segment boundaries.

| Segment | Hours | Phase | Content |
|---|---|---|---|
| s0 | 00:00–06:00 | Off | Ambient: after hours, Office Manager cleaning, the Party Member in the dark office, characters' home life / "time off". Nightly `daily-maintenance` runs at 00:00 |
| s1 | 06:00–12:00 | Morning | Coffee, CEO standup directive, Analyst's functional plan, Tech Lead's technical plan, delegation |
| s2 | 12:00–18:00 | Build | Engineer builds, Tester tests, bug loop, lunch, Marketing pitches |
| s3 | 18:00–00:00 | Ship | Fixes, final test, release, Marketing launch, Office Manager garbage collection, wind-down |

- Week = loop, per `ring_composition_spec.md` (Sunday 00:00 anchor, 28 segments, default
  `plot_0` ring `[16,4,8]`).
- Ring question: are Sundays work days too? Seven work days is assumed below (Q-B).

## 5. Workstreams

### WS-A: Backstories and lore (FIRST, next up)

For each of the 8 characters:

1. `campaigns/<pack>/cast/<id>.yaml`, the pack cast file:
   - `name`, `archetype`, `voice`, `system_prompt`
   - the agent_dnd §6.2 extension fields: `wants`, `fears`, `speech`, `relationships`,
     `knowledge`, `turn_order_pos`
2. `campaigns/<pack>/profiles/<id>.yaml`, a baseline in v4 format (§4/§10 of
   `character_generator_updater_v4.md`):
   - `identity`: full name, age, pronouns, title, tenure, home life, commute, hobbies
   - `appearance`: free-text physical description. This is the input for avatar generation
     (see WS-A2)
   - `personality`: traits, speech tics, work style, stress response
   - `objectives`: career wants, fears, secrets
   - `backstory.believed`: first person, the life they think they have. The only layer they see
   - `backstory.truth`: GM-only
   - `backstory_nodes`: permanent knowledge and relationship nodes, e.g. `trusts-tech-lead`,
     `suspects-party-member`, `remembers-two-previous-ceos`. Nodes must pass the v4
     name-regex / verb-allowlist check
   - `behaviour_contract`: never say that time repeats; stay within your rank; the Party Member
     never speaks
   - `retains_fragments: true`, `is_main: true`

Process:
- Draft company lore first, because the backstories refer to it.
- Then write the 8 characters in parallel, then a consistency pass: relationships must be
  mutual-aware and tenures must be consistent.
- User review.
- Validation script: node names, required keys, and `app/campaign/cli.py --validate`.

WS-A2 is the avatars. The seam is `generator/avatar.py: map_appearance(profile) -> dict`
(v4 §9). **It is a Phase-4 TODO and is not built.** v1 returns `SLIDER_DEFAULTS`. So "analyze the
backstory → avatar" means building `map_appearance`:
- An LLM reads `appearance` + `personality`.
- It outputs the 8 sliders plus `accent_color` (`app/character_schema.py`).
- `resolve_params` validates the output.
- The result is written into the cast YAML `character_params` and into the roster snippet.

It's small and can go straight after WS-A.

### WS-B: Session corpus utility (new, multi-machine)

A stand-alone sibling utility (working name `sessionCorpus`) that collects coding-agent
transcripts from every environment into one store.

- **Collectors, one per source type:**
  - Claude Code raw JSONL (`~/.claude/projects/*/*.jsonl`)
  - claudeBackupUtility rendered logs (`logs/claude/<ts>_<id>/`)
  - OpenCode exports (like `session-ses_fe30.md`)
  - Hermes sessions
  - others as found (Codex, aider)
- **Transport:** each machine runs the collector locally (Windows `.bat`/Task Scheduler, Linux
  cron/systemd). It pushes to a central store, or the central box pulls over SSH. Hosts: dev PC
  (Windows), argyre (192.168.1.23), mafober (192.168.1.120), plus any others.
- **Normalise** everything into one event format, a superset of `session_log_parser`'s output:
  - fields: `source_tool`, `host`, `project`, `session_id`, `started_at`, `events[]`
  - event types: `user_message`, `assistant_text`, `tool_call{tool, input, output, error}`
- **Dedupe** by hash of (tool, session_id) plus content.
- **Redact at ingest:** run the same `LEAK_AUDIT` rules, plus a hostname/username allowlist per
  machine. Raw files never leave their host unredacted, or else raw is stored in a locked
  location (Q-C).
- **Store:** Postgres table(s) on mafober, e.g. `session_corpus`, `session_events`, with
  pgvector embeddings for later "find me a session like X".
- **Catalog CLI:** list, filter (by project, tool, date, size, success), tag (e.g. `feature`,
  `bugfix`, `deploy`), export.
- Follows the project conventions: install.sh + uninstall.sh pair, Loki logging, `--dry-run`.

### WS-C: Pack + campaign arc

- Pack `campaigns/<pack>/`. The fantasy pack already uses `campaigns/ashiorid`, so the new one
  needs a distinct name, e.g. `campaigns/ashiorid_inc` (Q-A).
  - `campaign.yaml`: `gm: ceo`, players, `genre: office`, `theme`, `ambient`, `primitives`,
    and a new `seats:` map from cast id to tuber_N.
  - Office primitives registered in `app/campaign/primitives.py`: `assign_task`,
    `write_spec`, `open_ticket`, `commit`, `run_tests`, `file_bug`, `deploy`, `pitch`,
    `brew_coffee`, `take_out_trash`, `hr_notice`, `observe`.
  - `music/theme.yaml`: office lo-fi.
- A new source adapter, `CorpusAdapter`, in
  `utilities/3LayersWeeklyGeneration/src/source_adapter.py`. It feeds lore notes plus selected
  corpus sessions, as `SourceNote(kind="work_session")`, into Layer 1. The module docstring
  anticipates exactly this ("transcript set").
- Arc design (Layer 1, `plot_0` ring `[16,4,8]` over 28 segments). The week's story is a product
  push, e.g. a Ledgerline release before a client demo or an audit. The keystone lands mid-week
  (a production incident, an auditor, a board visit, a third new CEO?). Daily work spines are
  drawn from corpus sessions.
- Secondary `plot_n` threads: Marketing's campaign, office drama, the Party Member rumour,
  déjà-vu tethers.
- Ambient pool: about 60 prompts tagged by phase (s0 off-hours, s1–s3 work) and by participants.
- Spine generation goes through the existing `services/3layer-generator` API; the
  `pack_gate` check comes before promotion.
- Fix `build_campaign_episode.py` so it reads `seats:` from the pack instead of the hard-coded
  `SPEAKER_TO_WORKER`.

### WS-D: Replay path (fallback / pre-generated content)

Role attribution for recorded sessions, in `app/office/role_attribution.py`:

| Session event | Role |
|---|---|
| user prompts | CEO |
| requirement restatement | Analyst |
| planning / todo | Tech Lead |
| edits / builds | Engineer |
| tests | Tester |
| cleanup | Office Manager |

- An LLM pass adds delegation hand-off lines, Marketing reactions and Party Member stage
  directions.
- Re-run `audit()` afterwards as a gate.
- Add per-role tone to the `revoice.py` prompts.

This keeps the stream on air when live mode fails. The agent_dnd design already specifies a
"fallback to recorded episode" (W2).

### WS-E: Live-agent infrastructure (tested with this campaign)

Base: `.claude/prompts/agent_dnd_architecture.md` (DRAFT v1.0), with the CEO as the GM agent.
The office adds one thing to that design: **delegation**. Characters don't just take turns; they
issue orders down the chain, and completed work reports back up.

- **Roles:**
  - new `agent.role` values: `ceo`, `tech_lead`, `analyst`, `engineer`, `tester`, `marketing`,
    `office_manager`, `observer`
  - handlers in `app/agent_handlers/`
  - `MESSAGE_HANDLERS` / `IDLE_TICK_HOOKS` registration
  - 8 `config/workers/*.yaml`
  - a compose service for tuber_7
- **Protocol:**
  - agent_dnd messages: `scene_start`, `turn_assignment`, `character_reply`, `retake`,
    `gm_overrule`
  - office additions: `directive` (CEO → TL/Analyst/Marketing/OM), `functional_plan`,
    `technical_plan`, `task_assignment` (the existing one), `test_request`, `bug_report`
    (existing), `status_report` (up the chain)
  - a rank check: an agent may only direct its own reports, per the §2 table. Violations are
    rejected and logged as a retake.
- **Work engine (Q-D):** the existing manager → coder → tester loop is already a delegation
  loop, with coding backends (Claude/native/opencode/aider) and `test_runner.py`. The Engineer
  can run a real coding backend on a sandbox repo (the Ledgerline codebase, grown over the week).
  Alternatively, the Engineer roleplays its work while the replay path shows real tool activity.
- **Scheduler and clock:**
  - maps wall clock (America/New_York) to week, day and segment
  - live work during s1–s3
  - ambient/replays during s0
  - the JIT picker from the 3-layer design (N7, not built)
- **Memory:** live agents need the v4 memory DB (WS-F) for daily summaries and fragments.
- **Build order**, following the agent_dnd vertical slice:
  - W0: model benchmark, with qwen3.8:27b on the gx10 as the candidate
  - W1: CEO + Tech Lead only, one directive → plan → status report, over Kafka
  - W2: roundtable renders it
  - W3: all 8
  - W4: knowledge scoping
  - W5: record → episode

### WS-F: Memory fragmentation (the v4 build, re-piloted on this cast)

- Continue the v4 WP chain (WP-02 onward) with the office cast as the pilot instead of the
  Harry Potter trio.
- The book source stages (split, clean, speaker-tag, rank, timeline, backstory) are replaced by
  the WS-A hand-authored profiles, loaded directly into `characters`, `character_backstories` and
  `character_baselines`. That removes most of WP-10..16.
- Keep:
  - DB deploy, config/clock, stores
  - Kafka ingest to `experience_events`
  - daily summaries at 00:00
  - Sunday reset + `select_fragments`
  - recall (WP-23)
  - brief, testctl
- All 8 characters have `retains_fragments: true`, so the recall calibration now covers 8
  characters, not 1.
- The Party Member never speaks, so his fragments come from `visibility: present` events only
  (what he watched).

### WS-G: Stream wiring

- `config/workers/roundtable.yaml`: roster names, `speaker_names`, and voice per role.
  Voice proposal:
  - CEO: baritone_mid
  - Tech Lead: bass_low
  - Analyst: alto_warm
  - Engineer: tenor_high
  - Tester: tenor_low
  - Marketing: baritone_soft
  - Office Manager: alto_bright
  - Party Member: narrator_plain
  - Stage narration: narrator_warm
- Migrate the legacy speaker keys to tuber_N (WP-7 in the roundtable design).
- Party Member tile: idle-watch pose plus gaze following the current speaker (`gaze.py`).

## 6. Order

| # | Work | Depends on |
|---|---|---|
| 1 | WS-A lore + backstories, then WS-A2 avatars | Q-A/Q-E answers |
| 2 | WS-B corpus utility | Q-C |
| 3 | WS-C pack skeleton + primitives + seats | WS-A |
| 4 | WS-F memory DB, WP-02.. | Can start in parallel with 2–3 |
| 5 | WS-E W0/W1 live slice (CEO + Tech Lead) | WS-A, benchmark |
| 6 | WS-C arc generation via CorpusAdapter | WS-B, WS-C skeleton |
| 7 | WS-D replay path | WS-B |
| 8 | WS-E W2–W5, then WS-G wiring, then a full week run | |

## 7. Known gaps

- `map_appearance` (backstory → avatar) is not built; the seam is designed (v4 §9).
- The JIT picker / scheduler is not built.
- Ring code covers `plot_0` only: no multi-layer plots, tethers or seam rules (`src/ring.py:9-15`).
- Wave 4 campaign ↔ `agent.py` integration is not done.
- The v4 memory DB is not deployed; only WP-00 and WP-01 are done.
- tuber_7 has no container.

## 8. Open questions (v0.2)

- Q-A: pack directory name, given that the name collides with `campaigns/ashiorid`
    Call this ashiorid_office
- Q-B: are Sundays work days?
  Yes the characters dont get a day off
- Q-C: corpus environments, tools and storage
  Lets start by gathering hermes and claude conversations from this machine

- Q-D: live Engineer, real code or roleplay?
  They wil all be live agents who will write real code and push it to github
- Q-E: product choice; the office's relation to the fantasy Ashiorid
  Lets consdier the ashiorid_office is just a part of the larger world for now

## 9. Decisions log

- 2026-09-27:
  - Roster uses Analyst + Engineer; Operations becomes Office Manager (no deploy duties).
  - Corpus gets its own multi-machine gathering utility.
  - The loop is weekly, as in the existing plan.
  - A full campaign arc, same as the fantasy one, with an office background.
  - The Party Member only silently observes (for now).
  - All main characters retain fragments.
  - Backstories come first, then avatars are derived from them.
  - The company is named Ashiorid; product in financial software.
  - Build the live-agent infrastructure and test it with this campaign.
- 2026-09-27, round 2 (user comments in §8, now v0.3):
  - Pack is `campaigns/ashiorid_office`. The office is part of the larger Ashiorid world, with no
    simulation-layer twist for now.
  - Product: **Fraud-Stop**, an enterprise SaaS that banks route transactions through for fraud
    verdicts.
  - 7 of 7 work days; the characters get no day off.
  - Corpus v1 gathers Hermes and Claude conversations from this machine only.
  - All characters are live agents that write real code and push to GitHub.
  - The technical build plan is `.claude/prompts/ashiorid_office_build_plan.md`.
