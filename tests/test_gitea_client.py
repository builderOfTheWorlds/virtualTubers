import base64
import json
import os

import httpx
import pytest

from gitea_client import GiteaClient, GiteaError, GiteaReadOnlyError

FAKE_TOKEN = "tok_GITEA_SECRET_abcdef123456"
BASE = "http://192.168.1.120:3300"
REPO = "/api/v1/repos/gitea_admin/fraud-stop"


class FakeGitea:
    """httpx.MockTransport handler: records requests, serves canned routes."""

    def __init__(self):
        self.requests = []
        self.routes = {}

    def on(self, method, path, status=200, body=None, handler=None):
        self.routes[(method, path)] = handler or (lambda req: httpx.Response(status, json=body))

    def __call__(self, request):
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key not in self.routes:
            return httpx.Response(404, json={"message": f"no route {key}"})
        return self.routes[key](request)

    def last(self):
        return self.requests[-1]

    def last_json(self):
        return json.loads(self.last().content)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN_OFFICE", FAKE_TOKEN)
    return FakeGitea()


@pytest.fixture
def client(fake):
    return GiteaClient(BASE, "gitea_admin", "fraud-stop", transport=httpx.MockTransport(fake))


def test_base_url_normalized_and_auth_header(fake, client):
    fake.on("GET", f"{REPO}/pulls", body=[])
    client.list_prs()
    req = fake.last()
    assert str(req.url).startswith(f"{BASE}/api/v1/repos/gitea_admin/fraud-stop/pulls")
    assert req.headers["Authorization"] == f"token {FAKE_TOKEN}"
    assert FAKE_TOKEN not in str(req.url)


def test_base_url_already_has_api_suffix(fake):
    c = GiteaClient(BASE + "/api/v1/", "gitea_admin", "fraud-stop", transport=httpx.MockTransport(fake))
    assert c.api_base == BASE + "/api/v1"


def test_open_issue_with_labels_resolves_and_creates_ids(fake, client):
    fake.on("GET", f"{REPO}/labels", body=[{"id": 3, "name": "directive"}])
    fake.on("POST", f"{REPO}/labels", status=201, body={"id": 9, "name": "urgent"})
    fake.on("POST", f"{REPO}/issues", status=201, body={"number": 7, "title": "Ship v1"})

    issue = client.open_issue("Ship v1", "body", labels=["directive", "urgent"])

    assert issue["number"] == 7
    assert fake.last_json() == {"title": "Ship v1", "body": "body", "labels": [3, 9]}
    created = [r for r in fake.requests if r.method == "POST" and r.url.path.endswith("/labels")]
    assert json.loads(created[0].content)["name"] == "urgent"


def test_ensure_label_is_cached(fake, client):
    fake.on("GET", f"{REPO}/labels", body=[{"id": 3, "name": "directive"}])
    assert client.ensure_label("directive") == 3
    assert client.ensure_label("directive") == 3
    assert len(fake.requests) == 1


def test_close_and_comment_issue(fake, client):
    fake.on("PATCH", f"{REPO}/issues/7", body={"number": 7, "state": "closed"})
    fake.on("POST", f"{REPO}/issues/7/comments", status=201, body={"id": 1})

    assert client.close_issue(7)["state"] == "closed"
    assert fake.last_json() == {"state": "closed"}
    client.comment_issue(7, "CI green")
    assert fake.last_json() == {"body": "CI green"}


def test_list_issues_paginates_and_filters(fake, client):
    def handler(req):
        page = int(req.url.params["page"])
        size = 50 if page == 1 else 3
        return httpx.Response(200, json=[{"number": page * 100 + i} for i in range(size)])

    fake.on("GET", f"{REPO}/issues", handler=handler)
    issues = client.list_issues(state="all", labels=["directive", "bug"])

    assert len(issues) == 53
    params = fake.last().url.params
    assert params["state"] == "all"
    assert params["type"] == "issues"
    assert params["labels"] == "directive,bug"
    assert len(fake.requests) == 2


def test_open_pr_and_list_prs(fake, client):
    fake.on("POST", f"{REPO}/pulls", status=201, body={"number": 12})
    fake.on("GET", f"{REPO}/pulls", body=[{"number": 12}])

    assert client.open_pr("feature/x", "main", "feat: x", "desc")["number"] == 12
    assert fake.last_json() == {"head": "feature/x", "base": "main", "title": "feat: x", "body": "desc"}
    assert client.list_prs("closed") == [{"number": 12}]
    assert fake.last().url.params["state"] == "closed"


@pytest.mark.parametrize("event,wire", [
    ("APPROVE", "APPROVED"), ("approve", "APPROVED"),
    ("REQUEST_CHANGES", "REQUEST_CHANGES"), ("COMMENT", "COMMENT"),
])
def test_review_pr_maps_events(fake, client, event, wire):
    fake.on("POST", f"{REPO}/pulls/12/reviews", body={"id": 1, "state": wire})
    client.review_pr(12, event, "lgtm")
    assert fake.last_json() == {"event": wire, "body": "lgtm"}


def test_review_pr_rejects_unknown_event(fake, client):
    with pytest.raises(ValueError):
        client.review_pr(12, "MERGE")
    assert fake.requests == []


@pytest.mark.parametrize("style,delete", [("merge", True), ("squash", False)])
def test_merge_pr_payload(fake, client, style, delete):
    fake.on("POST", f"{REPO}/pulls/12/merge", handler=lambda r: httpx.Response(200))
    assert client.merge_pr(12, style=style, delete_branch=delete) is True
    assert fake.last_json() == {"Do": style, "delete_branch_after_merge": delete}


