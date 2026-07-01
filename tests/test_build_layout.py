"""
Tests for app/build_layout.py — the config-driven tmux layout engine.

Pure string-sequence + resolved-file assertions. No real tmux or Kafka is
involved: the engine only emits a shell script (a list of strings) and writes
YAML files. Fixture panels/layouts dirs are built under tmp_path so tests never
depend on the real config/ tree, and all writes land in tmp_path.

conftest.py adds app/ to sys.path, so `import build_layout` works directly.
"""
import pathlib

import yaml
import pytest

import build_layout


# ── Fixtures: minimal panels + a layout preset under tmp_path ─────────────────
@pytest.fixture
def dirs(tmp_path):
    panels = tmp_path / "panels"
    layouts = tmp_path / "layouts"
    runtime = tmp_path / "runtime"
    panels.mkdir()
    layouts.mkdir()

    def write(d, name, data):
        (d / f"{name}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    write(panels, "filetree", {
        "type": "filetree", "title": "Files", "border_color": "blue",
        "command": "tree /data/repo",
    })
    write(panels, "editor", {
        "type": "editor", "title": "Editor", "border_color": "green",
        "command": "nvim",
    })
    write(panels, "avatar", {
        "type": "avatar", "title": "Avatar", "border_color": "magenta",
        "command": "python3 /app/avatar.py --config {config_path}",
    })
    write(panels, "kafka_feed", {
        "type": "kafka_feed", "title": "Message Bus", "border_color": "cyan",
        "command": "python3 /app/tail_bus.py --bus-config {config_path} --feed-config {resolved_path}",
        "content": {"filters": {"hide_types": ["heartbeat"]}},
    })
    write(panels, "htop", {
        "type": "htop", "title": "System", "border_color": "yellow",
        "command": "htop",
    })

    write(layouts, "coder", {
        "preset": "coder",
        "panes": [
            {"use": "filetree", "size": 25},
            {"use": "editor", "target": "filetree", "split": "h", "size": 75},
            {"use": "avatar", "target": "filetree", "split": "v", "size": 60},
            {"use": "kafka_feed", "target": "editor", "split": "v", "size": 30},
            {"use": "htop", "target": "filetree", "split": "v", "size": 15},
        ],
    })
    return {
        "panels": str(panels),
        "layouts": str(layouts),
        "runtime": str(runtime),
        "root": tmp_path,
    }


@pytest.fixture
def worker_config(tmp_path):
    path = tmp_path / "worker.yaml"
    path.write_text(yaml.safe_dump({
        "layout": {"preset": "coder"},
        "message_bus": {"worker_id": "coder"},
    }), encoding="utf-8")
    return str(path)


# ── (a) exact emitted tmux command sequence ───────────────────────────────────
def test_emitted_tmux_sequence_matches_geometry(dirs, worker_config, monkeypatch):
    monkeypatch.delenv("LAYOUT_PRESET", raising=False)
    lines, _ = build_layout.build(worker_config, dirs["panels"], dirs["layouts"], dirs["runtime"])

    # new-session first, named "worker", with the default 240x67 box.
    assert lines[0] == "tmux new-session -d -s worker -x 240 -y 67"

    joined = "\n".join(lines)

    # The four splits reproduce the original geometry (targets + -p percentages).
    assert "tmux split-window -h -t worker:0.0 -p 75" in joined   # editor off filetree
    assert "tmux split-window -v -t worker:0.0 -p 60" in joined    # avatar off filetree
    assert "tmux split-window -v -t worker:0.1 -p 30" in joined    # feed off editor (idx 1)
    assert "tmux split-window -v -t worker:0.0 -p 15" in joined    # htop off filetree

    # Exactly one new-session and four splits (five panes => four splits).
    assert joined.count("tmux new-session") == 1
    assert joined.count("tmux split-window") == 4


def test_send_keys_use_resolved_pane_indices(dirs, worker_config):
    lines, _ = build_layout.build(worker_config, dirs["panels"], dirs["layouts"], dirs["runtime"])
    joined = "\n".join(lines)
    # Creation order: filetree=0, editor=1, avatar=2, feed=3, htop=4.
    assert "tmux send-keys -t worker:0.0 'tree /data/repo' Enter" in joined
    assert "tmux send-keys -t worker:0.1 'nvim' Enter" in joined
    assert "tmux send-keys -t worker:0.4 'htop' Enter" in joined


# ── (b) resolved files written with merged values ─────────────────────────────
def test_resolved_files_written_to_runtime_dir(dirs, worker_config):
    build_layout.build(worker_config, dirs["panels"], dirs["layouts"], dirs["runtime"])
    runtime = pathlib.Path(dirs["runtime"])
    for pane_id in ("filetree", "editor", "avatar", "kafka_feed", "htop"):
        assert (runtime / f"{pane_id}.yaml").is_file()

    feed = yaml.safe_load((runtime / "kafka_feed.yaml").read_text(encoding="utf-8"))
    # The full resolved panel dict (incl. content: block) is written for tail_bus.py.
    assert feed["type"] == "kafka_feed"
    assert feed["content"]["filters"]["hide_types"] == ["heartbeat"]
    # Placement knobs merged in too.
    assert feed["size"] == 30
    assert feed["target"] == "editor"


