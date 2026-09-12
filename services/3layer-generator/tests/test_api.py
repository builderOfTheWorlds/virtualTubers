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
