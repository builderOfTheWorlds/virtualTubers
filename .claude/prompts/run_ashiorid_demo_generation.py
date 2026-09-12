"""One-off driver: run the 3-layer generator against campaigns/ashiorid_1
using generation.ashiorid_demo.yaml and real local Ollama models. Not part of
the package -- lives in .claude/prompts/ per project convention for one-off
generation scripts.
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
import plan_arc  # noqa: E402
import plan_segment  # noqa: E402
import generate_segment_dialogue  # noqa: E402

CONFIG_PATH = UTIL_ROOT / "config" / "generation.ashiorid_demo.yaml"

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
print("=== LAYER 1: planning arc ===")
arc = plan_arc.plan_arc(pack, config, arc_llm, vocab, arc_plan_path)
print(f"arc segments planned: {len(arc['segments'])}  ({time.time()-t0:.1f}s)")
for seg in arc["segments"]:
    print(f"  order={seg['order']:2d} id={seg['id']:<24s} spine={seg['spine_scenes']}")

print("\n=== LAYER 2: planning segment briefs ===")
segment_ids = []
for seg in arc["segments"]:
    t1 = time.time()
    brief_path = config_module.brief_path(config, str(pack_path), seg["id"])
    brief = plan_segment.plan_segment(pack, seg, config, seg_llm, vocab, brief_path)
    n_slots = len(brief["slots"]) if brief else 0
    print(f"  {seg['id']:<24s} -> {n_slots} slots  ({time.time()-t1:.1f}s)")
    if brief and n_slots:
        segment_ids.append(seg["id"])

print(f"\nsegments with usable briefs: {len(segment_ids)} / {len(arc['segments'])}")

print("\n=== LAYER 3: generating dialogue takes ===")
t2 = time.time()
stats = generate_segment_dialogue.generate_segment_dialogue(
    pack, segment_ids, config, dlg_llm, out_root,
    progress=lambda done, total, sid: print(f"  segment {done}/{total} done: {sid}")
)
print(f"dialogue stats: {stats}  ({time.time()-t2:.1f}s)")
print(f"\nTOTAL TIME: {time.time()-t0:.1f}s")
