"""Acceptance tests for src/config.py — the one config file's loader.

Covers profile resolution (`active_model` vs `--model-profile`), the
`defaults:` merge that stops `base_url` being restated in every profile
(issue #12), up-front validation of a profile name against every stage an
invocation will run (issue #10), and the output-path derivation every layer
writes through.
"""
import pytest
import yaml

import config as config_module


MINIMAL = {
    "campaign": {"pack": "campaigns/ashiorid"},
    "output": {"dir": "utilities/3LayersWeeklyGeneration/output"},
    "defaults": {
        "provider": "ollama",
        "base_url": "http://localhost:11434",
        "temperature": 0.7,
        "max_tokens": 4096,
        "timeout_s": 600,
    },
    "arc": {
        "models": {"light": {"model": "qwen3-coder:30b"}, "heavy": {"model": "hermes3:70b"}},
        "active_model": "heavy",
    },
    "segment": {
        "models": {"light": {"model": "qwen3-coder:30b"}, "heavy": {"model": "hermes3:70b"}},
        "active_model": "heavy",
    },
    "dialogue": {
        "models": {
            "light": {"model": "llama3.1:8b", "temperature": 0.9, "max_tokens": 1024},
            "heavy": {"model": "hermes3:70b", "temperature": 0.9, "max_tokens": 1024},
        },
        "active_model": "heavy",
    },
    "state": {
        "flags": ["helen-wounded", "moonwell-tainted"],
        "moods": ["tense", "weary"],
        "carry_keys": ["helen-wounded"],
    },
}


@pytest.fixture
def config_file(tmp_path):
    """Write MINIMAL to a real YAML file and return its path."""
    path = tmp_path / "generation.yaml"
    path.write_text(yaml.safe_dump(MINIMAL), encoding="utf-8")
    return path


# ── load_config ───────────────────────────────────────────────────────────────

def test_load_config_returns_parsed_mapping(config_file):
    loaded = config_module.load_config(config_file)
    assert loaded["arc"]["active_model"] == "heavy"
    assert loaded["campaign"]["pack"] == "campaigns/ashiorid"


def test_load_config_missing_file_raises_config_error(tmp_path):
    with pytest.raises(config_module.ConfigError):
        config_module.load_config(tmp_path / "nope.yaml")


