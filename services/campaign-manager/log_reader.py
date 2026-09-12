"""
log_reader.py
On-demand read access to a container's stdout/stderr via the Docker socket,
scoped to a job's start/finish window. Used by the job detail page to show
live generator output without depending on the main stack's Postgres — which
was found to be misconfigured (services/log-shipper reads POSTGRES_HOST from
.env, and that currently points at the generator's separate database, not a
real 'virtualtubers' DB). Fixing that is a separate, stack-wide task; this
module intentionally has no dependency on it.

Docker-py's `.logs()` accepts `since`/`until` as unix timestamps, so job
timestamps (ISO-8601 strings from generation_store) are parsed to epoch
seconds before the call.
"""
import datetime
import logging
import os

log = logging.getLogger(__name__)


def available() -> bool:
    if not os.path.exists("/var/run/docker.sock"):
        return False
    try:
        import docker  # noqa: F401
    except ImportError:
        return False
    return True


def _to_epoch(iso_ts) -> float | None:
    """Parse an ISO-8601 timestamp string (as generation_store._row_to_dict
    emits) to epoch seconds. Returns None for falsy/unparseable input —
    callers treat that as 'no bound'."""
    if not iso_ts:
        return None
    try:
        return datetime.datetime.fromisoformat(iso_ts).timestamp()
    except (ValueError, TypeError) as exc:
        log.warning("_to_epoch: could not parse %r: %s", iso_ts, exc)
        return None


def get_job_logs(container_name: str, started_at, finished_at=None, limit: int = 300) -> list:
    """Log lines from `container_name` between `started_at` and
    `finished_at` (or now, when the job is still running), oldest first,
    capped at the `limit` most-recent matching lines.

    Raises nothing: any failure (socket unreachable, container not found,
    permission denied) returns [] with the error logged, so the job detail
    page degrades to an empty panel instead of a 500.
    """
    if not available():
        return []

    since = _to_epoch(started_at)
    if since is None:
        # No reliable lower bound — refuse rather than dumping the
        # container's entire history onto the page.
        return []
    # Pad the window slightly: the container's clock and Postgres's
    # started_at can be a second or two apart, and the interesting lines
    # (pack load, first LLM call) often land right at job start.
    since -= 2
    until = _to_epoch(finished_at)
    if until is not None:
        until += 2

    try:
        import docker
        client = docker.from_env()
        container = client.containers.get(container_name)
    except Exception as exc:
        log.warning("get_job_logs: could not reach container %s: %s", container_name, exc)
        return []

    try:
        kwargs = {"since": since, "timestamps": True, "stdout": True, "stderr": True}
        if until is not None:
            kwargs["until"] = until
        raw = container.logs(**kwargs)
    except Exception as exc:
        log.warning("get_job_logs: logs() failed for %s: %s", container_name, exc)
        return []

    lines = []
    for raw_line in raw.decode("utf-8", errors="replace").splitlines():
        # Docker's timestamps=True prefixes each line with an RFC3339Nano
        # timestamp, then a space — matches log-shipper's own parsing.
        ts, _, message = raw_line.partition(" ")
        if not message:
            continue
        lines.append({"timestamp": ts, "stream": "stdout", "message": message})

    # Keep only the most recent `limit` lines, chronological order preserved.
    return lines[-limit:]
