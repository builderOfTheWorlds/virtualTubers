import json

import pytest

from avatar import (
    build_avatar_from_persona,
    maybe_update_avatar,
    persona_identity,
    read_persona_file,
    resolve_display,
    wrap_bubble,
    STALE_AFTER_S,
)


def test_resolve_display_no_state_is_idle_with_no_bubble():
    expression, bubble = resolve_display(None, now=1000.0, bubble_duration_s=6)
    assert expression == "idle"
    assert bubble is None


def test_resolve_display_fresh_bubble_is_shown():
    state = {"expression": "speaking", "bubble": "Fixing the bug now.", "updated_at": 995.0}
    expression, bubble = resolve_display(state, now=1000.0, bubble_duration_s=6)
    assert expression == "speaking"
    assert bubble == "Fixing the bug now."


def test_resolve_display_expired_bubble_reverts_speaking_to_idle():
    state = {"expression": "speaking", "bubble": "Fixing the bug now.", "updated_at": 990.0}
    expression, bubble = resolve_display(state, now=1000.0, bubble_duration_s=6)
    assert expression == "idle"
    assert bubble is None


def test_resolve_display_expired_bubble_reverts_frustrated_to_idle():
    state = {"expression": "frustrated", "bubble": "Ugh, this broke.", "updated_at": 990.0}
    expression, bubble = resolve_display(state, now=1000.0, bubble_duration_s=6)
    assert expression == "idle"
    assert bubble is None


def test_resolve_display_thinking_without_bubble_persists_while_fresh():
    state = {"expression": "thinking", "bubble": None, "updated_at": 999.0}
    expression, bubble = resolve_display(state, now=1000.0, bubble_duration_s=6)
    assert expression == "thinking"
    assert bubble is None


def test_resolve_display_stale_bubbleless_state_falls_back_to_idle():
    state = {"expression": "thinking", "bubble": None, "updated_at": 1000.0 - STALE_AFTER_S - 1}
    expression, bubble = resolve_display(state, now=1000.0, bubble_duration_s=6)
    assert expression == "idle"


def test_wrap_bubble_empty_text_returns_empty_list():
    assert wrap_bubble("", width=32) == []
    assert wrap_bubble(None, width=32) == []


def test_wrap_bubble_wraps_long_text_to_width():
    text = "This function needs more error handling before we ship it."
    lines = wrap_bubble(text, width=20)
    assert len(lines) > 1
    assert all(len(line) <= 20 for line in lines)


def test_wrap_bubble_short_text_is_single_line():
    assert wrap_bubble("On it.", width=32) == ["On it."]


# ── Persona relay-file polling (CONTRACT.md §8) ────────────────────────────
# "No mechanism exists to extend" (recon_platform.md §7) -- avatar identity
# was 100% fixed at boot before this; every test below is new code.

def _persona_doc(**overrides):
    doc = {
        "campaign": "coder", "speaker": "coder", "name": "KODI-7", "title": "Software Engineer",
        "avatar": {"provider": "builtin", "name": "KODI-7", "title": "Software Engineer",
                  "expressions": {"idle": {"eyes": "◉  ◉", "mouth": "╰───╯"}}},
    }
    doc.update(overrides)
    return doc


def test_read_persona_file_missing_returns_none(tmp_path):
    assert read_persona_file(tmp_path / "nope.json") is None


def test_read_persona_file_malformed_returns_none(tmp_path):
    path = tmp_path / "persona.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert read_persona_file(path) is None


def test_read_persona_file_non_object_returns_none(tmp_path):
    path = tmp_path / "persona.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert read_persona_file(path) is None


def test_read_persona_file_returns_parsed_dict(tmp_path):
    path = tmp_path / "persona.json"
    path.write_text(json.dumps(_persona_doc()), encoding="utf-8")
    assert read_persona_file(path) == _persona_doc()


def test_persona_identity_none_for_falsy_doc():
    assert persona_identity(None) is None
    assert persona_identity({}) is None


def test_persona_identity_is_campaign_speaker_tuple():
    assert persona_identity(_persona_doc()) == ("coder", "coder")


def test_build_avatar_from_persona_uses_persona_avatar_config_and_name():
    avatar_config, name, title = build_avatar_from_persona(_persona_doc(), "WORKER", "Agent")
    assert avatar_config == _persona_doc()["avatar"]
    assert name == "KODI-7"
    assert title == "Software Engineer"


def test_build_avatar_from_persona_falls_back_to_current_name_title_when_absent():
    doc = _persona_doc(name=None, title=None)
    avatar_config, name, title = build_avatar_from_persona(doc, "WORKER", "Agent")
    # avatar.name/title still present in the nested avatar config -> used first
    assert name == "KODI-7"
    assert title == "Software Engineer"


