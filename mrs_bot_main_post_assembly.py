"""Assemble current main-post values, storage, publication and recovery owners.

The application supplies settings and its shared safety, state and transport
boundaries. This module alone connects the specialised main-post collaborators.
Construction performs no I/O, clock read or provider operation. A current
supplier is used only at boundaries which historically rebound runtime policy.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mrs_bot_main_post_attempt_values import (
    bound_meme_schedule_state as capture_bound_meme_schedule_state,
    bound_meme_schedule_state_is_valid,
    build_main_post_attempt,
    confirmation_epoch_for_main_attempt,
)
from mrs_bot_main_post_confirmation_persistence import (
    emergency_persist_confirmed_regular_post,
    promote_main_post_attempt_to_confirmed_pending_schedule,
    save_regular_post_protected_state,
)
from mrs_bot_post_creation import handoff_confirmed_media_upload_to_main_attempt
from mrs_bot_transport_source_preparation import (
    prepare_main_tweet_transport,
    validate_confirmed_media_upload_metadata,
)
from mrs_bot_transaction_recovery import ensure_reconciled_regular_receipt_schedule_is_future
from mrs_bot_main_post_publication import MainPostPublication
from mrs_bot_main_post_receipt_storage import MainPostReceipts
from mrs_bot_main_post_receipts import MainPostReceiptValues
from mrs_bot_main_post_reconciliation import (
    MainPostContextObligations,
    MainPostRecovery,
    MainPostRecoveryPersistence,
    MainPostRecoveryPolicy,
)
from mrs_bot_daily_meme import (
    DailyMemeRunner, MemeCatalog, MemeSchedule,
    require_valid_meme_post_id, run_daily_meme_stage,
)
from mrs_bot_quote_posting import QuotePostRunner
from mrs_bot_receipt_primitives import valid_post_id

if TYPE_CHECKING:
    from mrs_bot_core_contracts import (
        AttemptingMainPostAttempt, MainPostAttempt, MainPostLane,
        PendingMainPostReceipt, SendingMainPostAttempt,
    )
    from mrsMThatcher2 import (
        ApiError, ConfirmedPendingScheduleDurabilityUncertain,
        NoViableQuoteImagePair,
    )
    from remote_write_transport_journal import SourceReceiptBinding, TransportAuthority
    from mrs_bot_daily_meme import MemeCatalog, MemeSchedule
    from mrs_bot_image_selection import ImageSelection
    from mrs_bot_tweet_lookup_cache import TweetLookupCache


@dataclass(frozen=True)
class MainPostPolicy:
    """Bound schedule, catalog and receipt settings for one assembly."""

    schedule_timezone: str
    schedule_modes: set[str]
    schedule_version: int
    meme_text: str
    user_id: str
    regular_receipt_file: Path
    meme_receipt_file: Path
    confirmed_reply_receipt_file: Path
    media_upload_receipt_file: Path
    images_used_file: Path
    lines_used_file: Path
    transport_source_validator_id: str
    state_file: Path
    meme_dir: Path
    reset_meme_cycle: bool
    enable_daily_memes: bool
    meme_trigger_after_hour: int
    meme_fallback_hour: int
    meme_fallback_minute: int
    meme_delay_minimum: int
    meme_delay_maximum: int
    quote_delay_minimum: int
    quote_delay_maximum: int


@dataclass(frozen=True)
class MainPostErrors:
    """Canonical exception identities used by durable main-post operations."""

    invalid_regular: type[Exception]
    invalid_meme: type[Exception]
    unresolved_regular: type[Exception]
    unresolved_meme: type[Exception]
    invalid_confirmed_reply: type[Exception]
    ambiguous_outcome: type[ApiError]
    confirmed_pending_uncertain: type[ConfirmedPendingScheduleDurabilityUncertain]
    confirmed_local_failure: type[Exception]
    unrecoverable_confirmed: type[Exception]
    state_backup_failure: type[Exception]
    transport_journal_failure: type[Exception]
    bound_source_transition_failure: type[Exception]
    corrupt_used_history: type[Exception]
    no_viable_quote_pair: type[NoViableQuoteImagePair]
    media_upload_receipt_error: type[Exception]


@dataclass(frozen=True)
class MainPostReceiptIO:
    """Shared durable receipt primitives and proof-authorised retirement."""

    durable_create: Callable
    namespace_entry_exists: Callable
    retirement_is_blocking: Callable
    replace_exact_document: Callable
    mutation_authority: Callable
    load_no_follow: Callable
    atomic_write_json: Callable
    retire_current_source: Callable
    replace_bound_source: Callable
    fsync_parent_dir: Callable
    atomic_json_matches: Callable


@dataclass(frozen=True)
class MainPostTransport:
    """The application's sealed transport and interrupt authority."""

    begin_transport_transaction: Callable
    bind_lane_transport_source: Callable
    bind_media_handoff_to_transport: Callable
    confirmed_media_upload_type: type
    inspect_media_upload_receipt: Callable
    load_confirmed_media_upload: Callable
    retire_confirmed_media_upload: Callable
    begin_sigint: Callable
    create_post: Callable
    proves_non_success: Callable
    end_sigint: Callable
    incident_latched: Callable
    durable_barrier_exists: Callable
    retain_sigint: Callable
    inspect_confirmation: Callable
    journal_path: Callable
    bind_confirmed_source: Callable
    source_semantic_validator: Callable
    set_ambiguous_seen: Callable
    upload_media: Callable
    block_if_ambiguous: Callable
    latch_confirmed_failure: Callable


