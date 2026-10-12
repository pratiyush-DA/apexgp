"""Unit tests for the Calculator engine.
"""

from calculator import Calculator


def test_calculator_sum():
    """Verifies sum calculation."""
    calc = Calculator(10.0)
    result = calc.calculate_sum(5.0)
    assert result == 15.0


def test_calculator_product():
    """Verifies multiplication calculation."""
    calc = Calculator(4.0)
    result = calc.calculate_product(3.0)
    assert result == 12.0
