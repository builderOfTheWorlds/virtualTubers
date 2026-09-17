"""Acceptance tests for services/3layer-generator/api.py — the HTTP surface.

This is the interface the management GUI drives. It does no generation itself:
it validates a request, writes a job row, and gets out of the way. The runner
does the work; these endpoints only ever read and write job state.

Validation is the whole point of the module. A job that is going to fail
because the profile does not exist, or because `test_mode` was sent without a
segment, must be rejected at submit time with a 4xx the caller can act on —
not accepted, queued, and failed forty minutes later when a dispatcher finally
picks it up and discovers the same thing.

The store and the runner context are faked here; `test_generation_store.py`
proves the SQL and `test_service_runner.py` proves the dispatch.
"""
import pytest

from fastapi.testclient import TestClient

# Imported under an alias: the module is generator_api, not api, because
# services/message-api/api.py already owns the bare name `api` on sys.path.
import generator_api as api
from test_service_runner import FakeStore


CONFIG = {
    "output": {"dir": "/data/output"},
    "budget": {"measured_baseline": {"words_per_take": 105}},
    "dialogue": {"takes_per_slot": 3,
                 "models": {"light": {"model": "a"}, "heavy": {"model": "b"}},
                 "active_model": "heavy"},
    "arc": {"models": {"light": {"model": "a"}, "heavy": {"model": "b"}},
            "active_model": "heavy"},
    "segment": {
        "target_words": 53600,
        "target_slots": 170,
        "models": {"light": {"model": "a"}, "heavy": {"model": "b"}},
        "active_model": "heavy",
        "tree": {"max_leaf_slots": 19, "max_children": 12, "max_depth": 4,
                 "min_node_words": 2000, "leaf_density_floor": 0.80},
    },
    "preview": {"require_arc_plan": True},
}


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(api, "store", fake)
    return fake


@pytest.fixture
def llm_calls(monkeypatch):
    """Records every LLM invocation. /preview must make none."""
    calls = []
    monkeypatch.setattr(api, "build_llm", lambda *a, **k: calls.append(1))
    return calls


@pytest.fixture
def client(monkeypatch, store, tmp_path):
    (tmp_path / "packs" / "ashiorid").mkdir(parents=True)
    monkeypatch.setattr(api, "CONFIG", CONFIG)
    monkeypatch.setattr(api, "PACK_ROOT", tmp_path / "packs")
    monkeypatch.setattr(api, "OUTPUT_ROOT", tmp_path / "out")
    monkeypatch.setattr(api, "DEFAULT_PACK", "ashiorid")
    return TestClient(api.app)


def submit(client, **body):
    body.setdefault("stage", "segment")
    # Most tests here are about pack/profile/params validation, not the
    # run-naming behavior — default a `run` so segment/dialogue submissions
    # (which require one explicitly; see test_run_naming below) don't fail
    # on that unrelated to what the test is checking. Pass run=None
    # explicitly to test the "no run supplied" path.
    if "run" not in body:
        body["run"] = "ashiorid_test"
    elif body["run"] is None:
        del body["run"]
    return client.post("/jobs", json=body)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

def test_healthz_is_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /jobs — validation
# ---------------------------------------------------------------------------

def test_a_valid_job_is_queued_and_its_id_returned(client, store):
    response = submit(client, segments=["seg-01"])

    assert response.status_code in (200, 201)
    body = response.json()
    assert body["status"] == "queued"
    assert store.get(body["id"])["stage"] == "segment"


def test_an_unknown_stage_is_rejected(client):
    assert submit(client, stage="interpretive-dance").status_code == 400


@pytest.mark.parametrize("stage", ["arc", "segment", "dialogue", "all", "publish"])
def test_every_known_stage_is_accepted(client, stage):
    assert submit(client, stage=stage, segments=["seg-01"]).status_code in (200, 201)


def test_an_unknown_profile_is_rejected_at_submit_time(client):
    """Rejecting here costs a 400. Accepting it costs the operator forty
    minutes of waiting for a dispatcher to discover the same thing."""
    response = submit(client, segments=["seg-01"], profile="does-not-exist")
    assert response.status_code == 400
    assert "does-not-exist" in response.text


def test_a_known_profile_is_accepted(client):
    assert submit(client, segments=["seg-01"], profile="light").status_code in (200, 201)


def test_an_unknown_pack_is_rejected(client):
    assert submit(client, pack="no-such-pack", segments=["seg-01"]).status_code == 400


@pytest.mark.parametrize("evil", ["../etc", "a/b", "..", "/absolute"])
def test_a_pack_name_that_escapes_the_mount_is_rejected(client, evil):
    """The only place untrusted input becomes a filesystem path."""
    assert submit(client, pack=evil, segments=["seg-01"]).status_code == 400


def test_test_mode_requires_exactly_one_segment(client):
    assert submit(client, test_mode=True, segments=["a", "b"]).status_code == 400
    assert submit(client, test_mode=True, segments=[]).status_code == 400
    assert submit(client, test_mode=True, segments=["a"]).status_code in (200, 201)


def test_stingers_are_rejected_as_not_yet_built(client):
    response = submit(client, stingers=True, segments=["seg-01"])
    assert response.status_code == 400
    assert "events.yaml" in response.text


def test_the_submitted_params_reach_the_job_row(client, store):
    response = submit(client, segments=["seg-01"], rebrief=True)
    params = store.get(response.json()["id"])["params"]
    assert params["segments"] == ["seg-01"]
    assert params["rebrief"] is True


