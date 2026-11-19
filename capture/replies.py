from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from shift.profiles import parse_exact_number
from terminal.device import DeviceReply, LatchedRead


class ReplyError(ValueError):
    """Device evidence cannot be accepted for this order."""


@dataclass(frozen=True, slots=True)
class ValidatedReply:
    order_id: str
    raw: int | str
    exact: Fraction


def validate_reply(expected: LatchedRead, reply: DeviceReply) -> ValidatedReply:
    if not isinstance(reply, DeviceReply):
        raise ReplyError('expected a device reply')
    if reply.order_id != expected.order_id or reply.position != expected.position:
        raise ReplyError(f'reply identity or latched position differs for {expected.order_id!r}')
    if type(reply.raw) not in (int, str):
        raise ReplyError('raw payload must be an integer or exact numeric text')
    try:
        exact = parse_exact_number(reply.raw)
    except ValueError as exc:
        raise ReplyError(f'invalid raw payload for {expected.order_id!r}: {exc}') from exc
    return ValidatedReply(reply.order_id, reply.raw, exact)
