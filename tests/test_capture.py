import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

from archive.export import export_shift
from archive.readback import read_receipt
from archive.receipts import Receipt, ReceiptDraft
from archive.store import ArchiveStore
from capture.orders import ReadOrderError, ReadStage
from capture.readout import Readout
from capture.replies import ReplyError
from quality.limits import Verdict, judge_limits
from quality.units import Quantity
from shift.catalog import Position, make_lane_catalog
from shift.opening import open_shift
from shift.profiles import make_calibration_profile
from terminal.device import DeviceReply
from terminal.selection import ManualTerminal, SelectionError
from terminal.simulator import SimulatedDevice
from tests import SAMPLE_SHIFT, sample_bench


class CaptureTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.database = Path(directory) / 'capture.sqlite'
        self.store = self.enterContext(ArchiveStore.open(self.database))
        self.config, self.device, self.terminal, self.readout = sample_bench(self.store)

    def _select_a2(self):
        self.terminal.select('A')
        self.terminal.select()

    def _sample(self, raw):
        order = self.readout.begin_read()
        self.device.deliver_reply(order.order_id, raw)
        receipt = self.readout.finish_read(order.order_id)
        self.assertIsInstance(receipt, Receipt)
        return receipt

    def test_latched_position(self):
        self._select_a2()
        order = self.readout.begin_read()
        self.assertEqual(order.position, Position('A', 2))
        self.assertIs(self.readout.finish_read(order.order_id), ReadStage.PENDING)
        self.assertEqual(self.terminal.select('B'), Position('B', 1))
        reply = self.device.deliver_reply(order.order_id, 6)
        self.assertEqual(reply.position, Position('A', 2))
        receipt = self.readout.finish_read(order.order_id)
        self.assertEqual(self.device.position(), Position('B', 1))
        self.assertEqual((receipt.shift_id, receipt.order_id, receipt.position, receipt.raw),
                         (SAMPLE_SHIFT, order.order_id, Position('A', 2), 6))
        self.assertEqual(receipt.measurement.quantity, Quantity(Fraction(3), 'kPa'))
        self.assertIs(receipt.measurement.verdict, Verdict.PASS)
        self.assertEqual(receipt.measurement.calibration.scale, Fraction(1, 3))
        self.assertEqual(read_receipt(self.store, receipt.receipt_id), receipt)

    def test_wait_for_reply(self):
        self._select_a2()
        order = self.readout.begin_read()
        for _ in range(2):
            self.assertIs(self.readout.finish_read(order.order_id), ReadStage.PENDING)
            self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])
        self.device.deliver_reply(order.order_id, 6)
        self.assertEqual(self.readout.finish_read(order.order_id).order_id, order.order_id)

    def test_begin_guards(self):
        with self.assertRaises(SelectionError):
            self.readout.begin_read()
        self.assertIsNone(self.device.position())
        self._select_a2()
        order = self.readout.begin_read()
        with self.assertRaises(ReadOrderError):
            self.readout.begin_read()
        with self.assertRaises(RuntimeError):
            Readout(self.terminal, self.store)
        with self.assertRaises(ReadOrderError):
            self.readout.finish_read('unknown')
        self.assertIs(self.readout.finish_read(order.order_id), ReadStage.PENDING)
        self.assertEqual(self.device.position(), Position('A', 2))
        self.assertIs(self.readout.cancel_read(order.order_id), ReadStage.CANCELLED)
        self.device.stop()
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'device is stopped'):
                self.readout.begin_read()
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])

    def test_outside_limit(self):
        self._select_a2()
        receipt = self._sample(7)
        self.assertEqual(receipt.raw, 7)
        self.assertEqual(receipt.position, Position('A', 2))
        self.assertEqual(receipt.measurement.quantity, Quantity(Fraction(10, 3), 'kPa'))
        self.assertIs(receipt.measurement.verdict, Verdict.OUTSIDE)
        self.assertEqual(read_receipt(self.store, receipt.receipt_id), receipt)
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [receipt])

    def test_closed_interval(self):
        self._select_a2()
        for raw, expected in ((0, 1), (6, 3)):
            with self.subTest(raw=raw):
                receipt = self._sample(raw)
                self.assertEqual(receipt.measurement.quantity, Quantity(Fraction(expected), 'kPa'))
                self.assertIs(receipt.measurement.verdict, Verdict.PASS)
        below = self._sample('-1/3')
        self.assertEqual(below.measurement.quantity, Quantity(Fraction(8, 9), 'kPa'))
        self.assertIs(below.measurement.verdict, Verdict.OUTSIDE)
        with self.assertRaisesRegex(ValueError, 'cannot compare'):
            judge_limits(Quantity(Fraction(3), 'mV'), self.config.profile_at(Position('A', 2)))

    def test_single_point_interval(self):
        catalog = make_lane_catalog({'S': ['point']})
        calibration = make_calibration_profile(unit='mV', scale='-1/2', offset=3, lower=2, upper=2)
        config = open_shift(self.store, 'POINT', catalog, {Position('S', 'point'): calibration})
        device = SimulatedDevice(catalog)
        terminal = ManualTerminal(device, config)
        readout = Readout(terminal, self.store)
        terminal.select('S')
        for raw, value, verdict in (('2.0', Fraction(2), Verdict.PASS),
                                    ('5/2', Fraction(7, 4), Verdict.OUTSIDE)):
            with self.subTest(raw=raw):
                order = readout.begin_read()
                device.deliver_reply(order.order_id, raw)
                receipt = readout.finish_read(order.order_id)
                self.assertEqual(receipt.measurement.quantity, Quantity(value, 'mV'))
                self.assertIs(receipt.measurement.verdict, verdict)

    def test_invalid_value_retry(self):
        self._select_a2()
        order = self.readout.begin_read()
        self.assertIs(self.readout.finish_read(order.order_id), ReadStage.PENDING)
        for value in (None, True, False, '', '1/0', 'NaN', 'inf', '6,00',
                      6.0, float('inf'), float('nan'), Fraction(6)):
            with self.subTest(value=value):
                self.device.deliver_reply(order.order_id, value)
                with self.assertRaises(ReplyError):
                    self.readout.finish_read(order.order_id)
                self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])
                self.assertIs(self.readout.finish_read(order.order_id), ReadStage.PENDING)
        self.device.deliver_reply(order.order_id, '  +6.00 ')
        receipt = self.readout.finish_read(order.order_id)
        self.assertEqual(receipt.raw, '  +6.00 ')
        self.assertEqual(receipt.measurement.quantity, Quantity(Fraction(3), 'kPa'))
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [receipt])

    def test_cancel_late_reply(self):
        self._select_a2()
        old = self.readout.begin_read()
        self.assertIs(self.readout.cancel_read(old.order_id), ReadStage.CANCELLED)
        late = self.device.deliver_reply(old.order_id, 6)
        with self.assertRaises(ReadOrderError):
            self.readout.finish_read(old.order_id, late)
        with self.assertRaises(ReadOrderError):
            self.readout.finish_read(old.order_id)
        with self.assertRaises(ReadOrderError):
            self.readout.cancel_read(old.order_id)
        with self.assertRaises(ReadOrderError):
            self.readout.cancel_read('unknown')
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])
        next_order = self.readout.begin_read()
        self.assertNotEqual(next_order.order_id, old.order_id)
        self.assertIs(self.readout.finish_read(next_order.order_id), ReadStage.PENDING)
        self.device.deliver_reply(next_order.order_id, 7)
        receipt = self.readout.finish_read(next_order.order_id)
        self.assertEqual((receipt.order_id, receipt.raw), (next_order.order_id, 7))
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [receipt])

    def test_reply_identity(self):
        self._select_a2()
        old = self.readout.begin_read()
        self.readout.cancel_read(old.order_id)
        old_reply = self.device.deliver_reply(old.order_id, 6)
        current = self.readout.begin_read()
        self.assertEqual(old_reply.position, current.position)
        wrong_position = DeviceReply(current.order_id, Position('B', 1), 6)
        for invalid in (old_reply, wrong_position):
            with self.assertRaises(ReplyError):
                self.readout.finish_read(current.order_id, invalid)
            self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])
            self.assertIs(self.readout.finish_read(current.order_id), ReadStage.PENDING)
        self.device.deliver_reply(current.order_id, 7)
        receipt = self.readout.finish_read(current.order_id)
        self.assertEqual((receipt.order_id, receipt.position, receipt.raw),
                         (current.order_id, Position('A', 2), 7))
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [receipt])

    def test_retry_archive_commit(self):
        self._select_a2()
        historical = self._sample(7)
        with closing(sqlite3.connect(self.database)) as setup:
            setup.executescript('''
                CREATE TABLE test_approved_commit (key INTEGER PRIMARY KEY);
                CREATE TABLE test_commit_guard (
                    approved_key INTEGER NOT NULL REFERENCES test_approved_commit(key)
                    DEFERRABLE INITIALLY DEFERRED
                );
                CREATE TRIGGER test_check_receipt_commit AFTER INSERT ON receipts
                BEGIN
                    INSERT INTO test_commit_guard VALUES (1);
                END;
            ''')
        with ArchiveStore.open(self.database) as observer:
            order = self.readout.begin_read()
            reply = self.device.deliver_reply(order.order_id, '6.00')
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
                self.readout.finish_read(order.order_id)
            self.assertEqual(export_shift(observer, SAMPLE_SHIFT), [historical])
            with self.assertRaises(LookupError):
                read_receipt(observer, historical.receipt_id + 1)
            with self.assertRaises(ReadOrderError):
                self.readout.begin_read()
            with self.assertRaises(ReplyError):
                self.readout.finish_read(order.order_id, replace(reply, raw=6))
            with closing(sqlite3.connect(self.database)) as setup:
                setup.execute('DROP TRIGGER test_check_receipt_commit')
                setup.commit()
            self.terminal.select('B')
            receipt = self.readout.finish_read(order.order_id)
            self.assertEqual((receipt.order_id, receipt.position, receipt.raw),
                             (order.order_id, Position('A', 2), '6.00'))
            self.assertEqual(receipt.measurement.quantity, Quantity(Fraction(3), 'kPa'))
            self.assertIs(receipt.measurement.verdict, Verdict.PASS)
            self.assertEqual(read_receipt(observer, receipt.receipt_id), receipt)
            self.assertEqual(export_shift(observer, SAMPLE_SHIFT), [historical, receipt])
            self.assertEqual(self.readout.finish_read(order.order_id, reply), receipt)
            self.assertEqual(export_shift(observer, SAMPLE_SHIFT), [historical, receipt])
            self.assertEqual(self.readout.begin_read().position, Position('B', 1))

    def test_signed_replay(self):
        self._select_a2()
        order = self.readout.begin_read()
        reply = self.device.deliver_reply(order.order_id, '6/1')
        first = self.readout.finish_read(order.order_id)
        draft = ReceiptDraft(first.shift_id, first.order_id, first.position, first.raw, first.measurement)
        with ArchiveStore.open(self.database) as observer:
            self.assertEqual(observer.commit_receipt(draft), first)
            with self.assertRaises(ValueError):
                observer.commit_receipt(replace(draft, raw='6.0'))
        self.device.stop()
        self.assertEqual(self.readout.finish_read(order.order_id), first)
        self.assertEqual(self.readout.finish_read(order.order_id, reply), first)
        for invalid in (replace(reply, raw=6), replace(reply, position=Position('B', 1))):
            with self.assertRaises(ReplyError):
                self.readout.finish_read(order.order_id, invalid)
        with self.assertRaises(ReadOrderError):
            self.readout.cancel_read(order.order_id)
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [first])
