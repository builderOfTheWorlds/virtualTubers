"""Port of .claude/prompts/build_campaign_episode.py into library code for
the "publish" job stage (Phase 3, task 3.1).

`build_episode()` itself is copied from the original script unchanged
except for two things:
  * `speaker_map` is a parameter (see episode_builder.build_speaker_map —
    the shared helper that retires the old hardcoded SPEAKER_TO_WORKER dict
    with a per-pack Postgres lookup, decision 3), not a module constant.
  * This file assumes the CALLER has already loaded the pack and is
    responsible for it — and that caller, in the publish-stage path the
    plan's Phase 3 note calls out, is `runner._run_publish`, which loads
    via `ctx.load_pack_for_job(pack_name)` (Postgres, the 1.5 cutover), NOT
    `load_pack(REPO / args.pack)` off the host filesystem. That host-path
    read only remains in the original script's now-obsolete main(), which
    this file deliberately does not port at all.

Music: each scene's GEMS `mood:` list is attached as `music` to the first
event emitted for that scene, and the pack name becomes show.music.theme
(docs/episode_validator.md "Scene mood cues") — kept in step with the
original script.
"""
import datetime as dt
import re

from episode_builder import build_speaker_map as _build_speaker_map  # noqa: F401


# Background-music cues (docs/episode_validator.md "Scene mood cues"): the
# scene's GEMS `mood:` list rides along on the FIRST event emitted for that
# scene, and the pack name becomes show.music.theme, so the roundtable
# director can publish a music scene cue at each scene start.
_MUSIC_THEME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_GEMS_MOODS = frozenset({
    "wonder", "transcendence", "tenderness", "nostalgia", "peacefulness",
    "power", "joyful_activation", "tension", "sadness",
})


def scene_music(scene):
    """The `music` object for a scene's first event, or None when the scene
    carries no (valid) mood."""
    moods = [m for m in (getattr(scene, "mood", None) or []) if m in _GEMS_MOODS]
    if not moods:
        return None
    return {"mood": moods, "scene_id": str(scene.id)}


def attach_scene_music(events, start, scene):
    """Mark events[start] (the scene's first emitted event) with the scene's
    music cue. No-op when the scene emitted nothing or has no mood."""
    music = scene_music(scene)
    if music and len(events) > start:
        events[start]["music"] = music


def show_music_header(pack):
    """{"music": {"theme": <pack name>}} or None if the name is unsafe."""
    name = str(getattr(pack, "name", "") or "")
    if not _MUSIC_THEME_RE.match(name):
        return None
    return {"music": {"theme": name}}


def _text_of(beat):
    """A beat's text: `text`, or the first variant from a `texts:` pool."""
    if getattr(beat, "text", None):
        return str(beat.text).strip()
    texts = getattr(beat, "texts", None) or []
    for t in texts:
        if t and str(t).strip():
            return str(t).strip()
    return ""


def walk_scenes(pack, max_scenes):
    """Follow default_next from start_scene, skipping ambient scenes (they
    have no beats — they're runtime improv prompts, not authored content)."""
    seen, order, sid = set(), [], pack.start_scene
    while sid and sid not in seen and len(order) < max_scenes:
        seen.add(sid)
        try:
            scene = pack.scene(sid)
        except Exception:
            break
        if not scene.ambient and scene.beats:
            order.append(scene)
        sid = scene.default_next
    return order


def build_episode(pack, source, project, max_scenes, speaker_map):
    events = []
    for scene in walk_scenes(pack, max_scenes):
        start = len(events)
        if scene.enter_narration:
            events.append({
                "type": "user_message",
                "text": str(scene.enter_narration).strip(),
            })
        for beat in scene.beats:
            kind = getattr(beat, "kind", "")
            text = _text_of(beat)
            if not text:
                continue
            if kind == "dialogue":
                speaker = None
                raw_speaker = getattr(beat, "speaker", None)
                if raw_speaker:
                    if raw_speaker in speaker_map:
                        speaker = speaker_map[raw_speaker]
                    else:
                        speaker = speaker_map.get(raw_speaker.lower())
                ev = {"type": "assistant_text", "text": text}
                if speaker:
                    ev["speaker"] = speaker
                events.append(ev)
            elif kind in ("narration", "action"):
                events.append({"type": "user_message", "text": text})
        attach_scene_music(events, start, scene)
    episode = {
        "source": source,
        "project": project,
        "session_id": f"campaign-{source}",
        "date": dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
        "events": events,
    }
    header = show_music_header(pack)
    if header:
        episode["show"] = header
    return episode
