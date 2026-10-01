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
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
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

# ── Show mode: dev-team (default) vs the ashiorid_office show (OB-32) ─────
# docker-compose.office.yml re-seats the stream workers: every container's
# WORKER_ID becomes its office SEAT id (tuber_0..tuber_7) and a new
# worker-observer container holds tuber_7 (docs/office_deployment.md
# "Service -> seat mapping"). The lists above are keyed by dev-team worker
# ids, so in office mode health/Play/logs would address containers that no
# longer answer. CONTROL_PANEL_SHOW=office (set by docker-compose.office.yml
# on the control-panel service only) swaps in the office mapping below; any
# other value, or none, keeps the dev-team lists byte-for-byte.
CONTROL_PANEL_SHOW_ENV = "CONTROL_PANEL_SHOW"
SHOW_DEV_TEAM = "dev_team"
SHOW_OFFICE = "office"

#: office seat (bus worker id == tile slot) -> compose service, mirroring
#: docker-compose.office.yml's header table.
OFFICE_SEAT_TO_SERVICE = {
    "tuber_0": "worker-gm",              # CEO
    "tuber_1": "worker-manager",         # Tech Lead
    "tuber_2": "worker-coder-native",    # Analyst
    "tuber_3": "worker-coder-aider",     # Engineer
    "tuber_4": "worker-tester",          # Tester
    "tuber_5": "worker-coder-opencode",  # Marketing
    "tuber_6": "worker-coder",           # Office Manager
    "tuber_7": "worker-observer",        # Party Member (never speaks, U6)
}
#: The dev-team lists as defined above, captured before any show override.
DEV_TEAM_WORKER_IDS = list(WORKER_IDS)
DEV_TEAM_WORKER_TO_TUBER_SLOT = dict(WORKER_TO_TUBER_SLOT)
DEV_TEAM_WORKER_TO_SERVICE = dict(WORKER_TO_SERVICE)
#: The Party Member's seat: monitored (health, logs, themes) but never a
#: Play target — a replay on its channel would give it a voice.
OFFICE_OBSERVER_ID = "tuber_7"


def resolve_show(value: Optional[str] = None) -> str:
    """SHOW_OFFICE when `value` (default: the CONTROL_PANEL_SHOW env var) is
    "office" (case/space-insensitive), else SHOW_DEV_TEAM."""
    raw = os.environ.get(CONTROL_PANEL_SHOW_ENV, "") if value is None else value
    return SHOW_OFFICE if str(raw).strip().lower() == SHOW_OFFICE else SHOW_DEV_TEAM


def show_mapping(show: str) -> dict:
    """Every worker-id-keyed list/map the panel uses, for one show.

    WORKER_IDS        — health rows, the operator-message dropdown
    PLAY_WORKER_IDS   — character channels a Play click sends replay_request to
    WORKER_TO_TUBER_SLOT / WORKER_TO_SERVICE — roundtable cast / log services
    THEME_WORKER_IDS  — live-retheme targets (every stream container)
    """
    if show == SHOW_OFFICE:
        seats = list(OFFICE_SEAT_TO_SERVICE)
        return {
            "WORKER_IDS": seats,
            "PLAY_WORKER_IDS": [s for s in seats if s != OFFICE_OBSERVER_ID],
            "WORKER_TO_TUBER_SLOT": {s: s for s in seats},
            "WORKER_TO_SERVICE": dict(OFFICE_SEAT_TO_SERVICE),
            "THEME_WORKER_IDS": seats + [ROUNDTABLE_WORKER_ID],
        }
    return {
        "WORKER_IDS": list(DEV_TEAM_WORKER_IDS),
        "PLAY_WORKER_IDS": list(DEV_TEAM_WORKER_IDS),
        "WORKER_TO_TUBER_SLOT": dict(DEV_TEAM_WORKER_TO_TUBER_SLOT),
        "WORKER_TO_SERVICE": dict(DEV_TEAM_WORKER_TO_SERVICE),
        # tuber_0 (the GM's own channel) and roundtable are not in WORKER_IDS
        # but run app/theme_watcher.py too — see the theme block below.
        "THEME_WORKER_IDS": list(DEV_TEAM_WORKER_IDS) + ["tuber_0", ROUNDTABLE_WORKER_ID],
    }


