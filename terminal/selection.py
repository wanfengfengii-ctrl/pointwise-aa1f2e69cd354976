from __future__ import annotations

from shift.catalog import Position
from shift.configuration import ShiftConfig
from terminal.device import DevicePort, DeviceReply, LatchedRead
from terminal.navigation import SelectionError as SelectionError
from terminal.navigation import first_lane, next_lane


class ManualTerminal:
    """One terminal with a single physical selector.

    While an auto job holds the selector, manual navigation only prepares the
    operator's logical work position; the physical lane stays with the job until
    it hands the selector back.
    """

    def __init__(self, device: DevicePort, shift_config: ShiftConfig | None = None):
        self._device = device
        self._shift = shift_config
        self._reader_attached = False
        self._manual_position: Position | None = None
        self._selector_held = False

    def shift_config(self) -> ShiftConfig:
        if self._shift is None:
            raise SelectionError('no open shift is bound to the terminal')
        return self._shift

    # -- selector ownership ----------------------------------------------------

    def selector_held(self) -> bool:
        return self._selector_held

    def hold_selector(self) -> None:
        self._selector_held = True

    def release_selector(self) -> None:
        self._selector_held = False

    # -- manual work position --------------------------------------------------

    def current_position(self) -> Position | None:
        if self._selector_held:
            return self._manual_position
        position = self._device.position()
        if position is not None:
            self._manual_position = position
        return position

    def manual_position(self) -> Position | None:
        return self._manual_position

    def physical_position(self) -> Position | None:
        """Read the actual selector without moving the manual work position."""
        return self._device.position()

    def select(self, profile: str | None = None) -> Position:
        catalog = self.shift_config().catalog
        if profile is not None:
            target = first_lane(catalog, profile)
            position = target if self._selector_held else self._device.select_profile(target.profile)
            self._manual_position = position
            return position
        current = self.current_position()
        if current is None:
            raise SelectionError('select a profile before advancing')
        target = next_lane(catalog, current)
        position = target if self._selector_held else self._device.select_lane(target.lane)
        self._manual_position = position
        return position

    # -- readout plumbing ------------------------------------------------------

    def claim_readout(self) -> None:
        if self._reader_attached:
            raise RuntimeError('this terminal already has a readout')
        self._reader_attached = True

    def begin_device_read(self, order_id: str) -> LatchedRead:
        catalog = self.shift_config().catalog
        current = self._device.position()
        if current is None:
            raise SelectionError('select a position before requesting a read')
        if not catalog.contains(current):
            raise SelectionError(f'device position is outside the shift roster: {current!r}')
        latched = self._device.begin_read(order_id)
        self._manual_position = latched.position
        return latched

    def latch_device_read(self, order_id: str) -> LatchedRead:
        """Latch the actual selector position for an auto read order."""
        return self._device.begin_read(order_id)

    def move_profile(self, profile: str) -> Position:
        return self._device.select_profile(profile)

    def move_lane(self, lane: object) -> Position:
        return self._device.select_lane(lane)  # type: ignore[arg-type]

    def poll_device_reply(self, order_id: str) -> DeviceReply | None:
        return self._device.poll_reply(order_id)
