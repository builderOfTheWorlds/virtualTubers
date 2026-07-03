"""
string_utils.py
Small string helpers for the sandbox project the coder agents work on.
"""


def reverse(text):
    return text[::-1]


def is_palindrome(text):
    cleaned = "".join(ch.lower() for ch in text if ch.isalnum())
    return cleaned == cleaned[::-1]


def word_count(text):
    return len(text.split())


def truncate(text, max_length, suffix="..."):
    if max_length < 0:
        raise ValueError("max_length must be >= 0")
    if len(text) <= max_length:
        return text
    if max_length <= len(suffix):
        return text[:max_length]
    return text[: max_length - len(suffix)] + suffix
