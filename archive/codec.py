from __future__ import annotations

import json
from fractions import Fraction

from archive.receipts import Receipt, ReceiptDraft, seal_receipt
from quality.limits import Verdict
from quality.result import Measurement
from quality.units import Quantity
from shift.catalog import Position
from shift.profiles import CalibrationProfile


def _parts(value: Fraction) -> tuple[int, int]:
    return value.numerator, value.denominator


def encode_receipt_body(draft: ReceiptDraft) -> str:
    measurement = draft.measurement
    profile = measurement.calibration
    return json.dumps({
        'position': [draft.position.profile, draft.position.lane],
        'raw': draft.raw,
        'quantity': {'value': _parts(measurement.quantity.value), 'unit': measurement.quantity.unit},
        'verdict': measurement.verdict.value,
        'calibration': {
            'unit': profile.unit, 'scale': _parts(profile.scale), 'offset': _parts(profile.offset),
            'lower': _parts(profile.lower), 'upper': _parts(profile.upper),
        },
    }, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def decode_receipt(receipt_id: int, shift_id: str, order_id: str, body: str) -> Receipt:
    record = json.loads(body)
    position = Position(*record['position'])
    raw = record['raw']
    if type(raw) not in (int, str):
        raise ValueError(f'invalid stored raw payload for receipt {receipt_id}')
    calibrated = record['calibration']
    profile = CalibrationProfile(
        unit=calibrated['unit'], scale=Fraction(*calibrated['scale']),
        offset=Fraction(*calibrated['offset']), lower=Fraction(*calibrated['lower']),
        upper=Fraction(*calibrated['upper']),
    )
    reading = record['quantity']
    quantity = Quantity(Fraction(*reading['value']), reading['unit'])
    measurement = Measurement(quantity, Verdict(record['verdict']), profile)
    return seal_receipt(receipt_id, ReceiptDraft(shift_id, order_id, position, raw, measurement))
