# Character Generator + Updater — Plan Review

Reviewed: 2026-09-23. Subject: `.claude/prompts/character_generator_updater.md`
(design plan, not yet implemented). Every claim below was verified against the
repo at commit `1497bd9` (branch `main`).

## Verdict

The plan is strong. The "existing assets" table is accurate — every file,
table, and convention it cites exists. The stage-registry design genuinely
satisfies the "easy to add generation features" requirement, and the
deterministic-apply / LLM-judgment split is the right shape.

What follows is what should be fixed before building, ordered by the cost of
finding it late.

---

## Blockers — wrong or missing against the codebase

### 1. The experience source doesn't exist yet

Phase 1 says "from the `messages` table gather every narration/dialogue beat
the character participated in," and the config block lists
`messages_types: [narration, dialogue, campaign_event, chat]`.

**None of those are real bus types.** The actual types found in `app/` and
`services/` are: `user_message`, `assistant_text`, `replay_narration`, `cue`,
`end`, `agent_thinking`, `viewer_joined`, `coding_run_report`,
`replay_request`.

Worse: **nothing in `app/campaign/` publishes to the bus at all.**
`CampaignRuntime` persists to a JSON file (`app/campaign/runtime.py:166`
`save()`), and `services/campaign-manager/main.py` is a FastAPI dashboard, not
a producer.

Also, `messages` has no character column — `"from"` / `"to"` are agent/service
IDs (`docs/database_schema.md`), so attribution needs a worker-id → character
map.

Today the updater would read an empty stream.

**Fix:** either add "publish campaign beats to the bus" as step 0 of the build
order, or source Phase 1 from `CampaignState.history` instead — and say which
in the plan.

### 2. Week numbering has no source of truth

Every table keys on `week INT`, but the only counter in the system is
`CampaignState.loop` (`app/campaign/runtime.py:25`): per-campaign, stored in a
JSON state file, not Postgres, and incremented only by `reset()`
(`runtime.py:157`) — which is called from **nothing but
`tests/test_campaign_runtime.py`**.

"The campaign already has the weekly loop concept" is true as a *seam* only;
there is no running weekly loop.

**Fix:** decide where `week` comes from — `state.loop`, a new
`characters.current_week` column, or operator-supplied on the CLI. If
operator-supplied, the `--week` argument is load-bearing and needs a guard
against re-running the same week twice: **the reset is not idempotent as
specified.**

### 3. The avatar handoff is wrong for the tile path

The plan claims `avatar.termgl_avatar.character_params` "accepts exactly this
dict shape." Two problems:

- `termgl_avatar` has been superseded by `codec_avatar` — see the comment at
  `config/workers/coder.yaml:74`.
- For roundtable tiles, `app/tile_avatar.py:226-237`
  (`resolve_slot_character_params`) returns a **preset name string only**. An
  inline slider mapping is explicitly rejected and the slot falls back to the
  ASCII face.

A generated character with a JSONB slider dict will silently not render on a
tile.

**Fix:** either the `avatar` stage must also register a named preset in
`character_schema.PRESETS` (or a DB-backed preset lookup), or
`resolve_slot_character_params` needs extending. Pick one and name it.

### 4. `DOUBLE` is not a Postgres type

`knowledge_nodes.salience` and `knowledge_edges.strength` are specified as
`DOUBLE`. They need `DOUBLE PRECISION` — that is what
`docs/sql/02_create_tables.sql:39` uses. CREATE TABLE fails as written.

---

## Design questions that outrank D1–D4

### 5. The reset eats the generated backstory

Reset step 1 breaks *every* active edge except core↔core pairs and the top-N;
step 3 deactivates every non-core node except the top-N. But step 2's ranker
only selects from "the week's new/experience-derived knowledge."

So after the first reset, the entire source-derived graph — the 40 nodes the
`knowledge` stage worked to build — goes dormant, leaving `core` plus ≤8 week-1
nodes. Week 2's Harry doesn't know what Hogwarts is.

**Options:** exempt `origin='generated'` non-core nodes from the sweep; give
them a high floor salience and let the ranker choose from the whole graph
rather than only the week's additions; or decay `strength` instead of flipping
`active`.

Decide before `updater.py` is written — it changes the reset SQL and the tests.

### 6. Deactivating nodes contradicts the source concept

The user's words in `docs/character_agent_generator.md`: "the knowledge is
still there but forgotten… like in obsidian there are {{name}} tags… the
character will not remember the **connections** between them."

In Obsidian the notes persist with full content; only the links go. The plan
additionally hides node *content* behind `active=FALSE` and reduces the brief
to bare names. That is a stronger, different mechanic — possibly a better one
(it produces the "faint memory that resolves" flavour), but it is an addition,
not the user's model.

**Fix:** flag it explicitly as a divergence with a config switch
(`forget: edges_only | edges_and_nodes`) rather than burying it in step 3.