SHOW = resolve_show()
_SHOW_MAPPING = show_mapping(SHOW)
WORKER_IDS = _SHOW_MAPPING["WORKER_IDS"]
PLAY_WORKER_IDS = _SHOW_MAPPING["PLAY_WORKER_IDS"]
WORKER_TO_TUBER_SLOT = _SHOW_MAPPING["WORKER_TO_TUBER_SLOT"]
WORKER_TO_SERVICE = _SHOW_MAPPING["WORKER_TO_SERVICE"]
log.info("control panel show=%s workers=%d play_targets=%d", SHOW, len(WORKER_IDS),
         len(PLAY_WORKER_IDS))

# ── Console theme control (app/console_theme.py, message-api's
#    /console-theme(s) endpoints) ────────────────────────────────────────
# Every worker container runs app/theme_watcher.py unconditionally
# (startup.sh §7.6), including tuber_0 (the GM's own channel) and
# roundtable — unlike WORKER_IDS above, which deliberately excludes both
# for its own purposes (operator_message/replay_request addressing). All 8
# are valid live-retheme targets.
THEME_WORKER_IDS = _SHOW_MAPPING["THEME_WORKER_IDS"]

# In-memory cache of the theme name list — it's static per deploy
# (config/themes/gogh_themes.json ships baked into the message-api image),
# so there's no reason to re-fetch all 1247 names on every page load.
_THEME_NAMES_CACHE: Optional[list] = None

# ── GM live music control (app/music/control.py, message-api's /music
#    endpoints) ──────────────────────────────────────────────────────────
# Only the roundtable container runs app/music_director.py, so the Music
# card targets it alone. The mood list is fetched from GET /music-moods;
# this fallback keeps the form usable (and identical) if that call fails.
MUSIC_WORKER_ID = ROUNDTABLE_WORKER_ID
MUSIC_MOODS_FALLBACK = [
    "neutral", "wonder", "transcendence", "tenderness", "nostalgia",
    "peacefulness", "power", "joyful_activation", "tension", "sadness", "silence",
]
_MUSIC_MOODS_CACHE: Optional[list] = None

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


async def _music_moods() -> list:
    global _MUSIC_MOODS_CACHE
    if _MUSIC_MOODS_CACHE is not None:
        return _MUSIC_MOODS_CACHE
    result = await _mapi_request("GET", "/music-moods")
    if result.ok and result.data.get("moods"):
        _MUSIC_MOODS_CACHE = result.data["moods"]
        return _MUSIC_MOODS_CACHE
    log.debug("music moods fetch failed, using fallback error=%s", result.error)
    return MUSIC_MOODS_FALLBACK


async def _music_state(worker_id: str = MUSIC_WORKER_ID) -> dict:
    """Template context for _music_card.html: override + now-playing."""
    result = await _mapi_request("GET", f"/music/{worker_id}")
    if result.ok:
        return {
            "id": worker_id,
            "override": result.data.get("override"),
            "overridden": bool(result.data.get("overridden")),
            "running": bool(result.data.get("running")),
            "status": result.data.get("status") or {},
            "error": None,
        }
    return {"id": worker_id, "override": None, "overridden": False,
            "running": False, "status": {}, "error": result.error}


