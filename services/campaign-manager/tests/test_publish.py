"""Campaign-manager Publish flow (Phase 3, task 3.2).

The route is a pure proxy over the generator API (submit a `publish` job,
wait for it, call the upload endpoint) — so the tests monkeypatch
`main.http_client` the same way test_pack_viewer.py does, and check the
redirect + banner the operator actually sees, plus the exact request the
route sent (AsyncMock.call_args), not just the outcome.
"""
import httpx
import pytest
from unittest.mock import AsyncMock, patch

import main
from main import _publish_episode_name
from fastapi.testclient import TestClient


def _ok(status=200, body=None, text=None):
    if body is not None:
        return httpx.Response(status, json=body)
    return httpx.Response(status, text=text if text is not None else "")


# A completed dialogue job's run: pack + date + 4-char uuid suffix — the
# exact shape generate_run_name() produces for a top-of-pipeline run, which
# is the kind of run a `dialogue` job (the prerequisite for "Publish")
# always belongs to.
RUN = "ashiorid_20260913_180158_ce8d"


@pytest.fixture
def fclient():
    import types
    return types.SimpleNamespace(
        get=AsyncMock(), post=AsyncMock(), aclose=AsyncMock())


@pytest.fixture
def client(fclient):
    with patch.object(main, "_PUBLISH_WAIT_BUDGET_S", 0.5), \
         patch.object(main, "_PUBLISH_POLL_INTERVAL_S", 0.01), \
         patch.object(main, "http_client", fclient):
        yield TestClient(main.app)


def test_episode_name_matches_the_original_scripts_convention():
    # `ashiorid_generated_ce8d` — the exact form the old CLI's usage
    # example shows, NOT the run's full namespace
    # (ashiorid_20260913_180158_ce8d), which is an output namespace, not
    # an episode library key.
    assert _publish_episode_name("ashiorid", RUN) == "ashiorid_generated_ce8d"
    # A run with no underscored suffix falls back to a plain name — no
    # crash, no None in the string.
    assert _publish_episode_name("ashiorid", "") == "ashiorid_generated"


def test_publish_success_redirects_with_episode_name_and_event_count(client, fclient):
    job = {"id": "dialogue_job", "pack": "ashiorid", "run": RUN,
           "stage": "dialogue", "status": "completed"}
    pub_job = {"id": "pub_job", "status": "completed",
               "result": {"event_count": 37, "episode_name": "ashiorid_generated_ce8d"}}
    fclient.get.side_effect = [_ok(200, body=job), _ok(200, body=pub_job)]
    # Two POSTs: /jobs (submit) then /publish/{run}/upload.
    fclient.post.side_effect = [
        _ok(200, body={"id": "pub_job", "run": RUN, "status": "queued"}),
        _ok(200, body={"name": "ashiorid_generated_ce8d", "created": True,
                       "event_count": 37}),
    ]

    resp = client.post("/jobs/dialogue_job/publish", follow_redirects=False)
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert "published=1" in loc
    assert "episode_name=ashiorid_generated_ce8d" in loc
    assert "event_count=37" in loc
    assert "control_panel_url=" in loc

    # The submit call named the episode itself (the runner's _run_publish
    # reads this from job["params"]) — a real request-shape assertion, not
    # just "a POST happened".
    submit_call = fclient.post.await_args_list[0].args[0]
    submit_body = fclient.post.await_args_list[0].kwargs["json"]
    assert submit_call == "/jobs"
    assert submit_body["stage"] == "publish"
    assert submit_body["run"] == RUN
    assert submit_body["episode_name"] == "ashiorid_generated_ce8d"

    # And the upload call hit the RIGHT endpoint, with the same name.
    upload_call = fclient.post.await_args_list[1].args[0]
    assert upload_call == f"/publish/{RUN}/upload"
    assert fclient.post.await_args_list[1].kwargs["params"]["name"] == "ashiorid_generated_ce8d"


