"""Acceptance tests for services/3layer-generator/runner.py — the dispatcher.

Named test_service_runner.py, not test_runner.py: pytest puts every test
directory on sys.path, and `app/test_runner.py` is a real module that several
repo-root tests import `run_pytest` from. A file named test_runner.py here
shadows it and breaks four unrelated suites at collection.

The runner is the only thing that turns a queued job row into GPU work. It
claims one job at a time, drives the right layer function, mirrors what that
function wrote to disk into Postgres, and records the outcome.

It is written as directly-callable functions rather than a bare thread so
these tests can drive one job to completion synchronously. A dispatcher whose
only entry point is `while True:` can only be tested by sleeping, and a test
that sleeps is a test that is flaky on a loaded machine.

The store is faked in-memory here — `test_generation_store.py` already proves
the real SQL. What matters at this level is WHICH store calls the runner makes
and in what order, because that ordering is what the operator sees in the GUI.
"""
import pathlib

import pytest
import yaml

import runner


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeStore:
    """In-memory stand-in with the same surface as generation_store."""

    def __init__(self):
        self.jobs = {}
        self.artifacts = {}
        self.calls = []
        self._seq = 0
        self.configs = {}
        self._config_seq = 0
        self.pack_campaigns = {}
        self.pack_scenes = {}
        self.pack_cast = {}
        self.pack_lore = {}

    def submit(self, record):
        self._seq += 1
        job_id = f"job_{self._seq:04d}"
        self.jobs[job_id] = {
            "id": job_id, "pack": record["pack"], "run": record.get("run"),
            "stage": record["stage"],
            "profile": record.get("profile", ""), "status": "queued",
            "params": record.get("params", {}), "progress": None,
            "result": None, "error": None, "cancel_requested": False,
            "created_at": f"t{self._seq}", "started_at": None,
            "finished_at": None, "heartbeat_at": None,
        }
        return job_id

    def get(self, job_id):
        return self.jobs.get(job_id)

    def list_jobs(self, pack=None, run=None, stage=None, status=None):
        rows = list(self.jobs.values())
        if pack is not None:
            rows = [r for r in rows if r["pack"] == pack]
        if run is not None:
            rows = [r for r in rows if r["run"] == run]
        if stage is not None:
            rows = [r for r in rows if r["stage"] == stage]
        if status is not None:
            rows = [r for r in rows if r["status"] == status]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

    def list_runs(self):
        return sorted({r["run"] for r in self.jobs.values() if r.get("run")})

    def mark_running(self, job_id):
        row = self.jobs.get(job_id)
        if row is None or row["status"] != "queued":
            return False
        row["status"] = "running"
        row["started_at"] = "t-start"
        self.calls.append(("mark_running", job_id))
        return True

    def update_progress(self, job_id, progress):
        if job_id in self.jobs:
            self.jobs[job_id]["progress"] = progress
            self.jobs[job_id]["heartbeat_at"] = "t-beat"
            self.calls.append(("update_progress", job_id))

    def update_llm_progress(self, job_id, llm_progress):
        if job_id in self.jobs:
            self.jobs[job_id]["llm_progress"] = llm_progress
            self.jobs[job_id]["heartbeat_at"] = "t-beat"
            self.calls.append(("update_llm_progress", job_id))

    def finish(self, job_id, status, result=None, error=None):
        row = self.jobs.get(job_id)
        if row is None or row["status"] in ("completed", "failed", "cancelled"):
            return False
        row["status"] = status
        row["result"] = result
        row["error"] = error
        row["finished_at"] = "t-end"
        self.calls.append(("finish", job_id, status))
        return True

    def request_cancel(self, job_id):
        row = self.jobs.get(job_id)
        if row is None or row["status"] not in ("queued", "running"):
            return False
        row["cancel_requested"] = True
        return True

    def is_cancelled(self, job_id):
        row = self.jobs.get(job_id)
        return bool(row and row["cancel_requested"])

    def reconcile_orphans(self):
        count = 0
        for row in self.jobs.values():
            if row["status"] == "running":
                row["status"] = "failed"
                row["error"] = "interrupted by service restart"
                row["finished_at"] = "t-end"
                count += 1
        self.calls.append(("reconcile_orphans", count))
        return count

    def upsert_artifact(self, pack, kind, segment_id, content, job_id=None):
        self._artifact_seq = getattr(self, "_artifact_seq", 0) + 1
        key = (pack, kind, segment_id)
        existing_id = self.artifacts.get(key, {}).get("_id") if isinstance(self.artifacts.get(key), dict) and "_id" in self.artifacts.get(key, {}) else None
        artifact_id = existing_id or self._artifact_seq
        self.artifacts[key] = {
            "_id": artifact_id, "id": artifact_id, "pack": pack, "kind": kind,
            "segment_id": segment_id, "content": content, "job_id": job_id,
            "updated_at": f"t{self._artifact_seq}",
        }
        self.calls.append(("upsert_artifact", kind, segment_id))

    def load_artifact(self, pack, kind, segment_id):
        row = self.artifacts.get((pack, kind, segment_id))
        return row["content"] if row else None

    def list_artifacts(self, pack):
        return [{"id": row["id"], "pack": row["pack"], "kind": row["kind"],
                  "segment_id": row["segment_id"], "job_id": row["job_id"],
                  "updated_at": row["updated_at"]}
                for (p, k, s), row in self.artifacts.items() if p == pack]

    def get_artifact(self, artifact_id):
        for row in self.artifacts.values():
            if row["id"] == artifact_id:
                return {k: v for k, v in row.items() if k != "_id"}
        return None

    # -- saved configs (generator_configs) --------------------------------

    def list_configs(self):
        rows = list(self.configs.values())
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

    def get_config_row(self, config_id):
        return self.configs.get(config_id)

    def get_active_config_row(self):
        for row in self.configs.values():
            if row["is_active"]:
                return row
        return None

    def create_config(self, name, description, config_yaml):
        for row in self.configs.values():
            if row["name"] == name:
                raise Exception(f"duplicate key value violates unique constraint: {name}")
        self._config_seq += 1
        config_id = self._config_seq
        self.configs[config_id] = {
            "id": config_id, "name": name, "description": description,
            "config_yaml": config_yaml, "is_active": False,
            "created_at": f"tc{config_id}", "updated_at": f"tc{config_id}",
        }
        return config_id

    def update_config(self, config_id, description, config_yaml):
        row = self.configs.get(config_id)
        if row is None:
            return False
        row["description"] = description
        row["config_yaml"] = config_yaml
        row["updated_at"] = f"tc{config_id}-updated"
        return True

    def delete_config(self, config_id):
        return self.configs.pop(config_id, None) is not None

    def activate_config(self, config_id):
        if config_id not in self.configs:
            return False
        for row in self.configs.values():
            row["is_active"] = False
        self.configs[config_id]["is_active"] = True
        return True

    # -- Campaign pack content (Pack Viewer / Editor) ---------------------
    # Same surface as generation_store's pack_* functions, in-memory.
    # materialize_pack writes a REAL directory so tests exercising it drive
    # the real campaign.pack.load_pack, not a fake of it.

    def _pack_init(self, pack_name):
        if pack_name not in self.pack_campaigns:
            self.pack_campaigns[pack_name] = None
            self.pack_scenes[pack_name] = {}
            self.pack_cast[pack_name] = {}
            self.pack_lore[pack_name] = {}

    def list_pack_names(self):
        return sorted(self.pack_campaigns)

    def get_campaign_row(self, pack_name):
        row = self.pack_campaigns.get(pack_name)
        if row is None:
            return None
        return dict(row, pack_name=pack_name)

    def list_scenes(self, pack_name):
        rows = sorted((dict(r, scene_id=r["scene_id"])
                       for r in self.pack_scenes.get(pack_name, {}).values()),
                      key=lambda r: r["scene_id"])
        return [
            {"id": i, "pack_name": pack_name, "scene_id": r["scene_id"],
             "scene_yaml": r["scene_yaml"]}
            for i, r in enumerate(rows)
        ]

    def list_cast(self, pack_name):
        rows = sorted(self.pack_cast.get(pack_name, {}).values(),
                      key=lambda r: r["member_id"])
        return [
            {"id": i, "pack_name": pack_name, "member_id": r["member_id"],
             "member_yaml": r["member_yaml"],
             "worker_id": r.get("worker_id")}
            for i, r in enumerate(rows)
        ]

    def list_lore(self, pack_name):
        rows = sorted(self.pack_lore.get(pack_name, {}).values(),
                      key=lambda r: r["lore_name"])
        return [
            {"id": i, "pack_name": pack_name, "lore_name": r["lore_name"],
             "lore_text": r["lore_text"]}
            for i, r in enumerate(rows)
        ]

    def materialize_pack(self, pack_name):
        import pathlib
        import tempfile
        campaign = self.pack_campaigns.get(pack_name)
        if campaign is None:
            raise FileNotFoundError(
                f"no pack named {pack_name!r} in the (fake) store")
        root = pathlib.Path(tempfile.mkdtemp(prefix=f"fake-pack-{pack_name}-"))
        (root / "campaign.yaml").write_text(campaign["campaign_yaml"],
                                            encoding="utf-8")
        (root / "cast").mkdir()
        for r in self.pack_cast.get(pack_name, {}).values():
            (root / "cast" / f"{r['member_id']}.yaml").write_text(
                r["member_yaml"], encoding="utf-8")
        (root / "scenes").mkdir()
        for r in self.pack_scenes.get(pack_name, {}).values():
            (root / "scenes" / f"{r['scene_id']}.yaml").write_text(
                r["scene_yaml"], encoding="utf-8")
        lore = list(self.pack_lore.get(pack_name, {}).values())
        if lore:
            (root / "lore").mkdir()
            for r in lore:
                (root / "lore" / f"{r['lore_name']}.md").write_text(
                    r["lore_text"], encoding="utf-8")
        return root

    def upsert_campaign(self, pack_name, campaign_yaml):
        row = self.pack_campaigns.get(pack_name)
        if row is None:
            self._pack_init(pack_name)
            self.pack_campaigns[pack_name] = {"campaign_yaml": campaign_yaml}
            self.pack_scenes[pack_name] = {}
            self.pack_cast[pack_name] = {}
            self.pack_lore[pack_name] = {}
        else:
            self.pack_campaigns[pack_name]["campaign_yaml"] = campaign_yaml

    def upsert_scene(self, pack_name, scene_id, scene_yaml):
        self._pack_init(pack_name)
        self.pack_scenes[pack_name].setdefault(scene_id, {})
        self.pack_scenes[pack_name][scene_id]["scene_id"] = scene_id
        self.pack_scenes[pack_name][scene_id]["scene_yaml"] = scene_yaml

    def delete_scene(self, pack_name, scene_id):
        return self.pack_scenes.get(pack_name, {}).pop(scene_id, None) is not None

    def upsert_cast_member(self, pack_name, member_id, member_yaml,
                           worker_id=None):
        import datetime
        self._pack_init(pack_name)
        row = self.pack_cast[pack_name].setdefault(member_id, {})
        row["member_id"] = member_id
        row["member_yaml"] = member_yaml
        if worker_id is not None:
            row["worker_id"] = worker_id
        row["updated_at"] = datetime.datetime.now(datetime.timezone.utc)

    def delete_cast_member(self, pack_name, member_id):
        return self.pack_cast.get(pack_name, {}).pop(member_id, None) is not None

    def upsert_lore(self, pack_name, lore_name, lore_text):
        self._pack_init(pack_name)
        row = self.pack_lore[pack_name].setdefault(lore_name, {})
        row["lore_name"] = lore_name
        row["lore_text"] = lore_text

    def delete_lore(self, pack_name, lore_name):
        return self.pack_lore.get(pack_name, {}).pop(lore_name, None) is not None


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def ctx(tmp_path, store):
    """Everything the runner needs, with the layer functions faked."""
    calls = {"arc": [], "segment": [], "dialogue": []}
    # dispatch_once() loads the source pack from pack_root/<pack>, so it has
    # to exist. (boot() no longer scans this directory — it discovers runs
    # to rehydrate from job rows via ctx.store.list_runs().)
    (tmp_path / "packs" / "ashiorid").mkdir(parents=True, exist_ok=True)
    # A sentinel so the fakes can prove they were handed the LOADED pack and
    # not the pack name. The runner needs both — the name keys artifact paths
    # and store rows, the object is what the layer functions consume — and
    # conflating them fails every real job with
    # "'str' object has no attribute 'genre'".
    loaded_pack = object()

    def fake_plan_arc(pack, config, llm, vocab, out_path, on_llm_progress=None):
        assert pack is loaded_pack, (
            "layer functions take the LOADED PACK object, not the pack name")
        calls["arc"].append(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(yaml.safe_dump({"segments": [{"id": "seg-01"}]}),
                            encoding="utf-8")
        return {"segments": [{"id": "seg-01"}]}

    def fake_plan_segment(pack, arc_segment, config, llm, vocab, out_path,
                          progress=None, cancel_check=None):
        assert pack is loaded_pack, (
            "layer functions take the LOADED PACK object, not the pack name")
        calls["segment"].append((arc_segment["id"], progress, cancel_check))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(yaml.safe_dump({"segment_id": arc_segment["id"],
                                            "slots": [{"slot_id": "s-1"}]}),
                            encoding="utf-8")
        out_path.with_name("tree.yaml").write_text(
            yaml.safe_dump({arc_segment["id"]: {"kind": "leaf"}}),
            encoding="utf-8")
        if progress is not None:
            progress(1, 1, {"node_id": arc_segment["id"]})
        return {"segment_id": arc_segment["id"], "slots": [{"slot_id": "s-1"}]}

    def fake_generate_dialogue(pack, segment_ids, config, llm, out_root,
                               progress=None, cancel_check=None):
        assert pack is loaded_pack, (
            "layer functions take the LOADED PACK object, not the pack name")
        calls["dialogue"].append((tuple(segment_ids), progress, cancel_check))
        return {"planned": 1, "written": 1, "failed": 0}

    return runner.Context(
        config={"output": {"dir": str(tmp_path / "out")},
                "segment": {"concurrency": 2},
                "arc": {}, "dialogue": {}},
        pack_root=tmp_path / "packs",
        output_root=tmp_path / "out",
        store=store,
        build_llm=lambda profile, layer: object(),
        load_pack=lambda pack_path: loaded_pack,
        build_vocab=lambda config, pack: object(),
        plan_arc=fake_plan_arc,
        plan_segment=fake_plan_segment,
        generate_dialogue=fake_generate_dialogue,
        _calls=calls,
    )


def queue(store, stage, run="ashiorid", **params):
    return store.submit({"pack": "ashiorid", "run": run, "stage": stage,
                         "profile": "heavy", "params": params})


# ---------------------------------------------------------------------------
# Boot — reconciliation and rehydration
# ---------------------------------------------------------------------------

def test_boot_fails_orphaned_running_jobs(store, ctx):
    orphan = queue(store, "segment")
    store.mark_running(orphan)

    runner.boot(ctx)

    row = store.get(orphan)
    assert row["status"] == "failed"
    assert "restart" in row["error"]


def test_boot_leaves_queued_jobs_for_the_dispatcher(store, ctx):
    queued = queue(store, "segment")
    runner.boot(ctx)
    assert store.get(queued)["status"] == "queued"


def test_boot_rehydrates_an_artifact_missing_from_disk(store, ctx):
    """The working directory is scratch; Postgres is the durable store. On a
    fresh volume the filesystem-based resume rule would otherwise re-plan
    everything the database already holds.

    boot() discovers which runs to rehydrate from job rows (`list_runs()`),
    not by scanning the source pack directory — so a job establishing the
    run has to exist, same as it would in production."""
    queue(store, "segment")
    store.upsert_artifact("ashiorid", "brief", "seg-01",
                          {"segment_id": "seg-01", "slots": []})

    runner.boot(ctx)

    written = ctx.output_root / "ashiorid" / "segments" / "seg-01" / "brief.yaml"
    assert written.exists()
    assert yaml.safe_load(written.read_text(encoding="utf-8"))["segment_id"] == "seg-01"


def test_boot_does_not_overwrite_an_artifact_already_on_disk(store, ctx):
    queue(store, "segment")
    target = ctx.output_root / "ashiorid" / "segments" / "seg-01" / "brief.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump({"segment_id": "seg-01", "local": True}),
                      encoding="utf-8")
    store.upsert_artifact("ashiorid", "brief", "seg-01", {"segment_id": "from-db"})

    runner.boot(ctx)

    assert yaml.safe_load(target.read_text(encoding="utf-8")).get("local") is True


