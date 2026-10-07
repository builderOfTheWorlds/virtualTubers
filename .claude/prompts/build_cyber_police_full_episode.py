#!/usr/bin/env python3
"""Build a full on-air-day Rerun Theater episode from campaigns/cyber_police.

v2 (2026-10-02) — no-repeat build. The v1 builder looped the 7-scene authored
spine until the word target was hit (58 verbatim loops in cyber_police_day1,
one every ~12 min) and voiced generated GM-narration beats with the
Captain's seat. v2:

  * plays the authored spine ONCE, spread across the day: spine scene k of
    K lands when k/(K-1) of the ambient words have aired, so the spine's
    own time-of-day narration ("Mid-morning", "19:00") roughly lines up
    with the 07:00-19:00 on-air day (lore/rituals.md);
  * fills the gaps with generated ambient takes, each used at most once,
    round-robin across ambient scenes;
  * cleans every generated line (see clean_take): resolves "Name: ..."
    speaker labels against the cast (full name, name tokens, role words,
    initials) and re-attributes the line; drops lines labelled for a
    silent cast member, unresolvable labels, meta text and refusals;
    strips stage directions "(sighs)" / "[...]" and "||emotion:" residue
    from spoken text; narration beats become narrator lines, never a cast
    seat's voice;
  * enforces a hard no-repeat rule IN CODE (RepeatGuard): no spoken line
    whose normalised text equals — or is a near-duplicate of (3-gram
    Jaccard / difflib >= NEAR_DUP_RATIO) — a line aired within
    --min-repeat-gap-hours (default 12 = never within the on-air day).
    A take that would lose too many lines to the guard is skipped whole;
  * fails the build LOUDLY (exit 2, PoolTooSmall) when the cleaned, deduped
    pool cannot reach --target-words, reporting the shortfall in words and
    in average takes, instead of silently repeating. --allow-short writes
    the shorter candidate anyway (still repeat-free), for review.

Usage (writes files only; uploading requires --upload explicitly):
    python3 .claude/prompts/build_cyber_police_full_episode.py \\
        --pack campaigns/cyber_police --name cyber_police_day1 \\
        --out-dir .claude/prompts/out [--allow-short] [--upload]
"""
import argparse
import datetime as dt
import difflib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))

from campaign.pack import load_pack  # noqa: E402
from campaign.validator import check_seats  # noqa: E402
from session_log_parser import audit as leak_audit  # noqa: E402

MAX_BYTES_SAFETY = 7 * 1024 * 1024  # leave headroom under the server's 8MB cap
WORDS_PER_HOUR = 8929  # this project's documented generation target rate
SPOKEN_WPM = 150       # airtime estimate used for repeat-gap accounting
DEFAULT_TARGET_WORDS = WORDS_PER_HOUR * 12   # 07:00-19:00 on-air day
DEFAULT_MIN_REPEAT_GAP_HOURS = 12.0
NEAR_DUP_RATIO = 0.85
MAX_TAKE_DROP_FRACTION = 0.4   # skip a take that loses more than this to cleaning/dedupe
MIN_TAKE_LINES = 2
MIN_TAKE_WORDS = 12
SHORT_LINE_WORDS = 7

_MUSIC_THEME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_GEMS_MOODS = frozenset({
    "wonder", "transcendence", "tenderness", "nostalgia", "peacefulness",
    "power", "joyful_activation", "tension", "sadness",
})

# --- text cleaning -----------------------------------------------------------
LABEL_RE = re.compile(r"^\s*([A-Za-z][\w.'\- ]{0,40}?(?:\s*\([^)]{0,40}\))?)\s*:\s*(.*)$")
EMOTION_TAG_RE = re.compile(r"\s*\|\|.*$")   # "||emotion:x", "||neutral", ...
STAGE_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]|\*[^*]+\*")
ENUM_PREFIX_RE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+")
META_RE = re.compile(
    r"^(here'?s|here is|this (sets|shows|scene)|note:|beat\s*\d+|scene:|"
    r"\(?\s*end of scene|let me know|i hope this)", re.IGNORECASE)
