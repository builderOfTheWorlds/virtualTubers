"""WP-03 tests for app/character/config.py (plan §11, D-01, D-05, D-26).

Frozen test list (playbook §4 WP-03, items 1-7). The repo config is the
OB-41 office one: campaign ashiorid_office, epoch 2026-09-27, the 8 office
characters. `env` is always passed explicitly so the host environment (a real
CHARACTER_DB_PASSWORD, say) can never leak into a test.
"""
import dataclasses
from datetime import date

import pytest

from fakes import OFFICE_CAMPAIGN, OFFICE_SLUGS, REPO_CONFIG, write_config
from pending import require, skip_if_pending

config = require("character.config", "app/character/config.py")


def _load(tmp_path, mutate, env=None):
    return config.load(write_config(tmp_path, mutate), env=env or {})


# T03.1
def test_load_repo_config_succeeds():
    cfg = config.load(env={})
    assert cfg == config.load(REPO_CONFIG, env={})
    assert cfg.campaign == OFFICE_CAMPAIGN
    assert cfg.timezone == "America/New_York"
    assert cfg.loop.epoch == date(2026, 9, 27)
    assert cfg.loop.fragments_per_week == 1
    assert set(cfg.characters) == set(OFFICE_SLUGS)
    assert all(entry.retains_fragments for entry in cfg.characters.values())
    assert cfg.characters["ceo"].accent_color == "BLUE"
    assert cfg.db.host == "192.168.1.120"
    assert cfg.db.port == 5433
    assert cfg.db.dbname == "character_profile"
    assert cfg.db.user == "character_profile"
    assert cfg.llm.profiles["judge"].model == "qwen3.8:27b"
    assert cfg.llm.profiles["judge"].think is False
    assert cfg.ingest.types == ("agent_thinking", "character_say", "scene_event")
    assert cfg.recall.weights.trajectory == pytest.approx(0.5)
    assert cfg.recall.thresholds.surface == pytest.approx(0.55)
    assert cfg.reset.refresh_mode == "push"
    assert cfg.testctl.enabled is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.campaign = "other"
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.db.host = "elsewhere"


# T03.2
def test_epoch_not_sunday_names_loop_epoch(tmp_path):
    def monday(doc):
        doc["character"]["loop"]["epoch"] = "2026-09-28"

    with pytest.raises(config.ConfigError) as caught:
        _load(tmp_path, monday)
    assert caught.value.key == "loop.epoch"
    assert "loop.epoch" in str(caught.value)


# T03.3
@pytest.mark.parametrize("weights", [
    {"hooks": 0.3, "trajectory": 0.5, "gist": 0.3},
    {"hooks": 0.3, "trajectory": 0.5, "gist": 0.19},
])
def test_weights_not_summing_to_one_raise(tmp_path, weights):
    def bad_weights(doc):
        doc["character"]["recall"]["weights"] = weights

    with pytest.raises(config.ConfigError) as caught:
        _load(tmp_path, bad_weights)
    assert caught.value.key == "recall.weights"


def test_weights_within_tolerance_are_accepted(tmp_path):
    def near_one(doc):
        doc["character"]["recall"]["weights"] = {"hooks": 0.3, "trajectory": 0.5,
                                                 "gist": 0.2 + 5e-7}

    cfg = _load(tmp_path, near_one)
    assert cfg.recall.weights.gist == pytest.approx(0.2, abs=1e-6)


# T03.4
@pytest.mark.parametrize("unease,surface", [(0.55, 0.55), (0.6, 0.55)])
def test_unease_not_below_surface_raises(tmp_path, unease, surface):
    def bad_thresholds(doc):
        doc["character"]["recall"]["thresholds"] = {"unease": unease, "surface": surface}

    with pytest.raises(config.ConfigError) as caught:
        _load(tmp_path, bad_thresholds)
    assert caught.value.key == "recall.thresholds"


# T03.5
def test_env_overrides_db_host_and_password_never_in_repr():
    secret = "s3cret-Pw-do-not-print"
    cfg = config.load(env={"CHARACTER_DB_HOST": "10.9.8.7",
                           "CHARACTER_DB_PORT": "6543",
                           "CHARACTER_DB_PASSWORD": secret})
    assert cfg.db.host == "10.9.8.7"
    assert cfg.db.port == 6543
    assert cfg.db.password == secret
    for text in (repr(cfg), str(cfg), repr(cfg.db), str(cfg.db),
                 repr(cfg.ingest_db), str(cfg.ingest_db)):
        assert secret not in text


# T03.6
def test_missing_password_loads_then_connect_fails():
    cfg = config.load(env={})
    assert cfg.db.password is None
    assert cfg.ingest_db.password is None
    # The second half needs app/character/db.py (WP-04). Until it is promoted
    # this test reports as skipped after the load half above has passed.
    skip_if_pending("app/character/db.py", "WP-04")
    from character import db

    with pytest.raises(config.ConfigError) as caught:
        db.connect(cfg)
    assert caught.value.key == "db.password"


# T03.7
def test_ingest_dsn_falls_back_to_main_values():
    env = {"CHARACTER_DB_HOST": "10.1.1.1", "CHARACTER_DB_USER": "owner",
           "CHARACTER_DB_PASSWORD": "pw-main"}
    cfg = config.load(env=env)
    for field in ("host", "port", "dbname", "user", "password"):
        assert getattr(cfg.ingest_db, field) == getattr(cfg.db, field), field
    assert cfg.ingest_db.host == "10.1.1.1"

    cfg = config.load(env={**env, "CHARACTER_INGEST_DB_HOST": "10.2.2.2",
                           "CHARACTER_INGEST_DB_PASSWORD": "pw-ingest"})
    assert cfg.ingest_db.host == "10.2.2.2"
    assert cfg.ingest_db.password == "pw-ingest"
    assert cfg.ingest_db.user == "owner"
    assert cfg.ingest_db.port == cfg.db.port
    assert cfg.db.host == "10.1.1.1"