def test_build_avatar_from_persona_falls_back_all_the_way_when_avatar_has_no_name(tmp_path):
    doc = _persona_doc(name=None, title=None)
    doc["avatar"] = {"provider": "builtin"}  # no name/title in the avatar block either
    avatar_config, name, title = build_avatar_from_persona(doc, "WORKER", "Agent")
    assert name == "WORKER"
    assert title == "Agent"


def test_build_avatar_from_persona_raises_when_avatar_is_not_a_dict():
    doc = _persona_doc(avatar="not-a-dict")
    with pytest.raises(ValueError):
        build_avatar_from_persona(doc, "WORKER", "Agent")


def test_build_avatar_from_persona_raises_when_avatar_is_missing():
    doc = _persona_doc()
    del doc["avatar"]
    with pytest.raises(ValueError):
        build_avatar_from_persona(doc, "WORKER", "Agent")


# ── maybe_update_avatar: the tick-level integration point ──────────────────

def test_maybe_update_avatar_does_nothing_when_no_persona_file(tmp_path):
    provider, avatar_config, name, title, identity = maybe_update_avatar(
        tmp_path / "nope.json", None, {"provider": "builtin"}, "WORKER", "Agent")
    assert provider is None
    assert avatar_config == {"provider": "builtin"}
    assert name == "WORKER"
    assert title == "Agent"
    assert identity is None


def test_maybe_update_avatar_does_nothing_when_identity_unchanged(tmp_path):
    path = tmp_path / "persona.json"
    path.write_text(json.dumps(_persona_doc()), encoding="utf-8")

    provider, avatar_config, name, title, identity = maybe_update_avatar(
        path, ("coder", "coder"), {"provider": "builtin"}, "WORKER", "Agent")

    assert provider is None  # already up to date -- no rebuild
    assert identity == ("coder", "coder")


def test_maybe_update_avatar_rebuilds_provider_on_persona_change(tmp_path):
    path = tmp_path / "persona.json"
    path.write_text(json.dumps(_persona_doc()), encoding="utf-8")

    provider, avatar_config, name, title, identity = maybe_update_avatar(
        path, None, {"provider": "builtin"}, "WORKER", "Agent")

    assert provider is not None
    assert hasattr(provider, "render_tick")  # a real, working AvatarProvider
    assert name == "KODI-7"
    assert title == "Software Engineer"
    assert avatar_config == _persona_doc()["avatar"]
    assert identity == ("coder", "coder")


def test_maybe_update_avatar_keeps_current_face_on_malformed_persona(tmp_path, capsys):
    """CONTRACT.md §8: ANY failure keeps the current face -- the avatar
    pane's only job is to stay up. A persona doc with no usable `avatar`
    section must not touch the currently-running provider/name/title at
    all."""
    path = tmp_path / "persona.json"
    path.write_text(json.dumps(_persona_doc(avatar="not-a-dict")), encoding="utf-8")
    current_avatar_config = {"provider": "builtin", "name": "STAYS"}

    provider, avatar_config, name, title, identity = maybe_update_avatar(
        path, None, current_avatar_config, "STAYS", "Still Here")

    assert provider is None
    assert avatar_config is current_avatar_config
    assert name == "STAYS"
    assert title == "Still Here"
    assert identity is None  # not advanced -- a later, valid persona must still trigger a rebuild
    assert "persona update failed" in capsys.readouterr().err


def test_maybe_update_avatar_survives_completely_corrupt_relay_file(tmp_path):
    """A torn/half-written relay file (a concurrent writer mid-os.replace,
    or garbage) must never crash the avatar pane -- it degrades to "nothing
    new", same as a missing file."""
    path = tmp_path / "persona.json"
    path.write_text("{not json at all", encoding="utf-8")

    provider, avatar_config, name, title, identity = maybe_update_avatar(
        path, None, {"provider": "builtin"}, "WORKER", "Agent")

    assert provider is None
    assert identity is None


def test_maybe_update_avatar_survives_load_provider_raising(tmp_path, monkeypatch):
    """Belt-and-braces: even though avatar_providers.load_provider is
    documented to never raise (it falls back to builtin internally), this
    wraps the whole rebuild attempt in a broad except so a hypothetical
    future regression there still can't take the avatar pane down."""
    path = tmp_path / "persona.json"
    path.write_text(json.dumps(_persona_doc()), encoding="utf-8")

    import avatar_providers

    def explode(*a, **kw):
        raise RuntimeError("unexpected provider construction failure")

    monkeypatch.setattr(avatar_providers, "load_provider", explode)
    current_avatar_config = {"provider": "builtin"}

    provider, avatar_config, name, title, identity = maybe_update_avatar(
        path, None, current_avatar_config, "WORKER", "Agent")

    assert provider is None
    assert avatar_config is current_avatar_config
    assert name == "WORKER"
    assert identity is None