REFUSAL_RE = re.compile(
    r"as an AI|language model|I(?: am|'m) (?:sorry|unable)|"
    r"I (?:can(?:'|no)t|cannot|won't|will not) "
    r"(?:generate|write|create|help|produce|continue|roleplay)", re.IGNORECASE)
GM_LABELS = frozenset({"gm", "narrator", "narration", "scene"})
# Names from OTHER packs the shared generator prompt/examples leak into this
# pack's takes ("the Ashiorid case" — improviser.py's few-shot examples are
# ashiorid-flavoured). A line naming one never airs on this pack.
FOREIGN_TERMS = {
    "cyber_police": re.compile(r"\b(ashiorid|sodacan|moonwells?|begene|julian faire)\b", re.I),
}


def scene_music(scene):
    moods = [m for m in (getattr(scene, "mood", None) or []) if m in _GEMS_MOODS]
    if not moods:
        return None
    return {"mood": moods, "scene_id": str(scene.id)}


def attach_scene_music(events, start, scene):
    music = scene_music(scene)
    if music and len(events) > start:
        events[start]["music"] = music


def show_music_header(pack):
    name = str(getattr(pack, "name", "") or "")
    if not _MUSIC_THEME_RE.match(name):
        return None
    return {"music": {"theme": name}}


def carry_music_into_part(part_events, previous_events):
    if not part_events or "music" in part_events[0]:
        return
    for ev in reversed(previous_events):
        if "music" in ev:
            part_events[0]["music"] = dict(ev["music"])
            return


def speaker_map(pack):
    """cast id -> episode speaker id, from the pack's declared `seats:` map."""
    seats = getattr(pack, "seats", None) or {}
    if not seats:
        raise ValueError(
            "pack declares no seats: this builder requires a seated pack "
            "(see build_ashiorid_full_episode.py for the legacy worker-id path)")
    problems = check_seats(pack)
    if problems:
        raise ValueError("invalid seats: " + "; ".join(problems))
    return dict(seats)


def silent_cast_ids(pack):
    """Cast ids declared silent (same rule as campaign/improviser.py)."""
    return {mid for mid, m in pack.cast.items()
            if m.system_prompt and re.search(r"you never speak", m.system_prompt, re.I)}


def _tokens(s):
    return [t for t in re.findall(r"[a-z][a-z']*", s.lower())]


def build_alias_index(pack):
    """Map normalised speaker labels the generator model emits to cast ids.

    Covers: cast id / role word ("forensics"), full display name, unique name
    tokens ("thorn", "Captain Agar"), unique >=3-char first-name prefixes
    ("Des", "Pri"), and unique initials ("HV", "TMB", "TML", "DOP").
    Ambiguous aliases (e.g. "DO" = Deshawn Okafor AND Davey Okonkwo) are
    deliberately left unresolved — a guessed speaker is worse than a dropped line.
    """
    owners = defaultdict(set)
    for mid, member in pack.cast.items():
        name = member.name or ""
        toks = _tokens(name.replace("-", " "))
        owners[mid.lower()].add(mid)
        owners[" ".join(_tokens(name))].add(mid)
        owners[" ".join(toks)].add(mid)
        for t in toks:
            owners[t].add(mid)
            for n in range(3, len(t)):
                owners["~" + t[:n]].add(mid)
        hy = name.lower().split()        # hyphen kept: "okonkwo-pryce" counts as one word
        for words in (toks, hy, [w for w in toks if w not in ("captain", "detective", "the")],
                      [w for w in hy if w not in ("captain", "detective", "the")]):
            if len(words) >= 2:
                owners["#" + "".join(w[0] for w in words)].add(mid)
            # surname-only hyphen initials: "TMB-L" -> t m b l
            owners["#" + "".join(w[0] for w in re.split(r"[ -]", " ".join(words)) if w)].add(mid)
        # title + surname initials: "Captain Agar" -> CA, "Detective Thorn" -> DT
        if len(toks) >= 2 and toks[0] in ("captain", "detective"):
            owners["#" + toks[0][0] + toks[-1][0]].add(mid)
            owners[toks[0] + " " + toks[-1]].add(mid)
        # first initial glued to surname: "Dthorn", "Tmbeki-Lowndes", "TMB" (T + MBeki)
        if len(hy) >= 2:
            first, last = hy[-2][0], re.sub(r"[^a-z]", "", hy[-1])
            for n in (2, 3, len(last)):
                owners["#" + first + last[:n]].add(mid)
    alias = {k: next(iter(v)) for k, v in owners.items() if len(v) == 1}
    for k, mid in EXTRA_ALIASES.get(getattr(pack, "name", ""), {}).items():
        if mid in pack.cast:
            alias[k] = mid
    return alias


