"""Tests for app/campaign/renderer.py — turning beats into a performance.

The renderer is where a scene becomes something an audience sees and hears. It
composes pieces that already exist and are already tested elsewhere: Pacer for
rhythm, Palette for colour, agent_state for the avatar, tmux_control for panes,
and the TTS client for voices.

Every one of those channels is INJECTED and OPTIONAL, which is the whole design.
With none of them supplied the renderer writes plain text to a stream and
nothing else happens — that is `--dry-run`, and it is the surface almost all of
this is verified through. It also means a dead TTS backend or a missing tmux
pane degrades the show instead of ending it.
"""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from campaign.pack import Beat, CampaignPack, CastMember, Scene  # noqa: E402
from campaign.renderer import (  # noqa: E402
    RenderedBeat, RendererError, SceneRenderer,
)
from replay import Pacer, Palette  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────
def make_pack(scenes=(), primitives=("roll_check", "attack")):
    cast = {
        "gm": CastMember(id="gm", name="The Narrator", role="gm", voice="narrator"),
        "buffalo": CastMember(id="buffalo", name="Buffalo", role="player"),
        "helen": CastMember(id="helen", name="Helen", role="player"),
    }
    return CampaignPack(
        name="testpack", title="Test", genre="fantasy", start_scene="opening",
        gm_id="gm", player_ids=["buffalo", "helen"], primitives=list(primitives),
        theme={}, cast=cast, scenes={s.id: s for s in scenes},
        root=Path("/nonexistent"), lore_dir=None,
    )


def build(pack=None, **kwargs):
    """A renderer wired for tests: instant pacing, no colour, captured output."""
    kwargs.setdefault("out", io.StringIO())
    kwargs.setdefault("pacer", Pacer(enabled=False))
    kwargs.setdefault("palette", Palette(enabled=False))
    return SceneRenderer(pack or make_pack(), **kwargs)


def narration(text="The hall falls silent."):
    return Beat(kind="narration", speaker="gm", text=text)


# ── defaults ─────────────────────────────────────────────────────────────────
# Every other test injects all of these, so the bare constructor is the one
# path the rest of the suite cannot reach — and it is the path production uses.
def test_renderer_constructs_with_only_a_pack():
    assert SceneRenderer(make_pack()) is not None


def test_output_defaults_to_stdout():
    assert SceneRenderer(make_pack()).out is sys.stdout


def test_a_silent_renderer_allocates_no_audio_directory(tmp_path, monkeypatch):
    """A dry run must not litter temp directories it will never write to."""
    def explode(*args, **kwargs):
        raise AssertionError("no temp dir should be created without TTS")

    monkeypatch.setattr("tempfile.mkdtemp", explode)

    assert SceneRenderer(make_pack()).audio_dir is None


# ── plain rendering ──────────────────────────────────────────────────────────
def test_narration_reaches_the_output():
    renderer = build()
    renderer.render_beat(narration("The hall falls silent."))

    assert "The hall falls silent." in renderer.out.getvalue()


def test_dialogue_is_labelled_with_the_display_name():
    renderer = build()
    renderer.render_beat(Beat(kind="dialogue", speaker="buffalo", text="I don't like this."))
    output = renderer.out.getvalue()

    assert "Buffalo" in output and "I don't like this." in output


def test_dialogue_uses_the_cast_name_not_the_id():
    renderer = build()
    renderer.render_beat(Beat(kind="dialogue", speaker="gm", text="Welcome."))

    assert "The Narrator" in renderer.out.getvalue()


def test_action_beat_renders_through_the_primitive():
    renderer = build()
    renderer.render_beat(Beat(kind="action", speaker="buffalo",
                              primitive="roll_check", params={"skill": "Perception"}))
    output = renderer.out.getvalue()

    assert "Buffalo" in output and "Perception" in output


def test_action_beat_uses_the_display_name_as_the_actor():
    renderer = build()
    renderer.render_beat(Beat(kind="action", speaker="helen",
                              primitive="attack", params={"target": "the raider"}))

    assert "Helen attacks the raider" in renderer.out.getvalue()


def test_pane_beat_emits_no_narration():
    renderer = build()
    renderer.render_beat(Beat(kind="pane", show="map"))

    assert renderer.out.getvalue().strip() == ""


