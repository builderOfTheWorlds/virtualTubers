#!/usr/bin/env python3
"""
panel.py
Browser control panel for services/message-api. Server-rendered FastAPI +
Jinja2 + HTMX (no build step, no Node) — every route here is a thin wrapper
around one message-api HTTP call, so this service never touches Redis,
Kafka, or Postgres directly. That mirrors the trust boundary message-api's
own module docstring already describes: this is just a friendlier client of
the same HTTP surface docs/message_api.md documents for curl.

MESSAGE_API_URL (default http://message-api:8000) points this at message-api,
the same env-var pattern services/twitch-presence uses for its own calls to
POST /messages.
"""
import base64
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

log = logging.getLogger("control_panel")
logging.basicConfig(level=logging.INFO, format="[control-panel] %(levelname)s %(message)s")

BASE_DIR = Path(__file__).resolve().parent
MESSAGE_API_URL = os.environ.get("MESSAGE_API_URL", "http://message-api:8000")
# The campaign manager (job submission / pack editor / data viewer) — the
# dashboard cross-links INTO it so an operator reaches either tool in one
# click without having to remember three URLs (plan Phase 4). Port 8082
# matches the docker-compose mapping already in place.
CAMPAIGN_MANAGER_URL = os.environ.get("CAMPAIGN_MANAGER_URL", "http://localhost:8082")


def _campaign_manager_public_url(request: Request) -> str:
    """The campaign-manager link an operator's browser can actually
    navigate to: an explicitly-set CAMPAIGN_MANAGER_URL env var wins,
    otherwise derive scheme://host from the incoming request and point it
    at the campaign manager's own public port (8082 in docker-compose) —
    the host/IP the operator reached THIS app on, with the port swapped
    from ours (8091) to the manager's. Keeps the cross-link correct from
    any machine (LAN IP, tunnel) without per-host config; a non-standard
    port mapping or a different host is what the env override is for."""
    override = os.environ.get("CAMPAIGN_MANAGER_URL", "").strip()
    if override:
        return override
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.hostname or "localhost"
    if ":" in host and not host.startswith("["):  # IPv6 literal
        host = f"[{host}]"
    return f"{scheme}://{host}:8082"

# Same hardcoded lists api.py's own WORKER_ID_EXAMPLES/MESSAGE_TYPE_EXAMPLES
# use, for the same reason: message-api exposes no "list workers" or "list
# message types" endpoint, and these can drift from docker-compose.yml.
WORKER_IDS = ["coder", "coder-native", "coder-opencode", "coder-aider", "manager", "tester"]
MESSAGE_TYPE_EXAMPLES = ["operator_message", "status_update", "coding_run_report"]

# The roundtable SHOW container (docs/duet_replay.md,
# config/layouts/roundtable.yaml, config/workers/roundtable.yaml). It is a
# SEPARATE audience from the six character workers above: a plain replay_request
# (no payload.cast) addressed to it plays solo on its own pane and never touches
# its tile grid — only app/replay_pane.py's perform_director_request does that,
# and it only runs when payload.cast is present (docs/duet_replay.md, "Debugging:
# only the director performs, nobody else joins" — the tile-grid analog of that
# same gotcha). scripts/send_test_message.sh documents the fix as a "roundtable
# companion request": address the director directly with an explicit cast.
# WORKER_TO_TUBER_SLOT mirrors config/workers/roundtable.yaml's `roster:` map
# (reversed) so the Play button can build that cast automatically instead of an
# operator having to hand-type it every time.
#
# This must match the worker-roundtable service's WORKER_ID in docker-compose.yml
# and config/workers/roundtable.yaml's message_bus.worker_id. It used to be
# "tuber_0", when the GM character and the show shared one container; the GM now
# has its own tuber_base channel (worker-gm / config/workers/tuber_0.yaml) which
# is NOT a director, so a Play still addressed to "tuber_0" would air nothing at
# all — and silently, since nothing polls the request file on a non-director.
ROUNDTABLE_WORKER_ID = "roundtable"

