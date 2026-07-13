"""Phoneme-driven mouth frame selection for speaking animation."""

from __future__ import annotations

import threading


# Maps the first matching phoneme category to a speaking frame index (0-3):
#   0 = closed  ───────
#   1 = slight  ─╌─╌─╌─
#   2 = open    ╌         ╌
#   3 = wide    ═════════
_PHONEME_FRAME: dict[frozenset[str], int] = {
    frozenset({"m", "b", "p", "f", "v"}): 0,
    frozenset({"t", "d", "s", "z", "n", "l", "r"}): 1,
    frozenset({"a", "e", "i"}): 2,
    frozenset({"o", "u"}): 3,
}

# Explicit multi-character phoneme tokens mapped directly.
_TOKEN_FRAME: dict[str, int] = {
    "ah": 3,
    "ow": 3,
    "oh": 3,
    "oo": 3,
    "ee": 2,
    "ay": 2,
}

_DEFAULT_FRAME = 1  # slight open when nothing matches


def _frame_for_phoneme(phoneme: str) -> int | None:
    """Return frame index for a single phoneme token, or None if no match."""
    p = phoneme.lower()
    if p in _TOKEN_FRAME:
        return _TOKEN_FRAME[p]
    for char_set, frame in _PHONEME_FRAME.items():
        if p in char_set:
            return frame
    return None


def _dominant_frame(word: str) -> int:
    """Pick a speaking frame based on the dominant phoneme in *word*.

    Strategy: scan the word for vowel clusters first (they drive mouth shape),
    then fall back to the leading consonant, then use the default.
    """
    w = word.lower().strip(".,!?;:\"'")

    # Check two-character digraphs first (left to right, priority order)
    for i in range(len(w) - 1):
        digraph = w[i : i + 2]
        frame = _frame_for_phoneme(digraph)
        if frame is not None:
            return frame

    # Single characters — prefer vowels
    vowel_frame: int | None = None
    for ch in w:
        frame = _frame_for_phoneme(ch)
        if frame is not None:
            if frame >= 2:          # open or wide — return immediately
                return frame
            if vowel_frame is None:
                vowel_frame = frame # keep first consonant match as fallback

    return vowel_frame if vowel_frame is not None else _DEFAULT_FRAME


class MouthSync:
    """Thread-safe mouth frame selector driven by word-timing callbacks.

    Usage::

        sync = MouthSync()
        player.play(audio, sample_rate=sr, word_timings=timings,
                    on_word=sync.on_word)
        # renderer polls:
        frame_idx = sync.current_frame
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: int = 0

    # -- public interface ---------------------------------------------------

    @property
    def current_frame(self) -> int:
        """Current speaking frame index (0-3). Safe to read from any thread."""
        with self._lock:
            return self._frame

    def on_word(self, wt: object) -> None:
        """Callback fired by AudioPlayer for each word timing event.

        *wt* is a :class:`~avatar.voice.base.WordTiming` instance; we only
        need its ``word`` attribute, so we accept ``object`` to avoid a
        circular import.
        """
        word: str = getattr(wt, "word", "") or ""
        frame = self.frame_for_word(word)
        with self._lock:
            self._frame = frame

    def frame_for_word(self, word: str) -> int:
        """Return the speaking frame index for *word* (0-3).

        This is a pure function — no state is mutated — so it can be used
        independently for testing or look-ahead scheduling.
        """
        if not word or not word.strip():
            return 0  # silence / pause → closed
        return _dominant_frame(word)

    def reset(self) -> None:
        """Reset to closed mouth (call when speaking stops)."""
        with self._lock:
            self._frame = 0
