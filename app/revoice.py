"""
revoice.py
Per-airing narration pass for Rerun Theater: turns a validated episode
(app/episode_schema.py) into a *voiced show* -- the episode's own scenes,
each given a short spoken line and its synthesized audio.

Runs at showtime, per airing (never baked into the episode library), so
every re-run of the same episode gets fresh dialogue from the local LLM.
A scene's `render[]` entries are never altered -- narration is ADDITIVE;
the on-screen primitives stay exactly what the episode scripted
(docs/campaign_platform_build.md's re-voicing contract).

Timing model (docs/replay.md):
1. Estimate how long each scene takes to render on screen at base pacing
   (app/primitives.py::estimate_scene_seconds, over the scene's own
   render[] recipes). There is no event-grouping step here any more --
   a campaign generator already emits scene-sized units, one spoken line
   per scene (docs/campaign_platform_build.md "What this deletes").
2. Ask the LLM for a spoken line of roughly the matching word count -- a
   scene with a long visual sequence gets enough narration to fill it.
3. Synthesize the line and MEASURE the real audio duration; the performer
   then scales the scene's visual pacing so text and speech finish together
   (audio anchors, visuals adapt).

Every step degrades gracefully: LLM unreachable -> the scene's own
`fallback` text, or a narration.yaml fallback_template, or a last-resort
line built from render_summary(); TTS failure -> the scene simply plays
silent at normal pacing. A show must never fail to air.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from primitives import estimate_scene_seconds, render_summary

log = logging.getLogger("revoice")

WORDS_PER_SECOND = 2.5   # ~150 wpm -- typical conversational TTS rate
MIN_WORDS = 8            # even a 1-second scene gets a real sentence
MAX_WORDS = 130          # cap one scene's monologue (~50s of speech)

SYSTEM_PROMPT = (
    "You write single spoken lines for a VTuber stream where AI personas "
    "re-enact a scripted campaign. Reply with ONLY the spoken line - no "
    "quotes, no stage directions, no markdown. Keep it natural, casual, "
    "and in character. Never invent details that are not in the material "
    "given."
)


# -- Narration config loading --------------------------------------------------

def _default_campaigns_dir():
    in_container = Path("/config/campaigns")
    if in_container.is_dir():
        return in_container
    return Path("config") / "campaigns"


def load_narration_config(campaign, *, campaigns_dir=None):
    """config/campaigns/<campaign>/narration.yaml -> {kind: {prompt,
    fallback_template}}.

    Missing file (or any load failure) degrades to an empty config rather
    than raising -- fallback_line()'s own scene['fallback'] and its
    generic last resort keep a show airing even with no narration config
    at all, the same soft-degradation contract as an LLM/TTS outage."""
    campaigns_dir = Path(campaigns_dir) if campaigns_dir is not None else _default_campaigns_dir()
    path = Path(campaigns_dir) / campaign / "narration.yaml"
    if not path.is_file():
        log.debug("no narration config at %s for campaign %r; scenes fall back to "
                 "their own 'fallback' text", path, campaign)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as exc:
        log.error("failed to read narration config %s: %s", path, exc)
        return {}
    except yaml.YAMLError as exc:
        log.error("failed to parse narration config %s: %s", path, exc)
        return {}
    return data or {}


def scene_visual_seconds(scene, primitives, *, max_output_lines=24, speed=1.0):
    """Thin delegate to primitives.estimate_scene_seconds -- kept as the
    public name because replay_pane.py and the existing tests already use
    it. All the per-`kind` timing branching that used to live here is gone;
    screen time is now entirely a function of the scene's own render[]
    recipes (see docs/campaign_platform_build.md "What this deletes")."""
    return estimate_scene_seconds(scene, primitives, max_output_lines=max_output_lines, speed=speed)


def target_words(seconds):
    """Word budget for a spoken line meant to fill `seconds` of screen time."""
    return max(MIN_WORDS, min(MAX_WORDS, int(seconds * WORDS_PER_SECOND)))


# -- Narration text -------------------------------------------------------------

def _trim_words(text, max_words):
    words = " ".join(text.split()).split(" ")
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]) + "…"


@dataclass
class SpeakerNames:
    """Bundles the three inputs _display_name resolves a speaker id
    against (CONTRACT.md §5: "`names` bundles worker_name/boss_name/
    speaker_names"): an explicit per-speaker override wins, then the
    boss/coder legacy defaults, then the raw speaker id."""
    worker_name: str = "KODI-7"
    boss_name: str = "the boss"
    speaker_names: dict = field(default_factory=dict)


def _display_name(speaker, names):
    """Resolve a scene's speaker id to the name it's voiced/labeled under:
    an explicit `names.speaker_names` override, then the two backward-
    compat defaults (`names.boss_name` for "boss", `names.worker_name` for
    "coder"), then the raw speaker id as a last resort."""
    speaker_names = names.speaker_names or {}
    if speaker in speaker_names:
        return speaker_names[speaker]
    if speaker == "boss":
        return names.boss_name
    if speaker == "coder":
        return names.worker_name
    return speaker


def fallback_line(scene, narration_config, primitives, max_words, names):
    """Narration built without an LLM -- the last line of defense, so this
    must always produce something speakable and must never raise.

    Priority: the scene's own `fallback` text, then narration_config's
    `fallback_template` for this scene's `kind` (formatted with
    {name}/{render_summary}/{text}), then a generic last resort built from
    render_summary()."""
    fallback = (scene.get("fallback") or "").strip()
    if fallback:
        return _trim_words(fallback, max_words)

    kind_cfg = narration_config.get(scene.get("kind")) or {}
    template = kind_cfg.get("fallback_template")
    if template:
        name = _display_name(scene.get("speaker"), names)
        try:
            line = template.format(
                name=name,
                render_summary=render_summary(scene, primitives),
                text=scene.get("text", ""),
            )
        except Exception:
            log.debug("fallback_template formatting failed for kind %r; using raw template",
                     scene.get("kind"), exc_info=True)
            line = template
        line = line.strip()
        if line:
            return _trim_words(line, max_words)

    summary = render_summary(scene, primitives)
    return _trim_words(summary or "Let's keep going.", max_words)


def narrate_scene(scene, llm, words, names, narration_config, primitives, verbatim=False):
    """One spoken line for the scene: LLM-voiced, falling back to
    fallback_line() if the LLM is unreachable, unconfigured, has no prompt
    for this scene's `kind`, or returns nothing usable.

    `verbatim=True` speaks scene['text'] in full with no LLM call whenever
    `text` is non-empty -- this replaces today's `kind in ("boss",
    "coder_talk")` check now that `kind` is campaign-defined rather than a
    fixed set (CONTRACT.md §5)."""
    text = scene.get("text") or ""
    if verbatim and text.strip():
        return " ".join(text.split())

    kind_cfg = narration_config.get(scene.get("kind")) or {}
    prompt_template = kind_cfg.get("prompt")
    if prompt_template and llm is not None:
        name = _display_name(scene.get("speaker"), names)
        material = text if text.strip() else render_summary(scene, primitives)
        prompt = prompt_template.format(
            name=name, words=words, text=text, material=material,
            render_summary=render_summary(scene, primitives),
        )
        try:
            line = (llm.complete(SYSTEM_PROMPT, [{"role": "user", "content": prompt}]) or "").strip()
            if line:
                # A hard cap only -- the word budget is a suggestion to the
                # LLM; the performer syncs to whatever duration comes back.
                return _trim_words(line, MAX_WORDS * 2)
        except Exception:
            pass  # LLM down mid-show: the fallback keeps the show airing
    return fallback_line(scene, narration_config, primitives, words, names)


# -- Show preparation (the per-airing pass) ------------------------------------

def prepare_show(episode, llm, tts, workdir, *, primitives, narration_config,
                 worker_name="KODI-7", boss_name="the boss", speed=1.0,
                 max_output_lines=24, progress=None, speaker_names=None,
                 verbatim=False):
    """Build the voiced show for one airing.

    Iterates episode['scenes'] DIRECTLY -- no grouping step; a campaign
    generator already emits scene-sized units
    (docs/campaign_platform_build.md "What this deletes"). Returns those
    same scene dicts annotated with:
        narration -- the spoken line (always present)
        audio     -- tts_client.Narration (path + measured duration), or
                    None when TTS is disabled/failed (scene plays silent).

    `progress(message)` is called per scene so the theater pane can show
    "preparing tonight's episode…" while the LLM and TTS work.
    `speaker_names` maps speaker id -> display name for any per-scene
    speaker overrides; it falls back to worker_name/boss_name/raw id (see
    SpeakerNames/_display_name). `verbatim=True` (from a worker's
    `voice.verbatim` config) reads a scene's own text in full instead of
    paraphrasing it to fit the estimated screen time -- see narrate_scene.
    The audio-anchored pacing in replay.py adapts either way, so a longer
    verbatim line just holds the scene a little longer rather than
    desyncing.
    """
    notify = progress or (lambda message: None)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    names = SpeakerNames(worker_name=worker_name, boss_name=boss_name,
                         speaker_names=speaker_names or {})
    scenes = episode["scenes"]
    for index, scene in enumerate(scenes):
        seconds = scene_visual_seconds(scene, primitives, max_output_lines=max_output_lines, speed=speed)
        words = target_words(seconds)
        notify(f"scene {index + 1}/{len(scenes)}: writing {scene['kind']} line (~{words}w)")
        scene["narration"] = narrate_scene(scene, llm, words, names, narration_config,
                                           primitives, verbatim=verbatim)
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
