#!/usr/bin/env python3
"""Build a long-form Rerun Theater episode from campaigns/<pack> by looping
the spine (the show's own premise: a weekly time loop) and injecting
generated ambient-scene takes from <pack>/generated/ between spine scenes,
per campaign.yaml's `ambient.every` config.

Distinct from build_campaign_episode.py (walks the spine ONCE, no ambient)
and build_generated_episode_from_disk.py (bridges the 3-layer generator's
per-arc-segment output). This is the bridge for app/campaign/batch_generate.py
output — the live engine's own ambient-generation mechanism
(docs/campaign_content_expansion.md) — looped enough times to approach a
target duration.

Ambient takes are drawn round-robin per scene, cycling through every take on
disk before repeating any — so a loop naturally gets fresh wording each pass
through the same scene, for as many loops as there is material, with the
oldest look repeating once the pool is exhausted mid-run (better than
crashing; --min-loops/--target-words governs how far to push it).

Episode size is capped at episode_validator.MAX_BYTES (8MB); once a build
would exceed it, this script splits output across multiple sequentially
named episode files (`<name>_partNN`) rather than producing one file the
server would reject.

Usage:
    python3 .claude/prompts/build_ashiorid_full_episode.py \\
        --pack campaigns/ashiorid_1 --name ashiorid_full [--upload] \\
        --target-words 1500000
"""
import argparse
import datetime as dt
import itertools
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))

from campaign.pack import load_pack  # noqa: E402

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

MAX_BYTES_SAFETY = 7 * 1024 * 1024  # leave headroom under the server's 8MB cap
WORDS_PER_HOUR = 8929  # docs/campaign_content_expansion.md: 168h * 150wpm ~= 1.5M


def _text_of(beat):
    if getattr(beat, "text", None):
        return str(beat.text).strip()
    for t in (getattr(beat, "texts", None) or []):
        if t and str(t).strip():
            return str(t).strip()
    return ""


def _clean_beat_text(text):
    text = (text or "").strip()
    if not text:
        return None
    if FILLER_RE.match(text):
        return None
    text = ENUM_PREFIX_RE.sub("", text).strip()
    return text or None


def _take_events(take):
    import yaml
    data = yaml.safe_load(take.read_text(encoding="utf-8"))
    events = []
    for beat in data.get("beats", []):
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


def _spine_scenes(pack, max_scenes):
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


class AmbientCycler:
    """Round-robins takes for one ambient scene id, cycling the whole pool
    before repeating any take, so early loops get maximum variety."""

    def __init__(self, pack_root: Path, scene_id: str):
        self.scene_id = scene_id
        scene_dir = pack_root / "generated" / scene_id
        takes = sorted(scene_dir.glob("*.yaml")) if scene_dir.exists() else []
        self._cycle = itertools.cycle(takes) if takes else None

    def next_events(self):
        if self._cycle is None:
            return []
        take = next(self._cycle)
        return _take_events(take)


