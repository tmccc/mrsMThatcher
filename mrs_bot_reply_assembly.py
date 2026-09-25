"""Construct reply-lane collaborators at their original operation boundaries.

Construction binds dependencies without reading state, clocks, files or providers.
"""

from __future__ import annotations
import functools
from dataclasses import dataclass
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime as DateTime
from pathlib import Path
from types import ModuleType
import logging
from typing import TYPE_CHECKING, Any, Protocol
import mrs_bot_reply_drafts as _reply_drafts
import mrs_bot_reply_history as _reply_history
import mrs_bot_reply_generation as _reply_generation
import mrs_bot_reply_model_transport as _reply_model_transport
import mrs_bot_reply_receipt_values as _reply_receipt_values
import mrs_bot_reply_delivery as _reply_delivery
import mrs_bot_reply_reconciliation as _reply_reconciliation
import mrs_bot_reply_cycle_interfaces as _reply_cycle_interfaces
import mrs_bot_normal_reply_cycle as _normal_reply_cycle
import mrs_bot_quote_reply_cycle as _quote_reply_cycle
import mrs_bot_receipt_primitives as _receipt_primitives
import mrs_bot_api_cooldowns as _api_cooldowns
import mrs_bot_mention_authority as _mention_authority
import mrs_bot_mention_discovery as _mention_discovery
import mrs_bot_tweet_lookup_cache as _tweet_lookup_cache
import mrs_bot_transport_source_preparation as _transport_source_preparation
import mrs_bot_observability as _observability
import mrs_bot_reply_lane_policy as _reply_lane_policy
import mrs_bot_legacy_reply_validation as _legacy_reply_validation

import mrs_bot_reply_evaluation_state as _reply_evaluation_state
import mrs_bot_author_quarantines as _author_quarantines
import mrs_bot_daily_reply_accounting as _daily_reply_accounting
import mrs_bot_reply_clarifications as _reply_clarifications
import mrs_bot_reply_context as _reply_context
import mrs_bot_reply_native_media as _reply_native_media
import mrs_bot_hot_post_discovery as _hot_post_discovery
import mrs_bot_quote_discovery as _quote_discovery
import mrs_bot_runtime_control as _runtime_control

class PipelineModule(Protocol):
    """Lazily loaded pure pipeline functions used in production assembly."""

    def run_reply_pipeline(
        self, *, context: Mapping[str, Any], config: Mapping[str, Any],
        repository: EvidenceRepository, transport: ModelTransport,
        same_author_interactions: Sequence[Mapping[str, str]] = (),
        recent_account_replies: Sequence[object] = (),
        supplied_images: Sequence[Mapping[str, Any]] = (),
        visual_description: object = None,
    ) -> PipelineOutcome:
        """Return the one-call model decision."""
        ...

    def validate_persisted_draft(
        self, draft: object, *, context: Mapping[str, Any],
        repository: object, recent_account_replies: Sequence[object] = (),
    ) -> CurrentReplyDraft:
        """Validate one current draft before reuse."""
        ...

    def decision_telemetry(self, result: PipelineResult) -> dict[str, Any]:
        """Project bounded decision telemetry."""
        ...

    def quoted_post_reference_id(self, context: Mapping[str, Any]) -> str | None:
        """Read one canonical quoted post reference."""
        ...

    def bound_visible_conversation(
        self, turns: Sequence[Mapping[str, Any]], *, target_post_id: str,
    ) -> list[dict[str, str]]:
        """Bound a verified visible conversation."""
        ...


class RejectionProofModule(Protocol):
    """Sealed rejection proof operations loaded at retirement time."""

    def reply_create_rejection_payload(
        self, proof: object,
    ) -> dict[str, Any] | None:
        """Return the bounded rejected payload, when a proof is registered."""
        ...

    def claim_reply_create_rejection_for_receipt_retirement(
        self, proof: object, *, target_id: str, receipt_path: Path,
        receipt: Mapping[str, Any],
    ) -> bool:
        """Consume one exact transport-retired rejection proof."""
        ...


def _single_call_reply() -> PipelineModule:
    """Load the pure pipeline module only when an operation needs it."""
    import single_call_reply
    return single_call_reply


def _rejection_proofs() -> RejectionProofModule:
    """Load sealed proof authority at the invoked receipt boundary."""
    import x_api_error_semantics
    return x_api_error_semantics


if TYPE_CHECKING:
    from mrs_bot_core_contracts import ConfirmedReplyReceipt, CurrentReplyDraft, ReplyReceiptLoad
    from mrs_bot_reply_context import ReplyContext
    from mrsMThatcher2 import ApiError, ProvedRemotePostNonSuccess as ProvedNonSuccessValue
    from mrs_bot_state_generation import StateCommitProof
    from reply_evidence import EvidenceRepository
    from single_call_reply import ModelTransport, PipelineOutcome, PipelineResult, ValidatedReply


@dataclass(frozen=True)
class ReplyPolicy:
    """Current reply configuration, retaining mutable references."""

    QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS: int
    AI_REPLY_HISTORY_MAX_AGE_SECONDS: int
    AI_REPLY_HISTORY_MAX_RECORDS: int
    AI_REQUEST_RECORD_DIR: Path
    CONFIRMED_REPLY_RECEIPT_FILE: Path
    normal: _reply_cycle_interfaces.NormalReplyConfig
    quote: _reply_cycle_interfaces.QuoteReplyConfig
    MAX_MENTIONS_PER_CHECK: int
    MAX_QUOTE_POSTS_PER_CHECK: int
    MAX_REASONABLE_STATE_EPOCH: int
    MAX_RECENT_ACCOUNT_REPLIES: int
    MAX_SAME_AUTHOR_INTERACTIONS: int
    MY_USER_ID: str
    MY_USERNAME: str
    SPAMMY_PATTERNS: list[str]
    OPENAI_API_KEY: str
    OPENAI_BASE: str
    REPLY_INCOMING_MAX_CHARS: int
    SINGLE_CALL_MODEL: str
    SINGLE_CALL_REASONING_EFFORT: str
    SINGLE_CALL_STRATEGY_VERSION: str
    STATE_FILE: Path
    TRANSPORT_SOURCE_VALIDATOR_ID: str
    _DEFAULT_RECENT_ACCOUNT_REPLY_LIMIT: int
    single_call_reply: dict[str, object]
    ALWAYS_FETCH_PARENT_FOR_CONTEXT: bool
    AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY: str
    AUTHOR_NO_REPLY_QUARANTINE_SECONDS: int
    AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD: int
    AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS: int
    CLARIFICATION_REPLY_WINDOW_SECONDS: int
    ENABLE_HOT_POST_REPLY_CHECKS: bool
    EXTRA_QUOTE_WATCH_FILE: Path
    HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS: int
    HOT_POST_REPLY_SEARCH_API_MAX_RESULTS: int
    HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK: int
    HOT_POST_REPLY_USE_SINCE_ID: bool
    MAX_EXTRA_QUOTE_WATCH_POSTS: int
    MAX_HOT_POST_REPLIES_PER_CHECK: int
    MAX_REPLY_CONTEXT_PHOTOS: int
    MAX_SUPPLIED_IMAGES: int
    MAX_VISIBLE_TEXT_CHARACTERS: int
    MENTIONS_MAX_PAGES_PER_CHECK: int
    MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT: int
    QUOTE_LOOKUP_API_MAX_RESULTS: int
    QUOTE_LOOKUP_MAX_PAGES_PER_POST: int
    QUOTE_POST_LOOKBACK_MAIN_POSTS: int
    REPLY_EVALUATION_MAX_RECORDS: int
    REPLY_EVALUATION_MIN_RETENTION_SECONDS: int
    SINGLE_CALL_MAX_IMAGE_BYTES: int
    SKIP_REPLIES_TO_OWN_AUTO_REPLIES: bool
    TEST_MODE: bool
    THREAD_CONTEXT_MAX_DEPTH: int
    THREAD_CONTEXT_MAX_NETWORK_FETCHES: int
    _DEFAULT_REPLY_CONTEXT_POST_MAXIMUM_CHARS: int
    MAX_CONFIRMATION_EPOCH: int
    MAX_TRUSTED_FACTS: int
    MIN_CONFIRMATION_EPOCH: int
    QUOTE_REPEATED_CURSOR_SUPPRESSION_MAX_ENTRIES: int


