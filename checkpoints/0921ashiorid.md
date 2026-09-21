# Checkpoint — 2026-09-21 evening (Ashiorid week build, session handoff)

## What landed this session (before the night run):

1. **Ambient batch 1 (12 scenes) PROMOTED** from
   `.claude/prompts/ashiorid_week_stage/ambient/` → `campaigns/ashiorid/scenes/`.
   Gate PASS (0 errors, 0 warnings) before promote; pack re-validated after.
   Source refs: Sarah's Inn, The Mage Hole, Mystery Boy and Poison Ring,
   Dwarf Related Issues, WerePomeranian, Re-Adventure Unclaimed Loot,
   Falling Man, Henderson, Losira, Malmont Town Profile, Azra, Grovley.

2. **Ambient batch 2 (22 scenes) PROMOTED** from
   `.claude/prompts/ashiorid_week_stage/ambient_batch2/` → `campaigns/ashiorid/scenes/`.
   Gate PASS (0 errors, 0 warnings) before promote; pack re-validated after.

3. **Pack now carries 86 scenes total** (was 52, +34 ambient definitions).
   - 18 spine (15 original + 3 continuation ring: temple-of-malar,
     lighthouse-encounter, mutants-encounter — closed on malvakar-riddle).
   - 68 ambient.
   - `load_pack` clean; `validate_pack` 0 errors / 0 warnings.

4. **Ambient batch 3 launched (overnight, background).**
   - 15 notes: Items (3), Characters (4), Classes (1), Maps (7).
   - Refs in `.claude/prompts/ashiorid_ambient_batch3_notes.txt`.
   - Staging target: `.claude/prompts/ashiorid_week_stage/ambient_batch3/`.
   - Command:
     ```
     .venv/bin/python .claude/prompts/run_ashiorid_week_build.py \
       --ambient-only --gate-ambient-only \
       --ambient-notes .claude/prompts/ashiorid_ambient_batch3_notes.txt \
       --ambient-out .claude/prompts/ashiorid_week_stage/ambient_batch3
     ```
   - Log: `.claude/prompts/ashiorid_week_run7_batch3.log`.
   - Expected wall time: ~15-25 min sequential (1-2 min per note at 70b).
   - Not promoted; promotion is the next morning human step.

## What was deliberately NOT done:
- **No spine extension.** The spine ring is closed
  (malvakar-riddle ⇄ mutants-encounter). Continuing the spine means either
  (a) picking a new open endpoint and breaking the ring (a design call), or
  (b) densifying existing scenes (the Phase 2.5 path). Deferred to morning.
- **No Amulet of Wonder quest scenes.** The quest file is explicitly named
  `*Agent_Ignore*.md` in the vault — user instruction.
- **No new cast.** Characters notes (Buffalo, Carl the Ranger, Helen) used
  as ambient only; adding them as cast members is a separate decision.
- No commit/push of the promotions — operator's call. `git status` will
  show 34 new files under `campaigns/ashiorid/scenes/`.

## Pending operator calls (morning):
1. Review ambient batch 3 (15 scenes, gate-clean) → promote or discard.
2. Spine direction: close-and-restart the ring (new seed), or densify
   existing spine (Phase 2.5 in the plan)?
3. Commit the pack promotion to git.
4. Optional: re-derive the 168 h pacing numbers in §6A of the plan now that
   ambient definitions went 34 → ~50. The "10 airings per definition per
   week" health metric improves with each additional definition.

## Machine state (at launch 2026-09-21):
- `qwen3.8:27b` was resident in `ollama ps` for the current Hermes agent
  itself. `hermes3:70b` will load on first batch-3 call.
- Mem: ~46 GB free + 15 GB reclaimable / 121 GB total — 40 GB for 70b
  fits with headroom. This is the crash-guard boundary; if batch 3 hangs,
  check `free -h` before assuming a model-side fault.
- Concurrency constraint (host crashed 2026-09-19): ONE 70b in flight.
  This run honours it — the runner loops sequentially, one Ollama call at
  a time.

## What is uncommitted in the working tree:
- 34 new files under `campaigns/ashiorid/scenes/` (batches 1+2).
- Existing untracked: `app/agent_metrics.py`, `app/chat_list_pane.py`,
  `app/knowledge_graph_pane.py`, `app/radar_pane.py`, `app/thinking_pane.py`,
  `config/layouts/tuber_base.yaml`, several `config/panels/*.yaml`,
  `docs/tuber_base_layout_plan.md`, five corresponding `tests/test_*.py`,
  plus modified `app/agent.py`, `app/stream_supervisor.py`,
  `app/tail_bus.yaml`, `config/panels/kafka_feed.yaml`, worker configs,
  `docker-compose.yml`, `docs/todo.md`, `startup.sh`, and a few tests.
  These are the `tuber_base_layout` work in a separate thread, not part
  of this content build — leave them alone.

## Where the last session left off (for continuity):
- `checkpoints/0919ashiorid.md` — full 2026-09-19 state up to batch 2
  launch.
- `.claude/prompts/generator_retarget_scenes_plan.md` — the design.
- `.claude/prompts/source_shape_test_report.md` — source-shape
  comparison.
- `.claude/prompts/hp_source_shape_test/` — Harry Potter test pack (3
  scenes, gate-clean, promoted earlier to `campaigns/hptest/`).
