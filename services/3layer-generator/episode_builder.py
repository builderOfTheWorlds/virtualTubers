"""Port of .claude/prompts/build_generated_episode.py into library code for
the "publish" job stage (Phase 3, task 3.1), and home for the one shared
helper both episode builders use to resolve a cast member to a worker id.

Decision 3 ("no hardcoded values — these should be configs"), applied:

  * The old scripts each carried a hardcoded `SPEAKER_TO_WORKER` dict
    (build_generated_episode.py keyed by lowercased display names,
    build_campaign_episode.py keyed by a mix of member ids and display
    names). Both are retired in favor of the `pack_cast.worker_id` column
    — NOTE: that column was added by Task 1.1, not by Phase 3; the plan's
    task-3.1 prose says "add the column" but it already exists, so this
    file only READs it. An operator changes a mapping in the Pack Editor
    (Task 2.2), never by editing this file.

  * `build_speaker_map()` reads a pack's cast rows from Postgres and keys
    the result by BOTH the member id (e.g. "gm", "Leena") AND each
    member's display name (e.g. "Ashiorid", "Leena"), in both the
    original casing and lowercased, all pointing at that row's worker_id.
    Keying by both is deliberate: the generated pipeline's beat.speaker
    field and the authored pack's beat.speaker field are not guaranteed to
    use the same form (member id vs. display name, whatever casing the
    model/author wrote), so covering every plausible spelling with the
    SAME lookup is simpler and strictly more correct than each converter
    inventing its own key convention. A cast row whose worker_id is NULL
    is absent from the map entirely, which is exactly the "falls through
    to GM narration" behavior the old scripts had for any name not in
    their dict (e.g. an NPC) — no data migration needed for this to work,
    a pack just has no worker routing until its operator sets worker_id.
"""
import datetime as dt
import re
import yaml

# Meta-filler line patterns to drop outright (case-insensitive, whole line).
FILLER_RE = re.compile(
    r"^(here'?s?\s|here is\s).*(scene|beat|response|line)|"
    r"^beat\s*\d+:?$|"
    r"^\(?\s*$",
    re.IGNORECASE,
)
# Leading list-enumeration prefix to strip ("1. Name: text" -> "Name: text").
ENUM_PREFIX_RE = re.compile(r"^\d+\.\s*")
SPEAKER_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z _]{1,24}):\s*(.+)$")


def build_speaker_map(store, pack_name):
    """See module docstring. Returns {<speaker-key>: worker_id} covering
    member id + display name, original casing + lowercased. Empty dict if
    the pack has no cast rows or none of them carry a worker_id."""
    result = {}
    for row in store.list_cast(pack_name):
        worker = row.get("worker_id")
        if not worker:
            continue
        member_id = row.get("member_id") or ""
        member_name = ""
        try:
            data = yaml.safe_load(row.get("member_yaml") or "")
            if isinstance(data, dict) and data.get("name"):
                member_name = str(data["name"]).strip()
        except Exception:
            member_name = ""
        for key in (member_id, member_id.lower(), member_name, member_name.lower()):
            if key:
                result[key] = worker
    return result


def _clean_beat_text(text):
    text = (text or "").strip()
    if not text:
        return None
    if FILLER_RE.match(text):
        return None
    text = ENUM_PREFIX_RE.sub("", text).strip()
    if not text:
        return None
    return text


def _speaker_lookup(speaker_map, name):
    """Resolve a beat's `speaker` (whatever form it arrived in — member id
    or display name, any casing) to a worker id, or None."""
    if not name:
        return None
    name = name.strip()
    if name in speaker_map:
        return speaker_map[name]
    return speaker_map.get(name.lower())