@dataclass(frozen=True)
class ReplyErrors:
    """Canonical runtime types and exception identities."""

    AmbiguousRemotePostOutcome: type[ApiError]
    ApiError: type[ApiError]
    ConfirmedReplyLocalPersistenceError: type[Exception]
    ContextValidationError: type[Exception]
    InvalidConfirmedReplyReceipt: type[Exception]
    PipelineResult: type[PipelineResult]
    ProvedRemotePostNonSuccess: type[ProvedNonSuccessValue]
    RemoteOperationsPaused: type[Exception]
    ReplyEvidenceUnavailable: type[Exception]
    ReplyValidationError: type[Exception]
    StateBackupWriteError: type[Exception]
    TransportJournalError: type[Exception]
    UnrecoverableConfirmedReplyPersistenceError: type[Exception]
    UnresolvedSendingReplyReceipt: type[Exception]
    ValidatedReply: type[ValidatedReply]
    ReplyMediaTransientUnavailable: type[Exception]
    ReplyMediaUnavailable: type[Exception]
    _MentionBacklogContinuationLimit: type[Exception]


@dataclass(frozen=True)
class ReplyReceiptIO:
    """Shared durable receipt storage and exact source authority."""

    durable_create_receipt_json: Callable[[Path, object], None]
    json_file_matches: Callable[..., bool]
    load_receipt_json_no_follow: Callable[[Path], tuple[bool, object | None]]
    receipt_namespace_entry_exists: Callable[[Path], bool]
    remote_receipt_retirement_is_blocking: Callable[[], bool]
    replace_bound_source_receipt: Callable
    retire_current_source_receipt: Callable
    transaction_mutation_authority: Callable


@dataclass(frozen=True)
class ReplyTransport:
    """Sealed transport, signal and network authorities."""

    requests: ModuleType
    begin_confirmed_post_sigint_deferral: Callable
    bind_confirmed_transport_source: Callable
    create_post: Callable
    end_confirmed_post_sigint_deferral: Callable
    inspect_confirmed_transport_transaction: Callable
    journal_path_for_receipt: Callable
    latch_confirmed_post_persistence_failure: Callable
    retain_sigint_deferral_without_durable_barrier: Callable
    retire_lane_transport_journal_if_present: Callable
    verify_lane_transport_source_lineage_if_present: Callable
    request_timeout: Callable
    validate_supplied_images: Callable
    x_paginated_get: Callable
    x_quote_lookup_request: Callable
    x_request: Callable


@dataclass(frozen=True)
class ReplyApplication:
    """Shared state, clocks, cache, controls and observability."""

    datetime: type[DateTime]
    _api_cooldown_owner: Callable[[], _api_cooldowns.ApiCooldowns]
    _receipt_dates_owner: Callable[[], _receipt_primitives.ReceiptDates]
    _reply_remote_write_barrier: Callable
    _runtime_controls_owner: Callable[[], _runtime_control.RuntimeControls]
    _tweet_lookup_cache_owner: Callable[..., _tweet_lookup_cache.TweetLookupCache]
    api_error_is_permanent_target_failure: Callable[[Exception], bool]
    api_error_is_reply_not_allowed: Callable[[Exception], bool]
    log: logging.Logger
    log_event: Callable[..., None]
    monotonic: Callable[[], float]
    now_epoch: Callable[[], int]
    reply_evidence_repository: Callable[[], EvidenceRepository]
    report_bot_health_progress: Callable
    require_remote_operation_unpaused: Callable
    record_ambiguous_remote_post: Callable[[dict], None]
    main_post_attempt_binds_payload: Callable
    save_state: _reply_cycle_interfaces.SaveReplyState
    sleep: Callable[[float], None]
    api_error_is_invalid_pagination_cursor: Callable[[Exception], bool]
    current_utc_datetime: Callable[[], DateTime]
    log_json_debug: Callable


