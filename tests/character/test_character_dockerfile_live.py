"""WP-24 tests for services/character-updater/Dockerfile.live: the character-live image.

User decision 2026-09-28 (item 7), tracker P3b-Q4 (a): the live driver runs in
its own image, built from `services/character-updater/Dockerfile.live`, which
copies ALL of app/ (the driver imports app/campaign, app/office and the shared
modules, not just app/character) and campaigns/ (the pack). The compose
service `character-live` (character_wp24_compose.yaml, a hand step) builds
from it. Spec: tools/qwen_worker/specs/character_wp24_dockerfile_live.yaml.

Text checks only (no docker build here):
- base image style matches services/message-logger/Dockerfile (same FROM line,
  PYTHONUNBUFFERED, WORKDIR /app);
- COPY app/ into /app and campaigns/ into /campaigns (so jobs_story's
  repo-root-relative `campaigns/ashiorid_office` resolves to
  /campaigns/ashiorid_office, as config.py's DEFAULT_PATH resolves to
  /config/character.yaml);
- the entrypoint runs main.py and the default command is the live driver
  (`story-start --foreground`);
- no secret is baked into the image.
Guarded like the other pending v4 tests: skips while the Dockerfile is missing
(CHARACTER_V4_STRICT=1 makes it fail instead).
"""
import json
import re
from pathlib import Path

import pytest

from pending import skip_if_pending

TARGET = "services/character-updater/Dockerfile.live"
skip_if_pending(TARGET, wp="WP-24")

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGGER_DOCKERFILE = REPO_ROOT / "services" / "message-logger" / "Dockerfile"


def _instructions(path):
    """[(INSTRUCTION, args)] with comments, blank lines and continuations folded."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\\\n", " ")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        keyword, _, args = line.partition(" ")
        out.append((keyword.upper(), args.strip()))
    return out


def _exec_form(args):
    """An exec-form (JSON array) ENTRYPOINT/CMD as a list; shell form as split words."""
    try:
        value = json.loads(args)
    except ValueError:
        return args.split()
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


@pytest.fixture(scope="module")
def live():
    return _instructions(REPO_ROOT / TARGET)


def _values(instructions, keyword):
    return [args for key, args in instructions if key == keyword]


def test_base_image_style_matches_the_message_logger(live):
    logger = _instructions(LOGGER_DOCKERFILE)
    assert live[0][0] == "FROM"
    assert live[0] == logger[0]                                  # same base image line
    assert any(re.fullmatch(r"PYTHONUNBUFFERED[= ]1", env) for env in _values(live, "ENV"))
    assert _values(live, "WORKDIR") == ["/app"]
    assert any("pip install" in run and "--no-cache-dir" in run for run in _values(live, "RUN"))
    copies = [c.split() for c in _values(live, "COPY")]
    assert ["services/character-updater/requirements.txt", "/app/requirements.txt"] in copies
    # the driver imports app/worker_control.py (redis) and app/llm_client.py (anthropic),
    # which the WP-06 image's requirements.txt does not carry
    pip = " ".join(run for run in _values(live, "RUN") if "pip install" in run)
    assert "redis" in pip and "anthropic" in pip


def test_copies_all_of_app_and_campaigns(live):
    copies = [tuple(p.rstrip("/") or "/" for p in c.split()) for c in _values(live, "COPY")]
    assert ("app", "/app") in copies                            # all of app/, not just app/character
    assert ("campaigns", "/campaigns") in copies
    assert ("services/character-updater/main.py", "/app/main.py") in copies
    assert ("config/character.yaml", "/config/character.yaml") in copies
    assert not any(src.startswith("app/character") for src, *_ in copies)   # the WP-06 narrow copy


def test_entrypoint_runs_the_live_driver(live):
    (entrypoint,) = _values(live, "ENTRYPOINT")
    (cmd,) = _values(live, "CMD")
    assert _exec_form(entrypoint) == ["python3", "/app/main.py"]
    assert _exec_form(cmd) == ["story-start", "--foreground"]


def test_no_secret_is_baked_in(live):
    text = (REPO_ROOT / TARGET).read_text(encoding="utf-8")
    assert not re.search(r"PASSWORD|SECRET|TOKEN", text, re.IGNORECASE)
    assert not any(key == "ARG" for key, _ in live)
