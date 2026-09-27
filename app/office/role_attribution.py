"""
office/role_attribution.py
Recorded coding-agent session -> ashiorid_office replay episode (OB-12, WS-D).

Input is ONE sessionCorpus export record (sibling repo sessionCorpus,
src/normaliser.py `export_view`):

    {source_tool, host, project, session_id, started_at, ended_at, model,
     events: [{seq, ts, type: user_message|assistant_text|tool_call,
               text, tool, input, output, error}]}

Output is a replay episode in the canonical session_log_parser.parse_session
shape (source, project, session_id, date, events[]) where every event carries
`speaker: tuber_N` (the office seat, app/office/roles.py SEAT) and `role`,
plus a `show.slots` header listing the seats used — so episode_validator's
show-header stage checks every speaker is cast.

Attribution is a deterministic rule pass (design §5 WS-D table); an optional
LLM pass (`embellish=True`, client injected) inserts delegation hand-off lines
and Marketing reactions. The leak audit (session_log_parser.audit) is re-run
on the finished episode as a hard gate. See docs/office_role_attribution.md.

SECURITY: nothing here logs or echoes session content; an audit failure
reports only that it failed, never the match.
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Run as a script (python app/office/role_attribution.py): put app/ on
    # the path so `office.*` and the bare app modules import.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office.roles import SEAT, OfficeRole, can_direct  # noqa: E402
from session_log_parser import MAX_OUTPUT_CHARS, audit, clean_user_text, redact  # noqa: E402

log = logging.getLogger(__name__)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


class AttributionError(ValueError):
    """The record can't become a safe episode (bad shape, or it failed the
    leak audit). Messages never contain session content."""


# ── Tool vocabularies (Claude Code + Hermes names) ───────────────────────────
PLAN_TOOLS = {"TodoWrite", "todo_write", "todo", "todo_list", "ExitPlanMode", "plan"}
DELEGATE_TOOLS = {"delegate_task", "Task", "Agent"}
DISCOVERY_TOOLS = {"Read", "Grep", "Glob", "LS", "WebSearch", "WebFetch",
                   "read_file", "search_files", "web_search", "web_extract",
                   "skill_view", "skills_list"}
EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit", "patch", "write_file"}
SHELL_TOOLS = {"Bash", "PowerShell", "terminal"}

#: Hermes tool name -> the Claude Code name replay.Performer renders visually.
RENDER_AS = {"terminal": "Bash", "patch": "Edit", "write_file": "Write",
             "read_file": "Read", "MultiEdit": "Edit"}

_PREFIX_SEGMENT = re.compile(r"^\s*(cd|export|source|set|pushd|popd|\.)\b")
_GIT_LEAD = re.compile(r"\bgit\s+(commit|push|merge|tag)\b")
_CLEANUP = re.compile(r"\brm\b|\brmdir\b|\bprune\b|\bcleanup\b|\bclean\b|\bgit\s+gc\b"
                      r"|\bgit\s+branch\s+(-d|-D|--delete)\b|\bdocker\s+\w*\s*prune\b")
_TEST = re.compile(r"pytest|\btest\b|\bunittest\b|\btox\b")
_PLAN_WORDS = re.compile(r"(?i)\b(plan|todo|steps?|first,? i'?ll|approach|break (?:it|this) down"
                         r"|phase|roadmap|then i'?ll)\b")
_REQ_WORDS = re.compile(r"(?i)\b(you want|you'?re asking|requirements?|the goal|so you need|"
                        r"to clarify|understand(?:ing)?|restat|in other words|acceptance|"
                        r"must|should)\b")

MAX_TEXT_CHARS = 2000
SUMMARY_CHARS = 200
EMBELLISH_LINE_CHARS = 280
_NAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _primary_command(command):
    """The first shell segment that isn't a cd/export/source prefix."""
    segments = [s for s in re.split(r"&&|\|\||;|\n", command or "") if s.strip()]
    for segment in segments:
        if not _PREFIX_SEGMENT.match(segment):
            return segment.strip()
    return segments[0].strip() if segments else ""


def classify_command(command):
    """Office role for a shell command (design §5 WS-D)."""
    primary = _primary_command(command)
    if _GIT_LEAD.search(primary):
        role = OfficeRole.TECH_LEAD
    elif _TEST.search(primary):
        role = OfficeRole.TESTER
    elif _CLEANUP.search(primary):
        role = OfficeRole.OFFICE_MANAGER
    else:
        role = OfficeRole.ENGINEER   # builds, installs, everything else
    log.debug("classify_command role=%s", role.value)
    return role


def classify_opening(text):
    """The first assistant_text after a user ask: planning -> Tech Lead,
    requirement restatement -> Analyst, otherwise Tech Lead."""
    if _PLAN_WORDS.search(text or ""):
        return OfficeRole.TECH_LEAD
    if _REQ_WORDS.search(text or ""):
        return OfficeRole.ANALYST
    return OfficeRole.TECH_LEAD


