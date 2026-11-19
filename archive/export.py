from __future__ import annotations

from archive.receipts import Receipt
from archive.store import ArchiveStore


def export_shift(store: ArchiveStore, shift_id: str) -> list[Receipt]:
    if store.load_shift_record(shift_id) is None:
        raise LookupError(f'unknown shift {shift_id!r}')
    return store.list_shift_receipts(shift_id)
