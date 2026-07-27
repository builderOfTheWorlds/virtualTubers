#!/usr/bin/env python3
"""
replay.py
Performs a validated episode (app/episode_schema.py) as a paced, colorized
"show" on stdout -- designed to run inside a tmux pane on stream, but
equally happy in any terminal for local preview.

Display-only by design: every scene's `render[]` entries are recipes over
a handful of behaviors (app/primitives.py) -- they are RENDERED, never
executed. The only side effect outside the show's own output is the avatar
state file (agent_state.py), which the avatar pane already knows how to
read.

Persona re-voicing / narration is a separate per-airing pass (revoice.py):
it hands this module a "voiced show" -- the episode's own scenes, each
with a spoken line and its synthesized audio (tts_client.py). The
replayer performs whatever text it's given and never calls an LLM itself.
Audio anchors the timing: each scene's visual pacing is scaled so the
on-screen rendering and the spoken line finish together.

Rendering itself is delegated to app/primitives.py's perform_entry() over
a scene's render[] list -- this module owns only the show-level concerns:
pacing (Pacer), styling (Palette), audio-anchored scene timing, duet
hooks, and routing each entry's `target` to the right output sink.
"""
import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

from agent_state import write_state
from audio_player import play_wav, wait_extra
from primitives import RenderContext, estimate_scene_seconds, perform_entry

log = logging.getLogger("replay")

# ── Pacing defaults (--speed scales everything) ──────────────────────────────
# Per-behavior timing (chars/sec, lines/sec, pause seconds) used to live here
# as module constants; they now live in each campaign's own
# config/campaigns/<campaign>/primitives.yaml recipes (CONTRACT.md §2 "the
# constants MUST come out of replay.py") -- app/primitives.py estimates and
# performs them. What's left here is show-level pacing, not per-behavior
# rates.
MAX_OUTPUT_LINES = 24   # default truncation cap threaded into RenderContext
BUBBLE_CHARS = 120      # avatar speech-bubble excerpt length

# Audio-anchored scenes scale visual pacing to the spoken line's measured
# duration, clamped so a scene never crawls or blurs; outside the clamp the
# slack is absorbed by waiting (visuals done first) or by the audio simply
# ending early (visuals still going).
MIN_SCENE_SCALE = 0.4
MAX_SCENE_SCALE = 3.0


class ReplayStopped(Exception):
    """Raised by Pacer.check_stop when its should_stop hook fires mid-show —
    unwinds straight out of Performer.perform() (see perform() and
    _perform_scene), same clean shutdown as a duet follower's
    wait_for_scene returning -1."""


