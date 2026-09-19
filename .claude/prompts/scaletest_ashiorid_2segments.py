"""Scale-up probe: run Layer 1 (arc, ring-enabled) once against
generation.ashiorid_scaletest.yaml, then run Layer 2+3 for exactly 2
representative segments (one keystone, one descent) at ~5x/15x the demo
config's target_words/slots/takes_per_slot. Purpose: measure per-segment
wall-clock time and content quality at closer-to-production scale BEFORE
committing to a full 28-segment x full-production-scale run.
"""
import logging
import pathlib
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

REPO_ROOT = pathlib.Path("/home/secus/codeProjects/virtualTubers")
UTIL_ROOT = REPO_ROOT / "utilities" / "3LayersWeeklyGeneration"

for p in (REPO_ROOT / "app", UTIL_ROOT / "src"):
    sys.path.insert(0, str(p))

import config as config_module  # noqa: E402
from campaign.pack import load_pack  # noqa: E402
from vocabulary import Vocabulary  # noqa: E402
import concurrent_llm  # noqa: E402
import yaml  # noqa: E402
import plan_arc  # noqa: E402
import plan_segment  # noqa: E402
import generate_segment_dialogue  # noqa: E402

CONFIG_PATH = UTIL_ROOT / "config" / "generation.ashiorid_scaletest.yaml"
config = config_module.load_config(CONFIG_PATH)
pack_path = REPO_ROOT / config["campaign"]["pack"]
pack = load_pack(pack_path)
vocab = Vocabulary.from_config_and_pack(config, pack)

out_root = config_module.output_root(config, str(pack_path))
out_root.mkdir(parents=True, exist_ok=True)
print(f"output root: {out_root}")

arc_llm = concurrent_llm.from_profile(config_module.resolve_profile(config, "arc"))
seg_llm = concurrent_llm.from_profile(config_module.resolve_profile(config, "segment"))
dlg_llm = concurrent_llm.from_profile(config_module.resolve_profile(config, "dialogue"))

t0 = time.time()
arc_plan_path = config_module.arc_plan_path(config, str(pack_path))
print("=== LAYER 1: planning arc (scale-test config) ===")
arc = plan_arc.plan_arc(pack, config, arc_llm, vocab, arc_plan_path)
print(f"arc segments planned: {len(arc['segments'])}  ({time.time()-t0:.1f}s)")

by_role = {}
for seg in arc["segments"]:
    role = seg.get("plot_path", [{}])[0].get("role", "?")
    by_role.setdefault(role, []).append(seg)

probe_segments = []
if by_role.get("keystone"):
    probe_segments.append(by_role["keystone"][0])
if by_role.get("descent"):
    probe_segments.append(by_role["descent"][0])
print(f"\nprobe segments: {[s['id'] for s in probe_segments]}")

print("\n=== LAYER 2: planning segment briefs (scale-test) ===")
for seg in probe_segments:
    t1 = time.time()
    brief_path = config_module.brief_path(config, str(pack_path), seg["id"])
    brief = plan_segment.plan_segment(pack, seg, config, seg_llm, vocab, brief_path)
    n_slots = len(brief["slots"]) if brief else 0
    print(f"  {seg['id']:<24s} -> {n_slots} slots ({time.time()-t1:.1f}s) "
          f"needs_rebrief={brief.get('needs_rebrief') if brief else None}")

print("\n=== LAYER 3: generating dialogue takes (scale-test) ===")
t2 = time.time()
segment_ids = [s["id"] for s in probe_segments]
stats = generate_segment_dialogue.generate_segment_dialogue(
    pack, segment_ids, config, dlg_llm, out_root,
    progress=lambda done, total, sid: print(f"  segment {done}/{total} done: {sid} "
                                             f"({time.time()-t2:.1f}s elapsed)")
)
print(f"\ndialogue stats: {stats}  ({time.time()-t2:.1f}s)")
print(f"\nTOTAL TIME for 2-segment probe: {time.time()-t0:.1f}s")

total_words = 0
for seg in probe_segments:
    slots_dir = out_root / "segments" / seg["id"] / "slots"
    if slots_dir.exists():
        for slot_dir in slots_dir.iterdir():
            for take_path in slot_dir.glob("*.yaml"):
                take = yaml.safe_load(take_path.read_text(encoding="utf-8"))
                for beat in take.get("beats", []):
                    total_words += len(str(beat.get("text", "")).split())
print(f"words generated across {len(probe_segments)} probe segments: {total_words}")
print(f"extrapolated to 28 segments at this rate: {total_words / len(probe_segments) * 28:.0f} words")
