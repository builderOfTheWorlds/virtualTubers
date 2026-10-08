"""P3.6 review: table_gm.context_provider against a real DB with the D&D profiles.

The generated module's context_provider was stubbed in its own tests and called
functions that do not exist; this pins the rewritten one to the real stores.
"""
import pathlib

import pytest

from pending import require

gm = require("agent_handlers.table_gm", "app/agent_handlers/table_gm.py", wp="P3.6")
from character.generator import pack_profiles  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
PACK = REPO / "campaigns" / "ashiorid"

pytestmark = pytest.mark.integration


def test_gm_context_from_db_has_truth_blocks_and_the_active_sheet(pg_conn, monkeypatch):
    pack_profiles.load_pack(pg_conn, PACK / "profiles", PACK / "cast", campaign="ashiorid")
    pg_conn.commit()
    monkeypatch.setattr(gm, "_character_conn", lambda: pg_conn)
    cfg = {"system_prompt": "You are the GM of Ashiorid.", "table": {
        "gm_slug": "gm", "pack_dir": str(PACK), "seats": ["tuber_1"],
        "seat_slugs": {"tuber_0": "gm", "tuber_1": "chadwick", "tuber_2": "Leena"},
        "gm_blocks_order": ["truth", "secrets", "unlocks", "table_rules", "style"]}}
    contract = {"scene_id": "s1", "canon_goal": "Open the vault door.", "must_resolve": ["door"]}
    text = gm.context_provider(cfg, contract)
    assert "You are the GM of Ashiorid." in text
    assert "# GM block: truth" in text and "# GM block: secrets" in text
    assert "## tuber_1" in text and "Chadwick" in text
    assert "## tuber_2" not in text            # only ACTIVE seats are at the table
    assert "Open the vault door." in text
    assert len(text) <= 24000
