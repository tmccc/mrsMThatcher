"""Own normal mention and hot-post reply orchestration.

The runner holds stable collaborators for one pass. Caller state, candidate snapshots
and model-budget progress remain local to ``run``. A continuation result asks the
reply assembly to construct a fresh pass after a durable queue drain. Imports
perform no runtime I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from typing import TYPE_CHECKING, Any

from mrs_bot_runtime_state_helpers import append_unique_durable

from mrs_bot_author_quarantines import clear_author_evaluation_quarantine_history
from mrs_bot_mention_authority import mention_receipt_pagination
from mrs_bot_daily_reply_accounting import daily_author_reply_counts
from mrs_bot_reply_context import trim_context_text
from mrs_bot_reply_generation import _is_terminal_candidate_local_failure
from mrs_bot_reply_cycle_interfaces import (
    NORMAL_CHECK_STATUS_API_ERROR,
    NORMAL_CHECK_STATUS_CHECKED,
    NORMAL_CHECK_STATUS_DISABLED,
    NORMAL_CHECK_STATUS_POSTED,
    NORMAL_CHECK_STATUS_SKIPPED_CAP,
    NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN,
    NORMAL_CHECK_STATUS_SKIPPED_SPACING,
    FinishReplyCheck,
    LogReplyEvent,
    LogReplyPostingOutcome,
    LogValidatedReply,
    MarkHotPostSkipped,
    NormalReplyConfig,
    PreparedReplyContext,
    ReplyCandidateDiscovery,
    ReplyCycleDelivery,
    ReplyCyclePersistence,
    SkipReplyCandidate,
    SortRawTweets,
)
from mrs_bot_reply_delivery import ReplyDeliveryStop
from mrs_bot_reply_evaluation_state import (
    completed_mention_watermark_covers_target,
    terminal_reply_evaluation,
)
from mrs_bot_reply_preparation import (
    build_sending_reply_receipt,
    persist_validated_reply_draft,
)
from mrs_bot_reply_state import handled_reply_target_ids, retire_ineligible_reply_draft
from mrs_bot_reply_outcomes import outcome_disposition

if TYPE_CHECKING:
    from mrs_bot_core_contracts import BotState
    from mrs_bot_core_contracts import ConfirmedReplyReceipt, ReplyContextData, ReplyMediaContext
    from mrsMThatcher2 import ApiError as ApiErrorValue
    from mrs_bot_api_cooldowns import ApiCooldowns
    from mrs_bot_mention_discovery import MentionQueue
    from mrs_bot_author_quarantines import AuthorQuarantines
    from mrs_bot_daily_reply_accounting import DailyReplyAccounting
    from mrs_bot_reply_clarifications import ClarificationReplies
    from mrs_bot_reply_evaluation_state import ReplyEvaluations
    from mrs_bot_reply_context import ReplyContext
    from mrs_bot_reply_generation import ReplyGeneration
    from mrs_bot_reply_history import ReplyHistory
    from mrs_bot_tweet_lookup_cache import TweetLookupCache
    from mrs_bot_runtime_control import RuntimeControls
    from single_call_reply import PipelineOutcome, PipelineResult, ValidatedReply as ValidatedReplyValue


@dataclass(frozen=True)
class _ReplyCandidate:
    """Keep the original candidate and its once-read identity/source attribution."""

    mention: dict[str, Any]
    mention_id: str
    author_id: str
    incoming_text: str
    source: str
    log_source: str


@dataclass
class _ReplyCycleProgress:
    """Track the model budget and deferred bookkeeping across candidates."""

    fresh_mention_ai_evaluations: int
    # Quarantine retirement changed state that still needs a durable save.
    quarantine_retirements_pending: bool = False
    # A terminal evaluation was recorded without pruning the full record set.
    evaluation_record_pruning_pending: bool = False

    def prune_quarantine_retirement_batch(
        self,
        state: BotState,
        reply_evaluations: ReplyEvaluations,
    ) -> None:
        """Prune the pending batch before its caller durably saves the state."""
        if self.evaluation_record_pruning_pending:
            reply_evaluations.prune(state)
        else:
            reply_evaluations.prune_completed_mentions(state)
        self.evaluation_record_pruning_pending = False

    def flush_quarantine_retirements(
        self,
        state: BotState,
        reply_evaluations: ReplyEvaluations,
        persistence: ReplyCyclePersistence,
    ) -> None:
        """Save retirements, clearing each flag only after its step succeeds."""
        if not self.quarantine_retirements_pending:
            return
        self.prune_quarantine_retirement_batch(state, reply_evaluations)
        persistence.save(state, durable=True)
        self.quarantine_retirements_pending = False


@dataclass(frozen=True)
class ContinueNormalReplyPass:
    """Request another freshly assembled pass with the accumulated budget."""

    fresh_evaluations: int
    skip_hot_post_fetch: bool


class NormalReplyCycle:
    """Run one reply-lane pass with construction-time collaborators."""

    def __init__(
        self,
        *,
        delivery: ReplyCycleDelivery,
        author_quarantines: AuthorQuarantines,
        ApiError: type[ApiErrorValue],
        config: NormalReplyConfig,
        PipelineResult: type[PipelineResult],
        RemoteOperationsPaused: type[Exception],
        ReplyEvidenceUnavailable: type[Exception],
        SINGLE_CALL_STRATEGY_VERSION: str,
        ValidatedReply: type[ValidatedReplyValue],
        _log_validated_single_call_reply: LogValidatedReply,
        generation: ReplyGeneration,
        reply_contexts: ReplyContext,
        tweets: TweetLookupCache,
        clarifications: ClarificationReplies,
        persistence: ReplyCyclePersistence,
        conversational_reply_pipeline_enabled: Callable[[], bool],
        accounting: DailyReplyAccounting,
        dedupe_reply_candidates: Callable[
            [list[dict[str, Any]], list[dict[str, Any]]], list[dict[str, Any]]
        ],
        get_hot_post_reply_candidates: ReplyCandidateDiscovery,
        get_mentions: ReplyCandidateDiscovery,
        cooldowns: ApiCooldowns,
        is_probably_spam_or_not_worth_replying: Callable[[str], bool],
        controls: RuntimeControls,
        log: Logger,
        log_ai_reply_posting_outcome: LogReplyPostingOutcome,
        log_event: LogReplyEvent,
        mention_queue: MentionQueue,
        maybe_mark_hot_post_reply_skipped: MarkHotPostSkipped,
        now_epoch: Callable[[], int],
        reply_evaluations: ReplyEvaluations,
        history: ReplyHistory,
        reply_evidence_repository: Callable[[], object],
        reply_target_is_directly_eligible: Callable[[dict[str, Any]], bool],
        valid_tweets_sorted_by_id: SortRawTweets,
    ) -> None:
        """Bind stable collaborators for one normal reply pass."""
        self.delivery = delivery
        self.author_quarantines = author_quarantines
        self.ApiError = ApiError
        self.config = config
        self.PipelineResult = PipelineResult
        self.RemoteOperationsPaused = RemoteOperationsPaused
        self.ReplyEvidenceUnavailable = ReplyEvidenceUnavailable
        self.SINGLE_CALL_STRATEGY_VERSION = SINGLE_CALL_STRATEGY_VERSION
        self.ValidatedReply = ValidatedReply
        self._log_validated_single_call_reply = _log_validated_single_call_reply
        self.generation = generation
        self.reply_contexts = reply_contexts
        self.tweets = tweets
        self.clarifications = clarifications
        self.persistence = persistence
        self.conversational_reply_pipeline_enabled = (
            conversational_reply_pipeline_enabled
        )
        self.accounting = accounting
        self.dedupe_reply_candidates = dedupe_reply_candidates
        self.get_hot_post_reply_candidates = get_hot_post_reply_candidates
        self.get_mentions = get_mentions
        self.cooldowns = cooldowns
        self.is_probably_spam_or_not_worth_replying = (
            is_probably_spam_or_not_worth_replying
        )
        self.controls = controls
        self.log = log
        self.log_ai_reply_posting_outcome = log_ai_reply_posting_outcome
        self.log_event = log_event
        self.mention_queue = mention_queue
        self.maybe_mark_hot_post_reply_skipped = maybe_mark_hot_post_reply_skipped
        self.now_epoch = now_epoch
        self.reply_evaluations = reply_evaluations
        self.history = history
        self.reply_evidence_repository = reply_evidence_repository
        self.reply_target_is_directly_eligible = reply_target_is_directly_eligible
        self.valid_tweets_sorted_by_id = valid_tweets_sorted_by_id

    def run(
        self,
        state: BotState,
        *,
        _fresh_mention_ai_evaluations: int = 0,
        _skip_hot_post_fetch: bool = False,
    ) -> str | ContinueNormalReplyPass:
        """Process eligible mention and hot-post candidates under all reply limits."""
        self.log.info("Starting mention reply check")
        # A confirmed reply receipt and its transport journal are a recoverable
        # local transaction, not permission for a new remote write.  Reconcile it
        # before the general journal barrier so a restart can finish the exact
        # durable transaction without first weakening that barrier.
        self.accounting.reset(state)
        prior_reply_status, _prior_reply = self.delivery.load_receipt()
        if prior_reply_status == "valid" and self.delivery.reconcile_receipt(state):
            self.log.warning(
                "Reconciled confirmed reply receipt before checking new mention candidates"
            )
        self.delivery.block_ambiguous()

        if not self.config.enabled:
            self.log.info("Auto replies disabled")
            return NORMAL_CHECK_STATUS_DISABLED

        if not self.conversational_reply_pipeline_enabled():
            self.log.info(
                "Conversational reply pipeline disabled; skipping mention/hot-post checks"
            )
            return NORMAL_CHECK_STATUS_DISABLED

        if self.controls.lane_paused("disable_replies", "disable_normal_replies"):
            self.log.info(
                "Skipping mention/hot-post reply check due to runtime control file"
            )
            return NORMAL_CHECK_STATUS_DISABLED

        if self.cooldowns.active(state):
            self.log.info("Skipping mention check due to X read API cooldown")
            return NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN
        if self.cooldowns.active(state, scope="write"):
            self.log.info("Skipping mention check due to X write API cooldown")
            return NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN
        if self.cooldowns.active(state, scope="openai"):
            self.log.info("Skipping mention check due to OpenAI API cooldown")
            return NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN

        daily_replied_author_counts = daily_author_reply_counts(state)

        self.log.debug(
            "Reply cap status: daily_reply_count=%s max=%s",
            state.get("daily_reply_count"),
            self.config.maximum_daily_replies,
        )
        self.log.debug(
            "Daily per-author cap status: authors_replied_today=%d max_per_author=%s",
            len(daily_replied_author_counts),
            self.config.maximum_daily_author_replies,
        )

        if state["daily_reply_count"] >= self.config.maximum_daily_replies:
            self.log.info("Daily generated/replied cap reached")
            self.persistence.save(state)
            return NORMAL_CHECK_STATUS_SKIPPED_CAP

        current = self.now_epoch()
        if self.author_quarantines.prune(state, current_epoch=current):
            self.persistence.save(state)

        seconds_since_last_reply = current - int(state.get("last_reply_epoch", 0))
        self.log.debug(
            "Seconds since last generated/replied=%s minimum=%s",
            seconds_since_last_reply,
            self.config.minimum_reply_spacing,
        )

        if seconds_since_last_reply < self.config.minimum_reply_spacing:
            self.log.info(
                "Skipping mention check: minimum interval between replies not reached"
            )
            return NORMAL_CHECK_STATUS_SKIPPED_SPACING

        started_with_pending_mentions = bool(self.mention_queue.pending(state))
        try:
            mentions = self.get_mentions(state)
        except self.ApiError as e:
            self.log.exception("Failed to get mention reply candidates")
            self.cooldowns.record_error(state, e, "x")
            self.persistence.save(state)
            return NORMAL_CHECK_STATUS_API_ERROR
        except Exception:
            self.log.exception("Unexpected failure getting mention reply candidates")
            self.persistence.save(state)
            return NORMAL_CHECK_STATUS_API_ERROR

        if _skip_hot_post_fetch:
            hot_post_replies = []
        else:
            try:
                hot_post_replies = self.get_hot_post_reply_candidates(state)
            except self.ApiError as e:
                self.log.exception(
                    "Failed to get optional hot-post reply candidates; continuing with mentions"
                )
                self.cooldowns.record_error(state, e, "x", scope="quote")
                self.persistence.save(state)
                hot_post_replies = []
            except Exception:
                self.log.exception(
                    "Unexpected failure getting optional hot-post reply candidates; continuing with mentions"
                )
                self.persistence.save(state)
                hot_post_replies = []

        mentions = self.dedupe_reply_candidates(mentions, hot_post_replies)

        if not mentions:
            self.log.info("No mention or hot-post reply candidates returned")
            return NORMAL_CHECK_STATUS_CHECKED

        mentions = self.valid_tweets_sorted_by_id(
            mentions, context="mention/hot-post candidate"
        )

        replied_to_ids = handled_reply_target_ids(state)
        self.log.debug("replied_to_ids count=%d", len(replied_to_ids))

        progress = _ReplyCycleProgress(int(_fresh_mention_ai_evaluations))

        for mention in mentions:
            if self.cooldowns.active(state, scope="openai"):
                self.log.info(
                    "Stopping mention/hot-post candidate iteration because the "
                    "OpenAI cooldown became active"
                )
                progress.flush_quarantine_retirements(
                    state, self.reply_evaluations, self.persistence
                )
                self.persistence.save(state, durable=True)
                return NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN
            mention_id = str(mention["id"])
            author_id = str(mention.get("author_id"))
            incoming_text = mention.get("text", "")
            candidate_source = mention.get("_source", "mention")
            candidate_log_source = candidate_source
            if candidate_source == "mention" and mention.get("_also_hot_post_reply"):
                candidate_log_source = "mention+hot_post_reply"

            self.log.info(
                "Considering %s id=%s author_id=%s text=%r",
                candidate_log_source,
                mention_id,
                author_id,
                incoming_text,
            )

            candidate = _ReplyCandidate(
                mention,
                mention_id,
                author_id,
                incoming_text,
                candidate_source,
                candidate_log_source,
            )

            eligible = self._candidate_is_eligible(
                state, candidate, replied_to_ids, progress
            )
            if not eligible:
                continue

            try:
                clarification = self.clarifications.context(
                    state, mention, current=current
                )
            except self.RemoteOperationsPaused:
                progress.flush_quarantine_retirements(
                    state, self.reply_evaluations, self.persistence
                )
                self.persistence.save(state, durable=True)
                return NORMAL_CHECK_STATUS_CHECKED
            except self.ApiError as exc:
                self.log.exception(
                    "Could not refresh original clarification question for mention %s",
                    mention_id,
                )
                self.cooldowns.record_error(state, exc, "x")
                progress.flush_quarantine_retirements(
                    state, self.reply_evaluations, self.persistence
                )
                self.persistence.save(state, durable=True)
                return NORMAL_CHECK_STATUS_API_ERROR
            eligible = self._author_allows_evaluation(
                state, candidate, clarification, current, progress
            )
            if not eligible:
                continue

            progress.flush_quarantine_retirements(
                state, self.reply_evaluations, self.persistence
            )
            context_result = self._prepare_reply_context(
                state, candidate, clarification
            )
            if isinstance(context_result, FinishReplyCheck):
                return context_result.status
            if isinstance(context_result, SkipReplyCandidate):
                continue
            reply_context = context_result.context

            evaluation_result = self._evaluate_reply(
                state, candidate, reply_context, context_result.media_context, progress
            )
            if isinstance(evaluation_result, FinishReplyCheck):
                return evaluation_result.status
            if isinstance(evaluation_result, SkipReplyCandidate):
                continue
            disposition = outcome_disposition(evaluation_result)
            if disposition != "ready" or evaluation_result.status != "reply":
                outcome = self._retire_or_defer_no_reply(
                    state, candidate, evaluation_result, current
                )
                if isinstance(outcome, FinishReplyCheck):
                    return outcome.status
                continue
            reply_text = evaluation_result.reply

            receipt_template = self._prepare_reply_receipt(
                state, candidate, reply_text, reply_context, clarification
            )
            if isinstance(receipt_template, FinishReplyCheck):
                return receipt_template.status

            receipt = self._deliver_reply(
                state, candidate, replied_to_ids, reply_text, receipt_template
            )
            if isinstance(receipt, FinishReplyCheck):
                return receipt.status

            status = self._finalise_confirmed_reply(state, candidate, receipt)
            return status

        if progress.quarantine_retirements_pending:
            progress.flush_quarantine_retirements(
                state, self.reply_evaluations, self.persistence
            )
        else:
            self.persistence.save(state)
        if (
            started_with_pending_mentions
            and not self.mention_queue.pending(state)
            and state.get("mention_backlog")
            and progress.fresh_mention_ai_evaluations
            < self.config.maximum_fresh_evaluations
        ):
            self.log.info(
                "Durable pending mention queue drained; resuming backlog within the same check"
            )
            return ContinueNormalReplyPass(progress.fresh_mention_ai_evaluations, True)
        self.log.info("Mention reply check finished with no reply generated/posted")
        return NORMAL_CHECK_STATUS_CHECKED

    def _candidate_is_eligible(
        self,
        state: BotState,
        candidate: _ReplyCandidate,
        replied_to_ids: set[str],
        progress: _ReplyCycleProgress,
    ) -> bool:
        """Retire already handled and directly ineligible targets before context work."""
        if candidate.mention_id in replied_to_ids:
            self.log.info(
                "Skipping %s %s: already replied to",
                candidate.source,
                candidate.mention_id,
            )
            self.maybe_mark_hot_post_reply_skipped(
                state, candidate.mention, reason="already_replied"
            )
            self.log_event(
                "candidate_skipped",
                lane=candidate.log_source,
                id=candidate.mention_id,
                reason="already_replied",
            )
            self.mention_queue.mark_seen(state, candidate.mention)
            return False

        prior_evaluation = terminal_reply_evaluation(state, candidate.mention_id)
        if prior_evaluation is not None:
            prior_outcome = str(prior_evaluation.get("outcome") or "no_reply")
            self.log.info(
                "Skipping %s %s: terminal %s evaluation already recorded reason=%s",
                candidate.source,
                candidate.mention_id,
                prior_outcome,
                prior_evaluation.get("reason", ""),
            )
            skip_reason = f"already_evaluated_{prior_outcome}"
            self.maybe_mark_hot_post_reply_skipped(
                state, candidate.mention, reason=skip_reason
            )
            self.log_event(
                "candidate_skipped",
                lane=candidate.log_source,
                id=candidate.mention_id,
                reason=skip_reason,
            )
            self.mention_queue.mark_seen(state, candidate.mention)
            self.persistence.save(state)
            return False

        if candidate.author_id == str(self.config.user_id):
            self.log.info(
                "Skipping %s %s: authored by our own account",
                candidate.source,
                candidate.mention_id,
            )
            self.maybe_mark_hot_post_reply_skipped(
                state, candidate.mention, reason="own_account"
            )
            self.log_event(
                "candidate_skipped",
                lane=candidate.log_source,
                id=candidate.mention_id,
                reason="own_account",
            )
            self.mention_queue.mark_seen(state, candidate.mention)
            return False

        if self.clarifications.thread_is_terminal(state, candidate.mention):
            self.log.info(
                "Skipping %s %s: clarification already completed and thread is terminal",
                candidate.source,
                candidate.mention_id,
            )
            self.maybe_mark_hot_post_reply_skipped(
                state, candidate.mention, reason="clarification_thread_terminal"
            )
            self.log_event(
                "candidate_skipped",
                lane=candidate.log_source,
                id=candidate.mention_id,
                reason="clarification_thread_terminal",
            )
            self.mention_queue.mark_seen(state, candidate.mention)
            self.persistence.save(state)
            return False

        if not self.reply_target_is_directly_eligible(candidate.mention):
            reason = "target_does_not_directly_mention_account"
            self.log.warning(
                "Skipping %s %s before context/media/model work: target is not directly reply-eligible",
                candidate.source,
                candidate.mention_id,
            )
            retire_ineligible_reply_draft(
                state,
                candidate.mention_id,
                str(candidate.source),
                reason=reason,
                retire_draft=self.persistence.retire_ineligible,
                record_terminal_reply_evaluation=self.reply_evaluations.record,
            )
            self.maybe_mark_hot_post_reply_skipped(
                state, candidate.mention, reason="reply_not_permitted"
            )
            self.log_event(
                "reply_target_terminal",
                lane=candidate.log_source,
                target_id=candidate.mention_id,
                outcome="reply_not_permitted",
                reason=reason,
            )
            self.mention_queue.mark_seen(state, candidate.mention)
            if progress.quarantine_retirements_pending:
                progress.prune_quarantine_retirement_batch(
                    state, self.reply_evaluations
                )
            self.persistence.save(state, durable=True)
            progress.quarantine_retirements_pending = False
            return False
        return True

    def _author_allows_evaluation(
        self,
        state: BotState,
        candidate: _ReplyCandidate,
        clarification: dict[str, Any] | None,
        current: int,
        progress: _ReplyCycleProgress,
    ) -> bool:
        """Apply the eager author/spam checks and batch mention quarantine retirements."""
        author_cap_reached = (
            self.accounting.author_count(state, candidate.author_id)
            >= self.config.maximum_daily_author_replies
        )
        local_spam_rejection = self.is_probably_spam_or_not_worth_replying(
            candidate.incoming_text
        )

        if candidate.source == "mention" and clarification is None:
            quarantine = self.author_quarantines.active(
                state,
                candidate.author_id,
                current_epoch=current,
            )
            if quarantine is not None:
                reason = "author_evaluation_quarantine"
                self.log_event(
                    "author_evaluation_quarantine_skip",
                    author_id=candidate.author_id,
                    target_id=candidate.mention_id,
                    quarantine_until_epoch=quarantine.get("quarantine_until_epoch"),
                    pipeline_evaluations_skipped=int(
                        not author_cap_reached and not local_spam_rejection
                    ),
                )
                self.maybe_mark_hot_post_reply_skipped(
                    state, candidate.mention, reason=reason
                )
                self.log_event(
                    "candidate_skipped",
                    lane=candidate.log_source,
                    id=candidate.mention_id,
                    reason=reason,
                    author_id=candidate.author_id,
                )
                self.mention_queue.mark_seen(state, candidate.mention)
                if not completed_mention_watermark_covers_target(
                    state, candidate.mention_id
                ):
                    self.reply_evaluations.record(
                        state,
                        target_id=candidate.mention_id,
                        lane="mention",
                        reason=reason,
                        prune_records=False,
                        evidence_policy=(self.author_quarantines.evidence_policy),
                    )
                    progress.evaluation_record_pruning_pending = True
                progress.quarantine_retirements_pending = True
                return False

        if author_cap_reached:
            self.log.info(
                "Skipping mention %s: already reached per-author daily cap for author_id=%s",
                candidate.mention_id,
                candidate.author_id,
            )
            if not local_spam_rejection:
                self.tweets.store(
                    state,
                    tweet_id=candidate.mention_id,
                    text=candidate.incoming_text,
                    author_id=candidate.author_id,
                    conversation_id=str(
                        candidate.mention.get("conversation_id", candidate.mention_id)
                    ),
                    referenced_tweets=candidate.mention.get("referenced_tweets", []),
                    created_at=candidate.mention.get("created_at"),
                    post_type="author_cap_context",
                )
            self.maybe_mark_hot_post_reply_skipped(
                state, candidate.mention, reason="author_daily_cap"
            )
            self.log_event(
                "candidate_skipped",
                lane=candidate.log_source,
                id=candidate.mention_id,
                reason="author_daily_cap",
                author_id=candidate.author_id,
            )
            self.mention_queue.mark_seen(state, candidate.mention)
            self.persistence.save(state)
            return False

        if local_spam_rejection:
            self.log.info(
                "Skipping %s %s: spam/not worth replying",
                candidate.source,
                candidate.mention_id,
            )
            self.maybe_mark_hot_post_reply_skipped(
                state, candidate.mention, reason="spam_or_not_worth_replying"
            )
            self.log_event(
                "candidate_skipped",
                lane=candidate.log_source,
                id=candidate.mention_id,
                reason="spam_or_not_worth_replying",
            )
            self.mention_queue.mark_seen(state, candidate.mention)
            self.persistence.save(state)
            return False
        return True

    def _prepare_reply_context(
        self, state: BotState, candidate: _ReplyCandidate, clarification: dict[str, Any] | None
    ) -> PreparedReplyContext | SkipReplyCandidate | FinishReplyCheck:
        """Build canonical context and media, preserving the narrow context error boundary."""
        try:
            prepared = self.reply_contexts.build(candidate.mention, state)
        except self.RemoteOperationsPaused:
            self.log.info(
                "Deferring conversational reply evaluation lane=%s target_id=%s "
                "reason=global_runtime_control_pause",
                candidate.source,
                candidate.mention_id,
            )
            self.log_event(
                "reply_pipeline_paused",
                lane=str(candidate.source),
                target_id=candidate.mention_id,
                reason="global_runtime_control_pause",
            )
            self.persistence.save(state, durable=True)
            return FinishReplyCheck(NORMAL_CHECK_STATUS_CHECKED)
        except self.ApiError as e:
            self.log.exception(
                "Could not build context for %s %s due to API error",
                candidate.source,
                candidate.mention_id,
            )
            self.cooldowns.record_error(state, e, "x")
            self.persistence.save(state)
            return FinishReplyCheck(NORMAL_CHECK_STATUS_API_ERROR)

        if prepared is None:
            self.log.warning(
                "Retiring %s %s after a permanent canonical-context failure",
                candidate.source,
                candidate.mention_id,
            )
            self.generation.record_result(
                self.PipelineResult(
                    status="operational_failure",
                    reason="canonical_context_unavailable",
                    error_category="context_validation",
                    local_validation_status="failed",
                ),
                lane=str(candidate.source),
                target_id=candidate.mention_id,
            )
            self.reply_evaluations.record(
                state,
                target_id=candidate.mention_id,
                lane=str(candidate.source),
                reason="canonical_context_unavailable",
                outcome="operational_failure",
            )
            self.maybe_mark_hot_post_reply_skipped(
                state,
                candidate.mention,
                reason="operational_context_failure",
            )
            self.mention_queue.mark_seen(state, candidate.mention)
            self.persistence.save(state, durable=True)
            return SkipReplyCandidate()

        reply_context = prepared.context

        if clarification is not None:
            reply_context["clarification_request"] = {
                "original_question": trim_context_text(
                    clarification["question_text"],
                    self.config.incoming_max_chars,
                ),
                "correction": str(reply_context["incoming_contribution"]),
            }

        try:
            self.reply_evidence_repository()
        except self.ReplyEvidenceUnavailable as exc:
            self.log.error(
                "Skipping conversational reply target_id=%s because local evidence is unavailable: %s",
                candidate.mention_id,
                exc,
            )
            self.log_event(
                "reply_evidence_unavailable",
                lane=str(candidate.source),
                target_id=candidate.mention_id,
            )
            return FinishReplyCheck(NORMAL_CHECK_STATUS_CHECKED)

        return prepared

    def _evaluate_reply(
        self,
        state: BotState,
        candidate: _ReplyCandidate,
        reply_context: ReplyContextData,
        media_context: ReplyMediaContext | None,
        progress: _ReplyCycleProgress,
    ) -> PipelineOutcome | SkipReplyCandidate | FinishReplyCheck:
        """Recover or generate a draft, charging only fresh mention model evaluations."""
        evaluation = self.persistence.recover(
            state,
            candidate.mention_id,
            str(candidate.source),
            context=reply_context,
            recent_replies=self.history.recovery_replies(
                state,
                context=reply_context,
            ),
        )
        try:
            if evaluation is None or evaluation.status == "draft_discarded":
                if (
                    candidate.source == "mention"
                    and progress.fresh_mention_ai_evaluations
                    >= self.config.maximum_fresh_evaluations
                ):
                    self.log.info(
                        "Deferring mention %s: fresh model evaluation budget "
                        "exhausted (%s)",
                        candidate.mention_id,
                        self.config.maximum_fresh_evaluations,
                    )
                    self.log_event(
                        "mention_candidate_deferred",
                        target_id=candidate.mention_id,
                        author_id=candidate.author_id,
                        reason="fresh_model_evaluation_budget_exhausted",
                    )
                    return SkipReplyCandidate()
                if candidate.source == "mention":
                    progress.fresh_mention_ai_evaluations += 1
                evaluation = self.generation.evaluate(
                    reply_context,
                    media_context,
                    state=state,
                )
                if candidate.source == "mention" and evaluation.model_call_count == 0:
                    progress.fresh_mention_ai_evaluations -= 1
            elif evaluation.reply is not None:
                self.log.info(
                    "Reusing persisted single-call reply draft target_id=%s source=%s",
                    candidate.mention_id,
                    candidate.source,
                )
        except self.RemoteOperationsPaused:
            self.log.info(
                "Deferring conversational reply evaluation lane=%s target_id=%s "
                "reason=global_runtime_control_pause",
                candidate.source,
                candidate.mention_id,
            )
            self.log_event(
                "reply_pipeline_paused",
                lane=str(candidate.source),
                target_id=candidate.mention_id,
                reason="global_runtime_control_pause",
            )
            self.persistence.save(state, durable=True)
            return FinishReplyCheck(NORMAL_CHECK_STATUS_CHECKED)
        except self.ApiError as exc:
            self.log.exception("OpenAI single-call reply failed")
            if exc.service == "openai":
                self.cooldowns.record_error(state, exc, "openai")
            self.persistence.save(state)
            return FinishReplyCheck(NORMAL_CHECK_STATUS_API_ERROR)
        except Exception:
            self.log.exception("Unexpected single-call reply failure")
            self.persistence.save(state)
            return FinishReplyCheck(NORMAL_CHECK_STATUS_API_ERROR)
        return evaluation

    def _retire_or_defer_no_reply(
        self,
        state: BotState,
        candidate: _ReplyCandidate,
        evaluation: PipelineOutcome,
        current: int,
    ) -> SkipReplyCandidate | FinishReplyCheck:
        """Distinguish terminal local/editorial outcomes from retryable evaluation failures."""
        if _is_terminal_candidate_local_failure(evaluation):
            failure_category = str(evaluation.error_category)
            failure_reason = str(evaluation.reason or failure_category)
            self.log.warning(
                "Retiring %s %s after permanent candidate-local reply "
                "failure category=%s reason=%s",
                candidate.source,
                candidate.mention_id,
                failure_category,
                failure_reason,
            )
            self.reply_evaluations.record(
                state,
                target_id=candidate.mention_id,
                lane=str(candidate.source),
                reason=failure_reason,
                outcome="operational_failure",
            )
            skip_reason = f"operational_{failure_category}"
            self.maybe_mark_hot_post_reply_skipped(
                state,
                candidate.mention,
                reason=skip_reason,
            )
            self.log_event(
                "candidate_skipped",
                lane=candidate.log_source,
                id=candidate.mention_id,
                reason=skip_reason,
                author_id=candidate.author_id,
            )
            self.mention_queue.mark_seen(state, candidate.mention)
            self.persistence.save(state, durable=True)
            return SkipReplyCandidate()
        if outcome_disposition(evaluation) != "no_reply":
            self.log.warning(
                "Deferring %s %s after operational reply failure reason=%s",
                candidate.source,
                candidate.mention_id,
                evaluation.reason or "unknown",
            )
            self.persistence.save(state, durable=True)
            return FinishReplyCheck(NORMAL_CHECK_STATUS_API_ERROR)
        reason_code = str(
            evaluation.reason_code or evaluation.reason or "model_selected_no_reply"
        )
        self.reply_evaluations.record(
            state,
            target_id=candidate.mention_id,
            lane=str(candidate.source),
            reason=reason_code,
        )
        if candidate.source == "mention" and reason_code == "spam_or_abuse":
            self.author_quarantines.record_no_reply(
                state,
                candidate.author_id,
                current_epoch=current,
                explicit_spam_or_abuse=True,
            )
        self.log.info(
            "Sol selected no_reply for %s %s reason=%s",
            candidate.source,
            candidate.mention_id,
            reason_code,
        )
        self.maybe_mark_hot_post_reply_skipped(
            state,
            candidate.mention,
            reason=f"editorial_no_reply:{reason_code}",
        )
        self.mention_queue.mark_seen(state, candidate.mention)
        self.persistence.save(state, durable=True)
        return SkipReplyCandidate()

    def _prepare_reply_receipt(
        self,
        state: BotState,
        candidate: _ReplyCandidate,
        reply_text: ValidatedReplyValue,
        reply_context: ReplyContextData,
        clarification: dict[str, Any] | None,
    ) -> dict[str, Any] | FinishReplyCheck:
        """Persist the validated draft and bind receipt provenance before transport handling."""
        if not isinstance(reply_text, self.ValidatedReply):
            self.log.error(
                "Single-call pipeline returned an unvalidated reply type; "
                "deferring target_id=%s source=%s",
                candidate.mention_id,
                candidate.source,
            )
            self.persistence.save(state, durable=True)
            return FinishReplyCheck(NORMAL_CHECK_STATUS_API_ERROR)
        if candidate.source == "mention":
            clear_author_evaluation_quarantine_history(state, candidate.author_id)

        self._log_validated_single_call_reply(
            target_description="target",
            target_id=candidate.mention_id,
            reply=reply_text,
        )

        def log_validation_failure() -> None:
            """Retain the normal lane's persistence-validation log format."""
            self.log.error(
                "Single-call reply draft failed persistence validation; "
                "deferring target_id=%s source=%s",
                candidate.mention_id,
                candidate.source,
            )

        if not persist_validated_reply_draft(
            state,
            candidate.mention_id,
            candidate.source,
            reply_text,
            reply_context,
            SINGLE_CALL_STRATEGY_VERSION=self.SINGLE_CALL_STRATEGY_VERSION,
            persistence=self.persistence,
            log_validation_failure=log_validation_failure,
            log_event=self.log_event,
        ):
            return FinishReplyCheck(NORMAL_CHECK_STATUS_API_ERROR)
        receipt_template = build_sending_reply_receipt(
            {
                "target_id": candidate.mention_id,
                "author_id": candidate.author_id,
                "candidate_source": candidate.source,
                "conversation_id": str(
                    candidate.mention.get("conversation_id", candidate.mention_id)
                ),
                "reply_text": reply_text,
            },
            reply_context,
        )
        mention_pagination = mention_receipt_pagination(
            state,
            candidate.mention,
            candidate.source,
        )
        if mention_pagination is not None:
            receipt_template["mention_pagination"] = mention_pagination
        if clarification is not None:
            receipt_template["clarification_reply"] = {
                key: clarification[key]
                for key in (
                    "thread_id",
                    "prior_bot_reply_id",
                    "original_question_id",
                    "trigger",
                )
            }

        receipt_template = self.delivery.bind_attempt(receipt_template)
        return receipt_template

    def _retire_terminal_target(
        self,
        state: BotState,
        candidate: _ReplyCandidate,
        replied_to_ids: set[str],
        reply_text: ValidatedReplyValue,
        *,
        failure_reason: str,
        reason: str,
    ) -> None:
        """Record a terminal mention/hot-post outcome and durably retire its draft."""
        self.log_ai_reply_posting_outcome(
            reply=reply_text,
            status="posting_failed_terminal",
            lane=str(candidate.source),
            target_id=candidate.mention_id,
            failure_reason=failure_reason,
        )
        self.reply_evaluations.record(
            state,
            target_id=candidate.mention_id,
            lane=str(candidate.source),
            reason=reason,
            outcome="reply_not_permitted",
        )
        self.log_event(
            "reply_target_terminal",
            lane=candidate.log_source,
            target_id=candidate.mention_id,
            outcome="reply_not_permitted",
            reason=reason,
        )
        replied_to_ids.add(candidate.mention_id)
        self.persistence.clear(state, candidate.mention_id, str(candidate.source))
        state["replied_to_ids"] = append_unique_durable(
            state.get("replied_to_ids", []),
            candidate.mention_id,
        )
        self.mention_queue.mark_seen(state, candidate.mention)
        self.persistence.save(state, durable=True)

    def _deliver_reply(
        self,
        state: BotState,
        candidate: _ReplyCandidate,
        replied_to_ids: set[str],
        reply_text: ValidatedReplyValue,
        receipt_template: dict[str, Any],
    ) -> ConfirmedReplyReceipt | FinishReplyCheck:
        """Deliver through the shared boundary, retaining normal-lane retirement and statuses."""

        def retire_terminal_target(failure_reason: str) -> None:
            if failure_reason == "target_unavailable_pre_send":
                self.log.warning(
                    "Cannot reply to mention %s because it disappeared after "
                    "evaluation; marking it handled without consuming reply quota",
                    candidate.mention_id,
                )
            else:
                self.log.warning(
                    "Cannot reply to mention %s because X says replies are not allowed; "
                    "marking mention as handled without consuming reply quota",
                    candidate.mention_id,
                )
            self._retire_terminal_target(
                state,
                candidate,
                replied_to_ids,
                reply_text,
                failure_reason=failure_reason,
                reason=f"x_{failure_reason}",
            )

        outcome = self.delivery.deliver(
            state,
            candidate.mention_id,
            reply_text,
            receipt_template,
            lane=str(candidate.source),
            log_source=candidate.log_source,
            read_error_scope="api",
            mark_as_ai=self.config.mark_as_ai,
            retire_terminal_target=retire_terminal_target,
        )
        if outcome is ReplyDeliveryStop.TERMINAL or outcome is ReplyDeliveryStop.PAUSED:
            return FinishReplyCheck(NORMAL_CHECK_STATUS_CHECKED)
        if outcome is ReplyDeliveryStop.RETRYABLE:
            return FinishReplyCheck(NORMAL_CHECK_STATUS_API_ERROR)
        return outcome

    def _finalise_confirmed_reply(
        self, state: BotState, candidate: _ReplyCandidate, receipt: ConfirmedReplyReceipt
    ) -> str:
        """Finish the shared confirmation transaction and report this lane's success."""
        own_reply_id = self.delivery.finalise(
            state,
            receipt,
            target_id=candidate.mention_id,
            quote_reply=False,
        )

        self.log_event(
            "reply_posted",
            lane=candidate.log_source,
            target_id=candidate.mention_id,
            author_id=candidate.author_id,
            reply_post_id=own_reply_id,
            daily_reply_count=state.get("daily_reply_count"),
        )
        self.log.info("Reply posted successfully")
        return NORMAL_CHECK_STATUS_POSTED
