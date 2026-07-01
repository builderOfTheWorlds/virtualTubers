import subprocess

import pytest

import tmux_control
from tmux_control import (
    TmuxError,
    _pane_titles,
    list_panes,
    resolve_pane,
    select_pane,
    send_command,
    send_keys,
)

LIVE_PANES_OUT = "Editor\t%0\nAvatar\t%1\nFiles\t%2\n"


class FakeResult:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def fake_run(recorder, list_panes_out=LIVE_PANES_OUT, returncode=0, stderr=""):
    def _fake(cmd, capture_output, text):
        recorder.append(cmd)
        if cmd[:2] == ["tmux", "list-panes"]:
            return FakeResult(stdout=list_panes_out, returncode=returncode, stderr=stderr)
        return FakeResult(stdout="", returncode=returncode, stderr=stderr)
    return _fake


def test_pane_titles_reads_runtime_dir(tmp_path):
    (tmp_path / "editor.yaml").write_text("id: editor\ntitle: Editor\n", encoding="utf-8")
    (tmp_path / "filetree.yaml").write_text("id: filetree\ntitle: Files\n", encoding="utf-8")
    (tmp_path / "broken.yaml").write_text("not: [valid yaml", encoding="utf-8")

    assert _pane_titles(str(tmp_path)) == {"editor": "Editor", "filetree": "Files"}


def test_pane_titles_falls_back_to_filename_when_id_missing(tmp_path):
    (tmp_path / "htop.yaml").write_text("title: System\n", encoding="utf-8")
    assert _pane_titles(str(tmp_path)) == {"htop": "System"}


def test_list_panes_parses_tmux_output(monkeypatch):
    recorder = []
    monkeypatch.setattr(subprocess, "run", fake_run(recorder))

    assert list_panes() == {"Editor": "%0", "Avatar": "%1", "Files": "%2"}
    assert recorder[0][:2] == ["tmux", "list-panes"]


def test_resolve_pane_passes_through_literal_pane_id(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run([]))
    assert resolve_pane("%5") == "%5"


def test_resolve_pane_matches_live_title_directly(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run([]))
    assert resolve_pane("Editor") == "%0"


def test_resolve_pane_matches_config_id_via_runtime_dir(monkeypatch, tmp_path):
    (tmp_path / "filetree.yaml").write_text("id: filetree\ntitle: Files\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", fake_run([]))

    assert resolve_pane("filetree", runtime_dir=str(tmp_path)) == "%2"


def test_resolve_pane_raises_when_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", fake_run([]))
    with pytest.raises(TmuxError, match="nonexistent"):
        resolve_pane("nonexistent", runtime_dir=str(tmp_path))


def test_select_pane_sends_select_pane_command(monkeypatch):
    recorder = []
    monkeypatch.setattr(subprocess, "run", fake_run(recorder))

    select_pane("Editor")

    assert ["tmux", "select-pane", "-t", "%0"] in recorder


def test_send_keys_sends_literal_text_then_enter(monkeypatch):
    recorder = []
    monkeypatch.setattr(subprocess, "run", fake_run(recorder))

    send_keys("Editor", "print('hi')")

    assert ["tmux", "send-keys", "-t", "%0", "-l", "--", "print('hi')"] in recorder
    assert ["tmux", "send-keys", "-t", "%0", "Enter"] in recorder


def test_send_keys_without_enter_skips_enter_keypress(monkeypatch):
    recorder = []
    monkeypatch.setattr(subprocess, "run", fake_run(recorder))

    send_keys("Editor", "draft text", enter=False)

    assert ["tmux", "send-keys", "-t", "%0", "Enter"] not in recorder


def test_send_keys_does_not_interpret_key_names_in_text(monkeypatch):
    recorder = []
    monkeypatch.setattr(subprocess, "run", fake_run(recorder))

    send_keys("Editor", "press Enter to continue")

    assert ["tmux", "send-keys", "-t", "%0", "-l", "--", "press Enter to continue"] in recorder


def test_send_command_types_and_presses_enter(monkeypatch):
    recorder = []
    monkeypatch.setattr(subprocess, "run", fake_run(recorder))

    send_command("Editor", "ls -la")

    assert ["tmux", "send-keys", "-t", "%0", "-l", "--", "ls -la"] in recorder
    assert ["tmux", "send-keys", "-t", "%0", "Enter"] in recorder


def test_run_raises_tmux_error_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run([], returncode=1, stderr="no such pane"))

    with pytest.raises(TmuxError, match="no such pane"):
        tmux_control._run(["list-panes"])
