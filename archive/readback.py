from __future__ import annotations

from archive.receipts import Receipt
from archive.store import ArchiveStore


def read_receipt(store: ArchiveStore, receipt_id: int) -> Receipt:
    receipt = store.find_receipt(receipt_id)
    if receipt is None:
        raise LookupError(f'unknown receipt {receipt_id!r}')
    return receipt
