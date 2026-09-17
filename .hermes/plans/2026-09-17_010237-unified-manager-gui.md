# Unified virtualTubers Manager GUI — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Give the operator one web GUI that can see and control every layer of the
virtualTubers stack — campaign pack content, 3-layer generation, the episode
store, and the live worker/stream layer — instead of three disconnected tools
(campaign-manager, control-panel, and hand-editing files over SSH).

**Architecture:** Extend the existing `campaign-manager` FastAPI + Jinja2 + HTMX
service with a new **Pack Editor** section, and a new **Publish** section that
bridges a completed generator run into the episode store. Campaign pack content
(scenes, cast, lore, campaign.yaml) moves from host files into **Postgres** —
new tables in the existing `generator-postgres` instance, alongside
`generator_configs`/`generation_jobs`/`generation_artifacts` — exposed only
through new `generator_api.py` HTTP endpoints. **campaign-manager never gets a
filesystem mount or a DB credential for this: it stays a pure HTTP client of
the generator API**, exactly like it already is for configs/jobs/artifacts.
Control-panel's worker/message/replay surface stays as-is; campaign-manager
grows a *link out* to it (and vice versa) rather than merging two services with
different auth/deployment stories into one codebase in a single pass.

**Revision note:** the first draft of this plan proposed a read-write bind
mount of `campaigns/` into campaign-manager's container for Phase 2 editing.
Rejected on review — no service in this stack has ever had write access to a
host directory, including the generator itself (`3layer-generator` mounts
`campaigns/` `:ro`, confirmed in `docker-compose.yml:648`), and adding one
specifically to enable browser-driven edits is a strictly worse trust boundary
than what exists today. This revision removes every filesystem mount from the
plan; Postgres is the only new write target, reached the same way every other
mutation in this stack already happens (an HTTP endpoint, on a service that
already holds DB credentials).

**Tech Stack:** FastAPI, Jinja2, HTMX, Bootstrap 5 (matching existing services
exactly — zero new frontend framework, zero build step), PyYAML, httpx, psycopg2
(already a `3layer-generator` dependency; not a new one).

---

## Current-state inventory (verified against the running code, not assumed)

| Concern | Owner today | Gap |
|---|---|---|
| Submit/monitor arc/segment/dialogue jobs | `campaign-manager` (`/`, `/job/{id}`) | none — done this session |
| Browse generator artifacts (arc_plan/brief/tree/dialogue) | `campaign-manager` (`/data`) | read-only, JSON dump (Story Beats table added this session) |
| Generator model/budget configs | `campaign-manager` (`/configs/*`), stored in `generator-postgres` | none |
| **Campaign pack content** (scenes, cast, lore, campaign.yaml) | **nobody** — host files under `campaigns/`, read-only even to the generator container, hand-edited over SSH | **this plan's main gap** — and the reason it's on disk at all instead of Postgres is historical, not deliberate |
| Bridge generated dialogue → episode store | `.claude/prompts/build_generated_episode.py`, run by hand from a terminal | not reachable from any GUI, no job id, no history |
| Bridge authored pack → episode store | `.claude/prompts/build_campaign_episode.py`, run by hand | same |
| Episode store CRUD (upload/list/view/delete) | `control-panel` (`/partials/replays`, `/replays/*`) | works, but only accepts a pre-built episode JSON — no path from a pack or a run to a stored episode without leaving the browser |
| Worker enable/disable, operator messages, log pruning | `control-panel` | none |
| Confirm a channel is actually live on Twitch | nobody (skill `virtualtubers-stream-ops` — manual curl/screenshot) | acceptable to leave manual for now; automating Twitch verification is out of scope here |

Two systems already do real work (`campaign-manager`, `control-panel`). This
plan does **not** propose a rewrite — it fills the one gap that has no owner
(pack content) and stitches the two existing owners together for the handoff
that currently only exists as a script you have to remember to run by hand.

---

## Scope decisions (read before objecting to something "missing")

- **In scope:** move campaign pack content into Postgres; view + edit it from
  campaign-manager via generator API endpoints (no filesystem access anywhere
  in this path); a "Publish to Episode Store" action that runs the existing
  converter logic as a tracked job instead of a manual script; cross-links
  between campaign-manager and control-panel so an operator doesn't have to
  remember two URLs.
- **Out of scope for this plan** (flagged, not forgotten):
  - Merging campaign-manager and control-panel into one process/codebase.
    They have different auth models (control-panel has optional Basic Auth;
    campaign-manager has none) and different failure domains (generator down
    vs. message-api down) — merging is a real project of its own, listed as
    Phase 5 below, not bundled in.
  - A rich WYSIWYG scene/dialogue editor. Phase 2 is a structured form editor
    over the existing YAML shape (add/edit/remove scenes, beats, cast members,
    lore notes) — good enough to stop hand-editing files, not a game-design tool.
  - Automated Twitch-live verification in the GUI (stays a skill/manual step).
  - Multi-user auth/roles. Matches current state (campaign-manager has zero
    auth today); flagged as a risk below, not solved here.
  - Migrating `generator-postgres` itself off the host (out of scope; this
    plan only adds tables to the instance that already exists).

---

## Phase 0 — Basic Auth on campaign-manager

**Objective (decision 1):** campaign-manager gains the same optional HTTP
Basic Auth pattern `control-panel` already runs in production, copied
verbatim rather than reinvented — before Phase 2 turns campaign-manager
into a write surface for campaign content, not just a job submitter.
Ordered first because every later phase's live-verification `curl` calls in
this plan need to know whether auth is on.

**Files:**
- Modify: `services/campaign-manager/main.py`
- Modify: `docker-compose.yml` (env vars for the `campaign-manager` service)
- Test: `services/campaign-manager/tests/test_auth.py`

**Step 1: Port the middleware from `control-panel/panel.py` verbatim**
(same functions, same env var naming convention, same no-op-unless-both-set
behavior, same `/healthz` bypass so container healthchecks don't need
credentials):

```python
import base64
import secrets
from fastapi.responses import PlainTextResponse

BASIC_AUTH_USER = os.environ.get("CAMPAIGN_MANAGER_BASIC_AUTH_USER", "")
BASIC_AUTH_PASS = os.environ.get("CAMPAIGN_MANAGER_BASIC_AUTH_PASS", "")


def _auth_enabled() -> bool:
    return bool(BASIC_AUTH_USER and BASIC_AUTH_PASS)


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    if not _auth_enabled() or request.url.path == "/healthz":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    user = pw = ""
    if header.startswith("Basic "):
        try:
            user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except Exception:
            log.warning("malformed Authorization header")
    if secrets.compare_digest(user, BASIC_AUTH_USER) and secrets.compare_digest(pw, BASIC_AUTH_PASS):
        return await call_next(request)
    return PlainTextResponse(
        "authentication required", status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="campaign-manager"'},
    )
```

