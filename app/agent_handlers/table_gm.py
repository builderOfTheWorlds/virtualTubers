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
import random
import re
import time
from typing import Callable, Optional

from table import ambient_source, block_clock, commit_check, control, protocol
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
    # 512: a 6-seat round's notes + state_delta overran 256 and the cut JSON was
    # unparsable (live office run 2026-10-08: two failures -> scene_stuck).
    "adjudicate": {"max_tokens": 512, "reasoning_budget": 64},
    "overrule": {"max_tokens": 160, "reasoning_budget": 48},
}

_DIRECTION_INSTRUCTION = (
    "Narrate what happens NEXT, moving the scene toward the contract's canon_goal. "
    "Never repeat or paraphrase a line already in the transcript. "
    "Keep it to at most 120 words. Address the seated players by their names and "
    "never speak for them. Begin your reply with the narration only (no 'GM:' label)."
)

# Ambient office scenes (table.ambient_source) are plot-free filler between the story blocks.
_AMBIENT_DIRECTION_INSTRUCTION = (
    "This is an AMBIENT, plot-free scene of ordinary office life. Narrate what happens NEXT in "
    "the small moment the scene contract describes. Do not advance or resolve the story: "
    "nothing is decided, announced, discovered or changed that matters later. "
    "Never repeat or paraphrase a line already in the transcript. "
    "Keep it to at most 90 words. Address the seated players by their names and "
    "never speak for them. Begin your reply with the narration only (no 'GM:' label)."
)
_AMBIENT_MUST_RESOLVE = ("Keep it light and in the moment. Nothing needs to be settled, decided "
                         "or announced; do not move the story forward.")
_AMBIENT_ADJUDICATE_INSTRUCTION = (
    "This is an AMBIENT, plot-free scene: nothing to resolve and no story state may change. "
    "Set resolved to true once the small moment has played out, and state_delta to {}. "
)

_ADJUDICATE_INSTRUCTION = (
    "Adjudicate the round. Reply with ONLY a JSON object of the form "
    '{"verdict": "pass"|"reject", "retake_seat": null|name, "notes": str, '
    '"resolved": bool, "state_delta": object}. No prose, no code fences. '
    "Keep notes to one short sentence and state_delta to at most three keys. "
    "notes says WHAT HAPPENED in the story this round (events, who is where now), "
    "not how well the players did: it is the table's memory of this scene."
)

# The silent Party Member is the narration's "you" (office table). A direction that gives
# "you" a spoken line breaks the silent-seat rule (live run 2026-10-10: '"Fair?" you ask').
_YOU_SPEAK_RE = re.compile(
    r"\byou\s+(?:ask|asks|asked|say|says|said|reply|replied|answer|answered|whisper|"
    r"whispered|murmur|murmured|mutter|muttered|call|called|add|added|tell|told)\b",
    re.IGNORECASE)

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


# Contract fields that state what the scene is FOR. A GM-only phrase named here is
# the scene's own reveal, so the GM may say it (lore names only attach reference
# text, so they don't count).
_GOAL_FIELDS = ("canon_goal", "must_resolve", "expected_beats")


def _revealed_by_contract(contract, phrases):
    """The gm_forbidden_phrases this scene's goal itself reveals.

    2026-10-09 hour run: the e309 story's Grovley scenes ARE the Begene reveal,
    and the static forbidden list made the GM refuse them twice each, so all
    three were auto-skipped and the reveal never aired. User decision: allow a
    secret phrase when the scene's goal is that reveal."""
    goal = " ".join(
        " ".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v or "")
        for v in (contract.get(f) for f in _GOAL_FIELDS)).casefold()
    return {p for p in phrases if str(p).casefold() in goal}


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

    def __init__(self, llm_client, context_fn, *, seat_names, budgets, recent_fn=None,
                 repeat_threshold=0.5):
        self._llm = llm_client
        self._context_fn = context_fn
        # recent_fn() -> GM directions of the last scenes (table.story_log). Office loop
        # fix 2026-10-10: every scene reopened with the same arrival narration.
        self._recent_fn = recent_fn
        self._repeat_threshold = float(repeat_threshold)
        self._seat_names = dict(seat_names or {})
        self._budgets = budgets or {}
        self._gm_seat = self._budgets.get("gm_seat", "tuber_0")
        # display name (casefolded) -> seat id, for retake_seat mapping.
        self._name_to_seat = {}
        for seat, name in self._seat_names.items():
            if name:
                self._name_to_seat.setdefault(str(name).casefold(), seat)

    # ------------------------------------------------------------------ GMPort
    def direct(self, contract, transcript, round, _note=None):
        ambient = bool(contract.get("ambient"))
        system = self._context_fn(contract) + "\n\n" + (
            _AMBIENT_DIRECTION_INSTRUCTION if ambient else _DIRECTION_INSTRUCTION)
        if _note:
            system += "\n" + _note
        phrases = self._budgets.get("gm_forbidden_phrases", ())
        unlocked = _revealed_by_contract(contract, phrases)
        locked = [p for p in phrases if p not in unlocked]
        if locked:
            # The check below rejects these; naming them up front lets the model
            # avoid them instead of burning both tries (run 2: a reveal scene was
            # skipped after two refusals on a still-locked phrase).
            system += ("\nThese words are secret in this scene; never use them: "
                       + ", ".join(map(str, locked)) + ".")
        user = _render_transcript(transcript, self._seat_names)
        user = (user + "\n" if user else "") + f"Round {round}."
        max_tokens, reasoning = _budget(self._budgets, "direct")
        _, content = self._llm.complete_stream(
            system, [{"role": "user", "content": user}],
            max_tokens=max_tokens, reasoning_budget=reasoning)
        text = (content or "").strip()
        text = _GM_LABEL_RE.sub("", text, count=1).strip()
        lowered = text.casefold()
        leaked = [p for p in locked if str(p).casefold() in lowered]
        if re.search(r"gm block", lowered) or re.search(r"^\s*#", text, re.MULTILINE) or leaked:
            # 2026-10-08: GLM-4.7-Flash pasted its "GM block: truth" context into the
            # public narration. GM-only text never reaches the table; the arbiter retries.
            why = (f"phrases={leaked}" if leaked else
                   "'gm block'" if "gm block" in lowered else "markdown heading")
            raise ValueError(f"direction contains GM-only context ({why})")
        copied = commit_check.repeats_prior(text, [e.get("text") for e in transcript or []])
        if copied is not None:
            # 2026-10-08: the MoE GM copied a player's line as its direction. Raising
            # makes the arbiter retry on its next tick (and alarm after two).
            raise ValueError("direction repeats a transcript line")
        if self._budgets.get("gm_silent_you") and _YOU_SPEAK_RE.search(text):
            if _note is None:
                log.info("table gm direction gives the silent 'you' a line; asking again scene=%s",
                         contract.get("scene_id"))
                return self.direct(contract, transcript, round, _note=(
                    "You wrote a spoken line for \"you\" (the silent Party Member). They never "
                    "speak. Narrate again without giving them any words."))
            raise ValueError("direction gives the silent seat a spoken line")
        recent = tuple(self._recent_fn() or ()) if self._recent_fn is not None else ()
        earlier = repeats_recent(text, recent, self._repeat_threshold)
        if earlier is not None:
            if _note is None:
                log.info("table gm direction repeats an earlier scene; asking again scene=%s",
                         contract.get("scene_id"))
                return self.direct(contract, transcript, round, _note=(
                    "Your narration repeated an earlier scene's opening (\"" + str(earlier)[:120]
                    + "\"). That already happened. Continue from where the story is now; "
                    "do not restage arrivals or greetings."))
            # Never block the table on this: commit it and leave a trace.
            log.warning("table gm direction still repeats an earlier scene scene=%s",
                        contract.get("scene_id"))
        expects = [s for s in contract.get("participants", []) if s != self._gm_seat]
        must = contract.get("must_resolve") or []
        must_resolve = "; ".join(must) if must else (contract.get("canon_goal") or "")
        if ambient:
            must_resolve = _AMBIENT_MUST_RESOLVE
        return Direction(text=text, expects=expects, must_resolve=must_resolve)

    def adjudicate(self, contract, transcript, round):
        ambient = bool(contract.get("ambient"))
        system = self._context_fn(contract) + "\n\n" + (
            _AMBIENT_ADJUDICATE_INSTRUCTION if ambient else "") + _ADJUDICATE_INSTRUCTION
        user = _render_transcript(transcript, self._seat_names)
        user = (user + "\n" if user else "") + f"Round {round}."
        max_tokens, reasoning = _budget(self._budgets, "adjudicate")
        _, content = self._llm.complete_stream(
            system, [{"role": "user", "content": user}],
            max_tokens=max_tokens, reasoning_budget=reasoning)
        obj = _first_json_object(content)
        if not isinstance(obj, dict):
            tail = (content or "")[-160:].replace("\n", " ")
            raise ValueError(f"adjudicate: no parsable JSON object in GM reply "
                             f"(len={len(content or '')}, tail={tail!r})")
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
        if ambient:
            state_delta = {}        # a plot-free scene never writes story state
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


