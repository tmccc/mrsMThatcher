"""Own the quote-tweet reply cycle, eligibility, context and lane markers.

Seven root adapters supply current callbacks, settings, logger, modules and
application class/exception authority on each invocation; the dependency-free
profile formatter is a root alias. Private helpers separate lookup, eligibility,
context, evaluation and durable delivery. The cycle alone owns the shared
candidate budget and preserves context/media references and receipt recovery.

Watch-list/own-post lookup and quote discovery, shared context/media/evidence,
counters, pipeline, persistence, reconciliation and delivery remain in their
existing locations. Explicit calls may read providers, generate, save caller
state and publish through supplied callbacks. Standard-library-only import does
no runtime I/O and retains no callbacks, configuration, clients or state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from types import ModuleType


def quote_tweet_is_old_enough(
    quote_tweet: dict,
    *,
    QUOTE_REPLY_DELAY_SECONDS: int,
    log: Logger,
    now_epoch: Callable,
    parse_x_datetime_to_epoch: Callable,
) -> bool:
    """Return whether quote tweet is old enough."""
    created_epoch = parse_x_datetime_to_epoch(quote_tweet.get("created_at"))

    if created_epoch is None:
        log.warning(
            "Quote tweet %s has no usable created_at; treating as too recent so it can be retried later",
            quote_tweet.get("id"),
        )
        return False

    age = now_epoch() - created_epoch
    log.debug(
        "Quote tweet id=%s age_seconds=%s required_delay=%s",
        quote_tweet.get("id"),
        age,
        QUOTE_REPLY_DELAY_SECONDS,
    )

    return age >= QUOTE_REPLY_DELAY_SECONDS


def quote_tweet_directly_quotes_original(
    quote_tweet: dict,
    original_post_id: str,
    *,
    clean_text_for_reply_context: Callable,
    re: ModuleType,
) -> bool:
    """
    The /quote_tweets endpoint can surface reposts/retweets of someone else's
    quote-tweet. Those are not good reply targets. Only treat the item as
    replyable if X's structured referenced_tweets says it directly quoted the
    original post we are checking.
    """
    original_post_id = str(original_post_id)
    refs = quote_tweet.get("referenced_tweets", []) or []

    if any(ref.get("type") == "retweeted" for ref in refs):
        return False

    if any(
        ref.get("type") == "quoted" and str(ref.get("id")) == original_post_id
        for ref in refs
    ):
        return True

    # Fallback for any odd/legacy response shape: obvious old-style retweets
    # should not be treated as quote-tweets worth replying to.
    text = clean_text_for_reply_context(quote_tweet.get("text", ""))
    if re.match(r"^RT\s+@\w+:", text):
        return False

    # Conservative default: ambiguous quote lookup results are skipped before
    # model preparation or posting.
    return False


def quote_author_profile_text(quote_tweet: dict) -> str:
    """Return the quote author profile text."""
    user = quote_tweet.get("_author_user", {}) or {}

    parts = [
        str(user.get("name", "")),
        str(user.get("username", "")),
        str(user.get("description", "")),
    ]

    public_metrics = user.get("public_metrics", {}) or {}

    if public_metrics:
        parts.append(
            "followers={followers_count} following={following_count} tweets={tweet_count}".format(
                followers_count=public_metrics.get("followers_count", ""),
                following_count=public_metrics.get("following_count", ""),
                tweet_count=public_metrics.get("tweet_count", ""),
            )
        )

    return "\n".join(part for part in parts if part.strip())


def build_quote_tweet_reply_context(
    original_tweet: dict,
    quote_tweet: dict,
    *,
    MAX_VISIBLE_TEXT_CHARACTERS: int,
    REPLY_INCOMING_MAX_CHARS: int,
    _log_single_call_context_summary: Callable,
    _reply_context_post: Callable,
    bound_visible_conversation: Callable,
    copy: ModuleType,
    current_datetime: Callable,
    reply_media_context_for_candidate: Callable,
    trim_context_text: Callable,
    tweet_context_text: Callable,
) -> dict[str, object]:
    """Build the canonical two-turn context for a direct quote-tweet."""

    target_id = str(quote_tweet.get("id") or "")
    original_id = str(original_tweet.get("id") or "")
    author_id = str(quote_tweet.get("author_id") or "")
    target_turn = _reply_context_post(
        quote_tweet,
        principal_author_id=author_id,
        maximum_chars=REPLY_INCOMING_MAX_CHARS,
    )
    original_turn = {
        "post_id": original_id,
        "author_role": "account",
        "text": trim_context_text(
            tweet_context_text(original_tweet),
            max(1, MAX_VISIBLE_TEXT_CHARACTERS - len(target_turn["text"])),
        ),
    }
    bounded_visible = bound_visible_conversation(
        [original_turn, target_turn],
        target_post_id=target_id,
    )
    visible = [
        {
            "post_id": turn["post_id"],
            "author_role": turn["role"],
            "text": turn["text"],
        }
        for turn in bounded_visible
    ]
    context: dict[str, object] = {
        "target_id": target_id,
        "thread_id": str(
            quote_tweet.get("conversation_id") or target_id
        ),
        "root_post_id": original_id,
        "parent_post_id": original_id,
        "lane": "quote_tweet",
        "incoming_contribution": target_turn["text"],
        "quoted_post": copy.deepcopy(original_turn),
        "quoted_post_id": original_turn["post_id"],
        "quoted_post_relationship": "target_quote",
        "parent_thread": [copy.deepcopy(original_turn)],
        "visible_conversation": visible,
        "visual_description": None,
        "clarification_request": None,
        "current_date": current_datetime().strftime("%Y-%m-%d"),
        "target_author_id": author_id,
        "target_created_at": str(quote_tweet.get("created_at") or ""),
        "_prepared_media_context": reply_media_context_for_candidate(
            quote_tweet,
            lane="quote_tweet",
            target_id=target_id,
            quoted_candidate=original_tweet,
        ),
    }
    _log_single_call_context_summary("Single-call quote-tweet context", context)
    return context


def mark_quote_tweet_skipped(
    state: dict,
    quote_id: str,
    *,
    append_unique_capped: Callable,
) -> None:
    """Mark quote tweet skipped."""
    quote_id = str(quote_id)

    state["seen_quote_post_ids"] = append_unique_capped(
        state.get("seen_quote_post_ids", []),
        quote_id,
        2000,
    )
    state["skipped_quote_post_ids"] = append_unique_capped(
        state.get("skipped_quote_post_ids", []),
        quote_id,
        2000,
    )


def mark_quote_tweet_replied(
    state: dict,
    quote_id: str,
    *,
    append_unique_capped: Callable,
    append_unique_durable: Callable,
) -> None:
    """Mark quote tweet replied."""
    quote_id = str(quote_id)

    state["seen_quote_post_ids"] = append_unique_capped(
        state.get("seen_quote_post_ids", []),
        quote_id,
        2000,
    )
    state["replied_to_quote_post_ids"] = append_unique_durable(
        state.get("replied_to_quote_post_ids", []),
        quote_id,
    )


def mark_quote_spam_author(
    state: dict,
    author_id: str,
    *,
    append_unique_capped: Callable,
    log: Logger,
) -> None:
    """Mark quote spam author."""
    author_id = str(author_id)

    state["quote_spam_author_ids"] = append_unique_capped(
        state.get("quote_spam_author_ids", []),
        author_id,
        2000,
    )
    log.info("Marked author_id=%s as quote spam author. spam_author_count=%d", author_id, len(state["quote_spam_author_ids"]))


@dataclass(frozen=True)
class _QuoteCandidate:
    """Keep the candidate and its identity/text as first read during iteration."""

    tweet: dict
    quote_id: str
    author_id: str
    text: str


@dataclass(frozen=True)
class _QuoteCandidateStop:
    """Continue scanning when status is absent; otherwise return that cycle status."""

    status: str | None = None


def maybe_reply_to_quote_tweets(
    state: dict,
    *,
    AmbiguousRemotePostOutcome: type[Exception],
    ApiError: type[Exception],
    CONFIRMED_REPLY_RECEIPT_FILE: Path,
    ConfirmedReplyLocalPersistenceError: type[Exception],
    ContextValidationError: type[Exception],
    ENABLE_AUTO_REPLIES: bool,
    ENABLE_QUOTE_TWEET_CHECKS: bool,
    MARK_AI_REPLIES_AS_AI: bool,
    MAX_AUTO_REPLIES_PER_DAY: int,
    MAX_QUOTE_POSTS_PER_CHECK: int,
    MAX_QUOTE_REPLIES_PER_DAY: int,
    MAX_REPLIES_PER_AUTHOR_PER_DAY: int,
    MIN_SECONDS_BETWEEN_REPLIES: int,
    MY_USER_ID: str,
    PipelineResult: type,
    ProvedRemotePostNonSuccess: type[Exception],
    QUOTE_CHECK_STATUS_CHECKED: str,
    QUOTE_CHECK_STATUS_DISABLED: str,
    QUOTE_CHECK_STATUS_POSTED: str,
    QUOTE_CHECK_STATUS_SKIPPED_CAP: str,
    QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN: str,
    QUOTE_CHECK_STATUS_SKIPPED_SPACING: str,
    RemoteOperationsPaused: type[Exception],
    ReplyEvidenceUnavailable: type[Exception],
    SINGLE_CALL_STRATEGY_VERSION: str,
    UnrecoverableConfirmedReplyPersistenceError: type[Exception],
    ValidatedReply: type,
    _is_terminal_candidate_local_failure: Callable,
    _log_validated_single_call_reply: Callable,
    _record_single_call_result: Callable,
    api_error_is_permanent_target_failure: Callable,
    api_error_is_reply_not_allowed: Callable,
    apply_confirmed_reply_receipt: Callable,
    bind_conversational_reply_attempt_time: Callable,
    block_if_ambiguous_remote_post: Callable,
    build_quote_lookup_post_ids: Callable,
    build_quote_tweet_reply_context: Callable,
    cache_tweet: Callable,
    clean_text_for_reply_context: Callable,
    clear_pending_ai_reply: Callable,
    conversational_reply_pipeline_enabled: Callable,
    copy: ModuleType,
    daily_author_reply_count: Callable,
    daily_author_reply_counts: Callable,
    generate_single_call_reply: Callable,
    get_quote_tweets_for_post: Callable,
    get_tweet_by_id_cached: Callable,
    in_api_cooldown: Callable,
    is_probably_spam_or_not_worth_replying: Callable,
    lane_paused: Callable,
    load_confirmed_reply_receipt: Callable,
    log: Logger,
    log_ai_reply_posting_outcome: Callable,
    log_event: Callable,
    mark_quote_spam_author: Callable,
    mark_quote_tweet_skipped: Callable,
    now_epoch: Callable,
    pending_ai_reply: Callable,
    post_conversational_reply_with_durable_identity: Callable,
    quote_author_profile_text: Callable,
    quote_tweet_directly_quotes_original: Callable,
    quote_tweet_is_old_enough: Callable,
    reconcile_confirmed_reply_receipt: Callable,
    record_api_error: Callable,
    record_terminal_reply_evaluation: Callable,
    recovery_comparison_account_replies: Callable,
    remove_confirmed_reply_receipt: Callable,
    reply_evidence_repository: Callable,
    reply_media_context_for_candidate: Callable,
    reply_target_is_available_immediately_before_send: Callable,
    reset_daily_quote_reply_count_if_needed: Callable,
    reset_daily_reply_count_if_needed: Callable,
    retire_lane_transport_journal_if_present: Callable,
    retire_proved_rejected_conversational_reply_receipt: Callable,
    save_state: Callable,
    store_pending_ai_reply: Callable,
    terminal_reply_evaluation: Callable,
    valid_tweets_sorted_by_id: Callable,
) -> str:
    """Process eligible quote-tweet candidates under all reply limits."""
    log.info("Starting quote-tweet reply check")
    # Finish an exact confirmed local transaction before the unresolved-
    # journal guard rejects every new remote lane.
    reset_daily_reply_count_if_needed(state)
    reset_daily_quote_reply_count_if_needed(state)
    prior_reply_status, _prior_reply = load_confirmed_reply_receipt()
    if prior_reply_status == "valid" and reconcile_confirmed_reply_receipt(state):
        log.warning(
            "Reconciled confirmed reply receipt before checking new quote-tweet candidates"
        )
    block_if_ambiguous_remote_post()

    if not ENABLE_QUOTE_TWEET_CHECKS:
        log.info("Quote-tweet checks disabled")
        return QUOTE_CHECK_STATUS_DISABLED

    if not ENABLE_AUTO_REPLIES:
        log.info("Auto replies disabled; skipping quote-tweet checks")
        return QUOTE_CHECK_STATUS_DISABLED

    if not conversational_reply_pipeline_enabled():
        log.info("Conversational reply pipeline disabled; skipping quote-tweet checks")
        return QUOTE_CHECK_STATUS_DISABLED

    if lane_paused("disable_replies", "disable_quote_replies"):
        log.info("Skipping quote-tweet check due to runtime control file")
        return QUOTE_CHECK_STATUS_DISABLED

    if (
        in_api_cooldown(state, scope="write")
        or in_api_cooldown(state, scope="openai")
        or in_api_cooldown(state, scope="quote")
    ):
        log.info("Skipping quote-tweet check due to API cooldown")
        return QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN

    if int(state.get("daily_reply_count", 0) or 0) >= MAX_AUTO_REPLIES_PER_DAY:
        log.info("Skipping quote-tweet check: total daily reply cap reached")
        return QUOTE_CHECK_STATUS_SKIPPED_CAP

    if int(state.get("daily_quote_reply_count", 0) or 0) >= MAX_QUOTE_REPLIES_PER_DAY:
        log.info("Skipping quote-tweet check: daily quote-reply cap reached")
        return QUOTE_CHECK_STATUS_SKIPPED_CAP

    current = now_epoch()
    seconds_since_last_reply = current - int(state.get("last_reply_epoch", 0) or 0)

    if seconds_since_last_reply < MIN_SECONDS_BETWEEN_REPLIES:
        log.info(
            "Skipping quote-tweet check: minimum interval between replies not reached. seconds_since=%s",
            seconds_since_last_reply,
        )
        return QUOTE_CHECK_STATUS_SKIPPED_SPACING

    own_post_ids_for_quote_lookup = build_quote_lookup_post_ids(state)

    if not own_post_ids_for_quote_lookup:
        log.info("No own posts available for quote lookup")
        return QUOTE_CHECK_STATUS_CHECKED

    seen_quote_ids = set(str(x) for x in state.get("seen_quote_post_ids", []))
    replied_quote_ids = set(str(x) for x in state.get("replied_to_quote_post_ids", []))
    skipped_quote_ids = set(str(x) for x in state.get("skipped_quote_post_ids", []))
    quote_spam_author_ids = set(str(x) for x in state.get("quote_spam_author_ids", []))
    replied_to_ids = set(str(x) for x in state.get("replied_to_ids", []))
    daily_author_reply_counts(state)

    # Only this counter spans originals; charge after context/media extraction,
    # even when evaluation makes no model call. Snapshot sets above stay fixed
    # except for newly discovered spam authors.
    processed_candidates = 0

    for original_post_id in own_post_ids_for_quote_lookup:
        if processed_candidates >= MAX_QUOTE_POSTS_PER_CHECK:
            break

        lookup = _lookup_quote_candidates(
            original_post_id,
            state,
            ApiError=ApiError,
            QUOTE_CHECK_STATUS_CHECKED=QUOTE_CHECK_STATUS_CHECKED,
            get_quote_tweets_for_post=get_quote_tweets_for_post,
            get_tweet_by_id_cached=get_tweet_by_id_cached,
            log=log,
            record_api_error=record_api_error,
            save_state=save_state,
        )
        if isinstance(lookup, _QuoteCandidateStop):
            if lookup.status is not None:
                return lookup.status
            continue
        original_tweet, quote_tweets = lookup

        for quote_tweet in valid_tweets_sorted_by_id(quote_tweets, context="quote-tweet candidate"):
            if processed_candidates >= MAX_QUOTE_POSTS_PER_CHECK:
                break
            if in_api_cooldown(state, scope="openai"):
                log.info(
                    "Stopping quote-tweet candidate iteration because the "
                    "OpenAI cooldown became active"
                )
                save_state(state, durable=True)
                return QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN

            candidate = _QuoteCandidate(
                quote_tweet,
                str(quote_tweet.get("id", "")),
                str(quote_tweet.get("author_id", "")),
                quote_tweet.get("text", ""),
            )
            if not _candidate_is_eligible(
                candidate,
                original_post_id,
                state,
                seen_quote_ids,
                replied_quote_ids,
                skipped_quote_ids,
                quote_spam_author_ids,
                replied_to_ids,
                MY_USER_ID=MY_USER_ID,
                log=log,
                mark_quote_tweet_skipped=mark_quote_tweet_skipped,
                quote_tweet_directly_quotes_original=quote_tweet_directly_quotes_original,
                quote_tweet_is_old_enough=quote_tweet_is_old_enough,
                save_state=save_state,
                terminal_reply_evaluation=terminal_reply_evaluation,
            ):
                continue
            if not _author_allows_evaluation(
                candidate,
                original_post_id,
                original_tweet,
                state,
                quote_spam_author_ids,
                MAX_REPLIES_PER_AUTHOR_PER_DAY=MAX_REPLIES_PER_AUTHOR_PER_DAY,
                MY_USER_ID=MY_USER_ID,
                cache_tweet=cache_tweet,
                clean_text_for_reply_context=clean_text_for_reply_context,
                daily_author_reply_count=daily_author_reply_count,
                is_probably_spam_or_not_worth_replying=is_probably_spam_or_not_worth_replying,
                log=log,
                log_event=log_event,
                mark_quote_spam_author=mark_quote_spam_author,
                mark_quote_tweet_skipped=mark_quote_tweet_skipped,
                quote_author_profile_text=quote_author_profile_text,
                save_state=save_state,
            ):
                continue

            prepared = _prepare_reply_context(
                candidate,
                original_post_id,
                state,
                ApiError=ApiError,
                ContextValidationError=ContextValidationError,
                PipelineResult=PipelineResult,
                QUOTE_CHECK_STATUS_CHECKED=QUOTE_CHECK_STATUS_CHECKED,
                _record_single_call_result=_record_single_call_result,
                api_error_is_permanent_target_failure=api_error_is_permanent_target_failure,
                build_quote_tweet_reply_context=build_quote_tweet_reply_context,
                cache_tweet=cache_tweet,
                get_tweet_by_id_cached=get_tweet_by_id_cached,
                log=log,
                mark_quote_tweet_skipped=mark_quote_tweet_skipped,
                record_api_error=record_api_error,
                record_terminal_reply_evaluation=record_terminal_reply_evaluation,
                save_state=save_state,
            )
            if isinstance(prepared, _QuoteCandidateStop):
                if prepared.status is not None:
                    return prepared.status
                continue
            original_context_tweet, reply_context, prepared_media_context = prepared

            processed_candidates += 1
            evaluation = _evaluate_reply(
                candidate,
                original_context_tweet,
                reply_context,
                prepared_media_context,
                state,
                ApiError=ApiError,
                QUOTE_CHECK_STATUS_CHECKED=QUOTE_CHECK_STATUS_CHECKED,
                RemoteOperationsPaused=RemoteOperationsPaused,
                ReplyEvidenceUnavailable=ReplyEvidenceUnavailable,
                _is_terminal_candidate_local_failure=_is_terminal_candidate_local_failure,
                generate_single_call_reply=generate_single_call_reply,
                log=log,
                log_event=log_event,
                pending_ai_reply=pending_ai_reply,
                record_api_error=record_api_error,
                recovery_comparison_account_replies=recovery_comparison_account_replies,
                reply_evidence_repository=reply_evidence_repository,
                reply_media_context_for_candidate=reply_media_context_for_candidate,
                save_state=save_state,
            )
            if isinstance(evaluation, _QuoteCandidateStop):
                return evaluation.status
            reply_text, evaluation_outcome = evaluation
            decision = _resolve_reply_evaluation(
                candidate.quote_id,
                reply_text,
                evaluation_outcome,
                state,
                QUOTE_CHECK_STATUS_CHECKED=QUOTE_CHECK_STATUS_CHECKED,
                ValidatedReply=ValidatedReply,
                _is_terminal_candidate_local_failure=_is_terminal_candidate_local_failure,
                log=log,
                mark_quote_tweet_skipped=mark_quote_tweet_skipped,
                record_terminal_reply_evaluation=record_terminal_reply_evaluation,
                save_state=save_state,
            )
            if decision is not None:
                if decision.status is not None:
                    return decision.status
                continue

            receipt_template = _prepare_reply_receipt(
                candidate,
                original_post_id,
                reply_text,
                reply_context,
                state,
                QUOTE_CHECK_STATUS_CHECKED=QUOTE_CHECK_STATUS_CHECKED,
                SINGLE_CALL_STRATEGY_VERSION=SINGLE_CALL_STRATEGY_VERSION,
                _log_validated_single_call_reply=_log_validated_single_call_reply,
                bind_conversational_reply_attempt_time=bind_conversational_reply_attempt_time,
                copy=copy,
                log=log,
                log_event=log_event,
                save_state=save_state,
                store_pending_ai_reply=store_pending_ai_reply,
            )
            if isinstance(receipt_template, _QuoteCandidateStop):
                return receipt_template.status
            receipt = _deliver_reply(
                candidate.quote_id,
                reply_text,
                receipt_template,
                state,
                AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
                ApiError=ApiError,
                ConfirmedReplyLocalPersistenceError=ConfirmedReplyLocalPersistenceError,
                MARK_AI_REPLIES_AS_AI=MARK_AI_REPLIES_AS_AI,
                ProvedRemotePostNonSuccess=ProvedRemotePostNonSuccess,
                QUOTE_CHECK_STATUS_CHECKED=QUOTE_CHECK_STATUS_CHECKED,
                UnrecoverableConfirmedReplyPersistenceError=UnrecoverableConfirmedReplyPersistenceError,
                api_error_is_reply_not_allowed=api_error_is_reply_not_allowed,
                clear_pending_ai_reply=clear_pending_ai_reply,
                log=log,
                log_ai_reply_posting_outcome=log_ai_reply_posting_outcome,
                log_event=log_event,
                mark_quote_tweet_skipped=mark_quote_tweet_skipped,
                post_conversational_reply_with_durable_identity=post_conversational_reply_with_durable_identity,
                record_api_error=record_api_error,
                record_terminal_reply_evaluation=record_terminal_reply_evaluation,
                reply_target_is_available_immediately_before_send=reply_target_is_available_immediately_before_send,
                retire_proved_rejected_conversational_reply_receipt=retire_proved_rejected_conversational_reply_receipt,
                save_state=save_state,
            )
            if isinstance(receipt, _QuoteCandidateStop):
                return receipt.status
            return _finalise_confirmed_reply(
                candidate,
                original_post_id,
                receipt,
                state,
                CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
                ConfirmedReplyLocalPersistenceError=ConfirmedReplyLocalPersistenceError,
                QUOTE_CHECK_STATUS_POSTED=QUOTE_CHECK_STATUS_POSTED,
                apply_confirmed_reply_receipt=apply_confirmed_reply_receipt,
                log=log,
                log_event=log_event,
                remove_confirmed_reply_receipt=remove_confirmed_reply_receipt,
                retire_lane_transport_journal_if_present=retire_lane_transport_journal_if_present,
                save_state=save_state,
            )

    save_state(state)
    log.info("Quote-tweet reply check finished with no reply generated/posted")
    return QUOTE_CHECK_STATUS_CHECKED


def _lookup_quote_candidates(
    original_post_id: str,
    state: dict,
    *,
    ApiError: type[Exception],
    QUOTE_CHECK_STATUS_CHECKED: str,
    get_quote_tweets_for_post: Callable,
    get_tweet_by_id_cached: Callable,
    log: Logger,
    record_api_error: Callable,
    save_state: Callable,
) -> tuple[dict, list] | _QuoteCandidateStop:
    """Fetch one original and its quotes, preserving skip-original versus stop-cycle failures."""
    try:
        original_tweet = get_tweet_by_id_cached(original_post_id, state)
    except ApiError as e:
        log.exception("Failed to fetch original own post %s", original_post_id)
        record_api_error(state, e, "x", scope="quote")
        save_state(state)
        return _QuoteCandidateStop()
    except Exception:
        log.exception("Unexpected failure fetching original own post %s", original_post_id)
        save_state(state)
        return _QuoteCandidateStop()

    if not original_tweet:
        log.info("Could not find/fetch original own post %s", original_post_id)
        return _QuoteCandidateStop()

    try:
        quote_tweets = get_quote_tweets_for_post(original_post_id, state)
    except ApiError as e:
        log.exception("Failed to fetch quote tweets for post %s", original_post_id)
        record_api_error(state, e, "x", scope="quote")
        save_state(state)
        return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)
    except Exception:
        log.exception("Unexpected failure fetching quote tweets for post %s", original_post_id)
        save_state(state)
        return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)
    return original_tweet, quote_tweets


def _candidate_is_eligible(
    candidate: _QuoteCandidate,
    original_post_id: str,
    state: dict,
    seen_quote_ids: set[str],
    replied_quote_ids: set[str],
    skipped_quote_ids: set[str],
    quote_spam_author_ids: set[str],
    replied_to_ids: set[str],
    *,
    MY_USER_ID: str,
    log: Logger,
    mark_quote_tweet_skipped: Callable,
    quote_tweet_directly_quotes_original: Callable,
    quote_tweet_is_old_enough: Callable,
    save_state: Callable,
    terminal_reply_evaluation: Callable,
) -> bool:
    """Apply identity, relationship and age gates against the cycle's original snapshots."""
    quote_tweet = candidate.tweet
    quote_id = candidate.quote_id
    author_id = candidate.author_id
    quote_text = candidate.text

    if not quote_id:
        return False

    if author_id in quote_spam_author_ids:
        log.info(
            "Skipping quote tweet %s: author_id=%s is in quote spam author list",
            quote_id,
            author_id,
        )
        mark_quote_tweet_skipped(state, quote_id)
        save_state(state)
        return False

    log.info(
        "Considering quote tweet id=%s author_id=%s original_post_id=%s text=%r",
        quote_id,
        author_id,
        original_post_id,
        quote_text,
    )

    if quote_id in seen_quote_ids or quote_id in replied_quote_ids or quote_id in skipped_quote_ids:
        log.info("Skipping quote tweet %s: already seen/replied/skipped", quote_id)
        return False

    prior_evaluation = terminal_reply_evaluation(state, quote_id)
    if prior_evaluation is not None:
        log.info(
            "Skipping quote tweet %s: terminal %s evaluation already recorded reason=%s",
            quote_id,
            prior_evaluation.get("outcome", "no_reply"),
            prior_evaluation.get("reason", ""),
        )
        mark_quote_tweet_skipped(state, quote_id)
        save_state(state)
        return False

    if not quote_tweet_directly_quotes_original(quote_tweet, original_post_id):
        log.info(
            "Skipping quote tweet %s: not a direct quote of original post %s. referenced_tweets=%s",
            quote_id,
            original_post_id,
            quote_tweet.get("referenced_tweets", []),
        )
        mark_quote_tweet_skipped(state, quote_id)
        save_state(state)
        return False

    if quote_id in replied_to_ids:
        log.info("Skipping quote tweet %s: already handled by normal mention path", quote_id)
        mark_quote_tweet_skipped(state, quote_id)
        save_state(state)
        return False

    if author_id == str(MY_USER_ID):
        log.info("Skipping quote tweet %s: authored by own account", quote_id)
        mark_quote_tweet_skipped(state, quote_id)
        save_state(state)
        return False

    if not quote_tweet_is_old_enough(quote_tweet):
        log.info(
            "Quote tweet %s is too recent; leaving unmarked so it can be checked later",
            quote_id,
        )
        return False
    return True