def test_boot_rehydrates_two_runs_of_the_same_pack_independently(store, ctx):
    """Two arc jobs against the same source pack, each with its own
    timestamped run, must rehydrate into separate output directories."""
    queue(store, "arc", run="ashiorid_20260101_000000")
    queue(store, "arc", run="ashiorid_20260102_000000")
    store.upsert_artifact("ashiorid_20260101_000000", "brief", "seg-01",
                          {"segment_id": "seg-01", "slots": []})
    store.upsert_artifact("ashiorid_20260102_000000", "brief", "seg-01",
                          {"segment_id": "seg-01", "slots": []})

    runner.boot(ctx)

    first = ctx.output_root / "ashiorid_20260101_000000" / "segments" / "seg-01" / "brief.yaml"
    second = ctx.output_root / "ashiorid_20260102_000000" / "segments" / "seg-01" / "brief.yaml"
    assert first.exists()
    assert second.exists()


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------

def test_dispatch_once_returns_false_when_nothing_is_queued(store, ctx):
    assert runner.dispatch_once(ctx) is False


def test_dispatch_once_claims_the_oldest_queued_job_first(store, ctx):
    first = queue(store, "arc")
    queue(store, "arc")

    runner.dispatch_once(ctx)

    assert store.get(first)["status"] == "completed"


