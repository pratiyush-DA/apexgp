"""Calculator engine module utilizing math_ops.
"""

from math_ops import add, multiply


class Calculator:
    """Stateful calculator executing mathematical calculations."""

    def __init__(self, initial: float = 0.0):
        self.current = initial

    def calculate_sum(self, value: float) -> float:
        """Adds value to current total."""
        self.current = add(self.current, value)
        return self.current

    def calculate_product(self, factor: float) -> float:
        """Multiplies current total by factor."""
        self.current = multiply(self.current, factor)
        return self.current
