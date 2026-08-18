"""Offline batch generation of ambient-scene takes for a campaign pack.

Standalone script — imports the same building blocks the live path uses
(`load_pack`, `LLMImproviser`, `build_llm_client`) but never touches
`renderer.py`, `runtime.py`, or `cli.py`. It drives
`LLMImproviser.generate_scene()` directly, over and over, and writes each
successful take to disk as data under `<pack>/generated/`, well outside
`scenes/`, `cast/`, and `lore/` — `load_pack`/`validate_pack` never see it,
so the live graph is untouched.

`generate_scene()` never raises (see improviser.py) — it returns `[]` on any
failure. This script's only job on top of that is retrying a handful of
times before giving up on a take, and bookkeeping (take numbering, a
manifest) so runs are resumable and inspectable.
"""
import argparse
import datetime
import json
import logging
import sys
from pathlib import Path

import yaml

from campaign.improviser import LLMImproviser
from campaign.pack import PackError, load_pack
from llm_client import LLMError, build_llm_client

log = logging.getLogger(__name__)


def main(argv=None, out=None) -> int:
    """Run the batch generation script."""
    if argv is None:
        argv = sys.argv[1:]
    if out is None:
        out = sys.stdout

    args = build_parser().parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s", stream=sys.stderr)

    log.debug("parsed arguments: %s", args)

    try:
        pack = load_pack(args.pack)
    except PackError as exc:
        out.write(f"ERROR: {exc}\n")
        return 1

    try:
        config = _load_config(args.config)
    except OSError as exc:
        out.write(f"ERROR: failed to load {args.config}: {exc}\n")
        return 1
    except yaml.YAMLError as exc:
        out.write(f"ERROR: failed to parse {args.config}: {exc}\n")
        return 1

    try:
        llm = build_llm_client(config)
    except LLMError as exc:
        out.write(f"ERROR: {exc}\n")
        return 1

    improv_config = config.get("improv", {})
    improviser = LLMImproviser(
        pack,
        llm=llm,
        max_recent=improv_config.get("max_recent", 8),
        max_words=improv_config.get("max_words", 45),
        max_beats=improv_config.get("max_beats", 12),
    )

    if args.scenes:
        scene_ids = [s.strip() for s in args.scenes.split(",") if s.strip()]
        for scene_id in scene_ids:
            if scene_id not in pack.scenes:
                out.write(f"ERROR: no scene {scene_id!r}\n")
                return 1
    else:
        scene_ids = pack.ambient_scene_ids()

    if not scene_ids:
        out.write("no ambient scenes to generate\n")
        return 0

    out_dir = Path(args.out_dir) if args.out_dir else pack.root / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.jsonl"

    model = config.get("llm", {}).get("model", "")

    total_takes = 0
    total_words = 0

    for scene_id in scene_ids:
        scene = pack.scene(scene_id)
        if not scene.ambient:
            log.warning("scene %s is not ambient-flagged; generating anyway", scene_id)

        scene_dir = out_dir / scene_id
        scene_dir.mkdir(parents=True, exist_ok=True)
        start_take = _next_take_number(scene_dir)

        for offset in range(args.takes_per_scene):
            take = start_take + offset
            beats = _generate_take(improviser, scene, take, args.max_attempts)
            if not beats:
                log.warning("scene %s take %d: no usable content after %d attempt(s), skipping",
                            scene_id, take, args.max_attempts)
                continue

            word_count = sum(len((b.get("text") or "").split()) for b in beats)
            generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

            take_path = scene_dir / f"{take:03d}.yaml"
            _write_take(take_path, scene_id, take, model, generated_at, beats)

            _append_manifest(manifest_path, {
                "scene_id": scene_id,
                "take": take,
                "word_count": word_count,
                "beat_count": len(beats),
                "model": model,
                "timestamp": generated_at,
            })

            total_takes += 1
            total_words += word_count
            log.info("scene %s take %d: %d beats, %d words", scene_id, take, len(beats), word_count)

    out.write(f"wrote {total_takes} take(s), {total_words} word(s) total, to {out_dir}\n")
    log.info("batch generation complete: %d takes, %d words", total_takes, total_words)
    return 0


def _load_config(path) -> dict:
    """Load a batch config YAML file."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def _next_take_number(scene_dir: Path) -> int:
    """Find the next unused take number in a scene's output directory."""
    existing = []
    for p in scene_dir.glob("*.yaml"):
        try:
            existing.append(int(p.stem))
        except ValueError:
            continue
    return max(existing, default=0) + 1


def _generate_take(improviser: LLMImproviser, scene, take: int, max_attempts: int) -> list[dict]:
    """Generate one take of a scene, retrying up to max_attempts on empty output.

    Each take is a standalone airing: no transcript carries over between
    takes, and each attempt sees a different `loop` value so repeated
    attempts (and repeated takes) aren't handed identical context.
    """
    for attempt in range(1, max_attempts + 1):
        improviser.recent = []
        improviser.update_context(scene=scene, loop=take, carry={})
        beats = improviser.generate_scene(scene)
        if beats:
            return [_beat_to_dict(b) for b in beats]
        log.debug("scene %s take %d: attempt %d/%d produced nothing",
                  scene.id, take, attempt, max_attempts)
    return []


def _beat_to_dict(beat) -> dict:
    """Reduce a generated Beat to the fields worth persisting."""
    return {
        "kind": beat.kind,
        "speaker": beat.speaker,
        "text": beat.text,
    }


def _write_take(path: Path, scene_id: str, take: int, model: str, generated_at: str,
                 beats: list[dict]) -> None:
    """Write one generated take to disk as YAML."""
    data = {
        "id": scene_id,
        "take": take,
        "model": model,
        "generated_at": generated_at,
        "beats": beats,
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def _append_manifest(manifest_path: Path, entry: dict) -> None:
    """Append one line to the manifest file."""
    with open(manifest_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the batch generation script."""
    parser = argparse.ArgumentParser(
        description="Offline batch generation of ambient-scene takes. "
                     "Writes pre-rendered content to <pack>/generated/; "
                     "never touches the live runtime path."
    )
    parser.add_argument("--pack", default="campaigns/ashiorid",
                         help="Path to the campaign pack directory. Default campaigns/ashiorid.")
    parser.add_argument("--config", default="config/campaigns/ashiorid_batch.yaml",
                         help="Path to the batch LLM config. Default config/campaigns/ashiorid_batch.yaml.")
    parser.add_argument("--scenes", help="Comma-separated scene ids. Default: all ambient scenes in the pack.")
    parser.add_argument("--takes-per-scene", type=int, default=5,
                         help="How many takes to generate per scene this run. Default 5.")
    parser.add_argument("--max-attempts", type=int, default=2,
                         help="Retries per take before skipping it. Default 2.")
    parser.add_argument("--out-dir", help="Output directory. Default <pack>/generated/.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Turn on DEBUG logging to stderr.")

    return parser


if __name__ == "__main__":
    sys.exit(main())
