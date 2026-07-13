"""Tests for MouthSync phoneme-driven mouth animation."""

from __future__ import annotations

import threading

import pytest

from avatar.frames.mouth_sync import MouthSync, _dominant_frame
from avatar.voice.base import WordTiming


class TestFrameForWord:
    """Unit tests for the frame_for_word public method."""

    def test_empty_string_returns_closed(self):
        sync = MouthSync()
        assert sync.frame_for_word("") == 0

    def test_whitespace_returns_closed(self):
        sync = MouthSync()
        assert sync.frame_for_word("   ") == 0

    def test_closed_sounds_m(self):
        # Words dominated by bilabials → frame 0 (closed)
        sync = MouthSync()
        assert sync.frame_for_word("mm") == 0

    def test_mid_sounds_n(self):
        # "nn" — alveolar nasal → frame 1 (slight)
        sync = MouthSync()
        assert sync.frame_for_word("nn") == 1

    def test_open_sounds_ae(self):
        # Strong 'a' vowel → frame 2 (open)
        sync = MouthSync()
        assert sync.frame_for_word("say") == 2

    def test_wide_sounds_ow(self):
        # "ow" digraph → frame 3 (wide)
        sync = MouthSync()
        assert sync.frame_for_word("ow") == 3

    def test_wide_sounds_oh(self):
        sync = MouthSync()
        assert sync.frame_for_word("oh") == 3

    def test_wide_sounds_oo(self):
        sync = MouthSync()
        assert sync.frame_for_word("oo") == 3

    def test_word_with_punctuation_stripped(self):
        sync = MouthSync()
        # Trailing comma should not affect result
        result_clean = sync.frame_for_word("say")
        result_punct = sync.frame_for_word("say,")
        assert result_clean == result_punct

    def test_open_vowel_a(self):
        # "at" — 'a' vowel → frame 2
        sync = MouthSync()
        assert sync.frame_for_word("at") == 2

    def test_open_vowel_e(self):
        sync = MouthSync()
        # "set" → 'e' vowel → frame 2
        assert sync.frame_for_word("set") == 2

    def test_open_vowel_i(self):
        sync = MouthSync()
        # "it" → 'i' vowel → frame 2
        assert sync.frame_for_word("it") == 2

    def test_wide_vowel_o(self):
        sync = MouthSync()
        # "top" → 'o' vowel → frame 3
        assert sync.frame_for_word("top") == 3

    def test_wide_vowel_u(self):
        sync = MouthSync()
        # "but" → 'u' vowel → frame 3
        assert sync.frame_for_word("but") == 3

    def test_return_value_in_range(self):
        sync = MouthSync()
        words = ["hello", "world", "the", "quick", "brown", "fox", "jumps"]
        for word in words:
            result = sync.frame_for_word(word)
            assert 0 <= result <= 3, f"frame_for_word({word!r}) = {result} out of range"

    def test_case_insensitive(self):
        sync = MouthSync()
        assert sync.frame_for_word("SAY") == sync.frame_for_word("say")


class TestCurrentFrame:
    """Tests for the current_frame property and state transitions."""

    def test_initial_frame_is_closed(self):
        sync = MouthSync()
        assert sync.current_frame == 0

    def test_on_word_updates_frame(self):
        sync = MouthSync()
        wt = WordTiming(word="say", start=0.0, end=0.3)
        sync.on_word(wt)
        assert sync.current_frame == sync.frame_for_word("say")

    def test_on_word_with_closed_mouth_word(self):
        sync = MouthSync()
        wt = WordTiming(word="mm", start=0.0, end=0.2)
        sync.on_word(wt)
        assert sync.current_frame == 0

    def test_reset_returns_to_closed(self):
        sync = MouthSync()
        wt = WordTiming(word="say", start=0.0, end=0.3)
        sync.on_word(wt)
        assert sync.current_frame != 0  # sanity: it changed
        sync.reset()
        assert sync.current_frame == 0

    def test_on_word_accepts_object_with_word_attr(self):
        """on_word should work with any object that has a .word attribute."""
        sync = MouthSync()

        class FakeWordTiming:
            word = "hello"
            start = 0.0
            end = 0.5

        sync.on_word(FakeWordTiming())
        assert 0 <= sync.current_frame <= 3


class TestThreadSafety:
    """Verify MouthSync is safe under concurrent access."""

    def test_concurrent_writes_do_not_raise(self):
        sync = MouthSync()
        errors: list[Exception] = []

        words = ["hello", "world", "say", "mm", "oh", "the", "quick", "jump"]

        def writer(word: str) -> None:
            try:
                wt = WordTiming(word=word, start=0.0, end=0.3)
                for _ in range(50):
                    sync.on_word(wt)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(w,)) for w in words]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Exceptions in threads: {errors}"
        assert 0 <= sync.current_frame <= 3

    def test_concurrent_read_write(self):
        sync = MouthSync()
        results: list[int] = []
        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(100):
                    results.append(sync.current_frame)
            except Exception as exc:
                errors.append(exc)

        def writer() -> None:
            try:
                for word in ["say", "mm", "oh", "hello"] * 25:
                    wt = WordTiming(word=word, start=0.0, end=0.2)
                    sync.on_word(wt)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=reader),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        assert all(0 <= r <= 3 for r in results)

    def test_reset_is_thread_safe(self):
        sync = MouthSync()
        errors: list[Exception] = []

        def toggle() -> None:
            try:
                for _ in range(100):
                    wt = WordTiming(word="say", start=0.0, end=0.3)
                    sync.on_word(wt)
                    sync.reset()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=toggle) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        assert sync.current_frame in (0, 2)  # either reset or last "say" write