def test_a_claimed_job_is_marked_running_before_the_work_starts(store, ctx):
    queue(store, "arc")
    runner.dispatch_once(ctx)
    names = [c[0] for c in store.calls]
    assert names.index("mark_running") < names.index("finish")


# ---------------------------------------------------------------------------
# Stage dispatch
# ---------------------------------------------------------------------------

def test_arc_stage_calls_plan_arc(store, ctx):
    queue(store, "arc")
    runner.dispatch_once(ctx)
    assert len(ctx._calls["arc"]) == 1


def test_segment_stage_plans_each_requested_segment(store, ctx):
    queue(store, "segment", segments=["seg-01", "seg-02"])
    runner.dispatch_once(ctx)
    assert [c[0] for c in ctx._calls["segment"]] == ["seg-01", "seg-02"]


def test_dialogue_stage_calls_the_dialogue_entry_point(store, ctx):
    queue(store, "dialogue", segments=["seg-01"])
    runner.dispatch_once(ctx)
    assert ctx._calls["dialogue"] == [(("seg-01",), ctx._calls["dialogue"][0][1],
                                       ctx._calls["dialogue"][0][2])]


def test_stage_all_runs_arc_then_segment_then_dialogue(store, ctx):
    queue(store, "all", segments=["seg-01"])
    runner.dispatch_once(ctx)
    assert ctx._calls["arc"] and ctx._calls["segment"] and ctx._calls["dialogue"]


