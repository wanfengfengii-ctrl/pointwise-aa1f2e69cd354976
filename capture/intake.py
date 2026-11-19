from __future__ import annotations

from archive.receipts import ReceiptDraft
from capture.orders import ReadOrder, ReadOrderError, ReadStage
from quality.result import evaluate_measurement


def prepare_receipt(order: ReadOrder) -> ReceiptDraft:
    reply = order.reply
    if order.stage is not ReadStage.PENDING or reply is None or reply.order_id != order.order_id:
        raise ReadOrderError(f'order {order.order_id!r} has no publishable device evidence')
    profile = order.shift.profile_at(order.position)
    measurement = evaluate_measurement(reply.exact, profile)
    return ReceiptDraft(order.shift.shift_id, order.order_id, order.position, reply.raw, measurement)