# Values here are tuber SLOT ids (tuber_0..tuber_5), not container worker ids —
# they name a TILE on the roundtable grid, and the slots did not change when the
# director moved to its own container. So `manager` maps to the literal slot
# "tuber_0" rather than to ROUNDTABLE_WORKER_ID: those two were the same string
# before the split and are deliberately different now.
#
# manager -> tuber_0, NOT tuber_6, is deliberate and easy to get backwards:
# every episode-building script in this repo overloads the "manager" WORKER
# id to carry the GM/narrator's lines, not the MAX-1 character —
# .claude/prompts/build_campaign_episode.py's SPEAKER_TO_WORKER: {"gm":
# "manager"} and build_generated_episode.py's {"ashiorid": "manager"}
# ("Ashiorid -> manager (GM)"). roundtable.yaml's own roster comment confirms
# the other side of this: "tuber_6: MAX-1 — active worker, no campaign
# character cast" — nothing currently routes to MAX-1's tile for this kind
# of content. Mapping manager -> tuber_6 here (the naive "6 workers, 6
# non-GM slots" reading) sent every GM line to the wrong tile, so tuber_0
# never owned a scene and rendered silent (audio still played correctly —
# roundtable.yaml's voice.speakers.manager is deliberately the GM slot's own
# voice — but perform_director_request's ownership gate means only the tile
# the CAST maps a speaker to gets the bubble/status update). Reported live:
# "I can hear the voice but the Game Master shows no text."
WORKER_TO_TUBER_SLOT = {
    "coder": "tuber_1",
    "coder-native": "tuber_2",
    "coder-opencode": "tuber_3",
    "coder-aider": "tuber_4",
    "tester": "tuber_5",
    "manager": "tuber_0",
}

# docker-compose SERVICE names (not worker/tuber ids) for the same 7 Play
# targets — what services/log-shipper's container_logs rows are actually
# keyed by (docs/replay_logs.md, docs/log_shipper.md). Needed because the
# log viewer reads container stdout/stderr, which knows nothing about
# worker ids or tuber slots.
WORKER_TO_SERVICE = {
    "coder": "worker-coder",
    "coder-native": "worker-coder-native",
    "coder-opencode": "worker-coder-opencode",
    "coder-aider": "worker-coder-aider",
    "manager": "worker-manager",
    "tester": "worker-tester",
}
ROUNDTABLE_SERVICE = "worker-roundtable"

# Some episodes (e.g. roundtable-stream-check, built as a "does every seat
# wire up" sanity check) name their speakers with the literal slot id —
# "tuber_0".."tuber_7" — instead of a worker id like "coder"/"manager".
# perform_director_request's ownership gate only matches a speaker that's a
# KEY in the cast dict (app/replay_pane.py:673-711): WORKER_TO_TUBER_SLOT
# alone has no "tuber_0".."tuber_7" keys, so every line in such an episode
# falls through to "uncast" and the director voices/owns all of it itself —
# audio plays (it's still routed through the director's own pane) but no
# character tile ever gets ownership, so nobody's mouth animates and no
# tile shows any speech text. Reported live: "I don't see any of the
# avatars doing the speaking animation, and I don't see any of their
# speech displayed" on roundtable-stream-check specifically. The fix is an
# identity entry per slot, merged into the cast alongside the worker-id
# mapping above so BOTH speaker-naming conventions resolve to a tile.
#
# range(8), NOT range(7): the roster has 8 slots, tuber_0..tuber_7
# (config/workers/roundtable.yaml roster: — the 8th, tuber_7 "Iris", was
# "promoted from spare" per config/layouts/roundtable.yaml's tile_tuber_7
# comment). The first version of this fix used range(7) and missed
# tuber_7 — reported live as "the very last one iris didn't have a line"
# on roundtable-stream-check, which does cast a tuber_7 line.
TUBER_SLOT_IDENTITY_CAST = {f"tuber_{i}": f"tuber_{i}" for i in range(8)}

