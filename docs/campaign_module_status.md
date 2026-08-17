# Campaign Module — Status and Handoff

> **Read this first if you are picking up the campaign work.**
> Written 2026-08-16, at the end of Wave 3 + content. Nothing here is committed
> yet — the whole module is uncommitted on `main`.

## What this module is

`docs/weeklyLoopBrainstorm.md` describes turning virtualTubers from a fixed
"AI dev team" show into a **genre-hopping, weekly-resetting simulation loop** —
campaigns as interchangeable skins over one worker framework, branching scenes,
an emergent "sage" carrying memory across loops, viewer-driven influence.

The brainstorm opens by assuming a generic campaign layer already exists. **It
did not.** `app/agent.py` was 1,154 lines of hardcoded dev-team fiction
(`role: manager|coder|tester`, handlers for `task_assignment`/`bug_report`/
`test_passed`) with zero hits for campaign, scenario, or action-primitive
anywhere in `app/`, `config/`, or `tests/`.

This module is **the missing foundation only**. Weekly-loop state machine, chat
voting, viewer-power meters and roster scaling are explicitly deferred — but
every one of them has a named seam waiting (see *Seams* below).

## Execution model — how this was built

Claude (Opus) is the **orchestrator and architect**: it writes the spec and the
pytest file first, dispatches, then reviews, fixes and verifies.
**`qwen3-coder:30b` on local Ollama writes the implementations.** Claude
subagents are not used.

The specs and tests are not documentation — they are the **executable
acceptance criteria that make a 30B local model usable**. Keep that discipline
if you continue this way.

### The harness — `tools/qwen_worker/`

| File | Does |
|---|---|
| `runner.py` | CLI: `preflight` \| `run` \| `promote` |
| `ollama_client.py` | `/api/chat` against `http://localhost:11434` |
| `prompting.py` | Assembles goal + interface contract + context files + the pytest file |
| `sandbox.py` | Stages output, runs pytest against the staged tree |
| `specs/*.yaml` | One per task — seven exist, all for modules already built |

```bash
# check Ollama and the model are reachable
.venv/bin/python tools/qwen_worker/runner.py preflight

# generate + verify, retrying with failures fed back in
.venv/bin/python tools/qwen_worker/runner.py run tools/qwen_worker/specs/<task>.yaml --attempts 3

# after YOU have reviewed the staged files, copy them into the tree
.venv/bin/python tools/qwen_worker/runner.py promote tools/qwen_worker/specs/<task>.yaml
```

**The subcommand is positional — there is no `--spec` flag.** Output stages to
`.qwen_staging/<task_id>/` (gitignored) and lands in the working tree only on
`promote`, after human review.

**Debugging a staged failure:** copy
`.qwen_staging/<task>/app/campaign/<file>.py` into the tree and run pytest
directly. The test files `sys.path.insert` the repo's `app/`, so pointing
`PYTHONPATH` at a sandbox copy does **not** work.

### The ratchet — the rule that kept this bounded

When reviewing generated code, classify the finding and act accordingly:

| Finding | Action |
|---|---|
| **Behavioural gap** | Encode it as a **new test + a spec note**, then regenerate. Never hand-patch behaviour. |
| **Hygiene** (unused imports, dead branches, whitespace) | Hand-patch. |
| **Prose / content** | Hand-write. qwen does not write the show. |

This is what stops the same defect reappearing on the next module.

## Current state

### Code — `app/campaign/` (1,431 lines, all promoted and green)

| File | Lines | Responsibility |
|---|---|---|
| `pack.py` | 241 | Load a pack off disk into typed dataclasses. `PackError` for every structural problem. |
| `primitives.py` | 264 | Registry of cosmetic action verbs. **Nothing here executes anything real.** |
| `validator.py` | 192 | Semantic checks. Collects **every** problem into a report; never raises. |
| `scene_graph.py` | 184 | Graph traversal + the `BranchSelector` seam. |
| `renderer.py` | 190 | `SceneRenderer` over `Pacer`/`Palette`/avatar state/tmux/TTS. |
| `runtime.py` | 217 | `CampaignRuntime` — position, advancement, persistence. |
| `cli.py` | 142 | The operator surface and the main verification tool. |

### Tests — 308, all passing

| File | Tests |
|---|---|
| `test_campaign_pack.py` | 36 |
| `test_campaign_primitives.py` | 73 |
| `test_campaign_validator.py` | 40 |
| `test_campaign_scene_graph.py` | 34 |
| `test_campaign_renderer.py` | 38 |
| `test_campaign_runtime.py` | 57 |
| `test_campaign_cli.py` | 30 |

**Full suite: `924 passed`, exit 0** (was 837 before Wave 3). No pre-existing
test was touched.

### Content — `campaigns/ashiorid/`

The opening arc: 10 scenes, 5 cast, 2 forks, 3 lore notes. Curated from the
137-note Obsidian vault at `~/codeProjects/ashioridCampaign/DnD Campaign/`.
Validates with **zero errors and zero warnings**. Full detail in
[campaigns/ashiorid.md](campaigns/ashiorid.md).

### Docs

- [campaign_pack_format.md](campaign_pack_format.md) — the authoring guide.
- [campaigns/ashiorid.md](campaigns/ashiorid.md) — the arc.
- **Per-module API docs do not exist yet** — they are Wave 4 work, and CLAUDE.md
  requires one file per module.

## Architecture — the decisions worth not re-litigating

