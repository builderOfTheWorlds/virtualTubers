# gitea_client.py

## Overview

A thin wrapper over the Gitea REST API v1 (server 1.22.3 at
`http://192.168.1.120:3300`, API base `/api/v1`) for the Fraud-Stop office
loop. It covers:

- issues: the CEO's directives
- PRs, which every lane opens
- reviews and merges: Tech Lead
- branch deletes: Office Manager GC
- tags: the weekly `loop-seed`
- file reads: the observer

Git transport (push/fetch) lives in [`git_client.md`](git_client.md).

Auth is the `Authorization: token <T>` header. The token is read at request
time from the env var named by `token_env`. It is never logged, never put in
a URL, and scrubbed from any `GiteaError` message.

`read_only=True`, used for the Party Member / observer with
`GITEA_TOKEN_OBSERVER`, refuses every non-GET request with
`GiteaReadOnlyError` before any network I/O.

## Signature

```python
class GiteaError(RuntimeError):          # .status (int|None), .message, .method, .path
class GiteaReadOnlyError(GiteaError)

class GiteaClient:
    def __init__(self, base_url, owner, repo, token_env="GITEA_TOKEN_OFFICE",
                 timeout=15.0, read_only=False, transport=None)
    def list_labels(self) -> list[dict]
    def ensure_label(self, name, color="#8c8c8c") -> int
    def open_issue(self, title, body="", labels=None) -> dict
    def close_issue(self, number) -> dict
    def comment_issue(self, number, body) -> dict
    def list_issues(self, state="open", labels=None) -> list[dict]
    def open_pr(self, head, base, title, body="") -> dict
    def list_prs(self, state="open") -> list[dict]
    def review_pr(self, number, event, body="") -> dict
    def merge_pr(self, number, style="merge", delete_branch=True, message=None) -> bool
    def delete_branch(self, name) -> bool
    def create_tag(self, name, target, message="") -> dict
    def get_file(self, path, ref=None) -> str
```

## Parameters

- `base_url`: server root. `/api/v1` is appended if it's missing.
- `owner`, `repo`: e.g. `gitea_admin`, `fraud-stop`.
- `token_env`: the **name** of the env var holding the token. It must be a
  valid identifier. If the variable is unset, requests go out anonymously and
  a WARN is logged.
- `timeout`: per-request seconds (httpx).
- `read_only`: refuse all writes. This includes `ensure_label`'s create and
  `open_issue` with labels.
- `transport`: optional `httpx` transport, e.g. `httpx.MockTransport` in tests.
- `labels` are names. Gitea's issue API takes label **ids**, so
  `ensure_label` looks each name up (cached) and creates it if it's missing.
- `review_pr(event)`: `APPROVE` | `REQUEST_CHANGES` | `COMMENT`
  (case-insensitive). `APPROVE` is sent as Gitea's `APPROVED`.
- `merge_pr(style)`: `merge` | `squash` | `rebase` | `rebase-merge` |
  `fast-forward-only`.
- `list_issues` excludes PRs (`type=issues`) and joins `labels` with commas.
  List calls paginate at 50 per page (capped at 40 pages).

## Return Value

Parsed Gitea JSON objects (issue, PR, review, tag, comment) or lists of them.
`merge_pr` and `delete_branch` return `True`. `get_file` returns the decoded
UTF-8 text.

## Dependencies

- `httpx` (already in `requirements.txt`)
- stdlib `base64`, `logging`, `os`, `re`, `urllib.parse.quote`

## Usage Examples

```python
gt = GiteaClient("http://192.168.1.120:3300", "gitea_admin", "fraud-stop")
issue = gt.open_issue("Directive: add country-mismatch rule", "…", labels=["directive"])
pr = gt.open_pr("eng/country-rule", "loop/2026-W40", "feat(rules): country mismatch",
                f"Closes #{issue['number']}")
```

```python
# Tech Lead review + merge; OM garbage collection
gt.review_pr(pr["number"], "APPROVE", "LGTM")
gt.merge_pr(pr["number"], style="squash", delete_branch=True)
gt.delete_branch("eng/stale-experiment")
```

```python
# Observer: read-only token, writes refused locally
obs = GiteaClient("http://192.168.1.120:3300", "gitea_admin", "fraud-stop",
                  token_env="GITEA_TOKEN_OBSERVER", read_only=True)
obs.list_prs("all")
obs.get_file("CHANGELOG.md", ref="loop/2026-W40")
obs.close_issue(1)   # raises GiteaReadOnlyError
```

## Error Handling

- `GiteaError(status, message)` is raised for HTTP ≥ 400, carrying Gitea's
  `message` truncated to 500 chars with the token scrubbed. It is also raised
  for transport failures, with `status=None`, and when `get_file` targets a
  directory.
- `GiteaReadOnlyError` (a subclass) is raised for any write on a read-only
  client.
- `ValueError` is raised for an unknown review event or merge style, or an
  invalid `token_env` (the value is not echoed).
- Gitea itself returns 422 when a user approves their own PR. v1 uses a
  single `gitea_admin` token, so Tech Lead approvals on PRs opened with the
  same token will fail. Use `COMMENT` reviews, or per-persona bot tokens.

## Testing

`tests/test_gitea_client.py` mocks HTTP with `httpx.MockTransport`. The
single live test (`@pytest.mark.integration`) only lists issues, read-only.
It runs only when `GITEA_TOKEN_OFFICE` is set **and** `GITEA_INTEGRATION=1`.
`GITEA_BASE_URL`, `GITEA_OWNER` and `GITEA_REPO` override the target.

## Changelog

- v1.0.0 (2026-09-27): OB-23. Initial client: issues, labels, PRs, reviews,
  merges, branch delete, tags and file reads, plus read-only mode.
