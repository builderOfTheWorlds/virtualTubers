#!/usr/bin/env python3
"""
session_log_parser.py
Parses a claudeBackupUtility session log directory (conversation.md +
tool_NNN_<Tool>.md detail files) into a canonical, redacted "event script"
— the source material for stream replays (see docs/session_log_parser.md).

The script is the *facts* of a session: user turns, assistant narration,
and tool calls with enough re-enactment detail (edit old/new, shell
command/output, written file content) to drive tmux panes verbatim.
Persona re-voicing happens in a separate pass and must never alter
tool-call events — only narration text is fair game there.

Redaction is part of parsing, not an afterthought: every text field is
scrubbed (IPs, key-shaped tokens, emails, user home paths) before an
event leaves this module, because downstream consumers feed panes that
ffmpeg broadcasts to a public stream.
"""
import argparse
import json
import re
from pathlib import Path


# ── Redaction ────────────────────────────────────────────────────────────────
# Real usernames appearing in these logs. Word-boundary redacted everywhere —
# paths, ls output, git blame, anywhere. Extend when logs from other machines
# are added.
USERNAMES = ["frogg"]

# Order matters: specific token shapes first, generic patterns last.
REDACTION_RULES = [
    # API / access tokens by known prefix
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"), "[api-key]"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "[github-token]"),
    (re.compile(r"whsec_[A-Za-z0-9]{16,}"), "[webhook-secret]"),
    (re.compile(r"\blive_[A-Za-z0-9_]{16,}"), "[stream-key]"),
    (re.compile(r"\bAKIA[A-Z0-9]{16}\b"), "[aws-key]"),
    # Long hex blobs (potential secrets/hashes of credentials)
    (re.compile(r"\b[0-9a-fA-F]{40,}\b"), "[hex-token]"),
    # Emails
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[email]"),
    # IPv4 addresses (LAN topology, Tailscale/public endpoints)
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[ip]"),
    # Home directories -> generic user, all path styles seen in the logs
    (re.compile(r"(?i)([A-Za-z]:\\+Users\\+)[^\\/\s]+"), r"\1dev"),
    (re.compile(r"(?i)(/c/Users/)[^/\s]+"), r"\1dev"),
    (re.compile(r"(?i)(/home/)[^/\s]+"), r"\1dev"),
    # Slugified project-dir form used by ~/.claude/projects (c--Users-frogg-...)
    (re.compile(r"(?i)(users-)[a-z0-9]+(?=-)"), r"\1dev"),
    # Partial private/tailnet IP fragments (e.g. "192.168.1:8090", "10.0.x.x")
    # that the full-IPv4 rule above leaves behind
    (re.compile(r"\b(?:192\.168|10\.\d{1,3}|100\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.[0-9xX.]*"), "[ip]"),
] + [
    # Username catch-all — must run last so path rules got first shot
    (re.compile(r"(?i)\b" + re.escape(name) + r"\b"), "dev")
    for name in USERNAMES
]


def redact(text):
    """Scrub sensitive material from a text field. DEBUG-level concern:
    this must stay pure/deterministic — replays depend on stable output."""
    if not text:
        return text
    for pattern, replacement in REDACTION_RULES:
        text = pattern.sub(replacement, text)
    return text


# ── Noise filtering ──────────────────────────────────────────────────────────
# Harness-injected user "turns" that carry nothing performable.
NOISE_TAGS = (
    "<local-command-caveat>",
    "<command-name>",
    "<local-command-stdout>",
    "<task-notification>",
    "<system-reminder>",
    "<command-message>",
)

# Strip inline noise blocks (tag ... matching close tag) out of mixed turns.
_INLINE_NOISE = re.compile(
    r"<(local-command-caveat|command-name|command-message|command-args|"
    r"local-command-stdout|task-notification|system-reminder|ide_selection|"
    r"ide_opened_file)>.*?</\1>",
    re.DOTALL,
)


def clean_user_text(text):
    """Remove harness noise from a user turn; returns '' if nothing
    audience-worthy remains."""
    text = _INLINE_NOISE.sub("", text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(tag) for tag in NOISE_TAGS):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


