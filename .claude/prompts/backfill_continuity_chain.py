"""Second pass: regenerate SPINE continuity with chain context.

Fixes the two serious defects found in the first pass (see
`.claude/prompts/continuity_backfill_review.md`):

  A. Hallucinated identity — the-vault claimed the party ARE the Letos, when
     the scene says they INHERIT the Letos' duty and grovley-revelation says
     they are Begene Sisters program products.
  B. Event ordering inverted — amulet-map's outro consumed malvakar-riddle's
     content (solving the first riddle happens in the NEXT scene).

Both have one root cause: per-scene calls with no committed neighbour state.

THE FIX — three changes from pass 1:
  1. Process scenes in `default_next` order, feeding each the ACCEPTED
     continuity_out of its predecessor (not the predecessor's opening line).
  2. An explicit scene-boundary instruction against consuming the next scene.
  3. A canon fact sheet so identity cannot be inferred from a title.

Ambient scenes are NOT touched — pass 1's ambient output was accepted.

Usage:
    .venv/bin/python .claude/prompts/backfill_continuity_chain.py
"""
import argparse
import json
import logging
import pathlib
import re
import sys
import time
import urllib.request

import yaml

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("chain")

REPO = pathlib.Path("/home/secus/codeProjects/virtualTubers")
SCENES = REPO / "campaigns" / "ashiorid_1" / "scenes"
PASS1 = REPO / ".claude" / "prompts" / "continuity_backfill.yaml"
OUT = REPO / ".claude" / "prompts" / "continuity_backfill_v2.yaml"

OLLAMA = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "hermes3:70b"

# Canon the model must not re-infer. Drawn from the scene text itself, checked
# by hand — this is the antidote to Defect A.
CANON = """CANON FACTS — treat these as fixed. Never contradict them.

- The four player characters are Chadwick, Leena, Vigil and Sodacan Bob.
- They are products of the BEGENE SISTERS BREEDING PROGRAM. They were bred and
  assembled by design. They are NOT members of the Leto family.
- The Letos are a separate, ancient family who guarded the vault for ten
  thousand years. In 'the-vault' that DUTY passes to the four player
  characters. Inheriting the duty does not make them Letos.
- Grovley is the Leto family butler who reveals what the party is.
- 'The Event' is the catastrophe ten thousand years ago that the calendar is
  measured from.
- Duke Sorensen Leto the Third is murdered during the party attack.
"""

SYSTEM = (
    "You write continuity metadata for a serialized fantasy campaign. Nothing "
    "you write is ever spoken aloud — it is planning metadata.\n\n"
    "Rules:\n"
    "- One or two short declarative sentences per summary.\n"
    "- Record the party's knowledge, relationships, possessions, location and "
    "unresolved threats.\n"
    "- Never use second person. Never address the audience.\n"
    "- Never invent events the scene text does not support.\n"
    "- CRITICAL — SCENE BOUNDARIES: `continuity_out` describes the state at "
    "the END OF THIS SCENE ONLY. If this scene sets something up that is paid "
    "off in a later scene, the payoff belongs to THAT scene, not this one. "
    "Never describe an outcome the scene text does not actually reach.\n"
    "- CRITICAL — PRESENT vs RECOUNTED: some scenes depict legends or tales "
    "told aloud. For those, record what the LISTENING party now knows or has "
    "been told; never place the party inside the tale.\n"
    "- Write only the requested YAML. No preamble, no labels, no quotes."
)

USER = """{canon}
---

You are summarizing ONE scene in a chain. Here is the committed state handed
to you by the previous scene:

PREVIOUS SCENE ({prev_id}) ENDED WITH:
{prev_out}

---

THIS SCENE — id: {sid}
Title: {title}

Opening narration (spoken aloud):
{enter}

What actually happens in this scene:
{body}

{next_block}---

Write two summaries as YAML.

continuity_in: the state this scene assumes when it begins. It must be
CONSISTENT with what the previous scene ended with (above). Do not contradict
it, and do not add knowledge the party has not yet acquired.

continuity_out: the state guaranteed true when THIS scene ends — and nothing
beyond it. Do not borrow anything from the next scene.

Reply with ONLY:

continuity_in: <one or two sentences>
continuity_out: <one or two sentences>
"""