# Hand-checked, unambiguous labels the generator model invents that the
# generic rules above can't derive (keys are compact lowercase, '#'-prefixed
# like generated initials). Ambiguous ones (DO = Deshawn Okafor or Davey
# Okonkwo, DA, RO, ...) are deliberately NOT listed: those lines are dropped.
EXTRA_ALIASES = {
    "cyber_police": {
        "#tmbl": "quartermaster", "#tml": "quartermaster", "#tbl": "quartermaster",
        "#tmbekilw": "quartermaster", "#tmbekilowndes": "quartermaster",
    },
}


def resolve_label(label, alias):
    """Return a cast id for a 'Name:' label, or None if it can't be resolved."""
    label = re.sub(r"\([^)]*\)", " ", label).strip()
    toks = _tokens(label.replace("-", " "))
    if not toks:
        return None
    joined = " ".join(toks)
    if joined in alias:
        return alias[joined]
    ids = {alias.get(t) for t in toks if t in alias}
    if len(ids) == 1 and None not in ids and all(t in alias or t in ("the",) for t in toks):
        return ids.pop()
    compact = re.sub(r"[^a-z]", "", label.lower())
    if "#" + compact in alias and (len(label.split()) == 1 or label.replace("-", "").replace(".", "").isupper()):
        return alias["#" + compact]
    if len(toks) == 1 and "~" + toks[0] in alias:
        return alias["~" + toks[0]]
    return None


def looks_like_person_label(label):
    """'TMB', 'DO-P', 'Denny (reporter)', 'Captain Agar' — vs 'Supply notice'."""
    bare = re.sub(r"\([^)]*\)", " ", label).strip()
    compact = re.sub(r"[^A-Za-z]", "", bare)
    if compact.isupper() and 1 < len(compact) <= 5:
        return True
    words = bare.split()
    return 1 <= len(words) <= 3 and all(w[:1].isupper() for w in words)


def clean_spoken(text):
    """Strip emotion tags, stage directions and wrapping quotes from spoken text."""
    text = EMOTION_TAG_RE.sub("", text or "")
    text = ENUM_PREFIX_RE.sub("", text)
    text = STAGE_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    if len(text) >= 2 and text[0] == text[-1] == '"' and text.count('"') == 2:
        text = text[1:-1].strip()
    text = text.lstrip(",;:- ").strip()
    if text and text[-1].isalnum():
        text += "."        # unterminated generator line: give TTS a sentence end
    return text


def clean_narration(text):
    text = EMOTION_TAG_RE.sub("", text or "").strip()
    text = re.sub(r"^\(\s*(.*?)\s*\)$", r"\1", text)      # whole-line "( ... )"
    text = re.sub(r"^\[\s*(.*?)\s*\]$", r"\1", text)
    return clean_spoken(text)