def _author_allows_evaluation(
    candidate: _QuoteCandidate,
    original_post_id: str,
    original_tweet: dict,
    state: dict,
    quote_spam_author_ids: set[str],
    *,
    MAX_REPLIES_PER_AUTHOR_PER_DAY: int,
    MY_USER_ID: str,
    cache_tweet: Callable,
    clean_text_for_reply_context: Callable,
    daily_author_reply_count: Callable,
    is_probably_spam_or_not_worth_replying: Callable,
    log: Logger,
    log_event: Callable,
    mark_quote_spam_author: Callable,
    mark_quote_tweet_skipped: Callable,
    quote_author_profile_text: Callable,
    save_state: Callable,
) -> bool:
    """Check cleaned text and author limits, retaining cap context and newly found spam."""
    quote_tweet = candidate.tweet
    quote_id = candidate.quote_id
    author_id = candidate.author_id
    quote_text = candidate.text

    cleaned_quote_text = clean_text_for_reply_context(quote_text)
    cleaned_author_profile = clean_text_for_reply_context(
        quote_author_profile_text(quote_tweet)
    )
    spam_check_text = f"{cleaned_quote_text}\n{cleaned_author_profile}".strip()
    quote_is_usable = bool(cleaned_quote_text) and not is_probably_spam_or_not_worth_replying(
        spam_check_text
    )

    if daily_author_reply_count(state, author_id) >= MAX_REPLIES_PER_AUTHOR_PER_DAY:
        log.info(
            "Skipping quote tweet %s: already reached per-author daily cap for author_id=%s",
            quote_id,
            author_id,
        )
        if quote_is_usable:
            cache_tweet(
                state,
                tweet_id=str(original_tweet.get("id", original_post_id)),
                text=original_tweet.get("text", ""),
                author_id=str(original_tweet.get("author_id", MY_USER_ID)),
                conversation_id=str(original_tweet.get("conversation_id", original_post_id)),
                referenced_tweets=original_tweet.get("referenced_tweets", []),
                created_at=original_tweet.get("created_at"),
                image_summary=original_tweet.get("image_summary"),
                post_type=original_tweet.get("post_type"),
            )
            cache_tweet(
                state,
                tweet_id=quote_id,
                text=quote_text,
                author_id=author_id,
                conversation_id=str(quote_tweet.get("conversation_id", quote_id)),
                referenced_tweets=quote_tweet.get("referenced_tweets", []),
                created_at=quote_tweet.get("created_at"),
                post_type="author_cap_quote_context",
            )
        mark_quote_tweet_skipped(state, quote_id)
        save_state(state)
        return False

    if not cleaned_quote_text:
        log.info("Skipping quote tweet %s: no usable quote text after cleaning", quote_id)
        mark_quote_tweet_skipped(state, quote_id)
        log_event("quote_tweet_skipped", quote_tweet_id=quote_id, reason="no_usable_quote_text")
        save_state(state)
        return False

    if not quote_is_usable:
        log.info(
            "Skipping quote tweet %s: quote text/profile matched spam; marking author_id=%s as quote spam",
            quote_id,
            author_id,
        )
        log.debug("Quote spam check text for quote_id=%s: %r", quote_id, spam_check_text)
        mark_quote_tweet_skipped(state, quote_id)
        mark_quote_spam_author(state, author_id)
        quote_spam_author_ids.add(author_id)
        save_state(state)
        return False
    return True