def _command_of(event):
    inp = event.get("input")
    if isinstance(inp, dict):
        return str(inp.get("command") or "")
    return str(inp or "")


def _path_of(inp):
    if isinstance(inp, dict):
        for key in ("file_path", "path", "notebook_path", "file"):
            if isinstance(inp.get(key), str) and inp[key].strip():
                return inp[key].strip()
    return None


def _oneline(value, limit=SUMMARY_CHARS):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    flat = " ".join(value.split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def _as_text(value):
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _tool_event(raw):
    """sessionCorpus tool_call -> replay tool_call (parse_session shape)."""
    tool = str(raw.get("tool") or "unknown")
    render = RENDER_AS.get(tool, tool)
    inp = raw.get("input")
    output = _as_text(raw.get("output"))
    event = {"type": "tool_call", "tool": render, "error": bool(raw.get("error"))}
    if render != tool:
        event["source_tool_name"] = tool
    path = _path_of(inp)
    if render in ("Bash", "PowerShell"):
        command = _command_of(raw)
        event["input_summary"] = _oneline(command)
        event["detail"] = {"command": command, "output": output[:MAX_OUTPUT_CHARS]}
    elif render == "Edit" and isinstance(inp, dict):
        event["input_summary"] = path or _oneline(inp)
        detail = {"old": _as_text(inp.get("old_string"))[:MAX_OUTPUT_CHARS],
                  "new": _as_text(inp.get("new_string"))[:MAX_OUTPUT_CHARS]}
        if path:
            detail["file"] = path
        event["detail"] = detail
    elif render == "Write" and isinstance(inp, dict):
        event["input_summary"] = path or _oneline(inp)
        detail = {"content": _as_text(inp.get("content"))[:MAX_OUTPUT_CHARS]}
        if path:
            detail["file"] = path
        event["detail"] = detail
    elif render == "Read" and path:
        event["input_summary"] = path
        event["detail"] = {"file": path}
    else:
        event["input_summary"] = _oneline(inp)
    event["output_summary"] = _oneline(output)
    return event


def _redact_all(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_all(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_all(v) for v in value]
    return value


def _stamp(event, role, reason):
    event["speaker"] = SEAT[role]
    event["role"] = role.value
    log.debug("attribute.event type=%s role=%s reason=%s", event["type"], role.value, reason)
    return event


def _attribute_events(raw_events):
    """The deterministic rule pass. Turn state resets at each user ask."""
    out = []
    opening_pending = False   # next assistant_text is the turn's opening
    edited = False            # an edit has happened in this turn
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("type")
        if kind == "user_message":
            text = clean_user_text(str(raw.get("text") or ""))
            if not text:
                continue
            opening_pending, edited = True, False
            out.append(_stamp({"type": "user_message", "text": text[:MAX_TEXT_CHARS]},
                              OfficeRole.CEO, "user_ask"))
        elif kind == "assistant_text":
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            if opening_pending:
                role, reason = classify_opening(text), "turn_opening"
                opening_pending = False
            else:
                role, reason = OfficeRole.ENGINEER, "work_narration"
            out.append(_stamp({"type": "assistant_text", "text": text[:MAX_TEXT_CHARS]}, role, reason))
        elif kind == "tool_call":
            tool = str(raw.get("tool") or "")
            event = _tool_event(raw)
            if tool in PLAN_TOOLS or tool in DELEGATE_TOOLS:
                role, reason = OfficeRole.TECH_LEAD, "plan_or_delegate"
                opening_pending = False
            elif tool in DISCOVERY_TOOLS:
                role, reason = (OfficeRole.ENGINEER, "lookup_after_edit") if edited \
                    else (OfficeRole.ANALYST, "discovery")
            elif tool in EDIT_TOOLS:
                role, reason = OfficeRole.ENGINEER, "edit"
                edited = True
            elif tool in SHELL_TOOLS:
                role, reason = classify_command(_command_of(raw)), "shell"
            else:
                role, reason = OfficeRole.ENGINEER, "other_tool"
            out.append(_stamp(event, role, reason))
        else:
            log.debug("attribute.skip_unknown_type type=%r", kind)
    return out


# ── Optional LLM embellishment ───────────────────────────────────────────────
EMBELLISH_SYSTEM = (
    "You write short in-character office banter for a livestream that replays a "
    "software work session as an office. Roles: ceo, tech_lead, analyst, engineer, "
    "tester, marketing, office_manager, party_member. Reply with ONLY a JSON object: "
    '{"handoffs": [{"before": <event index>, "role": <role giving the order>, '
    '"text": <one sentence handing work to the next speaker>}], '
    '"reactions": [{"after": <event index>, "text": <one-sentence marketing hype reaction>}]}. '
    "At most one handoff per role change and at most 5 reactions. Never include secrets, "
    "credentials, IP addresses or usernames."
)


def _outline(events):
    lines = []
    for i, ev in enumerate(events):
        gist = ev.get("text") or f"{ev.get('tool')}: {ev.get('input_summary', '')}"
        lines.append(f"{i} [{ev['role']}] {ev['type']}: {_oneline(gist, 120)}")
    return "\n".join(lines)


def _parse_json_object(reply):
    reply = (reply or "").strip()
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    data = json.loads(reply[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("reply is not an object")
    return data


def _clean_line(text):
    text = _oneline(text, EMBELLISH_LINE_CHARS)
    return text or None


def embellish_events(events, llm_client):
    """Insert LLM hand-off lines and Marketing reactions. Falls back to the
    unembellished events (logged WARN) if the client fails or replies badly."""
    _trace("embellish_events enter events=%d", len(events))
    try:
        reply = llm_client.complete(EMBELLISH_SYSTEM, [{"role": "user", "content": _outline(events)}])
        data = _parse_json_object(reply)
    except Exception as exc:  # the pass is optional — never sink the episode
        log.warning("embellish.fallback reason=%s", type(exc).__name__)
        return events
    before, after = {}, {}
    for item in data.get("handoffs") or []:
        try:
            idx, role, text = int(item["before"]), OfficeRole(item["role"]), _clean_line(item["text"])
        except (KeyError, TypeError, ValueError):
            log.debug("embellish.skip_handoff reason=malformed")
            continue
        if not (0 <= idx < len(events)) or not text or idx in before:
            continue
        target = OfficeRole(events[idx]["role"])
        if role != target and not can_direct(role, target):
            log.debug("embellish.skip_handoff reason=chain role=%s target=%s", role.value, target.value)
            continue
        before[idx] = _stamp({"type": "assistant_text", "text": text, "embellished": "handoff"},
                             role, "llm_handoff")
    for item in (data.get("reactions") or [])[:5]:
        try:
            idx, text = int(item["after"]), _clean_line(item["text"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= idx < len(events) and text and idx not in after:
            after[idx] = _stamp({"type": "assistant_text", "text": text, "embellished": "reaction"},
                                OfficeRole.MARKETING, "llm_reaction")
    out = []
    for i, ev in enumerate(events):
        if i in before:
            out.append(before[i])
        out.append(ev)
        if i in after:
            out.append(after[i])
    log.debug("embellish.done handoffs=%d reactions=%d", len(before), len(after))
    return out


# ── Top level ────────────────────────────────────────────────────────────────
def episode_name(record):
    raw = f"office-{record.get('source_tool') or 'session'}-{record.get('session_id') or 'unknown'}"
    return _NAME_SAFE.sub("-", raw).strip("-")[:128]


def attribute(record, *, llm_client=None, embellish=False):
    """One sessionCorpus record -> office replay episode dict.

    Raises AttributionError for a malformed record, for embellish=True with no
    client, or when the finished episode fails the leak audit."""
    _trace("attribute enter embellish=%s", embellish)
    if not isinstance(record, dict) or not isinstance(record.get("events"), list):
        raise AttributionError("record must be an object with an 'events' list")
    if embellish and llm_client is None:
        raise AttributionError("embellish=True needs an llm_client")

    events = _attribute_events(record["events"])
    if embellish:
        events = embellish_events(events, llm_client)
    events = _redact_all(events)

    slots = sorted({ev["speaker"] for ev in events}, key=lambda s: int(s.split("_", 1)[1]))
    episode = {
        "source": episode_name(record),
        "project": redact(str(record.get("project") or "")),
        "session_id": redact(str(record.get("session_id") or "")),
        "date": str(record.get("started_at") or ""),
        "events": events,
    }
    if slots:
        episode["show"] = {"slots": slots}

    if audit(json.dumps(episode, ensure_ascii=False)) is not None:
        # Never echo the match — it is the secret.
        log.error("attribute.audit_failed session=%s", episode["source"])
        raise AttributionError("episode failed the leak audit; refusing to emit it")
    log.info("attribute.done source=%s events=%d slots=%d", episode["source"], len(events), len(slots))
    return episode


def load_record(path, session_id):
    """Find the record with `session_id` in a sessionCorpus JSONL export."""
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and str(record.get("session_id")) == str(session_id):
                return record
    raise AttributionError(f"session {session_id!r} not found in {Path(path).name}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Role-attribute a sessionCorpus session into an office replay episode")
    ap.add_argument("export", help="sessionCorpus export .jsonl")
    ap.add_argument("--session", required=True, help="session_id to convert")
    ap.add_argument("--out", required=True, help="episode JSON output path")
    ap.add_argument("--embellish", action="store_true",
                    help="add LLM hand-off lines + Marketing reactions (LLM_PROVIDER env / defaults)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    client = None
    if args.embellish:
        from llm_client import build_llm_client
        client = build_llm_client({})
    try:
        episode = attribute(load_record(args.export, args.session), llm_client=client, embellish=args.embellish)
    except AttributionError as exc:
        log.error("role_attribution.failed reason=%s", exc)
        return 1
    Path(args.out).write_text(json.dumps(episode, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[role_attribution] wrote {len(episode['events'])} events, slots={episode.get('show', {}).get('slots')} -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
