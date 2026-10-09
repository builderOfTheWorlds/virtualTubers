"""Repair the one YAML mistake models keep making in planner replies.

A planner reply is YAML written by an LLM. Prose fields (synopsis,
continuity_out, prompt, summary ...) come back as unquoted plain scalars, and
prose regularly contains ": " ("... and a warning: the tear is not a door").
YAML reads that as a nested mapping and the whole reply fails to parse
("mapping values are not allowed here"), so the batch is retried and, after
max_attempts, skipped. Seen live on vllm-agents (Qwen3.6-35B-A3B) 2026-10-09:
the opening arc batch was skipped 3/3 for exactly this.

`quote_plain_scalars` double-quotes any `key: value` line whose plain value
contains ": " or " #" (a comment marker that would silently truncate it).
Values that are already quoted, flow collections, block scalars, anchors or
aliases are left alone. Parsers try the reply as-is first and only fall back
to the repaired text, so a reply that already parses is never changed.
"""
import re

# `  - key: value` / `  key: value`, key = a plain identifier.
_LINE = re.compile(r"^(?P<lead>\s*(?:-\s+)?)(?P<key>[A-Za-z_][\w\-]*):[ \t]+(?P<val>.+?)\s*$")
_SKIP_START = ('"', "'", "{", "[", "|", ">", "&", "*", "!")


def _needs_quotes(val: str) -> bool:
    return ": " in val or " #" in val or val.endswith(":")


def quote_plain_scalars(text: str) -> str:
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            val = m.group("val")
            if not val.startswith(_SKIP_START) and _needs_quotes(val):
                escaped = val.replace("\\", "\\\\").replace('"', '\\"')
                line = f'{m.group("lead")}{m.group("key")}: "{escaped}"'
        out.append(line)
    return "\n".join(out)
