# Voice Channel — Distinct Spoken Output

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** The avatar speaks its own executive-summary voice channel — status updates, completions, blockers, questions — not a parrot of the text output. Like a direct report giving you a verbal status at your desk.

**Architecture:** The Stop hook sends `last_assistant_message` to Haiku via the Anthropic SDK for a fast, cheap transformation into a spoken status line. The result goes to Kokoro TTS. Total added latency: ~300ms (Haiku) + ~200ms (Kokoro) = ~500ms after Claude finishes.

**Tech Stack:** anthropic SDK (Haiku), existing hook_stop.py, existing Kokoro TTS pipeline

---

### Task 1: Add anthropic SDK to dependencies

**Files:**
- Modify: `/path/to/ascii-avatar/pyproject.toml`

**Step 1: Add anthropic to dependencies**

Add `anthropic` to the main dependencies list in pyproject.toml:

```toml
dependencies = [
    "blessed",
    "pyzmq",
    "sounddevice",
    "numpy",
    "mcp",
    "anthropic",
]
```

**Step 2: Install**

```bash
cd /path/to/ascii-avatar
source .venv/bin/activate
uv pip install -e ".[dev]"
pipx install --force "/path/to/ascii-avatar[kokoro]"
```

**Step 3: Verify anthropic is available**

```bash
python -c "import anthropic; print('OK')"
```

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add anthropic SDK for voice channel summarization"
```

---

### Task 2: Create voice channel summarizer

**Files:**
- Create: `/path/to/ascii-avatar/src/avatar/voice/summarizer.py`
- Create: `/path/to/ascii-avatar/tests/test_summarizer.py`

**Step 1: Write failing tests**

`tests/test_summarizer.py`:
```python
import os
import pytest
from avatar.voice.summarizer import summarize_for_voice, strip_markdown


class TestStripMarkdown:
    def test_removes_code_blocks(self):
        text = "I fixed it.\n```python\nprint('hello')\n```\nDone."
        result = strip_markdown(text)
        assert "```" not in result
        assert "print" not in result
        assert "I fixed it." in result

    def test_removes_inline_code(self):
        assert "`foo`" not in strip_markdown("Changed `foo` to bar")

    def test_removes_headers(self):
        assert "##" not in strip_markdown("## Summary\nAll good.")

    def test_removes_bold_italic(self):
        result = strip_markdown("**bold** and *italic*")
        assert "**" not in result
        assert "*" not in result
        assert "bold" in result

    def test_removes_links(self):
        result = strip_markdown("See [this](http://example.com)")
        assert "http" not in result
        assert "this" in result

    def test_removes_bullet_points(self):
        result = strip_markdown("- item one\n- item two")
        assert "item one" in result
        assert "-" not in result.split("item")[0]  # no leading dash


class TestSummarizeForVoice:
    @pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="No API key"
    )
    def test_real_summarization(self):
        text = """I've analyzed the authentication middleware and found the bug.
The issue is on line 42 of `src/auth/middleware.py` where the JWT token
validation skips the expiry check when the `iss` claim is missing.

Here's the fix:

```python
if not claims.get('iss'):
    raise AuthenticationError('Missing issuer claim')
if claims.get('exp', 0) < time.time():
    raise AuthenticationError('Token expired')
```

I've also added a test in `tests/test_auth.py` to cover this case.
All 23 tests pass after the fix."""

        result = summarize_for_voice(text)
        assert len(result) < 200
        assert len(result) > 10
        # Should mention the key outcome, not code details
        print(f"Voice output: {result}")

    def test_fallback_without_api_key(self):
        """Without API key, should fall back to first-sentence extraction."""
        key = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            result = summarize_for_voice("I fixed the bug. Here are the details.")
            assert "fixed the bug" in result
        finally:
            if key:
                os.environ["ANTHROPIC_API_KEY"] = key

    def test_empty_input(self):
        assert summarize_for_voice("") == ""

    def test_short_input_passes_through(self):
        result = summarize_for_voice("All tests pass.")
        assert "tests pass" in result
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_summarizer.py -v
```

Expected: ImportError

**Step 3: Implement summarizer**

`src/avatar/voice/summarizer.py`:
```python
"""Transform Claude's text response into a spoken status update.

Uses Haiku for fast, cheap summarization. Falls back to
first-sentence extraction if the API is unavailable.
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the voice of an AI coding assistant. Given the assistant's text response, produce a single spoken sentence (max 30 words) that a direct report would say to their manager.

Rules:
- Speak as "I" (first person)
- Focus on: what was done, what the outcome was, or what you need from them
- Never mention file paths, line numbers, code syntax, or markdown
- Never say "the assistant" or "Claude" — you ARE the assistant
- If the response is a question, just rephrase it conversationally
- If the response is an error/blocker, lead with that
- Be casual and direct, like talking to a colleague

Examples:
- "Found and fixed the auth bug, all 23 tests pass now."
- "I need your input on the database schema before I can continue."
- "Three agents are running, backend is about halfway done."
- "Tests are failing on the payment module, looking into it."
- "That's done and committed, ready for your review."
- "Hey, what are we working on today?"
"""


def strip_markdown(text: str) -> str:
    """Remove markdown formatting, code blocks, links."""
    # Remove code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code
    text = re.sub(r"`[^`]+`", "", text)
    # Remove headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
    # Remove links [text](url)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove bullet points
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    # Remove numbered lists
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Collapse whitespace
    text = " ".join(text.split())
    return text.strip()


def _first_sentence(text: str) -> str:
    """Extract first sentence as fallback."""
    text = strip_markdown(text)
    if not text:
        return ""
    # Find first sentence boundary
    for i, char in enumerate(text):
        if char in ".!?" and i > 10:
            return text[: i + 1].strip()
    return text[:150].strip()


