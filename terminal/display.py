from __future__ import annotations

from dataclasses import dataclass

from shift.catalog import Position
from terminal.selection import ManualTerminal


@dataclass(frozen=True, slots=True)
class SelectionView:
    shift_id: str
    position: Position | None
    lane_number: int | None


def describe_selection(terminal: ManualTerminal) -> SelectionView:
    shift = terminal.shift_config()
    actual = terminal.current_position()
    if actual is None:
        return SelectionView(shift.shift_id, None, None)
    lanes = shift.catalog.lanes(actual.profile)
    return SelectionView(shift.shift_id, actual, lanes.index(actual.lane) + 1)