def clean_take(beats, pack, speakers, alias, silent, stats, foreign_re=None):
    """Turn one generated take's raw beats into episode events.

    Returns (events, raw_line_count) — events is [] when the take is
    unusable (refusal/meta take). `stats` (a defaultdict(int)) accumulates
    per-reason counters for the build report.
    """
    events = []
    raw = 0
    for beat in beats or []:
        text = str(beat.get("text") or "").strip()
        if not text:
            continue
        raw += 1
        if REFUSAL_RE.search(text):
            stats["take_refusal"] += 1
            return [], raw
        kind = beat.get("kind") or "dialogue"
        spk = (beat.get("speaker") or "").strip()
        m = LABEL_RE.match(EMOTION_TAG_RE.sub("", text))
        if m and len(m.group(1).split()) <= 5:
            label, rest = m.group(1).strip(), m.group(2).strip()
            if label.lower() in GM_LABELS:
                kind, text = "narration", rest
                stats["label_gm_stripped"] += 1
            else:
                who = resolve_label(label, alias)
                if who in silent:
                    stats["drop_silent_cast"] += 1
                    continue
                if who:
                    if kind != "dialogue" or who != spk:
                        stats["reattributed"] += 1
                    kind, spk, text = "dialogue", who, rest
                    stats["label_stripped"] += 1
                elif kind == "narration" or looks_like_person_label(label):
                    # an unresolvable / ambiguous name ("DO", "TMB", "Denny"):
                    # the true speaker is unknown, so neither the label nor a
                    # guessed voice may air.
                    stats["drop_unresolved_label"] += 1
                    continue
                # dialogue with a non-name label ("Supply notice: ...",
                # "BREAKING: ...") — leave as spoken text.
        if leak_audit(json.dumps(text, ensure_ascii=False)) is not None:
            # e.g. a headline "The Scuttle's Secret: How ..." matches the
            # credential regex; one such line fails validation of the whole
            # episode, so the line is dropped (never printed).
            stats["drop_leak_audit_shape"] += 1
            continue
        if foreign_re and foreign_re.search(text):
            stats["drop_foreign_pack_term"] += 1
            continue
        if META_RE.match(text) or text.rstrip().endswith(":"):
            stats["drop_meta"] += 1
            continue
        if kind == "dialogue":
            if spk in silent:
                stats["drop_silent_cast"] += 1
                continue
            worker = speakers.get(spk)
            cleaned = clean_spoken(text)
            if worker is None:
                stats["drop_unknown_speaker"] += 1
                continue
            if cleaned != text:
                stats["spoken_cleaned"] += 1
            if not cleaned or len(cleaned.split()) < 1 or not re.search(r"[A-Za-z]", cleaned):
                stats["drop_empty_after_clean"] += 1
                continue
            events.append({"type": "assistant_text", "text": cleaned, "speaker": worker})
        else:
            cleaned = clean_narration(text)
            if not cleaned or not re.search(r"[A-Za-z]", cleaned):
                stats["drop_empty_after_clean"] += 1
                continue
            events.append({"type": "user_message", "text": cleaned})
    return events, raw


# --- repeat guard ------------------------------------------------------------
def normalize_line(text):
    t = (text or "").lower()
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return " ".join(t.split())


def _shingles(norm, k=3):
    w = norm.split()
    if len(w) < k:
        return {tuple(w)}
    return {tuple(w[i:i + k]) for i in range(len(w) - k + 1)}


class RepeatGuard:
    """Rejects a line if an identical or near-identical line aired within
    `min_gap_s` seconds of airtime. Positions are airtime seconds."""

    def __init__(self, min_gap_s, ratio=NEAR_DUP_RATIO):
        self.min_gap_s = min_gap_s
        self.ratio = ratio
        self._exact = {}                        # norm -> last position
        self._inv = defaultdict(set)            # shingle -> norms
        self._sh = {}
        self._short = defaultdict(list)         # word-count -> norms (<= SHORT_LINE_WORDS)

    def _recent(self, norm, pos):
        last = self._exact.get(norm)
        return last is not None and pos - last < self.min_gap_s

    def conflict(self, text, pos):
        """Return the earlier line text-key this would repeat, or None."""
        norm = normalize_line(text)
        if not norm:
            return None
        if self._recent(norm, pos):
            return norm
        n = len(norm.split())
        if n >= 5:
            sh = _shingles(norm)
            cand = defaultdict(int)
            for g in sh:
                for o in self._inv.get(g, ()):
                    cand[o] += 1
            for o, c in cand.items():
                if c / len(sh | self._sh[o]) >= self.ratio and self._recent(o, pos):
                    return o
                if c / len(sh) >= 0.5 and self._recent(o, pos) and \
                        difflib.SequenceMatcher(None, norm, o).ratio() >= self.ratio:
                    return o
        if n <= SHORT_LINE_WORDS:
            # short lines: 3-gram Jaccard is meaningless, compare by edit
            # ratio against every short line within +-2 words ("what have you
            # got" ~ "what have you got priya").
            for m in range(max(1, n - 2), n + 3):
                for o in self._short.get(m, ()):
                    if self._recent(o, pos) and \
                            difflib.SequenceMatcher(None, norm, o).ratio() >= self.ratio:
                        return o
        return None

    def add(self, text, pos):
        norm = normalize_line(text)
        if not norm:
            return
        first = norm not in self._exact
        self._exact[norm] = pos
        if first:
            n = len(norm.split())
            if n >= 5:
                sh = _shingles(norm)
                self._sh[norm] = sh
                for g in sh:
                    self._inv[g].add(norm)
            if n <= SHORT_LINE_WORDS:
                self._short[n].append(norm)


