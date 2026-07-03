"""
calculator.py
Small arithmetic helpers for the sandbox project the coder agents work on.
"""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    # BUG (seeded): division by zero should raise ValueError, not return 0.
    # tests/test_calculator.py::test_divide_by_zero_raises fails until fixed.
    if b == 0:
        return 0
    return a / b


def average(values):
    if not values:
        raise ValueError("average() requires at least one value")
    return sum(values) / len(values)
