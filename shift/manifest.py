from __future__ import annotations

import json
from pathlib import Path

from archive.store import ArchiveStore
from shift.catalog import Position, make_lane_catalog
from shift.configuration import ShiftConfig
from shift.opening import open_shift
from shift.profiles import make_calibration_profile


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate field {key!r}')
        result[key] = value
    return result


def _fields(value: object, names: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError(f'{label} requires fields {", ".join(sorted(names))}')
    return value


def _rows(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f'{label} must be an ordered array')
    return value


def open_shift_from_manifest(store: ArchiveStore, path: str | Path) -> ShiftConfig:
    path = Path(path)
    try:
        with path.open(encoding='utf-8') as source:
            document = json.load(source, object_pairs_hook=_unique_object)
        roster = _fields(document, {'shift_id', 'profiles'}, 'manifest')
        declarations = []
        calibrations = {}
        for index, value in enumerate(_rows(roster['profiles'], 'profiles')):
            profile = _fields(value, {'name', 'lanes'}, f'profiles[{index}]')
            name = profile['name']
            lanes = []
            for lane_index, value in enumerate(_rows(profile['lanes'], f'{name!r} lanes')):
                label = f'profiles[{index}].lanes[{lane_index}]'
                row = _fields(value, {'lane', 'calibration'}, label)
                try:
                    position = Position(name, row['lane'])
                    calibration = make_calibration_profile(**row['calibration'])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f'{label}: {exc}') from exc
                lanes.append(position.lane)
                calibrations[position] = calibration
            declarations.append((name, lanes))
        catalog = make_lane_catalog(declarations)
    except ValueError as exc:
        raise ValueError(f'{path}: {exc}') from exc
    return open_shift(store, roster['shift_id'], catalog, calibrations)