def _prepare_reply_context(
    candidate: _QuoteCandidate,
    original_post_id: str,
    state: dict,
    *,
    ApiError: type[Exception],
    ContextValidationError: type[Exception],
    PipelineResult: type,
    QUOTE_CHECK_STATUS_CHECKED: str,
    _record_single_call_result: Callable,
    api_error_is_permanent_target_failure: Callable,
    build_quote_tweet_reply_context: Callable,
    cache_tweet: Callable,
    get_tweet_by_id_cached: Callable,
    log: Logger,
    mark_quote_tweet_skipped: Callable,
    record_api_error: Callable,
    record_terminal_reply_evaluation: Callable,
    save_state: Callable,
) -> tuple[dict, dict, object] | _QuoteCandidateStop:
    """Refetch media, cache the quote and build context before charging its candidate budget."""
    quote_tweet = candidate.tweet
    quote_id = candidate.quote_id
    author_id = candidate.author_id
    quote_text = candidate.text

    try:
        original_context_tweet = get_tweet_by_id_cached(
            original_post_id,
            state,
            include_media=True,
        )
    except ApiError as exc:
        if api_error_is_permanent_target_failure(exc):
            log.warning(
                "Retiring quote tweet %s because its directly quoted "
                "post is permanently unavailable",
                quote_id,
            )
            _record_single_call_result(
                PipelineResult(
                    status="operational_failure",
                    reason="quoted_post_context_unavailable",
                    error_category="context_validation",
                    local_validation_status="not_run",
                ),
                lane="quote_tweet",
                target_id=quote_id,
            )
            record_terminal_reply_evaluation(
                state,
                target_id=quote_id,
                lane="quote_tweet",
                reason="quoted_post_context_unavailable",
                outcome="operational_failure",
            )
            mark_quote_tweet_skipped(state, quote_id)
            save_state(state, durable=True)
            return _QuoteCandidateStop()
        log.exception(
            "Could not collect directly quoted post context for quote "
            "tweet %s",
            quote_id,
        )
        record_api_error(state, exc, "x", scope="quote")
        save_state(state)
        return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)
    if original_context_tweet is None:
        log.warning(
            "Deferring quote tweet %s because its directly quoted "
            "post could not be collected",
            quote_id,
        )
        _record_single_call_result(
            PipelineResult(
                status="operational_failure",
                reason="quoted_post_context_unavailable",
                error_category="context_validation",
                local_validation_status="not_run",
            ),
            lane="quote_tweet",
            target_id=quote_id,
        )
        record_terminal_reply_evaluation(
            state,
            target_id=quote_id,
            lane="quote_tweet",
            reason="quoted_post_context_unavailable",
            outcome="operational_failure",
        )
        mark_quote_tweet_skipped(state, quote_id)
        save_state(state, durable=True)
        return _QuoteCandidateStop()

    cache_tweet(
        state,
        tweet_id=quote_id,
        text=quote_text,
        author_id=author_id,
        conversation_id=str(quote_tweet.get("conversation_id", quote_id)),
        referenced_tweets=quote_tweet.get("referenced_tweets", []),
        created_at=quote_tweet.get("created_at"),
        post_type="quote_tweet",
    )
    save_state(state)

    try:
        reply_context = build_quote_tweet_reply_context(
            original_context_tweet,
            quote_tweet,
        )
    except (ContextValidationError, TypeError, ValueError, UnicodeError) as exc:
        log.warning(
            "Retiring quote tweet %s after permanent canonical-context "
            "validation failure: %s",
            quote_id,
            exc,
        )
        _record_single_call_result(
            PipelineResult(
                status="operational_failure",
                reason="canonical_context_unavailable",
                error_category="context_validation",
                local_validation_status="failed",
            ),
            lane="quote_tweet",
            target_id=quote_id,
        )
        record_terminal_reply_evaluation(
            state,
            target_id=quote_id,
            lane="quote_tweet",
            reason="canonical_context_unavailable",
            outcome="operational_failure",
        )
        mark_quote_tweet_skipped(state, quote_id)
        save_state(state, durable=True)
        return _QuoteCandidateStop()
    prepared_media_context = reply_context.pop(
        "_prepared_media_context",
        None,
    )
    return original_context_tweet, reply_context, prepared_media_context


