"""Tests for symbolic-voice-registry resolution (roundtable_stream_design.md
v1.1 §7.2 / WP-2): TTSClient.voice_for's three-layer precedence and
revoice.show_bindings' tolerant show-header parsing.

The registry is FAKED in every test — either a tmp_path YAML loaded through
voice_registry.load(path, force=True) or a monkeypatched is_known/resolve pair.
Nothing here asserts against the real config/voices.yaml, which may legitimately
gain entries later. No real synthesis either: TTSClient is always built on the
`fake` provider, so no .onnx is ever loaded.
"""
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import revoice  # noqa: E402
import tts_client  # noqa: E402
import voice_registry  # noqa: E402
from tts_client import TTSClient  # noqa: E402


# ── Fake registry plumbing ───────────────────────────────────────────────────

FIXTURE = textwrap.dedent(
    """
    version: 1
    voices:
      narrator_warm: {provider: piper, model_path: /data/voices/warm.onnx}
      alto_bright:   {provider: piper, model_path: /data/voices/bright.onnx}
      cloud_alto:    {provider: elevenlabs, voice_id: EL-1234}
    """
)


@pytest.fixture
def fake_registry(tmp_path, monkeypatch):
    """Point the module-level registry seam at a throwaway YAML.

    Patching is_known/resolve (rather than VOICE_REGISTRY_PATH) keeps the real
    registry's module cache untouched, so test order can never leak a fake
    registry into another suite.
    """
    path = tmp_path / "voices.yaml"
    path.write_text(FIXTURE, encoding="utf-8")
    voices = voice_registry.load(str(path), force=True)
    assert set(voices) == {"narrator_warm", "alto_bright", "cloud_alto"}
    monkeypatch.setattr(voice_registry, "is_known",
                        lambda name, p=None: name in voices)
    monkeypatch.setattr(voice_registry, "resolve",
                        lambda name, p=None: dict(voices[name]) if name in voices else None)
    return voices


BASE_CONFIG = {
    "provider": "fake",          # never touches piper / the filesystem
    "rate": 1.1,                 # a non-speakers base key that must survive
    "model_path": "/data/voices/base.onnx",
    "speakers": {
        "boss": {"model_path": "/data/voices/boss.onnx"},
        "coder": {"model_path": "/data/voices/coder.onnx", "length_scale": 0.9},
    },
}


def client(config=None):
    return TTSClient(dict(config or BASE_CONFIG))


# ── voice_for precedence (§7.2) ──────────────────────────────────────────────

def test_registry_voice_wins_over_slot_config(fake_registry):
    merged = client().voice_for("boss", "alto_bright")
    assert merged["model_path"] == "/data/voices/bright.onnx"
    assert merged["provider"] == "piper"   # the registry fragment's provider


def test_slot_config_wins_when_no_voice_name_is_passed(fake_registry):
    assert client().voice_for("boss")["model_path"] == "/data/voices/boss.onnx"


def test_base_config_used_when_neither_applies(fake_registry):
    """A speaker with no slot entry and no cast voice falls all the way
    through to the base config."""
    merged = client().voice_for("tuber_5")
    assert merged["model_path"] == "/data/voices/base.onnx"
    assert merged["rate"] == 1.1


def test_unknown_voice_name_falls_back_to_slot_config(fake_registry):
    """A stale registry on a worker must not kill the show: an unknown name
    degrades to today's slot config rather than raising or blanking."""
    merged = client().voice_for("coder", "no_such_voice")
    assert merged["model_path"] == "/data/voices/coder.onnx"
    assert merged["length_scale"] == 0.9


def test_empty_voice_name_is_treated_as_absent(fake_registry):
    assert client().voice_for("boss", "") == client().voice_for("boss")
    assert client().voice_for("boss", None) == client().voice_for("boss")


# ── Backwards compatibility (§7.4) ───────────────────────────────────────────

@pytest.mark.parametrize("speaker", ["boss", "coder", "tuber_0", "unknown"])
def test_voice_for_without_voice_name_matches_pre_change_behaviour(speaker, fake_registry):
    """The pre-registry implementation, written out explicitly: base minus
    'speakers', with the slot entry merged over it."""
    expected = {k: v for k, v in BASE_CONFIG.items() if k != "speakers"}
    expected.update(BASE_CONFIG["speakers"].get(speaker) or {})
    assert client().voice_for(speaker) == expected


def test_speakers_key_never_leaks_and_base_keys_survive(fake_registry):
    for merged in (client().voice_for("coder"),
                   client().voice_for("coder", "alto_bright"),
                   client().voice_for("coder", "bogus")):
        assert "speakers" not in merged
        assert merged["rate"] == 1.1


def test_resolved_fragment_is_not_mutated_by_the_merge(fake_registry):
    """voice_for must never write back into the registry's fragment — a second
    resolution has to see the pristine entry."""
    before = dict(fake_registry["alto_bright"])
    client().voice_for("coder", "alto_bright")["model_path"] = "/tmp/clobbered.onnx"
    assert voice_registry.resolve("alto_bright") == before


# ── Provider agnosticism ─────────────────────────────────────────────────────

def test_cloud_entry_with_voice_id_and_no_model_path_resolves(fake_registry):
    """Registry fragments are provider-agnostic: a cloud entry carries
    voice_id, and nothing in voice_for may assume piper or an extension."""
    merged = client().voice_for("boss", "cloud_alto")
    assert merged["provider"] == "elevenlabs"
    assert merged["voice_id"] == "EL-1234"
    # The base model_path is still present (it was never a 'speakers' key), but
    # the cast voice's own identifying field is what the backend will read.
    assert "voice_id" in merged