**Step 2: Add env vars to `docker-compose.yml`**, next to
`campaign-manager`'s existing `environment:` block, matching
`control-panel`'s already-established `.env` naming convention:

```yaml
    environment:
      GENERATOR_API_URL: http://host.docker.internal:8001
      CAMPAIGN_MANAGER_BASIC_AUTH_USER: ${CAMPAIGN_MANAGER_BASIC_AUTH_USER:-}
      CAMPAIGN_MANAGER_BASIC_AUTH_PASS: ${CAMPAIGN_MANAGER_BASIC_AUTH_PASS:-}
```

Unset by default (matches `control-panel`'s own default-off posture) — set
both in `.env` to turn auth on. Document this in the plan's Phase 0
verification step below and again in whatever README/runbook covers
deployment env vars, so it isn't a surprise the first time someone deploys
with campaign write access enabled.

**Step 3: Test**

```python
"""Auth tests -- mirrors control-panel's own auth test pattern if one
exists; if not, this establishes it for both services to converge on."""
import base64

import pytest
from fastapi.testclient import TestClient

import main as api


@pytest.fixture
def auth_client(monkeypatch):
    monkeypatch.setattr(api, "BASIC_AUTH_USER", "op")
    monkeypatch.setattr(api, "BASIC_AUTH_PASS", "secret")
    return TestClient(api.app)


def _basic(user, pw):
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_no_credentials_is_401(auth_client):
    resp = auth_client.get("/")
    assert resp.status_code == 401


def test_correct_credentials_pass(auth_client):
    resp = auth_client.get("/", headers=_basic("op", "secret"))
    assert resp.status_code != 401


def test_wrong_password_is_401(auth_client):
    resp = auth_client.get("/", headers=_basic("op", "wrong"))
    assert resp.status_code == 401


def test_healthz_never_requires_auth(auth_client):
    assert auth_client.get("/healthz").status_code == 200


def test_auth_is_a_noop_when_unset(monkeypatch):
    monkeypatch.setattr(api, "BASIC_AUTH_USER", "")
    monkeypatch.setattr(api, "BASIC_AUTH_PASS", "")
    client = TestClient(api.app)
    assert client.get("/").status_code != 401
```

**Step 4: Run + commit**

```bash
cd services/campaign-manager && python -m pytest tests/test_auth.py -v
git add services/campaign-manager/main.py docker-compose.yml services/campaign-manager/tests/test_auth.py
git commit -m "feat(campaign-manager): optional HTTP Basic Auth, matching control-panel"
```

---

## Phase 1 — Campaign Pack Viewer (read-only, DB-backed)

**Objective:** An operator can browse a pack's full content (scenes, cast,
lore, campaign.yaml) from the browser, sourced from Postgres, with campaign-
manager touching neither a filesystem mount nor a database credential of its
own. This alone closes most of the "I can't see what's in the pack"
confusion from this session.

### Task 1.1: New Postgres tables for pack content

