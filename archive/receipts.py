from __future__ import annotations

from dataclasses import dataclass

from quality.result import Measurement
from shift.catalog import Position


@dataclass(frozen=True, slots=True)
class ReceiptDraft:
    shift_id: str
    order_id: str
    position: Position
    raw: int | str
    measurement: Measurement


@dataclass(frozen=True, slots=True)
class Receipt(ReceiptDraft):
    receipt_id: int


def seal_receipt(receipt_id: int, draft: ReceiptDraft) -> Receipt:
    return Receipt(draft.shift_id, draft.order_id, draft.position, draft.raw,
                   draft.measurement, receipt_id)
