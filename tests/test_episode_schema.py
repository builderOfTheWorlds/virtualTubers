"""Tests for app/episode_schema.py — the campaign-platform episode validator.

The contract under test (CONTRACT.md §4, docs/campaign_platform_build.md's
"Validator" section): config-driven rules (config/validation.yaml) checked
by code, with three headline properties this file exercises explicitly:

1. Campaign isolation falls out of `unknown_primitive` for free — a D&D
   episode must validate cleanly against the dnd campaign's merged
   primitive table and REJECT under the coder campaign's, with no separate
   isolation mechanism.
2. Private LAN IPs must survive the redaction sweep untouched while a
   public IP is rejected — config/validation.yaml's public_ip `except`
   clause, preserved from a real 2026-07-12 incident review.
3. A password-shaped string must be caught by the redaction sweep — the
   direct regression test for that same 2026-07-12 leak.

Fakes/fixtures are hand-written and duck-typed (no unittest.mock, matching
this repo's app/ test convention — see tests/test_primitives.py). Most
structural/limits/assets tests build a small isolated primitives table via
primitives.load_primitives against a tmp_path base+campaign layer pair
(mirroring test_primitives.py's own `layers`/`write_campaign` fixtures) so
they don't depend on the shape of the real shipped coder/dnd config; the
campaign-isolation and redaction-regex tests deliberately load the REAL
config/validation.yaml and config/campaigns/{coder,dnd}/primitives.yaml,
since those are exactly the files being regression-guarded.
"""
import json
import logging
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import episode_schema  # noqa: E402
from episode_schema import (  # noqa: E402
    EpisodeError, Issue, load_episode, load_rules, normalize_episode, validate_episode,
)
from primitives import load_primitives  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REAL_VALIDATION_PATH = ROOT / "config" / "validation.yaml"
REAL_BASE_PATH = ROOT / "config" / "primitives.yaml"
REAL_CAMPAIGNS_DIR = ROOT / "config" / "campaigns"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ── fixtures: an isolated tmp primitives table (independent of real config) ─

@pytest.fixture
def layers(tmp_path):
    base = tmp_path / "primitives.yaml"
    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    base.write_text(yaml.safe_dump({
        "say": {
            "target": "theater",
            "behaviors": [{"behavior": "type", "field": "payload.text", "rate_cps": 1000}],
        },
        "show_pic": {
            "target": "theater",
            "behaviors": [{"behavior": "image", "field": "payload.image", "hold_s": 1.0}],
        },
        "long_pause": {
            "behaviors": [{"behavior": "pause", "seconds": 200.0}],
        },
    }), encoding="utf-8")
    return {"base": base, "campaigns": campaigns}


@pytest.fixture
def demo_primitives(layers):
    return load_primitives("demo", base_path=layers["base"], campaigns_dir=layers["campaigns"])


def base_rules(**overrides):
    """A minimal, hermetic rules dict — every check enabled at its default
    severity — independent of config/validation.yaml's real content so
    structural tests don't break if the shipped file's limits change."""
    rules = {
        "structure": {
            "required_scene_fields": ["speaker", "kind"],
            "unknown_kind": "reject",
            "unknown_primitive": "reject",
            "unknown_speaker": "reject",
            "unknown_target": "reject",
            "external_refs": "reject",
        },
        "limits": {"max_scenes": 100, "max_text_chars": 1000, "max_scene_seconds": 90},
        "assets": {"root": "assets", "basename_only": True, "must_exist": True,
                   "manifest": None},
        "redaction": {"on_match": "reject", "patterns": []},
    }
    for section, patch in overrides.items():
        rules[section].update(patch)
    return rules


def make_scene(**kwargs):
    scene = {"speaker": "coder", "kind": "coder_work", "text": "", "fallback": "",
              "mode": "sequence", "render": []}
    scene.update(kwargs)
    return scene


def make_episode(scenes, cast=("boss", "coder")):
    return {"meta": {"schema": 1, "campaign": "demo", "id": "ep", "title": "t",
                      "created": "2026-07-26T00:00:00Z"},
            "cast": list(cast), "scenes": scenes}


# ── load_rules ────────────────────────────────────────────────────────────

