# OB-07: W0 model benchmark for the Ashiorid office

- **Run:** 2026-09-27, 16:45–17:20 EDT
- **Target:** gx10 Ollama `http://192.168.1.23:11434`, version 0.34.4
- **Script:** `.claude/prompts/office_w0_benchmark.py`. Stdlib only, streaming `/api/chat`, `num_ctx=8192`, temperature 0.7.
- **Raw JSON (scratch, not committed):** `%LOCALAPPDATA%/hermes/cache/scratch/office_w0_results.json`, `office_w0_extra.json`
- **Notation:** every number here was measured unless it is marked **[ESTIMATE]**.

## 0. Inventory and conditions

- **Candidates missing from `/api/tags`:**
  - `qwen3-coder:30b` is **not installed**, so it could not be benchmarked.
  - `qwen2.5:7b-instruct-q4_K_M` is also **not installed**. It is the model that all three `config/workers/coder-*.yaml` files currently name.
  - I pulled nothing, as the brief required.
- **Benchmarked:**
  - `qwen3.8:27b` (Q4_K_M)
  - `gemma4:26b` (Q4_K_M)
  - `gemma4:12b-it-q4_K_M`
  - `llama3.1:8b` (Q4_K_M)
  - `hermes3:70b` (Q4_0), load and one short probe only (see §2)
- **Also listed, not relevant:** `swift1.5-qwen3.8-flash-next:iq2_xxs` (75 GB), `llama3`, whisper, nomic-embed, `qwen2.5-coder:1.5b-base`.
- **Thinking:** qwen3.8 and both gemma4 models report the `thinking` capability. All three ran with `"think": false`. Across every run, zero thinking characters were streamed and there was no `<think>` leakage. hermes3 and llama3.1 don't support thinking.
- **Other load was present.** Another client kept `qwen3.8:27b` resident at **ctx 262144** (18.5 GB) for the whole run. It was loaded at the start, and every `/api/ps` poll saw it again. That client's requests share the server with ours, which means:
  - The first qwen c=1 dialogue level was contaminated: TTFT was 23 s because of queueing behind that client plus a reload. I re-ran c=1 cleanly afterwards; those numbers are the ones in the table.
  - Because our 8k ctx differed from that client's 262k ctx, Ollama had to reload qwen. Measured reloads:
    - 69.9 s on the first swap
    - 32.2 s in the cross-model test
- **Residency:** three models were resident at the same time: qwen3.8:27b@262k (18.5 GB), gemma4:26b@8k (17.7 GB) and gemma4:12b@8k (8.4 GB), 44.6 GB in total. So `OLLAMA_MAX_LOADED_MODELS` is at least 3. When `hermes3:70b` (40 GB) loaded, `/api/ps` right afterwards showed nothing resident: it was evicted, or evicted others under memory pressure.

## 1. Measured results (8k ctx, warm model unless noted)

**Column key:**

- **p50 / p95:** request latency (wall time).
- **TTFT p50:** time to first token.
- **Req tok/s:** per-request decode speed (`eval_count / eval_duration`).
- **Agg tok/s:** total generated tokens divided by the level's wall time.
- **Requests at c=1:** the in-character line runs 3 sequential requests; the code edit runs 1.

### (a) In-character Tech Lead line (cap 150 tokens; models stop naturally at about 90–135)

| Model | c | p50 s | p95 s | TTFT p50 s | Req tok/s | Agg tok/s | Avg tokens |
|---|---|---|---|---|---|---|---|
| qwen3.8:27b (clean c=1 re-run, n=3) | 1 | 10.4 | 10.4 | 0.22 | 13.1–15.3 | ~13.8 | 133 |
| qwen3.8:27b | 4 | 24.6 | 36.3 | 14.6 | 13.5 | 13.1 | 123 |
| qwen3.8:27b | 8 | 43.4 | 75.0 | 33.8 | 13.8 | 13.4 | 131 |
| gemma4:26b | 1 | 7.4 | 7.6 | 0.20 | 16.6 | 16.1 | 119 |
| gemma4:26b | 4 | 19.0 | 29.4 | 11.4 | 16.5 | 16.2 | 123 |
| gemma4:26b | 8 | 34.3 | 57.8 | 27.0 | 16.5 | 16.1 | 121 |
| gemma4:12b | 1 | 7.4 | 10.9 | 0.21 | 15.0 | 14.0 | 122 |
| gemma4:12b | 4 | 30.9 | 46.2 | 19.6 | 10.3 | 9.9 | 119 |
| gemma4:12b | 8 | 33.0 | 50.7 | 27.6 | 20.1 | 18.3 | 121 |
| llama3.1:8b | 1 | 2.5 | 2.7 | 0.07 | 36.7 | 35.6 | 92 |
| llama3.1:8b | 4 | 6.5 | 9.6 | 4.0 | 35.2 | 34.6 | 86 |
| llama3.1:8b | 8 | 12.5 | 20.4 | 9.9 | 35.4 | 35.0 | 92 |
| hermes3:70b | 1 | >150 (aborted) | – | – | 5.6 (4-token probe) | – | – |

