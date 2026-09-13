#!/usr/bin/env python3
"""
voice_registry.py
The symbolic voice registry (roundtable_stream_design.md v1.1 §7.2) — the shared
seam between story data and the TTS backend.

A show header names a voice symbolically (``"voice": "alto_bright"``). This
module is the only place that knows a symbolic name maps to a backend fragment
(``{provider: piper, model_path: /data/voices/….onnx}``). Two callers, two
different capabilities:

  * **message-api / episode_validator (V1, §8.1)** — needs to answer "is this
    voice name known?" at upload time. It has the registry mounted but NO
    /data/voices mount, so it must never touch the filesystem. Use
    ``names()`` / ``is_known()``.
  * **a TTS-capable container (V2, §8.2)** — needs to answer "does the asset
    this name points at actually load?". Use ``verify()`` or the ``--verify``
    CLI, which runs at GM container start.

That split is the whole reason the registry exists: it turns an unanswerable
file-stat into an answerable config lookup for the tier that can't see voices.

Load failures are LOUD but never fatal here: a missing registry yields an empty
one and an error on stderr, so a worker still boots (and its per-slot
``voice.speakers`` config still works as the fallback pool). The caller decides
severity — ``--verify`` exits non-zero, while ``resolve()`` returning None just
means "fall back to slot config" in TTSClient.voice_for.
"""
import argparse
import logging
import os
import sys
from pathlib import Path

import yaml

