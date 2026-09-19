"""Serialize a source note (or an LLM-authored scene dict) into a scene YAML file.

The plan (generator_retarget_scenes_plan.md) calls for Layer 3 to emit scene
YAML files under `scenes/`, not dialogue takes. This module is that
serializer. It is pure — no network, no LLM calls, no filesystem writes to
`campaigns/` — and writes to whichever directory the caller provides (a
staging dir like `proposed_scenes/`, a fixture dir in a test, or a temp
dir).

Scene shape, from `campaign_content_expansion.md` and `app/campaign/pack.py`:

  id: <slug>
  title: <human-readable>
  ambient: false              # true for ambient-only scenes
  prompt: >-                  # ambient scenes carry this, spine scenes may not
    ...
  enter_narration: >-         # spoken on entry (not spoken for ambient;
    ...                        # ambient scenes carry their content in `prompt`)
  beats:                      # spine scenes
    - type: narration
      speaker: gm
      text: ...
  lore: [stem, ...]           # closed set — must be in pack.lore
  source:                     # provenance (§9.1.2)
    run_id: ...
    batch: ...
    model: ...
    base_hash: ...
    version: <scene-id>@<N>
    authored: generated

`pack_gate` (a thin wrapper over `app/campaign/pack.py` load_pack + the
validator) rejects anything that does not conform to the pack format. The
writer here is responsible for emitting well-formed YAML that passes both
checks.
"""
import hashlib
import re
import time
from typing import Any

import yaml

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SceneWriterError(ValueError):
    """Raised for malformed scene dicts or filesystem issues."""


def slugify(text: str) -> str:
    """Lowercase, hyphenate, strip non-alphanumerics. Stable across runs."""
    out = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    if not SLUG_RE.match(out):
        raise SceneWriterError(f"could not derive a valid scene slug from {text!r}")
    return out or "scene"


def next_scene_filename(scenes_dir, prefix: str = "a") -> str:
    """Return `prefix<NN>-` + slug placeholder, where NN is the next unused
    number in `scenes_dir` for that prefix. Caller supplies the final slug;
    this function hands back just the numeric prefix, e.g. 'a' -> '038' for
    the next free a-prefixed slot."""
    import pathlib
    p = pathlib.Path(scenes_dir)
    if not p.is_dir():
        return f"{prefix}001"
    used = set()
    for f in p.iterdir():
        name = f.name
        if len(name) < 4 or not name.startswith(prefix):
            continue
        m = re.match(re.escape(prefix) + r"(\d+)-", name)
        if m:
            used.add(int(m.group(1)))
    nn = 1
    while nn in used:
        nn += 1
    return f"{prefix}{nn:03d}"


def scene_dict_to_yaml(scene: dict) -> str:
    """Serialize a scene dict to a string of YAML, sorting keys for stable
    diffs. The `id` field is required — this is the canonical scene stem."""
    if not isinstance(scene, dict) or "id" not in scene:
        raise SceneWriterError("scene dict must be a mapping with 'id'")
    if not SLUG_RE.match(scene["id"]):
        raise SceneWriterError(f"scene id must match {SLUG_RE.pattern}, got {scene['id']!r}")
    # Keep the canonical key order on output so hand-edits survive a
    # re-serialize: id, title, ambient, then the rest alphabetically.
    def _order(k):
        for i, fixed in enumerate(("id", "title", "ambient", "prompt",
                                   "enter_narration", "beats", "lore",
                                   "ring_tone", "mood", "source",
                                   "default_next", "branches")):
            if k == fixed:
                return i
        return len(fixed) + hash(k)  # stable in a given process; sort fallback
    def _dump_keys(d):
        if isinstance(d, dict):
            return {k: _dump_keys(v) for k, v in sorted(d.items(), key=lambda kv: _order(kv[0]))}
        if isinstance(d, list):
            return [_dump_keys(v) for v in d]
        return d
    return yaml.safe_dump(_dump_keys(scene), sort_keys=False, allow_unicode=True,
                          default_flow_style=False, width=80)


def provenance_block(*, run_id: str, batch: str, model: str,
                     base_hash: str, version: str,
                     authored: str = "generated") -> dict:
    """Return the `source:` block per §9.1.2. Pure — caller stamps it onto
    the scene dict before write. `authored` is one of {generated, human,
    human-edited} — see §6F.2 for the regeneration rules."""
    if authored not in {"generated", "human", "human-edited"}:
        raise SceneWriterError("authored must be one of 'generated', "
                               "'human', 'human-edited', got " + repr(authored))
    _fields = [("run_id", run_id), ("batch", batch), ("model", model),
               ("base_hash", base_hash), ("version", version)]
    missing = [name for name, value in _fields if not value]
    if missing:
        raise SceneWriterError(
            "source block requires run_id, batch, model, base_hash, version; "
            "missing: " + ", ".join(missing))
    return {
        "run_id": run_id,
        "batch": batch,
        "model": model,
        "base_hash": base_hash,
        "version": version,
        "authored": authored,
    }


def hash_text(text: str) -> str:
    """Stable sha256 hex digest of a source note's text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fresh_run_id(tag: str = "build") -> str:
    """`<tag>_YYYYMMDD_HHMMSS` — a run id shared by every artifact a single
    build produces (§9.1.2)."""
    return f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}"


def write_scene(scenes_dir, filename: str, scene: dict) -> str:
    """Write one scene YAML to `scenes_dir / filename`. Returns the full path.
    This is a STAGING write — caller decides when (or whether) to promote to
    `campaigns/`. Raises SceneWriterError on a malformed scene."""
    import pathlib
    target = pathlib.Path(scenes_dir) / filename
    yaml_text = scene_dict_to_yaml(scene)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml_text, encoding="utf-8")
    except OSError as exc:
        raise SceneWriterError(f"could not write scene to {target}: {exc}") from exc
    return str(target)


def roundtrip_check(scene: dict) -> dict:
    """Parse the YAML we just produced and return the resulting dict. Useful
    in tests: a scene that serializes but does not re-parse is broken
    even if the write succeeded."""
    text = scene_dict_to_yaml(scene)
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, dict) or "id" not in parsed:
        raise SceneWriterError(f"roundtrip did not yield a scene mapping: {type(parsed).__name__}")
    return parsed