**Files:**
- Modify: `services/3layer-generator/generation_store.py`
- Test: `services/3layer-generator/tests/test_generation_store.py` (or
  wherever the existing `generator_configs` store tests live — mirror that
  file's pattern exactly)

**Step 1: Add the schema**, next to `generator_configs`'s own
`CREATE_TABLE_SQL` block, same whole-document-per-row rationale (small
documents, validated at the edges not at rest, an operator wants to see/edit
one scene as the same YAML unit that used to be one file):

```sql
-- Campaign pack content (2026-09) — moved off host files specifically so no
-- container needs write access to a host directory to support browser-driven
-- editing (see plan doc .hermes/plans/2026-09-17_010237-unified-manager-gui.md
-- for why). One row per campaign.yaml/scene/cast-member/lore-note, each the
-- WHOLE document as YAML/text — same shape those files had on disk, not a
-- normalized schema, so load_pack()'s existing parser needs zero changes:
-- a pack is materialized back into that exact directory shape in a
-- container-local temp dir (never a host bind mount) whenever something
-- needs to call load_pack() against it.
CREATE TABLE IF NOT EXISTS pack_campaigns (
    pack_name     TEXT PRIMARY KEY,
    campaign_yaml TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pack_scenes (
    id          BIGSERIAL PRIMARY KEY,
    pack_name   TEXT NOT NULL REFERENCES pack_campaigns(pack_name) ON DELETE CASCADE,
    scene_id    TEXT NOT NULL,
    scene_yaml  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pack_name, scene_id)
);

CREATE TABLE IF NOT EXISTS pack_cast (
    id          BIGSERIAL PRIMARY KEY,
    pack_name   TEXT NOT NULL REFERENCES pack_campaigns(pack_name) ON DELETE CASCADE,
    member_id   TEXT NOT NULL,
    member_yaml TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pack_name, member_id)
);

CREATE TABLE IF NOT EXISTS pack_lore (
    id          BIGSERIAL PRIMARY KEY,
    pack_name   TEXT NOT NULL REFERENCES pack_campaigns(pack_name) ON DELETE CASCADE,
    lore_name   TEXT NOT NULL,
    lore_text   TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pack_name, lore_name)
);
```

Append to `ensure_schema()`'s existing `CREATE_TABLE_SQL` execution — same
function, same "safe to call repeatedly, `IF NOT EXISTS` everywhere"
contract already documented there.

**Step 2: Add read functions to `generation_store.py`**, mirroring
`list_configs`/`get_config_row`'s exact shape:

```python
_PACK_SCENE_COLUMNS = "id, pack_name, scene_id, scene_yaml, created_at, updated_at"
_PACK_CAST_COLUMNS = "id, pack_name, member_id, member_yaml, created_at, updated_at"
_PACK_LORE_COLUMNS = "id, pack_name, lore_name, lore_text, created_at, updated_at"


def list_pack_names() -> list[str]:
    """Every pack with a campaign_yaml row, alphabetical."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pack_name FROM pack_campaigns ORDER BY pack_name")
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def get_campaign_row(pack_name: str) -> dict | None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pack_name, campaign_yaml, created_at, updated_at "
                "FROM pack_campaigns WHERE pack_name = %s", (pack_name,))
            row = cur.fetchone()
            return _row_to_dict(cur, row) if row else None
    finally:
        conn.close()


def list_scenes(pack_name: str) -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_PACK_SCENE_COLUMNS} FROM pack_scenes "
                "WHERE pack_name = %s ORDER BY scene_id", (pack_name,))
            return [_row_to_dict(cur, row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_cast(pack_name: str) -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_PACK_CAST_COLUMNS} FROM pack_cast "
                "WHERE pack_name = %s ORDER BY member_id", (pack_name,))
            return [_row_to_dict(cur, row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_lore(pack_name: str) -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_PACK_LORE_COLUMNS} FROM pack_lore "
                "WHERE pack_name = %s ORDER BY lore_name", (pack_name,))
            return [_row_to_dict(cur, row) for row in cur.fetchall()]
    finally:
        conn.close()
```

**Step 3: Add the materialization function** — this is the piece that lets
`load_pack()` keep working completely unchanged. It writes one pack's DB
rows into a fresh directory tree shaped exactly like a pack on disk, but
under `tempfile.mkdtemp()` — **never a host bind mount, never anywhere
outside the container's own ephemeral filesystem**:

```python
import shutil
import tempfile
from pathlib import Path


def materialize_pack(pack_name: str) -> Path:
    """Write pack_name's DB rows into a fresh container-local temp directory
    shaped exactly like a pack on disk (campaign.yaml, cast/*.yaml,
    scenes/*.yaml, lore/*.md), so load_pack() -- which only knows how to
    read a directory -- keeps working completely unchanged.

    Caller owns cleanup: `shutil.rmtree(path)` in a `finally`. This is
    deliberately NOT cached across calls -- pack content changes via the
    editor and a stale materialized copy silently validating against old
    content would be worse than the small cost of rewriting a few small
    YAML files per call.

    Raises FileNotFoundError if pack_name has no campaign_yaml row.
    """
    campaign_row = get_campaign_row(pack_name)
    if campaign_row is None:
        raise FileNotFoundError(f"no pack named {pack_name!r} in Postgres")

    root = Path(tempfile.mkdtemp(prefix=f"pack-{pack_name}-"))
    (root / "campaign.yaml").write_text(campaign_row["campaign_yaml"], encoding="utf-8")

    cast_dir = root / "cast"
    cast_dir.mkdir()
    for row in list_cast(pack_name):
        (cast_dir / f"{row['member_id']}.yaml").write_text(row["member_yaml"], encoding="utf-8")

    scenes_dir = root / "scenes"
    scenes_dir.mkdir()
    for row in list_scenes(pack_name):
        (scenes_dir / f"{row['scene_id']}.yaml").write_text(row["scene_yaml"], encoding="utf-8")

    lore_rows = list_lore(pack_name)
    if lore_rows:
        lore_dir = root / "lore"
        lore_dir.mkdir()
        for row in lore_rows:
            (lore_dir / f"{row['lore_name']}.md").write_text(row["lore_text"], encoding="utf-8")

    return root
```

**Step 4: Add write functions** — upsert/delete for scene, cast member, lore
note, matching `create_config`/`update_config`/`delete_config`'s exact
shape:

```python
def upsert_scene(pack_name: str, scene_id: str, scene_yaml: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pack_scenes (pack_name, scene_id, scene_yaml) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (pack_name, scene_id) DO UPDATE "
                "SET scene_yaml = EXCLUDED.scene_yaml, updated_at = now()",
                (pack_name, scene_id, scene_yaml),
            )
    finally:
        conn.close()


def delete_scene(pack_name: str, scene_id: str) -> bool:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM pack_scenes WHERE pack_name = %s AND scene_id = %s",
                (pack_name, scene_id),
            )
            return cur.rowcount > 0
    finally:
        conn.close()


# upsert_cast_member / delete_cast_member, upsert_lore / delete_lore:
# identical shape to upsert_scene/delete_scene above, against pack_cast /
# pack_lore respectively -- not repeated here to avoid four copies of the
# same eight lines; implement by direct analogy.


def upsert_campaign(pack_name: str, campaign_yaml: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pack_campaigns (pack_name, campaign_yaml) "
                "VALUES (%s, %s) "
                "ON CONFLICT (pack_name) DO UPDATE "
                "SET campaign_yaml = EXCLUDED.campaign_yaml, updated_at = now()",
                (pack_name, campaign_yaml),
            )
    finally:
        conn.close()
```

**Step 5: Tests** — mirror whatever pattern the existing
`generator_configs` store tests use (real Postgres via a test fixture, or a
`FakeStore` at the `generator_api` layer — check which one
`test_service_runner.py`'s `FakeStore` already covers for configs and match
it). At minimum:

```python
def test_materialize_pack_writes_a_load_pack_shaped_directory(tmp_pg, monkeypatch):
    monkeypatch.setattr(tempfile, "mkdtemp", lambda prefix="": str(tmp_path_for_test))
    store.upsert_campaign("alpha", CAMPAIGN_YAML_FIXTURE)
    store.upsert_cast_member("alpha", "gm", CAST_YAML_FIXTURE)
    store.upsert_scene("alpha", "intro", SCENE_YAML_FIXTURE)

    root = store.materialize_pack("alpha")
    try:
        from campaign.pack import load_pack
        pack = load_pack(root)          # must not raise
        assert pack.name == "alpha"
    finally:
        shutil.rmtree(root)


def test_materialize_pack_raises_for_unknown_pack(tmp_pg):
    with pytest.raises(FileNotFoundError):
        store.materialize_pack("nope")


def test_upsert_scene_is_idempotent(tmp_pg):
    store.upsert_campaign("alpha", CAMPAIGN_YAML_FIXTURE)
    store.upsert_scene("alpha", "intro", "id: intro\ntitle: v1\n")
    store.upsert_scene("alpha", "intro", "id: intro\ntitle: v2\n")
    scenes = store.list_scenes("alpha")
    assert len(scenes) == 1
    assert "v2" in scenes[0]["scene_yaml"]
```

**Step 6: Run + commit**

```bash
cd services/3layer-generator && python -m pytest tests/ -k pack -v
git add generation_store.py tests/
git commit -m "feat(3layer-generator): add Postgres-backed pack content tables"
```

---

### Task 1.2: One-time import from the existing host files into Postgres

**Files:**
- Create: `services/3layer-generator/scripts/import_packs_to_postgres.py`

This is the ONE place the read-only `campaigns/:ro` host mount still gets
read from directly — a one-shot, idempotent (safe to re-run) import, not a
runtime dependency. After this runs successfully for a pack, Postgres is
authoritative for it; the host files become a stale backup, not a live
source.

```python
#!/usr/bin/env python3
"""One-time import: campaigns/<pack>/* on disk -> Postgres pack_* tables.

Idempotent -- re-running upserts every row again, so a partial failure
partway through a pack is safe to just re-run. Reads via the existing
read-only PACK_ROOT mount; this is the only script in the whole plan that
touches the host filesystem for pack content, and it never writes to it.

Usage (from inside the 3layer-generator container, or any environment with
the same POSTGRES_* env vars and PACK_ROOT set):
    python scripts/import_packs_to_postgres.py [pack_name ...]
    # with no args, imports every pack directory found under PACK_ROOT
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import generation_store as store

PACK_ROOT = Path("/data/packs")  # matches generator_api.PACK_ROOT


def import_pack(pack_name: str) -> None:
    pack_dir = PACK_ROOT / pack_name
    campaign_yaml = (pack_dir / "campaign.yaml").read_text(encoding="utf-8")
    store.upsert_campaign(pack_name, campaign_yaml)

    cast_dir = pack_dir / "cast"
    for path in sorted(cast_dir.glob("*.yaml")) + sorted(cast_dir.glob("*.yml")):
        store.upsert_cast_member(pack_name, path.stem, path.read_text(encoding="utf-8"))

    scenes_dir = pack_dir / "scenes"
    for path in sorted(scenes_dir.glob("*.yaml")) + sorted(scenes_dir.glob("*.yml")):
        import yaml
        scene_id = yaml.safe_load(path.read_text(encoding="utf-8"))["id"]
        store.upsert_scene(pack_name, scene_id, path.read_text(encoding="utf-8"))

    lore_dir = pack_dir / "lore"
    if lore_dir.is_dir():
        for path in sorted(lore_dir.glob("*.md")):
            store.upsert_lore(pack_name, path.stem, path.read_text(encoding="utf-8"))

    print(f"imported {pack_name}: "
          f"{len(list(cast_dir.glob('*.yaml')))} cast, "
          f"{len(list(scenes_dir.glob('*.yaml')))} scenes, "
          f"{len(list(lore_dir.glob('*.md'))) if lore_dir.is_dir() else 0} lore notes")


if __name__ == "__main__":
    names = sys.argv[1:] or sorted(p.name for p in PACK_ROOT.iterdir() if p.is_dir())
    for name in names:
        import_pack(name)
```

**Verify:** run it, then confirm via the DB that `ashiorid_1` round-trips
through `load_pack()`:

```bash
docker compose exec 3layer-generator python scripts/import_packs_to_postgres.py ashiorid_1
docker compose exec 3layer-generator python -c "
import generation_store as store
root = store.materialize_pack('ashiorid_1')
from campaign.pack import load_pack
pack = load_pack(root)
print('scenes:', len(pack.scenes), 'cast:', len(pack.cast))
"
```
Expected: scene/cast counts matching what `pack_summary` showed for
`ashiorid_1` earlier this session (28 spine scenes, several ambient, 5 cast
members incl. gm).

**Commit:**
```bash
git add services/3layer-generator/scripts/import_packs_to_postgres.py
git commit -m "feat(3layer-generator): one-time pack import script (disk -> Postgres)"
```

---

### Task 1.3: `generator_api.py` read endpoints

**Files:** Modify `services/3layer-generator/generator_api.py`; extend
`services/3layer-generator/tests/test_api.py`.

```python
@app.get("/packs")
def list_packs():
    """Pack names, now from Postgres (Task 1.1) instead of scanning
    PACK_ROOT. Unchanged response shape -- the campaign-manager dropdown
    this already feeds needs no changes."""
    return {"packs": store.list_pack_names()}


@app.get("/packs/{pack_name}")
def get_pack_summary(pack_name: str):
    """Materialize, load, summarize, clean up -- never leaves a temp dir
    behind even on the happy path."""
    import shutil
    try:
        root = store.materialize_pack(pack_name)
    except FileNotFoundError:
        raise HTTPException(404, f"pack {pack_name!r} not found")
    try:
        from campaign.pack import load_pack, PackError
        try:
            pack = load_pack(root)
        except PackError as exc:
            raise HTTPException(422, f"pack {pack_name!r} does not load: {exc}")
        return {
            "name": pack.name, "title": pack.title, "genre": pack.genre,
            "start_scene": pack.start_scene, "gm_id": pack.gm_id,
            "player_ids": pack.player_ids,
            "scenes": [{"id": s.id, "title": s.title, "ambient": s.ambient,
                        "default_next": s.default_next}
                       for s in sorted(pack.scenes.values(), key=lambda s: s.id)],
            "cast": [{"id": c.id, "name": c.name, "role": c.role,
                      "archetype": c.archetype}
                     for c in sorted(pack.cast.values(), key=lambda c: c.id)],
            "lore_names": sorted(pack.lore.keys()),
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


@app.get("/packs/{pack_name}/scenes/{scene_id}")
def get_pack_scene(pack_name: str, scene_id: str):
    """Raw scene YAML plus the parsed beat list -- the raw YAML is what an
    edit form pre-fills, the parsed beats are what a read-only detail view
    renders."""
    row = None
    for scene_row in store.list_scenes(pack_name):
        if scene_row["scene_id"] == scene_id:
            row = scene_row
            break
    if row is None:
        raise HTTPException(404, f"scene {scene_id!r} not found in pack {pack_name!r}")
    return {"scene_id": scene_id, "scene_yaml": row["scene_yaml"]}


@app.get("/packs/{pack_name}/cast/{member_id}")
def get_pack_cast_member(pack_name: str, member_id: str):
    for row in store.list_cast(pack_name):
        if row["member_id"] == member_id:
            return {"member_id": member_id, "member_yaml": row["member_yaml"]}
    raise HTTPException(404, f"cast member {member_id!r} not found in pack {pack_name!r}")


@app.get("/packs/{pack_name}/lore/{lore_name}")
def get_pack_lore(pack_name: str, lore_name: str):
    for row in store.list_lore(pack_name):
        if row["lore_name"] == lore_name:
            return {"lore_name": lore_name, "lore_text": row["lore_text"]}
    raise HTTPException(404, f"lore note {lore_name!r} not found in pack {pack_name!r}")
```

**Tests** (extend `test_api.py`, using its existing `FakeStore`/`client`
fixtures — add `list_pack_names`/`materialize_pack`/etc. to `FakeStore` in
`test_service_runner.py` first, same pattern the config methods already
follow there):

```python
def test_list_packs_reads_from_the_store(client, store):
    store.upsert_campaign("alpha", "name: alpha\n")
    resp = client.get("/packs")
    assert resp.json() == {"packs": ["alpha"]}


def test_get_pack_summary_for_unknown_pack_is_404(client, store):
    resp = client.get("/packs/nope")
    assert resp.status_code == 404


def test_get_pack_scene_returns_the_raw_yaml(client, store):
    store.upsert_campaign("alpha", CAMPAIGN_YAML_FIXTURE)
    store.upsert_cast_member("alpha", "gm", CAST_YAML_FIXTURE)
    store.upsert_scene("alpha", "intro", "id: intro\ntitle: Intro\n")
    resp = client.get("/packs/alpha/scenes/intro")
    assert resp.status_code == 200
    assert "title: Intro" in resp.json()["scene_yaml"]
```

**Commit:**
```bash
cd services/3layer-generator && python -m pytest tests/ -v
git add generator_api.py tests/
git commit -m "feat(3layer-generator): DB-backed pack read endpoints"
```

---

### Task 1.4: campaign-manager Pack Viewer (pure HTTP client, zero mounts)

**Files:**
- Modify: `services/campaign-manager/main.py` — **no Dockerfile change, no
  docker-compose.yml change, no new env var.** Every call below goes through
  the `http_client` campaign-manager already has pointed at the generator
  API, exactly like the existing `/configs/*` and `/data/*` routes.
- Create: `services/campaign-manager/templates/pack_viewer.html`
- Create: `services/campaign-manager/templates/pack_scene_detail.html`
- Modify: `services/campaign-manager/templates/dashboard.html` (nav link)

```python
@app.get("/packs/{pack_name}", response_class=HTMLResponse)
async def pack_viewer(request: Request, pack_name: str):
    resp = await http_client.get(f"/packs/{pack_name}")
    if resp.status_code == 404:
        return templates.TemplateResponse(
            request, "error.html", {"request": request,
                                     "error": f"pack {pack_name!r} not found"})
    resp.raise_for_status()
    return templates.TemplateResponse(request, "pack_viewer.html", {
        "request": request, "pack_name": pack_name, "pack": resp.json(),
    })


@app.get("/packs/{pack_name}/scenes/{scene_id}", response_class=HTMLResponse)
async def pack_scene_detail(request: Request, pack_name: str, scene_id: str):
    resp = await http_client.get(f"/packs/{pack_name}/scenes/{scene_id}")
    if resp.status_code == 404:
        return templates.TemplateResponse(
            request, "error.html",
            {"request": request, "error": f"scene {scene_id!r} not found"})
    resp.raise_for_status()
    data = resp.json()
    import yaml
    parsed = yaml.safe_load(data["scene_yaml"])
    return templates.TemplateResponse(request, "pack_scene_detail.html", {
        "request": request, "pack_name": pack_name,
        "scene_id": scene_id, "scene_yaml": data["scene_yaml"], "scene": parsed,
    })
```

Templates: same visual shape as the original draft's `pack_viewer.html` /
`pack_scene_detail.html` — reuse those templates verbatim, they only ever
read from a `pack`/`scene` context dict, which now comes from a JSON HTTP
response instead of a `campaign.pack.CampaignPack` object. (The one template
change: iterate `pack.scenes` / `pack.cast` — now plain dicts/lists from
JSON — instead of typed objects; Jinja's `{{ scene.title }}` attribute
syntax works identically on both, `dict.title` does not, so switch to
`{{ scene['title'] }}` or, cleaner, use `|attr` — simplest fix is templates
receive lists of dicts and use `scene.id`/`scene.title` via Jinja's
automatic dict-subscript-via-dot behavior, which **does** work for plain
dicts in Jinja2 — no change needed after all, confirmed by Jinja2's
`Environment.getattr` falling back to `__getitem__`.)

**Verify:**
```bash
docker compose build 3layer-generator campaign-manager
docker compose up -d 3layer-generator campaign-manager
docker compose exec 3layer-generator python scripts/import_packs_to_postgres.py ashiorid_1
curl -s http://localhost:8082/packs/ashiorid_1 -o /tmp/pv.html -w "%{http_code}\n"
grep -c "invitation-arc\|letos-manor\|invitation" /tmp/pv.html
```
Expected: `200`, at least one match.

**Commit:**
```bash
git add services/campaign-manager/main.py services/campaign-manager/templates/
git commit -m "feat(campaign-manager): Pack Viewer via generator API (no filesystem access)"
```

---

## Phase 2 — Campaign Pack Editor (write, via Postgres)

**Objective:** Add, edit, and remove scenes/cast/lore without SSH and
without any container ever gaining filesystem write access to a host
directory. Every write lands in Postgres through `generator_api.py`,
validated by materializing the *whole pack* into a container-local temp dir
and running the real `load_pack()` against it before the transaction is
allowed to stick — same safety contract the original draft had, just backed
by a database instead of a file-with-backup.

### Task 2.1: Validate-before-insert write endpoint pattern

**Decision (was open question 4): validate BEFORE the write ever reaches
Postgres, not after.** No edit is allowed to land in the database unless a
full `load_pack()` of the pack-as-it-would-be succeeds first. Postgres never
holds a broken pack, even transiently. This is a materialize-mutate-validate-
then-commit sequence, entirely in a container-local temp directory, with
Postgres only touched on the final, already-proven-valid step.

**Files:** Modify `services/3layer-generator/generator_api.py`.

```python
def _validate_candidate_pack(pack_name: str, mutate) -> None:
    """The shared pre-commit gate every pack write endpoint calls through.

    1. Materialize pack_name's CURRENT state (Postgres rows, as they are
       right now -- before this edit) into a container-local temp dir.
    2. Apply `mutate(root)` -- a small callback that makes exactly the one
       filesystem change this edit represents (write/overwrite one scene
       file, delete one cast file, etc.) against that temp copy.
    3. Run the real load_pack() against the MUTATED temp copy.
    4. On success: return normally, caller proceeds to write to Postgres.
    5. On failure: raise HTTPException(422) with load_pack's own error
       message and NEVER let the caller reach the Postgres write. Postgres
       is left byte-for-byte as it was before this request -- no rollback
       needed because nothing was ever written.

    Always cleans up the temp dir, success or failure.
    """
    import shutil
    from campaign.pack import load_pack, PackError
    try:
        root = store.materialize_pack(pack_name)
    except FileNotFoundError:
        # First scene/cast/lore row ever added to a brand-new pack -- there
        # is no existing pack to materialize yet. Callers creating a pack's
        # very first row (upsert_campaign) skip this gate entirely; every
        # other write requires campaign.yaml to exist first (enforced by
        # the pack_scenes/pack_cast/pack_lore FOREIGN KEY on pack_name).
        raise HTTPException(404, f"pack {pack_name!r} not found")
    try:
        mutate(root)
        load_pack(root)
    except PackError as exc:
        raise HTTPException(422, f"this edit would make pack {pack_name!r} "
                                  f"invalid, nothing was saved: {exc}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


@app.put("/packs/{pack_name}/scenes/{scene_id}")
def put_pack_scene(pack_name: str, scene_id: str, body: dict = Body(...)):
    scene_yaml = body["scene_yaml"]
    try:
        import yaml
        parsed = yaml.safe_load(scene_yaml)
    except yaml.YAMLError as exc:
        raise HTTPException(400, f"not valid YAML: {exc}")
    if not isinstance(parsed, dict) or parsed.get("id") != scene_id:
        raise HTTPException(400, f"scene_yaml's id must equal {scene_id!r}")

    def mutate(root):
        (root / "scenes" / f"{scene_id}.yaml").write_text(scene_yaml, encoding="utf-8")

    _validate_candidate_pack(pack_name, mutate)   # 422 before ANY DB write
    store.upsert_scene(pack_name, scene_id, scene_yaml)   # only reached if valid
    return {"status": "saved", "scene_id": scene_id}


@app.delete("/packs/{pack_name}/scenes/{scene_id}")
def delete_pack_scene(pack_name: str, scene_id: str):
    def mutate(root):
        path = root / "scenes" / f"{scene_id}.yaml"
        if not path.exists():
            raise HTTPException(404, f"scene {scene_id!r} not found")
        path.unlink()

    _validate_candidate_pack(pack_name, mutate)   # e.g. rejects deleting start_scene
    store.delete_scene(pack_name, scene_id)       # only reached if the pack still loads
    return {"status": "deleted", "scene_id": scene_id}
```

**Practical effect, now matching decision 4 exactly:** an edit that would
break the pack is rejected with a 422 and the operator's proposed text is
returned unsaved (the GUI in Task 2.2 keeps the textarea populated with what
they typed, so nothing is lost from their editing session, but nothing bad
reaches the database either). Postgres only ever contains pack states that
have passed a real `load_pack()` — the same guarantee the original
file-based draft had, achieved without ever writing bad data anywhere
first.

**Tests:**
```python
def test_put_pack_scene_saves_valid_yaml(client, store):
    store.upsert_campaign("alpha", CAMPAIGN_YAML_FIXTURE)
    store.upsert_cast_member("alpha", "gm", CAST_YAML_FIXTURE)
    store.upsert_scene("alpha", "intro", "id: intro\ntitle: Old\ndefault_next: intro\n")
    resp = client.put("/packs/alpha/scenes/intro",
                      json={"scene_yaml": "id: intro\ntitle: New\ndefault_next: intro\n"})
    assert resp.status_code == 200
    assert "New" in store.list_scenes("alpha")[0]["scene_yaml"]


def test_put_pack_scene_rejects_mismatched_id(client, store):
    store.upsert_campaign("alpha", CAMPAIGN_YAML_FIXTURE)
    resp = client.put("/packs/alpha/scenes/intro",
                      json={"scene_yaml": "id: WRONG\ntitle: New\n"})
    assert resp.status_code == 400


def test_an_edit_that_would_break_the_pack_never_reaches_postgres(client, store):
    """The core guarantee from decision 4: Postgres never holds a state
    load_pack() rejects, even transiently."""
    store.upsert_campaign("alpha", CAMPAIGN_YAML_FIXTURE)  # start_scene: intro
    store.upsert_cast_member("alpha", "gm", CAST_YAML_FIXTURE)
    store.upsert_scene("alpha", "intro", "id: intro\ntitle: Intro\ndefault_next: intro\n")

    resp = client.delete("/packs/alpha/scenes/intro")   # intro is start_scene
    assert resp.status_code == 422
    # The delete did NOT happen -- Postgres still has the scene, unlike the
    # commit-then-flag design this replaced.
    assert len(store.list_scenes("alpha")) == 1


def test_put_pack_scene_on_a_pack_with_no_campaign_row_is_404(client, store):
    resp = client.put("/packs/nope/scenes/intro", json={"scene_yaml": "id: intro\n"})
    assert resp.status_code == 404
```

**Commit:**
```bash
cd services/3layer-generator && python -m pytest tests/ -v
git add generator_api.py tests/
git commit -m "feat(3layer-generator): validate-before-insert pack write endpoints"
```

### Task 2.2: campaign-manager edit forms

Mirror Task 1.4's pure-HTTP-client pattern: a form POSTs (HTMX or a plain
form + redirect) to a campaign-manager route, which calls
`http_client.put(...)`, and renders either a success redirect or the 422
message inline. Repeat for cast members and lore notes — same shape three
times, not spelled out three times here; implement by direct analogy to the
scene editor once it's proven.

