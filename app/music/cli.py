"""
cli.py — offline rendering / auditioning / recording of the score.

Run from app/ (same convention as `python -m campaign.cli`):

  # 30 s of one mood -> WAV
  python -m music.cli render ../campaigns/ashiorid/music/theme.yaml \
      --mood sadness --seconds 30 --out ../preview_out/music/sadness.wav

  # a scripted mood timeline (mood[:intensity]@seconds) -> one WAV, showing
  # how the theme morphs across scene changes
  python -m music.cli timeline ../campaigns/ashiorid/music/theme.yaml \
      --cue peacefulness@0 --cue tension:0.7@20 --cue sadness@45 --seconds 70

  # add --record to either command to write the session to Postgres
  # (POSTGRES_* env must be set).

  # list recorded segments of a session / export one segment's audio
  python -m music.cli segments <session_id>
  python -m music.cli export <segment_id> --out seg.ogg
"""
import argparse
import logging
import sys
import time
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger("music.cli")


def _write_wav(path, audio, sr):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def parse_cue(text):
    """'tension:0.7@20' -> (20.0, 'tension', 0.7). Intensity optional."""
    try:
        head, at = text.rsplit("@", 1)
        mood, _, inten = head.partition(":")
        return float(at), mood.strip(), (float(inten) if inten else 0.5)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"cue {text!r} must look like mood[:intensity]@seconds") from exc


def _make_recorder(args, engine):
    if not args.record:
        return None
    from music import music_store
    from music.recorder import MusicRecorder
    if not music_store.available():
        sys.exit("--record needs psycopg2 and POSTGRES_DB/USER/PASSWORD env")
    rec = MusicRecorder(music_store, engine, worker_id=args.worker_id,
                        campaign=args.campaign or engine.theme.name, episode=args.episode)
    sid = rec.start()
    print(f"recording to postgres: music_sessions.id={sid}")
    return rec


def run_timeline(theme, cues, seconds, recorder=None):
    """Render with mood changes at cue times. Returns (audio, bars)."""
    from music.engine import MusicEngine
    cues = sorted(cues)
    first = cues[0] if cues else (0.0, "neutral", 0.5)
    engine = MusicEngine(theme, mood=first[1], intensity=first[2])
    return _drive(engine, cues, seconds, recorder)


def _drive(engine, cues, seconds, recorder):
    pending = list(sorted(cues))
    bars = []
    while engine.time_s < seconds:
        while pending and pending[0][0] <= engine.time_s:
            _, mood, inten = pending.pop(0)
            engine.set_mood(mood, inten)
            if recorder:
                recorder.set_context(source="cue", scene_id=f"{mood}@{engine.time_s:.1f}")
        bar = engine.render_bar()
        bars.append(bar)
        if recorder:
            recorder.add_bar(bar)
    return np.concatenate([b.audio for b in bars]), bars


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="music.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("theme", help="theme YAML path")
        p.add_argument("--seconds", type=float, default=30.0)
        p.add_argument("--out", help="output WAV path")
        p.add_argument("--record", action="store_true", help="write to Postgres")
        p.add_argument("--worker-id", default="cli")
        p.add_argument("--campaign", default="")
        p.add_argument("--episode", default="")

    p_r = sub.add_parser("render", help="render one mood")
    common(p_r)
    p_r.add_argument("--mood", default="neutral")
    p_r.add_argument("--intensity", type=float, default=0.5)

    p_t = sub.add_parser("timeline", help="render a mood timeline")
    common(p_t)
    p_t.add_argument("--cue", action="append", type=parse_cue, default=[],
                     help="mood[:intensity]@seconds (repeatable)")

    p_s = sub.add_parser("segments", help="list a recorded session's segments")
    p_s.add_argument("session_id", type=int)

    p_e = sub.add_parser("export", help="export one recorded segment's audio")
    p_e.add_argument("segment_id", type=int)
    p_e.add_argument("--out", required=True)

    args = ap.parse_args(argv)

    if args.cmd in ("segments", "export"):
        from music import music_store
        if args.cmd == "segments":
            for seg in music_store.list_segments(args.session_id):
                print(seg)
            return 0
        got = music_store.load_segment_audio(args.segment_id)
        if not got or not got[0]:
            print(f"segment {args.segment_id} has no audio", file=sys.stderr)
            return 1
        Path(args.out).write_bytes(got[0])
        print(f"wrote {args.out} ({got[1]}, {len(got[0])} bytes)")
        return 0

    from music.engine import MusicEngine
    from music.theme import load_theme
    theme = load_theme(args.theme)
    if args.cmd == "render":
        cues = [(0.0, args.mood, args.intensity)]
        default_name = f"{theme.name}_{args.mood}.wav"
    else:
        cues = args.cue or [(0.0, "neutral", 0.5)]
        default_name = f"{theme.name}_timeline.wav"
    first = sorted(cues)[0]
    engine = MusicEngine(theme, mood=first[1], intensity=first[2])
    recorder = _make_recorder(args, engine)

    t0 = time.perf_counter()
    audio, bars = _drive(engine, cues, args.seconds, recorder)
    elapsed = time.perf_counter() - t0
    if recorder:
        recorder.close()
    out = _write_wav(args.out or Path("../preview_out/music") / default_name, audio, engine.sr)
    audio_s = len(audio) / engine.sr
    print(f"wrote {out}  bars={len(bars)} audio={audio_s:.1f}s render={elapsed:.2f}s "
          f"realtime_factor={audio_s / max(elapsed, 1e-9):.1f}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
