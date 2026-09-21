"""TOML configuration loader for the D&D-agents benchmark.

The canonical form for "how to run the bench" lives in a TOML file
(`conf/benchmark.toml` by default). CLI flags are thin overrides of that
config, never a second config source — see the `override_config_from_cli`
helper for the exact precedence order:

    1. CLI flag value (if provided)          ← highest
    2. env BENCH_MARKER_CONFIG (if set)      ← next
    3. conf/benchmark.toml                   ← then
    4. built-in defaults below               ← last, lowest

`tomllib` is CPython ≥ 3.11.

The TOML shape groups settings under four sections — `host`, `models`,
`run`, `output`. Flat keys inside `run` are the CLI flags by name.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]            # utility → benchmarker → utilities → repo
LIB_DIR = Path(__file__).resolve().parent
DEFAULT_CONF = LIB_DIR.parent / "conf" / "benchmark.toml"
ENV_CONFIG = "BENCH_MARKER_CONFIG"

_HOST_DEFAULTS = {
    "ollama": "http://localhost:11434",
    "vllm": "http://localhost:8000",
}


@dataclass
class Config:
    """Runtime configuration. All CLI flags and TOML keys normalize to a
    set of these fields so `Runner` doesn't care which source the values
    came from."""
    host: str = "ollama"
    base_url: str = _HOST_DEFAULTS["ollama"]
    models: list[str] = None  # type: ignore[assignment]
    ctx_k: list[int] = None  # type: ignore[assignment]
    output_dir: Path | str = "utilities/benchmarker/output"
    report_path: Path | str = "utilities/benchmarker/output/report.md"
    think: bool = False
    temperature: float = 0.7
    concurrency_n: int = 3
    skip_full_round: bool = False
    raw: dict = None  # type: ignore[assignment]  # the full TOML dict

    def __post_init__(self) -> None:
        self.models = self.models or []
        self.ctx_k = self.ctx_k or []
        self.raw = self.raw or {}


def _coerce_bool(v: bool | str | int) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v != 0
    s = str(v).strip().lower()
    if s in ("1", "true", "t", "yes", "y", "on"):
        return True
    if s in ("0", "false", "f", "no", "n", "off"):
        return False
    raise ValueError(f"could not parse {v!r} as a bool")


def _split_csv(v: list[str | int] | str) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(x).strip() for x in str(v).split(",") if str(x).strip()]


def _split_int_csv(v: list[int | str] | str) -> list[int]:
    return [int(x) for x in _split_csv(v)]


def load_config(path: Path | str | None = None) -> Config:
    """Load the TOML config at `path` (defaults to `conf/benchmark.toml`
    next to this file) and return a `Config`. A file with missing
    sections is fine; missing keys fall back to the dataclass defaults.
    A non-existent path raises `FileNotFoundError`."""
    p = Path(path) if path else DEFAULT_CONF
    if not p.is_file():
        raise FileNotFoundError(f"benchmark config not found: {p}")
    with p.open("rb") as f:
        data = tomllib.load(f)

    host_sec = data.get("host", {}) or {}
    models_sec = data.get("models", {}) or {}
    run_sec = data.get("run", {}) or {}
    output_sec = data.get("output", {}) or {}

    host_name = str(host_sec.get("name", "ollama")).lower()
    base_url = host_sec.get("base_url") or _HOST_DEFAULTS.get(host_name)
    if not base_url:
        raise ValueError(
            f"config {host_sec.get('name')!r} needs a `base_url`; "
            f"set it under [[host]] in {p}")

    models = _split_csv(models_sec.get("list") or models_sec.get("models") or [])
    ctx_k = _split_int_csv(run_sec.get("ctx_k") or run_sec.get("context_k") or [])
    return Config(
        host=host_name,
        base_url=str(base_url),
        models=models,
        ctx_k=ctx_k,
        output_dir=output_sec.get("dir", "utilities/benchmarker/output"),
        report_path=output_sec.get("report", "utilities/benchmarker/output/report.md"),
        think=_coerce_bool(run_sec.get("think", False)),
        temperature=float(run_sec.get("temperature", 0.7)),
        concurrency_n=int(run_sec.get("concurrency_n", 3)),
        skip_full_round=_coerce_bool(run_sec.get("skip_full_round", False)),
        raw=data,
    )


def override_config_from_cli(cfg: Config, *,
                             host: str | None = None,
                             base_url: str | None = None,
                             models: list[str] | None = None,
                             ctx_k: list[int] | None = None,
                             output_dir: Path | str | None = None,
                             report_path: Path | str | None = None,
                             think: bool | None = None,
                             temperature: float | None = None,
                             concurrency_n: int | None = None,
                             skip_full_round: bool | None = None,
                             dry_run: bool | None = None,
                             ) -> Config:
    """Layer non-None CLI values on top of a loaded TOML config. The
    dataclass is mutated in place and returned, so callers can chain."""
    if host is not None:
        cfg.host = host
        if not base_url:
            cfg.base_url = _HOST_DEFAULTS.get(host, cfg.base_url)
    if base_url is not None:
        cfg.base_url = base_url
    if models is not None:
        cfg.models = models
    if ctx_k is not None:
        cfg.ctx_k = ctx_k
    if output_dir is not None:
        cfg.output_dir = output_dir
    if report_path is not None:
        cfg.report_path = report_path
    if think is not None:
        cfg.think = think
    if temperature is not None:
        cfg.temperature = temperature
    if concurrency_n is not None:
        cfg.concurrency_n = concurrency_n
    if skip_full_round is not None:
        cfg.skip_full_round = skip_full_round
    return cfg


def resolve_paths(cfg: Config, repo_root: Path) -> None:
    """Make relative `output_dir` / `report_path` absolute against the
    repo root, so the runner can write them regardless of where the
    caller launched from. Idempotent once already-absolute."""
    if not (Path(cfg.output_dir)).is_absolute():
        cfg.output_dir = (repo_root / cfg.output_dir)
    if not (Path(cfg.report_path)).is_absolute():
        cfg.report_path = (repo_root / cfg.report_path)