**Files:**
- Modify `services/campaign-manager/main.py`
- Modify `services/campaign-manager/templates/pack_scene_detail.html` (add
  a raw-YAML textarea form, pre-filled from `scene_yaml`)

```python
@app.post("/packs/{pack_name}/scenes/{scene_id}", response_class=HTMLResponse)
async def save_pack_scene(request: Request, pack_name: str, scene_id: str,
                          scene_yaml: str = Form(...)):
    resp = await http_client.put(f"/packs/{pack_name}/scenes/{scene_id}",
                                  json={"scene_yaml": scene_yaml})
    error = None
    if resp.status_code >= 400:
        detail = resp.json().get("detail") if resp.headers.get("content-type", "").startswith("application/json") else None
        error = detail or f"save failed: HTTP {resp.status_code}"
    return templates.TemplateResponse(request, "pack_scene_detail.html", {
        "request": request, "pack_name": pack_name, "scene_id": scene_id,
        "scene_yaml": scene_yaml, "scene": None, "save_error": error,
        "save_ok": error is None,
    })
```

Add to `pack_scene_detail.html`:
```html
{% if save_error %}<div class="alert alert-danger">{{ save_error }}</div>{% endif %}
{% if save_ok %}<div class="alert alert-success">Saved.</div>{% endif %}
<form method="POST" action="/packs/{{ pack_name }}/scenes/{{ scene_id }}">
    <textarea name="scene_yaml" class="form-control mb-2" rows="20" style="font-family: monospace;">{{ scene_yaml }}</textarea>
    <button type="submit" class="btn btn-primary">Save Scene</button>
</form>
```