**New runtime, reuse the parts.** The campaign engine is fresh code that borrows
`Pacer`, `Palette`, avatar state and pane control as libraries. It deliberately
does **not** extend the replay path — replay is stable and carries the recorded
dev-session shows.

**Scene vocabulary is new, not replay's event schema.** Replay's
`user_message`/`assistant_text`/`tool_call` model is bound to recorded sessions
and cannot express dialogue, dice, or branches. Campaign beats are
`narration` / `dialogue` / `action` / `pane`.

**Primitives are purely cosmetic.** `roll_check` renders "Helen rolls arcana
against DC 18" — it does not roll anything. Real mechanics, if ever wanted, go
behind the registry, not inside it.

**Genre is config, not code.** Every verb (fantasy *and* cyberpunk) is
registered in one registry; each pack whitelists its own in `campaign.yaml`.
A second campaign should require zero Python.

**Cycles are a feature.** The show is a weekly time loop. Traversal is bounded
by a step budget (`--max-scenes`), never by cycle detection. Do not "fix" this.

**Injected-and-optional side-effect channels.** The renderer takes tts, avatar
state path, audio, pane control and improviser as optional injections. Supplying
none *is* `--dry-run`. That is why the CLI builds
`SceneRenderer(pack, out=out, pacer=pacer, palette=palette)` and nothing more.

### Seams left for the deferred work

| Deferred feature | Where it plugs in |
|---|---|
| Chat voting | `BranchSelector.select()` — add `ChatVoteSelector` beside `ScriptedSelector`. No runtime change. |
| Viewer-power thresholds | Same seam. |
| Weekly loop / reset cadence | `CampaignRuntime.reset(keep_carry=True)`. |
| Sage memory across loops | `CampaignState.carry` — a dict that **survives `reset()`**. |
| Second campaign (cyberpunk) | `campaigns/<name>/` + a `primitives` whitelist. `execute_exploit` and `scan_target` are already registered. |

## Hard constraints — do not break these

- Existing `role: manager|coder|tester` keeps working.
- All 15 entries in `MESSAGE_HANDLERS` ([app/agent.py:1074](../app/agent.py#L1074)) keep working.
- All 34 pre-existing test files keep passing untouched.
- **Campaign mode is additive**, activated by a new `agent.campaign` config section.
- Never install into system Python — a `PreToolUse` hook blocks bare
  `python`/`pip` when `.venv` exists. Always `.venv/bin/python`.
- The worker image is **never** built by `docker compose up` (`pull_policy: never`)
  — build it on the host after any code change.
- `.env` is gitignored. Never commit real credentials.

## Running it

```bash
# validate a pack
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid --validate

# watch the show at performance speed
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid

# read it instantly, plain text
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --dry-run --no-pace --no-color

# walk an alternate route
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --scene party-attack --force-branch failure --dry-run
```

**`PYTHONPATH=app` is required** — modules under `app/` import each other by
package name and nothing there does `sys.path` surgery. Bare
`python app/campaign/cli.py` fails with `ModuleNotFoundError: No module named
'campaign'`.

There is **no audio, no tmux, no Kafka** in the CLI path. `pane` beats are inert.
One terminal, text only, until Wave 4.

## What remains — Wave 4

1. **`app/agent.py` integration** — new message types `campaign_start`,
   `scene_cue`, `beat_ack`, `branch_decision`, `campaign_end`, plus campaign-mode
   gating in `main()`. Model this on the **director/follower cue-ratchet** in
   [app/replay_pane.py:461](../app/replay_pane.py#L461) — it already solves
   multi-worker scene sync, which is exactly the GM→players problem.
2. **Configs** — `config/campaigns/ashiorid.yaml`, five worker configs, new
   `map`/`inventory`/`party` panels, a `campaign` layout preset.
3. **`docker-compose.yml`** — GM + 4 players.
4. **Docs** — one file per module per CLAUDE.md, plus README and CHANGELOG.

Expect to hand-fix the `agent.py` integration; it is the module most likely to
exceed qwen's ceiling. The test-first spec is what keeps that bounded.

### Also outstanding

- **Wire a lint gate into the harness.** Unused imports, `typing` imports,
  dead `except Exception: raise` blocks, trailing whitespace and missing
  `encoding="utf-8"` appeared in **all seven** generated modules. No linter is
  currently installed (`ruff`, `flake8`, `pylint` all absent). Adding one to the
  sandbox's verification step would remove an entire class of manual review.
- **Nothing is committed.** Still on `main`; branch before committing.

## Known qwen failure modes — check for these on review

- Unused imports and `typing` imports (spec says builtin generics only).
- `except Exception` written **alongside** a narrow catch, e.g.
  `except (CampaignRuntimeError, Exception)` — the second member silently makes
  the first meaningless and launders genuine bugs into "operator error, exit 1".
- Dead `except Exception: raise` blocks.
- Missing `encoding="utf-8"` on file opens.
- Omitting logging the spec explicitly asked for.
- Reading mutated state *after* the call that mutated it (this produced a
  `run()` returning `['defeat', None]`).

**Three of the five dispatch failures were defects in my own spec, not model
incapacity** — ambiguity about "missing key" vs. "value is None", and an
unstated ordering constraint. When a dispatch fails, suspect the spec first.

## Changelog

- **v1.0.0** (2026-08-16) — Waves 1–3 complete (7 modules, 308 tests), Ashiorid
  opening arc curated and verified, full suite 924 passing. Wave 4 outstanding.