def test_an_unknown_stage_fails_the_job_rather_than_crashing_the_loop(store, ctx):
    job = queue(store, "nonsense")

    assert runner.dispatch_once(ctx) is True

    row = store.get(job)
    assert row["status"] == "failed"
    assert row["error"]


# ---------------------------------------------------------------------------
# The V9 mirror — artifacts into Postgres
# ---------------------------------------------------------------------------

def test_a_planned_segment_is_mirrored_into_the_store(store, ctx):
    queue(store, "segment", segments=["seg-01"])
    runner.dispatch_once(ctx)

    assert store.load_artifact("ashiorid", "brief", "seg-01") is not None
    assert store.load_artifact("ashiorid", "tree", "seg-01") is not None


def test_the_arc_plan_is_mirrored_with_an_empty_segment_id(store, ctx):
    queue(store, "arc")
    runner.dispatch_once(ctx)
    assert store.load_artifact("ashiorid", "arc_plan", "") is not None


def test_the_result_records_artifact_keys_not_filesystem_paths(store, ctx):
    """Paths are container-local scratch and meaningless to the GUI."""
    job = queue(store, "segment", segments=["seg-01"])
    runner.dispatch_once(ctx)

    artifacts = store.get(job)["result"]["artifacts"]
    assert "brief:seg-01" in artifacts
    assert not any("/" in a for a in artifacts)


