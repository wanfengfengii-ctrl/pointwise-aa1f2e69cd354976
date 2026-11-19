import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from archive.export import export_shift
from archive.readback import read_receipt
from archive.receipts import Receipt
from archive.store import ArchiveStore
from capture.orders import ReadOrderError, ReadStage
from capture.readout import Readout
from capture.replies import ReplyError
from samples.auto_port import RecordedDevice
from shift.catalog import Position
from shift.manifest import open_shift_from_manifest
from terminal.device import DeviceReply
from terminal.display import describe_selection
from terminal.selection import ManualTerminal, SelectionError
from terminal.simulator import SimulatedDevice
from tests import SAMPLE_PATH, SAMPLE_SHIFT

PROFILE = 'B'
LANE = 1


def run_auto(test, readout, job_id, limit=60):
    for _ in range(limit):
        stage = readout.step_auto(job_id)
        if not (isinstance(stage, ReadStage) and stage is ReadStage.PENDING):
            return stage
    test.fail('auto job did not settle in a finite number of steps')


class AutoReadTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.database = Path(directory) / 'auto.sqlite'
        self.store = self.enterContext(ArchiveStore.open(self.database))
        self.config = open_shift_from_manifest(self.store, SAMPLE_PATH)
        self.device = RecordedDevice(self.config.catalog)
        self.terminal = ManualTerminal(self.device, self.config)
        self.readout = Readout(self.terminal, self.store)

    def _manual_a2(self):
        self.terminal.select('A')
        self.terminal.select()

    def _start(self, profile=PROFILE, lane=LANE):
        return self.readout.start_auto(SAMPLE_SHIFT, profile, lane)

    def _advance_to_latch(self, job_id):
        for _ in range(20):
            self.readout.step_auto(job_id)
            for latch in self.device.latches:
                if latch.order_id == job_id:
                    return latch
        self.fail('auto job never latched a read')

    def test_start_guards(self):
        self._manual_a2()
        for args in ((SAMPLE_SHIFT, 'Z', 1), (SAMPLE_SHIFT, 'B', 9), ('CW-OTHER', 'B', 1)):
            with self.subTest(args=args), self.assertRaises(ReadOrderError):
                self.readout.start_auto(*args)
        self.assertEqual(self.terminal.manual_position(), Position('A', 2))
        self.assertEqual(self.device.physical_position(), Position('A', 2))
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])

    def test_start_requires_manual_position(self):
        with self.assertRaises(ReadOrderError):
            self._start()
        self.assertIsNone(self.device.physical_position())

    def test_mutual_exclusion(self):
        self._manual_a2()
        order = self.readout.begin_read()
        with self.assertRaises(ReadOrderError):
            self._start()
        self.readout.cancel_read(order.order_id)
        job = self._start()
        with self.assertRaises(ReadOrderError):
            self.readout.begin_read()
        with self.assertRaises(ReadOrderError):
            self._start()
        with self.assertRaises(ReadOrderError):
            self.readout.step_auto('unknown-job')
        with self.assertRaises(ReadOrderError):
            self.readout.cancel_auto('unknown-job')
        self.assertEqual(self.terminal.manual_position(), Position('A', 2))
        self.assertEqual(self.device.physical_position(), Position('A', 2))
        self.assertEqual(len(self.device.latches), 1)
        self.assertEqual(self.device.latches[0].position, Position('A', 2))
        # The rejected re-entry left the accepted job in charge.
        self._advance_to_latch(job)
        self.device.deliver_reply(job, 5)
        receipt = run_auto(self, self.readout, job)
        self.assertEqual((receipt.order_id, receipt.position), (job, Position('B', 1)))
        self.assertEqual(self.device.physical_position(), Position('A', 2))

    def test_interleaved_move_before_issue(self):
        self._manual_a2()
        job = self._start()
        self.assertIs(self.readout.step_auto(job), ReadStage.PENDING)
        self.assertEqual(self.device.physical_position(), Position('B', 1))
        self.assertEqual(self.device.latches, [])
        self.assertEqual(self.terminal.select(), Position('A', 3))
        self.assertEqual(describe_selection(self.terminal).position, Position('A', 3))
        self.assertEqual(self.device.physical_position(), Position('B', 1))
        for _ in range(10):
            if self.device.latches:
                break
            self.assertIs(self.readout.step_auto(job), ReadStage.PENDING)
        self.assertEqual(self.device.latches[0].position, Position('B', 1))
        self.device.deliver_reply(job, 5)
        receipt = run_auto(self, self.readout, job)
        self.assertEqual((receipt.position, receipt.raw), (Position('B', 1), 5))
        self.assertEqual(receipt.measurement.quantity.value, 10)
        self.assertEqual(self.device.physical_position(), Position('A', 3))
        self.assertEqual(self.readout.begin_read().position, Position('A', 3))

    def test_interleaved_move_and_delayed_reply(self):
        self._manual_a2()
        job = self._start()
        latched = self._advance_to_latch(job)
        self.assertEqual(latched.position, Position('B', 1))
        self.assertEqual(self.terminal.select(), Position('A', 3))
        self.assertEqual(self.terminal.current_position(), Position('A', 3))
        self.assertEqual(describe_selection(self.terminal).position, Position('A', 3))
        self.assertEqual(self.device.physical_position(), Position('B', 1))
        self.device.deliver_reply(job, 5)
        receipt = run_auto(self, self.readout, job)
        self.assertIsInstance(receipt, Receipt)
        self.assertEqual((receipt.position, receipt.raw), (Position('B', 1), 5))
        self.assertEqual(receipt.measurement.quantity.value, 10)
        self.assertEqual(receipt.measurement.quantity.unit, 'mV')
        self.assertEqual(receipt.measurement.verdict.value, 'pass')
        self.assertEqual(self.device.physical_position(), Position('A', 3))
        self.assertEqual(self.terminal.manual_position(), Position('A', 3))
        self.assertEqual(self.readout.begin_read().position, Position('A', 3))

    def test_hold_through_signing(self):
        self._manual_a2()
        job = self._start()
        self._advance_to_latch(job)
        self.assertEqual(self.terminal.select('A'), Position('A', 1))
        self.assertEqual(self.terminal.select(), Position('A', 2))
        self.assertEqual(self.device.physical_position(), Position('B', 1))
        self.device.deliver_reply(job, 5)
        receipt = run_auto(self, self.readout, job)
        self.assertEqual((receipt.position, receipt.raw), (Position('B', 1), 5))
        self.assertEqual(self.device.physical_position(), Position('A', 2))
        self.assertEqual(self.terminal.manual_position(), Position('A', 2))

    def test_manual_select_does_not_touch_held_selector(self):
        self._manual_a2()
        job = self._start()
        self._advance_to_latch(job)
        before = len(self.device.calls)
        self.assertEqual(self.terminal.select('A'), Position('A', 1))
        self.assertEqual(self.terminal.select(), Position('A', 2))
        self.assertEqual(self.terminal.select(), Position('A', 3))
        self.assertEqual(self.terminal.select('B'), Position('B', 1))
        self.assertEqual(len(self.device.calls), before)
        self.assertEqual(self.device.physical_position(), Position('B', 1))
        self.device.deliver_reply(job, 5)
        receipt = run_auto(self, self.readout, job)
        self.assertEqual((receipt.position, receipt.raw), (Position('B', 1), 5))
        self.assertEqual(self.device.physical_position(), Position('B', 1))
        self.assertEqual(self.terminal.manual_position(), Position('B', 1))

    def test_handback_follows_latest_manual_position(self):
        self._manual_a2()
        job = self._start('B', 2)
        self._advance_to_latch(job)
        self.device.deliver_reply(job, 5)
        # Issue happens first; then the operator retargets the work position
        # before the selector has been driven back.
        issued = None
        for _ in range(10):
            stage = self.readout.step_auto(job)
            if export_shift(self.store, SAMPLE_SHIFT):
                issued = export_shift(self.store, SAMPLE_SHIFT)[0]
                break
            self.assertIs(stage, ReadStage.PENDING)
        self.assertIsNotNone(issued)
        self.assertEqual(issued.position, Position('B', 2))
        self.assertEqual(self.terminal.manual_position(), Position('A', 2))
        self.assertEqual(self.terminal.select('A'), Position('A', 1))
        receipt = run_auto(self, self.readout, job)
        self.assertEqual(receipt, issued)
        self.assertEqual(self.device.physical_position(), Position('A', 1))
        self.assertEqual(self.readout.begin_read().position, Position('A', 1))

    def test_evidence_survives_later_manual_moves(self):
        self._manual_a2()
        job = self._start()
        self._advance_to_latch(job)
        self.terminal.select('A')
        self.terminal.select()
        self.terminal.select()
        self.assertEqual(self.terminal.manual_position(), Position('A', 3))
        self.device.deliver_reply(job, 5)
        receipt = run_auto(self, self.readout, job)
        self.assertEqual(receipt.position, Position('B', 1))
        self.assertEqual(receipt.measurement.quantity.value, 10)
        self.assertEqual(self.device.physical_position(), Position('A', 3))

    def test_one_device_call_per_step(self):
        self._manual_a2()
        job = self._start()
        stage = ReadStage.PENDING
        delivered = False
        for _ in range(60):
            before = len(self.device.calls)
            stage = self.readout.step_auto(job)
            self.assertLessEqual(len(self.device.calls) - before, 1)
            if self.device.latches and not delivered:
                self.device.deliver_reply(job, 5)
                delivered = True
            if not (isinstance(stage, ReadStage) and stage is ReadStage.PENDING):
                break
        self.assertIsInstance(stage, Receipt)
        self.assertEqual(stage.position, Position('B', 1))

    def test_cancel_while_selected_then_late_reply(self):
        self._manual_a2()
        job = self._start()
        self._advance_to_latch(job)
        self.assertIs(self.readout.cancel_auto(job), ReadStage.PENDING)
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])
        self.device.deliver_reply(job, 5)
        self.assertIs(run_auto(self, self.readout, job), ReadStage.CANCELLED)
        self.assertEqual(self.device.physical_position(), Position('A', 2))
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])
        with self.assertRaises(ReadOrderError):
            self.readout.step_auto(job)
        with self.assertRaises(ReadOrderError):
            self.readout.cancel_auto(job)
        self.assertEqual(self.readout.begin_read().position, Position('A', 2))

    def test_cancel_before_moving(self):
        self._manual_a2()
        job = self._start()
        self.assertIs(self.readout.cancel_auto(job), ReadStage.CANCELLED)
        self.assertEqual(self.device.physical_position(), Position('A', 2))
        self.assertEqual(self.device.latches, [])
        with self.assertRaises(ReadOrderError):
            self.readout.cancel_auto(job)
        self.assertEqual(self.readout.begin_read().position, Position('A', 2))

    def test_cancel_after_signing_refused(self):
        self._manual_a2()
        job = self._start()
        self._advance_to_latch(job)
        self.device.deliver_reply(job, 5)
        receipt = run_auto(self, self.readout, job)
        with self.assertRaises(ReadOrderError):
            self.readout.cancel_auto(job)
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [receipt])

    def test_second_lane_target(self):
        self._manual_a2()
        job = self._start('B', 2)
        moves = []
        for _ in range(10):
            before = len(self.device.calls)
            self.readout.step_auto(job)
            moves.extend(self.device.calls[before:])
            if self.device.latches:
                break
        self.assertEqual(moves[:2], [('select_profile', ('B',)), ('select_lane', (2,))])
        self.assertEqual(self.device.latches[0].position, Position('B', 2))
        self.device.deliver_reply(job, 5)
        receipt = run_auto(self, self.readout, job)
        self.assertEqual((receipt.position, receipt.raw), (Position('B', 2), 5))
        self.assertEqual(self.device.physical_position(), Position('A', 2))

    def test_cancel_second_lane_after_latch_late_reply(self):
        self._manual_a2()
        job = self._start('B', 2)
        for _ in range(10):
            self.readout.step_auto(job)
            if self.device.latches:
                break
        self.assertEqual(self.device.latches[0].position, Position('B', 2))
        self.assertIs(self.readout.cancel_auto(job), ReadStage.PENDING)
        late = self.device.deliver_reply(job, 5)
        self.assertEqual(late.position, Position('B', 2))
        self.assertIs(run_auto(self, self.readout, job), ReadStage.CANCELLED)
        self.assertEqual(self.device.physical_position(), Position('A', 2))
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])
        self.assertEqual(self.readout.begin_read().position, Position('A', 2))

    def test_issued_refuses_cancel_until_handback(self):
        self._manual_a2()
        job = self._start()
        self._advance_to_latch(job)
        self.device.deliver_reply(job, 5)
        stage = ReadStage.PENDING
        for _ in range(10):
            stage = self.readout.step_auto(job)
            if export_shift(self.store, SAMPLE_SHIFT):
                break
            self.assertIs(stage, ReadStage.PENDING)
        self.assertEqual(len(export_shift(self.store, SAMPLE_SHIFT)), 1)
        with self.assertRaises(ReadOrderError):
            self.readout.cancel_auto(job)
        receipt = run_auto(self, self.readout, job)
        self.assertIsInstance(receipt, Receipt)
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [receipt])

    def test_manual_last_lane_overflow_preserves_state(self):
        self._manual_a2()
        self.terminal.select()
        self.assertEqual(self.terminal.manual_position(), Position('A', 3))
        job = self._start()
        self._advance_to_latch(job)
        with self.assertRaises(SelectionError):
            self.terminal.select()
        self.assertEqual(self.terminal.manual_position(), Position('A', 3))
        self.assertEqual(self.device.physical_position(), Position('B', 1))
        self.device.deliver_reply(job, 5)
        receipt = run_auto(self, self.readout, job)
        self.assertEqual((receipt.position, receipt.raw), (Position('B', 1), 5))
        self.assertEqual(self.device.physical_position(), Position('A', 3))

    def test_manual_entry_does_not_take_over_auto_order(self):
        self._manual_a2()
        job = self._start()
        self._advance_to_latch(job)
        for entry in (self.readout.finish_read, self.readout.cancel_read):
            with self.subTest(entry=entry.__name__), self.assertRaises(ReadOrderError):
                entry(job)
        self.assertEqual(self.device.physical_position(), Position('B', 1))
        self.device.deliver_reply(job, 5)
        receipt = run_auto(self, self.readout, job)
        self.assertEqual((receipt.position, receipt.raw), (Position('B', 1), 5))

    def test_retry_archive_commit_after_failure(self):
        self._manual_a2()
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
        job = self._start()
        self._advance_to_latch(job)
        self.device.deliver_reply(job, '6.00')
        with self.assertRaises(sqlite3.IntegrityError):
            for _ in range(20):
                self.readout.step_auto(job)
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])
        with closing(sqlite3.connect(self.database)) as setup:
            setup.execute('DROP TRIGGER test_check_receipt_commit')
            setup.commit()
        receipt = run_auto(self, self.readout, job)
        self.assertEqual((receipt.order_id, receipt.position, receipt.raw),
                         (job, Position('B', 1), '6.00'))
        self.assertEqual(receipt.measurement.quantity.value, 12)
        self.assertIs(receipt.measurement.verdict.value, 'outside_limit')
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [receipt])

    def test_source_off_readback(self):
        self._manual_a2()
        job = self._start()
        self._advance_to_latch(job)
        self.device.deliver_reply(job, 5)
        receipt = run_auto(self, self.readout, job)
        self.device.stop()
        self.store.close()
        with ArchiveStore.open(self.database) as reopened:
            self.assertEqual(export_shift(reopened, SAMPLE_SHIFT), [receipt])
            self.assertEqual(read_receipt(reopened, receipt.receipt_id), receipt)
            self.assertEqual(receipt.measurement.quantity.value, 10)
            self.assertEqual(receipt.measurement.quantity.unit, 'mV')
            self.assertEqual(receipt.measurement.verdict.value, 'pass')


