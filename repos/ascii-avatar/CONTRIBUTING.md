# Contributing to ASCII Avatar

## Setup

```bash
git clone https://github.com/Angelopvtac/ascii-avatar.git
cd ascii-avatar
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,kokoro]"
bash scripts/install.sh   # download Kokoro models
```

## Running tests

```bash
pytest tests/
pytest tests/ -v          # verbose
pytest tests/ -k "mouth"  # filter by name
```

## Code style

- Type hints on all public functions
- Docstrings on classes and non-trivial functions
- Keep imports sorted: stdlib, third-party, local
- No dependencies without a clear reason

## Architecture overview

```
src/avatar/
  main.py            -- entry point, CLI, render loop
  state_machine.py   -- thread-safe state transitions
  event_bus.py       -- ZeroMQ PULL socket, event parsing
  renderer.py        -- blessed terminal rendering
  personas.py        -- persona dataclass + registry
  frames/            -- ASCII art frame sets + mouth sync
  voice/             -- TTS engines + audio player
  bridge/            -- hooks, CLI, MCP server
```

## Adding a new TTS engine

1. Create `src/avatar/voice/your_engine.py`
2. Subclass `TTSEngine` from `voice/base.py`
3. Implement `synthesize()`, `stream_synthesize()`, `is_available()`, `sample_rate`
4. Register it in `main.py:resolve_tts_engine()`
5. Add tests in `tests/test_your_engine.py`

## Adding a new persona

Edit `src/avatar/personas.py` and add an entry to the `PERSONAS` dict. Each persona bundles: frame set, voice engine, voice ID, accent color, personality tag, and frame rate modifier.

## Pull requests

- One feature or fix per PR
- Include tests for new behavior
- Run `pytest tests/` before submitting
- Keep commits focused and messages descriptive