# ---------------------------------------------------------------------------
# Progress and cancellation
# ---------------------------------------------------------------------------

def test_progress_from_a_layer_function_reaches_the_job_row(store, ctx):
    job = queue(store, "segment", segments=["seg-01"])
    runner.dispatch_once(ctx)

    progress = store.get(job)["progress"]
    assert progress["done"] == 1
    assert progress["total"] == 1
    assert progress["last_node"] == "seg-01"


def test_the_segment_stage_is_given_a_cancel_check(store, ctx):
    queue(store, "segment", segments=["seg-01"])
    runner.dispatch_once(ctx)
    _, _, cancel_check = ctx._calls["segment"][0]
    assert callable(cancel_check)


def test_the_cancel_check_reflects_the_store_flag(store, ctx):
    job = queue(store, "segment", segments=["seg-01"])
    runner.dispatch_once(ctx)
    _, _, cancel_check = ctx._calls["segment"][0]

    assert cancel_check() is False
    store.jobs[job]["cancel_requested"] = True
    store.jobs[job]["status"] = "running"
    assert cancel_check() is True


def test_a_job_cancelled_mid_run_finishes_as_cancelled_not_completed(store, ctx):
    job = queue(store, "segment", segments=["seg-01", "seg-02"])

    # Cancel as soon as the first segment is under way.
    original = ctx.plan_segment

    def plan_then_cancel(pack, arc_segment, config, llm, vocab, out_path,
                         progress=None, cancel_check=None):
        store.request_cancel(job)
        return original(pack, arc_segment, config, llm, vocab, out_path,
                        progress=progress, cancel_check=cancel_check)

    ctx.plan_segment = plan_then_cancel
    runner.dispatch_once(ctx)

    assert store.get(job)["status"] == "cancelled"


