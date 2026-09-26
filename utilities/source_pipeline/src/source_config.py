"""Per-source config loading for the source pipeline."""
import logging
import pathlib
from typing import Union

import yaml

log = logging.getLogger(__name__)


class SourceConfigError(Exception):
    """Raised for a missing, malformed, or incomplete source config."""


REQUIRED = [("source", "id"), ("source", "text"), ("toc", "path"), ("toc", "format"), ("split", "output_dir")]


def load_source_config(path: Union[str, pathlib.Path], repo_root: Union[str, pathlib.Path]) -> dict:
    """Load a source YAML and resolve its repo-relative paths to absolute ones."""
    log.debug("load_source_config called with path=%s repo_root=%s", path, repo_root)
    path = pathlib.Path(path)
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.error("could not load source config %s: %s", path, exc)
        raise SourceConfigError(f"could not load source config {path}: {exc}") from exc

    missing = [".".join(k) for k in REQUIRED if not (cfg.get(k[0]) or {}).get(k[1])]
    if missing:
        raise SourceConfigError(f"{path}: missing required keys: {', '.join(missing)}")

    root = pathlib.Path(repo_root)
    cfg["source"]["text"] = root / cfg["source"]["text"]
    cfg["toc"]["path"] = root / cfg["toc"]["path"]
    cfg["split"]["output_dir"] = root / cfg["split"]["output_dir"]
    aliases = cfg["split"].get("heading_aliases") or {}
    cfg["split"]["heading_aliases"] = {int(k): list(v) for k, v in aliases.items()}
    log.debug("load_source_config returning source id=%s", cfg["source"]["id"])
    return cfg
