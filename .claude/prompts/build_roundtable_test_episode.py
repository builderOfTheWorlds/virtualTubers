#!/usr/bin/env python3
"""
build_roundtable_test_episode.py
Build a 7-slot roundtable test episode WITH a `show` header (design §7.1) and
upload it to the library.

Why this exists separately from build_campaign_episode.py: that script maps
campaign cast ids to the OLD worker ids (manager/coder/tester/...) because the
duet cast map and voice.speakers are keyed by worker id, and the worker-id
rename is still WP-7. This script instead emits SLOT speakers (tuber_0..tuber_6)
plus a show header binding each slot to a persona name and a symbolic registry
voice — which is exactly what the roundtable path consumes, and what V1
validation checks at upload.

The dialogue is deliberately short and generic: this is a plumbing test for the
tile fan-out + cue ratchet + 7-way audio ownership, not a story.

Usage:
    python3 .claude/prompts/build_roundtable_test_episode.py            # write JSON
    python3 .claude/prompts/build_roundtable_test_episode.py --upload   # + POST it
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))

# slot -> (persona name, symbolic registry voice from config/voices.yaml)
CAST = {
    "tuber_0": ("The Chronicler", "narrator_warm"),
    "tuber_1": ("Chadwick", "baritone_mid"),
    "tuber_2": ("Vigil", "tenor_low"),
    "tuber_3": ("Sodacan Bob", "baritone_soft"),
    "tuber_4": ("Ada", "alto_warm"),
    "tuber_5": ("Leena", "alto_bright"),
    "tuber_6": ("Max", "tenor_high"),
}

# One round of the table: the GM frames, each character answers, GM closes.
LINES = [
    ("tuber_0", "The table is set. Seven voices, one story — let us begin."),
    ("tuber_1", "Chadwick here. The foundations are solid; I checked them twice."),
    ("tuber_2", "Vigil. I watched the western approach. Nothing moved."),
    ("tuber_3", "Sodacan Bob, reporting. The archives are older than we assumed."),
    ("tuber_4", "Ada speaking. I can route around the damage if we act tonight."),
    ("tuber_5", "Leena. I tested every assumption in that plan. Two of them failed."),
    ("tuber_6", "Max. Then we revise the plan, not the deadline."),
    ("tuber_0", "Noted, all of you. We move at dawn — and the table remembers."),
]


def build():
    events = [{"type": "user_message",
               "text": "Convene the roundtable and hear every voice in turn."}]
    for slot, text in LINES:
        events.append({"type": "assistant_text", "text": text, "speaker": slot})

    return {
        "show": {
            "title": "Roundtable Plumbing Test — One Full Round",
            "slots": list(CAST),
            "persona": {slot: {"name": name, "voice": voice}
                        for slot, (name, voice) in CAST.items()},
        },
        "source": "roundtable_test",
        "project": "virtualTubers",
        "session_id": "roundtable-test-001",
        "date": "2026-09-13",
        "events": events,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="roundtable_test")
    ap.add_argument("--out", default="/tmp/roundtable_test.json")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--api", default="http://localhost:8090")
    args = ap.parse_args()

    episode = build()
    Path(args.out).write_text(json.dumps(episode, indent=2), encoding="utf-8")
    print(f"wrote {args.out}: {len(episode['events'])} events, "
          f"{len(episode['show']['slots'])} slots")

    # Validate locally first — the same gate message-api runs (§8.1). Catching a
    # problem here means never sending a broken episode over the wire.
    try:
        from episode_validator import validate_episode, EpisodeInvalid
        info = validate_episode(episode, name=args.name)
        print(f"local validation PASSED: {info}")
    except EpisodeInvalid as exc:
        print(f"local validation FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # registry not readable locally, etc.
        print(f"local validation skipped ({type(exc).__name__}: {exc})")

    if not args.upload:
        print("(not uploading; pass --upload)")
        return 0

    url = f"{args.api}/replays?name={args.name}&overwrite=true"
    req = urllib.request.Request(
        url, data=json.dumps(episode).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print(f"upload {resp.status}: {resp.read().decode()}")
    except urllib.error.HTTPError as exc:
        print(f"upload FAILED {exc.code}: {exc.read().decode()}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"upload unreachable: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
