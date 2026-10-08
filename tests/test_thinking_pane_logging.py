"""The Thinking pane is on air: its own logging must not fill it (2026-10-08 live test).

Reproduces a fresh process: no root handlers, tail_bus imported FIRST (its
basicConfig(level=INFO) wins unless thinking_pane forces its own).
"""
import importlib
import logging


def test_thinking_pane_root_logger_is_warning_even_after_tail_bus_import(monkeypatch):
    monkeypatch.delenv("THINKING_PANE_LOG_LEVEL", raising=False)
    monkeypatch.delenv("TAIL_BUS_LOG_LEVEL", raising=False)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        root.handlers.clear()
        root.setLevel(logging.NOTSET)
        import tail_bus
        importlib.reload(tail_bus)              # its basicConfig(level=INFO) applies now
        assert root.level == logging.INFO
        import thinking_pane
        importlib.reload(thinking_pane)
        assert root.level == logging.WARNING
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
