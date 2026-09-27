"""
task_backlog.py
Pluggable task sources the manager pulls from when the dev team is idle
(docs/task_backlog.md). Opt-in via `agent.backlog.enabled` — nothing here
runs unless the manager's config turns it on.

Every source implements the same three calls:

    next_task() -> {"id", "title", "body", "source"} | None
    mark_started(task_id, correlation_id=None)
    mark_done(task_id, outcome, correlation_id=None)   # outcome in OUTCOMES

Sources never raise out of these calls: a bad file, an unreachable Gitea or
a missing token logs a WARN and next_task() returns None ("nothing to do
right now"), so a broken backlog can never crash the agent loop.

Two sources:

* `file`  — a YAML/JSON list of tasks; done-state is persisted to a small
  JSON state file so a restart doesn't re-run finished tasks.
* `gitea` — open issues carrying a label on a configured repo; start/done
  are recorded as labels + comments on the issue. Token from env
  GITEA_TOKEN only (never config, never logged).
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

from relay_io import atomic_write_json, read_json


OUTCOMES = ("milestone", "escalation", "blocker")

DEFAULT_FILE_PATH = "/config/backlog.yaml"
DEFAULT_STATE_PATH = "/data/world-state/backlog_state.json"
DEFAULT_GITEA_LABEL = "stream-task"
DEFAULT_IN_PROGRESS_LABEL = "in-progress"
DEFAULT_BLOCKED_LABEL = "needs-human"
GITEA_TOKEN_ENV = "GITEA_TOKEN"
GITEA_TIMEOUT_S = 10
GITEA_PAGE_LIMIT = 50
GITEA_MAX_PAGES = 10


def _log(level, event, **fields):
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"[backlog] {level} {event} {kv}".rstrip())


def _check_outcome(outcome):
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")


def _task_text(title, body):
    return {"title": str(title).strip(), "body": str(body or "").strip()}


class BacklogSource:
    """Interface. `name` is reported in each task's "source" field."""

    name = "base"

    def next_task(self):
        raise NotImplementedError

    def mark_started(self, task_id, correlation_id=None):
        raise NotImplementedError

    def mark_done(self, task_id, outcome, correlation_id=None):
        raise NotImplementedError


