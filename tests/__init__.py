"""Fixtures for the public manual collection and handover regression suite."""

from pathlib import Path

from capture.readout import Readout
from shift.manifest import open_shift_from_manifest
from terminal.selection import ManualTerminal
from terminal.simulator import SimulatedDevice

SAMPLE_PATH = Path(__file__).resolve().parents[1] / 'samples' / 'day_shift.json'
SAMPLE_SHIFT = 'CW-DAY-01'


def sample_bench(store):
    config = open_shift_from_manifest(store, SAMPLE_PATH)
    device = SimulatedDevice(config.catalog)
    terminal = ManualTerminal(device, config)
    return config, device, terminal, Readout(terminal, store)
