#!/usr/bin/env python3
"""Unpack an external LLM's campaign-pack reply into a pack dir and validate it.

Companion to .claude/prompts/external_llm_pack_authoring_prompt.md. The
reply is a sequence of blocks:

    === FILE: relative/path ===
    ...contents...
    === END FILE ===

Steps:
  1. Parse blocks (tolerates stray ``` fences); reject unsafe paths.
  2. Write them to a temp dir and run the real loader + validator
     (app/campaign/pack.load_pack, app/campaign/validator.validate_pack).
  3. Extra checks validate_pack doesn't do: primitive params against the
     registry, voice/emotion vocabularies, silent-speaker dialogue, spine
     word counts, ambient count, generation_hints consistency.
  4. With --write, copy into campaigns/<name>/ (refuses to overwrite an
     existing pack unless --force).

Usage:
    .venv/bin/python .claude/prompts/unpack_llm_pack.py reply.txt
    .venv/bin/python .claude/prompts/unpack_llm_pack.py reply.txt --write
    .venv/bin/python .claude/prompts/unpack_llm_pack.py reply.txt --write --force

Exit code 0 = no errors (warnings allowed), 1 = errors, 2 = bad input.
"""
import argparse
import logging
import pathlib
import re
import shutil
import sys
import tempfile

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))

from campaign import primitives as prim_mod  # noqa: E402
from campaign.pack import PackError, load_pack  # noqa: E402
from campaign.validator import validate_pack  # noqa: E402
from emotion import EMOTIONS  # noqa: E402

log = logging.getLogger("unpack_llm_pack")

BLOCK_RE = re.compile(
    r"^===\s*FILE:\s*(?P<path>[^=\n]+?)\s*===\s*\n(?P<body>.*?)^===\s*END FILE\s*===\s*$",
    re.MULTILINE | re.DOTALL,
)
ALLOWED_PATH_RE = re.compile(
    r"^(campaign\.yaml|generation_hints\.yaml|cast/[a-z0-9_]+\.yaml|"
    r"scenes/[a-z0-9_.-]+\.ya?ml|lore/[a-z0-9_-]+\.md)$"
)
NEVER_SPEAKS_RE = re.compile(r"you never speak", re.IGNORECASE)
SPINE_WORDS_MIN, SPINE_WORDS_MAX = 110, 300
AMBIENT_MIN = 15


def load_voices() -> set:
    """Voice keys from config/voices.yaml `voices:`."""
    path = REPO / "config" / "voices.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return set((data.get("voices") or {}).keys())
    except (OSError, yaml.YAMLError) as exc:
        log.warning("could not read %s: %s", path, exc)
        return set()


def parse_blocks(text: str) -> dict:
    """Return {relpath: contents}. Raises ValueError on unsafe/duplicate paths."""
    text = re.sub(r"^```[a-zA-Z]*\s*$", "", text, flags=re.MULTILINE)
    files = {}
    for m in BLOCK_RE.finditer(text):
        rel = m.group("path").strip()
        if not ALLOWED_PATH_RE.match(rel):
            raise ValueError(f"unsafe or unexpected path in reply: {rel!r}")
        if rel in files:
            raise ValueError(f"duplicate file block: {rel}")
        files[rel] = m.group("body").rstrip() + "\n"
    return files


def _words(texts) -> int:
    return sum(len(t.split()) for t in texts if isinstance(t, str))


