"""One-off driver: run Layer 1 (arc planning) only, against
campaigns/ashiorid_1 using generation.ashiorid_continuation.yaml with the
new ring-composition wiring (plot_0, 16/4/8) and real local Ollama. Lives in
.claude/prompts/ per project convention for one-off generation scripts.
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

CONFIG_PATH = UTIL_ROOT / "config" / "generation.ashiorid_continuation.yaml"

config = config_module.load_config(CONFIG_PATH)
pack_path = REPO_ROOT / config["campaign"]["pack"]
pack = load_pack(pack_path)
vocab = Vocabulary.from_config_and_pack(config, pack)

out_root = config_module.output_root(config, str(pack_path))
out_root.mkdir(parents=True, exist_ok=True)
print(f"output root: {out_root}")

arc_llm = concurrent_llm.from_profile(config_module.resolve_profile(config, "arc"))

t0 = time.time()
arc_plan_path = config_module.arc_plan_path(config, str(pack_path))
# Force a fresh run rather than resuming a pre-ring plan on disk.
if arc_plan_path.exists():
    backup = arc_plan_path.with_suffix(".yaml.pre_ring_bak")
    arc_plan_path.rename(backup)
    print(f"moved existing arc_plan.yaml -> {backup}")

print("=== LAYER 1: planning arc (ring-composition enabled) ===")
arc = plan_arc.plan_arc(pack, config, arc_llm, vocab, arc_plan_path)
print(f"arc segments planned: {len(arc['segments'])}  ({time.time()-t0:.1f}s)")
for seg in arc["segments"]:
    role = seg.get("plot_path", [{}])[0].get("role", "?")
    mirror = seg.get("mirror_of", [])
    transform = seg.get("mirror_transform")
    print(f"  order={seg['order']:2d} role={role:<9s} id={seg['id']:<28s} "
          f"mirror_of={mirror} transform={transform}")
