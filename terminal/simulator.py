from __future__ import annotations

from shift.catalog import LaneCatalog, LaneId, Position
from terminal.device import DeviceReply, LatchedRead
from terminal.navigation import first_lane


class SimulatedDevice:
    """Step-driven instrument with one physical selector and delayed replies."""

    def __init__(self, catalog: LaneCatalog):
        self._catalog = catalog
        self._position: Position | None = None
        self._latched: dict[str, LatchedRead] = {}
        self._replies: dict[str, DeviceReply] = {}
        self._online = True

    def _require_online(self) -> None:
        if not self._online:
            raise RuntimeError('device is stopped')

    def position(self) -> Position | None:
        self._require_online()
        return self._position

    def select_profile(self, profile: str) -> Position:
        self._require_online()
        target = first_lane(self._catalog, profile)
        self._position = target
        return target

    def select_lane(self, lane: LaneId) -> Position:
        current = self.position()
        if current is None:
            raise ValueError('device has no selected profile')
        target = Position(current.profile, lane)
        if not self._catalog.contains(target):
            raise ValueError(f'unknown device lane: {target!r}')
        self._position = target
        return target

    def begin_read(self, order_id: str) -> LatchedRead:
        current = self.position()
        if current is None:
            raise ValueError('device has no selected lane')
        if not isinstance(order_id, str) or not order_id or order_id in self._latched:
            raise ValueError(f'invalid or repeated device read identifier: {order_id!r}')
        latched = LatchedRead(order_id, current)
        self._latched[order_id] = latched
        return latched

    def deliver_reply(self, order_id: str, raw: object) -> DeviceReply:
        self._require_online()
        latched = self._latched[order_id]
        if order_id in self._replies:
            raise ValueError(f'order {order_id!r} already has an unread reply')
        reply = DeviceReply(latched.order_id, latched.position, raw)
        self._replies[order_id] = reply
        return reply

    def poll_reply(self, order_id: str) -> DeviceReply | None:
        self._require_online()
        return self._replies.pop(order_id, None)

    def stop(self) -> None:
        self._online = False
