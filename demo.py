import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.export import export_shift
from archive.readback import read_receipt
from archive.store import ArchiveStore
from capture.orders import ReadStage
from capture.readout import Readout
from shift.catalog import Position
from shift.manifest import open_shift_from_manifest
from terminal.display import describe_selection
from terminal.selection import ManualTerminal
from terminal.simulator import SimulatedDevice


def summarize(receipt):
    measurement = receipt.measurement
    return {
        'position': [receipt.position.profile, receipt.position.lane],
        'raw': receipt.raw,
        'value': str(measurement.quantity.value),
        'unit': measurement.quantity.unit,
        'verdict': measurement.verdict.value,
    }


def main() -> None:
    manifest = Path(__file__).resolve().parent / 'samples' / 'day_shift.json'
    with TemporaryDirectory(prefix='chengwan-demo-') as directory:
        database = Path(directory) / 'calibration.sqlite'
        with ArchiveStore.open(database) as archive:
            config = open_shift_from_manifest(archive, manifest)
            device = SimulatedDevice(config.catalog)
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
            device.stop()
        with ArchiveStore.open(database) as archive:
            fixed = read_receipt(archive, accepted.receipt_id)
            exported = export_shift(archive, config.shift_id)
            assert fixed == accepted and fixed.order_id == first.order_id
            assert read_receipt(archive, outside.receipt_id) == outside
            assert exported == [accepted, outside] and outside.order_id == second.order_id
            expected = {'position': ['A', 2], 'raw': 6, 'value': '3', 'unit': 'kPa', 'verdict': 'pass'}
            outside_expected = {'position': ['A', 2], 'raw': 7, 'value': '10/3',
                                'unit': 'kPa', 'verdict': 'outside_limit'}
            actual = summarize(accepted)
            downstream = summarize(fixed)
            assert actual == expected and downstream == expected
            assert [summarize(receipt) for receipt in exported] == [expected, outside_expected]
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
                'case_id': 'n8',
                'input': {'shift_id': config.shift_id, 'raw_in_issue_order': [accepted.raw, outside.raw],
                          'device_stopped': True},
                'expected': [expected, outside_expected],
                'actual': [summarize(accepted), summarize(outside)],
                'downstream_expected': [expected, outside_expected],
                'downstream_actual': [summarize(receipt) for receipt in exported],
                'code_paths': ['capture/intake.py', 'archive/store.py', 'archive/codec.py',
                               'archive/readback.py', 'archive/export.py'],
                'oracle': '6/3 + 1 = 3 passes and 7/3 + 1 = 10/3 exceeds 3. The first device reply was signed before the second.',
            }, ensure_ascii=False))


if __name__ == '__main__':
    main()
