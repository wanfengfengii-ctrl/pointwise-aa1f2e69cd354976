from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from shift.catalog import LaneId, Position


@dataclass(frozen=True, slots=True)
class LatchedRead:
    order_id: str
    position: Position


@dataclass(frozen=True, slots=True)
class DeviceReply:
    order_id: str
    position: Position
    raw: object


class DevicePort(Protocol):
    """Device calls each perform a single operation on the physical port."""

    def position(self) -> Position | None:
        ...

    def select_profile(self, profile: str) -> Position:
        """Enter the profile at its first lane."""
        ...

    def select_lane(self, lane: LaneId) -> Position:
        """Change the lane in the currently selected physical profile."""
        ...

    def begin_read(self, order_id: str) -> LatchedRead:
        """Latch the current physical selector position for this order."""
        ...

    def poll_reply(self, order_id: str) -> DeviceReply | None:
        """Consume one available reply for the order, or return None."""
        ...
