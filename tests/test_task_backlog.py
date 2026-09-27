"""
test_task_backlog.py
app/task_backlog.py: the file source (ordering, done-state persistence
across a restart, live re-read, bad input) and the gitea source against a
mocked urlopen (label filter, start/done labels + comments, close_on_success,
network failure -> None, token never leaked), plus build_backlog().
"""
import io
import json
import urllib.error
import urllib.parse

import pytest
import yaml

import task_backlog
from task_backlog import FileBacklogSource, GiteaBacklogSource, build_backlog


# ── file source ──────────────────────────────────────────────────────────────

TASKS = [
    {"id": "t1", "title": "First", "body": "do one"},
    {"id": "t2", "title": "Second"},
    {"id": "t3", "title": "Third", "body": "do three"},
]


@pytest.fixture
def backlog_files(tmp_path):
    task_file = tmp_path / "backlog.yaml"
    task_file.write_text(yaml.safe_dump({"tasks": TASKS}))
    return task_file, tmp_path / "state" / "backlog_state.json"


def test_file_source_serves_tasks_in_file_order(backlog_files):
    task_file, state = backlog_files
    src = FileBacklogSource(str(task_file), str(state))
    task = src.next_task()
    assert task == {"id": "t1", "title": "First", "body": "do one", "source": "file"}
    # Started but not done: still the next task (a restart lost the chain).
    src.mark_started("t1", correlation_id="c1")
    assert src.next_task()["id"] == "t1"
    src.mark_done("t1", "milestone", correlation_id="c1")
    assert src.next_task()["id"] == "t2"


def test_file_source_done_state_survives_restart(backlog_files):
    task_file, state = backlog_files
    src = FileBacklogSource(str(task_file), str(state))
    src.mark_started("t1", correlation_id="c1")
    src.mark_done("t1", "blocker", correlation_id="c1")
    src.mark_done("t2", "escalation")

    restarted = FileBacklogSource(str(task_file), str(state))
    assert restarted.next_task()["id"] == "t3"
    saved = json.loads(state.read_text())
    assert saved["done"]["t1"]["outcome"] == "blocker"
    assert saved["done"]["t1"]["correlation_id"] == "c1"
    assert "t1" not in saved["started"]


def test_file_source_empty_when_all_done(backlog_files):
    task_file, state = backlog_files
    src = FileBacklogSource(str(task_file), str(state))
    for t in TASKS:
        src.mark_done(t["id"], "milestone")
    assert src.next_task() is None


def test_file_source_rereads_file_for_appended_tasks(backlog_files):
    task_file, state = backlog_files
    src = FileBacklogSource(str(task_file), str(state))
    for t in TASKS:
        src.mark_done(t["id"], "milestone")
    task_file.write_text(yaml.safe_dump({"tasks": TASKS + [{"id": "t4", "title": "Fourth"}]}))
    assert src.next_task()["id"] == "t4"


def test_file_source_accepts_json_list_and_title_as_id(tmp_path):
    task_file = tmp_path / "backlog.json"
    task_file.write_text(json.dumps([{"title": "No id here"}, {"body": "no title, skipped"}]))
    src = FileBacklogSource(str(task_file), str(tmp_path / "s.json"))
    assert src.next_task()["id"] == "No id here"
    src.mark_done("No id here", "milestone")
    assert src.next_task() is None


@pytest.mark.parametrize("content", [None, "just a string", "{bad: [yaml", "tasks: 5"])
def test_file_source_bad_or_missing_file_returns_none(tmp_path, content):
    task_file = tmp_path / "backlog.yaml"
    if content is not None:
        task_file.write_text(content)
    src = FileBacklogSource(str(task_file), str(tmp_path / "s.json"))
    assert src.next_task() is None


def test_file_source_corrupt_state_is_treated_as_empty(backlog_files):
    task_file, state = backlog_files
    state.parent.mkdir(parents=True)
    state.write_text("{not json")
    src = FileBacklogSource(str(task_file), str(state))
    assert src.next_task()["id"] == "t1"
    src.mark_done("t1", "milestone")
    assert src.next_task()["id"] == "t2"