def _evaluate_reply(
    candidate: _QuoteCandidate,
    original_context_tweet: dict,
    reply_context: dict,
    prepared_media_context: object,
    state: dict,
    *,
    ApiError: type[Exception],
    QUOTE_CHECK_STATUS_CHECKED: str,
    RemoteOperationsPaused: type[Exception],
    ReplyEvidenceUnavailable: type[Exception],
    _is_terminal_candidate_local_failure: Callable,
    generate_single_call_reply: Callable,
    log: Logger,
    log_event: Callable,
    pending_ai_reply: Callable,
    record_api_error: Callable,
    recovery_comparison_account_replies: Callable,
    reply_evidence_repository: Callable,
    reply_media_context_for_candidate: Callable,
    save_state: Callable,
) -> tuple[object, dict] | _QuoteCandidateStop:
    """Check evidence and recover or generate a draft with the original exception boundaries."""
    quote_tweet = candidate.tweet
    quote_id = candidate.quote_id

    try:
        reply_evidence_repository()
    except ReplyEvidenceUnavailable as exc:
        log.error(
            "Skipping quote-tweet reply target_id=%s because local evidence is unavailable: %s",
            quote_id,
            exc,
        )
        log_event(
            "reply_evidence_unavailable",
            lane="quote_tweet",
            target_id=quote_id,
        )
        return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)

    media_context = (
        prepared_media_context
        if isinstance(prepared_media_context, dict)
        else reply_media_context_for_candidate(
            quote_tweet,
            lane="quote_tweet",
            target_id=quote_id,
            quoted_candidate=original_context_tweet,
        )
    )

    evaluation_outcome: dict[str, object] = {}
    reply_text = pending_ai_reply(
        state,
        quote_id,
        "quote_tweet",
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
            reply_text = generate_single_call_reply(
                reply_context,
                media_context,
                state=state,
                evaluation_outcome=evaluation_outcome,
            )
        elif reply_text is not None:
            log.info(
                "Reusing persisted single-call reply draft "
                "target_id=%s source=quote_tweet",
                quote_id,
            )
    except RemoteOperationsPaused:
        log.info(
            "Deferring quote-tweet reply target_id=%s "
            "reason=global_runtime_control_pause",
            quote_id,
        )
        save_state(state, durable=True)
        return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)
    except ApiError as exc:
        log.exception("OpenAI single-call quote-tweet reply failed")
        if exc.service == "openai":
            record_api_error(state, exc, "openai")
        save_state(state)
        return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)
    except Exception:
        log.exception("Unexpected single-call quote-tweet reply failure")
        save_state(state)
        return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)
    return reply_text, evaluation_outcome


