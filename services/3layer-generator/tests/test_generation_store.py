"""Acceptance tests for services/3layer-generator/generation_store.py.

These run against a REAL Postgres — the store is nothing but SQL, so a mocked
connection would only assert that the module calls the methods the test already
told it to call. Point POSTGRES_* at a throwaway database; the suite creates and
truncates its own two tables and touches nothing else.

Skipped when no database is configured, so a checkout without one still runs
the rest of the suite green.
"""
import datetime
import os

import pytest

import generation_store


pytestmark = pytest.mark.skipif(
    not generation_store.available(),
    reason="no Postgres configured (POSTGRES_* env unset or psycopg2 missing)",
)


@pytest.fixture(autouse=True)
def clean_tables():
    """Every test starts from empty tables and owns the whole database."""
    generation_store.ensure_schema()
    conn = generation_store._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE generation_jobs, generation_artifacts;")
    finally:
        conn.close()
    yield


def _submit(pack="ashiorid", stage="segment", profile="heavy", params=None):
    return generation_store.submit({
        "pack": pack,
        "stage": stage,
        "profile": profile,
        "params": params if params is not None else {"segments": ["seg-01"]},
    })


# --------------------------------------------------------------------------
# schema and identity
# --------------------------------------------------------------------------

def test_ensure_schema_is_idempotent():
    generation_store.ensure_schema()
    generation_store.ensure_schema()


