from avatar import resolve_display, wrap_bubble, STALE_AFTER_S


def test_resolve_display_no_state_is_idle_with_no_bubble():
    expression, bubble = resolve_display(None, now=1000.0, bubble_duration_s=6)
    assert expression == "idle"
    assert bubble is None


def test_resolve_display_fresh_bubble_is_shown():
    state = {"expression": "speaking", "bubble": "Fixing the bug now.", "updated_at": 995.0}
    expression, bubble = resolve_display(state, now=1000.0, bubble_duration_s=6)
    assert expression == "speaking"
    assert bubble == "Fixing the bug now."


def test_resolve_display_expired_bubble_reverts_speaking_to_idle():
    state = {"expression": "speaking", "bubble": "Fixing the bug now.", "updated_at": 990.0}
    expression, bubble = resolve_display(state, now=1000.0, bubble_duration_s=6)
    assert expression == "idle"
    assert bubble is None


def test_resolve_display_expired_bubble_reverts_frustrated_to_idle():
    state = {"expression": "frustrated", "bubble": "Ugh, this broke.", "updated_at": 990.0}
    expression, bubble = resolve_display(state, now=1000.0, bubble_duration_s=6)
    assert expression == "idle"
    assert bubble is None


def test_resolve_display_thinking_without_bubble_persists_while_fresh():
    state = {"expression": "thinking", "bubble": None, "updated_at": 999.0}
    expression, bubble = resolve_display(state, now=1000.0, bubble_duration_s=6)
    assert expression == "thinking"
    assert bubble is None


def test_resolve_display_stale_bubbleless_state_falls_back_to_idle():
    state = {"expression": "thinking", "bubble": None, "updated_at": 1000.0 - STALE_AFTER_S - 1}
    expression, bubble = resolve_display(state, now=1000.0, bubble_duration_s=6)
    assert expression == "idle"


def test_wrap_bubble_empty_text_returns_empty_list():
    assert wrap_bubble("", width=32) == []
    assert wrap_bubble(None, width=32) == []


def test_wrap_bubble_wraps_long_text_to_width():
    text = "This function needs more error handling before we ship it."
    lines = wrap_bubble(text, width=20)
    assert len(lines) > 1
    assert all(len(line) <= 20 for line in lines)


def test_wrap_bubble_short_text_is_single_line():
    assert wrap_bubble("On it.", width=32) == ["On it."]