def _resolve_reply_evaluation(
    quote_id: str,
    reply_text: object,
    evaluation_outcome: dict,
    state: dict,
    *,
    QUOTE_CHECK_STATUS_CHECKED: str,
    ValidatedReply: type,
    _is_terminal_candidate_local_failure: Callable,
    log: Logger,
    mark_quote_tweet_skipped: Callable,
    record_terminal_reply_evaluation: Callable,
    save_state: Callable,
) -> _QuoteCandidateStop | None:
    """Retire terminal decisions, defer retryable failures, or allow a validated reply through."""
    if not reply_text:
        if _is_terminal_candidate_local_failure(evaluation_outcome):
            failure_category = str(evaluation_outcome["error_category"])
            failure_reason = str(
                evaluation_outcome.get("reason") or failure_category
            )
            log.warning(
                "Retiring quote tweet %s after permanent "
                "candidate-local reply failure category=%s reason=%s",
                quote_id,
                failure_category,
                failure_reason,
            )
            record_terminal_reply_evaluation(
                state,
                target_id=quote_id,
                lane="quote_tweet",
                reason=failure_reason,
                outcome="operational_failure",
            )
            mark_quote_tweet_skipped(state, quote_id)
            save_state(state, durable=True)
            return _QuoteCandidateStop()
        if evaluation_outcome.get("status") != "no_reply":
            log.warning(
                "Deferring quote tweet %s after operational reply "
                "failure reason=%s",
                quote_id,
                evaluation_outcome.get("reason", "unknown"),
            )
            save_state(state, durable=True)
            return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)
        reason_code = str(
            evaluation_outcome.get("reason_code")
            or evaluation_outcome.get("reason")
            or "model_selected_no_reply"
        )
        record_terminal_reply_evaluation(
            state,
            target_id=quote_id,
            lane="quote_tweet",
            reason=reason_code,
        )
        log.info(
            "Sol selected no_reply for quote tweet %s reason=%s",
            quote_id,
            reason_code,
        )
        mark_quote_tweet_skipped(state, quote_id)
        save_state(state, durable=True)
        return _QuoteCandidateStop()
    if not isinstance(reply_text, ValidatedReply):
        log.error(
            "Single-call pipeline returned an unvalidated quote-tweet "
            "reply; deferring target_id=%s",
            quote_id,
        )
        save_state(state, durable=True)
        return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)
    return None


