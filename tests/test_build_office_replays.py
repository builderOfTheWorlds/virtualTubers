"""Tests for scripts/build_office_replays.py (OB-33). A small synthetic
sessionCorpus JSONL fixture and a fake HTTP client — no network, no real
session content."""
import importlib.util
import json
import pathlib

import pytest

from episode_validator import validate_episode

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "build_office_replays.py"


@pytest.fixture(scope="module")
def bor():
    spec = importlib.util.spec_from_file_location("_build_office_replays", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ev(kind, **kw):
    base = {"seq": 0, "ts": "2026-09-01T10:00:00Z", "type": kind, "text": None,
            "tool": None, "input": None, "output": None, "error": False}
    base.update(kw)
    return base


def record(session_id, events=None, tags=None):
    rec = {"source_tool": "claude_code", "host": "devbox", "project": "demo-project",
           "session_id": session_id, "started_at": "2026-09-01T10:00:00Z",
           "ended_at": "2026-09-01T11:00:00Z", "model": "test-model",
           "events": events if events is not None else [
               ev("user_message", text="Add a greeting function to the app"),
               ev("assistant_text", text="Plan: read the module, add the function, test it."),
               ev("tool_call", tool="Read", input={"file_path": "src/app.py"}, output="def main(): pass"),
               ev("tool_call", tool="Edit", input={"file_path": "src/app.py", "old_string": "pass",
                                                   "new_string": "return greet()"}),
               ev("tool_call", tool="Bash", input={"command": "python -m pytest -q"}, output="3 passed"),
               ev("assistant_text", text="Done: greeting added and tests pass."),
           ]}
    if tags is not None:
        rec["tags"] = tags
    return rec


@pytest.fixture
def export(tmp_path):
    lines = [
        json.dumps(record("sess-a", tags=["feature"])),
        "",
        "{not json",
        json.dumps(record("sess-b", tags=["bugfix"])),
        json.dumps(record("sess-short", events=[ev("user_message", text="hi")])),
        json.dumps({"no_events": True}),
    ]
    path = tmp_path / "corpus_export.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data)

    def json(self):
        return self._data


class FakePost:
    """Records every call; answers from `responses` (by name) or 200 draft."""

    def __init__(self, responses=None, raises=None):
        self.calls = []
        self.responses = responses or {}
        self.raises = raises

    def __call__(self, url, content, headers, params, timeout):
        self.calls.append({"url": url, "content": content, "headers": headers,
                           "params": params, "timeout": timeout})
        if self.raises:
            raise self.raises
        name = params.get("name")
        if name in self.responses:
            return self.responses[name]
        return FakeResponse(200, {"name": name, "created": True, "status": "draft"})


def test_iter_records_skips_bad_lines_and_filters_tag(bor, export):
    assert [r["session_id"] for r in bor.iter_records(export)] == ["sess-a", "sess-b", "sess-short"]
    assert [r["session_id"] for r in bor.iter_records(export, tag="feature")] == ["sess-a"]
    assert [r["session_id"] for r in bor.iter_records(export, sessions=["sess-b"])] == ["sess-b"]


def test_dry_run_writes_valid_episodes_and_uploads_nothing(bor, export, tmp_path):
    post = FakePost()
    out = tmp_path / "out"
    summary = bor.run(export, out=out, dry_run=True, http_post=post)
    assert post.calls == []
    assert summary["built"] == 2 and summary["too_short"] == 1 and summary["written"] == 2
    files = sorted(p.name for p in out.iterdir())
    assert files == ["office-claude_code-sess-a.json", "office-claude_code-sess-b.json"]
    episode = json.loads((out / files[0]).read_text(encoding="utf-8"))
    assert all(e["speaker"].startswith("tuber_") for e in episode["events"])
    assert validate_episode(episode)["name"] == "office-claude_code-sess-a"


def test_dry_run_without_out_is_an_error(bor, export):
    with pytest.raises(ValueError):
        bor.run(export, dry_run=True)


def test_upload_posts_drafts_only_and_never_approves(bor, export):
    post = FakePost()
    summary = bor.run(export, message_api_url="http://mapi:8090/", http_post=post)
    assert summary["submitted"] == 2 and summary["upload_failed"] == 0
    assert len(post.calls) == 2
    for call in post.calls:
        assert call["url"] == "http://mapi:8090/replays"   # never /approve
        assert call["params"]["status"] == "draft"
        assert call["params"]["uploaded_by"] == "build_office_replays"
        assert call["headers"]["Content-Type"] == "application/json"
        body = json.loads(call["content"])
        assert body["source"] == call["params"]["name"]


def test_upload_409_counts_as_exists_not_failure(bor, export):
    post = FakePost(responses={"office-claude_code-sess-b": FakeResponse(409, {"detail": "already exists"})})
    summary = bor.run(export, http_post=post)
    assert summary["submitted"] == 1 and summary["exists"] == 1 and summary["upload_failed"] == 0


def test_upload_rejection_and_unreachable_are_failures(bor, export):
    post = FakePost(responses={"office-claude_code-sess-a": FakeResponse(400, {"detail": "bad shape"})})
    summary = bor.run(export, http_post=post)
    assert summary["upload_failed"] == 1 and summary["submitted"] == 1
    bad = [o for o in summary["outcomes"] if o["status"] == "failed"][0]
    assert bad["http_status"] == 400 and bad["error"] == "bad shape"

    down = bor.run(export, http_post=FakePost(raises=ConnectionError("refused")))
    assert down["upload_failed"] == 2


def test_upload_server_storing_non_draft_is_a_failure(bor, export):
    post = FakePost(responses={"office-claude_code-sess-a": FakeResponse(200, {"status": "approved"})})
    summary = bor.run(export, http_post=post, sessions=["sess-a"])
    assert summary["upload_failed"] == 1


def test_attribution_failure_is_counted_and_skipped(bor, export, monkeypatch):
    def boom(record, **kw):
        raise bor.AttributionError("episode failed the leak audit; refusing to emit it")
    monkeypatch.setattr(bor, "attribute", boom)
    summary = bor.run(export, http_post=FakePost())
    assert summary["failed"] == 3 and summary["built"] == 0


def test_limit_and_min_events(bor, export):
    episodes, stats = bor.build_episodes(bor.iter_records(export), limit=1)
    assert len(episodes) == 1
    episodes, stats = bor.build_episodes(bor.iter_records(export), min_events=1)
    assert stats["built"] == 3


@pytest.mark.parametrize("argv_extra,expected_code,expect_calls", [
    (["--dry-run"], 0, 0),
    ([], 0, 2),
])
def test_main_exit_codes(bor, export, tmp_path, argv_extra, expected_code, expect_calls):
    post = FakePost()
    code = bor.main([str(export), "--out", str(tmp_path / "o"), *argv_extra], http_post=post)
    assert code == expected_code
    assert len(post.calls) == expect_calls


def test_main_missing_export_returns_2(bor, tmp_path):
    assert bor.main([str(tmp_path / "nope.jsonl"), "--dry-run", "--out", str(tmp_path)]) == 2


def test_main_upload_failure_returns_1(bor, export):
    assert bor.main([str(export)], http_post=FakePost(raises=ConnectionError("x"))) == 1