Contaminated first qwen c=1 run (kept for honesty): p50 33.1 s and TTFT 23.3 s, caused by queueing behind the other client.

### (b) Code edit (velocity rule + pytest, cap 800 tokens; every model hit the cap)

| Model | c | p50 s | p95 s | TTFT p50 s | Req tok/s | Agg tok/s |
|---|---|---|---|---|---|---|
| qwen3.8:27b | 1 | 28.7 | 28.7 | 0.70 | 28.6 | 27.9 |
| qwen3.8:27b | 2 | 40.1 | 52.2 | 13.7 | 30.3 | 29.9 |
| gemma4:26b | 1 | 50.7 | 50.7 | 0.50 | 16.0 | 15.8 |
| gemma4:26b | 2 | 63.2 | 80.6 | 22.4 | 19.7 | 19.4 |
| gemma4:12b | 1 | 38.5 | 38.5 | 0.28 | 20.9 | 20.8 |
| gemma4:12b | 2 | 56.4 | 73.4 | 19.1 | 21.4 | 21.3 |
| llama3.1:8b | 1 | 48.2 | 48.2 | 0.14 | 16.6 | 16.6 |
| llama3.1:8b | 2 | 78.0 | 99.2 | 27.4 | 15.9 | 15.8 |

- A second llama code run decoded at 13.2 tok/s, after a 13 s reload.
- qwen3.8:27b decodes code about twice as fast as prose: 28–30 tok/s against 13–15 tok/s. This held across repeat runs, including a clean 150-token code probe at 29–30 tok/s. The likely cause is speculative or MTP-style acceleration that works better on predictable code tokens, but that is **[INFERENCE]** and I did not verify it.

### (c) Load and swap times (cold → first token)

| Model | Load s | Notes |
|---|---|---|
| llama3.1:8b | 3.5 | also a 13.1 s reload later, while other models were resident |
| gemma4:12b | 10.5 | |
| gemma4:26b | 18.6 | also 12.3 s on a later reload |
| qwen3.8:27b | 69.9 / 32.2 | reload from the other client's 262k ctx down to 8k |
| hermes3:70b | 125.8 | then evicted almost immediately; a 150-token line did not finish within 150 s |

### (d) Cross-model parallelism (one request each, fired together)

- gemma4:26b: 19.2 s, of which 12.3 s was reload
- llama3.1:8b: 6.9 s, decoding at 12.8 tok/s against 36 tok/s when run alone
- qwen3.8:27b: 41.8 s, of which 32.2 s was reload

Different models do run at the same time, but they share the unified-memory bandwidth, so each one slows down.

## 2. Findings that drive the allocation

1. **Same-model concurrency buys no throughput.** For every model, aggregate tok/s at c=4 and c=8 roughly equals the single-request decode rate: 13 for qwen, 16 for gemma26, 35 for llama8b. TTFT grows linearly with queue position. The server is effectively processing requests for a model one at a time. `OLLAMA_NUM_PARALLEL` appears to be 1, or batching doesn't help on GB10; this is **[INFERENCE]** from the numbers. Eight "concurrent" agents on one model are really a queue: at c=8, p95 is 58–75 s for the 26–27B models.
2. **Ctx mismatch forces a reload.** Every agent must use one `num_ctx` per model, and it must not fight other clients of the same model tag. The external Hermes-style client pins qwen3.8:27b at 262k, so the office's 8k requests to that tag cost 30–70 s reloads whenever the two alternate.
3. **hermes3:70b is not viable here.** It measured 126 s to load, 5.6 tok/s, and got evicted under the current co-tenancy. Drop it from the office plan; it also rules out Plan A and Plan B in agent_dnd §8 as written.
4. **qwen3-coder:30b is not installed.** The best installed coder by measurement is qwen3.8:27b, at 28–30 tok/s on code.

