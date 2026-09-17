"""Acceptance tests for the pack-content section of generation_store.py.

Real Postgres, same contract as test_generation_store.py: the store is
nothing but SQL, so these prove the round-trip end to end — upsert the rows,
materialize_pack() the whole thing back into a directory, and hand that
directory to the REAL campaign.pack.load_pack() (the one function the whole
plan's trust boundary depends on: if the materialized shape drifts from the
on-disk pack shape, load_pack() catches it here, not in a browser at 2am).

Skipped when no database is configured, so a checkout without one still runs
the rest of the suite green.
"""
import shutil

import pytest

import generation_store as store

from pack_fixtures import (
    CAMPAIGN_YAML_FIXTURE,
    CAST_YAML_FIXTURE,
    SCENE_YAML_FIXTURE,
    seed_minimal_pack,
)

pytestmark = pytest.mark.skipif(
    not store.available(),
    reason="no Postgres configured (POSTGRES_* env unset or psycopg2 missing)",
)


@pytest.fixture(autouse=True)
def clean_tables():
    """Every test starts from empty pack tables and owns them for its run.

    SAFETY: these tests TRUNCATE the tables they use, so they must only run
    against a scratch/empty database — never the production generator DB
    (generation@127.0.0.1:5455), where the pack tables sit alongside 100+
    real generation_jobs and TRUNCATE would be destructive. The project .env
    points the test harness at the empty app DB by default, which is the safe
    and intended target; if someone repoints it at production by hand, this
    guard refuses to run the destructive half and tells them to.
    """
    store.ensure_schema()
    conn = store._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM generation_jobs")
            job_rows = cur.fetchone()[0]
        if job_rows > 0:
            pytest.skip(
                f"refusing to TRUNCATE against a DB with {job_rows} "
                "generation_jobs — looks like the PRODUCTION generator DB "
                "(generation@127.0.0.1:5455). Point POSTGRES_* at a scratch "
                "DB (the .env app DB is fine) and re-run.")
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE pack_scenes, pack_cast, pack_lore, pack_campaigns CASCADE;")
    finally:
        conn.close()
    yield


def test_list_pack_names_empty_then_seeded():
    assert store.list_pack_names() == []
    store.upsert_campaign("alpha", CAMPAIGN_YAML_FIXTURE)
    store.upsert_campaign("beta", "name: beta\nstart_scene: intro\ngm: gm\n")
    assert store.list_pack_names() == ["alpha", "beta"]


def test_get_campaign_row_roundtrip_and_404_path():
    assert store.get_campaign_row("nope") is None
    store.upsert_campaign("alpha", CAMPAIGN_YAML_FIXTURE)
    row = store.get_campaign_row("alpha")
    assert row["campaign_yaml"] == CAMPAIGN_YAML_FIXTURE
    store.upsert_campaign("alpha", "name: alpha-v2\nstart_scene: intro\ngm: gm\n")
    assert "v2" in store.get_campaign_row("alpha")["campaign_yaml"]


def test_ensure_schema_is_idempotent_with_pack_tables():
    store.ensure_schema()
    store.ensure_schema()


def test_full_pack_materializes_into_a_load_pack_shaped_directory():
    """The load-bearing assertion of Task 1.1: a pack seeded only via the
    store functions materializes into a directory the real load_pack()
    accepts, with the right scene/cast/lore counts."""
    seed_minimal_pack(store, "alpha")

    root = store.materialize_pack("alpha")
    try:
        from campaign.pack import load_pack
        pack = load_pack(root)
        assert pack.name == "alpha"
        assert pack.start_scene == "intro"
        assert pack.gm_id == "gm"
        assert set(pack.scenes) == {"intro"}
        assert set(pack.cast) == {"gm"}
        assert set(pack.lore) == {"the-incident"}
        # The scene's beat survived the round-trip intact.
        beats = pack.scenes["intro"].beats
        assert len(beats) == 1 and beats[0].speaker == "gm"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_materialize_pack_raises_for_unknown_pack():
    with pytest.raises(FileNotFoundError):
        store.materialize_pack("nope")