logging.basicConfig(
    stream=sys.stderr,
    level=os.environ.get("VOICE_REGISTRY_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s voice_registry %(message)s",
)
log = logging.getLogger("voice_registry")

# Do NOT let this module's basicConfig become the whole process's logging setup.
#
# basicConfig installs a handler on the ROOT logger, so once any process imports
# voice_registry, every third-party library that logs through the root logger
# inherits this handler AND this format. In the GM container that meant kafka's
# chatty INFO stream ("Initializing connection for node_id...", metadata refresh,
# transport open/close) was printed into the director's pane — which IS the
# roundtable Show Log on the live broadcast — every line mislabelled
# "voice_registry". The main channel's log pane became unreadable noise.
#
# Raising kafka's own level is the narrow fix: this module keeps its logging,
# the broadcast pane keeps showing the SHOW.
for _noisy in ("kafka", "kafka.conn", "kafka.client", "kafka.producer",
               "kafka.consumer", "kafka.cluster", "kafka.coordinator",
               "kafka.metrics", "kafka.protocol"):
    logging.getLogger(_noisy).setLevel(
        os.environ.get("KAFKA_LOG_LEVEL", "WARNING"))

TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


#: Registry schema version this module understands.
SUPPORTED_VERSION = 1

#: Keys a show header's persona block may NEVER carry (§8.1 rule 5): they are
#: platform detail, and story data referencing one bypasses the registry.
PLATFORM_ONLY_KEYS = ("model_path", "voice_id", "base_url")

_ENV_PATH = "VOICE_REGISTRY_PATH"
_cache = {}


def default_path():
    """Registry location: env override, then in-container /config/voices.yaml,
    then the repo-relative config/voices.yaml for local runs/tests. Mirrors
    build_layout._default_dir's in-container-first convention."""
    env = os.environ.get(_ENV_PATH)
    if env:
        return env
    in_container = Path("/config/voices.yaml")
    if in_container.is_file():
        return str(in_container)
    return str(Path(__file__).resolve().parents[1] / "config" / "voices.yaml")


def load(path=None, force=False):
    """Load and cache the registry. Returns the ``voices`` mapping (name ->
    backend fragment), or {} when the file is missing/unreadable/malformed —
    never raises, so a worker boots even with a broken registry.

    Cached per resolved path; pass force=True to re-read (tests, hot reload).
    """
    path = path or default_path()
    key = str(path)
    if not force and key in _cache:
        return _cache[key]

    _trace("load path=%s", key)
    data = None
    try:
        with open(key, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError:
        log.error("voice registry not found at %s — symbolic voice names will "
                  "not resolve (falling back to per-slot voice.speakers config)", key)
    except (OSError, yaml.YAMLError) as exc:
        log.error("voice registry %s is unreadable/malformed: %s", key, exc)

    voices = {}
    if isinstance(data, dict):
        version = data.get("version")
        if version != SUPPORTED_VERSION:
            log.error("voice registry %s has version %r, expected %r — refusing "
                      "to load it", key, version, SUPPORTED_VERSION)
        else:
            raw = data.get("voices")
            if isinstance(raw, dict):
                for name, fragment in raw.items():
                    if not isinstance(fragment, dict):
                        log.error("voice %r: entry must be a mapping, got %s — skipped",
                                  name, type(fragment).__name__)
                        continue
                    if not fragment.get("provider"):
                        log.error("voice %r: missing required 'provider' — skipped", name)
                        continue
                    voices[str(name)] = dict(fragment)
            else:
                log.error("voice registry %s has no 'voices' mapping", key)
    elif data is not None:
        log.error("voice registry %s must be a mapping, got %s",
                  key, type(data).__name__)

    log.debug("loaded %d voice(s) from %s", len(voices), key)
    _cache[key] = voices
    return voices


def names(path=None):
    """Sorted known voice names. The V1 (§8.1) membership check reads this and
    touches no voice files."""
    return sorted(load(path))


def is_known(name, path=None):
    """True when `name` is a registry entry. Pure config lookup — safe in
    message-api, which has no /data/voices mount."""
    return name in load(path)


def resolve(name, path=None):
    """Backend fragment for a symbolic name, or None when unknown.

    A copy is returned, so a caller merging it (TTSClient.voice_for) can never
    mutate the cached registry.
    """
    fragment = load(path).get(name)
    return dict(fragment) if fragment is not None else None


def resolve_model_path(model, voices_dir=None):
    """Map a registry model_path to where the asset actually lives.

    The registry stores the CONTAINER path (/data/voices/x.onnx) because that is
    what the synthesizing container passes to piper. On a dev box or in CI the
    same files live in the repo's voices/ directory, so a bare host-side
    --verify would report every voice missing while nothing is actually wrong.

    `voices_dir` (CLI --voices-dir, else $VOICES_DIR) rebases the basename onto
    that directory. Unset and the registry path is used verbatim — which is the
    in-container case, where it is already correct.
    """
    voices_dir = voices_dir or os.environ.get("VOICES_DIR")
    if not voices_dir:
        return model
    return str(Path(voices_dir) / Path(model).name)


def verify(path=None, voices_dir=None):
    """V2 inventory check (§8.2): confirm every registry entry's asset is
    actually usable where voices are mounted.

    For a local-model provider (``piper``, and its ``kokoro`` alias), that means
    the model file exists AND PiperVoice.load succeeds — a present-but-corrupt
    .onnx is a real failure mode, and loading also warms tts_client's
    _LOCAL_VOICES cache (keyed by resolved path). Cloud providers have no local
    asset to check, so they are reported as "skipped".

    `voices_dir` rebases container paths for host/CI runs (see
    resolve_model_path).

    Returns (ok, results) where results is a list of
    (name, status, detail) and status is "ok" | "skipped" | "failed".
    ok is True only when nothing failed. Never raises.
    """
    voices = load(path)
    if not voices:
        log.error("voice registry is empty or unloadable — nothing to verify")
        return False, []

    results = []
    for name in sorted(voices):
        fragment = voices[name]
        provider = str(fragment.get("provider") or "").lower()

        if provider not in ("piper", "kokoro"):
            results.append((name, "skipped", f"provider {provider!r} has no local asset"))
            continue

        model = fragment.get("model_path")
        if not model:
            # Name the provider that actually failed: "piper" and "kokoro" both
            # reach here (kokoro is an alias for the same local .onnx setup in
            # tts_client._BACKENDS), and hardcoding "piper" in this message sent
            # a reader looking at the wrong registry entry.
            results.append((name, "failed",
                            f"provider is {provider!r} (local model) but no model_path"))
            continue
        model = resolve_model_path(model, voices_dir)
        if not Path(model).exists():
            results.append((name, "failed", f"model not found: {model}"))
            continue

        try:
            from tts_client import _load_local_voice
            _load_local_voice(model)
        except ImportError as exc:
            results.append((name, "skipped", f"piper package unavailable ({exc})"))
            continue
        except Exception as exc:
            results.append((name, "failed",
                            f"{Path(model).name} failed to load: "
                            f"{type(exc).__name__}: {exc}"))
            continue

        results.append((name, "ok", Path(model).name))

    ok = not any(status == "failed" for _, status, _ in results)
    return ok, results


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Symbolic voice registry (roundtable_stream_design.md v1.1 §7.2)")
    parser.add_argument("--registry", default=None,
                        help="registry path (default: $VOICE_REGISTRY_PATH, "
                             "/config/voices.yaml, then repo config/voices.yaml)")
    parser.add_argument("--voices-dir", default=None,
                        help="rebase registry model_path basenames onto this dir "
                             "(default: $VOICES_DIR). Use on a dev box/CI where "
                             "voices live in repo voices/ rather than the "
                             "container's /data/voices.")
    parser.add_argument("--verify", action="store_true",
                        help="V2 inventory check: confirm every entry's asset loads. "
                             "Exits non-zero on any failure.")
    parser.add_argument("--list", action="store_true",
                        help="print known voice names, one per line")
    args = parser.parse_args(argv)

    path = args.registry or default_path()

    if args.list:
        for name in names(path):
            print(name)
        return 0

    if args.verify:
        ok, results = verify(path, voices_dir=args.voices_dir)
        if not results:
            print(f"voice registry {path}: EMPTY / UNLOADABLE", file=sys.stderr)
            return 1
        width = max(len(name) for name, _, _ in results)
        for name, status, detail in results:
            marker = {"ok": "OK     ", "skipped": "SKIP   ", "failed": "FAILED "}[status]
            print(f"{marker} {name:<{width}}  {detail}")
        failed = [name for name, status, _ in results if status == "failed"]
        print(f"\n{len(results)} voice(s) in {path}; "
              f"{len(failed)} failed" + (f": {', '.join(failed)}" if failed else ""))
        if failed:
            print("a show casting a failed voice will refuse to air on EVERY channel "
                  "(docs: roundtable_stream_design.md §8.2)", file=sys.stderr)
        return 0 if ok else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
