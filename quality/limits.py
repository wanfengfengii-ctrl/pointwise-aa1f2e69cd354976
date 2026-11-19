from __future__ import annotations

from enum import Enum

from quality.units import Quantity, value_in_unit
from shift.profiles import CalibrationProfile


class Verdict(str, Enum):
    PASS = 'pass'
    OUTSIDE = 'outside_limit'


def judge_limits(quantity: Quantity, profile: CalibrationProfile) -> Verdict:
    value = value_in_unit(quantity, profile.unit)
    if profile.lower <= value <= profile.upper:
        return Verdict.PASS
    return Verdict.OUTSIDE
