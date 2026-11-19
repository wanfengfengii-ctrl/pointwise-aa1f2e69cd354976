from __future__ import annotations

import json
import sqlite3
from os import PathLike

from archive.codec import decode_receipt, encode_receipt_body
from archive.receipts import Receipt, ReceiptDraft, seal_receipt
from archive.schema import initialize_schema, write_transaction


class ArchiveStore:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    @classmethod
    def open(cls, path: str | PathLike[str]) -> ArchiveStore:
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            initialize_schema(connection)
        except BaseException:
            connection.close()
            raise
        return cls(connection)

    def __enter__(self) -> ArchiveStore:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def load_shift_record(self, shift_id: str) -> dict | None:
        if not isinstance(shift_id, str):
            return None
        row = self._connection.execute(
            'SELECT configuration FROM shifts WHERE shift_id = ?', (shift_id,),
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def register_shift_record(self, record: dict) -> dict:
        shift_id = record['shift_id']
        payload = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
        with write_transaction(self._connection):
            self._connection.execute('''
                INSERT INTO shifts (shift_id, configuration) VALUES (?, ?)
                ON CONFLICT (shift_id) DO NOTHING
            ''', (shift_id, payload))
            persisted = self.load_shift_record(shift_id)
            if persisted is None:
                raise sqlite3.DatabaseError(f'could not register shift {shift_id!r}')
        return persisted

    def commit_receipt(self, draft: ReceiptDraft) -> Receipt:
        body = encode_receipt_body(draft)
        with write_transaction(self._connection):
            prior = self._connection.execute('''
                SELECT receipt_id, shift_id, order_id, body FROM receipts WHERE order_id = ?
            ''', (draft.order_id,)).fetchone()
            if prior is not None:
                if prior[1] != draft.shift_id or prior[3] != body:
                    raise ValueError(f'conflicting archived evidence for order {draft.order_id!r}')
                receipt = decode_receipt(*prior)
            else:
                result = self._connection.execute('''
                    INSERT INTO receipts (shift_id, order_id, body) VALUES (?, ?, ?)
                ''', (draft.shift_id, draft.order_id, body))
                receipt = seal_receipt(result.lastrowid, draft)
        return receipt

    def find_receipt(self, receipt_id: int) -> Receipt | None:
        if type(receipt_id) is not int or not 0 < receipt_id < 2**63:
            return None
        row = self._connection.execute('''
            SELECT receipt_id, shift_id, order_id, body FROM receipts WHERE receipt_id = ?
        ''', (receipt_id,)).fetchone()
        return None if row is None else decode_receipt(*row)

    def list_shift_receipts(self, shift_id: str) -> list[Receipt]:
        rows = self._connection.execute('''
            SELECT receipt_id, shift_id, order_id, body FROM receipts
            WHERE shift_id = ? ORDER BY receipt_id
        ''', (shift_id,))
        return [decode_receipt(*row) for row in rows]