def test_publish_from_a_non_completed_job_is_a_banner_not_a_500(client, fclient):
    job = {"id": "arc_job", "pack": "ashiorid", "run": RUN,
           "stage": "arc", "status": "running"}
    fclient.get.return_value = _ok(200, body=job)
    resp = client.post("/jobs/arc_job/publish", follow_redirects=False)
    assert resp.status_code == 303
    assert "publish_error=" in resp.headers["location"]
    # No publish job may have been submitted as a side effect of the
    # refusal — the generator API must never be told to publish from a job
    # that didn't finish.
    fclient.post.assert_not_awaited()


def test_publish_failure_relayed_verbatim(client, fclient):
    job = {"id": "dialogue_job", "pack": "ashiorid", "run": RUN,
           "stage": "dialogue", "status": "completed"}
    pub_job = {"id": "pub_job", "status": "failed",
               "error": "publish: run 'ashiorid_20260913_180158_ce8d' produced "
                        "an episode with 0 events after cleaning"}
    fclient.get.side_effect = [_ok(200, body=job), _ok(200, body=pub_job)]
    fclient.post.return_value = _ok(200, body={"id": "pub_job", "run": RUN,
                                               "status": "queued"})
    resp = client.post("/jobs/dialogue_job/publish", follow_redirects=False)
    assert resp.status_code == 303
    from urllib.parse import parse_qs
    err = parse_qs(resp.headers["location"].split("?", 1)[1])["publish_error"][0]
    # The generator's own actionable message ("0 events after cleaning ...")
    # must survive the round trip, not get flattened to "500" or "failed".
    assert "0 events" in err
    assert "produced an episode with 0 events after cleaning" in err


def test_job_detail_renders_the_success_banner(client, fclient):
    fclient.get.return_value = _ok(200, body={"id": "dialogue_job", "pack": "ashiorid",
                                              "run": RUN, "stage": "dialogue",
                                              "status": "completed",
                                              "created_at": "t1", "finished_at": "t2",
                                              "params": {}, "result": None,
                                              "error": None, "started_at": None,
                                              "cancel_requested": False})
    resp = client.get(
        "/job/dialogue_job",
        params={"published": "1", "episode_name": "ashiorid_generated_ce8d",
                "event_count": "37",
                "control_panel_url": "http://localhost:8091"})
    assert resp.status_code == 200
    assert "Episode published" in resp.text
    assert "ashiorid_generated_ce8d" in resp.text
    assert "http://localhost:8091/replays/ashiorid_generated_ce8d/view" in resp.text
    assert "37 events" in resp.text


def test_job_detail_renders_the_publish_button_for_a_completed_job(client, fclient):
    fclient.get.return_value = _ok(200, body={"id": "dialogue_job", "pack": "ashiorid",
                                              "run": RUN, "stage": "dialogue",
                                              "status": "completed",
                                              "created_at": "t1", "finished_at": "t2",
                                              "params": {}, "result": None,
                                              "error": None, "started_at": None,
                                              "cancel_requested": False})
    resp = client.get("/job/dialogue_job")
    assert resp.status_code == 200
    assert 'action="/jobs/dialogue_job/publish"' in resp.text
    assert "Publish to Episode Store" in resp.text


def test_job_detail_hides_the_publish_button_for_a_non_completed_job(client, fclient):
    fclient.get.return_value = _ok(200, body={"id": "arc_job", "pack": "ashiorid",
                                              "run": RUN, "stage": "arc",
                                              "status": "running",
                                              "created_at": "t1", "finished_at": None,
                                              "params": {}, "result": None,
                                              "error": None, "started_at": None,
                                              "cancel_requested": False})
    resp = client.get("/job/arc_job")
    assert resp.status_code == 200
    assert 'action="/jobs/arc_job/publish"' not in resp.text
    assert "Publish to Episode Store" not in resp.text
