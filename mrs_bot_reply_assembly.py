"""Construct reply-lane collaborators at their original operation boundaries.

Construction binds dependencies without reading state, clocks, files or providers.
"""

from __future__ import annotations
import functools
from dataclasses import dataclass
from collections.abc import Callable
from typing import TYPE_CHECKING
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

import mrs_bot_reply_evaluation_state as _reply_evaluation_state
import mrs_bot_author_quarantines as _author_quarantines
import mrs_bot_daily_reply_accounting as _daily_reply_accounting
import mrs_bot_reply_clarifications as _reply_clarifications
import mrs_bot_reply_context as _reply_context
import mrs_bot_reply_native_media as _reply_native_media
import mrs_bot_hot_post_discovery as _hot_post_discovery
import mrs_bot_quote_discovery as _quote_discovery
import mrs_bot_runtime_control as _runtime_control

if TYPE_CHECKING:
    from mrs_bot_reply_context import ReplyContext


@dataclass(frozen=True)
class ReplyExternalBindings:
    """Current application settings and external service boundaries."""

    datetime: object
    requests: object
    QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS: object
    normalise_quote_repeated_cursor_suppressions: object
    quote_repeated_cursor_suppression_record: object

    AI_REPLY_HISTORY_MAX_AGE_SECONDS: object
    AI_REPLY_HISTORY_MAX_RECORDS: object
    AI_REQUEST_RECORD_DIR: object
    AmbiguousRemotePostOutcome: object
    ApiError: object
    CONFIRMED_REPLY_RECEIPT_FILE: object
    ConfirmedReplyLocalPersistenceError: object
    ContextValidationError: object
    ENABLE_AUTO_REPLIES: object
    ENABLE_QUOTE_TWEET_CHECKS: object
    InvalidConfirmedReplyReceipt: object
    MARK_AI_REPLIES_AS_AI: object
    MAX_AUTO_REPLIES_PER_DAY: object
    MAX_MENTIONS_PER_CHECK: object
    MAX_QUOTE_POSTS_PER_CHECK: object
    MAX_QUOTE_REPLIES_PER_DAY: object
    MAX_REASONABLE_STATE_EPOCH: object
    MAX_RECENT_ACCOUNT_REPLIES: object
    MAX_REPLIES_PER_AUTHOR_PER_DAY: object
    MAX_SAME_AUTHOR_INTERACTIONS: object
    MIN_SECONDS_BETWEEN_REPLIES: object
    MY_USER_ID: object
    OPENAI_API_KEY: object
    OPENAI_BASE: object
    PipelineResult: object
    ProvedRemotePostNonSuccess: object
    QUOTE_REPLY_DELAY_SECONDS: object
    REPLY_INCOMING_MAX_CHARS: object
    RemoteOperationsPaused: object
    ReplyEvidenceUnavailable: object
    ReplyValidationError: object
    SINGLE_CALL_MODEL: object
    SINGLE_CALL_REASONING_EFFORT: object
    SINGLE_CALL_STRATEGY_VERSION: object
    STATE_FILE: object
    StateBackupWriteError: object
    TRANSPORT_SOURCE_VALIDATOR_ID: object
    TransportJournalError: object
    UnrecoverableConfirmedReplyPersistenceError: object
    UnresolvedSendingReplyReceipt: object
    ValidatedReply: object
    _DEFAULT_RECENT_ACCOUNT_REPLY_LIMIT: object
    _api_cooldown_owner: object
    _legacy_ai_reply_receipt_draft_is_valid: object
    _legacy_conversational_transport_source_semantic_validator: object
    _log_validated_single_call_reply: object
    _receipt_dates_owner: object
    _reply_remote_write_barrier: object
    _runtime_controls_owner: object
    _tweet_lookup_cache_owner: object
    api_error_is_permanent_target_failure: object
    api_error_is_reply_not_allowed: object
    begin_confirmed_post_sigint_deferral: object
    bind_confirmed_transport_source: object
    conversational_reply_pipeline_enabled: object
    create_post: object
    dedupe_reply_candidates: object
    durable_create_receipt_json: object
    end_confirmed_post_sigint_deferral: object
    inspect_confirmed_transport_transaction: object
    is_probably_spam_or_not_worth_replying: object
    journal_path_for_receipt: object
    json_file_matches: object
    latch_confirmed_post_persistence_failure: object
    load_receipt_json_no_follow: object
    log: object
    log_ai_reply_posting_outcome: object
    log_event: object
    maybe_mark_hot_post_reply_skipped: object
    monotonic: object
    now_epoch: object
    parse_x_datetime_to_epoch: object
    quoted_post_reference_id: object
    receipt_int: object
    receipt_namespace_entry_exists: object
    remote_receipt_retirement_is_blocking: object
    remove_confirmed_reply_receipt: object
    replace_bound_source_receipt: object
    reply_evidence_repository: object
    reply_target_is_directly_eligible: object
    report_bot_health_progress: object
    require_remote_operation_unpaused: object
    retain_sigint_deferral_without_durable_barrier: object
    retire_lane_transport_journal_if_present: object
    retire_proved_rejected_conversational_reply_receipt: object
    run_single_call_reply_pipeline: object
    save_state: object
    single_call_decision_telemetry: object
    single_call_reply: object
    sleep: object
    transaction_mutation_authority: object
    transport_source_semantic_validator: object
    valid_receipt_epoch: object
    valid_tweets_sorted_by_id: object
    validate_single_call_persisted_draft: object
    verify_lane_transport_source_lineage_if_present: object
    ALWAYS_FETCH_PARENT_FOR_CONTEXT: object
    AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY: object
    AUTHOR_NO_REPLY_QUARANTINE_SECONDS: object
    AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD: object
    AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS: object
    CLARIFICATION_REPLY_WINDOW_SECONDS: object
    ENABLE_HOT_POST_REPLY_CHECKS: object
    EXTRA_QUOTE_WATCH_FILE: object
    HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS: object
    HOT_POST_REPLY_SEARCH_API_MAX_RESULTS: object
    HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK: object
    HOT_POST_REPLY_USE_SINCE_ID: object
    MAX_EXTRA_QUOTE_WATCH_POSTS: object
    MAX_HOT_POST_REPLIES_PER_CHECK: object
    MAX_REPLY_CONTEXT_PHOTOS: object
    MAX_SUPPLIED_IMAGES: object
    MAX_VISIBLE_TEXT_CHARACTERS: object
    MENTIONS_MAX_PAGES_PER_CHECK: object
    MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT: object
    QUOTE_LOOKUP_API_MAX_RESULTS: object
    QUOTE_LOOKUP_MAX_PAGES_PER_POST: object
    QUOTE_POST_LOOKBACK_MAIN_POSTS: object
    REPLY_EVALUATION_MAX_RECORDS: object
    REPLY_EVALUATION_MIN_RETENTION_SECONDS: object
    ReplyMediaTransientUnavailable: object
    ReplyMediaUnavailable: object
    SINGLE_CALL_MAX_IMAGE_BYTES: object
    SKIP_REPLIES_TO_OWN_AUTO_REPLIES: object
    TEST_MODE: object
    THREAD_CONTEXT_MAX_DEPTH: object
    THREAD_CONTEXT_MAX_NETWORK_FETCHES: object
    _DEFAULT_REPLY_CONTEXT_POST_MAXIMUM_CHARS: object
    _MentionBacklogContinuationLimit: object
    _REPLY_IMAGE_MIME_TYPES: object
    api_error_is_invalid_pagination_cursor: object
    bound_visible_conversation: object
    current_utc_datetime: object
    log_json_debug: object
    mark_hot_post_reply_skipped: object
    parse_tweet_id: object
    request_timeout: object
    validate_supplied_images: object
    x_paginated_get: object
    x_quote_lookup_request: object
    x_request: object


