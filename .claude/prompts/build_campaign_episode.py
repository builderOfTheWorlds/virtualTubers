#!/usr/bin/env python3
"""Convert a campaign pack (campaigns/<name>/) into a Rerun Theater episode
script that the replay pane can air, and upload it to the episode store.

Why this exists: the 3-layer generator writes arc/brief/dialogue artifacts
under utilities/3LayersWeeklyGeneration/output/, and the Rerun Theater
replay path reads episodes from Postgres via message-api /replays. Nothing
in app/ bridges the two, so an authored campaign can be played by
campaign.cli in a terminal but never reaches the stream. This script is that
bridge for the AUTHORED pack (the polished, human-written spine scenes).

Episode contract (app/episode_validator.py):
  required keys: source, project, session_id, date, events
  event types:   user_message{text} | assistant_text{text[,speaker]}
                 | tool_call{tool}
  >= 5 events, and it must survive a dry-run performance + narration plan.

Mapping:
  scene enter_narration / narration beats  -> user_message  (the GM/narrator
      voice; replay.py treats user_message as the prompting/narrating side)
  dialogue beats                           -> assistant_text + speaker
  action beats                             -> user_message (described action)

`speaker` is mapped from campaign cast ids to WORKER ids, because the duet
cast map and the avatar panes key off worker ids, not campaign names.

Usage:
    python3 .claude/prompts/build_campaign_episode.py \
        --pack campaigns/ashiorid_1 --name ashiorid --max-scenes 12 [--upload]
"""
import argparse
import datetime as dt
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))

from campaign.pack import load_pack  # noqa: E402

# Campaign cast id -> worker id. The replay pane resolves speakers to the
# worker avatars on screen, so an unmapped name would render as an unknown
# speaker AND lose its Piper voice (voice.speakers is keyed by worker id).
#
# One tuber per character, one for the GM, one left open:
#   manager        -> Ashiorid (GM / narrator)      twitch: mastermanagerchief
#   coder          -> Chadwick                      twitch: the_emperor_secus
#   tester         -> Leena                         twitch: burtyrenoldo
#   coder-native   -> Vigil                         twitch: ten_g_hands
#   coder-opencode -> Sodacan Bob                   twitch: spam_of_frogs
#   coder-aider    -> (OPEN - no character assigned) twitch: braxton_caruso
#
# Cast ids come from campaigns/ashiorid_1/campaign.yaml `players:` and must
# match the cast/ filenames exactly (chadwick.yaml, Leena.yaml, Vigil.yaml,
# sodacan_bob.yaml, gm.yaml) - a mismatch makes load_pack() fail outright.
SPEAKER_TO_WORKER = {
    "gm": "manager",
    "chadwick": "coder",
    "Leena": "tester",
    "Vigil": "coder-native",
    "sodacan_bob": "coder-opencode",
}


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


def build_episode(pack, source, project, max_scenes):
    events = []
    for scene in walk_scenes(pack, max_scenes):
        if scene.enter_narration:
            events.append({"type": "user_message",
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
                events.append(ev)
            elif kind in ("narration", "action"):
                events.append({"type": "user_message", "text": text})
    return {
        "source": source,
        "project": project,
        "session_id": f"campaign-{source}",
        "date": dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
        "events": events,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--name", required=True, help="episode key in the library")
    ap.add_argument("--project", default="virtualTubers")
    ap.add_argument("--max-scenes", type=int, default=12)
    ap.add_argument("--out", default=None)
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--api", default="http://localhost:8090")
    args = ap.parse_args()

    pack = load_pack(REPO / args.pack)
    ep = build_episode(pack, args.name, args.project, args.max_scenes)

    kinds = {}
    for e in ep["events"]:
        kinds[e["type"]] = kinds.get(e["type"], 0) + 1
    speakers = sorted({e.get("speaker") for e in ep["events"] if e.get("speaker")})
    words = sum(len(e.get("text", "").split()) for e in ep["events"])
    print(f"episode '{args.name}': {len(ep['events'])} events, {words} words")
    print(f"  types:    {kinds}")
    print(f"  speakers: {speakers}")

    # Validate locally with the SAME validator the server uses, so a failure
    # is reported here with a clear reason instead of as an opaque HTTP 400.
    try:
        from episode_validator import validate_episode
        validate_episode(ep, args.name)
        print("  validation: PASS (shape + dry-run performance)")
    except Exception as exc:
        print(f"  validation: FAIL — {type(exc).__name__}: {exc}")
        return 1

    out = pathlib.Path(args.out) if args.out else REPO / f"/tmp/{args.name}.json"
    out.write_text(json.dumps(ep, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote: {out}")

    if args.upload:
        import urllib.request
        import urllib.error
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
