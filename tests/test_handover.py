import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from archive.export import export_shift
from archive.readback import read_receipt
from archive.store import ArchiveStore
from capture.readout import Readout
from quality.limits import Verdict
from quality.units import Quantity
from shift.catalog import Position
from shift.opening import open_shift
from terminal.selection import ManualTerminal
from tests import SAMPLE_SHIFT, sample_bench


class HandoverTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.database = Path(directory) / 'handover.sqlite'
        self.store = self.enterContext(ArchiveStore.open(self.database))
        self.config, self.device, self.terminal, self.readout = sample_bench(self.store)

    def _sample(self, raw, readout=None):
        readout = self.readout if readout is None else readout
        order = readout.begin_read()
        self.device.deliver_reply(order.order_id, raw)
        return readout.finish_read(order.order_id)

    def _select_a2(self):
        self.terminal.select('A')
        self.terminal.select()

    def _register_peer(self, shift_id):
        return open_shift(self.store, shift_id, self.config.catalog, dict(self.config.calibrations))

    def _stop_and_close(self):
        self.device.stop()
        self.store.close()
        with self.assertRaisesRegex(RuntimeError, 'device is stopped'):
            self.device.position()

    def test_source_off_readback(self):
        self._select_a2()
        accepted = self._sample(6)
        outside = self._sample(7)
        self._stop_and_close()
        with ArchiveStore.open(self.database) as reopened:
            self.assertEqual(read_receipt(reopened, accepted.receipt_id), accepted)
            self.assertEqual(read_receipt(reopened, outside.receipt_id), outside)
            history = export_shift(reopened, SAMPLE_SHIFT)
            self.assertEqual(history, [accepted, outside])
            self.assertEqual([(r.position, r.raw, r.measurement.quantity, r.measurement.verdict)
                              for r in history], [
                (Position('A', 2), 6, Quantity(Fraction(3), 'kPa'), Verdict.PASS),
                (Position('A', 2), 7, Quantity(Fraction(10, 3), 'kPa'), Verdict.OUTSIDE),
            ])

    def test_raw_evidence_roundtrip(self):
        self._select_a2()
        text_receipt = self._sample('6.00')
        integer_receipt = self._sample(7)
        self._stop_and_close()
        with ArchiveStore.open(self.database) as reopened:
            history = export_shift(reopened, SAMPLE_SHIFT)
            self.assertEqual(history, [text_receipt, integer_receipt])
            for receipt in history:
                self.assertEqual(read_receipt(reopened, receipt.receipt_id), receipt)
                self.assertEqual(receipt.position, Position('A', 2))
                profile = receipt.measurement.calibration
                self.assertEqual((profile.unit, profile.scale, profile.offset, profile.lower, profile.upper),
                                 ('kPa', Fraction(1, 3), Fraction(1), Fraction(1), Fraction(3)))
            self.assertIs(type(history[0].raw), str)
            self.assertEqual(history[0].raw, '6.00')
            self.assertEqual(history[0].measurement.quantity, Quantity(Fraction(3), 'kPa'))
            self.assertIs(history[0].measurement.verdict, Verdict.PASS)
            self.assertIs(type(history[1].raw), int)
            self.assertEqual(history[1].raw, 7)
            self.assertEqual(history[1].measurement.quantity, Quantity(Fraction(10, 3), 'kPa'))
            self.assertIs(history[1].measurement.verdict, Verdict.OUTSIDE)

    def test_issue_sequence(self):
        peer = self._register_peer('CW-PEER')
        self.terminal.select('B')
        first = self._sample(5)
        peer_terminal = ManualTerminal(self.device, peer)
        peer_readout = Readout(peer_terminal, self.store)
        peer_terminal.select('A')
        foreign = self._sample(4, peer_readout)
        self._select_a2()
        second = self._sample(7)
        self.assertLess(first.receipt_id, foreign.receipt_id)
        self.assertLess(foreign.receipt_id, second.receipt_id)
        self._stop_and_close()
        with ArchiveStore.open(self.database) as reopened:
            history = export_shift(reopened, SAMPLE_SHIFT)
            self.assertEqual(history, [first, second])
            self.assertEqual(export_shift(reopened, peer.shift_id), [foreign])
            self.assertEqual([(r.position, r.raw, r.measurement.quantity, r.measurement.verdict)
                              for r in history], [
                (Position('B', 1), 5, Quantity(Fraction(10), 'mV'), Verdict.PASS),
                (Position('A', 2), 7, Quantity(Fraction(10, 3), 'kPa'), Verdict.OUTSIDE),
            ])
            for receipt in history:
                self.assertEqual(read_receipt(reopened, receipt.receipt_id), receipt)

    def test_empty_and_unknown(self):
        self._register_peer('CW-EMPTY')
        self._select_a2()
        existing = self._sample(6)
        self._stop_and_close()
        with ArchiveStore.open(self.database) as reopened:
            self.assertEqual(export_shift(reopened, 'CW-EMPTY'), [])
            with self.assertRaisesRegex(LookupError, 'unknown shift'):
                export_shift(reopened, 'CW-UNKNOWN')
            with self.assertRaisesRegex(LookupError, 'unknown receipt'):
                read_receipt(reopened, existing.receipt_id + 1)
            with self.assertRaises(LookupError):
                read_receipt(reopened, True)
            self.assertEqual(read_receipt(reopened, existing.receipt_id), existing)

    def test_reinitialize_keeps_receipts(self):
        self.terminal.select('B')
        first = self._sample('5.0')
        self._select_a2()
        second = self._sample(7)
        self._stop_and_close()
        with ArchiveStore.open(self.database) as reopened:
            self.assertEqual(export_shift(reopened, SAMPLE_SHIFT), [first, second])
        with ArchiveStore.open(self.database) as reinitialized:
            self.assertEqual(export_shift(reinitialized, SAMPLE_SHIFT), [first, second])
            self.assertEqual(read_receipt(reinitialized, first.receipt_id), first)
            self.assertEqual(read_receipt(reinitialized, second.receipt_id), second)
            self.assertEqual(first.raw, '5.0')
            self.assertIs(second.measurement.verdict, Verdict.OUTSIDE)
