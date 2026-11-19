from __future__ import annotations

from shift.catalog import Position
from shift.configuration import ShiftConfig
from terminal.device import DevicePort, DeviceReply, LatchedRead
from terminal.navigation import SelectionError as SelectionError
from terminal.navigation import first_lane, next_lane


class ManualTerminal:
    def __init__(self, device: DevicePort, shift_config: ShiftConfig | None = None):
        self._device = device
        self._shift = shift_config
        self._reader_attached = False

    def shift_config(self) -> ShiftConfig:
        if self._shift is None:
            raise SelectionError('no open shift is bound to the terminal')
        return self._shift

    def current_position(self) -> Position | None:
        return self._device.position()

    def select(self, profile: str | None = None) -> Position:
        catalog = self.shift_config().catalog
        if profile is not None:
            target = first_lane(catalog, profile)
            return self._device.select_profile(target.profile)
        current = self.current_position()
        if current is None:
            raise SelectionError('select a profile before advancing')
        target = next_lane(catalog, current)
        return self._device.select_lane(target.lane)

    def claim_readout(self) -> None:
        if self._reader_attached:
            raise RuntimeError('this terminal already has a readout')
        self._reader_attached = True

    def begin_device_read(self, order_id: str) -> LatchedRead:
        catalog = self.shift_config().catalog
        current = self.current_position()
        if current is None:
            raise SelectionError('select a position before requesting a read')
        if not catalog.contains(current):
            raise SelectionError(f'device position is outside the shift roster: {current!r}')
        return self._device.begin_read(order_id)

    def poll_device_reply(self, order_id: str) -> DeviceReply | None:
        return self._device.poll_reply(order_id)
