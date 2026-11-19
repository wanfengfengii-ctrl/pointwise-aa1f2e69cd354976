from __future__ import annotations

from fractions import Fraction

from quality.units import Quantity
from shift.profiles import CalibrationProfile


def convert_raw(raw: Fraction, profile: CalibrationProfile) -> Quantity:
    if not isinstance(raw, Fraction):
        raise ValueError('conversion requires a parsed exact raw value')
    return Quantity(raw * profile.scale + profile.offset, profile.unit)