# ── the RenderedBeat record ──────────────────────────────────────────────────
def test_render_beat_returns_a_record_of_what_was_performed():
    renderer = build()
    result = renderer.render_beat(narration("A door opens."))

    assert isinstance(result, RenderedBeat)
    assert result.kind == "narration"
    assert result.speaker == "gm"
    assert result.text == "A door opens."


def test_rendered_action_text_is_the_expanded_sentence():
    renderer = build()
    result = renderer.render_beat(Beat(kind="action", speaker="buffalo",
                                       primitive="attack", params={"target": "the door"}))

    assert result.text == "Buffalo attacks the door."


def test_rendered_pane_beat_carries_no_text():
    renderer = build()
    result = renderer.render_beat(Beat(kind="pane", show="map"))

    assert result.kind == "pane"
    assert not result.text


# ── scenes ───────────────────────────────────────────────────────────────────
def test_render_scene_returns_one_record_per_beat():
    scene = Scene(id="opening", beats=[narration("One."), narration("Two.")])
    results = build().render_scene(scene)

    assert [r.text for r in results] == ["One.", "Two."]


def test_render_scene_performs_beats_in_order():
    scene = Scene(id="opening", beats=[narration("First."), narration("Second.")])
    renderer = build()
    renderer.render_scene(scene)
    output = renderer.out.getvalue()

    assert output.index("First.") < output.index("Second.")


def test_enter_narration_plays_before_the_beats():
    scene = Scene(id="opening", enter_narration="Rain on the roof.",
                  beats=[narration("Then a knock.")])
    renderer = build()
    renderer.render_scene(scene)
    output = renderer.out.getvalue()

    assert output.index("Rain on the roof.") < output.index("Then a knock.")


def test_enter_narration_is_attributed_to_the_gm():
    scene = Scene(id="opening", enter_narration="Rain on the roof.", beats=[])
    results = build().render_scene(scene)

    assert [(r.speaker, r.text) for r in results] == [("gm", "Rain on the roof.")]


def test_render_scene_with_no_beats_is_not_an_error():
    assert build().render_scene(Scene(id="opening", beats=[])) == []


# ── failure modes ────────────────────────────────────────────────────────────
def test_unknown_speaker_names_the_speaker():
    renderer = build()

    with pytest.raises(RendererError, match="ghost"):
        renderer.render_beat(Beat(kind="dialogue", speaker="ghost", text="boo"))


def test_unknown_primitive_names_the_primitive():
    renderer = build()

    with pytest.raises(RendererError, match="fireball"):
        renderer.render_beat(Beat(kind="action", speaker="helen", primitive="fireball"))


def test_primitive_not_enabled_for_this_pack_is_refused():
    """cast_spell is a real primitive, but this pack never enabled it."""
    renderer = build(make_pack(primitives=("roll_check",)))

    with pytest.raises(RendererError, match="attack"):
        renderer.render_beat(Beat(kind="action", speaker="helen",
                                  primitive="attack", params={"target": "x"}))


def test_bad_primitive_params_are_reported_as_a_render_error():
    renderer = build()

    with pytest.raises(RendererError):
        renderer.render_beat(Beat(kind="action", speaker="helen", primitive="attack"))


def test_unknown_beat_kind_names_the_kind():
    renderer = build()

    with pytest.raises(RendererError, match="interpretive_dance"):
        renderer.render_beat(Beat(kind="interpretive_dance"))


# ── avatar state ─────────────────────────────────────────────────────────────
def test_avatar_state_is_written_when_a_path_is_given(tmp_path):
    state_path = tmp_path / "state.json"
    renderer = build(state_path=str(state_path))
    renderer.render_beat(Beat(kind="dialogue", speaker="buffalo", text="Hello."))

    assert json.loads(state_path.read_text())["bubble"] == "Hello."


def test_avatar_state_is_skipped_when_no_path_is_given():
    """The dry-run path must not touch the filesystem."""
    renderer = build()
    renderer.render_beat(narration())

    assert renderer.state_path is None


# ── panes ────────────────────────────────────────────────────────────────────
def test_pane_beat_calls_the_pane_hook():
    seen = []
    renderer = build(pane_control=seen.append)
    renderer.render_beat(Beat(kind="pane", show="map"))

    assert seen == ["map"]


