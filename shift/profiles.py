from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

_DECIMAL = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')
_RATIO = re.compile(r'([+-]?[0-9]+)/([+-]?[0-9]+)')


def parse_exact_number(value: object) -> Fraction:
    if isinstance(value, Fraction) or type(value) is int:
        return Fraction(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            if match := _RATIO.fullmatch(text):
                return Fraction(int(match[1]), int(match[2]))
            if _DECIMAL.fullmatch(text):
                return Fraction(text)
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError(f'invalid exact number: {value!r}') from exc
    raise ValueError(f'invalid exact number: {value!r}')


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    unit: str
    scale: Fraction
    offset: Fraction
    lower: Fraction
    upper: Fraction

    def __post_init__(self) -> None:
        if not isinstance(self.unit, str) or not self.unit.strip():
            raise ValueError('calibration requires a nonempty unit')
        for field in ('scale', 'offset', 'lower', 'upper'):
            try:
                number = parse_exact_number(getattr(self, field))
            except ValueError as exc:
                raise ValueError(f'invalid calibration {field}: {exc}') from exc
            object.__setattr__(self, field, number)
        if self.lower > self.upper:
            raise ValueError('calibration lower limit exceeds upper limit')


def make_calibration_profile(
    *, unit: str, scale: object, offset: object, lower: object, upper: object,
    offset_unit: str | None = None, lower_unit: str | None = None,
    upper_unit: str | None = None,
) -> CalibrationProfile:
    for field, declared in (
        ('offset', offset_unit), ('lower', lower_unit), ('upper', upper_unit),
    ):
        if declared is not None and declared != unit:
            raise ValueError(f'{field} unit {declared!r} differs from {unit!r}')
    return CalibrationProfile(unit, scale, offset, lower, upper)