def airtime_s(text):
    return len((text or "").split()) / SPOKEN_WPM * 60.0


def assert_no_repeats(events, min_gap_s):
    """Final, independent check over the finished event list (exact
    normalised repeats only). Raises RepeatViolation with the worst case."""
    last, pos, worst = {}, 0.0, None
    for i, e in enumerate(events):
        norm = normalize_line(e.get("text", ""))
        if norm in last and pos - last[norm][0] < min_gap_s:
            gap = pos - last[norm][0]
            if worst is None or gap < worst[0]:
                worst = (gap, last[norm][1], i, e.get("text", ""))
        if norm:
            last[norm] = (pos, i)
        pos += airtime_s(e.get("text", ""))
    if worst:
        gap, a, b, text = worst
        raise RepeatViolation(
            f"line repeats after {gap / 60:.1f} min (< {min_gap_s / 3600:.1f} h): "
            f"events {a} and {b}: {text[:80]!r}")


class RepeatViolation(RuntimeError):
    pass


class PoolTooSmall(RuntimeError):
    def __init__(self, msg, report):
        super().__init__(msg)
        self.report = report


# --- assembly ----------------------------------------------------------------
def _text_of(beat):
    if getattr(beat, "text", None):
        return str(beat.text).strip()
    for t in (getattr(beat, "texts", None) or []):
        if t and str(t).strip():
            return str(t).strip()
    return ""


def _spine_scenes(pack, max_scenes):
    seen, order, sid = set(), [], pack.start_scene
    while sid and sid not in seen and len(order) < max_scenes:
        seen.add(sid)
        try:
            scene = pack.scene(sid)
        except Exception:
            break
        if not scene.ambient and scene.beats:
            order.append(scene)
        sid = scene.default_next
    return order


def spine_scene_events(scene, speakers):
    evs = []
    if scene.enter_narration:
        evs.append({"type": "user_message", "text": str(scene.enter_narration).strip()})
    for beat in scene.beats:
        kind = getattr(beat, "kind", "")
        text = _text_of(beat)
        if not text:
            continue
        if kind == "dialogue":
            ev = {"type": "assistant_text", "text": text}
            speaker = speakers.get(getattr(beat, "speaker", None))
            if speaker:
                ev["speaker"] = speaker
            evs.append(ev)
        elif kind in ("narration", "action"):
            evs.append({"type": "user_message", "text": text})
    attach_scene_music(evs, 0, scene)
    return evs


def load_ambient_takes(pack, speakers, stats):
    """All usable cleaned takes, round-robin across ambient scenes:
    [(scene_id, take_name, events)]."""
    import yaml
    alias = build_alias_index(pack)
    silent = silent_cast_ids(pack)
    per_scene = []
    for sid in pack.ambient_scene_ids():
        d = Path(pack.root) / "generated" / sid
        takes = []
        for f in sorted(d.glob("*.yaml")) if d.exists() else []:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            evs, raw = clean_take(data.get("beats"), pack, speakers, alias, silent, stats,
                                  FOREIGN_TERMS.get(getattr(pack, "name", "")))
            stats["takes_on_disk"] += 1
            stats["raw_lines"] += raw
            if not evs:
                stats["take_unusable"] += 1
                continue
            takes.append((sid, f"{sid}/{f.stem}", evs, raw))
        per_scene.append(takes)
    out = []
    for i in range(max((len(t) for t in per_scene), default=0)):
        for takes in per_scene:
            if i < len(takes):
                out.append(takes[i])
    return out