class ReplyAssembly:
    """Construct and coordinate normal, quote and send operations."""

    def __init__(
        self, *, policy: ReplyPolicy, errors: ReplyErrors,
        receipt_io: ReplyReceiptIO, transport: ReplyTransport,
        application: ReplyApplication, current: Callable[[], ReplyAssembly],
    ) -> None:
        """Retain one operation's policy and shared authorities."""
        self.policy = policy
        self.errors = errors
        self.receipt_io = receipt_io
        self.transport = transport
        self.application = application
        self.current = current

    def pipeline_enabled(self) -> bool:
        """Read the current reply strategy switch from the shared config reference."""
        return self.policy.single_call_reply.get("enabled") is True

    def reply_target_is_directly_eligible(self, tweet: dict[str, Any]) -> bool:
        """Check current account identity against one reply target."""
        return _reply_lane_policy.reply_target_is_directly_eligible(
            tweet, MY_USERNAME=self.policy.MY_USERNAME, MY_USER_ID=self.policy.MY_USER_ID,
        )

    def spam_or_not_worth_replying(self, text: str) -> bool:
        """Apply the current shared spam patterns to one candidate."""
        return _reply_lane_policy.is_probably_spam_or_not_worth_replying(
            text, SPAMMY_PATTERNS=self.policy.SPAMMY_PATTERNS,
            log=self.application.log,
        )

    def dedupe_candidates(self, mentions: list[dict[str, Any]], hot_posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge candidates under current diagnostics."""
        return _hot_post_discovery.dedupe_reply_candidates(
            mentions, hot_posts, log=self.application.log,
        )

    def posting_outcome(
        self, *, reply: str, status: str, lane: str, target_id: str,
        failure_reason: str, reply_post_id: str | None = None,
    ) -> None:
        """Emit bounded reply posting telemetry with the current event service."""
        return _reply_generation.log_ai_reply_posting_outcome(
            reply=reply, status=status, lane=lane, target_id=target_id,
            failure_reason=failure_reason,
            **({"reply_post_id": reply_post_id} if reply_post_id is not None else {}),
            log_event=self.application.log_event,
        )

    def log_validated_reply(
        self, *, target_description: str, target_id: str, reply: object,
    ) -> None:
        """Log only bounded metadata for a validated reply."""
        return _observability._log_validated_single_call_reply(
            target_description=target_description, target_id=target_id,
            reply=reply, log=self.application.log,
        )

    def mark_hot_post_reply_skipped(
        self, state: dict[str, Any], reply_id: str, *, reason: str = "unspecified",
        original_post_id: str | None = None, retryable: bool | None = None,
    ) -> None:
        """Record a hot-post skip through the reply discovery owner."""
        return _hot_post_discovery.mark_hot_post_reply_skipped(
            state, reply_id, reason=reason, original_post_id=original_post_id,
            retryable=retryable, log_event=self.application.log_event,
            now_epoch=self.application.now_epoch,
        )

    def maybe_mark_hot_post_reply_skipped(
        self, state: dict[str, Any], candidate: dict[str, Any], reason: str = "unspecified",
    ) -> None:
        """Record a skipped hot-post candidate through the same owner."""
        return _hot_post_discovery.maybe_mark_hot_post_reply_skipped(
            state, candidate, reason,
            mark_hot_post_reply_skipped=self.mark_hot_post_reply_skipped,
        )

    def _reply_draft_owner(
        self,
        *,
        history: _reply_history.ReplyHistory | None = None,
        generation: _reply_generation.ReplyGeneration | None = None,
    ) -> _reply_drafts.ReplyDrafts:
        """Compose current draft owners without acquiring evidence or caller state."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        if history is None:
            history = self._reply_history_owner()
        if generation is None:
            generation = self._reply_generation_owner(history=history)
        return _reply_drafts.ReplyDrafts(
            validate_persisted_draft=_single_call_reply().validate_persisted_draft,
            evidence_repository=a.reply_evidence_repository,
            history=history,
            generation=generation,
            log_event=a.log_event,
            log=a.log,
            strategy_version=p.SINGLE_CALL_STRATEGY_VERSION,
            model=p.SINGLE_CALL_MODEL,
            result_type=e.PipelineResult,
            reply_type=e.ValidatedReply,
            evidence_unavailable=e.ReplyEvidenceUnavailable,
            validation_error=e.ReplyValidationError,
        )

    def _reply_history_owner(self) -> _reply_history.ReplyHistory:
        """Bind current history dependencies without reading state or the clock."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        return _reply_history.ReplyHistory(
            now_epoch=a.now_epoch,
            quoted_post_reference_id=_single_call_reply().quoted_post_reference_id,
            maximum_state_epoch=p.MAX_REASONABLE_STATE_EPOCH,
            maximum_recent_replies=p.MAX_RECENT_ACCOUNT_REPLIES,
            default_recent_reply_limit=p._DEFAULT_RECENT_ACCOUNT_REPLY_LIMIT,
            maximum_same_author_interactions=p.MAX_SAME_AUTHOR_INTERACTIONS,
            maximum_age_seconds=p.AI_REPLY_HISTORY_MAX_AGE_SECONDS,
            maximum_records=p.AI_REPLY_HISTORY_MAX_RECORDS,
        )

    def _reply_model_transport_owner(
        self,
    ) -> _reply_model_transport.ReplyModelTransport:
        """Bind model transport without making a provider request."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        return _reply_model_transport.ReplyModelTransport(
            log=a.log,
            model=p.SINGLE_CALL_MODEL,
            reasoning_effort=p.SINGLE_CALL_REASONING_EFFORT,
            monotonic=a.monotonic,
            require_remote_operation_unpaused=a.require_remote_operation_unpaused,
            report_bot_health_progress=a.report_bot_health_progress,
            requests=t.requests,
            base_url=p.OPENAI_BASE,
            api_key=p.OPENAI_API_KEY,
            sleep=a.sleep,
            now_epoch=a.now_epoch,
            error_type=e.ApiError,
            request_record_directory=p.AI_REQUEST_RECORD_DIR,
            log_event=a.log_event,
        )

    def _reply_generation_owner(
        self,
        *,
        cooldowns: _api_cooldowns.ApiCooldowns | None = None,
        history: _reply_history.ReplyHistory | None = None,
    ) -> _reply_generation.ReplyGeneration:
        """Compose current generation owners without collecting evidence or media."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        if cooldowns is None:
            cooldowns = a._api_cooldown_owner()
        if history is None:
            history = self._reply_history_owner()
        return _reply_generation.ReplyGeneration(
            media=self._reply_media_owner(),
            remote_operations_paused=e.RemoteOperationsPaused,
            result_type=e.PipelineResult,
            log=a.log,
            history=history,
            require_remote_operation_unpaused=a.require_remote_operation_unpaused,
            run_pipeline=_single_call_reply().run_reply_pipeline,
            config=p.single_call_reply,
            evidence_repository=a.reply_evidence_repository,
            model_transport=self._reply_model_transport_owner(),
            cooldowns=cooldowns,
            reply_type=e.ValidatedReply,
            decision_telemetry=_single_call_reply().decision_telemetry,
            log_event=a.log_event,
            strategy_version=p.SINGLE_CALL_STRATEGY_VERSION,
        )

    def _reply_receipt_values_owner(
        self,
        *,
        dates: _receipt_primitives.ReceiptDates | None = None,
        drafts: _reply_drafts.ReplyDrafts | None = None,
    ) -> _reply_receipt_values.ReplyReceiptValues:
        """Bind current receipt value boundaries without reading the clock or state."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        if dates is None:
            dates = a._receipt_dates_owner()
        if drafts is None:
            drafts = self._reply_draft_owner()
        return _reply_receipt_values.ReplyReceiptValues(
            valid_receipt_epoch=functools.partial(_receipt_primitives.valid_receipt_epoch, MIN_CONFIRMATION_EPOCH=p.MIN_CONFIRMATION_EPOCH, MAX_CONFIRMATION_EPOCH=p.MAX_CONFIRMATION_EPOCH),
            dates=dates,
            legacy_draft_is_valid=functools.partial(
                _legacy_reply_validation._legacy_ai_reply_receipt_draft_is_valid,
                _legacy_single_sol_reply_draft_is_valid=functools.partial(
                    _legacy_reply_validation._legacy_single_sol_reply_draft_is_valid,
                    bound_visible_conversation=_single_call_reply().bound_visible_conversation,
                    MAX_TRUSTED_FACTS=p.MAX_TRUSTED_FACTS,
                    MAX_SUPPLIED_IMAGES=p.MAX_SUPPLIED_IMAGES,
                    SINGLE_CALL_MAX_IMAGE_BYTES=p.SINGLE_CALL_MAX_IMAGE_BYTES,
                ),
            ),
            drafts=drafts,
            now_epoch=a.now_epoch,
            log=a.log,
            invalid_receipt=e.InvalidConfirmedReplyReceipt,
        )

    def reply_receipts(
        self, *, values: _reply_receipt_values.ReplyReceiptValues | None = None
    ) -> _reply_delivery.ReplyReceipts:
        """Bind runtime receipt authorities for one load, publication or promotion."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        if values is None:
            values = self._reply_receipt_values_owner()
        return _reply_delivery.ReplyReceipts(
            path=p.CONFIRMED_REPLY_RECEIPT_FILE,
            read_json=io.load_receipt_json_no_follow,
            log=a.log,
            values=values,
            retirement_is_blocking=io.remote_receipt_retirement_is_blocking,
            invalid_receipt=e.InvalidConfirmedReplyReceipt,
            namespace_entry_exists=io.receipt_namespace_entry_exists,
            create_json=io.durable_create_receipt_json,
            unresolved_sending=e.UnresolvedSendingReplyReceipt,
            bind_confirmed_source=t.bind_confirmed_transport_source,
            journal_path=t.journal_path_for_receipt,
            validator_id=p.TRANSPORT_SOURCE_VALIDATOR_ID,
            transport_validator=functools.partial(
                _transport_source_preparation.transport_source_semantic_validator,
                main_post_attempt_binds_payload=a.main_post_attempt_binds_payload,
                sending_reply_receipt_is_semantically_valid=values.sending_is_valid,
            ),
            legacy_transport_validator=functools.partial(
                _transport_source_preparation._legacy_conversational_transport_source_semantic_validator,
                _legacy_sending_reply_receipt_is_semantically_valid=values.legacy_sending_is_valid,
            ),
            transport_journal_error=e.TransportJournalError,
            replace_bound_source=io.replace_bound_source_receipt,
            mutation_authority=io.transaction_mutation_authority,
            current_receipts=lambda: self.current().reply_receipts(),
            retire_current_source_receipt=io.retire_current_source_receipt,
            proved_non_success=e.ProvedRemotePostNonSuccess,
            reply_not_allowed=a.api_error_is_reply_not_allowed,
            rejection_payload=_rejection_proofs().reply_create_rejection_payload,
            claim_rejection=_rejection_proofs().claim_reply_create_rejection_for_receipt_retirement,
            record_ambiguous=a.record_ambiguous_remote_post,
            persistence_error=e.ConfirmedReplyLocalPersistenceError,
        )

    def emergency_representation_is_complete(self, receipt: dict[str, Any], state: dict[str, Any]) -> bool:
        """Check whether the current state durably represents a confirmed reply."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        drafts = self._reply_draft_owner()
        return _reply_reconciliation.confirmed_reply_emergency_representation_is_complete(
            receipt,
            state,
            receipt_values=self._reply_receipt_values_owner(drafts=drafts),
            InvalidConfirmedReplyReceipt=e.InvalidConfirmedReplyReceipt,
            receipt_int=_receipt_primitives.receipt_int,
            has_target_draft=drafts.has_target,
        )

    def observed_confirmation_epoch(
        self, receipt: dict[str, Any], confirmation_epoch: int | None = None
    ) -> int:
        """Resolve a recovered reply's confirmation time with current receipt rules."""
        return self._reply_receipt_values_owner().observed_confirmation_epoch(
            receipt, confirmation_epoch
        )

    def sending_receipt_is_valid(self, receipt: dict[str, Any], *, legacy: bool = False) -> bool:
        """Validate a current or historical sending receipt at this operation boundary."""
        values = self._reply_receipt_values_owner()
        return (
            values.legacy_sending_is_valid(receipt)
            if legacy else values.sending_is_valid(receipt)
        )

    def sending_receipt_from_confirmed(self, receipt: dict[str, Any]) -> dict[str, Any]:
        """Recover the exact sending value from a current confirmed receipt."""
        return self._reply_receipt_values_owner().sending_from_confirmed(receipt)

    def validate_transport_source(
        self, lane: str, receipt: dict[str, Any], payload: dict[str, Any], *, legacy: bool = False,
    ) -> bool:
        """Inspect a registered source using current reply validation rules."""
        values = self._reply_receipt_values_owner()
        if legacy:
            return _transport_source_preparation._legacy_conversational_transport_source_semantic_validator(
                lane, receipt, payload,
                _legacy_sending_reply_receipt_is_semantically_valid=values.legacy_sending_is_valid,
            )
        return _transport_source_preparation.transport_source_semantic_validator(
            lane, receipt, payload,
            main_post_attempt_binds_payload=self.application.main_post_attempt_binds_payload,
            sending_reply_receipt_is_semantically_valid=values.sending_is_valid,
        )

    def load_receipt(self) -> ReplyReceiptLoad:
        """Load one reply receipt with current nested validation settings."""
        return self.reply_receipts().load()

    def promote_receipt(
        self, sending: dict[str, Any], *, reply_post_id: str,
        confirmation_epoch: int, legacy_recovery: bool = False,
    ) -> dict[str, Any]:
        """Promote one exact current or recovered transport source."""
        return self.reply_receipts().promote(
            sending, reply_post_id=reply_post_id,
            confirmation_epoch=confirmation_epoch, legacy_recovery=legacy_recovery,
        )

    def reconcile_receipt(self, state: dict[str, Any]) -> bool:
        """Complete a saved confirmation under current state and receipt authority."""
        return self._reply_completion_owner().reconcile(state)

    def remove_receipt(
        self, receipt: dict[str, Any], *, sending_disposition: str | None = None,
        commit_proof: StateCommitProof | None = None,
    ) -> None:
        """Retire an exact receipt under the supplied commit authority."""
        return self.reply_receipts().remove(
            receipt, sending_disposition=sending_disposition,
            commit_proof=commit_proof,
        )

    def retire_rejected_receipt(self, receipt: dict[str, Any], error: Exception) -> None:
        """Retire a proved rejection after the caller made terminal state durable."""
        self.reply_receipts().retire_rejected(receipt, error)

    def load_extra_quote_watch_posts(self) -> list[str]:
        """Read optional quote watch IDs with current path and limits."""
        return self._quote_watch_posts_owner().load_extra()

    def _confirmed_reply_state_applier(
        self,
        *,
        dates: _receipt_primitives.ReceiptDates | None = None,
        drafts: _reply_drafts.ReplyDrafts | None = None,
        receipt_values: _reply_receipt_values.ReplyReceiptValues | None = None,
        mention_authority: _mention_authority.MentionAuthority | None = None,
        mention_queue: _mention_discovery.MentionQueue | None = None,
        tweets: _tweet_lookup_cache.TweetLookupCache | None = None,
        history: _reply_history.ReplyHistory | None = None,
    ) -> Callable[[dict[str, Any], dict[str, Any]], None]:
        """Compose current typed owners for one confirmed-state application."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        if dates is None:
            dates = a._receipt_dates_owner()
        if drafts is None:
            drafts = (
                receipt_values.drafts
                if receipt_values is not None
                else self._reply_draft_owner()
            )
        if receipt_values is None:
            receipt_values = self._reply_receipt_values_owner(
                dates=dates, drafts=drafts
            )
        if mention_authority is None:
            mention_authority = self.mention_authority()
        if mention_queue is None:
            mention_queue = self._mention_queue_owner(authority=mention_authority)
        if tweets is None:
            tweets = a._tweet_lookup_cache_owner()
        if history is None:
            history = self._reply_history_owner()
        return functools.partial(
            _reply_reconciliation.apply_confirmed_reply_receipt,
            receipt_values=receipt_values,
            mention_authority=mention_authority,
            mention_queue=mention_queue,
            STATE_FILE=p.STATE_FILE,
            InvalidConfirmedReplyReceipt=e.InvalidConfirmedReplyReceipt,
            dates=dates,
            accounting=self.daily_reply_accounting(dates=dates),
            log=a.log,
            drafts=drafts,
            tweets=tweets,
            MY_USER_ID=p.MY_USER_ID,
            datetime=a.datetime,
            history=history,
            clarifications=self.clarification_replies(),
            log_event=a.log_event,
        )

    def _reply_completion_owner(
        self,
        *,
        dates: _receipt_primitives.ReceiptDates | None = None,
        drafts: _reply_drafts.ReplyDrafts | None = None,
        receipt_values: _reply_receipt_values.ReplyReceiptValues | None = None,
        receipts: _reply_delivery.ReplyReceipts | None = None,
        tweets: _tweet_lookup_cache.TweetLookupCache | None = None,
        history: _reply_history.ReplyHistory | None = None,
    ) -> _reply_reconciliation.ReplyCompletion:
        """Bind current completion authorities without reading state or receipt files."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        if dates is None:
            dates = a._receipt_dates_owner()
        if drafts is None:
            drafts = (
                receipt_values.drafts
                if receipt_values is not None
                else self._reply_draft_owner()
            )
        if receipt_values is None:
            receipt_values = (
                receipts.values
                if receipts is not None
                else self._reply_receipt_values_owner(dates=dates, drafts=drafts)
            )
        if receipts is None:
            receipts = self.reply_receipts(values=receipt_values)
        return _reply_reconciliation.ReplyCompletion(
            receipt_path=p.CONFIRMED_REPLY_RECEIPT_FILE,
            persistence_error=e.ConfirmedReplyLocalPersistenceError,
            apply_state=self._confirmed_reply_state_applier(
                dates=dates,
                drafts=drafts,
                receipt_values=receipt_values,
                tweets=tweets,
                history=history,
            ),
            save_state=a.save_state,
            retire_journal=t.retire_lane_transport_journal_if_present,
            log=a.log,
            receipts=receipts,
            unresolved_sending_receipt=e.UnresolvedSendingReplyReceipt,
            invalid_receipt=e.InvalidConfirmedReplyReceipt,
            verify_lineage=t.verify_lane_transport_source_lineage_if_present,
        )

    def post_with_current_owners(
        self,
        *,
        state: dict[str, Any],
        receipt_template: dict[str, Any],
        reply_text: str,
        reply_to_id: str,
        made_with_ai: bool,
        lane: str,
    ) -> tuple[dict[str, Any], ConfirmedReplyReceipt]:
        """Refresh send-time settings and authorities before publication."""
        return self.current()._post_with_bound_owners(
            state=state, receipt_template=receipt_template, reply_text=reply_text,
            reply_to_id=reply_to_id, made_with_ai=made_with_ai, lane=lane,
        )

    def _post_with_bound_owners(
        self, *, state: dict[str, Any], receipt_template: dict[str, Any], reply_text: str,
        reply_to_id: str, made_with_ai: bool, lane: str,
    ) -> tuple[dict[str, Any], ConfirmedReplyReceipt]:
        """Compose one publication transaction from current owners."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        drafts = self._reply_draft_owner()
        receipt_values = self._reply_receipt_values_owner(drafts=drafts)
        receipts = self.reply_receipts(values=receipt_values)
        completion = self._reply_completion_owner(
            drafts=drafts, receipt_values=receipt_values, receipts=receipts
        )
        return _reply_delivery.post_conversational_reply_with_durable_identity(
            state=state,
            receipt_template=receipt_template,
            reply_text=reply_text,
            reply_to_id=reply_to_id,
            made_with_ai=made_with_ai,
            lane=lane,
            receipt_values=receipt_values,
            receipt_namespace_entry_exists=io.receipt_namespace_entry_exists,
            CONFIRMED_REPLY_RECEIPT_FILE=p.CONFIRMED_REPLY_RECEIPT_FILE,
            InvalidConfirmedReplyReceipt=e.InvalidConfirmedReplyReceipt,
            block_if_ambiguous_remote_post=a._reply_remote_write_barrier(
                receipts=receipts
            ),
            receipts=lambda: self.current().reply_receipts(),
            begin_confirmed_post_sigint_deferral=t.begin_confirmed_post_sigint_deferral,
            create_post=functools.partial(
                t.create_post,
                reply_receipt_validator=receipt_values.sending_is_valid,
                reply_receipts=receipts,
            ),
            AmbiguousRemotePostOutcome=e.AmbiguousRemotePostOutcome,
            end_confirmed_post_sigint_deferral=t.end_confirmed_post_sigint_deferral,
            cooldowns=a._api_cooldown_owner(),
            save_state=a.save_state,
            log=a.log,
            RemoteOperationsPaused=e.RemoteOperationsPaused,
            remove_confirmed_reply_receipt=receipts.remove,
            ConfirmedReplyLocalPersistenceError=e.ConfirmedReplyLocalPersistenceError,
            ProvedRemotePostNonSuccess=e.ProvedRemotePostNonSuccess,
            ApiError=e.ApiError,
            inspect_confirmed_transport_transaction=t.inspect_confirmed_transport_transaction,
            journal_path_for_receipt=t.journal_path_for_receipt,
            StateBackupWriteError=e.StateBackupWriteError,
            json_file_matches=io.json_file_matches,
            STATE_FILE=p.STATE_FILE,
            confirmed_reply_emergency_representation_is_complete=functools.partial(
                _reply_reconciliation.confirmed_reply_emergency_representation_is_complete,
                receipt_values=receipt_values,
                InvalidConfirmedReplyReceipt=e.InvalidConfirmedReplyReceipt,
                receipt_int=_receipt_primitives.receipt_int,
                has_target_draft=drafts.has_target,
            ),
            latch_confirmed_post_persistence_failure=t.latch_confirmed_post_persistence_failure,
            retain_sigint_deferral_without_durable_barrier=t.retain_sigint_deferral_without_durable_barrier,
            UnrecoverableConfirmedReplyPersistenceError=e.UnrecoverableConfirmedReplyPersistenceError,
            completion=completion,
        )

    def _reply_cycle_persistence(
        self, *, drafts: _reply_drafts.ReplyDrafts | None = None
    ) -> _reply_cycle_interfaces.ReplyCyclePersistence:
        """Bind one draft owner and the current durable save for a cycle invocation."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        if drafts is None:
            drafts = self._reply_draft_owner()
        return _reply_cycle_interfaces.ReplyCyclePersistence(
            save=a.save_state,
            recover=drafts.recover_checked,
            store=drafts.store,
            clear=drafts.clear,
            retire_ineligible=drafts.retire_ineligible,
        )

    def _reply_cycle_delivery(
        self,
        *,
        cooldowns: _api_cooldowns.ApiCooldowns | None = None,
        drafts: _reply_drafts.ReplyDrafts | None = None,
        tweets: _tweet_lookup_cache.TweetLookupCache | None = None,
    ) -> _reply_cycle_interfaces.ReplyCycleDelivery:
        """Compose current receipt and delivery owners without retaining caller state."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        if cooldowns is None:
            cooldowns = a._api_cooldown_owner()
        if drafts is None:
            drafts = self._reply_draft_owner()
        if tweets is None:
            tweets = a._tweet_lookup_cache_owner()
        receipt_values = self._reply_receipt_values_owner(drafts=drafts)
        receipts = self.reply_receipts(values=receipt_values)
        completion = self._reply_completion_owner(
            drafts=drafts,
            receipt_values=receipt_values,
            receipts=receipts,
            tweets=tweets,
            history=drafts.history,
        )
        return _reply_cycle_interfaces.ReplyCycleDelivery(
            receipts=receipts,
            completion=completion,
            block_ambiguous=a._reply_remote_write_barrier(receipts=receipts),
            receipt_values=receipt_values,
            tweets=tweets,
            post=self.post_with_current_owners,
            retire_rejected=receipts.retire_rejected,
            ambiguous_outcome=e.AmbiguousRemotePostOutcome,
            remote_operations_paused=e.RemoteOperationsPaused,
            api_error=e.ApiError,
            confirmed_local_failure=e.ConfirmedReplyLocalPersistenceError,
            proved_non_success=e.ProvedRemotePostNonSuccess,
            unrecoverable_confirmed=e.UnrecoverableConfirmedReplyPersistenceError,
            reply_not_allowed=a.api_error_is_reply_not_allowed,
            save_state=a.save_state,
            log=a.log,
            posting_outcome=self.posting_outcome,
            cooldowns=cooldowns,
        )

    def normal_runner(self) -> _normal_reply_cycle.NormalReplyCycle:
        """Build one normal runner with intentionally shared owner instances."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        tweets = a._tweet_lookup_cache_owner()
        reply_evaluations = self.reply_evaluations()
        mention_queue = self._mention_queue_owner()
        cooldowns = a._api_cooldown_owner()
        history = self._reply_history_owner()
        generation = self._reply_generation_owner(cooldowns=cooldowns, history=history)
        drafts = self._reply_draft_owner(history=history, generation=generation)
        controls = a._runtime_controls_owner()
        return _normal_reply_cycle.NormalReplyCycle(
            config=p.normal,
            persistence=self._reply_cycle_persistence(drafts=drafts),
            delivery=self._reply_cycle_delivery(
                cooldowns=cooldowns, drafts=drafts, tweets=tweets
            ),
            author_quarantines=self.author_quarantines(),
            ApiError=e.ApiError,
            PipelineResult=e.PipelineResult,
            RemoteOperationsPaused=e.RemoteOperationsPaused,
            ReplyEvidenceUnavailable=e.ReplyEvidenceUnavailable,
            SINGLE_CALL_STRATEGY_VERSION=p.SINGLE_CALL_STRATEGY_VERSION,
            ValidatedReply=e.ValidatedReply,
            _log_validated_single_call_reply=self.log_validated_reply,
            clarifications=self.clarification_replies(),
            conversational_reply_pipeline_enabled=self.pipeline_enabled,
            accounting=self.daily_reply_accounting(),
            reply_contexts=self._reply_context_owner(),
            tweets=tweets,
            generation=generation,
            history=history,
            dedupe_reply_candidates=self.dedupe_candidates,
            get_hot_post_reply_candidates=self._hot_post_discovery_callback(
                tweets=tweets,
                drafts=drafts,
                cooldowns=cooldowns,
                controls=controls,
                reply_evaluations=reply_evaluations,
            ),
            get_mentions=self._mention_discovery_callback(
                tweets=tweets,
                mention_queue=mention_queue,
                reply_evaluations=reply_evaluations,
            ),
            cooldowns=cooldowns,
            is_probably_spam_or_not_worth_replying=self.spam_or_not_worth_replying,
            controls=controls,
            log=a.log,
            log_ai_reply_posting_outcome=self.posting_outcome,
            log_event=a.log_event,
            mention_queue=mention_queue,
            maybe_mark_hot_post_reply_skipped=self.maybe_mark_hot_post_reply_skipped,
            now_epoch=a.now_epoch,
            reply_evaluations=reply_evaluations,
            reply_evidence_repository=a.reply_evidence_repository,
            reply_target_is_directly_eligible=self.reply_target_is_directly_eligible,
            valid_tweets_sorted_by_id=functools.partial(_reply_context.valid_tweets_sorted_by_id, log=a.log),
        )

    def quote_runner(self) -> _quote_reply_cycle.QuoteReplyCycle:
        """Build one quote runner with intentionally shared owner instances."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        tweets = a._tweet_lookup_cache_owner()
        cooldowns = a._api_cooldown_owner()
        history = self._reply_history_owner()
        generation = self._reply_generation_owner(cooldowns=cooldowns, history=history)
        drafts = self._reply_draft_owner(history=history, generation=generation)
        return _quote_reply_cycle.QuoteReplyCycle(
            config=p.quote,
            persistence=self._reply_cycle_persistence(drafts=drafts),
            delivery=self._reply_cycle_delivery(
                cooldowns=cooldowns, drafts=drafts, tweets=tweets
            ),
            ApiError=e.ApiError,
            ContextValidationError=e.ContextValidationError,
            PipelineResult=e.PipelineResult,
            RemoteOperationsPaused=e.RemoteOperationsPaused,
            ReplyEvidenceUnavailable=e.ReplyEvidenceUnavailable,
            SINGLE_CALL_STRATEGY_VERSION=p.SINGLE_CALL_STRATEGY_VERSION,
            ValidatedReply=e.ValidatedReply,
            _log_validated_single_call_reply=self.log_validated_reply,
            api_error_is_permanent_target_failure=a.api_error_is_permanent_target_failure,
            watch_posts=self._quote_watch_posts_owner(tweets=tweets),
            conversational_reply_pipeline_enabled=self.pipeline_enabled,
            accounting=self.daily_reply_accounting(),
            reply_contexts=self._reply_context_owner(),
            tweets=tweets,
            generation=generation,
            history=history,
            get_quote_tweets_for_posts=self.get_quote_tweets_for_posts,
            cooldowns=cooldowns,
            is_probably_spam_or_not_worth_replying=self.spam_or_not_worth_replying,
            controls=a._runtime_controls_owner(),
            log=a.log,
            log_ai_reply_posting_outcome=self.posting_outcome,
            log_event=a.log_event,
            now_epoch=a.now_epoch,
            parse_x_datetime_to_epoch=functools.partial(_reply_context.parse_x_datetime_to_epoch, log=a.log),
            reply_evaluations=self.reply_evaluations(),
            reply_evidence_repository=a.reply_evidence_repository,
            valid_tweets_sorted_by_id=functools.partial(_reply_context.valid_tweets_sorted_by_id, log=a.log),
        )

    def run_quote(self, state: dict[str, Any]) -> str:
        """Run a quote check through a freshly constructed runner."""
        return self.quote_runner().run(state)

    def run_normal(self, state: dict[str, Any]) -> str:
        """Rebuild a fresh normal runner for each backlog continuation pass."""
        fresh_evaluations = 0
        skip_hot_post_fetch = False
        current = self
        while True:
            outcome = current.normal_runner().run(
                state,
                _fresh_mention_ai_evaluations=fresh_evaluations,
                _skip_hot_post_fetch=skip_hot_post_fetch,
            )
            if not isinstance(outcome, _normal_reply_cycle.ContinueNormalReplyPass):
                return outcome
            fresh_evaluations = outcome.fresh_evaluations
            skip_hot_post_fetch = outcome.skip_hot_post_fetch
            current = self.current()

    def reply_evaluations(self) -> _reply_evaluation_state.ReplyEvaluations:
        """Bind current terminal evaluation limits without reading state or the clock."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        return _reply_evaluation_state.ReplyEvaluations(
            now_epoch=a.now_epoch,
            log=a.log,
            quarantine_evidence_policy=p.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
            maximum_state_epoch=p.MAX_REASONABLE_STATE_EPOCH,
            maximum_records=p.REPLY_EVALUATION_MAX_RECORDS,
            minimum_retention_seconds=p.REPLY_EVALUATION_MIN_RETENTION_SECONDS,
        )

    def author_quarantines(self) -> _author_quarantines.AuthorQuarantines:
        """Bind current author quarantine policy without reading state or the clock."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        return _author_quarantines.AuthorQuarantines(
            now_epoch=a.now_epoch,
            log=a.log,
            log_event=a.log_event,
            maximum_state_epoch=p.MAX_REASONABLE_STATE_EPOCH,
            threshold=p.AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD,
            window_seconds=p.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS,
            quarantine_seconds=p.AUTHOR_NO_REPLY_QUARANTINE_SECONDS,
            evidence_policy=p.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        )

    def mention_authority(self) -> _mention_authority.MentionAuthority:
        """Bind current mention authority policy without inspecting or saving state."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        return _mention_authority.MentionAuthority(
            state_file=p.STATE_FILE,
            maximum_epoch=p.MAX_REASONABLE_STATE_EPOCH,
            token_limit=p.MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT,
            log=a.log,
            log_event=a.log_event,
        )

    def daily_reply_accounting(
        self, *, dates: _receipt_primitives.ReceiptDates | None = None
    ) -> _daily_reply_accounting.DailyReplyAccounting:
        """Bind current daily accounting boundaries without reading dates or state."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        if dates is None:
            dates = a._receipt_dates_owner()
        return _daily_reply_accounting.DailyReplyAccounting(log=a.log, dates=dates)

    def clarification_replies(self) -> _reply_clarifications.ClarificationReplies:
        """Bind current clarification capabilities and policy without reading state."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        return _reply_clarifications.ClarificationReplies(
            pipeline_enabled=self.pipeline_enabled,
            contexts=self._reply_context_owner(),
            api_error=e.ApiError,
            invalid_receipt=e.InvalidConfirmedReplyReceipt,
            window_seconds=p.CLARIFICATION_REPLY_WINDOW_SECONDS,
            log_event=a.log_event,
        )

    def _reply_context_owner(self) -> _reply_context.ReplyContext:
        """Bind current context boundaries without fetching posts or retaining state."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        return _reply_context.ReplyContext(
            api_error=e.ApiError,
            parse_tweet_id=functools.partial(_reply_context.parse_tweet_id, log=a.log),
            maximum_parent_depth=p.THREAD_CONTEXT_MAX_DEPTH,
            maximum_parent_network_fetches=p.THREAD_CONTEXT_MAX_NETWORK_FETCHES,
            is_permanent_target_failure=a.api_error_is_permanent_target_failure,
            tweets=a._tweet_lookup_cache_owner(),
            log=a.log,
            log_json_debug=a.log_json_debug,
            user_id=p.MY_USER_ID,
            parse_x_datetime_to_epoch=functools.partial(_reply_context.parse_x_datetime_to_epoch, log=a.log),
            always_fetch_parent=p.ALWAYS_FETCH_PARENT_FOR_CONTEXT,
            context_validation_error=e.ContextValidationError,
            incoming_maximum_chars=p.REPLY_INCOMING_MAX_CHARS,
            maximum_visible_chars=p.MAX_VISIBLE_TEXT_CHARACTERS,
            skip_own_auto_replies=p.SKIP_REPLIES_TO_OWN_AUTO_REPLIES,
            bound_visible_conversation=_single_call_reply().bound_visible_conversation,
            current_utc_datetime=a.current_utc_datetime,
            media=self._reply_media_owner(),
            default_post_maximum_chars=p._DEFAULT_REPLY_CONTEXT_POST_MAXIMUM_CHARS,
        )

    def _reply_media_owner(self) -> _reply_native_media.ReplyMedia:
        """Bind current media policy and capabilities without performing work."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        return _reply_native_media.ReplyMedia(
            maximum_context_photos=p.MAX_REPLY_CONTEXT_PHOTOS,
            maximum_supplied_images=p.MAX_SUPPLIED_IMAGES,
            maximum_image_bytes=p.SINGLE_CALL_MAX_IMAGE_BYTES,
            image_mime_types=_reply_native_media._REPLY_IMAGE_MIME_TYPES,
            log=a.log,
            media_unavailable=e.ReplyMediaUnavailable,
            media_transient_unavailable=e.ReplyMediaTransientUnavailable,
            test_mode=p.TEST_MODE,
            require_remote_operation_unpaused=a.require_remote_operation_unpaused,
            requests=t.requests,
            request_timeout=t.request_timeout,
            validate_supplied_images=t.validate_supplied_images,
        )

    def _mention_queue_owner(
        self, *, authority: _mention_authority.MentionAuthority | None = None
    ) -> _mention_discovery.MentionQueue:
        """Bind current mention queue boundaries without reading state or files."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        if authority is None:
            authority = self.mention_authority()
        return _mention_discovery.MentionQueue(
            state_file=p.STATE_FILE,
            authority=authority,
            save=a.save_state,
            sort_candidates=functools.partial(_reply_context.valid_tweets_sorted_by_id, log=a.log),
            log=a.log,
        )

    def _mention_discovery_callback(
        self,
        *,
        tweets: _tweet_lookup_cache.TweetLookupCache | None = None,
        mention_queue: _mention_discovery.MentionQueue | None = None,
        reply_evaluations: _reply_evaluation_state.ReplyEvaluations | None = None,
    ) -> _reply_cycle_interfaces.ReplyCandidateDiscovery:
        """Bind one mention-discovery callback, reusing supplied cycle owners."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        if tweets is None:
            tweets = a._tweet_lookup_cache_owner()
        if mention_queue is None:
            mention_queue = self._mention_queue_owner()
        if reply_evaluations is None:
            reply_evaluations = self.reply_evaluations()
        return functools.partial(
            _mention_discovery.get_mentions,
            ApiError=e.ApiError,
            MAX_MENTIONS_PER_CHECK=p.MAX_MENTIONS_PER_CHECK,
            MENTIONS_MAX_PAGES_PER_CHECK=p.MENTIONS_MAX_PAGES_PER_CHECK,
            MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT=p.MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT,
            MY_USER_ID=p.MY_USER_ID,
            _MentionBacklogContinuationLimit=e._MentionBacklogContinuationLimit,
            api_error_is_invalid_pagination_cursor=a.api_error_is_invalid_pagination_cursor,
            tweets=tweets,
            log=a.log,
            log_event=a.log_event,
            log_json_debug=a.log_json_debug,
            now_epoch=a.now_epoch,
            mention_queue=mention_queue,
            reply_evaluations=reply_evaluations,
            save_state=a.save_state,
            valid_tweets_sorted_by_id=functools.partial(_reply_context.valid_tweets_sorted_by_id, log=a.log),
            x_paginated_get=t.x_paginated_get,
            x_request=t.x_request,
            api_error_is_permanent_target_failure=a.api_error_is_permanent_target_failure,
        )

    def _hot_post_discovery_callback(
        self,
        *,
        tweets: _tweet_lookup_cache.TweetLookupCache | None = None,
        drafts: _reply_drafts.ReplyDrafts | None = None,
        cooldowns: _api_cooldowns.ApiCooldowns | None = None,
        controls: _runtime_control.RuntimeControls | None = None,
        watch_posts: _quote_discovery.QuoteWatchPosts | None = None,
        reply_evaluations: _reply_evaluation_state.ReplyEvaluations | None = None,
    ) -> _reply_cycle_interfaces.ReplyCandidateDiscovery:
        """Bind one hot-post discovery callback, reusing supplied cycle owners."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        if tweets is None:
            tweets = a._tweet_lookup_cache_owner()
        if drafts is None:
            drafts = self._reply_draft_owner()
        if cooldowns is None:
            cooldowns = a._api_cooldown_owner()
        if controls is None:
            controls = a._runtime_controls_owner()
        if watch_posts is None:
            watch_posts = self._quote_watch_posts_owner(tweets=tweets)
        if reply_evaluations is None:
            reply_evaluations = self.reply_evaluations()
        return functools.partial(
            _hot_post_discovery.get_hot_post_reply_candidates,
            ApiError=e.ApiError,
            ENABLE_HOT_POST_REPLY_CHECKS=p.ENABLE_HOT_POST_REPLY_CHECKS,
            EXTRA_QUOTE_WATCH_FILE=p.EXTRA_QUOTE_WATCH_FILE,
            HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS=p.HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS,
            HOT_POST_REPLY_SEARCH_API_MAX_RESULTS=p.HOT_POST_REPLY_SEARCH_API_MAX_RESULTS,
            HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK=p.HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK,
            HOT_POST_REPLY_USE_SINCE_ID=p.HOT_POST_REPLY_USE_SINCE_ID,
            MAX_HOT_POST_REPLIES_PER_CHECK=p.MAX_HOT_POST_REPLIES_PER_CHECK,
            MY_USER_ID=p.MY_USER_ID,
            tweets=tweets,
            retire_ineligible_draft=drafts.retire_ineligible,
            cooldowns=cooldowns,
            controls=controls,
            watch_posts=watch_posts,
            log=a.log,
            log_event=a.log_event,
            log_json_debug=a.log_json_debug,
            mark_hot_post_reply_skipped=self.mark_hot_post_reply_skipped,
            reply_evaluations=reply_evaluations,
            reply_target_is_directly_eligible=self.reply_target_is_directly_eligible,
            save_state=a.save_state,
            valid_tweets_sorted_by_id=functools.partial(_reply_context.valid_tweets_sorted_by_id, log=a.log),
            x_paginated_get=t.x_paginated_get,
            x_quote_lookup_request=t.x_quote_lookup_request,
        )

    def _quote_watch_posts_owner(
        self, *, tweets: _tweet_lookup_cache.TweetLookupCache | None = None
    ) -> _quote_discovery.QuoteWatchPosts:
        """Bind current watch selection settings without reading the watch file."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        if tweets is None:
            tweets = a._tweet_lookup_cache_owner()
        return _quote_discovery.QuoteWatchPosts(
            watch_file=p.EXTRA_QUOTE_WATCH_FILE,
            maximum_extra_posts=p.MAX_EXTRA_QUOTE_WATCH_POSTS,
            maximum_posts=p.MAX_QUOTE_POSTS_PER_CHECK,
            lookback_posts=p.QUOTE_POST_LOOKBACK_MAIN_POSTS,
            tweets=tweets,
            log=a.log,
        )

    def get_quote_tweets_for_posts(
        self, post_ids: list[str], state: dict[str, Any] | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Search watched originals together using current root dependencies."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        return _quote_discovery.get_quote_tweets_for_posts(
            post_ids,
            state,
            QUOTE_LOOKUP_API_MAX_RESULTS=p.QUOTE_LOOKUP_API_MAX_RESULTS,
            QUOTE_LOOKUP_MAX_PAGES_PER_POST=p.QUOTE_LOOKUP_MAX_PAGES_PER_POST,
            log=a.log,
            save_state=a.save_state,
            x_paginated_get=t.x_paginated_get,
            x_quote_lookup_request=t.x_quote_lookup_request,
        )

    def get_quote_tweets_for_post(
        self, post_id: str, state: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Discover quotes for one original with current external boundaries."""
        p, e, io, t, a = self.policy, self.errors, self.receipt_io, self.transport, self.application
        cursor_record = functools.partial(
            _quote_discovery.quote_repeated_cursor_suppression_record,
            MAX_REASONABLE_STATE_EPOCH=p.MAX_REASONABLE_STATE_EPOCH,
            QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS=p.QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS,
        )
        return _quote_discovery.get_quote_tweets_for_post(
            post_id,
            state,
            QUOTE_LOOKUP_API_MAX_RESULTS=p.QUOTE_LOOKUP_API_MAX_RESULTS,
            QUOTE_LOOKUP_MAX_PAGES_PER_POST=p.QUOTE_LOOKUP_MAX_PAGES_PER_POST,
            QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS=p.QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS,
            log=a.log,
            log_event=a.log_event,
            log_json_debug=a.log_json_debug,
            normalise_quote_repeated_cursor_suppressions=functools.partial(
                _quote_discovery.normalise_quote_repeated_cursor_suppressions,
                QUOTE_REPEATED_CURSOR_SUPPRESSION_MAX_ENTRIES=p.QUOTE_REPEATED_CURSOR_SUPPRESSION_MAX_ENTRIES,
                now_epoch=a.now_epoch,
                quote_repeated_cursor_suppression_record=cursor_record,
            ),
            now_epoch=a.now_epoch,
            quote_repeated_cursor_suppression_record=cursor_record,
            save_state=a.save_state,
            x_paginated_get=t.x_paginated_get,
            x_quote_lookup_request=t.x_quote_lookup_request,
        )