def test_load_rules_reads_the_real_shipped_validation_yaml():
    """The real config/validation.yaml must parse into the four top-level
    sections the design doc specifies — a regression here means the shipped
    file's shape drifted from what episode_schema.py expects."""
    rules = load_rules(REAL_VALIDATION_PATH)
    assert set(rules) == {"structure", "limits", "assets", "redaction"}
    assert rules["structure"]["required_scene_fields"] == ["speaker", "kind"]
    assert rules["redaction"]["on_match"] == "reject"


def test_load_rules_missing_file_raises_episode_error(tmp_path):
    """Unlike a missing campaign primitives file (fine, shared-only), a
    missing validation.yaml has no safe default -- especially the
    redaction patterns -- so it must raise, not silently skip checks."""
    with pytest.raises(EpisodeError):
        load_rules(tmp_path / "nope.yaml")


def test_load_rules_malformed_yaml_raises_episode_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("structure: [unclosed", encoding="utf-8")
    with pytest.raises(EpisodeError):
        load_rules(bad)


# ── normalize_episode ─────────────────────────────────────────────────────

def test_normalize_episode_fills_scene_defaults():
    raw = {"scenes": [{"speaker": "coder", "kind": "coder_work"}]}
    normalized = normalize_episode(raw)
    scene = normalized["scenes"][0]
    assert scene["text"] == ""
    assert scene["fallback"] == ""
    assert scene["mode"] == "sequence"
    assert scene["render"] == []


def test_normalize_episode_never_manufactures_missing_required_fields():
    """speaker/kind are required (structure.required_scene_fields) --
    normalize_episode must leave a missing one absent so validate_episode
    can actually catch it, rather than papering over the hole."""
    raw = {"scenes": [{"kind": "coder_work"}]}
    normalized = normalize_episode(raw)
    assert "speaker" not in normalized["scenes"][0]


def test_normalize_episode_does_not_mutate_the_caller_raw_dict():
    raw = {"scenes": [{"speaker": "coder", "kind": "coder_work"}]}
    normalize_episode(raw)
    assert raw["scenes"][0] == {"speaker": "coder", "kind": "coder_work"}  # untouched


def test_normalize_episode_gives_each_scene_its_own_independent_render_list():
    """Regression guard: a naive scene.setdefault("render", SHARED_LIST)
    would hand every scene missing render[] the SAME list object, so
    mutating one scene's render list later would silently leak into every
    other scene sharing that default."""
    raw = {"scenes": [{"speaker": "boss", "kind": "boss"}, {"speaker": "coder", "kind": "coder_work"}]}
    normalized = normalize_episode(raw)
    normalized["scenes"][0]["render"].append({"primitive": "whatever"})
    assert normalized["scenes"][1]["render"] == []


def test_normalize_episode_missing_meta_and_cast_default_to_empty():
    normalized = normalize_episode({"scenes": []})
    assert normalized["meta"] == {}
    assert normalized["cast"] == []


# ── structure.required_scene_fields ──────────────────────────────────────

def test_required_scene_fields_missing_speaker_rejects(demo_primitives):
    episode = make_episode([{"kind": "coder_work", "text": "", "fallback": "", "mode": "sequence", "render": []}])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert any(i.rule == "required_scene_fields" and i.level == "reject" for i in issues)


def test_required_scene_fields_missing_kind_rejects(demo_primitives):
    episode = make_episode([{"speaker": "coder", "text": "", "fallback": "", "mode": "sequence", "render": []}])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert any(i.rule == "required_scene_fields" for i in issues)


def test_required_scene_fields_present_passes(demo_primitives):
    episode = make_episode([make_scene()])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert not any(i.rule == "required_scene_fields" for i in issues)


def test_scene_that_is_not_an_object_rejects(demo_primitives):
    episode = make_episode(["not a dict"])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert any(i.level == "reject" for i in issues)


# ── structure.unknown_kind ────────────────────────────────────────────────

def test_unknown_kind_rejects_by_default_when_kinds_given(demo_primitives):
    episode = make_episode([make_scene(kind="not_a_real_kind")])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules(),
                               kinds={"coder_work", "boss"})
    assert any(i.rule == "unknown_kind" and i.level == "reject" for i in issues)


def test_unknown_kind_warn_severity_does_not_reject(demo_primitives):
    rules = base_rules()
    rules["structure"]["unknown_kind"] = "warn"
    episode = make_episode([make_scene(kind="mystery")])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules, kinds={"coder_work"})
    kind_issues = [i for i in issues if i.rule == "unknown_kind"]
    assert len(kind_issues) == 1
    assert kind_issues[0].level == "warn"


