"""`table_round` probe (build plan P1.1): one live-table round on ONE shared model.

Two shapes, so the two-pass decision (plan §1 row 4 / §6 Q1) rests on data:

  one_pass  GM direction -> N seats in turn, each ONE call with reasoning ON
            (reasoning + spoken line) -> GM adjudication
  two_pass  GM direction -> N parallel THINK calls (reasoning ON, private
            intent) -> N sequential SPEAK calls (reasoning OFF, own intent +
            committed transcript) -> GM adjudication

Invariants (enforced here, asserted by tests/test_benchmarker_table_round.py):
- a spoken line is only produced with every previously committed line in its
  context (SPEAK is strictly sequential);
- a seat's private intent goes into THAT seat's SPEAK prompt only (plan U6);
- only THINK runs in parallel.

Prompt sizes follow the plan: a player brief is capped at 6000 chars, the GM
gets the full pack (~16k tokens). The prompts come from prompts.py, using the real
campaigns/<pack> sheets and lore.

CLI (the key is read from the env var named by --api-key-env, never printed):
  .venv/bin/python utilities/benchmarker/lib/table_round.py \
      --base-url http://localhost:8092 --model table-agents \
      --seats 4,7 --shapes two_pass,one_pass --repeats 2
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import prompts  # noqa: E402

BRIEF_MAX_CHARS = 6000
OUTPUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "output"


@dataclass
class Budgets:
    gm_direction: int = 1536      # reasoning ON: reasoning + direction
    think: int = 1024             # two_pass THINK: reasoning + <=40-word intent
    speak: int = 160              # two_pass SPEAK: reasoning OFF, one line
    one_pass: int = 1280          # one_pass seat: reasoning + line
    adjudication: int = 256       # reasoning OFF, JSON verdict
    gm_think: bool = True
    adjudication_think: bool = False
    # vLLM thinking_token_budget per stage (None = unbounded; needs the
    # server started with --reasoning-config). max_tokens still caps the total.
    reasoning_gm: int | None = None
    reasoning_think: int | None = None
    reasoning_seat: int | None = None
    reasoning_speak: int | None = None     # always-thinking models (Qwen3-Thinking-2507)
    reasoning_adjudication: int | None = None


@dataclass
class Stage:
    stage: str
    seat: str | None
    started_s: float
    total_s: float
    ttft_s: float
    prompt_tokens: int
    content_tokens: int
    reasoning_tokens: int
    finish_reason: str | None
    ok: bool
    budget_capped: bool
    error: str | None = None
    content: str = ""


@dataclass
class RoundResult:
    shape: str
    n_seats: int
    model: str
    wall_s: float
    stages: list[Stage] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        def times(prefix):
            return [s.total_s for s in self.stages if s.stage == prefix and s.ok]
        think = times("think")
        speak = times("speak") or times("seat_one_pass")
        return {
            "shape": self.shape, "n_seats": self.n_seats, "wall_s": round(self.wall_s, 1),
            "gm_direction_s": _first(times("gm_direction")),
            "think_pass_wall_s": (round(max(s.started_s + s.total_s for s in self.stages if s.stage == "think")
                                        - min(s.started_s for s in self.stages if s.stage == "think"), 1)
                                  if think else None),
            "think_p95_s": _p95(think),
            "speak_p50_s": _p50(speak),
            "speak_max_s": round(max(speak), 1) if speak else None,
            "adjudication_s": _first(times("adjudication")),
            "reasoning_tokens": sum(s.reasoning_tokens for s in self.stages),
            "content_tokens": sum(s.content_tokens for s in self.stages),
            "n_failed": sum(1 for s in self.stages if not s.ok),
            "n_budget_capped": sum(1 for s in self.stages if s.budget_capped),
        }


def _first(xs):
    return round(xs[0], 1) if xs else None


def _p50(xs):
    return round(statistics.median(xs), 1) if xs else None


def _p95(xs):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))], 1)


# ── prompts ────────────────────────────────────────────────────────────────

def seat_cast(n_seats: int, pack: str = prompts.DEFAULT_PACK) -> list[str]:
    cast = [c for c in prompts.list_cast(pack) if c.lower() != "gm"]
    if not cast:
        raise RuntimeError(f"pack {pack!r} has no cast")
    return [cast[i % len(cast)] for i in range(n_seats)]


def seat_label(i: int, cast_id: str) -> str:
    return f"tuber_{i + 1}:{cast_id}"


def brief_for(cast_id: str, pack: str = prompts.DEFAULT_PACK) -> tuple[str, str]:
    """(name, brief<=6000 chars). Stand-in for character.brief (P2.6): the sheet
    plus the pack's lore, which is the right size for a player brief."""
    sheet = prompts.load_cast_sheet(cast_id, pack)
    parts = [f"# Who you are\nYou are {sheet.name}, {sheet.archetype}.\n{sheet.system_prompt}",
             "# What you know"]
    for stem in prompts.list_lore_stems(pack):
        parts.append(f"## {stem}\n{prompts.load_lore(stem, pack, limit_words=180)}")
    parts.append("# How you play\nStay in character, first person. Yes-and the GM. "
                 "Never speak for another character. Never break meta.")
    return sheet.name, "\n\n".join(parts)[:BRIEF_MAX_CHARS]


