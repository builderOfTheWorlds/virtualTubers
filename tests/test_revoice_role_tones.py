"""Tests for the revoice.py per-role tone hook (OB-33)."""
import pathlib

import pytest

from revoice import (
    OFFICE_ROLE_TONES,
    load_role_tones,
    narrate_scene,
    prepare_show,
    scene_tone,
)

PACK_CAST = pathlib.Path(__file__).resolve().parents[1] / "campaigns" / "ashiorid_office" / "cast"


class RecordingLLM:
    def __init__(self):
        self.prompts = []

    def complete(self, system_prompt, messages):
        self.prompts.append(messages[0]["content"])
        return "A spoken line."


def talk(speaker, role=None):
    event = {"type": "assistant_text", "text": "Tests are green.", "speaker": speaker}
    if role:
        event["role"] = role
    return {"kind": "coder_talk", "speaker": speaker, "events": [event]}


def test_default_prompt_unchanged_without_tone():
    a, b = RecordingLLM(), RecordingLLM()
    narrate_scene(talk("tuber_4"), a, words=20, worker_name="K", boss_name="B")
    narrate_scene(talk("tuber_4"), b, words=20, worker_name="K", boss_name="B", tone=None)
    assert a.prompts == b.prompts
    assert "Speak in this style" not in a.prompts[0]


def test_tone_appended_to_llm_prompt():
    llm = RecordingLLM()
    narrate_scene(talk("tuber_4"), llm, words=20, worker_name="K", boss_name="B", tone="terse")
    assert llm.prompts[0].endswith("Speak in this style: terse")


def test_tone_does_not_touch_fallback_or_verbatim():
    assert narrate_scene(talk("tuber_4"), None, 20, "K", "B", tone="terse") == \
        narrate_scene(talk("tuber_4"), None, 20, "K", "B")
    assert narrate_scene(talk("tuber_4"), RecordingLLM(), 20, "K", "B", verbatim=True,
                         tone="terse") == "Tests are green."


@pytest.mark.parametrize("scene,tones,expected", [
    (talk("tuber_5"), None, None),
    (talk("tuber_5"), {}, None),
    (talk("tuber_5"), {"tuber_5": "upbeat"}, "upbeat"),
    (talk("tuber_4", role="tester"), {"tester": "terse"}, "terse"),
    (talk("tuber_4", role="tester"), {"tuber_4": "seat wins", "tester": "terse"}, "seat wins"),
    (talk("coder"), {"tester": "terse"}, None),
])
def test_scene_tone_resolution(scene, tones, expected):
    assert scene_tone(scene, tones) == expected


def test_prepare_show_role_tones_reach_prompts(tmp_path):
    script = {"events": [
        {"type": "user_message", "text": "Ship it.", "speaker": "tuber_0", "role": "ceo"},
        {"type": "assistant_text", "text": "Love that.", "speaker": "tuber_5", "role": "marketing"},
    ]}
    llm = RecordingLLM()
    prepare_show(script, llm, None, tmp_path, role_tones={"marketing": "upbeat"})
    assert "Speak in this style" not in llm.prompts[0]
    assert llm.prompts[1].endswith("Speak in this style: upbeat")

    plain = RecordingLLM()
    prepare_show(script, plain, None, tmp_path)
    assert all("Speak in this style" not in p for p in plain.prompts)


def test_load_role_tones_from_cast_files(tmp_path):
    (tmp_path / "tester.yaml").write_text(
        "seat: tuber_4\noffice_role: tester\nspeech: 'Numbers first.'\n", encoding="utf-8")
    (tmp_path / "marketing.yaml").write_text(
        "seat: tuber_5\noffice_role: marketing\ntone: upbeat\nspeech: ignored\n", encoding="utf-8")
    (tmp_path / "engineer.yaml").write_text("seat: tuber_3\noffice_role: engineer\n", encoding="utf-8")
    (tmp_path / "broken.yaml").write_text("seat: [\n", encoding="utf-8")
    tones = load_role_tones(tmp_path)
    assert tones["tuber_4"] == tones["tester"] == "Numbers first."
    assert tones["marketing"] == "upbeat"
    assert tones["engineer"] == OFFICE_ROLE_TONES["engineer"]


def test_load_role_tones_missing_dir_is_empty(tmp_path):
    assert load_role_tones(tmp_path / "none") == {}


def test_load_role_tones_real_office_cast():
    tones = load_role_tones(PACK_CAST)
    assert {"tuber_4", "tester", "tuber_5", "marketing"} <= set(tones)
