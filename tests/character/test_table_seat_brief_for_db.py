"""P3.5 review: agent_handlers.table.brief_for against a real DB (D&D profiles).

The generated brief_for was monkeypatched in its own tests and passed the agent
config to character.config.load() as a path; this pins the production path.
"""
import pathlib

import pytest

from pending import require

seat = require("agent_handlers.table", "app/agent_handlers/table.py", wp="P3.5")
from character.generator import pack_profiles  # noqa: E402

PACK = pathlib.Path(__file__).resolve().parents[2] / "campaigns" / "ashiorid"

pytestmark = pytest.mark.integration


def test_brief_for_reads_the_seat_brief_and_leaves_no_open_transaction(pg_conn, monkeypatch):
    pack_profiles.load_pack(pg_conn, PACK / "profiles", PACK / "cast", campaign="ashiorid")
    pg_conn.commit()
    monkeypatch.setitem(seat._CONN_CACHE, "conn", pg_conn)
    cfg = {"role": "table_seat", "table": {"character_slug": "chadwick", "pack_dir": str(PACK)}}
    text = seat.brief_for(cfg, "tuber_1")
    assert text.startswith("# Who you are") and "Chadwick" in text
    import psycopg2.extensions as ext
    assert pg_conn.get_transaction_status() == ext.TRANSACTION_STATUS_IDLE
