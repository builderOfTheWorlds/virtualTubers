"""ASCII Avatar — entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from avatar.animation import AnimationCompositor
from avatar.bridge.paths import get_socket_path
from avatar.event_bus import AvatarEvent, EventBus
from avatar.frames.mouth_sync import MouthSync
from avatar.personas import DEFAULT_PERSONA, get_persona, list_personas
from avatar.renderer import AvatarRenderer
from avatar.state_machine import AvatarState, AvatarStateMachine
from avatar.voice.audio_player import AudioPlayer
from avatar.voice.base import TTSEngine

log = logging.getLogger(__name__)


def resolve_tts_engine(persona) -> TTSEngine | None:
    """Resolve TTS engine from persona config. Returns None if unavailable."""
    if persona.voice_engine == "kokoro":
        from avatar.voice.kokoro_engine import KokoroEngine
        engine = KokoroEngine(voice=persona.voice_id)
        if engine.is_available():
            return engine
        log.warning(
            "Kokoro model not found. Run scripts/install.sh to download. "
            "Running in animation-only mode."
        )
        return None
    elif persona.voice_engine == "elevenlabs":
        from avatar.voice.elevenlabs_engine import ElevenLabsEngine
        engine = ElevenLabsEngine(voice_id=persona.voice_id)
        if engine.is_available():
            return engine
        log.warning(
            "ELEVENLABS_API_KEY not set. Falling back to Kokoro."
        )
        # Fall back to kokoro
        from avatar.voice.kokoro_engine import KokoroEngine
        fallback = KokoroEngine(voice=persona.voice_id)
        return fallback if fallback.is_available() else None
    elif persona.voice_engine == "piper":
        from avatar.voice.piper_engine import PiperEngine
        engine = PiperEngine()
        return engine if engine.is_available() else None
    return None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ASCII Avatar for Claude Code")
    parser.add_argument(
        "--persona", default=DEFAULT_PERSONA,
        choices=list_personas(),
        help=f"Persona preset (default: {DEFAULT_PERSONA})",
    )
    parser.add_argument(
        "--socket", default=get_socket_path(),
        help="Unix socket path for event bus",
    )
    parser.add_argument("--no-voice", action="store_true", help="Disable TTS")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    parser.add_argument(
        "--voice", default=None,
        help="Override persona voice ID",
    )
    parser.add_argument(
        "--audio-device", default=None, type=int,
        help="Override audio output device index",
    )
    parser.add_argument("--compact", action="store_true", help="Compact mode")
    parser.add_argument("--no-boot", action="store_true", help="Skip boot animation")
    parser.add_argument(
        "--portrait", default=None,
        help="Path to portrait image for avatar (overrides persona frame set)",
    )
    parser.add_argument(
        "--charset", default=None,
        choices=["auto", "density", "halfblock", "halfblock_rgb", "braille", "braille_rgb", "sixel"],
        help="Rendering charset (default: auto). Fidelity: sixel > braille > halfblock_rgb > halfblock > density. 'auto' picks the best your terminal supports.",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Headless mode: run event bus and state machine without terminal rendering (for testing)",
    )
    parser.add_argument(
        "--agent", action="store_true",
        help="Agent mode: use Haiku to intelligently control avatar state and speech",
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    persona = get_persona(args.persona)
    if args.voice:
        # Override voice from persona
        from avatar.personas import Persona
        persona = Persona(
            name=persona.name, frames=persona.frames,
            voice_engine=persona.voice_engine, voice_id=args.voice,
            accent_color=persona.accent_color, personality=persona.personality,
            frame_rate_modifier=persona.frame_rate_modifier,
        )

    # Audio device override
    if args.audio_device is not None:
        import sounddevice as sd
        sd.default.device = args.audio_device

    # TTS engine
    tts: TTSEngine | None = None
    if not args.no_voice:
        tts = resolve_tts_engine(persona)

    audio_player = AudioPlayer()
    mouth_sync = MouthSync()

    # State machine
    sm = AvatarStateMachine(idle_timeout=30)

    # Shutdown handler
    running = True

    def shutdown(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Event bus OR agent mode (mutually exclusive — both own the ZeroMQ socket)
    bus: EventBus | None = None
    agent = None
    last_event = ""

    if args.agent:
        # Agent mode: AgentLoop owns the ZeroMQ socket and drives state + speech
        from avatar.agent import AgentLoop

        agent = AgentLoop(socket_path=args.socket)

        def on_state_change(state_str: str) -> None:
            try:
                new_state = AvatarState(state_str)
                sm.transition(new_state)
            except ValueError:
                log.warning("Agent returned unknown state: %s", state_str)

        def on_speak(text: str) -> None:
            sm.transition(AvatarState.SPEAKING)
            if tts and text:
                try:
                    audio, timings = tts.synthesize(text)
                    audio_player.play(
                        audio,
                        sample_rate=tts.sample_rate,
                        word_timings=timings,
                        on_word=mouth_sync.on_word,
                        on_complete=lambda: (
                            mouth_sync.reset(),
                            sm.transition(AvatarState.IDLE),
                        ),
                    )
                except Exception as e:
                    log.error("TTS failed: %s", e)

        agent.on_state_change = on_state_change
        agent.on_speak = on_speak

        log.info("Avatar started (agent mode). Persona: %s. Socket: %s", persona.name, args.socket)
        log.info("TTS: %s", "enabled" if tts else "disabled (animation only)")

    else:
        # Standard mode: EventBus receives events from external hooks
        bus = EventBus(socket_path=args.socket)

        def handle_event(event: AvatarEvent) -> None:
            nonlocal last_event
            if event.event == "heartbeat":
                last_event = "heartbeat"
                return
            last_event = event.event
            if event.event == "state_change":
                try:
                    new_state = AvatarState(event.state)
                    sm.transition(new_state)
                except ValueError:
                    log.warning("Unknown state: %s", event.state)
            elif event.event == "speak_start":
                sm.transition(AvatarState.SPEAKING)
                if tts and event.text:
                    try:
                        audio, timings = tts.synthesize(event.text)
                        audio_player.play(
                            audio,
                            sample_rate=tts.sample_rate,
                            word_timings=timings,
                            on_word=mouth_sync.on_word,
                            on_complete=lambda: (
                                mouth_sync.reset(),
                                sm.transition(AvatarState.IDLE),
                            ),
                        )
                    except Exception as e:
                        log.error("TTS failed: %s", e)
            elif event.event == "speak_end":
                sm.transition(AvatarState.IDLE)

        bus.on_event = handle_event

        def _send_startup_ping(socket_path: str) -> None:
            """Send a heartbeat ping after a short delay to confirm the socket is ready."""
            import zmq as _zmq
            time.sleep(0.5)
            try:
                ctx = _zmq.Context()
                sock = ctx.socket(_zmq.PUSH)
                sock.connect(f"ipc://{socket_path}")
                sock.send_json({"event": "heartbeat"})
                sock.close()
                ctx.term()
            except Exception as e:
                log.debug("Startup ping failed: %s", e)

        bus.start()
        threading.Thread(
            target=_send_startup_ping,
            args=(args.socket,),
            daemon=True,
        ).start()
        log.info("Avatar started. Persona: %s. Socket: %s", persona.name, args.socket)
        log.info("TTS: %s", "enabled" if tts else "disabled (animation only)")

    if args.headless:
        # Headless mode: no terminal rendering
        log.info("Running in headless mode (no terminal rendering).")
        try:
            if agent:
                # Agent mode + headless: run agent loop on main thread (blocking)
                agent.run()
            else:
                while running:
                    time.sleep(0.1)
        finally:
            audio_player.stop()
            sm.shutdown()
            if agent:
                agent.stop()
            if bus:
                bus.stop()
            log.info("Avatar stopped.")
        return

    # If agent mode with rendering, start agent loop in a background thread
    if agent:
        agent_thread = threading.Thread(target=agent.run, daemon=True)
        agent_thread.start()

    # Renderer (only needed for interactive mode)
    import blessed
    term = blessed.Terminal()
    if args.no_color:
        term.number_of_colors = 2
    # Determine frame set: --portrait overrides persona frames
    frame_set = persona.frames
    if args.portrait:
        frame_set = f"portrait:{args.portrait}"
    elif persona.frames == "portrait":
        frame_set = "portrait"

    renderer = AvatarRenderer(
        terminal=term,
        frame_set=frame_set,
        frame_rate_modifier=persona.frame_rate_modifier,
        charset=args.charset,
    )

    # Create the animation compositor for micro-event overlays
    compositor = AnimationCompositor(renderer._frames, renderer._rates)
    log.info(
        "Animation compositor: %d overlay types available",
        len(compositor._overlay_counts),
    )

    frame_index = 0

    try:
        with term.fullscreen(), term.hidden_cursor():
            # Boot animation — plays once unless suppressed
            if not args.no_boot:
                from avatar.frames.boot import BOOT_FRAMES, BOOT_FRAME_RATE
                for boot_frame in BOOT_FRAMES:
                    if not running:
                        break
                    renderer.render_frame(boot_frame, " BOOTING... ")
                    time.sleep(BOOT_FRAME_RATE)

            while running:
                state = sm.state
                state_val = state.value

                # Mouth override: only use mouth_sync when TTS is actively
                # playing audio. Otherwise let frame_index cycle naturally
                # so speaking animation works without voice.
                mouth_override = None
                if tts and state == AvatarState.SPEAKING:
                    mouth_override = mouth_sync.current_frame

                # Use compositor for frame selection — handles micro-events
                frame = compositor.get_frame(
                    state_val, frame_index,
                    mouth_frame_override=mouth_override,
                )

                # Fall back to renderer if compositor returns empty
                if not frame:
                    frame = renderer.get_current_frame(
                        state, frame_index,
                        mouth_frame_override=mouth_override,
                    )

                status = renderer.format_status_bar(
                    state=state,
                    connected=bus.connected if bus else True,
                    tts_loaded=tts is not None,
                    last_event=last_event if bus else "agent",
                    time_since_last_event=bus.time_since_last_event if bus else 0,
                )
                renderer.render_frame(frame, status)

                # Use compositor's rate (faster during micro-events)
                rate = compositor.get_frame_rate(state_val) * persona.frame_rate_modifier
                time.sleep(rate)
                frame_index = renderer.next_frame_index(state, frame_index)

                # Check for quit key
                key = term.inkey(timeout=0)
                if key == "q" or key.name == "KEY_ESCAPE":
                    break
    finally:
        audio_player.stop()
        sm.shutdown()
        if agent:
            agent.stop()
        if bus:
            bus.stop()
        log.info("Avatar stopped.")


if __name__ == "__main__":
    main()
