#!/usr/bin/env python3
"""
replay.py
Performs a parsed session script (app/session_log_parser.py) as a paced,
colorized "show" on stdout — designed to run inside a tmux pane on stream,
but equally happy in any terminal for local preview.

Display-only by design: recorded commands and edits are RENDERED, never
executed. The only side effect is the avatar state file (agent_state.py),
which the avatar pane already knows how to read.

Persona re-voicing / narration is a separate per-airing pass (revoice.py):
it hands this module a "voiced show" — the script's events grouped into
scenes, each with a spoken line and its synthesized audio (tts_client.py).
The replayer performs whatever text it's given and never calls an LLM
itself. Audio anchors the timing: each scene's visual pacing is scaled so
the on-screen rendering and the spoken line finish together.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from agent_state import write_state
from audio_player import play_wav, wait_extra


# ── Pacing defaults (chars/sec and pauses; --speed scales everything) ────────
DIALOGUE_CPS = 45       # human-ish typing for spoken lines
CODE_CPS = 130          # faster for code being "written"
OUTPUT_LINES_PER_S = 18  # terminal output scrolls in at this rate
EVENT_PAUSE_S = 0.8     # beat between events
MAX_OUTPUT_LINES = 24   # cap displayed command output / file content
BUBBLE_CHARS = 120      # avatar speech-bubble excerpt length

# Audio-anchored scenes scale visual pacing to the spoken line's measured
# duration, clamped so a scene never crawls or blurs; outside the clamp the
# slack is absorbed by waiting (visuals done first) or by the audio simply
# ending early (visuals still going).
MIN_SCENE_SCALE = 0.4
MAX_SCENE_SCALE = 3.0
TOOL_BEAT_S = 0.5       # pause for Read/generic tool events


class Pacer:
    """All sleeps/typing rhythm go through here so tests (and --no-delay)
    can run instantly with enabled=False. `scale` is the per-scene
    audio-sync factor layered on top of the show-wide `speed`."""

    def __init__(self, speed=1.0, enabled=True):
        self.speed = max(speed, 0.01)
        self.enabled = enabled
        self.scale = 1.0

    @property
    def effective_speed(self):
        return max(self.speed * self.scale, 0.01)

    def sleep(self, seconds):
        if self.enabled and seconds > 0:
            time.sleep(seconds / self.effective_speed)

    def type_out(self, write, text, cps):
        """Emit text through `write` at roughly cps characters/second."""
        if not self.enabled:
            write(text)
            return
        delay = 1.0 / (cps * self.effective_speed)
        for ch in text:
            write(ch)
            time.sleep(delay)


def estimate_event_seconds(event, max_output_lines=MAX_OUTPUT_LINES):
    """Seconds one event takes to render at speed 1.0 / scale 1.0.

    Mirrors the Performer handlers' pacing math — revoice.py sizes each
    scene's narration from this, and _perform_scene derives the audio-sync
    scale from it, so keep the two in lockstep when touching pacing.
    """

    def displayed(text):
        lines = text.splitlines()
        return lines[:max_output_lines]

    kind = event.get("type")
    if kind == "user_message":
        return len(event.get("text", "")) / (DIALOGUE_CPS * 2) + EVENT_PAUSE_S
    if kind == "assistant_text":
        return len(event.get("text", "")) / DIALOGUE_CPS + EVENT_PAUSE_S
    if kind != "tool_call":
        return 0.0

    tool = event.get("tool")
    detail = event.get("detail") or {}
    seconds = EVENT_PAUSE_S
    if tool in ("Bash", "PowerShell"):
        command = detail.get("command") or event.get("input_summary") or ""
        seconds += len(command) / CODE_CPS
        output = detail.get("output")
        if output:
            seconds += len(displayed(output)) / OUTPUT_LINES_PER_S
    elif tool == "Edit":
        old, new = detail.get("old"), detail.get("new")
        if old:
            seconds += len(displayed(old)) / OUTPUT_LINES_PER_S
        if new:
            seconds += sum(len(line) for line in displayed(new)) / CODE_CPS
    elif tool == "Write":
        content = detail.get("content")
        if content:
            seconds += sum(len(line) for line in displayed(content)) / (CODE_CPS * 2)
    else:
        seconds += TOOL_BEAT_S
    return seconds


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
                 state_path=None, max_output_lines=MAX_OUTPUT_LINES, *,
                 on_scene_start=None, wait_for_scene=None):
        self.out = out or sys.stdout
        self.pacer = pacer or Pacer()
        self.c = palette or Palette()
        self.worker_name = worker_name
        self.state_path = state_path
        self.max_output_lines = max_output_lines
        # Duet hooks (both default None => today's straight-through solo
        # performance, unchanged). A director sets on_scene_start to publish
        # a per-scene cue immediately before that scene performs; a follower
        # sets wait_for_scene to block until its own cue file authorizes the
        # scene (see perform()). Exceptions from on_scene_start are caught
        # there — a bus hiccup must not take the show down.
        self.on_scene_start = on_scene_start
        self.wait_for_scene = wait_for_scene

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

    # ── scene performance (a scene = events + optional spoken narration) ────
    def _perform_events(self, events):
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

    def _perform_scene(self, scene):
        """Perform one scene; when it carries synthesized narration, anchor
        the visual pacing to the audio's measured duration so both finish
        together (revoice.py's timing model).

        Duet followers (docs/duet_replay.md) pass scenes for speakers they
        don't voice: "owned" (default True) gates whether THIS worker plays
        that scene's audio and shows the speaking bubble at all — every
        stream still renders the full visuals and prints the narration line,
        so viewers on any cast member's channel see the whole show. A scene
        that isn't owned (or is owned but has no audio, e.g. reuse dropped
        the WAV) carries "target_duration" instead: visual pacing scales to
        that instead of audio.duration, using the same clamp, and the scene
        holds on the wall clock until target_duration has elapsed — keeping
        this worker's stream in lockstep with the owner's, even with no
        sound to anchor to."""
        owned = scene.get("owned", True)
        narration = scene.get("narration")
        audio = scene.get("audio")
        target_duration = scene.get("target_duration")
        if narration:
            self._line()
            self._line(f"{self.c.dim}♪ {narration}{self.c.reset}")
            if owned:
                self._avatar("speaking", action="narrating the rerun", bubble=narration)
            else:
                self._avatar("idle", action="listening to the show")

        playback, started, hold_seconds = None, None, None
        if owned and audio is not None and audio.duration > 0:
            natural = sum(
                estimate_event_seconds(e, self.max_output_lines)
                for e in scene["events"]
            ) / self.pacer.speed
            self.pacer.scale = min(MAX_SCENE_SCALE,
                                   max(MIN_SCENE_SCALE, natural / audio.duration))
            playback = play_wav(audio.audio_path)
            started = time.monotonic()
        elif not (owned and audio is not None) and target_duration is not None and target_duration > 0:
            natural = sum(
                estimate_event_seconds(e, self.max_output_lines)
                for e in scene["events"]
            ) / self.pacer.speed
            self.pacer.scale = min(MAX_SCENE_SCALE,
                                   max(MIN_SCENE_SCALE, natural / target_duration))
            started = time.monotonic()
            hold_seconds = target_duration
        try:
            self._perform_events(scene["events"])
        finally:
            self.pacer.scale = 1.0
        if playback is not None:
            # Visuals done first (scale clamped, or estimate ran short):
            # hold the scene until the spoken line lands.
            if self.pacer.enabled:
                wait_extra(playback, started, audio.duration)
            else:
                playback.stop()
        elif hold_seconds is not None and self.pacer.enabled:
            remaining = hold_seconds - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)

    # ── top level ────────────────────────────────────────────────────────────
    def perform(self, script, show=None, start=0, limit=None):
        """Perform an episode. `show` is an optional voiced show from
        revoice.prepare_show() — scenes with narration + audio; without it,
        the script's events play silently exactly as before. start/limit
        slice events when unvoiced, scenes when voiced.

        Index-based (rather than a plain for-loop) so a duet follower's
        wait_for_scene hook can jump the index forward or abort mid-show
        (docs/duet_replay.md, "cue ratchet + fast-forward rule"). With
        neither on_scene_start nor wait_for_scene set this is exactly
        today's straight-through loop.
        """
        c = self.c
        if show is None:
            events = script.get("events", [])[start:]
            if limit is not None:
                events = events[:limit]
            scenes = [{"events": [event]} for event in events]
        else:
            scenes = show[start:]
            if limit is not None:
                scenes = scenes[:limit]
        title = script.get("source", "episode")
        self._line(f"{c.bold}{c.magenta}══ REPLAY: {title} "
                   f"({len(scenes)} scenes) ══{c.reset}")
        self._avatar("idle", action="getting ready for a rerun")

        n = len(scenes)
        i = 0
        while i < n:
            if self.on_scene_start is not None:
                try:
                    self.on_scene_start(i)
                except Exception as exc:
                    # A cue-publish failure must not take the show down —
                    # the follower's watchdog will time out and recover.
                    print(f"[replay] on_scene_start hook failed for scene {i}: {exc}",
                          file=sys.stderr)

            catch_up_to = None
            if self.wait_for_scene is not None:
                authorized = self.wait_for_scene(i)
                if authorized == -1:
                    self._line()
                    self._line(f"{c.bold}{c.magenta}══ interrupted ══{c.reset}")
                    self._avatar("idle", action="show interrupted")
                    return
                if authorized - i >= 2:
                    catch_up_to = min(authorized, n - 1)

            self._perform_scene(scenes[i])
            i += 1

            if catch_up_to is not None:
                # ≥2 scenes behind the cue: race through the backlog with
                # pacing off so this stream catches back up to the director.
                was_enabled = self.pacer.enabled
                self.pacer.enabled = False
                try:
                    while i <= catch_up_to:
                        self._perform_scene(scenes[i])
                        i += 1
                finally:
                    self.pacer.enabled = was_enabled

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


def prepare_voiced_show(script, config, workdir, worker_name="KODI-7",
                        speed=1.0, max_output_lines=MAX_OUTPUT_LINES,
                        progress=None):
    """Glue for callers holding a worker config: build the LLM + TTS clients
    from its `llm`/`voice` sections and run revoice.prepare_show(). Returns
    None when voice is disabled (voice.provider null/missing) — meaning
    "perform silently". Imported lazily: revoice imports this module's
    estimator, and llm_client drags in the anthropic/httpx deps."""
    from llm_client import build_llm_client
    from revoice import prepare_show
    from tts_client import build_tts_client

    tts = build_tts_client(config)
    if tts is None:
        return None
    return prepare_show(
        script, build_llm_client(config), tts, workdir,
        worker_name=worker_name,
        boss_name=(config.get("voice") or {}).get("boss_name", "the boss"),
        speed=speed, max_output_lines=max_output_lines, progress=progress,
    )


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
    parser.add_argument("--voice-config", default=None,
                        help="Worker config YAML whose voice+llm sections drive spoken "
                             "narration (docs/revoice.md); omit for a silent show")
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

    show = None
    if args.voice_config:
        import tempfile

        import yaml
        config = yaml.safe_load(Path(args.voice_config).read_text(encoding="utf-8")) or {}
        with tempfile.TemporaryDirectory(prefix="replay_voice_") as workdir:
            show = prepare_voiced_show(
                script, config, workdir, worker_name=args.worker_name,
                speed=args.speed, max_output_lines=args.max_output_lines,
                progress=lambda message: print(f"[replay] {message}"),
            )
            if show is None:
                print("[replay] voice disabled in config — performing silently")
            try:
                performer.perform(script, show=show, start=args.start, limit=args.limit)
            except KeyboardInterrupt:
                print("\n[replay] interrupted")
        return

    try:
        performer.perform(script, show=show, start=args.start, limit=args.limit)
    except KeyboardInterrupt:
        print("\n[replay] interrupted")


if __name__ == "__main__":
    main()
