"""Rebrief pass: retry Layer 2 (segment briefs) for every segment currently
flagged needs_rebrief: true (thin briefs, well under target_slots) or with an
empty slots list. Old brief dirs are renamed aside with a timestamped suffix
so plan_segment's resume check does not short-circuit on the stale brief.
"""
import logging
import pathlib
import shutil
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
import plan_segment  # noqa: E402

CONFIG_PATH = UTIL_ROOT / "config" / "generation.ashiorid_continuation.yaml"
config = config_module.load_config(CONFIG_PATH)
pack_path = REPO_ROOT / config["campaign"]["pack"]
pack = load_pack(pack_path)
vocab = Vocabulary.from_config_and_pack(config, pack)

arc_plan_path = config_module.arc_plan_path(config, str(pack_path))
arc = yaml.safe_load(arc_plan_path.read_text(encoding="utf-8"))
arc_segments = {s["id"]: s for s in arc["segments"]}

out_root = config_module.output_root(config, str(pack_path))
segments_dir = out_root / "segments"

target_slots = config["segment"]["target_slots"]
density_floor = config["segment"]["density_floor"]
threshold = target_slots * density_floor

to_rebrief = []
for seg_id, arc_seg in arc_segments.items():
    brief_path = segments_dir / seg_id / "brief.yaml"
    if not brief_path.exists():
        to_rebrief.append(seg_id)
        continue
    brief = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
    n_slots = len(brief.get("slots", []))
    if brief.get("needs_rebrief") or n_slots < threshold:
        to_rebrief.append(seg_id)

print(f"rebriefing {len(to_rebrief)} segments (threshold {threshold} slots): {to_rebrief}")

stamp = time.strftime("%Y%m%d_%H%M%S")
for seg_id in to_rebrief:
    seg_dir = segments_dir / seg_id
    if seg_dir.exists():
        backup = segments_dir / f"{seg_id}.rebrief_bak_{stamp}"
        shutil.move(str(seg_dir), str(backup))

seg_llm = concurrent_llm.from_profile(config_module.resolve_profile(config, "segment"))

t0 = time.time()
results = []
for seg_id in to_rebrief:
    t1 = time.time()
    arc_seg = arc_segments[seg_id]
    brief_path = config_module.brief_path(config, str(pack_path), seg_id)
    brief = plan_segment.plan_segment(pack, arc_seg, config, seg_llm, vocab, brief_path)
    n_slots = len(brief["slots"]) if brief else 0
    needs_rebrief = brief.get("needs_rebrief") if brief else None
    print(f"  {seg_id:<28s} -> {n_slots} slots ({time.time()-t1:.1f}s) "
          f"needs_rebrief={needs_rebrief}")
    results.append((seg_id, n_slots, needs_rebrief))

print(f"\nTOTAL TIME: {time.time()-t0:.1f}s")
still_thin = [r for r in results if r[1] < threshold]
print(f"still thin after rebrief: {still_thin}")