**Note on the editor UX choice:** the original draft used per-field
structured forms (a textarea just for `enter_narration`); this revision
uses one raw-YAML textarea per scene/cast-member/lore-note instead, because
the validated-write contract (Task 2.1) already produces a good error
message for a bad edit, and a single textarea is a much smaller amount of
new template code than per-field forms for every one of a scene's several
optional fields (`beats`, `lore`, `default_next`, variant-pool `text:`
lists). If per-field structured editing is wanted instead, that is a
template-only change on top of the same PUT endpoint — the backend
contract doesn't change either way. Flagged as an open question below
rather than assumed.

**Commit:**
```bash
git add services/campaign-manager/main.py services/campaign-manager/templates/pack_scene_detail.html
git commit -m "feat(campaign-manager): scene editor via generator API PUT"
```

---

## Phase 3 — Publish to Episode Store (the generator → stream bridge)

**Objective:** Turn `.claude/prompts/build_generated_episode.py` and
`build_campaign_episode.py` from manual scripts into tracked jobs reachable
from campaign-manager, so "generate content" and "get it on stream" are two
clicks in one tool instead of a script run from a terminal that leaves no
record.

**Note:** `build_campaign_episode.py` (the authored-pack converter) reads a
pack via `load_pack(REPO / args.pack)` today — a host path. Once Phase 1/2
land, this converter should also switch to `store.materialize_pack(pack_name)`
so both converters agree on where pack content lives; call this out
explicitly as part of Task 3.1 rather than leaving one converter reading
Postgres and the other reading host files that Phase 1's import script will
make increasingly stale.

