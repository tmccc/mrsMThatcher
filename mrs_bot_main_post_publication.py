"""Own the shared durable publication steps for quote and meme posts.

One instance binds `MainPostReceipts`, `MainPostReceiptValues` and the current
transport authorities at cycle entry, calling the owners directly for attempt
publication, pending construction and finalization. It retains only that
publication's progress. Callers keep their distinct exception scopes,
history rollback, schedule projections and emergency state recovery. Explicit
guard release lets those projections complete before interrupts are delivered.
Construction and import perform no runtime I/O or configuration access.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

from mrs_bot_main_post_attempt_values import confirmation_epoch_for_main_attempt
from mrs_bot_receipt_primitives import valid_post_id


if TYPE_CHECKING:
    from mrs_bot_core_contracts import (
        AttemptingMainPostAttempt, MainPostAttempt, MainPostLane,
        CurrentMemePostReceipt, CurrentRegularPostReceipt, PendingMainPostReceipt,
        SendingMainPostAttempt,
    )
    from mrs_bot_main_post_receipt_storage import MainPostReceipts
    from mrs_bot_main_post_receipts import MainPostReceiptValues
    from mrsMThatcher2 import ApiError, ConfirmedPostSigintDeferral
    from remote_write_transport_journal import (
        ConfirmedTransportDetails, SourceReceiptBinding, TransportAuthority,
    )


_T = TypeVar("_T")


class PrepareMainPostTransport(Protocol):
    """Consume one sending authorisation while preserving its receipt identity."""

    def __call__(
        self, attempt: SendingMainPostAttempt,
    ) -> tuple[AttemptingMainPostAttempt, SourceReceiptBinding, TransportAuthority]: ...


class CreatePreparedMainPost(Protocol):
    """Send the already bound main-post request through the sealed transport."""

    def __call__(
        self, *, text: str, media_ids: list[str], reply_to_id: None,
        made_with_ai: bool, prepared_main_post_attempt: AttemptingMainPostAttempt,
        prepared_transport_authority: TransportAuthority,
        prepared_transport_source: SourceReceiptBinding,
    ) -> dict[str, Any]: ...


class RetireMainPostAttempt(Protocol):
    """Retire an exact attempt under a proved local disposition."""

    def __call__(
        self, attempt: MainPostAttempt, *, sending_disposition: str,
    ) -> None: ...


class PromoteMainPostPending(Protocol):
    """Bind confirmed remote identity before computing local schedules."""

    def __call__(
        self, attempt: AttemptingMainPostAttempt, *, post_id: str,
        confirmation_epoch: int, image_summary: str = "",
    ) -> PendingMainPostReceipt: ...


class RunPublicationStage(Protocol):
    """Preserve the meme's named diagnostics around a typed operation."""

    def __call__(self, stage: str, operation: Callable[[], _T]) -> _T: ...


_UNAVAILABLE = object()


