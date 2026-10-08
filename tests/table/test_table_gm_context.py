"""P2.7 frozen tests for app/table/gm_context.py (build plan P2.7, decision U7).

The GM context = every PRESENT GM-only block in config order, then any other
blocks (alphabetical), then every player sheet, then the arc segment, then the
scene contract. Adding a block is data, not code.
"""
import pytest

from pending import require

gc = require("table.gm_context", "app/table/gm_context.py", wp="P2.7")

BLOCKS = {
    "truth": "Leto fathered all four. The vault holds half a signet ring.",
    "secrets": [{"who": "Vigil", "secret": "four cribs", "reveal_at": "grovley-revelation"}],
    "unlocks": {"grovley-revelation": ["the-begene-program"]},
    "style": "Short declaratives. Concrete over atmospheric.",
    "zz_custom_block": "A brand-new block nobody wrote code for.",
}
ORDER = ["truth", "secrets", "unlocks", "table_rules", "style"]
SHEETS = {"tuber_1": "# Who you are\nChadwick, half-orc paladin.",
          "tuber_2": "# Who you are\nLeena, sorcerer."}
SEGMENT = {"id": "the-vault-arc", "synopsis": "The party opens the vault.", "continuity_out": "Ring found."}
CONTRACT = {"scene_id": "s1", "canon_goal": "Open the vault door.", "must_resolve": ["door opened"]}


def build(**over):
    kw = dict(gm_blocks=BLOCKS, gm_identity="You are the Game Master.", player_sheets=SHEETS,
              segment=SEGMENT, contract=CONTRACT, blocks_order=ORDER)
    kw.update(over)
    return gc.build_gm_context(**kw)


def test_contains_identity_every_block_sheets_segment_contract():
    text = build()
    for needle in ("You are the Game Master.", "Leto fathered all four", "four cribs",
                   "the-begene-program", "Short declaratives", "A brand-new block",
                   "Chadwick, half-orc paladin", "Leena, sorcerer", "The party opens the vault",
                   "Open the vault door", "door opened"):
        assert needle in text, needle


def test_order_identity_blocks_in_config_order_then_extras_then_sheets_segment_contract():
    text = build()
    marks = ["You are the Game Master.", gc.block_heading("truth"), gc.block_heading("secrets"),
             gc.block_heading("unlocks"), gc.block_heading("style"),
             gc.block_heading("zz_custom_block"), gc.PLAYERS_HEADING, gc.SEGMENT_HEADING,
             gc.CONTRACT_HEADING]
    positions = [text.index(m) for m in marks]
    assert positions == sorted(positions)


def test_absent_blocks_in_order_are_skipped_without_heading():
    text = build()
    assert gc.block_heading("table_rules") not in text


def test_extra_blocks_alphabetical():
    text = build(gm_blocks={"b_extra": "B", "a_extra": "A", "truth": "T"})
    assert text.index(gc.block_heading("a_extra")) < text.index(gc.block_heading("b_extra"))


def test_structured_blocks_render_as_yaml_like_text_not_python_repr():
    text = build()
    assert "{'who'" not in text and "[{" not in text
    assert "reveal_at" in text and "grovley-revelation" in text


def test_player_sheets_rendered_in_seat_order_with_seat_ids():
    text = build(player_sheets={"tuber_2": "LEENA", "tuber_1": "CHAD"})
    assert text.index("tuber_1") < text.index("tuber_2")
    assert text.index("CHAD") < text.index("LEENA")


def test_segment_and_contract_optional():
    text = build(segment=None, contract=None)
    assert gc.SEGMENT_HEADING not in text and gc.CONTRACT_HEADING not in text


def test_max_chars_drops_player_sheet_bodies_last_resort_never_blocks():
    long_sheets = {f"tuber_{i}": "x " * 3000 for i in range(1, 5)}
    text = build(player_sheets=long_sheets, max_chars=9000)
    assert len(text) <= 9000
    assert "Leto fathered all four" in text          # GM truth survives the cap
    assert "Open the vault door" in text             # the contract survives the cap


def test_max_chars_too_small_for_blocks_raises():
    with pytest.raises(ValueError):
        build(max_chars=50)


def test_empty_blocks_is_allowed():
    text = build(gm_blocks={})
    assert gc.PLAYERS_HEADING in text


def test_pure_no_mutation():
    import copy
    blocks = copy.deepcopy(BLOCKS)
    build(gm_blocks=blocks)
    assert blocks == BLOCKS
