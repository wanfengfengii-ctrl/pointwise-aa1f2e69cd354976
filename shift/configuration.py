from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from shift.catalog import LaneCatalog, Position, make_lane_catalog
from shift.profiles import CalibrationProfile, make_calibration_profile


@dataclass(frozen=True, slots=True)
class ShiftConfig:
    shift_id: str
    catalog: LaneCatalog
    calibrations: tuple[tuple[Position, CalibrationProfile], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.shift_id, str) or not self.shift_id.strip():
            raise ValueError('shift requires a nonempty identifier')
        if not isinstance(self.catalog, LaneCatalog):
            raise ValueError('shift requires a lane catalog')
        try:
            supplied = dict(self.calibrations)
        except (TypeError, ValueError) as exc:
            raise ValueError('calibrations must map positions to profiles') from exc
        positions = tuple(Position(profile, lane)
                          for profile, lanes in self.catalog.profiles for lane in lanes)
        if len(supplied) != len(self.calibrations) or set(supplied) != set(positions):
            raise ValueError('calibrations must cover each catalog position exactly once')
        if any(not isinstance(value, CalibrationProfile) for value in supplied.values()):
            raise ValueError('each position requires a validated calibration profile')
        ordered = tuple((position, supplied[position]) for position in positions)
        object.__setattr__(self, 'calibrations', ordered)

    def profile_at(self, position: Position) -> CalibrationProfile:
        for candidate, calibration in self.calibrations:
            if candidate == position:
                return calibration
        raise KeyError(position)

    def to_record(self) -> dict:
        profiles = []
        calibrations = dict(self.calibrations)
        for name, lanes in self.catalog.profiles:
            lane_records = []
            for lane in lanes:
                profile = calibrations[Position(name, lane)]
                lane_records.append({'lane': lane, 'calibration': {
                    'unit': profile.unit,
                    'scale': str(profile.scale),
                    'offset': str(profile.offset),
                    'lower': str(profile.lower),
                    'upper': str(profile.upper),
                }})
            profiles.append({'name': name, 'lanes': lane_records})
        return {'shift_id': self.shift_id, 'profiles': profiles}

    @classmethod
    def from_record(cls, record: dict) -> ShiftConfig:
        rows = []
        calibrations = {}
        for profile in record['profiles']:
            name = profile['name']
            lanes = []
            for row in profile['lanes']:
                lane = row['lane']
                lanes.append(lane)
                calibrations[Position(name, lane)] = make_calibration_profile(**row['calibration'])
            rows.append((name, lanes))
        return make_shift_config(record['shift_id'], make_lane_catalog(rows), calibrations)


def make_shift_config(
    shift_id: str, catalog: LaneCatalog,
    calibrations: Mapping[Position, CalibrationProfile],
) -> ShiftConfig:
    if not isinstance(calibrations, Mapping):
        raise ValueError('calibrations must map positions to profiles')
    return ShiftConfig(shift_id, catalog, tuple(calibrations.items()))
