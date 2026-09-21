"""
test_radar_pane.py
Unit tests for the pure rendering/formatting/loading helpers in
app/radar_pane.py. No real filesystem tmux/Kafka dependency beyond a tmp
file for the metrics JSON. conftest.py inserts app/ onto sys.path.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

import radar_pane


# ── load_metrics: defensive file handling ──────────────────────────────────
def test_load_metrics_missing_file_returns_defaults(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    metrics = radar_pane.load_metrics(path)
    assert metrics == radar_pane.DEFAULT_METRICS


def test_load_metrics_none_path_returns_defaults():
    assert radar_pane.load_metrics(None) == radar_pane.DEFAULT_METRICS


def test_load_metrics_malformed_json_returns_defaults(tmp_path):
    path = tmp_path / "metrics_coder.json"
    path.write_text("{not valid json", encoding="utf-8")
    metrics = radar_pane.load_metrics(str(path))
    assert metrics == radar_pane.DEFAULT_METRICS


def test_load_metrics_non_object_json_returns_defaults(tmp_path):
    path = tmp_path / "metrics_coder.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    metrics = radar_pane.load_metrics(str(path))
    assert metrics == radar_pane.DEFAULT_METRICS


def test_load_metrics_valid_file_reads_all_fields(tmp_path):
    payload = {
        "worker_id": "coder",
        "updated_at": "2026-09-21T19:00:00+00:00",
        "tokens_per_sec": 12.4,
        "avg_latency_s": 3.2,
        "context_tokens": 5400,
        "error_rate_pct": 4.5,
        "uptime_pct": 100.0,
        "messages_sent": 87,
    }
    path = tmp_path / "metrics_coder.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    metrics = radar_pane.load_metrics(str(path))
    assert metrics == payload


def test_load_metrics_partial_file_merges_over_defaults(tmp_path):
    path = tmp_path / "metrics_coder.json"
    path.write_text(json.dumps({"tokens_per_sec": 5.0}), encoding="utf-8")
    metrics = radar_pane.load_metrics(str(path))
    assert metrics["tokens_per_sec"] == 5.0
    assert metrics["messages_sent"] == 0  # default fallback


# ── is_stale ────────────────────────────────────────────────────────────────
def test_is_stale_missing_updated_at():
    assert radar_pane.is_stale({}) is True


def test_is_stale_bad_timestamp():
    assert radar_pane.is_stale({"updated_at": "not-a-date"}) is True


def test_is_stale_fresh_timestamp():
    now = datetime.now(timezone.utc)
    metrics = {"updated_at": now.isoformat()}
    assert radar_pane.is_stale(metrics, now=now) is False


def test_is_stale_old_timestamp():
    now = datetime.now(timezone.utc)
    old = now - timedelta(seconds=radar_pane.STALE_AFTER_S + 10)
    metrics = {"updated_at": old.isoformat()}
    assert radar_pane.is_stale(metrics, now=now) is True


# ── normalize / render_bar (pure math) ──────────────────────────────────────
def test_normalize_clamps_within_bounds():
    assert radar_pane.normalize(-5, 0, 10) == 0.0
    assert radar_pane.normalize(15, 0, 10) == 1.0
    assert radar_pane.normalize(5, 0, 10) == 0.5


def test_normalize_invert_lower_is_better():
    assert radar_pane.normalize(0, 0, 10, invert=True) == 1.0
    assert radar_pane.normalize(10, 0, 10, invert=True) == 0.0


def test_normalize_non_numeric_returns_zero():
    assert radar_pane.normalize("garbage", 0, 10) == 0.0
    assert radar_pane.normalize(None, 0, 10) == 0.0


def test_render_bar_full_and_empty():
    assert radar_pane.render_bar(1.0, width=10) == radar_pane.FILLED * 10
    assert radar_pane.render_bar(0.0, width=10) == radar_pane.EMPTY * 10


def test_render_bar_half():
    bar = radar_pane.render_bar(0.5, width=10)
    assert bar.count(radar_pane.FILLED) == 5
    assert bar.count(radar_pane.EMPTY) == 5


# ── render_radar: never crashes, always renders all six metrics ────────────
def test_render_radar_with_defaults_never_crashes():
    output = radar_pane.render_radar(radar_pane.DEFAULT_METRICS, stale=True, worker_id="coder")
    assert "coder" in output
    assert "STALE" in output
    for name, *_rest in radar_pane.METRIC_SPECS:
        assert name in output


def test_render_radar_live_status_when_not_stale():
    output = radar_pane.render_radar(radar_pane.DEFAULT_METRICS, stale=False, worker_id="tester")
    assert "live" in output
    assert "STALE" not in output


def test_render_radar_missing_worker_id_falls_back_to_metrics_field():
    metrics = dict(radar_pane.DEFAULT_METRICS)
    metrics["worker_id"] = "manager"
    output = radar_pane.render_radar(metrics, stale=False, worker_id=None)
    assert "manager" in output


def test_render_radar_unknown_worker_shows_placeholder():
    output = radar_pane.render_radar(radar_pane.DEFAULT_METRICS, stale=False, worker_id=None)
    assert "[?]" in output


# ── resolve_metrics_path ─────────────────────────────────────────────────────
def test_resolve_metrics_path_env_wins(monkeypatch):
    monkeypatch.setenv("METRICS_PATH", "/tmp/explicit_env.json")
    path = radar_pane.resolve_metrics_path({}, explicit_path="/tmp/explicit_cli.json")
    assert path == "/tmp/explicit_env.json"


def test_resolve_metrics_path_explicit_cli_arg(monkeypatch):
    monkeypatch.delenv("METRICS_PATH", raising=False)
    path = radar_pane.resolve_metrics_path({}, explicit_path="/tmp/explicit_cli.json")
    assert path == "/tmp/explicit_cli.json"


def test_resolve_metrics_path_derived_from_worker_config(monkeypatch):
    monkeypatch.delenv("METRICS_PATH", raising=False)
    monkeypatch.delenv("WORKER_ID", raising=False)
    monkeypatch.delenv("METRICS_RUNTIME_DIR", raising=False)
    worker_config = {"message_bus": {"worker_id": "coder"}}
    path = radar_pane.resolve_metrics_path(worker_config, explicit_path=None)
    assert path == os.path.join(radar_pane.DEFAULT_RUNTIME_DIR, "metrics_coder.json")


def test_resolve_metrics_path_env_worker_id_wins_over_config(monkeypatch):
    monkeypatch.delenv("METRICS_PATH", raising=False)
    monkeypatch.setenv("WORKER_ID", "manager")
    monkeypatch.delenv("METRICS_RUNTIME_DIR", raising=False)
    worker_config = {"message_bus": {"worker_id": "coder"}}
    path = radar_pane.resolve_metrics_path(worker_config, explicit_path=None)
    assert path.endswith("metrics_manager.json")
