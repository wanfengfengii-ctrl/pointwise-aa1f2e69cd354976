import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.export import export_shift
from archive.readback import read_receipt
from archive.store import ArchiveStore
from capture.orders import ReadStage
from capture.readout import Readout
from samples.auto_port import RecordedDevice
from shift.catalog import Position
from shift.manifest import open_shift_from_manifest
from terminal.display import describe_selection
from terminal.selection import ManualTerminal


def summarize(receipt):
    measurement = receipt.measurement
    return {
        'position': [receipt.position.profile, receipt.position.lane],
        'raw': receipt.raw,
        'value': str(measurement.quantity.value),
        'unit': measurement.quantity.unit,
        'verdict': measurement.verdict.value,
    }


def run_until(readout, device, job_id, predicate, limit=40):
    """Advance an auto job one device step at a time until ``predicate``."""
    for _ in range(limit):
        before = len(device.calls)
        stage = readout.step_auto(job_id)
        assert len(device.calls) - before <= 1, 'each step performs at most one port call'
        if predicate(stage):
            return stage
    raise AssertionError('auto job did not settle in the expected number of steps')


def settled(stage):
    return not (isinstance(stage, ReadStage) and stage is ReadStage.PENDING)


def main() -> None:
    manifest = Path(__file__).resolve().parent / 'samples' / 'day_shift.json'
    with TemporaryDirectory(prefix='chengwan-demo-') as directory:
        database = Path(directory) / 'calibration.sqlite'
        with ArchiveStore.open(database) as archive:
            config = open_shift_from_manifest(archive, manifest)
            device = RecordedDevice(config.catalog)
            terminal = ManualTerminal(device, config)
            readout = Readout(terminal, archive)
            assert terminal.select('A') == Position('A', 1)
            assert terminal.select() == Position('A', 2)
            first = readout.begin_read()
            assert first.position == Position('A', 2)
            assert readout.finish_read(first.order_id) is ReadStage.PENDING
            assert terminal.select('B') == Position('B', 1)
            delayed = device.deliver_reply(first.order_id, 6)
            accepted = readout.finish_read(first.order_id)
            selector = describe_selection(terminal)
            assert selector.position == Position('B', 1) and selector.lane_number == 1
            terminal.select('A')
            terminal.select()
            second = readout.begin_read()
            device.deliver_reply(second.order_id, 7)
            outside = readout.finish_read(second.order_id)

            # -- automatic collection while the operator keeps preparing a point --
            assert terminal.manual_position() == Position('A', 2)
            job = readout.start_auto('CW-DAY-01', 'B', 1)
            assert readout.step_auto(job) is ReadStage.PENDING
            assert device.physical_position() == Position('B', 1)
            interleaved = terminal.select()
            assert interleaved == Position('A', 3)
            assert describe_selection(terminal).position == Position('A', 3)
            assert device.physical_position() == Position('B', 1)
            while not any(latch.order_id == job for latch in device.latches):
                assert readout.step_auto(job) is ReadStage.PENDING
            auto_latched = next(latch for latch in device.latches if latch.order_id == job)
            assert auto_latched.position == Position('B', 1)
            late_auto = device.deliver_reply(job, 5)
            automatic = run_until(readout, device, job, settled)
            assert automatic.position == Position('B', 1)
            assert automatic.measurement.quantity.value == 10
            assert automatic.measurement.verdict.value == 'pass'
            assert terminal.manual_position() == Position('A', 3)
            assert device.physical_position() == Position('A', 3)

            # a real manual read after the auto job handed the selector back
            manual = readout.begin_read()
            assert manual.position == Position('A', 3)
            device.deliver_reply(manual.order_id, 6)
            after_handback = readout.finish_read(manual.order_id)
            assert after_handback.position == Position('A', 3)
            assert after_handback.measurement.quantity.value == 4
            assert after_handback.measurement.verdict.value == 'pass'

            # cancelling: a late reply must not create a new receipt
            before_cancel = export_shift(archive, config.shift_id)
            job2 = readout.start_auto('CW-DAY-01', 'B', 2)
            while not any(latch.order_id == job2 for latch in device.latches):
                assert readout.step_auto(job2) is ReadStage.PENDING
            cancelled_latch = next(latch for latch in device.latches if latch.order_id == job2)
            assert cancelled_latch.position == Position('B', 2)
            assert readout.cancel_auto(job2) is ReadStage.PENDING
            late_cancelled = device.deliver_reply(job2, 5)
            assert late_cancelled.position == Position('B', 2)
            assert run_until(readout, device, job2, settled) is ReadStage.CANCELLED
            assert export_shift(archive, config.shift_id) == before_cancel
            assert device.physical_position() == Position('A', 3)
            with_guard = readout.begin_read()
            assert with_guard.position == Position('A', 3)
            readout.cancel_read(with_guard.order_id)
            handback_physical = device.physical_position()
            assert handback_physical == Position('A', 3)

            device.stop()
        with ArchiveStore.open(database) as archive:
            fixed = read_receipt(archive, accepted.receipt_id)
            exported = export_shift(archive, config.shift_id)
            assert fixed == accepted and fixed.order_id == first.order_id
            assert read_receipt(archive, outside.receipt_id) == outside
            assert read_receipt(archive, automatic.receipt_id) == automatic
            assert read_receipt(archive, after_handback.receipt_id) == after_handback
            assert exported == [accepted, outside, automatic, after_handback]
            assert automatic.order_id == job and after_handback.order_id == manual.order_id
            expected = {'position': ['A', 2], 'raw': 6, 'value': '3', 'unit': 'kPa', 'verdict': 'pass'}
            outside_expected = {'position': ['A', 2], 'raw': 7, 'value': '10/3',
                                'unit': 'kPa', 'verdict': 'outside_limit'}
            auto_expected = {'position': ['B', 1], 'raw': 5, 'value': '10', 'unit': 'mV', 'verdict': 'pass'}
            handback_expected = {'position': ['A', 3], 'raw': 6, 'value': '4', 'unit': 'kPa', 'verdict': 'pass'}
            actual = summarize(accepted)
            downstream = summarize(fixed)
            assert actual == expected and downstream == expected
            assert [summarize(receipt) for receipt in exported] == [
                expected, outside_expected, auto_expected, handback_expected]
            profile = config.profile_at(delayed.position)
            print('POINTWISE_SCENARIO ' + json.dumps({
                'case_id': 'n4',
                'input': {'raw': delayed.raw, 'latched': [delayed.position.profile, delayed.position.lane],
                          'selector_at_reply': [selector.position.profile, selector.position.lane],
                          'scale': str(profile.scale), 'offset': str(profile.offset),
                          'limits': [str(profile.lower), str(profile.upper)], 'unit': profile.unit},
                'expected': expected, 'actual': actual,
                'downstream_expected': expected, 'downstream_actual': downstream,
                'code_paths': ['terminal/simulator.py', 'capture/readout.py', 'capture/intake.py',
                               'quality/conversion.py', 'quality/limits.py', 'archive/readback.py'],
                'oracle': 'A/2 locks before the move. Hand calculation 6 × 1/3 + 1 = 3 kPa is the included upper endpoint.',
            }, ensure_ascii=False))
            print('POINTWISE_SCENARIO ' + json.dumps({
                'case_id': 'auto-read',
                'input': {
                    'shift_id': config.shift_id, 'job_id': job,
                    'target': ['B', 1], 'manual_before': ['A', 2],
                    'interleaved_manual': [interleaved.profile, interleaved.lane],
                    'latched': [auto_latched.position.profile, auto_latched.position.lane],
                    'late_reply_raw': late_auto.raw,
                    'selector_after_handback': [handback_physical.profile,
                                                handback_physical.lane],
                    'cancelled_job': job2,
                    'cancelled_late_reply': [late_cancelled.position.profile,
                                             late_cancelled.position.lane, late_cancelled.raw],
                },
                'expected': auto_expected, 'actual': summarize(automatic),
                'manual_after_handback': handback_expected,
                'manual_after_handback_actual': summarize(after_handback),
                'issued_before_cancel': [summarize(receipt) for receipt in before_cancel],
                'issued_after_cancel_late_reply': [summarize(receipt) for receipt in exported[:3]],
                'code_paths': ['terminal/selection.py', 'capture/auto.py', 'capture/readout.py',
                               'capture/replies.py', 'capture/intake.py', 'archive/store.py'],
                'oracle': 'The latch keeps B/1 though the operator moved to A/3; the delayed raw 5 '
                          'signs 10 mV at B/1. The selector hands back to A/3 and a fresh manual '
                          'read actually latches A/3. Cancelling the B/2 job after its latch lets '
                          'the late reply fall away without a receipt.',
            }, ensure_ascii=False))
            print('POINTWISE_SCENARIO ' + json.dumps({
                'case_id': 'n8',
                'input': {'shift_id': config.shift_id, 'raw_in_issue_order':
                          [accepted.raw, outside.raw, automatic.raw, after_handback.raw],
                          'device_stopped': True},
                'expected': [expected, outside_expected, auto_expected, handback_expected],
                'actual': [summarize(accepted), summarize(outside),
                           summarize(automatic), summarize(after_handback)],
                'downstream_expected': [expected, outside_expected, auto_expected, handback_expected],
                'downstream_actual': [summarize(receipt) for receipt in exported],
                'code_paths': ['capture/intake.py', 'archive/store.py', 'archive/codec.py',
                               'archive/readback.py', 'archive/export.py'],
                'oracle': 'Source off keeps every issued receipt by number and in issue order, '
                          'including the auto B/1 read and the manual A/3 read after handback.',
            }, ensure_ascii=False))


if __name__ == '__main__':
    main()