def extra_checks(pack, root: pathlib.Path, voices: set) -> tuple:
    """Checks validate_pack() does not perform. Returns (errors, warnings)."""
    errors, warnings = [], []
    silent = {cid for cid, m in pack.cast.items()
              if m.system_prompt and NEVER_SPEAKS_RE.search(m.system_prompt)}

    for cid, member in pack.cast.items():
        if voices and member.voice not in voices:
            errors.append(f"cast {cid!r}: voice {member.voice!r} not in config/voices.yaml")
        if not member.system_prompt:
            errors.append(f"cast {cid!r}: missing system_prompt")
        elif not 100 <= len(member.system_prompt.split()) <= 450:
            warnings.append(f"cast {cid!r}: system_prompt is "
                            f"{len(member.system_prompt.split())} words (target 150-350)")

    for p in pack.primitives:
        if p not in prim_mod.DEFAULT_REGISTRY:
            errors.append(f"campaign.yaml: primitive {p!r} is not in the registry")

    n_ambient = 0
    for scene in pack.scenes.values():
        if scene.ambient:
            n_ambient += 1
            if scene.beats:
                warnings.append(f"ambient scene {scene.id!r} has beats (expected prompt only)")
            if scene.prompt and "no new plot" not in scene.prompt.lower():
                warnings.append(f"ambient scene {scene.id!r}: prompt lacks 'No new plot facts'")
            continue
        spoken = []
        for i, beat in enumerate(scene.beats):
            where = f"scene {scene.id!r} beat {i}"
            if beat.kind == "dialogue" and beat.speaker in silent:
                errors.append(f"{where}: dialogue for silent cast member {beat.speaker!r}")
            if beat.emotion is not None and beat.emotion not in EMOTIONS:
                errors.append(f"{where}: emotion {beat.emotion!r} not in {list(EMOTIONS)}")
            if beat.kind == "action" and beat.primitive in prim_mod.DEFAULT_REGISTRY:
                try:
                    prim_mod.render(beat.primitive, beat.speaker or "someone", beat.params)
                except prim_mod.PrimitiveError as exc:
                    errors.append(f"{where}: {beat.primitive}: {exc}")
            if beat.kind in ("narration", "dialogue"):
                spoken.append(beat.text or "")
                for t in beat.texts:
                    if re.search(r"[*_]{1,2}\w|\(\s*[a-z][^)]*\)", t or ""):
                        warnings.append(f"{where}: text looks like it has markdown/stage directions")
                        break
        words = _words(spoken) + _words([scene.enter_narration or ""])
        if words < SPINE_WORDS_MIN:
            errors.append(f"spine {scene.id!r}: {words} spoken words (< {SPINE_WORDS_MIN})")
        elif words > SPINE_WORDS_MAX * 1.5:
            warnings.append(f"spine {scene.id!r}: {words} spoken words (target <= {SPINE_WORDS_MAX})")
        if not scene.ring_tone:
            warnings.append(f"spine {scene.id!r}: no ring_tone")

    if n_ambient < AMBIENT_MIN:
        warnings.append(f"only {n_ambient} ambient scenes (target >= {AMBIENT_MIN})")

    unused = set(pack.lore) - {s for sc in pack.scenes.values() for s in sc.lore}
    for stem in sorted(unused):
        warnings.append(f"lore {stem!r} is never referenced by a scene")

    hints_path = root / "generation_hints.yaml"
    if not hints_path.exists():
        warnings.append("generation_hints.yaml missing (reply may be truncated)")
    else:
        try:
            hints = yaml.safe_load(hints_path.read_text(encoding="utf-8")) or {}
            state = hints.get("state") or {}
            flags = set(state.get("flags") or [])
            extra = set(state.get("carry_keys") or []) - flags
            if extra:
                errors.append(f"generation_hints: carry_keys not in flags: {sorted(extra)}")
            for ph in hints.get("day_phases") or []:
                for sid in ph.get("spine_scenes") or []:
                    if sid not in pack.scenes or pack.scenes[sid].ambient:
                        errors.append(f"generation_hints: day_phases spine {sid!r} is not a spine scene")
        except yaml.YAMLError as exc:
            errors.append(f"generation_hints.yaml: {exc}")
    return errors, warnings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("reply", type=pathlib.Path, help="file holding the LLM reply")
    ap.add_argument("--write", action="store_true", help="copy into campaigns/<name>/")
    ap.add_argument("--force", action="store_true", help="overwrite an existing pack dir")
    ap.add_argument("--dest-root", type=pathlib.Path, default=REPO / "campaigns")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s %(message)s")

    try:
        files = parse_blocks(args.reply.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    if "campaign.yaml" not in files:
        print(f"ERROR: no campaign.yaml block found ({len(files)} blocks parsed)")
        return 2
    print(f"parsed {len(files)} file blocks")

    with tempfile.TemporaryDirectory(prefix="llm_pack_") as tmp:
        root = pathlib.Path(tmp) / "pack"
        for rel, body in files.items():
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")

        try:
            pack = load_pack(root)
        except PackError as exc:
            print(f"ERROR (load_pack): {exc}")
            return 1

        report = validate_pack(pack)
        x_err, x_warn = extra_checks(pack, root, load_voices())
        errors = report.errors + x_err
        warnings = report.warnings + x_warn

        n_amb = len([s for s in pack.scenes.values() if s.ambient])
        print(f"pack {pack.name!r}: {len(pack.cast)} cast, "
              f"{len(pack.scenes) - n_amb} spine, {n_amb} ambient, {len(pack.lore)} lore")
        for w in warnings:
            print(f"WARN  {w}")
        for e in errors:
            print(f"ERROR {e}")
        if errors:
            print(f"\n{len(errors)} error(s). Paste them back to the model and ask it "
                  "to re-emit only the changed files.")
            return 1

        if not args.write:
            print("\nOK (dry run). Re-run with --write to install.")
            return 0
        target = args.dest_root / pack.name
        if target.exists():
            if not args.force:
                print(f"ERROR: {target} exists; use --force to overwrite")
                return 1
            shutil.rmtree(target)
        shutil.copytree(root, target)
        print(f"\nwrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