# ── conversation.md parsing ──────────────────────────────────────────────────
_TURN_HEADER = re.compile(r"^## (User|Assistant)\s*$")
_TOOL_KEY = re.compile(r"^\*\*(Tool|Input|Output):\*\*\s?(.*)$")
_DETAILS_LINK = re.compile(r"^\[Full details\]\(([^)]+)\)\s*$")
_META_LINE = re.compile(r"^\*\*(Project|Session ID|Date):\*\*\s*`?([^`]+)`?\s*$")

MAX_OUTPUT_CHARS = 4000  # cap re-enactment payloads; panes can't show more anyway


def _parse_tool_block(block_lines, session_dir):
    """A blockquote group starting '> **Tool:**' -> tool_call event."""
    fields = {}
    current_key = None
    detail_file = None
    for raw in block_lines:
        line = raw[2:] if raw.startswith("> ") else raw.lstrip(">")
        link = _DETAILS_LINK.match(line.strip())
        if link:
            detail_file = link.group(1)
            continue
        key_match = _TOOL_KEY.match(line.strip())
        if key_match:
            current_key = key_match.group(1).lower()
            fields[current_key] = key_match.group(2)
        elif current_key:
            fields[current_key] += "\n" + line
    tool_name = (fields.get("tool") or "unknown").strip()
    # Errored calls render as "<Tool> **ERROR**" in conversation.md — keep the
    # error as a flag so the replayer can act it out (frustrated avatar, etc.).
    errored = "**ERROR**" in tool_name
    if errored:
        tool_name = tool_name.replace("**ERROR**", "").strip()
    event = {
        "type": "tool_call",
        "tool": tool_name,
        "error": errored,
        "input_summary": (fields.get("input") or "").strip(),
        "output_summary": (fields.get("output") or "").strip(),
        "detail_file": detail_file,
    }
    if detail_file:
        detail = parse_tool_detail(session_dir / detail_file, event["tool"])
        if detail:
            event["detail"] = detail
    return event


def parse_conversation(session_dir):
    """conversation.md -> (metadata, raw event list). Redaction is applied
    by parse_session, not here, so unit tests can assert on both stages."""
    text = (session_dir / "conversation.md").read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    metadata = {}
    events = []
    role = None
    text_buf = []
    quote_buf = []

    def flush_text():
        content = "\n".join(text_buf).strip()
        text_buf.clear()
        if not content or role is None:
            return
        if role == "User":
            content = clean_user_text(content)
            if content:
                events.append({"type": "user_message", "text": content})
        else:
            events.append({"type": "assistant_text", "text": content})

    def flush_quote():
        if not quote_buf:
            return
        first = quote_buf[0].lstrip("> ").strip()
        if first.startswith("**Tool:**"):
            # Narration typed before this tool call must land before it in
            # the event stream, or replays perform actions out of order.
            flush_text()
            events.append(_parse_tool_block(list(quote_buf), session_dir))
        else:
            # An ordinary markdown blockquote inside narration — keep as text.
            text_buf.extend(quote_buf)
        quote_buf.clear()

    for line in lines:
        meta = _META_LINE.match(line.strip())
        if meta and role is None:
            metadata[meta.group(1).lower().replace(" ", "_")] = meta.group(2).strip()
            continue
        header = _TURN_HEADER.match(line)
        if header:
            flush_quote()
            flush_text()
            role = header.group(1)
            continue
        if line.startswith(">"):
            quote_buf.append(line)
            continue
        if quote_buf and not line.strip():
            flush_quote()
            continue
        flush_quote()
        text_buf.append(line)
    flush_quote()
    flush_text()
    return metadata, events


# ── tool_NNN_<Tool>.md detail parsing ────────────────────────────────────────
_FILE_LINE = re.compile(r"^\*\*File:\*\*\s*`?(.+?)`?\s*$", re.MULTILINE)


def _sections(text):
    """Split a detail file on '## ' headings -> {heading: body}."""
    parts = re.split(r"^## +(.+)$", text, flags=re.MULTILINE)
    out = {}
    for i in range(1, len(parts) - 1, 2):
        out[parts[i].strip().lower()] = parts[i + 1]
    return out


