from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from uuid import uuid4

from archive.receipts import Receipt
from capture.replies import ReplyError, ValidatedReply
from shift.catalog import Position
from shift.configuration import ShiftConfig
from terminal.device import LatchedRead


class ReadOrderError(ValueError):
    """A read order is unknown or cannot make the requested transition."""


class ReadStage(str, Enum):
    PENDING = 'pending'
    CANCELLED = 'cancelled'
    SIGNED = 'signed'


@dataclass(frozen=True, slots=True)
class ReadOrder:
    shift: ShiftConfig
    latched: LatchedRead
    stage: ReadStage = ReadStage.PENDING
    reply: ValidatedReply | None = None
    receipt_id: int | None = None

    @property
    def order_id(self) -> str:
        return self.latched.order_id

    @property
    def position(self) -> Position:
        return self.latched.position


def new_order_id() -> str:
    return uuid4().hex


class ReadOrders:
    def __init__(self) -> None:
        self._orders: dict[str, ReadOrder] = {}
        self._pending: str | None = None

    def require_available(self) -> None:
        if self._pending is not None:
            raise ReadOrderError('terminal already has a pending read')

    def get(self, order_id: str) -> ReadOrder:
        if not isinstance(order_id, str) or order_id not in self._orders:
            raise ReadOrderError(f'unknown read order {order_id!r}')
        return self._orders[order_id]

    def require_pending(self, order_id: str) -> ReadOrder:
        order = self.get(order_id)
        if order.stage is not ReadStage.PENDING or self._pending != order_id:
            raise ReadOrderError(f'order {order_id!r} is {order.stage.value}')
        return order

    def add_latched(self, order_id: str, shift: ShiftConfig, latched: LatchedRead) -> ReadOrder:
        self.require_available()
        if order_id in self._orders or latched.order_id != order_id:
            raise ReadOrderError(f'invalid latched identity for {order_id!r}')
        if not shift.catalog.contains(latched.position):
            raise ReadOrderError(f'latched position is outside shift {shift.shift_id!r}')
        order = ReadOrder(shift, latched)
        self._orders[order_id] = order
        self._pending = order_id
        return order

    def record_reply(self, order_id: str, reply: ValidatedReply) -> ReadOrder:
        order = self.require_pending(order_id)
        if reply.order_id != order_id or (order.reply is not None and order.reply != reply):
            raise ReplyError(f'conflicting evidence for order {order_id!r}')
        updated = replace(order, reply=reply)
        self._orders[order_id] = updated
        return updated

    def mark_signed(self, order_id: str, receipt: Receipt) -> None:
        order = self.require_pending(order_id)
        if (order.reply is None or receipt.order_id != order_id
                or receipt.shift_id != order.shift.shift_id or receipt.position != order.position
                or receipt.raw != order.reply.raw):
            raise ReadOrderError(f'receipt does not match order {order_id!r}')
        self._orders[order_id] = replace(order, stage=ReadStage.SIGNED, receipt_id=receipt.receipt_id)
        self._pending = None

    def cancel(self, order_id: str) -> ReadStage:
        order = self.require_pending(order_id)
        self._orders[order_id] = replace(order, stage=ReadStage.CANCELLED, reply=None)
        self._pending = None
        return ReadStage.CANCELLED