def test_unknown_kind_skip_scene_severity_flags_scene_for_drop(demo_primitives):
    rules = base_rules()
    rules["structure"]["unknown_kind"] = "skip_scene"
    episode = make_episode([make_scene(kind="mystery")])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules, kinds={"coder_work"})
    assert any(i.rule == "unknown_kind:skip_scene" and i.level == "warn" and i.scene_index == 0
               for i in issues)


def test_unknown_kind_check_skipped_entirely_when_kinds_is_none(demo_primitives):
    """CONTRACT.md §4's specific ask: kinds=None must skip the check
    entirely, not just default to permissive -- callers without a
    narration config loaded shouldn't get spurious issues."""
    episode = make_episode([make_scene(kind="anything_goes")])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules(), kinds=None)
    assert not any(i.rule.startswith("unknown_kind") for i in issues)


# ── structure.unknown_primitive (+ campaign isolation headline test) ─────

def test_unknown_primitive_rejects_by_default(demo_primitives):
    episode = make_episode([make_scene(render=[{"primitive": "does_not_exist", "payload": {}}])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert any(i.rule == "unknown_primitive" and i.level == "reject" for i in issues)


def test_unknown_primitive_warn_severity_does_not_reject(demo_primitives):
    rules = base_rules()
    rules["structure"]["unknown_primitive"] = "warn"
    episode = make_episode([make_scene(render=[{"primitive": "nope", "payload": {}}])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules)
    assert all(i.level != "reject" for i in issues)
    assert any(i.rule == "unknown_primitive" for i in issues)


def test_unknown_primitive_skip_scene_severity(demo_primitives):
    rules = base_rules()
    rules["structure"]["unknown_primitive"] = "skip_scene"
    episode = make_episode([make_scene(render=[{"primitive": "nope", "payload": {}}])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules)
    assert any(i.rule == "unknown_primitive:skip_scene" for i in issues)


def test_known_primitive_passes(demo_primitives):
    episode = make_episode([make_scene(render=[{"primitive": "say", "payload": {"text": "hi"}}])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert not any(i.rule.startswith("unknown_primitive") for i in issues)


def test_dnd_episode_validates_cleanly_against_the_dnd_campaign():
    """Headline property #1, half A: a real D&D episode must pass
    unknown_primitive/unknown_speaker/unknown_kind cleanly when validated
    against the campaign it was actually written for."""
    dnd_primitives = load_primitives("dnd", base_path=REAL_BASE_PATH, campaigns_dir=REAL_CAMPAIGNS_DIR)
    raw = json.loads((FIXTURES / "dnd_episode_valid.json").read_text(encoding="utf-8"))
    episode = normalize_episode(raw)
    rules = base_rules()
    rules["assets"]["must_exist"] = False  # no real assets/ dir for this unit test
    issues = validate_episode(episode, primitives=dnd_primitives, rules=rules,
                               kinds={"recap", "scene_set", "dialogue"})
    assert not any(i.level == "reject" for i in issues)


def test_dnd_episode_rejects_under_the_coder_campaign():
    """Headline property #1, half B: the SAME D&D episode must REJECT when
    validated against the coder campaign's merged primitive table --
    display_map/open_inventory don't exist there. This is campaign
    isolation falling out of unknown_primitive 'for free', per
    docs/campaign_platform_build.md's own framing -- no separate isolation
    mechanism should be needed."""
    coder_primitives = load_primitives("coder", base_path=REAL_BASE_PATH, campaigns_dir=REAL_CAMPAIGNS_DIR)
    raw = json.loads((FIXTURES / "dnd_episode_valid.json").read_text(encoding="utf-8"))
    episode = normalize_episode(raw)
    rules = base_rules()
    rules["assets"]["must_exist"] = False
    issues = validate_episode(episode, primitives=coder_primitives, rules=rules)
    reject_rules = {i.rule for i in issues if i.level == "reject"}
    assert "unknown_primitive" in reject_rules
    # Note: cast is episode-level metadata, not campaign-level, so
    # unknown_speaker does NOT also isolate here -- gm/thorin/sable are
    # still the episode's own declared cast regardless of which campaign's
    # primitives are used to validate it. unknown_primitive is the rule
    # docs/campaign_platform_build.md specifically calls out as giving
    # campaign isolation "for free"; this test guards exactly that claim.


# ── structure.unknown_speaker ─────────────────────────────────────────────

def test_unknown_speaker_rejects_by_default(demo_primitives):
    episode = make_episode([make_scene(speaker="stranger")], cast=("boss", "coder"))
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert any(i.rule == "unknown_speaker" and i.level == "reject" for i in issues)


def test_speaker_in_cast_passes(demo_primitives):
    episode = make_episode([make_scene(speaker="coder")], cast=("boss", "coder"))
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert not any(i.rule == "unknown_speaker" for i in issues)


def test_unknown_speaker_rejects_even_with_empty_cast(demo_primitives):
    """An empty cast list means NO speaker is valid -- must not be treated
    as 'no cast configured, skip the check'."""
    episode = make_episode([make_scene(speaker="coder")], cast=())
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert any(i.rule == "unknown_speaker" for i in issues)


# ── structure.unknown_target ──────────────────────────────────────────────

def test_unknown_target_rejects_when_pane_ids_given(demo_primitives):
    episode = make_episode([make_scene(render=[
        {"primitive": "say", "target": "sidebar", "payload": {"text": "hi"}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules(),
                               pane_ids={"theater", "notes"})
    assert any(i.rule == "unknown_target" and i.level == "reject" for i in issues)


def test_known_target_passes(demo_primitives):
    episode = make_episode([make_scene(render=[
        {"primitive": "say", "target": "notes", "payload": {"text": "hi"}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules(),
                               pane_ids={"theater", "notes"})
    assert not any(i.rule == "unknown_target" for i in issues)


def test_unknown_target_check_skipped_when_pane_ids_is_none(demo_primitives):
    episode = make_episode([make_scene(render=[
        {"primitive": "say", "target": "nonsense-pane", "payload": {"text": "hi"}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules(), pane_ids=None)
    assert not any(i.rule == "unknown_target" for i in issues)


def test_entry_without_explicit_target_never_checked(demo_primitives):
    """No target on the entry means the recipe's own default applies --
    that's trusted config, not user content, so it must never be flagged."""
    episode = make_episode([make_scene(render=[{"primitive": "say", "payload": {"text": "hi"}}])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules(), pane_ids=set())
    assert not any(i.rule == "unknown_target" for i in issues)


# ── structure.external_refs ────────────────────────────────────────────────

def test_external_refs_rejects_detail_file_key(demo_primitives):
    episode = make_episode([make_scene(detail_file="tool_001_Bash.md")])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert any(i.rule == "external_refs" and i.level == "reject" for i in issues)


def test_external_refs_rejects_detail_file_key_nested_in_payload(demo_primitives):
    episode = make_episode([make_scene(render=[
        {"primitive": "say", "payload": {"text": "hi", "detail_file": "tool_002_Edit.md"}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert any(i.rule == "external_refs" for i in issues)


def test_external_refs_rejects_bare_sidecar_path_shaped_payload_value(demo_primitives):
    episode = make_episode([make_scene(render=[
        {"primitive": "say", "payload": {"text": "tool_001_Bash.md"}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert any(i.rule == "external_refs" for i in issues)


def test_external_refs_does_not_flag_ordinary_prose_mentioning_a_filename(demo_primitives):
    episode = make_episode([make_scene(render=[
        {"primitive": "say", "payload": {"text": "Reading the config file to check settings."}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert not any(i.rule == "external_refs" for i in issues)


def test_external_refs_skip_scene_severity(demo_primitives):
    rules = base_rules()
    rules["structure"]["external_refs"] = "skip_scene"
    episode = make_episode([make_scene(detail_file="tool_001_Bash.md")])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules)
    assert any(i.rule == "external_refs:skip_scene" and i.level == "warn" for i in issues)


# ── limits.max_scenes / max_text_chars / max_scene_seconds ────────────────

def test_max_scenes_rejects_episode_level_not_scene_level(demo_primitives):
    rules = base_rules(limits={"max_scenes": 2})
    episode = make_episode([make_scene(), make_scene(), make_scene()])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules)
    max_scenes_issues = [i for i in issues if i.rule == "max_scenes"]
    assert len(max_scenes_issues) == 1
    assert max_scenes_issues[0].level == "reject"
    assert max_scenes_issues[0].scene_index is None


def test_max_text_chars_rejects_overlong_scene_text(demo_primitives):
    rules = base_rules(limits={"max_text_chars": 10})
    episode = make_episode([make_scene(text="x" * 50)])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules)
    assert any(i.rule == "max_text_chars" and i.level == "reject" and i.scene_index == 0 for i in issues)


def test_max_text_chars_passes_short_scene_text(demo_primitives):
    rules = base_rules(limits={"max_text_chars": 1000})
    episode = make_episode([make_scene(text="short")])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules)
    assert not any(i.rule == "max_text_chars" for i in issues)


def test_max_scene_seconds_rejects_over_budget_scene(demo_primitives):
    """This is the direct replacement for the old MAX_SCENE_EVENTS cap
    (docs/campaign_platform_build.md) -- computed from
    primitives.estimate_scene_seconds, not an event count."""
    rules = base_rules(limits={"max_scene_seconds": 90})
    episode = make_episode([make_scene(render=[{"primitive": "long_pause", "payload": {}}])])  # 200s
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules)
    scene_seconds_issues = [i for i in issues if i.rule == "max_scene_seconds"]
    assert len(scene_seconds_issues) == 1
    assert scene_seconds_issues[0].level == "reject"


def test_max_scene_seconds_passes_within_budget(demo_primitives):
    rules = base_rules(limits={"max_scene_seconds": 90})
    episode = make_episode([make_scene(render=[{"primitive": "say", "payload": {"text": "hi"}}])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules)
    assert not any(i.rule == "max_scene_seconds" for i in issues)


def test_max_scene_seconds_does_not_crash_on_unknown_primitive(demo_primitives):
    """A scene whose render references an unknown primitive can't have its
    screen time estimated -- must degrade silently (unknown_primitive
    already reports the real problem) rather than raising out of
    validate_episode, which must never raise for content problems."""
    rules = base_rules(limits={"max_scene_seconds": 1})
    episode = make_episode([make_scene(render=[{"primitive": "ghost", "payload": {}}])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules)  # must not raise
    assert not any(i.rule == "max_scene_seconds" for i in issues)


# ── assets: basename_only hostile-path sweep ──────────────────────────────

@pytest.mark.parametrize("hostile", [
    "../../../etc/passwd",
    "..\\..\\secrets.json",
    "/etc/passwd",
    "c:\\Users\\dev\\.env",
])
def test_asset_basename_only_rejects_hostile_path_shaped_refs(demo_primitives, hostile):
    """The same hostile inputs app/replay_pane.py::resolve_episode is
    tested against (tests/test_replay_pane.py) -- asset containment is
    episode_schema.py's job now (CONTRACT.md §9b: primitives.py's
    RenderContext deliberately has no assets_dir and does no
    containment of its own)."""
    episode = make_episode([make_scene(render=[
        {"primitive": "show_pic", "payload": {"image": hostile}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())
    assert any(i.rule == "assets.basename_only" and i.level == "reject" for i in issues)


def test_asset_plain_basename_passes_basename_only_check(demo_primitives):
    rules = base_rules()
    rules["assets"]["must_exist"] = False
    episode = make_episode([make_scene(render=[
        {"primitive": "show_pic", "payload": {"image": "map.png"}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules)
    assert not any(i.rule.startswith("assets.") for i in issues)


# ── assets: must_exist + manifest (need a real assets_dir) ────────────────

def test_asset_must_exist_rejects_missing_file(tmp_path, demo_primitives):
    assets_dir = tmp_path / "library"
    (assets_dir / "assets").mkdir(parents=True)
    rules = base_rules()
    episode = make_episode([make_scene(render=[
        {"primitive": "show_pic", "payload": {"image": "missing.png"}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules, assets_dir=assets_dir)
    assert any(i.rule == "assets.must_exist" and i.level == "reject" for i in issues)


def test_asset_must_exist_passes_when_file_present(tmp_path, demo_primitives):
    assets_dir = tmp_path / "library"
    (assets_dir / "assets").mkdir(parents=True)
    (assets_dir / "assets" / "map.png").write_bytes(b"fake-png")
    rules = base_rules()
    episode = make_episode([make_scene(render=[
        {"primitive": "show_pic", "payload": {"image": "map.png"}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules, assets_dir=assets_dir)
    assert not any(i.rule.startswith("assets.") for i in issues)


def test_asset_must_exist_skipped_when_assets_dir_not_given(demo_primitives):
    """Can't check existence without knowing where the library lives --
    must skip the check rather than reject everything by default."""
    rules = base_rules()
    episode = make_episode([make_scene(render=[
        {"primitive": "show_pic", "payload": {"image": "map.png"}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules, assets_dir=None)
    assert not any(i.rule == "assets.must_exist" for i in issues)


def test_asset_manifest_rejects_file_not_in_allowlist(tmp_path, demo_primitives):
    assets_dir = tmp_path / "library"
    (assets_dir / "assets").mkdir(parents=True)
    (assets_dir / "assets" / "map.png").write_bytes(b"x")
    (assets_dir / "assets" / "unapproved.png").write_bytes(b"x")
    (assets_dir / "assets" / "approved.txt").write_text("map.png\n", encoding="utf-8")
    rules = base_rules()
    rules["assets"]["manifest"] = "assets/approved.txt"
    episode = make_episode([make_scene(render=[
        {"primitive": "show_pic", "payload": {"image": "unapproved.png"}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules, assets_dir=assets_dir)
    assert any(i.rule == "assets.manifest" and i.level == "reject" for i in issues)


def test_asset_manifest_passes_file_in_allowlist(tmp_path, demo_primitives):
    assets_dir = tmp_path / "library"
    (assets_dir / "assets").mkdir(parents=True)
    (assets_dir / "assets" / "map.png").write_bytes(b"x")
    (assets_dir / "assets" / "approved.txt").write_text("map.png\n", encoding="utf-8")
    rules = base_rules()
    rules["assets"]["manifest"] = "assets/approved.txt"
    episode = make_episode([make_scene(render=[
        {"primitive": "show_pic", "payload": {"image": "map.png"}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules, assets_dir=assets_dir)
    assert not any(i.rule.startswith("assets.") for i in issues)


def test_asset_manifest_check_skipped_when_manifest_file_absent(tmp_path, demo_primitives):
    """An optional allowlist that hasn't been created yet must not fail
    every asset -- it's an allowlist you opt into by creating the file."""
    assets_dir = tmp_path / "library"
    (assets_dir / "assets").mkdir(parents=True)
    (assets_dir / "assets" / "map.png").write_bytes(b"x")
    rules = base_rules()
    rules["assets"]["manifest"] = "assets/approved.txt"  # never created
    episode = make_episode([make_scene(render=[
        {"primitive": "show_pic", "payload": {"image": "map.png"}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules, assets_dir=assets_dir)
    assert not any(i.rule == "assets.manifest" for i in issues)


# ── redaction: the real config/validation.yaml patterns ───────────────────

@pytest.fixture(scope="module")
def real_redaction_rules():
    return load_rules(REAL_VALIDATION_PATH)["redaction"]


def test_redaction_private_lan_ip_survives_untouched(demo_primitives, real_redaction_rules):
    """Headline property #2, half A: a private LAN IP must never trigger
    the redaction rule -- config/validation.yaml's public_ip `except`
    clause exists specifically to keep these readable (2026-07-12
    incident review, preserved verbatim)."""
    rules = base_rules(redaction={"patterns": real_redaction_rules["patterns"],
                                   "on_match": real_redaction_rules["on_match"]})
    for private_ip in ("192.168.1.117", "10.0.0.5", "172.16.0.1", "127.0.0.1"):
        episode = make_episode([make_scene(text=f"connect to {private_ip} for the dashboard")])
        issues = validate_episode(episode, primitives=demo_primitives, rules=rules)
        assert not any(i.rule.startswith("redaction:public_ip") for i in issues), private_ip


def test_redaction_public_ip_rejected(demo_primitives, real_redaction_rules):
    """Headline property #2, half B: a public IP must be rejected under
    the real shipped on_match: reject config."""
    rules = base_rules(redaction={"patterns": real_redaction_rules["patterns"],
                                   "on_match": real_redaction_rules["on_match"]})
    episode = make_episode([make_scene(text="the stream host is at 8.8.8.8 tonight")])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules)
    assert any(i.rule == "redaction:public_ip" and i.level == "reject" for i in issues)


def test_redaction_password_shaped_string_is_caught(demo_primitives, real_redaction_rules):
    """Direct regression test for the 2026-07-12 password leak
    (docs/campaign_platform_build.md, CONTRACT.md §4) -- a credential
    assignment anywhere in the episode must be caught by the config-driven
    redaction sweep, the same way app/session_log_parser.py's
    REDACTION_RULES catches it on the generator side."""
    rules = base_rules(redaction={"patterns": real_redaction_rules["patterns"],
                                   "on_match": real_redaction_rules["on_match"]})
    episode = make_episode([make_scene(render=[
        {"primitive": "say", "payload": {"text": "startup used password: hunter2 to connect"}},
    ])])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules)
    assert any(i.rule == "redaction:password" and i.level == "reject" for i in issues)


def test_redaction_warn_severity_does_not_reject(demo_primitives):
    rules = base_rules(redaction={
        "on_match": "warn",
        "patterns": [{"name": "password", "regex": r"(?i)\bpassword\s*[:=]\s*\S+"}],
    })
    episode = make_episode([make_scene(text="password: hunter2")])
    issues = validate_episode(episode, primitives=demo_primitives, rules=rules)
    assert all(i.level != "reject" for i in issues)
    assert any(i.rule == "redaction:password" for i in issues)


def test_redaction_no_patterns_configured_produces_no_redaction_issues(demo_primitives):
    episode = make_episode([make_scene(text="password: hunter2")])
    issues = validate_episode(episode, primitives=demo_primitives, rules=base_rules())  # empty patterns
    assert not any(i.rule.startswith("redaction:") for i in issues)


# ── load_episode: end-to-end read + normalize + validate ──────────────────

def test_load_episode_reads_the_real_coder_fixture_end_to_end():
    episode = load_episode(FIXTURES / "coder_episode_valid.json", campaign="coder")
    assert episode["meta"]["campaign"] == "coder"
    assert len(episode["scenes"]) == 2
    assert episode["scenes"][0]["speaker"] == "boss"


def test_load_episode_reads_the_real_dnd_fixture_end_to_end():
    episode = load_episode(FIXTURES / "dnd_episode_valid.json", campaign="dnd")
    assert episode["meta"]["campaign"] == "dnd"
    assert len(episode["scenes"]) == 3


def test_load_episode_raises_episode_error_listing_every_rejecting_issue(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "meta": {"campaign": "coder"}, "cast": ["boss"],
        "scenes": [{"speaker": "an_unknown_speaker", "kind": "boss", "render": []}],
    }), encoding="utf-8")
    with pytest.raises(EpisodeError, match="unknown_speaker"):
        load_episode(bad, campaign="coder")


def test_load_episode_missing_campaign_and_no_meta_campaign_raises(tmp_path):
    bad = tmp_path / "no_campaign.json"
    bad.write_text(json.dumps({"cast": [], "scenes": []}), encoding="utf-8")
    with pytest.raises(EpisodeError):
        load_episode(bad)


def test_load_episode_unreadable_file_raises_episode_error(tmp_path):
    with pytest.raises(EpisodeError):
        load_episode(tmp_path / "does_not_exist.json", campaign="coder")


def test_load_episode_malformed_json_raises_episode_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(EpisodeError):
        load_episode(bad, campaign="coder")


def test_load_episode_rejects_unknown_kind_when_kinds_is_supplied(tmp_path, demo_primitives):
    """Regression test for a contract defect fixed alongside the campaign
    persona-assignment work: load_episode used to accept no `kinds`
    parameter at all, so unknown_kind could NEVER fire through the normal
    ingest path -- only via a direct validate_episode() call (see the now-
    corrected claim in the test right below this one). load_episode now
    threads an explicit `kinds` straight through to validate_episode's own
    `kinds` param, so a real ingest call can reject an episode whose scene
    `kind` isn't one of the campaign's known narration kinds."""
    rules_path = tmp_path / "validation.yaml"
    rules_path.write_text(yaml.safe_dump(base_rules()), encoding="utf-8")
    episode_path = tmp_path / "ep.json"
    episode_path.write_text(json.dumps(make_episode([
        make_scene(kind="not_a_real_kind"),
    ])), encoding="utf-8")

    with pytest.raises(EpisodeError, match="unknown_kind"):
        load_episode(episode_path, primitives=demo_primitives, rules=load_rules(rules_path),
                     campaign="demo", kinds={"coder_work", "boss"})


def test_load_episode_kinds_defaults_to_none_and_skips_the_check(tmp_path, demo_primitives):
    """The other half of the same fix: `kinds` must default to None (check
    skipped entirely) when a caller doesn't supply one explicitly -- callers
    with no narration config loaded must not start getting spurious
    rejections just because load_episode gained the parameter."""
    rules_path = tmp_path / "validation.yaml"
    rules_path.write_text(yaml.safe_dump(base_rules()), encoding="utf-8")
    episode_path = tmp_path / "ep.json"
    episode_path.write_text(json.dumps(make_episode([
        make_scene(kind="anything_goes"),
    ])), encoding="utf-8")

    result = load_episode(episode_path, primitives=demo_primitives, rules=load_rules(rules_path),
                          campaign="demo")  # kinds omitted

    assert result is not None


def test_load_episode_warn_issues_are_logged_not_raised(tmp_path, caplog, demo_primitives):
    """load_episode defaults `kinds=None` (check skipped) unless a caller
    supplies it explicitly -- see test_load_episode_rejects_unknown_kind_
    when_kinds_is_supplied above for the ingest path that now DOES thread
    it through (app/replay_pane.py passes the campaign's narration.yaml
    keys on every load_episode call). This test doesn't pass `kinds`, so
    unknown_kind still can't fire here; unknown_primitive:warn is used
    instead as a rule load_episode always evaluates regardless."""
    rules = base_rules()
    rules["structure"]["unknown_primitive"] = "warn"
    rules_path = tmp_path / "validation.yaml"
    rules_path.write_text(yaml.safe_dump(rules), encoding="utf-8")
    episode_path = tmp_path / "ep.json"
    episode_path.write_text(
        json.dumps(make_episode([make_scene(render=[{"primitive": "ghost", "payload": {}}])])),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="episode_schema"):
        result = load_episode(episode_path, primitives=demo_primitives, rules=load_rules(rules_path))
    assert result is not None
    assert any("unknown_primitive" in record.message for record in caplog.records)


def test_load_episode_drops_skip_scene_flagged_scenes(tmp_path, demo_primitives):
    """Same reasoning as the warn test above: exercise skip_scene through
    unknown_primitive, since load_episode never supplies `kinds`."""
    rules = base_rules()
    rules["structure"]["unknown_primitive"] = "skip_scene"
    rules_path = tmp_path / "validation.yaml"
    rules_path.write_text(yaml.safe_dump(rules), encoding="utf-8")
    episode_path = tmp_path / "ep.json"
    episode_path.write_text(json.dumps(make_episode([
        make_scene(kind="coder_work", render=[{"primitive": "say", "payload": {"text": "hi"}}]),
        make_scene(kind="coder_work", render=[{"primitive": "ghost", "payload": {}}]),
    ])), encoding="utf-8")
    result = load_episode(episode_path, primitives=demo_primitives, rules=load_rules(rules_path),
                           campaign="demo")
    assert len(result["scenes"]) == 1
    assert result["scenes"][0]["render"][0]["primitive"] == "say"


def test_load_episode_redact_mode_scrubs_matched_text_in_returned_episode(tmp_path, demo_primitives):
    """on_match: redact must actually rewrite the offending substring in
    the dict load_episode returns -- validate_episode stays pure/Issues
    only (see _apply_redaction's docstring for why the rewrite lives
    here instead)."""
    rules = base_rules()
    rules["redaction"] = {
        "on_match": "redact",
        "patterns": [{"name": "password", "regex": r"(?i)\bpassword\s*[:=]\s*\S+"}],
    }
    rules_path = tmp_path / "validation.yaml"
    rules_path.write_text(yaml.safe_dump(rules), encoding="utf-8")
    episode_path = tmp_path / "ep.json"
    episode_path.write_text(json.dumps(make_episode([
        make_scene(text="startup used password: hunter2 to connect"),
    ])), encoding="utf-8")
    result = load_episode(episode_path, primitives=demo_primitives, rules=load_rules(rules_path),
                           campaign="demo")
    assert "hunter2" not in result["scenes"][0]["text"]
    assert "[redacted:password]" in result["scenes"][0]["text"]


# ── Issue / EpisodeError shape ─────────────────────────────────────────────

def test_issue_scene_index_defaults_to_none():
    issue = Issue(level="warn", rule="x", message="y")
    assert issue.scene_index is None


def test_episode_error_is_an_exception():
    assert issubclass(EpisodeError, Exception)
