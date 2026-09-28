"""Fakes and helpers for the character v4 Phase 2 tests (WP-10o, WP-11, WP-14).

Kept apart from tests/character/fakes.py so Phase 2 and Phase 3 test authors
don't edit the same file. Like fakes.py, nothing here imports a `character.*`
module, so importing it never trips the pending guard (tests/character/pending.py)
and never needs a database.

- Pack copies: `copy_office_pack(tmp_path)` and `copy_pack(src, tmp_path)` copy
  a campaign pack into tmp_path, so a test can edit or export into it without
  touching the repo's campaigns/ directory.
- `edit_profile(path, mutate)`: re-dump a profile YAML after a mutation (its
  sha256 changes).
- `RecordingTransport`: an `httpx.MockTransport` that replays canned replies
  and records every request body, for llm.py and embeddings.py.
- `ollama_reply(content)` / `embeddings_reply(vectors)`: response bodies in
  Ollama `/api/chat` and OpenAI `/embeddings` shape.
- `db_env(dsn)`: CHARACTER_DB_* environment variables pointing at a test DSN,
  for running a CLI as a subprocess against the `pg` fixture's database.
"""
import json
import logging
import os
import shutil
from pathlib import Path

import yaml

from fakes import OFFICE_CAMPAIGN, OFFICE_SLUGS  # noqa: F401  (re-exported for Phase 2 tests)

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
OFFICE_PACK = REPO_ROOT / "campaigns" / "ashiorid_office"
HPTEST_PACK = REPO_ROOT / "campaigns" / "hptest"
OFFICE_PROFILES = OFFICE_PACK / "profiles"
OFFICE_CAST = OFFICE_PACK / "cast"

#: A string that appears nowhere in the real profiles. Tests put it in a
#: profile's `backstory.truth` and check it never reaches a character-facing row.
TRUTH_MARKER = "GM-ONLY-TRUTH-MARKER-7f3c"

#: The slider keys of app/character_schema.py SLIDER_DEFAULTS, spelled out so
#: this module stays import-free.
SLIDER_KEYS = ("head_width", "head_taper", "eye_size", "eye_spacing", "jaw_width",
               "nose_length", "ear_size", "build")


def read_yaml(path):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_yaml(path, doc):
    Path(path).write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
                          encoding="utf-8")


def copy_pack(src, tmp_path, name=None):
    """Copy a whole campaign pack directory into tmp_path; return the copy's Path."""
    dest = Path(tmp_path) / (name or Path(src).name)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.wav", "*.mp3"))
    log.debug("copied pack %s -> %s", src, dest)
    return dest


def copy_office_pack(tmp_path):
    """A full copy of campaigns/ashiorid_office; returns (pack, profiles_dir, cast_dir)."""
    pack = copy_pack(OFFICE_PACK, tmp_path)
    return pack, pack / "profiles", pack / "cast"


def edit_profile(path, mutate):
    """Load a profile YAML, apply `mutate(doc)` in place, write it back."""
    doc = read_yaml(path)
    mutate(doc)
    write_yaml(path, doc)
    return doc


def snapshot_files(directory, pattern="*.yaml"):
    """{name: bytes} for every file matching pattern (a byte-level before/after check)."""
    return {p.name: p.read_bytes() for p in sorted(Path(directory).glob(pattern))}


def ollama_reply(content, model="qwen3.8:27b"):
    """An Ollama /api/chat non-streaming reply whose message content is `content`.

    A dict is serialised to JSON text; a str is used as-is (so a test can send
    invalid JSON).
    """
    text = content if isinstance(content, str) else json.dumps(content)
    return {"model": model, "message": {"role": "assistant", "content": text}, "done": True}


def embeddings_reply(vectors, model="nomic-embed-text"):
    """An OpenAI-shape /embeddings reply for `vectors`, in index order."""
    return {"object": "list", "model": model,
            "data": [{"object": "embedding", "index": i, "embedding": list(v)}
                     for i, v in enumerate(vectors)]}


class RecordingTransport:
    """httpx.MockTransport that replays replies in order and records requests.

    `replies` is a list; each item is one of:
      - a dict: returned as a 200 JSON body
      - (status, body): body is a dict (JSON) or str (text)
      - a callable(request_json) -> dict or (status, body): computed per request
      - an Exception instance: raised as the transport error
    The last item repeats when the list runs out.
    `.requests` holds {"url", "method", "json"} for every request made.
    """

    def __init__(self, replies):
        import httpx

        if not isinstance(replies, list) or not replies:
            raise ValueError("RecordingTransport needs a non-empty list of replies")
        self._httpx = httpx
        self._replies = replies
        self.requests = []
        self.transport = httpx.MockTransport(self._handle)

    def client(self):
        return self._httpx.Client(transport=self.transport)

    def _handle(self, request):
        body = json.loads(request.content.decode("utf-8")) if request.content else None
        self.requests.append({"url": str(request.url), "method": request.method, "json": body})
        reply = self._replies[min(len(self.requests) - 1, len(self._replies) - 1)]
        if callable(reply) and not isinstance(reply, Exception):
            reply = reply(body)
        if isinstance(reply, Exception):
            raise reply
        status, payload = reply if isinstance(reply, tuple) else (200, reply)
        if isinstance(payload, str):
            return self._httpx.Response(status, text=payload)
        return self._httpx.Response(status, json=payload)


def db_env(dsn, password="character-test", base=None):
    """os.environ (or `base`) plus CHARACTER_DB_* for `dsn`, minus PYTHONPATH.

    pgserver uses trust auth, so any non-empty password works; character.db
    refuses to connect without one (WP-03 T03.6). The port defaults to 5432
    when the DSN is a unix-socket DSN without one.
    """
    from psycopg2.extensions import parse_dsn

    parts = parse_dsn(dsn)
    env = {key: value for key, value in (os.environ if base is None else base).items()
           if key != "PYTHONPATH" and not key.startswith("CHARACTER_DB_")
           and not key.startswith("CHARACTER_INGEST_DB_")}
    env.update({
        "CHARACTER_DB_HOST": parts.get("host", "localhost"),
        "CHARACTER_DB_PORT": str(parts.get("port", "5432")),
        "CHARACTER_DB_NAME": parts["dbname"],
        "CHARACTER_DB_USER": parts.get("user", "postgres"),
        "CHARACTER_DB_PASSWORD": parts.get("password") or password,
    })
    return env


def count_rows(conn, table, where="", params=()):
    """SELECT count(*) FROM <table> [WHERE ...]; table names are test constants."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} {where}", params)
        return cur.fetchone()[0]
