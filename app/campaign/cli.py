"""Command-line interface for running campaign packs locally."""
import argparse
import logging
import sys

from campaign.pack import load_pack, PackError
from campaign.renderer import SceneRenderer, RendererError
from campaign.runtime import CampaignRuntime, CampaignRuntimeError
from campaign.scene_graph import ForcedSelector, WeightedRandomSelector
from campaign.validator import validate_pack
from replay import Pacer, Palette

log = logging.getLogger(__name__)


def main(argv=None, out=None) -> int:
    """Run the campaign CLI."""
    if argv is None:
        argv = sys.argv[1:]
    if out is None:
        out = sys.stdout

    args = build_parser().parse_args(argv)

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s", stream=sys.stderr)

    log.debug("parsed arguments: %s", args)

    # Load the pack
    try:
        pack = load_pack(args.pack)
    except PackError as exc:
        out.write(f"ERROR: {exc}\n")
        return 1

    # Validate the pack
    report = validate_pack(pack)
    if not report.ok:
        for error in report.errors:
            out.write(f"ERROR: {error}\n")
        return 1

    # Print validation report if requested
    if args.validate:
        for warning in report.warnings:
            out.write(f"WARNING: {warning}\n")
        out.write("ok\n")
        return 0

    # List scenes if requested
    if args.list_scenes:
        for scene_id in sorted(pack.scenes.keys()):
            out.write(f"{scene_id}\n")
        return 0

    # Build renderer and runtime
    pacer = Pacer(enabled=False) if args.no_pace else Pacer()
    palette = Palette(enabled=False) if args.no_color else Palette()

    renderer = SceneRenderer(
        pack,
        out=out,
        pacer=pacer,
        palette=palette,
    )

    selector = None
    if args.force_branch:
        selector = ForcedSelector(args.force_branch)
    elif args.seed is not None:
        selector = WeightedRandomSelector(seed=args.seed)

    runtime = CampaignRuntime(
        pack,
        renderer=renderer,
        selector=selector,
    )

    # Handle resume
    if args.resume:
        if not args.state_file:
            out.write("ERROR: --resume requires --state-file\n")
            return 1
        try:
            runtime.load(args.state_file)
        except CampaignRuntimeError as exc:
            out.write(f"ERROR: {exc}\n")
            return 1

    # Handle scene start
    if args.scene:
        if args.scene not in pack.scenes:
            out.write(f"ERROR: no scene {args.scene!r}\n")
            return 1
        runtime.state.scene_id = args.scene

    # Play the campaign
    try:
        runtime.run(max_scenes=args.max_scenes)
    except (RendererError, CampaignRuntimeError) as exc:
        log.error("campaign %s stopped: %s", pack.name, exc)
        out.write(f"ERROR: {exc}\n")
        return 1

    # Save state if requested
    if args.state_file:
        try:
            runtime.save(args.state_file)
        except CampaignRuntimeError as exc:
            out.write(f"ERROR: {exc}\n")
            return 1

    log.info("campaign %s played to completion", pack.name)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the campaign CLI."""
    parser = argparse.ArgumentParser(
        description="Play a campaign pack locally without Kafka, tmux, docker or TTS."
    )
    parser.add_argument("--pack", required=True, help="Path to the campaign pack directory.")
    parser.add_argument("--scene", help="Start at this scene instead of the pack's start_scene.")
    parser.add_argument("--validate", action="store_true", help="Validate the pack and print the report.")
    parser.add_argument("--list-scenes", action="store_true", help="Print every scene id, do not play.")
    parser.add_argument("--dry-run", action="store_true", help="Play with no side effects.")
    parser.add_argument("--force-branch", help="Use ForcedSelector(ID) for every branch.")
    parser.add_argument("--seed", type=int, help="Use WeightedRandomSelector(seed=N) instead of the default ScriptedSelector.")
    parser.add_argument("--max-scenes", type=int, default=100, help="Scene budget passed to runtime.run(). Default 100.")
    parser.add_argument("--state-file", help="Save the final position here after playing.")
    parser.add_argument("--resume", action="store_true", help="Load the position from --state-file before playing.")
    parser.add_argument("--no-pace", action="store_true", help="Disable all typing delays (Pacer(enabled=False)).")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colour (Palette(enabled=False)).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Turn on DEBUG logging to stderr.")

    return parser


if __name__ == "__main__":
    sys.exit(main())
