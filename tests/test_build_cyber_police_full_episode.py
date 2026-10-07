"""No-repeat episode building for .claude/prompts/build_cyber_police_full_episode.py
(v2, 2026-10-02 cyber_police_day1 audit: the v1 builder looped the 7-scene
spine 58x — every authored line aired every ~12 min).

The builder is a script, not a module, so it is loaded by path. Ambient takes
are synthetic (written to tmp_path) so these tests never depend on the
contents of campaigns/cyber_police/generated/.
"""
import copy
import importlib.util
import pathlib

import pytest

from campaign.pack import load_pack
from episode_validator import validate_episode

REPO = pathlib.Path(__file__).resolve().parents[1]
PACK_DIR = REPO / "campaigns" / "cyber_police"

pytestmark = pytest.mark.skipif(not PACK_DIR.exists(), reason="cyber_police pack not present")


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location(
        "_builder_cyber_police_full", REPO / ".claude" / "prompts" / "build_cyber_police_full_episode.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def real_pack():
    return load_pack(PACK_DIR)


@pytest.fixture()
def pack(tmp_path, real_pack):
    p = copy.copy(real_pack)
    p.root = tmp_path
    return p


def _write_take(pack, scene_id, name, lines):
    d = pathlib.Path(pack.root) / "generated" / scene_id
    d.mkdir(parents=True, exist_ok=True)
    body = "beats:\n" + "".join(
        f"- kind: {k}\n  speaker: {s}\n  text: {t!r}\n" for k, s, t in lines)
    (d / f"{name}.yaml").write_text(body, encoding="utf-8")


def _fill_unique(pack, n_per_scene, words_per_line=12):
    i = 0
    for sid in pack.ambient_scene_ids():
        for t in range(n_per_scene):
            lines = []
            for who in ("detective", "analyst", "agent"):
                i += 1
                lines.append(("dialogue", who, " ".join(f"w{i}x{j}" for j in range(words_per_line)) + "."))
            _write_take(pack, sid, f"{t:03d}", lines)


# --- repeat guard ------------------------------------------------------------
def test_repeat_guard_rejects_exact_and_near_duplicates_within_gap(builder):
    g = builder.RepeatGuard(min_gap_s=3600)
    g.add("The trace servers are requisitioned, not approved, so don't count on them.", 0)
    assert g.conflict("The trace servers are requisitioned, not approved, so don't count on them.", 10)
    assert g.conflict("the trace servers are requisitioned not approved so don't count on them!", 10)
    assert g.conflict("The trace servers are requisitioned, not approved, so don't count on them yet.", 10)
    assert g.conflict("A completely different line about the duck video.", 10) is None


def test_repeat_guard_short_line_near_duplicates(builder):
    g = builder.RepeatGuard(min_gap_s=3600)
    g.add("What have you got?", 0)
    assert g.conflict("What have you got, Priya?", 5)
    assert g.conflict("Morning, Captain.", 5) is None


def test_repeat_guard_allows_repeat_after_gap(builder):
    g = builder.RepeatGuard(min_gap_s=60)
    g.add("Same line exactly here.", 0)
    assert g.conflict("Same line exactly here.", 30)
    assert g.conflict("Same line exactly here.", 61) is None


def test_assert_no_repeats_raises_on_close_repeat(builder):
    evs = [{"type": "user_message", "text": "Hello there, team."},
           {"type": "user_message", "text": "Something else entirely."},
           {"type": "user_message", "text": "hello there team"}]
    with pytest.raises(builder.RepeatViolation):
        builder.assert_no_repeats(evs, 3600)
    builder.assert_no_repeats(evs[:2], 3600)


# --- cleaning ----------------------------------------------------------------
@pytest.mark.parametrize("label,expected", [
    ("Rosalind Agar", "captain"), ("Marcus Thorn", "detective"), ("Captain Agar", "captain"),
    ("Detective Thorn", "detective"), ("HV", "forensics"), ("PN", "analyst"),
    ("TMB-L", "quartermaster"), ("Des", "agent"), ("Teresa (Quartermaster)", "quartermaster"),
    ("DOP", "press"), ("The Observer", "observer"),
    ("DO", None),          # Deshawn Okafor or Davey Okonkwo: ambiguous -> unresolved
    ("Denny", None), ("Supply notice", None),
])
def test_resolve_label(builder, real_pack, label, expected):
    alias = builder.build_alias_index(real_pack)
    assert builder.resolve_label(label, alias) == expected


def test_clean_take_reattributes_strips_and_drops(builder, real_pack):
    from collections import defaultdict
    speakers = builder.speaker_map(real_pack)
    alias = builder.build_alias_index(real_pack)
    silent = builder.silent_cast_ids(real_pack)
    stats = defaultdict(int)
    beats = [
        {"kind": "narration", "speaker": "captain", "text": "Marcus Thorn: Alright, let's get to it."},
        {"kind": "dialogue", "speaker": "analyst", "text": "(muttering) I'll keep doing my job ||emotion:sad"},
        {"kind": "narration", "speaker": "captain", "text": "Observer: (silence)"},
        {"kind": "dialogue", "speaker": "observer", "text": "I see everything."},
        {"kind": "narration", "speaker": "captain", "text": "DO: ambiguous initials line."},
        {"kind": "narration", "speaker": "captain", "text": "(The bullpen settles into its routine.)"},
        {"kind": "dialogue", "speaker": "press", "text": "Waiting on the Ashiorid Chronicle."},
    ]
    evs, raw = builder.clean_take(beats, real_pack, speakers, alias, silent, stats,
                                  builder.FOREIGN_TERMS["cyber_police"])
    assert raw == 7
    assert evs == [
        {"type": "assistant_text", "text": "Alright, let's get to it.", "speaker": speakers["detective"]},
        {"type": "assistant_text", "text": "I'll keep doing my job.", "speaker": speakers["analyst"]},
        {"type": "user_message", "text": "The bullpen settles into its routine."},
    ]
    assert stats["drop_silent_cast"] == 2
    assert stats["drop_unresolved_label"] == 1
    assert stats["drop_foreign_pack_term"] == 1


def test_clean_take_refusal_rejects_whole_take(builder, real_pack):
    from collections import defaultdict
    stats = defaultdict(int)
    evs, _ = builder.clean_take(
        [{"kind": "dialogue", "speaker": "agent", "text": "Fine."},
         {"kind": "narration", "speaker": "captain", "text": "As an AI, I cannot write this scene."}],
        real_pack, builder.speaker_map(real_pack), builder.build_alias_index(real_pack),
        builder.silent_cast_ids(real_pack), stats)
    assert evs == [] and stats["take_refusal"] == 1


# --- build -------------------------------------------------------------------
def test_build_plays_spine_once_and_never_repeats(builder, pack):
    _fill_unique(pack, n_per_scene=10)
    speakers = builder.speaker_map(pack)
    events, report = builder.build_events(pack, 20, target_words=4000, speakers=speakers)
    texts = [e["text"] for e in events]
    assert len(texts) == len(set(texts))
    opener = str(pack.scene(pack.start_scene).enter_narration).strip()
    assert texts.count(opener) == 1
    assert texts[0] == opener                     # spine opens the day ...
    spine = builder._spine_scenes(pack, 20)
    last_scene_lines = [e["text"] for e in builder.spine_scene_events(spine[-1], speakers)]
    assert texts[-len(last_scene_lines):] == last_scene_lines   # ... and closes it
    assert report["unique_ratio"] == 1.0 and report["min_repeat_gap_h"] is None
    assert report["total_words"] >= 4000


def test_build_fails_loudly_when_pool_too_small(builder, pack):
    _fill_unique(pack, n_per_scene=1)
    speakers = builder.speaker_map(pack)
    with pytest.raises(builder.PoolTooSmall) as exc:
        builder.build_events(pack, 20, target_words=50_000, speakers=speakers)
    rep = exc.value.report
    assert rep["shortfall_words"] > 0 and rep["takes_needed_estimate"] > 0
    events, report = builder.build_events(pack, 20, target_words=50_000, speakers=speakers,
                                          allow_short=True)
    assert "warning" in report and report["unique_ratio"] == 1.0


def test_build_drops_duplicate_takes_and_lines(builder, pack):
    same = [("dialogue", "agent", "Okay okay so the trace tool is frozen again, and then it crashed."),
            ("dialogue", "analyst", "So. Three things. One, it was always going to crash today.")]
    sid = pack.ambient_scene_ids()[0]
    _write_take(pack, sid, "001", same)
    _write_take(pack, sid, "002", same)      # identical take
    _write_take(pack, sid, "003", same + [("dialogue", "agent", "Morning, everyone.")] * 3)
    events, report = builder.build_events(pack, 20, target_words=50_000,
                                          speakers=builder.speaker_map(pack), allow_short=True)
    texts = [e["text"] for e in events]
    assert texts.count(same[0][2]) == 1
    assert "Morning, everyone." not in texts   # take 003 lost >40% to dedupe -> skipped whole
    assert report["stats"]["take_skipped_after_clean_dedupe"] >= 1


def test_built_episode_passes_validator(builder, pack):
    _fill_unique(pack, n_per_scene=2)
    events, _ = builder.build_events(pack, 20, target_words=50_000,
                                     speakers=builder.speaker_map(pack), allow_short=True)
    assert any(e["text"].startswith("w1x0 ") for e in events)
    seats = pack.seats
    ep = {"source": "t", "project": "virtualTubers", "session_id": "t", "date": "2026-10-02_00-00-00",
          "events": events,
          "show": {"music": {"theme": "cyber_police"},
                   "slots": sorted(set(seats.values()), key=lambda s: int(s.split("_")[1]))}}
    assert validate_episode(ep, "t")["event_count"] == len(events)
