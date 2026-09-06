"""Run the normal mention and hot-post reply cycle through current root authority.

The root adapter supplies every callback, setting, logger, application class and
exception on each invocation. The complete loop and nested quarantine closures
retain their original operation order, state/reference boundaries, model-call
budget, receipt durability and error routing. Backlog continuation calls the
supplied current root maybe_reply_to_mentions callback with the original state.

Discovery/pagination, counters/watermarks/quarantine policy, pipeline/evidence,
context/media, persistence, reconciliation and delivery stay in their existing
locations. Explicit calls may read providers, generate a reply, save state and
publish through those callbacks. Standard-library-only import performs no file,
environment, provider or RNG work and retains no callbacks or configuration.
"""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger
from pathlib import Path
from types import ModuleType


def maybe_reply_to_mentions(
    state: dict,
    *,
    _fresh_mention_ai_evaluations: int = 0,
    _skip_hot_post_fetch: bool = False,
    AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY: str,
    AmbiguousRemotePostOutcome: type[Exception],
    ApiError: type[Exception],
    CONFIRMED_REPLY_RECEIPT_FILE: Path,
    ConfirmedReplyLocalPersistenceError: type[Exception],
    ENABLE_AUTO_REPLIES: bool,
    MARK_AI_REPLIES_AS_AI: bool,
    MAX_AUTO_REPLIES_PER_DAY: int,
    MAX_MENTIONS_PER_CHECK: int,
    MAX_REPLIES_PER_AUTHOR_PER_DAY: int,
    MIN_SECONDS_BETWEEN_REPLIES: int,
    MY_USER_ID: str,
    NORMAL_CHECK_STATUS_API_ERROR: str,
    NORMAL_CHECK_STATUS_CHECKED: str,
    NORMAL_CHECK_STATUS_DISABLED: str,
    NORMAL_CHECK_STATUS_POSTED: str,
    NORMAL_CHECK_STATUS_SKIPPED_CAP: str,
    NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN: str,
    NORMAL_CHECK_STATUS_SKIPPED_SPACING: str,
    PipelineResult: type,
    ProvedRemotePostNonSuccess: type[Exception],
    REPLY_INCOMING_MAX_CHARS: int,
    RemoteOperationsPaused: type[Exception],
    ReplyEvidenceUnavailable: type[Exception],
    SINGLE_CALL_STRATEGY_VERSION: str,
    UnrecoverableConfirmedReplyPersistenceError: type[Exception],
    ValidatedReply: type,
    _is_terminal_candidate_local_failure: Callable,
    _log_validated_single_call_reply: Callable,
    _record_single_call_result: Callable,
    active_author_evaluation_quarantine: Callable,
    api_error_is_reply_not_allowed: Callable,
    append_unique_durable: Callable,
    apply_confirmed_reply_receipt: Callable,
    bind_conversational_reply_attempt_time: Callable,
    block_if_ambiguous_remote_post: Callable,
    build_context_for_reply_ai: Callable,
    cache_tweet: Callable,
    clarification_reply_context: Callable,
    clarification_thread_is_terminal: Callable,
    clear_author_evaluation_quarantine_history: Callable,
    clear_pending_ai_reply: Callable,
    completed_mention_watermark_covers_target: Callable,
    conversational_reply_pipeline_enabled: Callable,
    copy: ModuleType,
    daily_author_reply_count: Callable,
    daily_author_reply_counts: Callable,
    dedupe_reply_candidates: Callable,
    generate_single_call_reply: Callable,
    get_hot_post_reply_candidates: Callable,
    get_mentions: Callable,
    in_api_cooldown: Callable,
    is_probably_spam_or_not_worth_replying: Callable,
    lane_paused: Callable,
    load_confirmed_reply_receipt: Callable,
    log: Logger,
    log_ai_reply_posting_outcome: Callable,
    log_event: Callable,
    mark_mention_seen_if_applicable: Callable,
    maybe_mark_hot_post_reply_skipped: Callable,
    maybe_reply_to_mentions: Callable,
    mention_pagination_provenance_is_valid: Callable,
    now_epoch: Callable,
    pending_ai_reply: Callable,
    pending_ai_reply_draft_key: Callable,
    pending_mention_candidates: Callable,
    post_conversational_reply_with_durable_identity: Callable,
    prune_author_evaluation_quarantines: Callable,
    prune_completed_mention_quarantine_evaluations: Callable,
    prune_reply_evaluation_records: Callable,
    reconcile_confirmed_reply_receipt: Callable,
    record_api_error: Callable,
    record_qualifying_author_no_reply: Callable,
    record_terminal_reply_evaluation: Callable,
    recovery_comparison_account_replies: Callable,
    remove_confirmed_reply_receipt: Callable,
    reply_evidence_repository: Callable,
    reply_media_context_for_candidate: Callable,
    reply_target_is_available_immediately_before_send: Callable,
    reply_target_is_directly_eligible: Callable,
    reset_daily_reply_count_if_needed: Callable,
    retire_lane_transport_journal_if_present: Callable,
    retire_proved_rejected_conversational_reply_receipt: Callable,
    save_state: Callable,
    store_pending_ai_reply: Callable,
    terminal_reply_evaluation: Callable,
    trim_context_text: Callable,
    valid_tweets_sorted_by_id: Callable,
) -> str:
    """Process eligible mention and hot-post candidates under all reply limits."""
    log.info("Starting mention reply check")
    # A confirmed reply receipt and its transport journal are a recoverable
    # local transaction, not permission for a new remote write.  Reconcile it
    # before the general journal barrier so a restart can finish the exact
    # durable transaction without first weakening that barrier.
    reset_daily_reply_count_if_needed(state)
    prior_reply_status, _prior_reply = load_confirmed_reply_receipt()
    if prior_reply_status == "valid" and reconcile_confirmed_reply_receipt(state):
        log.warning(
            "Reconciled confirmed reply receipt before checking new mention candidates"
        )
    block_if_ambiguous_remote_post()

    if not ENABLE_AUTO_REPLIES:
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
        MAX_AUTO_REPLIES_PER_DAY,
    )
    log.debug(
        "Daily per-author cap status: authors_replied_today=%d max_per_author=%s",
        len(daily_replied_author_counts),
        MAX_REPLIES_PER_AUTHOR_PER_DAY,
    )

    if state["daily_reply_count"] >= MAX_AUTO_REPLIES_PER_DAY:
        log.info("Daily generated/replied cap reached")
        save_state(state)
        return NORMAL_CHECK_STATUS_SKIPPED_CAP

    current = now_epoch()
    if prune_author_evaluation_quarantines(state, current_epoch=current):
        save_state(state)

    seconds_since_last_reply = current - int(state.get("last_reply_epoch", 0))
    log.debug(
        "Seconds since last generated/replied=%s minimum=%s",
        seconds_since_last_reply,
        MIN_SECONDS_BETWEEN_REPLIES,
    )

    if seconds_since_last_reply < MIN_SECONDS_BETWEEN_REPLIES:
        log.info("Skipping mention check: minimum interval between replies not reached")
        return NORMAL_CHECK_STATUS_SKIPPED_SPACING

    started_with_pending_mentions = bool(pending_mention_candidates(state))
    try:
        mentions = get_mentions(state)
    except ApiError as e:
        log.exception("Failed to get mention reply candidates")
        record_api_error(state, e, "x")
        save_state(state)
        return NORMAL_CHECK_STATUS_API_ERROR
    except Exception:
        log.exception("Unexpected failure getting mention reply candidates")
        save_state(state)
        return NORMAL_CHECK_STATUS_API_ERROR

    if _skip_hot_post_fetch:
        hot_post_replies = []
    else:
        try:
            hot_post_replies = get_hot_post_reply_candidates(state)
        except ApiError as e:
            log.exception("Failed to get optional hot-post reply candidates; continuing with mentions")
            record_api_error(state, e, "x", scope="quote")
            save_state(state)
            hot_post_replies = []
        except Exception:
            log.exception("Unexpected failure getting optional hot-post reply candidates; continuing with mentions")
            save_state(state)
            hot_post_replies = []

    mentions = dedupe_reply_candidates(mentions, hot_post_replies)

    if not mentions:
        log.info("No mention or hot-post reply candidates returned")
        return NORMAL_CHECK_STATUS_CHECKED

    mentions = valid_tweets_sorted_by_id(mentions, context="mention/hot-post candidate")

    replied_to_ids = set(str(x) for x in state.get("replied_to_ids", []))
    log.debug("replied_to_ids count=%d", len(replied_to_ids))

    fresh_mention_ai_evaluations = int(_fresh_mention_ai_evaluations)
    quarantine_retirements_pending = False
    quarantine_evaluations_deferred = False

    def prune_quarantine_retirement_batch() -> None:
        nonlocal quarantine_evaluations_deferred
        if quarantine_evaluations_deferred:
            prune_reply_evaluation_records(state)
        else:
            prune_completed_mention_quarantine_evaluations(state)
        quarantine_evaluations_deferred = False

    def flush_quarantine_retirements() -> None:
        nonlocal quarantine_retirements_pending
        if not quarantine_retirements_pending:
            return
        prune_quarantine_retirement_batch()
        save_state(state, durable=True)
        quarantine_retirements_pending = False

    for mention in mentions:
        if in_api_cooldown(state, scope="openai"):
            log.info(
                "Stopping mention/hot-post candidate iteration because the "
                "OpenAI cooldown became active"
            )
            flush_quarantine_retirements()
            save_state(state, durable=True)
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

        if mention_id in replied_to_ids:
            log.info("Skipping %s %s: already replied to", candidate_source, mention_id)
            maybe_mark_hot_post_reply_skipped(state, mention, reason="already_replied")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="already_replied")
            mark_mention_seen_if_applicable(state, mention)
            continue

        prior_evaluation = terminal_reply_evaluation(state, mention_id)
        if prior_evaluation is not None:
            prior_outcome = str(prior_evaluation.get("outcome") or "no_reply")
            log.info(
                "Skipping %s %s: terminal %s evaluation already recorded reason=%s",
                candidate_source,
                mention_id,
                prior_outcome,
                prior_evaluation.get("reason", ""),
            )
            skip_reason = f"already_evaluated_{prior_outcome}"
            maybe_mark_hot_post_reply_skipped(state, mention, reason=skip_reason)
            log_event(
                "candidate_skipped",
                lane=candidate_log_source,
                id=mention_id,
                reason=skip_reason,
            )
            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            continue

        if author_id == str(MY_USER_ID):
            log.info("Skipping %s %s: authored by our own account", candidate_source, mention_id)
            maybe_mark_hot_post_reply_skipped(state, mention, reason="own_account")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="own_account")
            mark_mention_seen_if_applicable(state, mention)
            continue

        if clarification_thread_is_terminal(state, mention):
            log.info(
                "Skipping %s %s: clarification already completed and thread is terminal",
                candidate_source,
                mention_id,
            )
            maybe_mark_hot_post_reply_skipped(state, mention, reason="clarification_thread_terminal")
            log_event(
                "candidate_skipped",
                lane=candidate_log_source,
                id=mention_id,
                reason="clarification_thread_terminal",
            )
            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            continue

        if not reply_target_is_directly_eligible(mention):
            reason = "target_does_not_directly_mention_account"
            log.warning(
                "Skipping %s %s before context/media/model work: target is not directly reply-eligible",
                candidate_source,
                mention_id,
            )
            drafts = state.get("pending_ai_reply_drafts", {})
            pending_key = pending_ai_reply_draft_key(mention_id, str(candidate_source))
            pending_record = drafts.get(pending_key) if isinstance(drafts, dict) else None
            if isinstance(pending_record, dict):
                log_event(
                    "single_call_reply_posting_outcome",
                    status="posting_failed_terminal",
                    lane=str(candidate_source),
                    target_id=mention_id,
                    reply_post_id="",
                    strategy_version=pending_record.get("strategy_version"),
                    reply_kind=pending_record.get("reply_kind"),
                    reason_code=pending_record.get("reason_code"),
                    validated_draft_hash=pending_record.get(
                        "validated_draft_hash"
                    ),
                    failure_reason="reply_not_permitted_preflight",
                )
                clear_pending_ai_reply(state, mention_id, str(candidate_source))
            record_terminal_reply_evaluation(
                state,
                target_id=mention_id,
                lane=str(candidate_source),
                reason=reason,
                outcome="reply_not_permitted",
            )
            maybe_mark_hot_post_reply_skipped(state, mention, reason="reply_not_permitted")
            log_event(
                "reply_target_terminal",
                lane=candidate_log_source,
                target_id=mention_id,
                outcome="reply_not_permitted",
                reason=reason,
            )
            mark_mention_seen_if_applicable(state, mention)
            if quarantine_retirements_pending:
                prune_quarantine_retirement_batch()
            save_state(state, durable=True)
            quarantine_retirements_pending = False
            continue

        clarification = clarification_reply_context(state, mention, current=current)
        author_cap_reached = daily_author_reply_count(state, author_id) >= MAX_REPLIES_PER_AUTHOR_PER_DAY
        local_spam_rejection = is_probably_spam_or_not_worth_replying(incoming_text)

        if candidate_source == "mention" and clarification is None:
            quarantine = active_author_evaluation_quarantine(
                state,
                author_id,
                current_epoch=current,
            )
            if quarantine is not None:
                reason = "author_evaluation_quarantine"
                log_event(
                    "author_evaluation_quarantine_skip",
                    author_id=author_id,
                    target_id=mention_id,
                    quarantine_until_epoch=quarantine.get(
                        "quarantine_until_epoch"
                    ),
                    pipeline_evaluations_skipped=int(
                        not author_cap_reached and not local_spam_rejection
                    ),
                )
                maybe_mark_hot_post_reply_skipped(state, mention, reason=reason)
                log_event(
                    "candidate_skipped",
                    lane=candidate_log_source,
                    id=mention_id,
                    reason=reason,
                    author_id=author_id,
                )
                mark_mention_seen_if_applicable(state, mention)
                if not completed_mention_watermark_covers_target(state, mention_id):
                    record_terminal_reply_evaluation(
                        state,
                        target_id=mention_id,
                        lane="mention",
                        reason=reason,
                        prune_records=False,
                        evidence_policy=(
                            AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY
                        ),
                    )
                    quarantine_evaluations_deferred = True
                quarantine_retirements_pending = True
                continue

        if author_cap_reached:
            log.info(
                "Skipping mention %s: already reached per-author daily cap for author_id=%s",
                mention_id,
                author_id,
            )
            if not local_spam_rejection:
                cache_tweet(
                    state,
                    tweet_id=mention_id,
                    text=incoming_text,
                    author_id=author_id,
                    conversation_id=str(mention.get("conversation_id", mention_id)),
                    referenced_tweets=mention.get("referenced_tweets", []),
                    created_at=mention.get("created_at"),
                    post_type="author_cap_context",
                )
            maybe_mark_hot_post_reply_skipped(state, mention, reason="author_daily_cap")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="author_daily_cap", author_id=author_id)
            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            continue

        if local_spam_rejection:
            log.info("Skipping %s %s: spam/not worth replying", candidate_source, mention_id)
            maybe_mark_hot_post_reply_skipped(state, mention, reason="spam_or_not_worth_replying")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="spam_or_not_worth_replying")
            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            continue

        flush_quarantine_retirements()
        try:
            reply_context, should_continue = build_context_for_reply_ai(mention, state)
        except RemoteOperationsPaused:
            log.info(
                "Deferring conversational reply evaluation lane=%s target_id=%s "
                "reason=global_runtime_control_pause",
                candidate_source,
                mention_id,
            )
            log_event(
                "reply_pipeline_paused",
                lane=str(candidate_source),
                target_id=mention_id,
                reason="global_runtime_control_pause",
            )
            save_state(state, durable=True)
            return NORMAL_CHECK_STATUS_CHECKED
        except ApiError as e:
            log.exception("Could not build context for %s %s due to API error", candidate_source, mention_id)
            record_api_error(state, e, "x")
            save_state(state)
            return NORMAL_CHECK_STATUS_API_ERROR

        if not should_continue:
            log.warning(
                "Retiring %s %s after a permanent canonical-context failure",
                candidate_source,
                mention_id,
            )
            _record_single_call_result(
                PipelineResult(
                    status="operational_failure",
                    reason="canonical_context_unavailable",
                    error_category="context_validation",
                    local_validation_status="failed",
                ),
                lane=str(candidate_source),
                target_id=mention_id,
            )
            record_terminal_reply_evaluation(
                state,
                target_id=mention_id,
                lane=str(candidate_source),
                reason="canonical_context_unavailable",
                outcome="operational_failure",
            )
            maybe_mark_hot_post_reply_skipped(
                state,
                mention,
                reason="operational_context_failure",
            )
            mark_mention_seen_if_applicable(state, mention)
            save_state(state, durable=True)
            continue

        prepared_media_context = reply_context.pop(
            "_prepared_media_context",
            None,
        )

        if clarification is not None:
            reply_context["clarification_request"] = {
                "original_question": trim_context_text(
                    clarification["question_text"],
                    REPLY_INCOMING_MAX_CHARS,
                ),
                "correction": str(reply_context["incoming_contribution"]),
            }

        try:
            reply_evidence_repository()
        except ReplyEvidenceUnavailable as exc:
            log.error(
                "Skipping conversational reply target_id=%s because local evidence is unavailable: %s",
                mention_id,
                exc,
            )
            log_event(
                "reply_evidence_unavailable",
                lane=str(candidate_source),
                target_id=mention_id,
            )
            return NORMAL_CHECK_STATUS_CHECKED

        media_context = (
            prepared_media_context
            if isinstance(prepared_media_context, dict)
            else reply_media_context_for_candidate(
                mention,
                lane=str(candidate_source),
                target_id=mention_id,
            )
        )

        evaluation_outcome: dict[str, object] = {}
        reply_text = pending_ai_reply(
            state,
            mention_id,
            str(candidate_source),
            context=reply_context,
            recent_replies=recovery_comparison_account_replies(
                state,
                context=reply_context,
            ),
            evaluation_outcome=evaluation_outcome,
        )
        try:
            if (
                reply_text is None
                and not _is_terminal_candidate_local_failure(
                    evaluation_outcome
                )
            ):
                if (
                    candidate_source == "mention"
                    and fresh_mention_ai_evaluations >= MAX_MENTIONS_PER_CHECK
                ):
                    log.info(
                        "Deferring mention %s: fresh model evaluation budget "
                        "exhausted (%s)",
                        mention_id,
                        MAX_MENTIONS_PER_CHECK,
                    )
                    log_event(
                        "mention_candidate_deferred",
                        target_id=mention_id,
                        author_id=author_id,
                        reason="fresh_model_evaluation_budget_exhausted",
                    )
                    continue
                if candidate_source == "mention":
                    fresh_mention_ai_evaluations += 1
                reply_text = generate_single_call_reply(
                    reply_context,
                    media_context,
                    state=state,
                    evaluation_outcome=evaluation_outcome,
                )
                if (
                    candidate_source == "mention"
                    and evaluation_outcome.get("model_call_count") == 0
                ):
                    fresh_mention_ai_evaluations -= 1
            elif reply_text is not None:
                log.info(
                    "Reusing persisted single-call reply draft "
                    "target_id=%s source=%s",
                    mention_id,
                    candidate_source,
                )
        except RemoteOperationsPaused:
            log.info(
                "Deferring conversational reply evaluation lane=%s target_id=%s "
                "reason=global_runtime_control_pause",
                candidate_source,
                mention_id,
            )
            log_event(
                "reply_pipeline_paused",
                lane=str(candidate_source),
                target_id=mention_id,
                reason="global_runtime_control_pause",
            )
            save_state(state, durable=True)
            return NORMAL_CHECK_STATUS_CHECKED
        except ApiError as exc:
            log.exception("OpenAI single-call reply failed")
            if exc.service == "openai":
                record_api_error(state, exc, "openai")
            save_state(state)
            return NORMAL_CHECK_STATUS_API_ERROR
        except Exception:
            log.exception("Unexpected single-call reply failure")
            save_state(state)
            return NORMAL_CHECK_STATUS_API_ERROR

        if not reply_text:
            if _is_terminal_candidate_local_failure(evaluation_outcome):
                failure_category = str(evaluation_outcome["error_category"])
                failure_reason = str(
                    evaluation_outcome.get("reason") or failure_category
                )
                log.warning(
                    "Retiring %s %s after permanent candidate-local reply "
                    "failure category=%s reason=%s",
                    candidate_source,
                    mention_id,
                    failure_category,
                    failure_reason,
                )
                record_terminal_reply_evaluation(
                    state,
                    target_id=mention_id,
                    lane=str(candidate_source),
                    reason=failure_reason,
                    outcome="operational_failure",
                )
                skip_reason = f"operational_{failure_category}"
                maybe_mark_hot_post_reply_skipped(
                    state,
                    mention,
                    reason=skip_reason,
                )
                log_event(
                    "candidate_skipped",
                    lane=candidate_log_source,
                    id=mention_id,
                    reason=skip_reason,
                    author_id=author_id,
                )
                mark_mention_seen_if_applicable(state, mention)
                save_state(state, durable=True)
                continue
            if evaluation_outcome.get("status") != "no_reply":
                log.warning(
                    "Deferring %s %s after operational reply failure reason=%s",
                    candidate_source,
                    mention_id,
                    evaluation_outcome.get("reason", "unknown"),
                )
                save_state(state, durable=True)
                return NORMAL_CHECK_STATUS_API_ERROR
            reason_code = str(
                evaluation_outcome.get("reason_code")
                or evaluation_outcome.get("reason")
                or "model_selected_no_reply"
            )
            record_terminal_reply_evaluation(
                state,
                target_id=mention_id,
                lane=str(candidate_source),
                reason=reason_code,
            )
            if candidate_source == "mention" and reason_code == "spam_or_abuse":
                record_qualifying_author_no_reply(
                    state,
                    author_id,
                    current_epoch=current,
                    explicit_spam_or_abuse=True,
                )
            log.info(
                "Sol selected no_reply for %s %s reason=%s",
                candidate_source,
                mention_id,
                reason_code,
            )
            maybe_mark_hot_post_reply_skipped(
                state,
                mention,
                reason=f"editorial_no_reply:{reason_code}",
            )
            mark_mention_seen_if_applicable(state, mention)
            save_state(state, durable=True)
            continue
        if not isinstance(reply_text, ValidatedReply):
            log.error(
                "Single-call pipeline returned an unvalidated reply type; "
                "deferring target_id=%s source=%s",
                mention_id,
                candidate_source,
            )
            save_state(state, durable=True)
            return NORMAL_CHECK_STATUS_API_ERROR
        if candidate_source == "mention":
            clear_author_evaluation_quarantine_history(state, author_id)

        _log_validated_single_call_reply(
            target_description="target",
            target_id=mention_id,
            reply=reply_text,
        )
        draft_stored = store_pending_ai_reply(
            state,
            mention_id,
            str(candidate_source),
            reply_text,
            context=reply_context,
        )
        if not draft_stored:
            log.error(
                "Single-call reply draft failed persistence validation; "
                "deferring target_id=%s source=%s",
                mention_id,
                candidate_source,
            )
            log_event(
                "single_call_reply_posting_outcome",
                status="draft_persistence_failed",
                lane=str(candidate_source),
                target_id=mention_id,
                strategy_version=SINGLE_CALL_STRATEGY_VERSION,
                reply_kind=reply_text.draft_record.get("reply_kind"),
                reason_code=reply_text.draft_record.get("reason_code"),
                validated_draft_hash=reply_text.draft_record.get(
                    "validated_draft_hash"
                ),
                failure_reason="draft_persistence_validation_failed",
            )
            save_state(state, durable=True)
            return NORMAL_CHECK_STATUS_API_ERROR
        save_state(state, durable=True)
        receipt_template = {
            "schema_version": 4,
            "lifecycle_state": "sending",
            "target_id": mention_id,
            "author_id": author_id,
            "candidate_source": candidate_source,
            "conversation_id": str(
                mention.get("conversation_id", mention_id)
            ),
            "reply_text": reply_text,
            "reply_context": copy.deepcopy(reply_context),
            "ai_reply_draft": copy.deepcopy(reply_text.draft_record),
        }
        if candidate_source == "mention":
            mention_pagination = (
                mention.get("_mention_pagination")
                if mention.get("_pagination_truncated")
                else state.get("mention_pagination")
            )
        else:
            mention_pagination = None
        if candidate_source == "mention" and mention_pagination:
            if not mention_pagination_provenance_is_valid(mention_pagination):
                raise RuntimeError(
                    "Refusing to post a reply from a truncated mention batch "
                    "without valid pagination provenance"
                )
            base_since_id = str(mention_pagination["base_since_id"])
            current_since_id = str(state.get("last_seen_mention_id") or "")
            active_pagination = state.get("mention_pagination")
            if (
                current_since_id != base_since_id
                or not mention_pagination_provenance_is_valid(active_pagination)
                or active_pagination != mention_pagination
            ):
                raise RuntimeError(
                    "Refusing to post a reply whose mention pagination "
                    "provenance no longer matches durable state"
                )
            receipt_template["mention_pagination"] = copy.deepcopy(
                mention_pagination
            )
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

        receipt_template = bind_conversational_reply_attempt_time(receipt_template)
        try:
            if not reply_target_is_available_immediately_before_send(mention_id):
                log.warning(
                    "Cannot reply to mention %s because it disappeared after "
                    "evaluation; marking it handled without consuming reply quota",
                    mention_id,
                )
                log_ai_reply_posting_outcome(
                    reply=reply_text,
                    status="posting_failed_terminal",
                    lane=str(candidate_source),
                    target_id=mention_id,
                    failure_reason="target_unavailable_pre_send",
                )
                record_terminal_reply_evaluation(
                    state,
                    target_id=mention_id,
                    lane=str(candidate_source),
                    reason="x_target_unavailable_pre_send",
                    outcome="reply_not_permitted",
                )
                log_event(
                    "reply_target_terminal",
                    lane=candidate_log_source,
                    target_id=mention_id,
                    outcome="reply_not_permitted",
                    reason="x_target_unavailable_pre_send",
                )
                replied_to_ids.add(mention_id)
                clear_pending_ai_reply(state, mention_id, str(candidate_source))
                state["replied_to_ids"] = append_unique_durable(
                    state.get("replied_to_ids", []),
                    mention_id,
                )
                mark_mention_seen_if_applicable(state, mention)
                save_state(state, durable=True)
                return NORMAL_CHECK_STATUS_CHECKED
            reply_response, receipt = (
                post_conversational_reply_with_durable_identity(
                    state=state,
                    receipt_template=receipt_template,
                    reply_text=reply_text,
                    reply_to_id=mention_id,
                    made_with_ai=MARK_AI_REPLIES_AS_AI,
                    lane=str(candidate_source),
                )
            )
        except UnrecoverableConfirmedReplyPersistenceError:
            log.critical(
                "Confirmed %s reply lost every complete durable local identity; "
                "the global remote-write safety barrier remains active",
                candidate_log_source,
                exc_info=True,
            )
            raise
        except ConfirmedReplyLocalPersistenceError:
            log.critical(
                "Confirmed %s reply required its durable state fallback",
                candidate_log_source,
                exc_info=True,
            )
            raise
        except AmbiguousRemotePostOutcome:
            log.critical(
                "%s reply stopped after an ambiguous remote outcome; the global "
                "remote-write safety barrier remains active",
                candidate_log_source,
                exc_info=True,
            )
            log_ai_reply_posting_outcome(
                reply=reply_text,
                status="posting_failed_retryable",
                lane=str(candidate_source),
                target_id=mention_id,
                failure_reason="ambiguous_remote_outcome",
            )
            raise
        except ApiError as e:
            if api_error_is_reply_not_allowed(e):
                log.warning(
                    "Cannot reply to mention %s because X says replies are not allowed; "
                    "marking mention as handled without consuming reply quota",
                    mention_id,
                )
                log_ai_reply_posting_outcome(
                    reply=reply_text,
                    status="posting_failed_terminal",
                    lane=str(candidate_source),
                    target_id=mention_id,
                    failure_reason="reply_not_permitted",
                )
                record_terminal_reply_evaluation(
                    state,
                    target_id=mention_id,
                    lane=str(candidate_source),
                    reason="x_reply_not_permitted",
                    outcome="reply_not_permitted",
                )
                log_event(
                    "reply_target_terminal",
                    lane=candidate_log_source,
                    target_id=mention_id,
                    outcome="reply_not_permitted",
                    reason="x_reply_not_permitted",
                )
                replied_to_ids.add(mention_id)
                clear_pending_ai_reply(state, mention_id, str(candidate_source))
                state["replied_to_ids"] = append_unique_durable(
                    state.get("replied_to_ids", []),
                    mention_id,
                )
                mark_mention_seen_if_applicable(state, mention)
                save_state(state, durable=True)
                if isinstance(e, ProvedRemotePostNonSuccess):
                    retire_proved_rejected_conversational_reply_receipt(
                        receipt_template,
                        e,
                    )
                return NORMAL_CHECK_STATUS_CHECKED

            log.exception("Failed to post generated reply")
            log_ai_reply_posting_outcome(
                reply=reply_text,
                status="posting_failed_retryable",
                lane=str(candidate_source),
                target_id=mention_id,
                failure_reason=f"x_api_{getattr(e, 'status_code', 'error')}",
            )
            record_api_error(state, e, "x", scope="write")
            save_state(state)
            return NORMAL_CHECK_STATUS_API_ERROR
        except Exception as e:
            log.exception("Unexpected failure posting generated reply")
            log_ai_reply_posting_outcome(
                reply=reply_text,
                status="posting_failed_retryable",
                lane=str(candidate_source),
                target_id=mention_id,
                failure_reason="unexpected_posting_error",
            )
            record_api_error(state, e, "x", scope="write")
            save_state(state)
            return NORMAL_CHECK_STATUS_API_ERROR

        own_reply_id = str(receipt["reply_post_id"])

        apply_confirmed_reply_receipt(state, receipt)
        log.info("Recorded and cached own auto-reply id=%s", own_reply_id)
        save_state(state, durable=True)
        try:
            retire_lane_transport_journal_if_present(
                receipt_path=CONFIRMED_REPLY_RECEIPT_FILE,
                receipt=receipt,
                lane="conversational_reply",
                post_id=own_reply_id,
            )
            remove_confirmed_reply_receipt(receipt)
        except Exception as exc:
            log.critical(
                "Confirmed reply id=%s to target=%s was saved but receipt removal failed",
                own_reply_id,
                mention_id,
                exc_info=True,
            )
            raise ConfirmedReplyLocalPersistenceError(
                f"Confirmed reply {own_reply_id} to {mention_id} but receipt removal failed"
            ) from exc

        log_event(
            "reply_posted",
            lane=candidate_log_source,
            target_id=mention_id,
            author_id=author_id,
            reply_post_id=own_reply_id,
            daily_reply_count=state.get("daily_reply_count"),
        )
        log.info("Reply posted successfully")
        return NORMAL_CHECK_STATUS_POSTED

    if quarantine_retirements_pending:
        flush_quarantine_retirements()
    else:
        save_state(state)
    if (
        started_with_pending_mentions
        and not pending_mention_candidates(state)
        and state.get("mention_backlog")
        and fresh_mention_ai_evaluations < MAX_MENTIONS_PER_CHECK
    ):
        log.info(
            "Durable pending mention queue drained; resuming backlog within the same check"
        )
        return maybe_reply_to_mentions(
            state,
            _fresh_mention_ai_evaluations=fresh_mention_ai_evaluations,
            _skip_hot_post_fetch=True,
        )
    log.info("Mention reply check finished with no reply generated/posted")
    return NORMAL_CHECK_STATUS_CHECKED
