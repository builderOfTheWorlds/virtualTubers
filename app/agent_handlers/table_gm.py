"""GM agent + table runtime for the live agent table (build plan P3.6).

The GM worker (role ``table_gm``, worker id ``tuber_0``) owns the turn arbiter
(``app/turns.py``). This module provides:

- ``LLMGM``: the arbiter's ``GMPort`` over the shared vLLM model (direction,
  JSON adjudication, overrule line), each call with its own token + reasoning
  budgets.
- ``build_check``: the per-seat commit validator the arbiter calls before
  committing a reply (``table.commit_check``).
- ``TableRuntime``: loads the scene contracts, runs one ``Arbiter`` per scene,
  feeds it table replies, ticks it and advances to the next contract on resolve.
- The worker hooks: ``table_gm_idle_tick`` and ``handle_table_message``.

The worker loop is single-threaded and blocks while a hook runs, so nothing
here sleeps or waits: the ``Arbiter`` is step-driven. A hook never raises.
"""
import json
import logging
import re
import time
from typing import Callable, Optional

from table import commit_check, protocol
from turns import (
    ADJUDICATING,
    ABORTED,
    DIRECTING,
    RESOLVED,
    SPEAK,
    TERMINAL,
    THINK,
    Arbiter,
    Direction,
    GMUnavailable,
    Verdict,
)

log = logging.getLogger(__name__)

ROLE = "table_gm"

# Per-call budget defaults (max_tokens / reasoning_budget).
_DEFAULT_BUDGETS = {
    "direct": {"max_tokens": 768, "reasoning_budget": 384},
    "adjudicate": {"max_tokens": 256, "reasoning_budget": 64},
    "overrule": {"max_tokens": 160, "reasoning_budget": 48},
}

_DIRECTION_INSTRUCTION = (
    "Narrate what happens next, moving the scene toward the contract's canon_goal. "
    "Keep it to at most 120 words. Address the seated players by their names and "
    "never speak for them. Begin your reply with the narration only (no 'GM:' label)."
)

_ADJUDICATE_INSTRUCTION = (
    "Adjudicate the round. Reply with ONLY a JSON object of the form "
    '{"verdict": "pass"|"reject", "retake_seat": null|name, "notes": str, '
    '"resolved": bool, "state_delta": object}. No prose, no code fences.'
)

_OVERRULE_INSTRUCTION = (
    "Write the single line the given seat says, in that character's voice, so the "
    "scene can move on. Reply with the line only (no name label, no quotes)."
)

# A leading "GM:" / "GM -" / "**GM:**" style label on a direction line.
_GM_LABEL_RE = re.compile(r"^\s*(?:\*\*)?GM(?:\*\*)?(?:\s*[:\-]|\s+[-\u2013\u2014])\s*",
                          re.IGNORECASE)

_QUOTES = "\"'\u201c\u201d\u2018\u2019"

# A leading "<Name>:" label (optionally bolded) on an overrule line.
_NAME_LABEL_RE = re.compile(r"^\s*(?:\*\*)?([^\s:*\u2013\u2014][^:]*?)(?:\*\*)?\s*[:\-]\s*")


def _budget(budgets, key):
    """(max_tokens, reasoning_budget) for a call kind, with defaults."""
    entry = (budgets or {}).get(key) or {}
    max_tokens = entry.get("max_tokens", _DEFAULT_BUDGETS[key]["max_tokens"])
    reasoning = entry.get("reasoning_budget", _DEFAULT_BUDGETS[key]["reasoning_budget"])
    return max_tokens, reasoning


def _render_transcript(transcript, seat_names):
    """Render a transcript list as "<name>: <text>" lines (name via seat_names)."""
    lines = []
    for entry in transcript or []:
        if not isinstance(entry, dict):
            continue
        speaker = entry.get("speaker")
        name = seat_names.get(speaker, speaker)
        lines.append(f"{name}: {entry.get('text', '')}")
    return "\n".join(lines)


def _strip_quotes(text):
    prev = None
    while prev != text:
        prev = text
        text = text.strip().strip(_QUOTES)
    return text


