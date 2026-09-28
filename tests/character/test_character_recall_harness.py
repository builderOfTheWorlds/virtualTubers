"""WP-23 tests for app/character/recall_harness.py: replay beats against fragments.

Frozen test list (playbook §4 WP-23, item 11), plus the DB side the harness
needs (plan §7: candidates are the character's DORMANT fragments only, item 9
from the loader's side). The harness replays beats from a file or the DB
against fragments and prints scores; its gate is a run with real embeddings
on gx10 to re-tune the thresholds (plan §7 "The recall harness (WP-23) must
re-tune these on replayed beats before the pilot").

OB-41 test correction (tracker .claude/prompts/character_v4_tracker_phase3b.md):
"replays the probe's fixture" -> replays the OFFICE fixture
tests/character/fixtures/recall_office_engineer.yaml, which mirrors the probe's
structure (8 lead-up beats; replay with 2 noise beats, shuffled with the probe's
permutation, unrelated) on the engineer's Fraud-Stop outage. OB-41: "The recall
harness (WP-23) is calibrated on office beats."
"""
from datetime import datetime, timezone

import pytest
import yaml

from fakes import seed_character
from fakes_e2e import FIXTURES, embed_for_probe_fixture, load_yaml_fixture
from pending import require, skip_if_pending

skip_if_pending("app/character/recall.py", wp="WP-23")
harness = require("character.recall_harness", "app/character/recall_harness.py", wp="WP-23")
from character import recall  # noqa: E402

FIXTURE = FIXTURES / "recall_office_engineer.yaml"


def _params():
    return recall.RecallParams(window_beats=12, bias=0.6, ref_cosine=0.8, gap=0.1, w_hooks=0.3,
                               w_trajectory=0.5, w_gist=0.2, unease=0.35, surface=0.55,
                               decay=0.85, spread=0.3, cooldown_beats=40)


# T23.11
def test_harness_ranks_replay_above_shuffled_above_unrelated(capsys):
    fixture = harness.load_fixture(FIXTURE)
    assert fixture == load_yaml_fixture("recall_office_engineer.yaml")
    embed = embed_for_probe_fixture(fixture)
    scores = harness.score_fixture(fixture, embed, _params())
    assert set(scores) == {"replay", "shuffled", "unrelated"}
    replay, shuffled, unrelated = scores["replay"], scores["shuffled"], scores["unrelated"]
    assert set(replay) >= {"sw_raw", "trajectory", "peak_activation"}
    assert replay["trajectory"] > shuffled["trajectory"] > unrelated["trajectory"] == 0.0
    assert replay["sw_raw"] > shuffled["sw_raw"] > unrelated["sw_raw"] == 0.0
    assert replay["peak_activation"] > shuffled["peak_activation"] >= unrelated["peak_activation"]
    assert harness.separates(scores) is True

    assert harness.main(["--file", str(FIXTURE)], embed=embed) == 0
    out = capsys.readouterr().out
    assert out.index("replay") < out.index("shuffled") < out.index("unrelated")
    assert "PASS" in out


def test_harness_fails_when_replay_does_not_separate(tmp_path, capsys):
    fixture = load_yaml_fixture("recall_office_engineer.yaml")
    fixture["windows"]["replay"] = list(fixture["windows"]["unrelated"])
    fixture["paraphrase_of"] = {}
    path = tmp_path / "flat.yaml"
    path.write_text(yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")
    embed = embed_for_probe_fixture(fixture)
    assert harness.separates(harness.score_fixture(fixture, embed, _params())) is False
    assert harness.main(["--file", str(path)], embed=embed) == 1
    assert "FAIL" in capsys.readouterr().out


def _seed_fragment(cur, fid, cid, gist, beats, hooks=None, week=1):
    import psycopg2.extras

    cur.execute("INSERT INTO memory_fragments (id, character_id, source_week, gist, hooks) "
                "VALUES (%s, %s, %s, %s, %s)", (fid, cid, week, gist, psycopg2.extras.Json(hooks or {})))
    for position in reversed(range(len(beats))):         # inserted out of order on purpose
        cur.execute("INSERT INTO fragment_lead_up (fragment_id, position, text) VALUES (%s, %s, %s)",
                    (fid, position, beats[position]))


@pytest.fixture
def office_fragments(pg_conn):
    fixture = load_yaml_fixture("recall_office_engineer.yaml")
    cid = seed_character(pg_conn, "engineer", name="Theo Palliser")
    tester = seed_character(pg_conn, "tester", name="Owen Hask")
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE characters SET aliases = %s WHERE id = %s",
                    (["Tester", "Owen", "Hask"], tester))
        _seed_fragment(cur, "f-outage", cid, fixture["fragment"]["gist"], fixture["lead_up"],
                       hooks=fixture["fragment"]["hooks"])
        _seed_fragment(cur, "f-fridge", cid, "I laughed at the fridge audit notice.",
                       fixture["windows"]["unrelated"][:3])
        _seed_fragment(cur, "f-unlocked", cid, "The Glass Box went dark.", ["It was dark."])
        cur.execute("INSERT INTO fragment_links (from_fragment_id, to_fragment_id, relation) "
                    "VALUES ('f-fridge', 'f-outage', 'co_occurred')")
        cur.execute("INSERT INTO fragment_unlocks (fragment_id, character_id, unlocked_week) "
                    "VALUES ('f-unlocked', %s, 1)", (cid,))
        for i, text in enumerate(fixture["windows"]["replay"]):
            ts = datetime(2026, 10, 7, 16, 0, i, tzinfo=timezone.utc)
            cur.execute("INSERT INTO experience_events (message_id, character_id, msg_type, "
                        "from_agent, visibility, text, payload, ts, loop_week, loop_day) VALUES "
                        "(%s, %s, 'character_say', 'char:tech_lead', 'present', %s, '{}', %s, 2, "
                        "'2026-10-07')", (f"m{i}", cid, text, ts))
    pg_conn.commit()
    return cid, fixture


# T23.9 (the loader side): candidates are dormant fragments only
@pytest.mark.integration
def test_load_candidates_returns_dormant_fragments_with_ordered_lead_up(pg_conn, office_fragments):
    cid, fixture = office_fragments
    candidates = {c.fragment_id: c for c in harness.load_candidates(pg_conn, cid)}
    assert set(candidates) == {"f-outage", "f-fridge"}
    outage = candidates["f-outage"]
    assert list(outage.lead_up) == fixture["lead_up"]
    assert outage.gist == fixture["fragment"]["gist"]
    assert outage.hooks["objects"] == ["latency graph"]
    assert "f-fridge" in outage.links and "f-outage" in candidates["f-fridge"].links
    aliases = harness.load_aliases(pg_conn, "ashiorid_office")
    assert aliases["tester"][:1] == ["Owen Hask"] and "Owen" in aliases["tester"]


@pytest.mark.integration
def test_replay_events_from_the_db_scores_the_matching_fragment_highest(pg_conn, office_fragments):
    cid, fixture = office_fragments
    embed = embed_for_probe_fixture(fixture)
    peaks = harness.replay_events(pg_conn, "engineer", 2, embed, _params())
    assert set(peaks) == {"f-outage", "f-fridge"}
    assert peaks["f-outage"] > peaks["f-fridge"] >= 0.0
    assert "f-unlocked" not in peaks
