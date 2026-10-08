# Live agent table: model benchmark (2026-10-08)

GB10 (121 GiB unified), vLLM 0.30, one model at a time, gpu-mem-util 0.55, reasoning budgets
GM 384 / THINK 256 / SPEAK 48 / adjudication 64 tokens. Two-pass round. Raw records:
`utilities/benchmarker/output/candidates/<model>/record.json` (+ `REPORT.md`, slice JSONs).

Gate (P1.4 proposal): SPEAK p50 <= 15 s, THINK p95 <= 60 s, D&D round (4 seats) <= 180 s,
office round (7 seats) <= 300 s.

## Summary

| Model | Weights | Decode | 4-seat round | 7-seat round | Failed calls | Slice (2 scenes, GM+Chadwick) | Verdict |
|---|---|---|---|---|---|---|---|
| **Qwen3.6-35B-A3B-FP8** | 33 GiB | ~29 tok/s | 32 s | 38 s | 0 | 194 s, 2/2 resolved, 0 extra retakes | **Pick.** Fast, clean, varied lines |
| Nemotron-3.5-Lightning-30B-A3B NVFP4 | 18 GiB | ~33 tok/s | 32 s | 34 s | 0 | 82 s, 2/2 resolved, 3 retakes | Fastest; repeats itself, flatter voice |
| Qwen3-30B-A3B-Thinking-2507 FP8 | 29 GiB | ~43 tok/s | 37 s | 42 s | 0 | 176 s, 2/2 resolved, 3 repeat retakes | Fast but repetition-prone |
| Qwen3.8-27B-FP8 (dense) | 28 GiB | ~5 tok/s | 143 s | 164 s | 0 | timed out after 1 scene (GM slow) | Best writing, too slow |
| GLM-4.7-Flash FP8 | 31 GiB | ~27 tok/s | 67 s | 70 s | 22 of 26 failed/capped | 1/2 resolved, then stuck | **Reject:** GM pasted its secret truth block into the narration |
| gpt-oss-20b (MXFP4) | 14 GiB | ~16-26 tok/s | 48 s* | 60 s* | SPEAK empty* | did not resolve* | Unfinished: rerun with reasoning_effort=low |
| Hermes-4-14B FP8 | 15 GiB | ~10 tok/s | n/a | n/a | GM always empty | did not resolve | Unfinished: reasoning/template mismatch |
| Gemma-4-26B-A4B FP8 | n/a | n/a | n/a | n/a | n/a | n/a | Blocked: vLLM 0.30 image cannot load Gemma4 |

\* gpt-oss first runs used our token budgets, which its harmony reasoning ignores, so every
160-token SPEAK call spent its whole budget thinking. With `reasoning_effort: "low"` its smoke
test answered in 2.4 s with a clean line; the full rerun was stopped for the live test.

## Sample lines (same scene, Chadwick seat)

**Qwen3.6-35B-A3B-FP8**
- GM: The foyer stretches wide beneath the crystal lights. Three hundred guests wear silk and steel. A steward in grey livery approaches you…
- Chadwick: This is the way. Stand fast, I'll handle it.
- Chadwick: Keep your cups. This wine smells like iron and bad faith. Stand ready.
- Chadwick: This is the way. Draw steel and hold the line.

**Qwen3.8-27B-FP8** (richest, most grounded in the sheet)
- GM: The steward's bow does not break. "His Grace is in the east hall. He has been expecting you." … "The doors will close at the ninth bell."
- Chadwick: Where is Sorensen Leto? Speak plainly, or I will make you.
- Chadwick: East hall. You lead. If the manor decides anything else, I decide first.

**Nemotron-3.5-Lightning NVFP4**
- GM: The gatehouse gates groan open as you crest the drive. A liveried steward … hands each of you a sealed card…
- Chadwick: this is the way. / Ask them plainly, or I will. / Ask them plainly, or I will.

**Qwen3-30B-A3B-Thinking-2507** (current model before this run)
- Chadwick: This is the way. / This is the way. / This is the way. Ask them plainly, or I will.

**GLM-4.7-Flash FP8**
- GM: "GM block: truth Ten thousand years ago the Age of War burned the world's magic…" (secret block copied into public narration)

(Every first Chadwick line starts with `*draws his sword*`: that is the benchmark's forced retake test.)

## Findings

1. Qwen3.6-35B-A3B-FP8 is the only model that is both fast (5x under the round gate) and needed no
   validator retakes beyond the forced one. Chosen for the live test; it ran a live scene on
   Twitch (2026-10-08) with the same behaviour.
2. All small-active MoE models lean on Chadwick's profile tic ("This is the way"). The `repeats`
   commit rule stops verbatim repeats; the tic itself is allowed.
3. The GM's own lines are not leak-checked today (commit_check covers seat replies). GLM shows
   a model can paste GM-only context into the direction. Add a GM-output leak check
   (gm_blocks phrases) before any model change.
4. GB10 quirks: FP8 needs `--linear-backend triton` (some need `torch`: GLM, Hermes); NVFP4 works
   on `auto`; gpt-oss needs local tiktoken files (`TIKTOKEN_ENCODINGS_BASE`).

## Open

- Finish gpt-oss-20b with `reasoning_effort: low` (fast and small; promising smoke result).
- Hermes-4-14B: inspect its raw output to pick the right reasoning parser.
- Gemma 4: needs a newer vLLM/transformers image.
- Your P1.3 quality scoring before freezing the model (P1.4).
