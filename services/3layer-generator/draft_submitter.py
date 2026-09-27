"""
draft_submitter.py
Opt-in bridge from a finished `publish` job to the Rerun Theater review
queue (docs/draft_submitter.md).

Before this, a generated episode reached message-api only when someone
hand-ran a builder script or clicked Publish. With AUTO_SUBMIT_DRAFTS on,
runner.dispatch_once hands every successfully written episode.json to a
DraftSubmitter, which POSTs it to message-api's `POST /replays?status=draft`.
A draft is validated and stored but NEVER airs until an operator approves it
in the control panel — so turning this on cannot put unreviewed content on a
stream.

Two rules this module exists to keep:

  * message-api stays the ONLY writer of replay_episodes. This goes over
    HTTP; it never opens a connection to the app database (the generator's
    own Postgres on :5455 is a different database and is not touched here
    either).
  * A submit failure never fails the job. The episode.json is on disk
    either way, and the manual `POST /publish/{run}/upload` path still
    works. So __call__ never raises: every outcome — including message-api
    being down, a 400 from the validator, or a 409 name clash — comes back
    as a dict the runner records on the job's result under `auto_submit`.

Default OFF. from_env() returns None unless AUTO_SUBMIT_DRAFTS is truthy.
"""
import logging
import os
import pathlib

log = logging.getLogger(__name__)

DEFAULT_MESSAGE_API_URL = "http://127.0.0.1:8090"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_UPLOADED_BY = "3layer-generator"
_TRUTHY = {"1", "true", "yes", "on"}
# message-api's error detail is operator-facing and short; cap what goes
# onto the job row anyway so a surprise HTML error page can't bloat it.
_MAX_DETAIL_CHARS = 500


def _httpx_post(url, content, headers, params, timeout):
    import httpx
    with httpx.Client(timeout=timeout) as client:
        return client.post(url, content=content, headers=headers, params=params)


def _is_request_error(exc) -> bool:
    """True for a transport-level failure (refused, DNS, timeout) as opposed
    to a bug in this module. httpx is imported lazily so a fake `http_post`
    in the tests never needs it."""
    try:
        import httpx
    except ImportError:
        return False
    return isinstance(exc, httpx.RequestError)


class DraftSubmitter:
    """Callable: `submitter(episode_path, name) -> dict`. Never raises."""

    def __init__(self, message_api_url=DEFAULT_MESSAGE_API_URL,
                 timeout_s=DEFAULT_TIMEOUT_S, uploaded_by=DEFAULT_UPLOADED_BY,
                 http_post=None):
        self.message_api_url = message_api_url.rstrip("/")
        self.timeout_s = timeout_s
        self.uploaded_by = uploaded_by
        self._http_post = http_post or _httpx_post

    @property
    def url(self) -> str:
        return f"{self.message_api_url}/replays"

    def __call__(self, episode_path, name) -> dict:
        log.debug("draft_submitter: enter path=%s name=%s url=%s",
                  episode_path, name, self.url)
        outcome = {"status": "failed", "name": name, "url": self.url}
        try:
            body = pathlib.Path(episode_path).read_bytes()
        except OSError as exc:
            log.error("draft_submitter: cannot read episode path=%s error=%s",
                      episode_path, exc)
            outcome["error"] = f"cannot read {episode_path}: {exc}"
            return outcome

        params = {"status": "draft", "uploaded_by": self.uploaded_by}
        if name:
            params["name"] = name
        log.debug("draft_submitter: POST url=%s bytes=%d", self.url, len(body))
        try:
            resp = self._http_post(
                self.url, content=body,
                headers={"Content-Type": "application/json"},
                params=params, timeout=self.timeout_s)
        except Exception as exc:  # noqa: BLE001 — must never fail the job
            kind = "unreachable" if _is_request_error(exc) else "error"
            log.error("draft_submitter: message-api %s url=%s name=%s error=%s: %s",
                      kind, self.url, name, type(exc).__name__, exc)
            outcome["error"] = f"message-api {kind}: {type(exc).__name__}: {exc}"
            return outcome

        outcome["http_status"] = resp.status_code
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001 — a non-JSON body is still an outcome
            data = None
        if 200 <= resp.status_code < 300:
            outcome["status"] = "submitted"
            if isinstance(data, dict) and data.get("name"):
                outcome["name"] = data["name"]
            log.info("draft_submitter: submitted draft name=%s http_status=%s",
                     outcome["name"], resp.status_code)
            return outcome

        detail = data.get("detail") if isinstance(data, dict) else None
        if detail is None:
            detail = (getattr(resp, "text", "") or "")
        outcome["error"] = str(detail)[:_MAX_DETAIL_CHARS]
        log.warning("draft_submitter: message-api rejected draft name=%s "
                    "http_status=%s detail=%s", name, resp.status_code,
                    outcome["error"])
        return outcome


def from_env(env=None):
    """A DraftSubmitter when AUTO_SUBMIT_DRAFTS is truthy, else None (the
    default — auto-submit is off unless explicitly enabled).

    Reads MESSAGE_API_URL (default http://127.0.0.1:8090, the same var
    generator_api's manual upload proxy already uses) and
    AUTO_SUBMIT_TIMEOUT_S (default 60 — message-api dry-run renders every
    upload, so this is deliberately generous). A malformed timeout falls
    back to the default with a warning rather than disabling the feature."""
    env = os.environ if env is None else env
    enabled = str(env.get("AUTO_SUBMIT_DRAFTS", "")).strip().lower() in _TRUTHY
    if not enabled:
        log.debug("draft_submitter: AUTO_SUBMIT_DRAFTS off")
        return None
    url = env.get("MESSAGE_API_URL") or DEFAULT_MESSAGE_API_URL
    raw_timeout = env.get("AUTO_SUBMIT_TIMEOUT_S")
    timeout = DEFAULT_TIMEOUT_S
    if raw_timeout:
        try:
            timeout = float(raw_timeout)
            if timeout <= 0:
                raise ValueError("must be > 0")
        except ValueError as exc:
            log.warning("draft_submitter: bad AUTO_SUBMIT_TIMEOUT_S=%r (%s), "
                        "using %s", raw_timeout, exc, DEFAULT_TIMEOUT_S)
            timeout = DEFAULT_TIMEOUT_S
    log.info("draft_submitter: auto-submit ON url=%s timeout_s=%s", url, timeout)
    return DraftSubmitter(message_api_url=url, timeout_s=timeout)
