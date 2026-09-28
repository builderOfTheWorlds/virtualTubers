"""WP-23 tests for app/character/recall.py: the pure recall engine.

Frozen test list (playbook §4 WP-23, items 1-10; item 11 is in
test_character_recall_harness.py). Plan §7, D-08, D-18. Per beat, for each
DORMANT candidate fragment of one character, three signals in 0..1:
  hooks       fraction of the fragment's hook terms (entities, places,
              objects, alias aware) found in the beat, case-insensitive whole words
  trajectory  Smith-Waterman of the last window_beats beats against the
              lead-up: match = cos - bias, gaps cost gap, best / (len(lead_up) *
              (ref_cosine - bias)), clipped to 1
  gist        clip((cos(beat, gist) - bias) / (ref_cosine - bias), 0, 1)
activation = w_h*hooks + w_t*trajectory + w_g*gist; it decays by `decay` per
beat and spreads `spread` x to linked fragments. Crossing `unease` gives one
unease event per cooldown; crossing `surface` asks the judge, and a yes gives
a surface event and an unlock. Pure: embed() and judge() are injected (fakes).
Office beats (OB-41).
"""
import math

import pytest

from fakes_e2e import FakeJudge, TopicEmbed
from pending import require

recall = require("character.recall", "app/character/recall.py", wp="WP-23")

HOOKS_ONLY = dict(w_hooks=1.0, w_trajectory=0.0, w_gist=0.0)


def _axis(i, dim=16):
    return [1.0 if k == i else 0.0 for k in range(dim)]


def _near(i, cos, dim=16):
    """A unit vector with cosine `cos` to axis i (the rest goes to axis i+8)."""
    vec = [0.0] * dim
    vec[i] = cos
    vec[i + 8] = math.sqrt(1.0 - cos * cos)
    return vec


def _params(**overrides):
    base = dict(window_beats=12, bias=0.6, ref_cosine=0.8, gap=0.1, w_hooks=0.3,
                w_trajectory=0.5, w_gist=0.2, unease=0.35, surface=0.55, decay=0.85,
                spread=0.3, cooldown_beats=40)
    base.update(overrides)
    return recall.RecallParams(**base)


def _candidate(fid, hooks, links=(), lead_up=("I sat at my desk.",), gist=None):
    return recall.Candidate(fragment_id=fid, gist=gist or f"I felt something about {fid}.",
                            lead_up=tuple(lead_up), hooks=hooks, links=tuple(links))


# T23.1
def test_sw_score_of_identical_sequence_is_one_clipped():
    lead = [_axis(i) for i in range(8)]
    assert recall.sw_raw(lead, lead, bias=0.6, gap=0.1) == pytest.approx(0.4)   # (1 - 0.6) per beat
    assert recall.sw_score(lead, lead, bias=0.6, ref_cosine=0.8, gap=0.1) == 1.0
    assert recall.sw_score(lead, [], bias=0.6, ref_cosine=0.8, gap=0.1) == 0.0
    assert recall.cosine(_axis(0), _near(0, 0.7)) == pytest.approx(0.7)


# T23.2
def test_shuffled_sequence_scores_lower_than_ordered():
    lead = [_axis(i) for i in range(8)]
    ordered = [_near(i, 0.7) for i in range(8)]
    shuffled = [ordered[i] for i in (7, 3, 0, 6, 2, 5, 1, 4)]
    kw = dict(bias=0.6, ref_cosine=0.8, gap=0.1)
    in_order = recall.sw_score(lead, ordered, **kw)
    assert in_order == pytest.approx(0.5)            # 8 x (0.7 - 0.6) / (8 x 0.2)
    assert 0.0 < recall.sw_score(lead, shuffled, **kw) < in_order


