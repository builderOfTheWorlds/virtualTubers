"""Tests for app/campaign/cli.py — running a campaign from the command line.

This is the operator's surface and the project's main verification tool: it
plays a campaign locally with no Kafka, no tmux, no docker and no TTS, so a
pack can be written and watched in a terminal before any of the streaming
stack is involved.

Because it is a verification tool, its exit codes are part of the contract —
0 for a show that played, 1 for a pack that is broken or a position that
cannot be restored. Everything is driven through `main(argv, out=...)` so the
tests never touch the real stdout or the real process exit.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from campaign import cli  # noqa: E402


# ── a real pack on disk ──────────────────────────────────────────────────────
CAMPAIGN = {
    "name": "testpack",
    "title": "A Test Campaign",
    "genre": "fantasy",
    "start_scene": "opening",
    "gm": "gm",
    "players": ["buffalo"],
    "primitives": ["roll_check"],
}

CAST = {
    "gm": {"name": "The GM", "voice": "narrator"},
    "buffalo": {"name": "Buffalo", "voice": "gruff"},
}

SCENES = [
    {
        "id": "opening",
        "title": "The Opening",
        "enter_narration": "The hall is cold.",
        "beats": [
            {"type": "dialogue", "speaker": "buffalo", "text": "I don't like this."},
            {"type": "action", "speaker": "buffalo", "primitive": "roll_check",
             "params": {"skill": "Insight", "dc": 14}},
        ],
        "branches": [
            {"id": "win", "next": "victory", "when": {"outcome": "success"}},
            {"id": "lose", "next": "defeat", "when": {"outcome": "failure"}},
        ],
        "default_next": "victory",
    },
    {"id": "victory", "beats": [
        {"type": "narration", "speaker": "gm", "text": "The doors hold."}]},
    {"id": "defeat", "beats": [
        {"type": "narration", "speaker": "gm", "text": "The doors give way."}]},
]


def write_pack(root, campaign=None, scenes=None, cast=None):
    """Write a campaign pack to disk and return its path."""
    root = Path(root)
    (root / "cast").mkdir(parents=True, exist_ok=True)
    (root / "scenes").mkdir(parents=True, exist_ok=True)

    (root / "campaign.yaml").write_text(yaml.safe_dump(campaign or CAMPAIGN))
    for member_id, data in (cast or CAST).items():
        (root / "cast" / f"{member_id}.yaml").write_text(yaml.safe_dump(data))
    for scene in (scenes or SCENES):
        (root / "scenes" / f"{scene['id']}.yaml").write_text(yaml.safe_dump(scene))
    return root


@pytest.fixture
def pack_dir(tmp_path):
    return write_pack(tmp_path / "campaigns" / "testpack")


class Out:
    """A stand-in for stdout that keeps everything written to it."""

    def __init__(self):
        self.chunks = []

    def write(self, text):
        self.chunks.append(text)
        return len(text)

    def flush(self):
        pass

    @property
    def text(self):
        return "".join(self.chunks)


def run(*argv, out=None):
    """Invoke the CLI, returning (exit_code, captured_output)."""
    out = out or Out()
    code = cli.main(list(argv) + ["--no-pace", "--no-color"], out=out)
    return code, out.text


# ── playing a pack ───────────────────────────────────────────────────────────
def test_a_dry_run_plays_the_campaign_and_succeeds(pack_dir):
    code, _ = run("--pack", str(pack_dir), "--dry-run")

    assert code == 0


def test_a_dry_run_shows_the_scripted_lines(pack_dir):
    _, text = run("--pack", str(pack_dir), "--dry-run")

    assert "The hall is cold." in text
    assert "I don't like this." in text


def test_dialogue_is_labelled_with_the_display_name(pack_dir):
    _, text = run("--pack", str(pack_dir), "--dry-run")

    assert "Buffalo: I don't like this." in text


def test_an_action_beat_is_expanded_through_its_primitive(pack_dir):
    _, text = run("--pack", str(pack_dir), "--dry-run")

    assert "Buffalo rolls Insight against DC 14." in text


def test_the_default_branch_is_taken_with_no_context(pack_dir):
    _, text = run("--pack", str(pack_dir), "--dry-run")

    assert "The doors hold." in text
    assert "The doors give way." not in text


def test_a_forced_branch_takes_the_other_path(pack_dir):
    _, text = run("--pack", str(pack_dir), "--dry-run", "--force-branch", "lose")

    assert "The doors give way." in text
    assert "The doors hold." not in text


def test_a_forced_branch_that_no_scene_has_still_plays_the_default(pack_dir):
    code, text = run("--pack", str(pack_dir), "--dry-run",
                     "--force-branch", "no-such-branch")

    assert code == 0
    assert "The doors hold." in text


def test_starting_at_a_named_scene_skips_the_opening(pack_dir):
    _, text = run("--pack", str(pack_dir), "--dry-run", "--scene", "defeat")

    assert "The doors give way." in text
    assert "The hall is cold." not in text


def test_starting_at_an_unknown_scene_fails(pack_dir):
    code, text = run("--pack", str(pack_dir), "--dry-run", "--scene", "nowhere")

    assert code == 1
    assert "nowhere" in text


def test_a_missing_pack_fails_without_a_traceback(pack_dir):
    code, text = run("--pack", str(pack_dir.parent / "gone"), "--dry-run")

    assert code == 1
    assert "gone" in text


def test_a_dry_run_writes_no_avatar_state(pack_dir, tmp_path):
    run("--pack", str(pack_dir), "--dry-run")

    assert not (tmp_path / "state.json").exists()


# ── validation ───────────────────────────────────────────────────────────────
def test_validate_accepts_a_good_pack(pack_dir):
    code, text = run("--validate", "--pack", str(pack_dir))

    assert code == 0
    assert "ok" in text.lower() or "valid" in text.lower()


def test_playing_does_not_print_the_validation_verdict(pack_dir):
    """The show is the output. A clean pack validates silently.

    Validation runs on every invocation, but its verdict belongs to
    --validate. Printing "ok" ahead of the cold open puts a status line in
    front of the audience.
    """
    _, text = run("--pack", str(pack_dir), "--dry-run")

    assert text.splitlines()[0] == "The hall is cold."


def test_listing_scenes_prints_only_scene_ids(pack_dir):
    _, text = run("--list-scenes", "--pack", str(pack_dir))

    assert sorted(text.split()) == ["defeat", "opening", "victory"]


def test_validate_does_not_play_the_show(pack_dir):
    _, text = run("--validate", "--pack", str(pack_dir))

    assert "I don't like this." not in text


def test_validate_rejects_a_pack_with_a_dangling_branch(tmp_path):
    scenes = [dict(SCENES[0]), SCENES[1], SCENES[2]]
    scenes[0] = json.loads(json.dumps(SCENES[0]))
    scenes[0]["branches"][0]["next"] = "nowhere"
    root = write_pack(tmp_path / "broken", scenes=scenes)

    code, text = run("--validate", "--pack", str(root))

    assert code == 1
    assert "nowhere" in text


def test_validate_reports_every_error_not_just_the_first(tmp_path):
    scenes = json.loads(json.dumps(SCENES))
    scenes[0]["branches"][0]["next"] = "nowhere"
    scenes[0]["branches"][1]["next"] = "elsewhere"
    root = write_pack(tmp_path / "broken", scenes=scenes)

    _, text = run("--validate", "--pack", str(root))

    assert "nowhere" in text
    assert "elsewhere" in text


def test_validate_surfaces_warnings_without_failing(tmp_path):
    scenes = json.loads(json.dumps(SCENES))
    scenes.append({"id": "orphan", "beats": [
        {"type": "narration", "speaker": "gm", "text": "Nobody comes here."}]})
    root = write_pack(tmp_path / "warned", scenes=scenes)

    code, text = run("--validate", "--pack", str(root))

    assert code == 0
    assert "orphan" in text


def test_a_broken_pack_is_rejected_before_it_plays(tmp_path):
    scenes = json.loads(json.dumps(SCENES))
    scenes[0]["branches"][0]["next"] = "nowhere"
    root = write_pack(tmp_path / "broken", scenes=scenes)

    code, text = run("--pack", str(root), "--dry-run")

    assert code == 1
    assert "I don't like this." not in text


# ── listing ──────────────────────────────────────────────────────────────────
def test_list_scenes_prints_every_scene_id(pack_dir):
    code, text = run("--list-scenes", "--pack", str(pack_dir))

    assert code == 0
    for scene_id in ("opening", "victory", "defeat"):
        assert scene_id in text


def test_list_scenes_does_not_play_the_show(pack_dir):
    _, text = run("--list-scenes", "--pack", str(pack_dir))

    assert "I don't like this." not in text


# ── budget ───────────────────────────────────────────────────────────────────
def test_a_looping_pack_stops_at_the_scene_budget(tmp_path):
    scenes = [{"id": "opening", "default_next": "opening", "beats": [
        {"type": "narration", "speaker": "gm", "text": "Monday again."}]}]
    campaign = dict(CAMPAIGN, players=[], primitives=[])
    root = write_pack(tmp_path / "loop", campaign=campaign, scenes=scenes,
                      cast={"gm": CAST["gm"]})

    code, text = run("--pack", str(root), "--dry-run", "--max-scenes", "3")

    assert code == 0
    assert text.count("Monday again.") == 3


# ── persistence ──────────────────────────────────────────────────────────────
def test_a_state_file_is_written_when_asked(pack_dir, tmp_path):
    state = tmp_path / "position.json"

    run("--pack", str(pack_dir), "--dry-run", "--state-file", str(state))

    assert state.exists()


def test_a_resumed_run_continues_instead_of_restarting(pack_dir, tmp_path):
    state = tmp_path / "position.json"
    run("--pack", str(pack_dir), "--dry-run", "--scene", "opening",
        "--max-scenes", "1", "--state-file", str(state))

    _, text = run("--pack", str(pack_dir), "--dry-run",
                  "--state-file", str(state), "--resume")

    assert "The hall is cold." not in text
    assert "The doors hold." in text


def test_resuming_without_a_state_file_fails(pack_dir):
    code, text = run("--pack", str(pack_dir), "--dry-run", "--resume")

    assert code == 1
    assert "state" in text.lower()


def test_resuming_a_corrupt_state_file_fails(pack_dir, tmp_path):
    state = tmp_path / "position.json"
    state.write_text("{not json")

    code, _ = run("--pack", str(pack_dir), "--dry-run",
                  "--state-file", str(state), "--resume")

    assert code == 1


def test_an_unexpected_error_is_not_disguised_as_an_operator_mistake(
        pack_dir, monkeypatch):
    """A bug in the runtime must surface, not be reported as a bad pack.

    Catching bare Exception around the play would turn every programming
    error into a one-line "ERROR: ..." and exit 1, which is the same signal a
    dangling branch gives — and the operator would go looking at their YAML.
    """
    class Exploding:
        def __init__(self, *args, **kwargs):
            self.state = type("S", (), {"scene_id": "opening"})()

        def run(self, **kwargs):
            raise ValueError("a bug, not a bad pack")

    monkeypatch.setattr(cli, "CampaignRuntime", Exploding)

    with pytest.raises(ValueError, match="a bug"):
        run("--pack", str(pack_dir), "--dry-run")


# ── argument handling ────────────────────────────────────────────────────────
def test_the_pack_argument_is_required():
    with pytest.raises(SystemExit):
        cli.main(["--dry-run"], out=Out())


def test_seeding_the_random_selector_is_reproducible(tmp_path):
    scenes = json.loads(json.dumps(SCENES))
    scenes[0]["branches"] = [
        {"id": "win", "next": "victory", "when": {"weight": 1}},
        {"id": "lose", "next": "defeat", "when": {"weight": 1}},
    ]
    root = write_pack(tmp_path / "weighted", scenes=scenes)

    _, first = run("--pack", str(root), "--dry-run", "--seed", "7")
    _, second = run("--pack", str(root), "--dry-run", "--seed", "7")

    assert first == second


def test_main_defaults_out_to_stdout(pack_dir, capsys):
    cli.main(["--pack", str(pack_dir), "--dry-run", "--no-pace", "--no-color",
              "--list-scenes"])

    assert "opening" in capsys.readouterr().out
