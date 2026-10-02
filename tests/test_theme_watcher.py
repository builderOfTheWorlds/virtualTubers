"""theme_watcher.apply_theme: the theme must reach the outer xterm (tmux
client tty), not a single pane — a pane-tty write re-themed only the
roundtable's top-left seat."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import theme_watcher  # noqa: E402
from console_theme import OSC_RESET_SEQUENCE, osc_sequence  # noqa: E402

THEME = {"foreground": "#ffffff", "background": "#000000", "cursor": "#ffffff",
         "colors": ["#111111"] * 16}


def _patch(monkeypatch, clients, panes):
    writes = []
    monkeypatch.setattr(theme_watcher, "_client_ttys", lambda s: clients)
    monkeypatch.setattr(theme_watcher, "_pane_ttys", lambda s: panes)
    monkeypatch.setattr(theme_watcher, "_write",
                        lambda tty, data: writes.append((tty, data)) or True)
    return writes


def test_apply_theme_writes_palette_to_client_and_resets_every_pane(monkeypatch):
    panes = [f"/dev/pts/{i}" for i in (0, 3, 4, 5)]
    writes = _patch(monkeypatch, ["/dev/pts/1"], panes)

    assert theme_watcher.apply_theme("worker", THEME) is True

    assert ("/dev/pts/1", osc_sequence(THEME)) in writes
    assert {t for t, d in writes if d == OSC_RESET_SEQUENCE} == set(panes)
    assert not any(t in panes and d == osc_sequence(THEME) for t, d in writes)


def test_apply_theme_returns_false_when_no_client_attached(monkeypatch):
    _patch(monkeypatch, [], ["/dev/pts/0"])
    assert theme_watcher.apply_theme("worker", THEME) is False