# T23.3
def test_unrelated_beats_under_the_bias_score_zero():
    lead = [_axis(i) for i in range(8)]
    kw = dict(bias=0.6, ref_cosine=0.8, gap=0.1)
    orthogonal = [_axis(i + 8) for i in range(8)]
    weak = [_near(i, 0.55) for i in range(8)]        # cosine 0.55 < bias 0.6
    assert recall.sw_score(lead, orthogonal, **kw) == 0.0
    assert recall.sw_score(lead, weak, **kw) == 0.0
    assert recall.gist_score(_near(0, 0.5), _axis(0), bias=0.6, ref_cosine=0.8) == 0.0
    assert recall.gist_score(_near(0, 0.7), _axis(0), bias=0.6, ref_cosine=0.8) == pytest.approx(0.5)
    assert recall.gist_score(_axis(0), _axis(0), bias=0.6, ref_cosine=0.8) == 1.0


# T23.4
def test_hooks_are_case_insensitive_whole_words_and_alias_aware():
    hooks = {"entities": ["tech-lead", "Owen"], "places": ["Malmont"], "objects": ["latency graph"],
             "tone": "dread"}
    assert recall.hook_terms(hooks) == ["tech-lead", "owen", "malmont", "latency graph"]
    aliases = {"tech-lead": ["Anselm Brody", "Brody", "Tech Lead"]}
    assert recall.hook_score("MALMONT again.", {"places": ["Malmont"]}) == 1.0
    assert recall.hook_score("The Malmonts called.", {"places": ["Malmont"]}) == 0.0
    assert recall.hook_score("Brody looked at me.", {"entities": ["tech-lead"]}, aliases) == 1.0
    assert recall.hook_score("Brodys desk was empty.", {"entities": ["tech-lead"]}, aliases) == 0.0
    assert recall.hook_score("Brody's desk was empty.", {"entities": ["tech-lead"]}, aliases) == 1.0
    assert recall.hook_score("Brody watched the latency  graph with Owen at Malmont.", hooks,
                             aliases) == 1.0
    assert recall.hook_score("Owen shrugged.", hooks, aliases) == 0.25
    assert recall.hook_score("Nothing to see.", {}, aliases) == 0.0


# T23.5
def test_activation_decays_by_decay_per_beat():
    engine = recall.RecallEngine([_candidate("f1", {"places": ["Malmont"]})], TopicEmbed(),
                                 _params(**HOOKS_ONLY, unease=2.0, surface=3.0))
    assert engine.observe("The Malmont numbers came back.") == []
    assert engine.activation("f1") == pytest.approx(1.0)
    for step in range(1, 4):
        engine.observe(f"Nora restocked shelf {step}.")
        assert engine.activation("f1") == pytest.approx(0.85 ** step)


# T23.6
def test_spread_reaches_linked_fragments():
    candidates = [_candidate("A", {"places": ["Malmont"]}, links=("B",)),
                  _candidate("B", {"objects": ["latency graph"]}),
                  _candidate("C", {"objects": ["fridge"]})]
    engine = recall.RecallEngine(candidates, TopicEmbed(), _params(**HOOKS_ONLY, unease=2.0, surface=3.0))
    engine.observe("The Malmont traffic again.")
    assert engine.activation("A") == pytest.approx(1.0)
    assert engine.activation("B") == pytest.approx(0.3)      # spread x A's signal
    assert engine.activation("C") == 0.0
    engine.observe("The latency graph went red.")
    assert engine.activation("B") == pytest.approx(1.0)
    assert engine.activation("A") == pytest.approx(0.85)     # decay beats the spread back from B


# T23.7
def test_crossing_unease_gives_one_event_per_cooldown():
    engine = recall.RecallEngine([_candidate("f1", {"places": ["Malmont"]})], TopicEmbed(),
                                 _params(**HOOKS_ONLY, unease=0.5, surface=2.0, cooldown_beats=5))
    events = []
    for i in range(12):
        events.extend(engine.observe(f"Malmont, again, beat {i}.", beat_id=f"b{i}"))
    assert [e.level for e in events] == ["unease"] * 3
    assert [e.beat_index for e in events] == [1, 6, 11]
    assert [e.beat_id for e in events] == ["b0", "b5", "b10"]
    first = events[0]
    assert first.fragment_id == "f1" and first.activation >= 0.5
    assert set(first.signals) == {"hooks", "trajectory", "gist"}
    assert recall.UNEASE_TEXT == "something about this feels familiar"