def shared_ngram(text, prior_lines, n=6):
    """The first prior line sharing a run of >= n consecutive words with `text`, or None.

    Catches a catchphrase wrapped in new words ("Three things, then I'll let you go"),
    which the whole-line Jaccard of commit_check.repeats_prior lets through."""
    words = commit_check._words(text)
    if n <= 0 or len(words) < n:
        return None
    grams = {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}
    for prior in prior_lines or ():
        pw = commit_check._words(prior)
        if any(tuple(pw[i:i + n]) in grams for i in range(len(pw) - n + 1)):
            return prior
    return None


def repeats_recent(text, recent, threshold=0.5, n=6):
    """The earlier-scene line `text` repeats (near-copy, word-set overlap or shared
    n-gram), or None. `recent` comes from table.story_log."""
    if not recent:
        return None
    from table import contract_filter
    hit = commit_check.repeats_prior(text, recent)
    if hit is not None:
        return hit
    for prior in recent:
        if contract_filter.similarity(text, prior) >= threshold:
            return prior
    return shared_ngram(text, recent, n)


def build_check(table_cfg, recent_fn=None):
    """Build the per-seat commit validator the arbiter calls before committing.

    recent_fn() -> committed seat lines of the last scenes. When given and
    table.cross_scene_repeat is set ({ngram: 6}), a line sharing an n-word run with
    an earlier scene fails as "repeats" (office loop fix 2026-10-10). Within one
    scene a recurring tic plus new content still passes (commit_check rule)."""
    seat_names = table_cfg.get("seat_names", {})
    seats = table_cfg.get("seats", [])
    cast_names = {seat: name for seat, name in seat_names.items() if seat in seats}
    silent_seats = frozenset(table_cfg.get("silent_seats", ()))
    forbidden = table_cfg.get("forbidden_phrases", {})
    max_words = table_cfg.get("max_words", 60)
    max_lines = table_cfg.get("max_lines", 2)
    cross = table_cfg.get("cross_scene_repeat") or None

    def check(seat, text, prior=()):
        phrases = tuple(forbidden.get(seat, ()))
        ctx = commit_check.CommitContext(
            seat=seat, cast_names=cast_names, silent_seats=silent_seats,
            forbidden_phrases=phrases, max_words=max_words, max_lines=max_lines,
            prior_lines=tuple(prior))
        result = commit_check.check_reply(text, ctx)
        if not result.ok or recent_fn is None or not cross:
            return result
        hit = shared_ngram(text, recent_fn() or (), int(cross.get("ngram", 6)))
        if hit is None:
            hit = commit_check.repeats_prior(text, recent_fn() or ())
        if hit is not None:
            log.debug("table check: seat=%s repeats an earlier scene", seat)
            return commit_check.CheckResult(
                False, "repeats",
                f"repeats: repeats an earlier scene ('{str(hit)[:60]}'); say something new")
        return result

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
    from table import contract, contract_filter

    artifacts = _load_artifacts(table_cfg)
    contracts = contract.contracts_for_run(
        artifacts, seat_of=table_cfg.get("seat_of", {}), pack_cast=_pack_cast(table_cfg))
    payloads = [c.to_payload() for c in contracts]
    if table_cfg.get("dedupe_canon_goal"):
        # The segment planner sometimes repeats a slot verbatim inside one leaf
        # (e309: 3 of the first 14 contracts); replaying it airs the same scene twice.
        seen, kept = set(), []
        for p in payloads:
            key = " ".join(str(p.get("canon_goal") or "").lower().split())
            if key and key in seen:
                continue
            seen.add(key)
            kept.append(p)
        if len(kept) != len(payloads):
            log.info("table contracts: dropped %d duplicate canon_goal(s)", len(payloads) - len(kept))
        payloads = kept
    if table_cfg.get("collapse_redundant_leaves"):
        # Office loop fix (2026-10-10): the segment planner wrote sibling leaves that
        # retell the same events ("the Office Manager arrives" in 3 leaves of one segment).
        before = len(payloads)
        payloads, dropped = contract_filter.collapse_leaves(
            payloads, threshold=float(table_cfg.get("collapse_leaf_threshold", 0.5)),
            min_share=float(table_cfg.get("collapse_leaf_min_share", 0.5)))
        for leaf, against, share in dropped:
            log.info("table contracts: dropped redundant leaf=%s (repeats leaf=%s share=%.2f)",
                     leaf, against, share)
        log.info("table contracts: leaf collapse %d -> %d", before, len(payloads))
    skip = set(table_cfg.get("skip_scenes") or ())
    if skip:
        # Operator-curated drops (paraphrased retellings the word filters cannot see).
        unknown = skip - {p.get("scene_id") for p in payloads}
        if unknown:
            log.warning("table contracts: skip_scenes not in this run: %s", sorted(unknown))
        before = len(payloads)
        payloads = [p for p in payloads if p.get("scene_id") not in skip]
        log.info("table contracts: skip_scenes dropped %d", before - len(payloads))
    near = table_cfg.get("dedupe_near_threshold")
    if near:
        payloads, dropped = contract_filter.dedupe_near(
            payloads, threshold=float(near), window=int(table_cfg.get("dedupe_near_window", 8)))
        for dropped_id, kept_id, score in dropped:
            log.info("table contracts: dropped near-duplicate scene=%s (like %s score=%.2f)",
                     dropped_id, kept_id, score)
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


