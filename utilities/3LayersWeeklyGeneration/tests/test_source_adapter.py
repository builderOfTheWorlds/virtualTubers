"""Tests for source_adapter.py — the --source parameter (§6F.5).

Covers both adapters and the two behaviours the plan cares about:
  * ObsidianVaultAdapter honours the *Agent_Ignore* filename convention.
  * kind is derived from the folder, not invented.
  * hash is stable across reads and changes when the file changes.
  * SingleTextAdapter chunks a monolithic text into ~target-sized notes and
    gives each a stable id + hash.
"""
import pathlib
import sys
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
UTILITY_SRC = REPO / "utilities" / "3LayersWeeklyGeneration" / "src"
for path in (REPO / "app", UTILITY_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import source_adapter as sa  # noqa: E402


def _write(vault: pathlib.Path, rel: str, text: str):
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_vault_adapter_excludes_agent_ignore():
    with tempfile.TemporaryDirectory() as d:
        v = pathlib.Path(d)
        _write(v, "Plots/Good.md", "A good plot note.")
        _write(v, "Plots/Bad*Agent_Ignore*.md", "Excluded note.")
        notes = sa.ObsidianVaultAdapter(v).notes()
        ids = [n.rel_path for n in notes]
        assert "Plots/Good.md" in ids
        assert "Plots/Bad*Agent_Ignore*.md" not in ids


def test_vault_kind_is_derived_from_folder():
    with tempfile.TemporaryDirectory() as d:
        v = pathlib.Path(d)
        _write(v, "Plots/P.md", "plot")
        _write(v, "NPCs/N.md", "npc")
        _write(v, "Locations/L.md", "location")
        _write(v, "World/W.md", "lore")
        _write(v, "Characters/C.md", "character")
        _write(v, "loose.md", "unclassified")
        notes = {n.rel_path: n.kind for n in sa.ObsidianVaultAdapter(v).notes()}
        assert notes["Plots/P.md"] == "plot"
        assert notes["NPCs/N.md"] == "npc"
        assert notes["Locations/L.md"] == "location"
        assert notes["World/W.md"] == "lore"
        assert notes["Characters/C.md"] == "character"
        assert notes["loose.md"] == "unclassified"


def test_vault_hash_is_stable_and_changes():
    with tempfile.TemporaryDirectory() as d:
        v = pathlib.Path(d)
        _write(v, "Plots/P.md", "v1")
        h1 = {n.rel_path: n.hash for n in sa.ObsidianVaultAdapter(v).notes()}["Plots/P.md"]
        # Read again: same hash.
        h2 = {n.rel_path: n.hash for n in sa.ObsidianVaultAdapter(v).notes()}["Plots/P.md"]
        assert h1 == h2
        # Change the file: hash changes.
        _write(v, "Plots/P.md", "v2")
        h3 = {n.rel_path: n.hash for n in sa.ObsidianVaultAdapter(v).notes()}["Plots/P.md"]
        assert h1 != h3


def test_vault_skips_empty_files():
    with tempfile.TemporaryDirectory() as d:
        v = pathlib.Path(d)
        _write(v, "Plots/blank.md", "   \n  ")
        _write(v, "Plots/good.md", "content")
        notes = sa.ObsidianVaultAdapter(v).notes()
        assert [n.rel_path for n in notes] == ["Plots/good.md"]


def test_single_text_adapter_chunks_and_hashes():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "blob.txt"
        p.write_text("word " * 5000, encoding="utf-8")  # 5000 words, no newlines
        notes = sa.SingleTextAdapter(p, target_chars=1000).notes()
        assert len(notes) > 3
        # Each note has the required fields and a stable hash.
        for n in notes:
            assert n.kind == "lore"
            assert len(n.hash) == 64
            assert n.id.startswith("block-")
            assert n.rel_path == "blob.txt"
        # Ids are unique and ordered; hashes differ between different blocks.
        assert len({n.id for n in notes}) == len(notes)
        assert len({n.hash for n in notes}) == len(notes) or len(notes) == 1
        # Total word budget is preserved (chunking must not drop the tail).
        assert sum(len(n.text.split()) for n in notes) >= 4995


def test_single_text_adapter_requires_a_file():
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(FileNotFoundError):
            sa.SingleTextAdapter(pathlib.Path(d) / "nope.txt")


def test_load_source_dispatches_on_type():
    with tempfile.TemporaryDirectory() as d:
        v = pathlib.Path(d) / "vault"
        v.mkdir()
        (v / "Plots").mkdir()
        (v / "Plots" / "P.md").write_text("plot note")
        # Directory -> vault adapter.
        assert sa.load_source(v)[0].kind == "plot"
        b = pathlib.Path(d) / "blob.txt"
        b.write_text("word " * 2000)
        # File -> single-text adapter.
        file_notes = sa.load_source(b, target_chars=500)
        assert all(n.kind == "lore" for n in file_notes)


def test_load_source_missing_raises():
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(FileNotFoundError):
            sa.load_source(pathlib.Path(d) / "missing")
