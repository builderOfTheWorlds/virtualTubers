"""Retry Layer 2 (segment briefs) for a specific list of segment ids whose
prior brief was empty (0 slots, needs_rebrief). Their old empty brief dirs
must already be moved/renamed aside so plan_segment's resume check does not
short-circuit on the stale brief.yaml.
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

RETRY_IDS = {"invitation-arc-alt2", "magic-retained-arc-alt2", "the-vault-arc-loop1"}

CONFIG_PATH = UTIL_ROOT / "config" / "generation.ashiorid_continuation.yaml"
config = config_module.load_config(CONFIG_PATH)
pack_path = REPO_ROOT / config["campaign"]["pack"]
pack = load_pack(pack_path)
vocab = Vocabulary.from_config_and_pack(config, pack)

arc_plan_path = config_module.arc_plan_path(config, str(pack_path))
arc = yaml.safe_load(arc_plan_path.read_text(encoding="utf-8"))
arc_segments = [s for s in arc["segments"] if s["id"] in RETRY_IDS]
print(f"retrying {len(arc_segments)} segments: {[s['id'] for s in arc_segments]}")

seg_llm = concurrent_llm.from_profile(config_module.resolve_profile(config, "segment"))

for arc_seg in arc_segments:
    t1 = time.time()
    brief_path = config_module.brief_path(config, str(pack_path), arc_seg["id"])
    brief = plan_segment.plan_segment(pack, arc_seg, config, seg_llm, vocab, brief_path)
    n_slots = len(brief["slots"]) if brief else 0
    print(f"  {arc_seg['id']:<24s} -> {n_slots} slots ({time.time()-t1:.1f}s) "
          f"needs_rebrief={brief.get('needs_rebrief') if brief else None}")
