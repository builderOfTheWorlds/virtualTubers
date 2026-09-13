#!/usr/bin/env python3
"""Convert a completed 3-layer-generator RUN's dialogue_takes artifacts into
a Rerun Theater episode script and upload it to the episode store.

Why this exists: build_campaign_episode.py bridges the AUTHORED pack (the
polished, human-written spine scenes) to playback. Nothing bridges the
GENERATED path (arc -> segment -> dialogue, Layer 3 output stored as
Postgres artifacts via the generator API at http://localhost:8001) to
playback -- see .claude/skills devops/3layer-generator-ops,
references/output_consumption_and_demo_scoping.md ("nothing in app/ reads
generator output"). This script is that bridge, built for run
ashiorid_1_20260913_180158_ce8d (job_20260913T180158_66d6a7 arc /
job_20260913T194943_2d5ff1 segment / job_20260913T195715_f66367 dialogue).

IMPORTANT — content quality: generated takes are visibly rougher than
authored prose. Observed junk patterns this script strips:
  - meta-filler lines ("Here is the first line of the scene:", "Beat 3:",
    numbered-list preambles like "1. Ashiorid: ...")
  - every beat labelled kind="narration" even when it's clearly dialogue
    (the model didn't reliably use the dialogue/narration distinction)
  - empty takes (all beats stripped to nothing, e.g. one leaf in
    grovley-revelation-arc3 -- see Gitea issue #15 for the two segments
    that produced ZERO takes at all)
Read a few sample episodes/*.json events before airing this to an audience.

Mapping (same cast table as build_campaign_episode.py -- these are OLD
worker ids, not roundtable slots; WP-7 rename not done yet):
  Ashiorid -> manager (GM)   Chadwick -> coder   Leena -> tester
  Vigil -> coder-native      Sodacan Bob / Sodacan_Bob -> coder-opencode
Any other named speaker (e.g. Grovley, an NPC) is left as GM narration
(user_message) with the name kept inline in the text, same as build_
campaign_episode.py's handling of unmapped speakers.

Usage:
    python3 .claude/prompts/build_generated_episode.py \\
        --run ashiorid_1_20260913_180158_ce8d --name ashiorid_generated_ce8d [--upload]
"""
import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))

GENERATOR_API = "http://localhost:8001"

SPEAKER_TO_WORKER = {
    "ashiorid": "manager",
    "chadwick": "coder",
    "leena": "tester",
    "vigil": "coder-native",
    "sodacanbob": "coder-opencode",
    "sodacan_bob": "coder-opencode",
    "sodacan bob": "coder-opencode",
}

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


def _api_get(path):
    with urllib.request.urlopen(f"{GENERATOR_API}{path}", timeout=30) as r:
        return json.loads(r.read().decode())


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


def _leaf_dialogue_events(dialogue_by_slot, segment_id, slot_id):
    """Pull the lowest-take-number dialogue artifact for one slot (from a
    pre-fetched run-wide index -- the API's segment_id query param is an
    EXACT match, not a prefix match, so per-slot querying silently misses
    everything because the real segment_id includes a /NNN take suffix),
    cleaned into event dicts. Returns [] if no take exists or the take is
    empty after cleaning (both are real, observed cases -- see module
    docstring)."""
    prefix = f"{segment_id}/{slot_id}/"
    takes = [a for a in dialogue_by_slot if a["segment_id"].startswith(prefix)]
    if not takes:
        return []
    takes.sort(key=lambda a: a["segment_id"])  # .../001 before .../002
    take = _api_get(f"/artifacts/{takes[0]['id']}")
    events = []
    for beat in take["content"].get("beats", []):
        cleaned = _clean_beat_text(beat.get("text"))
        if cleaned is None:
            continue
        # The beat's own `speaker` field is authoritative when the model set
        # it (kind="dialogue" beats reliably carry it even though kind
        # itself is unreliable -- see module docstring). Only fall back to
        # parsing "Name: text" out of the raw text when speaker is absent,
        # which happens on kind="narration" beats that still read as
        # dialogue (the GM narrating another character's line, or an NPC
        # not in the mapped cast at all).
        beat_speaker = (beat.get("speaker") or "").strip()
        worker = SPEAKER_TO_WORKER.get(beat_speaker.lower()) if beat_speaker else None
        if worker:
            m = SPEAKER_LINE_RE.match(cleaned)
            text = m.group(2).strip() if m and m.group(1).strip().lower() == beat_speaker.lower() else cleaned
            events.append({"type": "assistant_text", "text": text, "speaker": worker})
            continue
        m = SPEAKER_LINE_RE.match(cleaned)
        if m:
            name, rest = m.group(1).strip(), m.group(2).strip()
            worker = SPEAKER_TO_WORKER.get(name.lower())
            if worker:
                events.append({"type": "assistant_text", "text": rest, "speaker": worker})
                continue
            # Unmapped named speaker (e.g. an NPC like Grovley): keep as GM
            # narration text, name inline -- same convention as
            # build_campaign_episode.py for unmapped cast ids.
            events.append({"type": "user_message", "text": cleaned})
            continue
        events.append({"type": "user_message", "text": cleaned})
    return events


