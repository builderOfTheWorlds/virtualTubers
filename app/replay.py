#!/usr/bin/env python3
"""
replay.py
Performs a parsed session script (app/session_log_parser.py) as a paced,
colorized "show" on stdout — designed to run inside a tmux pane on stream,
but equally happy in any terminal for local preview.

Display-only by design: recorded commands and edits are RENDERED, never
executed. The only side effect is the avatar state file (agent_state.py),
which the avatar pane already knows how to read.

Persona re-voicing (a later pass) rewrites narration text in the script
before it reaches this module; the replayer performs whatever text it's
given and never calls an LLM itself.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from agent_state import write_state


# ── Pacing defaults (chars/sec and pauses; --speed scales everything) ────────
DIALOGUE_CPS = 45       # human-ish typing for spoken lines
CODE_CPS = 130          # faster for code being "written"
OUTPUT_LINES_PER_S = 18  # terminal output scrolls in at this rate
EVENT_PAUSE_S = 0.8     # beat between events
MAX_OUTPUT_LINES = 24   # cap displayed command output / file content
BUBBLE_CHARS = 120      # avatar speech-bubble excerpt length


class Pacer:
    """All sleeps/typing rhythm go through here so tests (and --no-delay)
    can run instantly with enabled=False."""

    def __init__(self, speed=1.0, enabled=True):
        self.speed = max(speed, 0.01)
        self.enabled = enabled

    def sleep(self, seconds):
        if self.enabled and seconds > 0:
            time.sleep(seconds / self.speed)

    def type_out(self, write, text, cps):
        """Emit text through `write` at roughly cps characters/second."""
        if not self.enabled:
            write(text)
            return
        delay = 1.0 / (cps * self.speed)
        for ch in text:
            write(ch)
            time.sleep(delay)


# ── ANSI helpers ─────────────────────────────────────────────────────────────
class Palette:
    def __init__(self, enabled=True):
        c = enabled
        self.reset = "\x1b[0m" if c else ""
        self.dim = "\x1b[2m" if c else ""
        self.bold = "\x1b[1m" if c else ""
        self.cyan = "\x1b[36m" if c else ""
        self.green = "\x1b[32m" if c else ""
        self.yellow = "\x1b[33m" if c else ""
        self.red = "\x1b[31m" if c else ""
        self.magenta = "\x1b[35m" if c else ""


def _truncate_lines(text, max_lines):
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return lines, 0
    return lines[:max_lines], len(lines) - max_lines


class Performer:
    """Renders script events in order. One instance per episode."""

    def __init__(self, out=None, pacer=None, palette=None, worker_name="KODI-7",
                 state_path=None, max_output_lines=MAX_OUTPUT_LINES):
        self.out = out or sys.stdout
        self.pacer = pacer or Pacer()
        self.c = palette or Palette()
        self.worker_name = worker_name
        self.state_path = state_path
        self.max_output_lines = max_output_lines

    # ── low-level emit helpers ───────────────────────────────────────────────
    def _write(self, text):
        self.out.write(text)
        self.out.flush()

    def _line(self, text=""):
        self._write(text + "\n")

    def _typed(self, text, cps, prefix="", color=""):
        if prefix:
            self._write(prefix)
        if color:
            self._write(color)
        self.pacer.type_out(self._write, text, cps)
        if color:
            self._write(self.c.reset)
        self._write("\n")

    def _avatar(self, expression, action="", bubble=None):
        """Best-effort avatar update — a missing/read-only state path must
        never take the show down."""
        if not self.state_path:
            return
        if bubble:
            bubble = bubble[:BUBBLE_CHARS]
        try:
            write_state(self.state_path, expression, action=action, bubble=bubble)
        except OSError as exc:
            print(f"[replay] avatar state update skipped: {exc}", file=sys.stderr)

    def _paced_output(self, text, color=""):
        """Scroll pre-recorded output in line by line, truncated."""
        lines, hidden = _truncate_lines(text, self.max_output_lines)
        for line in lines:
            self._line(f"{color}{line}{self.c.reset}" if color else line)
            self.pacer.sleep(1.0 / OUTPUT_LINES_PER_S)
        if hidden:
            self._line(f"{self.c.dim}… ({hidden} more lines){self.c.reset}")

    # ── event handlers ───────────────────────────────────────────────────────
    def _on_user_message(self, event):
        c = self.c
        self._line()
        self._line(f"{c.cyan}{c.bold}┌─ BOSS ─────────────────────────────{c.reset}")
        self._avatar("thinking", action="reading a message from the boss")
        for line in event["text"].splitlines():
            self._typed(line, DIALOGUE_CPS * 2, prefix=f"{c.cyan}│ {c.reset}")
        self._line(f"{c.cyan}└────────────────────────────────────{c.reset}")

    def _on_assistant_text(self, event):
        c = self.c
        text = event["text"]
        self._line()
        self._avatar("speaking", action="talking to the stream", bubble=text)
        self._write(f"{c.green}{c.bold}{self.worker_name} ▸{c.reset} ")
        first = True
        for line in text.splitlines():
            if not first:
                self._write("  ")
            self.pacer.type_out(self._write, line, DIALOGUE_CPS)
            self._write("\n")
            first = False

    def _on_tool_call(self, event):
        handler = {
            "Bash": self._perform_shell,
            "PowerShell": self._perform_shell,
            "Edit": self._perform_edit,
            "Write": self._perform_write,
            "Read": self._perform_read,
        }.get(event["tool"], self._perform_generic)
        handler(event)
        if event.get("error"):
            self._avatar("frustrated", action=f"{event['tool']} failed",
                         bubble="Ugh, that didn't work...")
            self._line(f"{self.c.red}✗ that didn't work{self.c.reset}")

    def _perform_shell(self, event):
        c = self.c
        detail = event.get("detail") or {}
        command = detail.get("command") or event.get("input_summary") or "(command)"
        self._line()
        self._avatar("focused", action=f"running: {command[:60]}")
        for i, line in enumerate(command.splitlines()):
            prefix = f"{c.yellow}${c.reset} " if i == 0 else "  "
            self._typed(line, CODE_CPS, prefix=prefix)
        output = detail.get("output")
        if output:
            self._paced_output(output, color=c.dim)

    def _perform_edit(self, event):
        c = self.c
        detail = event.get("detail") or {}
        target = detail.get("file") or event.get("input_summary") or "(file)"
        self._line()
        self._line(f"{c.magenta}✎ editing {target}{c.reset}")
        self._avatar("focused", action=f"editing {Path(target).name}")
        old, new = detail.get("old"), detail.get("new")
        if old:
            lines, hidden = _truncate_lines(old, self.max_output_lines)
            for line in lines:
                self._line(f"{c.red}- {line}{c.reset}")
                self.pacer.sleep(1.0 / OUTPUT_LINES_PER_S)
            if hidden:
                self._line(f"{c.dim}… ({hidden} more lines){c.reset}")
        if new:
            lines, hidden = _truncate_lines(new, self.max_output_lines)
            for line in lines:
                self._typed(line, CODE_CPS, prefix=f"{c.green}+ {c.reset}", color=c.green)
            if hidden:
                self._line(f"{c.dim}… ({hidden} more lines){c.reset}")

    def _perform_write(self, event):
        c = self.c
        detail = event.get("detail") or {}
        target = detail.get("file") or event.get("input_summary") or "(file)"
        self._line()
        self._line(f"{c.magenta}✎ new file {target}{c.reset}")
        self._avatar("focused", action=f"writing {Path(target).name}")
        content = detail.get("content")
        if content:
            lines, hidden = _truncate_lines(content, self.max_output_lines)
            for line in lines:
                self._typed(line, CODE_CPS * 2, prefix=f"{c.green}+ {c.reset}", color=c.green)
            if hidden:
                self._line(f"{c.dim}… ({hidden} more lines){c.reset}")

    def _perform_read(self, event):
        detail = event.get("detail") or {}
        target = detail.get("file") or event.get("input_summary") or "(file)"
        self._line(f"{self.c.dim}⋯ reading {target}{self.c.reset}")
        self._avatar("focused", action=f"reading {Path(target).name}")
        self.pacer.sleep(0.5)

    def _perform_generic(self, event):
        summary = event.get("input_summary", "")[:100]
        self._line(f"{self.c.dim}⋯ {event['tool']}: {summary}{self.c.reset}")
        self._avatar("focused", action=f"using {event['tool']}")
        self.pacer.sleep(0.5)

    # ── top level ────────────────────────────────────────────────────────────
    def perform(self, script, start=0, limit=None):
        c = self.c
        events = script.get("events", [])[start:]
        if limit is not None:
            events = events[:limit]
        title = script.get("source", "episode")
        self._line(f"{c.bold}{c.magenta}══ REPLAY: {title} "
                   f"({len(events)} scenes) ══{c.reset}")
        self._avatar("idle", action="getting ready for a rerun")

        dispatch = {
            "user_message": self._on_user_message,
            "assistant_text": self._on_assistant_text,
            "tool_call": self._on_tool_call,
        }
        for event in events:
            handler = dispatch.get(event.get("type"))
            if handler is None:
                continue
            handler(event)
            self.pacer.sleep(EVENT_PAUSE_S)

        self._line()
        self._line(f"{c.bold}{c.magenta}══ fin ══{c.reset}")
        self._avatar("happy", action="that's the end of the rerun",
                     bubble="And that's how we did it!")


def load_script(source):
    """Accept a script .json OR a raw session log directory."""
    source = Path(source)
    if source.is_dir():
        # Parse on the fly — keeps the pane command simple in the container.
        from session_log_parser import parse_session
        return parse_session(source)
    return json.loads(source.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Perform a parsed session script as a stream show")
    parser.add_argument("source", help="Script .json (from session_log_parser) or a session log directory")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier")
    parser.add_argument("--no-delay", action="store_true", help="Render instantly (testing)")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    parser.add_argument("--worker-name", default="KODI-7", help="Persona name for dialogue lines")
    parser.add_argument("--state-file", default=None,
                        help="Avatar state file to drive (default: none; in-container use /tmp/agent_state.json)")
    parser.add_argument("--start", type=int, default=0, help="First event index to perform")
    parser.add_argument("--limit", type=int, default=None, help="Max events to perform")
    parser.add_argument("--max-output-lines", type=int, default=MAX_OUTPUT_LINES)
    args = parser.parse_args()

    # Legacy Windows consoles default to cp1252, which can't encode the
    # box-drawing glyphs; the show must render anywhere, degraded not dead.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    script = load_script(args.source)
    performer = Performer(
        pacer=Pacer(speed=args.speed, enabled=not args.no_delay),
        palette=Palette(enabled=not args.no_color),
        worker_name=args.worker_name,
        state_path=args.state_file,
        max_output_lines=args.max_output_lines,
    )
    try:
        performer.perform(script, start=args.start, limit=args.limit)
    except KeyboardInterrupt:
        print("\n[replay] interrupted")


if __name__ == "__main__":
    main()
