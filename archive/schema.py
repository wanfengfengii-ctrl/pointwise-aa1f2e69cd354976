from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def write_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield
        connection.execute('COMMIT')
    except BaseException:
        if connection.in_transaction:
            connection.execute('ROLLBACK')
        raise


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute('PRAGMA foreign_keys = ON')
    with write_transaction(connection):
        connection.execute('''
            CREATE TABLE IF NOT EXISTS shifts (
                shift_id TEXT NOT NULL PRIMARY KEY,
                configuration TEXT NOT NULL
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS receipts (
                receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
                order_id TEXT NOT NULL UNIQUE,
                body TEXT NOT NULL
            )
        ''')
        connection.execute('''
            CREATE INDEX IF NOT EXISTS receipts_by_shift_issue
            ON receipts (shift_id, receipt_id)
        ''')
