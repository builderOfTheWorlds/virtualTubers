"""
gitea_client.py
Thin wrapper over the Gitea REST API v1 (server 1.22.x) for the office loop:
issues (CEO directives), PRs (every lane), reviews/merges (Tech Lead),
branch deletes (Office Manager GC), tags (weekly loop-seed) and file reads.

Auth is the `Authorization: token <T>` header. The token is read from an
environment variable whose NAME is configured (default GITEA_TOKEN_OFFICE) at
request time; it is never logged, never put in URLs, and never included in a
GiteaError message.

read_only=True (the Party Member / observer) makes every non-GET request raise
GiteaReadOnlyError before anything touches the network.
"""
import base64
import logging
import os
import re
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

DEFAULT_TOKEN_ENV = "GITEA_TOKEN_OFFICE"
DEFAULT_TIMEOUT = 15.0
PAGE_LIMIT = 50
MAX_PAGES = 40
DEFAULT_LABEL_COLOR = "#8c8c8c"

_REVIEW_EVENTS = {
    "APPROVE": "APPROVED",
    "APPROVED": "APPROVED",
    "REQUEST_CHANGES": "REQUEST_CHANGES",
    "COMMENT": "COMMENT",
}
_MERGE_STYLES = ("merge", "squash", "rebase", "rebase-merge", "fast-forward-only")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class GiteaError(RuntimeError):
    """A Gitea API call failed. `status` is the HTTP status (None for
    transport errors); `message` is Gitea's message. Never carries the token."""

    def __init__(self, status, message, method=None, path=None):
        self.status = status
        self.message = message
        self.method = method
        self.path = path
        where = f"{method} {path} " if method else ""
        super().__init__(f"Gitea {where}failed ({status}): {message}")


class GiteaReadOnlyError(GiteaError):
    """A write was attempted on a read-only client (the observer)."""

    def __init__(self, method, path):
        super().__init__(None, "client is read-only; write refused", method, path)


def _normalize_base(base_url):
    base = base_url.rstrip("/")
    if not base.endswith("/api/v1"):
        base += "/api/v1"
    return base