def test_file_source_rejects_unknown_outcome(backlog_files):
    task_file, state = backlog_files
    with pytest.raises(ValueError):
        FileBacklogSource(str(task_file), str(state)).mark_done("t1", "shipped")


def test_example_backlog_parses_to_three_sandbox_tasks(tmp_path):
    import pathlib
    example = pathlib.Path(__file__).resolve().parents[1] / "config" / "backlog.example.yaml"
    src = FileBacklogSource(str(example), str(tmp_path / "s.json"))
    ids = []
    while (task := src.next_task()) is not None:
        ids.append(task["id"])
        src.mark_done(task["id"], "milestone")
    assert len(ids) == 3 and len(set(ids)) == 3


# ── gitea source ─────────────────────────────────────────────────────────────

TOKEN = "s3cret-token-value"


def _issue(number, labels, title=None, pull_request=None):
    return {"number": number, "title": title or f"Issue {number}", "body": f"body {number}",
            "labels": [{"name": n} for n in labels], "pull_request": pull_request}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeGitea:
    """urlopen stand-in: routes (method, path) to canned JSON and records
    every request."""

    def __init__(self, issues=None, labels=None, fail=None):
        self.issues = issues or []
        self.labels = labels if labels is not None else [
            {"id": 1, "name": "stream-task"}, {"id": 2, "name": "in-progress"},
            {"id": 3, "name": "needs-human"}]
        self.fail = fail
        self.requests = []

    def __call__(self, req, timeout=None):
        parsed = urllib.parse.urlparse(req.full_url)
        body = json.loads(req.data) if req.data else None
        self.requests.append({"method": req.get_method(), "path": parsed.path,
                              "query": urllib.parse.parse_qs(parsed.query), "body": body,
                              "auth": req.get_header("Authorization")})
        if self.fail == "url":
            raise urllib.error.URLError("connection refused")
        if self.fail == "http":
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(b"err"))
        if req.get_method() == "GET" and parsed.path.endswith("/issues"):
            return _Resp(json.dumps(self.issues).encode())
        if req.get_method() == "GET" and parsed.path.endswith("/labels"):
            return _Resp(json.dumps(self.labels).encode())
        return _Resp(b"{}")

    def calls(self, method, suffix=""):
        return [r for r in self.requests if r["method"] == method and r["path"].endswith(suffix)]


def _gitea(fake, **kw):
    return GiteaBacklogSource("http://gitea.local:3300/", "owner", "repo",
                              token=TOKEN, opener=fake, **kw)


def test_gitea_next_task_filters_by_label_and_skips_busy_issues():
    fake = FakeGitea(issues=[
        _issue(7, ["stream-task"]),
        _issue(3, ["stream-task", "in-progress"]),
        _issue(4, ["stream-task", "needs-human"]),
        _issue(5, ["other"]),
        _issue(2, ["stream-task"], pull_request={"merged": False}),
    ])
    task = _gitea(fake).next_task()
    assert task == {"id": "7", "title": "Issue 7", "body": "body 7", "source": "gitea"}
    req = fake.calls("GET", "/issues")[0]
    assert req["path"] == "/api/v1/repos/owner/repo/issues"
    assert req["query"]["labels"] == ["stream-task"]
    assert req["query"]["state"] == ["open"]
    assert req["auth"] == f"token {TOKEN}"


def test_gitea_next_task_oldest_first():
    fake = FakeGitea(issues=[_issue(9, ["stream-task"]), _issue(4, ["stream-task"])])
    assert _gitea(fake).next_task()["id"] == "4"


