"""
episode_schema.py
Load, normalize, and validate a campaign-platform episode (the score a
Performer performs -- docs/campaign_platform_build.md, CONTRACT.md §1/§4).

Three functions, three jobs:
    load_rules(path=None)          -- config/validation.yaml -> a rules dict.
    normalize_episode(raw)         -- fills scene defaults. Never validates.
    validate_episode(episode, ...) -- pure. Returns a list[Issue], never raises
                                       for content problems.
    load_episode(path, ...)        -- read + normalize + validate. Raises
                                       EpisodeError if any Issue rejects.

This module is the other half of "same config, same engine on both sides"
(docs/campaign_platform_build.md's Validator section): the platform imports
it at ingest (refuse to load, log loudly, do not air), and the coder
generator (generators/coder/build_library.py, built separately -- see
CONTRACT.md §9) imports it before writing an episode at all (fail the
build, do not write the episode).

Campaign isolation falls out of `unknown_primitive` for free: it checks a
render entry's `primitive` against the MERGED primitive table for the
campaign actually being loaded (app/primitives.py::load_primitives), so a
D&D episode's `open_inventory` fails validation the moment someone tries to
air it under the coder campaign, with no separate mechanism required.

Redaction here mirrors scripts/build_replay_library.py's LEAK_AUDIT
belt-and-braces approach: rather than trusting a fixed list of "fields that
might contain user text", the whole serialized scene (meta/cast get their
own single serialized pass too) is re-scanned as JSON text against every
configured pattern. Private LAN IPs are deliberately exempted via each
pattern's own `except` clause -- see config/validation.yaml's comment on
the 2026-07-12 password-leak incident review. `validate_episode` itself
never rewrites anything (it stays pure, Issues only); when
config/validation.yaml's redaction.on_match is "redact", the actual
substitution is applied by `load_episode` (the one function in this module
allowed to hand back edited content, since it already owns building the
returned normalized dict) -- see `_apply_redaction`.
"""
import copy
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from primitives import PrimitiveError, estimate_scene_seconds, resolve_recipe

log = logging.getLogger("episode_schema")

# Fields a normalized scene always has, with their defaults. `speaker` and
# `kind` are NOT here on purpose -- they are required (structure.
# required_scene_fields) and normalize_episode must never manufacture a
# fake value for a field validate_episode is responsible for rejecting.
_SCENE_DEFAULTS = {
    "text": "",
    "fallback": "",
    "mode": "sequence",
    "render": [],
}

# "bare relative path to a .md/.txt/.json sidecar" (CONTRACT.md §4's
# external_refs rule) -- a payload string that is nothing BUT a filename or
# relative path ending in one of the three sidecar extensions the old
# detail_file mechanism used. Deliberately narrow (extension-anchored) so it
# never flags ordinary prose that happens to mention a filename mid-sentence.
_EXTERNAL_REF_RE = re.compile(r"^(?:[\w.\-]+/)*[\w.\-]+\.(?:md|txt|json)$", re.IGNORECASE)

# "no path separators, no traversal" (CONTRACT.md §4's assets.basename_only)
# -- implemented as an explicit, platform-independent string check rather
# than leaning on pathlib.Path(...).name (which only treats backslash as a
# separator on Windows). Validation must reject the same hostile inputs
# regardless of which OS runs the validator.
_PATH_SEP_RE = re.compile(r"[\\/]")


class EpisodeError(Exception):
    """Raised by load_episode when validate_episode returns any Issue with
    level 'reject', or when the episode file/validation-rules file itself
    can't be read or parsed."""


@dataclass
class Issue:
    level: str          # "reject" | "warn"
    rule: str            # e.g. "unknown_primitive"; skip_scene severities
                         # append ":skip_scene" so load_episode can find
                         # and drop the offending scene (see load_episode).
    message: str
    scene_index: int | None = None


# ── config loading ────────────────────────────────────────────────────────

def _default_rules_path():
    in_container = Path("/config/validation.yaml")
    if in_container.is_file():
        return in_container
    return Path("config") / "validation.yaml"