@dataclass(eq=False)
class MainPostPublication:
    """Publish one main post while retaining exact partial recovery progress."""

    lane: MainPostLane
    receipt_path: Path
    log: Logger
    receipts: MainPostReceipts
    receipt_values: MainPostReceiptValues
    prepare_transport: PrepareMainPostTransport
    handoff_media: Callable[[AttemptingMainPostAttempt, TransportAuthority], None]
    begin_sigint: Callable[[], ConfirmedPostSigintDeferral]
    create_post: CreatePreparedMainPost
    proves_non_success: Callable[[BaseException], bool]
    retire_attempt: RetireMainPostAttempt
    end_sigint: Callable[[ConfirmedPostSigintDeferral | None], None]
    ambiguous_outcome: type[ApiError]
    incident_latched: Callable[[], bool]
    durable_barrier_exists: Callable[[], bool]
    retain_sigint: Callable[..., None]
    inspect_confirmation: Callable[[Path], ConfirmedTransportDetails]
    journal_path: Callable[[Path], Path]
    promote_pending: PromoteMainPostPending
    run_stage: RunPublicationStage | None
    validate_meme_post_id: Callable[[object], None] | None

    attempt: object = field(default=_UNAVAILABLE, init=False)
    pending_receipt: object = field(default=_UNAVAILABLE, init=False)
    pending_promoted: bool = field(default=False, init=False)
    guard: ConfirmedPostSigintDeferral | None = field(default=None, init=False)
    transport_source: SourceReceiptBinding = field(init=False, repr=False)
    transport_authority: TransportAuthority = field(init=False, repr=False)

    @property
    def pending_available(self) -> bool:
        """Distinguish a completed builder returning None from an unrun builder."""
        return self.pending_receipt is not _UNAVAILABLE

    def _stage(self, name: str, operation: Callable[[], _T]) -> _T:
        """Keep the meme's named diagnostics around exactly the original calls."""
        return operation() if self.run_stage is None else self.run_stage(name, operation)

    def prepare(self, attempt: SendingMainPostAttempt) -> None:
        """Publish the attempt, bind transport and hand off confirmed media."""
        self.attempt = attempt
        self._stage(
            "main_post_attempt_persistence",
            lambda: self.receipts.write_attempt(attempt),
        )
        (
            self.attempt, self.transport_source, self.transport_authority,
        ) = self._stage(
            "tweet_transport_preparation", lambda: self.prepare_transport(attempt),
        )
        self._stage(
            "media_upload_handoff",
            lambda: self.handoff_media(cast("AttemptingMainPostAttempt", self.attempt), self.transport_authority),
        )

    def begin_guard(self) -> None:
        """Begin deferral at the caller's existing pre-send exception boundary."""
        self.guard = self.begin_sigint()

    def send(self, *, text: str, media_id: str, made_with_ai: bool) -> object:
        """Create the prepared post and validate its returned identity."""
        response = self._stage(
            "x_post_request",
            lambda: self.create_post(
                text=text,
                media_ids=[media_id],
                reply_to_id=None,
                made_with_ai=made_with_ai,
                prepared_main_post_attempt=cast("AttemptingMainPostAttempt", self.attempt),
                prepared_transport_authority=self.transport_authority,
                prepared_transport_source=self.transport_source,
            ),
        )
        posted_id = response.get("data", {}).get("id") if isinstance(response, dict) else None
        if self.lane == "quote_image":
            self.log.debug("Posted_id=%s", posted_id)
            if not valid_post_id(posted_id):
                raise RuntimeError("Quote/image post did not return a valid post id; used histories unchanged")
        else:
            self.log.debug("Posted meme id=%s", posted_id)
            self._stage(
                "x_post_response_validation",
                lambda: cast(Callable[[object], None], self.validate_meme_post_id)(posted_id),
            )
        return posted_id

    def release_guard(self) -> None:
        """Deliver deferred interrupts, including the original end(None) calls."""
        self.end_sigint(self.guard)
        self.guard = None

    def retain_guard(self) -> None:
        """Keep interrupts deferred while no durable recovery barrier exists."""
        self.retain_sigint(lane=self.lane, guard=self.guard)

    def handle_remote_failure(self, error: BaseException) -> None:
        """Retire proved non-success or retain the unresolved remote barrier."""
        if self.attempt is not _UNAVAILABLE and self.proves_non_success(error):
            try:
                self.retire_attempt(cast("MainPostAttempt", self.attempt), sending_disposition="definite_non_success")
            except BaseException as removal_exc:
                self.release_guard()
                label = "regular" if self.lane == "quote_image" else "meme"
                raise self.ambiguous_outcome(
                    f"A definitely unsuccessful {label} post left its durable "
                    "sending receipt unresolved",
                    service="x",
                ) from removal_exc
        if (
            isinstance(error, self.ambiguous_outcome)
            and self.incident_latched()
            and not self.durable_barrier_exists()
        ):
            self.retain_guard()
        else:
            self.release_guard()

    def read_confirmation_epoch(self, posted_id: object) -> int:
        """Check the exact journal identity and derive its confirmation time."""
        confirmation = self.inspect_confirmation(self.journal_path(self.receipt_path))
        if confirmation.post_id != str(posted_id):
            label = "regular" if self.lane == "quote_image" else "meme"
            raise self.ambiguous_outcome(
                f"Confirmed {label}-post identity differs from its journal", service="x",
            )
        return confirmation_epoch_for_main_attempt(
            cast("MainPostAttempt", self.attempt),
            confirmation.confirmation_epoch,
            log=self.log,
        )

    def confirm_pending_schedule(
        self, posted_id: object, confirmation_epoch: int, *, image_summary: str = "",
    ) -> CurrentRegularPostReceipt | CurrentMemePostReceipt:
        """Promote confirmation before finalization, retaining each partial result."""
        # Quote callbacks omit this keyword; meme callbacks always receive it.
        summary = {} if self.lane == "quote_image" else {"image_summary": image_summary}
        self.pending_receipt = self.receipt_values.current().build_pending(
            cast("AttemptingMainPostAttempt", self.attempt), post_id=str(posted_id), confirmation_epoch=confirmation_epoch,
            **summary,
        )
        self.pending_receipt = self.promote_pending(
            cast("AttemptingMainPostAttempt", self.attempt), post_id=str(posted_id), confirmation_epoch=confirmation_epoch,
            **summary,
        )
        self.pending_promoted = True
        return self.receipts.current().finalize_pending(self.pending_receipt)
