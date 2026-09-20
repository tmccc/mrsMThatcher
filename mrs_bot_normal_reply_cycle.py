"""Run the normal mention and hot-post reply cycle through current root authority.

The root supplies typed settings, persistence and delivery boundaries,
context, lookup, generation, history, quarantine, evaluation, clarification and
accounting owners, plus directly composed mention/hot-post discovery operations,
current policy callbacks, logger and application classes on each invocation.
Private helpers call owners directly and separate candidate eligibility,
context/model evaluation, draft/receipt preparation and delivery/recovery. The
cycle retains operation order, shared budget/quarantine progress, receipt
durability and error routing. Backlog continuation calls the supplied current
root maybe_reply_to_mentions callback with the original state.

Discovery retains X transport and pagination callbacks while using mention
authority, queue, tweet-cache, evaluation and watched-post owners directly.
Counters/watermarks/quarantine policy, pipeline/evidence, context/media,
persistence, reconciliation and delivery stay in their existing locations.
Explicit calls may read providers, generate a reply, save state and publish
through those callbacks. Fixed check statuses, dependency-free state helpers and
context text trimming are imported from their inert owners. Imports perform no
file, environment, provider or RNG work and retain no callbacks or configuration.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from typing import TYPE_CHECKING

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
    FinishReplyCheck, NormalReplyConfig, PreparedReplyContext,
    ReplyCycleDelivery, ReplyCyclePersistence, SkipReplyCandidate,
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

if TYPE_CHECKING:
    from mrs_bot_mention_discovery import MentionQueue
    from mrs_bot_author_quarantines import AuthorQuarantines
    from mrs_bot_daily_reply_accounting import DailyReplyAccounting
    from mrs_bot_reply_clarifications import ClarificationReplies
    from mrs_bot_reply_evaluation_state import ReplyEvaluations
    from mrs_bot_reply_context import ReplyContext
    from mrs_bot_reply_generation import ReplyGeneration
    from mrs_bot_reply_history import ReplyHistory
    from mrs_bot_tweet_lookup_cache import TweetLookupCache
    from single_call_reply import PipelineResult


@dataclass(frozen=True)
class _ReplyCandidate:
    """Keep the original candidate and its once-read identity/source attribution."""

    mention: dict
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
        state: dict,
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
        state: dict,
        reply_evaluations: ReplyEvaluations,
        persistence: ReplyCyclePersistence,
    ) -> None:
        """Save retirements, clearing each flag only after its step succeeds."""
        if not self.quarantine_retirements_pending:
            return
        self.prune_quarantine_retirement_batch(state, reply_evaluations)
        persistence.save(state, durable=True)
        self.quarantine_retirements_pending = False



def maybe_reply_to_mentions(
    state: dict,
    *,
    delivery: ReplyCycleDelivery,
    _fresh_mention_ai_evaluations: int = 0,
    _skip_hot_post_fetch: bool = False,
    author_quarantines: AuthorQuarantines,
    ApiError: type[Exception],
    config: NormalReplyConfig,
    PipelineResult: type,
    RemoteOperationsPaused: type[Exception],
    ReplyEvidenceUnavailable: type[Exception],
    SINGLE_CALL_STRATEGY_VERSION: str,
    ValidatedReply: type,
    _log_validated_single_call_reply: Callable,
    generation: ReplyGeneration,
    reply_contexts: ReplyContext,
    tweets: TweetLookupCache,
    clarifications: ClarificationReplies,
    persistence: ReplyCyclePersistence,
    conversational_reply_pipeline_enabled: Callable,
    accounting: DailyReplyAccounting,
    dedupe_reply_candidates: Callable,
    get_hot_post_reply_candidates: Callable,
    get_mentions: Callable,
    in_api_cooldown: Callable,
    is_probably_spam_or_not_worth_replying: Callable,
    lane_paused: Callable,
    log: Logger,
    log_ai_reply_posting_outcome: Callable,
    log_event: Callable,
    mention_queue: MentionQueue,
    maybe_mark_hot_post_reply_skipped: Callable,
    maybe_reply_to_mentions: Callable,
    now_epoch: Callable,
    reply_evaluations: ReplyEvaluations,
    record_api_error: Callable,
    history: ReplyHistory,
    reply_evidence_repository: Callable,
    reply_target_is_directly_eligible: Callable,
    valid_tweets_sorted_by_id: Callable,
) -> str:
    """Process eligible mention and hot-post candidates under all reply limits."""
    log.info("Starting mention reply check")
    # A confirmed reply receipt and its transport journal are a recoverable
    # local transaction, not permission for a new remote write.  Reconcile it
    # before the general journal barrier so a restart can finish the exact
    # durable transaction without first weakening that barrier.
    accounting.reset(state)
    prior_reply_status, _prior_reply = delivery.load_receipt()
    if prior_reply_status == "valid" and delivery.reconcile_receipt(state):
        log.warning(
            "Reconciled confirmed reply receipt before checking new mention candidates"
        )
    delivery.block_ambiguous()

    if not config.enabled:
        log.info("Auto replies disabled")
        return NORMAL_CHECK_STATUS_DISABLED

    if not conversational_reply_pipeline_enabled():
        log.info("Conversational reply pipeline disabled; skipping mention/hot-post checks")
        return NORMAL_CHECK_STATUS_DISABLED

    if lane_paused("disable_replies", "disable_normal_replies"):
        log.info("Skipping mention/hot-post reply check due to runtime control file")
        return NORMAL_CHECK_STATUS_DISABLED

    if in_api_cooldown(state):
        log.info("Skipping mention check due to X read API cooldown")
        return NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN
    if in_api_cooldown(state, scope="write"):
        log.info("Skipping mention check due to X write API cooldown")
        return NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN
    if in_api_cooldown(state, scope="openai"):
        log.info("Skipping mention check due to OpenAI API cooldown")
        return NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN

    daily_replied_author_counts = daily_author_reply_counts(state)

    log.debug(
        "Reply cap status: daily_reply_count=%s max=%s",
        state.get("daily_reply_count"),
        config.maximum_daily_replies,
    )
    log.debug(
        "Daily per-author cap status: authors_replied_today=%d max_per_author=%s",
        len(daily_replied_author_counts),
        config.maximum_daily_author_replies,
    )

    if state["daily_reply_count"] >= config.maximum_daily_replies:
        log.info("Daily generated/replied cap reached")
        persistence.save(state)
        return NORMAL_CHECK_STATUS_SKIPPED_CAP

    current = now_epoch()
    if author_quarantines.prune(state, current_epoch=current):
        persistence.save(state)

    seconds_since_last_reply = current - int(state.get("last_reply_epoch", 0))
    log.debug(
        "Seconds since last generated/replied=%s minimum=%s",
        seconds_since_last_reply,
        config.minimum_reply_spacing,
    )

    if seconds_since_last_reply < config.minimum_reply_spacing:
        log.info("Skipping mention check: minimum interval between replies not reached")
        return NORMAL_CHECK_STATUS_SKIPPED_SPACING

    started_with_pending_mentions = bool(mention_queue.pending(state))
    try:
        mentions = get_mentions(state)
    except ApiError as e:
        log.exception("Failed to get mention reply candidates")
        record_api_error(state, e, "x")
        persistence.save(state)
        return NORMAL_CHECK_STATUS_API_ERROR
    except Exception:
        log.exception("Unexpected failure getting mention reply candidates")
        persistence.save(state)
        return NORMAL_CHECK_STATUS_API_ERROR

    if _skip_hot_post_fetch:
        hot_post_replies = []
    else:
        try:
            hot_post_replies = get_hot_post_reply_candidates(state)
        except ApiError as e:
            log.exception("Failed to get optional hot-post reply candidates; continuing with mentions")
            record_api_error(state, e, "x", scope="quote")
            persistence.save(state)
            hot_post_replies = []
        except Exception:
            log.exception("Unexpected failure getting optional hot-post reply candidates; continuing with mentions")
            persistence.save(state)
            hot_post_replies = []

    mentions = dedupe_reply_candidates(mentions, hot_post_replies)

    if not mentions:
        log.info("No mention or hot-post reply candidates returned")
        return NORMAL_CHECK_STATUS_CHECKED

    mentions = valid_tweets_sorted_by_id(mentions, context="mention/hot-post candidate")

    replied_to_ids = handled_reply_target_ids(state)
    log.debug("replied_to_ids count=%d", len(replied_to_ids))

    progress = _ReplyCycleProgress(int(_fresh_mention_ai_evaluations))

    for mention in mentions:
        if in_api_cooldown(state, scope="openai"):
            log.info(
                "Stopping mention/hot-post candidate iteration because the "
                "OpenAI cooldown became active"
            )
            progress.flush_quarantine_retirements(state, reply_evaluations, persistence)
            persistence.save(state, durable=True)
            return NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN
        mention_id = str(mention["id"])
        author_id = str(mention.get("author_id"))
        incoming_text = mention.get("text", "")
        candidate_source = mention.get("_source", "mention")
        candidate_log_source = candidate_source
        if candidate_source == "mention" and mention.get("_also_hot_post_reply"):
            candidate_log_source = "mention+hot_post_reply"

        log.info(
            "Considering %s id=%s author_id=%s text=%r",
            candidate_log_source,
            mention_id,
            author_id,
            incoming_text,
        )

        candidate = _ReplyCandidate(
            mention, mention_id, author_id, incoming_text,
            candidate_source, candidate_log_source,
        )

        eligible = _candidate_is_eligible(
            state, candidate, replied_to_ids, progress,
            config=config,
            clarifications=clarifications,
            persistence=persistence, log=log, log_event=log_event,
            mention_queue=mention_queue,
            maybe_mark_hot_post_reply_skipped=maybe_mark_hot_post_reply_skipped,
            reply_evaluations=reply_evaluations,
            reply_target_is_directly_eligible=reply_target_is_directly_eligible,
        )
        if not eligible:
            continue

        try:
            clarification = clarifications.context(state, mention, current=current)
        except RemoteOperationsPaused:
            progress.flush_quarantine_retirements(state, reply_evaluations, persistence)
            persistence.save(state, durable=True)
            return NORMAL_CHECK_STATUS_CHECKED
        except ApiError as exc:
            log.exception("Could not refresh original clarification question for mention %s", mention_id)
            record_api_error(state, exc, "x")
            progress.flush_quarantine_retirements(state, reply_evaluations, persistence)
            persistence.save(state, durable=True)
            return NORMAL_CHECK_STATUS_API_ERROR
        eligible = _author_allows_evaluation(
            state, candidate, clarification, current, progress,
            author_quarantines=author_quarantines,
            config=config,
            tweets=tweets,
            accounting=accounting,
            is_probably_spam_or_not_worth_replying=is_probably_spam_or_not_worth_replying, log=log,
            log_event=log_event, mention_queue=mention_queue,
            maybe_mark_hot_post_reply_skipped=maybe_mark_hot_post_reply_skipped,
            reply_evaluations=reply_evaluations,
            persistence=persistence,
        )
        if not eligible:
            continue

        progress.flush_quarantine_retirements(state, reply_evaluations, persistence)
        context_result = _prepare_reply_context(
            state, candidate, clarification,
            ApiError=ApiError, PipelineResult=PipelineResult,
            config=config,
            RemoteOperationsPaused=RemoteOperationsPaused,
            ReplyEvidenceUnavailable=ReplyEvidenceUnavailable,
            generation=generation,
            reply_contexts=reply_contexts, log=log, log_event=log_event,
            mention_queue=mention_queue,
            maybe_mark_hot_post_reply_skipped=maybe_mark_hot_post_reply_skipped,
            record_api_error=record_api_error,
            reply_evaluations=reply_evaluations,
            reply_evidence_repository=reply_evidence_repository,
            persistence=persistence,
        )
        if isinstance(context_result, FinishReplyCheck):
            return context_result.status
        if isinstance(context_result, SkipReplyCandidate):
            continue
        reply_context = context_result.context

        evaluation_result = _evaluate_reply(
            state, candidate, reply_context, context_result.media_context, progress,
            ApiError=ApiError, config=config,
            RemoteOperationsPaused=RemoteOperationsPaused,
            generation=generation, log=log, log_event=log_event,
            persistence=persistence, record_api_error=record_api_error,
            history=history,
        )
        if isinstance(evaluation_result, FinishReplyCheck):
            return evaluation_result.status
        if isinstance(evaluation_result, SkipReplyCandidate):
            continue
        reply_text = evaluation_result.reply

        if not reply_text:
            outcome = _retire_or_defer_no_reply(
                state, candidate, evaluation_result, current,
                log_event=log_event, mention_queue=mention_queue,
                maybe_mark_hot_post_reply_skipped=maybe_mark_hot_post_reply_skipped,
                author_quarantines=author_quarantines,
                reply_evaluations=reply_evaluations,
                persistence=persistence,
                log=log,
            )
            if isinstance(outcome, FinishReplyCheck):
                return outcome.status
            continue

        receipt_template = _prepare_reply_receipt(
            state, candidate, reply_text, reply_context, clarification,
            SINGLE_CALL_STRATEGY_VERSION=SINGLE_CALL_STRATEGY_VERSION,
            ValidatedReply=ValidatedReply,
            _log_validated_single_call_reply=_log_validated_single_call_reply,
            delivery=delivery,
            log=log, log_event=log_event,
            persistence=persistence,
        )
        if isinstance(receipt_template, FinishReplyCheck):
            return receipt_template.status

        receipt = _deliver_reply(
            state, candidate, replied_to_ids, reply_text, receipt_template,
            config=config,
            persistence=persistence, log=log,
            log_ai_reply_posting_outcome=log_ai_reply_posting_outcome, log_event=log_event,
            mention_queue=mention_queue,
            delivery=delivery,
            reply_evaluations=reply_evaluations,
        )
        if isinstance(receipt, FinishReplyCheck):
            return receipt.status

        status = _finalise_confirmed_reply(
            state, candidate, receipt,
            delivery=delivery,
            log=log,
            log_event=log_event,
        )
        return status

    if progress.quarantine_retirements_pending:
        progress.flush_quarantine_retirements(state, reply_evaluations, persistence)
    else:
        persistence.save(state)
    if (
        started_with_pending_mentions
        and not mention_queue.pending(state)
        and state.get("mention_backlog")
        and progress.fresh_mention_ai_evaluations < config.maximum_fresh_evaluations
    ):
        log.info(
            "Durable pending mention queue drained; resuming backlog within the same check"
        )
        return maybe_reply_to_mentions(
            state,
            _fresh_mention_ai_evaluations=progress.fresh_mention_ai_evaluations,
            _skip_hot_post_fetch=True,
        )
    log.info("Mention reply check finished with no reply generated/posted")
    return NORMAL_CHECK_STATUS_CHECKED


def _candidate_is_eligible(
    state: dict,
    candidate: _ReplyCandidate,
    replied_to_ids: set[str],
    progress: _ReplyCycleProgress,
    *,
    config: NormalReplyConfig,
    clarifications: ClarificationReplies,
    persistence: ReplyCyclePersistence,
    log: Logger,
    log_event: Callable,
    mention_queue: MentionQueue,
    maybe_mark_hot_post_reply_skipped: Callable,
    reply_evaluations: ReplyEvaluations,
    reply_target_is_directly_eligible: Callable,
) -> bool:
    """Retire already handled and directly ineligible targets before context work."""
    if candidate.mention_id in replied_to_ids:
        log.info("Skipping %s %s: already replied to", candidate.source, candidate.mention_id)
        maybe_mark_hot_post_reply_skipped(state, candidate.mention, reason="already_replied")
        log_event("candidate_skipped", lane=candidate.log_source, id=candidate.mention_id, reason="already_replied")
        mention_queue.mark_seen(state, candidate.mention)
        return False

    prior_evaluation = terminal_reply_evaluation(state, candidate.mention_id)
    if prior_evaluation is not None:
        prior_outcome = str(prior_evaluation.get("outcome") or "no_reply")
        log.info(
            "Skipping %s %s: terminal %s evaluation already recorded reason=%s",
            candidate.source,
            candidate.mention_id,
            prior_outcome,
            prior_evaluation.get("reason", ""),
        )
        skip_reason = f"already_evaluated_{prior_outcome}"
        maybe_mark_hot_post_reply_skipped(state, candidate.mention, reason=skip_reason)
        log_event(
            "candidate_skipped",
            lane=candidate.log_source,
            id=candidate.mention_id,
            reason=skip_reason,
        )
        mention_queue.mark_seen(state, candidate.mention)
        persistence.save(state)
        return False

    if candidate.author_id == str(config.user_id):
        log.info("Skipping %s %s: authored by our own account", candidate.source, candidate.mention_id)
        maybe_mark_hot_post_reply_skipped(state, candidate.mention, reason="own_account")
        log_event("candidate_skipped", lane=candidate.log_source, id=candidate.mention_id, reason="own_account")
        mention_queue.mark_seen(state, candidate.mention)
        return False

    if clarifications.thread_is_terminal(state, candidate.mention):
        log.info(
            "Skipping %s %s: clarification already completed and thread is terminal",
            candidate.source,
            candidate.mention_id,
        )
        maybe_mark_hot_post_reply_skipped(state, candidate.mention, reason="clarification_thread_terminal")
        log_event(
            "candidate_skipped",
            lane=candidate.log_source,
            id=candidate.mention_id,
            reason="clarification_thread_terminal",
        )
        mention_queue.mark_seen(state, candidate.mention)
        persistence.save(state)
        return False

    if not reply_target_is_directly_eligible(candidate.mention):
        reason = "target_does_not_directly_mention_account"
        log.warning(
            "Skipping %s %s before context/media/model work: target is not directly reply-eligible",
            candidate.source,
            candidate.mention_id,
        )
        retire_ineligible_reply_draft(
            state,
            candidate.mention_id,
            str(candidate.source),
            reason=reason,
            retire_draft=persistence.retire_ineligible,
            record_terminal_reply_evaluation=reply_evaluations.record,
        )
        maybe_mark_hot_post_reply_skipped(state, candidate.mention, reason="reply_not_permitted")
        log_event(
            "reply_target_terminal",
            lane=candidate.log_source,
            target_id=candidate.mention_id,
            outcome="reply_not_permitted",
            reason=reason,
        )
        mention_queue.mark_seen(state, candidate.mention)
        if progress.quarantine_retirements_pending:
            progress.prune_quarantine_retirement_batch(state, reply_evaluations)
        persistence.save(state, durable=True)
        progress.quarantine_retirements_pending = False
        return False
    return True


def _author_allows_evaluation(
    state: dict,
    candidate: _ReplyCandidate,
    clarification: dict | None,
    current: int,
    progress: _ReplyCycleProgress,
    *,
    author_quarantines: AuthorQuarantines,
    config: NormalReplyConfig,
    tweets: TweetLookupCache,
    accounting: DailyReplyAccounting,
    is_probably_spam_or_not_worth_replying: Callable,
    log: Logger,
    log_event: Callable,
    mention_queue: MentionQueue,
    maybe_mark_hot_post_reply_skipped: Callable,
    reply_evaluations: ReplyEvaluations,
    persistence: ReplyCyclePersistence,
) -> bool:
    """Apply the eager author/spam checks and batch mention quarantine retirements."""
    author_cap_reached = accounting.author_count(state, candidate.author_id) >= config.maximum_daily_author_replies
    local_spam_rejection = is_probably_spam_or_not_worth_replying(candidate.incoming_text)

    if candidate.source == "mention" and clarification is None:
        quarantine = author_quarantines.active(
            state,
            candidate.author_id,
            current_epoch=current,
        )
        if quarantine is not None:
            reason = "author_evaluation_quarantine"
            log_event(
                "author_evaluation_quarantine_skip",
                author_id=candidate.author_id,
                target_id=candidate.mention_id,
                quarantine_until_epoch=quarantine.get(
                    "quarantine_until_epoch"
                ),
                pipeline_evaluations_skipped=int(
                    not author_cap_reached and not local_spam_rejection
                ),
            )
            maybe_mark_hot_post_reply_skipped(state, candidate.mention, reason=reason)
            log_event(
                "candidate_skipped",
                lane=candidate.log_source,
                id=candidate.mention_id,
                reason=reason,
                author_id=candidate.author_id,
            )
            mention_queue.mark_seen(state, candidate.mention)
            if not completed_mention_watermark_covers_target(state, candidate.mention_id):
                reply_evaluations.record(
                    state,
                    target_id=candidate.mention_id,
                    lane="mention",
                    reason=reason,
                    prune_records=False,
                    evidence_policy=(
                        author_quarantines.evidence_policy
                    ),
                )
                progress.evaluation_record_pruning_pending = True
            progress.quarantine_retirements_pending = True
            return False

    if author_cap_reached:
        log.info(
            "Skipping mention %s: already reached per-author daily cap for author_id=%s",
            candidate.mention_id,
            candidate.author_id,
        )
        if not local_spam_rejection:
            tweets.store(
                state,
                tweet_id=candidate.mention_id,
                text=candidate.incoming_text,
                author_id=candidate.author_id,
                conversation_id=str(candidate.mention.get("conversation_id", candidate.mention_id)),
                referenced_tweets=candidate.mention.get("referenced_tweets", []),
                created_at=candidate.mention.get("created_at"),
                post_type="author_cap_context",
            )
        maybe_mark_hot_post_reply_skipped(state, candidate.mention, reason="author_daily_cap")
        log_event("candidate_skipped", lane=candidate.log_source, id=candidate.mention_id, reason="author_daily_cap", author_id=candidate.author_id)
        mention_queue.mark_seen(state, candidate.mention)
        persistence.save(state)
        return False

    if local_spam_rejection:
        log.info("Skipping %s %s: spam/not worth replying", candidate.source, candidate.mention_id)
        maybe_mark_hot_post_reply_skipped(state, candidate.mention, reason="spam_or_not_worth_replying")
        log_event("candidate_skipped", lane=candidate.log_source, id=candidate.mention_id, reason="spam_or_not_worth_replying")
        mention_queue.mark_seen(state, candidate.mention)
        persistence.save(state)
        return False
    return True


def _prepare_reply_context(
    state: dict,
    candidate: _ReplyCandidate,
    clarification: dict | None,
    *,
    ApiError: type[Exception],
    PipelineResult: type,
    config: NormalReplyConfig,
    RemoteOperationsPaused: type[Exception],
    ReplyEvidenceUnavailable: type[Exception],
    generation: ReplyGeneration,
    reply_contexts: ReplyContext,
    log: Logger,
    log_event: Callable,
    mention_queue: MentionQueue,
    maybe_mark_hot_post_reply_skipped: Callable,
    record_api_error: Callable,
    reply_evaluations: ReplyEvaluations,
    reply_evidence_repository: Callable,
    persistence: ReplyCyclePersistence,
) -> PreparedReplyContext | SkipReplyCandidate | FinishReplyCheck:
    """Build canonical context and media, preserving the narrow context error boundary."""
    try:
        prepared = reply_contexts.build(candidate.mention, state)
    except RemoteOperationsPaused:
        log.info(
            "Deferring conversational reply evaluation lane=%s target_id=%s "
            "reason=global_runtime_control_pause",
            candidate.source,
            candidate.mention_id,
        )
        log_event(
            "reply_pipeline_paused",
            lane=str(candidate.source),
            target_id=candidate.mention_id,
            reason="global_runtime_control_pause",
        )
        persistence.save(state, durable=True)
        return FinishReplyCheck(NORMAL_CHECK_STATUS_CHECKED)
    except ApiError as e:
        log.exception("Could not build context for %s %s due to API error", candidate.source, candidate.mention_id)
        record_api_error(state, e, "x")
        persistence.save(state)
        return FinishReplyCheck(NORMAL_CHECK_STATUS_API_ERROR)

    if prepared is None:
        log.warning(
            "Retiring %s %s after a permanent canonical-context failure",
            candidate.source,
            candidate.mention_id,
        )
        generation.record_result(
            PipelineResult(
                status="operational_failure",
                reason="canonical_context_unavailable",
                error_category="context_validation",
                local_validation_status="failed",
            ),
            lane=str(candidate.source),
            target_id=candidate.mention_id,
        )
        reply_evaluations.record(
            state,
            target_id=candidate.mention_id,
            lane=str(candidate.source),
            reason="canonical_context_unavailable",
            outcome="operational_failure",
        )
        maybe_mark_hot_post_reply_skipped(
            state,
            candidate.mention,
            reason="operational_context_failure",
        )
        mention_queue.mark_seen(state, candidate.mention)
        persistence.save(state, durable=True)
        return SkipReplyCandidate()

    reply_context = prepared.context

    if clarification is not None:
        reply_context["clarification_request"] = {
            "original_question": trim_context_text(
                clarification["question_text"],
                config.incoming_max_chars,
            ),
            "correction": str(reply_context["incoming_contribution"]),
        }

    try:
        reply_evidence_repository()
    except ReplyEvidenceUnavailable as exc:
        log.error(
            "Skipping conversational reply target_id=%s because local evidence is unavailable: %s",
            candidate.mention_id,
            exc,
        )
        log_event(
            "reply_evidence_unavailable",
            lane=str(candidate.source),
            target_id=candidate.mention_id,
        )
        return FinishReplyCheck(NORMAL_CHECK_STATUS_CHECKED)

    return prepared


def _evaluate_reply(
    state: dict,
    candidate: _ReplyCandidate,
    reply_context: dict,
    media_context: object,
    progress: _ReplyCycleProgress,
    *,
    ApiError: type[Exception],
    config: NormalReplyConfig,
    RemoteOperationsPaused: type[Exception],
    generation: ReplyGeneration,
    log: Logger,
    log_event: Callable,
    persistence: ReplyCyclePersistence,
    record_api_error: Callable,
    history: ReplyHistory,
) -> PipelineResult | SkipReplyCandidate | FinishReplyCheck:
    """Recover or generate a draft, charging only fresh mention model evaluations."""
    evaluation = persistence.recover(
        state,
        candidate.mention_id,
        str(candidate.source),
        context=reply_context,
        recent_replies=history.recovery_replies(
            state,
            context=reply_context,
        ),
    )
    try:
        if evaluation is None or evaluation.status == "draft_discarded":
            if (
                candidate.source == "mention"
                and progress.fresh_mention_ai_evaluations >= config.maximum_fresh_evaluations
            ):
                log.info(
                    "Deferring mention %s: fresh model evaluation budget "
                    "exhausted (%s)",
                    candidate.mention_id,
                    config.maximum_fresh_evaluations,
                )
                log_event(
                    "mention_candidate_deferred",
                    target_id=candidate.mention_id,
                    author_id=candidate.author_id,
                    reason="fresh_model_evaluation_budget_exhausted",
                )
                return SkipReplyCandidate()
            if candidate.source == "mention":
                progress.fresh_mention_ai_evaluations += 1
            evaluation = generation.evaluate(
                reply_context,
                media_context,
                state=state,
            )
            if (
                candidate.source == "mention"
                and evaluation.model_call_count == 0
            ):
                progress.fresh_mention_ai_evaluations -= 1
        elif evaluation.reply is not None:
            log.info(
                "Reusing persisted single-call reply draft "
                "target_id=%s source=%s",
                candidate.mention_id,
                candidate.source,
            )
    except RemoteOperationsPaused:
        log.info(
            "Deferring conversational reply evaluation lane=%s target_id=%s "
            "reason=global_runtime_control_pause",
            candidate.source,
            candidate.mention_id,
        )
        log_event(
            "reply_pipeline_paused",
            lane=str(candidate.source),
            target_id=candidate.mention_id,
            reason="global_runtime_control_pause",
        )
        persistence.save(state, durable=True)
        return FinishReplyCheck(NORMAL_CHECK_STATUS_CHECKED)
    except ApiError as exc:
        log.exception("OpenAI single-call reply failed")
        if exc.service == "openai":
            record_api_error(state, exc, "openai")
        persistence.save(state)
        return FinishReplyCheck(NORMAL_CHECK_STATUS_API_ERROR)
    except Exception:
        log.exception("Unexpected single-call reply failure")
        persistence.save(state)
        return FinishReplyCheck(NORMAL_CHECK_STATUS_API_ERROR)
    return evaluation


def _retire_or_defer_no_reply(
    state: dict,
    candidate: _ReplyCandidate,
    evaluation: PipelineResult,
    current: int,
    *,
    log: Logger,
    log_event: Callable,
    mention_queue: MentionQueue,
    maybe_mark_hot_post_reply_skipped: Callable,
    author_quarantines: AuthorQuarantines,
    reply_evaluations: ReplyEvaluations,
    persistence: ReplyCyclePersistence,
) -> SkipReplyCandidate | FinishReplyCheck:
    """Distinguish terminal local/editorial outcomes from retryable evaluation failures."""
    if _is_terminal_candidate_local_failure(evaluation):
        failure_category = str(evaluation.error_category)
        failure_reason = str(
            evaluation.reason or failure_category
        )
        log.warning(
            "Retiring %s %s after permanent candidate-local reply "
            "failure category=%s reason=%s",
            candidate.source,
            candidate.mention_id,
            failure_category,
            failure_reason,
        )
        reply_evaluations.record(
            state,
            target_id=candidate.mention_id,
            lane=str(candidate.source),
            reason=failure_reason,
            outcome="operational_failure",
        )
        skip_reason = f"operational_{failure_category}"
        maybe_mark_hot_post_reply_skipped(
            state,
            candidate.mention,
            reason=skip_reason,
        )
        log_event(
            "candidate_skipped",
            lane=candidate.log_source,
            id=candidate.mention_id,
            reason=skip_reason,
            author_id=candidate.author_id,
        )
        mention_queue.mark_seen(state, candidate.mention)
        persistence.save(state, durable=True)
        return SkipReplyCandidate()
    if evaluation.status != "no_reply":
        log.warning(
            "Deferring %s %s after operational reply failure reason=%s",
            candidate.source,
            candidate.mention_id,
            evaluation.reason or "unknown",
        )
        persistence.save(state, durable=True)
        return FinishReplyCheck(NORMAL_CHECK_STATUS_API_ERROR)
    reason_code = str(
        evaluation.reason_code
        or evaluation.reason
        or "model_selected_no_reply"
    )
    reply_evaluations.record(
        state,
        target_id=candidate.mention_id,
        lane=str(candidate.source),
        reason=reason_code,
    )
    if candidate.source == "mention" and reason_code == "spam_or_abuse":
        author_quarantines.record_no_reply(
            state,
            candidate.author_id,
            current_epoch=current,
            explicit_spam_or_abuse=True,
        )
    log.info(
        "Sol selected no_reply for %s %s reason=%s",
        candidate.source,
        candidate.mention_id,
        reason_code,
    )
    maybe_mark_hot_post_reply_skipped(
        state,
        candidate.mention,
        reason=f"editorial_no_reply:{reason_code}",
    )
    mention_queue.mark_seen(state, candidate.mention)
    persistence.save(state, durable=True)
    return SkipReplyCandidate()


def _prepare_reply_receipt(
    state: dict,
    candidate: _ReplyCandidate,
    reply_text: object,
    reply_context: dict,
    clarification: dict | None,
    *,
    SINGLE_CALL_STRATEGY_VERSION: str,
    ValidatedReply: type,
    _log_validated_single_call_reply: Callable,
    delivery: ReplyCycleDelivery,
    log: Logger,
    log_event: Callable,
    persistence: ReplyCyclePersistence,
) -> dict | FinishReplyCheck:
    """Persist the validated draft and bind receipt provenance before transport handling."""
    if not isinstance(reply_text, ValidatedReply):
        log.error(
            "Single-call pipeline returned an unvalidated reply type; "
            "deferring target_id=%s source=%s",
            candidate.mention_id,
            candidate.source,
        )
        persistence.save(state, durable=True)
        return FinishReplyCheck(NORMAL_CHECK_STATUS_API_ERROR)
    if candidate.source == "mention":
        clear_author_evaluation_quarantine_history(state, candidate.author_id)

    _log_validated_single_call_reply(
        target_description="target",
        target_id=candidate.mention_id,
        reply=reply_text,
    )

    def log_validation_failure() -> None:
        """Retain the normal lane's persistence-validation log format."""
        log.error(
            "Single-call reply draft failed persistence validation; "
            "deferring target_id=%s source=%s",
            candidate.mention_id,
            candidate.source,
        )

    if not persist_validated_reply_draft(
        state, candidate.mention_id, candidate.source, reply_text, reply_context,
        SINGLE_CALL_STRATEGY_VERSION=SINGLE_CALL_STRATEGY_VERSION,
        persistence=persistence,
        log_validation_failure=log_validation_failure,
        log_event=log_event,
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
        state, candidate.mention, candidate.source,
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

    receipt_template = delivery.bind_attempt(receipt_template)
    return receipt_template


def _retire_terminal_target(
    state: dict,
    candidate: _ReplyCandidate,
    replied_to_ids: set[str],
    reply_text: object,
    *,
    failure_reason: str,
    reason: str,
    persistence: ReplyCyclePersistence,
    log_ai_reply_posting_outcome: Callable,
    log_event: Callable,
    mention_queue: MentionQueue,
    reply_evaluations: ReplyEvaluations,
) -> None:
    """Record a terminal mention/hot-post outcome and durably retire its draft."""
    log_ai_reply_posting_outcome(
        reply=reply_text,
        status="posting_failed_terminal",
        lane=str(candidate.source),
        target_id=candidate.mention_id,
        failure_reason=failure_reason,
    )
    reply_evaluations.record(
        state,
        target_id=candidate.mention_id,
        lane=str(candidate.source),
        reason=reason,
        outcome="reply_not_permitted",
    )
    log_event(
        "reply_target_terminal",
        lane=candidate.log_source,
        target_id=candidate.mention_id,
        outcome="reply_not_permitted",
        reason=reason,
    )
    replied_to_ids.add(candidate.mention_id)
    persistence.clear(state, candidate.mention_id, str(candidate.source))
    state["replied_to_ids"] = append_unique_durable(
        state.get("replied_to_ids", []),
        candidate.mention_id,
    )
    mention_queue.mark_seen(state, candidate.mention)
    persistence.save(state, durable=True)


def _deliver_reply(
    state: dict,
    candidate: _ReplyCandidate,
    replied_to_ids: set[str],
    reply_text: object,
    receipt_template: dict,
    *,
    config: NormalReplyConfig,
    persistence: ReplyCyclePersistence,
    log: Logger,
    log_ai_reply_posting_outcome: Callable,
    log_event: Callable,
    mention_queue: MentionQueue,
    delivery: ReplyCycleDelivery,
    reply_evaluations: ReplyEvaluations,
) -> dict | FinishReplyCheck:
    """Deliver through the shared boundary, retaining normal-lane retirement and statuses."""
    def retire_terminal_target(failure_reason: str) -> None:
        if failure_reason == "target_unavailable_pre_send":
            log.warning(
                "Cannot reply to mention %s because it disappeared after "
                "evaluation; marking it handled without consuming reply quota",
                candidate.mention_id,
            )
        else:
            log.warning(
                "Cannot reply to mention %s because X says replies are not allowed; "
                "marking mention as handled without consuming reply quota",
                candidate.mention_id,
            )
        _retire_terminal_target(
            state, candidate, replied_to_ids, reply_text,
            failure_reason=failure_reason,
            reason=f"x_{failure_reason}",
            persistence=persistence,
            log_ai_reply_posting_outcome=log_ai_reply_posting_outcome,
            log_event=log_event,
            mention_queue=mention_queue,
            reply_evaluations=reply_evaluations,
        )

    outcome = delivery.deliver(
        state, candidate.mention_id, reply_text, receipt_template,
        lane=str(candidate.source), log_source=candidate.log_source,
        read_error_scope="api",
        mark_as_ai=config.mark_as_ai,
        retire_terminal_target=retire_terminal_target,
    )
    if outcome is ReplyDeliveryStop.TERMINAL:
        return FinishReplyCheck(NORMAL_CHECK_STATUS_CHECKED)
    if outcome is ReplyDeliveryStop.RETRYABLE:
        return FinishReplyCheck(NORMAL_CHECK_STATUS_API_ERROR)
    return outcome


def _finalise_confirmed_reply(
    state: dict,
    candidate: _ReplyCandidate,
    receipt: dict,
    *,
    log: Logger,
    log_event: Callable,
    delivery: ReplyCycleDelivery,
) -> str:
    """Finish the shared confirmation transaction and report this lane's success."""
    own_reply_id = delivery.finalise(
        state, receipt, target_id=candidate.mention_id,
        quote_reply=False,
    )

    log_event(
        "reply_posted",
        lane=candidate.log_source,
        target_id=candidate.mention_id,
        author_id=candidate.author_id,
        reply_post_id=own_reply_id,
        daily_reply_count=state.get("daily_reply_count"),
    )
    log.info("Reply posted successfully")
    return NORMAL_CHECK_STATUS_POSTED