def test_non_pane_beats_do_not_touch_panes():
    seen = []
    renderer = build(pane_control=seen.append)
    renderer.render_beat(narration())

    assert seen == []


def test_a_failing_pane_hook_does_not_stop_the_show():
    def explode(name):
        raise RuntimeError("no tmux here")

    renderer = build(pane_control=explode)
    scene = Scene(id="opening", beats=[Beat(kind="pane", show="map"),
                                       narration("The show goes on.")])
    renderer.render_scene(scene)

    assert "The show goes on." in renderer.out.getvalue()


# ── voice ────────────────────────────────────────────────────────────────────
class FakeTTS:
    """Stands in for TTSClient: records calls, reports a fixed duration."""

    def __init__(self, duration=2.0, fail=False):
        self.duration = duration
        self.fail = fail
        self.calls = []

    def synthesize(self, text, out_wav, speaker="coder"):
        self.calls.append((text, speaker))
        if self.fail:
            raise RuntimeError("piper is down")
        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
        Path(out_wav).write_bytes(b"RIFF")
        return type("Narration", (), {"audio_path": Path(out_wav),
                                      "duration": self.duration})()


def test_speech_is_synthesized_when_a_tts_client_is_given(tmp_path):
    tts = FakeTTS()
    renderer = build(tts=tts, audio_dir=tmp_path)
    renderer.render_beat(Beat(kind="dialogue", speaker="buffalo", text="Hello."))

    assert tts.calls == [("Hello.", "buffalo")]


def test_pane_beats_are_never_spoken(tmp_path):
    tts = FakeTTS()
    renderer = build(tts=tts, audio_dir=tmp_path)
    renderer.render_beat(Beat(kind="pane", show="map"))

    assert tts.calls == []


def test_a_failing_tts_backend_does_not_stop_the_show(tmp_path):
    renderer = build(tts=FakeTTS(fail=True), audio_dir=tmp_path)
    result = renderer.render_beat(narration("Still narrated."))

    assert "Still narrated." in renderer.out.getvalue()
    assert result.audio is None


def test_synthesized_audio_is_handed_to_the_player(tmp_path):
    played = []
    renderer = build(tts=FakeTTS(), audio_dir=tmp_path, audio_player=played.append)
    renderer.render_beat(narration("Speak."))

    assert len(played) == 1


def test_no_player_means_no_playback_but_still_a_record(tmp_path):
    renderer = build(tts=FakeTTS(), audio_dir=tmp_path)
    result = renderer.render_beat(narration("Speak."))

    assert result.audio is not None


def test_without_a_tts_client_nothing_is_spoken():
    result = build().render_beat(narration("Silent."))

    assert result.audio is None


# ── improv seam ──────────────────────────────────────────────────────────────
def test_improv_beats_are_routed_through_the_improviser():
    def improviser(beat, member):
        return f"{member.name} riffs on: {beat.text}"

    renderer = build(improviser=improviser)
    result = renderer.render_beat(
        Beat(kind="dialogue", speaker="helen", text="We should run.", improv=True))

    assert result.text == "Helen riffs on: We should run."


def test_non_improv_beats_bypass_the_improviser():
    def explode(beat, member):
        raise AssertionError("should not be called")

    renderer = build(improviser=explode)
    result = renderer.render_beat(
        Beat(kind="dialogue", speaker="helen", text="Scripted.", improv=False))

    assert result.text == "Scripted."


def test_improv_without_an_improviser_falls_back_to_the_script():
    """Scripted text is the intent, so it is also the safe default."""
    result = build().render_beat(
        Beat(kind="dialogue", speaker="helen", text="As written.", improv=True))

    assert result.text == "As written."


def test_a_failing_improviser_falls_back_to_the_script(tmp_path):
    def explode(beat, member):
        raise RuntimeError("ollama is busy")

    result = build(improviser=explode).render_beat(
        Beat(kind="dialogue", speaker="helen", text="As written.", improv=True))

    assert result.text == "As written."


def test_an_improviser_returning_nothing_falls_back_to_the_script():
    result = build(improviser=lambda beat, member: "  ").render_beat(
        Beat(kind="dialogue", speaker="helen", text="As written.", improv=True))

    assert result.text == "As written."