def _transcript(lines: list[str]) -> str:
    return "\n".join(lines) if lines else "(nothing said yet this round)"


def think_prompt(name, brief, direction, committed):
    system = brief + ("\n\nYou are deciding PRIVATELY what you want to do next. Nobody "
                      "else sees this. Reply with your private intent in at most 40 "
                      "words: what you want, and what you will say or do.")
    user = (f"=== GM DIRECTION ===\n{direction}\n\n=== COMMITTED TRANSCRIPT ===\n"
            f"{_transcript(committed)}\n\nYour private intent, {name}:")
    return system, user


def speak_prompt(name, brief, direction, committed, intent):
    system = brief + ("\n\nReply with exactly ONE spoken line in character, at most 45 "
                      "words, no name label, no quotation marks, no stage directions.")
    user = (f"=== GM DIRECTION ===\n{direction}\n\n=== COMMITTED TRANSCRIPT ===\n"
            f"{_transcript(committed)}\n\n")
    if intent is not None:
        user += f"=== YOUR PRIVATE INTENT (only you know this) ===\n{intent}\n\n"
    user += f"Your line, {name}:"
    return system, user


def gm_direction_prompt(pack, seat_names):
    bundle = prompts.build_gm_prompt(
        canon_goal="The party opens the vault beneath Malmont and finds the moonwell sigil broken.",
        expected_beats=[f"{n} reacts to the broken sigil in their own way" for n in seat_names],
        committed_transcript_lines=prompts.load_transcript_example_lines(4, pack),
        target_context_tokens=16_000, pack_name=pack)
    return str(bundle["system"]), str(bundle["user"])


def adjudication_prompt(committed, seat_names):
    system = ("You are the GM judging the round. Reply with ONLY a JSON object: "
              '{"verdict": "pass"|"retake", "retake_seat": null|"<name>", "reason": "<one line>"}.')
    user = (f"Seats: {', '.join(seat_names)}\n\n=== ROUND TRANSCRIPT ===\n"
            f"{_transcript(committed)}\n\nVerdict:")
    return system, user


# ── the probe ──────────────────────────────────────────────────────────────

