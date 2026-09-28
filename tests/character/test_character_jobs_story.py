"""WP-24 tests for app/character/jobs_story.py (`story-start` / `story-stop`)
(WP-24's second spec).

Frozen test list (playbook §4 WP-24, item 8). Plan §5 job table:
- story-start: "Enables char-live:<campaign> in WorkerControl
  (app/worker_control.py:27), then `docker compose up -d character-live` (or
  runs the driver inline with --foreground)".
- story-stop: "Disables char-live:<campaign>. The driver finishes the current
  scene, publishes scene_event kind=story_end, and exits 0."
Both run through the WP-06 framework (lock, jobs row, --dry-run writes
nothing) and are idempotent. WorkerControl, the compose call and the driver
are injected (fakes); nothing here touches Redis or Docker.
"""
import psycopg2
import pytest

from fakes_e2e import FakeWorkerControl, count
from pending import require, skip_if_pending

skip_if_pending("app/character/live.py", wp="WP-24")
jobs_story = require("character.jobs_story", "app/character/jobs_story.py", wp="WP-24")
from character import config, db, jobs  # noqa: E402

pytestmark = pytest.mark.integration

DRIVER = "char-live:ashiorid_office"
UP = ["docker", "compose", "up", "-d", "character-live"]


@pytest.fixture
def cfg():
    return config.load(env={})


@pytest.fixture
def dsn(pg):
    conn = psycopg2.connect(pg)
    try:
        db.migrate(conn)
    finally:
        conn.close()
    return pg


@pytest.fixture
def connect(dsn):
    return lambda cfg, role="main": psycopg2.connect(dsn)


class FakeDriver:
    def __init__(self):
        self.runs = 0

    def run(self):
        self.runs += 1
        return 0


def _rows(dsn):
    return count(dsn, "SELECT coalesce(string_agg(job || ':' || status, ',' ORDER BY created_at), '') "
                      "FROM character_jobs")


# T24.8
def test_story_start_enables_and_story_stop_disables_both_idempotent(cfg, connect, dsn):
    control, calls, driver = FakeWorkerControl({DRIVER: False}), [], FakeDriver()

    def start():
        return jobs_story.StoryStartJob(control=control, compose=calls.append,
                                        driver_factory=lambda ctx: driver)

    stop = jobs_story.StoryStopJob(control=control)
    assert jobs_story.StoryStartJob.name == "story-start" and stop.name == "story-stop"

    assert jobs.run_job(start(), ["--dry-run"], cfg=cfg, connect=connect) == 0
    assert control.flags[DRIVER] is False and calls == [] and _rows(dsn) == ""

    for _ in range(2):
        assert jobs.run_job(start(), [], cfg=cfg, connect=connect) == 0
        assert control.flags[DRIVER] is True
    assert calls == [UP, UP] and driver.runs == 0

    for _ in range(2):
        assert jobs.run_job(stop, [], cfg=cfg, connect=connect) == 0
        assert control.flags[DRIVER] is False
    assert jobs.run_job(stop, ["--dry-run"], cfg=cfg, connect=connect) == 0

    assert jobs.run_job(start(), ["--foreground"], cfg=cfg, connect=connect) == 0
    assert control.flags[DRIVER] is True and driver.runs == 1 and calls == [UP, UP]
    assert _rows(dsn) == ",".join(["story-start:completed"] * 2 + ["story-stop:completed"] * 2
                                  + ["story-start:completed"])

    other = FakeWorkerControl()
    assert jobs.run_job(jobs_story.StoryStopJob(control=other), ["--campaign", "night_shift"],
                        cfg=cfg, connect=connect) == 0
    assert other.sets == [("char-live:night_shift", False)]


def test_a_failing_compose_call_exits_1(cfg, connect, dsn):
    control = FakeWorkerControl()

    def broken(argv):
        raise RuntimeError("docker is not running")

    assert jobs.run_job(jobs_story.StoryStartJob(control=control, compose=broken), [],
                        cfg=cfg, connect=connect) == 1
    assert _rows(dsn) == "story-start:failed"
