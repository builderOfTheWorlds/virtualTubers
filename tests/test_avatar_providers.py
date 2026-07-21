import pytest

import avatar_providers
from avatar_providers import load_provider
from avatar_providers.base import AvatarProvider
from avatar_providers.builtin import DEFAULT_EXPRESSIONS, BuiltinProvider
from avatar_providers.ascii_avatar import DEFAULT_EXPRESSION_MAP, AsciiAvatarProvider


def test_load_provider_defaults_to_builtin_when_no_provider_key(monkeypatch):
    monkeypatch.delenv("AVATAR_PROVIDER", raising=False)
    provider = load_provider({}, "KODI-7", "Software Engineer")
    assert isinstance(provider, BuiltinProvider)


def test_load_provider_env_overrides_config(monkeypatch):
    # Config asks for ascii_avatar; env should win and give us builtin instead.
    monkeypatch.setenv("AVATAR_PROVIDER", "builtin")
    provider = load_provider({"provider": "ascii_avatar"}, "KODI-7", "Software Engineer")
    assert isinstance(provider, BuiltinProvider)


def test_load_provider_unknown_name_falls_back_to_builtin(monkeypatch):
    monkeypatch.delenv("AVATAR_PROVIDER", raising=False)
    provider = load_provider({"provider": "nonexistent_thing"}, "KODI-7", "Software Engineer")
    assert isinstance(provider, BuiltinProvider)


def test_load_provider_construction_failure_falls_back_to_builtin(monkeypatch):
    class BrokenProvider(AvatarProvider):
        def __init__(self, avatar_config, name, title):
            raise RuntimeError("boom")

    monkeypatch.delenv("AVATAR_PROVIDER", raising=False)
    monkeypatch.setitem(avatar_providers.PROVIDERS, "broken", lambda: BrokenProvider)

    provider = load_provider({"provider": "broken"}, "KODI-7", "Software Engineer")
    assert isinstance(provider, BuiltinProvider)


def test_builtin_provider_honors_custom_expressions_config():
    custom_expressions = {"idle": {"eyes": "^  ^", "mouth": "-----"}}
    provider = BuiltinProvider({"expressions": custom_expressions}, "KODI-7", "Software Engineer")
    assert provider.expressions == custom_expressions
    assert provider.expressions != DEFAULT_EXPRESSIONS


def test_builtin_provider_defaults_to_default_expressions_when_unconfigured():
    provider = BuiltinProvider({}, "KODI-7", "Software Engineer")
    assert provider.expressions == DEFAULT_EXPRESSIONS


def test_builtin_provider_alternates_talk_mouth_while_bubble_shown(capsys):
    provider = BuiltinProvider({}, "KODI-7", "Software Engineer")
    provider.render_tick("speaking", ["hello"])
    first = capsys.readouterr().out
    provider.render_tick("speaking", ["hello"])
    second = capsys.readouterr().out
    assert DEFAULT_EXPRESSIONS["speaking"]["talk_mouth"] in first
    assert DEFAULT_EXPRESSIONS["speaking"]["mouth"] in second
    assert first != second


def test_builtin_provider_holds_static_mouth_without_a_bubble(capsys):
    provider = BuiltinProvider({}, "KODI-7", "Software Engineer")
    provider.render_tick("speaking", None)
    first = capsys.readouterr().out
    provider.render_tick("speaking", None)
    second = capsys.readouterr().out
    assert first == second
    assert DEFAULT_EXPRESSIONS["speaking"]["mouth"] in first


def test_builtin_provider_expression_without_talk_mouth_stays_static(capsys):
    # "idle" has no talk_mouth entry — a bubble showing alongside it (not a
    # real code path today, but the fallback must not crash) keeps one glyph.
    provider = BuiltinProvider({}, "KODI-7", "Software Engineer")
    provider.render_tick("idle", ["hello"])
    first = capsys.readouterr().out
    provider.render_tick("idle", ["hello"])
    second = capsys.readouterr().out
    assert first == second


def test_ascii_avatar_default_expression_map_matches_our_seven_expressions():
    assert DEFAULT_EXPRESSION_MAP == {
        "idle": "idle",
        "thinking": "thinking",
        "typing": "thinking",
        "focused": "thinking",
        "speaking": "speaking",
        "happy": "speaking",
        "frustrated": "error",
    }


def test_ascii_avatar_expression_map_override_merges_over_default():
    # blessed degrades to a no-op when stdout isn't a real tty (does_styling
    # is False), so constructing the provider here never touches a real
    # terminal — see avatar_providers/ascii_avatar.py's module docstring.
    provider = AsciiAvatarProvider(
        {"expression_map": {"happy": "listening"}}, "KODI-7", "Software Engineer",
    )
    assert provider.expression_map["happy"] == "listening"
    # Everything else in the default map should survive the merge untouched.
    assert provider.expression_map["idle"] == "idle"
    assert provider.expression_map["frustrated"] == "error"
    assert provider.expression_map["thinking"] == "thinking"


def test_ascii_avatar_expression_map_defaults_when_unconfigured():
    provider = AsciiAvatarProvider({}, "KODI-7", "Software Engineer")
    assert provider.expression_map == DEFAULT_EXPRESSION_MAP
