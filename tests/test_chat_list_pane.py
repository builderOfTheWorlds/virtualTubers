"""
test_chat_list_pane.py
Unit tests for app/chat_list_pane.py's pure render function. Static stub —
always exactly one entry, "all", reverse-video highlighted.
conftest.py inserts app/ onto sys.path.
"""
import chat_list_pane


def test_render_chat_list_default_shows_all_only():
    output = chat_list_pane.render_chat_list()
    assert "all" in output
    # Exactly one line for the default single-channel stub.
    assert len(output.splitlines()) == 1


def test_render_chat_list_is_reverse_video():
    output = chat_list_pane.render_chat_list()
    assert chat_list_pane.REVERSE in output
    assert chat_list_pane.RESET in output


def test_render_chat_list_custom_entries():
    output = chat_list_pane.render_chat_list(["all", "ops"])
    lines = output.splitlines()
    assert len(lines) == 2
    assert "all" in lines[0]
    assert "ops" in lines[1]


def test_channels_constant_is_stub_all_only():
    assert chat_list_pane.CHANNELS == ["all"]
