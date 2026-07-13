"""Transform Claude's text response into natural spoken summaries.

Two paths:
1. API path (preferred): Uses Haiku to generate a conversational voice-over
   from the full response. Set ANTHROPIC_API_KEY to enable.
2. Local path (fallback): Structural analysis of the response to extract
   2-3 key sentences and compose them into natural speech.

The goal is NOT to extract a single fragment. It's to produce what a
person would actually say to summarize what just happened — like a
co-pilot narrating their actions.
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Markdown → clean text (preserving inline code content this time)
# --------------------------------------------------------------------------- #

def strip_markdown(text: str) -> str:
    """Remove markdown formatting while preserving meaningful content.

    Unlike the old version, this keeps the TEXT inside backticks rather
    than deleting it entirely (which caused fragments like "Pushed. is live on .").
    """
    # Remove fenced code blocks entirely (actual code isn't speakable)
    text = re.sub(r"```[\s\S]*?```", " ", text)
    # Inline code: keep the content, drop the backticks
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Headers: keep text, drop #
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bold/italic: keep text
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    # Links: keep label
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Bullet/numbered lists: keep text, drop prefix
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Tables: extract cell content
    text = re.sub(r"^\|[-|:\s]+\|$", "", text, flags=re.MULTILINE)  # separator rows
    text = re.sub(r"\|", " ", text)  # cell borders → spaces
    # HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Long file paths (but keep short names)
    text = re.sub(r"/?(?:home|tmp|usr|etc|var)/\S{15,}", "", text)
    # Collapse whitespace
    text = re.sub(r"\n{2,}", ". ", text)
    text = " ".join(text.split())
    return text.strip()


# --------------------------------------------------------------------------- #
# Sentence utilities
# --------------------------------------------------------------------------- #

def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, filtering out noise."""
    parts = re.split(r'(?<=[.!?])\s+', text)
    sentences = []
    for s in parts:
        s = s.strip()
        # Skip very short fragments and pure-symbol noise
        if len(s) < 8:
            continue
        # Skip sentences that are mostly non-alpha (code artifacts)
        alpha_ratio = sum(c.isalpha() or c.isspace() for c in s) / max(len(s), 1)
        if alpha_ratio < 0.5:
            continue
        sentences.append(s)
    return sentences


def _classify_sentence(s: str) -> str:
    """Classify a sentence by its rhetorical function."""
    lower = s.lower().strip()

    if s.rstrip().endswith("?"):
        return "question"

    # Action completed
    if re.search(r"\b(pushed|committed|deployed|installed|created|updated|"
                 r"wrote|built|fixed|merged|shipped|published|saved|generated|"
                 r"configured|set up|started|stopped|restarted|deleted|removed)\b", lower):
        return "action"

    # Result/status
    if re.search(r"\b(done|complete|finished|ready|passed|succeeded|failed|"
                 r"running|working|live|active|available|all \d+)\b", lower):
        return "result"

    # Explanation of what was done
    if re.search(r"\b(this (adds|creates|fixes|updates|changes|replaces|removes)|"
                 r"now (you|the|it|we)|here's|you should|you can|"
                 r"the (key|main|big) (change|difference|improvement))\b", lower):
        return "explanation"

    # Request for user action/decision
    if re.search(r"\b(want me to|should I|shall I|let me know|what do you|"
                 r"check .*(out|it)|take a look|how does)\b", lower):
        return "request"

    return "other"


# --------------------------------------------------------------------------- #
# Local summarizer (no API)
# --------------------------------------------------------------------------- #

