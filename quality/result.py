from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from quality.conversion import convert_raw
from quality.limits import Verdict, judge_limits
from quality.units import Quantity
from shift.profiles import CalibrationProfile


@dataclass(frozen=True, slots=True)
class Measurement:
    quantity: Quantity
    verdict: Verdict
    calibration: CalibrationProfile


def evaluate_measurement(raw: Fraction, profile: CalibrationProfile) -> Measurement:
    quantity = convert_raw(raw, profile)
    return Measurement(quantity, judge_limits(quantity, profile), profile)