# T23.8
def test_crossing_surface_calls_judge_yes_surfaces_and_unlocks_no_gives_nothing():
    unlocked = []
    judge = FakeJudge(verdicts=[False, True])
    gist = "My stomach dropped when the latency graph went red."
    engine = recall.RecallEngine(
        [_candidate("f1", {"objects": ["latency graph"]}, gist=gist)], TopicEmbed(),
        _params(**HOOKS_ONLY, unease=0.3, surface=0.5, cooldown_beats=4), judge=judge,
        on_unlock=lambda fid, event: unlocked.append((fid, event.level)))
    per_beat = [engine.observe(f"The latency graph, beat {i}.") for i in range(1, 7)]
    assert per_beat[0] == []                          # judge said no: nothing
    assert [e.level for e in per_beat[1]] == ["unease"]
    assert per_beat[2] == [] and per_beat[3] == []    # judge cooling down, unease too
    assert len(judge.calls) == 2                      # beat 1 and beat 5 (cooldown 4)
    surfaced = per_beat[4]
    assert [e.level for e in surfaced] == ["surface"]
    assert surfaced[0].judge_verdict is True and surfaced[0].gist == gist
    assert unlocked == [("f1", "surface")]
    assert judge.calls[1][0] == "f1"
    assert judge.calls[1][1][-1] == "The latency graph, beat 5."
    assert recall.surface_text(gist) == f"a feeling surfaces: {gist}"


# T23.9
def test_unlocked_fragments_are_never_candidates():
    judge = FakeJudge(verdicts=[True, True, True])
    engine = recall.RecallEngine([_candidate("f1", {"objects": ["latency graph"]}),
                                  _candidate("f2", {"objects": ["fridge"]})], TopicEmbed(),
                                 _params(**HOOKS_ONLY, unease=0.3, surface=0.5, cooldown_beats=1),
                                 judge=judge)
    assert engine.candidate_ids == ["f1", "f2"]
    assert [e.level for e in engine.observe("The latency graph went red.")] == ["surface"]
    assert engine.candidate_ids == ["f2"]
    for _ in range(5):
        assert engine.observe("The latency graph went red again.") == []
    assert len(judge.calls) == 1
    assert engine.activation("f1") == 0.0


# T23.10
def test_reset_clears_activation_window_and_cooldowns():
    embed = TopicEmbed()
    engine = recall.RecallEngine([_candidate("f1", {"places": ["Malmont"]})], embed,
                                 _params(**HOOKS_ONLY, unease=0.5, surface=2.0, cooldown_beats=50))
    assert [e.level for e in engine.observe("Malmont.")] == ["unease"]
    assert engine.observe("Malmont.") == []               # cooling down
    engine.reset()
    assert engine.activation("f1") == 0.0
    assert [e.level for e in engine.observe("Malmont.")] == ["unease"]   # cooldown cleared
    trajectory = recall.RecallEngine(
        [_candidate("t1", {}, lead_up=("Beat one.", "Beat two."))],
        embed.assign("Beat one.", "x").assign("Beat one, again.", "x"),
        _params(w_hooks=0.0, w_trajectory=1.0, w_gist=0.0, unease=2.0, surface=3.0))
    trajectory.observe("Beat one, again.")
    assert trajectory.activation("t1") > 0.0
    trajectory.reset()
    trajectory.observe("An unrelated beat.")
    assert trajectory.activation("t1") == 0.0             # the window was emptied too


def test_params_from_config_match_config_recall():
    from character import config

    cfg = config.load(env={})
    params = recall.RecallParams.from_config(cfg.recall)
    assert (params.window_beats, params.bias, params.ref_cosine, params.gap) == (12, 0.6, 0.8, 0.1)
    assert (params.w_hooks, params.w_trajectory, params.w_gist) == (0.3, 0.5, 0.2)
    assert (params.unease, params.surface, params.decay, params.spread, params.cooldown_beats) == \
        (0.35, 0.55, 0.85, 0.3, 40)