class Pacer:
    """All sleeps/typing rhythm go through here so tests (and --no-delay)
    can run instantly with enabled=False. `scale` is the per-scene
    audio-sync factor layered on top of the show-wide `speed`.

    `should_stop` (optional, no-arg callable returning bool) is polled on
    every sleep and every typed character — an operator-issued stop
    (docs/operator_commands.md `replay_stop`) is checked here rather than
    only between scenes so it lands within a fraction of a second even
    mid-typing, not just at the next scene boundary."""

    def __init__(self, speed=1.0, enabled=True, should_stop=None):
        self.speed = max(speed, 0.01)
        self.enabled = enabled
        self.scale = 1.0
        self.should_stop = should_stop

    @property
    def effective_speed(self):
        return max(self.speed * self.scale, 0.01)

    def check_stop(self):
        if self.should_stop is not None and self.should_stop():
            raise ReplayStopped()

    def sleep(self, seconds):
        self.check_stop()
        if self.enabled and seconds > 0:
            time.sleep(seconds / self.effective_speed)

    def type_out(self, write, text, cps):
        """Emit text through `write` at roughly cps characters/second."""
        if not self.enabled:
            write(text)
            return
        delay = 1.0 / (cps * self.effective_speed)
        for ch in text:
            self.check_stop()
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
    """Renders a validated episode's scenes in order. One instance per
    episode."""

    def __init__(self, out=None, pacer=None, palette=None, worker_name="KODI-7",
                 state_path=None, max_output_lines=MAX_OUTPUT_LINES, *,
                 on_scene_start=None, wait_for_scene=None, speaker_names=None,
                 primitives=None, sinks=None):
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
        # Multi-speaker duet display names (docs/revoice.md): kept for
        # backward compatibility with the constructor's frozen shape
        # (CONTRACT.md §6). The on-screen speaker label is now baked into a
        # scene's render[] payload by the generator (e.g. show_coder_line's
        # payload.name) rather than resolved here at render time, so
        # _display_name/_resolve_display_name below no longer drive any
        # visible output -- they're kept only so _perform_scene's existing
        # set/reset-after-scene behavior (regression-tested) still holds.
        self.speaker_names = speaker_names or {}
        self._display_name = self.worker_name
        # The merged primitive recipe table for this episode's campaign
        # (app/primitives.py::load_primitives) -- required to render any
        # scene with a non-empty render[]; a scene with render: [] never
        # touches it, so a Performer built without one can still run a
        # narration-only/no-visuals test.
        self.primitives = primitives
        # target -> writable sink (default: everything goes to `out`, i.e.
        # this single stream is the "theater" pane). See _perform_render /
        # _route_write and docs/replay.md's honest limit on unwired targets.
        self.sinks = sinks or {"theater": self.out}
        self._sink_locks = {name: threading.Lock() for name in self.sinks}
        self._warned_targets = set()   # targets already warned about (see _route_write)

    # ── low-level emit helpers ───────────────────────────────────────────────
    def _write(self, text):
        self.out.write(text)
        self.out.flush()

    def _line(self, text=""):
        self._write(text + "\n")

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

    def _resolve_display_name(self, speaker):
        """Map a scene's speaker id to a display name, mirroring
        revoice._display_name's override/fallback order. Kept for
        _perform_scene's set/reset bookkeeping (see __init__) even though
        nothing currently reads self._display_name for rendering — the
        speaker label now comes from the render[] payload itself."""
        if speaker in self.speaker_names:
            return self.speaker_names[speaker]
        if speaker is None or speaker in ("boss", "coder"):
            return self.worker_name
        return speaker

    # ── render routing (target -> sink) ──────────────────────────────────────
    def _route_write(self, text, target=None):
        """RenderContext.write: route one write() call to the sink for
        `target` (already fully resolved by primitives.perform_entry as
        entry override > recipe default > "theater" -- see
        primitives.py::perform_entry). An unresolvable target falls back
        to the theater sink and logs a WARNING rather than inventing
        cross-pane IPC (docs/replay.md's honest limit). A per-target lock
        keeps any single write() call atomic so `mode: parallel` entries on
        different threads can never tear an ANSI escape sequence in half."""
        target = target or "theater"
        sink = self.sinks.get(target)
        if sink is None:
            # Warn once per target, not once per write() -- a typed line calls
            # write() per character, which turned one unwired pane into
            # thousands of identical log lines.
            if target not in self._warned_targets:
                self._warned_targets.add(target)
                log.warning("no sink wired for render target %r; falling back to theater", target)
            sink = self.sinks.get("theater") or self.out
            target = "theater"
        lock = self._sink_locks.get(target)
        if lock is None:
            lock = self._sink_locks.setdefault("theater", threading.Lock())
        with lock:
            sink.write(text)
            sink.flush()

    # ── scene rendering (a scene = render[] + optional spoken narration) ────
    def _perform_one_entry(self, entry, ctx):
        """Execute one render[] entry, never letting it take the scene (or
        the show) down. ReplayStopped is the one exception that MUST keep
        propagating -- it's how an operator replay_stop or a duet abort
        unwinds perform() cleanly; anything else is a bug in one entry, not
        a reason to kill the whole airing (the "show must always air"
        rule), so it's logged loudly and the scene continues."""
        try:
            perform_entry(entry, self.primitives, ctx)
        except ReplayStopped:
            raise
        except Exception:
            log.error("render entry failed (primitive=%r); scene continues",
                     entry.get("primitive") if isinstance(entry, dict) else entry,
                     exc_info=True)

    def _perform_render_parallel(self, render, ctx):
        """mode: parallel -- every entry's behaviors run concurrently on
        real threads, joined before this returns, so entries targeting
        different panes (e.g. a map on "theater" and a caption on "notes")
        genuinely render at the same time.

        Pacer is safe to share across threads here: `scale`/`speed` are set
        once per scene, before this ever runs, and nothing in the
        behavior-execution path (sleep/type_out/check_stop) mutates them --
        they only read. Writes are serialized per-sink (_route_write's
        lock), so two entries can never tear a single write() call in
        half; two entries that both target the SAME sink can still
        visually interleave line-by-line -- an honest limit, not a bug,
        see docs/replay.md.

        A ReplayStopped raised on any thread is re-raised here (after every
        thread has been joined) so an operator stop still unwinds the show
        even though it fired on a background thread; any OTHER exception
        is logged and swallowed per entry, matching the sequence-mode
        behavior in _perform_one_entry."""
        outcomes = [None] * len(render)

        def run(index, entry):
            try:
                perform_entry(entry, self.primitives, ctx)
            except Exception as exc:  # captured, not swallowed, until after join()
                outcomes[index] = exc

        threads = [threading.Thread(target=run, args=(index, entry), daemon=True)
                  for index, entry in enumerate(render)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stopped = next((exc for exc in outcomes if isinstance(exc, ReplayStopped)), None)
        if stopped is not None:
            raise stopped
        for entry, exc in zip(render, outcomes):
            if exc is not None:
                log.error("render entry failed (primitive=%r); scene continues",
                         entry.get("primitive") if isinstance(entry, dict) else entry,
                         exc_info=exc)

    def _perform_render(self, scene):
        """Build a RenderContext and run scene['render'] entries.

        mode 'sequence' (the default): one after another, in order. mode
        'parallel': interleaved via real threads, joined before this
        returns (see _perform_render_parallel) -- entries targeting
        different sinks genuinely render simultaneously; entries sharing a
        sink are serialized at the write() level so output can't corrupt,
        even though their line ordering may interleave. A single entry
        failing never aborts the rest of the scene in either mode."""
        render = scene.get("render") or []
        if not render:
            return
        ctx = RenderContext(
            write=self._route_write,
            pacer=self.pacer,
            palette=self.c,
            avatar=self._avatar,
            max_output_lines=self.max_output_lines,
        )
        mode = scene.get("mode", "sequence")
        if mode == "parallel" and len(render) > 1:
            self._perform_render_parallel(render, ctx)
        else:
            for entry in render:
                self._perform_one_entry(entry, ctx)

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
        sound to anchor to.

        `natural` (the scene's own screen-time estimate the audio-sync
        scale is derived from) now comes from
        primitives.estimate_scene_seconds(scene, self.primitives, ...)
        instead of summing per-event timings — that is the only change to
        this method's math versus the pre-campaign-platform version."""
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
            natural = estimate_scene_seconds(scene, self.primitives,
                                             max_output_lines=self.max_output_lines,
                                             speed=self.pacer.speed)
            self.pacer.scale = min(MAX_SCENE_SCALE,
                                   max(MIN_SCENE_SCALE, natural / audio.duration))
            playback = play_wav(audio.audio_path)
            started = time.monotonic()
        elif not (owned and audio is not None) and target_duration is not None and target_duration > 0:
            natural = estimate_scene_seconds(scene, self.primitives,
                                             max_output_lines=self.max_output_lines,
                                             speed=self.pacer.speed)
            self.pacer.scale = min(MAX_SCENE_SCALE,
                                   max(MIN_SCENE_SCALE, natural / target_duration))
            started = time.monotonic()
            hold_seconds = target_duration
        self._display_name = self._resolve_display_name(scene.get("speaker"))
        try:
            self._perform_render(scene)
        except ReplayStopped:
            # A stop mid-scene must not leave audio playing under a show
            # that already unwound — same responsibility perform()'s
            # ReplayStopped handler can't take since `playback` is local.
            if playback is not None:
                playback.stop()
            raise
        finally:
            self.pacer.scale = 1.0
            self._display_name = self.worker_name
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
    def perform(self, episode, show=None, start=0, limit=None):
        """Perform an episode. `show` is an optional voiced show from
        revoice.prepare_show() -- the episode's own scenes annotated with
        narration + audio; without it, `episode['scenes']` perform directly
        with no narration (silent). start/limit slice the scene list
        either way -- there is no more separate "unvoiced == one scene per
        event" branch now that scenes[] is already the unit of performance
        (no events[] grouping step, docs/campaign_platform_build.md).

        Index-based (rather than a plain for-loop) so a duet follower's
        wait_for_scene hook can jump the index forward or abort mid-show
        (docs/duet_replay.md, "cue ratchet + fast-forward rule"). With
        neither on_scene_start nor wait_for_scene set this is exactly
        today's straight-through loop.

        Returns True when the show ran to its natural end, False when it
        was cut short — a duet follower's wait_for_scene returning -1, or
        an operator replay_stop firing the pacer's should_stop hook
        (ReplayStopped, docs/operator_commands.md) — so callers like
        perform_director_request can tell followers the real reason
        (docs/duet_replay.md `replay_end` "finished" vs "stopped").
        """
        c = self.c
        scenes = episode.get("scenes", []) if show is None else show
        scenes = scenes[start:]
        if limit is not None:
            scenes = scenes[:limit]
        meta = episode.get("meta") or {}
        title = meta.get("title") or meta.get("id") or episode.get("source") or "episode"
        self._line(f"{c.bold}{c.magenta}══ REPLAY: {title} "
                   f"({len(scenes)} scenes) ══{c.reset}")
        self._avatar("idle", action="getting ready for a rerun")

        n = len(scenes)
        i = 0
        try:
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
                        return False
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
        except ReplayStopped:
            self._line()
            self._line(f"{c.bold}{c.magenta}══ stopped ══{c.reset}")
            self._avatar("idle", action="show stopped by operator")
            return False

        self._line()
        self._line(f"{c.bold}{c.magenta}══ fin ══{c.reset}")
        self._avatar("happy", action="that's the end of the rerun",
                     bubble="And that's how we did it!")
        return True


def load_script(source):
    """Load and validate an episode .json via episode_schema.load_episode.

    No more directory-source branch -- app/session_log_parser.py has moved
    out to the generator side (docs/campaign_platform_build.md §9); the
    platform only ever consumes pre-built, pre-validated episode JSON from
    a campaign's own library. Imported lazily so this module stays
    importable even in a context where episode_schema's own config
    dependencies (config/validation.yaml, a campaign's primitives) aren't
    set up -- only load_script()'s callers pay that cost."""
    from episode_schema import load_episode
    return load_episode(source)


def prepare_voiced_show(episode, config, workdir, worker_name="KODI-7",
                        speed=1.0, max_output_lines=MAX_OUTPUT_LINES,
                        progress=None, campaign="coder"):
    """Glue for callers holding a worker config: build the LLM + TTS clients
    from its `llm`/`voice` sections, load this campaign's primitive recipes
    and narration config, and run revoice.prepare_show(). Returns None when
    voice is disabled (voice.provider null/missing) — meaning "perform
    silently". Imported lazily: llm_client drags in the anthropic/httpx
    deps, and this keeps every heavier import scoped to the one call site
    that needs it (matching the rest of this function's existing style).

    `campaign` selects which config/campaigns/<campaign>/{primitives,
    narration}.yaml to load (CONTRACT.md §7 campaign namespacing); callers
    that already resolved a campaign for this request (replay_pane.py) pass
    it through, the CLI (main(), below) resolves it from --campaign or the
    episode's own meta.campaign.

    `REPLAY_SKIP_LLM=1` skips building the LLM client (narrate_scene then
    always takes its no-LLM fallback path — the same one it uses when the
    LLM errors mid-show) so a test airing pays no LLM latency at all. Scoped
    to this one call site, not llm_client.build_llm_client, so it can never
    affect a worker's real coding-task LLM use."""
    from llm_client import build_llm_client
    from primitives import load_primitives
    from revoice import load_narration_config, prepare_show
    from tts_client import build_tts_client

    tts = build_tts_client(config)
    if tts is None:
        return None
    voice_config = config.get("voice") or {}
    skip_llm = os.environ.get("REPLAY_SKIP_LLM", "").lower() in ("1", "true", "yes")
    llm = None if skip_llm else build_llm_client(config)
    primitives_table = load_primitives(campaign)
    narration_config = load_narration_config(campaign)
    return prepare_show(
        episode, llm, tts, workdir,
        primitives=primitives_table, narration_config=narration_config,
        worker_name=worker_name,
        boss_name=voice_config.get("boss_name", "the boss"),
        speaker_names=voice_config.get("speaker_names") or {},
        speed=speed, max_output_lines=max_output_lines, progress=progress,
        verbatim=bool(voice_config.get("verbatim", False)),
    )


def main():
    parser = argparse.ArgumentParser(description="Perform a validated episode as a stream show")
    parser.add_argument("source", help="Episode .json, validated via app/episode_schema.py")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier")
    parser.add_argument("--no-delay", action="store_true", help="Render instantly (testing)")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    parser.add_argument("--worker-name", default="KODI-7", help="Persona name for dialogue lines")
    parser.add_argument("--state-file", default=None,
                        help="Avatar state file to drive (default: none; in-container use /tmp/agent_state.json)")
    parser.add_argument("--start", type=int, default=0, help="First scene index to perform")
    parser.add_argument("--limit", type=int, default=None, help="Max scenes to perform")
    parser.add_argument("--max-output-lines", type=int, default=MAX_OUTPUT_LINES)
    parser.add_argument("--campaign", default=None,
                        help="Campaign whose primitives/narration config to render with "
                             "(default: the episode's own meta.campaign, else 'coder')")
    parser.add_argument("--voice-config", default=None,
                        help="Worker config YAML whose voice+llm sections drive spoken "
                             "narration (docs/revoice.md); omit for a silent show")
    args = parser.parse_args()

    # Legacy Windows consoles default to cp1252, which can't encode the
    # box-drawing glyphs; the show must render anywhere, degraded not dead.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    episode = load_script(args.source)
    campaign = args.campaign or (episode.get("meta") or {}).get("campaign") or "coder"

    from primitives import load_primitives
    primitives_table = load_primitives(campaign)

    config = None
    if args.voice_config:
        import yaml
        config = yaml.safe_load(Path(args.voice_config).read_text(encoding="utf-8")) or {}

    performer = Performer(
        pacer=Pacer(speed=args.speed, enabled=not args.no_delay),
        palette=Palette(enabled=not args.no_color),
        worker_name=args.worker_name,
        state_path=args.state_file,
        max_output_lines=args.max_output_lines,
        speaker_names=(config.get("voice") or {}).get("speaker_names") or {} if config else None,
        primitives=primitives_table,
    )

    show = None
    if args.voice_config:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="replay_voice_") as workdir:
            show = prepare_voiced_show(
                episode, config, workdir, worker_name=args.worker_name,
                speed=args.speed, max_output_lines=args.max_output_lines,
                progress=lambda message: print(f"[replay] {message}"),
                campaign=campaign,
            )
            if show is None:
                print("[replay] voice disabled in config — performing silently")
            try:
                performer.perform(episode, show=show, start=args.start, limit=args.limit)
            except KeyboardInterrupt:
                print("\n[replay] interrupted")
        return

    try:
        performer.perform(episode, show=show, start=args.start, limit=args.limit)
    except KeyboardInterrupt:
        print("\n[replay] interrupted")


if __name__ == "__main__":
    main()