def _prepare_reply_receipt(
    candidate: _QuoteCandidate,
    original_post_id: str,
    reply_text: object,
    reply_context: dict,
    state: dict,
    *,
    QUOTE_CHECK_STATUS_CHECKED: str,
    SINGLE_CALL_STRATEGY_VERSION: str,
    _log_validated_single_call_reply: Callable,
    bind_conversational_reply_attempt_time: Callable,
    copy: ModuleType,
    log: Logger,
    log_event: Callable,
    save_state: Callable,
    store_pending_ai_reply: Callable,
) -> dict | _QuoteCandidateStop:
    """Persist the validated draft before copying and binding its sending receipt."""
    quote_tweet = candidate.tweet
    quote_id = candidate.quote_id
    author_id = candidate.author_id

    _log_validated_single_call_reply(
        target_description="quote tweet",
        target_id=quote_id,
        reply=reply_text,
    )
    draft_stored = store_pending_ai_reply(
        state,
        quote_id,
        "quote_tweet",
        reply_text,
        context=reply_context,
    )
    if not draft_stored:
        log.error(
            "Single-call reply draft failed persistence validation; "
            "deferring target_id=%s source=quote_tweet",
            quote_id,
        )
        log_event(
            "single_call_reply_posting_outcome",
            status="draft_persistence_failed",
            lane="quote_tweet",
            target_id=quote_id,
            strategy_version=SINGLE_CALL_STRATEGY_VERSION,
            reply_kind=reply_text.draft_record.get("reply_kind"),
            reason_code=reply_text.draft_record.get("reason_code"),
            validated_draft_hash=reply_text.draft_record.get(
                "validated_draft_hash"
            ),
            failure_reason="draft_persistence_validation_failed",
        )
        save_state(state, durable=True)
        return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)
    save_state(state, durable=True)
    receipt_template = {
        "schema_version": 4,
        "lifecycle_state": "sending",
        "target_id": quote_id,
        "author_id": author_id,
        "candidate_source": "quote_tweet",
        "conversation_id": str(
            quote_tweet.get("conversation_id", quote_id)
        ),
        "reply_text": reply_text,
        "original_post_id": str(original_post_id),
        "reply_context": copy.deepcopy(reply_context),
        "ai_reply_draft": copy.deepcopy(reply_text.draft_record),
    }
    receipt_template = bind_conversational_reply_attempt_time(
        receipt_template
    )
    return receipt_template


