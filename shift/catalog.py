from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

LaneId = int | str


def _valid_name(name: object) -> bool:
    return isinstance(name, str) and bool(name.strip())


def _valid_lane(lane: object) -> bool:
    return type(lane) is int or _valid_name(lane)


@dataclass(frozen=True, slots=True)
class Position:
    profile: str
    lane: LaneId

    def __post_init__(self) -> None:
        if not _valid_name(self.profile) or not _valid_lane(self.lane):
            raise ValueError('position requires a profile name and a valid lane')


@dataclass(frozen=True, slots=True)
class LaneCatalog:
    profiles: tuple[tuple[str, tuple[LaneId, ...]], ...]

    def __post_init__(self) -> None:
        try:
            rows = tuple((name, tuple(lanes)) for name, lanes in self.profiles
                         if not isinstance(lanes, (str, bytes)))
        except (TypeError, ValueError) as exc:
            raise ValueError('catalog requires pairs of profile names and lane sequences') from exc
        if not rows or len(rows) != len(self.profiles):
            raise ValueError('catalog requires at least one profile with a lane sequence')
        names = set()
        for name, lanes in rows:
            if not _valid_name(name) or name in names:
                raise ValueError(f'invalid or duplicate profile: {name!r}')
            if not lanes or any(not _valid_lane(lane) for lane in lanes):
                raise ValueError(f'profile {name!r} requires valid lanes')
            if len(set(lanes)) != len(lanes):
                raise ValueError(f'duplicate lane in profile {name!r}')
            names.add(name)
        object.__setattr__(self, 'profiles', rows)

    def lanes(self, profile: str) -> tuple[LaneId, ...]:
        for name, lanes in self.profiles:
            if name == profile:
                return lanes
        raise KeyError(profile)

    def contains(self, position: Position) -> bool:
        for profile, lanes in self.profiles:
            if profile == position.profile:
                return position.lane in lanes
        return False


def make_lane_catalog(
    profiles: Mapping[str, Iterable[LaneId]] | Iterable[tuple[str, Iterable[LaneId]]],
) -> LaneCatalog:
    rows = profiles.items() if isinstance(profiles, Mapping) else profiles
    try:
        declared = tuple(rows)
    except TypeError as exc:
        raise ValueError('catalog must provide profile and lane pairs') from exc
    return LaneCatalog(declared)
