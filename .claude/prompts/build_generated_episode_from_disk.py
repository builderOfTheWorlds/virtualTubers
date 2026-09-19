#!/usr/bin/env python3
"""Convert a completed LOCAL 3-layer-generator run's filesystem artifacts
(arc_plan.yaml + segments/<id>/brief.yaml + segments/<id>/slots/<slot>/NNN.yaml)
into a Rerun Theater episode script and upload it to the episode store.

Why this exists: build_generated_episode.py bridges the GENERATOR-API path
(Postgres artifacts via http://localhost:8001) to playback. The ring-
composition run for ashiorid_1 (28/28 segments, 168h arc) was executed by
calling plan_arc/plan_segment/generate_segment_dialogue directly against
utilities/3LayersWeeklyGeneration/output/, bypassing the generator-api
service entirely -- so nothing in Postgres knows this run exists. This
script is the filesystem-native equivalent, reading the same on-disk shape
the layer functions themselves wrote.

Same cast mapping and junk-filtering conventions as build_generated_episode.py.

Usage:
    python3 .claude/prompts/build_generated_episode_from_disk.py \\
        --pack campaigns/ashiorid_1 --name ashiorid_generated_ring_v1 [--upload]
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
UTIL_ROOT = REPO / "utilities" / "3LayersWeeklyGeneration"
sys.path.insert(0, str(REPO / "app"))
sys.path.insert(0, str(UTIL_ROOT / "src"))

SPEAKER_TO_WORKER = {
    "gm": "manager",
    "ashiorid": "manager",
    "chadwick": "coder",
    "leena": "tester",
    "vigil": "coder-native",
    "sodacanbob": "coder-opencode",
    "sodacan_bob": "coder-opencode",
    "sodacan bob": "coder-opencode",
}

FILLER_RE = re.compile(
    r"^(here'?s?\s|here is\s).*(scene|beat|response|line)|"
    r"^beat\s*\d+:?$|"
    r"^\(?\s*$",
    re.IGNORECASE,
)
ENUM_PREFIX_RE = re.compile(r"^\d+\.\s*")
SPEAKER_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z _]{1,24}):\s*(.+)$")


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


def _lowest_take_path(seg_dir: Path, slot_id: str):
    slot_dir = seg_dir / "slots" / slot_id
    if not slot_dir.exists():
        return None
    takes = sorted(slot_dir.glob("*.yaml"))
    return takes[0] if takes else None


def _slot_dialogue_events(seg_dir: Path, slot_id: str):
    import yaml
    take_path = _lowest_take_path(seg_dir, slot_id)
    if take_path is None:
        return []
    take = yaml.safe_load(take_path.read_text(encoding="utf-8"))
    events = []
    for beat in take.get("beats", []):
        cleaned = _clean_beat_text(beat.get("text"))
        if cleaned is None:
            continue
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
            events.append({"type": "user_message", "text": cleaned})
            continue
        events.append({"type": "user_message", "text": cleaned})
    return events


def build_episode(pack_path_str, source, project):
    import yaml
    import config as config_module

    config_path = UTIL_ROOT / "config" / "generation.ashiorid_continuation.yaml"
    config = config_module.load_config(config_path)
    pack_path = REPO / pack_path_str
    out_root = config_module.output_root(config, str(pack_path))
    arc_plan_path = config_module.arc_plan_path(config, str(pack_path))

    arc_plan = yaml.safe_load(arc_plan_path.read_text(encoding="utf-8"))
    segments = sorted(arc_plan["segments"], key=lambda s: s["order"])

    events = []
    empty_slots, total_slots, missing_briefs = 0, 0, 0
    for seg in segments:
        seg_id = seg["id"]
        seg_dir = out_root / "segments" / seg_id
        brief_path = seg_dir / "brief.yaml"
        if not brief_path.exists():
            missing_briefs += 1
            continue
        brief = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
        for slot in brief.get("slots", []):
            if slot.get("kind") == "spine":
                continue  # authored scene content, not a generated take
            total_slots += 1
            slot_events = _slot_dialogue_events(seg_dir, slot["slot_id"])
            if not slot_events:
                empty_slots += 1
                continue
            events.extend(slot_events)

    print(f"  segments: {len(segments)}, missing briefs: {missing_briefs}")
    print(f"  slots seen: {total_slots}, empty/skipped: {empty_slots}")

    return {
        "source": source,
        "project": project,
        "session_id": f"generated-{source}",
        "date": dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
        "events": events,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, help="e.g. campaigns/ashiorid_1")
    ap.add_argument("--name", required=True, help="episode key in the library")
    ap.add_argument("--project", default="virtualTubers")
    ap.add_argument("--out", default=None)
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--api", default="http://localhost:8090")
    args = ap.parse_args()

    ep = build_episode(args.pack, args.name, args.project)

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