def test_load_config_malformed_yaml_raises_config_error(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("arc: [unclosed\n", encoding="utf-8")
    with pytest.raises(config_module.ConfigError):
        config_module.load_config(path)


# ── resolve_profile: the defaults merge (issue #12) ───────────────────────────

def test_resolve_profile_fills_missing_keys_from_defaults(config_file):
    """A profile stating only `model:` still gets a complete llm config."""
    loaded = config_module.load_config(config_file)
    resolved = config_module.resolve_profile(loaded, "arc", "heavy")
    assert resolved["model"] == "hermes3:70b"
    assert resolved["provider"] == "ollama"
    assert resolved["base_url"] == "http://localhost:11434"
    assert resolved["temperature"] == 0.7
    assert resolved["max_tokens"] == 4096


def test_resolve_profile_profile_keys_override_defaults(config_file):
    """dialogue.heavy sets its own temperature/max_tokens; defaults must lose."""
    loaded = config_module.load_config(config_file)
    resolved = config_module.resolve_profile(loaded, "dialogue", "heavy")
    assert resolved["temperature"] == 0.9
    assert resolved["max_tokens"] == 1024
    # ...while unstated keys still come from defaults
    assert resolved["base_url"] == "http://localhost:11434"


def test_resolve_profile_defaults_to_active_model(config_file):
    """No profile name given -> that layer's active_model is used."""
    loaded = config_module.load_config(config_file)
    assert config_module.resolve_profile(loaded, "dialogue")["model"] == "hermes3:70b"

    loaded["dialogue"]["active_model"] = "light"
    assert config_module.resolve_profile(loaded, "dialogue")["model"] == "llama3.1:8b"


def test_resolve_profile_does_not_mutate_the_loaded_config(config_file):
    """Resolving must not write the merged result back into the config tree."""
    loaded = config_module.load_config(config_file)
    config_module.resolve_profile(loaded, "arc", "light")
    assert loaded["arc"]["models"]["light"] == {"model": "qwen3-coder:30b"}


def test_resolve_profile_unknown_name_raises_config_error_naming_it(config_file):
    """A typo'd profile must fail clearly, not with a bare KeyError."""
    loaded = config_module.load_config(config_file)
    with pytest.raises(config_module.ConfigError) as excinfo:
        config_module.resolve_profile(loaded, "arc", "ludicrous")
    message = str(excinfo.value)
    assert "ludicrous" in message
    assert "arc" in message


def test_resolve_profile_unknown_layer_raises_config_error(config_file):
    loaded = config_module.load_config(config_file)
    with pytest.raises(config_module.ConfigError):
        config_module.resolve_profile(loaded, "nosuchlayer", "heavy")


# ── profile enumeration: what --test-mode iterates ────────────────────────────

def test_profile_names_returns_every_configured_profile(config_file):
    loaded = config_module.load_config(config_file)
    assert sorted(config_module.profile_names(loaded, "dialogue")) == ["heavy", "light"]


def test_profile_names_unknown_layer_raises_config_error(config_file):
    loaded = config_module.load_config(config_file)
    with pytest.raises(config_module.ConfigError):
        config_module.profile_names(loaded, "nosuchlayer")


# ── up-front cross-stage validation (issue #10) ───────────────────────────────

def test_validate_profile_for_stages_accepts_a_name_present_everywhere(config_file):
    loaded = config_module.load_config(config_file)
    config_module.validate_profile_for_stages(
        loaded, "light", ["arc", "segment", "dialogue"])


def test_validate_profile_for_stages_names_the_missing_layer(config_file):
    """--stage all --model-profile X must fail before any GPU time is spent."""
    loaded = config_module.load_config(config_file)
    del loaded["segment"]["models"]["light"]
    with pytest.raises(config_module.ConfigError) as excinfo:
        config_module.validate_profile_for_stages(
            loaded, "light", ["arc", "segment", "dialogue"])
    message = str(excinfo.value)
    assert "segment" in message
    assert "light" in message


def test_validate_profile_for_stages_accepts_none_as_per_layer_active_model(config_file):
    """No --model-profile means each layer uses its own active_model: always valid."""
    config_module.validate_profile_for_stages(
        config_module.load_config(config_file), None, ["arc", "segment", "dialogue"])


# ── output paths ──────────────────────────────────────────────────────────────

def test_output_root_is_output_dir_joined_with_the_pack_name(config_file):
    loaded = config_module.load_config(config_file)
    root = config_module.output_root(loaded, "campaigns/ashiorid")
    assert root.name == "ashiorid"
    assert root.parent.name == "output"


def test_output_root_tolerates_a_trailing_slash_on_the_pack_path(config_file):
    loaded = config_module.load_config(config_file)
    assert config_module.output_root(loaded, "campaigns/ashiorid/").name == "ashiorid"


def test_segment_dir_and_brief_path_are_derived_from_output_root(config_file):
    loaded = config_module.load_config(config_file)
    root = config_module.output_root(loaded, "campaigns/ashiorid")
    segment_dir = config_module.segment_dir(loaded, "campaigns/ashiorid", "seg-001")
    assert segment_dir == root / "segments" / "seg-001"
    assert config_module.brief_path(loaded, "campaigns/ashiorid", "seg-001") == \
        segment_dir / "brief.yaml"
    assert config_module.arc_plan_path(loaded, "campaigns/ashiorid") == root / "arc_plan.yaml"


# ── the shipped config actually works ─────────────────────────────────────────

def test_the_repo_generation_yaml_loads_and_resolves_every_profile():
    """The config file this utility ships with must satisfy its own loader."""
    import pathlib

    shipped = (pathlib.Path(__file__).resolve().parents[1]
               / "config" / "generation.yaml")
    loaded = config_module.load_config(shipped)

    for layer in ("arc", "segment", "dialogue"):
        names = config_module.profile_names(loaded, layer)
        assert names, f"{layer} has no model profiles"
        for name in names:
            resolved = config_module.resolve_profile(loaded, layer, name)
            assert resolved["model"], f"{layer}/{name} resolved without a model"
            assert resolved["base_url"].startswith("http")
            assert resolved["provider"] == "ollama"

    assert loaded["state"]["carry_keys"]
    assert loaded["dialogue"]["takes_per_slot"] == 3
    assert loaded["dialogue"]["neutral_takes"] == 1


# ── every failure is a ConfigError, and every failure is logged ───────────────

def test_load_config_on_a_directory_raises_config_error(tmp_path):
    """A path that exists but is not a file must still be a ConfigError."""
    with pytest.raises(config_module.ConfigError):
        config_module.load_config(tmp_path)


def test_output_root_without_an_output_block_raises_config_error(config_file):
    """A config missing `output:` must fail as a ConfigError, not a KeyError."""
    loaded = config_module.load_config(config_file)
    del loaded["output"]
    with pytest.raises(config_module.ConfigError):
        config_module.output_root(loaded, "campaigns/ashiorid")


def test_output_root_without_a_dir_key_raises_config_error(config_file):
    loaded = config_module.load_config(config_file)
    loaded["output"] = {}
    with pytest.raises(config_module.ConfigError):
        config_module.output_root(loaded, "campaigns/ashiorid")


@pytest.mark.parametrize("call", [
    lambda m, c: m.resolve_profile(c, "arc", "ludicrous"),
    lambda m, c: m.resolve_profile(c, "nosuchlayer"),
    lambda m, c: m.profile_names(c, "nosuchlayer"),
    lambda m, c: m.validate_profile_for_stages(c, "light", ["arc", "nosuchlayer"]),
])
def test_every_error_path_logs_at_error_before_raising(config_file, caplog, call):
    """A long unattended run is only as debuggable as its ERROR lines."""
    loaded = config_module.load_config(config_file)
    caplog.set_level("ERROR")
    with pytest.raises(config_module.ConfigError):
        call(config_module, loaded)
    assert caplog.records, "the failure was raised without an ERROR log line"
    assert all(record.levelname == "ERROR" for record in caplog.records)
