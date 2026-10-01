"""Audit every take in a run against its slot. Read-only.

Usage: python3 audit_takes.py <run_dir>
Flags per take: speakers outside the slot's participants, silent cast
(speech: None / never speaks) given dialogue, refusal text, and empty takes.
Prints per-segment counts, word totals, and the offending slot ids.
"""
import glob
import pathlib
import re
import sys

import yaml

REFUSAL = re.compile(r"\b(I (?:will|can)(?: ?not|'t) (?:generate|write|roleplay|produce|create|help)"
                     r"|as an AI|I'm (?:sorry|unable)|I am (?:sorry|unable)|I hope you understand)\b",
                     re.IGNORECASE)

run = pathlib.Path(sys.argv[1])
pack_root = pathlib.Path("/home/secus/codeProjects/virtualTubers/campaigns/ashiorid_office")
silent = set()
for f in (pack_root / "cast").glob("*.yaml"):
    speech = str((yaml.safe_load(f.read_text()) or {}).get("speech") or "").strip().lower()
    if speech.startswith("none") or "never speaks" in speech:
        silent.add(f.stem)

plan = yaml.safe_load((run / "arc_plan.yaml").read_text())["segments"]
grand = {"takes": 0, "words": 0, "bad": 0}
for seg in sorted(plan, key=lambda s: s["order"]):
    sid = seg["id"]
    brief = yaml.safe_load((run / "segments" / sid / "brief.yaml").read_text())
    slots = {s["slot_id"]: s for s in brief.get("slots") or []}
    issues = {"outside_cast": [], "silent_spoke": [], "refusal": [], "empty": []}
    takes = sorted(glob.glob(str(run / "segments" / sid / "slots" / "*" / "*.yaml")))
    words = 0
    for f in takes:
        slot_id = pathlib.Path(f).parent.name
        allowed = set((slots.get(slot_id) or {}).get("participants") or [])
        beats = (yaml.safe_load(open(f)) or {}).get("beats") or []
        if not beats:
            issues["empty"].append(slot_id)
        for b in beats:
            text = str(b.get("text", "")).split("||")[0]
            words += len(text.split())
            spk = b.get("speaker")
            if b.get("kind") == "dialogue" and allowed and spk not in allowed:
                issues["outside_cast"].append(f"{slot_id}:{spk}")
            if b.get("kind") == "dialogue" and spk in silent:
                issues["silent_spoke"].append(f"{slot_id}:{spk}")
            if REFUSAL.search(text):
                issues["refusal"].append(slot_id)
    bad_slots = {i.split(":")[0] for v in issues.values() for i in v}
    grand["takes"] += len(takes)
    grand["words"] += words
    grand["bad"] += len(bad_slots)
    print(f"== {sid}: takes={len(takes)} words={words} bad_slots={len(bad_slots)}")
    for kind, items in issues.items():
        if items:
            print(f"   {kind}: {sorted(set(items))}")
print(f"TOTAL takes={grand['takes']} words={grand['words']} (~{grand['words'] / 150:.1f} min) "
      f"bad_slots={grand['bad']} silent_cast={sorted(silent)}")
