"""Own the quote-tweet reply cycle, eligibility and lane markers.

The cycle receives typed settings, persistence and delivery boundaries plus
context, tweet lookup, generation, history, evaluation, accounting, cooldown,
runtime-control and watched-post owners.
The root composes these owners once per invocation; private helpers call their
operations directly. The dependency-free
profile formatter is a root alias. Private helpers separate lookup, eligibility,
context, evaluation and durable delivery. The cycle alone owns the shared
candidate budget and preserves context/media references and receipt recovery.

The watched-post owner performs watch-list/own-post lookup directly; quote
discovery, shared context/media/evidence, counters, pipeline, persistence,
reconciliation and delivery remain in their existing locations. Explicit calls
may read providers, generate, save caller state and publish through supplied
callbacks. Fixed check statuses and terminal evaluation lookup and text cleanup
are imported from their inert owners; dependency-free quote helpers are called
locally. Imports do no runtime I/O and retain no callbacks, configuration,
clients or state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from typing import TYPE_CHECKING

from mrs_bot_runtime_state_helpers import append_unique_capped
from mrs_bot_reply_state import (
    mark_quote_tweet_replied, mark_quote_tweet_skipped,
    remove_pending_quote_candidate,
)

from mrs_bot_daily_reply_accounting import daily_author_reply_counts
from mrs_bot_reply_context import clean_text_for_reply_context
from mrs_bot_reply_generation import _is_terminal_candidate_local_failure
from mrs_bot_reply_cycle_interfaces import (
    QUOTE_CHECK_STATUS_CHECKED,
    QUOTE_CHECK_STATUS_DISABLED,
    QUOTE_CHECK_STATUS_POSTED,
    QUOTE_CHECK_STATUS_SKIPPED_CAP,
    QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN,
    QUOTE_CHECK_STATUS_SKIPPED_SPACING,
    FinishReplyCheck, PreparedReplyContext, QuoteReplyConfig,
    QuoteTweetDiscovery, ReplyCycleDelivery, ReplyCyclePersistence,
    SkipReplyCandidate,
)
from mrs_bot_reply_delivery import ReplyDeliveryStop
from mrs_bot_reply_evaluation_state import terminal_reply_evaluation
from mrs_bot_reply_preparation import (
    build_sending_reply_receipt,
    persist_validated_reply_draft,
)

if TYPE_CHECKING:
    from mrs_bot_api_cooldowns import ApiCooldowns
    from mrs_bot_daily_reply_accounting import DailyReplyAccounting
    from mrs_bot_reply_evaluation_state import ReplyEvaluations
    from mrs_bot_reply_context import ReplyContext
    from mrs_bot_reply_generation import ReplyGeneration
    from mrs_bot_reply_history import ReplyHistory
    from mrs_bot_quote_discovery import QuoteWatchPosts
    from mrs_bot_tweet_lookup_cache import TweetLookupCache
    from mrs_bot_runtime_control import RuntimeControls
    from single_call_reply import PipelineResult, ValidatedReply as ValidatedReplyValue


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


def mark_quote_spam_author(
    state: dict,
    author_id: str,
    *,
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
class _QuoteScanHistory:
    """Keep admission ledgers fixed while accumulating spam authors for one scan."""

    seen_quote_ids: frozenset[str]
    replied_quote_ids: frozenset[str]
    skipped_quote_ids: frozenset[str]
    spam_author_ids: set[str]
    replied_to_ids: frozenset[str]

    @classmethod
    def capture(cls, state: dict) -> _QuoteScanHistory:
        """Snapshot each ledger in admission order without retaining caller state."""

        return cls(
            seen_quote_ids=frozenset(str(x) for x in state.get("seen_quote_post_ids", [])),
            replied_quote_ids=frozenset(str(x) for x in state.get("replied_to_quote_post_ids", [])),
            skipped_quote_ids=frozenset(str(x) for x in state.get("skipped_quote_post_ids", [])),
            spam_author_ids=set(str(x) for x in state.get("quote_spam_author_ids", [])),
            replied_to_ids=frozenset(str(x) for x in state.get("replied_to_ids", [])),
        )


@dataclass(frozen=True)
class _QuoteCandidate:
    """Keep the candidate and its identity/text as first read during iteration."""

    tweet: dict
    quote_id: str
    author_id: str
    text: str



def maybe_reply_to_quote_tweets(
    state: dict,
    *,
    delivery: ReplyCycleDelivery,
    ApiError: type[Exception],
    ContextValidationError: type[Exception],
    config: QuoteReplyConfig,
    PipelineResult: type,
    RemoteOperationsPaused: type[Exception],
    ReplyEvidenceUnavailable: type[Exception],
    SINGLE_CALL_STRATEGY_VERSION: str,
    ValidatedReply: type,
    _log_validated_single_call_reply: Callable,
    generation: ReplyGeneration,
    api_error_is_permanent_target_failure: Callable,
    watch_posts: QuoteWatchPosts,
    reply_contexts: ReplyContext,
    tweets: TweetLookupCache,
    persistence: ReplyCyclePersistence,
    conversational_reply_pipeline_enabled: Callable,
    accounting: DailyReplyAccounting,
    get_quote_tweets_for_posts: QuoteTweetDiscovery,
    cooldowns: ApiCooldowns,
    is_probably_spam_or_not_worth_replying: Callable,
    controls: RuntimeControls,
    log: Logger,
    log_ai_reply_posting_outcome: Callable,
    log_event: Callable,
    now_epoch: Callable,
    parse_x_datetime_to_epoch: Callable,
    reply_evaluations: ReplyEvaluations,
    history: ReplyHistory,
    reply_evidence_repository: Callable,
    valid_tweets_sorted_by_id: Callable,
) -> str:
    """Process eligible quote-tweet candidates under all reply limits."""
    log.info("Starting quote-tweet reply check")
    # Finish an exact confirmed local transaction before the unresolved-
    # journal guard rejects every new remote lane.
    accounting.reset(state)
    accounting.reset_quotes(state)
    prior_reply_status, _prior_reply = delivery.load_receipt()
    if prior_reply_status == "valid" and delivery.reconcile_receipt(state):
        log.warning(
            "Reconciled confirmed reply receipt before checking new quote-tweet candidates"
        )
    delivery.block_ambiguous()

    if not config.quote_checks_enabled:
        log.info("Quote-tweet checks disabled")
        return QUOTE_CHECK_STATUS_DISABLED

    if not config.enabled:
        log.info("Auto replies disabled; skipping quote-tweet checks")
        return QUOTE_CHECK_STATUS_DISABLED

    if not conversational_reply_pipeline_enabled():
        log.info("Conversational reply pipeline disabled; skipping quote-tweet checks")
        return QUOTE_CHECK_STATUS_DISABLED

    if controls.lane_paused("disable_replies", "disable_quote_replies"):
        log.info("Skipping quote-tweet check due to runtime control file")
        return QUOTE_CHECK_STATUS_DISABLED

    if (
        cooldowns.active(state, scope="write")
        or cooldowns.active(state, scope="openai")
        or cooldowns.active(state, scope="quote")
    ):
        log.info("Skipping quote-tweet check due to API cooldown")
        return QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN

    if int(state.get("daily_reply_count", 0) or 0) >= config.maximum_daily_replies:
        log.info("Skipping quote-tweet check: total daily reply cap reached")
        return QUOTE_CHECK_STATUS_SKIPPED_CAP

    if int(state.get("daily_quote_reply_count", 0) or 0) >= config.maximum_daily_quote_replies:
        log.info("Skipping quote-tweet check: daily quote-reply cap reached")
        return QUOTE_CHECK_STATUS_SKIPPED_CAP

    current = now_epoch()
    seconds_since_last_reply = current - int(state.get("last_reply_epoch", 0) or 0)

    if seconds_since_last_reply < config.minimum_reply_spacing:
        log.info(
            "Skipping quote-tweet check: minimum interval between replies not reached. seconds_since=%s",
            seconds_since_last_reply,
        )
        return QUOTE_CHECK_STATUS_SKIPPED_SPACING

    own_post_ids_for_quote_lookup = watch_posts.lookup(state)

    if not own_post_ids_for_quote_lookup and not state.get("quote_pending_candidates"):
        log.info("No own posts available for quote lookup")
        return QUOTE_CHECK_STATUS_CHECKED

    scan_history = _QuoteScanHistory.capture(state)
    daily_author_reply_counts(state)

    try:
        quotes_by_post = get_quote_tweets_for_posts(own_post_ids_for_quote_lookup, state)
    except ApiError as exc:
        log.exception("Failed to search quote tweets for watched posts")
        cooldowns.record_error(state, exc, "x", scope="quote")
        persistence.save(state)
        return QUOTE_CHECK_STATUS_CHECKED
    except Exception:
        log.exception("Unexpected failure searching quote tweets for watched posts")
        persistence.save(state)
        return QUOTE_CHECK_STATUS_CHECKED

    # Only this counter spans originals; charge after context/media extraction,
    # even when evaluation makes no model call. Admission history stays fixed
    # except for newly discovered spam authors.
    processed_candidates = 0

    # Fetched work remains ours even after its original leaves the watched set.
    for original_post_id in dict.fromkeys([*own_post_ids_for_quote_lookup, *quotes_by_post]):
        if processed_candidates >= config.maximum_candidates:
            break
        quote_tweets = quotes_by_post.get(original_post_id, [])
        if not quote_tweets:
            continue

        lookup = _lookup_quote_candidates(
            original_post_id,
            state,
            quote_tweets,
            ApiError=ApiError,
            api_error_is_permanent_target_failure=api_error_is_permanent_target_failure,
            tweets=tweets,
            cooldowns=cooldowns,
            log=log,
            parse_x_datetime_to_epoch=parse_x_datetime_to_epoch,
            persistence=persistence,
        )
        if isinstance(lookup, FinishReplyCheck):
            return lookup.status
        if isinstance(lookup, SkipReplyCandidate):
            continue
        original_tweet, quote_tweets = lookup

        for quote_tweet in valid_tweets_sorted_by_id(quote_tweets, context="quote-tweet candidate"):
            if processed_candidates >= config.maximum_candidates:
                break
            if cooldowns.active(state, scope="openai"):
                log.info(
                    "Stopping quote-tweet candidate iteration because the "
                    "OpenAI cooldown became active"
                )
                persistence.save(state, durable=True)
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
                scan_history,
                config=config,
                log=log,
                now_epoch=now_epoch,
                parse_x_datetime_to_epoch=parse_x_datetime_to_epoch,
                persistence=persistence,
            ):
                continue
            if not _author_allows_evaluation(
                candidate,
                original_post_id,
                original_tweet,
                state,
                scan_history.spam_author_ids,
                config=config,
                tweets=tweets,
                accounting=accounting,
                is_probably_spam_or_not_worth_replying=is_probably_spam_or_not_worth_replying,
                log=log,
                log_event=log_event,
                persistence=persistence,
            ):
                continue

            prepared = _prepare_reply_context(
                candidate,
                original_post_id,
                state,
                ApiError=ApiError,
                ContextValidationError=ContextValidationError,
                PipelineResult=PipelineResult,
                generation=generation,
                api_error_is_permanent_target_failure=api_error_is_permanent_target_failure,
                reply_contexts=reply_contexts,
                tweets=tweets,
                log=log,
                cooldowns=cooldowns,
                reply_evaluations=reply_evaluations,
                persistence=persistence,
            )
            if isinstance(prepared, FinishReplyCheck):
                return prepared.status
            if isinstance(prepared, SkipReplyCandidate):
                continue
            reply_context = prepared.context

            processed_candidates += 1
            evaluation = _evaluate_reply(
                candidate,
                prepared,
                state,
                ApiError=ApiError,
                RemoteOperationsPaused=RemoteOperationsPaused,
                ReplyEvidenceUnavailable=ReplyEvidenceUnavailable,
                generation=generation,
                log=log,
                log_event=log_event,
                persistence=persistence,
                cooldowns=cooldowns,
                history=history,
                reply_evidence_repository=reply_evidence_repository,
            )
            if isinstance(evaluation, FinishReplyCheck):
                return evaluation.status
            reply_text = evaluation.reply
            decision = _resolve_reply_evaluation(
                candidate.quote_id,
                evaluation,
                state,
                ValidatedReply=ValidatedReply,
                log=log,
                reply_evaluations=reply_evaluations,
                persistence=persistence,
            )
            if isinstance(decision, FinishReplyCheck):
                return decision.status
            if isinstance(decision, SkipReplyCandidate):
                continue

            receipt_template = _prepare_reply_receipt(
                candidate,
                original_post_id,
                reply_text,
                reply_context,
                state,
                SINGLE_CALL_STRATEGY_VERSION=SINGLE_CALL_STRATEGY_VERSION,
                _log_validated_single_call_reply=_log_validated_single_call_reply,
                delivery=delivery,
                log=log,
                log_event=log_event,
                persistence=persistence,
            )
            if isinstance(receipt_template, FinishReplyCheck):
                return receipt_template.status
            receipt = _deliver_reply(
                candidate.quote_id,
                reply_text,
                receipt_template,
                state,
                config=config,
                persistence=persistence,
                log=log,
                log_ai_reply_posting_outcome=log_ai_reply_posting_outcome,
                log_event=log_event,
                delivery=delivery,
                reply_evaluations=reply_evaluations,
            )
            if isinstance(receipt, FinishReplyCheck):
                return receipt.status
            return _finalise_confirmed_reply(
                candidate,
                original_post_id,
                receipt,
                state,
                delivery=delivery,
                log=log,
                log_event=log_event,
            )

    persistence.save(state)
    log.info("Quote-tweet reply check finished with no reply generated/posted")
    return QUOTE_CHECK_STATUS_CHECKED


def _lookup_quote_candidates(
    original_post_id: str,
    state: dict,
    quote_tweets: list[dict],
    *,
    ApiError: type[Exception],
    api_error_is_permanent_target_failure: Callable,
    tweets: TweetLookupCache,
    cooldowns: ApiCooldowns,
    log: Logger,
    parse_x_datetime_to_epoch: Callable,
    persistence: ReplyCyclePersistence,
) -> tuple[dict, list] | SkipReplyCandidate | FinishReplyCheck:
    """Fetch context for an original with discovered quotes, preserving failure routing."""
    try:
        original_tweet = tweets.get_cached(original_post_id, state)
    except ApiError as e:
        if api_error_is_permanent_target_failure(e):
            log.info("Original own post %s is unavailable; skipping quote lookup", original_post_id)
            for quote in quote_tweets:
                mark_quote_tweet_skipped(state, str(quote["id"]))
            persistence.save(state, durable=True)
            return SkipReplyCandidate()
        log.exception("Failed to fetch original own post %s", original_post_id)
        cooldowns.record_error(state, e, "x", scope="quote")
        persistence.save(state)
        if cooldowns.active(state, scope="quote"):
            return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
        return SkipReplyCandidate()
    except Exception:
        log.exception("Unexpected failure fetching original own post %s", original_post_id)
        persistence.save(state)
        return SkipReplyCandidate()

    if not original_tweet:
        log.info("Could not find/fetch original own post %s", original_post_id)
        for quote in quote_tweets:
            mark_quote_tweet_skipped(state, str(quote["id"]))
        persistence.save(state, durable=True)
        return SkipReplyCandidate()

    # A frozen malformed timestamp must not block every subsequent search.
    # Refresh only affected queued targets; ordinary young quotes keep waiting.
    retained = []
    for quote in quote_tweets:
        quote_id = str(quote.get("id", ""))
        if (
            quote_id in state.get("quote_pending_candidates", {})
            and parse_x_datetime_to_epoch(quote.get("created_at")) is None
        ):
            try:
                fresh = tweets.get_cached(quote_id, state, include_media=True)
            except ApiError as exc:
                if not api_error_is_permanent_target_failure(exc):
                    cooldowns.record_error(state, exc, "x", scope="quote")
                    persistence.save(state, durable=True)
                    return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
                fresh = None
            except Exception:
                log.exception("Could not refresh queued quote timestamp target_id=%s", quote_id)
                persistence.save(state, durable=True)
                return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
            if fresh is not None and str(fresh.get("id", "")) != quote_id:
                raise ValueError("Queued quote refresh returned a different target")
            if fresh is None or parse_x_datetime_to_epoch(fresh.get("created_at")) is None:
                log.warning("Retiring queued quote with unavailable creation time target_id=%s", quote_id)
                mark_quote_tweet_skipped(state, quote_id)
                persistence.save(state, durable=True)
                continue
            quote.update(fresh)
            persistence.save(state, durable=True)
        retained.append(quote)
    return original_tweet, retained


def _candidate_is_eligible(
    candidate: _QuoteCandidate,
    original_post_id: str,
    state: dict,
    scan_history: _QuoteScanHistory,
    *,
    config: QuoteReplyConfig,
    log: Logger,
    now_epoch: Callable,
    parse_x_datetime_to_epoch: Callable,
    persistence: ReplyCyclePersistence,
) -> bool:
    """Apply identity, relationship and age gates against the cycle's original snapshots."""
    quote_tweet = candidate.tweet
    quote_id = candidate.quote_id
    author_id = candidate.author_id
    quote_text = candidate.text

    if not quote_id:
        return False

    if author_id in scan_history.spam_author_ids:
        log.info(
            "Skipping quote tweet %s: author_id=%s is in quote spam author list",
            quote_id,
            author_id,
        )
        mark_quote_tweet_skipped(state, quote_id)
        persistence.save(state)
        return False

    log.info(
        "Considering quote tweet id=%s author_id=%s original_post_id=%s text=%r",
        quote_id,
        author_id,
        original_post_id,
        quote_text,
    )

    if (
        quote_id in scan_history.seen_quote_ids
        or quote_id in scan_history.replied_quote_ids
        or quote_id in scan_history.skipped_quote_ids
    ):
        log.info("Skipping quote tweet %s: already seen/replied/skipped", quote_id)
        if remove_pending_quote_candidate(state, quote_id):
            persistence.save(state, durable=True)
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
        persistence.save(state)
        return False

    if not quote_tweet_directly_quotes_original(quote_tweet, original_post_id):
        log.info(
            "Skipping quote tweet %s: not a direct quote of original post %s. referenced_tweets=%s",
            quote_id,
            original_post_id,
            quote_tweet.get("referenced_tweets", []),
        )
        mark_quote_tweet_skipped(state, quote_id)
        persistence.save(state)
        return False

    if quote_id in scan_history.replied_to_ids:
        log.info("Skipping quote tweet %s: already handled by normal mention path", quote_id)
        mark_quote_tweet_skipped(state, quote_id)
        persistence.save(state)
        return False

    if author_id == str(config.user_id):
        log.info("Skipping quote tweet %s: authored by own account", quote_id)
        mark_quote_tweet_skipped(state, quote_id)
        persistence.save(state)
        return False

    if not quote_tweet_is_old_enough(
        quote_tweet,
        QUOTE_REPLY_DELAY_SECONDS=config.minimum_quote_age_seconds,
        log=log,
        now_epoch=now_epoch,
        parse_x_datetime_to_epoch=parse_x_datetime_to_epoch,
    ):
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
    config: QuoteReplyConfig,
    tweets: TweetLookupCache,
    accounting: DailyReplyAccounting,
    is_probably_spam_or_not_worth_replying: Callable,
    log: Logger,
    log_event: Callable,
    persistence: ReplyCyclePersistence,
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

    if accounting.author_count(state, author_id) >= config.maximum_daily_author_replies:
        log.info(
            "Skipping quote tweet %s: already reached per-author daily cap for author_id=%s",
            quote_id,
            author_id,
        )
        if quote_is_usable:
            tweets.store(
                state,
                tweet_id=str(original_tweet.get("id", original_post_id)),
                text=original_tweet.get("text", ""),
                author_id=str(original_tweet.get("author_id", config.user_id)),
                conversation_id=str(original_tweet.get("conversation_id", original_post_id)),
                referenced_tweets=original_tweet.get("referenced_tweets", []),
                created_at=original_tweet.get("created_at"),
                image_summary=original_tweet.get("image_summary"),
                post_type=original_tweet.get("post_type"),
            )
            tweets.store(
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
        persistence.save(state)
        return False

    if not cleaned_quote_text:
        log.info("Skipping quote tweet %s: no usable quote text after cleaning", quote_id)
        mark_quote_tweet_skipped(state, quote_id)
        log_event("quote_tweet_skipped", quote_tweet_id=quote_id, reason="no_usable_quote_text")
        persistence.save(state)
        return False

    if not quote_is_usable:
        log.info(
            "Skipping quote tweet %s: quote text/profile matched spam; marking author_id=%s as quote spam",
            quote_id,
            author_id,
        )
        log.debug("Quote spam check text for quote_id=%s: %r", quote_id, spam_check_text)
        mark_quote_tweet_skipped(state, quote_id)
        mark_quote_spam_author(state, author_id, log=log)
        quote_spam_author_ids.add(author_id)
        persistence.save(state)
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
    generation: ReplyGeneration,
    api_error_is_permanent_target_failure: Callable,
    reply_contexts: ReplyContext,
    tweets: TweetLookupCache,
    log: Logger,
    cooldowns: ApiCooldowns,
    reply_evaluations: ReplyEvaluations,
    persistence: ReplyCyclePersistence,
) -> PreparedReplyContext | SkipReplyCandidate | FinishReplyCheck:
    """Refetch media, cache the quote and build context before charging its candidate budget."""
    quote_tweet = candidate.tweet
    quote_id = candidate.quote_id
    author_id = candidate.author_id
    quote_text = candidate.text

    def retire_context_failure(reason: str, validation_status: str) -> SkipReplyCandidate:
        """Record the context failure and durably retire this candidate."""
        generation.record_result(
            PipelineResult(
                status="operational_failure",
                reason=reason,
                error_category="context_validation",
                local_validation_status=validation_status,
            ),
            lane="quote_tweet",
            target_id=quote_id,
        )
        reply_evaluations.record(
            state,
            target_id=quote_id,
            lane="quote_tweet",
            reason=reason,
            outcome="operational_failure",
        )
        mark_quote_tweet_skipped(state, quote_id)
        persistence.save(state, durable=True)
        return SkipReplyCandidate()

    try:
        original_context_tweet = tweets.get_cached(
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
            return retire_context_failure(
                "quoted_post_context_unavailable", "not_run",
            )
        log.exception(
            "Could not collect directly quoted post context for quote "
            "tweet %s",
            quote_id,
        )
        cooldowns.record_error(state, exc, "x", scope="quote")
        persistence.save(state)
        return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
    if original_context_tweet is None:
        log.warning(
            "Retiring quote tweet %s because its directly quoted "
            "post could not be collected",
            quote_id,
        )
        return retire_context_failure(
            "quoted_post_context_unavailable", "not_run",
        )

    tweets.store(
        state,
        tweet_id=quote_id,
        text=quote_text,
        author_id=author_id,
        conversation_id=str(quote_tweet.get("conversation_id", quote_id)),
        referenced_tweets=quote_tweet.get("referenced_tweets", []),
        created_at=quote_tweet.get("created_at"),
        post_type="quote_tweet",
    )
    persistence.save(state)

    try:
        prepared = reply_contexts.build_quote(
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
        return retire_context_failure(
            "canonical_context_unavailable", "failed",
        )
    return prepared


def _evaluate_reply(
    candidate: _QuoteCandidate,
    prepared: PreparedReplyContext,
    state: dict,
    *,
    ApiError: type[Exception],
    RemoteOperationsPaused: type[Exception],
    ReplyEvidenceUnavailable: type[Exception],
    generation: ReplyGeneration,
    log: Logger,
    log_event: Callable,
    persistence: ReplyCyclePersistence,
    cooldowns: ApiCooldowns,
    history: ReplyHistory,
    reply_evidence_repository: Callable,
) -> PipelineResult | FinishReplyCheck:
    """Check evidence and recover or generate a draft with the original exception boundaries."""
    reply_context = prepared.context
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
        return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)

    evaluation = persistence.recover(
        state,
        quote_id,
        "quote_tweet",
        context=reply_context,
        recent_replies=history.recovery_replies(
            state,
            context=reply_context,
        ),
    )
    try:
        if evaluation is None or evaluation.status == "draft_discarded":
            evaluation = generation.evaluate(
                reply_context,
                prepared.media_context,
                state=state,
            )
        elif evaluation.reply is not None:
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
        persistence.save(state, durable=True)
        return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
    except ApiError as exc:
        log.exception("OpenAI single-call quote-tweet reply failed")
        if exc.service == "openai":
            cooldowns.record_error(state, exc, "openai")
        persistence.save(state)
        return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
    except Exception:
        log.exception("Unexpected single-call quote-tweet reply failure")
        persistence.save(state)
        return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
    return evaluation


def _resolve_reply_evaluation(
    quote_id: str,
    evaluation: PipelineResult,
    state: dict,
    *,
    ValidatedReply: type,
    log: Logger,
    reply_evaluations: ReplyEvaluations,
    persistence: ReplyCyclePersistence,
) -> SkipReplyCandidate | FinishReplyCheck | None:
    """Retire terminal decisions, defer retryable failures, or allow a validated reply through."""
    reply_text = evaluation.reply
    if not reply_text:
        if _is_terminal_candidate_local_failure(evaluation):
            failure_category = str(evaluation.error_category)
            failure_reason = str(
                evaluation.reason or failure_category
            )
            log.warning(
                "Retiring quote tweet %s after permanent "
                "candidate-local reply failure category=%s reason=%s",
                quote_id,
                failure_category,
                failure_reason,
            )
            reply_evaluations.record(
                state,
                target_id=quote_id,
                lane="quote_tweet",
                reason=failure_reason,
                outcome="operational_failure",
            )
            mark_quote_tweet_skipped(state, quote_id)
            persistence.save(state, durable=True)
            return SkipReplyCandidate()
        if evaluation.status != "no_reply":
            log.warning(
                "Deferring quote tweet %s after operational reply "
                "failure reason=%s",
                quote_id,
                evaluation.reason or "unknown",
            )
            persistence.save(state, durable=True)
            return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
        reason_code = str(
            evaluation.reason_code
            or evaluation.reason
            or "model_selected_no_reply"
        )
        reply_evaluations.record(
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
        persistence.save(state, durable=True)
        return SkipReplyCandidate()
    if not isinstance(reply_text, ValidatedReply):
        log.error(
            "Single-call pipeline returned an unvalidated quote-tweet "
            "reply; deferring target_id=%s",
            quote_id,
        )
        persistence.save(state, durable=True)
        return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
    return None


def _prepare_reply_receipt(
    candidate: _QuoteCandidate,
    original_post_id: str,
    reply_text: ValidatedReplyValue,
    reply_context: dict,
    state: dict,
    *,
    SINGLE_CALL_STRATEGY_VERSION: str,
    _log_validated_single_call_reply: Callable,
    delivery: ReplyCycleDelivery,
    log: Logger,
    log_event: Callable,
    persistence: ReplyCyclePersistence,
) -> dict | FinishReplyCheck:
    """Persist the validated draft before copying and binding its sending receipt."""
    quote_tweet = candidate.tweet
    quote_id = candidate.quote_id
    author_id = candidate.author_id

    _log_validated_single_call_reply(
        target_description="quote tweet",
        target_id=quote_id,
        reply=reply_text,
    )

    def log_validation_failure() -> None:
        """Retain the quote lane's persistence-validation log format."""
        log.error(
            "Single-call reply draft failed persistence validation; "
            "deferring target_id=%s source=quote_tweet",
            quote_id,
        )

    if not persist_validated_reply_draft(
        state, quote_id, "quote_tweet", reply_text, reply_context,
        SINGLE_CALL_STRATEGY_VERSION=SINGLE_CALL_STRATEGY_VERSION,
        persistence=persistence,
        log_validation_failure=log_validation_failure,
        log_event=log_event,
    ):
        return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
    receipt_template = build_sending_reply_receipt(
        {
            "target_id": quote_id,
            "author_id": author_id,
            "candidate_source": "quote_tweet",
            "conversation_id": str(
                quote_tweet.get("conversation_id", quote_id)
            ),
            "reply_text": reply_text,
            "original_post_id": str(original_post_id),
        },
        reply_context,
    )
    receipt_template = delivery.bind_attempt(
        receipt_template
    )
    return receipt_template


def _retire_terminal_target(
    state: dict,
    quote_id: str,
    reply_text: ValidatedReplyValue,
    *,
    failure_reason: str,
    reason: str,
    persistence: ReplyCyclePersistence,
    log_ai_reply_posting_outcome: Callable,
    log_event: Callable,
    reply_evaluations: ReplyEvaluations,
) -> None:
    """Record a terminal quote outcome and durably skip its target and draft."""
    log_ai_reply_posting_outcome(
        reply=reply_text,
        status="posting_failed_terminal",
        lane="quote_tweet",
        target_id=quote_id,
        failure_reason=failure_reason,
    )
    log_event(
        "reply_target_terminal",
        lane="quote_tweet",
        target_id=quote_id,
        outcome="reply_not_permitted",
        reason=reason,
    )
    reply_evaluations.record(
        state,
        target_id=quote_id,
        lane="quote_tweet",
        reason=reason,
        outcome="reply_not_permitted",
    )
    mark_quote_tweet_skipped(state, quote_id)
    persistence.clear(state, quote_id, "quote_tweet")
    persistence.save(state, durable=True)


def _deliver_reply(
    quote_id: str,
    reply_text: ValidatedReplyValue,
    receipt_template: dict,
    state: dict,
    *,
    config: QuoteReplyConfig,
    persistence: ReplyCyclePersistence,
    log: Logger,
    log_ai_reply_posting_outcome: Callable,
    log_event: Callable,
    delivery: ReplyCycleDelivery,
    reply_evaluations: ReplyEvaluations,
) -> dict | FinishReplyCheck:
    """Deliver through the shared boundary, retaining quote-lane retirement and statuses."""
    def retire_terminal_target(failure_reason: str) -> None:
        if failure_reason == "target_unavailable_pre_send":
            log.warning(
                "Cannot reply to quote tweet %s because it disappeared "
                "after evaluation; marking it skipped without consuming "
                "reply quota",
                quote_id,
            )
        else:
            log.warning(
                "Cannot reply to quote tweet %s because X says replies are not allowed; "
                "marking quote tweet as skipped without consuming reply quota",
                quote_id,
            )
        _retire_terminal_target(
            state, quote_id, reply_text,
            failure_reason=failure_reason,
            reason=f"x_{failure_reason}",
            persistence=persistence,
            log_ai_reply_posting_outcome=log_ai_reply_posting_outcome,
            log_event=log_event,
            reply_evaluations=reply_evaluations,
        )

    outcome = delivery.deliver(
        state, quote_id, reply_text, receipt_template,
        lane="quote_tweet", log_source="quote-tweet", mark_as_ai=config.mark_as_ai,
        read_error_scope="quote",
        retire_terminal_target=retire_terminal_target,
    )
    if isinstance(outcome, ReplyDeliveryStop):
        return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
    return outcome


def _finalise_confirmed_reply(
    candidate: _QuoteCandidate,
    original_post_id: str,
    receipt: dict,
    state: dict,
    *,
    log: Logger,
    log_event: Callable,
    delivery: ReplyCycleDelivery,
) -> str:
    """Finish the shared confirmation transaction and report this lane's success."""
    quote_id = candidate.quote_id
    author_id = candidate.author_id
    own_reply_id = delivery.finalise(
        state, receipt, target_id=quote_id,
        quote_reply=True,
    )

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