def build_episode(run, source, project):
    all_artifacts = _api_get(f"/artifacts?run={run}")
    arc_plan_artifact = next(a for a in all_artifacts if a["kind"] == "arc_plan")
    arc_plan = _api_get(f"/artifacts/{arc_plan_artifact['id']}")
    segments = sorted(arc_plan["content"]["segments"], key=lambda s: s["order"])
    dialogue_by_slot = [a for a in all_artifacts if a["kind"] == "dialogue"]

    events = []
    empty_slots, total_slots = 0, 0
    for seg in segments:
        seg_id = seg["id"]
        tree_artifact = next(
            (a for a in all_artifacts
             if a["kind"] == "tree" and a["segment_id"] == seg_id), None)
        if tree_artifact is None:
            continue  # segment never got a brief/tree at all
        tree = _api_get(f"/artifacts/{tree_artifact['id']}")["content"]
        leaves = sorted(
            (n for n in tree.values() if isinstance(n, dict) and n.get("kind") == "leaf"),
            key=lambda n: n["order"])
        for leaf in leaves:
            for slot in leaf.get("slots") or []:
                if slot.get("kind") == "spine":
                    continue  # authored scene content, not a generated take
                total_slots += 1
                slot_events = _leaf_dialogue_events(dialogue_by_slot, seg_id, slot["slot_id"])
                if not slot_events:
                    empty_slots += 1
                    continue
                events.extend(slot_events)

    print(f"  slots seen: {total_slots}, empty/skipped: {empty_slots}")

    return {
        "source": source,
        "project": project,
        "session_id": f"generated-{run}",
        "date": dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
        "events": events,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="generator run id, e.g. ashiorid_1_20260913_180158_ce8d")
    ap.add_argument("--name", required=True, help="episode key in the library")
    ap.add_argument("--project", default="virtualTubers")
    ap.add_argument("--out", default=None)
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--api", default="http://localhost:8090")
    args = ap.parse_args()

    ep = build_episode(args.run, args.name, args.project)

    kinds = {}
    for e in ep["events"]:
        kinds[e["type"]] = kinds.get(e["type"], 0) + 1
    speakers = sorted({e.get("speaker") for e in ep["events"] if e.get("speaker")})
    words = sum(len(e.get("text", "").split()) for e in ep["events"])
    print(f"episode '{args.name}': {len(ep['events'])} events, {words} words")
    print(f"  types:    {kinds}")
    print(f"  speakers: {speakers}")

    try:
        from episode_validator import validate_episode
        validate_episode(ep, args.name)
        print("  validation: PASS (shape + dry-run performance)")
    except Exception as exc:
        print(f"  validation: FAIL — {type(exc).__name__}: {exc}")
        return 1

    out = Path(args.out) if args.out else REPO / f"/tmp/{args.name}.json"
    out.write_text(json.dumps(ep, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote: {out}")

    if args.upload:
        url = f"{args.api}/replays?name={args.name}&overwrite=true"
        req = urllib.request.Request(
            url, data=out.read_bytes(), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                print("  upload:", r.status, r.read().decode()[:200])
        except urllib.error.HTTPError as e:
            print("  upload FAILED:", e.code, e.read().decode()[:400])
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