def test_a_cancelled_job_stops_before_the_remaining_segments(store, ctx):
    job = queue(store, "segment", segments=["seg-01", "seg-02", "seg-03"])
    original = ctx.plan_segment

    def plan_then_cancel(pack, arc_segment, config, llm, vocab, out_path,
                         progress=None, cancel_check=None):
        store.request_cancel(job)
        return original(pack, arc_segment, config, llm, vocab, out_path,
                        progress=progress, cancel_check=cancel_check)

    ctx.plan_segment = plan_then_cancel
    runner.dispatch_once(ctx)

    assert len(ctx._calls["segment"]) < 3


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

def test_a_raising_layer_function_fails_the_job_with_a_readable_error(store, ctx):
    job = queue(store, "arc")

    def boom(*args, **kwargs):
        raise RuntimeError("ollama unreachable")

    ctx.plan_arc = boom
    runner.dispatch_once(ctx)

    row = store.get(job)
    assert row["status"] == "failed"
    assert "ollama unreachable" in row["error"]


def test_a_failure_does_not_stop_the_dispatcher_taking_the_next_job(store, ctx):
    """One bad job must not wedge the queue for every job behind it."""
    def boom(*args, **kwargs):
        raise RuntimeError("nope")

    bad = queue(store, "arc")
    good = queue(store, "segment", segments=["seg-01"])

    ctx.plan_arc = boom
    runner.dispatch_once(ctx)
    runner.dispatch_once(ctx)

    assert store.get(bad)["status"] == "failed"
    assert store.get(good)["status"] == "completed"


def test_the_error_is_a_message_not_a_traceback(store, ctx):
    job = queue(store, "arc")

    def boom(*args, **kwargs):
        raise RuntimeError("ollama unreachable")

    ctx.plan_arc = boom
    runner.dispatch_once(ctx)

    assert "Traceback" not in store.get(job)["error"]


# ---------------------------------------------------------------------------
# Completion summary
# ---------------------------------------------------------------------------

def test_a_completed_job_records_a_duration(store, ctx):
    job = queue(store, "arc")
    runner.dispatch_once(ctx)
    assert "duration_s" in store.get(job)["result"]


def test_a_completed_segment_job_counts_what_it_planned(store, ctx):
    job = queue(store, "segment", segments=["seg-01", "seg-02"])
    runner.dispatch_once(ctx)
    assert store.get(job)["result"]["segments"] == 2


# ---------------------------------------------------------------------------
# build_default_context — the wiring the fakes above deliberately bypass
#
# Every test above injects fake layer functions, so none of them ever exercises
# the real dependency wiring. That gap hid a live bug: `build_llm` called
# `config.resolve_profile(...)` where `config` was the config DICT parameter
# shadowing the config MODULE, and with the wrong argument order besides. The
# unit suite was fully green; the container failed every job with
# "'dict' object has no attribute 'resolve_profile'".
# ---------------------------------------------------------------------------

def test_build_default_context_builds_a_working_llm_factory(tmp_path, monkeypatch):
    import concurrent_llm

    captured = {}
    monkeypatch.setattr(concurrent_llm, "from_profile",
                        lambda resolved: captured.setdefault("resolved", resolved))

    import config as config_module
    cfg = config_module.load_config(
        "utilities/3LayersWeeklyGeneration/config/generation.yaml")

    ctx = runner.build_default_context(cfg, tmp_path / "packs", tmp_path / "out")
    ctx.build_llm("light", "segment")

    # A resolved profile is a flat dict carrying at least the model name.
    assert "model" in captured["resolved"]


