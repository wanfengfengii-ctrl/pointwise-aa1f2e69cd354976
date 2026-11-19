from __future__ import annotations

from shift.catalog import LaneCatalog, LaneId, Position, make_lane_catalog
from terminal.device import DeviceReply, LatchedRead
from terminal.simulator import SimulatedDevice


class RecordedDevice:
    """Observe calls at the port boundary around the existing instrument."""

    def __init__(self, catalog: LaneCatalog):
        self._source = SimulatedDevice(catalog)
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.latches: list[LatchedRead] = []

    def position(self) -> Position | None:
        self.calls.append(("position", ()))
        return self._source.position()

    def select_profile(self, profile: str) -> Position:
        self.calls.append(("select_profile", (profile,)))
        return self._source.select_profile(profile)

    def select_lane(self, lane: LaneId) -> Position:
        self.calls.append(("select_lane", (lane,)))
        return self._source.select_lane(lane)

    def begin_read(self, order_id: str) -> LatchedRead:
        self.calls.append(("begin_read", (order_id,)))
        latched = self._source.begin_read(order_id)
        self.latches.append(latched)
        return latched

    def poll_reply(self, order_id: str) -> DeviceReply | None:
        self.calls.append(("poll_reply", (order_id,)))
        return self._source.poll_reply(order_id)

    def physical_position(self) -> Position | None:
        return self._source.position()

    def deliver_reply(self, order_id: str, raw: object) -> DeviceReply:
        return self._source.deliver_reply(order_id, raw)

    def stop(self) -> None:
        self._source.stop()


def main() -> None:
    catalog = make_lane_catalog([("A", [1, 2, 3]), ("B", [1, 2])])
    device = RecordedDevice(catalog)
    assert device.select_profile("B") == Position("B", 1)
    latched = device.begin_read("port-example")
    assert latched == LatchedRead("port-example", Position("B", 1))
    assert device.poll_reply(latched.order_id) is None
    assert device.select_profile("A") == Position("A", 1)
    assert device.select_lane(2) == Position("A", 2)
    assert device.physical_position() == Position("A", 2)
    device.deliver_reply(latched.order_id, 5)
    reply = device.poll_reply(latched.order_id)
    assert reply == DeviceReply("port-example", Position("B", 1), 5)
    assert device.latches == [latched]
    assert device.calls == [
        ("select_profile", ("B",)),
        ("begin_read", ("port-example",)),
        ("poll_reply", ("port-example",)),
        ("select_profile", ("A",)),
        ("select_lane", (2,)),
        ("poll_reply", ("port-example",)),
    ]
    print("Selected:", device.physical_position(), "Latched reply:", reply)


if __name__ == "__main__":
    main()