### Task 3.1: Move the converters into the generator service as library code

**Files:**
- Create: `services/3layer-generator/episode_builder.py` (ports
  `build_generated_episode.py`'s `build_episode()` function, unchanged logic)
- Create: `services/3layer-generator/campaign_episode_builder.py` (ports
  `build_campaign_episode.py`'s `build_episode()`, switched to
  `store.materialize_pack()` per the note above)
- Test: `services/3layer-generator/tests/test_episode_builder.py`

Why the generator service and not campaign-manager: the converters need
`generation_store`/artifact access (`services/3layer-generator/generation_store.py`
already has connection pooling and query helpers this needs), and campaign-
manager should stay a thin HTTP client of the generator, matching its
existing "does no parsing/validation of its own" design.

**Step 1: Port the pure logic, with speaker mapping generalized (decision 3:
no hardcoded values — this becomes real config, not a code constant).**
Copy `build_episode()`, `_clean_beat_text()`, `_leaf_dialogue_events()`, the
regexes, verbatim from `.claude/prompts/build_generated_episode.py` into
`episode_builder.py`, with two signature changes:
  - take `store` (injectable for tests) as a parameter instead of calling
    the generator API over HTTP internally — this is an in-process call
    now, not a script hitting `localhost:8001`.
  - **replace the hardcoded `SPEAKER_TO_WORKER` dict with a per-pack lookup
    read from Postgres.** Add a `worker_id` column to `pack_cast` (Task 1.1
    follow-up: `ALTER TABLE pack_cast ADD COLUMN worker_id TEXT;`, nullable
    — a cast member with no `worker_id` set stays GM narration, matching
    today's "unmapped name falls through to narration" fallback exactly, so
    existing packs keep working with zero data migration required beyond
    the column itself). `episode_builder.build_episode()` builds its
    speaker map by reading `store.list_cast(pack_name)` once at the top and
    keying off each row's `worker_id` instead of a module-level constant:

```python
def _speaker_map_for_pack(pack_name: str, store) -> dict[str, str]:
    """cast_id -> worker_id, sourced from Postgres (pack_cast.worker_id)
    instead of the old hardcoded SPEAKER_TO_WORKER dict. A cast member with
    no worker_id set (None) is simply absent from the returned map, which
    build_episode's existing fallback already treats as 'keep as GM
    narration' -- identical behavior to today's unmapped-name case, so
    packs imported before this column existed need no backfill to keep
    working; they just don't get name resolution until an operator sets
    worker_id via the Pack Editor's cast form (Task 2.2)."""
    return {
        row["member_id"]: row["worker_id"]
        for row in store.list_cast(pack_name)
        if row.get("worker_id")
    }
```

  Also expose `worker_id` through the cast read/write endpoints from Task
  1.3/2.2 (`GET/PUT /packs/{pack}/cast/{member_id}`) so it's editable from
  the Pack Editor alongside a cast member's other fields — this is exactly
  the mechanism decision 3 asks for: an operator changes a mapping through
  the GUI/DB, not a code deploy.

**Step 2: Add a new job stage, `"publish"`, to `generator_api.py`.**

```python
VALID_STAGES = {"arc", "segment", "dialogue", "all", "publish"}
```

`submit_job`'s existing `run` handling already requires `run` for any
non-arc stage — `publish` naturally fits that same branch (it operates on
an existing run's artifacts, same as segment/dialogue).

**Step 3: `runner.py` dispatches `"publish"` to `episode_builder.build_episode`**,
writing the resulting episode JSON to
`{OUTPUT_ROOT}/{run}/episode.json` and returning
`{"event_count": N, "episode_name": ..., "written_to": path}` as the job
result — matching the existing per-stage result-shape convention.

**Step 4: A new endpoint uploads the built episode into the episode store**,
`POST /publish/{run}/upload`, proxying to message-api's `POST /replays`
(needs an httpx client added to `generator_api.py` pointed at
`MESSAGE_API_URL`, mirroring the pattern `campaign-manager`'s `http_client`
already uses for the generator API).

*(Full step-by-step code for Tasks 3.1–3.4 intentionally deferred to a
follow-up detailed pass after Phase 1/2 scope is confirmed in review — this
phase touches three services' contracts at once and deserves its own
focused planning pass rather than being rushed inside this document.)*

### Task 3.2: campaign-manager UI for Publish

- A "Publish" button on `/job/{job_id}` for any completed `dialogue`-stage
  job, submitting a `publish` job for that job's `run`.
- A result banner showing event count + a direct link to
  `control-panel`'s `/replays/{name}/view` (cross-link into the other
  existing tool, per the scope decision above — not a duplicate viewer).

---

## Phase 4 — Cross-links between campaign-manager and control-panel

**Objective:** An operator on either tool can reach the other in one click,
closing the "which of three URLs do I need" confusion without merging
codebases.

### Task 4.1: Add nav links each way

- `campaign-manager/templates/dashboard.html` navbar: add
  `<a href="{{ control_panel_url }}" class="btn btn-outline-light btn-sm">Control Panel</a>`,
  `CONTROL_PANEL_URL` env var (default `http://localhost:8091`, matching
  the docker-compose port mapping already in place).
- `control-panel/templates/base.html` navbar: add the mirrored link back,
  `CAMPAIGN_MANAGER_URL` env var (default `http://localhost:8082`).

Trivial, high-value, no backend logic, no new mounts or credentials either
side — do this task regardless of how much of Phase 2/3 ships.

---

## Phase 5 (explicitly deferred, not planned in detail here)

- Merge campaign-manager + control-panel into one service/nav, once both
  Phase 0–4 are stable and the operator has lived with the two-tool split
  long enough to say whether it's still annoying.
- A rich scene/beat editor (structured per-field forms, drag-reorder beats,
  live default_next graph view) beyond Phase 2's raw-YAML textarea —
  decision 2 confirmed raw-YAML is acceptable for now.
- Automated Twitch-live verification surfaced in a GUI panel instead of the
  current skill-driven manual curl check.
- Dropping the `campaigns/:ro` host mount entirely once every pack has been
  imported and campaign-manager/generator are confirmed running purely off
  Postgres for a full production cycle (this is now the plan's actual
  target state per decision 5 — see Task 1.5 below — so this bullet is
  really "remove the now-vestigial read path once Task 1.5 has been live
  long enough to trust fully," not a hypothetical).

---

## Decisions from review (resolved — superseded the original "Risks and open questions" list)

1. **Auth: yes, build it.** → Phase 0 above. HTTP Basic Auth on
   campaign-manager, same pattern as control-panel, unset (off) by default,
   enabled via `.env`.
2. **Scene editor scope: raw-YAML textarea is acceptable for now.** → Task
   2.2 stays as originally drafted (one textarea per scene/cast/lore
   document). Structured per-field forms remain a Phase 5 candidate, not
   built now.
3. **No hardcoded values — these should be configs.** → Applied to
   `SPEAKER_TO_WORKER` specifically (Task 3.1: became a `pack_cast.worker_id`
   Postgres column, editable via the Pack Editor, instead of a Python dict
   literal). Any other hardcoded list touched while implementing this plan
   (e.g. if a similar per-pack constant turns up during Phase 3's fuller
   implementation pass) should get the same treatment — read from Postgres
   or an env var, not a code constant — as a standing rule for this plan's
   scope, not just this one instance.
4. **Postgres is the target; validate before insert to control bad-data
   risk.** → Task 2.1 rewritten: every write is validated (full `load_pack()`
   against a mutated container-local temp copy) *before* anything reaches
   Postgres. A bad edit is rejected with a 422 and never written — Postgres
   never holds a state `load_pack()` would reject, not even transiently.
   This replaces the earlier commit-then-flag design entirely.
5. **Postgres is the source of truth, end to end.** → Promoted from an open
   question to an explicit task, below.

### Task 1.5: Cut the live job-dispatch pipeline over to Postgres

**Objective:** `runner.py`'s actual arc/segment/dialogue job execution
currently loads packs via the `campaigns/:ro` host mount
(`generator_api.PACK_ROOT`), which is a *different* read path than the new
viewer/editor endpoints (Task 1.3/1.4/2.x) that read from Postgres via
`store.materialize_pack()`. Per decision 5, these must converge: Postgres is
the only pack source of truth, for generation jobs and for the editor alike.
Deliberately sequenced as its own task, after Phase 1 is otherwise verified
working, specifically so a regression in the live generation pipeline
during this cutover is never confused with "the pack viewer stuff broke
something."

**Files:**
- Modify: `services/3layer-generator/runner.py` (wherever it currently
  resolves a pack path — likely a call shaped like
  `load_pack(PACK_ROOT / pack_name)` inside the job-dispatch loop)
- Test: whatever `test_service_runner.py`'s existing pack-loading tests are
  — extend them to assert the Postgres path is used, not the host mount

**Step 1: Find the exact current call site.**

```bash
grep -n "PACK_ROOT\|load_pack" services/3layer-generator/runner.py
```

**Step 2: Replace the direct `load_pack(PACK_ROOT / pack_name)` call** with:

```python
import shutil
from generation_store import materialize_pack

def _load_pack_for_job(pack_name: str):
    """The ONE place a generation job resolves pack content, now backed by
    Postgres (decision 5) instead of the campaigns/:ro host mount. Returns
    the loaded pack; caller is responsible for cleaning up the temp dir
    this creates (materialize_pack's own contract) once the job that needed
    it is done -- typically the whole dispatch-one-job function body, not
    held open across jobs."""
    root = materialize_pack(pack_name)
    try:
        from campaign.pack import load_pack
        return load_pack(root), root
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
```

Every call site that currently does something like
`pack = load_pack(PACK_ROOT / job["pack"])` becomes
`pack, pack_temp_root = _load_pack_for_job(job["pack"])`, with a
`shutil.rmtree(pack_temp_root, ignore_errors=True)` added wherever that job's
processing completes (success or failure) — mirror whatever cleanup/`finally`
structure the existing per-job dispatch loop already uses for its other
resources.

**Step 3: Tests** — extend the runner test suite with a case that proves
the cutover, not just that nothing broke:

```python
def test_a_job_loads_its_pack_from_postgres_not_the_host_mount(tmp_path, monkeypatch):
    """The actual cutover assertion: if PACK_ROOT is pointed at an empty/
    nonexistent directory but the pack exists in the (fake) store, the job
    must still succeed -- proving it never touched the host mount."""
    monkeypatch.setattr(runner, "PACK_ROOT", tmp_path / "definitely-empty")
    store = FakeStore()
    store.upsert_campaign("alpha", CAMPAIGN_YAML_FIXTURE)
    store.upsert_cast_member("alpha", "gm", CAST_YAML_FIXTURE)
    store.upsert_scene("alpha", "intro", SCENE_YAML_FIXTURE)
    # ... dispatch one arc job for pack "alpha" against this store ...
    # assert it completes rather than raising "cast directory not found"
```

**Step 4: Verify against a real run**, not just unit tests — this is the
pipeline that burns real GPU-hours, so a live smoke test matters more here
than anywhere else in this plan:

```bash
docker compose build 3layer-generator && docker compose up -d 3layer-generator
docker compose exec 3layer-generator python scripts/import_packs_to_postgres.py ashiorid_1
# Optional but recommended for this one verification: temporarily rename
# the host campaigns/ directory (or point PACK_ROOT at an empty dir via
# .env) to PROVE the job succeeds with zero host filesystem access, not
# just that it still happens to work while the old path is also present.
curl -s -X POST http://localhost:8001/jobs -H "Content-Type: application/json" -d '{
  "pack": "ashiorid_1", "stage": "arc"
}' | python3 -m json.tool
# poll to completion, confirm status: completed, segments > 0
```

**Step 5: Commit**

```bash
cd services/3layer-generator && python -m pytest tests/ -v
git add runner.py tests/
git commit -m "feat(3layer-generator): job dispatch reads packs from Postgres (decision 5 cutover)"
```

**After Task 1.5 lands:** the `campaigns/:ro` host mount on both
`3layer-generator` and (never-added, per this plan's revision) campaign-
manager becomes read-only backup/import-source only — nothing in the live
path depends on it. Safe to eventually remove per the Phase 5 bullet above,
once you've run a full production cycle purely off Postgres and are
comfortable dropping the fallback.

---

## Verification checklist (after each phase)

```bash
# Backend
cd services/3layer-generator && python -m pytest tests/ -v
cd services/campaign-manager && python -m pytest tests/ -v

# Live smoke test
docker compose build 3layer-generator campaign-manager
docker compose up -d 3layer-generator campaign-manager
docker compose exec 3layer-generator python scripts/import_packs_to_postgres.py ashiorid_1
curl -s http://localhost:8001/healthz
curl -s http://localhost:8082/healthz   # never requires auth, even with Phase 0 enabled

# If CAMPAIGN_MANAGER_BASIC_AUTH_USER/PASS are set in .env (Phase 0):
curl -s -u "$CAMPAIGN_MANAGER_BASIC_AUTH_USER:$CAMPAIGN_MANAGER_BASIC_AUTH_PASS" \
  http://localhost:8082/packs/ashiorid_1 -o /tmp/pv.html -w "%{http_code}\n"
curl -s http://localhost:8082/packs/ashiorid_1 -o /dev/null -w "no-auth: %{http_code}\n"   # expect 401

# No regressions in the job-submission flow this session already verified
curl -s -X POST http://localhost:8082/jobs/submit -o /dev/null -w "%{http_code}\n" \
  --data-urlencode "pack=ashiorid_1" --data-urlencode "stage=arc" \
  --data-urlencode "profile=" --data-urlencode "run=" --data-urlencode "segments_json=[]"

# After Task 1.5 specifically: prove the job pipeline no longer needs the
# host mount at all (decision 5's end-to-end cutover)
sudo mv campaigns campaigns.bak   # or: point PACK_ROOT at an empty dir via .env
docker compose restart 3layer-generator
curl -s -X POST http://localhost:8001/jobs -H "Content-Type: application/json" \
  -d '{"pack": "ashiorid_1", "stage": "arc"}' | python3 -m json.tool
# expect this to succeed -- Postgres is now the only pack source
sudo mv campaigns.bak campaigns   # restore
```
