from __future__ import annotations

from collections.abc import Mapping

from archive.store import ArchiveStore
from shift.catalog import LaneCatalog, Position
from shift.configuration import ShiftConfig, make_shift_config
from shift.profiles import CalibrationProfile


class ShiftConflictError(ValueError):
    """The requested shift identifier already has a different frozen roster."""


def open_shift(
    store: ArchiveStore, shift_id: str, catalog: LaneCatalog,
    calibrations: Mapping[Position, CalibrationProfile],
) -> ShiftConfig:
    requested = make_shift_config(shift_id, catalog, calibrations)
    persisted = store.register_shift_record(requested.to_record())
    frozen = ShiftConfig.from_record(persisted)
    if frozen != requested:
        raise ShiftConflictError(f'shift {shift_id!r} already has a different configuration')
    return frozen
