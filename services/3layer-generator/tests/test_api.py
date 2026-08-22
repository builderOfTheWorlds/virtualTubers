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


@pytest.mark.parametrize("stage", ["arc", "segment", "dialogue", "all"])
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