def _deliver_reply(
    quote_id: str,
    reply_text: object,
    receipt_template: dict,
    state: dict,
    *,
    AmbiguousRemotePostOutcome: type[Exception],
    ApiError: type[Exception],
    ConfirmedReplyLocalPersistenceError: type[Exception],
    MARK_AI_REPLIES_AS_AI: bool,
    ProvedRemotePostNonSuccess: type[Exception],
    QUOTE_CHECK_STATUS_CHECKED: str,
    UnrecoverableConfirmedReplyPersistenceError: type[Exception],
    api_error_is_reply_not_allowed: Callable,
    clear_pending_ai_reply: Callable,
    log: Logger,
    log_ai_reply_posting_outcome: Callable,
    log_event: Callable,
    mark_quote_tweet_skipped: Callable,
    post_conversational_reply_with_durable_identity: Callable,
    record_api_error: Callable,
    record_terminal_reply_evaluation: Callable,
    reply_target_is_available_immediately_before_send: Callable,
    retire_proved_rejected_conversational_reply_receipt: Callable,
    save_state: Callable,
) -> dict | _QuoteCandidateStop:
    """Recheck availability and deliver, retaining terminal, retryable and confirmed outcomes."""
    try:
        if not reply_target_is_available_immediately_before_send(quote_id):
            log.warning(
                "Cannot reply to quote tweet %s because it disappeared "
                "after evaluation; marking it skipped without consuming "
                "reply quota",
                quote_id,
            )
            log_ai_reply_posting_outcome(
                reply=reply_text,
                status="posting_failed_terminal",
                lane="quote_tweet",
                target_id=quote_id,
                failure_reason="target_unavailable_pre_send",
            )
            log_event(
                "reply_target_terminal",
                lane="quote_tweet",
                target_id=quote_id,
                outcome="reply_not_permitted",
                reason="x_target_unavailable_pre_send",
            )
            record_terminal_reply_evaluation(
                state,
                target_id=quote_id,
                lane="quote_tweet",
                reason="x_target_unavailable_pre_send",
                outcome="reply_not_permitted",
            )
            mark_quote_tweet_skipped(state, quote_id)
            clear_pending_ai_reply(state, quote_id, "quote_tweet")
            save_state(state, durable=True)
            return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)
        reply_response, receipt = (
            post_conversational_reply_with_durable_identity(
                state=state,
                receipt_template=receipt_template,
                reply_text=reply_text,
                reply_to_id=quote_id,
                made_with_ai=MARK_AI_REPLIES_AS_AI,
                lane="quote_tweet",
            )
        )
    except UnrecoverableConfirmedReplyPersistenceError:
        log.critical(
            "Confirmed quote-tweet reply lost every complete durable local "
            "identity; the global remote-write safety barrier remains active",
            exc_info=True,
        )
        raise
    except ConfirmedReplyLocalPersistenceError:
        log.critical(
            "Confirmed quote-tweet reply required its durable state fallback",
            exc_info=True,
        )
        raise
    except AmbiguousRemotePostOutcome:
        log.critical(
            "Quote-tweet reply stopped after an ambiguous remote outcome; "
            "the global remote-write safety barrier remains active",
            exc_info=True,
        )
        log_ai_reply_posting_outcome(
            reply=reply_text,
            status="posting_failed_retryable",
            lane="quote_tweet",
            target_id=quote_id,
            failure_reason="ambiguous_remote_outcome",
        )
        raise
    except ApiError as e:
        if api_error_is_reply_not_allowed(e):
            log.warning(
                "Cannot reply to quote tweet %s because X says replies are not allowed; "
                "marking quote tweet as skipped without consuming reply quota",
                quote_id,
            )
            log_ai_reply_posting_outcome(
                reply=reply_text,
                status="posting_failed_terminal",
                lane="quote_tweet",
                target_id=quote_id,
                failure_reason="reply_not_permitted",
            )
            log_event(
                "reply_target_terminal",
                lane="quote_tweet",
                target_id=quote_id,
                outcome="reply_not_permitted",
                reason="x_reply_not_permitted",
            )
            record_terminal_reply_evaluation(
                state,
                target_id=quote_id,
                lane="quote_tweet",
                reason="x_reply_not_permitted",
                outcome="reply_not_permitted",
            )
            mark_quote_tweet_skipped(state, quote_id)
            clear_pending_ai_reply(state, quote_id, "quote_tweet")
            save_state(state, durable=True)
            if isinstance(e, ProvedRemotePostNonSuccess):
                retire_proved_rejected_conversational_reply_receipt(
                    receipt_template,
                    e,
                )
            return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)

        log.exception("Failed to post generated quote-tweet reply")
        log_ai_reply_posting_outcome(
            reply=reply_text,
            status="posting_failed_retryable",
            lane="quote_tweet",
            target_id=quote_id,
            failure_reason=f"x_api_{getattr(e, 'status_code', 'error')}",
        )
        record_api_error(state, e, "x", scope="write")
        save_state(state)
        return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)
    except Exception as e:
        log.exception("Unexpected failure posting generated quote-tweet reply")
        log_ai_reply_posting_outcome(
            reply=reply_text,
            status="posting_failed_retryable",
            lane="quote_tweet",
            target_id=quote_id,
            failure_reason="unexpected_posting_error",
        )
        record_api_error(state, e, "x", scope="write")
        save_state(state)
        return _QuoteCandidateStop(QUOTE_CHECK_STATUS_CHECKED)
    return receipt