def test_gitea_mark_started_adds_label_and_comments_correlation():
    fake = FakeGitea(issues=[_issue(4, ["stream-task"]), _issue(5, ["stream-task"])])
    src = _gitea(fake)
    src.mark_started("4", correlation_id="corr-1")
    assert fake.calls("POST", "/issues/4/labels")[0]["body"] == {"labels": [2]}
    assert "corr-1" in fake.calls("POST", "/issues/4/comments")[0]["body"]["body"]
    # Remembered in-process even though the fake still lists it unlabelled.
    assert src.next_task()["id"] == "5"


@pytest.mark.parametrize("outcome, close_on_success, expect_close, expect_blocked", [
    ("milestone", True, True, False),
    ("milestone", False, False, False),
    ("escalation", True, False, True),
    ("blocker", True, False, True),
])
def test_gitea_mark_done(outcome, close_on_success, expect_close, expect_blocked):
    fake = FakeGitea()
    _gitea(fake, close_on_success=close_on_success).mark_done("4", outcome, correlation_id="corr-9")
    comment = fake.calls("POST", "/issues/4/comments")[0]["body"]["body"]
    assert outcome in comment and "corr-9" in comment
    assert fake.calls("DELETE", "/issues/4/labels/2")  # in-progress removed
    closes = fake.calls("PATCH", "/issues/4")
    assert bool(closes) == expect_close
    if expect_close:
        assert closes[0]["body"] == {"state": "closed"}
    blocked = [r for r in fake.calls("POST", "/issues/4/labels") if r["body"] == {"labels": [3]}]
    assert bool(blocked) == expect_blocked


def test_gitea_missing_label_on_repo_still_comments():
    fake = FakeGitea(labels=[{"id": 1, "name": "stream-task"}])
    _gitea(fake).mark_started("4", correlation_id="c")
    assert not fake.calls("POST", "/issues/4/labels")
    assert fake.calls("POST", "/issues/4/comments")


@pytest.mark.parametrize("fail", ["url", "http"])
def test_gitea_network_failure_returns_none_and_never_raises(fail, capsys):
    fake = FakeGitea(issues=[_issue(1, ["stream-task"])], fail=fail)
    src = _gitea(fake)
    assert src.next_task() is None
    src.mark_started("1", correlation_id="c")
    src.mark_done("1", "milestone", correlation_id="c")
    out = capsys.readouterr().out
    assert "WARN" in out
    assert TOKEN not in out


def test_gitea_without_token_idles(monkeypatch):
    monkeypatch.delenv("GITEA_TOKEN", raising=False)
    fake = FakeGitea(issues=[_issue(1, ["stream-task"])])
    src = GiteaBacklogSource("http://g", "o", "r", opener=fake)
    assert src.next_task() is None
    assert fake.requests == []


def test_gitea_token_read_from_env(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "env-token")
    fake = FakeGitea(issues=[_issue(1, ["stream-task"])])
    GiteaBacklogSource("http://g", "o", "r", opener=fake).next_task()
    assert fake.requests[0]["auth"] == "token env-token"


# ── factory ──────────────────────────────────────────────────────────────────

def test_build_backlog_disabled_by_default():
    assert build_backlog(None) is None
    assert build_backlog({}) is None
    assert build_backlog({"enabled": False, "source": "file"}) is None


def test_build_backlog_file_and_gitea(tmp_path, monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "x")
    src = build_backlog({"enabled": True, "source": "file", "file_path": "a.yaml",
                         "state_path": str(tmp_path / "s.json")})
    assert isinstance(src, FileBacklogSource) and src.file_path == "a.yaml"
    src = build_backlog({"enabled": True, "source": "gitea",
                         "gitea": {"base_url": "http://g", "owner": "o", "repo": "r",
                                   "label": "L", "close_on_success": True}})
    assert isinstance(src, GiteaBacklogSource)
    assert (src.label, src.close_on_success) == ("L", True)


def test_build_backlog_unknown_source_is_none():
    assert build_backlog({"enabled": True, "source": "jira"}) is None


def test_outcomes_constant():
    assert task_backlog.OUTCOMES == ("milestone", "escalation", "blocker")
