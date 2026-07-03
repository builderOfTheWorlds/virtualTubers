import pytest

from calculator import add, subtract, multiply, divide, average


def test_add_returns_sum():
    assert add(2, 3) == 5


def test_subtract_returns_difference():
    assert subtract(10, 4) == 6


def test_multiply_returns_product():
    assert multiply(3, 4) == 12


def test_divide_returns_quotient():
    assert divide(10, 4) == 2.5


def test_divide_by_zero_raises():
    with pytest.raises(ValueError):
        divide(1, 0)


def test_average_returns_mean():
    assert average([2, 4, 6]) == 4


def test_average_empty_raises():
    with pytest.raises(ValueError):
        average([])
