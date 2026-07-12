"""
revoice.py
Per-airing narration pass for Rerun Theater: turns a parsed episode script
(session_log_parser.py) into a *voiced show* — the script's events grouped
into scenes, each with a short spoken line (boss or coder voice) and its
synthesized audio.

Runs at showtime, per airing (never baked into the episode library), so
every re-run of the same episode gets fresh dialogue from the local LLM.
Tool_call events are never altered — narration is ADDITIVE; the on-screen
commands/edits/outputs stay exactly what the parser recorded
(docs/session_log_parser.md's re-voicing contract).

Timing model (docs/replay.md):
1. Estimate how long each scene takes to render on screen at base pacing.
2. Ask the LLM for a spoken line of roughly the matching word count — a
   scene with minutes of scrolling output gets enough narration to fill it.
3. Synthesize the line and MEASURE the real audio duration; the performer
   then scales the scene's visual pacing so text and speech finish together
   (audio anchors, visuals adapt).

Every step degrades gracefully: LLM unreachable -> template narration built
from the (already-redacted) script text; TTS failure -> the scene simply
plays silent at normal pacing. A show must never fail to air.
"""
from pathlib import Path

from replay import estimate_event_seconds

WORDS_PER_SECOND = 2.5   # ~150 wpm — typical conversational TTS rate
MIN_WORDS = 8            # even a 1-second scene gets a real sentence
MAX_WORDS = 130          # cap one scene's monologue (~50s of speech)
MAX_SCENE_CONTEXT = 1800  # chars of scene material shown to the LLM
MAX_SCENE_EVENTS = 8     # split marathon tool runs into multiple scenes

SYSTEM_PROMPT = (
    "You write single spoken lines for a VTuber stream where AI personas "
    "re-enact a real software development session. Reply with ONLY the "
    "spoken line - no quotes, no stage directions, no markdown. Keep it "
    "natural, casual, and in character. Never invent file names, commands, "
    "or results that are not in the material given."
)


# ── Scene planning ────────────────────────────────────────────────────────────

def plan_scenes(events):
    """Group a script's events into scenes, each owned by one speaker.

    boss        — one user_message (the boss talking to the coder)
    coder_talk  — one assistant_text (the coder addressing the stream)
    coder_work  — a run of consecutive tool_calls (the coder doing things),
                  capped at MAX_SCENE_EVENTS so one spoken line never has to
                  cover an unbounded stretch of screen time.
    """
    scenes = []
    work = []

    def flush_work():
        nonlocal work
        for start in range(0, len(work), MAX_SCENE_EVENTS):
            chunk = work[start:start + MAX_SCENE_EVENTS]
            scenes.append({"kind": "coder_work", "speaker": "coder", "events": chunk})
        work = []

    for event in events:
        kind = event.get("type")
        if kind == "tool_call":
            work.append(event)
            continue
        flush_work()
        if kind == "user_message":
            scenes.append({"kind": "boss", "speaker": "boss", "events": [event]})
        elif kind == "assistant_text":
            scenes.append({"kind": "coder_talk", "speaker": "coder", "events": [event]})
        # unknown event types: the performer skips them, so we drop them here
        # too rather than desync scene timing estimates
    flush_work()
    return scenes


def scene_visual_seconds(scene, max_output_lines, speed=1.0):
    """Wall-clock seconds this scene takes to render at the given speed."""
    total = sum(estimate_event_seconds(e, max_output_lines) for e in scene["events"])
    return total / max(speed, 0.01)


def target_words(seconds):
    """Word budget for a spoken line meant to fill `seconds` of screen time."""
    return max(MIN_WORDS, min(MAX_WORDS, int(seconds * WORDS_PER_SECOND)))


# ── Narration text ────────────────────────────────────────────────────────────

def _trim_words(text, max_words):
    words = " ".join(text.split()).split(" ")
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]) + "…"


def _scene_material(scene):
    """Compact, truncated rendering of a scene's events for the LLM prompt.
    The script is already redacted upstream, so this is broadcast-safe."""
    parts = []
    for event in scene["events"]:
        kind = event.get("type")
        if kind in ("user_message", "assistant_text"):
            parts.append(event.get("text", ""))
            continue
        tool = event.get("tool", "?")
        detail = event.get("detail") or {}
        if tool in ("Bash", "PowerShell"):
            parts.append(f"ran: {detail.get('command', event.get('input_summary', ''))}")
            output = detail.get("output")
            if output:
                parts.append(f"output: {output[:400]}")
        elif tool == "Edit":
            parts.append(f"edited {detail.get('file', '?')}")
        elif tool == "Write":
            parts.append(f"wrote {detail.get('file', '?')}")
        elif tool == "Read":
            parts.append(f"read {detail.get('file', '?')}")
        else:
            parts.append(f"{tool}: {event.get('input_summary', '')[:120]}")
        if event.get("error"):
            parts.append("(that one FAILED)")
    return "\n".join(parts)[:MAX_SCENE_CONTEXT]


