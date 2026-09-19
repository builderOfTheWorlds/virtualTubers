"""Backfill continuity_in / continuity_out onto the 49 authored Ashiorid scenes.

Plan reference: .claude/prompts/generator_retarget_scenes_plan.md §6C (open
question 8, answered yes).

WHY THIS IS SAFE TO DELEGATE TO A LOCAL MODEL
---------------------------------------------
`docs/campaign_module_status.md` ("the ratchet") says prose/show content is
hand-written, never model-written. Continuity summaries are NOT show content:
they are planning metadata that is never spoken aloud (§6C.3) and never
reaches TTS. The model is summarizing prose a human already wrote, not
authoring new prose. That is a summarization task, not an authoring task.

OUTPUT IS PROPOSED, NOT PROMOTED
--------------------------------
Writes to .claude/prompts/continuity_backfill.yaml for human review. It does
NOT touch campaigns/ashiorid_1/scenes/. Promotion is a separate, explicit step
(plan §1.4) — a model never writes tracked source directly.

HARD RULE (§6C.3)
-----------------
Ambient scenes get continuity_in ONLY. An ambient scene that emits a
continuity_out is a validation error: ambient decides nothing, so nothing may
ever depend on it having played. That rule is what keeps ambient injectable
anywhere, and it is enforced here structurally (we never even ask the model
for an ambient outro) rather than hoped for in a prompt.

Usage:
    .venv/bin/python .claude/prompts/backfill_continuity.py [--limit N] [--model M]
"""
import argparse
import json
import logging
import pathlib
import sys
import time
import urllib.request

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
log = logging.getLogger("backfill")

REPO = pathlib.Path("/home/secus/codeProjects/virtualTubers")
PACK = REPO / "campaigns" / "ashiorid_1"
SCENES = PACK / "scenes"
OUT = REPO / ".claude" / "prompts" / "continuity_backfill.yaml"

OLLAMA = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "hermes3:70b"

SYSTEM = (
    "You write continuity metadata for a serialized fantasy campaign. You are "
    "NOT writing dialogue or narration — nothing you write is ever spoken "
    "aloud. You are recording, in plain declarative prose, what is true about "
    "the story's state at a given moment.\n\n"
    "Rules:\n"
    "- One or two short sentences. No more.\n"
    "- Declarative statements of fact about the party's knowledge, "
    "relationships, possessions, location, and unresolved threats.\n"
    "- Never use second person. Never address the audience.\n"
    "- Never invent events that the scene text does not support.\n"
    "- CRITICAL: distinguish the PRESENT of the campaign from stories told "
    "INSIDE it. Some scenes are flashbacks, legends, or tales a character "
    "narrates about the distant past. For those, the continuity you record is "
    "about what the LISTENING party now knows or believes — NOT about the "
    "events of the tale as though the party lived them. If a scene depicts "
    "ancient history being recounted, the party has HEARD a story; they have "
    "not travelled anywhere or done anything.\n"
    "- Write only the summary text. No preamble, no labels, no quotes."
)

SPINE_USER = """Scene id: {sid}
Title: {title}

Opening narration (spoken):
{enter}

What happens in this scene:
{body}

{prev_block}Before writing, decide: does this scene happen in the campaign's
PRESENT, or does it depict/recount events from the distant past (a legend, a
flashback, a tale told aloud)? If the latter, your summaries must describe what
the party now KNOWS or has been TOLD — never place the party inside the tale.


Write TWO summaries for this scene, as YAML with exactly two keys.

continuity_in: the state of the story this scene ASSUMES is already true when
it begins. What must the party already know, have, or have done for this scene
to make sense?

continuity_out: the state this scene GUARANTEES is true when it ends. What has
changed — what do they now know, have, or owe?

Reply with ONLY this YAML shape and nothing else:

continuity_in: <one or two sentences>
continuity_out: <one or two sentences>
"""

AMBIENT_USER = """Ambient scene id: {sid}

This is a filler scene. It decides nothing and can be injected at ANY point in
the campaign, including very early before the party has learned anything.

Its generation prompt:
{prompt}

Write ONE summary: the minimum state this scene assumes. Because it may play
at any time, this must be as UNDEMANDING as possible — ideally it assumes only
that the party is travelling together. Do not assume any plot revelation.

Reply with ONLY this YAML shape and nothing else:

continuity_in: <one short sentence>
"""