def _local_summarize(text: str) -> str:
    """Extract a natural 2-3 sentence summary from a Claude response.

    Strategy:
    1. Parse response into classified sentences
    2. Pick the most important ones by type (action > result > explanation > request > question)
    3. Compose them in natural order
    4. Cap at ~50 words for comfortable TTS duration (~15-20 seconds)
    """
    clean = strip_markdown(text)
    if not clean:
        return ""

    # Very short responses — just speak them
    if len(clean) < 120:
        return clean

    sentences = _split_sentences(clean)
    if not sentences:
        return clean[:200]

    # Classify all sentences
    classified: dict[str, list[str]] = {
        "question": [], "action": [], "result": [],
        "explanation": [], "request": [], "other": [],
    }
    for s in sentences:
        cat = _classify_sentence(s)
        classified[cat].append(s)

    # Build summary from best sentences (pick 2-3)
    picked: list[str] = []

    # Always include an action sentence if available (what was done)
    if classified["action"]:
        # Prefer later actions (closer to conclusion)
        picked.append(classified["action"][-1])

    # Add a result or explanation
    if classified["result"]:
        picked.append(classified["result"][-1])
    elif classified["explanation"]:
        picked.append(classified["explanation"][-1])

    # End with a question or request if present (engagement)
    if classified["question"]:
        picked.append(classified["question"][-1])
    elif classified["request"]:
        picked.append(classified["request"][-1])

    # If we got nothing interesting, take the last 2 meaningful sentences
    if not picked:
        picked = sentences[-2:] if len(sentences) >= 2 else sentences[-1:]

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for s in picked:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    summary = " ".join(unique)
    return _cap(summary, max_words=50)


# --------------------------------------------------------------------------- #
# API summarizer (Haiku)
# --------------------------------------------------------------------------- #

def _api_summarize(text: str) -> str | None:
    """Use Haiku to generate a natural voice-over summary.

    Returns None if API is unavailable or fails, so caller can fall back.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        # Truncate very long responses to save tokens
        truncated = text[:3000] if len(text) > 3000 else text

        response = client.messages.create(
            model="claude-haiku-4-20250414",
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": f"""You are a cyberpunk AI assistant narrating what you just did.
Summarize this response as 2-3 short spoken sentences (under 40 words total).
Be natural, direct, slightly cool. No markdown. No filler words.
Focus on: what you did and what the user should know.

Response to summarize:
{truncated}"""
            }],
        )
        result = response.content[0].text.strip()
        if result:
            log.debug("Haiku summary: %s", result)
            return result
    except Exception as e:
        log.debug("Haiku summarization failed: %s", e)

    return None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def summarize_for_voice(text: str) -> str:
    """Transform a Claude response into natural spoken narration.

    Tries API summarization first (if ANTHROPIC_API_KEY is set),
    falls back to local structural extraction.
    """
    if not text or not text.strip():
        return ""

    # Try API path first
    result = _api_summarize(text)
    if result:
        return result

    # Fall back to local
    return _local_summarize(text)


def tool_narration(tool_name: str, tool_input: dict | None = None) -> str:
    """Generate a brief spoken narration for a tool use.

    Used by PostToolUse hooks to give mid-response status updates.
    Returns a short phrase (3-8 words) suitable for quick TTS.
    """
    name = tool_name.lower()

    # Map common tools to natural narrations
    narrations = {
        "read": "Reading the file.",
        "write": "Writing changes.",
        "edit": "Making edits.",
        "bash": "Running a command.",
        "glob": "Searching for files.",
        "grep": "Searching the code.",
        "agent": "Delegating to a subagent.",
        "websearch": "Searching the web.",
        "webfetch": "Fetching a page.",
    }

    for key, narration in narrations.items():
        if key in name:
            return narration

    return "Working on it."


def _cap(text: str, max_words: int = 50) -> str:
    """Cap at max_words, ending at a natural sentence boundary."""
    words = text.split()
    if len(words) <= max_words:
        return text
    capped = " ".join(words[:max_words])
    # Try to end at a sentence boundary
    for i in range(len(capped) - 1, max(0, len(capped) - 30), -1):
        if capped[i] in ".!?":
            return capped[: i + 1]
    return capped + "."
