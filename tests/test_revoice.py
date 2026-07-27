"""Tests for app/revoice.py -- the per-airing narration pass that turns a
validated episode's own scenes into voiced scenes. LLM and TTS are fakes
throughout; the contract under test is narration-text selection
(fallback_line's priority chain), sizing, and graceful degradation (the
show must always air). Real coder-campaign primitives/narration config
(config/primitives.yaml + config/campaigns/coder/*.yaml -- already shipped
and covered by tests/test_primitives.py) are used directly rather than
hand-rolled fakes, since they're a stable, already-tested dependency and
using them here gives revoice.py real integration coverage against the
actual recipe/prompt tables it will run against in production.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import primitives  # noqa: E402
import revoice  # noqa: E402
from revoice import (  # noqa: E402
    MAX_WORDS, MIN_WORDS, SpeakerNames,
    fallback_line, load_narration_config, narrate_scene, prepare_show,
    scene_visual_seconds, target_words,
)

PRIMITIVES = primitives.load_primitives("coder")
NARRATION = revoice.load_narration_config("coder")
NAMES = SpeakerNames(worker_name="KODI-7", boss_name="the boss")


def boss_scene(text="Fix the flaky test please", fallback=""):
    return {"speaker": "boss", "kind": "boss", "text": text, "fallback": fallback,
            "mode": "sequence",
            "render": [{"primitive": "show_boss_message", "payload": {"text": text}}]}


def coder_talk_scene(text="On it, boss.", fallback=""):
    return {"speaker": "coder", "kind": "coder_talk", "text": text, "fallback": fallback,
            "mode": "sequence",
            "render": [{"primitive": "show_coder_line",
                        "payload": {"text": text, "name": "KODI-7"}}]}


def coder_work_scene(text="", fallback=""):
    return {"speaker": "coder", "kind": "coder_work", "text": text, "fallback": fallback,
            "mode": "sequence",
            "render": [
                {"primitive": "show_command",
                 "payload": {"command": "pytest -x", "output": "1 failed"}},
                {"primitive": "show_diff",
                 "payload": {"file": "app/agent.py", "hunks": ["- a", "+ b"]}},
            ]}


def _episode(scenes):
    return {"meta": {"schema": 1, "campaign": "coder", "id": "ep1", "title": "Ep1",
                     "created": "2026-07-26T00:00:00Z"},
            "cast": ["boss", "coder"], "scenes": scenes}


class FakeLLM:
    def __init__(self, reply="A fresh spoken line.", error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def complete(self, system_prompt, messages):
        self.calls.append(messages[0]["content"])
        if self.error:
            raise self.error
        return self.reply


class FakeTTS:
    def __init__(self, duration=2.0, error=None):
        self.duration = duration
        self.error = error

    def synthesize(self, text, out_wav, speaker="coder"):
        if self.error:
            raise self.error

        class Narration:
            audio_path = Path(out_wav)
            duration = self.duration
        return Narration()


# -- deleted API: the event-grouping layer is gone ---------------------------

def test_deleted_symbols_are_gone():
    """CONTRACT.md §5: plan_scenes/_PROMPTS/fallback_narration/
    _scene_material/MAX_SCENE_EVENTS are deleted, not just unused -- a
    generator now emits scene-sized units directly, so there is nothing
    left for revoice.py to group."""
    for name in ("plan_scenes", "_PROMPTS", "fallback_narration",
                "_scene_material", "MAX_SCENE_EVENTS"):
        assert not hasattr(revoice, name), f"revoice.{name} should have been deleted"


# -- load_narration_config ----------------------------------------------------

def test_load_narration_config_reads_coder_campaign_kinds():
    assert set(NARRATION) >= {"boss", "coder_talk", "coder_work"}
    assert "{material}" in NARRATION["coder_work"]["prompt"]
    assert NARRATION["boss"]["fallback_template"] == "{text}"


def test_load_narration_config_missing_campaign_returns_empty(tmp_path):
    config = load_narration_config("no-such-campaign", campaigns_dir=tmp_path)
    assert config == {}


def test_load_narration_config_malformed_yaml_degrades_to_empty(tmp_path):
    """A broken narration.yaml must not take the platform down at load time
    -- the show-must-air rule extends to config, not just runtime."""
    campaign_dir = tmp_path / "broken"
    campaign_dir.mkdir()
    (campaign_dir / "narration.yaml").write_text("{{not: valid: yaml: [", encoding="utf-8")
    config = load_narration_config("broken", campaigns_dir=tmp_path)
    assert config == {}


# -- scene_visual_seconds (thin delegate to primitives.estimate_scene_seconds) -

def test_scene_visual_seconds_delegates_and_scales_with_speed():
    scene = coder_work_scene()
    base = scene_visual_seconds(scene, PRIMITIVES, max_output_lines=24, speed=1.0)
    assert base > 0
    assert scene_visual_seconds(scene, PRIMITIVES, max_output_lines=24, speed=2.0) == pytest.approx(base / 2)


def test_scene_visual_seconds_empty_render_is_zero():
    scene = {"speaker": "coder", "kind": "coder_work", "text": "", "fallback": "",
             "mode": "sequence", "render": []}
    assert scene_visual_seconds(scene, PRIMITIVES) == 0.0


# -- sizing --------------------------------------------------------------------

def test_target_words_scales_with_screen_time_and_clamps():
    assert target_words(0.1) == MIN_WORDS
    assert target_words(10) == 25          # 2.5 words/sec
    assert target_words(10_000) == MAX_WORDS


# -- fallback_line: the priority chain ------------------------------------------

def test_fallback_line_prefers_scenes_own_fallback_text():
    scene = coder_work_scene(fallback="Running the tests, then fixing the bug.")
    line = fallback_line(scene, NARRATION, PRIMITIVES, 40, NAMES)
    assert line == "Running the tests, then fixing the bug."


def test_fallback_line_uses_narration_config_template_when_scene_has_no_fallback():
    scene = coder_work_scene()  # fallback="" -> falls through to the template
    line = fallback_line(scene, NARRATION, PRIMITIVES, 40, NAMES)
    assert line.startswith("Okay —")            # coder_work's fallback_template
    assert "show_command" in line                # {render_summary} content


def test_fallback_line_generic_last_resort_when_kind_has_no_template():
    scene = {"speaker": "coder", "kind": "mystery_kind", "text": "", "fallback": "",
             "mode": "sequence",
             "render": [{"primitive": "show_read", "payload": {"file": "app/agent.py"}}]}
    line = fallback_line(scene, NARRATION, PRIMITIVES, 40, NAMES)
    assert line  # never empty -- must always produce something speakable
    assert "show_read" in line  # falls back to render_summary itself


def test_fallback_line_respects_word_cap():
    scene = boss_scene(fallback="word " * 500)
    line = fallback_line(scene, NARRATION, PRIMITIVES, 10, NAMES)
    assert len(line.split()) <= 11  # 10 words + ellipsis marker


def test_fallback_line_never_raises_on_bad_template(tmp_path):
    """A fallback_template referencing a token this module doesn't supply
    (e.g. {oops}) must degrade to the raw template text, not raise -- this
    IS the last line of defense when the LLM is down."""
    narration_config = {"weird": {"fallback_template": "uh oh {oops}"}}
    scene = {"speaker": "coder", "kind": "weird", "text": "", "fallback": "",
             "mode": "sequence", "render": []}
    line = fallback_line(scene, narration_config, PRIMITIVES, 40, NAMES)
    assert "uh oh" in line


# -- narrate_scene ---------------------------------------------------------------

def test_narrate_scene_uses_llm_line():
    scene = boss_scene()
    llm = FakeLLM(reply="Hey, could you sort out that flaky test for me?")
    line = narrate_scene(scene, llm, 20, NAMES, NARRATION, PRIMITIVES)
    assert line == "Hey, could you sort out that flaky test for me?"
    assert "20" in llm.calls[0]  # the word budget reached the prompt


def test_narrate_scene_falls_back_when_llm_fails():
    scene = boss_scene()
    llm = FakeLLM(error=RuntimeError("ollama down"))
    line = narrate_scene(scene, llm, 20, NAMES, NARRATION, PRIMITIVES)
    assert "Fix the flaky test" in line  # fallback speaks the scene's own text via {text}


def test_narrate_scene_without_llm_uses_fallback():
    scene = coder_work_scene()
    line = narrate_scene(scene, None, 20, NAMES, NARRATION, PRIMITIVES)
    assert line.startswith("Okay —")


def test_narrate_scene_uses_speaker_names_override():
    scene = coder_talk_scene()
    scene["speaker"] = "tester"
    llm = FakeLLM(reply="A fresh spoken line.")
    names = SpeakerNames(worker_name="KODI-7", boss_name="the boss",
                         speaker_names={"tester": "TESS-3"})
    narrate_scene(scene, llm, 20, names, NARRATION, PRIMITIVES)
    assert "TESS-3" in llm.calls[0]


def test_narrate_scene_falls_back_to_raw_speaker_id_when_unmapped():
    scene = coder_talk_scene()
    scene["speaker"] = "coder-native"
    llm = FakeLLM(reply="A fresh spoken line.")
    names = SpeakerNames(worker_name="KODI-7", boss_name="the boss",
                         speaker_names={"tester": "TESS-3"})
    narrate_scene(scene, llm, 20, names, NARRATION, PRIMITIVES)
    assert "coder-native" in llm.calls[0]


def test_narrate_scene_verbatim_reads_full_line_without_llm():
    long_text = ("TESS-3 here, and before anyone asks, no, I have not found "
                 "a bug yet, but the stream just started, give me a minute.")
    scene = coder_talk_scene(text=long_text)
    llm = FakeLLM(reply="A fresh spoken line.")
    line = narrate_scene(scene, llm, 11, NAMES, NARRATION, PRIMITIVES, verbatim=True)
    assert line == long_text
    assert llm.calls == []  # verbatim never calls the LLM when text is non-empty


def test_narrate_scene_verbatim_still_paraphrases_scene_with_empty_text():
    """A scene with NO scripted text (pure visual business, e.g. coder_work
    without a dictated line) always goes through the LLM/fallback path
    regardless of verbatim -- this replaces the old `kind in ("boss",
    "coder_talk")` check now that `kind` is campaign-defined rather than a
    fixed set (CONTRACT.md §5)."""
    scene = coder_work_scene(text="")
    llm = FakeLLM(reply="A fresh spoken line.")
    line = narrate_scene(scene, llm, 20, NAMES, NARRATION, PRIMITIVES, verbatim=True)
    assert line == "A fresh spoken line."
    assert len(llm.calls) == 1


# -- prepare_show -----------------------------------------------------------------

def test_prepare_show_iterates_scenes_directly_with_no_grouping_step(tmp_path):
    episode = _episode([boss_scene(), coder_talk_scene(), coder_work_scene()])
    show = prepare_show(episode, FakeLLM(), FakeTTS(duration=3.0), tmp_path,
                        primitives=PRIMITIVES, narration_config=NARRATION)
    assert len(show) == 3
    assert show is episode["scenes"]  # annotated in place, not copied/regrouped
    for scene in show:
        assert scene["narration"]
        assert scene["audio"].duration == 3.0


def test_prepare_show_tts_failure_leaves_scene_silent(tmp_path):
    episode = _episode([boss_scene(), coder_work_scene()])
    show = prepare_show(episode, FakeLLM(), FakeTTS(error=RuntimeError("no model")), tmp_path,
                        primitives=PRIMITIVES, narration_config=NARRATION)
    assert all(scene["audio"] is None for scene in show)
    assert all(scene["narration"] for scene in show)  # text still there


def test_prepare_show_without_tts_is_narrated_but_silent(tmp_path):
    episode = _episode([boss_scene(), coder_work_scene()])
    show = prepare_show(episode, FakeLLM(), None, tmp_path,
                        primitives=PRIMITIVES, narration_config=NARRATION)
    assert all(scene["audio"] is None for scene in show)


def test_prepare_show_reports_progress(tmp_path):
    messages = []
    episode = _episode([boss_scene(), coder_talk_scene(), coder_work_scene()])
    prepare_show(episode, FakeLLM(), None, tmp_path, primitives=PRIMITIVES,
                narration_config=NARRATION, progress=messages.append)
    assert len(messages) == 3 and "scene 1/3" in messages[0]


def test_prepare_show_threads_speaker_names(tmp_path):
    scene = coder_talk_scene()
    scene["speaker"] = "tester"
    episode = _episode([scene])
    llm = FakeLLM(reply="A fresh spoken line.")
    prepare_show(episode, llm, None, tmp_path, primitives=PRIMITIVES, narration_config=NARRATION,
                speaker_names={"tester": "TESS-3"})
    assert "TESS-3" in llm.calls[0]


def test_prepare_show_verbatim_skips_llm_for_scenes_with_scripted_text(tmp_path):
    episode = _episode([boss_scene(), coder_talk_scene(), coder_work_scene(text="")])
    llm = FakeLLM(reply="A fresh spoken line.")
    show = prepare_show(episode, llm, None, tmp_path, primitives=PRIMITIVES,
                        narration_config=NARRATION, verbatim=True)
    assert show[0]["narration"] == "Fix the flaky test please"  # boss, verbatim
    assert show[1]["narration"] == "On it, boss."                # coder_talk, verbatim
    assert show[2]["narration"] == "A fresh spoken line."         # empty text, still LLM
    assert len(llm.calls) == 1  # only the empty-text scene called the LLM


def test_prepare_show_llm_down_across_whole_show_still_airs_with_fallbacks(tmp_path):
    """The soft-degradation contract: an LLM outage never stops the show --
    every scene still gets SOME spoken line and, when TTS is up, audio."""
    episode = _episode([boss_scene(), coder_talk_scene(), coder_work_scene()])
    llm = FakeLLM(error=RuntimeError("ollama down"))
    show = prepare_show(episode, llm, FakeTTS(duration=1.0), tmp_path,
                        primitives=PRIMITIVES, narration_config=NARRATION)
    assert len(show) == 3
    assert all(scene["narration"] for scene in show)
    assert all(scene["audio"] is not None for scene in show)