def build_events(pack, max_scenes, target_words):
    """Loop the spine, injecting ambient takes per campaign.yaml's
    ambient.every, until target_words is reached or ambient material runs
    out entirely (checked once per loop, not mid-loop, to avoid stopping on
    a lucky word-count undershoot mid-scene)."""
    spine = _spine_scenes(pack, max_scenes)
    ambient_ids = pack.ambient_scene_ids()
    cyclers = {sid: AmbientCycler(pack.root, sid) for sid in ambient_ids}
    ambient_available = any(c._cycle is not None for c in cyclers.values())

    events = []
    total_words = 0
    loop_count = 0
    ambient_pointer = 0

    def word_count(evs):
        return sum(len(e.get("text", "").split()) for e in evs)

    while total_words < target_words:
        loop_count += 1
        loop_events = []
        for i, scene in enumerate(spine):
            if scene.enter_narration:
                loop_events.append({"type": "user_message",
                                     "text": str(scene.enter_narration).strip()})
            for beat in scene.beats:
                kind = getattr(beat, "kind", "")
                text = _text_of(beat)
                if not text:
                    continue
                if kind == "dialogue":
                    speaker = SPEAKER_TO_WORKER.get(getattr(beat, "speaker", None))
                    ev = {"type": "assistant_text", "text": text}
                    if speaker:
                        ev["speaker"] = speaker
                    loop_events.append(ev)
                elif kind in ("narration", "action"):
                    loop_events.append({"type": "user_message", "text": text})

            if pack.ambient_every and (i + 1) % pack.ambient_every == 0 and ambient_ids:
                sid = ambient_ids[ambient_pointer % len(ambient_ids)]
                ambient_pointer += 1
                loop_events.extend(cyclers[sid].next_events())

        events.extend(loop_events)
        total_words += word_count(loop_events)

        if not ambient_available and loop_count >= 1:
            # No generated ambient content at all: looping the bare ~2k-word
            # spine forever would be pure repetition with zero new wording.
            # Stop after one loop and report the shortfall honestly rather
            # than silently producing a "168h" episode that is actually the
            # same ~15 minutes copy-pasted hundreds of times.
            break

    return events, total_words, loop_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--project", default="virtualTubers")
    ap.add_argument("--max-scenes", type=int, default=30)
    ap.add_argument("--target-words", type=int, default=WORDS_PER_HOUR * 168)
    ap.add_argument("--out", default=None)
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--api", default="http://localhost:8090")
    args = ap.parse_args()

    pack = load_pack(REPO / args.pack)
    events, total_words, loop_count = build_events(pack, args.max_scenes, args.target_words)

    hours_at_150wpm = total_words / 150 / 60
    hours_at_target_rate = total_words / WORDS_PER_HOUR
    print(f"episode '{args.name}': {len(events)} events, {total_words:,} words, "
          f"{loop_count} spine loop(s)")
    print(f"  estimated duration at ~150 wpm: {hours_at_150wpm:.2f} hours")
    print(f"  target-rate equivalent ({WORDS_PER_HOUR} words/hr): {hours_at_target_rate:.2f} hours")
    print(f"  target was {args.target_words:,} words "
          f"({args.target_words / WORDS_PER_HOUR:.1f} hours at target rate)")

    def make_episode(name, evs):
        return {
            "source": name,
            "project": args.project,
            "session_id": f"campaign-loop-{name}",
            "date": dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
            "events": evs,
        }

    # Split into byte-capped parts if needed.
    parts = []
    current, current_bytes = [], 2  # "[]"
    for ev in events:
        ev_bytes = len(json.dumps(ev, ensure_ascii=False).encode("utf-8")) + 1
        if current and current_bytes + ev_bytes > MAX_BYTES_SAFETY:
            parts.append(current)
            current, current_bytes = [], 2
        current.append(ev)
        current_bytes += ev_bytes
    if current:
        parts.append(current)

    print(f"  split into {len(parts)} episode file(s) (8MB server cap)")

    from episode_validator import validate_episode

    written = []
    for i, part_events in enumerate(parts):
        part_name = args.name if len(parts) == 1 else f"{args.name}_part{i+1:02d}"
        ep = make_episode(part_name, part_events)
        try:
            validate_episode(ep, part_name)
            status = "PASS"
        except Exception as exc:
            status = f"FAIL — {type(exc).__name__}: {exc}"
        out = Path(args.out) if (args.out and len(parts) == 1) else REPO / f"/tmp/{part_name}.json"
        out.write_text(json.dumps(ep, indent=1, ensure_ascii=False), encoding="utf-8")
        part_words = sum(len(e.get("text", "").split()) for e in part_events)
        print(f"  part {i+1}/{len(parts)} '{part_name}': {len(part_events)} events, "
              f"{part_words:,} words, validation: {status}, wrote: {out}")
        written.append((part_name, out, status))

        if args.upload and status == "PASS":
            import urllib.error
            import urllib.request
            url = f"{args.api}/replays?name={part_name}&overwrite=true"
            req = urllib.request.Request(
                url, data=out.read_bytes(), method="POST",
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    print(f"    upload: {r.status} {r.read().decode()[:200]}")
            except urllib.error.HTTPError as e:
                print(f"    upload FAILED: {e.code} {e.read().decode()[:400]}")

    failed = [p for p in written if p[2] != "PASS"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
