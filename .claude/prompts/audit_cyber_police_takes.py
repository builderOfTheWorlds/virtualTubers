"""Audit every batch-generated take in campaigns/cyber_police/generated/
against the pack's cast. Read-only. Mirrors audit_takes.py's checks
(speakers outside the roster, refusal text, empty takes) but adapted for
app/campaign/batch_generate.py's flat <scene>/<NNN>.yaml output instead of
the 3-layer generator's arc_plan/segments/slots tree.

Usage: python3 audit_cyber_police_takes.py
"""
import glob
import pathlib
import re
import sys

import yaml

REFUSAL = re.compile(
    r"\b(I (?:will|can)(?: ?not|'t) (?:generate|write|roleplay|produce|create|help)"
    r"|as an AI|I'm (?:sorry|unable)|I am (?:sorry|unable)|I hope you understand)\b",
    re.IGNORECASE)

PACK_ROOT = pathlib.Path("/home/secus/codeProjects/virtualTubers/campaigns/cyber_police")
GENERATED = PACK_ROOT / "generated"

cast_ids = {f.stem for f in (PACK_ROOT / "cast").glob("*.yaml")}
silent = set()
for f in (PACK_ROOT / "cast").glob("*.yaml"):
    data = yaml.safe_load(f.read_text()) or {}
    speech = str(data.get("speech") or "").strip().lower()
    if speech.startswith("silent") or "never speaks" in speech:
        silent.add(f.stem)

# 2026-10-02 additions (cyber_police_day1 dialogue audit): artefacts that
# reached air in the looped v1 build. These are counted, not fixed — the v2
# builder (build_cyber_police_full_episode.py clean_take) cleans/drops them.
LABEL = re.compile(r"^\s*[A-Za-z][\w.'\- ]{0,40}?(?:\s*\([^)]{0,40}\))?\s*:\s*")
STAGE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
EMOTION_RESIDUE = re.compile(r"\|\|")
OBSERVER_LABEL = re.compile(r"^\s*(?:the\s+)?observer\s*:", re.IGNORECASE)
FOREIGN = re.compile(r"\b(ashiorid|sodacan|moonwells?|begene)\b", re.IGNORECASE)
artefacts = {"label_in_text": 0, "gm_narration_voiced_by_captain_seat": 0,
             "stage_direction": 0, "emotion_residue": 0, "observer_label": 0,
             "foreign_pack_term": 0}
line_takes = {}

grand = {"takes": 0, "words": 0, "bad": 0, "empty": 0}
for scene_dir in sorted(GENERATED.iterdir()):
    if not scene_dir.is_dir():
        continue
    takes = sorted(scene_dir.glob("*.yaml"))
    if not takes:
        continue
    words = 0
    issues = {"outside_cast": [], "silent_spoke": [], "refusal": [], "empty": []}
    for f in takes:
        data = yaml.safe_load(f.read_text()) or {}
        beats = data.get("beats") or []
        if not beats:
            issues["empty"].append(f.stem)
            continue
        for b in beats:
            text = str(b.get("text", ""))
            words += len(text.split())
            spk = b.get("speaker")
            if b.get("kind") == "dialogue" and spk and spk not in cast_ids:
                issues["outside_cast"].append(f"{f.stem}:{spk}")
            if b.get("kind") == "dialogue" and spk in silent:
                issues["silent_spoke"].append(f"{f.stem}:{spk}")
            if REFUSAL.search(text):
                issues["refusal"].append(f.stem)
            label = LABEL.match(text)
            if label and len(label.group(0).split()) <= 6:
                artefacts["label_in_text"] += 1
            if b.get("kind") == "narration":
                artefacts["gm_narration_voiced_by_captain_seat"] += 1
            if STAGE.search(text):
                artefacts["stage_direction"] += 1
            if EMOTION_RESIDUE.search(text):
                artefacts["emotion_residue"] += 1
            if OBSERVER_LABEL.match(text):
                artefacts["observer_label"] += 1
                issues["silent_spoke"].append(f"{f.stem}:label")
            if FOREIGN.search(text):
                artefacts["foreign_pack_term"] += 1
            line_takes.setdefault(" ".join(text.lower().split()), set()).add(
                f"{scene_dir.name}/{f.stem}")
    bad = {i.split(":")[0] for v in issues.values() for i in v}
    grand["takes"] += len(takes)
    grand["words"] += words
    grand["bad"] += len(bad)
    grand["empty"] += len(issues["empty"])
    print(f"== {scene_dir.name}: takes={len(takes)} words={words} bad={len(bad)}")
    for kind, items in issues.items():
        if items:
            print(f"   {kind}: {sorted(set(items))[:10]}"
                  f"{' ...' if len(set(items)) > 10 else ''}")

print(f"\nTOTAL takes={grand['takes']} words={grand['words']} "
      f"(~{grand['words'] / 150:.1f} min at 150wpm) bad_takes={grand['bad']} "
      f"empty_takes={grand['empty']} silent_cast={sorted(silent)}")
print(f"artefacts (beats): {artefacts}")
dups = sorted(((len(v), k) for k, v in line_takes.items() if len(v) > 1), reverse=True)
print(f"lines appearing in >1 take: {len(dups)} distinct, "
      f"{sum(n - 1 for n, _ in dups)} extra copies; top: "
      f"{[(n, k[:40]) for n, k in dups[:8]]}")
print(f"airtime capacity at 150wpm: {grand['words'] / 150 / 60:.2f} h raw pool "
      f"(before cleaning/dedupe; the v2 builder reports the usable figure)")