def build_events(pack, max_scenes, target_words, speakers,
                 min_gap_hours=DEFAULT_MIN_REPEAT_GAP_HOURS, allow_short=False):
    """One spine pass spread across the day + unique ambient takes.

    Returns (events, report). Raises PoolTooSmall when the deduped pool can't
    reach target_words and allow_short is False.
    """
    stats = defaultdict(int)
    min_gap_s = min_gap_hours * 3600.0
    spine = _spine_scenes(pack, max_scenes)
    spine_evs = [spine_scene_events(s, speakers) for s in spine]
    spine_words = sum(len(e["text"].split()) for evs in spine_evs for e in evs)
    takes = load_ambient_takes(pack, speakers, stats)

    # Pass 1: dedupe the pool in airing order against the spine (all spine
    # lines are reserved first) and against already-accepted takes. With
    # min_gap >= episode length this is exact; positions are refined below.
    guard = RepeatGuard(min_gap_s)
    for evs in spine_evs:
        for e in evs:
            guard.add(e["text"], 0.0)
    ambient_budget = max(0, target_words - spine_words)
    accepted, amb_words, pos = [], 0, 0.0
    for sid, name, evs, raw in takes:
        if amb_words >= ambient_budget:
            break
        kept = []
        take_guard = RepeatGuard(min_gap_s)     # repeats inside this one take
        for e in evs:
            if guard.conflict(e["text"], pos) is not None or \
                    take_guard.conflict(e["text"], pos) is not None:
                stats["drop_repeat_line"] += 1
                continue
            take_guard.add(e["text"], pos)
            kept.append(e)
        words = sum(len(e["text"].split()) for e in kept)
        dropped_frac = 1 - len(kept) / max(1, raw)
        if len(kept) < MIN_TAKE_LINES or words < MIN_TAKE_WORDS or dropped_frac > MAX_TAKE_DROP_FRACTION:
            stats["take_skipped_after_clean_dedupe"] += 1
            continue
        for e in kept:
            guard.add(e["text"], pos)
        accepted.append((sid, name, kept, words))
        amb_words += words

    total_words = spine_words + amb_words
    report = {
        "spine_scenes": len(spine), "spine_words": spine_words,
        "ambient_takes_used": len(accepted), "ambient_words": amb_words,
        "total_words": total_words, "target_words": target_words,
        "hours_at_150wpm": round(total_words / SPOKEN_WPM / 60, 2),
        "stats": dict(stats),
    }
    if total_words < target_words:
        short = target_words - total_words
        avg = amb_words / max(1, len(accepted))
        report["shortfall_words"] = short
        report["avg_clean_take_words"] = round(avg, 1)
        report["takes_needed_estimate"] = int(short / max(1.0, avg)) + 1
        msg = (f"ambient pool too small for a repeat-free {target_words:,}-word episode: "
               f"have {total_words:,} words ({report['hours_at_150wpm']} h @150wpm) after "
               f"cleaning/dedupe, short {short:,} words (~{report['takes_needed_estimate']} "
               f"more unique takes at {avg:.0f} words/take)")
        if not allow_short:
            raise PoolTooSmall(msg, report)
        report["warning"] = msg

    # Pass 2: lay out the day. Spine scene k airs once k/(K-1) of the ambient
    # words have aired (first scene opens the day, last scene closes it).
    events = []
    k_total = len(spine_evs)
    targets = [amb_words * k / max(1, k_total - 1) for k in range(k_total)]
    aired_amb, ti = 0, 0
    next_spine = 0
    while next_spine < k_total or ti < len(accepted):
        if next_spine < k_total and (aired_amb >= targets[next_spine] or ti >= len(accepted)):
            events.extend(spine_evs[next_spine])
            next_spine += 1
            continue
        sid, name, kept, words = accepted[ti]
        ti += 1
        start = len(events)
        events.extend(dict(e) for e in kept)
        try:
            attach_scene_music(events, start, pack.scene(sid))
        except Exception:
            pass
        aired_amb += words

    assert_no_repeats(events, min_gap_s)
    report.update(repeat_stats(events))
    return events, report