# ── Console theme control (app/console_theme.py, message-api's
#    /console-theme(s) endpoints) ────────────────────────────────────────
# Every worker container runs app/theme_watcher.py unconditionally
# (startup.sh §7.6), including tuber_0 (the GM's own channel) and
# roundtable — unlike WORKER_IDS above, which deliberately excludes both
# for its own purposes (operator_message/replay_request addressing). All 8
# are valid live-retheme targets.
THEME_WORKER_IDS = WORKER_IDS + ["tuber_0", ROUNDTABLE_WORKER_ID]

# In-memory cache of the theme name list — it's static per deploy
# (config/themes/gogh_themes.json ships baked into the message-api image),
# so there's no reason to re-fetch all 1247 names on every page load.
_THEME_NAMES_CACHE: Optional[list] = None

# In-memory only (module-level, resets on restart) — message-api has no
# "list filtered types" endpoint either, so the panel just tracks whichever
# types an operator has looked at/added this process's lifetime, seeded with
# the one type log_filter_control.py excludes by default.
KNOWN_LOG_TYPES = ["status_update"]

# ── Optional HTTP Basic Auth — no-ops unless both vars are set ─────────────
BASIC_AUTH_USER = os.environ.get("CONTROL_PANEL_BASIC_AUTH_USER", "")
BASIC_AUTH_PASS = os.environ.get("CONTROL_PANEL_BASIC_AUTH_PASS", "")


def _auth_enabled() -> bool:
    return bool(BASIC_AUTH_USER and BASIC_AUTH_PASS)


http_client = httpx.AsyncClient(base_url=MESSAGE_API_URL, timeout=10.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup message_api_url=%s auth_enabled=%s", MESSAGE_API_URL, _auth_enabled())
    yield
    await http_client.aclose()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["age"] = lambda seconds: format_age(seconds)


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
        headers={"WWW-Authenticate": 'Basic realm="control-panel"'},
    )


class MapiResult:
    """Normalized outcome of one message-api call — every route branches on
    `.ok` rather than juggling httpx exceptions and HTTP status codes itself."""

    def __init__(self, ok: bool, status_code: int, data: Any = None, error: Optional[str] = None):
        self.ok = ok
        self.status_code = status_code
        self.data = data
        self.error = error


async def _mapi_request(method: str, path: str, **kwargs) -> MapiResult:
    try:
        resp = await http_client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        log.error("message-api unreachable method=%s path=%s error=%s", method, path, exc)
        return MapiResult(ok=False, status_code=0, error=f"message-api unreachable: {exc}")

    data = None
    if resp.content:
        try:
            data = resp.json()
        except ValueError:
            data = None

    if resp.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else None
        error = detail or f"message-api returned HTTP {resp.status_code}"
        log.warning("message-api error method=%s path=%s status=%s detail=%s", method, path, resp.status_code, error)
        return MapiResult(ok=False, status_code=resp.status_code, data=data, error=error)

    return MapiResult(ok=True, status_code=resp.status_code, data=data)


async def _worker_status(worker_id: str) -> dict:
    result = await _mapi_request("GET", f"/workers/{worker_id}")
    if result.ok:
        return {"id": worker_id, "enabled": result.data.get("enabled"), "error": None}
    return {"id": worker_id, "enabled": None, "error": result.error}


def _health_or_none(data: Any) -> Optional[dict]:
    return data if isinstance(data, dict) and data.get("worker_id") else None


async def _workers_health() -> dict:
    """{worker_id: health row} from ONE GET /workers/health call. {} when
    message-api is unreachable/old — every row then renders "unknown"."""
    result = await _mapi_request("GET", "/workers/health")
    if not result.ok or not isinstance(result.data, dict):
        return {}
    rows = [_health_or_none(r) for r in result.data.get("workers") or []]
    return {r["worker_id"]: r for r in rows if r}


