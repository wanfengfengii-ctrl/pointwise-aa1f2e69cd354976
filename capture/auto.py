from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from archive.receipts import Receipt
from archive.store import ArchiveStore
from capture.intake import prepare_receipt
from capture.orders import ReadOrder, ReadOrderError, ReadStage, new_order_id
from capture.replies import ValidatedReply, validate_reply
from shift.catalog import Position
from shift.configuration import ShiftConfig
from terminal.device import LatchedRead
from terminal.navigation import SelectionError
from terminal.selection import ManualTerminal


class AutoPhase(str, Enum):
    APPROACH = 'approach'
    LATCHED = 'latched'
    ISSUED = 'issued'
    SIGNED = 'signed'
    CANCELLED = 'cancelled'


@dataclass(slots=True)
class AutoJob:
    """One frozen auto read order for a single terminal."""

    job_id: str
    shift: ShiftConfig
    target: Position
    phase: AutoPhase = AutoPhase.APPROACH
    tracked: Position | None = None
    latched: LatchedRead | None = None
    reply: ValidatedReply | None = None
    receipt: Receipt | None = None
    cancel_requested: bool = False
    mismatch: bool = False

    @property
    def finished(self) -> bool:
        return self.phase in (AutoPhase.SIGNED, AutoPhase.CANCELLED)


class AutoReads:
    """Step-wise auto collection on the terminal's single physical selector.

    Each ``step`` performs at most one ``DevicePort`` call: entering the target
    profile, selecting the target lane, latching the read, polling one reply, or
    one move of the selector back towards the manual work position.
    """

    def __init__(self, terminal: ManualTerminal, archive: ArchiveStore):
        self._terminal = terminal
        self._archive = archive
        self._jobs: dict[str, AutoJob] = {}
        self._active: str | None = None

    # -- lifecycle -------------------------------------------------------------

    def has_active(self) -> bool:
        return self._active is not None

    def start(self, shift_id: object, profile: object, lane: object) -> str:
        terminal = self._terminal
        if self._active is not None:
            raise ReadOrderError('terminal already has an active auto job')
        try:
            shift = terminal.shift_config()
        except SelectionError as exc:
            raise ReadOrderError('terminal has no open shift') from exc
        if not isinstance(shift_id, str) or shift_id != shift.shift_id:
            raise ReadOrderError(
                f'shift {shift_id!r} is not the terminal shift {shift.shift_id!r}')
        try:
            target = Position(profile, lane)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ReadOrderError(f'invalid auto target: {exc}') from exc
        if not shift.catalog.contains(target):
            raise ReadOrderError(f'auto target {target!r} is outside the shift roster')
        manual = terminal.manual_position()
        if manual is None or not shift.catalog.contains(manual):
            raise ReadOrderError('terminal has no valid manual work position')
        job_id = new_order_id()
        # The selector is idle, so the manual work position is also where the
        # physical selector sits right now.  Tracking it here costs no port
        # call and lets a cancel know whether a hand-back move is still due.
        self._jobs[job_id] = AutoJob(job_id, shift, target, tracked=manual)
        self._active = job_id
        terminal.hold_selector()
        return job_id

    def step(self, job_id: object) -> ReadStage | Receipt:
        job = self._require_active(job_id)
        if job.cancel_requested:
            return self._conclude_cancel(job)
        return self._advance(job)

    def cancel(self, job_id: object) -> ReadStage:
        job = self._require_active(job_id)
        if job.receipt is not None:
            raise ReadOrderError(f'auto job {job.job_id!r} is already issued')
        job.cancel_requested = True
        if self._at_manual(job):
            # The selector is already back at the latest manual work position.
            return self._finish_cancel(job)
        return ReadStage.PENDING

    # -- internals -------------------------------------------------------------

    def _require_active(self, job_id: object) -> AutoJob:
        job = self._jobs.get(job_id) if isinstance(job_id, str) else None
        if job is None:
            raise ReadOrderError(f'unknown auto job {job_id!r}')
        if job.finished or self._active != job.job_id:
            raise ReadOrderError(f'auto job {job.job_id!r} is already finished')
        return job

    def _advance(self, job: AutoJob) -> ReadStage | Receipt:
        terminal = self._terminal
        if job.mismatch:
            # The latch did not match the frozen target: refuse to sign and wait
            # for cancel_auto to hand the selector back.
            return ReadStage.PENDING
        if job.latched is None:
            if job.tracked is None:
                # The selector position is unknown (only after a rejected
                # latch); re-read it without trusting it as evidence.
                job.tracked = terminal.physical_position()
                return ReadStage.PENDING
            if job.tracked != job.target:
                if job.tracked.profile != job.target.profile:
                    job.tracked = terminal.move_profile(job.target.profile)
                else:
                    job.tracked = terminal.move_lane(job.target.lane)
                return ReadStage.PENDING
            latched = terminal.latch_device_read(job.job_id)
            job.latched = latched
            job.tracked = latched.position
            job.phase = AutoPhase.LATCHED
            if (latched.order_id != job.job_id
                    or latched.position != job.target
                    or not job.shift.catalog.contains(latched.position)):
                job.mismatch = True
                job.tracked = None
            return ReadStage.PENDING

        if job.reply is None:
            reply = terminal.poll_device_reply(job.job_id)
            if reply is None:
                return ReadStage.PENDING
            job.reply = validate_reply(job.latched, reply)
            if job.receipt is None:
                job.receipt = self._commit(job)
                job.phase = AutoPhase.ISSUED
            if not self._at_manual(job):
                return ReadStage.PENDING
            return self._finish_signed(job)

        if job.receipt is None:
            job.receipt = self._commit(job)
            job.phase = AutoPhase.ISSUED
        if self._move_home_step(job):
            return ReadStage.PENDING
        return self._finish_signed(job)

    def _commit(self, job: AutoJob) -> Receipt:
        if job.latched is None or job.reply is None:
            raise ReadOrderError(f'auto job {job.job_id!r} has no publishable evidence')
        order = ReadOrder(job.shift, job.latched, ReadStage.PENDING, job.reply)
        return self._archive.commit_receipt(prepare_receipt(order))

    def _manual_target(self, job: AutoJob) -> Position | None:
        manual = self._terminal.manual_position()
        return job.tracked if manual is None else manual

    def _at_manual(self, job: AutoJob) -> bool:
        return job.tracked is not None and job.tracked == self._manual_target(job)

    def _move_home_step(self, job: AutoJob) -> bool:
        """Perform at most one selector move towards the manual work position."""
        manual = self._manual_target(job)
        if job.tracked is None:
            job.tracked = self._terminal.physical_position()
            return True
        if manual is None or job.tracked == manual:
            return False
        if job.tracked.profile != manual.profile:
            job.tracked = self._terminal.move_profile(manual.profile)
        else:
            job.tracked = self._terminal.move_lane(manual.lane)
        return True

    def _conclude_cancel(self, job: AutoJob) -> ReadStage:
        if self._move_home_step(job):
            return ReadStage.PENDING
        return self._finish_cancel(job)

    def _finish_signed(self, job: AutoJob) -> Receipt:
        if job.receipt is None:
            raise RuntimeError(f'auto job {job.job_id!r} has no receipt to hand out')
        job.phase = AutoPhase.SIGNED
        self._active = None
        self._terminal.release_selector()
        return job.receipt

    def _finish_cancel(self, job: AutoJob) -> ReadStage:
        job.phase = AutoPhase.CANCELLED
        job.reply = None
        self._active = None
        self._terminal.release_selector()
        return ReadStage.CANCELLED