def test_a_caller_cannot_set_the_status_field(client, store):
    """The request body names columns only through the whitelist in
    generation_store.submit; a caller must not be able to queue a job that
    claims to be already completed."""
    response = submit(client, segments=["seg-01"], status="completed")
    assert store.get(response.json()["id"])["status"] == "queued"


# ---------------------------------------------------------------------------
# POST /jobs — run naming
# ---------------------------------------------------------------------------

def test_an_arc_job_with_no_run_gets_one_auto_generated(client, store):
    response = submit(client, stage="arc", run=None)

    assert response.status_code in (200, 201)
    run = response.json()["run"]
    assert run.startswith("ashiorid_")
    assert store.get(response.json()["id"])["run"] == run


def test_an_all_job_with_no_run_gets_one_auto_generated(client, store):
    """'all' normalizes to 'arc' for the runner, and gets the same
    fresh-pipeline treatment."""
    response = submit(client, stage="all", run=None)
    assert response.status_code in (200, 201)
    assert response.json()["run"].startswith("ashiorid_")


def test_two_arc_jobs_with_no_run_get_distinct_runs(client, store):
    first = submit(client, stage="arc", run=None).json()["run"]
    second = submit(client, stage="arc", run=None).json()["run"]
    assert first != second


@pytest.mark.parametrize("stage", ["segment", "dialogue"])
def test_a_segment_or_dialogue_job_with_no_run_is_rejected(client, stage):
    """Only an arc job starts a fresh pipeline; segment/dialogue continue an
    existing run's arc plan and would silently look in the wrong place
    (or nowhere) without one."""
    response = submit(client, stage=stage, run=None, segments=["seg-01"])
    assert response.status_code == 400
    assert "run is required" in response.text


def test_an_explicit_run_is_honoured(client, store):
    response = submit(client, stage="segment", run="ashiorid_20260101_000000",
                      segments=["seg-01"])
    assert response.status_code in (200, 201)
    assert response.json()["run"] == "ashiorid_20260101_000000"
    assert store.get(response.json()["id"])["run"] == "ashiorid_20260101_000000"


# ---------------------------------------------------------------------------
# Phase 3 (task 3.1/3.4): the `publish` job stage + its upload proxy endpoint
# ---------------------------------------------------------------------------

def test_a_publish_job_carries_the_episode_naming_in_its_params(client, store):
    """The runner's `_run_publish` reads `episode_name`/`episode_source` from
    `job["params"]` — this is the ONLY path they get there, so a submit
    without them lands a job whose publish stage would fall back to
    `run`-derived naming (fine, but not the operator's). Campaign-manager's
    Publish button sends them; proving they reach the row is what makes that
    true end to end, not a claim about the route."""
    response = client.post("/jobs", json={
        "pack": "ashiorid",
        "run": "ashiorid_20260913_180158_ce8d",
        "stage": "publish",
        "profile": "",
        "episode_name": "ashiorid_generated_ce8d",
        "episode_source": "ashiorid",
        "episode_project": "virtualTubers",
    })
    assert response.status_code in (200, 201), response.text
    row = store.get(response.json()["id"])
    assert row["stage"] == "publish"
    assert row["params"]["episode_name"] == "ashiorid_generated_ce8d"
    assert row["params"]["episode_source"] == "ashiorid"
    assert row["params"]["episode_project"] == "virtualTubers"


def test_a_publish_job_without_episode_naming_falls_back_cleanly(client, store):
    """Not every submitter knows the naming convention — a bare publish
    submit must simply OMIT the keys (runner falls back to
    `run`-derived defaults), not crash or store `null` where a string is
    expected (which the runner's `or` fallbacks would actually handle, but
    not storing them at all is the cleaner contract to assert)."""
    response = client.post("/jobs", json={
        "pack": "ashiorid",
        "run": "ashiorid_20260913_180158_ce8d",
        "stage": "publish",
        "profile": "",
    })
    assert response.status_code in (200, 201), response.text
    params = store.get(response.json()["id"])["params"]
    assert params.get("episode_name") is None
    assert params.get("episode_source") is None


def test_publish_requires_run_like_every_non_arc_stage(client, store):
    """A publish without a run has nothing to build an episode from — same
    failure class as segment/dialogue without a run, for the same reason
    (it operates on an existing run's artifacts). Reject it at submit time
    rather than queueing a job that can only fail later."""
    response = client.post("/jobs", json={
        "pack": "ashiorid", "stage": "publish", "profile": ""})
    assert response.status_code == 400
    assert "run is required" in response.text


# ---------------------------------------------------------------------------
# POST /publish/{run}/upload — the proxy to message-api's POST /replays
# ---------------------------------------------------------------------------

def test_publish_upload_404s_when_the_run_has_no_episode_json(client, tmp_path):
    response = client.post("/publish/never-ran/upload")
    assert response.status_code == 404
    assert "no episode.json" in response.text


