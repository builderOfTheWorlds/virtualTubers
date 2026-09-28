#!/usr/bin/env python3
"""
build_office_replays.py
sessionCorpus JSONL export -> ashiorid_office replay episodes -> DRAFTS in
the Rerun Theater library (OB-33). See docs/build_office_replays.md.

Every session in the export goes through OB-12's role attribution
(app/office/role_attribution.py `attribute`), which maps each event to an
office seat and re-runs the leak audit as a hard gate. Each resulting
episode is then either:

  * --dry-run: written to --out as <name>.json, nothing uploaded; or
  * uploaded to message-api `POST /replays?status=draft&uploaded_by=...&name=...`.

Uploads are ALWAYS drafts. This script never calls /approve and has no flag
to: the control panel's "Drafts awaiting review" list stays the only way an
office replay reaches air (and the only way OfficePlaylist sees it).

    .venv/bin/python scripts/build_office_replays.py corpus_export.jsonl --dry-run --out office_replays
    .venv/bin/python scripts/build_office_replays.py corpus_export.jsonl --message-api http://127.0.0.1:8090

Exit code: 0 when every built episode was written/uploaded (a 409 "already
exists" counts as skipped, not failed), 1 when any upload failed, 2 on bad
input. A session that fails attribution or the leak audit is counted and
skipped, not fatal: a large corpus is expected to have a few.
Session content is never logged or printed; only names and counts.
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from office.role_attribution import AttributionError, attribute  # noqa: E402

log = logging.getLogger("build_office_replays")
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

DEFAULT_MESSAGE_API_URL = "http://127.0.0.1:8090"
DEFAULT_TIMEOUT_S = 60.0     # message-api dry-run renders every upload
DEFAULT_MIN_EVENTS = 5
UPLOADED_BY = "build_office_replays"
DRAFT = "draft"              # the only status this script ever sends
_MAX_DETAIL_CHARS = 300


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


def _httpx_post(url, content, headers, params, timeout):
    import httpx
    with httpx.Client(timeout=timeout) as client:
        return client.post(url, content=content, headers=headers, params=params)


def iter_records(path, tag=None, sessions=None):
    """Yield sessionCorpus records from a JSONL export. Bad lines are
    skipped with a warning naming only the line number."""
    _trace("iter_records enter path=%s tag=%s", path, tag)
    wanted = {str(s) for s in sessions} if sessions else None
    with Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                log.warning("build.bad_line line=%d", lineno)
                continue
            if not isinstance(record, dict) or not isinstance(record.get("events"), list):
                log.warning("build.bad_record line=%d", lineno)
                continue
            if tag and tag not in (record.get("tags") or []):
                log.debug("build.skip_tag line=%d", lineno)
                continue
            if wanted is not None and str(record.get("session_id")) not in wanted:
                continue
            yield record


def build_episodes(records, *, min_events=DEFAULT_MIN_EVENTS, llm_client=None, embellish=False,
                   limit=None):
    """Attribute each record. Returns (episodes, stats): episodes is a list of
    (name, episode) and stats counts built / too_short / failed."""
    episodes, stats = [], {"built": 0, "too_short": 0, "failed": 0}
    for record in records:
        if limit is not None and len(episodes) >= limit:
            log.debug("build.limit_reached limit=%d", limit)
            break
        try:
            episode = attribute(record, llm_client=llm_client, embellish=embellish)
        except AttributionError as exc:
            # The message never contains session content (role_attribution contract).
            log.error("build.attribution_failed session=%s reason=%s",
                      record.get("session_id"), exc)
            stats["failed"] += 1
            continue
        if len(episode["events"]) < min_events:
            log.debug("build.too_short name=%s events=%d", episode["source"], len(episode["events"]))
            stats["too_short"] += 1
            continue
        episodes.append((episode["source"], episode))
        stats["built"] += 1
    log.info("build.episodes built=%d too_short=%d failed=%d",
             stats["built"], stats["too_short"], stats["failed"])
    return episodes, stats


def write_episode(out_dir, name, episode):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.json"
    path.write_text(json.dumps(episode, indent=1, ensure_ascii=False), encoding="utf-8")
    log.debug("build.wrote path=%s", path)
    return path


def upload_draft(name, episode, *, message_api_url=DEFAULT_MESSAGE_API_URL, http_post=None,
                 timeout_s=DEFAULT_TIMEOUT_S):
    """POST one episode to message-api as a draft. Never raises: returns
    {"status": "submitted"|"exists"|"failed", "name", "http_status"?, "error"?}."""
    url = f"{message_api_url.rstrip('/')}/replays"
    post = http_post or _httpx_post
    params = {"status": DRAFT, "uploaded_by": UPLOADED_BY, "name": name}
    body = json.dumps(episode, ensure_ascii=False).encode("utf-8")
    outcome = {"status": "failed", "name": name}
    log.debug("build.upload url=%s name=%s bytes=%d", url, name, len(body))
    try:
        resp = post(url, content=body, headers={"Content-Type": "application/json"},
                    params=params, timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 — one bad upload must not stop the batch
        log.error("build.upload_unreachable url=%s name=%s error=%s", url, name, type(exc).__name__)
        outcome["error"] = f"message-api unreachable: {type(exc).__name__}"
        return outcome
    outcome["http_status"] = resp.status_code
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = None
    if 200 <= resp.status_code < 300:
        if isinstance(data, dict) and data.get("status") not in (None, DRAFT):
            # message-api echoes the stored status; anything else is a server bug.
            log.error("build.upload_not_draft name=%s status=%s", name, data.get("status"))
            outcome["error"] = f"server stored status {data.get('status')!r}, expected draft"
            return outcome
        outcome["status"] = "submitted"
        log.info("build.uploaded_draft name=%s", name)
        return outcome
    detail = data.get("detail") if isinstance(data, dict) else getattr(resp, "text", "")
    outcome["error"] = str(detail or "")[:_MAX_DETAIL_CHARS]
    if resp.status_code == 409:
        outcome["status"] = "exists"
        log.info("build.upload_exists name=%s", name)
    else:
        log.warning("build.upload_rejected name=%s http_status=%s", name, resp.status_code)
    return outcome


def run(export, *, out=None, dry_run=False, message_api_url=DEFAULT_MESSAGE_API_URL,
        http_post=None, min_events=DEFAULT_MIN_EVENTS, tag=None, sessions=None, limit=None,
        llm_client=None, embellish=False, timeout_s=DEFAULT_TIMEOUT_S):
    """The whole pipeline. Returns a summary dict (counts + per-episode outcomes)."""
    _trace("run enter dry_run=%s", dry_run)
    if dry_run and not out:
        raise ValueError("--dry-run needs --out (the directory episodes are written to)")
    episodes, stats = build_episodes(iter_records(export, tag=tag, sessions=sessions),
                                     min_events=min_events, llm_client=llm_client,
                                     embellish=embellish, limit=limit)
    summary = {**stats, "written": 0, "submitted": 0, "exists": 0, "upload_failed": 0,
               "dry_run": dry_run, "outcomes": []}
    for name, episode in episodes:
        if out:
            write_episode(out, name, episode)
            summary["written"] += 1
        if dry_run:
            summary["outcomes"].append({"status": "written", "name": name})
            continue
        outcome = upload_draft(name, episode, message_api_url=message_api_url,
                               http_post=http_post, timeout_s=timeout_s)
        summary["outcomes"].append(outcome)
        key = {"submitted": "submitted", "exists": "exists"}.get(outcome["status"], "upload_failed")
        summary[key] += 1
    log.info("build.done built=%d written=%d submitted=%d exists=%d upload_failed=%d failed=%d",
             summary["built"], summary["written"], summary["submitted"], summary["exists"],
             summary["upload_failed"], summary["failed"])
    return summary


def main(argv=None, http_post=None, llm_client=None):
    ap = argparse.ArgumentParser(description="Build ashiorid_office replay drafts from a sessionCorpus export")
    ap.add_argument("export", help="sessionCorpus export .jsonl")
    ap.add_argument("--out", help="write episodes here (required with --dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="write episodes to --out, upload nothing")
    ap.add_argument("--message-api", default=os.environ.get("MESSAGE_API_URL") or DEFAULT_MESSAGE_API_URL,
                    help="message-api base URL (default $MESSAGE_API_URL or %(default)s)")
    ap.add_argument("--min-events", type=int, default=DEFAULT_MIN_EVENTS,
                    help="skip episodes with fewer events (default %(default)s)")
    ap.add_argument("--tag", help="only sessions whose export `tags` contain this (e.g. feature)")
    ap.add_argument("--session", action="append", dest="sessions", help="only this session_id (repeatable)")
    ap.add_argument("--limit", type=int, help="stop after this many episodes")
    ap.add_argument("--embellish", action="store_true",
                    help="OB-12 LLM pass: hand-off lines + Marketing reactions")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="upload timeout seconds")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s %(message)s")
    if not Path(args.export).is_file():
        log.error("build.export_missing path=%s", args.export)
        print(f"[build_office_replays] export not found: {args.export}", file=sys.stderr)
        return 2
    if args.embellish and llm_client is None:
        from llm_client import build_llm_client
        llm_client = build_llm_client({})
    try:
        summary = run(args.export, out=args.out, dry_run=args.dry_run, message_api_url=args.message_api,
                      http_post=http_post, min_events=args.min_events, tag=args.tag,
                      sessions=args.sessions, limit=args.limit, llm_client=llm_client,
                      embellish=args.embellish, timeout_s=args.timeout)
    except ValueError as exc:
        log.error("build.bad_arguments reason=%s", exc)
        print(f"[build_office_replays] {exc}", file=sys.stderr)
        return 2
    for outcome in summary["outcomes"]:
        extra = f" ({outcome.get('http_status')}: {outcome['error']})" if outcome.get("error") else ""
        print(f"  {outcome['status']:<9} {outcome['name']}{extra}")
    print(f"[build_office_replays] built={summary['built']} too_short={summary['too_short']} "
          f"attribution_failed={summary['failed']} written={summary['written']} "
          f"drafts_submitted={summary['submitted']} exists={summary['exists']} "
          f"upload_failed={summary['upload_failed']}"
          + (" (dry run, nothing uploaded)" if args.dry_run else ""))
    return 1 if summary["upload_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