class WrongLatchDevice:
    """A port whose first latch reports a lane different from the selector."""

    def __init__(self, inner, wrong):
        self._inner = inner
        self._wrong = wrong

    def position(self):
        return self._inner.position()

    def select_profile(self, profile):
        return self._inner.select_profile(profile)

    def select_lane(self, lane):
        return self._inner.select_lane(lane)

    def begin_read(self, order_id):
        from terminal.device import LatchedRead
        self._inner.begin_read(order_id)
        if self._wrong is not None:
            wrong, self._wrong = self._wrong, None
            return LatchedRead(order_id, wrong)
        return LatchedRead(order_id, self._inner.position())

    def poll_reply(self, order_id):
        return self._inner.poll_reply(order_id)


class ReplyShiftDevice:
    """A port that answers once with a reply carrying a different position."""

    def __init__(self, inner, wrong, raw=5):
        self._inner = inner
        self._wrong = wrong
        self._raw = raw
        self._sent = False

    def position(self):
        return self._inner.position()

    def select_profile(self, profile):
        return self._inner.select_profile(profile)

    def select_lane(self, lane):
        return self._inner.select_lane(lane)

    def begin_read(self, order_id):
        return self._inner.begin_read(order_id)

    def poll_reply(self, order_id):
        if not self._sent:
            self._sent = True
            return DeviceReply(order_id, self._wrong, self._raw)
        return self._inner.poll_reply(order_id)


class AutoEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.database = Path(directory) / 'auto-evidence.sqlite'
        self.store = self.enterContext(ArchiveStore.open(self.database))
        self.config = open_shift_from_manifest(self.store, SAMPLE_PATH)

    def _bench(self, device):
        terminal = ManualTerminal(device, self.config)
        readout = Readout(terminal, self.store)
        terminal.select('A')
        terminal.select()
        return terminal, readout

    def test_wrong_latch_is_not_signed(self):
        inner = SimulatedDevice(self.config.catalog)
        device = WrongLatchDevice(inner, Position('B', 2))
        terminal, readout = self._bench(device)
        job = readout.start_auto(SAMPLE_SHIFT, 'B', 1)
        for _ in range(10):
            self.assertIs(readout.step_auto(job), ReadStage.PENDING)
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])
        self.assertIs(readout.cancel_auto(job), ReadStage.PENDING)
        self.assertIs(run_auto(self, readout, job), ReadStage.CANCELLED)
        self.assertEqual(inner.position(), Position('A', 2))
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])
        self.assertEqual(terminal.manual_position(), Position('A', 2))
        self.assertEqual(readout.begin_read().position, Position('A', 2))

    def test_invalid_raw_keeps_waiting_on_same_order(self):
        device = RecordedDevice(self.config.catalog)
        terminal, readout = self._bench(device)
        job = readout.start_auto(SAMPLE_SHIFT, 'B', 1)
        for _ in range(20):
            self.assertIs(readout.step_auto(job), ReadStage.PENDING)
            if device.latches:
                break
        self.assertEqual(device.latches[0].position, Position('B', 1))
        device.deliver_reply(job, None)
        with self.assertRaises(ValueError):
            readout.step_auto(job)
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])
        device.deliver_reply(job, 5)
        receipt = run_auto(self, readout, job)
        self.assertEqual((receipt.order_id, receipt.raw), (job, 5))
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [receipt])

    def test_misplaced_reply_is_rejected_and_can_retry(self):
        inner = SimulatedDevice(self.config.catalog)
        device = ReplyShiftDevice(inner, Position('A', 3))
        terminal, readout = self._bench(device)
        job = readout.start_auto(SAMPLE_SHIFT, 'B', 1)
        with self.assertRaises(ReplyError):
            for _ in range(20):
                readout.step_auto(job)
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])
        inner.deliver_reply(job, 5)
        receipt = run_auto(self, readout, job)
        self.assertEqual((receipt.order_id, receipt.position, receipt.raw),
                         (job, Position('B', 1), 5))
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [receipt])

    def test_invalid_targets_keep_state(self):
        device = SimulatedDevice(self.config.catalog)
        terminal, readout = self._bench(device)
        for profile, lane in (('B', 7), ('Q', 1), ('B', True)):
            with self.subTest(profile=profile, lane=lane), self.assertRaises(ReadOrderError):
                readout.start_auto(SAMPLE_SHIFT, profile, lane)
            self.assertEqual(device.position(), Position('A', 2))
            self.assertEqual(terminal.manual_position(), Position('A', 2))
        with self.assertRaises(ReadOrderError):
            readout.start_auto('  ', 'B', 1)
        self.assertEqual(export_shift(self.store, SAMPLE_SHIFT), [])


if __name__ == '__main__':
    unittest.main()
