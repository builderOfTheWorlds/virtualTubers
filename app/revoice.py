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

Per-role tone (OB-33, ashiorid_office): `role_tones` maps a speaker id
(`tuber_N`) or an office role (`marketing`, `tester`, ...) to a short style
note that is appended to that scene's LLM prompt. `load_role_tones(cast_dir)`
builds the map from a pack's cast files. Omitted or empty, every prompt is
byte-for-byte what it was before.
"""
import logging
from pathlib import Path

from replay import estimate_event_seconds

log = logging.getLogger(__name__)

WORDS_PER_SECOND = 2.5   # ~150 wpm — typical conversational TTS rate
MIN_WORDS = 8            # even a 1-second scene gets a real sentence
MAX_WORDS = 130          # cap one scene's monologue (~50s of speech)
MAX_SCENE_CONTEXT = 1800  # chars of scene material shown to the LLM
MAX_SCENE_EVENTS = 8     # split marathon tool runs into multiple scenes
MAX_TONE_CHARS = 400     # one style note, not a second system prompt

#: Fallback style notes per office role, used by load_role_tones() only for a
#: cast file that has neither `tone:` nor `speech:`. Never applied implicitly.
OFFICE_ROLE_TONES = {
    "ceo": "calm, big-picture, frames everything as a client and a deadline",
    "tech_lead": "dry, exact, short sentences, names the next step",
    "analyst": "careful and precise, restates what done means",
    "engineer": "friendly and practical, thinks out loud with an analogy",
    "tester": "terse, numbers first, no adjectives",
    "marketing": "upbeat and smooth, turns it into a tagline or a customer picture",
    "office_manager": "warm but brisk, tidies up as she talks",
}

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
            speaker = chunk[0].get("speaker") or "coder"
            scenes.append({"kind": "coder_work", "speaker": speaker, "events": chunk})
        work = []

    for event in events:
        kind = event.get("type")
        if kind == "tool_call":
            speaker = event.get("speaker") or "coder"
            if work and (work[0].get("speaker") or "coder") != speaker:
                flush_work()
            work.append(event)
            continue
        flush_work()
        if kind == "user_message":
            scenes.append({"kind": "boss", "speaker": event.get("speaker") or "boss", "events": [event]})
        elif kind == "assistant_text":
            scenes.append({"kind": "coder_talk", "speaker": event.get("speaker") or "coder", "events": [event]})
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
        "You are voicing {name}, the boss, sending the dev a request. "
        "Re-voice this message as ONE natural spoken line of about {words} "
        "words, keeping every concrete requirement intact:\n\n{material}"
    ),
    "coder_talk": (
        "You are voicing {name}, live-streaming their work. Re-voice this "
        "narration in your own words, about {words} words, keeping the "
        "technical content accurate:\n\n{material}"
    ),
    "coder_work": (
        "You are voicing {name}, live-streaming their work. Describe out "
        "loud, present tense, what you are doing in these recorded actions "
        "- about {words} words, enough to talk over the whole "
        "sequence:\n\n{material}"
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


def show_bindings(script):
    """Extract the show header's per-slot bindings ONCE, for every caller.

    Returns (names_by_slot, voices_by_slot):
        names_by_slot  — slot id -> persona DISPLAY name (§7.1, §14 risk row 2:
                         `show.persona.<slot>.name` is authoritative when
                         present, so a show's cast can never be mislabeled by
                         a stale worker config).
        voices_by_slot — slot id -> SYMBOLIC registry voice name (§7.2), fed
                         straight to TTSClient.synthesize(voice_name=…).

    Deliberately tolerant: a missing, non-dict or persona-less `show` block
    yields two empty dicts, which restores today's behaviour exactly (§7.4) —
    recorded-session replays have no header at all. A malformed header must
    never be the reason a show fails to air, so nothing here raises; entries
    that aren't usable are simply skipped.
    """
    show = (script or {}).get("show") if isinstance(script, dict) else None
    if not isinstance(show, dict):
        return {}, {}
    persona = show.get("persona")
    if not isinstance(persona, dict):
        return {}, {}

    names, voices = {}, {}
    for slot, binding in persona.items():
        if not isinstance(binding, dict):
            continue  # a scalar persona entry is malformed — ignore, don't die
        name = binding.get("name")
        if name:
            names[str(slot)] = str(name)
        voice = binding.get("voice")
        if voice:
            # A symbolic NAME only. A header carrying model_path/voice_id is
            # rejected upstream at upload (§8.1); here we just never look for
            # one, so platform detail can't sneak in through story data.
            voices[str(slot)] = str(voice)
    return names, voices


def _display_name(speaker, speaker_names, worker_name, boss_name):
    """Resolve a scene's speaker id to the name it's voiced/labeled under:
    an explicit `speaker_names` override, then the two backward-compat
    defaults (`boss_name` for "boss", `worker_name` for "coder"), then the
    raw speaker id as a last resort."""
    speaker_names = speaker_names or {}
    if speaker in speaker_names:
        return speaker_names[speaker]
    if speaker == "boss":
        return boss_name
    if speaker == "coder":
        return worker_name
    return speaker


def scene_tone(scene, role_tones):
    """The style note for a scene: by its speaker id first, then by the
    office `role` its first event carries (role_attribution episodes stamp
    one). None when there is no map or no match."""
    if not role_tones:
        return None
    tone = role_tones.get(scene.get("speaker"))
    if not tone:
        events = scene.get("events") or [{}]
        role = events[0].get("role") if isinstance(events[0], dict) else None
        tone = role_tones.get(role) if role else None
    return " ".join(str(tone).split())[:MAX_TONE_CHARS] if tone else None


def load_role_tones(cast_dir):
    """Build a `role_tones` map from a pack's `cast/*.yaml`.

    Each cast file contributes its `tone:` (else `speech:`, else the
    OFFICE_ROLE_TONES entry for its `office_role`) under both its `seat`
    (tuber_N) and its `office_role`. A missing directory or an unreadable
    file is skipped with a warning — a show must never fail to air."""
    log.debug("load_role_tones enter cast_dir=%s", cast_dir)
    root = Path(cast_dir)
    if not root.is_dir():
        log.warning("revoice.tones_dir_missing dir=%s", root)
        return {}
    import yaml  # lazy: only callers that opt in need it

    tones = {}
    for path in sorted(root.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.warning("revoice.tones_unreadable file=%s error=%s", path.name, type(exc).__name__)
            continue
        if not isinstance(data, dict):
            continue
        role = data.get("office_role")
        tone = data.get("tone") or data.get("speech") or OFFICE_ROLE_TONES.get(role)
        if not tone:
            continue
        tone = " ".join(str(tone).split())[:MAX_TONE_CHARS]
        for key in (data.get("seat"), role):
            if key:
                tones[str(key)] = tone
    log.debug("load_role_tones exit entries=%d", len(tones))
    return tones


def narrate_scene(scene, llm, words, worker_name, boss_name, speaker_names=None,
                   verbatim=False, tone=None):
    """One spoken line for the scene: LLM-voiced, falling back to the
    template line if the LLM is unreachable or returns nothing usable.

    `verbatim=True` skips paraphrasing entirely for `boss`/`coder_talk`
    scenes — the original scripted line is spoken in full, untrimmed,
    with no LLM call at all. `coder_work` scenes have no single original
    line to read (they describe a run of tool calls), so they always go
    through the normal LLM/fallback path regardless of `verbatim`.

    `tone` (see scene_tone) is appended to the LLM prompt as a style note;
    None leaves the prompt unchanged. The fallback line ignores it."""
    if verbatim and scene["kind"] in ("boss", "coder_talk"):
        default = "New instructions." if scene["kind"] == "boss" else "Let me think."
        return " ".join(scene["events"][0].get("text", default).split())
    name = _display_name(scene["speaker"], speaker_names, worker_name, boss_name)
    prompt = _PROMPTS[scene["kind"]].format(
        name=name, words=words,
        material=_scene_material(scene),
    )
    if tone:
        prompt += f"\n\nSpeak in this style: {tone}"
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
                 progress=None, speaker_names=None, verbatim=False,
                 voice_names=None, role_tones=None, checkpoint=None):
    """Build the voiced show for one airing.

    Returns plan_scenes()' scenes, each annotated with:
        narration — the spoken line (always present)
        audio     — tts_client.Narration (path + measured duration), or None
                    when TTS is disabled/failed (scene plays silent).

    `progress(message)` is called per scene so the theater pane can show
    "preparing tonight's episode…" while the LLM and TTS work. `speaker_names`
    maps speaker id -> display name for any per-event speaker overrides
    (see plan_scenes); it falls back to worker_name/boss_name/raw id.
    `verbatim=True` (from a worker's `voice.verbatim` config) reads
    boss/coder dialogue lines in full instead of paraphrasing them to fit
    the estimated screen time — see `narrate_scene`. The audio-anchored
    pacing in replay.py adapts either way, so a longer verbatim line just
    holds the scene a little longer rather than desyncing.

    The script's optional `show` header (§7.1) is read here through
    show_bindings():
      * persona DISPLAY names layer OVER `speaker_names` — the show casting a
        slot is authoritative over worker config (§14, risk row 2);
      * per-slot SYMBOLIC voice names (§7.2) are passed to synthesize(), which
        resolves them through the registry.
    `voice_names` lets a caller supply/override that slot -> voice-name map
    directly (e.g. a tile that already parsed the header); it wins over the
    header so an explicit argument is never silently ignored. Both are
    optional, and an episode with no header behaves exactly as before (§7.4).
    `role_tones` (speaker id or office role -> style note, see
    load_role_tones) adds a per-role tone to each scene's LLM prompt; None
    keeps every prompt unchanged.

    `checkpoint` (a voice_prep_checkpoint.PrepCheckpoint, optional) makes
    the pass resumable: a scene whose content hash is already checkpointed
    is restored (text + WAV) instead of re-narrated/re-synthesized, and
    every freshly finished scene is checkpointed as soon as it's done. The
    progress line format is unchanged ("scene i/N: ...") so the control
    panel's progress bar keeps parsing it; restored scenes say "restored".
    """
    notify = progress or (lambda message: None)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    header_names, header_voices = show_bindings(script)
    # Config first, header on top: the persona name wins where both exist.
    speaker_names = {**(speaker_names or {}), **header_names}
    voice_names = {**header_voices, **(voice_names or {})}
    scenes = plan_scenes(script.get("events", []))
    for index, scene in enumerate(scenes):
        seconds = scene_visual_seconds(scene, max_output_lines, speed)
        words = target_words(seconds)
        tone = scene_tone(scene, role_tones)
        voice_name = voice_names.get(scene["speaker"])
        scene_hash = None
        wav_path = workdir / f"scene_{index:03d}.wav"
        if checkpoint is not None:
            from voice_prep_checkpoint import compute_scene_hash

            scene_hash = compute_scene_hash(scene, words=words, tone=tone,
                                            voice_name=voice_name)
            restored = checkpoint.lookup(index, scene_hash, wav_path)
            # A checkpointed silent scene is only trusted when TTS is off
            # too — otherwise it was a TTS failure worth retrying.
            if restored is not None and (restored[1] is not None or tts is None):
                scene["narration"], scene["audio"] = restored
                notify(f"scene {index + 1}/{len(scenes)}: restored {scene['kind']} line "
                       f"from checkpoint")
                continue
        notify(f"scene {index + 1}/{len(scenes)}: writing {scene['kind']} line (~{words}w)")
        scene["narration"] = narrate_scene(scene, llm, words, worker_name, boss_name,
                                            speaker_names=speaker_names, verbatim=verbatim,
                                            tone=tone)
        scene["audio"] = None
        if tts is not None:
            try:
                # The kwarg is only passed when this slot is actually cast by a
                # header. Uncast slots take the exact pre-v1.1 call — which also
                # keeps every duck-typed `tts` (test fakes, thin wrappers) that
                # predates `voice_name` working untouched (§7.4).
                extra = {}
                if voice_name:
                    extra["voice_name"] = voice_name
                scene["audio"] = tts.synthesize(
                    scene["narration"], wav_path,
                    speaker=scene["speaker"], **extra,
                )
            except Exception as exc:
                notify(f"scene {index + 1}: TTS failed ({exc}) — playing silent")
        if checkpoint is not None:
            checkpoint.record(index, scene_hash, scene["narration"], scene["audio"])
    return scenes
