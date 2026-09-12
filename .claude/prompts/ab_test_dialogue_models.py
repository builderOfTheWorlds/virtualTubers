"""One-off A/B test: compare hermes3:70b vs qwen3.8:27b on the exact same
ambient-scene generation prompt the real pipeline builds (LLMImproviser.
generate_scene), using campaigns/ashiorid_1's real 'camp-fire' ambient scene
and cast. Not wired into the generator - just prints both outputs so a human
can judge quality before touching generation.yaml.

Run: python3 .claude/prompts/ab_test_dialogue_models.py
"""
import pathlib
import sys
import time

REPO_ROOT = pathlib.Path("/home/secus/codeProjects/virtualTubers")
UTIL_ROOT = REPO_ROOT / "utilities" / "3LayersWeeklyGeneration"

for p in (REPO_ROOT / "app", UTIL_ROOT / "src"):
    sys.path.insert(0, str(p))

from campaign.pack import load_pack, Scene  # noqa: E402
from campaign.improviser import LLMImproviser  # noqa: E402
import concurrent_llm  # noqa: E402

pack = load_pack(REPO_ROOT / "campaigns" / "ashiorid_1")
scene_data = pack.scenes["camp-fire"]
scene = Scene(id=scene_data.id, prompt=scene_data.prompt, lore=scene_data.lore)

MODELS = ["hermes3:70b", "qwen3.8:27b"]

for model in MODELS:
    print(f"\n{'='*70}\nMODEL: {model}\n{'='*70}")
    llm = concurrent_llm.from_profile({
        "provider": "ollama",
        "base_url": "http://localhost:11434",
        "model": model,
        "temperature": 0.9,
        "max_tokens": 1024,
        "timeout_s": 300,
        "num_ctx": 8192,
    })
    improv = LLMImproviser(pack, llm)
    improv.update_context(scene=scene, loop=1, carry={})
    t0 = time.time()
    beats = improv.generate_scene(scene)
    dt = time.time() - t0
    print(f"-- {len(beats)} beats generated in {dt:.1f}s --")
    for b in beats:
        print(f"[{b.kind}] {b.speaker}: {b.text}")
    llm.close()
