#!/usr/bin/env python3
"""
build_library.py
Batch-parses claudeBackupUtility session logs into a coder-campaign episode
library for the "Rerun Theater" replay pane (docs/replay_pane.md), emitting
the campaign-platform episode schema (meta/cast/scenes[], docs/
campaign_platform_build.md; CONTRACT.md §1/§9/§9b) rather than the old flat
events[] shape.

This is a GENERATOR (generators/coder/, see generators/README.md): it lives
outside the platform on purpose -- it need not share a deploy cycle with the
worker image (generators/ is excluded via .dockerignore), and it is
explicitly allowed to import from app/ for one reason only: reusing
app/episode_schema.py's validator, so "same config and same engine on both
sides" (the platform's own ingest check) is literal, not just aspirational.
See generators/README.md's "the app/ import exception" section before
touching the sys.path insert below.

Run on the machine that has the logs (the Windows dev box):

    .venv/Scripts/python.exe generators/coder/build_library.py \\
        --logs "path/to/logs/claude/virtualTubers" --out replays/coder

Skips sessions that produce fewer than --min-events events (nothing
watchable in them). Redaction happens inside session_log_parser.redact();
this script additionally runs the same strict LEAK_AUDIT re-scan the old
scripts/build_replay_library.py used (moved here unchanged) over the fully
serialized episode, and separately validates every episode against
app/episode_schema.py before writing anything -- either check refuses to
write and makes the whole run exit non-zero.

Grouping raw parsed events into scenes (formerly app/revoice.py's
plan_scenes, deleted from the platform per CONTRACT.md §5/§9 -- recovered
from git history) is a generator concern now: a campaign generator is
expected to emit scene-sized units directly, one spoken line per scene.
MAX_SCENE_EVENTS caps a single coder_work run the same coarse way
plan_scenes always did; _split_oversized_work_scene then re-splits any
chunk whose estimated screen time (app/primitives.py::estimate_scene_seconds,
over the campaign's real render recipes) exceeds config/validation.yaml's
limits.max_scene_seconds -- so this generator's cap is DERIVED from the same
budget the platform's validator enforces at ingest, rather than duplicating
a second hardcoded number that could drift out of sync with it.

Every string is inlined into the episode -- no detail_file sidecars. This
was already effectively true of session_log_parser's own output (a tool
call's sidecar file is read and merged into event["detail"] at parse time);
what changes here is that the render[] payloads built below are assembled
field-by-field from that merged "detail" dict (command/output/old/new/
content/file), never by copying a whole event (and its leftover
"detail_file" pointer key) wholesale into a payload. See
generators/coder/README.md.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# ── sibling import: session_log_parser lives right next to this file ───────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_log_parser import parse_session  # noqa: E402

# ── the one documented app/ import exception (generators/README.md) ────────
# Reused so the generator validates against the EXACT SAME engine
# (app/episode_schema.py) and recipe table (app/primitives.py) the platform
# uses at ingest -- not a re-implementation that could drift.
_APP_DIR = Path(__file__).resolve().parents[2] / "app"
sys.path.insert(0, str(_APP_DIR))
from episode_schema import (  # noqa: E402
    Issue,  # noqa: F401  (re-exported for callers/tests that want the type)
    load_rules,
    normalize_episode,
    validate_episode,
)
from primitives import PrimitiveError, estimate_scene_seconds, load_primitives  # noqa: E402

CAMPAIGN = "coder"

# First-pass grouping cap -- mirrors the deleted app/revoice.py's own
# MAX_SCENE_EVENTS (8). This is a COARSE cap applied before any primitive
# table or render entries exist; _split_oversized_work_scene (below) then
# re-splits against the real config/validation.yaml budget once a scene's
# render[] can actually be estimated.
MAX_SCENE_EVENTS = 8

DEFAULT_WORKER_NAME = "KODI-7"  # matches app/replay.py's own default

# Strict last-line-of-defense audit -- a leaked episode must never reach a
# broadcastable library. Private LAN IPs (192.168.x etc.) are allowed by
# policy; tailnet (100.x) and credential-looking assignments are not.
# The password arm tolerates JSON escaping (password\": \"...) and only
# fires when the value is NOT the parser's [password] dummy marker.
# UNCHANGED from the pre-move scripts/build_replay_library.py -- see
# CONTRACT.md §9 ("preserve ... the separate LEAK_AUDIT re-scan").
LEAK_AUDIT = re.compile(
    r"frogg|sk-ant-[A-Za-z0-9_-]{8}|ghp_[A-Za-z0-9]{8}|100\.\d{1,3}\.\d"
    r"|(?i:\w*(?:password|passwd|passphrase|pwd|secret)(?:\\?[\"'])*\s*[:=]>?\s*(?:\\?[\"'])*"
    r"(?!\[password\])[^\s\\\"',;&|=\[])"
)


# ── narration.yaml kinds (for the validator's unknown_kind check) ──────────

def _default_campaigns_dir():
    in_container = Path("/config/campaigns")
    if in_container.is_dir():
        return in_container
    return Path(__file__).resolve().parents[2] / "config" / "campaigns"


def load_narration_kinds(campaign=CAMPAIGN, *, campaigns_dir=None):
    """The campaign's narration.yaml keys -- passed as validate_episode's
    `kinds=` so unknown_kind actually fires (episode_schema.py's own
    docstring flags this as otherwise-skipped when going through
    load_episode alone; a generator calling validate_episode directly is
    exactly the escape hatch CONTRACT.md §4 describes). Returns None (which
    skips the check) rather than raising when the file is missing or
    unreadable -- a missing narration config is the platform's problem to
    surface loudly at showtime, not a reason for the generator to crash."""
    campaigns_dir = Path(campaigns_dir) if campaigns_dir is not None else _default_campaigns_dir()
    path = campaigns_dir / campaign / "narration.yaml"
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return set((data or {}).keys())


# ── event -> render[] entry mapping (CONTRACT.md §9b's payload contract) ───

def event_to_render(event, worker_name=DEFAULT_WORKER_NAME):
    """Map one raw parsed event (session_log_parser's user_message /
    assistant_text / tool_call shapes) to a (primitive, payload) render
    entry.

    The seven cases, per CONTRACT.md §9b:
        user_message        -> show_boss_message  {text}
        assistant_text       -> show_coder_line    {text, name}
        Bash / PowerShell    -> show_command       {command, output?, error?}
        Edit                 -> show_diff          {file, hunks, error?}
        Write                -> show_write         {file, content, error?}
        Read                 -> show_read          {file, error?}
        everything else      -> show_tool          {tool, summary, error?}

    `hunks` is every pre-edit line prefixed "- ", THEN every post-edit line
    prefixed "+ ", concatenated in that order -- NOT a unified diff (this is
    what lets app/primitives.py's `diff` behavior reproduce Edit's old-
    scroll/new-type split with a single field). `summary` on show_tool is
    pre-truncated to <=100 chars here, mirroring the pre-rewrite
    app/replay.py's `_perform_generic` (`event.get("input_summary", "")[:100]`)
    exactly -- app/primitives.py does not truncate template tokens itself.
    File/command fallbacks (`or event.get("input_summary") or "(command)"` /
    `"(file)"`) also mirror that pre-rewrite Performer's own handlers
    (`_perform_shell`/`_perform_edit`/`_perform_write`/`_perform_read`) so a
    thin/undetailed tool call still renders something instead of a blank
    field."""
    etype = event.get("type")
    if etype == "user_message":
        return "show_boss_message", {"text": event.get("text", "")}
    if etype == "assistant_text":
        return "show_coder_line", {"text": event.get("text", ""), "name": worker_name}

    tool = event.get("tool", "")
    detail = event.get("detail") or {}
    error = bool(event.get("error"))

    if tool in ("Bash", "PowerShell"):
        payload = {"command": detail.get("command") or event.get("input_summary") or "(command)"}
        output = detail.get("output")
        if output:
            payload["output"] = output
        if error:
            payload["error"] = True
        return "show_command", payload

    if tool == "Edit":
        file_ = detail.get("file") or event.get("input_summary") or "(file)"
        old = detail.get("old") or ""
        new = detail.get("new") or ""
        hunks = [f"- {line}" for line in old.splitlines()] + [f"+ {line}" for line in new.splitlines()]
        payload = {"file": file_, "hunks": hunks}
        if error:
            payload["error"] = True
        return "show_diff", payload

    if tool == "Write":
        file_ = detail.get("file") or event.get("input_summary") or "(file)"
        payload = {"file": file_, "content": detail.get("content") or ""}
        if error:
            payload["error"] = True
        return "show_write", payload

    if tool == "Read":
        file_ = detail.get("file") or event.get("input_summary") or "(file)"
        payload = {"file": file_}
        if error:
            payload["error"] = True
        return "show_read", payload

    # Generic fallback -- Agent, ScheduleWakeup, and anything else
    # parse_tool_detail doesn't know how to re-enact in detail.
    summary = (event.get("input_summary") or "")[:100]
    payload = {"tool": tool or "unknown", "summary": summary}
    if error:
        payload["error"] = True
    return "show_tool", payload


# ── scene builders ───────────────────────────────────────────────────────

def _build_boss_scene(event):
    text = event.get("text", "")
    primitive, payload = event_to_render(event)
    return {
        "speaker": event.get("speaker") or "boss",
        "kind": "boss",
        "text": text,
        "fallback": "",  # narration.yaml's boss.fallback_template ("{text}") covers this
        "mode": "sequence",
        "render": [{"primitive": primitive, "payload": payload}],
    }


def _build_coder_talk_scene(event, worker_name):
    text = event.get("text", "")
    primitive, payload = event_to_render(event, worker_name=worker_name)
    return {
        "speaker": event.get("speaker") or "coder",
        "kind": "coder_talk",
        "text": text,
        "fallback": "",  # narration.yaml's coder_talk.fallback_template ("{text}") covers this
        "mode": "sequence",
        "render": [{"primitive": primitive, "payload": payload}],
    }


def _summarize_work_events(events):
    """A short present-ish description of a coder_work chunk's actions,
    recovered in spirit from the deleted app/revoice.py's
    fallback_narration() (git history: `git show HEAD:app/revoice.py`) --
    narrating "what this run of tool calls did" for both the scene's own
    `text` (the LLM re-voice's source material) and its `fallback` (the
    last-resort spoken line if re-voicing fails entirely)."""
    actions = []
    for event in events:
        tool = event.get("tool", "?")
        detail = event.get("detail") or {}
        if tool in ("Bash", "PowerShell"):
            command = detail.get("command") or event.get("input_summary") or "a command"
            actions.append(f"running {command.splitlines()[0][:60]}")
        elif tool in ("Edit", "Write"):
            target = detail.get("file") or event.get("input_summary") or "a file"
            actions.append(f"{'editing' if tool == 'Edit' else 'writing'} {Path(target).name}")
        elif tool == "Read":
            target = detail.get("file") or event.get("input_summary") or "a file"
            actions.append(f"checking {Path(target).name}")
        else:
            actions.append(f"using {tool}")
    return "Okay — " + ", then ".join(actions[:4]) + "."


def _build_work_scene(events, worker_name):
    speaker = (events[0].get("speaker") or "coder") if events else "coder"
    summary = _summarize_work_events(events) if events else ""
    render = [
        {"primitive": primitive, "payload": payload}
        for primitive, payload in (event_to_render(event, worker_name=worker_name) for event in events)
    ]
    return {
        "speaker": speaker,
        "kind": "coder_work",
        "text": summary,
        "fallback": summary,
        "mode": "sequence",
        "render": render,
    }


def _split_oversized_work_scene(events, primitives_table, budget, worker_name):
    """Recursively halve a coder_work event chunk until every resulting
    scene's estimated screen time fits `budget` seconds
    (config/validation.yaml's limits.max_scene_seconds, threaded in from
    main()) -- this is the generator's cap "consistent with" the platform's
    own budget rather than a second hardcoded number (CONTRACT.md §9)."""
    scene = _build_work_scene(events, worker_name)
    if len(events) <= 1 or budget is None:
        return [scene]
    try:
        seconds = estimate_scene_seconds(scene, primitives_table)
    except PrimitiveError:
        # An unknown primitive here means a real bug in event_to_render, not
        # a timing problem -- let validate_episode's unknown_primitive rule
        # report it properly rather than silently under-splitting.
        return [scene]
    if seconds <= budget:
        return [scene]
    mid = len(events) // 2
    return (
        _split_oversized_work_scene(events[:mid], primitives_table, budget, worker_name)
        + _split_oversized_work_scene(events[mid:], primitives_table, budget, worker_name)
    )


def events_to_scenes(events, *, primitives_table, max_scene_seconds=None, worker_name=DEFAULT_WORKER_NAME):
    """Group a parsed session's flat events[] into campaign-platform
    scenes[] -- the moved-and-adapted app/revoice.py::plan_scenes (deleted
    from the platform, recovered from git history; CONTRACT.md §9). Grouping
    rules are unchanged from the original:

        boss        -- one user_message (the boss talking to the coder)
        coder_talk  -- one assistant_text (the coder addressing the stream)
        coder_work  -- a run of consecutive tool_calls by the same speaker,
                      capped at MAX_SCENE_EVENTS, then further re-split by
                      _split_oversized_work_scene against the real screen-
                      time budget.

    Unknown event types are dropped, exactly as plan_scenes did (the
    performer has nothing to do with them and dropping keeps timing
    estimates honest)."""
    scenes = []
    work = []

    def flush_work():
        nonlocal work
        if not work:
            return
        for start in range(0, len(work), MAX_SCENE_EVENTS):
            chunk = work[start:start + MAX_SCENE_EVENTS]
            scenes.extend(_split_oversized_work_scene(chunk, primitives_table, max_scene_seconds, worker_name))
        work = []

    for event in events:
        etype = event.get("type")
        if etype == "tool_call":
            speaker = event.get("speaker") or "coder"
            if work and (work[0].get("speaker") or "coder") != speaker:
                flush_work()
            work.append(event)
            continue
        flush_work()
        if etype == "user_message":
            scenes.append(_build_boss_scene(event))
        elif etype == "assistant_text":
            scenes.append(_build_coder_talk_scene(event, worker_name))
        # else: unknown event type, dropped (see docstring)
    flush_work()
    return scenes


def build_episode(script, *, primitives_table, max_scene_seconds=None,
                  worker_name=DEFAULT_WORKER_NAME, campaign=CAMPAIGN,
                  title=None, created=None):
    """A parsed session_log_parser script -> a full episode dict (meta/cast/
    scenes[], CONTRACT.md §1). Not yet normalized/validated -- callers
    (main(), tests) do that explicitly so failures are reported per-session
    rather than raised out of this pure builder."""
    episode_id = script.get("source") or "episode"
    scenes = events_to_scenes(
        script.get("events", []), primitives_table=primitives_table,
        max_scene_seconds=max_scene_seconds, worker_name=worker_name,
    )
    cast = sorted({scene["speaker"] for scene in scenes}) or ["boss", "coder"]
    return {
        "meta": {
            "schema": 1,
            "campaign": campaign,
            "id": episode_id,
            "title": title or script.get("project") or episode_id,
            "created": created or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "cast": cast,
        "scenes": scenes,
    }


# ── top level ────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Parse session logs into a coder-campaign episode library (new schema)"
    )
    parser.add_argument("--logs", required=True, help="Directory of <timestamp>_<id> session log dirs")
    parser.add_argument("--out", default="replays/coder", help="Episode library output directory")
    parser.add_argument("--min-events", type=int, default=5,
                        help="Skip sessions with fewer performable events (default 5)")
    parser.add_argument("--worker-name", default=DEFAULT_WORKER_NAME,
                        help="On-screen coder display name baked into show_coder_line payloads")
    args = parser.parse_args(argv)

    primitives_table = load_primitives(CAMPAIGN)
    rules = load_rules()
    kinds = load_narration_kinds()
    max_scene_seconds = (rules.get("limits") or {}).get("max_scene_seconds")

    logs = Path(args.logs)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    written, skipped, failed = 0, 0, 0
    for session_dir in sorted(p for p in logs.iterdir() if p.is_dir()):
        try:
            script = parse_session(session_dir)
        except Exception as exc:
            print(f"  FAIL  {session_dir.name}: {exc}")
            failed += 1
            continue
        if len(script["events"]) < args.min_events:
            skipped += 1
            continue

        episode = build_episode(
            script, primitives_table=primitives_table,
            max_scene_seconds=max_scene_seconds, worker_name=args.worker_name,
        )
        episode = normalize_episode(episode)
        payload = json.dumps(episode, indent=1, ensure_ascii=False)

        leak = LEAK_AUDIT.search(payload)
        if leak:
            print(f"  LEAK  {session_dir.name}: {leak.group(0)!r} — NOT writing")
            failed += 1
            continue

        issues = validate_episode(episode, primitives=primitives_table, rules=rules, kinds=kinds)
        rejecting = [issue for issue in issues if issue.level == "reject"]
        if rejecting:
            detail = "; ".join(f"[{issue.rule}] scene {issue.scene_index}: {issue.message}" for issue in rejecting)
            print(f"  INVALID {session_dir.name}: {detail}")
            failed += 1
            continue
        for issue in issues:
            if issue.level == "warn":
                print(f"  warn  {session_dir.name}: [{issue.rule}] {issue.message}")

        (out / f"{session_dir.name}.json").write_text(payload, encoding="utf-8")
        print(f"  ok    {session_dir.name}: {len(episode['scenes'])} scenes")
        written += 1

    print(f"[build_library] wrote {written} episode(s) to {out} "
          f"(skipped {skipped} thin, {failed} failed/leaked/invalid)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
