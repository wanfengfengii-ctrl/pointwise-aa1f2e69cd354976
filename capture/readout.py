from __future__ import annotations

from archive.readback import read_receipt
from archive.receipts import Receipt
from archive.store import ArchiveStore
from capture.intake import prepare_receipt
from capture.orders import ReadOrder, ReadOrders, ReadStage, new_order_id
from capture.replies import ReplyError, validate_reply
from terminal.device import DeviceReply
from terminal.selection import ManualTerminal


class Readout:
    def __init__(self, terminal: ManualTerminal, archive: ArchiveStore):
        terminal.claim_readout()
        self._terminal = terminal
        self._archive = archive
        self._orders = ReadOrders()

    def begin_read(self) -> ReadOrder:
        self._orders.require_available()
        shift = self._terminal.shift_config()
        order_id = new_order_id()
        latched = self._terminal.begin_device_read(order_id)
        return self._orders.add_latched(order_id, shift, latched)

    def finish_read(self, order_id: str, reply: DeviceReply | None = None) -> ReadStage | Receipt:
        order = self._orders.get(order_id)
        if order.stage is ReadStage.SIGNED:
            return self._replay(order, reply)
        order = self._orders.require_pending(order_id)
        if reply is None and order.reply is None:
            reply = self._terminal.poll_device_reply(order_id)
            if reply is None:
                return ReadStage.PENDING
        if reply is not None:
            validated = validate_reply(order.latched, reply)
            order = self._orders.record_reply(order_id, validated)
        receipt = self._archive.commit_receipt(prepare_receipt(order))
        self._orders.mark_signed(order_id, receipt)
        return receipt

    def _replay(self, order: ReadOrder, reply: DeviceReply | None) -> Receipt:
        if reply is not None and validate_reply(order.latched, reply) != order.reply:
            raise ReplyError(f'conflicting replay for order {order.order_id!r}')
        if order.receipt_id is None:
            raise RuntimeError(f'signed order {order.order_id!r} lacks a receipt identifier')
        return read_receipt(self._archive, order.receipt_id)

    def cancel_read(self, order_id: str) -> ReadStage:
        return self._orders.cancel(order_id)