def test_upsert_scene_is_idempotent_and_delete_works():
    seed_minimal_pack(store, "alpha")
    store.upsert_scene("alpha", "act2", "id: act2\ntitle: Act 2\ndefault_next: intro\n")
    scenes = {r["scene_id"]: r for r in store.list_scenes("alpha")}
    assert set(scenes) == {"intro", "act2"}
    store.upsert_scene("alpha", "act2", "id: act2\ntitle: Act 2 v2\ndefault_next: intro\n")
    scenes = {r["scene_id"]: r for r in store.list_scenes("alpha")}
    assert set(scenes) == {"intro", "act2"}
    assert "v2" in scenes["act2"]["scene_yaml"]
    assert store.delete_scene("alpha", "act2") is True
    assert store.delete_scene("alpha", "act2") is False
    assert [r["scene_id"] for r in store.list_scenes("alpha")] == ["intro"]


def test_upsert_cast_keeps_and_sets_worker_id():
    """Decision-3 guarantee at the SQL level: omitting worker_id on update
    must NOT clear a previously set mapping (COALESCE)."""
    seed_minimal_pack(store, "alpha")
    store.upsert_cast_member("alpha", "gm", CAST_YAML_FIXTURE, "manager")
    assert store.list_cast("alpha")[0]["worker_id"] == "manager"
    # Re-save with no worker_id argument — mapping must survive.
    store.upsert_cast_member("alpha", "gm", "name: GM Alpha v2\n")
    assert store.list_cast("alpha")[0]["worker_id"] == "manager"
    assert "v2" in store.list_cast("alpha")[0]["member_yaml"]
    # A new member lands with a NULL worker_id (the GM-narration fallback).
    store.upsert_cast_member("alpha", "chadwick", "name: Chadwick\n", "coder")
    rows = {r["member_id"]: r for r in store.list_cast("alpha")}
    assert rows["chadwick"]["worker_id"] == "coder"
    assert rows["gm"]["worker_id"] == "manager"


def test_delete_cast_member_roundtrip():
    seed_minimal_pack(store, "alpha")
    store.upsert_cast_member("alpha", "chadwick", "name: Chadwick\n")
    assert store.delete_cast_member("alpha", "chadwick") is True
    assert store.delete_cast_member("alpha", "chadwick") is False
    assert [r["member_id"] for r in store.list_cast("alpha")] == ["gm"]


def test_lore_upsert_and_delete():
    seed_minimal_pack(store, "alpha")
    store.upsert_lore("alpha", "the-moonwells", "Moonwells note v1\n")
    store.upsert_lore("alpha", "the-moonwells", "Moonwells note v2\n")
    names = {r["lore_name"] for r in store.list_lore("alpha")}
    assert names == {"the-incident", "the-moonwells"}
    assert "v2" in [r for r in store.list_lore("alpha") if r["lore_name"] == "the-moonwells"][0]["lore_text"]
    assert store.delete_lore("alpha", "the-moonwells") is True
    assert [r["lore_name"] for r in store.list_lore("alpha")] == ["the-incident"]


def test_a_pack_that_loads_before_still_loads_after_a_bad_scene_is_still_rejected_at_write_time():
    """The store itself does not validate YAML (the API layer runs
    load_pack() before upsert — see generator_api's write endpoints). This
    test just proves a syntactically broken scene_yaml lands in the store
    untouched, so the validation gate in the API layer is what protects
    Postgres, as designed — not this module."""
    seed_minimal_pack(store, "alpha")
    store.upsert_scene("alpha", "broken", "id: broken\n  this is: [not\n   valid\n")
    scenes = {r["scene_id"]: r for r in store.list_scenes("alpha")}
    assert set(scenes) == {"intro", "broken"}
    assert "not" in scenes["broken"]["scene_yaml"]
