"""Deliver conversational replies and manage their durable receipt lifecycle.

ReplyReceipts owns loading, exclusive publication and current/legacy promotion.
Root composition supplies a fresh owner at each receipt operation boundary and
binds the global write barrier directly to the applicable receipt owner.
ReplyCycleDelivery owns pre-send checks and delivery outcome handling and calls
its cycle-bound receipt, completion, tweet and cooldown owners directly. The lanes retain their own terminal
bookkeeping and check statuses. Receipt
operations retain exact source binding, error order, shallow references,
conservative confirmation and fallback state completeness, and the existing
SIGINT deferral boundaries.

No-follow/create/replace/retire primitives, transport journals and mutation
authority, create_post, runtime barriers, SIGINT guard implementation, state
persistence and reconciliation remain in their existing owners and are invoked
through current runtime boundaries. Send-time ambiguity bookkeeping receives a
fresh cooldown owner. Proof-bound exact retirement retains its existing authority
callback, and
ReplyCompletion owns durable commit and ordered retirement.
Import uses inert primitives and performs no file, environment, provider or RNG
work; runtime bindings are retained only by operation-scoped owners.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from mrs_bot_durable_json_io import canonical_atomic_json_bytes

if TYPE_CHECKING:
    from mrs_bot_api_cooldowns import ApiCooldowns
    from mrs_bot_reply_cycle_interfaces import PostReply, SaveReplyState
    from mrs_bot_reply_receipt_values import ReplyReceiptValues
    from mrs_bot_reply_reconciliation import ReplyCompletion
    from mrs_bot_tweet_lookup_cache import TweetLookupCache


class ReplyDeliveryStop(Enum):
    """A delivery failure that the calling lane maps to its own check status."""

    TERMINAL = "terminal"
    RETRYABLE = "retryable"


@dataclass(frozen=True)
class ReplyCycleDelivery:
    """Own reply delivery and error routing with current cycle-bound authorities.

    The post callback retains its own send-time composition. Lane-specific
    terminal bookkeeping and check-status mapping remain with the caller.
    """

    receipts: ReplyReceipts
    completion: ReplyCompletion
    block_ambiguous: Callable[[], None]
    receipt_values: ReplyReceiptValues
    tweets: TweetLookupCache
    post: PostReply
    retire_rejected: Callable[[dict, Exception], None]
    ambiguous_outcome: type[Exception]
    remote_operations_paused: type[Exception]
    api_error: type[Exception]
    confirmed_local_failure: type[Exception]
    proved_non_success: type[Exception]
    unrecoverable_confirmed: type[Exception]
    reply_not_allowed: Callable
    save_state: SaveReplyState
    log: logging.Logger
    posting_outcome: Callable
    cooldowns: ApiCooldowns

    def load_receipt(self) -> tuple[str, dict | None]:
        """Load through the cycle-bound receipt owner."""
        return self.receipts.load()

    def reconcile_receipt(self, state: dict) -> bool:
        """Reconcile through the cycle-bound completion owner."""
        return self.completion.reconcile(state)

    def bind_attempt(self, receipt_template: dict) -> dict:
        """Bind one receipt through the cycle-bound value owner."""
        return self.receipt_values.bind_attempt(receipt_template)

    def target_available(self, target_id: str) -> bool:
        """Perform the fresh pre-send lookup through the cycle-bound tweet owner."""
        return self.tweets.target_is_available(target_id)

    def finalise(
        self, state: dict, receipt: dict, *, target_id: str, quote_reply: bool,
    ) -> str:
        """Finalise through the cycle-bound completion owner."""
        return self.completion.finalise(
            state, receipt, target_id=target_id, quote_reply=quote_reply,
        )

    def deliver(
        self,
        state: dict,
        target_id: str,
        reply_text: object,
        receipt_template: dict,
        *,
        lane: str,
        log_source: str,
        read_error_scope: str,
        mark_as_ai: bool,
        retire_terminal_target: Callable[[str], None],
    ) -> dict | ReplyDeliveryStop:
        """Recheck, send and route delivery failures without owning lane bookkeeping.

        Terminal retirement before sending stays inside the transport exception
        boundary. Retirement after a proved API refusal stays in its exception
        handler, so its failures propagate and cannot remove the sending journal
        before the lane has durably retired its target. Confirmed and ambiguous
        outcomes always propagate to the existing recovery authority. Local
        runtime pauses defer without API-health bookkeeping. Pre-send lookup
        failures use the caller's read cooldown without retiring the draft.
        """
        error_scope = read_error_scope
        try:
            target_available = self.target_available(target_id)
            error_scope = "write"
            if not target_available:
                retire_terminal_target("target_unavailable_pre_send")
                return ReplyDeliveryStop.TERMINAL
            _, receipt = self.post(
                state=state,
                receipt_template=receipt_template,
                reply_text=reply_text,
                reply_to_id=target_id,
                made_with_ai=mark_as_ai,
                lane=lane,
            )
        except self.unrecoverable_confirmed:
            self.log.critical(
                "Confirmed %s reply lost every complete durable local identity; "
                "the global remote-write safety barrier remains active",
                log_source,
                exc_info=True,
            )
            raise
        except self.confirmed_local_failure:
            self.log.critical(
                "Confirmed %s reply required its durable state fallback",
                log_source,
                exc_info=True,
            )
            raise
        except self.ambiguous_outcome:
            self.log.critical(
                "%s reply stopped after an ambiguous remote outcome; the global "
                "remote-write safety barrier remains active",
                "Quote-tweet" if lane == "quote_tweet" else log_source,
                exc_info=True,
            )
            self.posting_outcome(
                reply=reply_text,
                status="posting_failed_retryable",
                lane=lane,
                target_id=target_id,
                failure_reason="ambiguous_remote_outcome",
            )
            raise
        except self.remote_operations_paused:
            self.log.info(
                "Deferring %s reply delivery because remote operations are paused",
                log_source,
            )
            return ReplyDeliveryStop.RETRYABLE
        except self.api_error as e:
            if error_scope == "write" and self.reply_not_allowed(e):
                retire_terminal_target("reply_not_permitted")
                if isinstance(e, self.proved_non_success):
                    self.retire_rejected(receipt_template, e)
                return ReplyDeliveryStop.TERMINAL

            self.log.exception(
                "Failed to revalidate reply target before send"
                if error_scope != "write" else (
                    "Failed to post generated quote-tweet reply"
                    if lane == "quote_tweet" else "Failed to post generated reply"
                )
            )
            self.posting_outcome(
                reply=reply_text,
                status="posting_failed_retryable",
                lane=lane,
                target_id=target_id,
                failure_reason=f"x_api_{getattr(e, 'status_code', 'error')}",
            )
            self.cooldowns.record_error(state, e, "x", scope=error_scope)
            self.save_state(state)
            return ReplyDeliveryStop.RETRYABLE
        except Exception as e:
            self.log.exception(
                "Unexpected failure revalidating reply target before send"
                if error_scope != "write" else (
                    "Unexpected failure posting generated quote-tweet reply"
                    if lane == "quote_tweet" else "Unexpected failure posting generated reply"
                )
            )
            self.posting_outcome(
                reply=reply_text,
                status="posting_failed_retryable",
                lane=lane,
                target_id=target_id,
                failure_reason=(
                    "unexpected_pre_send_lookup_error"
                    if error_scope != "write" else "unexpected_posting_error"
                ),
            )
            self.cooldowns.record_error(state, e, "x", scope=error_scope)
            self.save_state(state)
            return ReplyDeliveryStop.RETRYABLE
        return receipt


@dataclass(frozen=True)
class ReplyReceipts:
    """Load, publish and promote the conversational reply receipt namespace.

    One operation keeps its current path, values and runtime authorities. Nested
    loading binds a fresh owner at the existing read boundary; transport and
    exact source retirement retain their independent authority owners.
    """

    path: Path
    read_json: Callable
    log: logging.Logger
    values: ReplyReceiptValues
    retirement_is_blocking: Callable
    invalid_receipt: type[Exception]
    namespace_entry_exists: Callable
    create_json: Callable
    unresolved_sending: type[Exception]
    bind_confirmed_source: Callable
    journal_path: Callable
    validator_id: str
    transport_validator: Callable
    legacy_transport_validator: Callable
    transport_journal_error: type[Exception]
    replace_bound_source: Callable
    mutation_authority: Callable
    current_receipts: Callable[[], ReplyReceipts]

    def load(self) -> tuple[str, dict | None]:
        """Load confirmed reply receipt."""
        try:
            present, data = self.read_json(
                self.path
            )
        except Exception:
            self.log.exception(
                "Malformed or unsafe confirmed-reply receipt blocks auto-reply "
                "processing until repaired: %s",
                self.path,
            )
            return "invalid", None
        if not present:
            return "absent", None
        if not isinstance(data, dict):
            self.log.critical(
                "Invalid confirmed-reply receipt blocks auto-reply processing until repaired: %s",
                self.path,
            )
            return "invalid", None
        if self.values.sending_is_valid(data):
            return "sending", data
        if self.values.legacy_sending_is_valid(data):
            return "legacy_sending", data
        if not (
            self.values.confirmed_is_valid(data)
            or self.values.legacy_confirmed_is_valid(data)
        ):
            self.log.critical(
                "Semantically invalid confirmed-reply receipt blocks auto-reply processing until repaired: %s",
                self.path,
            )
            return "invalid", data
        return "valid", data

    def write(self, receipt: dict, *, confirmed: bool) -> None:
        """Publish a validated sending or confirmed receipt without overwriting recovery.

        Retirement and namespace checks precede value validation. Exclusive creation
        handles a namespace race without replacing the competing receipt; each
        lifecycle retains its existing diagnostic and confirmation log fields.
        """
        receipt_label = "confirmed-reply" if confirmed else "conversational-reply"
        if self.retirement_is_blocking():
            raise self.invalid_receipt(
                f"Refusing {receipt_label} publication during source-receipt retirement"
            )
        if self.namespace_entry_exists(self.path):
            raise self.invalid_receipt(
                f"Refusing to overwrite unresolved {receipt_label} receipt: {self.path}"
            )
        valid = (
            self.values.confirmed_is_valid(receipt)
            if confirmed else self.values.sending_is_valid(receipt)
        )
        if not valid:
            raise RuntimeError(
                "Internal error: generated confirmed-reply receipt failed semantic validation"
                if confirmed else "Internal error: generated sending-reply receipt failed validation"
            )
        try:
            self.create_json(self.path, receipt)
        except FileExistsError as exc:
            raise self.invalid_receipt(
                f"Refusing to overwrite a {receipt_label} namespace entry which "
                "appeared during publication"
            ) from exc
        if confirmed:
            self.log.warning(
                "Wrote confirmed reply receipt pending local reconciliation source=%s target_id=%s reply_post_id=%s path=%s",
                receipt.get("candidate_source", "mention"),
                receipt.get("target_id"),
                receipt.get("reply_post_id"),
                self.path,
            )
        else:
            self.log.warning(
                "Wrote conversational reply sending receipt source=%s target_id=%s path=%s",
                receipt.get("candidate_source", "mention"),
                receipt.get("target_id"),
                self.path,
            )

    def promote(
        self,
        sending_receipt: dict,
        *,
        reply_post_id: str,
        confirmation_epoch: int,
        legacy_recovery: bool = False,
    ) -> dict:
        """Promote one exact transport-bound source under its receipt family rules.

        Frozen legacy sources use the supplied recovery-only transport validator;
        both families share source identity checks before value projection and the
        atomic replacement without mutating the caller-owned source.
        """
        status, current = self.current_receipts().load()
        expected_status = "legacy_sending" if legacy_recovery else "sending"
        if status != expected_status or current != sending_receipt:
            raise self.unresolved_sending(
                "Legacy conversational sending receipt changed before recovery"
                if legacy_recovery else
                "Conversational reply sending receipt changed before confirmation"
            )
        recovery = self.bind_confirmed_source(
            journal_path=self.journal_path(self.path),
            receipt_path=self.path,
            validator_id=self.validator_id,
            validator=(
                self.legacy_transport_validator
                if legacy_recovery else self.transport_validator
            ),
        )
        if (
            recovery.details.lane != "conversational_reply"
            or recovery.details.post_id != str(reply_post_id)
            or recovery.details.confirmation_epoch != int(confirmation_epoch)
            or recovery.source_binding.receipt_document != sending_receipt
            or recovery.source_binding.receipt_bytes
            != canonical_atomic_json_bytes(sending_receipt)
        ):
            raise self.transport_journal_error(
                "confirmed legacy conversational transport/source lineage changed"
                if legacy_recovery else
                "confirmed conversational transport/source lineage changed"
            )
        confirmed = self.values.confirmed_from_sending(
            sending_receipt,
            reply_post_id=reply_post_id,
            confirmation_epoch=confirmation_epoch,
        )
        if not (
            self.values.legacy_confirmed_is_valid(confirmed)
            if legacy_recovery else self.values.confirmed_is_valid(confirmed)
        ):
            raise RuntimeError(
                "Internal error: promoted legacy reply receipt failed recovery validation"
                if legacy_recovery else
                "Internal error: promoted confirmed-reply receipt failed validation"
            )
        self.replace_bound_source(
            recovery.source_binding,
            canonical_atomic_json_bytes(confirmed),
            mutation_authority=self.mutation_authority(
                "confirmed legacy conversational source receipt promotion"
                if legacy_recovery else "confirmed conversational source receipt promotion"
            ),
        )
        self.log.warning(
            "Promoted legacy conversational reply receipt from exact confirmed "
            "transport source=%s target_id=%s reply_post_id=%s path=%s"
            if legacy_recovery else
            "Promoted conversational reply receipt to confirmed source=%s "
            "target_id=%s reply_post_id=%s path=%s",
            confirmed.get("candidate_source", "mention"),
            confirmed.get("target_id"),
            confirmed.get("reply_post_id"),
            self.path,
        )
        return confirmed


def remove_confirmed_reply_receipt(
    receipt: dict,
    *,
    sending_disposition: str | None = None,
    CONFIRMED_REPLY_RECEIPT_FILE: Path,
    load_receipt_json_no_follow: Callable[[Path], tuple[bool, object | None]],
    InvalidConfirmedReplyReceipt: type[Exception],
    retire_current_source_receipt: Callable,
    log: logging.Logger,
) -> None:
    """Retire one exact conversational-reply source receipt."""

    present, current = load_receipt_json_no_follow(CONFIRMED_REPLY_RECEIPT_FILE)
    if not present:
        raise FileNotFoundError(CONFIRMED_REPLY_RECEIPT_FILE)
    if current != receipt:
        raise InvalidConfirmedReplyReceipt(
            "Refusing to remove a conversational-reply receipt whose "
            "transaction identity changed"
        )
    if receipt.get("lifecycle_state") == "sending":
        if sending_disposition not in {
            "definite_non_success",
            "confirmed_state_fallback",
        }:
            raise ValueError(
                "Removing a sending reply receipt requires an explicit disposition"
            )
    elif sending_disposition is not None:
        raise ValueError("A confirmed reply receipt cannot use a sending disposition")
    retire_current_source_receipt(
        CONFIRMED_REPLY_RECEIPT_FILE,
        canonical_atomic_json_bytes(receipt),
    )
    if receipt.get("lifecycle_state") == "sending":
        log.warning(
            "Removed conversational reply sending receipt disposition=%s "
            "source=%s target_id=%s path=%s",
            sending_disposition,
            receipt.get("candidate_source", "mention"),
            receipt.get("target_id"),
            CONFIRMED_REPLY_RECEIPT_FILE,
        )
    else:
        log.info(
            "Removed reconciled confirmed-reply receipt source=%s target_id=%s "
            "reply_post_id=%s path=%s",
            receipt.get("candidate_source", "mention"),
            receipt.get("target_id"),
            receipt.get("reply_post_id"),
            CONFIRMED_REPLY_RECEIPT_FILE,
        )


def retire_proved_rejected_conversational_reply_receipt(
    receipt: dict,
    error: ProvedRemotePostNonSuccess,
    *,
    ProvedRemotePostNonSuccess: type[Exception],
    api_error_is_reply_not_allowed: Callable,
    sending_reply_receipt_is_semantically_valid: Callable,
    reply_create_rejection_payload: Callable,
    claim_reply_create_rejection_for_receipt_retirement: Callable,
    CONFIRMED_REPLY_RECEIPT_FILE: Path,
    remove_confirmed_reply_receipt: Callable,
    record_ambiguous_remote_post: Callable,
    ConfirmedReplyLocalPersistenceError: type[Exception],
) -> None:
    """Retire the exact sending receipt after terminal state is durable."""

    if (
        not isinstance(error, ProvedRemotePostNonSuccess)
        or not api_error_is_reply_not_allowed(error)
        or not sending_reply_receipt_is_semantically_valid(receipt)
    ):
        raise ValueError(
            "conversational receipt retirement requires a proved target rejection"
        )
    payload = reply_create_rejection_payload(
        error.remote_non_success_proof
    )
    if (
        payload is None
        or str(payload.get("text") or "") != str(receipt.get("reply_text") or "")
        or str((payload.get("reply") or {}).get("in_reply_to_tweet_id") or "")
        != str(receipt.get("target_id") or "")
        or payload.get("media") is not None
    ):
        raise ValueError(
            "proved target rejection no longer binds the conversational receipt"
        )
    try:
        if not claim_reply_create_rejection_for_receipt_retirement(
            error.remote_non_success_proof,
            target_id=str(receipt.get("target_id") or ""),
            receipt_path=CONFIRMED_REPLY_RECEIPT_FILE,
            receipt=receipt,
        ):
            raise ValueError(
                "proved target rejection was not retired by its exact transport"
            )
        remove_confirmed_reply_receipt(
            receipt,
            sending_disposition="definite_non_success",
        )
    except BaseException as retirement_error:
        # Terminal state was saved before this call, so the target cannot be
        # retried.  Still preserve a durable global barrier for any uncertain
        # receipt namespace transition instead of pretending the transaction
        # is wholly clear.
        record_ambiguous_remote_post(payload)
        if not isinstance(retirement_error, Exception):
            raise
        raise ConfirmedReplyLocalPersistenceError(
            "A proved-rejected conversational reply left its sending receipt "
            "retirement unresolved"
        ) from retirement_error


def post_conversational_reply_with_durable_identity(
    *,
    state: dict,
    receipt_template: dict,
    reply_text: str,
    reply_to_id: str,
    made_with_ai: bool,
    lane: str,
    receipt_values: ReplyReceiptValues,
    receipt_namespace_entry_exists: Callable,
    CONFIRMED_REPLY_RECEIPT_FILE: Path,
    InvalidConfirmedReplyReceipt: type[Exception],
    block_if_ambiguous_remote_post: Callable,
    receipts: Callable[[], ReplyReceipts],
    begin_confirmed_post_sigint_deferral: Callable,
    create_post: Callable,
    AmbiguousRemotePostOutcome: type[Exception],
    end_confirmed_post_sigint_deferral: Callable,
    cooldowns: ApiCooldowns,
    save_state: Callable,
    log: logging.Logger,
    RemoteOperationsPaused: type[Exception],
    remove_confirmed_reply_receipt: Callable,
    ConfirmedReplyLocalPersistenceError: type[Exception],
    ProvedRemotePostNonSuccess: type[Exception],
    ApiError: type[Exception],
    inspect_confirmed_transport_transaction: Callable,
    journal_path_for_receipt: Callable,
    StateBackupWriteError: type[Exception],
    json_file_matches: Callable,
    STATE_FILE: Path,
    confirmed_reply_emergency_representation_is_complete: Callable,
    latch_confirmed_post_persistence_failure: Callable,
    retain_sigint_deferral_without_durable_barrier: Callable,
    UnrecoverableConfirmedReplyPersistenceError: type[Exception],
    completion: ReplyCompletion,
) -> tuple[dict, dict]:
    """Create a conversational reply and durably bind its remote identity.

    A controlled SIGINT is deferred from the first remote-create instruction
    until either the confirmed-reply receipt, a complete canonical state
    fallback, or the global manual-reconciliation barrier is durable.
    """
    receipt_template = receipt_values.prepare_sending_template(receipt_template, lane=lane)

    # Preserve the receipt-specific error for an already unresolved reply, but
    # do not create a new competing reply receipt while a confirmed main post
    # is represented by a local-only pending-schedule obligation.
    if receipt_namespace_entry_exists(CONFIRMED_REPLY_RECEIPT_FILE):
        raise InvalidConfirmedReplyReceipt(
            "Refusing to overwrite unresolved conversational-reply receipt: "
            f"{CONFIRMED_REPLY_RECEIPT_FILE}"
        )
    block_if_ambiguous_remote_post()
    receipts().write(receipt_template, confirmed=False)
    sigint_guard = begin_confirmed_post_sigint_deferral()
    try:
        response = create_post(
            # ``AIReply`` is a provenance-bearing ``str`` subclass.  The
            # transport journal intentionally accepts only exact JSON scalar
            # types, so cross this authority boundary with an ordinary string.
            text=str(reply_text),
            media_ids=None,
            reply_to_id=reply_to_id,
            made_with_ai=made_with_ai,
            prepared_conversational_reply_receipt=receipt_template,
        )
    except AmbiguousRemotePostOutcome as exc:
        # The pre-send receipt is itself the restart-safe ambiguity barrier,
        # including when create_post could not write its global marker.
        end_confirmed_post_sigint_deferral(sigint_guard)
        try:
            cooldowns.record_error(state, exc, "x", scope="write")
            save_state(state)
        except Exception:
            log.critical(
                "The conversational reply sending receipt is durable, but "
                "write-error bookkeeping could not be persisted",
                exc_info=True,
            )
        raise
    except RemoteOperationsPaused:
        # The runtime-control check is local and occurs before transmission.
        try:
            remove_confirmed_reply_receipt(
                receipt_template,
                sending_disposition="definite_non_success",
            )
        except Exception as removal_error:
            end_confirmed_post_sigint_deferral(sigint_guard)
            raise ConfirmedReplyLocalPersistenceError(
                "A definitely failed conversational reply left its durable "
                "sending receipt unresolved"
            ) from removal_error
        end_confirmed_post_sigint_deferral(sigint_guard)
        raise
    except ProvedRemotePostNonSuccess:
        # create_post has already retired the exact consumed journal/fence.
        # Keep the sending receipt until the lane durably records its terminal
        # target outcome, then let that lane retire the receipt explicitly.
        end_confirmed_post_sigint_deferral(sigint_guard)
        raise
    except ApiError as remote_error:
        # No provider-contract evidence makes a post-transmission HTTP status a
        # proof of non-creation.  Preserve the durable sending receipt and
        # require reconciliation rather than authorising another reply.
        end_confirmed_post_sigint_deferral(sigint_guard)
        try:
            cooldowns.record_error(state, remote_error, "x", scope="write")
            save_state(state)
        except Exception:
            log.critical(
                "The conversational reply sending receipt is durable, but "
                "write-error bookkeeping could not be persisted",
                exc_info=True,
            )
        raise AmbiguousRemotePostOutcome(
            "Conversational reply received a post-transmission error whose "
            "remote-create outcome is unproved; its sending receipt remains",
            service="x",
            status_code=remote_error.status_code,
            request_method=remote_error.request_method,
            request_path=remote_error.request_path,
        ) from remote_error
    except BaseException as remote_error:
        # Any unclassified interruption may have happened after bytes reached
        # X. Preserve the pre-send receipt as a restart-safe manual
        # reconciliation barrier. In particular, never discard it for
        # KeyboardInterrupt/SystemExit or an unexpected transport exception.
        end_confirmed_post_sigint_deferral(sigint_guard)
        if not isinstance(remote_error, Exception):
            raise
        raise AmbiguousRemotePostOutcome(
            "Conversational reply execution was interrupted with an unclassified "
            "remote outcome; its durable sending receipt requires reconciliation",
            service="x",
        ) from remote_error

    try:
        own_reply_id = str(response.get("data", {}).get("id") or "")
        transport_confirmation = inspect_confirmed_transport_transaction(
            journal_path_for_receipt(CONFIRMED_REPLY_RECEIPT_FILE)
        )
        if transport_confirmation.post_id != own_reply_id:
            raise AmbiguousRemotePostOutcome(
                "Confirmed conversational reply identity differs from its journal",
                service="x",
            )
        confirmation_epoch = receipt_values.observed_confirmation_epoch(
            receipt_template,
            transport_confirmation.confirmation_epoch,
        )
        receipt = receipt_values.confirmed_from_sending(
            receipt_template,
            reply_post_id=own_reply_id,
            confirmation_epoch=confirmation_epoch,
        )
    except BaseException as identity_error:
        # create_post has already left the exact sending receipt and confirmed
        # transport journal durable.  Restore controlled-stop handling without
        # permitting an automatic retry of this remote outcome.
        end_confirmed_post_sigint_deferral(sigint_guard)
        if not isinstance(identity_error, Exception):
            raise
        raise AmbiguousRemotePostOutcome(
            "Conversational reply returned from transport but its confirmed "
            "identity could not be derived safely; durable barriers remain",
            service="x",
        ) from identity_error
    try:
        if not receipt_values.confirmed_is_valid(receipt):
            raise RuntimeError(
                "Internal error: confirmed reply representation failed validation"
            )
        receipt = receipts().promote(
            receipt_template,
            reply_post_id=own_reply_id,
            confirmation_epoch=confirmation_epoch,
        )
    except BaseException as receipt_error:
        fallback_error: BaseException | None = None
        fallback_complete = False
        try:
            if not receipt_values.confirmed_is_valid(receipt):
                raise InvalidConfirmedReplyReceipt(
                    "Refusing to apply an invalid confirmed reply representation"
                )
            completion.apply_state(state, receipt)
            try:
                commit_proof = completion.commit(state, receipt_template)
            except StateBackupWriteError as exc:
                commit_proof = getattr(exc, "commit_proof", None)
                if commit_proof is None or not json_file_matches(STATE_FILE, state, commit_proof=commit_proof):
                    raise
                log.warning(
                    "Confirmed conversational reply canonical state was committed, "
                    "but its latest backup write failed; using canonical state as "
                    "the durable replay barrier",
                    exc_info=True,
                )
            fallback_complete = bool(
                confirmed_reply_emergency_representation_is_complete(
                    receipt,
                    state,
                )
                and json_file_matches(STATE_FILE, state, commit_proof=commit_proof)
            )
        except BaseException as exc:
            fallback_error = exc
            log.critical(
                "Confirmed conversational reply id=%s target=%s lost its receipt "
                "and emergency state save",
                own_reply_id,
                reply_to_id,
                exc_info=True,
            )

        if not fallback_complete:
            durable_marker_written = latch_confirmed_post_persistence_failure(
                lane=lane,
                post_id=own_reply_id,
                failure_components=[
                    "confirmed_reply_receipt",
                    "reply_state",
                ],
            )
            status, current_receipt = receipts().load()
            if (
                durable_marker_written
                or status == "invalid"
                or (status == "sending" and current_receipt == receipt_template)
            ):
                end_confirmed_post_sigint_deferral(sigint_guard)
            else:
                retain_sigint_deferral_without_durable_barrier(
                    lane=lane,
                    guard=sigint_guard,
                )
            raise UnrecoverableConfirmedReplyPersistenceError(
                f"Confirmed conversational reply {own_reply_id} to "
                f"{reply_to_id} has no complete durable recovery representation"
            ) from (fallback_error or receipt_error)

        try:
            completion.retire(
                receipt_template, post_id=own_reply_id,
                sending_disposition="confirmed_state_fallback", commit_proof=commit_proof,
            )
        except Exception as removal_error:
            end_confirmed_post_sigint_deferral(sigint_guard)
            raise ConfirmedReplyLocalPersistenceError(
                f"Confirmed conversational reply {own_reply_id} to {reply_to_id} "
                "was preserved in canonical state but its sending receipt remains"
            ) from removal_error
        end_confirmed_post_sigint_deferral(sigint_guard)
        if not isinstance(receipt_error, Exception):
            raise
        raise ConfirmedReplyLocalPersistenceError(
            f"Confirmed conversational reply {own_reply_id} to {reply_to_id} "
            "but failed writing its recovery receipt"
        ) from receipt_error

    end_confirmed_post_sigint_deferral(sigint_guard)
    return response, receipt
