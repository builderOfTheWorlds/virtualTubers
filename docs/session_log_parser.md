# session_log_parser

## Overview

Parses a `claudeBackupUtility` session log directory (`conversation.md` +
`tool_NNN_<Tool>.md` detail files) into a **canonical, redacted event
script** — the source material for stream replays ("reenactments" of real
past dev sessions, performed by the workers in their tmux panes).

The script captures the *facts* of a session: user turns, assistant
narration, and tool calls with enough detail to re-enact them visually
(Edit old/new strings, shell commands + outputs, written file content).
A separate persona re-voicing pass may rewrite narration text per airing —
it must never alter `tool_call` events, or the on-screen actions stop
matching the story.

**Redaction is built into parsing.** Every text field is scrubbed before it
leaves this module, because script consumers drive panes that ffmpeg
broadcasts publicly. What gets replaced with a dummy marker:

- **Passwords / credential values** → `[password]`: the value side of any
  `KEY=value` / `KEY: value` / `"key": "value"` assignment whose key names a
  credential (`password`, `passwd`, `passphrase`, `pwd`, `secret`, with
  prefixes — `POSTGRES_PASSWORD`, `PGPASSWORD`, `client_secret`), CLI
  `--password <value>` flags, and the password in
  `scheme://user:pass@host` URLs (user and host survive).
- **Public and tailnet IPv4 addresses** → `[ip]`. **Private LAN IPs stay
  readable** (RFC1918 `10.x`/`172.16-31.x`/`192.168.x`, loopback,
  link-local) — they're harmless on stream and keep shows legible.
  CGNAT/Tailscale `100.x` is scrubbed (maps the tailnet overlay).
- API-key/token shapes (`sk-ant-`, `ghp_`, `whsec_`, `live_`, AWS `AKIA`),
  long hex blobs, emails.
- Usernames in any form, home paths including the slugified
  `c--Users-<name>-...` form.

The strict last-line-of-defense audit regex lives in
`scripts/build_replay_library.py` (`LEAK_AUDIT`) — the build refuses to
write any episode that fails it.

## Signature

```python
def parse_session(session_dir: str | Path) -> dict
```

Also public: `redact(text) -> str`, `clean_user_text(text) -> str`,
`parse_tool_detail(path, tool) -> dict | None`, `summarize(script) -> dict`.

## Parameters

- `session_dir` (str | Path, required): a `<timestamp>_<shortid>` session
  log directory containing `conversation.md`.

## Return Value

A script dict:

```python
{
  "source": "2026-07-02_04-27-00_6ecdde82",
  "project": "virtualTubers",
  "session_id": "6ecdde82-...",
  "date": "2026-07-02_04-27-00",
  "events": [
    {"type": "user_message",   "text": "..."},
    {"type": "assistant_text", "text": "..."},
    {"type": "tool_call", "tool": "Edit", "error": False,
     "input_summary": "...", "output_summary": "...",
     "detail_file": "tool_017_Edit.md",
     "detail": {"file": "...", "old": "...", "new": "..."}},
  ],
}
```

`detail` is present for re-enactable tools: `Bash`/`PowerShell`
(`command`, `description`, `output` — output capped at 4000 chars),
`Edit` (`file`, `old`, `new`), `Write` (`file`, `content`), `Read`
(`file`). Other tools carry summaries only. `error: True` marks calls the
original session logged as `**ERROR**` (replayer can act out frustration).

Harness noise (`<local-command-caveat>`, `<system-reminder>`,
`<task-notification>`, command echoes) is filtered; user turns that were
pure noise are dropped entirely.

## Dependencies

Standard library only (`argparse`, `json`, `re`, `pathlib`).

## Usage Examples

CLI — parse one session and inspect:

```bash
python app/session_log_parser.py \
  "path/to/logs/claude/virtualTubers/2026-07-02_04-27-00_6ecdde82" \
  --out script.json --summary
```

Library — batch-parse an episode catalog:

```python
from pathlib import Path
from session_log_parser import parse_session

for d in sorted(Path(LOGS).iterdir()):
    if d.is_dir():
        script = parse_session(d)
        # feed to the re-voicer / replayer
```

## Error Handling

- Missing/unreadable tool detail files → the event keeps its summaries,
  `detail` is omitted (never raises).
- Shell detail whose input block isn't a JSON object → raw text preserved
  in `detail["command"]`.
- `parse_session` raises `FileNotFoundError` only if `conversation.md`
  itself is absent.

## Changelog

- **v1.1.0** (2026-07-12): Password/credential-value redaction added
  (`KEY=value` assignments, CLI flags, URL credentials → `[password]`)
  after a password reached a live stream. IP policy changed: private LAN
  IPs are now left readable; only public and tailnet (CGNAT `100.x`)
  addresses are scrubbed. `LEAK_AUDIT` updated to match (allows
  `192.168.x`, flags un-redacted credential assignments). Episode library
  must be rebuilt and re-synced after this change.
- **v1.0.0** (2026-07-12): Initial version. Validated against the full
  42-session corpus (2,168 events, 0 parse failures, 0 leaks under strict
  audit).