async def _render_music_card(request: Request, error: Optional[str] = None):
    music = await _music_state()
    if error:
        # The write failed — show it, even if the follow-up read succeeded.
        music["error"] = error
    return templates.TemplateResponse(request, "_music_card.html", {
        "music": music, "music_moods": await _music_moods(),
    })


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
    music = await _music_state()
    music_moods = await _music_moods()
    return templates.TemplateResponse(request, "base.html", {
        "message_api_url": MESSAGE_API_URL,
        "campaign_manager_url": _campaign_manager_public_url(request),
        "workers": workers,
        "message_type_examples": MESSAGE_TYPE_EXAMPLES,
        "worker_ids": WORKER_IDS,
        "log_types": log_types,
        **episode_lists,
        "current_airing": await _current_airing(),
        "message_result": None,
        "prune_result": None,
        "themes": themes,
        "themes_error": None if themes else "message-api unreachable or returned no themes",
        "theme_workers": theme_workers,
        "music": music,
        "music_moods": music_moods,
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


# ── GM live music ────────────────────────────────────────────────────────
@app.get("/partials/music", response_class=HTMLResponse)
async def partial_music(request: Request):
    return await _render_music_card(request)


@app.post("/music/set", response_class=HTMLResponse)
async def set_music(request: Request, mood: str = Form(...), intensity: float = Form(0.5)):
    result = await _mapi_request("POST", f"/music/{MUSIC_WORKER_ID}",
                                 json={"mood": mood, "intensity": intensity})
    if result.ok:
        log.info("music override set via panel mood=%s intensity=%s", mood, intensity)
    return await _render_music_card(request, None if result.ok else result.error)


@app.post("/music/clear", response_class=HTMLResponse)
async def clear_music(request: Request):
    result = await _mapi_request("DELETE", f"/music/{MUSIC_WORKER_ID}")
    if result.ok:
        log.info("music override cleared via panel")
    return await _render_music_card(request, None if result.ok else result.error)


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


async def _recordings_view() -> dict:
    """Saved recordings + budget for the Recordings table. A failure is a
    banner, never a broken replays section."""
    result = await _mapi_request("GET", "/recordings")
    if not result.ok:
        return {"recordings": [], "recordings_error": result.error, "recordings_budget": None}
    data = result.data or {}
    budget = {"used": _fmt_bytes(data.get("used_bytes")), "limit": _fmt_bytes(data.get("limit_bytes")),
              "remaining": _fmt_bytes(data.get("remaining_bytes")),
              "percent": min(100, round(100 * (data.get("used_bytes") or 0)
                                        / max(data.get("limit_bytes") or 1, 1)))}
    recordings = [{**rec, "size": _fmt_bytes(rec.get("bytes")),
                   "files": [{**f, "size": _fmt_bytes(f.get("bytes"))} for f in rec.get("files", [])]}
                  for rec in data.get("recordings", [])]
    return {"recordings": recordings, "recordings_error": None, "recordings_budget": budget}


_QUEUED_EPISODE_RE = re.compile(r"queued replay episode '([^']+)'")


async def _current_airing() -> Optional[dict]:
    """The most recent airing the roundtable accepted, so the progress bar
    survives a page refresh / another browser instead of living only in the
    response to the Play click that started it.

    Server-side and stateless on purpose: the roundtable's own
    "queued replay episode '<name>'" line (app/agent_handlers/replay_relay.py)
    is already shipped to container_logs, so its newest occurrence IS the
    current airing, whoever pressed Play and from wherever. `since` is backed
    off one second so the queued line itself falls inside the progress
    window (the filter is strictly-after); parse_replay_progress resets on
    it, so a preempted airing's tail just before it can't leak in. A
    finished/stopped airing still renders (the progress endpoint answers 286
    and the bar freezes at its final state) — "last airing: finished" is
    the honest status when nothing is running."""
    result = await _mapi_request("GET", "/logs/containers", params=[
        ("service", ROUNDTABLE_SERVICE), ("contains", PROGRESS_MARKERS["queued"]), ("limit", "1")])
    if not result.ok:
        log.warning("current_airing lookup failed error=%s", result.error)
        return None
    rows = (result.data or {}).get("logs", [])
    if not rows:
        log.debug("current_airing none")
        return None
    row = rows[-1]
    m = _QUEUED_EPISODE_RE.search(_ANSI_RE.sub("", row.get("message") or ""))
    try:
        queued_at = datetime.fromisoformat(row["log_timestamp"])
    except (KeyError, TypeError, ValueError):
        queued_at = None
    if not m or queued_at is None:
        log.debug("current_airing unparseable row=%r", row)
        return None
    name = m.group(1)
    since = (queued_at - timedelta(seconds=1)).isoformat()
    log.debug("current_airing name=%s since=%s", name, since)
    return {"name": name,
            "progress_url": f"/replays/{quote(name)}/progress?since={quote(since)}",
            "log_url": f"/replays/{quote(name)}/log?since={quote(since)}"}


async def _replays_section_context(banner: Optional[dict] = None, play_result: Optional[dict] = None,
                                   approve_result: Optional[dict] = None) -> dict:
    return {
        **await _episode_lists(),
        **await _recordings_view(),
        "upload_result": banner,
        "worker_ids": WORKER_IDS,
        "play_result": play_result,
        # A fresh Play renders its own bar; every other re-render (refresh,
        # upload, approve, recording delete) re-attaches to the live airing.
        "current_airing": None if play_result and play_result.get("progress_url") else await _current_airing(),
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


#: Play's "save to file" choices (docs/stream_recorder.md) -> which streams
#: get recorded. "roundtable" is the one composite view of the whole show
#: (~2 GB/hour); "all" records every one of the 7 airing streams (7x that).
RECORD_CHOICES = ("none", "roundtable", "all")


def _record_streams(record: str) -> List[str]:
    if record == "roundtable":
        return [ROUNDTABLE_WORKER_ID]
    if record == "all":
        return [*PLAY_WORKER_IDS, ROUNDTABLE_WORKER_ID]
    return []


async def _reserve_recording(name: str, record: str) -> tuple:
    """Ask message-api to estimate + reserve storage for a recorded Play.
    Returns (record_payload, info, error): payload is the replay_request's
    `record` field; error is set (and nothing may air) when the recording
    was refused or couldn't be reserved."""
    streams = _record_streams(record)
    result = await _mapi_request("POST", "/recordings",
                                 json={"episode": name, "streams": streams})
    if not result.ok:
        detail = result.data.get("detail") if isinstance(result.data, dict) else None
        reason = detail.get("reason") if isinstance(detail, dict) else result.error
        log.warning("recording refused name=%s record=%s reason=%s", name, record, reason)
        return None, None, f"not started — recording refused: {reason}"
    data = result.data or {}
    payload = {"recording_id": data["recording_id"], "max_bytes": data["max_bytes_per_stream"]}
    info = {"recording_id": data["recording_id"], "streams": len(streams),
            "estimated": _fmt_bytes(data.get("estimated_bytes", 0)),
            "duration": _fmt_duration(data.get("estimated_duration_s", 0)),
            "remaining": _fmt_bytes(data.get("remaining_bytes", 0) - data.get("reserved_bytes", 0)),
            "limit": _fmt_bytes(data.get("limit_bytes", 0))}
    log.info("recording reserved name=%s record=%s recording_id=%s estimated_bytes=%s",
             name, record, data["recording_id"], data.get("estimated_bytes"))
    return payload, info, None


def _fmt_bytes(n: Any) -> str:
    n = float(n or 0)
    if n >= 1e9:
        return f"{n / 1e9:.2f} GB"
    return f"{n / 1e6:.0f} MB"


def _fmt_duration(seconds: Any) -> str:
    minutes, secs = divmod(int(float(seconds or 0)), 60)
    return f"{minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m{secs:02d}s"


@app.post("/replays/{name}/play", response_class=HTMLResponse)
async def play_replay(request: Request, name: str, record: str = Form("none")):
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
       app/agent_handlers/relay_files.py's _write_replay_request /
       app/agent_handlers/replay_relay.py's handle_replay_request,
       which — unlike handle_replay_invite — has no "don't clobber a
       pending request" guard). Addressing each of the 6 character
       workers BY NAME instead of "broadcast" means nothing but this one
       call ever writes to the roundtable's request file, so there is no
       race to reason about.

    Every failure is best-effort and independently reported — one
    unreachable worker must not stop the episode airing on the other six.

    Stops each of the 7 targets before requesting the new episode
    (docs/operator_commands.md `replay_stop`,
    app/agent_handlers/replay_relay.py handle_replay_stop): replay_pane.py's poll loop only reads a fresh
    request file once it's idle between episodes, so a bare replay_request
    fired at a worker that's still mid-show just queues silently behind
    whatever's already playing — potentially minutes away, with nothing in
    this UI to say so (reported live: hit Play on 'roundtable-stream-check',
    saw it logged on the Kafka panel, but the 'coder' channel just kept
    airing its previous ashiorid_generated_ce8d episode for another 25+
    minutes). replay_stop cancels a still-queued request outright and
    signals a currently-playing one to abort within a fraction of a second
    (Pacer.should_stop), so Play now always preempts rather than queuing.

    `record` ("none" | "roundtable" | "all", docs/stream_recorder.md): save
    the airing to disk as well. The size is estimated and reserved against
    the recordings budget (message-api POST /recordings) BEFORE anything is
    stopped or queued — a refused recording airs nothing, so the operator
    can pick a smaller option rather than get an unrecorded airing they
    didn't ask for.
    """
    if record not in RECORD_CHOICES:
        record = "none"
    record_payload, record_info = None, None
    if record != "none":
        record_payload, record_info, error = await _reserve_recording(name, record)
        if error:
            banner = {"ok": False, "name": name, "error": error}
            return templates.TemplateResponse(
                request, "_replays_section.html", await _replays_section_context(play_result=banner))
    recorded = set(_record_streams(record))

    def _payload(worker_id: str, base: dict) -> dict:
        if record_payload and worker_id in recorded:
            return {**base, "record": record_payload}
        return base

    results = []
    for worker_id in PLAY_WORKER_IDS:
        await _mapi_request(
            "POST", "/messages",
            json={"to": worker_id, "type": "replay_stop", "payload": {}},
        )
        r = await _mapi_request(
            "POST", "/messages",
            json={"to": worker_id, "type": "replay_request",
                  "payload": _payload(worker_id, {"episode": name})},
        )
        results.append((worker_id, r))
    await _mapi_request(
        "POST", "/messages",
        json={"to": ROUNDTABLE_WORKER_ID, "type": "replay_stop", "payload": {}},
    )
    roundtable_result = await _mapi_request(
        "POST", "/messages",
        json={"to": ROUNDTABLE_WORKER_ID, "type": "replay_request",
              "payload": _payload(ROUNDTABLE_WORKER_ID, {
                  "episode": name,
                  "cast": {**WORKER_TO_TUBER_SLOT, **TUBER_SLOT_IDENTITY_CAST}})},
    )
    results.append((ROUNDTABLE_WORKER_ID, roundtable_result))

    failed = [worker_id for worker_id, r in results if not r.ok]
    played_at = datetime.now(timezone.utc).isoformat()
    log_url = f"/replays/{quote(name)}/log?since={quote(played_at)}"
    progress_url = f"/replays/{quote(name)}/progress?since={quote(played_at)}"
    if not failed:
        banner = {"ok": True, "name": name, "log_url": log_url, "progress_url": progress_url,
                  "to": f"all {len(results)} streams ({len(results) - 1} channels + roundtable)",
                  "recording": record_info}
    else:
        errors = "; ".join(f"{w}: {r.error}" for w, r in results if not r.ok)
        succeeded = len(results) - len(failed)
        banner = {"ok": False, "name": name, "log_url": log_url, "progress_url": progress_url,
                  "error": f"{succeeded}/{len(results)} streams queued — failed: {errors}",
                  "recording": record_info}
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


# ── Replay progress bar ──────────────────────────────────────────────────
# The roundtable director is the airing's master clock (it cues every other
# stream per scene — app/replay_pane.py perform_director_request), so its
# own pane output is the one authoritative "how far along is this" signal.
# These are the literal milestone substrings it prints (app/replay_pane.py,
# app/replay.py Performer.perform); message-api filters on them server-side
# so the hundreds of heartbeat lines in the same window can't push them out
# of the row LIMIT.
PROGRESS_MARKERS = {
    "queued": "queued replay episode",
    "preparing": "[replay_pane] preparing: ",
    "reusing": "[replay_pane] reusing cached narration",
    "airing": "══ REPLAY: ",
    "scene": "♪ ",
    "fin": "══ fin ══",
    "stopped": "══ stopped ══",
    "interrupted": "══ interrupted ══",
    "refused": "duet refused",
    "missing": "episode not in the library",
    "failed": "[replay_pane] episode failed",
}
# Share of the bar each phase owns: voice prep is real work (LLM + TTS per
# scene) but airing is what the operator is actually waiting through.
PROGRESS_QUEUED_PCT = 5
PROGRESS_PREP_END_PCT = 35
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_PREP_RE = re.compile(r"preparing: scene (\d+)/(\d+)")
_AIRING_RE = re.compile(r"══ REPLAY: .*\((\d+) scenes\) ══")


def parse_replay_progress(lines: List[str]) -> dict:
    """Fold the director's milestone lines (oldest-first) into one progress
    snapshot: {state, phase, percent, label, scene, total}.

    state is "running" | "done" | "stopped" | "failed". Terminal markers
    only count once THIS airing has visibly started (prep or header seen):
    Play sends replay_stop first, so the PREVIOUS airing's own
    "══ stopped ══"/"duet refused" lines routinely land inside this window
    and must not end the new bar before it begins."""
    log.debug("parse_replay_progress lines=%d", len(lines))
    snap = {"state": "running", "phase": "requested", "percent": 1,
            "label": "request sent, waiting for the roundtable to pick it up",
            "scene": 0, "total": 0}
    started = False
    queued = False
    for raw in lines:
        line = _ANSI_RE.sub("", raw or "")
        m = _AIRING_RE.search(line)
        if m:
            started = True
            snap.update(phase="airing", total=int(m.group(1)), scene=0,
                        percent=PROGRESS_PREP_END_PCT, label="airing — starting scene 1")
            continue
        if PROGRESS_MARKERS["queued"] in line:
            # The roundtable accepted a request: whatever came before it in
            # this window belongs to the PREVIOUS airing being preempted.
            started, queued = False, True
            snap.update(state="running", phase="queued", percent=PROGRESS_QUEUED_PCT,
                        label="queued on the roundtable — waiting for voice prep",
                        scene=0, total=0)
            continue
        m = _PREP_RE.search(line)
        if m and snap["phase"] != "airing":
            started = True
            i, n = int(m.group(1)), max(int(m.group(2)), 1)
            pct = PROGRESS_QUEUED_PCT + (PROGRESS_PREP_END_PCT - PROGRESS_QUEUED_PCT) * i / n
            snap.update(phase="preparing", total=n, scene=i, percent=round(pct),
                        label=f"preparing voices — scene {i}/{n}")
            continue
        if PROGRESS_MARKERS["reusing"] in line and snap["phase"] != "airing":
            started = True
            snap.update(phase="preparing", percent=PROGRESS_PREP_END_PCT,
                        label="reusing cached narration")
            continue
        if snap["phase"] == "airing" and line.lstrip().startswith("♪"):
            total = max(snap["total"], 1)
            scene = min(snap["scene"] + 1, total)
            pct = PROGRESS_PREP_END_PCT + (100 - PROGRESS_PREP_END_PCT) * (scene - 1) / total
            snap.update(scene=scene, percent=round(pct),
                        label=f"airing — scene {scene}/{total}")
            continue
        if not started:
            # Pre-start errors only count for a request the roundtable has
            # actually accepted in this window (see docstring) — and never
            # the preempted airing's own "refused: operator replay_stop…",
            # which Play itself caused and which can print after "queued".
            if queued and any(PROGRESS_MARKERS[k] in line for k in ("refused", "missing", "failed")) \
                    and "replay_stop" not in line:
                snap.update(state="failed", phase="failed", label=line.strip()[:200])
                log.debug("parse_replay_progress branch=failed_before_start")
            continue
        if PROGRESS_MARKERS["fin"] in line:
            snap.update(state="done", phase="done", percent=100, scene=snap["total"],
                        label="finished")
        elif PROGRESS_MARKERS["stopped"] in line or PROGRESS_MARKERS["interrupted"] in line:
            snap.update(state="stopped", phase="stopped",
                        label=f"stopped at scene {snap['scene']}/{snap['total']}")
        elif any(PROGRESS_MARKERS[k] in line for k in ("refused", "missing", "failed")):
            snap.update(state="failed", phase="failed", label=line.strip()[:200])
    log.debug("parse_replay_progress result state=%s phase=%s percent=%s",
              snap["state"], snap["phase"], snap["percent"])
    return snap


def _format_elapsed(since: str) -> str:
    try:
        started = datetime.fromisoformat(since)
    except ValueError:
        return ""
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    seconds = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    return f"{seconds // 60}:{seconds % 60:02d}"


@app.get("/replays/{name}/progress", response_class=HTMLResponse)
async def replay_progress(request: Request, name: str, since: str = Query("")):
    """Progress bar for the airing a Play click started, polled alongside
    the log viewer. Answers HTTP 286 once the airing reaches a terminal
    state — htmx's documented "stop polling" status — so a finished bar
    freezes instead of re-querying Postgres forever."""
    params: List[Any] = [("service", ROUNDTABLE_SERVICE), ("limit", "500")]
    params += [("contains", marker) for marker in PROGRESS_MARKERS.values()]
    if since:
        params.append(("since", since))
    result = await _mapi_request("GET", "/logs/containers", params=params)
    if not result.ok:
        log.error("replay_progress fetch failed name=%s error=%s", name, result.error)
        snap = {"state": "running", "phase": "unknown", "percent": 0,
                "label": f"progress unavailable: {result.error}", "scene": 0, "total": 0}
    else:
        snap = parse_replay_progress([row["message"] for row in result.data.get("logs", [])])
    snap["elapsed"] = _format_elapsed(since) if since else ""
    status_code = 200 if snap["state"] == "running" else 286
    if status_code == 286:
        log.info("replay_progress terminal name=%s state=%s", name, snap["state"])
    return templates.TemplateResponse(request, "_replay_progress.html",
                                      {"name": name, "p": snap}, status_code=status_code)


# ── Saved replay recordings (docs/stream_recorder.md) ────────────────────
@app.get("/recordings/{recording_id}/{filename}")
async def download_recording(recording_id: str, filename: str):
    """Stream a saved recording through from message-api (the only service
    that mounts the recordings volume) — the browser never needs to reach
    message-api directly. No read timeout: files run to GBs."""
    path = f"/recordings/{quote(recording_id)}/{quote(filename)}"
    req = http_client.build_request("GET", path, timeout=httpx.Timeout(10.0, read=None))
    try:
        resp = await http_client.send(req, stream=True)
    except httpx.RequestError as exc:
        log.error("recording download failed recording_id=%s file=%s error=%s", recording_id, filename, exc)
        return PlainTextResponse(f"message-api unreachable: {exc}", status_code=502)
    if resp.status_code >= 400:
        body = await resp.aread()
        await resp.aclose()
        return PlainTextResponse(body.decode("utf-8", "replace"), status_code=resp.status_code)

    async def _body():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()

    headers = {k: v for k, v in resp.headers.items()
               if k.lower() in ("content-length", "content-disposition")}
    return StreamingResponse(_body(), media_type="video/mp4", headers=headers)


@app.post("/recordings/{recording_id}/delete", response_class=HTMLResponse)
async def delete_recording(request: Request, recording_id: str):
    result = await _mapi_request("DELETE", f"/recordings/{quote(recording_id)}")
    banner = None
    if not result.ok:
        banner = {"ok": False, "name": recording_id, "error": f"delete recording failed: {result.error}"}
    else:
        log.info("recording deleted recording_id=%s", recording_id)
    return templates.TemplateResponse(
        request, "_replays_section.html", await _replays_section_context(play_result=banner))


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