# ── file source ──────────────────────────────────────────────────────────────
class FileBacklogSource(BacklogSource):
    """Tasks from a YAML/JSON file (a list, or a mapping with a `tasks`
    list). Each item: {id, title, body?}; `id` falls back to the title.
    Items are served in file order. The file is re-read on every
    next_task(), so an operator can append tasks without a restart.

    Done-state lives in `state_path` (JSON: {"started": {id: {...}},
    "done": {id: {"outcome", ...}}}). A task that is done — whatever the
    outcome — is never served again; a task that was started but never
    finished (the worker restarted mid-chain, so its chain is lost) is
    served again.
    """

    name = "file"

    def __init__(self, file_path=DEFAULT_FILE_PATH, state_path=DEFAULT_STATE_PATH):
        self.file_path = file_path
        self.state_path = state_path

    def _load_tasks(self):
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as exc:
            _log("WARN", "file_unreadable", path=self.file_path, error=exc)
            return []
        if isinstance(data, dict):
            data = data.get("tasks")
        if not isinstance(data, list):
            _log("WARN", "file_not_a_list", path=self.file_path)
            return []
        tasks = []
        for index, item in enumerate(data):
            if not isinstance(item, dict) or not item.get("title"):
                _log("WARN", "file_item_skipped", path=self.file_path, index=index,
                     reason="not a mapping with a title")
                continue
            task_id = str(item.get("id") or item["title"]).strip()
            tasks.append({"id": task_id, **_task_text(item["title"], item.get("body")),
                          "source": self.name})
        return tasks

    def _load_state(self):
        state = read_json(self.state_path)
        if not isinstance(state, dict):
            state = {}
        state.setdefault("started", {})
        state.setdefault("done", {})
        return state

    def _save_state(self, state):
        try:
            directory = os.path.dirname(self.state_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            atomic_write_json(self.state_path, state, fsync=True)
        except (OSError, TypeError, ValueError) as exc:
            _log("WARN", "state_write_failed", path=self.state_path, error=exc)

    def next_task(self):
        done = self._load_state()["done"]
        for task in self._load_tasks():
            if task["id"] not in done:
                _log("DEBUG", "next_task", source=self.name, id=task["id"])
                return task
        return None

    def mark_started(self, task_id, correlation_id=None):
        state = self._load_state()
        state["started"][task_id] = {"at": time.time(), "correlation_id": correlation_id}
        self._save_state(state)

    def mark_done(self, task_id, outcome, correlation_id=None):
        _check_outcome(outcome)
        state = self._load_state()
        state["started"].pop(task_id, None)
        state["done"][task_id] = {"outcome": outcome, "at": time.time(),
                                  "correlation_id": correlation_id}
        self._save_state(state)


# ── gitea source ─────────────────────────────────────────────────────────────
class GiteaBacklogSource(BacklogSource):
    """Open issues labelled `label` on {base_url}/{owner}/{repo}, oldest
    first. Issues already carrying `in_progress_label` or `blocked_label`
    are skipped.

    mark_started adds `in_progress_label` and comments the correlation id.
    mark_done comments the outcome + correlation id and removes
    `in_progress_label`; a milestone closes the issue when
    `close_on_success`, an escalation/blocker adds `blocked_label` so the
    issue is not picked up again until a human removes that label. Label
    names are resolved to ids once via the repo's labels list; a label that
    doesn't exist on the repo is skipped with a WARN (comments still land).
    Ids handled in this process are also remembered in memory, so a failed
    label write can't make the same issue come round again before a restart.

    `opener` is urllib.request.urlopen, injectable for tests.
    """

    name = "gitea"

    def __init__(self, base_url, owner, repo, label=DEFAULT_GITEA_LABEL,
                 close_on_success=False, in_progress_label=DEFAULT_IN_PROGRESS_LABEL,
                 blocked_label=DEFAULT_BLOCKED_LABEL, token=None, opener=None,
                 timeout_s=GITEA_TIMEOUT_S):
        self.base_url = (base_url or "").rstrip("/")
        self.owner = owner
        self.repo = repo
        self.label = label
        self.close_on_success = bool(close_on_success)
        self.in_progress_label = in_progress_label
        self.blocked_label = blocked_label
        self._token = token if token is not None else os.environ.get(GITEA_TOKEN_ENV, "").strip()
        self._opener = opener or urllib.request.urlopen
        self.timeout_s = timeout_s
        self._label_ids = None
        self._handled = set()
        if not self._token:
            _log("WARN", "gitea_no_token", env=GITEA_TOKEN_ENV)

    # -- http --
    def _api(self, method, path, payload=None):
        """JSON request against /api/v1. Returns (ok, body). Never raises;
        never includes the token in anything it logs."""
        url = f"{self.base_url}/api/v1{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"token {self._token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        _log("DEBUG", "gitea_request", method=method, path=path)
        try:
            with self._opener(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode()
            return True, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            _log("WARN", "gitea_http_error", method=method, path=path, status=exc.code)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            _log("WARN", "gitea_unreachable", method=method, path=path, error=exc)
        return False, None

    def _repo_path(self, suffix=""):
        owner = urllib.parse.quote(str(self.owner), safe="")
        repo = urllib.parse.quote(str(self.repo), safe="")
        return f"/repos/{owner}/{repo}{suffix}"

    def _configured(self):
        return bool(self._token and self.base_url and self.owner and self.repo)

    # -- labels --
    def _label_id(self, name):
        if not name:
            return None
        if self._label_ids is None:
            ok, labels = self._api("GET", self._repo_path(f"/labels?limit={GITEA_PAGE_LIMIT}"))
            if not ok:
                return None  # retry the lookup next time
            self._label_ids = {lab.get("name"): lab.get("id")
                               for lab in (labels or []) if isinstance(lab, dict)}
        label_id = self._label_ids.get(name)
        if label_id is None:
            _log("WARN", "gitea_label_missing", label=name, repo=f"{self.owner}/{self.repo}")
        return label_id

    def _add_label(self, number, name):
        label_id = self._label_id(name)
        if label_id is not None:
            self._api("POST", self._repo_path(f"/issues/{number}/labels"), {"labels": [label_id]})

    def _remove_label(self, number, name):
        label_id = self._label_id(name)
        if label_id is not None:
            self._api("DELETE", self._repo_path(f"/issues/{number}/labels/{label_id}"))

    def _comment(self, number, body):
        self._api("POST", self._repo_path(f"/issues/{number}/comments"), {"body": body})

    # -- interface --
    def _open_issues(self):
        issues = []
        label = urllib.parse.quote(str(self.label), safe="")
        for page in range(1, GITEA_MAX_PAGES + 1):
            ok, batch = self._api("GET", self._repo_path(
                f"/issues?state=open&type=issues&labels={label}"
                f"&limit={GITEA_PAGE_LIMIT}&page={page}"))
            if not ok:
                return None
            batch = batch or []
            issues.extend(i for i in batch if isinstance(i, dict))
            if len(batch) < GITEA_PAGE_LIMIT:
                break
        return issues

    def next_task(self):
        if not self._configured():
            return None
        issues = self._open_issues()
        if issues is None:
            return None
        skip_labels = {self.in_progress_label, self.blocked_label} - {None, ""}
        for issue in sorted(issues, key=lambda i: i.get("number", 0)):
            if issue.get("pull_request"):
                continue
            names = {lab.get("name") for lab in issue.get("labels") or [] if isinstance(lab, dict)}
            if self.label not in names:  # belt and braces over the server filter
                continue
            if names & skip_labels:
                continue
            task_id = str(issue.get("number"))
            if task_id in self._handled:
                continue
            _log("DEBUG", "next_task", source=self.name, id=task_id)
            return {"id": task_id, **_task_text(issue.get("title", ""), issue.get("body")),
                    "source": self.name}
        return None

    def mark_started(self, task_id, correlation_id=None):
        self._handled.add(str(task_id))
        if not self._configured():
            return
        self._add_label(task_id, self.in_progress_label)
        self._comment(task_id, f"Picked up by the stream manager (correlation_id=`{correlation_id}`).")

    def mark_done(self, task_id, outcome, correlation_id=None):
        _check_outcome(outcome)
        self._handled.add(str(task_id))
        if not self._configured():
            return
        self._comment(task_id, f"Stream outcome: **{outcome}** (correlation_id=`{correlation_id}`).")
        self._remove_label(task_id, self.in_progress_label)
        if outcome == "milestone":
            if self.close_on_success:
                self._api("PATCH", self._repo_path(f"/issues/{task_id}"), {"state": "closed"})
        else:
            self._add_label(task_id, self.blocked_label)


# ── factory ──────────────────────────────────────────────────────────────────
def build_backlog(backlog_config):
    """The configured source for `agent.backlog`, or None when the backlog
    is disabled / misconfigured (logged). Never raises."""
    cfg = backlog_config or {}
    if not cfg.get("enabled"):
        return None
    source = cfg.get("source", "file")
    if source == "file":
        return FileBacklogSource(cfg.get("file_path") or DEFAULT_FILE_PATH,
                                 cfg.get("state_path") or DEFAULT_STATE_PATH)
    if source == "gitea":
        g = cfg.get("gitea") or {}
        return GiteaBacklogSource(
            g.get("base_url"), g.get("owner"), g.get("repo"),
            label=g.get("label") or DEFAULT_GITEA_LABEL,
            close_on_success=g.get("close_on_success", False),
            in_progress_label=g.get("in_progress_label", DEFAULT_IN_PROGRESS_LABEL),
            blocked_label=g.get("blocked_label", DEFAULT_BLOCKED_LABEL),
        )
    _log("WARN", "unknown_source", source=source)
    return None
