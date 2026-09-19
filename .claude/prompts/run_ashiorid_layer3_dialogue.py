"""One-off driver: run Layer 3 (dialogue generation) for every segment that
has a usable Layer 2 brief under campaigns/ashiorid_1, using
generation.ashiorid_continuation.yaml. This produces the actual spoken-word
takes that TTS turns into stream audio -- the last of the three layers.
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
import generate_segment_dialogue  # noqa: E402

CONFIG_PATH = UTIL_ROOT / "config" / "generation.ashiorid_continuation.yaml"
config = config_module.load_config(CONFIG_PATH)
pack_path = REPO_ROOT / config["campaign"]["pack"]
pack = load_pack(pack_path)
vocab = Vocabulary.from_config_and_pack(config, pack)

arc_plan_path = config_module.arc_plan_path(config, str(pack_path))
arc = yaml.safe_load(arc_plan_path.read_text(encoding="utf-8"))
arc_segments = sorted(arc["segments"], key=lambda s: s["order"])

out_root = config_module.output_root(config, str(pack_path))
segments_dir = out_root / "segments"

segment_ids = []
for seg in arc_segments:
    brief_path = segments_dir / seg["id"] / "brief.yaml"
    if brief_path.exists():
        brief = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
        if brief.get("slots"):
            segment_ids.append(seg["id"])

print(f"segments with usable briefs: {len(segment_ids)} / {len(arc_segments)}")
print(segment_ids)

dlg_llm = concurrent_llm.from_profile(config_module.resolve_profile(config, "dialogue"))

t0 = time.time()
print("\n=== LAYER 3: generating dialogue takes ===")
stats = generate_segment_dialogue.generate_segment_dialogue(
    pack, segment_ids, config, dlg_llm, out_root,
    progress=lambda done, total, sid: print(f"  segment {done}/{total} done: {sid} "
                                             f"({time.time()-t0:.1f}s elapsed)")
)
print(f"\ndialogue stats: {stats}")
print(f"TOTAL TIME: {time.time()-t0:.1f}s")