async def _worker_health(worker_id: str) -> Optional[dict]:
    result = await _mapi_request("GET", f"/workers/{worker_id}/health")
    return _health_or_none(result.data) if result.ok else None


async def _workers_view() -> list:
    """The dashboard's worker rows: on/off status + health (alive/stale/down,
    last seen, local kill switch) merged per worker id."""
    health = await _workers_health()
    workers = [await _worker_status(w) for w in WORKER_IDS]
    for worker in workers:
        worker["health"] = health.get(worker["id"])
    return workers


def format_age(seconds: Any) -> str:
    """12.3 -> "12s", 130 -> "2m 10s", 7500 -> "2h 5m"; "?" if not a number.
    Jinja filter `age` for the workers table's "last seen ... ago"."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return "?"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"


async def _log_filter_status(message_type: str) -> dict:
    result = await _mapi_request("GET", f"/log-filter/{message_type}")
    if result.ok:
        return {"type": message_type, "excluded": result.data.get("excluded"), "error": None}
    return {"type": message_type, "excluded": None, "error": result.error}


async def _theme_names() -> list:
    global _THEME_NAMES_CACHE
    if _THEME_NAMES_CACHE is not None:
        return _THEME_NAMES_CACHE
    result = await _mapi_request("GET", "/console-themes")
    if result.ok:
        _THEME_NAMES_CACHE = result.data.get("themes", [])
    return _THEME_NAMES_CACHE or []


async def _worker_theme_status(worker_id: str) -> dict:
    result = await _mapi_request("GET", f"/console-theme/{worker_id}")
    if result.ok:
        return {
            "id": worker_id,
            "theme": result.data.get("theme"),
            "overridden": result.data.get("overridden"),
            "error": None,
        }
    return {"id": worker_id, "theme": None, "overridden": False, "error": result.error}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    workers = await _workers_view()
    log_types = [await _log_filter_status(t) for t in KNOWN_LOG_TYPES]
    episode_lists = await _episode_lists()
    themes = await _theme_names()
    theme_workers = [await _worker_theme_status(w) for w in THEME_WORKER_IDS]
    return templates.TemplateResponse(request, "base.html", {
        "message_api_url": MESSAGE_API_URL,
        "campaign_manager_url": _campaign_manager_public_url(request),
        "workers": workers,
        "message_type_examples": MESSAGE_TYPE_EXAMPLES,
        "worker_ids": WORKER_IDS,
        "log_types": log_types,
        **episode_lists,
        "message_result": None,
        "prune_result": None,
        "themes": themes,
        "themes_error": None if themes else "message-api unreachable or returned no themes",
        "theme_workers": theme_workers,
    })


# ── Workers ──────────────────────────────────────────────────────────────
@app.get("/partials/workers", response_class=HTMLResponse)
async def partial_workers(request: Request):
    workers = await _workers_view()
    return templates.TemplateResponse(request, "_workers_table.html", {"workers": workers})


async def _set_worker(request: Request, worker_id: str, enabled: bool):
    result = await _mapi_request("POST", f"/workers/{worker_id}/{'enable' if enabled else 'disable'}")
    if result.ok:
        worker = {"id": worker_id, "enabled": result.data.get("enabled"), "error": None}
    else:
        worker = {"id": worker_id, "enabled": None, "error": result.error}
    # Re-read health so the swapped-in row keeps its alive/kill-switch
    # badges (and shows the kill switch right after a futile Enable).
    worker["health"] = await _worker_health(worker_id)
    return templates.TemplateResponse(request, "_worker_row.html", {"worker": worker})


@app.post("/workers/{worker_id}/enable", response_class=HTMLResponse)
async def enable_worker(request: Request, worker_id: str):
    return await _set_worker(request, worker_id, True)


@app.post("/workers/{worker_id}/disable", response_class=HTMLResponse)
async def disable_worker(request: Request, worker_id: str):
    return await _set_worker(request, worker_id, False)


# ── Console theme ────────────────────────────────────────────────────────
@app.get("/partials/theme-workers", response_class=HTMLResponse)
async def partial_theme_workers(request: Request):
    themes = await _theme_names()
    theme_workers = [await _worker_theme_status(w) for w in THEME_WORKER_IDS]
    return templates.TemplateResponse(request, "_theme_workers_table.html", {
        "theme_workers": theme_workers, "themes": themes,
    })


@app.post("/console-theme/{worker_id}", response_class=HTMLResponse)
async def set_console_theme(request: Request, worker_id: str, theme: str = Form(...)):
    result = await _mapi_request("POST", f"/console-theme/{worker_id}", json={"theme": theme})
    themes = await _theme_names()
    if result.ok:
        worker = {"id": worker_id, "theme": result.data.get("theme"), "overridden": True, "error": None}
    else:
        worker = {"id": worker_id, "theme": None, "overridden": False, "error": result.error}
    return templates.TemplateResponse(request, "_theme_worker_row.html", {"worker": worker, "themes": themes})


@app.post("/console-theme/{worker_id}/clear", response_class=HTMLResponse)
async def clear_console_theme(request: Request, worker_id: str):
    result = await _mapi_request("DELETE", f"/console-theme/{worker_id}")
    themes = await _theme_names()
    if result.ok:
        # Re-resolve so the row shows what the worker will actually fall
        # back to (its config file's console.theme, or the built-in
        # default) rather than a bare "cleared" state.
        worker = await _worker_theme_status(worker_id)
    else:
        worker = {"id": worker_id, "theme": None, "overridden": False, "error": result.error}
    return templates.TemplateResponse(request, "_theme_worker_row.html", {"worker": worker, "themes": themes})


# ── Log filter ───────────────────────────────────────────────────────────
@app.post("/log-filter/add", response_class=HTMLResponse)
async def add_log_filter_type(request: Request, message_type: str = Form(...)):
    message_type = message_type.strip()
    if message_type and message_type not in KNOWN_LOG_TYPES and len(message_type) <= 128:
        KNOWN_LOG_TYPES.append(message_type)
    log_types = [await _log_filter_status(t) for t in KNOWN_LOG_TYPES]
    return templates.TemplateResponse(request, "_log_filter_table.html", {"log_types": log_types})


async def _set_log_filter(request: Request, message_type: str, excluded: bool):
    result = await _mapi_request(
        "POST", f"/log-filter/{message_type}/{'exclude' if excluded else 'include'}")
    if result.ok:
        row = {"type": message_type, "excluded": result.data.get("excluded"), "error": None}
    else:
        row = {"type": message_type, "excluded": None, "error": result.error}
    return templates.TemplateResponse(request, "_log_filter_row.html", {"log_type": row})


@app.post("/log-filter/{message_type}/exclude", response_class=HTMLResponse)
async def exclude_log_type(request: Request, message_type: str):
    return await _set_log_filter(request, message_type, True)


@app.post("/log-filter/{message_type}/include", response_class=HTMLResponse)
async def include_log_type(request: Request, message_type: str):
    return await _set_log_filter(request, message_type, False)


# ── Message composer ────────────────────────────────────────────────────
@app.post("/messages", response_class=HTMLResponse)
async def send_message(
    request: Request,
    to: str = Form(...),
    msg_type: str = Form("operator_message", alias="type"),
    payload: str = Form("{}"),
):
    try:
        payload_obj = json.loads(payload) if payload.strip() else {}
        if not isinstance(payload_obj, dict):
            raise ValueError("payload must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        return templates.TemplateResponse(request, "_message_result.html", {
            "message_result": {"ok": False, "error": f"payload is not a valid JSON object: {exc}"},
        })

    result = await _mapi_request("POST", "/messages", json={"to": to, "type": msg_type, "payload": payload_obj})
    if result.ok:
        message_result = {"ok": True, "sent": result.data}
    else:
        message_result = {"ok": False, "error": result.error}
    return templates.TemplateResponse(request, "_message_result.html", {"message_result": message_result})


# ── Log pruning ──────────────────────────────────────────────────────────
@app.post("/logs/prune", response_class=HTMLResponse)
async def prune_logs(request: Request, after: str = Form(""), before: str = Form("")):
    body = {}
    if after.strip():
        body["after"] = after.strip()
    if before.strip():
        body["before"] = before.strip()
    result = await _mapi_request("POST", "/logs/prune", json=body)
    if result.ok:
        prune_result = {"ok": True, "deleted": result.data.get("deleted")}
    else:
        prune_result = {"ok": False, "error": result.error}
    return templates.TemplateResponse(request, "_prune_result.html", {"prune_result": prune_result})


# ── Rerun Theater replays ───────────────────────────────────────────────
async def _episode_lists() -> dict:
    """The airable library AND the review queue, as template context.

    message-api's plain GET /replays is approved-only (drafts never air, so
    they never show up next to a Play button); drafts are a separate
    ?status=draft listing rendered in their own "awaiting review" table
    with Approve / Delete instead of Play."""
    library = await _mapi_request("GET", "/replays")
    drafts = await _mapi_request("GET", "/replays", params={"status": "draft"})
    return {
        "replays": library.data.get("episodes", []) if library.ok else [],
        "replays_error": None if library.ok else library.error,
        "drafts": drafts.data.get("episodes", []) if drafts.ok else [],
        "drafts_error": None if drafts.ok else drafts.error,
    }


async def _replays_section_context(banner: Optional[dict] = None, play_result: Optional[dict] = None,
                                   approve_result: Optional[dict] = None) -> dict:
    return {
        **await _episode_lists(),
        "upload_result": banner,
        "worker_ids": WORKER_IDS,
        "play_result": play_result,
        "approve_result": approve_result,
    }


@app.get("/partials/replays", response_class=HTMLResponse)
async def partial_replays(request: Request):
    return templates.TemplateResponse(request, "_replays_section.html", await _replays_section_context())


@app.post("/replays/upload", response_class=HTMLResponse)
async def upload_replay(
    request: Request,
    file: UploadFile,
    name: str = Form(""),
    overwrite: bool = Form(False),
):
    body = await file.read()
    params = {"overwrite": "true" if overwrite else "false"}
    if name.strip():
        params["name"] = name.strip()
    result = await _mapi_request(
        "POST", "/replays", params=params, content=body,
        headers={"Content-Type": "application/json"},
    )
    if result.ok:
        banner = {"ok": True, "name": result.data.get("name"), "event_count": result.data.get("event_count")}
    else:
        banner = {"ok": False, "error": result.error}
    return templates.TemplateResponse(
        request, "_replays_section.html", await _replays_section_context(banner))


@app.post("/replays/{name}/play", response_class=HTMLResponse)
async def play_replay(request: Request, name: str):
    """Launch a Rerun Theater airing for an already-uploaded episode on
    EVERY stream at once: the six character channels AND the roundtable's
    tile grid.

    Deliberately NOT one `to: "broadcast"` message. Two reasons:

    1. The roundtable's tile grid only lights up via the duet DIRECTOR path
       (app/replay_pane.py perform_director_request), which only runs when
       payload.cast is present — a bare request plays solo with audio but no
       tile text/status update (reported live: "I can hear the voices on the
       roundtable but don't see any text or the tubers' statuses updating").
       So the roundtable needs a DIFFERENT payload (with cast) than the
       other six.
    2. Addressing "broadcast" would ALSO reach the roundtable's agent with
       the bare payload, racing its own request file against the cast-bearing
       one below (both are unconditional file writes — see
       app/agent.py's _write_replay_request / handle_replay_request,
       which — unlike handle_replay_invite — has no "don't clobber a
       pending request" guard). Addressing each of the 6 character
       workers BY NAME instead of "broadcast" means nothing but this one
       call ever writes to the roundtable's request file, so there is no
       race to reason about.

    Every failure is best-effort and independently reported — one
    unreachable worker must not stop the episode airing on the other six.

    Stops each of the 7 targets before requesting the new episode
    (docs/operator_commands.md `replay_stop`, app/agent.py
    handle_replay_stop): replay_pane.py's poll loop only reads a fresh
    request file once it's idle between episodes, so a bare replay_request
    fired at a worker that's still mid-show just queues silently behind
    whatever's already playing — potentially minutes away, with nothing in
    this UI to say so (reported live: hit Play on 'roundtable-stream-check',
    saw it logged on the Kafka panel, but the 'coder' channel just kept
    airing its previous ashiorid_generated_ce8d episode for another 25+
    minutes). replay_stop cancels a still-queued request outright and
    signals a currently-playing one to abort within a fraction of a second
    (Pacer.should_stop), so Play now always preempts rather than queuing.
    """
    results = []
    for worker_id in WORKER_IDS:
        await _mapi_request(
            "POST", "/messages",
            json={"to": worker_id, "type": "replay_stop", "payload": {}},
        )
        r = await _mapi_request(
            "POST", "/messages",
            json={"to": worker_id, "type": "replay_request", "payload": {"episode": name}},
        )
        results.append((worker_id, r))
    await _mapi_request(
        "POST", "/messages",
        json={"to": ROUNDTABLE_WORKER_ID, "type": "replay_stop", "payload": {}},
    )
    roundtable_result = await _mapi_request(
        "POST", "/messages",
        json={"to": ROUNDTABLE_WORKER_ID, "type": "replay_request",
              "payload": {"episode": name,
                          "cast": {**WORKER_TO_TUBER_SLOT, **TUBER_SLOT_IDENTITY_CAST}}},
    )
    results.append((ROUNDTABLE_WORKER_ID, roundtable_result))

    failed = [worker_id for worker_id, r in results if not r.ok]
    played_at = datetime.now(timezone.utc).isoformat()
    log_url = f"/replays/{quote(name)}/log?since={quote(played_at)}"
    if not failed:
        banner = {"ok": True, "name": name, "log_url": log_url,
                  "to": f"all {len(results)} streams (6 channels + roundtable)"}
    else:
        errors = "; ".join(f"{w}: {r.error}" for w, r in results if not r.ok)
        succeeded = len(results) - len(failed)
        banner = {"ok": False, "name": name, "log_url": log_url,
                  "error": f"{succeeded}/{len(results)} streams queued — failed: {errors}"}
    return templates.TemplateResponse(
        request, "_replays_section.html", await _replays_section_context(play_result=banner))


# ── Replay log viewer (docs/replay_logs.md) ─────────────────────────────
# The 7 Play targets, in the two shapes message-api's two log endpoints
# need: container_logs is keyed by compose SERVICE name, messages by bus
# WORKER id. Order doesn't matter; a dict comprehension would drop
# duplicates, which can't happen here since each maps 1:1, but a plain list
# keeps the intent obvious at the call site.
_LOG_SERVICES = list(WORKER_TO_SERVICE.values()) + [ROUNDTABLE_SERVICE]
_LOG_WORKER_IDS = WORKER_IDS + [ROUNDTABLE_WORKER_ID]


@app.get("/replays/{name}/log", response_class=HTMLResponse)
async def replay_log(request: Request, name: str, since: str = Query("")):
    """Merged, chronological tail of container stdout/stderr AND bus
    messages for every worker a Play click could have targeted — polled by
    the log viewer div every few seconds starting from the Play click's
    timestamp (`since`, ISO-8601). Deliberately fetches the WHOLE window
    from `since` every poll rather than tracking a per-poll cursor: a
    Rerun Theater airing is short and MAX_LOG_LIMIT-bounded on the
    message-api side, so re-fetching is simple and correct rather than
    fast."""
    container_params: List[Any] = [("service", s) for s in _LOG_SERVICES]
    message_params: List[Any] = [("worker_id", w) for w in _LOG_WORKER_IDS]
    if since:
        container_params.append(("since", since))
        message_params.append(("since", since))

    containers_result = await _mapi_request(
        "GET", "/logs/containers", params=container_params + [("limit", "500")])
    messages_result = await _mapi_request(
        "GET", "/logs/messages", params=message_params + [("limit", "500")])

    entries = []
    if containers_result.ok:
        for row in containers_result.data.get("logs", []):
            entries.append({
                "ts": row["log_timestamp"], "source": row["container_name"],
                "kind": "log", "detail": row["stream"], "text": row["message"],
            })
    if messages_result.ok:
        for row in messages_result.data.get("messages", []):
            entries.append({
                "ts": row["timestamp"], "source": f'{row["from"]} → {row["to"]}',
                "kind": "bus", "detail": row["type"], "text": json.dumps(row["payload"]),
            })
    entries.sort(key=lambda e: e["ts"])

    error = None
    if not containers_result.ok and not messages_result.ok:
        error = containers_result.error or messages_result.error
    return templates.TemplateResponse(request, "_replay_log.html", {
        "name": name, "entries": entries, "error": error,
    })


@app.post("/replays/{name}/delete", response_class=HTMLResponse)
async def delete_replay(request: Request, name: str):
    result = await _mapi_request("DELETE", f"/replays/{name}")
    if result.ok:
        return HTMLResponse("")  # row's hx-swap="outerHTML" removes it from the DOM
    replay = {"name": name, "error": result.error}
    return templates.TemplateResponse(request, "_replay_row.html", {"replay": replay})


# ── Draft review (docs/episode_store.md "Review status") ─────────────────
@app.post("/replays/{name}/approve", response_class=HTMLResponse)
async def approve_replay(request: Request, name: str):
    """Promote a draft into the airable library. Re-renders the whole
    replays section so the episode moves from the drafts table into the
    library table (where Play becomes available) in one swap."""
    result = await _mapi_request("POST", f"/replays/{quote(name)}/approve")
    if result.ok:
        log.info("approved draft name=%s previous_status=%s",
                 name, (result.data or {}).get("previous_status"))
        banner = {"ok": True, "name": name}
    else:
        banner = {"ok": False, "name": name, "error": result.error}
    return templates.TemplateResponse(
        request, "_replays_section.html", await _replays_section_context(approve_result=banner))


@app.post("/replays/{name}/reject", response_class=HTMLResponse)
async def reject_replay(request: Request, name: str):
    """Reject a draft = message-api's existing DELETE. Same empty-body /
    error-row contract as delete_replay, but an error re-renders a DRAFT
    row (Approve/Delete, no Play button) since that's what it replaces."""
    result = await _mapi_request("DELETE", f"/replays/{quote(name)}")
    if result.ok:
        log.info("rejected draft name=%s deleted=%s", name, (result.data or {}).get("deleted"))
        return HTMLResponse("")
    draft = {"name": name, "error": result.error}
    return templates.TemplateResponse(request, "_draft_row.html", {"draft": draft})


@app.get("/replays/{name}/view", response_class=HTMLResponse)
async def view_replay(request: Request, name: str):
    result = await _mapi_request("GET", f"/replays/{name}")
    if result.ok:
        pretty = json.dumps(result.data, indent=2)
        return templates.TemplateResponse(request, "_replay_view.html", {"pretty": pretty, "error": None})
    return templates.TemplateResponse(request, "_replay_view.html", {"pretty": None, "error": result.error})