def summarize_for_voice(text: str) -> str:
    """Transform assistant text into a spoken status line.

    Uses Haiku for quality. Falls back to first-sentence extraction
    if API is unavailable or text is short enough already.
    """
    if not text:
        return ""

    # Short text doesn't need summarization
    clean = strip_markdown(text)
    if len(clean) < 100:
        return _first_sentence(clean) or clean

    # Try Haiku
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.debug("No ANTHROPIC_API_KEY, falling back to first-sentence")
        return _first_sentence(text)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text[:2000]}],
        )
        result = response.content[0].text.strip()
        # Remove quotes if Haiku wraps the response
        result = result.strip('"').strip("'")
        log.debug("Haiku voice summary: %s", result)
        return result
    except Exception as e:
        log.warning("Haiku summarization failed: %s", e)
        return _first_sentence(text)
```

**Step 4: Run tests**

```bash
python -m pytest tests/test_summarizer.py -v
```

Expected: All pass (API test skipped if no key, fallback tests pass)

**Step 5: Commit**

```bash
git add src/avatar/voice/summarizer.py tests/test_summarizer.py
git commit -m "feat: voice channel summarizer — Haiku transforms text to spoken status"
```

---

### Task 3: Wire summarizer into hook_stop

**Files:**
- Modify: `/path/to/ascii-avatar/src/avatar/bridge/hook_stop.py`

**Step 1: Replace summarize_for_speech with summarize_for_voice**

`src/avatar/bridge/hook_stop.py`:
```python
#!/usr/bin/env python3
"""Hook script for Claude Code 'Stop' event.

Reads last_assistant_message from hook input, transforms it into
a spoken status update via Haiku, speaks it, then switches to listen.
"""

import json
import sys
import datetime
from pathlib import Path

from avatar.bridge.hooks import respond, listen
from avatar.voice.summarizer import summarize_for_voice

LOG = Path("/tmp/avatar-hooks.log")


def log(msg: str):
    with open(LOG, "a") as f:
        f.write(f"{datetime.datetime.now().isoformat()} [stop] {msg}\n")


def main():
    log("hook fired")

    try:
        stdin_data = sys.stdin.read()
        hook_input = json.loads(stdin_data) if stdin_data.strip() else {}
    except (json.JSONDecodeError, EOFError) as e:
        log(f"stdin parse error: {e}")
        hook_input = {}

    socket_path = "/tmp/ascii-avatar.sock"

    last_message = hook_input.get("last_assistant_message", "")
    log(f"last_assistant_message length: {len(last_message)}")

    speech = summarize_for_voice(last_message)
    log(f"speech: {speech}")

    if speech:
        try:
            respond(speech, socket_path=socket_path)
            log("speak sent OK")
        except Exception as e:
            log(f"speak failed: {e}")

    try:
        listen(socket_path=socket_path)
        log("listen sent OK")
    except Exception as e:
        log(f"listen failed: {e}")


if __name__ == "__main__":
    main()
```

**Step 2: Reinstall**

```bash
cd /path/to/ascii-avatar
source .venv/bin/activate
uv pip install -e ".[dev]"
pipx install --force "/path/to/ascii-avatar[kokoro]"
```

**Step 3: Test the hook manually**

```bash
echo '{"last_assistant_message": "I analyzed the auth middleware and found a null reference on line 42. Fixed it and added a test. All 23 tests pass now."}' | ANTHROPIC_API_KEY=$(grep -oP 'ANTHROPIC_API_KEY=\K.*' ~/.bashrc 2>/dev/null || echo "") python3 -m avatar.bridge.hook_stop
cat /tmp/avatar-hooks.log | tail -5
```

The speech line should be something like "Fixed the auth bug, all tests pass now." — NOT the raw input text.

**Step 4: Commit**

```bash
git add src/avatar/bridge/hook_stop.py
git commit -m "feat: hook_stop uses Haiku to generate spoken status updates"
```

---

### Task 4: Ensure API key is available to hooks

**Files:**
- Modify: `~/.claude/agents/avatar-start.sh`

The hook runs as a subprocess of Claude Code. Claude Code has `ANTHROPIC_API_KEY` in its environment (it needs it to function). The hook inherits this — so Haiku calls should work automatically.

**Step 1: Verify**

```bash
# Check if Claude Code passes its env to hook subprocesses
echo '{}' | python3 -c "
import os
key = os.environ.get('ANTHROPIC_API_KEY', '')
print(f'Key present: {bool(key)}')
print(f'Key prefix: {key[:10]}...' if key else 'No key')
"
```

If no key is present in the hook environment, we need to pass it explicitly. Update the startup script to forward it:

```bash
# In avatar-start.sh, add to the hook commands:
"command": "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY $PIPX_PY -m avatar.bridge.hook_stop"
```

**Step 2: Test end-to-end**

```bash
tmux kill-session -t avatar 2>/dev/null
rm -f /tmp/avatar-hooks.log
bash ~/.claude/agents/avatar-start.sh
```

Talk to the avatar session. Check:
1. Avatar thinks when you send a message
2. Avatar speaks a natural status update (not raw text)
3. Avatar listens when done

**Step 3: Commit**

```bash
git add -A && git commit -m "fix: ensure ANTHROPIC_API_KEY available in hook env"
```

---

## Dependency Graph

```
Task 1 (add anthropic SDK)
  └── Task 2 (summarizer module + tests)
        └── Task 3 (wire into hook_stop)
              └── Task 4 (API key + end-to-end test)
```

All sequential.