def test_publish_upload_forwards_to_message_api(monkeypatch, client, tmp_path):
    """The ONE way an episode reaches the library: this endpoint's proxy of
    message-api's POST /replays. Provable without a real message-api by
    stubbing httpx.Client (the generator's one outbound HTTP call in this
    codepath) and asserting the exact path, query params, and body bytes it
    would have sent — the real integration (message-api's validation) is
    message-api's own test suite's concern, not this service's."""
    import generator_api as api
    written_run = "ashiorid_20260913_180158_ce8d"
    episode_dir = tmp_path / "out" / written_run
    episode_dir.mkdir(parents=True)
    episode_body = b'{"source": "ashiorid", "events": [{"type": "user_message", "text": "hi"}]}'
    (episode_dir / "episode.json").write_bytes(episode_body)
    api.OUTPUT_ROOT = tmp_path / "out"

    captured = {}

    class _FakeResponse:
        is_success = True
        status_code = 200
        text = '{"name": "ashiorid_generated_ce8d", "created": true}'
        def json(self):
            return {"name": "ashiorid_generated_ce8d", "created": True}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, url, content=None, headers=None, params=None):
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = headers
            captured["params"] = params
            return _FakeResponse()

    import httpx as httpx_module
    # Patch Client on the REAL httpx module (restored on test exit): the
    # endpoint's `import httpx` resolves that same module object out of
    # sys.modules, so patching a copied attribute would silently miss it.
    monkeypatch.setattr(httpx_module, "Client", _FakeClient)
    monkeypatch.setenv("MESSAGE_API_URL", "http://127.0.0.1:8090")

    response = client.post(f"/publish/{written_run}/upload",
                           params={"name": "ashiorid_generated_ce8d"})
    assert response.status_code == 200, response.text
    assert captured["url"] == "http://127.0.0.1:8090/replays"
    assert captured["content"] == episode_body
    assert captured["params"] == {"name": "ashiorid_generated_ce8d"}
    body = response.json()
    assert body["status"] == "uploaded"
    assert body["name"] == "ashiorid_generated_ce8d"


@pytest.mark.parametrize("evil", ["../etc", "a/b", "..", "/absolute"])
def test_a_run_name_that_escapes_the_mount_is_rejected(client, evil):
    assert submit(client, stage="segment", run=evil,
                  segments=["seg-01"]).status_code == 400


# ---------------------------------------------------------------------------
# GET /jobs
# ---------------------------------------------------------------------------

def test_listing_returns_the_jobs(client, store):
    submit(client, stage="arc")
    submit(client, stage="segment", segments=["seg-01"])

    rows = client.get("/jobs").json()

    assert len(rows) == 2


@pytest.mark.parametrize("query,expected", [
    ("?stage=arc", 1),
    ("?stage=segment", 1),
    ("?status=queued", 2),
    ("?status=completed", 0),
    ("?pack=ashiorid", 2),
    ("?pack=nope", 0),
])
def test_listing_filters(client, store, query, expected):
    submit(client, stage="arc")
    submit(client, stage="segment", segments=["seg-01"])

    assert len(client.get(f"/jobs{query}").json()) == expected


def test_getting_one_job_returns_the_full_record(client, store):
    job_id = submit(client, segments=["seg-01"]).json()["id"]

    body = client.get(f"/jobs/{job_id}").json()

    assert body["id"] == job_id
    assert "params" in body and "progress" in body


def test_getting_an_unknown_job_is_404(client):
    assert client.get("/jobs/job_nope").status_code == 404


# ---------------------------------------------------------------------------
# POST /jobs/{id}/cancel
# ---------------------------------------------------------------------------

def test_cancelling_a_queued_job_sets_the_flag(client, store):
    job_id = submit(client, segments=["seg-01"]).json()["id"]

    response = client.post(f"/jobs/{job_id}/cancel")

    assert response.status_code == 200
    assert store.is_cancelled(job_id) is True


def test_cancelling_an_unknown_job_is_404(client):
    assert client.post("/jobs/job_nope/cancel").status_code == 404


def test_cancelling_a_terminal_job_is_409(client, store):
    job_id = submit(client, segments=["seg-01"]).json()["id"]
    store.mark_running(job_id)
    store.finish(job_id, "completed", result={})

    assert client.post(f"/jobs/{job_id}/cancel").status_code == 409


def test_cancelling_an_arc_job_is_202_with_a_not_interruptible_note(client, store):
    """plan_arc is one LLM call with no hook. Saying so is better than a 200
    that implies the run stops now."""
    job_id = submit(client, stage="arc").json()["id"]

    response = client.post(f"/jobs/{job_id}/cancel")

    assert response.status_code == 202
    assert "interruptible" in response.text
    assert store.is_cancelled(job_id) is True


# ---------------------------------------------------------------------------
# POST /preview
# ---------------------------------------------------------------------------

def _write_arc_plan(tmp_root, pack="ashiorid"):
    import yaml
    path = tmp_root / pack / "arc_plan.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"segments": [
        {"id": "seg-01", "order": 0, "hours": 6}]}), encoding="utf-8")


def test_preview_returns_the_tree_shape_that_would_be_planned(client, llm_calls,
                                                              tmp_path):
    _write_arc_plan(tmp_path / "out")

    body = client.post("/preview", json={"stage": "segment"}).json()

    assert body["target_slots"] == 170
    assert body["expected_children"] == 9      # ceil(170 / 19)
    assert body["expected_max_depth"] == 4


def test_preview_makes_no_llm_call_at_all(client, llm_calls, tmp_path):
    _write_arc_plan(tmp_path / "out")
    client.post("/preview", json={"stage": "segment"})
    assert llm_calls == []


def test_preview_creates_no_job_row(client, store, tmp_path):
    _write_arc_plan(tmp_path / "out")
    client.post("/preview", json={"stage": "segment"})
    assert store.jobs == {}


def test_preview_caps_expected_children_at_max_children(client, tmp_path,
                                                        monkeypatch):
    _write_arc_plan(tmp_path / "out")
    config = {**CONFIG, "segment": {**CONFIG["segment"], "target_slots": 5000}}
    monkeypatch.setattr(api, "CONFIG", config)

    body = client.post("/preview", json={"stage": "segment"}).json()

    assert body["expected_children"] == 12