class TableRoundProbe:
    """`host` is anything with vLLMHost.complete(model=, system=, user=,
    num_predict=, think=, temperature=) -> CompletionResult."""

    def __init__(self, host, model: str, budgets: Budgets | None = None,
                 temperature: float = 0.7, pack: str = prompts.DEFAULT_PACK,
                 clock=time.perf_counter):
        self.host = host
        self.model = model
        self.b = budgets or Budgets()
        self.temperature = temperature
        self.pack = pack
        self.clock = clock

    def _call(self, stage, seat, system, user, num_predict, think, t_round, budget=None):
        start = self.clock()
        extra = {} if budget is None else {"thinking_token_budget": budget}
        try:
            r = self.host.complete(model=self.model, system=system, user=user,
                                   num_predict=num_predict, think=think,
                                   temperature=self.temperature, **extra)
        except Exception as exc:  # noqa: BLE001 - recorded, round continues
            return Stage(stage, seat, start - t_round, self.clock() - start, 0.0,
                         0, 0, 0, None, False, False, error=repr(exc))
        content = (r.content_text or "").strip()
        capped = r.finish_reason == "length"
        return Stage(stage, seat, start - t_round, r.total_s, r.ttft_s, r.prompt_tokens,
                     r.content_tokens, r.reasoning_tokens, r.finish_reason,
                     ok=bool(content), budget_capped=capped,
                     error=None if content else "empty content", content=content)

    def run(self, shape: str, n_seats: int) -> RoundResult:
        if shape not in ("one_pass", "two_pass"):
            raise ValueError(f"unknown shape {shape!r}")
        seats = []
        for i, cast_id in enumerate(seat_cast(n_seats, self.pack)):
            name, brief = brief_for(cast_id, self.pack)
            seats.append((seat_label(i, cast_id), name, brief))
        res = RoundResult(shape, n_seats, self.model, 0.0)
        t0 = self.clock()

        gs, gu = gm_direction_prompt(self.pack, [s[1] for s in seats])
        d = self._call("gm_direction", "gm", gs, gu + "\n\nWrite the GM direction now (<=120 words).",
                       self.b.gm_direction, self.b.gm_think, t0, self.b.reasoning_gm)
        res.stages.append(d)
        direction = d.content or "(the GM gestures at the broken sigil)"

        committed: list[str] = []
        intents: dict[str, str | None] = {s[0]: None for s in seats}

        if shape == "two_pass":
            # Every seat reasons in parallel over the SAME committed snapshot.
            snapshot = list(committed)

            def think(seat):
                label, name, brief = seat
                s, u = think_prompt(name, brief, direction, snapshot)
                return self._call("think", label, s, u, self.b.think, True, t0,
                                  self.b.reasoning_think)

            with ThreadPoolExecutor(max_workers=n_seats) as pool:
                for st in pool.map(think, seats):
                    res.stages.append(st)
                    intents[st.seat] = st.content or None

        for label, name, brief in seats:              # strictly in turn order
            if shape == "two_pass":
                s, u = speak_prompt(name, brief, direction, committed, intents[label])
                st = self._call("speak", label, s, u, self.b.speak, False, t0,
                                self.b.reasoning_speak)
            else:
                s, u = speak_prompt(name, brief, direction, committed, None)
                st = self._call("seat_one_pass", label, s, u, self.b.one_pass, True, t0,
                                self.b.reasoning_seat)
            res.stages.append(st)
            if st.content:
                committed.append(f"{name}: {st.content}")

        s, u = adjudication_prompt(committed, [x[1] for x in seats])
        res.stages.append(self._call("adjudication", "gm", s, u, self.b.adjudication,
                                     self.b.adjudication_think, t0,
                                     self.b.reasoning_adjudication))
        res.wall_s = self.clock() - t0
        return res


# ── CLI ────────────────────────────────────────────────────────────────────

def _markdown(rows: list[dict[str, Any]], model: str) -> str:
    cols = ["shape", "n_seats", "wall_s", "gm_direction_s", "think_pass_wall_s", "think_p95_s",
            "speak_p50_s", "speak_max_s", "adjudication_s", "reasoning_tokens",
            "n_failed", "n_budget_capped"]
    out = [f"# table_round: {model}", "", "| " + " | ".join(cols) + " |",
           "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c)) for c in cols) + " |")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--base-url", default="http://localhost:8092")
    p.add_argument("--model", default="table-agents")
    p.add_argument("--api-key-env", default="VLLM_API_KEY")
    p.add_argument("--env-file", default=None,
                   help="read the key line <api-key-env>=... from this file if the env var is unset")
    p.add_argument("--seats", default="4,7")
    p.add_argument("--shapes", default="two_pass,one_pass")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--pack", default=prompts.DEFAULT_PACK)
    p.add_argument("--tag", default="")
    for name, default in asdict(Budgets()).items():
        if isinstance(default, bool):
            continue
        p.add_argument(f"--budget-{name.replace('_', '-')}", type=int, default=default,
                       dest=f"budget_{name}")   # reasoning_* default None = unbounded
    args = p.parse_args(argv)

    from vllm_host import vLLMHost
    key = os.environ.get(args.api_key_env)
    if not key and args.env_file:
        for line in pathlib.Path(args.env_file).read_text().splitlines():
            if line.startswith(args.api_key_env + "="):
                key = line.split("=", 1)[1].strip() or None
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    host = vLLMHost(args.base_url, headers=headers)
    budgets = Budgets(**{k: getattr(args, f"budget_{k}") for k, v in asdict(Budgets()).items()
                         if not isinstance(v, bool)})
    probe = TableRoundProbe(host, args.model, budgets, pack=args.pack)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_json = OUTPUT_DIR / f"table_round_{args.tag + '_' if args.tag else ''}{stamp}.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rounds, rows = [], []
    for rep in range(args.repeats):
        for n in [int(x) for x in args.seats.split(",")]:
            for shape in args.shapes.split(","):
                r = probe.run(shape, n)
                row = r.summary() | {"repeat": rep}
                rows.append(row)
                rounds.append(asdict(r))
                print("[table_round]", json.dumps(row), flush=True)
                out_json.write_text(json.dumps({"model": args.model, "budgets": asdict(budgets),
                                                "rows": rows, "rounds": rounds}, indent=1))
    md = _markdown(rows, args.model)
    out_json.with_suffix(".md").write_text(md)
    print(md)
    print(f"[table_round] wrote {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