def _load_artifacts(table_cfg):
    """The run's generator artifacts (fixture JSON, else the generator DB)."""
    from table import arc_source
    if table_cfg.get("contracts_fixture"):
        return arc_source.RunArtifacts.from_json(table_cfg["contracts_fixture"])
    conn = arc_source.connect_generator()
    try:
        return arc_source.load_run(conn, table_cfg["run_id"])
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - connection already gone
            pass


def segments_provider(agent_config):
    """{segment_id: {"segment", "synopsis", "starts_from", "ends_at", "parts": {leaf_id: title}}}

    The arc segment the GM context shows (design rule U8: the story comes from the
    arc plan). Was never wired: context_provider passed segment=None."""
    artifacts = _load_artifacts(agent_config.get("table", {}))
    out = {}
    for seg in artifacts.segments():
        seg_id = str(seg.get("id"))
        tree = artifacts.trees.get(seg_id) or {}
        parts = {str(nid): str(n.get("title") or "") for nid, n in tree.items()
                 if isinstance(n, dict) and n.get("kind") == "leaf"}
        out[seg_id] = {"segment": seg_id, "synopsis": str(seg.get("synopsis") or ""),
                       "starts_from": str(seg.get("continuity_in") or ""),
                       "ends_at": str(seg.get("continuity_out") or ""), "parts": parts}
    return out


def segment_view(segments, contract):
    """The compact segment section for one contract ({} when unknown)."""
    seg = (segments or {}).get(str(contract.get("segment_id") or ""))
    if not seg:
        return None
    view = {k: v for k, v in seg.items() if k != "parts" and v}
    part = seg.get("parts", {}).get(str(contract.get("leaf_id") or ""))
    if part:
        view["this part"] = part
    return view


def ambient_pool_provider(agent_config):
    """Production default: the ambient scene pool (Postgres pack_scenes and/or a YAML dir,
    table.ambient). Tests replace this."""
    table_cfg = agent_config.get("table", {})
    return ambient_source.load_pool(table_cfg.get("ambient") or {},
                                    default_pack=str(table_cfg.get("pack") or ""))


_CHAR_CONN = None
_LORE_CACHE = {}

GM_IDENTITY = ("You are the Game Master of this table. You narrate, direct, judge the "
               "players' lines against the scene contract and keep the story on its spine. "
               "You alone know the truth below; never reveal it except where a secret's "
               "reveal_at scene has come. Each scene continues the story from what has "
               "already happened; never restage an arrival, greeting or scene that is "
               "already in that list.")


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


def context_provider(agent_config, contract, *, history="", segment=None):
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
        segment=segment, contract=contract, history=history or None,
        blocks_order=table_cfg.get("gm_blocks_order", []),
        max_chars=table_cfg.get("gm_context_max_chars", 24000))