### 7. `retained_top_n: 8` is a hard global cap

Regardless of week volume or node kind. A week with one huge event and a week
with thirty small ones get the same budget.

Consider a salience threshold with a cap, or per-kind quotas (always retain new
`relationship` and `objective` nodes). Also define the **salience decay
function** for nodes that aren't re-selected — currently `salience` is
"re-ranked each reset" with no stated rule.

### 8. Phase 2 is a single unbounded LLM call

It carries the full week's experience stream + the active subgraph + all
dormant names. On a busy week that exceeds the context window, and one
malformed JSON response loses the whole reset.

**Specify:** a stream cap or summarisation pre-pass, a strict JSON schema
validator, and a bounded retry. The 3layer runner's per-stage pattern already
gives the shape.

### 9. The 5.8 MB source is one table cell

`sourceworks/Harry_Potter_all_books_preprocessed.txt` is 5.8 MB, handed off as
"chunked; map-reduce over long sources." That is the largest unspecified
engineering chunk in the generator and it carries a real token bill.

Needs its own section: chunk strategy, whether to scope to one book, whether
there is a retrieval step, and how `evidence` pointers survive the reduce.

Also — `_quarantine_harry_potter/` is *named* quarantine. Find out why that
hand analysis was quarantined before committing to Harry as the D4 pilot.

---

## Smaller fixes

10. **Two ingestion paths, unreconciled.** Cast YAML
    (`campaigns/hptest/cast/harry.yaml`) carries `name` / `archetype` / `voice`
    / `system_prompt` and is what `load_pack()` (`app/campaign/pack.py:189`)
    consumes. The plan's brief is a much larger 7-section document loaded by
    `loader.py` at worker boot. The plan claims "loadable by the campaign pack
    format with zero changes to `load_pack()`" — say which artifact becomes
    `system_prompt`, and whether the brief supplements or replaces it. (Note:
    the real cast files have no `avatar:` key even though `pack.py:45` reads
    one.)

11. **Table ownership is undefined.** `docs/database_schema.md` says each
    table's CREATE is run on startup by an owning long-lived service, with
    `episode_store`'s best-effort-at-import as the exception for tables nobody
    owns. "Created by `store.py` on first use" needs to name the owner. The
    doc's "four independent copies of the schema" rule also means this adds a
    fifth — say which files must stay in sync.

12. **`character_weeks.edges_relearned`** is "tallied at next reset" but the row
    is written at *this* reset — the count belongs to week W+1's row. Define it
    or drop it.

13. **"Auditable and reversible" needs a command.** Versioned profiles make
    revert *possible*; nothing in the listed `cli.py` surface makes it
    *doable*. Add `revert --character X --to-version N`.

14. **No index guidance.** Brief assembly is
    `WHERE character_id = ? AND active = TRUE` on both node and edge tables —
    specify those indexes alongside the DDL.

15. **Cross-character consistency is silently out of scope.**
    `UNIQUE (character_id, name)` means Harry's `ron-weasley` and Ron's
    `harry-potter` are unrelated rows, so nothing stops their shared history
    diverging. Defensible for v1 — state it as a decision.

16. **Wording.** "Config: `config/character.yaml` (new top-level section in
    `config/worker.yaml` is the template)" parses two ways. Pick a file.

---

## On the open decisions as written

- **D1** (manual reset first, with `--dry-run`) — right.
- **D2** (brief at worker boot only) — right.
- **D3** (standalone `character_jobs` / `character_artifacts`) — right, and
  correctly reasoned from the "never modify a shared utility for one project's
  needs" rule in CLAUDE.md.
- **D4** (Harry as pilot) — see #9 first.

## Suggested next step

Resolve #5, #6 and #1 with the user before touching `updater.py`; they are the
three that change the schema or the reset SQL. #4 and #12 are one-line edits to
the plan. The rest can be folded in as a revision pass.

---

## Verification notes (what was actually checked)

Confirmed present: `docs/database_schema.md`, `services/3layer-generator/`
(with `generation_jobs` / `generation_artifacts` in `generation_store.py:41,81`),
`app/character_schema.py` (`SLIDER_DEFAULTS:38`, presets incl. `chadwick`),
`app/character_preview.py` (`--json` flag at line 191),
`app/campaign/runtime.py` (`reset(keep_carry=True)` at :151, `carry` at :26),
`app/campaign/validator.py`, `app/knowledge_graph_pane.py` (136 lines, static
placeholder), `app/llm_client.py`, `sourceworks/`, `_quarantine_harry_potter/`,
`campaigns/hptest/` (cast: gm, harry, hermione, ron),
`docs/sql/02_create_tables.sql`, `docs/character_generator.md` (avatar only —
the plan's naming concern is real), `config/workers/`, `CHANGELOG.md`, `tests/`.

Confirmed absent: `app/character/` (nothing implemented yet).
