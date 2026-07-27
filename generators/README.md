# generators/ — campaign episode generators (outside the platform, on purpose)

## The boundary

**The platform performs a finished script. It does not generate one.**

`replays/<campaign>/*.json` is the only handoff between "how an episode gets
made" and "how it airs on stream." Everything under `generators/` produces
episode JSON; everything under `app/` and `services/` consumes it. Neither
side needs to know how the other is implemented.

```
┌─ generators/ (this directory) ─────┐          ┌─ the platform (app/, services/) ──┐
│  generators/coder/   session logs  │          │  app/episode_schema.py  (ingest)   │
│  generators/dnd/...   outline, etc │  ──────▶ │  app/primitives.py     (render)    │
│  emit: episode JSON                │  writes  │  app/replay.py         (perform)   │
└─────────────────────────────────────┘  replays/<campaign>/*.json          └──────┘
```

### Why the split exists

- **Generators need not share a language, a machine, or a deploy cycle with
  the platform.** `generators/coder/` happens to be Python because it reuses
  `session_log_parser.py`, but a D&D generator could be a two-pass LLM
  pipeline in a notebook, run on a totally different box, as long as it
  writes valid episode JSON at the end.
- **The episode schema is the only contract.** Everything a generator knows
  about "what the coder campaign looks like" — primitive names, payload
  shapes, narration kinds — comes from the same `config/` files the platform
  itself reads (`config/campaigns/<campaign>/primitives.yaml`,
  `narration.yaml`). There is no separate generator-side copy of that
  knowledge to drift out of sync.
- **`generators/` never ships in the worker image.** See `.dockerignore`'s
  "Generators" section — this directory is excluded from every
  `docker build` context in this repo, the same way `replays/` and `voices/`
  are excluded as runtime-only content. A generator can be as heavy
  (extra dependencies, large intermediate files) as it needs to be without
  touching image size or build time.

### The one deliberate exception: importing `app/`

A generator's whole job is producing an episode that will pass the
platform's own ingest checks — so it is allowed, and expected, to import
`app/episode_schema.py` (and, transitively, `app/primitives.py`) and
validate its own output **before writing anything**, rather than
re-implementing the validator's rules a second time in a different
language or copy-pasting them. This is the design doc's "same config and
same engine on both sides" (`docs/campaign_platform_build.md`'s Validator
section): fail the build here, using the exact engine that will otherwise
fail the show at ingest.

Concretely, every generator that imports `app/` does it the same way:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))
from episode_schema import load_rules, normalize_episode, validate_episode
from primitives import load_primitives
```

This is the **only** sanctioned reach across the generator/platform
boundary. A generator must never import `app/replay.py`, `app/agent.py`,
`app/avatar.py`, or anything else — those are runtime modules with their own
config/dependency footprint (TTS, LLM clients, Kafka, Redis) that a generator
has no business dragging in. If a generator needs to know how long a scene
will take on screen, it calls `primitives.estimate_scene_seconds` directly
(the same function the validator's own `max_scene_seconds` rule uses) rather
than trying to guess.

### Adding a new campaign's generator

1. Create `generators/<campaign>/` with whatever internal structure that
   campaign's generation process needs (a parser, an LLM pass, a hand-authored
   template — anything).
2. Its output is a dict matching the episode schema
   (`meta`/`cast`/`scenes[]` — see `docs/episode_schema.md` and
   `replays/coder/sample.json` for a worked example): `cast` is a top-level
   sibling of `meta`, never nested inside it; every scene carries
   `speaker`/`kind`/`text`/`fallback`/`mode`/`render[]`; every string is
   inlined (no `detail_file`-style sidecar references — the validator's
   `external_refs: reject` rule exists specifically to catch this).
3. Before writing an episode file, validate it:
   ```python
   primitives_table = load_primitives("<campaign>")
   rules = load_rules()
   episode = normalize_episode(raw_episode)
   issues = validate_episode(episode, primitives=primitives_table, rules=rules,
                             kinds=set(narration_config.keys()))
   if any(issue.level == "reject" for issue in issues):
       ...  # fail the build; do not write the episode
   ```
4. Write to `replays/<campaign>/<episode-id>.json`. Binary assets (if any)
   go in `replays/<campaign>/assets/`, referenced by bare basename only —
   the validator's `assets.basename_only`/`must_exist` rules enforce
   containment at ingest.
5. Add a `generators/<campaign>/README.md` explaining that generator's own
   CLI/inputs, following `generators/coder/README.md`'s shape.

### What this directory is not

- Not a plugin registry the platform loads at runtime — the platform has
  zero code that knows `generators/` exists. Campaign isolation between
  `replays/coder/` and `replays/dnd/` is a filesystem/config concern
  (`config/campaigns/<campaign>/`), not a generator-registry concern.
- Not where redaction policy lives conceptually — `config/validation.yaml`'s
  `redaction.patterns` is the platform's own last line of defense and is
  owned by `app/episode_schema.py`. A generator's own redaction (e.g.
  `generators/coder/session_log_parser.py`'s `REDACTION_RULES` +
  `LEAK_AUDIT`) is a *second*, independent, earlier line of defense specific
  to that generator's raw input — belt and braces, not a shared mechanism.

## See also

- `docs/campaign_platform_build.md` — the full design doc this split
  implements.
- `docs/episode_schema.md` / `docs/primitives.md` — the two platform modules
  every generator's output is checked against.
- `generators/coder/README.md` — the coder campaign's own generator, usage,
  and CLI flags.