def _first_json_object(content):
    """Parse the first {...} object in `content`, tolerating prose and fences."""
    if not isinstance(content, str):
        return None
    start = content.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(content)):
            ch = content[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(content[start:i + 1])
                    except (ValueError, TypeError):
                        break
        start = content.find("{", start + 1)
    return None


class LLMGM:
    """The arbiter's ``GMPort`` over the shared vLLM model."""

    def __init__(self, llm_client, context_fn, *, seat_names, budgets):
        self._llm = llm_client
        self._context_fn = context_fn
        self._seat_names = dict(seat_names or {})
        self._budgets = budgets or {}
        self._gm_seat = self._budgets.get("gm_seat", "tuber_0")
        # display name (casefolded) -> seat id, for retake_seat mapping.
        self._name_to_seat = {}
        for seat, name in self._seat_names.items():
            if name:
                self._name_to_seat.setdefault(str(name).casefold(), seat)

    # ------------------------------------------------------------------ GMPort
    def direct(self, contract, transcript, round):
        system = self._context_fn(contract) + "\n\n" + _DIRECTION_INSTRUCTION
        user = _render_transcript(transcript, self._seat_names)
        user = (user + "\n" if user else "") + f"Round {round}."
        max_tokens, reasoning = _budget(self._budgets, "direct")
        _, content = self._llm.complete_stream(
            system, [{"role": "user", "content": user}],
            max_tokens=max_tokens, reasoning_budget=reasoning)
        text = (content or "").strip()
        text = _GM_LABEL_RE.sub("", text, count=1).strip()
        expects = [s for s in contract.get("participants", []) if s != self._gm_seat]
        must = contract.get("must_resolve") or []
        must_resolve = "; ".join(must) if must else (contract.get("canon_goal") or "")
        return Direction(text=text, expects=expects, must_resolve=must_resolve)

    def adjudicate(self, contract, transcript, round):
        system = self._context_fn(contract) + "\n\n" + _ADJUDICATE_INSTRUCTION
        user = _render_transcript(transcript, self._seat_names)
        user = (user + "\n" if user else "") + f"Round {round}."
        max_tokens, reasoning = _budget(self._budgets, "adjudicate")
        _, content = self._llm.complete_stream(
            system, [{"role": "user", "content": user}],
            max_tokens=max_tokens, reasoning_budget=reasoning)
        obj = _first_json_object(content)
        if not isinstance(obj, dict):
            raise ValueError("adjudicate: no parsable JSON object in GM reply")
        verdict = obj.get("verdict")
        if verdict == "retake":
            verdict = "reject"
        if verdict not in ("pass", "reject"):
            raise ValueError(f"adjudicate: invalid verdict {verdict!r}")
        retake_seat = self._map_seat(obj.get("retake_seat"))
        notes = obj.get("notes")
        notes = notes if isinstance(notes, str) else ""
        resolved = bool(obj.get("resolved", False))
        state_delta = obj.get("state_delta")
        state_delta = state_delta if isinstance(state_delta, dict) else {}
        return Verdict(verdict=verdict, retake_seat=retake_seat, notes=notes,
                       state_delta=state_delta, resolved=resolved)

    def overrule(self, contract, transcript, seat, reason):
        name = self._seat_names.get(seat, seat)
        system = self._context_fn(contract) + "\n\n" + _OVERRULE_INSTRUCTION
        user = _render_transcript(transcript, self._seat_names)
        user = (user + "\n" if user else "") + (
            f"Seat {seat} ({name}) must say one line to move on. Reason: {reason}")
        max_tokens, reasoning = _budget(self._budgets, "overrule")
        _, content = self._llm.complete_stream(
            system, [{"role": "user", "content": user}],
            max_tokens=max_tokens, reasoning_budget=reasoning)
        text = (content or "").strip()
        text = _NAME_LABEL_RE.sub("", text, count=1).strip()
        text = _strip_quotes(text)
        return text

    # ------------------------------------------------------------------ helpers
    def _map_seat(self, value):
        """Map a retake_seat value (seat id or display name) to a seat id."""
        if value is None:
            return None
        value = str(value).strip()
        if not value:
            return None
        if value in self._seat_names:
            return value
        return self._name_to_seat.get(value.casefold())


def build_check(table_cfg):
    """Build the per-seat commit validator the arbiter calls before committing."""
    seat_names = table_cfg.get("seat_names", {})
    seats = table_cfg.get("seats", [])
    cast_names = {seat: name for seat, name in seat_names.items() if seat in seats}
    silent_seats = frozenset(table_cfg.get("silent_seats", ()))
    forbidden = table_cfg.get("forbidden_phrases", {})
    max_words = table_cfg.get("max_words", 60)
    max_lines = table_cfg.get("max_lines", 2)

    def check(seat, text):
        phrases = tuple(forbidden.get(seat, ()))
        ctx = commit_check.CommitContext(
            seat=seat, cast_names=cast_names, silent_seats=silent_seats,
            forbidden_phrases=phrases, max_words=max_words, max_lines=max_lines)
        return commit_check.check_reply(text, ctx)

    return check


def _pack_cast(table_cfg):
    slugs = list((table_cfg.get("seat_slugs") or {}).values())
    return slugs or list(table_cfg.get("pack_cast", []))


def contracts_provider(agent_config):
    """Production default: the run's scene contracts as scene_start payloads.

    Review fixes (orchestrator): pack_cast comes from seat_slugs (the generated
    config has no pack_cast key), and with only_with_active_players a contract
    is kept only if at least one ACTIVE seat participates (the slice seats one
    player; a contract naming only unseated players would have nobody speak).
    """
    table_cfg = agent_config.get("table", {})
    from table import arc_source, contract

    if table_cfg.get("contracts_fixture"):
        artifacts = arc_source.RunArtifacts.from_json(table_cfg["contracts_fixture"])
    else:
        conn = arc_source.connect_generator()
        try:
            artifacts = arc_source.load_run(conn, table_cfg["run_id"])
        finally:
            try:
                conn.close()
            except Exception:  # pragma: no cover - connection already gone
                pass
    contracts = contract.contracts_for_run(
        artifacts, seat_of=table_cfg.get("seat_of", {}), pack_cast=_pack_cast(table_cfg))
    payloads = [c.to_payload() for c in contracts]
    if table_cfg.get("only_with_active_players"):
        active = set(table_cfg.get("seats", []))
        payloads = [p for p in payloads if active & set(p.get("participants", []))]
    start = int(table_cfg.get("start_index", 0))
    payloads = payloads[start:]
    limit = table_cfg.get("max_scenes")
    if limit is not None:
        payloads = payloads[:int(limit)]
    log.info("table contracts loaded=%d (start=%d limit=%s)", len(payloads), start, limit)
    return payloads


_CHAR_CONN = None
_LORE_CACHE = {}

GM_IDENTITY = ("You are the Game Master of this table. You narrate, direct, judge the "
               "players' lines against the scene contract and keep the story on its spine. "
               "You alone know the truth below; never reveal it except where a secret's "
               "reveal_at scene has come.")


def _character_conn():
    """Process-wide character DB connection (re-opened after an error)."""
    global _CHAR_CONN
    if _CHAR_CONN is None or getattr(_CHAR_CONN, "closed", 1):
        from character import config as char_config, db as char_db
        _CHAR_CONN = char_db.connect(char_config.load())
    return _CHAR_CONN


def _pack_lore(pack_dir):
    if not pack_dir:
        return {}
    if pack_dir not in _LORE_CACHE:
        from pathlib import Path
        lore_dir = Path(pack_dir) / "lore"
        _LORE_CACHE[pack_dir] = {p.stem: p.read_text(encoding="utf-8")
                                 for p in sorted(lore_dir.glob("*.md"))} if lore_dir.is_dir() else {}
    return _LORE_CACHE[pack_dir]


def context_provider(agent_config, contract):
    """Production default: GM context from the character DB (review rewrite: the
    generated version called functions that do not exist).

    gm_blocks of the GM baseline + the ACTIVE players' seat briefs (the GM sees
    every seated sheet) + the scene contract.
    """
    global _CHAR_CONN
    table_cfg = agent_config.get("table", {})
    from character.store import characters
    from table import gm_context, seat_brief

    lore = _pack_lore(table_cfg.get("pack_dir"))
    active = set(table_cfg.get("seats", []))
    try:
        conn = _character_conn()
        blocks = characters.gm_blocks(conn, table_cfg.get("gm_slug", "gm")) or {}
        sheets = {seat: seat_brief.build_table_brief(conn, slug, lore=lore)
                  for seat, slug in (table_cfg.get("seat_slugs") or {}).items() if seat in active}
        conn.rollback()
    except Exception:
        if _CHAR_CONN is not None:
            try:
                _CHAR_CONN.close()
            except Exception:  # pragma: no cover
                pass
        _CHAR_CONN = None
        raise
    identity = GM_IDENTITY + ("\n\n" + agent_config["system_prompt"].strip()
                              if agent_config.get("system_prompt") else "")
    return gm_context.build_gm_context(
        gm_blocks=blocks, gm_identity=identity, player_sheets=sheets,
        segment=None, contract=contract,
        blocks_order=table_cfg.get("gm_blocks_order", []),
        max_chars=table_cfg.get("gm_context_max_chars", 24000))


class TableRuntime:
    """Loads scene contracts, runs one ``Arbiter`` per scene, advances on resolve."""

    def __init__(self, worker_id, agent_config, llm_client, producer, *,
                 clock=time.monotonic, store=None):
        self.worker_id = worker_id
        self.agent_config = agent_config
        self.llm_client = llm_client
        self.producer = producer
        self.clock = clock
        self.store = store
        self.table_cfg = agent_config.get("table", {})
        self._contracts = None
        self.index = 0
        self.arbiter = None
        self._context_cache = {}

    # ------------------------------------------------------------------ lazy
    @property
    def contracts(self):
        if self._contracts is None:
            self._contracts = list(contracts_provider(self.agent_config))
        return self._contracts

    def _context(self, contract):
        scene_id = contract.get("scene_id")
        if scene_id not in self._context_cache:
            self._context_cache[scene_id] = context_provider(self.agent_config, contract)
        return self._context_cache[scene_id]

    # ------------------------------------------------------------------ API
    def ensure_started(self):
        if self.arbiter is not None:
            return
        if self.index >= len(self.contracts):
            return
        contract = self.contracts[self.index]
        seats = self.table_cfg.get("seats", [])
        gm_seat = self.table_cfg.get("gm_seat", "tuber_0")
        gm = LLMGM(self.llm_client, self._context,
                   seat_names=self.table_cfg.get("seat_names", {}),
                   budgets=self.table_cfg)
        check = build_check(self.table_cfg)
        try:
            arbiter = Arbiter(
                contract, seats, gm, check, send=self.producer.send,
                clock=self.clock, gm_seat=gm_seat,
                think_s=self.table_cfg.get("think_s", 60),
                speak_s=self.table_cfg.get("speak_s", 45),
                store=self.store, arbiter_id=self.worker_id)
        except Exception as exc:  # noqa: BLE001 - a bad contract must not kill the hook
            log.warning("table_gm cannot build arbiter scene=%s: %s", contract.get("scene_id"), exc)
            self.index += 1          # skip the bad contract instead of retrying it forever
            return
        # Keep the arbiter even if start() fails: it stays in DIRECTING and its own
        # tick() retries the GM call (review fix: re-creating it re-sent scene_start
        # on every tick).
        self.arbiter = arbiter
        try:
            arbiter.start()
        except GMUnavailable as exc:
            log.warning("table_gm start: GM unavailable scene=%s: %s", contract.get("scene_id"), exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("table_gm start failed scene=%s: %s", contract.get("scene_id"), exc)

    def on_message(self, msg):
        if self.arbiter is not None:
            self.arbiter.on_message(msg)

    def tick(self):
        self.ensure_started()
        if self.arbiter is None:
            return
        self.arbiter.tick()
        if self.arbiter.state["phase"] in TERMINAL and self.table_cfg.get("auto_advance", True):
            self.index += 1
            self.arbiter = None
            self.ensure_started()


_RUNTIMES = {}


def runtime_for(worker_id):
    return _RUNTIMES.get(worker_id)


def reset_runtimes():
    _RUNTIMES.clear()


def _make_runtime(worker_id, agent_config, llm_client, producer, clock):
    rt = TableRuntime(worker_id, agent_config, llm_client, producer, clock=clock)
    _RUNTIMES[worker_id] = rt
    return rt


def table_gm_idle_tick(worker_id, agent_config, llm_client, producer, state_path=None, *,
                       clock=time.monotonic):
    """IDLE_TICK_HOOKS["table_gm"]: tick the GM runtime. Never raises."""
    try:
        if (agent_config or {}).get("role") != ROLE:
            return
        rt = _RUNTIMES.get(worker_id)
        if rt is None:
            rt = _make_runtime(worker_id, agent_config, llm_client, producer, clock)
        rt.tick()
    except Exception as exc:  # noqa: BLE001 - a hook must never raise
        log.warning("table_gm_idle_tick failed worker=%s: %s", worker_id, exc)


def handle_table_message(worker_id, agent_config, llm_client, producer, msg, state_path=None,
                         coding_backend=None):
    """MESSAGE_HANDLERS for table messages: route to the GM runtime. Never raises."""
    try:
        if (agent_config or {}).get("role") != ROLE:
            return
        rt = _RUNTIMES.get(worker_id)
        if rt is None:
            rt = _make_runtime(worker_id, agent_config, llm_client, producer, time.monotonic)
        rt.on_message(msg)
    except Exception as exc:  # noqa: BLE001 - a hook must never raise
        log.warning("handle_table_message failed worker=%s: %s", worker_id, exc)
