"""Shared fakes and small helpers for the character v4 tests (playbook §4).

Created in WP-03 by hand and extended as later WPs need more fakes. Nothing
here imports a `character.*` module, so importing it never trips the pending
guard (tests/character/pending.py) and never needs a database.

- `write_config(tmp_path, mutate)`: a copy of config/character.yaml with a
  mutation applied, for the config validation tests.
- `seed_character(conn, slug)`: insert one `characters` row with raw SQL, so
  store tests don't depend on the characters store.
- `FakeClock`: a settable monotonic clock for code that waits or batches.
"""
import copy
import logging
import uuid
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_CONFIG = REPO_ROOT / "config" / "character.yaml"

#: The OB-41 pilot cast (campaigns/ashiorid_office/profiles/*.yaml).
OFFICE_SLUGS = ("ceo", "tech_lead", "analyst", "engineer", "tester",
                "marketing", "office_manager", "party_member")
OFFICE_CAMPAIGN = "ashiorid_office"


def load_repo_config_dict():
    """The parsed repo config/character.yaml, as a plain dict (a fresh copy)."""
    with open(REPO_CONFIG, encoding="utf-8") as handle:
        return copy.deepcopy(yaml.safe_load(handle))


def write_config(tmp_path, mutate=None, name="character.yaml"):
    """Write the repo config, optionally mutated, to tmp_path; return the Path.

    `mutate(doc)` receives the whole parsed document (top key `character`)
    and edits it in place.
    """
    doc = load_repo_config_dict()
    if mutate is not None:
        mutate(doc)
    path = Path(tmp_path) / name
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    log.debug("wrote test config %s", path)
    return path


def seed_character(conn, slug, campaign=OFFICE_CAMPAIGN, retains_fragments=True, name=None):
    """Insert a characters row directly; return its id. Does not commit."""
    character_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO characters (id, slug, name, campaign, retains_fragments) "
            "VALUES (%s, %s, %s, %s, %s)",
            (character_id, slug, name or slug.replace("_", " ").title(), campaign,
             retains_fragments))
    return character_id


class FakeClock:
    """A monotonic clock under test control: `clock()` returns `now`."""

    def __init__(self, start=0.0):
        self.now = float(start)
        self.sleeps = []

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
