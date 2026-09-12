"""Post one issue to Gitea: LLMImproviser.generate_scene case-sensitive
speaker-id parsing bug, found during model A/B testing on 2026-08-26.

Token is read from the GITEA_TOKEN env var, or ~/.gitea_token if unset.
Never printed, never logged.

Usage:
    GITEA_TOKEN=... python3 .claude/prompts/post_case_sensitivity_issue.py [--dry-run]
"""
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

BASE = "http://192.168.1.120:3300"
OWNER = "gitea_admin"
REPO = "virtualTubers"

TITLE = "LLMImproviser.generate_scene: case-sensitive speaker-id match misfiles dialogue as narration"

BODY = """## Summary

`LLMImproviser.generate_scene` in `app/campaign/improviser.py` parses each
line of the model's reply as either `dialogue` (if the line's speaker
prefix matches a cast id) or `narration` (fallback). The match is
**case-sensitive**, so whenever the LLM writes a speaker label in a
different case than the cast id (e.g. `vigil:` instead of `Vigil:`,
`leena:` instead of `Leena:`), the line is misfiled as GM narration
instead of dialogue — even though it's clearly meant to be spoken by that
character.

## Where

`app/campaign/improviser.py`, `generate_scene()`:

```python
prefix, _, rest = line.partition(":")
if _ and prefix.strip() in self.pack.cast:
    kind, speaker, raw = "dialogue", prefix.strip(), rest
else:
    kind, speaker, raw = "narration", self.pack.gm_id, line
```

`prefix.strip() in self.pack.cast` is a case-sensitive dict-key lookup
against cast ids like `Vigil`, `Leena`, `sodacan_bob`, `chadwick` — mixed
casing already exists in the cast roster itself (see
`campaigns/ashiorid_1/cast/*.yaml`), which makes this worse: the model has
no consistent casing convention to imitate even when it's trying to.

## Evidence

Found while running a controlled A/B test of `hermes3:70b` vs
`qwen3.8:27b` against real ambient scenes in `campaigns/ashiorid_1`,
through the exact production code path (`LLMImproviser.generate_scene`).
Both models exhibited the bug — it is independent of model choice.

Sample from `chadwick-lost` (hermes3:70b), lines that should be dialogue
misfiled as narration:

```
[dialogue] chadwick: We've been walking for hours, and I still don't recognize anything.
[narration] gm: leena: Maybe we should consult the map, just to be sure.
[narration] gm: vigil: I know exactly where we are, but I'll wait until someone asks.
[dialogue] sodacan_bob: This is hilarious! Chadwick's so lost, but he won't admit it!
[narration] gm: leena: You know, Chadwick, even the greatest leaders need help sometimes.
```

Batch results across 6 real ambient scenes (`camp-fire`, `road-talk`,
`night-watch`, `the-meal`, `sodacan_bob-on-craft`, `chadwick-lost`):

| Model | Beats | Misattributed (dialogue -> narration) |
|---|---|---|
| hermes3:70b | 46 | 12 |
| qwen3.8:27b | 20 | 6 |

Roughly a quarter of all generated beats across both models were affected.

## Impact

- Ambient scene dialogue silently loses per-character voice/TTS routing
  and gets read as GM narration instead — a real content-quality
  regression, not just a display issue, since downstream consumers
  (TTS, transcript rendering) branch on `kind`/`speaker`.
- Affects the real production heavy model (`hermes3:70b`) as much as or
  more than lighter models, so this isn't masked by the current config.

## Suggested fix

Normalize case on both sides of the membership check, e.g. build a
lowercased lookup of cast ids once (`{cid.lower(): cid for cid in
self.pack.cast}`) and match `prefix.strip().lower()` against it, then use
the *canonical* cast id (correct casing) as `speaker` regardless of what
casing the model produced. Should be a small, localized change to
`generate_scene()`; the per-beat `__call__` improviser path already
receives a `CastMember` object directly and is not affected by this
specific bug.

## Repro

```python
import sys, pathlib
REPO_ROOT = pathlib.Path("/home/secus/codeProjects/virtualTubers")
sys.path.insert(0, str(REPO_ROOT / "app"))
sys.path.insert(0, str(REPO_ROOT / "utilities/3LayersWeeklyGeneration/src"))
from campaign.pack import load_pack, Scene
from campaign.improviser import LLMImproviser
import concurrent_llm

pack = load_pack(REPO_ROOT / "campaigns/ashiorid_1")
scene_data = pack.scenes["chadwick-lost"]
scene = Scene(id=scene_data.id, prompt=scene_data.prompt, lore=scene_data.lore)
llm = concurrent_llm.from_profile({
    "provider": "ollama", "base_url": "http://localhost:11434",
    "model": "hermes3:70b", "temperature": 0.9, "max_tokens": 1024,
    "timeout_s": 300, "num_ctx": 8192,
})
improv = LLMImproviser(pack, llm)
improv.update_context(scene=scene, loop=1, carry={})
for b in improv.generate_scene(scene):
    print(f"[{b.kind}] {b.speaker}: {b.text}")
```
"""


def load_token() -> str:
    token = os.environ.get("GITEA_TOKEN", "").strip()
    if token:
        return token
    path = pathlib.Path.home() / ".gitea_token"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    sys.exit("ERROR: no token. Set GITEA_TOKEN or write it to ~/.gitea_token")


def api(method: str, path: str, token: str, payload=None):
    url = f"{BASE}/api/v1{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        sys.exit(f"ERROR: {method} {path} -> HTTP {exc.code}: {detail}")


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    token = load_token()

    existing = set()
    for state in ("open", "closed"):
        page = 1
        while True:
            batch = api("GET", f"/repos/{OWNER}/{REPO}/issues?state={state}"
                               f"&limit=50&page={page}", token) or []
            existing.update(i["title"] for i in batch)
            if len(batch) < 50:
                break
            page += 1

    if TITLE in existing:
        print(f"skip (exists): {TITLE}")
        return 0

    if dry_run:
        print(f"would create: {TITLE}  ({len(BODY)} chars)")
        return 0

    issue = api("POST", f"/repos/{OWNER}/{REPO}/issues", token,
                {"title": TITLE, "body": BODY})
    print(f"created #{issue['number']}: {TITLE}")
    print(f"URL: {BASE}/{OWNER}/{REPO}/issues/{issue['number']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