class ReplyAssembly:
    """Construct and coordinate normal, quote and send operations."""

    def __init__(self, current_bindings: Callable[[], ReplyExternalBindings]) -> None:
        """Retain the narrow supplier for current external bindings."""
        self._current_bindings = current_bindings

    @property
    def bindings(self) -> ReplyExternalBindings:
        """Read current external bindings at an operation boundary."""
        return self._current_bindings()

    def _reply_draft_owner(
        self,
        *,
        history: _reply_history.ReplyHistory | None = None,
        generation: _reply_generation.ReplyGeneration | None = None,
    ) -> _reply_drafts.ReplyDrafts:
        """Compose current draft owners without acquiring evidence or caller state."""
        b = self.bindings
        if history is None:
            history = self._reply_history_owner()
        if generation is None:
            generation = self._reply_generation_owner(history=history)
        return _reply_drafts.ReplyDrafts(
            validate_persisted_draft=b.validate_single_call_persisted_draft,
            evidence_repository=b.reply_evidence_repository,
            history=history,
            generation=generation,
            log_event=b.log_event,
            log=b.log,
            strategy_version=b.SINGLE_CALL_STRATEGY_VERSION,
            model=b.SINGLE_CALL_MODEL,
            result_type=b.PipelineResult,
            reply_type=b.ValidatedReply,
            evidence_unavailable=b.ReplyEvidenceUnavailable,
            validation_error=b.ReplyValidationError,
        )

    def _reply_history_owner(self) -> _reply_history.ReplyHistory:
        """Bind current history dependencies without reading state or the clock."""
        b = self.bindings
        return _reply_history.ReplyHistory(
            now_epoch=b.now_epoch,
            quoted_post_reference_id=b.quoted_post_reference_id,
            maximum_state_epoch=b.MAX_REASONABLE_STATE_EPOCH,
            maximum_recent_replies=b.MAX_RECENT_ACCOUNT_REPLIES,
            default_recent_reply_limit=b._DEFAULT_RECENT_ACCOUNT_REPLY_LIMIT,
            maximum_same_author_interactions=b.MAX_SAME_AUTHOR_INTERACTIONS,
            maximum_age_seconds=b.AI_REPLY_HISTORY_MAX_AGE_SECONDS,
            maximum_records=b.AI_REPLY_HISTORY_MAX_RECORDS,
        )

    def _reply_model_transport_owner(
        self,
    ) -> _reply_model_transport.ReplyModelTransport:
        """Bind model transport without making a provider request."""
        b = self.bindings
        return _reply_model_transport.ReplyModelTransport(
            log=b.log,
            model=b.SINGLE_CALL_MODEL,
            reasoning_effort=b.SINGLE_CALL_REASONING_EFFORT,
            monotonic=b.monotonic,
            require_remote_operation_unpaused=b.require_remote_operation_unpaused,
            report_bot_health_progress=b.report_bot_health_progress,
            requests=b.requests,
            base_url=b.OPENAI_BASE,
            api_key=b.OPENAI_API_KEY,
            sleep=b.sleep,
            now_epoch=b.now_epoch,
            error_type=b.ApiError,
            request_record_directory=b.AI_REQUEST_RECORD_DIR,
            log_event=b.log_event,
        )

    def _reply_generation_owner(
        self,
        *,
        cooldowns: _api_cooldowns.ApiCooldowns | None = None,
        history: _reply_history.ReplyHistory | None = None,
    ) -> _reply_generation.ReplyGeneration:
        """Compose current generation owners without collecting evidence or media."""
        b = self.bindings
        if cooldowns is None:
            cooldowns = b._api_cooldown_owner()
        if history is None:
            history = self._reply_history_owner()
        return _reply_generation.ReplyGeneration(
            media=self._reply_media_owner(),
            remote_operations_paused=b.RemoteOperationsPaused,
            result_type=b.PipelineResult,
            log=b.log,
            history=history,
            require_remote_operation_unpaused=b.require_remote_operation_unpaused,
            run_pipeline=b.run_single_call_reply_pipeline,
            config=b.single_call_reply,
            evidence_repository=b.reply_evidence_repository,
            model_transport=self._reply_model_transport_owner(),
            cooldowns=cooldowns,
            reply_type=b.ValidatedReply,
            decision_telemetry=b.single_call_decision_telemetry,
            log_event=b.log_event,
            strategy_version=b.SINGLE_CALL_STRATEGY_VERSION,
        )

    def _reply_receipt_values_owner(
        self,
        *,
        dates: _receipt_primitives.ReceiptDates | None = None,
        drafts: _reply_drafts.ReplyDrafts | None = None,
    ) -> _reply_receipt_values.ReplyReceiptValues:
        """Bind current receipt value boundaries without reading the clock or state."""
        b = self.bindings
        if dates is None:
            dates = b._receipt_dates_owner()
        if drafts is None:
            drafts = self._reply_draft_owner()
        return _reply_receipt_values.ReplyReceiptValues(
            valid_receipt_epoch=b.valid_receipt_epoch,
            dates=dates,
            legacy_draft_is_valid=b._legacy_ai_reply_receipt_draft_is_valid,
            drafts=drafts,
            now_epoch=b.now_epoch,
            log=b.log,
            invalid_receipt=b.InvalidConfirmedReplyReceipt,
        )

    def _reply_receipts_owner(
        self, *, values: _reply_receipt_values.ReplyReceiptValues | None = None
    ) -> _reply_delivery.ReplyReceipts:
        """Bind runtime receipt authorities for one load, publication or promotion."""
        b = self.bindings
        if values is None:
            values = self._reply_receipt_values_owner()
        return _reply_delivery.ReplyReceipts(
            path=b.CONFIRMED_REPLY_RECEIPT_FILE,
            read_json=b.load_receipt_json_no_follow,
            log=b.log,
            values=values,
            retirement_is_blocking=b.remote_receipt_retirement_is_blocking,
            invalid_receipt=b.InvalidConfirmedReplyReceipt,
            namespace_entry_exists=b.receipt_namespace_entry_exists,
            create_json=b.durable_create_receipt_json,
            unresolved_sending=b.UnresolvedSendingReplyReceipt,
            bind_confirmed_source=b.bind_confirmed_transport_source,
            journal_path=b.journal_path_for_receipt,
            validator_id=b.TRANSPORT_SOURCE_VALIDATOR_ID,
            transport_validator=b.transport_source_semantic_validator,
            legacy_transport_validator=b._legacy_conversational_transport_source_semantic_validator,
            transport_journal_error=b.TransportJournalError,
            replace_bound_source=b.replace_bound_source_receipt,
            mutation_authority=b.transaction_mutation_authority,
            current_receipts=lambda: self._reply_receipts_owner(),
        )

    def emergency_representation_is_complete(self, receipt: dict, state: dict) -> bool:
        """Check whether the current state durably represents a confirmed reply."""
        b = self.bindings
        drafts = self._reply_draft_owner()
        return _reply_reconciliation.confirmed_reply_emergency_representation_is_complete(
            receipt,
            state,
            receipt_values=self._reply_receipt_values_owner(drafts=drafts),
            InvalidConfirmedReplyReceipt=b.InvalidConfirmedReplyReceipt,
            receipt_int=b.receipt_int,
            has_target_draft=drafts.has_target,
        )

    def observed_confirmation_epoch(
        self, receipt: dict, confirmation_epoch: int | None = None
    ) -> int:
        """Resolve a recovered reply's confirmation time with current receipt rules."""
        return self._reply_receipt_values_owner().observed_confirmation_epoch(
            receipt, confirmation_epoch
        )

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
    ) -> functools.partial:
        """Compose current typed owners for one confirmed-state application."""
        b = self.bindings
        if dates is None:
            dates = b._receipt_dates_owner()
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
            mention_authority = self._mention_authority_owner()
        if mention_queue is None:
            mention_queue = self._mention_queue_owner(authority=mention_authority)
        if tweets is None:
            tweets = b._tweet_lookup_cache_owner()
        if history is None:
            history = self._reply_history_owner()
        return functools.partial(
            _reply_reconciliation.apply_confirmed_reply_receipt,
            receipt_values=receipt_values,
            mention_authority=mention_authority,
            mention_queue=mention_queue,
            STATE_FILE=b.STATE_FILE,
            InvalidConfirmedReplyReceipt=b.InvalidConfirmedReplyReceipt,
            dates=dates,
            accounting=self._daily_reply_accounting_owner(dates=dates),
            log=b.log,
            drafts=drafts,
            tweets=tweets,
            MY_USER_ID=b.MY_USER_ID,
            datetime=b.datetime,
            history=history,
            clarifications=self._clarification_reply_owner(),
            log_event=b.log_event,
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
        b = self.bindings
        if dates is None:
            dates = b._receipt_dates_owner()
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
            receipts = self._reply_receipts_owner(values=receipt_values)
        return _reply_reconciliation.ReplyCompletion(
            receipt_path=b.CONFIRMED_REPLY_RECEIPT_FILE,
            persistence_error=b.ConfirmedReplyLocalPersistenceError,
            apply_state=self._confirmed_reply_state_applier(
                dates=dates,
                drafts=drafts,
                receipt_values=receipt_values,
                tweets=tweets,
                history=history,
            ),
            save_state=b.save_state,
            retire_journal=b.retire_lane_transport_journal_if_present,
            remove_receipt=b.remove_confirmed_reply_receipt,
            log=b.log,
            receipts=receipts,
            unresolved_sending_receipt=b.UnresolvedSendingReplyReceipt,
            invalid_receipt=b.InvalidConfirmedReplyReceipt,
            verify_lineage=b.verify_lane_transport_source_lineage_if_present,
        )

    def post_with_current_owners(
        self,
        *,
        state: dict,
        receipt_template: dict,
        reply_text: str,
        reply_to_id: str,
        made_with_ai: bool,
        lane: str,
    ):
        """Compose send-time owners and execute the durable reply publication."""
        b = self.bindings
        drafts = self._reply_draft_owner()
        receipt_values = self._reply_receipt_values_owner(drafts=drafts)
        receipts = self._reply_receipts_owner(values=receipt_values)
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
            receipt_namespace_entry_exists=b.receipt_namespace_entry_exists,
            CONFIRMED_REPLY_RECEIPT_FILE=b.CONFIRMED_REPLY_RECEIPT_FILE,
            InvalidConfirmedReplyReceipt=b.InvalidConfirmedReplyReceipt,
            block_if_ambiguous_remote_post=b._reply_remote_write_barrier(
                receipts=receipts
            ),
            receipts=lambda: self._reply_receipts_owner(),
            begin_confirmed_post_sigint_deferral=b.begin_confirmed_post_sigint_deferral,
            create_post=b.create_post,
            AmbiguousRemotePostOutcome=b.AmbiguousRemotePostOutcome,
            end_confirmed_post_sigint_deferral=b.end_confirmed_post_sigint_deferral,
            cooldowns=b._api_cooldown_owner(),
            save_state=b.save_state,
            log=b.log,
            RemoteOperationsPaused=b.RemoteOperationsPaused,
            remove_confirmed_reply_receipt=b.remove_confirmed_reply_receipt,
            ConfirmedReplyLocalPersistenceError=b.ConfirmedReplyLocalPersistenceError,
            ProvedRemotePostNonSuccess=b.ProvedRemotePostNonSuccess,
            ApiError=b.ApiError,
            inspect_confirmed_transport_transaction=b.inspect_confirmed_transport_transaction,
            journal_path_for_receipt=b.journal_path_for_receipt,
            StateBackupWriteError=b.StateBackupWriteError,
            json_file_matches=b.json_file_matches,
            STATE_FILE=b.STATE_FILE,
            confirmed_reply_emergency_representation_is_complete=functools.partial(
                _reply_reconciliation.confirmed_reply_emergency_representation_is_complete,
                receipt_values=receipt_values,
                InvalidConfirmedReplyReceipt=b.InvalidConfirmedReplyReceipt,
                receipt_int=b.receipt_int,
                has_target_draft=drafts.has_target,
            ),
            latch_confirmed_post_persistence_failure=b.latch_confirmed_post_persistence_failure,
            retain_sigint_deferral_without_durable_barrier=b.retain_sigint_deferral_without_durable_barrier,
            UnrecoverableConfirmedReplyPersistenceError=b.UnrecoverableConfirmedReplyPersistenceError,
            completion=completion,
        )

    def _reply_cycle_persistence(
        self, *, drafts: _reply_drafts.ReplyDrafts | None = None
    ) -> _reply_cycle_interfaces.ReplyCyclePersistence:
        """Bind one draft owner and the current durable save for a cycle invocation."""
        b = self.bindings
        if drafts is None:
            drafts = self._reply_draft_owner()
        return _reply_cycle_interfaces.ReplyCyclePersistence(
            save=b.save_state,
            recover=drafts.recover,
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
        b = self.bindings
        if cooldowns is None:
            cooldowns = b._api_cooldown_owner()
        if drafts is None:
            drafts = self._reply_draft_owner()
        if tweets is None:
            tweets = b._tweet_lookup_cache_owner()
        receipt_values = self._reply_receipt_values_owner(drafts=drafts)
        receipts = self._reply_receipts_owner(values=receipt_values)
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
            block_ambiguous=b._reply_remote_write_barrier(receipts=receipts),
            receipt_values=receipt_values,
            tweets=tweets,
            post=self.post_with_current_owners,
            retire_rejected=b.retire_proved_rejected_conversational_reply_receipt,
            ambiguous_outcome=b.AmbiguousRemotePostOutcome,
            remote_operations_paused=b.RemoteOperationsPaused,
            api_error=b.ApiError,
            confirmed_local_failure=b.ConfirmedReplyLocalPersistenceError,
            proved_non_success=b.ProvedRemotePostNonSuccess,
            unrecoverable_confirmed=b.UnrecoverableConfirmedReplyPersistenceError,
            reply_not_allowed=b.api_error_is_reply_not_allowed,
            save_state=b.save_state,
            log=b.log,
            posting_outcome=b.log_ai_reply_posting_outcome,
            cooldowns=cooldowns,
        )

    def normal_runner(self) -> _normal_reply_cycle.NormalReplyCycle:
        """Build one normal runner with intentionally shared owner instances."""
        b = self.bindings
        tweets = b._tweet_lookup_cache_owner()
        reply_evaluations = self._reply_evaluation_owner()
        mention_queue = self._mention_queue_owner()
        cooldowns = b._api_cooldown_owner()
        history = self._reply_history_owner()
        generation = self._reply_generation_owner(cooldowns=cooldowns, history=history)
        drafts = self._reply_draft_owner(history=history, generation=generation)
        controls = b._runtime_controls_owner()
        return _normal_reply_cycle.NormalReplyCycle(
            config=_reply_cycle_interfaces.NormalReplyConfig(
                enabled=b.ENABLE_AUTO_REPLIES,
                mark_as_ai=b.MARK_AI_REPLIES_AS_AI,
                maximum_daily_replies=b.MAX_AUTO_REPLIES_PER_DAY,
                maximum_daily_author_replies=b.MAX_REPLIES_PER_AUTHOR_PER_DAY,
                minimum_reply_spacing=b.MIN_SECONDS_BETWEEN_REPLIES,
                user_id=b.MY_USER_ID,
                maximum_fresh_evaluations=b.MAX_MENTIONS_PER_CHECK,
                incoming_max_chars=b.REPLY_INCOMING_MAX_CHARS,
            ),
            persistence=self._reply_cycle_persistence(drafts=drafts),
            delivery=self._reply_cycle_delivery(
                cooldowns=cooldowns, drafts=drafts, tweets=tweets
            ),
            author_quarantines=self._author_quarantine_owner(),
            ApiError=b.ApiError,
            PipelineResult=b.PipelineResult,
            RemoteOperationsPaused=b.RemoteOperationsPaused,
            ReplyEvidenceUnavailable=b.ReplyEvidenceUnavailable,
            SINGLE_CALL_STRATEGY_VERSION=b.SINGLE_CALL_STRATEGY_VERSION,
            ValidatedReply=b.ValidatedReply,
            _log_validated_single_call_reply=b._log_validated_single_call_reply,
            clarifications=self._clarification_reply_owner(),
            conversational_reply_pipeline_enabled=b.conversational_reply_pipeline_enabled,
            accounting=self._daily_reply_accounting_owner(),
            reply_contexts=self._reply_context_owner(),
            tweets=tweets,
            generation=generation,
            history=history,
            dedupe_reply_candidates=b.dedupe_reply_candidates,
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
            is_probably_spam_or_not_worth_replying=b.is_probably_spam_or_not_worth_replying,
            controls=controls,
            log=b.log,
            log_ai_reply_posting_outcome=b.log_ai_reply_posting_outcome,
            log_event=b.log_event,
            mention_queue=mention_queue,
            maybe_mark_hot_post_reply_skipped=b.maybe_mark_hot_post_reply_skipped,
            now_epoch=b.now_epoch,
            reply_evaluations=reply_evaluations,
            reply_evidence_repository=b.reply_evidence_repository,
            reply_target_is_directly_eligible=b.reply_target_is_directly_eligible,
            valid_tweets_sorted_by_id=b.valid_tweets_sorted_by_id,
        )

    def quote_runner(self) -> _quote_reply_cycle.QuoteReplyCycle:
        """Build one quote runner with intentionally shared owner instances."""
        b = self.bindings
        tweets = b._tweet_lookup_cache_owner()
        cooldowns = b._api_cooldown_owner()
        history = self._reply_history_owner()
        generation = self._reply_generation_owner(cooldowns=cooldowns, history=history)
        drafts = self._reply_draft_owner(history=history, generation=generation)
        return _quote_reply_cycle.QuoteReplyCycle(
            config=_reply_cycle_interfaces.QuoteReplyConfig(
                enabled=b.ENABLE_AUTO_REPLIES,
                mark_as_ai=b.MARK_AI_REPLIES_AS_AI,
                maximum_daily_replies=b.MAX_AUTO_REPLIES_PER_DAY,
                maximum_daily_author_replies=b.MAX_REPLIES_PER_AUTHOR_PER_DAY,
                minimum_reply_spacing=b.MIN_SECONDS_BETWEEN_REPLIES,
                user_id=b.MY_USER_ID,
                quote_checks_enabled=b.ENABLE_QUOTE_TWEET_CHECKS,
                minimum_quote_age_seconds=b.QUOTE_REPLY_DELAY_SECONDS,
                maximum_candidates=b.MAX_QUOTE_POSTS_PER_CHECK,
                maximum_daily_quote_replies=b.MAX_QUOTE_REPLIES_PER_DAY,
            ),
            persistence=self._reply_cycle_persistence(drafts=drafts),
            delivery=self._reply_cycle_delivery(
                cooldowns=cooldowns, drafts=drafts, tweets=tweets
            ),
            ApiError=b.ApiError,
            ContextValidationError=b.ContextValidationError,
            PipelineResult=b.PipelineResult,
            RemoteOperationsPaused=b.RemoteOperationsPaused,
            ReplyEvidenceUnavailable=b.ReplyEvidenceUnavailable,
            SINGLE_CALL_STRATEGY_VERSION=b.SINGLE_CALL_STRATEGY_VERSION,
            ValidatedReply=b.ValidatedReply,
            _log_validated_single_call_reply=b._log_validated_single_call_reply,
            api_error_is_permanent_target_failure=b.api_error_is_permanent_target_failure,
            watch_posts=self._quote_watch_posts_owner(tweets=tweets),
            conversational_reply_pipeline_enabled=b.conversational_reply_pipeline_enabled,
            accounting=self._daily_reply_accounting_owner(),
            reply_contexts=self._reply_context_owner(),
            tweets=tweets,
            generation=generation,
            history=history,
            get_quote_tweets_for_posts=self.get_quote_tweets_for_posts,
            cooldowns=cooldowns,
            is_probably_spam_or_not_worth_replying=b.is_probably_spam_or_not_worth_replying,
            controls=b._runtime_controls_owner(),
            log=b.log,
            log_ai_reply_posting_outcome=b.log_ai_reply_posting_outcome,
            log_event=b.log_event,
            now_epoch=b.now_epoch,
            parse_x_datetime_to_epoch=b.parse_x_datetime_to_epoch,
            reply_evaluations=self._reply_evaluation_owner(),
            reply_evidence_repository=b.reply_evidence_repository,
            valid_tweets_sorted_by_id=b.valid_tweets_sorted_by_id,
        )

    def run_quote(self, state: dict) -> str:
        """Run a quote check through a freshly constructed runner."""
        return self.quote_runner().run(state)

    def run_normal(self, state: dict) -> str:
        """Rebuild a fresh normal runner for each backlog continuation pass."""
        fresh_evaluations = 0
        skip_hot_post_fetch = False
        while True:
            outcome = self.normal_runner().run(
                state,
                _fresh_mention_ai_evaluations=fresh_evaluations,
                _skip_hot_post_fetch=skip_hot_post_fetch,
            )
            if not isinstance(outcome, _normal_reply_cycle.ContinueNormalReplyPass):
                return outcome
            fresh_evaluations = outcome.fresh_evaluations
            skip_hot_post_fetch = outcome.skip_hot_post_fetch

    def _reply_evaluation_owner(self) -> _reply_evaluation_state.ReplyEvaluations:
        """Bind current terminal evaluation limits without reading state or the clock."""
        b = self.bindings
        return _reply_evaluation_state.ReplyEvaluations(
            now_epoch=b.now_epoch,
            log=b.log,
            quarantine_evidence_policy=b.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
            maximum_state_epoch=b.MAX_REASONABLE_STATE_EPOCH,
            maximum_records=b.REPLY_EVALUATION_MAX_RECORDS,
            minimum_retention_seconds=b.REPLY_EVALUATION_MIN_RETENTION_SECONDS,
        )

    def _author_quarantine_owner(self) -> _author_quarantines.AuthorQuarantines:
        """Bind current author quarantine policy without reading state or the clock."""
        b = self.bindings
        return _author_quarantines.AuthorQuarantines(
            now_epoch=b.now_epoch,
            log=b.log,
            log_event=b.log_event,
            maximum_state_epoch=b.MAX_REASONABLE_STATE_EPOCH,
            threshold=b.AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD,
            window_seconds=b.AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS,
            quarantine_seconds=b.AUTHOR_NO_REPLY_QUARANTINE_SECONDS,
            evidence_policy=b.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        )

    def _mention_authority_owner(self) -> _mention_authority.MentionAuthority:
        """Bind current mention authority policy without inspecting or saving state."""
        b = self.bindings
        return _mention_authority.MentionAuthority(
            state_file=b.STATE_FILE,
            maximum_epoch=b.MAX_REASONABLE_STATE_EPOCH,
            token_limit=b.MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT,
            log=b.log,
            log_event=b.log_event,
        )

    def _daily_reply_accounting_owner(
        self, *, dates: _receipt_primitives.ReceiptDates | None = None
    ) -> _daily_reply_accounting.DailyReplyAccounting:
        """Bind current daily accounting boundaries without reading dates or state."""
        b = self.bindings
        if dates is None:
            dates = b._receipt_dates_owner()
        return _daily_reply_accounting.DailyReplyAccounting(log=b.log, dates=dates)

    def _clarification_reply_owner(self) -> _reply_clarifications.ClarificationReplies:
        """Bind current clarification capabilities and policy without reading state."""
        b = self.bindings
        return _reply_clarifications.ClarificationReplies(
            pipeline_enabled=b.conversational_reply_pipeline_enabled,
            contexts=self._reply_context_owner(),
            api_error=b.ApiError,
            invalid_receipt=b.InvalidConfirmedReplyReceipt,
            window_seconds=b.CLARIFICATION_REPLY_WINDOW_SECONDS,
            log_event=b.log_event,
        )

    def _reply_context_owner(self) -> _reply_context.ReplyContext:
        """Bind current context boundaries without fetching posts or retaining state."""
        b = self.bindings
        return _reply_context.ReplyContext(
            api_error=b.ApiError,
            parse_tweet_id=b.parse_tweet_id,
            maximum_parent_depth=b.THREAD_CONTEXT_MAX_DEPTH,
            maximum_parent_network_fetches=b.THREAD_CONTEXT_MAX_NETWORK_FETCHES,
            is_permanent_target_failure=b.api_error_is_permanent_target_failure,
            tweets=b._tweet_lookup_cache_owner(),
            log=b.log,
            log_json_debug=b.log_json_debug,
            user_id=b.MY_USER_ID,
            parse_x_datetime_to_epoch=b.parse_x_datetime_to_epoch,
            always_fetch_parent=b.ALWAYS_FETCH_PARENT_FOR_CONTEXT,
            context_validation_error=b.ContextValidationError,
            incoming_maximum_chars=b.REPLY_INCOMING_MAX_CHARS,
            maximum_visible_chars=b.MAX_VISIBLE_TEXT_CHARACTERS,
            skip_own_auto_replies=b.SKIP_REPLIES_TO_OWN_AUTO_REPLIES,
            bound_visible_conversation=b.bound_visible_conversation,
            current_utc_datetime=b.current_utc_datetime,
            media=self._reply_media_owner(),
            default_post_maximum_chars=b._DEFAULT_REPLY_CONTEXT_POST_MAXIMUM_CHARS,
        )

    def _reply_media_owner(self) -> _reply_native_media.ReplyMedia:
        """Bind current media policy and capabilities without performing work."""
        b = self.bindings
        return _reply_native_media.ReplyMedia(
            maximum_context_photos=b.MAX_REPLY_CONTEXT_PHOTOS,
            maximum_supplied_images=b.MAX_SUPPLIED_IMAGES,
            maximum_image_bytes=b.SINGLE_CALL_MAX_IMAGE_BYTES,
            image_mime_types=b._REPLY_IMAGE_MIME_TYPES,
            log=b.log,
            media_unavailable=b.ReplyMediaUnavailable,
            media_transient_unavailable=b.ReplyMediaTransientUnavailable,
            test_mode=b.TEST_MODE,
            require_remote_operation_unpaused=b.require_remote_operation_unpaused,
            requests=b.requests,
            request_timeout=b.request_timeout,
            validate_supplied_images=b.validate_supplied_images,
        )

    def _mention_queue_owner(
        self, *, authority: _mention_authority.MentionAuthority | None = None
    ) -> _mention_discovery.MentionQueue:
        """Bind current mention queue boundaries without reading state or files."""
        b = self.bindings
        if authority is None:
            authority = self._mention_authority_owner()
        return _mention_discovery.MentionQueue(
            state_file=b.STATE_FILE,
            authority=authority,
            save=b.save_state,
            sort_candidates=b.valid_tweets_sorted_by_id,
            log=b.log,
        )

    def _mention_discovery_callback(
        self,
        *,
        tweets: _tweet_lookup_cache.TweetLookupCache | None = None,
        mention_queue: _mention_discovery.MentionQueue | None = None,
        reply_evaluations: _reply_evaluation_state.ReplyEvaluations | None = None,
    ) -> _reply_cycle_interfaces.ReplyCandidateDiscovery:
        """Bind one mention-discovery callback, reusing supplied cycle owners."""
        b = self.bindings
        if tweets is None:
            tweets = b._tweet_lookup_cache_owner()
        if mention_queue is None:
            mention_queue = self._mention_queue_owner()
        if reply_evaluations is None:
            reply_evaluations = self._reply_evaluation_owner()
        return functools.partial(
            _mention_discovery.get_mentions,
            ApiError=b.ApiError,
            MAX_MENTIONS_PER_CHECK=b.MAX_MENTIONS_PER_CHECK,
            MENTIONS_MAX_PAGES_PER_CHECK=b.MENTIONS_MAX_PAGES_PER_CHECK,
            MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT=b.MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT,
            MY_USER_ID=b.MY_USER_ID,
            _MentionBacklogContinuationLimit=b._MentionBacklogContinuationLimit,
            api_error_is_invalid_pagination_cursor=b.api_error_is_invalid_pagination_cursor,
            tweets=tweets,
            log=b.log,
            log_event=b.log_event,
            log_json_debug=b.log_json_debug,
            now_epoch=b.now_epoch,
            mention_queue=mention_queue,
            reply_evaluations=reply_evaluations,
            save_state=b.save_state,
            valid_tweets_sorted_by_id=b.valid_tweets_sorted_by_id,
            x_paginated_get=b.x_paginated_get,
            x_request=b.x_request,
            api_error_is_permanent_target_failure=b.api_error_is_permanent_target_failure,
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
        b = self.bindings
        if tweets is None:
            tweets = b._tweet_lookup_cache_owner()
        if drafts is None:
            drafts = self._reply_draft_owner()
        if cooldowns is None:
            cooldowns = b._api_cooldown_owner()
        if controls is None:
            controls = b._runtime_controls_owner()
        if watch_posts is None:
            watch_posts = self._quote_watch_posts_owner(tweets=tweets)
        if reply_evaluations is None:
            reply_evaluations = self._reply_evaluation_owner()
        return functools.partial(
            _hot_post_discovery.get_hot_post_reply_candidates,
            ApiError=b.ApiError,
            ENABLE_HOT_POST_REPLY_CHECKS=b.ENABLE_HOT_POST_REPLY_CHECKS,
            EXTRA_QUOTE_WATCH_FILE=b.EXTRA_QUOTE_WATCH_FILE,
            HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS=b.HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS,
            HOT_POST_REPLY_SEARCH_API_MAX_RESULTS=b.HOT_POST_REPLY_SEARCH_API_MAX_RESULTS,
            HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK=b.HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK,
            HOT_POST_REPLY_USE_SINCE_ID=b.HOT_POST_REPLY_USE_SINCE_ID,
            MAX_HOT_POST_REPLIES_PER_CHECK=b.MAX_HOT_POST_REPLIES_PER_CHECK,
            MY_USER_ID=b.MY_USER_ID,
            tweets=tweets,
            retire_ineligible_draft=drafts.retire_ineligible,
            cooldowns=cooldowns,
            controls=controls,
            watch_posts=watch_posts,
            log=b.log,
            log_event=b.log_event,
            log_json_debug=b.log_json_debug,
            mark_hot_post_reply_skipped=b.mark_hot_post_reply_skipped,
            reply_evaluations=reply_evaluations,
            reply_target_is_directly_eligible=b.reply_target_is_directly_eligible,
            save_state=b.save_state,
            valid_tweets_sorted_by_id=b.valid_tweets_sorted_by_id,
            x_paginated_get=b.x_paginated_get,
            x_quote_lookup_request=b.x_quote_lookup_request,
        )

    def _quote_watch_posts_owner(
        self, *, tweets: _tweet_lookup_cache.TweetLookupCache | None = None
    ) -> _quote_discovery.QuoteWatchPosts:
        """Bind current watch selection settings without reading the watch file."""
        b = self.bindings
        if tweets is None:
            tweets = b._tweet_lookup_cache_owner()
        return _quote_discovery.QuoteWatchPosts(
            watch_file=b.EXTRA_QUOTE_WATCH_FILE,
            maximum_extra_posts=b.MAX_EXTRA_QUOTE_WATCH_POSTS,
            maximum_posts=b.MAX_QUOTE_POSTS_PER_CHECK,
            lookback_posts=b.QUOTE_POST_LOOKBACK_MAIN_POSTS,
            tweets=tweets,
            log=b.log,
        )

    def get_quote_tweets_for_posts(
        self, post_ids: list[str], state: dict | None = None
    ) -> dict[str, list[dict]]:
        """Search watched originals together using current root dependencies."""
        b = self.bindings
        return _quote_discovery.get_quote_tweets_for_posts(
            post_ids,
            state,
            QUOTE_LOOKUP_API_MAX_RESULTS=b.QUOTE_LOOKUP_API_MAX_RESULTS,
            QUOTE_LOOKUP_MAX_PAGES_PER_POST=b.QUOTE_LOOKUP_MAX_PAGES_PER_POST,
            log=b.log,
            save_state=b.save_state,
            x_paginated_get=b.x_paginated_get,
            x_quote_lookup_request=b.x_quote_lookup_request,
        )

    def get_quote_tweets_for_post(
        self, post_id: str, state: dict | None = None,
    ) -> list[dict]:
        """Discover quotes for one original with current external boundaries."""
        b = self.bindings
        return _quote_discovery.get_quote_tweets_for_post(
            post_id,
            state,
            QUOTE_LOOKUP_API_MAX_RESULTS=b.QUOTE_LOOKUP_API_MAX_RESULTS,
            QUOTE_LOOKUP_MAX_PAGES_PER_POST=b.QUOTE_LOOKUP_MAX_PAGES_PER_POST,
            QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS=b.QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS,
            log=b.log,
            log_event=b.log_event,
            log_json_debug=b.log_json_debug,
            normalise_quote_repeated_cursor_suppressions=b.normalise_quote_repeated_cursor_suppressions,
            now_epoch=b.now_epoch,
            quote_repeated_cursor_suppression_record=b.quote_repeated_cursor_suppression_record,
            save_state=b.save_state,
            x_paginated_get=b.x_paginated_get,
            x_quote_lookup_request=b.x_quote_lookup_request,
        )
