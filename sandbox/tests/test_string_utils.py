import pytest

from string_utils import reverse, is_palindrome, word_count, truncate


def test_reverse_returns_reversed_text():
    assert reverse("abc") == "cba"


def test_is_palindrome_ignores_case_and_punctuation():
    assert is_palindrome("A man, a plan, a canal: Panama!")


def test_is_palindrome_false_for_non_palindrome():
    assert not is_palindrome("hello world")


def test_word_count_counts_whitespace_separated_words():
    assert word_count("one two  three") == 3


def test_truncate_short_text_unchanged():
    assert truncate("hi", 10) == "hi"


def test_truncate_long_text_adds_suffix():
    assert truncate("hello world", 8) == "hello..."


def test_truncate_negative_max_length_raises():
    with pytest.raises(ValueError):
        truncate("hello", -1)
