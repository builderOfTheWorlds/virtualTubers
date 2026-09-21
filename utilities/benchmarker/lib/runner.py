"""Measurement runner for the D&D-agents benchmark.

Probes per design doc §13 (`.claude/prompts/agent_dnd_architecture.md`):

1. `line`          — one in-role spoken line (~45 words). The "feels
                     live" unit. Measured at 8k and 16k context.
2. `prefill_decode` — big prompt (8k/16k target) + ~64-token completion.
                     Measures prefill speed at real context AND decode
                     speed with the model's KV cache warm.
3. `concurrency`   — N simultaneous `complete()` calls on the same model.
                     On unified GB10 memory this is where KV-cache
                     pressure and CPU offload bite.
4. `full_round`    — one full D&D round per the design doc: GM direction
                     (70b, 16k) + N character replies in turn (per plan
                     per-seat model, 8k) + GM adjudication (70b). This
                     is the "is this actually live?" number that decides
                     `deadline_s` in §4.4.
5. `co_resident`   — W0 item 4: GM model + a seat model in parallel;
                     reports the speedup over running them solo.

Configuration lives in `conf/benchmark.toml` (loaded by `config.py`);
CLI flags are thin overrides of that file, never a second config source.
The launcher is `bin/runBenchmark.sh` → `main.py` → `runner.main()`.
Hosts: `--host ollama` (OllamaHost, native /api/chat) or `--host vllm`
(vLLMHost, OpenAI v1 + Prometheus KV-cache metrics).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import prompts
from host_base import CompletionResult, HostError, HostState
from ollama_host import OllamaHost
from vllm_host import vLLMHost

from config import (
    DEFAULT_CONF,
    REPO_ROOT,
    load_config,
    override_config_from_cli,
    resolve_paths,
)


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class CellResult:
    """One (model, ctx_k, task, n_calls) data point. When `n_calls`>1
    the fields are the *median* of the batch (see `_summarise_batch`)
    and `raw_cells` preserves the individual results."""
    host_protocol: str
    model: str
    ctx_k: int
    task: str
    think: bool
    prompt_tokens: int
    content_tokens: int
    reasoning_tokens: int
    decode_tps: float | None = None
    prefill_tps: float | None = None
    ttft_s: float = 0.0
    total_s: float = 0.0
    n_calls: int = 1
    error: str | None = None
    raw_cells: list[CompletionResult] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Run:
    host_protocol: str
    base_url: str
    started_at: float
    finished_at: float | None = None
    config: dict[str, Any] = field(default_factory=dict)
    host_state_before: dict[str, Any] = field(default_factory=dict)
    host_state_after: dict[str, Any] = field(default_factory=dict)
    cells: list[CellResult] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _summarise_batch(cells: list[CompletionResult]) -> dict[str, Any]:
    """Median/mean of a batch of call results + pass/fail counts."""
    ok = [c for c in cells if not c.error and c.total_s > 0]
    fails = len(cells) - len(ok)
    out: dict[str, Any] = {
        "decode_tps": 0.0, "prefill_tps": 0.0, "ttft": 0.0, "total": 0.0,
        "prompt": 0.0, "content": 0.0, "reasoning": 0.0,
        "n_ok": len(ok), "n_fail": fails,
    }
    if not ok:
        return out
    decode_tps_list: list[float] = []
    prefill_tps_list: list[float] = []
    for c in ok:
        decode_s = (c.total_s - c.ttft_s) if c.ttft_s is not None else c.total_s
        if decode_s and decode_s > 0 and c.content_tokens:
            decode_tps_list.append(c.content_tokens / decode_s)
        if c.ttft_s and c.ttft_s > 0 and c.prompt_tokens:
            prefill_tps_list.append(c.prompt_tokens / c.ttft_s)
    out["decode_tps"] = statistics.median(decode_tps_list) if decode_tps_list else 0.0
    out["prefill_tps"] = statistics.median(prefill_tps_list) if prefill_tps_list else 0.0
    out["ttft"] = statistics.median([c.ttft_s for c in ok])
    out["total"] = statistics.median([c.total_s for c in ok])
    out["prompt"] = sum(c.prompt_tokens for c in ok) / len(ok)
    out["content"] = sum(c.content_tokens for c in ok) / len(ok)
    out["reasoning"] = sum(c.reasoning_tokens for c in ok) / len(ok)
    return out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class Runner:
    def __init__(self, host: OllamaHost | vLLMHost, *,
                 models: list[str], ctx_k: list[int],
                 output_dir: pathlib.Path,
                 report_path: pathlib.Path,
                 think: bool = False,
                 temperature: float = 0.7,
                 concurrency_n: int = 3,
                 ) -> None:
        self.host: OllamaHost | vLLMHost = host
        self.models = models
        self.ctx_k = ctx_k
        self.output_dir = output_dir
        self.report_path = report_path
        self.think = think
        self.temperature = temperature
        self.concurrency_n = concurrency_n
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.progress_path = output_dir / "progress.jsonl"
        self.progress_lock = threading.Lock()

    def _log(self, event: str, **fields: Any) -> None:
        rec = {"ts": time.time(), "event": event, **fields}
        try:
            line = json.dumps(rec, default=str)
        except TypeError:
            line = json.dumps({k: str(v) for k, v in rec.items()})
        with self.progress_lock, self.progress_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(f"[bench] {event} "
              + " ".join(f"{k}={v}" for k, v in list(fields.items())[:9]),
              flush=True)

    def _summarise_and_store(self, *, model: str, ctx_k: int, task: str,
                             cells: list[CompletionResult],
                             n_calls: int,
                             run: Run) -> None:
        s = _summarise_batch(cells)
        n_ok = s["n_ok"]
        # A model that burned its entire num_predict budget on reasoning
        # with zero content is `budget_capped` — the caller can see
        # "this model thought too long to actually speak" and either
        # raise the budget or pick a different model.
        budget_capped = n_ok > 0 and (
            s["reasoning"] > 0 and s["content"] < s["reasoning"] / 2
        )
        cell = CellResult(
            host_protocol=self.host.protocol, model=model, ctx_k=ctx_k,
            task=task, think=self.think,
            prompt_tokens=int(s["prompt"]), content_tokens=int(s["content"]),
            reasoning_tokens=int(s["reasoning"]),
            decode_tps=float(s["decode_tps"]), prefill_tps=float(s["prefill_tps"]),
            ttft_s=float(s["ttft"]), total_s=float(s["total"]), n_calls=n_calls,
            error=None if s["n_fail"] == 0 else f"{s['n_fail']}/{n_calls} calls failed",
            raw_cells=[c for c in cells if c],
            extra={
                "n_ok": n_ok,
                "n_fail": s["n_fail"],
                "budget_capped_on_reasoning": budget_capped,
            },
        )
        run.cells.append(cell)
        self._log("cell", model=model, ctx_k=ctx_k, task=task,
                  decode_tps=round(float(s["decode_tps"]), 2),
                  prefill_tps=round(float(s["prefill_tps"]), 1),
                  ttft_s=round(float(s["ttft"]), 3),
                  total_s=round(float(s["total"]), 3),
                  n_ok=n_ok, n_fail=s["n_fail"],
                  content_tokens=int(s["content"]),
                  reasoning_tokens=int(s["reasoning"]),
                  budget_capped=bool(budget_capped), think=self.think)

    # -- individual probes ---------------------------------------------------

    def probe_line(self, model: str, ctx_k: int, run: Run,
                   n_samples: int = 2) -> None:
        """In-role spoken line (~45 words) at `ctx_k` context."""
        sheet = prompts.load_cast_sheet("Leena") if (
            "Leena" in prompts.list_cast()) else prompts.load_cast_sheet(
            prompts.list_cast()[0])
        prompt = prompts.build_character_prompt(
            sheet=sheet,
            scene_direction=(
                "The party stands before the vault door. The sigil above "
                "the latch is cracked. Something under the floor has just "
                "stopped breathing, and Leena is the only one who heard."
            ),
            committed_transcript_lines=prompts.load_transcript_example_lines(4),
            target_context_tokens=ctx_k * 1000,
        )
        cells: list[CompletionResult] = []
        for _ in range(n_samples):
            try:
                c = self.host.complete(
                    model=model,
                    system=str(prompt["system"]),
                    user=(
                        "The GM has just spoken. Your line is now — "
                        "exactly one in-character speech, no name label, "
                        "no quotation marks, no stage directions.\n\n"
                        + str(prompt["user"])
                    ),
                    num_predict=512,
                    think=self.think,
                    temperature=self.temperature,
                )
                cells.append(c)
            except Exception as exc:  # noqa: BLE001
                cells.append(CompletionResult(
                    model=model, prompt_tokens=0, content_tokens=0,
                    reasoning_tokens=0, ttft_s=0.0, total_s=0.0,
                    error=repr(exc)))
        self._summarise_and_store(model=model, ctx_k=ctx_k, task="line",
                                  cells=cells, n_calls=n_samples, run=run)

    def probe_prefill_decode(self, model: str, ctx_k: int, run: Run,
                             n_samples: int = 2) -> None:
        """Large prompt + moderate completion. Measures prefill speed at
        real context AND decode speed with a warm KV cache.
        `num_predict=256` keeps the decode phase short so total_s is
        dominated by prefill + the 256-token decode — both of which we
        care about.
        """
        ctx_tokens = ctx_k * 1000
        unit = "oak the Event the Begene vault "
        n = max(1, int((ctx_tokens * 3.4) / len(unit)))
        prompt_text = (unit * n)[: ctx_tokens * 34]
        user = (
            prompt_text +
            "\n\nSummarize the state of this scene in one GM line."
        )
        cells: list[CompletionResult] = []
        for _ in range(n_samples):
            try:
                c = self.host.complete(
                    model=model,
                    system="You are the GM. Reply in one line.",
                    user=user,
                    num_predict=256,
                    think=self.think,
                    temperature=self.temperature,
                )
                cells.append(c)
            except Exception as exc:  # noqa: BLE001
                cells.append(CompletionResult(
                    model=model, prompt_tokens=0, content_tokens=0,
                    reasoning_tokens=0, ttft_s=0.0, total_s=0.0,
                    error=repr(exc)))
        self._summarise_and_store(model=model, ctx_k=ctx_k,
                                  task="prefill_decode", cells=cells,
                                  n_calls=n_samples, run=run)

    def probe_concurrency(self, model: str, ctx_k: int, run: Run,
                          n_calls: int | None = None) -> None:
        """Fire N simultaneous `complete()` calls at the same model. This
        is where unified GB10 memory pressure shows up: a single-model
        call leaves headroom; many at once exhaust the KV pool and the
        CPU offload path starts to bite."""
        n = n_calls or self.concurrency_n
        ctx_tokens = ctx_k * 1000
        # A per-call prompt sized to n*ctx so N calls in parallel would
        # each be at `ctx` — but we keep *each call* at `ctx` (the point
        # is the *simultaneity*, not the per-call size).
        unit = "oak the Event the Begene vault "
        per_call_tokens = max(1, int((ctx_tokens * 3.4) / n / len(unit)))
        per_prompt = unit * per_call_tokens
        user = (per_prompt + "\n\nOne GM line.")

        cells: list[CompletionResult] = []
        errors: list[str] = []
        t0 = time.perf_counter()

        def _one() -> None:
            try:
                cells.append(self.host.complete(
                    model=model, system="You are the GM.", user=user,
                    num_predict=64, think=self.think,
                    temperature=self.temperature,
                ))
            except Exception as exc:  # noqa: BLE001
                cells.append(CompletionResult(
                    model=model, prompt_tokens=0, content_tokens=0,
                    reasoning_tokens=0, ttft_s=0.0, total_s=0.0,
                    error=repr(exc)))
                errors.append(str(exc))

        threads = [threading.Thread(target=_one, daemon=True) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=self.host.timeout_s)
        wall = time.perf_counter() - t0

        cells.sort(key=lambda c: (c.total_s or 0))
        self._summarise_and_store(model=model, ctx_k=ctx_k, task="concurrency",
                                  cells=cells, n_calls=n, run=run)
        if cells:
            cells[-1].raw_extra["wall_s"] = round(wall, 3)

    def probe_co_resident(self, gm_model: str, seat_model: str, ctx_k: int,
                          run: Run, n_samples: int = 2) -> None:
        """W0 item 4 (design doc §13): can the GM *and* a seat run at the
        same time? Fire 2 in-parallel calls, one on each model, and
        record the wall time. If wall ≈ max(gm_solo, seat_solo), the
        models co-reside on the shared GB10 (the tiered architecture
        gives a concurrency benefit); if wall ≈ sum, they're
        serialising on the shared KV-cache pool."""
        gm_user = (
            "The party is reacting. Write a 30-word GM direction for the "
            "next beat, naming one seat to focus on.\n\n"
            "Canonical goal: the party opens the Begene vault and finds "
            "the sigil already broken."
        )
        seat_user = (
            "React as your character to the GM's direction. Exactly one "
            "in-character line, no name label, no quotation marks, no "
            "stage directions.\n\n"
            + "\n\n".join(prompts.load_transcript_example_lines(4))
        )
        sample_pairs: list[tuple[CompletionResult, CompletionResult]] = []
        for _ in range(n_samples):
            result: dict[str, CompletionResult] = {}
            t0 = time.perf_counter()

            def _gm() -> None:
                try:
                    result["gm"] = self.host.complete(
                        model=gm_model,
                        system="You are the GM. Reply in one short block.",
                        user=gm_user, num_predict=128, think=self.think,
                        temperature=self.temperature)
                except Exception as exc:  # noqa: BLE001
                    result["gm"] = CompletionResult(
                        model=gm_model, prompt_tokens=0, content_tokens=0,
                        reasoning_tokens=0, ttft_s=0.0, total_s=0.0,
                        error=repr(exc))

            def _seat() -> None:
                try:
                    result["seat"] = self.host.complete(
                        model=seat_model,
                        system="You are a seat character. Reply in one line.",
                        user=seat_user, num_predict=64, think=self.think,
                        temperature=self.temperature)
                except Exception as exc:  # noqa: BLE001
                    result["seat"] = CompletionResult(
                        model=seat_model, prompt_tokens=0, content_tokens=0,
                        reasoning_tokens=0, ttft_s=0.0, total_s=0.0,
                        error=repr(exc))

            th_gm = threading.Thread(target=_gm, daemon=True)
            th_seat = threading.Thread(target=_seat, daemon=True)
            th_gm.start()
            th_seat.start()
            th_gm.join(timeout=self.host.timeout_s)
            th_seat.join(timeout=self.host.timeout_s)
            wall = time.perf_counter() - t0
            gm_c = result.get("gm")
            seat_c = result.get("seat")
            if gm_c is None or seat_c is None:
                continue
            gm_c.raw_extra["co_wall_s"] = round(wall, 3)
            seat_c.raw_extra["co_wall_s"] = round(wall, 3)
            sample_pairs.append((gm_c, seat_c))

        if not sample_pairs:
            self._log("co_resident_no_result", gm=gm_model, seat=seat_model)
            return

        gm_cells = [g for g, _ in sample_pairs]
        seat_cells = [s for _, s in sample_pairs]
        gm_stats = _summarise_batch(gm_cells)
        seat_stats = _summarise_batch(seat_cells)
        walls = [c.raw_extra.get("co_wall_s", 0.0) for c in gm_cells]
        wall_median = statistics.median(walls) if walls else 0.0
        # Reference: the sum of the two *solo* medians.
        solo_sum = gm_stats["total"] + seat_stats["total"]

        cell = CellResult(
            host_protocol=self.host.protocol,
            model=f"{gm_model} + {seat_model}",
            ctx_k=ctx_k, task="co_resident", think=self.think,
            prompt_tokens=int(gm_stats["prompt"] + seat_stats["prompt"]),
            content_tokens=int(gm_stats["content"] + seat_stats["content"]),
            reasoning_tokens=int(gm_stats["reasoning"] + seat_stats["reasoning"]),
            decode_tps=gm_stats["decode_tps"] or None,
            prefill_tps=gm_stats["prefill_tps"] or None,
            ttft_s=gm_stats["ttft"],
            total_s=round(wall_median, 3),
            n_calls=len(sample_pairs),
            error=None if gm_stats["n_fail"] == 0 and seat_stats["n_fail"] == 0
                    else f"gm_fail={gm_stats['n_fail']} seat_fail={seat_stats['n_fail']}",
            extra={
                "wall_s": round(wall_median, 3),
                "solo_max_s": round(max(gm_stats["total"], seat_stats["total"]), 3),
                "solo_sum_s": round(solo_sum, 3),
                "co_speedup_over_sum": (
                    round(solo_sum / wall_median, 2)
                    if (solo_sum > 0 and wall_median > 0) else None
                ),
                "gm": self._cell_stats_to_dict(gm_stats, gm_cells),
                "seat": self._cell_stats_to_dict(seat_stats, seat_cells),
                "note": "wall_s is the shared wall-clock time; "
                        "co_speedup_over_sum ≈ 2.0 means perfect co-residence; "
                        "≈ 1.0 means they're serialising on the shared pool.",
            })
        run.cells.append(cell)
        self._log("co_resident", gm=gm_model, seat=seat_model,
                  wall=round(wall_median, 3),
                  solo_sum=round(solo_sum, 3),
                  speedup=cell.extra.get("co_speedup_over_sum"))

    @staticmethod
    def _cell_stats_to_dict(stats: dict[str, Any],
                            cells: list[CompletionResult]) -> dict[str, Any]:
        return {
            "n_ok": stats["n_ok"], "n_fail": stats["n_fail"],
            "decode_tps": stats["decode_tps"],
            "prefill_tps": stats["prefill_tps"],
            "ttft_s": stats["ttft"], "total_s": stats["total"],
            "content_tokens": int(stats["content"]),
            "reasoning_tokens": int(stats["reasoning"]),
            "errors": [c.error for c in cells if c.error][:3],
        }

    # -- per-plan full-round ---------------------------------------------------

    def probe_full_round(self, seats: list[tuple[str, str, str]],
                         gm_model: str, plan_name: str, run: Run) -> None:
        """One full D&D round per §4.4: GM direction → N character replies
        in turn (sequential: the committed-transcript rule requires it) →
        GM adjudication. Each is one `complete()` call with the
        appropriate model. Measured end-to-end as one unit.

        `seats` is a list of (label, cast_for_sheet, model) tuples.
        `cast_for_sheet` picks which real campaign sheet to load for the
        prompt; `model` is what gets measured.
        """
        stage_log: list[dict[str, Any]] = []
        t0 = time.perf_counter()
        total_prompt = 0
        total_content = 0
        total_reasoning = 0
        errors: list[str] = []

        # 1. GM direction (big model, 16k context, 220-word target)
        gm_prompt = prompts.build_gm_prompt(
            canon_goal="The party opens the vault and finds the sigil broken.",
            expected_beats=[
                "Leena identifies the lock mechanism (locksmith sheet)",
                "Vance hesitates about opening a sealed door (superstitious)",
            ],
            committed_transcript_lines=prompts.load_transcript_example_lines(4),
            target_context_tokens=16_000,
        )

        # 3. GM adjudication (small prompt, 100-word target)
        adjudication_user = (
            "The party has just reacted. Did they hit the canonical goal? "
            "Reply pass or reject + one line of feedback."
        )

        try:
            direction_res = self.host.complete(
                model=gm_model,
                system=str(gm_prompt["system"]),
                user=(
                    "Write the next GM direction. Steer toward the canonical "
                    "goal, name the seats you direct by id, and give each a "
                    "specific must_resolve beat for this round. Exactly one "
                    "GM block: short scene direction + beat plan.\n\n"
                    + str(gm_prompt["user"])
                ),
                num_predict=256,
                think=self.think,
                temperature=self.temperature,
            )
            direction_text = direction_res.content_text
        except Exception as exc:  # noqa: BLE001
            errors.append(f"gm_direction: {exc}")
            direction_text = "(GM direction errored — continuing with scripted fallback)"
            direction_res = None

        stage_log.append({
            "stage": "gm_direction", "model": gm_model,
            "ok": direction_res is not None,
            "prompt_tokens": (direction_res.prompt_tokens if direction_res else 0),
            "content_tokens": (direction_res.content_tokens if direction_res else 0),
            "reasoning_tokens": (direction_res.reasoning_tokens if direction_res else 0),
            "ttft_s": round(direction_res.ttft_s, 3) if direction_res else None,
            "total_s": round(direction_res.total_s, 3) if direction_res else None,
        })
        if direction_res:
            total_prompt += direction_res.prompt_tokens
            total_content += direction_res.content_tokens
            total_reasoning += direction_res.reasoning_tokens

        # Character replies — sequential, committed-transcript style.
        committed: list[str] = []
        for i, (label, sheet_id, model) in enumerate(seats):
            cast_names = prompts.list_cast()
            if sheet_id not in cast_names:
                # Sheet isn't in this pack; fall back to the first
                # available cast so the prompt shape is still realistic.
                sheet_id = cast_names[0] if cast_names else sheet_id
            cast = prompts.load_cast_sheet(sheet_id)
            cp = prompts.build_character_prompt(
                sheet=cast,
                scene_direction=direction_text,
                committed_transcript_lines=committed,
                target_context_tokens=8_000,
            )
            stage_name = f"char_{label} (sheet={cast.name}, model={model})"
            try:
                r = self.host.complete(
                    model=model,
                    system=str(cp["system"]),
                    user=(
                        "React as your character to the GM's direction. "
                        "Exactly one in-character line, no name label, no "
                        "quotation marks, no stage directions.\n\n"
                        + str(cp["user"])
                    ),
                    num_predict=256,
                    think=self.think,
                    temperature=self.temperature,
                )
                stage_log.append({
                    "stage": stage_name,
                    "ok": True,
                    "prompt_tokens": r.prompt_tokens,
                    "content_tokens": r.content_tokens,
                    "reasoning_tokens": r.reasoning_tokens,
                    "ttft_s": round(r.ttft_s, 3),
                    "total_s": round(r.total_s, 3),
                })
                if r.content_text:
                    committed.append(f"{cast.name}: {r.content_text[:80]}")
                total_prompt += r.prompt_tokens
                total_content += r.content_tokens
                total_reasoning += r.reasoning_tokens
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{stage_name}: {exc}")
                stage_log.append({
                    "stage": stage_name,
                    "ok": False, "error": str(exc),
                })

        # 4. GM adjudication — per §4.4, the GM makes a second call to
        #    judge the seats' reactions against the canonical goal and
        #    either pass them or issue a retake. Measured as one call
        #    on the 70b.
        try:
            a = self.host.complete(
                model=gm_model,
                system="You are the GM judging the party's reactions. "
                       "Reply 'pass' or 'retake <seat>' plus one line of "
                       "feedback. Do not restate the scene.",
                user=adjudication_user,
                num_predict=128,
                think=self.think,
                temperature=self.temperature,
            )
            stage_log.append({
                "stage": "gm_adjudication", "model": gm_model,
                "ok": True,
                "prompt_tokens": a.prompt_tokens,
                "content_tokens": a.content_tokens,
                "reasoning_tokens": a.reasoning_tokens,
                "ttft_s": round(a.ttft_s, 3),
                "total_s": round(a.total_s, 3),
            })
            total_prompt += a.prompt_tokens
            total_content += a.content_tokens
            total_reasoning += a.reasoning_tokens
        except Exception as exc:  # noqa: BLE001
            errors.append(f"gm_adjudication: {exc}")
            stage_log.append({"stage": "gm_adjudication", "model": gm_model,
                              "ok": False, "error": str(exc)})
        total_s = time.perf_counter() - t0
        run.cells.append(CellResult(
            host_protocol=self.host.protocol,
            model=f"plan:{plan_name}",
            ctx_k=max(self.ctx_k),
            task="full_round",
            think=self.think,
            prompt_tokens=total_prompt,
            content_tokens=total_content,
            reasoning_tokens=total_reasoning,
            decode_tps=None,
            prefill_tps=None,
            ttft_s=0.0,
            total_s=total_s,
            n_calls=sum(1 for s in stage_log if s.get("ok")),
            error=None if not errors else f"{len(errors)} stage(s) failed: {errors[:3]}",
            raw_cells=[],
            extra={"stages": stage_log},
        ))
        self._log("full_round_complete", plan=plan_name, total_s=round(total_s, 3),
                  ok_stages=str(len([s for s in stage_log if s.get('ok')])),
                  n_stages=str(len(stage_log)), n_fail=str(len(errors)))

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def run(self, plans: dict[str, tuple[str, list[tuple[str, str, str]]]]
            | None = None,
            skip_full_round: bool = False) -> Run:
        """Execute the full battery. `plans` maps plan name → (gm_model,
        seats) where each seat is (label, cast_for_sheet, model). When
        None, defaults to the 4 probes per model + one full_round per
        §8 plan whose GM model is in `--models`. `skip_full_round=True`
        skips all full_round probes (per-model + concurrency only)."""
        t0 = time.time()
        run = Run(host_protocol=self.host.protocol, base_url=self.host.base_url,
                  started_at=t0,
                  config={"models": self.models, "ctx_k": self.ctx_k,
                          "think": self.think, "temperature": self.temperature,
                          "concurrency": self.concurrency_n},
                  host_state_before=self._safe_state(),
                  host_state_after={})
        self._log("run_start", host=run.host_protocol, models=",".join(self.models),
                  ctx_k=str(self.ctx_k), think=self.think)
        try:
            for model in self.models:
                for ctx_k in self.ctx_k:
                    # Warm-up — load the model into the pool / warm KV cache.
                    try:
                        self.host.complete(
                            model=model,
                            system="You are a helpful GM.",
                            user="ok",
                            num_predict=2,
                            temperature=0.0,
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._log("warmup_err", model=model, err=str(exc))
                    self.probe_line(model, ctx_k, run)
                    self.probe_prefill_decode(model, ctx_k, run)

            # Concurrency on the *smallest* model in the list (cheapest to spin
            # up, where KV pressure is felt first).
            if self.models:
                smallest = self.models[-1]
                for ctx_k in self.ctx_k:
                    self.probe_concurrency(smallest, ctx_k, run)

            # Per-plan full rounds
            if not skip_full_round:
                plans = plans or self._default_plans()
                co_pair: tuple[str, str] | None = None
                if plans:
                    for plan_name, (gm, seats) in plans.items():
                        missing_seats = [s[2] for s in seats if s[2] not in self.models]
                        if gm not in self.models:
                            self._log("plan_skip", plan=plan_name,
                                      reason=f"gm_model {gm!r} not in --models list "
                                             f"({self.models!r}). Add it or drop the "
                                             f"plan.")
                            continue
                        if missing_seats:
                            continue
                        self.probe_full_round(seats, gm, plan_name, run)
                        if co_pair is None and seats:
                            co_pair = (gm, seats[0][2])

                    # W0 item 4: can the GM *and* a seat co-reside on the
                    # shared GB10 KV-cache pool? Run the GM model of the
                    # first valid plan against that seat model in
                    # parallel.
                    if co_pair:
                        for ctx_k in self.ctx_k:
                            self.probe_co_resident(
                                gm_model=co_pair[0],
                                seat_model=co_pair[1],
                                ctx_k=ctx_k, run=run)
            else:
                self._log("full_round_skipped", reason="--skip-full-round")

            run.host_state_after = self._safe_state()
            run.finished_at = time.time()
            self._write_json(run)
            self._write_markdown(run)
            self._log("run_done", n_cells=len(run.cells),
                      dur_s=round(run.finished_at - run.started_at, 1))
        except Exception as exc:  # noqa: BLE001 - top-level catch: still write partial report
            run.error = repr(exc)
            run.finished_at = time.time()
            self._log("run_failed", err=repr(exc))
            self._write_json(run)
            self._write_markdown(run)
        finally:
            try:
                self.host.close()
            except Exception:
                pass
        return run

    def _default_plans(self) -> dict[str, tuple[str, list[tuple[str, str, str]]]]:
        """Plan names → (gm_model, seats).

        Each seat is (label, cast_for_sheet, model). The cast_for_sheet
        determines which real campaign sheet is loaded to build a
        realistic prompt; the model is what gets measured. When the real
        pack has fewer cast members than seats, we reuse sheets for
        padding (the prompt *shape* is the same — same character length
        of prose; just not a unique name) so the benchmark still reflects
        6-seat rounds as the design doc specifies."""
        cast = prompts.list_cast()
        if not cast:
            cast = ["chadwick", "Leena", "Vigil", "sodacan_bob"]

        def _seats(assignment: list[str]) -> list[tuple[str, str, str]]:
            """`assignment` is a list of model names, one per seat.
            Cycle through the real cast for the sheet keys so each seat
            gets a realistic prompt size."""
            return [
                (f"seat_{i+1}", cast[i % len(cast)], m)
                for i, m in enumerate(assignment)
            ]

        return {
            # 2026-09-20 re-spec of design doc §8, per first-baseline bench:
            # llama3.1:8b is the only model that cleanly emits a 45-word
            # line in budget; the thinking models are budget-capped at
            # 512 tokens. Plan A = recommended tiered split: 70b GM with
            # 8b seats. Plan B = 70b GM with 12b thinking seats as the
            # "quality tier" alternative (budget-capped flag expected).
            "A (70b GM + 6x8b seats)": (
                "hermes3:70b",
                _seats(["llama3.1:8b"] * 6),
            ),
            "B (70b GM + 6x12b seats)": (
                "hermes3:70b",
                _seats(["gemma4:12b-it-q4_K_M"] * 6),
            ),
        }

    def _safe_state(self) -> dict[str, Any]:
        try:
            st = self.host.state()
            return {
                "protocol": st.protocol,
                "note": st.note,
                "loaded": st.loaded,
                "metrics": st.metrics,
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Report writers
    # ------------------------------------------------------------------

    def _write_json(self, run: Run) -> None:
        target = self.output_dir / "run.json"
        target.write_text(
            json.dumps(asdict(run), indent=2, default=str),
            encoding="utf-8")

    def _write_markdown(self, run: Run) -> None:
        lines: list[str] = []

        def _f(x: float | None, nd: int = 2) -> str:
            return f"{x:.{nd}f}" if isinstance(x, (int, float)) else "—"

        def _f_co(x: float | None) -> str:
            return f"{x:.3f}" if isinstance(x, (int, float)) else "—"

        def rel(p: pathlib.Path) -> str:
            """Best-effort path relative to the repo root for the report
            footer (falls back to the absolute path if not expressible)."""
            try:
                return (p.resolve().relative_to(Path.cwd())).as_posix()
            except (ValueError, OSError):
                return p.as_posix()

        lines.append("# D&D-agents benchmark report")
        lines.append("")
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(run.started_at))
        dur = (run.finished_at - run.started_at) if run.finished_at else None
        lines.append(f"- **when**: {when}Z · **duration**: {round(dur,1) if dur else '—'}s")
        lines.append(f"- **host**: `{run.host_protocol}` @ `{run.base_url}`")
        lines.append(f"- **models**: {', '.join(run.config.get('models', []))}")
        lines.append(f"- **ctx tiers**: {run.config.get('ctx_k')}k")
        lines.append(f"- **think knob**: `{run.config.get('think')}` (OFF = content-only)")
        if run.error:
            lines.append("")
            lines.append(f"> **error**: `{run.error}` — partial results below.")
        lines.append("")

        # -- Per-model table -------------------------------------------------

        lines.append("## Per-model probes")
        lines.append("")
        lines.append("| model | ctx | task | dec tok/s | pre tok/s | ttft (s) | total (s) | content | reason | n_ok/n | capped |")
        lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---|---|")
        for c in run.cells:
            if c.task in ("full_round", "co_resident"):
                continue
            capped = "⚠ YES" if c.extra.get("budget_capped_on_reasoning") else "no"
            err = (c.error or "—")
            lines.append(
                f"| {c.model} | {c.ctx_k}k | {c.task} | {_f(c.decode_tps)} | "
                f"{_f(c.prefill_tps, 1)} | {_f(c.ttft_s, 3)} | {_f(c.total_s, 3)} | "
                f"{c.content_tokens} | {c.reasoning_tokens} | "
                f"{c.extra.get('n_ok', '?')}/{c.n_calls} | {capped} | {err} |"
            )
        lines.append("")

        # -- Co-resident (W0 item 4) ----------------------------------------

        cores = [c for c in run.cells if c.task == "co_resident"]
        if cores:
            lines.append("## Co-resident: GM + seat in parallel (W0 item 4)")
            lines.append("")
            lines.append("Can the big GM model and a seat model run *at the same time* "
                         "on this GB10? `speedup ≈ 2.0` = perfect co-residence "
                         "(both run concurrently). `speedup ≈ 1.0` = they're "
                         "serialising on the shared KV-cache pool, so the tiered "
                         "architecture gives no concurrency benefit.")
            lines.append("")
            lines.append("| pair | ctx | wall (s, shared) | solo-max (s) | solo-sum (s) | speedup |")
            lines.append("|---|---|---:|---:|---:|---:|")
            for c in cores:
                sp = c.extra.get("co_speedup_over_sum")
                sp_str = f"{sp:.2f}" if isinstance(sp, (int, float)) else "—"
                lines.append(
                    f"| {c.model} | {c.ctx_k}k | {_f_co(c.total_s)} | "
                    f"{_f_co(c.extra.get('solo_max_s'))} | "
                    f"{_f_co(c.extra.get('solo_sum_s'))} | {sp_str} |"
                )
            lines.append("")

        # -- Full-round table ------------------------------------------------

        rounds = [c for c in run.cells if c.task == "full_round"]
        if rounds:
            lines.append("## Full-round (plan) timings")
            lines.append("")
            lines.append("Time per full D&D round per design doc §4.4 — the "
                         "number that decides `deadline_s`. Lower is better, but "
                         "we want it well under the 45s `deadline_s` *per seat*.")
            lines.append("")
            lines.append("| plan | total (s) | n OK stages | think | err |")
            lines.append("|---|---:|---|---|---|")
            for c in rounds:
                lines.append(
                    f"| {c.model} | {c.total_s:.2f} | {c.n_calls} | {c.think} | "
                    f"{c.error or '—'} |"
                )
            lines.append("")

        # -- Host state -----------------------------------------------------

        lines.append("## Host state — before")
        lines.append("```json")
        lines.append(json.dumps(run.host_state_before, indent=2, default=str))
        lines.append("```")
        lines.append("")
        lines.append("## Host state — after")
        lines.append("```json")
        lines.append(json.dumps(run.host_state_after, indent=2, default=str))
        lines.append("```")
        lines.append("")
        lines.append(f"> **Raw JSON**: `{rel(self.output_dir / 'run.json')}`")
        lines.append(f"> **Live progress**: `tail -f {rel(self.output_dir / 'progress.jsonl')}`")
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="runBenchmark.sh",
        description=(
            "D&D-agents benchmark: per-model tok/s, per-plan time-per-round, "
            "concurrency, co-resident, host-state. See "
            ".claude/prompts/agent_dnd_architecture.md §13.\n"
            "\nValues are read from conf/benchmark.toml by default; any "
            "flag passed here overrides the corresponding key. See "
            "`--help` and the conf file for the full option set."
        ),
    )
    p.add_argument("--config", default=None,
                   help="TOML config path (default: conf/benchmark.toml; "
                        "or $BENCH_MARKER_CONFIG)")
    p.add_argument("--host", choices=["ollama", "vllm"], default=None,
                   help="override [host] name in the config")
    p.add_argument("--base-url", default=None,
                   help="override [host] base_url in the config")
    p.add_argument("--models", default=None,
                   help="override [models] list (comma-separated)")
    p.add_argument("--ctx-k", default=None,
                   help="override [run] ctx_k (comma-separated K tokens)")
    p.add_argument("--output", default=None,
                   help="override [output] dir")
    p.add_argument("--report", default=None,
                   help="override [output] report path")
    p.add_argument("--think", action="store_true", default=None,
                   help="override [run] think (force ON)")
    p.add_argument("--no-thinking", action="store_true", dest="no_thinking",
                   default=None,
                   help="override [run] think (force OFF)")
    p.add_argument("--temperature", type=float, default=None)
    p.add_argument("--concurrency-n", type=int, dest="concurrency_n",
                   default=None)
    p.add_argument("--skip-full-round", action="store_true",
                   dest="skip_full_round", default=None,
                   help="override [run] skip_full_round (per-model + "
                        "concurrency only)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the resolved plan without running")
    return p


def _cli_models_list(v: str | None) -> list[str] | None:
    if v is None:
        return None
    return [m for m in v.split(",") if m.strip()]


def _cli_ctx_list(v: str | None) -> list[int] | None:
    if v is None:
        return None
    return [int(c) for c in v.split(",") if c.strip()]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    cfg_path = args.config or None
    if not cfg_path and "BENCH_MARKER_CONFIG" in os.environ:
        cfg_path = os.environ["BENCH_MARKER_CONFIG"]
    try:
        cfg = load_config(cfg_path)
    except FileNotFoundError as exc:
        print(f"[bench] {exc}", file=sys.stderr)
        return 2

    # Layer CLI overrides onto the TOML config.
    think_override = args.think if args.think is not None \
        else (False if args.no_thinking else None)
    override_config_from_cli(
        cfg,
        host=args.host,
        base_url=args.base_url,
        models=_cli_models_list(args.models),
        ctx_k=_cli_ctx_list(args.ctx_k),
        output_dir=args.output,
        report_path=args.report,
        think=think_override,
        temperature=args.temperature,
        concurrency_n=args.concurrency_n,
        skip_full_round=args.skip_full_round,
    )
    resolve_paths(cfg, REPO_ROOT)

    if args.dry_run:
        out = str(cfg.output_dir) if cfg.output_dir else ""
        print("[dry-run] would benchmark:")
        print(f"  config       : {cfg_path or DEFAULT_CONF}")
        print(f"  host         : {cfg.host} @ {cfg.base_url}")
        print(f"  models       : {cfg.models}")
        print(f"  ctx_k        : {cfg.ctx_k}")
        print(f"  think        : {cfg.think}")
        print(f"  temperature  : {cfg.temperature}")
        print(f"  concurrency_n: {cfg.concurrency_n}")
        print(f"  skip_full_round: {cfg.skip_full_round}")
        print(f"  reports to   : {out}/run.json  {cfg.report_path}")
        return 0

    if not cfg.models:
        print("[bench] no models configured — set [models].list in "
              f"{cfg_path or DEFAULT_CONF} or pass --models", file=sys.stderr)
        return 2
    if not cfg.ctx_k:
        print("[bench] no context tiers configured — set [run].ctx_k in "
              f"{cfg_path or DEFAULT_CONF} or pass --ctx-k", file=sys.stderr)
        return 2

    if cfg.host == "ollama":
        host = OllamaHost(cfg.base_url)
    else:
        host = vLLMHost(cfg.base_url)

    if not host.health():
        print(f"[bench] host not reachable at {cfg.base_url}. "
              f"Is the {cfg.host} server running?", file=sys.stderr)
        return 3

    runner = Runner(
        host, models=cfg.models, ctx_k=cfg.ctx_k,
        output_dir=Path(cfg.output_dir),
        report_path=Path(cfg.report_path),
        think=cfg.think, temperature=cfg.temperature,
        concurrency_n=cfg.concurrency_n,
    )
    run = runner.run(skip_full_round=cfg.skip_full_round)

    print(f"[bench] done in {round((run.finished_at or 0) - run.started_at, 1)}s.")
    print(f"[bench] cells  : {len(run.cells)}")
    for c in run.cells:
        capped = c.extra.get("budget_capped_on_reasoning")
        cap_flag = "  [BUDGET-CAPPED-ON-THINKING]" if capped else ""
        print(
            f"  - {c.model:<45} {c.ctx_k:>2}k  {c.task:<14} "
            f"decode={f'{c.decode_tps:.1f}' if c.decode_tps else '-':>6} t/s "
            f"ttft={f'{c.ttft_s:.2f}' if c.ttft_s else '-':>5}s "
            f"total={f'{c.total_s:.2f}' if c.total_s else '-':>6}s "
            f"content={c.content_tokens} reason={c.reasoning_tokens}{cap_flag}"
        )
    print(f"[bench] report : {Path(cfg.report_path)}")
    print(f"[bench] JSON   : {Path(cfg.output_dir)}/run.json")
    print(f"[bench] live   : tail -f {Path(cfg.output_dir)}/progress.jsonl")
    print(f"[bench] config : {cfg_path or DEFAULT_CONF}")
    return 0 if not run.error else 1


if __name__ == "__main__":
    raise SystemExit(main())