def test_the_llm_factory_honours_the_requested_profile(tmp_path, monkeypatch):
    import concurrent_llm

    captured = {}
    monkeypatch.setattr(concurrent_llm, "from_profile",
                        lambda resolved: captured.setdefault("resolved", resolved))

    import config as config_module
    cfg = config_module.load_config(
        "utilities/3LayersWeeklyGeneration/config/generation.yaml")

    ctx = runner.build_default_context(cfg, tmp_path / "packs", tmp_path / "out")
    ctx.build_llm("light", "segment")
    light = captured["resolved"]["model"]

    captured.clear()
    ctx.build_llm("heavy", "segment")

    assert captured["resolved"]["model"] != light


def test_the_context_carries_the_config_dict_not_the_module(tmp_path):
    import config as config_module
    cfg = config_module.load_config(
        "utilities/3LayersWeeklyGeneration/config/generation.yaml")

    ctx = runner.build_default_context(cfg, tmp_path / "packs", tmp_path / "out")

    assert isinstance(ctx.config, dict)
    assert "segment" in ctx.config


# ---------------------------------------------------------------------------
# Empty output (plan_arc skips batches instead of raising)
# ---------------------------------------------------------------------------

def _plan_arc_writing(segments):
    """A fake plan_arc that returns normally after writing `segments`."""
    def fake(pack, config, llm, vocab, out_path, on_llm_progress=None):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(yaml.safe_dump({"segments": segments}),
                            encoding="utf-8")
        return {"segments": segments}
    return fake


def test_an_arc_plan_with_no_segments_fails_the_job(store, ctx):
    """Every batch skipped is a total failure, not a green job."""
    job = queue(store, "arc")
    ctx.plan_arc = _plan_arc_writing([])

    runner.dispatch_once(ctx)

    row = store.get(job)
    assert row["status"] == "failed"
    assert "empty arc plan" in row["error"]


def test_an_empty_arc_plan_is_not_mirrored_into_the_store(store, ctx):
    """boot() rehydrates from the store, so an empty plan must never land
    there — it would come back and shadow a later, real run."""
    queue(store, "arc")
    ctx.plan_arc = _plan_arc_writing([])

    runner.dispatch_once(ctx)

    assert store.load_artifact("ashiorid", "arc_plan", "") is None


def test_a_missing_arc_plan_file_fails_the_job(store, ctx):
    job = queue(store, "arc")
    ctx.plan_arc = lambda *a, **k: {"segments": []}

    runner.dispatch_once(ctx)

    row = store.get(job)
    assert row["status"] == "failed"
    assert "no arc plan" in row["error"]


def test_an_empty_arc_plan_aborts_the_rest_of_stage_all(store, ctx):
    job = queue(store, "all")
    ctx.plan_arc = _plan_arc_writing([])

    runner.dispatch_once(ctx)

    assert store.get(job)["status"] == "failed"
    assert not ctx._calls["segment"]
    assert not ctx._calls["dialogue"]


def test_a_partial_arc_plan_completes_but_records_the_shortfall(store, ctx):
    """A partial plan is still usable, so it completes — but the skipped
    batches go on the job row instead of vanishing."""
    job = queue(store, "arc")
    ctx.config["arc"] = {"hours_total": 12, "segment_hours": 6}  # expects 2
    ctx.plan_arc = _plan_arc_writing([{"id": "seg-01", "order": 0}])

    runner.dispatch_once(ctx)

    row = store.get(job)
    assert row["status"] == "completed"
    assert row["result"]["segments"] == 1
    assert row["result"]["skipped_segments"] == 1


def test_a_complete_arc_plan_records_no_shortfall(store, ctx):
    job = queue(store, "arc")
    ctx.config["arc"] = {"hours_total": 12, "segment_hours": 6}
    ctx.plan_arc = _plan_arc_writing([{"id": "seg-01", "order": 0},
                                      {"id": "seg-02", "order": 1}])

    runner.dispatch_once(ctx)

    row = store.get(job)
    assert row["status"] == "completed"
    assert "skipped_segments" not in row["result"]