def test_synthesize_passes_voice_name_through_to_voice_for(tmp_path, fake_registry, monkeypatch):
    seen = {}

    def spy_backend(text, out_wav, voice_cfg):
        seen.update(voice_cfg)
        tts_client._fake(text, out_wav, voice_cfg)

    tts = client()
    monkeypatch.setattr(tts, "_backend", spy_backend)
    tts.synthesize("hello there", tmp_path / "a.wav", speaker="boss",
                   voice_name="alto_bright")
    assert seen["model_path"] == "/data/voices/bright.onnx"


def test_synthesize_without_voice_name_uses_slot_config(tmp_path, fake_registry, monkeypatch):
    seen = {}
    tts = client()
    monkeypatch.setattr(tts, "_backend",
                        lambda text, out, cfg: (seen.update(cfg),
                                                tts_client._fake(text, out, cfg)))
    tts.synthesize("hello there", tmp_path / "b.wav", speaker="boss")
    assert seen["model_path"] == "/data/voices/boss.onnx"


# ── revoice.show_bindings (§7.1) ─────────────────────────────────────────────

HEADER_SCRIPT = {
    "show": {
        "title": "The Malvakar Riddle — Act 1",
        "slots": ["tuber_0", "tuber_1"],
        "persona": {
            "tuber_0": {"name": "The Chronicler", "voice": "narrator_warm"},
            "tuber_1": {"name": "Alcinoe", "voice": "alto_bright"},
        },
    },
    "events": [],
}


def test_show_bindings_extracts_both_maps():
    names, voices = revoice.show_bindings(HEADER_SCRIPT)
    assert names == {"tuber_0": "The Chronicler", "tuber_1": "Alcinoe"}
    assert voices == {"tuber_0": "narrator_warm", "tuber_1": "alto_bright"}


@pytest.mark.parametrize("script", [
    {},                                   # no show block at all (a replay)
    {"show": None},
    {"show": "roundtable"},               # non-dict header
    {"show": {"persona": None}},          # header with no persona
    {"show": {"persona": ["tuber_0"]}},   # persona isn't a mapping
    {"show": {"title": "x"}},
    None,
])
def test_show_bindings_is_tolerant_of_missing_or_malformed_headers(script):
    assert revoice.show_bindings(script) == ({}, {})


def test_show_bindings_skips_malformed_persona_entries():
    names, voices = revoice.show_bindings({"show": {"persona": {
        "tuber_0": "Alcinoe",                  # scalar — malformed, skipped
        "tuber_1": {"name": "Vance"},          # name only, no voice
        "tuber_2": {"voice": "tenor_low"},     # voice only, no name
    }}})
    assert names == {"tuber_1": "Vance"}
    assert voices == {"tuber_2": "tenor_low"}


# ── Threading through prepare_show ───────────────────────────────────────────

class RecordingTTS:
    """Accepts the new kwarg and records every synthesize call."""

    def __init__(self):
        self.calls = []

    def synthesize(self, text, out_wav, speaker="coder", voice_name=None):
        self.calls.append({"speaker": speaker, "voice_name": voice_name})

        class _N:
            audio_path = Path(out_wav)
            duration = 1.0
        return _N()


class LegacyTTS:
    """A pre-v1.1 duck-typed client with no `voice_name` parameter."""

    def __init__(self):
        self.calls = []

    def synthesize(self, text, out_wav, speaker="coder"):
        self.calls.append(speaker)

        class _N:
            audio_path = Path(out_wav)
            duration = 1.0
        return _N()


class StubLLM:
    def complete(self, system_prompt, messages):
        self.prompt = messages[0]["content"]
        return "A spoken line."


def script_with_header():
    return {
        "show": {"persona": {
            "tuber_0": {"name": "The Chronicler", "voice": "narrator_warm"},
        }},
        "events": [{"type": "assistant_text", "text": "Hello.", "speaker": "tuber_0"}],
    }


def test_prepare_show_passes_the_cast_voice_name(tmp_path):
    tts = RecordingTTS()
    revoice.prepare_show(script_with_header(), StubLLM(), tts, tmp_path)
    assert tts.calls == [{"speaker": "tuber_0", "voice_name": "narrator_warm"}]


def test_prepare_show_without_a_header_keeps_todays_call(tmp_path):
    """No header means no voice_name kwarg at all, so pre-v1.1 TTS clients
    keep working unchanged."""
    tts = LegacyTTS()
    script = {"events": [{"type": "assistant_text", "text": "Hi.", "speaker": "coder"}]}
    show = revoice.prepare_show(script, StubLLM(), tts, tmp_path)
    assert tts.calls == ["coder"]
    assert show[0]["audio"] is not None


def test_explicit_voice_names_argument_overrides_the_header(tmp_path):
    tts = RecordingTTS()
    revoice.prepare_show(script_with_header(), StubLLM(), tts, tmp_path,
                         voice_names={"tuber_0": "alto_bright"})
    assert tts.calls[0]["voice_name"] == "alto_bright"


def test_persona_display_name_overrides_speaker_names_config(tmp_path):
    """§14 risk row 2: the show header's persona name is authoritative."""
    llm = StubLLM()
    revoice.prepare_show(script_with_header(), llm, None, tmp_path,
                         speaker_names={"tuber_0": "Config Name"})
    assert "The Chronicler" in llm.prompt
    assert "Config Name" not in llm.prompt


def test_speaker_names_config_still_applies_for_uncast_slots(tmp_path):
    llm = StubLLM()
    script = script_with_header()
    script["events"] = [{"type": "assistant_text", "text": "Hi.", "speaker": "tuber_9"}]
    revoice.prepare_show(script, llm, None, tmp_path,
                         speaker_names={"tuber_9": "TESS-3"})
    assert "TESS-3" in llm.prompt