def load_rules(path=None):
    """Load config/validation.yaml into a plain dict.

    Missing or malformed file raises EpisodeError -- there is no safe
    default for validation rules (especially the redaction patterns), the
    same posture app/primitives.py::load_primitives takes toward its own
    required shared base config.
    """
    path = Path(path) if path is not None else _default_rules_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as exc:
        log.error("failed to read validation rules at %s: %s", path, exc)
        raise EpisodeError(f"failed to read validation rules at {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        log.error("failed to parse validation rules at %s: %s", path, exc)
        raise EpisodeError(f"failed to parse validation rules at {path}: {exc}") from exc
    return data or {}


# ── normalization ─────────────────────────────────────────────────────────

def normalize_episode(raw):
    """Fill in scene defaults (text/fallback/mode/render). Never validates.

    Deep-copies `raw` so the caller's original dict is never mutated by
    this module or by anything downstream of it (load_episode's redact-mode
    rewrite in particular) -- callers can safely reuse or re-serialize the
    dict they passed in.
    """
    episode = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    episode.setdefault("meta", {})
    episode.setdefault("cast", [])

    raw_scenes = episode.get("scenes")
    scenes = raw_scenes if isinstance(raw_scenes, list) else []

    normalized_scenes = []
    for scene in scenes:
        if not isinstance(scene, dict):
            normalized_scenes.append(scene)  # left as-is; validate_episode rejects it
            continue
        for key, default in _SCENE_DEFAULTS.items():
            # Copy mutable defaults (render's []) -- scene.setdefault would
            # otherwise hand every scene missing this key the SAME list
            # object, so one scene's later mutation would leak into every
            # other scene sharing the default.
            scene.setdefault(key, copy.deepcopy(default))
        normalized_scenes.append(scene)
    episode["scenes"] = normalized_scenes
    return episode


# ── small local helpers ──────────────────────────────────────────────────

def _resolve_field(entry, path):
    """Safe dotted-path getter into a render entry, mirroring
    primitives._resolve_field's contract (None on any missing segment) --
    duplicated locally rather than imported since it is not part of
    primitives.py's frozen public API."""
    if not path:
        return None
    value = entry
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return value


def _fails_basename_only(ref):
    s = str(ref)
    if not s or s in (".", ".."):
        return True
    return bool(_PATH_SEP_RE.search(s))


def _load_manifest(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        log.debug("asset manifest %s not readable; skipping manifest check", path, exc_info=True)
        return None
    return {line.strip() for line in lines if line.strip() and not line.strip().startswith("#")}


def _severity_issue(setting, *, scene_index, rule_name, message, issues):
    """Apply a configurable reject|warn|skip_scene setting: appends the
    right Issue. skip_scene is encoded as level="warn" (it never blocks
    load_episode) with ":skip_scene" appended to `rule`, so load_episode's
    post-validation pass can find and drop the offending scene -- Issue's
    frozen shape (CONTRACT.md §4) has no separate field for this, and
    `rule` is the only place left to carry it without inventing a third
    Issue.level value the contract doesn't define.
    """
    if setting == "reject":
        issues.append(Issue(level="reject", rule=rule_name, message=message, scene_index=scene_index))
    elif setting == "skip_scene":
        issues.append(Issue(level="warn", rule=f"{rule_name}:skip_scene", message=message, scene_index=scene_index))
    else:  # "warn", or an unrecognized value -- degrade to warn rather than raise
        issues.append(Issue(level="warn", rule=rule_name, message=message, scene_index=scene_index))


def _check_asset_ref(ref, assets_cfg, assets_dir, scene_index):
    issues = []
    basename_only = assets_cfg.get("basename_only", True)
    if basename_only and _fails_basename_only(ref):
        issues.append(Issue(
            level="reject", rule="assets.basename_only",
            message=f"scene {scene_index}: asset ref {ref!r} is not a bare basename "
                     "(path separators / traversal are never allowed)",
            scene_index=scene_index,
        ))
        return issues  # a hostile ref can't be meaningfully existence-checked

    if assets_dir is None:
        log.debug("no assets_dir given; skipping must_exist/manifest checks for %r", ref)
        return issues

    root = assets_cfg.get("root", "assets")
    asset_path = Path(assets_dir) / root / str(ref)

    if assets_cfg.get("must_exist", True) and not asset_path.is_file():
        issues.append(Issue(
            level="reject", rule="assets.must_exist",
            message=f"scene {scene_index}: asset {ref!r} not found at {asset_path}",
            scene_index=scene_index,
        ))

    manifest_rel = assets_cfg.get("manifest")
    if manifest_rel:
        allowed = _load_manifest(Path(assets_dir) / manifest_rel)
        if allowed is not None and str(ref) not in allowed:
            issues.append(Issue(
                level="reject", rule="assets.manifest",
                message=f"scene {scene_index}: asset {ref!r} is not in the approved manifest",
                scene_index=scene_index,
            ))
    return issues


def _find_external_refs(scene):
    """CONTRACT.md §4: reject any scene/payload key literally named
    `detail_file` (anywhere in the scene), or any render[].payload STRING
    value that looks like a bare relative path to a .md/.txt/.json sidecar.
    Returns human-readable message fragments, not Issues -- the caller
    applies the configured severity uniformly."""
    messages = []

    def walk_for_detail_file(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "detail_file":
                    messages.append(f"forbidden 'detail_file' key at {path or '<scene>'}")
                walk_for_detail_file(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                walk_for_detail_file(value, f"{path}[{idx}]")

    walk_for_detail_file(scene, "")

    for j, entry in enumerate(scene.get("render") or []):
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            if isinstance(value, str) and _EXTERNAL_REF_RE.match(value.strip()):
                messages.append(
                    f"render[{j}].payload.{key} looks like an external sidecar reference: {value!r}"
                )
    return messages


def _redaction_matches(payload_text, pattern_cfg):
    """Every match of pattern_cfg['regex'] in payload_text not excluded by
    pattern_cfg['except'] (checked against the matched substring, anchored
    the same way config/validation.yaml's public_ip except clause is)."""
    regex = re.compile(pattern_cfg["regex"])
    except_re = re.compile(pattern_cfg["except"]) if pattern_cfg.get("except") else None
    matches = []
    for m in regex.finditer(payload_text):
        matched = m.group(0)
        if except_re and except_re.match(matched):
            continue
        matches.append(matched)
    return matches


def _check_redaction(node, redaction_cfg, scene_index):
    """Belt-and-braces scan mirroring scripts/build_replay_library.py's
    LEAK_AUDIT: serialize the WHOLE node (a scene dict, or {meta, cast}) to
    JSON text and regex over that, rather than trusting a fixed list of
    field names to check. One Issue per (pattern, node) that has at least
    one non-excepted match -- matches LEAK_AUDIT's own reporting style of
    surfacing a single example rather than every occurrence."""
    patterns = redaction_cfg.get("patterns") or []
    if not patterns:
        return []
    on_match = redaction_cfg.get("on_match", "warn")
    payload_text = json.dumps(node, ensure_ascii=False, default=str)

    issues = []
    for pattern_cfg in patterns:
        name = pattern_cfg.get("name", "unnamed")
        matches = _redaction_matches(payload_text, pattern_cfg)
        if not matches:
            continue
        where = f"scene {scene_index}" if scene_index is not None else "episode metadata"
        message = f"redaction pattern {name!r} matched {matches[0]!r} in {where}"
        if on_match == "reject":
            issues.append(Issue(level="reject", rule=f"redaction:{name}", message=message, scene_index=scene_index))
        elif on_match == "redact":
            issues.append(Issue(level="warn", rule=f"redaction:{name}:redact", message=message, scene_index=scene_index))
        else:  # "warn" or unrecognized -> warn
            issues.append(Issue(level="warn", rule=f"redaction:{name}", message=message, scene_index=scene_index))
    return issues


# ── validation ────────────────────────────────────────────────────────────

def validate_episode(episode, *, primitives, rules, pane_ids=None, assets_dir=None, kinds=None):
    """Validate a normalized episode. Pure -- never raises for content
    problems, always returns a list[Issue] (empty if nothing's wrong).

    `kinds`: optional set/container of valid scene `kind` values (typically
    a campaign's narration.yaml keys). When None, the unknown_kind check is
    skipped entirely -- callers that don't have narration config loaded
    (or don't care about it) simply omit it. CONTRACT.md §4 asks for this
    exact escape hatch on the unknown_kind check specifically.
    """
    issues = []
    structure = rules.get("structure") or {}
    limits = rules.get("limits") or {}
    assets_cfg = rules.get("assets") or {}
    redaction_cfg = rules.get("redaction") or {}

    raw_scenes = episode.get("scenes")
    scenes = raw_scenes if isinstance(raw_scenes, list) else []
    raw_cast = episode.get("cast")
    cast = set(raw_cast) if isinstance(raw_cast, list) else set()

    required_fields = structure.get("required_scene_fields") or []
    unknown_kind_setting = structure.get("unknown_kind", "reject")
    unknown_primitive_setting = structure.get("unknown_primitive", "reject")
    unknown_speaker_setting = structure.get("unknown_speaker", "reject")
    unknown_target_setting = structure.get("unknown_target", "reject")
    external_refs_setting = structure.get("external_refs", "reject")

    max_scenes = limits.get("max_scenes")
    max_text_chars = limits.get("max_text_chars")
    max_scene_seconds = limits.get("max_scene_seconds")

    if max_scenes is not None and len(scenes) > max_scenes:
        issues.append(Issue(
            level="reject", rule="max_scenes",
            message=f"episode has {len(scenes)} scenes, exceeds max_scenes={max_scenes}",
            scene_index=None,
        ))

    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            issues.append(Issue(level="reject", rule="required_scene_fields",
                                 message=f"scene {i} is not an object", scene_index=i))
            continue

        # structure.required_scene_fields -- always a hard reject, no
        # severity knob in config/validation.yaml (a bare list, not a
        # reject|warn|skip_scene setting like its neighbors).
        missing = [field for field in required_fields if not scene.get(field)]
        if missing:
            issues.append(Issue(
                level="reject", rule="required_scene_fields",
                message=f"scene {i} missing required field(s): {missing}",
                scene_index=i,
            ))

        # structure.unknown_kind
        if kinds is not None:
            kind = scene.get("kind")
            if kind is not None and kind not in kinds:
                _severity_issue(unknown_kind_setting, scene_index=i, rule_name="unknown_kind",
                                 message=f"scene {i} has unknown kind {kind!r}", issues=issues)

        # structure.unknown_speaker
        speaker = scene.get("speaker")
        if speaker is not None and speaker not in cast:
            _severity_issue(unknown_speaker_setting, scene_index=i, rule_name="unknown_speaker",
                             message=f"scene {i} speaker {speaker!r} not in cast {sorted(cast)}",
                             issues=issues)

        # limits.max_text_chars
        if max_text_chars is not None:
            text = scene.get("text") or ""
            if len(text) > max_text_chars:
                issues.append(Issue(
                    level="reject", rule="max_text_chars",
                    message=f"scene {i} text is {len(text)} chars, exceeds max_text_chars={max_text_chars}",
                    scene_index=i,
                ))

        # limits.max_scene_seconds (app/primitives.py::estimate_scene_seconds)
        if max_scene_seconds is not None:
            try:
                seconds = estimate_scene_seconds(scene, primitives)
            except PrimitiveError:
                seconds = None  # unknown_primitive (below) already reports this
            if seconds is not None and seconds > max_scene_seconds:
                issues.append(Issue(
                    level="reject", rule="max_scene_seconds",
                    message=f"scene {i} estimated at {seconds:.1f}s, exceeds max_scene_seconds={max_scene_seconds}",
                    scene_index=i,
                ))

        # render[] entries: unknown_primitive / unknown_target / assets
        render = scene.get("render") or []
        for j, entry in enumerate(render):
            if not isinstance(entry, dict):
                _severity_issue(unknown_primitive_setting, scene_index=i, rule_name="unknown_primitive",
                                 message=f"scene {i} render[{j}] is not an object", issues=issues)
                continue

            name = entry.get("primitive")
            recipe = None
            try:
                recipe = resolve_recipe(primitives, name)
            except PrimitiveError:
                _severity_issue(unknown_primitive_setting, scene_index=i, rule_name="unknown_primitive",
                                 message=f"scene {i} render[{j}] uses unknown primitive {name!r}",
                                 issues=issues)

            target = entry.get("target")
            if target and pane_ids is not None and target not in pane_ids:
                _severity_issue(unknown_target_setting, scene_index=i, rule_name="unknown_target",
                                 message=f"scene {i} render[{j}] targets unknown pane {target!r}",
                                 issues=issues)

            if recipe is not None:
                for behavior in recipe.get("behaviors", []):
                    if behavior.get("behavior") != "image":
                        continue
                    field = behavior.get("field", "payload.image")
                    ref = _resolve_field(entry, field)
                    if ref:
                        issues.extend(_check_asset_ref(ref, assets_cfg, assets_dir, scene_index=i))

        # structure.external_refs
        for msg in _find_external_refs(scene):
            _severity_issue(external_refs_setting, scene_index=i, rule_name="external_refs",
                             message=f"scene {i}: {msg}", issues=issues)

        # redaction.patterns -- serialized per-scene (belt-and-braces; see
        # _check_redaction's docstring)
        issues.extend(_check_redaction(scene, redaction_cfg, scene_index=i))

    # Redaction also sweeps meta/cast once at the episode level -- a leak
    # could in principle hide in meta.title or a cast id, not just scenes.
    issues.extend(_check_redaction(
        {"meta": episode.get("meta"), "cast": episode.get("cast")}, redaction_cfg, scene_index=None,
    ))

    return issues


# ── redact-mode rewrite (load_episode only; validate_episode stays pure) ──

def _apply_redaction(episode, patterns):
    """Actually scrub matched substrings from the episode's own text
    fields, for config/validation.yaml's redaction.on_match == "redact".

    validate_episode never mutates its input (kept pure, Issues only) --
    this is the one place a redact-mode match becomes an edit, applied
    right before load_episode hands back the normalized dict it already
    owns building. Scans/rewrites per string field (text/fallback/payload
    values) rather than the whole serialized JSON detection scan does,
    since none of these patterns' matches contain characters JSON escaping
    would mangle, so a field-level regex substitution is equivalent and
    much simpler to apply back onto the structured episode.
    """
    def redact_value(value):
        for pattern_cfg in patterns:
            name = pattern_cfg.get("name", "redacted")
            regex = re.compile(pattern_cfg["regex"])
            except_re = re.compile(pattern_cfg["except"]) if pattern_cfg.get("except") else None

            def _repl(m, except_re=except_re, name=name):
                if except_re and except_re.match(m.group(0)):
                    return m.group(0)
                return f"[redacted:{name}]"

            value = regex.sub(_repl, value)
        return value

    for scene in episode.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for key in ("text", "fallback"):
            if isinstance(scene.get(key), str):
                scene[key] = redact_value(scene[key])
        for entry in scene.get("render") or []:
            if not isinstance(entry, dict):
                continue
            payload = entry.get("payload")
            if isinstance(payload, dict):
                for pkey, pvalue in list(payload.items()):
                    if isinstance(pvalue, str):
                        payload[pkey] = redact_value(pvalue)


# ── top-level entry point ────────────────────────────────────────────────

def load_episode(path, *, primitives=None, rules=None, pane_ids=None, assets_dir=None,
                 campaign=None, kinds=None):
    """Read `path` (a JSON episode file), normalize it, and validate it.

    `rules` defaults to load_rules() when omitted. `primitives` defaults to
    primitives.load_primitives(campaign) when omitted, in which case
    `campaign` is required unless the episode's own meta.campaign supplies
    it. `kinds` is threaded straight through to validate_episode's own
    `kinds` param (optional set/container of valid scene `kind` values,
    typically a campaign's narration.yaml keys) -- defaults to None, which
    skips the unknown_kind check entirely, exactly as validate_episode
    itself does. Without a caller passing `kinds` explicitly here,
    unknown_kind can never fire through the normal ingest path; see
    app/replay_pane.py, which loads a campaign's narration config and passes
    its keys as `kinds` on every load_episode call, and
    generators/coder/build_library.py, which should do the same on the
    generator side. Raises EpisodeError, listing every rejecting Issue, if
    validation fails. Warn-level issues are logged, not raised.
    skip_scene-severity issues cause their scene to be dropped from the
    returned episode. Returns the normalized (and, when
    redaction.on_match == "redact", scrubbed) episode dict.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        log.error("failed to read episode %s: %s", path, exc)
        raise EpisodeError(f"failed to read episode {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        log.error("failed to parse episode %s: %s", path, exc)
        raise EpisodeError(f"failed to parse episode {path}: {exc}") from exc

    episode = normalize_episode(raw)

    if rules is None:
        rules = load_rules()

    if primitives is None:
        # Local import to avoid a hard module-level dependency for callers
        # that always pass their own `primitives` table in.
        from primitives import load_primitives
        campaign_name = campaign or (episode.get("meta") or {}).get("campaign")
        if not campaign_name:
            raise EpisodeError(
                f"episode {path} has no meta.campaign and no campaign was given; "
                "cannot resolve its primitive table"
            )
        primitives = load_primitives(campaign_name)

    issues = validate_episode(episode, primitives=primitives, rules=rules,
                               pane_ids=pane_ids, assets_dir=assets_dir, kinds=kinds)

    rejecting = [issue for issue in issues if issue.level == "reject"]
    if rejecting:
        detail = "; ".join(f"[{issue.rule}] {issue.message}" for issue in rejecting)
        log.error("episode %s failed validation: %s", path, detail)
        raise EpisodeError(f"episode {path} failed validation: {detail}")

    skip_indices = {
        issue.scene_index for issue in issues
        if issue.rule.endswith(":skip_scene") and issue.scene_index is not None
    }
    if skip_indices:
        log.warning("episode %s: dropping %d scene(s) flagged skip_scene: %s",
                    path, len(skip_indices), sorted(skip_indices))
        episode["scenes"] = [
            scene for idx, scene in enumerate(episode["scenes"]) if idx not in skip_indices
        ]

    redaction_cfg = rules.get("redaction") or {}
    if redaction_cfg.get("on_match") == "redact" and redaction_cfg.get("patterns"):
        _apply_redaction(episode, redaction_cfg["patterns"])

    for issue in issues:
        if issue.level == "warn":
            log.warning("episode %s: [%s] %s", path, issue.rule, issue.message)

    return episode
