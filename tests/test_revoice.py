"""Tests for app/revoice.py — the per-airing narration pass that turns an
episode script into voiced scenes. LLM and TTS are fakes throughout; the
contract under test is grouping, sizing, and graceful degradation."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import revoice  # noqa: E402
from revoice import (  # noqa: E402
    MAX_SCENE_EVENTS, MAX_WORDS, MIN_WORDS,
    fallback_narration, narrate_scene, plan_scenes, prepare_show,
    scene_visual_seconds, target_words,
)


def tool(name="Bash", speaker=None, **detail):
    event = {"type": "tool_call", "tool": name, "error": False,
             "input_summary": "", "output_summary": "", "detail_file": None,
             "detail": detail}
    if speaker is not None:
        event["speaker"] = speaker
    return event


EVENTS = [
    {"type": "user_message", "text": "Fix the flaky test please"},
    {"type": "assistant_text", "text": "On it, boss."},
    tool(command="pytest -x", output="1 failed"),
    tool("Edit", file="app/agent.py", old="a", new="b"),
    {"type": "assistant_text", "text": "That should do it."},
]


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


# ── plan_scenes ──────────────────────────────────────────────────────────────

def test_plan_scenes_groups_by_speaker_and_kind():
    scenes = plan_scenes(EVENTS)
    assert [scene["kind"] for scene in scenes] == [
        "boss", "coder_talk", "coder_work", "coder_talk"]
    assert scenes[0]["speaker"] == "boss"
    assert scenes[2]["speaker"] == "coder"
    assert len(scenes[2]["events"]) == 2  # both tool calls in one scene


def test_plan_scenes_splits_marathon_tool_runs():
    events = [tool(command=f"step {i}") for i in range(MAX_SCENE_EVENTS * 2 + 3)]
    scenes = plan_scenes(events)
    assert [len(scene["events"]) for scene in scenes] == [
        MAX_SCENE_EVENTS, MAX_SCENE_EVENTS, 3]


def test_plan_scenes_drops_unknown_event_types():
    scenes = plan_scenes([{"type": "mystery"}, {"type": "assistant_text", "text": "hi"}])
    assert len(scenes) == 1 and scenes[0]["kind"] == "coder_talk"


def test_plan_scenes_honors_explicit_speaker_override():
    events = [
        {"type": "user_message", "text": "hi", "speaker": "director"},
        {"type": "assistant_text", "text": "yo", "speaker": "tester"},
        tool(command="run", speaker="coder-native"),
    ]
    scenes = plan_scenes(events)
    assert scenes[0]["kind"] == "boss" and scenes[0]["speaker"] == "director"
    assert scenes[1]["kind"] == "coder_talk" and scenes[1]["speaker"] == "tester"
    assert scenes[2]["kind"] == "coder_work" and scenes[2]["speaker"] == "coder-native"


def test_plan_scenes_splits_tool_work_on_speaker_change():
    events = [
        tool(command="a", speaker="coder-native"),
        tool(command="b", speaker="coder-native"),
        tool(command="c", speaker="tester"),
        tool(command="d", speaker="tester"),
    ]
    scenes = plan_scenes(events)
    assert [scene["speaker"] for scene in scenes] == ["coder-native", "tester"]
    assert [len(scene["events"]) for scene in scenes] == [2, 2]


def test_plan_scenes_does_not_split_tool_work_when_speaker_unchanged():
    events = [tool(command="a", speaker="tester"), tool(command="b", speaker="tester")]
    scenes = plan_scenes(events)
    assert len(scenes) == 1
    assert len(scenes[0]["events"]) == 2


# ── sizing ───────────────────────────────────────────────────────────────────

def test_target_words_scales_with_screen_time_and_clamps():
    assert target_words(0.1) == MIN_WORDS
    assert target_words(10) == 25          # 2.5 words/sec
    assert target_words(10_000) == MAX_WORDS


def test_scene_visual_seconds_positive_and_speed_scaled():
    scene = plan_scenes(EVENTS)[2]
    base = scene_visual_seconds(scene, max_output_lines=24, speed=1.0)
    assert base > 0
    assert scene_visual_seconds(scene, 24, speed=2.0) == pytest.approx(base / 2)


# ── narration text ───────────────────────────────────────────────────────────

def test_narrate_scene_uses_llm_line():
    scene = plan_scenes(EVENTS)[0]
    llm = FakeLLM(reply="Hey, could you sort out that flaky test for me?")
    line = narrate_scene(scene, llm, words=20, worker_name="KODI-7", boss_name="the boss")
    assert line == "Hey, could you sort out that flaky test for me?"
    assert "20" in llm.calls[0]  # the word budget reached the prompt


def test_narrate_scene_falls_back_when_llm_fails():
    scene = plan_scenes(EVENTS)[0]
    llm = FakeLLM(error=RuntimeError("ollama down"))
    line = narrate_scene(scene, llm, words=20, worker_name="K", boss_name="B")
    assert "flaky test" in line  # fallback speaks the redacted script text


def test_narrate_scene_without_llm_uses_fallback():
    scene = plan_scenes(EVENTS)[2]
    line = narrate_scene(scene, None, words=20, worker_name="K", boss_name="B")
    assert "pytest -x" in line and "agent.py" in line


def test_narrate_scene_uses_speaker_names_override():
    scene = {"kind": "coder_talk", "speaker": "tester",
             "events": [{"type": "assistant_text", "text": "hi"}]}
    llm = FakeLLM(reply="A fresh spoken line.")
    narrate_scene(scene, llm, words=20, worker_name="KODI-7", boss_name="the boss",
                  speaker_names={"tester": "TESS-3"})
    assert "TESS-3" in llm.calls[0]


def test_narrate_scene_falls_back_to_raw_speaker_id_when_unmapped():
    scene = {"kind": "coder_talk", "speaker": "coder-native",
             "events": [{"type": "assistant_text", "text": "hi"}]}
    llm = FakeLLM(reply="A fresh spoken line.")
    narrate_scene(scene, llm, words=20, worker_name="KODI-7", boss_name="the boss",
                  speaker_names={"tester": "TESS-3"})
    assert "coder-native" in llm.calls[0]


def test_narrate_scene_verbatim_reads_full_line_without_llm():
    long_text = ("TESS-3 here, and before anyone asks, no, I have not found "
                 "a bug yet, but the stream just started, give me a minute.")
    scene = {"kind": "coder_talk", "speaker": "tester", "events": [{"text": long_text}]}
    llm = FakeLLM(reply="A fresh spoken line.")
    line = narrate_scene(scene, llm, words=11, worker_name="K", boss_name="B", verbatim=True)
    assert line == long_text
    assert llm.calls == []  # verbatim never calls the LLM for dialogue scenes


def test_narrate_scene_verbatim_still_paraphrases_coder_work():
    scene = plan_scenes(EVENTS)[2]
    llm = FakeLLM(reply="A fresh spoken line.")
    line = narrate_scene(scene, llm, words=20, worker_name="K", boss_name="B", verbatim=True)
    assert line == "A fresh spoken line."
    assert len(llm.calls) == 1  # coder_work always goes through the LLM/fallback path


@pytest.mark.parametrize("index, fragment", [
    (0, "Fix the flaky test"),   # boss: speaks the message
    (1, "On it, boss."),         # coder_talk: speaks the narration
    (2, "running pytest -x"),    # coder_work: describes the actions
])
def test_fallback_narration_per_kind(index, fragment):
    scenes = plan_scenes(EVENTS)
    assert fragment in fallback_narration(scenes[index], max_words=40)


def test_fallback_narration_respects_word_cap():
    scene = {"kind": "boss", "speaker": "boss",
             "events": [{"type": "user_message", "text": "word " * 500}]}
    line = fallback_narration(scene, max_words=10)
    assert len(line.split()) <= 11  # 10 words + ellipsis marker


# ── prepare_show ─────────────────────────────────────────────────────────────

def test_prepare_show_attaches_narration_and_audio(tmp_path):
    script = {"source": "ep1", "events": EVENTS}
    show = prepare_show(script, FakeLLM(), FakeTTS(duration=3.0), tmp_path)
    assert len(show) == 4
    for scene in show:
        assert scene["narration"]
        assert scene["audio"].duration == 3.0


def test_prepare_show_tts_failure_leaves_scene_silent(tmp_path):
    script = {"source": "ep1", "events": EVENTS}
    show = prepare_show(script, FakeLLM(), FakeTTS(error=RuntimeError("no model")), tmp_path)
    assert all(scene["audio"] is None for scene in show)
    assert all(scene["narration"] for scene in show)  # text still there


def test_prepare_show_without_tts_is_narrated_but_silent(tmp_path):
    script = {"source": "ep1", "events": EVENTS}
    show = prepare_show(script, FakeLLM(), None, tmp_path)
    assert all(scene["audio"] is None for scene in show)


def test_prepare_show_reports_progress(tmp_path):
    messages = []
    prepare_show({"events": EVENTS}, FakeLLM(), None, tmp_path,
                 progress=messages.append)
    assert len(messages) == 4 and "scene 1/4" in messages[0]


def test_prepare_show_threads_speaker_names(tmp_path):
    events = [{"type": "assistant_text", "text": "hi", "speaker": "tester"}]
    script = {"source": "ep1", "events": events}
    llm = FakeLLM(reply="A fresh spoken line.")
    prepare_show(script, llm, None, tmp_path, speaker_names={"tester": "TESS-3"})
    assert "TESS-3" in llm.calls[0]


def test_prepare_show_verbatim_skips_llm_for_dialogue_scenes(tmp_path):
    script = {"source": "ep1", "events": EVENTS}
    llm = FakeLLM(reply="A fresh spoken line.")
    show = prepare_show(script, llm, None, tmp_path, verbatim=True)
    assert show[0]["narration"] == "Fix the flaky test please"  # boss, verbatim
    assert show[1]["narration"] == "On it, boss."                # coder_talk, verbatim
    assert show[2]["narration"] == "A fresh spoken line."         # coder_work, still LLM
    assert len(llm.calls) == 1  # only the coder_work scene called the LLM
