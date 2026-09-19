"""One-off driver: run Layer 2 (segment briefs) for every segment in the
already-planned arc_plan.yaml under campaigns/ashiorid_1, using
generation.ashiorid_continuation.yaml. Layer 1 (ring-composition arc plan,
28/28 segments) is already complete on disk; this expands each arc segment
into a detailed slot-level brief that Layer 3 turns into dialogue.
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
import plan_segment  # noqa: E402

CONFIG_PATH = UTIL_ROOT / "config" / "generation.ashiorid_continuation.yaml"

config = config_module.load_config(CONFIG_PATH)
pack_path = REPO_ROOT / config["campaign"]["pack"]
pack = load_pack(pack_path)
vocab = Vocabulary.from_config_and_pack(config, pack)

arc_plan_path = config_module.arc_plan_path(config, str(pack_path))
arc = yaml.safe_load(arc_plan_path.read_text(encoding="utf-8"))
arc_segments = sorted(arc["segments"], key=lambda s: s["order"])
print(f"loaded arc plan: {len(arc_segments)} segments")

seg_llm = concurrent_llm.from_profile(config_module.resolve_profile(config, "segment"))

t0 = time.time()
print("=== LAYER 2: planning segment briefs ===")
ok, failed = [], []
for arc_seg in arc_segments:
    t1 = time.time()
    brief_path = config_module.brief_path(config, str(pack_path), arc_seg["id"])
    try:
        brief = plan_segment.plan_segment(pack, arc_seg, config, seg_llm, vocab, brief_path)
        n_slots = len(brief["slots"]) if brief else 0
        print(f"  order={arc_seg['order']:2d} {arc_seg['id']:<28s} -> {n_slots} slots "
              f"({time.time()-t1:.1f}s)")
        if brief and n_slots:
            ok.append(arc_seg["id"])
        else:
            failed.append(arc_seg["id"])
    except Exception as exc:
        print(f"  order={arc_seg['order']:2d} {arc_seg['id']:<28s} -> FAILED: {exc}")
        failed.append(arc_seg["id"])

print(f"\nsegments with usable briefs: {len(ok)} / {len(arc_segments)}")
if failed:
    print(f"failed/empty: {failed}")
print(f"TOTAL TIME: {time.time()-t0:.1f}s")