# ── (c) enabled: false pane omitted ───────────────────────────────────────────
def test_disabled_pane_is_omitted(dirs, worker_config):
    layout_path = pathlib.Path(dirs["layouts"]) / "coder.yaml"
    data = yaml.safe_load(layout_path.read_text(encoding="utf-8"))
    for pane in data["panes"]:
        if pane["use"] == "htop":
            pane["enabled"] = False
    layout_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    lines, panes = build_layout.build(worker_config, dirs["panels"], dirs["layouts"], dirs["runtime"])
    ids = [p["id"] for p in panes]
    assert "htop" not in ids
    assert len(panes) == 4                     # five defined, one disabled
    joined = "\n".join(lines)
    assert "htop" not in joined                # no split, no send-keys
    assert not (pathlib.Path(dirs["runtime"]) / "htop.yaml").exists()


# ── (d) merge precedence: worker override beats layout beats panel default ─────
def test_merge_precedence_worker_beats_layout_beats_panel(dirs, tmp_path):
    # layout overrides the panel default command; worker overrides the layout.
    layout_path = pathlib.Path(dirs["layouts"]) / "coder.yaml"
    data = yaml.safe_load(layout_path.read_text(encoding="utf-8"))
    for pane in data["panes"]:
        if pane["use"] == "editor":
            pane["command"] = "layout-editor"     # beats panel default "nvim"
    layout_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    worker_path = tmp_path / "worker_ov.yaml"
    worker_path.write_text(yaml.safe_dump({
        "layout": {
            "preset": "coder",
            "panes": {"editor": {"command": "worker-editor", "title": "Overridden"}},
        },
    }), encoding="utf-8")

    _, panes = build_layout.build(str(worker_path), dirs["panels"], dirs["layouts"], dirs["runtime"])
    editor = next(p for p in panes if p["id"] == "editor")
    assert editor["command"] == "worker-editor"   # worker wins over layout wins over panel
    assert editor["title"] == "Overridden"

    filetree = next(p for p in panes if p["id"] == "filetree")
    assert filetree["command"] == "tree /data/repo"   # untouched panel default


def test_with_block_overrides_panel_default(dirs, worker_config):
    layout_path = pathlib.Path(dirs["layouts"]) / "coder.yaml"
    data = yaml.safe_load(layout_path.read_text(encoding="utf-8"))
    for pane in data["panes"]:
        if pane["use"] == "editor":
            pane["with"] = {"variant": "nvim", "border_color": "red"}
    layout_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    _, panes = build_layout.build(worker_config, dirs["panels"], dirs["layouts"], dirs["runtime"])
    editor = next(p for p in panes if p["id"] == "editor")
    assert editor["variant"] == "nvim"
    assert editor["border_color"] == "red"        # with-block beat the panel default "green"


# ── (e) placeholder substitution in the kafka_feed command ────────────────────
def test_kafka_feed_command_substitution(dirs, worker_config):
    lines, _ = build_layout.build(worker_config, dirs["panels"], dirs["layouts"], dirs["runtime"])
    feed_line = next(l for l in lines if "tail_bus.py" in l)
    # Emitted paths are POSIX (the script runs in the Linux container).
    resolved_path = dirs["runtime"].rstrip("/\\") + "/kafka_feed.yaml"
    assert f"--bus-config {worker_config}" in feed_line
    assert f"--feed-config {resolved_path}" in feed_line
    assert "{config_path}" not in feed_line
    assert "{resolved_path}" not in feed_line


def test_avatar_command_gets_config_path(dirs, worker_config):
    lines, _ = build_layout.build(worker_config, dirs["panels"], dirs["layouts"], dirs["runtime"])
    avatar_line = next(l for l in lines if "avatar.py" in l)
    assert f"--config {worker_config}" in avatar_line


# ── preset selection ──────────────────────────────────────────────────────────
def test_env_var_overrides_preset(dirs, worker_config, monkeypatch):
    # A "manager" preset that only changes the editor command.
    data = yaml.safe_load((pathlib.Path(dirs["layouts"]) / "coder.yaml").read_text(encoding="utf-8"))
    data["preset"] = "manager"
    for pane in data["panes"]:
        if pane["use"] == "editor":
            pane["command"] = "ticket-board"
    (pathlib.Path(dirs["layouts"]) / "manager.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    monkeypatch.setenv("LAYOUT_PRESET", "manager")
    _, panes = build_layout.build(worker_config, dirs["panels"], dirs["layouts"], dirs["runtime"])
    editor = next(p for p in panes if p["id"] == "editor")
    assert editor["command"] == "ticket-board"


def test_shorthand_layout_string(dirs, tmp_path, monkeypatch):
    monkeypatch.delenv("LAYOUT_PRESET", raising=False)
    worker_path = tmp_path / "short.yaml"
    worker_path.write_text(yaml.safe_dump({"layout": "coder"}), encoding="utf-8")
    _, panes = build_layout.build(str(worker_path), dirs["panels"], dirs["layouts"], dirs["runtime"])
    assert len(panes) == 5


# ── substitute() unit behavior ────────────────────────────────────────────────
def test_substitute_leaves_unknown_tokens_intact():
    assert build_layout.substitute("a {x} b {y}", {"x": "1"}) == "a 1 b {y}"