def _fenced(body):
    """Content between the first and last fence line of a section body.
    Tolerates nested backticks (e.g. a Write of a markdown file)."""
    lines = body.splitlines()
    fence_idx = [i for i, l in enumerate(lines) if l.startswith("```")]
    if len(fence_idx) < 2:
        return None
    return "\n".join(lines[fence_idx[0] + 1:fence_idx[-1]])


def _labeled_fences(body, labels):
    """For sections holding multiple labeled fenced blocks (Edit's
    **Old:** / **New:**): returns {label: content}."""
    out = {}
    positions = []
    for label in labels:
        m = re.search(r"^\*\*" + label + r":\*\*\s*$", body, re.MULTILINE)
        if m:
            positions.append((m.start(), label))
    positions.sort()
    for idx, (start, label) in enumerate(positions):
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(body)
        content = _fenced(body[start:end])
        if content is not None:
            out[label.lower()] = content
    return out


def parse_tool_detail(path, tool):
    """Extract re-enactment detail for the tools the replayer performs
    visually. Unknown tools return None — the summary line is enough."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    sections = _sections(text)
    input_body = sections.get("input", "")
    output_body = sections.get("output", "")

    if tool in ("Bash", "PowerShell"):
        detail = {}
        raw = _fenced(input_body)
        if raw:
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                detail["command"] = payload.get("command", "")
                detail["description"] = payload.get("description", "")
            else:
                # Input block wasn't a JSON object (truncated/odd log) —
                # keep the raw text so the replayer can still show something.
                detail["command"] = raw
        out = _fenced(output_body)
        if out is not None:
            detail["output"] = out[:MAX_OUTPUT_CHARS]
        return detail or None

    if tool == "Edit":
        file_match = _FILE_LINE.search(input_body)
        detail = _labeled_fences(input_body, ["Old", "New"])
        if file_match:
            detail["file"] = file_match.group(1)
        return detail or None

    if tool == "Write":
        file_match = _FILE_LINE.search(input_body)
        content = _fenced(input_body)
        detail = {}
        if file_match:
            detail["file"] = file_match.group(1)
        if content is not None:
            detail["content"] = content[:MAX_OUTPUT_CHARS]
        return detail or None

    if tool == "Read":
        file_match = _FILE_LINE.search(input_body)
        return {"file": file_match.group(1)} if file_match else None

    return None


# ── Top level ────────────────────────────────────────────────────────────────
def _redact_event(event):
    for key, value in list(event.items()):
        if isinstance(value, str):
            event[key] = redact(value)
        elif isinstance(value, dict):
            _redact_event(value)
    return event


def parse_session(session_dir):
    """Session log directory -> canonical redacted script dict."""
    session_dir = Path(session_dir)
    metadata, events = parse_conversation(session_dir)
    events = [_redact_event(e) for e in events]
    return {
        "source": session_dir.name,
        "project": metadata.get("project", ""),
        "session_id": metadata.get("session_id", ""),
        "date": metadata.get("date", ""),
        "events": events,
    }


def summarize(script):
    counts = {}
    for event in script["events"]:
        key = event["type"] if event["type"] != "tool_call" else f"tool:{event['tool']}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def main():
    parser = argparse.ArgumentParser(description="Parse a Claude session log into a replay script")
    parser.add_argument("session_dir", help="Path to a <timestamp>_<id> session log directory")
    parser.add_argument("--out", help="Write the script JSON here")
    parser.add_argument("--summary", action="store_true", help="Print event-type counts")
    args = parser.parse_args()

    script = parse_session(args.session_dir)
    if args.out:
        Path(args.out).write_text(
            json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"[session_log_parser] wrote {len(script['events'])} events -> {args.out}")
    if args.summary or not args.out:
        print(f"[session_log_parser] session={script['source']} events={len(script['events'])}")
        for key, count in sorted(summarize(script).items()):
            print(f"  {key:24s} {count}")


if __name__ == "__main__":
    main()
