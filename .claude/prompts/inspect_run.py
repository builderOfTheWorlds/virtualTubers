"""Summarise one generator run's segment briefs (and dialogue takes, if any).

Usage: python3 inspect_run.py <run_dir> [--takes N]
Prints per segment: slot count by kind, spine scene_refs, cast usage, the
ambient prompts, and (with --takes) word counts plus N sample take lines.
Read-only.
"""
import collections
import glob
import pathlib
import sys

import yaml

run = pathlib.Path(sys.argv[1])
n_samples = int(sys.argv[sys.argv.index("--takes") + 1]) if "--takes" in sys.argv else 0
plan = yaml.safe_load((run / "arc_plan.yaml").read_text())["segments"]

total_words = 0
for seg in sorted(plan, key=lambda s: s["order"]):
    sid = seg["id"]
    brief = yaml.safe_load((run / "segments" / sid / "brief.yaml").read_text())
    slots = brief.get("slots") or []
    kinds = collections.Counter(s.get("kind") for s in slots)
    spine = [s.get("scene_ref") for s in slots if s.get("kind") == "spine"]
    cast = collections.Counter(p for s in slots for p in s.get("participants") or [])
    print(f"== order {seg['order']} {sid}: {len(slots)} slots {dict(kinds)} "
          f"arc_spine={seg['spine_scenes']} slot_spine_refs={spine}")
    print(f"   cast: {dict(cast)}")
    for s in slots:
        if s.get("kind") == "ambient":
            print(f"   - [{s.get('sensitivity')}] {str(s.get('prompt'))[:130]}")

    if n_samples:
        takes = sorted(glob.glob(str(run / "segments" / sid / "slots" / "*" / "*.yaml")))
        words = 0
        lines = []
        for f in takes:
            for b in (yaml.safe_load(open(f)) or {}).get("beats") or []:
                text = str(b.get("text", "")).split("||")[0].strip()
                words += len(text.split())
                lines.append(f"{b.get('speaker')}: {text}")
        total_words += words
        print(f"   takes={len(takes)} words={words} (~{words / 150:.1f} min at 150 wpm)")
        for line in lines[:n_samples]:
            print(f"     > {line[:160]}")

if n_samples:
    print(f"TOTAL words={total_words} (~{total_words / 150:.1f} min spoken)")
