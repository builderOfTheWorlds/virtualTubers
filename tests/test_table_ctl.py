"""scripts/table_ctl.py: request building and reply matching (no network)."""
import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "table_ctl.py"
spec = importlib.util.spec_from_file_location("table_ctl", SCRIPT)
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)


def _args(argv):
    import argparse  # noqa: F401
    captured = {}

    def fake_post(url, to, type_, payload):
        captured.update(url=url, to=to, type=type_, payload=payload)
        return {"id": "m1"}

    def fake_wait(url, gm, request_id, since, timeout_s):
        return {"result": "started", "scene_id": "s1"}

    ctl.post, ctl.wait_reply = fake_post, fake_wait
    rc = ctl.main(argv)
    return rc, captured


def test_next_force():
    rc, c = _args(["next", "--force"])
    assert rc == 0 and c["to"] == "tuber_0"
    assert (c["type"], c["payload"]) == ("scene_request", {"next": True, "force": True})


def test_start_by_index_and_by_id():
    assert _args(["start", "6"])[1]["payload"] == {"index": 6, "force": False}
    assert _args(["start", "first-standup-arc.x-001"])[1]["payload"] == \
        {"scene_id": "first-standup-arc.x-001", "force": False}


def test_status_and_stop():
    assert _args(["status"])[1]["type"] == "table_status_request"
    assert _args(["stop"])[1]["type"] == "scene_stop"
