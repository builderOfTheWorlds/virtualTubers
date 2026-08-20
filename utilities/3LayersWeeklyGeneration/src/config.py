"""
Configuration loader for the hierarchical 3-layer offline content generator.

This module handles loading and resolving the single YAML configuration file
used by the 3-layer generation pipeline (arc, segment, dialogue). It manages
profile resolution with defaults merging, validates cross-stage profile usage,
and computes output paths.
"""
import logging
import pathlib
from typing import Dict, List, Optional, Union

import yaml

log = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised for any configuration problem detected by this module."""
    pass


def load_config(path: Union[str, pathlib.Path]) -> dict:
    """
    Read and parse the YAML config at `path`.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        The parsed configuration mapping.

    Raises:
        ConfigError: If the file is missing, not readable, or malformed YAML.
    """
    log.debug("load_config called with path=%s", path)
    try:
        path = pathlib.Path(path)
        if not path.is_file():
            raise ConfigError(f"config file not found or not a file: {path}")
        content = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        if parsed is None:
            parsed = {}
        return parsed
    except yaml.YAMLError as exc:
        log.error("malformed YAML in config file %s: %s", path, exc)
        raise ConfigError(f"malformed YAML in config file {path}: {exc}") from exc
    except OSError as exc:
        log.error("could not read config file %s: %s", path, exc)
        raise ConfigError(f"could not read config file {path}: {exc}") from exc


def resolve_profile(config: dict, layer: str, profile_name: Optional[str] = None) -> dict:
    """
    Resolve one layer's model profile to a complete, flat llm config dict.

    Args:
        config: The loaded configuration.
        layer: One of "arc", "segment", "dialogue".
        profile_name: Name of the profile to resolve. If None, uses active_model.

    Returns:
        A complete flat dictionary for LLM client construction.

    Raises:
        ConfigError: For unknown layers, missing models map, or unknown profile names.
    """
    log.debug("resolve_profile called with layer=%s, profile_name=%s", layer, profile_name)
    if layer not in config:
        log.error("unknown layer %r requested (configured: %s)", layer, sorted(config.keys()))
        raise ConfigError(f"unknown layer {layer!r} (configured: {sorted(config.keys())})")

    layer_config = config[layer]
    if "models" not in layer_config:
        log.error("layer %r has no 'models' map", layer)
        raise ConfigError(f"layer {layer!r} has no 'models' map")

    models = layer_config["models"]
    if profile_name is None:
        profile_name = layer_config["active_model"]
        log.debug("using active_model %r for layer %r", profile_name, layer)

    if profile_name not in models:
        configured_profiles = sorted(models.keys())
        log.error("unknown profile %r for layer %r (configured: %s)", profile_name, layer, configured_profiles)
        raise ConfigError(f"unknown profile {profile_name!r} for layer {layer!r} (configured: {configured_profiles})")

    profile = models[profile_name]
    defaults = config.get("defaults", {})
    resolved = dict(defaults)
    resolved.update(profile)
    log.debug("resolved profile %r to %r", profile_name, resolved)
    return resolved


def profile_names(config: dict, layer: str) -> List[str]:
    """
    Get all profile names configured for a layer.

    Args:
        config: The loaded configuration.
        layer: One of "arc", "segment", "dialogue".

    Returns:
        List of profile names in YAML declaration order.

    Raises:
        ConfigError: For unknown layers.
    """
    log.debug("profile_names called with layer=%s", layer)
    if layer not in config:
        log.error("unknown layer %r requested (configured: %s)", layer, sorted(config.keys()))
        raise ConfigError(f"unknown layer {layer!r} (configured: {sorted(config.keys())})")

    layer_config = config[layer]
    if "models" not in layer_config:
        log.error("layer %r has no 'models' map", layer)
        raise ConfigError(f"layer {layer!r} has no 'models' map")

    return list(layer_config["models"].keys())


def validate_profile_for_stages(config: dict, profile_name: Optional[str], stages: List[str]) -> None:
    """
    Validate that a profile name is defined in all stages it will be used.

    Args:
        config: The loaded configuration.
        profile_name: Name of the profile to check. If None, checks each layer's active_model.
        stages: List of stage names (arc, segment, dialogue) to validate against.

    Raises:
        ConfigError: If profile is missing from any stage.
    """
    log.debug("validate_profile_for_stages called with profile_name=%s, stages=%s", profile_name, stages)
    if profile_name is None:
        return

    for stage in stages:
        if stage not in config:
            log.error("unknown stage %r requested (configured: %s)", stage, sorted(config.keys()))
            raise ConfigError(f"unknown stage {stage!r} (configured: {sorted(config.keys())})")

        layer_config = config[stage]
        if "models" not in layer_config:
            log.error("stage %r has no 'models' map", stage)
            raise ConfigError(f"stage {stage!r} has no 'models' map")

        models = layer_config["models"]
        if profile_name not in models:
            log.error("profile %r not found in stage %r (configured: %s)", profile_name, stage, sorted(models.keys()))
            raise ConfigError(f"profile {profile_name!r} not found in stage {stage!r} (configured: {sorted(models.keys())})")


def output_root(config: dict, pack_path: str) -> pathlib.Path:
    """
    Compute the output root directory for a campaign pack.

    Args:
        config: The loaded configuration.
        pack_path: Path to the campaign pack directory.

    Returns:
        A pathlib.Path to the output root.

    Raises:
        ConfigError: If config lacks 'output' or 'output.dir'.
    """
    log.debug("output_root called with pack_path=%s", pack_path)
    if "output" not in config:
        log.error("config missing 'output' block")
        raise ConfigError("config missing 'output' block")

    output_config = config["output"]
    if "dir" not in output_config:
        log.error("config.output missing 'dir' key")
        raise ConfigError("config.output missing 'dir' key")

    output_dir = pathlib.Path(output_config["dir"])
    pack_name = pathlib.Path(pack_path).name
    return output_dir / pack_name


def segment_dir(config: dict, pack_path: str, segment_id: str) -> pathlib.Path:
    """
    Compute the segment directory path.

    Args:
        config: The loaded configuration.
        pack_path: Path to the campaign pack directory.
        segment_id: ID of the segment.

    Returns:
        A pathlib.Path to the segment directory.
    """
    return output_root(config, pack_path) / "segments" / segment_id


def brief_path(config: dict, pack_path: str, segment_id: str) -> pathlib.Path:
    """
    Compute the path to a segment's brief.yaml file.

    Args:
        config: The loaded configuration.
        pack_path: Path to the campaign pack directory.
        segment_id: ID of the segment.

    Returns:
        A pathlib.Path to the brief.yaml file.
    """
    return segment_dir(config, pack_path, segment_id) / "brief.yaml"


def arc_plan_path(config: dict, pack_path: str) -> pathlib.Path:
    """
    Compute the path to the arc plan file.

    Args:
        config: The loaded configuration.
        pack_path: Path to the campaign pack directory.

    Returns:
        A pathlib.Path to the arc_plan.yaml file.
    """
    return output_root(config, pack_path) / "arc_plan.yaml"
