"""Tests for source_adapter.CorpusAdapter (OB-12) — sessionCorpus JSONL export
-> SourceNote(kind='work_session'). Synthetic records only."""
import json

import pytest

import source_adapter as sa


def ev(kind, **kw):
    base = {"seq": 0, "ts": None, "type": kind, "text": None, "tool": None,
            "input": None, "output": None, "error": False}
    base.update(kw)
    return base


def record(session_id="s1", n_extra=0, **kw):
    events = [
        ev("user_message", text="Please add CSV export to the report page"),
        ev("assistant_text", text="Plan: read the view, add the export."),
        ev("tool_call", tool="Read", input={"file_path": "app/report.py"}),
        ev("tool_call", tool="Edit", input={"file_path": "app/report.py", "old_string": "a", "new_string": "b"}),
        ev("tool_call", tool="write_file", input={"path": "tests/test_report.py", "content": "x"}),
        ev("tool_call", tool="Bash", input={"command": "python -m pytest -q"},
           output="1 failed, 4 passed in 0.2s"),
        ev("tool_call", tool="terminal", input={"command": "pytest tests/"}, output="5 passed in 0.3s"),
        ev("assistant_text", text="Done: CSV export added, all 5 tests pass."),
    ] + [ev("user_message", text=f"follow-up {i}") for i in range(n_extra)]
    rec = {"source_tool": "claude_code", "host": "h", "project": "demo", "session_id": session_id,
           "started_at": "2026-09-01T10:00:00Z", "ended_at": None, "model": "m", "events": events}
    rec.update(kw)
    return rec


@pytest.fixture
def export(tmp_path):
    def _write(*records, extra_lines=()):
        p = tmp_path / "corpus.jsonl"
        lines = [json.dumps(r) for r in records] + list(extra_lines)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p
    return _write


def test_corpus_adapter_digest_contains_asks_files_commands_tests_result(export):
    notes = sa.CorpusAdapter(export(record())).notes()
    assert len(notes) == 1
    note = notes[0]
    assert note.kind == "work_session"
    assert note.id == "claude-code-s1"
    assert note.rel_path == "corpus.jsonl#s1"
    assert note.title.startswith("Please add CSV export")
    assert "Please add CSV export" in note.text
    assert "Files edited: app/report.py, tests/test_report.py" in note.text
    assert "Files read: app/report.py" in note.text
    assert "- python -m pytest -q" in note.text
    assert "Tests: 2 run(s); last run: 5 passed." in note.text
    assert note.text.rstrip().endswith("Result: Done: CSV export added, all 5 tests pass.")


def test_corpus_adapter_skips_short_sessions_and_bad_lines(export):
    short = record("short")
    short["events"] = short["events"][:3]
    notes = sa.CorpusAdapter(export(record("ok"), short, extra_lines=["{not json", "[]"])).notes()
    assert [n.id for n in notes] == ["claude-code-ok"]
    assert len(sa.CorpusAdapter(export(short), min_events=3).notes()) == 1


def test_corpus_adapter_tag_filter(export):
    p = export(record("a", tags=["feature"]), record("b", tags=["bugfix"]), record("c"))
    assert [n.id for n in sa.CorpusAdapter(p, tag="feature").notes()] == ["claude-code-a"]
    assert len(sa.CorpusAdapter(p).notes()) == 3


def test_corpus_adapter_caps_length_but_keeps_result(export):
    big = record("big")
    big["events"] = [ev("user_message", text="x" * 5000) for _ in range(20)] + big["events"]
    big["events"] += [ev("tool_call", tool="Edit", input={"file_path": f"f{i}_" + "p" * 200 + ".py"})
                      for i in range(40)]
    big["events"].append(ev("assistant_text", text="Final wrap-up."))
    text = sa.CorpusAdapter(export(big), max_chars=1000).notes()[0].text
    assert len(text) <= 1000
    assert text.endswith("Result: Final wrap-up.")


def test_corpus_adapter_hash_stable_and_content_sensitive(export, tmp_path):
    p = export(record())
    h1 = sa.CorpusAdapter(p).notes()[0].hash
    assert sa.CorpusAdapter(p).notes()[0].hash == h1
    changed = record()
    changed["events"][-1]["text"] = "Done differently."
    assert sa.CorpusAdapter(export(changed)).notes()[0].hash != h1


def test_corpus_adapter_test_counts_not_detected(export):
    rec = record()
    for e in rec["events"]:
        if e["type"] == "tool_call" and e["tool"] in ("Bash", "terminal"):
            e["output"] = None
    assert "counts not detected" in sa.CorpusAdapter(export(rec)).notes()[0].text


def test_corpus_adapter_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        sa.CorpusAdapter(tmp_path / "nope.jsonl")


def test_load_source_dispatches_jsonl_to_corpus_adapter(export, tmp_path):
    notes = sa.load_source(export(record()))
    assert notes and notes[0].kind == "work_session"
    txt = tmp_path / "plain.txt"
    txt.write_text("some words here " * 10, encoding="utf-8")
    assert sa.load_source(txt)[0].kind == "lore"