def _finalise_confirmed_reply(
    candidate: _QuoteCandidate,
    original_post_id: str,
    receipt: dict,
    state: dict,
    *,
    CONFIRMED_REPLY_RECEIPT_FILE: Path,
    ConfirmedReplyLocalPersistenceError: type[Exception],
    QUOTE_CHECK_STATUS_POSTED: str,
    apply_confirmed_reply_receipt: Callable,
    log: Logger,
    log_event: Callable,
    remove_confirmed_reply_receipt: Callable,
    retire_lane_transport_journal_if_present: Callable,
    save_state: Callable,
) -> str:
    """Apply and save confirmation before retiring recovery records and reporting success."""
    quote_id = candidate.quote_id
    author_id = candidate.author_id

    own_reply_id = str(receipt["reply_post_id"])

    apply_confirmed_reply_receipt(state, receipt)
    log.info("Recorded and cached own quote-tweet auto-reply id=%s", own_reply_id)
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
            "Confirmed quote-tweet reply id=%s to target=%s was saved but receipt removal failed",
            own_reply_id,
            quote_id,
            exc_info=True,
        )
        raise ConfirmedReplyLocalPersistenceError(
            f"Confirmed quote-tweet reply {own_reply_id} to {quote_id} but receipt removal failed"
        ) from exc

    log_event(
        "reply_posted",
        lane="quote_tweet",
        target_id=quote_id,
        author_id=author_id,
        original_post_id=original_post_id,
        reply_post_id=own_reply_id,
        daily_reply_count=state.get("daily_reply_count"),
        daily_quote_reply_count=state.get("daily_quote_reply_count"),
    )
    log.info("Quote-tweet reply posted successfully")
    return QUOTE_CHECK_STATUS_POSTED