def repeat_stats(events):
    """Unique-line ratio, min repeat gap (h) and airtime before first repeat."""
    seen, pos, gaps, first = {}, 0.0, [], None
    for e in events:
        n = normalize_line(e.get("text", ""))
        if n in seen:
            gaps.append(pos - seen[n])
            if first is None:
                first = pos
        if n:
            seen[n] = pos
        pos += airtime_s(e.get("text", ""))
    return {
        "lines": len(events), "unique_lines": len(seen),
        "unique_ratio": round(len(seen) / max(1, len(events)), 4),
        "min_repeat_gap_h": round(min(gaps) / 3600, 2) if gaps else None,
        "hours_before_first_repeat": round((first if first is not None else pos) / 3600, 2),
    }


def split_parts(events):
    parts, current, current_bytes = [], [], 2
    for ev in events:
        ev_bytes = len(json.dumps(ev, ensure_ascii=False).encode("utf-8")) + 1
        if current and current_bytes + ev_bytes > MAX_BYTES_SAFETY:
            parts.append(current)
            current, current_bytes = [], 2
        current.append(ev)
        current_bytes += ev_bytes
    if current:
        parts.append(current)
    seen = []
    for p in parts:
        carry_music_into_part(p, seen)
        seen.extend(p)
    return parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="campaigns/cyber_police")
    ap.add_argument("--name", required=True)
    ap.add_argument("--project", default="virtualTubers")
    ap.add_argument("--max-scenes", type=int, default=20)
    ap.add_argument("--target-words", type=int, default=DEFAULT_TARGET_WORDS)
    ap.add_argument("--min-repeat-gap-hours", type=float, default=DEFAULT_MIN_REPEAT_GAP_HOURS)
    ap.add_argument("--allow-short", action="store_true",
                    help="write a repeat-free but shorter-than-target candidate instead of failing")
    ap.add_argument("--out-dir", default=str(REPO / ".claude" / "prompts" / "out"))
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--api", default="http://localhost:8090")
    args = ap.parse_args()

    pack = load_pack(REPO / args.pack)
    try:
        speakers = speaker_map(pack)
    except ValueError as exc:
        print(f"episode '{args.name}': FAIL — {exc}")
        return 1

    try:
        events, report = build_events(pack, args.max_scenes, args.target_words, speakers,
                                      args.min_repeat_gap_hours, args.allow_short)
    except PoolTooSmall as exc:
        print(f"episode '{args.name}': FAIL — {exc}")
        print(json.dumps(exc.report, indent=1))
        return 2
    except RepeatViolation as exc:
        print(f"episode '{args.name}': FAIL — repeat rule violated: {exc}")
        return 3

    print(f"episode '{args.name}': {len(events)} events")
    print(json.dumps(report, indent=1))

    music_header = show_music_header(pack)
    seats = getattr(pack, "seats", None) or {}

    def make_episode(name, evs):
        episode = {
            "source": name, "project": args.project,
            "session_id": f"campaign-day-{name}",
            "date": dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
            "events": evs,
        }
        header = dict(music_header) if music_header else {}
        if seats:
            header["slots"] = sorted(set(seats.values()), key=lambda s: int(s.split("_", 1)[1]))
        if header:
            episode["show"] = header
        return episode

    from episode_validator import validate_episode
    parts = split_parts(events)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for i, part_events in enumerate(parts):
        part_name = args.name if len(parts) == 1 else f"{args.name}_part{i+1:02d}"
        ep = make_episode(part_name, part_events)
        try:
            validate_episode(ep, part_name)
            status = "PASS"
        except Exception as exc:
            status = f"FAIL — {type(exc).__name__}: {exc}"
        out = out_dir / f"{part_name}.json"
        out.write_text(json.dumps(ep, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  part {i+1}/{len(parts)} '{part_name}': {len(part_events)} events, "
              f"validation: {status}, wrote: {out}")
        written.append((part_name, out, status))
        if args.upload and status == "PASS":
            import urllib.error
            import urllib.request
            req = urllib.request.Request(
                f"{args.api}/replays?name={part_name}&overwrite=true",
                data=out.read_bytes(), method="POST",
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    print(f"    upload: {r.status} {r.read().decode()[:200]}")
            except urllib.error.HTTPError as e:
                print(f"    upload FAILED: {e.code} {e.read().decode()[:400]}")
    (out_dir / f"{args.name}.report.json").write_text(json.dumps(report, indent=1))
    return 1 if any(p[2] != "PASS" for p in written) else 0


if __name__ == "__main__":
    raise SystemExit(main())