## 3. Recommended allocation for the 8 office agents

**Turns must be serialized.** One speaker at a time is the only mode with good latency. Parallel speech just turns into queueing, as the §1 table shows. At most one background code job should run alongside the spoken turn.

| Role(s) | Model | Why | Realistic seconds per spoken line (about 120 tokens) |
|---|---|---|---|
| CEO, Tech Lead, Analyst, Marketing (the voices that carry the plot) | `gemma4:26b` @ `num_ctx 8192`, `think:false` | Fastest ~26B-class model on prose (16.5 tok/s, TTFT 0.2 s), 17.7 GB, and no conflict with the external qwen client | **7–8 s** when serialized and warm (measured p50 7.4, p95 7.6). **About 11–13 s** if a code job is decoding at the same time **[ESTIMATE]**, based on the §1(d) slowdown. |
| Tester, Office Manager (short, formulaic lines) | `gemma4:12b-it-q4_K_M` @ 8k | 8.4 GB. Measured 7.4 s p50, so it is no faster than gemma26 for lines, but it spreads load and it is small | **7–11 s** (measured p50 7.4, p95 10.9) |
| Engineer (coding) | `qwen3.8:27b` @ **the same ctx the external client uses (262144), or a dedicated tag** | Best measured code decode (28–30 tok/s). An 800-token edit takes 29 s warm. | Speaks through gemma4:26b. Code turns take about 30 s, plus 30–70 s if a reload is triggered. |
| Party Member (observer, never speaks) | none | no LLM calls | – |
| Fallback or "fast mode" for any speaker | `llama3.1:8b` | 2.5 s/line and 35 tok/s, but the weakest in character | 2.5–3 s |

### Resident set and a full round

- **Resident set:** gemma4:26b (17.7) + gemma4:12b (8.4) + qwen3.8:27b (18.5) = **44.6 GB**. I observed these three co-resident during this run, so `MAX_LOADED` is at least 3.
- **Keep-alive:** set `keep_alive` to at least 30m on every office call so the models don't unload between turns. Swaps cost 10–70 s (§1(c)).
- **Full office round:** 7 speakers × about 7.5 s = **about 53 s per stand-up round**, serialized **[ESTIMATE]**. This is derived from the measured c=1 p50, not from a timed full round.

### Engineer coding backend

- **Use `aider` with `qwen3.8:27b`.** Aider's diff and whole-file edit formats and its repo map suit a real, growing Fraud-Stop `src/` tree.
  - `native` caps the workspace at 48k chars (`MAX_TOTAL_CHARS`) and sends whole files, so it will stop fitting once the repo grows. Keep it only as a fallback for tiny tasks.
  - `opencode` relies on multi-turn tool use. That multiplies the 800-token-class completions, at about 30 s each here.
  - This choice is **[JUDGEMENT]**. I didn't benchmark the backends end to end.
- **Update the worker config:** set `coding_backend.model` (or `llm.model`) to `qwen3.8:27b` in the office coder config. The current `qwen2.5:7b-instruct-q4_K_M` is not installed on gx10.
- **Decide the ctx before Wave 3:** either
  - send `num_ctx: 262144` to match the external client (no reloads, but a larger KV cache), or
  - coordinate so that client isn't hammering qwen3.8 during office hours.

  Otherwise each alternation costs a 30–70 s reload (measured).

### Relation to agent_dnd §8 plans

- Plan A and Plan B both depend on hermes3:70b, which is non-viable (§2.3). Plan C, a per-turn 70b swap, would cost at least 126 s per swap.
- The recommendation above amounts to a new **Plan D**: a gemma4:26b voice tier, a gemma4:12b support tier, and a qwen3.8:27b coder, all serialized.
- **Not measured:** 16k-ctx decode and KV-cache GB, and a timed multi-speaker round with real transcript-sized prompts (our prompts were about 140–200 tokens). Re-run the script with larger prompts once `brief.py` exists.