def _leaf_dialogue_events(dialogue_by_slot, get_artifact, segment_id, slot_id,
                          speaker_map):
    """Pull the lowest-take-number dialogue artifact for one slot (from a
    pre-fetched run-wide index — the API's segment_id query param is an
    EXACT match, not a prefix match, so per-slot querying silently misses
    everything because the real segment_id includes a /NNN take suffix),
    cleaned into event dicts. Returns [] if no take exists or the take is
    empty after cleaning (both are real, observed cases -- see the original
    script's module docstring, and Gitea issue #15 for the two segments that
    produced ZERO takes at all)."""
    prefix = f"{segment_id}/{slot_id}/"
    takes = [a for a in dialogue_by_slot if (a.get("segment_id") or "").startswith(prefix)]
    if not takes:
        return []
    takes.sort(key=lambda a: a["segment_id"])  # .../001 before .../002
    take = get_artifact(takes[0]["id"])
    events = []
    for beat in take.get("content", {}).get("beats", []):
        cleaned = _clean_beat_text(beat.get("text"))
        if cleaned is None:
            continue
        # The beat's own `speaker` field is authoritative when the model set
        # it (kind="dialogue" beats reliably carry it even though kind
        # itself is unreliable). Only fall back to parsing "Name: text" out
        # of the raw text when speaker is absent.
        beat_speaker = (beat.get("speaker") or "").strip()
        worker = _speaker_lookup(speaker_map, beat_speaker) if beat_speaker else None
        if worker:
            m = SPEAKER_LINE_RE.match(cleaned)
            text = (m.group(2).strip() if m and m.group(1).strip().lower() == beat_speaker.lower()
                    else cleaned)
            events.append({"type": "assistant_text", "text": text, "speaker": worker})
            continue
        m = SPEAKER_LINE_RE.match(cleaned)
        if m:
            name, rest = m.group(1).strip(), m.group(2).strip()
            worker = _speaker_lookup(speaker_map, name)
            if worker:
                events.append({"type": "assistant_text", "text": rest, "speaker": worker})
                continue
            # Unmapped named speaker (e.g. an NPC): keep as GM narration
            # text, name inline -- same convention as
            # campaign_episode_builder.py for unmapped cast ids.
            events.append({"type": "user_message", "text": cleaned})
            continue
        events.append({"type": "user_message", "text": cleaned})
    return events


def build_episode(run, source, project, artifacts, get_artifact, speaker_map):
    """Build the episode event list for one completed generated run.

    `artifacts` is expected to already be the run-scoped list
    (generation_store.list_artifacts(run) in production) — no re-filtering
    by run happens here, which is what lets this be called in-process
    against FakeStore in tests without a URL.
    """
    arc_plan_artifact = next(a for a in artifacts if a.get("kind") == "arc_plan")
    arc_plan = get_artifact(arc_plan_artifact["id"])
    segments = sorted(arc_plan["content"]["segments"], key=lambda s: s["order"])
    dialogue_by_slot = [a for a in artifacts if a.get("kind") == "dialogue"]

    events = []
    empty_slots, total_slots = 0, 0
    for seg in segments:
        seg_id = seg["id"]
        tree_artifact = next(
            (a for a in artifacts
             if a.get("kind") == "tree" and a.get("segment_id") == seg_id), None)
        if tree_artifact is None:
            continue  # segment never got a brief/tree at all
        tree = get_artifact(tree_artifact["id"]).get("content", {})
        leaves = sorted(
            (n for n in tree.values() if isinstance(n, dict) and n.get("kind") == "leaf"),
            key=lambda n: n["order"])
        for leaf in leaves:
            for slot in leaf.get("slots") or []:
                if slot.get("kind") == "spine":
                    continue  # authored scene content, not a generated take
                total_slots += 1
                slot_events = _leaf_dialogue_events(
                    dialogue_by_slot, get_artifact, seg_id, slot["slot_id"],
                    speaker_map)
                if not slot_events:
                    empty_slots += 1
                    continue
                events.extend(slot_events)

    return {
        "source": source,
        "project": project,
        "session_id": f"generated-{run}",
        "date": dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
        "events": events,
    }
