from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True, slots=True)
class Quantity:
    value: Fraction
    unit: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, Fraction):
            raise ValueError('quantity must carry an exact Fraction')
        if not isinstance(self.unit, str) or not self.unit.strip():
            raise ValueError('quantity requires a nonempty unit')


def value_in_unit(quantity: Quantity, unit: str) -> Fraction:
    if quantity.unit != unit:
        raise ValueError(f'cannot compare {quantity.unit!r} to limits in {unit!r}')
    return quantity.value