def scene_body(data, limit=2600):
    parts = []
    for beat in data.get("beats") or []:
        kind = beat.get("kind") or beat.get("type")
        if kind == "pane":
            continue
        text = beat.get("text")
        if isinstance(text, list):
            text = text[0] if text else None
        if not text:
            continue
        speaker = beat.get("speaker")
        prefix = f"{speaker}: " if speaker and speaker != "gm" else ""
        parts.append(f"{prefix}{str(text).strip()}")
    return "\n".join(parts)[:limit] or "(no scripted beats)"


def ask(model, user, retries=3):
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user}],
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 320},
    }
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                OLLAMA, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.load(resp)["message"]["content"].strip()
        except Exception as exc:
            last = exc
            log.warning("attempt %d/%d: %s", attempt, retries, exc)
            time.sleep(3 * attempt)
    raise RuntimeError(f"ollama failed: {last}")


def _normalize_reply(text):
    """Strip the common model artifacts before field extraction.

    Handles code fences, a whole reply wrapped as one quoted scalar with
    literal ``\\n`` escapes (the grovley-revelation failure), and inline
    quoted values, while leaving a clean mapping untouched.
    """
    text = text.strip()
    if "```" in text:  # unwrap: keep the line after the language tag
        m = re.search(r"```[a-zA-Z]*\n(.*)\n?```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    if text and text[0] in "\"'":  # whole blob is one quoted scalar
        q = text[0]
        if text.endswith(q) and len(text) >= 2:
            text = text[1:-1].strip()
    text = text.replace("\\n", "\n")  # literal escapes -> real newlines
    # Strip per-value quotes that hug a continuity_ key (both the opening
    # '"continuity_out' and the closing 'value" continuity_in' position).
    text = text.replace('"continuity_', 'continuity_')
    text = re.sub(r'"\s+(?=continuity_)', ' ', text)
    return text


def parse_reply(reply):
    """Extract continuity_in / continuity_out from the model's reply.

    The model occasionally wraps its YAML in a single quoted scalar with
    literal ``\\n`` escapes (grovley-revelation reliably does), which
    ``yaml.safe_load`` returns as a *str*, so an old parser rejected it as
    "unparseable" even though the fields were present. We normalize the
    common artifacts away, then extract the two fields directly by key,
    and fall back to ``yaml.safe_load`` for the clean case.
    """
    text = _normalize_reply(reply)

    def _grab(key, blob):
        m = re.search(
            re.escape(key) + r"\s*:\s*(.*?)"
            r"(?=\n\s*continuity_\w+\s*:"   # clean mapping: next key on newline
            r"|\"\s*\n\s*continuity_\w+\s*:" # quoted value, then next key
            r"|[\s]*\Z)",                  # end of blob
            blob, re.DOTALL | re.IGNORECASE)
        if not m:
            return None
        val = m.group(1).strip()
        if val[:1] in "\"'":
            val = val[1:]
        if val[-1:] in "\"'" and val not in ('"', "'"):
            val = val[:-1]
        val = val.strip()
        return val if val else None

    cin = _grab("continuity_in", text)
    cout = _grab("continuity_out", text)
    if cin and cout:
        return {"continuity_in": " ".join(cin.split()),
                "continuity_out": " ".join(cout.split())}

    # Fallback: clean-mapping case via the loader.
    if "```" in text:
        for chunk in text.split("```"):
            if "continuity_in" in chunk:
                text = chunk.replace("yaml", "", 1).strip()
                break
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    cin, cout = data.get("continuity_in"), data.get("continuity_out")
    if not (isinstance(cin, str) and cin.strip()):
        return None
    if not (isinstance(cout, str) and cout.strip()):
        return None
    return {"continuity_in": " ".join(cin.split()),
            "continuity_out": " ".join(cout.split())}


def build_graph(loaded):
    """Return (topological order, predecessor map) over the SPINE graph.

    The spine is a DAG with branches and convergence points, NOT a line:
        the-age-of-war --success--> magic-retained --+
                       --failure--> magic-lost ------+--> letos-manor

    A naive default_next walk (pass 1's bug) visits only the default path and
    then appends branch scenes in arbitrary order, so `magic-lost` was fed
    `malvakar-riddle`'s outro — eleven scenes and one whole branch away. The
    resulting contract was fluent, plausible and completely wrong.

    We instead order by dependency and hand each scene its REAL predecessor.
    At a convergence point (several branches feeding one scene) the incoming
    states differ, so the scene's continuity_in must be satisfiable by ALL of
    them; we pass every inbound outro and say so explicitly.
    """
    inbound = {}
    for sid, data in loaded.items():
        targets = []
        for br in data.get("branches") or []:
            if br.get("next"):
                targets.append(br["next"])
        dn = data.get("default_next")
        if dn:
            targets.append(dn)
        for tgt in targets:
            if tgt in loaded and tgt != sid:
                inbound.setdefault(tgt, [])
                if sid not in inbound[tgt]:
                    inbound[tgt].append(sid)

    # Kahn topological sort; ties broken by filename order for determinism.
    remaining = dict(loaded)
    order = []
    placed = set()
    while remaining:
        ready = [sid for sid in remaining
                 if all(p in placed for p in inbound.get(sid, []))]
        if not ready:
            # cycle (e.g. a loop-closing edge) — break it deterministically
            ready = [sorted(remaining)[0]]
            log.warning("cycle detected; breaking at %s", ready[0])
        ready.sort()
        for sid in ready:
            order.append(sid)
            placed.add(sid)
            remaining.pop(sid, None)
    return order, inbound


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    loaded = {}
    for path in sorted(SCENES.glob("*.yaml")):
        d = yaml.safe_load(path.read_text())
        loaded[d["id"]] = d

    pass1 = yaml.safe_load(PASS1.read_text())["scenes"]

    spine = {k: v for k, v in loaded.items() if not v.get("ambient")}
    spine_order, inbound = build_graph(spine)
    log.info("spine DAG (%d) topological order: %s",
             len(spine_order), " -> ".join(spine_order))
    for sid, preds in sorted(inbound.items()):
        if len(preds) > 1:
            log.info("  convergence: %s <- %s", sid, preds)

    results = {}
    if OUT.exists():
        results = (yaml.safe_load(OUT.read_text()) or {}).get("scenes", {}) or {}
        log.info("resuming: %d done", len(results))

    for sid in spine_order:
        if sid in results:
            continue
        data = spine[sid]

        preds = inbound.get(sid, [])
        if not preds:
            prev_id = "none"
            prev_out = ("(nothing — this is the first scene of the campaign. "
                        "The party has no shared history yet.)")
        elif len(preds) == 1:
            prev_id = preds[0]
            prev_out = (results.get(prev_id)
                        or pass1.get(prev_id, {})).get(
                            "continuity_out", "(unknown)")
        else:
            # Convergence: several branches lead here. continuity_in must hold
            # for EVERY inbound path, so show them all and require the overlap.
            prev_id = " OR ".join(preds)
            lines = []
            for p in preds:
                out = (results.get(p) or pass1.get(p, {})).get(
                    "continuity_out", "(unknown)")
                lines.append(f"  - via '{p}': {out}")
            prev_out = (
                "THIS SCENE IS A CONVERGENCE POINT. Any of the following "
                "paths may have led here:\n" + "\n".join(lines) +
                "\n\ncontinuity_in must be true on EVERY one of those paths. "
                "Record only what they share; never assume a particular "
                "branch was taken.")

        nxt = data.get("default_next")
        next_block = ""
        if nxt and nxt in loaded:
            nxt_enter = (loaded[nxt].get("enter_narration") or "").strip()
            next_block = (
                f"FOR AWARENESS ONLY — the NEXT scene is '{nxt}', which opens:\n"
                f"{nxt_enter}\n"
                f"Do NOT include any of that scene's events in continuity_out.\n\n")

        user = USER.format(
            canon=CANON,
            prev_id=prev_id,
            prev_out=prev_out,
            sid=sid,
            title=data.get("title") or sid,
            enter=(data.get("enter_narration") or "").strip(),
            body=scene_body(data),
            next_block=next_block,
        )

        t0 = time.time()
        try:
            parsed = parse_reply(ask(args.model, user))
        except Exception as exc:
            log.error("%s: %s", sid, exc)
            continue
        if parsed is None:
            log.error("%s: unparseable reply", sid)
            continue

        parsed["ambient"] = False
        results[sid] = parsed
        log.info("[%d/%d] %s (%.1fs)", len(results), len(spine_order), sid,
                 time.time() - t0)
        OUT.write_text(yaml.safe_dump(
            {"model": args.model, "pass": 2, "scenes": results},
            sort_keys=True, allow_unicode=True, width=100))

    missing = [s for s in spine_order if s not in results]
    log.info("complete: %d/%d spine scenes", len(results), len(spine_order))
    if missing:
        log.warning("MISSING: %s", ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
