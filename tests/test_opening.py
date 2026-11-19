import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from archive.store import ArchiveStore
from shift.catalog import Position, make_lane_catalog
from shift.manifest import open_shift_from_manifest
from shift.opening import ShiftConflictError, open_shift
from shift.profiles import make_calibration_profile
from terminal.display import SelectionView, describe_selection
from terminal.selection import ManualTerminal, SelectionError
from terminal.simulator import SimulatedDevice
from tests import SAMPLE_PATH, SAMPLE_SHIFT


class OpeningTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.database = Path(directory) / 'opening.sqlite'
        self.store = self.enterContext(ArchiveStore.open(self.database))

    def _terminal(self, config):
        device = SimulatedDevice(config.catalog)
        return ManualTerminal(device, config), device

    def _open_custom(self, shift_id, rows):
        catalog = make_lane_catalog(rows)
        calibration = make_calibration_profile(unit='mV', scale=1, offset=0, lower=0, upper=9)
        calibrations = {Position(profile, lane): calibration
                        for profile, lanes in catalog.profiles for lane in lanes}
        return open_shift(self.store, shift_id, catalog, calibrations)

    def test_open_and_enter_first_lane(self):
        config = open_shift_from_manifest(self.store, SAMPLE_PATH)
        equivalent = dict(config.calibrations)
        equivalent[Position('A', 2)] = make_calibration_profile(
            unit='kPa', scale=Fraction(2, 6), offset='1.00', lower='2/2', upper='3.0',
        )
        reopened = open_shift(self.store, SAMPLE_SHIFT, config.catalog, equivalent)
        self.assertEqual(config, reopened)
        self.assertEqual(config.catalog.lanes('A'), (1, 2, 3))
        terminal, device = self._terminal(reopened)
        self.assertEqual(describe_selection(terminal), SelectionView(SAMPLE_SHIFT, None, None))
        self.assertEqual(terminal.select('A'), Position('A', 1))
        self.assertEqual(terminal.select(), Position('A', 2))
        self.assertEqual(device.position(), Position('A', 2))
        self.assertEqual(describe_selection(terminal), SelectionView(SAMPLE_SHIFT, Position('A', 2), 2))

    def test_declared_lane_order(self):
        config = self._open_custom('ORDER', [('B', ['return', 'feed']), ('A', [1, 3, 2])])
        terminal, device = self._terminal(config)
        self.assertEqual([profile for profile, _ in config.catalog.profiles], ['B', 'A'])
        self.assertEqual(terminal.select('A'), Position('A', 1))
        self.assertEqual(terminal.select(), Position('A', 3))
        self.assertEqual(device.position(), Position('A', 3))
        self.assertEqual(describe_selection(terminal), SelectionView('ORDER', Position('A', 3), 2))
        self.assertEqual(terminal.select(), Position('A', 2))
        self.assertEqual(terminal.select('B'), Position('B', 'return'))
        self.assertEqual(terminal.select(), Position('B', 'feed'))
        with self.assertRaises(ShiftConflictError):
            self._open_custom('ORDER', [('B', ['return', 'feed']), ('A', [1, 2, 3])])
        with self.assertRaises(ShiftConflictError):
            self._open_custom('ORDER', [('A', [1, 3, 2]), ('B', ['return', 'feed'])])
        self.assertEqual(terminal.select('A'), Position('A', 1))
        self.assertEqual(terminal.select(), Position('A', 3))

    def test_switch_profile(self):
        config = open_shift_from_manifest(self.store, SAMPLE_PATH)
        terminal, device = self._terminal(config)
        terminal.select('A')
        terminal.select()
        self.assertEqual(terminal.select('B'), Position('B', 1))
        self.assertEqual(terminal.select(), Position('B', 2))
        self.assertEqual(terminal.select('A'), Position('A', 1))
        self.assertEqual(device.position(), Position('A', 1))
        terminal.select()
        self.assertEqual(terminal.select('A'), Position('A', 1))

    def test_reinitialize_keeps_shift(self):
        config = self._open_custom('USER-SHIFT', [('X', ['feed', 'check'])])
        self.store.close()
        with ArchiveStore.open(self.database) as reopened:
            self.assertIsNone(reopened.load_shift_record(SAMPLE_SHIFT))
            frozen = open_shift(reopened, config.shift_id, config.catalog, dict(config.calibrations))
            self.assertEqual(frozen, config)
            terminal, _ = self._terminal(frozen)
            self.assertEqual(terminal.select('X'), Position('X', 'feed'))
            self.assertEqual(terminal.select(), Position('X', 'check'))

    def test_conflicting_reopen(self):
        original = open_shift_from_manifest(self.store, SAMPLE_PATH)
        changed_catalog = make_lane_catalog([('A', [1, 2, 4]), ('B', [1, 2])])
        changed_profiles = dict(original.calibrations)
        changed_profiles[Position('A', 4)] = changed_profiles.pop(Position('A', 3))
        with self.assertRaises(ShiftConflictError):
            open_shift(self.store, SAMPLE_SHIFT, changed_catalog, changed_profiles)
        reopened = open_shift_from_manifest(self.store, SAMPLE_PATH)
        self.assertEqual(reopened, original)
        terminal, _ = self._terminal(reopened)
        terminal.select('A')
        terminal.select()
        self.assertEqual(terminal.select(), Position('A', 3))

    def test_changed_calibration(self):
        config = open_shift_from_manifest(self.store, SAMPLE_PATH)
        position = Position('A', 2)
        for scale, upper in [('2/3', 3), ('1/3', 4)]:
            with self.subTest(scale=scale, upper=upper):
                changed = dict(config.calibrations)
                changed[position] = make_calibration_profile(
                    unit='kPa', scale=scale, offset=1, lower=1, upper=upper,
                )
                with self.assertRaises(ShiftConflictError):
                    open_shift(self.store, SAMPLE_SHIFT, config.catalog, changed)
                frozen = open_shift_from_manifest(self.store, SAMPLE_PATH).profile_at(position)
                self.assertEqual(frozen.scale, Fraction(1, 3))
                self.assertEqual(frozen.upper, Fraction(3))

    def test_invalid_catalog(self):
        for rows in ([], [('A', [])], [('A', [1, 1])], [('A', [True])],
                     [('A', '12')], [('A', [1]), ('A', [2])], [(' ', [1])]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                make_lane_catalog(rows)
        config = open_shift_from_manifest(self.store, SAMPLE_PATH)
        incomplete = dict(config.calibrations)
        incomplete.pop(Position('A', 2))
        with self.assertRaises(ValueError):
            open_shift(self.store, 'INCOMPLETE', config.catalog, incomplete)
        self.assertIsNone(self.store.load_shift_record('INCOMPLETE'))
        extra = dict(config.calibrations)
        extra[Position('B', 99)] = config.profile_at(Position('B', 1))
        with self.assertRaises(ValueError):
            open_shift(self.store, 'EXTRA', config.catalog, extra)
        self.assertIsNone(self.store.load_shift_record('EXTRA'))

    def test_invalid_manifest_calibration_leaves_no_shift(self):
        path = self.database.with_name('invalid.json')
        for edits in ({'upper': 0}, {'offset_unit': 'mV'}, {'upper_unit': 'mV'},
                      {'lower_unit': 'mV'}, {'scale': '1/0'}, {'scale': 'NaN'},
                      {'scale': True}, {'scale': 0.25}, {'offset': None}, {'unit': ''}):
            with self.subTest(edits=edits):
                data = json.loads(SAMPLE_PATH.read_text(encoding='utf-8'))
                data['shift_id'] = 'INVALID'
                data['profiles'][0]['lanes'][1]['calibration'].update(edits)
                path.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, r'profiles\[0\]\.lanes\[1\]'):
                    open_shift_from_manifest(self.store, path)
                self.assertIsNone(self.store.load_shift_record('INVALID'))
        path.write_text('{"shift_id":"DUP", "shift_id":"OTHER", "profiles":[]}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duplicate field'):
            open_shift_from_manifest(self.store, path)
        self.assertIsNone(self.store.load_shift_record('DUP'))
        self.assertIsNone(self.store.load_shift_record('OTHER'))

    def test_next_past_end(self):
        config = open_shift_from_manifest(self.store, SAMPLE_PATH)
        terminal, device = self._terminal(config)
        terminal.select('A')
        terminal.select()
        terminal.select()
        with self.assertRaises(SelectionError):
            terminal.select()
        self.assertEqual(device.position(), Position('A', 3))
        self.assertEqual(describe_selection(terminal), SelectionView(SAMPLE_SHIFT, Position('A', 3), 3))

    def test_invalid_selection(self):
        config = open_shift_from_manifest(self.store, SAMPLE_PATH)
        terminal, device = self._terminal(config)
        for attempt in (lambda: terminal.select(), lambda: terminal.select('absent')):
            with self.assertRaises(SelectionError):
                attempt()
            self.assertIsNone(device.position())
        terminal.select('B')
        with self.assertRaises(SelectionError):
            terminal.select('absent')
        with self.assertRaises(SelectionError):
            ManualTerminal(device).select('A')
        self.assertEqual(device.position(), Position('B', 1))