class GiteaClient:
    def __init__(
        self,
        base_url,
        owner,
        repo,
        token_env=DEFAULT_TOKEN_ENV,
        timeout=DEFAULT_TIMEOUT,
        read_only=False,
        transport=None,
    ):
        if not _ENV_NAME_RE.match(token_env or ""):
            # Guards against someone passing the token itself instead of the var name.
            raise ValueError("token_env must be an environment variable NAME (e.g. GITEA_TOKEN_OFFICE)")
        self.api_base = _normalize_base(base_url)
        self.owner = owner
        self.repo = repo
        self.token_env = token_env
        self.timeout = timeout
        self.read_only = read_only
        self._transport = transport
        self._label_ids = {}
        log.debug("gitea.init base=%s owner=%s repo=%s token_env=%s read_only=%s",
                  self.api_base, owner, repo, token_env, read_only)

    # ── plumbing ─────────────────────────────────────────────────────────────

    @property
    def _repo_path(self):
        return f"/repos/{quote(self.owner, safe='')}/{quote(self.repo, safe='')}"

    def _headers(self):
        headers = {"Accept": "application/json"}
        token = os.environ.get(self.token_env, "").strip()
        if token:
            headers["Authorization"] = f"token {token}"
        else:
            log.warning("gitea.auth event=no_token token_env=%s (request is anonymous)", self.token_env)
        return headers

    def _scrub(self, text):
        token = os.environ.get(self.token_env, "").strip()
        text = str(text)
        return text.replace(token, "***") if token else text

    def _request(self, method, path, json=None, params=None, expect_json=True):
        method = method.upper()
        if self.read_only and method != "GET":
            log.error("gitea.request event=read_only_refused method=%s path=%s", method, path)
            raise GiteaReadOnlyError(method, path)
        url = self.api_base + path
        log.debug("gitea.request event=enter method=%s path=%s params=%s", method, path, params)
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                resp = client.request(method, url, json=json, params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            msg = self._scrub(f"{type(exc).__name__}: {exc}")
            log.error("gitea.request event=transport_error method=%s path=%s error=%s", method, path, msg)
            raise GiteaError(None, msg, method, path) from None
        log.debug("gitea.request event=exit method=%s path=%s status=%s", method, path, resp.status_code)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                message = body.get("message") or body.get("errors") or resp.text
            except ValueError:
                message = resp.text
            message = self._scrub(message or resp.reason_phrase)[:500]
            log.error("gitea.request event=http_error method=%s path=%s status=%s message=%s",
                      method, path, resp.status_code, message)
            raise GiteaError(resp.status_code, message, method, path)
        if not expect_json or resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def _paged(self, path, params):
        results = []
        for page in range(1, MAX_PAGES + 1):
            batch = self._request("GET", path, params={**params, "page": page, "limit": PAGE_LIMIT}) or []
            results.extend(batch)
            if len(batch) < PAGE_LIMIT:
                break
        else:
            log.warning("gitea.paged event=page_cap path=%s pages=%s", path, MAX_PAGES)
        return results

    # ── labels ───────────────────────────────────────────────────────────────

    def list_labels(self):
        labels = self._paged(f"{self._repo_path}/labels", {})
        for label in labels:
            self._label_ids[label["name"]] = label["id"]
        return labels

    def ensure_label(self, name, color=DEFAULT_LABEL_COLOR):
        """Return the id of label `name`, creating it if missing (Gitea issue
        endpoints take label ids, not names)."""
        if name in self._label_ids:
            return self._label_ids[name]
        self.list_labels()
        if name in self._label_ids:
            return self._label_ids[name]
        log.debug("gitea.ensure_label event=create name=%s", name)
        created = self._request("POST", f"{self._repo_path}/labels", json={"name": name, "color": color}) or {}
        self._label_ids[name] = created["id"]
        log.info("gitea.ensure_label event=created name=%s id=%s", name, created["id"])
        return created["id"]

    # ── issues ───────────────────────────────────────────────────────────────

    def open_issue(self, title, body="", labels=None):
        if self.read_only:
            raise GiteaReadOnlyError("POST", f"{self._repo_path}/issues")
        payload = {"title": title, "body": body}
        if labels:
            payload["labels"] = [self.ensure_label(name) for name in labels]
        issue = self._request("POST", f"{self._repo_path}/issues", json=payload) or {}
        log.info("gitea.open_issue event=done number=%s title=%r", issue.get("number"), title)
        return issue

    def close_issue(self, number):
        issue = self._request("PATCH", f"{self._repo_path}/issues/{int(number)}", json={"state": "closed"})
        log.info("gitea.close_issue event=done number=%s", number)
        return issue

    def comment_issue(self, number, body):
        """Comment on an issue or PR (Gitea shares the numbering)."""
        comment = self._request("POST", f"{self._repo_path}/issues/{int(number)}/comments", json={"body": body})
        log.info("gitea.comment_issue event=done number=%s", number)
        return comment

    def list_issues(self, state="open", labels=None):
        """Issues only (PRs excluded). state: open|closed|all."""
        params = {"state": state, "type": "issues"}
        if labels:
            params["labels"] = ",".join(labels)
        return self._paged(f"{self._repo_path}/issues", params)

    # ── pull requests ────────────────────────────────────────────────────────

    def open_pr(self, head, base, title, body=""):
        pr = self._request("POST", f"{self._repo_path}/pulls",
                           json={"head": head, "base": base, "title": title, "body": body}) or {}
        log.info("gitea.open_pr event=done number=%s head=%s base=%s", pr.get("number"), head, base)
        return pr

    def list_prs(self, state="open"):
        return self._paged(f"{self._repo_path}/pulls", {"state": state})

    def review_pr(self, number, event, body=""):
        """event: APPROVE | REQUEST_CHANGES | COMMENT. Note Gitea refuses
        approve/request-changes on your own PR (same user as the token)."""
        key = (event or "").upper()
        if key not in _REVIEW_EVENTS:
            raise ValueError(f"event must be one of APPROVE, REQUEST_CHANGES, COMMENT; got {event!r}")
        review = self._request("POST", f"{self._repo_path}/pulls/{int(number)}/reviews",
                               json={"event": _REVIEW_EVENTS[key], "body": body})
        log.info("gitea.review_pr event=done number=%s review=%s", number, _REVIEW_EVENTS[key])
        return review

    def merge_pr(self, number, style="merge", delete_branch=True, message=None):
        if style not in _MERGE_STYLES:
            raise ValueError(f"style must be one of {_MERGE_STYLES}; got {style!r}")
        payload = {"Do": style, "delete_branch_after_merge": bool(delete_branch)}
        if message:
            payload["MergeMessageField"] = message
        self._request("POST", f"{self._repo_path}/pulls/{int(number)}/merge", json=payload, expect_json=False)
        log.info("gitea.merge_pr event=done number=%s style=%s delete_branch=%s", number, style, delete_branch)
        return True

    # ── branches, tags, files ────────────────────────────────────────────────

    def delete_branch(self, name):
        self._request("DELETE", f"{self._repo_path}/branches/{quote(name, safe='')}", expect_json=False)
        log.info("gitea.delete_branch event=done name=%s", name)
        return True

    def create_tag(self, name, target, message=""):
        """target: branch name or commit sha."""
        tag = self._request("POST", f"{self._repo_path}/tags",
                            json={"tag_name": name, "target": target, "message": message})
        log.info("gitea.create_tag event=done name=%s target=%s", name, target)
        return tag

    def get_file(self, path, ref=None):
        """Decoded UTF-8 text of `path` at `ref` (default branch when None)."""
        params = {"ref": ref} if ref else None
        data = self._request("GET", f"{self._repo_path}/contents/{quote(path.lstrip('/'), safe='/')}",
                             params=params)
        if not isinstance(data, dict) or data.get("type") != "file":
            raise GiteaError(None, f"{path!r} is not a file", "GET", path)
        raw = base64.b64decode(data.get("content") or "")
        return raw.decode("utf-8")