class TableRuntime:
    """Loads scene contracts, runs one ``Arbiter`` per scene, takes operator control.

    table.start_mode:
      manual  wait for a scene_request (table.control); go idle after each scene.
      auto    start by itself at the persisted position (else table.start_index) and
              advance after each scene, playing at most table.max_scenes automatically.
    The FULL contract list is loaded (start_index/max_scenes no longer slice it) so any
    scene can be requested. The last started index persists in table.position_file.

    Long unattended runs (auto mode only):
      table.auto_duration_s  stop starting new scenes once this much wall time has
                             passed since the first automatic scene (the running
                             scene still finishes). Unset = only max_scenes limits.
      table.auto_pace        {words_per_s, per_line_s, lead_s}: the roundtable voices
                             lines far slower than the model writes them, so the GM
                             models the voice queue (each committed line adds
                             per_line_s + words/words_per_s after the previous one)
                             and starts the next scene only lead_s before that queue
                             drains. Without it the spool backs up past max_age_s and
                             lines are dropped. Unset = no pacing.
      table.auto_skip_stuck  (default true) a scene the GM got stuck on is skipped
                             instead of waiting for an operator; after
                             auto_max_consecutive_stuck (default 3) skips in a row
                             the table halts (model likely down) and stays stuck.

    Unattended week (table.block_clock set; golive plan D1/A1-A3, app/table/block_clock.py):
      The story's arc segments are wall-clock blocks. Between scenes the runtime asks
      block_clock.plan_next what to play: the block's spine contracts first (an earlier
      block's leftovers carry over), then plot-free AMBIENT scenes (table.ambient) until the
      window ends; before the anchor and after the last block it plays ambient only. The
      played set, per-scene attempts and the ambient history persist in the position file, so
      a GM restart resumes by itself (start_mode auto, no operator). A scene running longer
      than table.scene_max_s (default 1800) counts as stuck. Differences from the
      plain auto mode: a stuck scene never halts the table (after auto_max_consecutive_stuck
      in a row it cools down auto_stuck_cooldown_s, doubling up to auto_stuck_cooldown_max_s,
      then plays an ambient scene and carries on; a skipped spine scene is retried later, at
      most block_clock.spine_max_attempts times); there is no end unless
      block_clock.end_after_last_block_s is set. Ambient scenes never touch the spine position
      or story state and leave only a short story-log entry.
    """

    def __init__(self, worker_id, agent_config, llm_client, producer, *,
                 clock=time.monotonic, store=None, wall=time.time, rng=None):
        self.worker_id = worker_id
        self.agent_config = agent_config
        self.llm_client = llm_client
        self.producer = producer
        self.clock = clock
        self.store = store
        self.wall = wall                # epoch seconds: the block clock and ambient history
        self._rng = rng or random.Random()
        self.table_cfg = agent_config.get("table", {})
        self.mode = self.table_cfg.get("start_mode", "auto")
        self.position_file = self.table_cfg.get("position_file") or \
            f"/data/world-state/table_{self.table_cfg.get('pack') or 'table'}_position.json"
        self._contracts = None
        self._segments = None
        self.arbiter = None
        self._context_cache = {}
        self.last_index = self._load_position()
        # Office loop fix (2026-10-10): what already happened, persisted with the position.
        self.story = self._load_story()
        self.index = (self.last_index + 1 if self.last_index is not None
                      else int(self.table_cfg.get("start_index", 0) or 0))
        self.auto_played = 0
        self._auto_started_at = None
        self._duration_s = self.table_cfg.get("auto_duration_s")   # table_run_start overrides
        self._air_until = None          # modelled end of the roundtable voice queue
        self._consecutive_stuck = 0
        self._skipped_scene = None
        self._scene_started_at = None
        self._halted = False
        self._said = set()              # once-only log keys
        # Unattended week: block scheduler + ambient fill (see class doc). All of it is
        # inert when table.block_clock is unset, so the D&D table behaves as before.
        self._block = block_clock.BlockClock.from_config(self.table_cfg.get("block_clock"))
        bc = self.table_cfg.get("block_clock") or {}
        self._spine_max_attempts = int(bc.get("spine_max_attempts", 2))
        self._ambient_cfg = dict(self.table_cfg.get("ambient") or {})
        self._pool = None
        self._pool_next_load = None
        self._layout_cache = None
        self._layout_retry_at = 0.0
        self._cooldown_until = None
        self._gap_until = None
        self._force_ambient = False
        self._deferred = set()          # spine scenes that got stuck since the last good scene
        self._stuck_total = 0
        self._played, self._attempts = set(), {}
        self._ambient_history, self._last_speakers, self._last_ambient_id = [], (), None
        self._load_unattended()
        # P4.1: committed lines -> roundtable live transcript (table.live_feed)
        from table import live_feed
        self._feed = (live_feed.CommitPublisher(worker_id, producer.send)
                      if self.table_cfg.get("live_feed", True) else None)

    def _on_commit(self, entry, scene_id):
        if self._feed is not None:
            self._feed.publish_entry(entry, scene_id)
        pace = self.table_cfg.get("auto_pace")
        if pace:
            words = len(str((entry or {}).get("text") or "").split())
            est = float(pace.get("per_line_s", 5.0)) + words / float(pace.get("words_per_s", 2.6))
            now = self.clock()
            start = now if self._air_until is None else max(now, self._air_until)
            self._air_until = start + est

    def _once(self, key, msg, *args):
        if key not in self._said:
            self._said.add(key)
            log.info(msg, *args)

    def _auto_blocked(self):
        """True while auto mode must not start a new scene yet (pace / time budget)."""
        now = self.clock()
        duration = self._duration_s
        if duration and self._auto_started_at is not None \
                and now - self._auto_started_at >= float(duration):
            self._once("duration", "table auto run time budget reached (%ss); no new scenes",
                       duration)
            return True
        if self._block is not None and self._block.finished(self.wall(), self._layout()[2]):
            self._once("clock-end", "table block clock run is over; no new scenes")
            return True
        if self._cooldown_until is not None:
            if now < self._cooldown_until:
                return True
            self._cooldown_until = None
            log.warning("table stuck cooldown over; playing an ambient scene first")
        if self._gap_until is not None:
            if now < self._gap_until:
                return True
            self._gap_until = None
        pace = self.table_cfg.get("auto_pace")
        if pace and self._air_until is not None:
            ready_at = self._air_until - float(pace.get("lead_s", 15.0))
            if now < ready_at:
                return True
        return False

    def _publish(self):
        if self._feed is not None and self.arbiter is not None:
            self._feed.publish_new(self.arbiter.state)

    # ------------------------------------------------------------------ position
    def _load_position(self):
        try:
            with open(self.position_file, encoding="utf-8") as fh:
                value = json.load(fh).get("index")
            return value if isinstance(value, int) and value >= 0 else None
        except (OSError, ValueError, AttributeError):
            return None

    def _load_story(self):
        from table import story_log
        keep = int(self.table_cfg.get("story_keep", 12))
        try:
            with open(self.position_file, encoding="utf-8") as fh:
                items = json.load(fh).get("story")
        except (OSError, ValueError, AttributeError):
            items = None
        story = story_log.StoryLog.from_list(
            items, keep=keep, ambient_keep=int(self.table_cfg.get("story_ambient_keep", 4)))
        if len(story):
            log.info("table story restored entries=%d file=%s", len(story), self.position_file)
        return story

    def _story_on(self):
        return bool(self.table_cfg.get("story_log", True))

    def _gm_history(self):
        if not self._story_on():
            return ""
        return self.story.render(max_entries=int(self.table_cfg.get("story_max_entries", 6)),
                                 max_chars=int(self.table_cfg.get("story_max_chars", 2500)))

    def _seat_story(self):
        if not self._story_on():
            return ""
        return self.story.render(max_entries=int(self.table_cfg.get("story_seat_max_entries", 4)),
                                 max_chars=int(self.table_cfg.get("story_seat_max_chars", 800)))

    def _recent_lines(self):
        cross = self.table_cfg.get("cross_scene_repeat") or {}
        return self.story.recent_lines(int(cross.get("scenes", 2))) if cross else ()

    def _recent_directions(self):
        """Earlier scenes' GM directions AND seat lines: the GM also voiced a seat's
        catchphrase in its own narration (live run 2026-10-10)."""
        cross = self.table_cfg.get("cross_scene_repeat") or {}
        if not cross:
            return ()
        n = int(cross.get("gm_scenes", 3))
        return self.story.recent_directions(n) + self.story.recent_lines(n)

    def _record_scene(self, state):
        """Append the finished scene to the story log and persist it."""
        if not self._story_on():
            return
        from table import story_log
        scene_id = state.get("scene_id")
        if scene_id is not None and scene_id == getattr(self, "_discard_scene", None):
            self._discard_scene = None
            return
        contract = next((c for c in self.contracts if c.get("scene_id") == scene_id),
                        state.get("contract") or {})
        skipped = state.get("phase") == ABORTED or scene_id == self._skipped_scene
        if not skipped and state.get("resolved") is False and not any(
                e.get("kind") == "reply" for e in state.get("transcript") or []):
            skipped = True      # ended before any seat spoke (operator skip / stop)
        ambient = bool(contract.get("ambient"))
        if ambient and (skipped or self._ambient_cfg.get("story_log", "short") in ("none", False)):
            self._save_position(self.last_index, scene_id)      # a plot-free scene leaves no trace
            return
        entry = story_log.entry_from_state(state, contract, skipped=skipped, ambient=ambient,
                                           max_lines=6 if ambient else 12)
        self.story.append(entry)
        self._context_cache.clear()
        log.info("table story += scene=%s skipped=%s entries=%d", scene_id, skipped, len(self.story))
        self._save_position(self.last_index, scene_id)

    def _save_position(self, index, scene_id):
        try:
            import relay_io
            import os
            os.makedirs(os.path.dirname(self.position_file) or ".", exist_ok=True)
            doc = {"index": index, "scene_id": scene_id, "at": time.time(),
                   "story": self.story.to_list()
                   if getattr(self, "story", None) is not None else []}
            if getattr(self, "_block", None) is not None:
                doc.update(self._unattended_doc())
            relay_io.atomic_write_json(self.position_file, doc)
        except Exception as exc:  # noqa: BLE001 - a lost position must not stop the table
            log.warning("table position not saved file=%s: %s", self.position_file, exc)

    # ------------------------------------------------------------------ unattended week
    def _load_unattended(self):
        """Restore the played set, attempts and ambient history from the position file."""
        try:
            with open(self.position_file, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError, AttributeError):
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        played = doc.get("played")
        if isinstance(played, list):
            self._played = {str(x) for x in played}
        else:       # a position file from before the block clock: the story log knows the spine
            self._played = {e.scene_id for e in self.story.entries if not e.ambient}
        attempts = doc.get("attempts")
        if isinstance(attempts, dict):
            self._attempts = {str(k): int(v) for k, v in attempts.items()
                              if isinstance(v, (int, float))}
        history = doc.get("ambient_history")
        if isinstance(history, list):
            self._ambient_history = [{"id": str(h["id"]), "at": float(h.get("at") or 0)}
                                     for h in history if isinstance(h, dict) and h.get("id")]
        speakers = doc.get("last_speakers")
        self._last_speakers = tuple(str(x) for x in speakers) if isinstance(speakers, list) else ()
        self._last_ambient_id = doc.get("last_ambient_id") or None
        total = doc.get("stuck_total")
        self._stuck_total = int(total) if isinstance(total, (int, float)) else 0
        if self._block is not None and (self._played or self._ambient_history):
            log.info("table unattended state restored played=%d ambient_history=%d stuck_total=%d",
                     len(self._played), len(self._ambient_history), self._stuck_total)

    def _unattended_doc(self):
        window = float(self._ambient_cfg.get("no_repeat_s", ambient_source.DEFAULT_NO_REPEAT_S))
        cutoff = self.wall() - 2 * window
        history = [h for h in self._ambient_history if h["at"] >= cutoff][-2000:]
        return {"played": sorted(self._played), "attempts": dict(self._attempts),
                "ambient_history": history, "last_speakers": list(self._last_speakers),
                "last_ambient_id": self._last_ambient_id, "stuck_total": self._stuck_total}

    def _load_segments(self):
        if self._segments is None:
            try:
                self._segments = segments_provider(self.agent_config)
            except Exception as exc:  # noqa: BLE001 - the segment is context, not required
                log.warning("table segments not loaded: %s", exc)
                self._segments = {}
        return self._segments

    def _layout(self):
        """(spine_blocks, spine_index, n_blocks): the spine contract ids per block (arc segment
        order = block index), scene_id -> contract index, and the number of blocks.

        Cached once the contracts load. While they cannot load (generator DB down at boot)
        this is an EMPTY layout, retried every 30 s: the table plays ambient meanwhile and
        picks the spine (with carry-over) up as soon as the story is readable."""
        if self._layout_cache is not None:
            return self._layout_cache
        empty = ([], {}, 0)
        if self.wall() < self._layout_retry_at:
            return empty
        try:
            contracts = self.contracts
        except Exception as exc:  # noqa: BLE001
            self._layout_retry_at = self.wall() + 30
            log.error("table story contracts not loadable, playing ambient only until they are: %s",
                      exc)
            return empty
        order = list(self._load_segments() or {})
        for c in contracts:
            seg = str(c.get("segment_id"))
            if seg not in order:
                order.append(seg)
        blocks = block_clock.group_spine(contracts, block_clock.block_map_from_segments(order))
        index = {c.get("scene_id"): i for i, c in enumerate(contracts)}
        self._layout_cache = (blocks, index, len(order))
        log.info("table block layout blocks=%d spine_per_block=%s", len(order),
                 [len(b) for b in blocks])
        return self._layout_cache

    def _decide(self):
        """block_clock.plan_next for now: spine contract or ambient, with the block status."""
        blocks, _, n_blocks = self._layout()
        done = set(self._played)
        for sid, n in self._attempts.items():       # tried too often: give the scene up
            if n >= self._spine_max_attempts and sid not in done:
                self._once(f"giveup:{sid}", "table gives up spine scene=%s after %d attempts",
                           sid, n)
                done.add(sid)
        return block_clock.plan_next(self._block, blocks, done, self.wall(),
                                     defer=self._deferred, n_blocks=n_blocks)

    def _ambient_pool(self):
        """The ambient pool, loaded lazily and refreshed every ambient.reload_s (a failed
        refresh keeps the old pool; an empty/failed one is retried within a minute)."""
        now = self.wall()
        if self._pool is None or (self._pool_next_load is not None and now >= self._pool_next_load):
            reload_s = float(self._ambient_cfg.get("reload_s", 900))
            try:
                pool = ambient_pool_provider(self.agent_config)
                self._pool = pool
                self._pool_next_load = now + (reload_s if len(pool) else min(60.0, reload_s))
                log.info("table ambient pool loaded scenes=%d", len(pool))
            except Exception as exc:  # noqa: BLE001 - the story must not depend on filler
                log.warning("table ambient pool not loaded: %s", exc)
                self._pool_next_load = now + min(60.0, reload_s)
                if self._pool is None:
                    self._pool = ambient_source.AmbientPool()
        return self._pool

    def _make_ambient(self, phase):
        """An ambient SceneContract payload for `phase`, or None when nothing can be played."""
        pool = self._ambient_pool()
        if not pool:
            return None
        now = self.wall()
        pick = pool.pick(
            phase, now_ts=now, history=self._ambient_history,
            seat_of=self.table_cfg.get("seat_of", {}), active_seats=self.table_cfg.get("seats", []),
            last_speakers=self._last_speakers,
            no_repeat_s=float(self._ambient_cfg.get("no_repeat_s", ambient_source.DEFAULT_NO_REPEAT_S)),
            rng=self._rng)
        if pick is None:
            return None
        if pick.fell_back:
            self._once(f"ambient-fallback:{phase}",
                       "table ambient: no scene for phase=%s, borrowing from other phases", phase)
        if pick.relaxed:
            self._once(f"ambient-relaxed:{phase}:{int(now // 3600)}",
                       "table ambient: every %s scene aired inside the no-repeat window; reusing "
                       "the least recently used", phase)
        sid = f"ambient.{ambient_source.safe_id(pick.scene.id)}.t{int(now)}"
        contract = ambient_source.to_contract(
            pick.scene, pick.participants, phase=phase, run_id=str(self.table_cfg.get("run_id") or ""),
            scene_id=sid, rounds=self._ambient_cfg.get("rounds"))
        return contract.to_payload()

    def _gap_for(self, decision):
        """Seconds of quiet before the next scene (per phase; spine uses block_clock.spine_gap_s)."""
        if decision.kind == block_clock.SPINE:
            spec = (self.table_cfg.get("block_clock") or {}).get("spine_gap_s", 0)
        else:
            gaps = self._ambient_cfg.get("gap_s") or {}
            spec = gaps.get(decision.phase, gaps.get("default", 0))
        if isinstance(spec, (list, tuple)) and len(spec) == 2:
            lo, hi = float(spec[0]), float(spec[1])
            return self._rng.uniform(min(lo, hi), max(lo, hi))
        return float(spec or 0)

    def _finish_block_scene(self, final):
        """Book-keeping for a finished scene in block mode (before the story log append)."""
        contract = final.get("contract") or {}
        sid = final.get("scene_id")
        stuck_skipped = sid == self._skipped_scene
        speakers = tuple(sorted({e.get("speaker") for e in final.get("transcript") or []
                                 if e.get("kind") == "reply" and e.get("speaker")}))
        if contract.get("ambient"):
            self._ambient_history.append({"id": str(contract.get("ambient_id") or sid),
                                          "at": self.wall()})
            self._last_ambient_id = contract.get("ambient_id") or sid
            if not stuck_skipped:
                self._last_speakers = speakers
        elif stuck_skipped:
            # not played: retried after the next good scene (attempts caps the retries)
            self._deferred.add(sid)
        else:
            self._played.add(sid)
            self._attempts.pop(sid, None)
            self._deferred.discard(sid)
        if not stuck_skipped:
            self._deferred.clear()
        if self.mode == "auto" and self.table_cfg.get("auto_advance", True):
            try:
                gap = self._gap_for(self._decide())
            except Exception as exc:  # noqa: BLE001 - a gap is a nicety
                log.warning("table gap not computed: %s", exc)
                gap = 0.0
            if gap > 0:
                base = max(self.clock(), self._air_until or 0.0)
                self._gap_until = base + gap
        self._save_position(self.last_index, sid)

    def _block_status(self):
        """The block-clock view for /table/status: block, phase, spine left, carry-over,
        ambient pool, last ambient id, stuck counters."""
        limit = int(self.table_cfg.get("auto_max_consecutive_stuck", 3))
        out = {"enabled": self._block is not None}
        if self._block is None:
            return out
        d = self._decide()
        blocks, _, n_blocks = self._layout()
        out.update({
            "index": d.block, "of": n_blocks, "window": d.window, "phase": d.phase,
            "effective_now": d.effective, "offset_s": self._block.offset_s,
            "spine_in_block": len(blocks[d.block]) if 0 <= d.block < len(blocks) else 0,
            "spine_remaining": d.spine_remaining, "carry_over": d.carry_over_count,
            "dropped_after_end": d.dropped_after_end,
            "next_kind": d.kind, "next_spine_scene": d.scene_id,
            "playing": ("ambient" if (self.arbiter is not None and self.running()
                                      and self.arbiter.state["contract"].get("ambient"))
                        else "spine" if self.running() else None),
            "ambient_pool": None if self._pool is None else len(self._pool),
            "ambient_pool_phase": (None if self._pool is None
                                   else len(self._pool.for_phase(d.phase))),
            "last_ambient_id": self._last_ambient_id,
            "ambient_played": len(self._ambient_history),
            "spine_played": len(self._played),
            "stuck": {"consecutive": self._consecutive_stuck, "total": self._stuck_total,
                      "limit": limit,
                      "cooldown_s": (0.0 if self._cooldown_until is None
                                     else round(max(0.0, self._cooldown_until - self.clock()), 1))},
        })
        if 0 <= d.block:
            start, end = self._block.window(d.block)
            out["block_start"], out["block_end"] = start.isoformat(), end.isoformat()
        return out

    # ------------------------------------------------------------------ lazy
    @property
    def contracts(self):
        if self._contracts is None:
            full = dict(self.agent_config, table=dict(self.table_cfg, start_index=0, max_scenes=None))
            self._contracts = list(contracts_provider(full))
        return self._contracts

    def _segment_for(self, contract):
        if contract.get("ambient"):
            return None         # plot-free: no arc segment in the GM's context
        return segment_view(self._load_segments(), contract)

    def _context(self, contract):
        # Cleared whenever the story grows (_record_scene), so a replayed scene never
        # gets a stale "what has already happened".
        scene_id = contract.get("scene_id")
        if scene_id not in self._context_cache:
            self._context_cache[scene_id] = context_provider(
                self.agent_config, contract, history=self._gm_history(),
                segment=self._segment_for(contract))
        return self._context_cache[scene_id]

    # ------------------------------------------------------------------ scenes
    def running(self):
        return self.arbiter is not None and self.arbiter.state["phase"] not in TERMINAL

    def next_index(self):
        return self.last_index + 1 if self.last_index is not None else \
            int(self.table_cfg.get("start_index", 0) or 0)

    def _start(self, index):
        """Build and start the arbiter for contracts[index]. Returns True when started."""
        return self._launch(self.contracts[index], index)

    def _launch(self, contract, index=None):
        """Build and start the arbiter for `contract`. `index` is the spine position (None for
        an ambient scene, which never moves the position). Returns True when started."""
        cross = self.table_cfg.get("cross_scene_repeat") or {}
        gm = LLMGM(self.llm_client, self._context,
                   seat_names=self.table_cfg.get("seat_names", {}), budgets=self.table_cfg,
                   recent_fn=self._recent_directions if cross else None,
                   repeat_threshold=cross.get("gm_threshold", 0.5))
        try:
            arbiter = Arbiter(
                contract, self.table_cfg.get("seats", []), gm,
                build_check(self.table_cfg, recent_fn=self._recent_lines if cross else None),
                send=self.producer.send, clock=self.clock,
                gm_seat=self.table_cfg.get("gm_seat", "tuber_0"),
                think_s=self.table_cfg.get("think_s", 60), speak_s=self.table_cfg.get("speak_s", 45),
                store=self.store, arbiter_id=self.worker_id, on_commit=self._on_commit,
                story=self._seat_story())
        except Exception as exc:  # noqa: BLE001 - a bad contract must not kill the hook
            log.warning("table_gm cannot build arbiter scene=%s: %s", contract.get("scene_id"), exc)
            return False
        # Keep the arbiter even if start() fails: it stays in DIRECTING and its own
        # tick() retries the GM call (review fix: re-creating it re-sent scene_start).
        self.arbiter = arbiter
        self._scene_started_at = self.clock()
        if index is not None:
            self.index, self.last_index = index, index
        if self._block is not None and not contract.get("ambient"):
            sid = contract.get("scene_id")
            self._attempts[sid] = self._attempts.get(sid, 0) + 1
        self._save_position(self.last_index, contract.get("scene_id"))
        try:
            arbiter.start()
        except GMUnavailable as exc:
            log.warning("table_gm start: GM unavailable scene=%s: %s", contract.get("scene_id"), exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("table_gm start failed scene=%s: %s", contract.get("scene_id"), exc)
        self._publish()
        return True

    def ensure_started(self):
        """Auto mode only: start the next scene while the automatic budget lasts."""
        if self.mode != "auto" or self.arbiter is not None or self._halted:
            return
        if self._auto_blocked():
            return
        if self._block is not None:
            return self._ensure_started_block()
        limit = self.table_cfg.get("max_scenes")
        while self.index < len(self.contracts) and (limit is None or self.auto_played < int(limit)):
            index = self.index
            if self._start(index):
                self.auto_played += 1
                if self._auto_started_at is None:
                    self._auto_started_at = self.clock()
                return
            self.index = index + 1            # skip a contract that cannot be built

    def _ensure_started_block(self):
        """Block-clock auto mode: the next scene is whatever block_clock.plan_next says."""
        self._ambient_pool()
        d = self._decide()
        if d.kind == block_clock.AMBIENT or self._force_ambient:
            contract = self._make_ambient(d.phase)
            if contract is not None and self._launch(contract):
                self._force_ambient = False
                self._last_ambient_id = contract.get("ambient_id")
                self._note_started()
                log.info("table ambient scene=%s block=%s phase=%s window=%s seats=%s rounds=%s",
                         contract["scene_id"], d.block, d.phase, d.window,
                         contract["participants"], contract["max_rounds"])
                return
            if d.kind == block_clock.AMBIENT:
                self._once(f"no-ambient:{d.phase}",
                           "table has NO ambient scene to play (phase=%s pool=%d); idle, retrying",
                           d.phase, len(self._pool or ()))
                return
            # a stuck cooldown asked for ambient but there is none: play the spine instead
        index = self._layout()[1].get(d.scene_id)
        if index is None:
            return
        if self._start(index):
            self._note_started()
            log.info("table spine scene=%s block=%s carry_over=%s remaining=%d",
                     d.scene_id, d.block, d.carry_over, d.spine_remaining)
            return
        self._played.add(d.scene_id)        # a contract that cannot be built is given up

    def _note_started(self):
        self.auto_played += 1
        if self._auto_started_at is None:
            self._auto_started_at = self.clock()

    def _skip_if_stuck(self):
        """Auto mode: skip a stuck scene so an unattended run keeps going."""
        if self.mode != "auto" or not self.running():
            return
        # Unattended (block clock): never wait for an operator, whatever auto_skip_stuck says.
        if not self.table_cfg.get("auto_skip_stuck", True) and self._block is None:
            return
        if not self.arbiter.state.get("stuck") and not self._overtime():
            return
        limit = int(self.table_cfg.get("auto_max_consecutive_stuck", 3))
        if self._consecutive_stuck >= limit and self._block is None:
            if not self._halted:
                self._halted = True
                log.error("table halted: %d stuck scenes in a row (is the model up?) scene=%s",
                          self._consecutive_stuck, self.arbiter.state["scene_id"])
            return
        self._consecutive_stuck += 1
        self._stuck_total += 1
        log.warning("table auto-skip stuck scene=%s phase=%s (%d in a row, %d total)",
                    self.arbiter.state["scene_id"], self.arbiter.state["phase"],
                    self._consecutive_stuck, self._stuck_total)
        self._skipped_scene = self.arbiter.state["scene_id"]
        self._end_running("auto: scene stuck")
        if self._block is not None and self._consecutive_stuck >= limit:
            # Unattended: never halt. Cool down (model/GM probably down), then probe with an
            # ambient scene; every further failure doubles the cooldown up to the cap.
            base = float(self.table_cfg.get("auto_stuck_cooldown_s", 300))
            cap = float(self.table_cfg.get("auto_stuck_cooldown_max_s", 1800))
            cool = min(cap, base * 2 ** min(self._consecutive_stuck - limit, 10))
            self._cooldown_until = self.clock() + cool
            self._force_ambient = True
            log.error("TABLE STUCK x%d in a row (total %d); cooling down %.0fs, then ambient, "
                      "then carrying on (is the model up?)",
                      self._consecutive_stuck, self._stuck_total, cool)

    def _overtime(self):
        """Block-clock watchdog: a scene running longer than table.scene_max_s (default 30 min;
        null disables) counts as stuck, whatever phase the arbiter is waiting in."""
        if self._block is None or self._scene_started_at is None:
            return False
        limit = self.table_cfg.get("scene_max_s", 1800)
        if not limit or self.clock() - self._scene_started_at < float(limit):
            return False
        log.error("table scene=%s ran over scene_max_s=%ss; treating it as stuck",
                  self.arbiter.state["scene_id"], limit)
        return True

    def _end_running(self, note):
        """End the running scene through the arbiter's own operator_override path."""
        if not self.running():
            return
        override = protocol.build("operator_override", "operator", self.worker_id, {
            "scene_id": self.arbiter.state["scene_id"], "action": "skip_scene", "note": note})
        self.arbiter.on_message(override)
        self._publish()

    # ------------------------------------------------------------------ messages
    def on_message(self, msg):
        if isinstance(msg, dict) and msg.get("type") in control.CONTROL_TYPES:
            self._control(msg)
            return
        if self.arbiter is not None:
            self.arbiter.on_message(msg)
            self._publish()

    def _run_info(self):
        elapsed = (None if self._auto_started_at is None
                   else round(self.clock() - self._auto_started_at, 1))
        duration = float(self._duration_s) if self._duration_s else None
        limit = self.table_cfg.get("max_scenes")
        out_of_time = bool(duration and elapsed is not None and elapsed >= duration)
        if self._block is not None:
            # the week is wall-clock bound: running out of spine just means ambient
            out_of_time = out_of_time or self._block.finished(self.wall(), self._layout()[2])
            out_of_scenes = limit is not None and self.auto_played >= int(limit)
        else:
            out_of_scenes = self.next_index() >= len(self.contracts) or \
                (limit is not None and self.auto_played >= int(limit))
        return {
            "active": self.mode == "auto" and not self._halted
                      and not out_of_time and not out_of_scenes,
            "elapsed_s": elapsed,
            "duration_s": duration,
            "remaining_s": (round(max(0.0, duration - elapsed), 1)
                            if duration and elapsed is not None else None),
            "played": self.auto_played,
            "halted": self._halted,
            "finished": self.mode == "auto" and (out_of_time or out_of_scenes),
            "story_run": self.table_cfg.get("run_id"),
        }

    def status(self, result="status", error="", scenes=False):
        nxt = self.next_index()
        state = self.arbiter.state if self.arbiter is not None else {}
        extra = {}
        if self._block is not None:
            try:
                extra["block"] = self._block_status()
            except Exception as exc:  # noqa: BLE001 - status must always answer
                extra["block"] = {"enabled": True, "error": str(exc)}
        if scenes:      # on request only (operator UI picker): keeps the polled status small
            extra["scenes"] = [{"index": i, "scene_id": c.get("scene_id"),
                                "goal": str(c.get("canon_goal") or "")[:140]}
                               for i, c in enumerate(self.contracts)]
        return {
            **extra,
            "seat_names": dict(self.table_cfg.get("seat_names") or {}),
            "run": self._run_info(),
            "state": "running" if self.running() else "idle",
            "scene_id": state.get("scene_id") if self.running() else None,
            "round": state.get("round") if self.running() else None,
            "phase": state.get("phase") if self.running() else None,
            "index": self.last_index,
            "next_scene_id": self._status_next(nxt),
            "total": len(self._contracts_or_empty()),
            "mode": self.mode,
            "result": result,
            "error": error,
        }

    def _contracts_or_empty(self):
        if self._block is None:
            return self.contracts
        try:
            return self.contracts
        except Exception:  # noqa: BLE001 - generator DB down: status still answers
            return []

    def _status_next(self, nxt):
        contracts = self._contracts_or_empty()
        if self._block is not None:
            try:
                d = self._decide()
                return d.scene_id if d.kind == block_clock.SPINE else f"ambient:{d.phase}"
            except Exception:  # noqa: BLE001
                return None
        return contracts[nxt]["scene_id"] if nxt < len(contracts) else None

    def _reply(self, msg, result, error="", scenes=False):
        from message_bus import build_message
        self.producer.send(build_message(
            self.worker_id, msg.get("from") or "operator", control.TABLE_STATUS,
            self.status(result, error, scenes=scenes), causation_id=msg.get("id")))

    def _control(self, msg):
        type_, payload = msg.get("type"), msg.get("payload") or {}
        if type_ == control.STATUS_REQUEST:
            return self._reply(msg, "status", scenes=bool(payload.get("scenes")))
        if type_ == control.RUN_START:
            return self._run_start(msg, payload)
        if type_ == control.RUN_STOP:
            return self._run_stop(msg, payload)
        if type_ == control.SCENE_STOP:
            if not self.running():
                return self._reply(msg, "idle")
            self._end_running("operator scene_stop")
            return self._reply(msg, "stopped")
        try:
            req = control.parse_scene_request(payload)
        except control.ControlError as exc:
            return self._reply(msg, "error", str(exc))
        if "next" in req:
            target = self.next_index()
        elif "index" in req:
            target = req["index"]
        else:
            ids = [c.get("scene_id") for c in self.contracts]
            target = ids.index(req["scene_id"]) if req["scene_id"] in ids else None
        if target is None or not 0 <= target < len(self.contracts):
            return self._reply(msg, "error", f"no such scene ({payload})")
        if self.running():
            if not req["force"]:
                return self._reply(msg, "busy")
            self._end_running("operator scene_request force")
        self.arbiter = None
        self._halted, self._consecutive_stuck = False, 0   # operator took over
        if not self._start(target):
            return self._reply(msg, "error", "scene could not be built")
        log.info("table scene requested index=%d scene=%s", target, self.contracts[target]["scene_id"])
        return self._reply(msg, "started")

    def _run_start(self, msg, payload):
        """table_run_start: unattended auto play from the operator UI (no GM restart)."""
        try:
            req = control.parse_run_start(payload)
        except control.ControlError as exc:
            return self._reply(msg, "error", str(exc))
        start = req["from"]
        if start == "beginning":
            target = 0
        elif start == "next":
            target = self.next_index()
        else:
            target = start
        if not 0 <= target < len(self.contracts):
            return self._reply(msg, "error", f"no scene at index {target} (total {len(self.contracts)})")
        self.mode = "auto"
        if req["duration_s"] is not None:
            self._duration_s = req["duration_s"]
        self.auto_played = 0
        self._auto_started_at = None
        self._halted, self._consecutive_stuck = False, 0
        self._cooldown_until, self._gap_until, self._force_ambient = None, None, False
        self._said.discard("duration")
        if self._block is not None:     # the spine position IS the played set
            if target == 0:
                self._played, self._attempts, self._deferred = set(), {}, set()
            elif start != "next":
                self._played = {c.get("scene_id") for c in self.contracts[:target]}
                self._attempts, self._deferred = {}, set()
        if target == 0:     # a fresh story: forget what "already happened"
            if self.running():  # the old run's last scene must not seed the new story
                self._discard_scene = self.arbiter.state["scene_id"]
            self.story.clear()
            self._context_cache.clear()
            log.info("table story cleared (run from the beginning)")
        if self.running():
            # The running scene finishes first; the run continues from `target`
            # (next_index() reads last_index, so point it just before target).
            self.last_index = target - 1 if target > 0 else None
            self.index = target
            if target == 0:
                self.last_index = None
        else:
            self.arbiter = None
            self.last_index = target - 1 if target > 0 else None
            self.index = target
            self.ensure_started()
        log.info("table run started from=%s index=%d duration_s=%s", start, target, self._duration_s)
        return self._reply(msg, "started")

    def _run_stop(self, msg, payload):
        try:
            req = control.parse_run_stop(payload)
        except control.ControlError as exc:
            return self._reply(msg, "error", str(exc))
        self.mode = "manual"
        if req["now"] and self.running():
            self._end_running("operator table_run_stop now")
        log.info("table run stopped now=%s", req["now"])
        return self._reply(msg, "stopped")

    def tick(self):
        if self.arbiter is None:
            self.ensure_started()
        if self.arbiter is None:
            return
        self.arbiter.tick()
        self._publish()
        self._skip_if_stuck()
        if self.arbiter.state["phase"] in TERMINAL:
            final = self.arbiter.state
            if final["scene_id"] != self._skipped_scene:
                self._consecutive_stuck = 0     # a scene finished on its own
            if self._block is not None:
                try:
                    self._finish_block_scene(final)
                except Exception as exc:  # noqa: BLE001 - never stop the table
                    log.warning("table block bookkeeping failed scene=%s: %s",
                                final.get("scene_id"), exc)
            try:
                self._record_scene(final)
            except Exception as exc:  # noqa: BLE001 - memory must never stop the table
                log.warning("table story record failed scene=%s: %s", final.get("scene_id"), exc)
            self.arbiter = None
            if self.mode == "auto" and self.table_cfg.get("auto_advance", True):
                self.index = self.next_index()
                self.ensure_started()


_RUNTIMES = {}


def runtime_for(worker_id):
    return _RUNTIMES.get(worker_id)


def reset_runtimes():
    _RUNTIMES.clear()


def _make_runtime(worker_id, agent_config, llm_client, producer, clock, wall=time.time):
    rt = TableRuntime(worker_id, agent_config, llm_client, producer, clock=clock, wall=wall)
    _RUNTIMES[worker_id] = rt
    return rt


def table_gm_idle_tick(worker_id, agent_config, llm_client, producer, state_path=None, *,
                       clock=time.monotonic, wall=time.time):
    """IDLE_TICK_HOOKS["table_gm"]: tick the GM runtime. Never raises."""
    try:
        if (agent_config or {}).get("role") != ROLE:
            return
        rt = _RUNTIMES.get(worker_id)
        if rt is None:
            rt = _make_runtime(worker_id, agent_config, llm_client, producer, clock, wall)
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