@dataclass(frozen=True)
class MainPostApplication:
    """Shared state, observability, and optional-context application services."""

    log: Logger
    log_event: Callable
    emit_account_root_posted: Callable
    now_epoch: Callable[[], int]
    safe_bound_schedule_date: Callable
    valid_receipt_epoch: Callable
    bound_schedule_datetime: Callable
    save_state: Callable
    save_image_used_basenames: Callable
    save_quote_used_hashes: Callable
    json_file_matches: Callable
    retire_transport_journal: Callable
    verify_transport_lineage: Callable
    quote_schedule: Callable
    require_context_outbox_writable: Callable
    enqueue_context: Callable
    process_due_context: Callable
    load_meme_analysis_index: Callable
    selection: Callable[[], ImageSelection]
    tweets: Callable[[], TweetLookupCache]


class MainPostAssembly:
    """Build one operation's cohesive main-post graph at its binding point."""

    def __init__(
        self,
        *,
        policy: MainPostPolicy,
        errors: MainPostErrors,
        receipt_io: MainPostReceiptIO,
        transport: MainPostTransport,
        application: MainPostApplication,
        current: Callable[[], MainPostAssembly],
    ) -> None:
        """Retain current settings and explicit later-binding authority."""
        self.policy = policy
        self.errors = errors
        self.receipt_io = receipt_io
        self.transport = transport
        self.application = application
        self.current = current

    def values(self) -> MainPostReceiptValues:
        """Construct receipt values with their original refresh semantics."""
        p, e, a = self.policy, self.errors, self.application
        return MainPostReceiptValues(
            schedule_timezone=p.schedule_timezone,
            schedule_modes=p.schedule_modes,
            schedule_version=p.schedule_version,
            bound_meme_state_is_valid=self.bound_meme_state_is_valid,
            safe_schedule_date=a.safe_bound_schedule_date,
            valid_epoch=a.valid_receipt_epoch,
            invalid_regular_receipt=e.invalid_regular,
            invalid_meme_receipt=e.invalid_meme,
            bound_schedule_datetime=a.bound_schedule_datetime,
            current=lambda: self.current().values(),
        )

    def bound_meme_state_is_valid(
        self, value: object, *, schedule_timezone: str | None = None,
    ) -> bool:
        """Validate a bound plan using the policy current at this boundary."""
        p, a = self.policy, self.application
        return bound_meme_schedule_state_is_valid(
            value,
            schedule_timezone=schedule_timezone,
            MAIN_POST_SCHEDULE_TIMEZONE=p.schedule_timezone,
            MEME_SCHEDULE_MODES=p.schedule_modes,
            MEME_SCHEDULE_VERSION=p.schedule_version,
            safe_bound_schedule_date_str=a.safe_bound_schedule_date,
            valid_receipt_epoch=a.valid_receipt_epoch,
        )

    def receipts(self, *, values: MainPostReceiptValues | None = None) -> MainPostReceipts:
        """Construct storage without loading a receipt or beginning a transaction."""
        p, e, io, a = self.policy, self.errors, self.receipt_io, self.application
        value_factory = values.current if values is not None else lambda: self.current().values()
        return MainPostReceipts(
            MEME_POST_RECEIPT_FILE=p.meme_receipt_file,
            REGULAR_POST_RECEIPT_FILE=p.regular_receipt_file,
            CONFIRMED_REPLY_RECEIPT_FILE=p.confirmed_reply_receipt_file,
            InvalidConfirmedReplyReceipt=e.invalid_confirmed_reply,
            UnresolvedRegularPostReceipt=e.unresolved_regular,
            durable_create_receipt_json=io.durable_create,
            log=a.log,
            receipt_namespace_entry_exists=io.namespace_entry_exists,
            remote_receipt_retirement_is_blocking=io.retirement_is_blocking,
            AmbiguousRemotePostOutcome=e.ambiguous_outcome,
            replace_exact_source_receipt_document=io.replace_exact_document,
            transaction_mutation_authority=io.mutation_authority,
            load_receipt_json_no_follow=io.load_no_follow,
            UnresolvedMemePostReceipt=e.unresolved_meme,
            atomic_write_json=io.atomic_write_json,
            values=value_factory,
            current=lambda: self.current().receipts(),
        )

    def build_attempt(
        self, *, lane: str, text: str, media_ids: list[str], made_with_ai: bool,
        selected_identity: dict[str, object], recovery_plan: dict[str, object],
        attempt_epoch: int | None = None,
    ) -> SendingMainPostAttempt:
        """Build a pre-send attempt using settings bound at this call."""
        return build_main_post_attempt(
            lane=lane, text=text, media_ids=media_ids, made_with_ai=made_with_ai,
            selected_identity=selected_identity, recovery_plan=recovery_plan,
            attempt_epoch=attempt_epoch,
            MAIN_POST_SCHEDULE_TIMEZONE=self.policy.schedule_timezone,
            current_main_post_attempt_is_semantically_valid=self.values().current_attempt_is_valid,
            now_epoch=self.application.now_epoch,
            os=os,
        )

    def confirmation_epoch(self, attempt: dict, observed_epoch: int) -> int:
        """Keep a confirmed epoch at or after its durable attempt epoch."""
        return confirmation_epoch_for_main_attempt(
            attempt, observed_epoch, log=self.application.log,
        )

    def bound_meme_state(self, state: dict, *, schedule_timezone: str | None = None) -> dict:
        """Capture the current meme schedule in an exact pre-send plan."""
        return capture_bound_meme_schedule_state(
            state,
            schedule_timezone=schedule_timezone,
            MAIN_POST_SCHEDULE_TIMEZONE=self.policy.schedule_timezone,
            MEME_SCHEDULE_VERSION=self.policy.schedule_version,
            safe_bound_schedule_date_str=self.application.safe_bound_schedule_date,
        )

    def prepare_transport(
        self, attempt: SendingMainPostAttempt,
    ) -> tuple[AttemptingMainPostAttempt, SourceReceiptBinding, TransportAuthority]:
        """Promote the attempt and bind its exact sealed X transport source."""
        values = self.values()
        return prepare_main_tweet_transport(
            attempt,
            receipts=self.receipts(values=values),
            receipt_values=values,
            TransportJournalError=self.errors.transport_journal_failure,
            begin_transport_transaction=self.transport.begin_transport_transaction,
            bind_lane_transport_source=self.transport.bind_lane_transport_source,
        )

    def handoff_media(
        self, attempt: AttemptingMainPostAttempt,
        transport_authority: TransportAuthority,
    ) -> None:
        """Retire confirmed media only beneath its prepared tweet authority."""
        t, p, e = self.transport, self.policy, self.errors

        def validate(confirmation: object) -> None:
            validate_confirmed_media_upload_metadata(
                confirmation,
                ConfirmedMediaUpload=t.confirmed_media_upload_type,
                MediaUploadReceiptError=e.media_upload_receipt_error,
                inspect_media_upload_receipt=t.inspect_media_upload_receipt,
            )

        return handoff_confirmed_media_upload_to_main_attempt(
            attempt, transport_authority,
            receipts=self.receipts(),
            MEDIA_UPLOAD_RECEIPT_FILE=p.media_upload_receipt_file,
            MediaUploadReceiptError=e.media_upload_receipt_error,
            bind_media_handoff_to_transport=t.bind_media_handoff_to_transport,
            validate_confirmed_media_upload_metadata=validate,
            load_confirmed_media_upload=t.load_confirmed_media_upload,
            log=self.application.log,
            retire_confirmed_media_upload=t.retire_confirmed_media_upload,
            transaction_mutation_authority=self.receipt_io.mutation_authority,
        )

    def save_regular_protected_state(
        self, lines_used: set, images_used: set, state: dict, *, durable: bool,
    ):
        """Save regular-post histories and canonical state in their existing order."""
        return save_regular_post_protected_state(
            lines_used, images_used, state, durable=durable,
            IMAGES_USED_FILE=self.policy.images_used_file,
            LINES_USED_FILE=self.policy.lines_used_file,
            save_image_used_basenames=self.application.save_image_used_basenames,
            save_quote_used_hashes=self.application.save_quote_used_hashes,
            save_state=self.application.save_state,
        )

    def emergency_regular(self, lines_used: set, images_used: set, state: dict):
        """Persist an emergency regular-post representation after confirmation."""
        p, e, a = self.policy, self.errors, self.application
        return emergency_persist_confirmed_regular_post(
            lines_used, images_used, state,
            IMAGES_USED_FILE=p.images_used_file,
            LINES_USED_FILE=p.lines_used_file,
            STATE_FILE=p.state_file,
            StateBackupWriteError=e.state_backup_failure,
            json_file_matches=a.json_file_matches,
            log=a.log,
            save_image_used_basenames=a.save_image_used_basenames,
            save_quote_used_hashes=a.save_quote_used_hashes,
            save_state=a.save_state,
        )

    def ensure_regular_schedule_future(
        self, receipt: dict, state: dict, current: int,
    ) -> bool:
        """Delay a due quote schedule before retiring a replayed receipt."""
        return ensure_reconciled_regular_receipt_schedule_is_future(
            receipt, state, current,
            log=self.application.log,
            quote_schedule=self.application.quote_schedule(),
        )

    def remove_attempt(self, attempt: Mapping[str, Any], *, sending_disposition: str, commit_proof=None) -> None:
        """Retire exactly one attempt under its required proof or disposition."""
        if sending_disposition == "confirmed_state_fallback":
            from mrs_bot_state_generation import require_commit_proof
            require_commit_proof(commit_proof)
            commit_proof.require_receipt(attempt)
        return self.receipts().remove_attempt(
            attempt,
            sending_disposition=sending_disposition,
            retire_current_source_receipt=functools.partial(
                self.receipt_io.retire_current_source,
                commit_proof=commit_proof,
                **({"disposition": "definite_non_success"}
                   if sending_disposition == "definite_non_success" else {}),
            ),
        )

    def remove_regular(self, receipt: dict, *, commit_proof=None) -> None:
        """Require the exact commit proof before removing a regular receipt."""
        from mrs_bot_state_generation import require_commit_proof
        require_commit_proof(commit_proof)
        commit_proof.require_receipt(receipt)
        return self.receipts().remove_regular(
            receipt,
            retire_current_source_receipt=functools.partial(
                self.receipt_io.retire_current_source, commit_proof=commit_proof,
            ),
        )

    def remove_meme(self, receipt: dict, *, commit_proof=None) -> None:
        """Require the exact commit proof before removing a meme receipt."""
        from mrs_bot_state_generation import require_commit_proof
        require_commit_proof(commit_proof)
        commit_proof.require_receipt(receipt)
        return self.receipts().remove_meme(
            receipt,
            retire_current_source_receipt=functools.partial(
                self.receipt_io.retire_current_source, commit_proof=commit_proof,
            ),
        )

    def promote_pending(
        self, attempt: AttemptingMainPostAttempt, *, post_id: str, confirmation_epoch: int,
        image_summary: str = "",
    ) -> PendingMainPostReceipt:
        """Bind a confirmed remote identity before fallible schedule work."""
        values = self.values()
        p, e, io, t, a = (
            self.policy, self.errors, self.receipt_io, self.transport, self.application,
        )
        return promote_main_post_attempt_to_confirmed_pending_schedule(
            attempt,
            post_id=post_id,
            confirmation_epoch=confirmation_epoch,
            image_summary=image_summary,
            receipts=self.receipts(values=values),
            receipt_values=values,
            AmbiguousRemotePostOutcome=e.ambiguous_outcome,
            BoundSourceReceiptTransitionError=e.bound_source_transition_failure,
            ConfirmedPendingScheduleDurabilityUncertain=e.confirmed_pending_uncertain,
            REGULAR_POST_RECEIPT_FILE=p.regular_receipt_file,
            TRANSPORT_SOURCE_VALIDATOR_ID=p.transport_source_validator_id,
            TransportJournalError=e.transport_journal_failure,
            _set_ambiguous_remote_post_seen=t.set_ambiguous_seen,
            atomic_json_file_exactly_matches=io.atomic_json_matches,
            bind_confirmed_transport_source=t.bind_confirmed_source,
            fsync_parent_dir=io.fsync_parent_dir,
            journal_path_for_receipt=t.journal_path,
            latch_confirmed_post_persistence_failure=t.latch_confirmed_failure,
            log=a.log,
            remote_write_safety_incident_is_latched=t.incident_latched,
            replace_bound_source_receipt=io.replace_bound_source,
            transaction_mutation_authority=io.mutation_authority,
            transport_source_semantic_validator=t.source_semantic_validator,
        )

    def publication(
        self, lane: MainPostLane, *, receipts: MainPostReceipts, values: MainPostReceiptValues,
    ) -> MainPostPublication:
        """Bind one publication and its partial progress for one transaction."""
        p, e, t, a = self.policy, self.errors, self.transport, self.application

        def prepare_transport(
            attempt: SendingMainPostAttempt,
        ) -> tuple[AttemptingMainPostAttempt, SourceReceiptBinding, TransportAuthority]:
            return self.current().prepare_transport(attempt)

        def handoff_media(
            attempt: AttemptingMainPostAttempt, authority: TransportAuthority,
        ) -> None:
            self.current().handoff_media(attempt, authority)

        def retire_attempt(
            attempt: MainPostAttempt, *, sending_disposition: str,
        ) -> None:
            self.current().remove_attempt(
                attempt, sending_disposition=sending_disposition,
            )

        def promote_pending(
            attempt: AttemptingMainPostAttempt, *, post_id: str,
            confirmation_epoch: int, image_summary: str = "",
        ) -> PendingMainPostReceipt:
            return self.current().promote_pending(
                attempt, post_id=post_id, confirmation_epoch=confirmation_epoch,
                image_summary=image_summary,
            )

        return MainPostPublication(
            lane=lane,
            receipt_path=p.regular_receipt_file if lane == "quote_image" else p.meme_receipt_file,
            log=a.log,
            receipts=receipts,
            receipt_values=values,
            prepare_transport=prepare_transport,
            handoff_media=handoff_media,
            begin_sigint=t.begin_sigint,
            create_post=t.create_post,
            proves_non_success=t.proves_non_success,
            retire_attempt=retire_attempt,
            end_sigint=t.end_sigint,
            ambiguous_outcome=e.ambiguous_outcome,
            incident_latched=t.incident_latched,
            durable_barrier_exists=t.durable_barrier_exists,
            retain_sigint=t.retain_sigint,
            inspect_confirmation=t.inspect_confirmation,
            journal_path=t.journal_path,
            promote_pending=promote_pending,
            run_stage=(
                (lambda stage, operation: run_daily_meme_stage(
                    stage, operation, log=a.log, log_event=a.log_event,
                ))
                if lane == "daily_meme" else None
            ),
            validate_meme_post_id=(
                (lambda posted_id: require_valid_meme_post_id(posted_id, valid_post_id=valid_post_id))
                if lane == "daily_meme" else None
            ),
        )

    def recovery(
        self, *, receipts: MainPostReceipts, values: MainPostReceiptValues,
        tweets: TweetLookupCache,
    ) -> MainPostRecovery:
        """Bind local receipt application and replay to the same operation."""
        p, e, a = self.policy, self.errors, self.application
        return MainPostRecovery(
            receipts=receipts,
            values=values,
            tweets=tweets,
            meme_schedule=lambda: self.current().meme_schedule(),
            policy=MainPostRecoveryPolicy(
                meme_text=p.meme_text,
                meme_schedule_version=p.schedule_version,
                user_id=p.user_id,
                regular_receipt_file=p.regular_receipt_file,
                meme_receipt_file=p.meme_receipt_file,
                invalid_regular_receipt=e.invalid_regular,
                invalid_meme_receipt=e.invalid_meme,
                unresolved_regular_receipt=e.unresolved_regular,
            ),
            persistence=MainPostRecoveryPersistence(
                save_state=a.save_state,
                save_regular_protected_state=lambda *args, **kwargs: self.current().save_regular_protected_state(*args, **kwargs),
                emergency_regular=lambda *args, **kwargs: self.current().emergency_regular(*args, **kwargs),
                remove_regular_receipt=lambda *args, **kwargs: self.current().remove_regular(*args, **kwargs),
                remove_meme_receipt=lambda *args, **kwargs: self.current().remove_meme(*args, **kwargs),
                retire_transport_journal=a.retire_transport_journal,
                verify_transport_lineage=a.verify_transport_lineage,
                ensure_regular_schedule_future=lambda *args, **kwargs: self.current().ensure_regular_schedule_future(*args, **kwargs),
            ),
            context=MainPostContextObligations(
                enqueue=a.enqueue_context,
                process_due=a.process_due_context,
            ),
            log=a.log,
            emit_account_root_posted=a.emit_account_root_posted,
            both_receipts_exist=lambda: self.current().both_receipts_exist(),
            valid_receipt_epoch=a.valid_receipt_epoch,
            safe_bound_schedule_date=a.safe_bound_schedule_date,
        )

    def both_receipts_exist(self) -> bool:
        """Inspect both current main-post receipt namespace entries."""
        return self.receipt_io.namespace_entry_exists(self.policy.regular_receipt_file) and (
            self.receipt_io.namespace_entry_exists(self.policy.meme_receipt_file)
        )

    def meme_catalog(self) -> MemeCatalog:
        """Bind catalog policy for one meme operation without scanning files."""
        return MemeCatalog(
            log=self.application.log,
            directory=self.policy.meme_dir,
            reset_when_exhausted=self.policy.reset_meme_cycle,
            save_state=self.application.save_state,
        )

    def meme_schedule(self) -> MemeSchedule:
        """Bind current meme calendar and state persistence operations."""
        p, a = self.policy, self.application
        return MemeSchedule(
            bound_datetime=a.bound_schedule_datetime,
            timezone=p.schedule_timezone,
            now_epoch=a.now_epoch,
            fallback_hour=p.meme_fallback_hour,
            fallback_minute=p.meme_fallback_minute,
            version=p.schedule_version,
            modes=p.schedule_modes,
            enabled=p.enable_daily_memes,
            trigger_hour=p.meme_trigger_after_hour,
            minimum_delay=p.meme_delay_minimum,
            maximum_delay=p.meme_delay_maximum,
            save_state=a.save_state,
            log=a.log,
        )

    def quote_runner(self) -> QuotePostRunner:
        """Bind one ordinary quotation transaction and its replay collaborators."""
        selection = self.application.selection()
        values = self.values()
        receipts = self.receipts(values=values)
        tweets = self.application.tweets()
        publication = self.publication("quote_image", receipts=receipts, values=values)
        return QuotePostRunner(
            publication=publication,
            receipts=receipts,
            receipt_values=values,
            tweets=tweets,
            selection=selection,
            recovery=self.recovery(receipts=receipts, values=values, tweets=tweets),
            policy=self.policy,
            errors=self.errors,
            transport=self.transport,
            application=self.application,
            build_attempt=lambda **kwargs: self.current().build_attempt(**kwargs),
            bound_meme_state=lambda *args, **kwargs: self.current().bound_meme_state(*args, **kwargs),
            remove_attempt=lambda *args, **kwargs: self.current().remove_attempt(*args, **kwargs),
        )

    def recovery_operation(self) -> MainPostRecovery:
        """Bind fresh replay collaborators for startup or a root API request."""
        values = self.values()
        receipts = self.receipts(values=values)
        tweets = self.application.tweets()
        return self.recovery(receipts=receipts, values=values, tweets=tweets)

    def meme_runner(self) -> DailyMemeRunner:
        """Bind one daily meme transaction and its replay collaborators."""
        values = self.values()
        receipts = self.receipts(values=values)
        tweets = self.application.tweets()
        publication = self.publication("daily_meme", receipts=receipts, values=values)
        catalog = self.meme_catalog()
        schedule = self.meme_schedule()
        return DailyMemeRunner(
            publication=publication,
            receipts=receipts,
            receipt_values=values,
            tweets=tweets,
            catalog=catalog,
            schedule=schedule,
            recovery=self.recovery(receipts=receipts, values=values, tweets=tweets),
            policy=self.policy,
            errors=self.errors,
            transport=self.transport,
            application=self.application,
            build_attempt=lambda **kwargs: self.current().build_attempt(**kwargs),
            remove_attempt=lambda *args, **kwargs: self.current().remove_attempt(*args, **kwargs),
        )