_PROMPTS = {
    "boss": (
        "You are voicing {boss_name}, the boss, sending the dev a request. "
        "Re-voice this message as ONE natural spoken line of about {words} "
        "words, keeping every concrete requirement intact:\n\n{material}"
    ),
    "coder_talk": (
        "You are voicing {worker_name}, an AI coder live-streaming their "
        "work. Re-voice this narration in your own words, about {words} "
        "words, keeping the technical content accurate:\n\n{material}"
    ),
    "coder_work": (
        "You are voicing {worker_name}, an AI coder live-streaming their "
        "work. Describe out loud, present tense, what you are doing in these "
        "recorded actions - about {words} words, enough to talk over the "
        "whole sequence:\n\n{material}"
    ),
}


def fallback_narration(scene, max_words):
    """Narration built without an LLM, straight from the redacted script."""
    kind = scene["kind"]
    if kind == "boss":
        return _trim_words(scene["events"][0].get("text", "New instructions."), max_words)
    if kind == "coder_talk":
        return _trim_words(scene["events"][0].get("text", "Let me think."), max_words)
    actions = []
    for event in scene["events"]:
        tool = event.get("tool", "?")
        detail = event.get("detail") or {}
        if tool in ("Bash", "PowerShell"):
            command = (detail.get("command") or event.get("input_summary") or "a command")
            actions.append(f"running {command.splitlines()[0][:60]}")
        elif tool in ("Edit", "Write"):
            target = detail.get("file") or "a file"
            actions.append(f"{'editing' if tool == 'Edit' else 'writing'} {Path(target).name}")
        elif tool == "Read":
            actions.append(f"checking {Path(detail.get('file') or 'a file').name}")
        else:
            actions.append(f"using {tool}")
    line = "Okay — " + ", then ".join(actions[:4]) + "."
    return _trim_words(line, max_words)


def narrate_scene(scene, llm, words, worker_name, boss_name):
    """One spoken line for the scene: LLM-voiced, falling back to the
    template line if the LLM is unreachable or returns nothing usable."""
    prompt = _PROMPTS[scene["kind"]].format(
        worker_name=worker_name, boss_name=boss_name, words=words,
        material=_scene_material(scene),
    )
    if llm is not None:
        try:
            line = (llm.complete(SYSTEM_PROMPT, [{"role": "user", "content": prompt}]) or "").strip()
            if line:
                # A hard cap only — the word budget is a suggestion to the
                # LLM; the performer syncs to whatever duration comes back.
                return _trim_words(line, MAX_WORDS * 2)
        except Exception:
            pass  # LLM down mid-show: the fallback keeps the show airing
    return fallback_narration(scene, words)


# ── Show preparation (the per-airing pass) ────────────────────────────────────

def prepare_show(script, llm, tts, workdir, worker_name="KODI-7",
                 boss_name="the boss", speed=1.0, max_output_lines=24,
                 progress=None):
    """Build the voiced show for one airing.

    Returns plan_scenes()' scenes, each annotated with:
        narration — the spoken line (always present)
        audio     — tts_client.Narration (path + measured duration), or None
                    when TTS is disabled/failed (scene plays silent).

    `progress(message)` is called per scene so the theater pane can show
    "preparing tonight's episode…" while the LLM and TTS work.
    """
    notify = progress or (lambda message: None)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    scenes = plan_scenes(script.get("events", []))
    for index, scene in enumerate(scenes):
        seconds = scene_visual_seconds(scene, max_output_lines, speed)
        words = target_words(seconds)
        notify(f"scene {index + 1}/{len(scenes)}: writing {scene['kind']} line (~{words}w)")
        scene["narration"] = narrate_scene(scene, llm, words, worker_name, boss_name)
        scene["audio"] = None
        if tts is None:
            continue
        try:
            scene["audio"] = tts.synthesize(
                scene["narration"], workdir / f"scene_{index:03d}.wav",
                speaker=scene["speaker"],
            )
        except Exception as exc:
            notify(f"scene {index + 1}: TTS failed ({exc}) — playing silent")
    return scenes
