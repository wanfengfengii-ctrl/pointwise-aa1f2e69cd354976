from __future__ import annotations

from shift.catalog import LaneCatalog, Position


class SelectionError(ValueError):
    """The requested navigation cannot be applied to the current roster."""


def first_lane(catalog: LaneCatalog, profile: str) -> Position:
    try:
        lane = catalog.lanes(profile)[0]
    except KeyError as exc:
        raise SelectionError(f'unknown profile {profile!r}') from exc
    return Position(profile, lane)


def next_lane(catalog: LaneCatalog, position: Position) -> Position:
    try:
        lanes = catalog.lanes(position.profile)
        index = lanes.index(position.lane)
    except (KeyError, ValueError) as exc:
        raise SelectionError(f'position is outside the roster: {position!r}') from exc
    if index + 1 == len(lanes):
        raise SelectionError(f'already at the last lane in {position.profile!r}')
    return Position(position.profile, lanes[index + 1])
