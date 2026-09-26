"""Decide one finite, local recovery pass before remote scheduling.

The supplied authorities own exact receipt, journal, guard and state-commit
proofs. This owner never calls a provider or grants send permission. A clear
result is only an observation; lane and send-time preflights must read again.
Construction has no runtime effects.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from mrs_bot_core_contracts import BotState


class RecoveryStatus(Enum):
    """The current pass's local progress and remaining control boundary."""

    CLEAR = "clear"
    RECOVERED = "recovered"
    BLOCKED = "blocked"
    PROGRESSED_BLOCKED = "progressed_blocked"
    PAUSED = "paused"
    INCIDENT = "incident"
    FAILED = "failed"


class RecoveryStage(Enum):
    """The local authority that produced a recognised failure."""

    MEDIA = "media"
    SOURCE = "source"
    RECONCILE = "reconcile"


class BarrierState(Enum):
    """Current global barrier observation after durability maintenance."""

    CLEAR = "clear"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class RecoveryErrors:
    """Recognised local failures; unrelated errors retain their identity."""

    media: tuple[type[Exception], ...]
    source: tuple[type[Exception], ...]
    reconcile: tuple[type[Exception], ...]


@dataclass(frozen=True)
class RecoveryOutcome:
    """Report one pass without authorising any future remote operation."""

    status: RecoveryStatus
    barrier: BarrierState
    reconciled_lanes: tuple[str, ...] = ()
    failure_stage: RecoveryStage | None = None
    failure: Exception | None = None

    @property
    def reload_committed_state(self) -> bool:
        """Discard possibly partial caller state after failed reconciliation."""
        return self.failure_stage is RecoveryStage.RECONCILE


class ResumeSourceRetirement(Protocol):
    """Resume exact source retirement under a freshly checked pause."""

    def __call__(self, *, maintenance_paused: bool) -> bool: ...


class ReconcileConfirmed(Protocol):
    """Reconcile only locally proved confirmed transactions."""

    def __call__(
        self, lines_used: set[str], images_used: set[str], state: BotState,
        current: int | None = None,
    ) -> Mapping[str, bool]: ...


class LocalRecovery:
    """Compose current recovery evidence with existing local authorities."""

    def __init__(
        self, *,
        global_paused: Callable[[], bool],
        incident_latched: Callable[[], bool],
        resume_media_retirement: Callable[[], bool],
        resume_source_retirement: ResumeSourceRetirement,
        reconcile_confirmed_transactions: ReconcileConfirmed,
        maintain_barrier: Callable[[], bool],
        errors: RecoveryErrors,
    ) -> None:
        """Bind dependencies without inspecting controls or local files."""
        self.global_paused = global_paused
        self.incident_latched = incident_latched
        self.resume_media_retirement = resume_media_retirement
        self.resume_source_retirement = resume_source_retirement
        self.reconcile_confirmed_transactions = reconcile_confirmed_transactions
        self.maintain_barrier = maintain_barrier
        self.errors = errors

    def run_once(
        self, lines_used: set[str], images_used: set[str], state: BotState,
        *, current: int | None = None,
    ) -> RecoveryOutcome:
        """Make one recovery pass, then maintain and assess the global barrier."""
        progress = False
        reconciled_lanes: tuple[str, ...] = ()
        failure_stage: RecoveryStage | None = None
        failure: Exception | None = None

        def finish() -> RecoveryOutcome:
            """Assess controls and durability after the most recent mutation."""
            blocked = self.maintain_barrier()
            if self.incident_latched():
                status = RecoveryStatus.INCIDENT
            elif self.global_paused():
                status = RecoveryStatus.PAUSED
            elif failure is not None:
                status = RecoveryStatus.FAILED
            elif blocked:
                status = (
                    RecoveryStatus.PROGRESSED_BLOCKED if progress
                    else RecoveryStatus.BLOCKED
                )
            else:
                status = RecoveryStatus.RECOVERED if progress else RecoveryStatus.CLEAR
            return RecoveryOutcome(
                status,
                BarrierState.BLOCKED if blocked else BarrierState.CLEAR,
                reconciled_lanes,
                failure_stage,
                failure,
            )

        if self.global_paused() or self.incident_latched():
            return finish()

        try:
            progress = self.resume_media_retirement()
        except self.errors.media as exc:
            failure_stage, failure = RecoveryStage.MEDIA, exc
            return finish()

        if self.global_paused() or self.incident_latched():
            return finish()

        try:
            progress = self.resume_source_retirement(maintenance_paused=False) or progress
        except self.errors.source as exc:
            failure_stage, failure = RecoveryStage.SOURCE, exc
            return finish()

        if self.global_paused() or self.incident_latched():
            return finish()

        try:
            if current is None:
                reconciled = self.reconcile_confirmed_transactions(
                    lines_used, images_used, state,
                )
            else:
                reconciled = self.reconcile_confirmed_transactions(
                    lines_used, images_used, state, current,
                )
        except self.errors.reconcile as exc:
            failure_stage, failure = RecoveryStage.RECONCILE, exc
        else:
            reconciled_lanes = tuple(
                lane for lane, completed in reconciled.items() if completed
            )
            progress = bool(reconciled_lanes) or progress

        return finish()