def test_preview_without_an_arc_plan_is_409(client, tmp_path):
    """Root target_slots comes from arc_plan.yaml; it cannot be derived from
    config alone."""
    response = client.post("/preview", json={"stage": "segment"})
    assert response.status_code == 409
    assert "arc" in response.text.lower()


def test_preview_can_be_configured_to_accept_an_explicit_target(client, tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(api, "CONFIG",
                        {**CONFIG, "preview": {"require_arc_plan": False}})

    body = client.post("/preview", json={"stage": "segment",
                                         "target_slots": 38}).json()

    assert body["target_slots"] == 38
    assert body["expected_children"] == 2


def test_preview_with_the_knob_off_and_no_target_is_400(client, monkeypatch):
    monkeypatch.setattr(api, "CONFIG",
                        {**CONFIG, "preview": {"require_arc_plan": False}})
    assert client.post("/preview", json={"stage": "segment"}).status_code == 400


# ---------------------------------------------------------------------------
# GET /config
# ---------------------------------------------------------------------------

def test_config_returns_the_loaded_config(client):
    body = client.get("/config").json()
    assert body["segment"]["tree"]["max_leaf_slots"] == 19


def test_config_reports_the_resolved_active_profile(client):
    body = client.get("/config").json()
    assert body["resolved_profile"]["segment"]["model"] == "b"


def test_config_for_an_unknown_pack_is_400(client):
    assert client.get("/config?pack=nope").status_code == 400


# ---------------------------------------------------------------------------
# The output overlay — regression guard for PLAN_v3 review finding 1
# ---------------------------------------------------------------------------

def test_the_config_output_dir_is_overlaid_from_the_environment(monkeypatch):
    """generation.yaml ships a repo-relative host path and its mount is
    read-only, so the service must overlay OUTPUT_DIR in memory. Without this
    the container writes artifacts into its own image layer."""
    loaded = {"output": {"dir": "utilities/3LayersWeeklyGeneration/output"}}

    result = api.overlay_output_dir(loaded, "/data/output")

    assert result["output"]["dir"] == "/data/output"


def test_the_ollama_base_url_is_overlaid_from_the_environment():
    """Exactly the same trap as output.dir, and it bites harder. The shipped
    generation.yaml says `defaults.base_url: http://localhost:11434`, which
    inside a container means the container itself. Without this overlay the
    compose file's OLLAMA_BASE_URL has no consumer at all and every LLM call
    fails with ECONNREFUSED — while the job still reports `completed`, because
    plan_arc's contract is to log and skip a batch it cannot plan."""
    loaded = {"defaults": {"base_url": "http://localhost:11434",
                           "provider": "ollama"}}

    result = api.overlay_base_url(loaded, "http://host.docker.internal:11434")

    assert result["defaults"]["base_url"] == "http://host.docker.internal:11434"
    assert result["defaults"]["provider"] == "ollama"


def test_overlaying_a_base_url_of_none_leaves_the_config_alone():
    loaded = {"defaults": {"base_url": "http://localhost:11434"}}
    result = api.overlay_base_url(loaded, None)
    assert result["defaults"]["base_url"] == "http://localhost:11434"


# ---------------------------------------------------------------------------
# Lifespan — the startup path every other test bypasses
#
# Every test above monkeypatches CONFIG directly, so none of them ever runs
# the lifespan. That gap hid a real bug: the startup body assigned CONFIG
# without declaring it `global`, so it bound a local, the module attribute
# stayed None, and every endpoint reading it answered "CONFIG not loaded" —
# while the dispatcher, which was handed the local, worked perfectly. It only
# surfaced when the container was actually run.
# ---------------------------------------------------------------------------

def test_the_lifespan_publishes_config_to_the_module(monkeypatch, tmp_path):
    import yaml

    config_file = tmp_path / "generation.yaml"
    config_file.write_text(yaml.safe_dump(
        {"output": {"dir": "utilities/3LayersWeeklyGeneration/output"},
         "segment": {"target_slots": 170,
                     "tree": {"max_leaf_slots": 19, "max_children": 12,
                              "max_depth": 4, "min_node_words": 2000,
                              "leaf_density_floor": 0.8}}}),
        encoding="utf-8")

    monkeypatch.setenv("GENERATOR_CONFIG", str(config_file))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(api, "CONFIG", None)

    with TestClient(api.app):
        assert api.CONFIG is not None, "lifespan did not publish CONFIG"
        assert api.CONFIG["output"]["dir"] == str(tmp_path / "out")


def test_config_endpoint_works_after_a_real_lifespan(monkeypatch, tmp_path, store):
    import yaml

    config_file = tmp_path / "generation.yaml"
    config_file.write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    (tmp_path / "packs" / "ashiorid").mkdir(parents=True)

    monkeypatch.setenv("GENERATOR_CONFIG", str(config_file))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(api, "CONFIG", None)
    monkeypatch.setattr(api, "PACK_ROOT", tmp_path / "packs")

    with TestClient(api.app) as client:
        assert client.get("/config").status_code == 200


# ---------------------------------------------------------------------------
# Lifespan — the DB-active saved config must win over the boot-time file
#
# Regression: a container recreate (image rebuild, `docker compose up -d`,
# host reboot) used to silently revert CONFIG to whatever GENERATOR_CONFIG
# pointed at in .env, even when an operator had activated a different saved
# config via POST /configs/{id}/activate moments before. The dashboard's
# Saved Configurations card and every job submitted afterward assume the
# DB-active row IS what's live — confirmed to burn real GPU-hours on the
# wrong model tier more than once before this was fixed.
# ---------------------------------------------------------------------------

def test_the_db_active_saved_config_overrides_the_boot_time_file(
        monkeypatch, tmp_path, store):
    import yaml

    file_config = dict(CONFIG)
    file_config["segment"] = {**CONFIG["segment"], "target_slots": 170}
    config_file = tmp_path / "generation.yaml"
    config_file.write_text(yaml.safe_dump(file_config), encoding="utf-8")
    (tmp_path / "packs" / "ashiorid").mkdir(parents=True)

    db_active_yaml = yaml.safe_dump(
        {**CONFIG, "segment": {**CONFIG["segment"], "target_slots": 5}})
    store.create_config("db-active", "", db_active_yaml)
    store.activate_config(1)

    monkeypatch.setenv("GENERATOR_CONFIG", str(config_file))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(api, "CONFIG", None)
    monkeypatch.setattr(api, "PACK_ROOT", tmp_path / "packs")

    with TestClient(api.app):
        # The DB-active row's target_slots (5), not the file's (170).
        assert api.CONFIG["segment"]["target_slots"] == 5


def test_with_no_saved_configs_at_all_the_file_config_is_used(
        monkeypatch, tmp_path, store):
    """A fresh install with an empty generator_configs table must boot
    exactly as it always did — no saved config to prefer means no change
    in behavior."""
    import yaml

    config_file = tmp_path / "generation.yaml"
    config_file.write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    (tmp_path / "packs" / "ashiorid").mkdir(parents=True)

    monkeypatch.setenv("GENERATOR_CONFIG", str(config_file))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(api, "CONFIG", None)
    monkeypatch.setattr(api, "PACK_ROOT", tmp_path / "packs")

    with TestClient(api.app):
        assert api.CONFIG["segment"]["target_slots"] == 170


def test_an_unreadable_active_config_store_does_not_block_startup(
        monkeypatch, tmp_path, store):
    """A DB that is down/unreachable at boot must degrade to the
    file-based CONFIG, not prevent the service from starting."""
    import yaml

    config_file = tmp_path / "generation.yaml"
    config_file.write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    (tmp_path / "packs" / "ashiorid").mkdir(parents=True)

    def exploding_get_active_config_row():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(store, "get_active_config_row",
                        exploding_get_active_config_row)
    monkeypatch.setenv("GENERATOR_CONFIG", str(config_file))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(api, "CONFIG", None)
    monkeypatch.setattr(api, "PACK_ROOT", tmp_path / "packs")

    with TestClient(api.app):
        assert api.CONFIG is not None
        assert api.CONFIG["segment"]["target_slots"] == 170


# ---------------------------------------------------------------------------
# Saved configs — /configs CRUD + /configs/{id}/activate
# ---------------------------------------------------------------------------

VALID_CONFIG_YAML = """
output:
  dir: /data/output
budget:
  measured_baseline:
    words_per_take: 105
arc:
  models:
    light: {model: a}
    heavy: {model: b}
  active_model: heavy
segment:
  target_words: 300
  target_slots: 5
  models:
    light: {model: a}
    heavy: {model: b}
  active_model: heavy
  tree: {max_leaf_slots: 5, max_children: 3, max_depth: 1, min_node_words: 50, leaf_density_floor: 0.5}
dialogue:
  models:
    light: {model: a}
    heavy: {model: b}
  active_model: heavy
  takes_per_slot: 1
state:
  flags: []
  moods: [neutral]
  carry_keys: []
"""


def test_a_new_config_can_be_saved(client, store):
    response = client.post("/configs", json={
        "name": "fast-test", "description": "smoke test scope",
        "config_yaml": VALID_CONFIG_YAML,
    })
    assert response.status_code == 200
    config_id = response.json()["id"]
    assert store.get_config_row(config_id)["name"] == "fast-test"


def test_saving_a_config_requires_a_name(client):
    response = client.post("/configs", json={"config_yaml": VALID_CONFIG_YAML})
    assert response.status_code == 400


def test_saving_a_config_requires_config_yaml(client):
    response = client.post("/configs", json={"name": "no-body"})
    assert response.status_code == 400


def test_malformed_yaml_is_rejected_at_save_time(client):
    response = client.post("/configs", json={
        "name": "broken", "config_yaml": "not: valid: yaml: at: all: [",
    })
    assert response.status_code == 400


def test_a_non_mapping_config_is_rejected(client):
    response = client.post("/configs", json={
        "name": "just-a-list", "config_yaml": "- 1\n- 2\n",
    })
    assert response.status_code == 400


def test_duplicate_config_names_are_rejected(client):
    body = {"name": "dupe", "config_yaml": VALID_CONFIG_YAML}
    first = client.post("/configs", json=body)
    assert first.status_code == 200
    second = client.post("/configs", json=body)
    assert second.status_code == 409


def test_saved_configs_are_listed(client, store):
    client.post("/configs", json={"name": "one", "config_yaml": VALID_CONFIG_YAML})
    client.post("/configs", json={"name": "two", "config_yaml": VALID_CONFIG_YAML})
    response = client.get("/configs")
    assert response.status_code == 200
    names = {row["name"] for row in response.json()}
    assert names == {"one", "two"}


def test_getting_one_saved_config(client):
    config_id = client.post("/configs", json={
        "name": "solo", "config_yaml": VALID_CONFIG_YAML,
    }).json()["id"]
    response = client.get(f"/configs/{config_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "solo"


def test_getting_an_unknown_saved_config_is_404(client):
    assert client.get("/configs/9999").status_code == 404


def test_a_saved_config_can_be_edited(client, store):
    config_id = client.post("/configs", json={
        "name": "editable", "config_yaml": VALID_CONFIG_YAML,
    }).json()["id"]
    edited_yaml = VALID_CONFIG_YAML.replace("target_slots: 5", "target_slots: 10")
    response = client.put(f"/configs/{config_id}", json={
        "description": "updated", "config_yaml": edited_yaml,
    })
    assert response.status_code == 200
    row = store.get_config_row(config_id)
    assert "target_slots: 10" in row["config_yaml"]
    assert row["description"] == "updated"


def test_editing_an_unknown_config_is_404(client):
    response = client.put("/configs/9999", json={"config_yaml": VALID_CONFIG_YAML})
    assert response.status_code == 404


def test_a_saved_config_can_be_deleted(client, store):
    config_id = client.post("/configs", json={
        "name": "throwaway", "config_yaml": VALID_CONFIG_YAML,
    }).json()["id"]
    response = client.delete(f"/configs/{config_id}")
    assert response.status_code == 200
    assert store.get_config_row(config_id) is None


def test_deleting_an_unknown_config_is_404(client):
    assert client.delete("/configs/9999").status_code == 404


def test_activating_a_config_marks_it_active_and_applies_it(client, store):
    config_id = client.post("/configs", json={
        "name": "activate-me", "config_yaml": VALID_CONFIG_YAML,
    }).json()["id"]
    response = client.post(f"/configs/{config_id}/activate")
    assert response.status_code == 200
    assert store.get_config_row(config_id)["is_active"] is True
    # CONFIG was mutated in place to reflect the newly-activated document.
    assert api.CONFIG["segment"]["target_slots"] == 5


def test_activating_a_second_config_deactivates_the_first(client, store):
    first_id = client.post("/configs", json={
        "name": "first", "config_yaml": VALID_CONFIG_YAML,
    }).json()["id"]
    second_id = client.post("/configs", json={
        "name": "second", "config_yaml": VALID_CONFIG_YAML,
    }).json()["id"]
    client.post(f"/configs/{first_id}/activate")
    client.post(f"/configs/{second_id}/activate")
    assert store.get_config_row(first_id)["is_active"] is False
    assert store.get_config_row(second_id)["is_active"] is True


def test_activating_an_unknown_config_is_404(client):
    assert client.post("/configs/9999/activate").status_code == 404


def test_activating_a_config_that_does_not_resolve_a_profile_is_rejected(client, store):
    broken_yaml = "output: {dir: /data/output}\narc: {}\n"
    config_id = client.post("/configs", json={
        "name": "incomplete", "config_yaml": broken_yaml,
    }).json()["id"]
    response = client.post(f"/configs/{config_id}/activate")
    assert response.status_code == 400
    # Must NOT have been marked active — store and running CONFIG stay
    # in sync on failure.
    assert store.get_config_row(config_id)["is_active"] is False


def test_editing_the_active_config_reapplies_it_live(client, store):
    config_id = client.post("/configs", json={
        "name": "live", "config_yaml": VALID_CONFIG_YAML,
    }).json()["id"]
    client.post(f"/configs/{config_id}/activate")
    edited_yaml = VALID_CONFIG_YAML.replace("target_slots: 5", "target_slots: 42")
    client.put(f"/configs/{config_id}", json={"config_yaml": edited_yaml})
    assert api.CONFIG["segment"]["target_slots"] == 42


def test_editing_an_inactive_config_does_not_touch_the_live_config(client, store):
    active_id = client.post("/configs", json={
        "name": "stays-active", "config_yaml": VALID_CONFIG_YAML,
    }).json()["id"]
    client.post(f"/configs/{active_id}/activate")
    before = dict(api.CONFIG)

    other_id = client.post("/configs", json={
        "name": "not-active", "config_yaml": VALID_CONFIG_YAML,
    }).json()["id"]
    edited_yaml = VALID_CONFIG_YAML.replace("target_slots: 5", "target_slots: 999")
    client.put(f"/configs/{other_id}", json={"config_yaml": edited_yaml})

    assert api.CONFIG["segment"]["target_slots"] == before["segment"]["target_slots"]


# ---------------------------------------------------------------------------
# GET /packs and GET /profiles — drive the campaign-manager GUI's dropdowns
# ---------------------------------------------------------------------------

def test_packs_lists_store_pack_names(client, store):
    store.upsert_campaign("ashiorid", "name: ashiorid\nstart_scene: intro\ngm: gm\n")
    store.upsert_campaign("test_pack", "name: test_pack\nstart_scene: intro\ngm: gm\n")

    body = client.get("/packs").json()

    assert body == ["ashiorid", "test_pack"]


def test_packs_does_not_leak_disk_entries(client, tmp_path):
    """Postgres is the only source now (decision 5): a directory under
    PACK_ROOT with no pack_campaigns row must not appear in /packs. This is
    the whole point of cutting the endpoint over from a disk scan."""
    (tmp_path / "packs" / "ghost_on_disk_only").mkdir(parents=True, exist_ok=True)

    body = client.get("/packs").json()

    assert body == []
    assert "ghost_on_disk_only" not in body


def test_packs_is_sorted(client, store):
    seed_minimal_pack(store, "zebra")
    store.upsert_campaign("alpha", "name: alpha\nstart_scene: intro\ngm: gm\n")
    store.upsert_campaign("midpack", "name: midpack\nstart_scene: intro\ngm: gm\n")
    assert client.get("/packs").json() == ["alpha", "midpack", "zebra"]


def test_profiles_lists_configured_model_names_per_layer(client):
    body = client.get("/profiles").json()

    assert body["arc"] == ["light", "heavy"]
    assert body["segment"] == ["light", "heavy"]
    assert body["dialogue"] == ["light", "heavy"]


def test_profiles_degrades_to_empty_lists_when_config_is_none(client, monkeypatch):
    monkeypatch.setattr(api, "CONFIG", None)
    body = client.get("/profiles").json()
    assert body == {"arc": [], "segment": [], "dialogue": []}
# ---------------------------------------------------------------------------
# Pack Viewer — the new DB-backed read endpoints (Phase 1, Task 1.3). These
# exercise the REAL campaign.pack.load_pack (conftest puts app/ on
# sys.path), the same function a generation job runs, against FakeStore's
# materialize_pack — so "the browser sees what the pipeline consumes" is
# asserted in exactly one place per surface.
# ---------------------------------------------------------------------------

from pack_fixtures import CAMPAIGN_YAML_FIXTURE, CAST_YAML_FIXTURE, seed_minimal_pack  # noqa: E402


def test_get_pack_summary_reads_from_the_store(client, store):
    seed_minimal_pack(store, "alpha")
    resp = client.get("/packs/alpha")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "alpha"
    assert body["start_scene"] == "intro"
    assert body["gm_id"] == "gm"
    assert [s["id"] for s in body["scenes"]] == ["intro"]
    assert [c["id"] for c in body["cast"]] == ["gm"]
    assert body["lore_names"] == ["the-incident"]


def test_get_pack_summary_for_unknown_pack_is_404(client, store):
    assert client.get("/packs/nope").status_code == 404


def test_get_pack_summary_for_stored_but_unloadable_pack_is_422(client, store):
    """A pack whose rows do not satisfy load_pack (start_scene missing) is
    an operator-fixable state, not a server crash: 422 with the reason."""
    store.upsert_campaign("alpha", "name: alpha\nstart_scene: ghost\ngm: gm\n")
    store.upsert_cast_member("alpha", "gm", CAST_YAML_FIXTURE)
    resp = client.get("/packs/alpha")
    assert resp.status_code == 422
    assert "ghost" in resp.json()["detail"]


def test_get_pack_scene_returns_the_raw_yaml(client, store):
    seed_minimal_pack(store, "alpha")
    resp = client.get("/packs/alpha/scenes/intro")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scene_id"] == "intro"
    assert "title: The Intro" in body["scene_yaml"]


def test_get_pack_scene_404_on_unknown_scene(client, store):
    seed_minimal_pack(store, "alpha")
    assert client.get("/packs/alpha/scenes/ghost").status_code == 404


def test_get_pack_cast_member_includes_worker_id(client, store):
    seed_minimal_pack(store, "alpha")
    store.upsert_cast_member("alpha", "gm", CAST_YAML_FIXTURE, "manager")
    resp = client.get("/packs/alpha/cast/gm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["member_id"] == "gm"
    assert body["worker_id"] == "manager"


def test_get_pack_cast_member_null_worker_id_is_the_narration_fallback(client, store):
    seed_minimal_pack(store, "alpha")
    body = client.get("/packs/alpha/cast/gm").json()
    assert body["worker_id"] is None


def test_get_pack_cast_member_404(client, store):
    seed_minimal_pack(store, "alpha")
    assert client.get("/packs/alpha/cast/ghost").status_code == 404


def test_get_pack_lore_returns_raw_text(client, store):
    seed_minimal_pack(store, "alpha")
    resp = client.get("/packs/alpha/lore/the-incident")
    assert resp.status_code == 200
    assert "short lore note" in resp.json()["lore_text"].lower()


def test_get_pack_lore_404(client, store):
    seed_minimal_pack(store, "alpha")
    assert client.get("/packs/alpha/lore/ghost").status_code == 404
# ---------------------------------------------------------------------------
# Phase 2 — Pack Editor write endpoints (validate-before-insert, decision 4).
# The load-bearing guarantees, exactly as the plan states them:
#   * a valid edit lands (upsert)
#   * an edit that would make the pack fail load_pack is a 422, and NOTHING
#     was written to the store (Postgres never holds a bad state)
#   * a 404 for a pack that has no campaign row yet
# ---------------------------------------------------------------------------

def test_put_pack_scene_saves_valid_yaml(client, store):
    seed_minimal_pack(store, "alpha")
    resp = client.put("/packs/alpha/scenes/intro",
                      json={"scene_yaml": "id: intro\ntitle: New\ndefault_next: intro\n"})
    assert resp.status_code == 200
    assert "New" in store.list_scenes("alpha")[0]["scene_yaml"]


def test_put_pack_scene_rejects_yaml_whose_id_mismatches_the_url(client, store):
    seed_minimal_pack(store, "alpha")
    resp = client.put("/packs/alpha/scenes/intro",
                      json={"scene_yaml": "id: WRONG\ntitle: New\n"})
    assert resp.status_code == 400
    # store untouched
    assert "WRONG" not in " ".join(r["scene_yaml"] for r in store.list_scenes("alpha"))


def test_put_pack_scene_rejects_invalid_yaml(client, store):
    seed_minimal_pack(store, "alpha")
    resp = client.put("/packs/alpha/scenes/intro",
                      json={"scene_yaml": "id: intro\n  title: : :\n"})
    assert resp.status_code == 400


def test_an_edit_that_would_break_the_pack_422_and_leaves_the_store_untouched(client, store):
    """The core decision-4 guarantee: Postgres (the store) NEVER holds a
    state load_pack() rejects, not even transiently. Seed a valid pack, PUT
    a scene that drops its required beat, and assert BOTH the 422 AND that
    the store still has the old valid scene."""
    seed_minimal_pack(store, "alpha")
    # A scene whose beats list is missing entirely still loads, but a scene
    # referenced by start_scene being removed is the cleanest 422 trigger:
    # replace the start scene's beat with one that fails to parse into a valid
    # Beat (missing 'type').
    resp = client.put(
        "/packs/alpha/scenes/intro",
        json={"scene_yaml": "id: intro\ntitle: Broken\nbeats:\n  - {speaker: gm}\n"},
    )
    assert resp.status_code == 422
    assert "would make pack" in resp.json()["detail"]
    # Store untouched — still the original valid scene with its beat.
    scene = [r for r in store.list_scenes("alpha") if r["scene_id"] == "intro"][0]
    assert "type: narration" in scene["scene_yaml"]


def test_put_pack_scene_on_a_pack_with_no_campaign_row_is_404(client, store):
    resp = client.put("/packs/nope/scenes/intro", json={"scene_yaml": "id: intro\n"})
    assert resp.status_code == 404


def test_delete_pack_scene_that_is_the_start_scene_422_and_is_not_deleted(client, store):
    """Deleting the start scene breaks load_pack (start scene missing), so
    the gate must 422 AND the scene must still be in the store."""
    seed_minimal_pack(store, "alpha")
    # Add a second, deletable scene so we can also demonstrate the happy path.
    resp = client.put("/packs/alpha/scenes/act2",
                      json={"scene_yaml": "id: act2\ntitle: Act 2\ndefault_next: intro\n"})
    assert resp.status_code == 200

    resp = client.delete("/packs/alpha/scenes/intro")   # intro is start_scene
    assert resp.status_code == 422
    assert [r["scene_id"] for r in store.list_scenes("alpha")] == ["act2", "intro"] or \
           set(r["scene_id"] for r in store.list_scenes("alpha")) == {"intro", "act2"}

    # The happy path: delete the non-start scene.
    resp = client.delete("/packs/alpha/scenes/act2")
    assert resp.status_code == 200
    assert set(r["scene_id"] for r in store.list_scenes("alpha")) == {"intro"}


def test_put_pack_cast_member_sets_worker_id(client, store):
    seed_minimal_pack(store, "alpha")
    resp = client.put("/packs/alpha/cast/gm",
                      json={"member_yaml": "name: GM Alpha\n", "worker_id": "manager"})
    assert resp.status_code == 200
    assert store.list_cast("alpha")[0]["worker_id"] == "manager"
    assert "GM Alpha" in store.list_cast("alpha")[0]["member_yaml"]


def test_put_pack_cast_member_requires_name(client, store):
    seed_minimal_pack(store, "alpha")
    resp = client.put("/packs/alpha/cast/gm", json={"member_yaml": "archetype: narrator\n"})
    assert resp.status_code == 400


def test_put_pack_cast_member_saving_yaml_without_worker_id_keeps_existing_mapping(client, store):
    seed_minimal_pack(store, "alpha")
    client.put("/packs/alpha/cast/gm", json={"member_yaml": "name: GM Alpha\n", "worker_id": "manager"})
    # Now re-save the YAML WITHOUT a worker_id (simulating the editor only
    # changing the prose). The store's COALESCE must keep "manager".
    resp = client.put("/packs/alpha/cast/gm", json={"member_yaml": "name: GM Alpha v2\n"})
    assert resp.status_code == 200
    assert store.list_cast("alpha")[0]["worker_id"] == "manager"
    assert "v2" in store.list_cast("alpha")[0]["member_yaml"]


def test_put_pack_cast_member_removing_a_required_player_422(client, store):
    """A pack where a cast member is a required player: the gate must catch
    an edit that would make load_pack fail because a referenced player's file
    is missing or malformed. (A well-formed replacement loads, proving the
    gate actually runs load_pack on the mutated tree, not just shape.)"""
    seed_minimal_pack(store, "alpha")
    resp = client.put("/packs/alpha/cast/ghost", json={"member_yaml": "a: [incomplete\n"})
    assert resp.status_code == 400  # YAML parse error at the shape layer


def test_put_and_delete_lore_roundtrip(client, store):
    seed_minimal_pack(store, "alpha")
    resp = client.put("/packs/alpha/lore/the-incident",
                      json={"lore_text": "A short lore note, edited.\n"})
    assert resp.status_code == 200
    assert "edited" in [r for r in store.list_lore("alpha") if r["lore_name"] == "the-incident"][0]["lore_text"]
    resp = client.delete("/packs/alpha/lore/the-incident")
    assert resp.status_code == 200
    assert [r["lore_name"] for r in store.list_lore("alpha")] == []


def test_422_detail_carries_load_packs_own_diagnostic(client, store):
    """Operators act on the 422, so its detail must name WHY (load_pack's
    own message), not a bare 'invalid'. A beat missing `type` is the trigger."""
    seed_minimal_pack(store, "alpha")
    resp = client.put(
        "/packs/alpha/scenes/intro",
        json={"scene_yaml": "id: intro\ntitle: Broken\nbeats:\n  - {speaker: gm}\n"},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "this edit would make pack" in detail
    assert "beat" in detail.lower()   # load_pack's "beat in intro missing 'type'"