def test_new_job_id_is_unique_and_prefixed():
    ids = {generation_store.new_job_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(i.startswith("job_") for i in ids)


# --------------------------------------------------------------------------
# submit / get / list
# --------------------------------------------------------------------------

def test_submit_returns_id_and_queues_the_job():
    job_id = _submit()
    row = generation_store.get(job_id)

    assert row["id"] == job_id
    assert row["status"] == "queued"
    assert row["pack"] == "ashiorid"
    assert row["stage"] == "segment"
    assert row["profile"] == "heavy"
    assert row["params"] == {"segments": ["seg-01"]}
    assert row["cancel_requested"] is False
    assert row["progress"] is None
    assert row["result"] is None
    assert row["error"] is None
    assert row["started_at"] is None
    assert row["finished_at"] is None


def test_submit_defaults_profile_and_params_when_absent():
    job_id = generation_store.submit({"pack": "ashiorid", "stage": "arc"})
    row = generation_store.get(job_id)
    assert row["profile"] == ""
    assert row["params"] == {}


def test_get_returns_none_for_unknown_id():
    assert generation_store.get("job_does_not_exist") is None


def test_timestamps_are_iso_strings_not_datetimes():
    """The API serializes these straight to JSON — datetimes would blow up."""
    row = generation_store.get(_submit())
    assert isinstance(row["created_at"], str)
    # Parses as ISO-8601.
    datetime.datetime.fromisoformat(row["created_at"])


def test_list_jobs_returns_newest_first():
    first = _submit(stage="arc")
    second = _submit(stage="segment")
    third = _submit(stage="dialogue")

    ids = [r["id"] for r in generation_store.list_jobs()]
    assert ids == [third, second, first]


@pytest.mark.parametrize("filters,expected_stages", [
    ({"stage": "arc"}, ["arc"]),
    ({"status": "queued"}, ["dialogue", "segment", "arc"]),
    ({"pack": "nothing-here"}, []),
    ({"stage": "arc", "pack": "ashiorid"}, ["arc"]),
])
def test_list_jobs_filters(filters, expected_stages):
    _submit(stage="arc")
    _submit(stage="segment")
    _submit(stage="dialogue")

    rows = generation_store.list_jobs(**filters)
    assert [r["stage"] for r in rows] == expected_stages


def test_list_jobs_with_no_filters_returns_everything():
    _submit(pack="a")
    _submit(pack="b")
    assert len(generation_store.list_jobs()) == 2


# --------------------------------------------------------------------------
# progress and heartbeat
# --------------------------------------------------------------------------

def test_update_progress_stores_document_and_sets_heartbeat():
    job_id = _submit()
    assert generation_store.get(job_id)["heartbeat_at"] is None

    generation_store.update_progress(job_id, {"done": 4, "total": 9,
                                              "last_node": "seg-01-n2"})

    row = generation_store.get(job_id)
    assert row["progress"] == {"done": 4, "total": 9, "last_node": "seg-01-n2"}
    assert row["heartbeat_at"] is not None


def test_update_progress_overwrites_previous_document():
    job_id = _submit()
    generation_store.update_progress(job_id, {"done": 1, "total": 9})
    generation_store.update_progress(job_id, {"done": 7, "total": 9})
    assert generation_store.get(job_id)["progress"] == {"done": 7, "total": 9}


def test_update_progress_on_unknown_job_is_a_noop():
    generation_store.update_progress("job_nope", {"done": 1, "total": 2})


# --------------------------------------------------------------------------
# start / finish lifecycle
# --------------------------------------------------------------------------

def test_mark_running_sets_status_and_started_at():
    job_id = _submit()
    assert generation_store.mark_running(job_id) is True

    row = generation_store.get(job_id)
    assert row["status"] == "running"
    assert row["started_at"] is not None


def test_mark_running_refuses_a_job_that_is_not_queued():
    job_id = _submit()
    generation_store.mark_running(job_id)
    assert generation_store.mark_running(job_id) is False


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_finish_sets_terminal_status_and_finished_at(status):
    job_id = _submit()
    generation_store.mark_running(job_id)

    assert generation_store.finish(job_id, status, result={"nodes": 9}) is True

    row = generation_store.get(job_id)
    assert row["status"] == status
    assert row["result"] == {"nodes": 9}
    assert row["finished_at"] is not None


def test_finish_records_the_error_string():
    job_id = _submit()
    generation_store.mark_running(job_id)
    generation_store.finish(job_id, "failed", error="Ollama unreachable")

    row = generation_store.get(job_id)
    assert row["status"] == "failed"
    assert row["error"] == "Ollama unreachable"


def test_finish_is_a_noop_on_an_already_terminal_job():
    """A cancelled job that then raises must not be relabelled 'failed'."""
    job_id = _submit()
    generation_store.mark_running(job_id)
    generation_store.finish(job_id, "cancelled", result={"partial": True})

    assert generation_store.finish(job_id, "failed", error="too late") is False

    row = generation_store.get(job_id)
    assert row["status"] == "cancelled"
    assert row["error"] is None
    assert row["result"] == {"partial": True}


def test_finish_rejects_a_non_terminal_status():
    job_id = _submit()
    with pytest.raises(ValueError):
        generation_store.finish(job_id, "running")


# --------------------------------------------------------------------------
# cancellation — the finding-4 regression surface
# --------------------------------------------------------------------------

def test_request_cancel_sets_the_flag_on_a_queued_job():
    job_id = _submit()
    assert generation_store.request_cancel(job_id) is True
    assert generation_store.is_cancelled(job_id) is True


def test_request_cancel_sets_the_flag_on_a_running_job():
    job_id = _submit()
    generation_store.mark_running(job_id)
    assert generation_store.request_cancel(job_id) is True
    assert generation_store.is_cancelled(job_id) is True


def test_request_cancel_refuses_a_terminal_job():
    job_id = _submit()
    generation_store.mark_running(job_id)
    generation_store.finish(job_id, "completed", result={})

    assert generation_store.request_cancel(job_id) is False
    assert generation_store.is_cancelled(job_id) is False


def test_request_cancel_on_unknown_job_returns_false():
    assert generation_store.request_cancel("job_nope") is False


def test_is_cancelled_is_false_for_unknown_job():
    assert generation_store.is_cancelled("job_nope") is False


def test_progress_update_does_not_clobber_a_concurrent_cancel():
    """THE regression test for the read-modify-write race.

    The file-based draft read the whole record, mutated a dict and rewrote the
    file, so a progress write that started before a cancel landed would erase
    the flag and the job would run to completion. Column-level UPDATEs cannot
    do that. Interleave the two orders and assert the flag always survives.
    """
    job_id = _submit()
    generation_store.mark_running(job_id)

    generation_store.update_progress(job_id, {"done": 1, "total": 9})
    generation_store.request_cancel(job_id)
    generation_store.update_progress(job_id, {"done": 2, "total": 9})
    generation_store.update_progress(job_id, {"done": 3, "total": 9})

    assert generation_store.is_cancelled(job_id) is True
    assert generation_store.get(job_id)["progress"] == {"done": 3, "total": 9}
    assert generation_store.get(job_id)["cancel_requested"] is True


# --------------------------------------------------------------------------
# orphan reconciliation
# --------------------------------------------------------------------------

def test_reconcile_orphans_fails_running_jobs():
    orphan = _submit()
    generation_store.mark_running(orphan)

    assert generation_store.reconcile_orphans() == 1

    row = generation_store.get(orphan)
    assert row["status"] == "failed"
    assert "restart" in row["error"]
    assert row["finished_at"] is not None


def test_reconcile_orphans_leaves_queued_jobs_alone():
    """The dispatcher picks queued rows up normally — failing them would
    silently discard work the operator submitted."""
    queued = _submit()
    generation_store.reconcile_orphans()
    assert generation_store.get(queued)["status"] == "queued"


def test_reconcile_orphans_leaves_terminal_jobs_alone():
    done = _submit()
    generation_store.mark_running(done)
    generation_store.finish(done, "completed", result={"nodes": 3})

    generation_store.reconcile_orphans()

    row = generation_store.get(done)
    assert row["status"] == "completed"
    assert row["error"] is None


def test_reconcile_orphans_returns_zero_on_a_clean_boot():
    assert generation_store.reconcile_orphans() == 0


# --------------------------------------------------------------------------
# artifacts
# --------------------------------------------------------------------------

def test_upsert_artifact_then_load_round_trips_the_document():
    content = {"segments": [{"id": "seg-01", "order": 1}], "nested": {"a": [1, 2]}}
    generation_store.upsert_artifact("ashiorid", "arc_plan", "", content)

    assert generation_store.load_artifact("ashiorid", "arc_plan", "") == content


def test_upsert_artifact_is_idempotent_on_the_natural_key():
    generation_store.upsert_artifact("ashiorid", "brief", "seg-01", {"v": 1})
    generation_store.upsert_artifact("ashiorid", "brief", "seg-01", {"v": 2})

    assert generation_store.load_artifact("ashiorid", "brief", "seg-01") == {"v": 2}
    assert len(generation_store.list_artifacts("ashiorid")) == 1


def test_upsert_artifact_separates_kinds_and_segments():
    generation_store.upsert_artifact("ashiorid", "brief", "seg-01", {"k": "b1"})
    generation_store.upsert_artifact("ashiorid", "tree", "seg-01", {"k": "t1"})
    generation_store.upsert_artifact("ashiorid", "brief", "seg-02", {"k": "b2"})
    generation_store.upsert_artifact("other", "brief", "seg-01", {"k": "x"})

    assert len(generation_store.list_artifacts("ashiorid")) == 3
    assert len(generation_store.list_artifacts("other")) == 1
    assert generation_store.load_artifact("ashiorid", "tree", "seg-01") == {"k": "t1"}


def test_upsert_artifact_records_the_job_that_produced_it():
    job_id = _submit()
    generation_store.upsert_artifact("ashiorid", "brief", "seg-01", {"v": 1},
                                     job_id=job_id)

    rows = generation_store.list_artifacts("ashiorid")
    assert rows[0]["job_id"] == job_id


def test_load_artifact_returns_none_when_absent():
    assert generation_store.load_artifact("ashiorid", "brief", "nope") is None


def test_list_artifacts_returns_metadata_without_content():
    """The GUI's artifact browser lists hundreds of these; shipping every
    document body in the listing would be megabytes per call."""
    generation_store.upsert_artifact("ashiorid", "brief", "seg-01", {"big": "x" * 1000})

    rows = generation_store.list_artifacts("ashiorid")
    assert len(rows) == 1
    assert "content" not in rows[0]
    assert rows[0]["kind"] == "brief"
    assert rows[0]["segment_id"] == "seg-01"
    assert isinstance(rows[0]["updated_at"], str)


def test_list_artifacts_is_empty_for_unknown_pack():
    assert generation_store.list_artifacts("no-such-pack") == []