def test_merge_pr_rejects_bad_style(client):
    with pytest.raises(ValueError):
        client.merge_pr(12, style="octopus")


def test_delete_branch_quotes_name(fake, client):
    fake.on("DELETE", f"{REPO}/branches/loop/2026-W40", handler=lambda r: httpx.Response(204))
    assert client.delete_branch("loop/2026-W40") is True
    assert fake.last().url.raw_path.endswith(b"/branches/loop%2F2026-W40")


def test_create_tag(fake, client):
    fake.on("POST", f"{REPO}/tags", status=201, body={"name": "loop-seed"})
    client.create_tag("loop-seed", "main", "seed")
    assert fake.last_json() == {"tag_name": "loop-seed", "target": "main", "message": "seed"}


def test_get_file_decodes_base64(fake, client):
    content = base64.b64encode("héllo\n".encode()).decode()
    fake.on("GET", f"{REPO}/contents/docs/design/a.md", body={"type": "file", "content": content})
    assert client.get_file("docs/design/a.md", ref="loop/w1") == "héllo\n"
    assert fake.last().url.params["ref"] == "loop/w1"


def test_get_file_on_directory_raises(fake, client):
    fake.on("GET", f"{REPO}/contents/src", body=[{"type": "file"}])
    with pytest.raises(GiteaError):
        client.get_file("src")


def test_http_error_carries_status_and_message_not_token(fake, client, caplog):
    caplog.set_level("DEBUG", logger="gitea_client")
    fake.on("POST", f"{REPO}/pulls/12/reviews", status=422,
            body={"message": f"approve your own pull is not allowed (token {FAKE_TOKEN})"})

    with pytest.raises(GiteaError) as exc:
        client.review_pr(12, "APPROVE")

    assert exc.value.status == 422
    assert "approve your own pull" in exc.value.message
    assert FAKE_TOKEN not in str(exc.value)
    assert FAKE_TOKEN not in caplog.text


def test_transport_error_wrapped(fake, client):
    def boom(req):
        raise httpx.ConnectError("connection refused", request=req)

    fake.on("GET", f"{REPO}/pulls", handler=boom)
    with pytest.raises(GiteaError) as exc:
        client.list_prs()
    assert exc.value.status is None


def test_token_never_logged(fake, client, caplog):
    caplog.set_level("DEBUG", logger="gitea_client")
    fake.on("GET", f"{REPO}/labels", body=[])
    fake.on("POST", f"{REPO}/labels", status=201, body={"id": 1, "name": "x"})
    fake.on("POST", f"{REPO}/issues", status=201, body={"number": 1})
    client.open_issue("t", "b", labels=["x"])
    assert FAKE_TOKEN not in caplog.text


def test_missing_token_sends_no_auth_header(fake, monkeypatch, client):
    monkeypatch.delenv("GITEA_TOKEN_OFFICE")
    fake.on("GET", f"{REPO}/pulls", body=[])
    client.list_prs()
    assert "Authorization" not in fake.last().headers


def test_custom_token_env(fake, monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN_OBSERVER", "observer-tok")
    c = GiteaClient(BASE, "gitea_admin", "fraud-stop", token_env="GITEA_TOKEN_OBSERVER",
                    transport=httpx.MockTransport(fake))
    fake.on("GET", f"{REPO}/pulls", body=[])
    c.list_prs()
    assert fake.last().headers["Authorization"] == "token observer-tok"


def test_token_env_must_be_a_name():
    with pytest.raises(ValueError) as exc:
        GiteaClient(BASE, "o", "r", token_env="abc def-123")
    assert "abc def-123" not in str(exc.value)


# ── read-only (observer) ─────────────────────────────────────────────────────

@pytest.fixture
def ro(fake):
    return GiteaClient(BASE, "gitea_admin", "fraud-stop", read_only=True, transport=httpx.MockTransport(fake))


@pytest.mark.parametrize("call", [
    lambda c: c.open_issue("t", labels=["x"]),
    lambda c: c.close_issue(1),
    lambda c: c.comment_issue(1, "b"),
    lambda c: c.open_pr("h", "main", "t"),
    lambda c: c.review_pr(1, "APPROVE"),
    lambda c: c.merge_pr(1),
    lambda c: c.delete_branch("b"),
    lambda c: c.create_tag("t", "main"),
    lambda c: c.ensure_label("new"),
])
def test_read_only_refuses_writes_before_network(fake, ro, call):
    fake.on("GET", f"{REPO}/labels", body=[])
    with pytest.raises(GiteaReadOnlyError):
        call(ro)
    assert all(r.method == "GET" for r in fake.requests)


def test_read_only_allows_reads(fake, ro):
    fake.on("GET", f"{REPO}/issues", body=[{"number": 1}])
    assert ro.list_issues() == [{"number": 1}]


# ── live integration (opt-in) ────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.skipif(
    not (os.environ.get("GITEA_TOKEN_OFFICE") and os.environ.get("GITEA_INTEGRATION") == "1"),
    reason="set GITEA_TOKEN_OFFICE and GITEA_INTEGRATION=1 to hit the live Gitea",
)
def test_live_list_issues_read_only():
    client = GiteaClient(
        os.environ.get("GITEA_BASE_URL", BASE),
        os.environ.get("GITEA_OWNER", "gitea_admin"),
        os.environ.get("GITEA_REPO", "fraud-stop"),
        read_only=True,
    )
    assert isinstance(client.list_issues(state="all"), list)
