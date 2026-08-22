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

    def submit(self, record):
        self._seq += 1
        job_id = f"job_{self._seq:04d}"
        self.jobs[job_id] = {
            "id": job_id, "pack": record["pack"], "stage": record["stage"],
            "profile": record.get("profile", ""), "status": "queued",
            "params": record.get("params", {}), "progress": None,
            "result": None, "error": None, "cancel_requested": False,
            "created_at": f"t{self._seq}", "started_at": None,
            "finished_at": None, "heartbeat_at": None,
        }
        return job_id

    def get(self, job_id):
        return self.jobs.get(job_id)

    def list_jobs(self, pack=None, stage=None, status=None):
        rows = list(self.jobs.values())
        if pack is not None:
            rows = [r for r in rows if r["pack"] == pack]
        if stage is not None:
            rows = [r for r in rows if r["stage"] == stage]
        if status is not None:
            rows = [r for r in rows if r["status"] == status]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

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
        self.artifacts[(pack, kind, segment_id)] = content
        self.calls.append(("upsert_artifact", kind, segment_id))

    def load_artifact(self, pack, kind, segment_id):
        return self.artifacts.get((pack, kind, segment_id))

    def list_artifacts(self, pack):
        return [{"pack": p, "kind": k, "segment_id": s}
                for (p, k, s) in self.artifacts if p == pack]


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def ctx(tmp_path, store):
    """Everything the runner needs, with the layer functions faked."""
    calls = {"arc": [], "segment": [], "dialogue": []}
    # boot() discovers packs by listing pack_root, so the pack has to exist.
    (tmp_path / "packs" / "ashiorid").mkdir(parents=True, exist_ok=True)
    # A sentinel so the fakes can prove they were handed the LOADED pack and
    # not the pack name. The runner needs both — the name keys artifact paths
    # and store rows, the object is what the layer functions consume — and
    # conflating them fails every real job with
    # "'str' object has no attribute 'genre'".
    loaded_pack = object()

    def fake_plan_arc(pack, config, llm, vocab, out_path):
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


def queue(store, stage, **params):
    return store.submit({"pack": "ashiorid", "stage": stage,
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
    everything the database already holds."""
    store.upsert_artifact("ashiorid", "brief", "seg-01",
                          {"segment_id": "seg-01", "slots": []})

    runner.boot(ctx)

    written = ctx.output_root / "ashiorid" / "segments" / "seg-01" / "brief.yaml"
    assert written.exists()
    assert yaml.safe_load(written.read_text(encoding="utf-8"))["segment_id"] == "seg-01"


def test_boot_does_not_overwrite_an_artifact_already_on_disk(store, ctx):
    target = ctx.output_root / "ashiorid" / "segments" / "seg-01" / "brief.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump({"segment_id": "seg-01", "local": True}),
                      encoding="utf-8")
    store.upsert_artifact("ashiorid", "brief", "seg-01", {"segment_id": "from-db"})

    runner.boot(ctx)

    assert yaml.safe_load(target.read_text(encoding="utf-8")).get("local") is True


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
    def fake(pack, config, llm, vocab, out_path):
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