# ---------------------------------------------------------------------------
# 1.5 cutover — the job pipeline reads pack content from Postgres, not the
# campaigns/:ro host mount (decision 5). The existing ctx fixture's fake
# layer functions assert pack IS a shared sentinel object, which is exactly
# the pre-cutover call shape this branch is replacing — so these tests build
# their own non-identity fakes and a ctx with load_pack_for_job wired to
# the real (materialize, load_pack) pair, pointing pack_root at an
# EMPTY directory to prove the host mount is genuinely never consulted.
# ---------------------------------------------------------------------------
import campaign.pack as _campaign_pack
from tests.pack_fixtures import seed_minimal_pack
import dataclasses


def _make_cutover_ctx(tmp_path, store):
    """A Context like the shared ctx fixture's, but with load_pack_for_job
    cut over and pack_root deliberately pointed at a directory that does
    NOT exist — the whole point of this test is that a nonexistent host
    path must not matter at all."""
    loaded_pack = object()

    def fake_plan_arc(pack, config, llm, vocab, out_path, on_llm_progress=None):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(yaml.safe_dump({"segments": [{"id": "seg-01"}]}),
                            encoding="utf-8")
        return {"segments": [{"id": "seg-01"}]}

    def fake_plan_segment(pack, arc_segment, config, llm, vocab, out_path,
                          progress=None, cancel_check=None):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(yaml.safe_dump({"segment_id": arc_segment["id"],
                                            "slots": [{"slot_id": "s-1"}]}),
                            encoding="utf-8")
        out_path.with_name("tree.yaml").write_text(
            yaml.safe_dump({arc_segment["id"]: {"kind": "leaf"}}), encoding="utf-8")
        return {"segment_id": arc_segment["id"], "slots": [{"slot_id": "s-1"}]}

    def fake_generate_dialogue(pack, segment_ids, config, llm, out_root,
                               progress=None, cancel_check=None):
        return {"planned": 1, "written": 1, "failed": 0}

    return dataclasses.replace(
        runner.Context(
            config={"output": {"dir": str(tmp_path / "out")},
                    "segment": {"concurrency": 2},
                    "arc": {}, "dialogue": {}},
            pack_root=tmp_path / "definitely-empty-and-nonexistent",
            output_root=tmp_path / "out",
            store=store,
            build_llm=lambda profile, layer: object(),
            load_pack=lambda pack_path: (_ for _ in ()).throw(
                AssertionError("pack_root was consulted — cutover failed")),
            build_vocab=lambda config, pack: object(),
            plan_arc=fake_plan_arc,
            plan_segment=fake_plan_segment,
            generate_dialogue=fake_generate_dialogue,
        ),
        load_pack_for_job=lambda pack_name: runner._load_pack_for_job_from_postgres(
            store.materialize_pack, _campaign_pack.load_pack, pack_name),
    )


def test_a_job_loads_its_pack_from_postgres_not_the_host_mount(tmp_path, store):
    """The cutover assertion, exactly as the plan's Task 1.5 Step 3 describes
    it: a pack with zero files at its (nonexistent) pack_root must still
    dispatch and complete, because the only thing ever consulted is the
    (fake) store's materialize function — not a filesystem path at all."""
    seed_minimal_pack(store, "ashiorid")

    # Belt and braces: a pack_root directory that doesn't even exist on disk.
    # dispatch_once used to call ctx.load_pack(ctx.pack_root / pack_name)
    # directly, which against this empty/nonexistent dir would fail with the
    # same "campaign.yaml not found in ..." a missing pack produces today.
    ctx = _make_cutover_ctx(tmp_path, store)

    job = queue(store, "arc")
    assert runner.dispatch_once(ctx) is True
    row = store.get(job)
    assert row["status"] == "completed", f"job failed: {row['error']!r}"
    assert row["result"]["artifacts"]


def test_a_pack_with_no_rows_in_the_store_fails_with_an_actionable_error(
        tmp_path, store):
    """Symmetric case: cut over, but the pack was never imported. The job
    must fail cleanly (not crash the dispatcher loop) with an error that
    names the import script, since that's the one thing an operator can do
    about this specific failure mode."""
    empty_store = FakeStore()
    ctx = _make_cutover_ctx(tmp_path, empty_store)

    job = queue(empty_store, "arc")
    assert runner.dispatch_once(ctx) is True
    row = empty_store.get(job)
    assert row["status"] == "failed"
    assert "import_packs_to_postgres.py" in row["error"]
