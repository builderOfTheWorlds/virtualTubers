"""Batch A/B test: hermes3:70b vs qwen3.8:27b across several REAL ambient
scenes from campaigns/ashiorid_1, using the exact production code path
(LLMImproviser.generate_scene). Writes results to ab_results.json in this
directory and prints a scoring summary (parse failures = beats that landed
in narration instead of dialogue due to speaker-id case mismatch, or that
look truncated).

Run: python3 .claude/prompts/ab_test_batch.py > /tmp/ab_batch.log 2>&1
(run in background - ~10-15 scenes x 2 models x ~50s each can exceed 600s)
"""
import json
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

SCENE_IDS = [
    "camp-fire", "road-talk", "night-watch", "the-meal",
    "sodacan_bob-on-craft", "chadwick-lost",
]
MODELS = ["hermes3:70b", "qwen3.8:27b"]

cast_ids_lower = {cid.lower() for cid in pack.cast}

results = {}

for model in MODELS:
    print(f"\n{'='*70}\nMODEL: {model}\n{'='*70}", flush=True)
    llm = concurrent_llm.from_profile({
        "provider": "ollama",
        "base_url": "http://localhost:11434",
        "model": model,
        "temperature": 0.9,
        "max_tokens": 1024,
        "timeout_s": 300,
        "num_ctx": 8192,
    })
    model_results = []
    for scene_id in SCENE_IDS:
        scene_data = pack.scenes.get(scene_id)
        if scene_data is None:
            print(f"  !! scene {scene_id} not found, skipping")
            continue
        scene = Scene(id=scene_data.id, prompt=scene_data.prompt, lore=scene_data.lore)
        improv = LLMImproviser(pack, llm)
        improv.update_context(scene=scene, loop=1, carry={})
        t0 = time.time()
        beats = improv.generate_scene(scene)
        dt = time.time() - t0

        # Scoring heuristics:
        # - misattributed: a narration beat whose text looks like "name: ..." where
        #   name (case-insensitive) matches a cast id -> the parser missed it as
        #   dialogue due to case mismatch.
        # - truncated: beat text doesn't end in terminal punctuation.
        misattributed = 0
        truncated = 0
        for b in beats:
            if b.kind == "narration":
                prefix = b.text.split(":", 1)[0].strip().lower()
                if prefix in cast_ids_lower:
                    misattributed += 1
            if b.text and b.text[-1] not in ".!?\u2026\"'":
                truncated += 1

        word_count = sum(len(b.text.split()) for b in beats)
        print(f"  {scene_id}: {len(beats)} beats, {word_count} words, "
              f"{dt:.1f}s, misattributed={misattributed}, truncated={truncated}", flush=True)
        for b in beats:
            print(f"      [{b.kind}] {b.speaker}: {b.text}")

        model_results.append({
            "scene_id": scene_id,
            "n_beats": len(beats),
            "word_count": word_count,
            "seconds": dt,
            "misattributed": misattributed,
            "truncated": truncated,
            "beats": [{"kind": b.kind, "speaker": b.speaker, "text": b.text} for b in beats],
        })
    llm.close()
    results[model] = model_results

out_path = pathlib.Path(__file__).parent / "ab_results.json"
out_path.write_text(json.dumps(results, indent=2))
print(f"\nWrote {out_path}")

print(f"\n{'='*70}\nSUMMARY\n{'='*70}")
for model, rows in results.items():
    total_beats = sum(r["n_beats"] for r in rows)
    total_words = sum(r["word_count"] for r in rows)
    total_mis = sum(r["misattributed"] for r in rows)
    total_trunc = sum(r["truncated"] for r in rows)
    total_time = sum(r["seconds"] for r in rows)
    print(f"{model}: {len(rows)} scenes, {total_beats} beats, {total_words} words, "
          f"{total_time:.1f}s total, misattributed={total_mis}, truncated={total_trunc}")