def scene_body(data, limit=2200):
    """Flatten a scene's beats into readable text for the model."""
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
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 300},
    }
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                OLLAMA, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.load(resp)
            return data["message"]["content"].strip()
        except Exception as exc:
            last = exc
            log.warning("attempt %d/%d failed: %s", attempt, retries, exc)
            time.sleep(3 * attempt)
    raise RuntimeError(f"ollama failed after {retries} attempts: {last}")


def parse_reply(reply, want_out):
    """Pull continuity_in/out from a model reply, tolerating fenced YAML."""
    text = reply.strip()
    if "```" in text:
        chunks = text.split("```")
        for chunk in chunks:
            if "continuity_in" in chunk:
                text = chunk.replace("yaml", "", 1).strip()
                break
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        data = None
    if not isinstance(data, dict):
        return None
    result = {}
    cin = data.get("continuity_in")
    if not isinstance(cin, str) or not cin.strip():
        return None
    result["continuity_in"] = " ".join(cin.split())
    if want_out:
        cout = data.get("continuity_out")
        if not isinstance(cout, str) or not cout.strip():
            return None
        result["continuity_out"] = " ".join(cout.split())
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files = sorted(SCENES.glob("*.yaml"))
    if args.limit:
        files = files[: args.limit]
    log.info("backfilling %d scenes with %s", len(files), args.model)

    # resume: keep anything already produced
    results = {}
    if OUT.exists():
        prior = yaml.safe_load(OUT.read_text()) or {}
        results = prior.get("scenes", {}) or {}
        log.info("resuming: %d already done", len(results))

    # map scene id -> predecessor, for spine context
    loaded = {}
    for path in files:
        d = yaml.safe_load(path.read_text())
        loaded[d["id"]] = (path, d)
    prev_of = {}
    for sid, (_, d) in loaded.items():
        nxt = d.get("default_next")
        if nxt:
            prev_of.setdefault(nxt, sid)
        for br in d.get("branches") or []:
            if br.get("next"):
                prev_of.setdefault(br["next"], sid)

    done = 0
    for path in files:
        data = yaml.safe_load(path.read_text())
        sid = data["id"]
        if sid in results:
            continue
        ambient = bool(data.get("ambient"))

        if ambient:
            prompt = (data.get("prompt") or "").strip()
            user = AMBIENT_USER.format(sid=sid, prompt=prompt[:1200])
        else:
            prev_block = ""
            prev = prev_of.get(sid)
            if prev and prev in loaded:
                pdata = loaded[prev][1]
                penter = (pdata.get("enter_narration") or "").strip()
                prev_block = (
                    f"The scene immediately before this one is "
                    f"'{prev}', which opens: {penter}\n\n")
            user = SPINE_USER.format(
                sid=sid,
                title=data.get("title") or sid,
                enter=(data.get("enter_narration") or "").strip(),
                body=scene_body(data),
                prev_block=prev_block,
            )

        t0 = time.time()
        try:
            reply = ask(args.model, user)
        except Exception as exc:
            log.error("scene %s: %s", sid, exc)
            continue

        parsed = parse_reply(reply, want_out=not ambient)
        if parsed is None:
            log.error("scene %s: unparseable reply: %r", sid, reply[:200])
            continue

        # HARD RULE §6C.3 — structurally enforced, not merely requested.
        if ambient:
            parsed.pop("continuity_out", None)

        parsed["ambient"] = ambient
        results[sid] = parsed
        done += 1
        log.info("[%d/%d] %s (%.1fs) %s", len(results), len(files), sid,
                 time.time() - t0, "ambient" if ambient else "spine")

        OUT.write_text(yaml.safe_dump(
            {"model": args.model, "scenes": results},
            sort_keys=True, allow_unicode=True, width=100))

    log.info("complete: %d scenes total, %d new this run -> %s",
             len(results), done, OUT)

    missing = [p.stem for p in files
               if yaml.safe_load(p.read_text())["id"] not in results]
    if missing:
        log.warning("MISSING %d: %s", len(missing), ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
