"""Own quote-tweet reply orchestration and lane-specific policy.

The runner holds stable collaborators for one check while state, scan history
and candidate progress remain local to ``run``. Imports perform no runtime I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from typing import TYPE_CHECKING, Any

from mrs_bot_runtime_state_helpers import append_unique_capped
from mrs_bot_reply_state import (
    mark_quote_tweet_replied,
    mark_quote_tweet_skipped,
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
    FinishReplyCheck,
    LogReplyEvent,
    LogReplyPostingOutcome,
    LogValidatedReply,
    PreparedReplyContext,
    QuoteReplyConfig,
    QuoteTweetDiscovery,
    ReplyCycleDelivery,
    ReplyCyclePersistence,
    SkipReplyCandidate,
    SortRawTweets,
)
from mrs_bot_reply_delivery import ReplyDeliveryStop
from mrs_bot_reply_outcomes import outcome_disposition
from mrs_bot_reply_evaluation_state import terminal_reply_evaluation
from mrs_bot_reply_preparation import (
    build_sending_reply_receipt,
    persist_validated_reply_draft,
)

if TYPE_CHECKING:
    from mrs_bot_core_contracts import BotState
    from mrs_bot_core_contracts import ConfirmedReplyReceipt, ReplyContextData
    from mrsMThatcher2 import ApiError as ApiErrorValue
    from mrs_bot_api_cooldowns import ApiCooldowns
    from mrs_bot_daily_reply_accounting import DailyReplyAccounting
    from mrs_bot_reply_evaluation_state import ReplyEvaluations
    from mrs_bot_reply_context import ReplyContext
    from mrs_bot_reply_generation import ReplyGeneration
    from mrs_bot_reply_history import ReplyHistory
    from mrs_bot_quote_discovery import QuoteWatchPosts
    from mrs_bot_tweet_lookup_cache import TweetLookupCache
    from mrs_bot_runtime_control import RuntimeControls
    from single_call_reply import PipelineOutcome, PipelineResult, ValidatedReply as ValidatedReplyValue


def quote_tweet_is_old_enough(
    quote_tweet: dict[str, Any],
    *,
    QUOTE_REPLY_DELAY_SECONDS: int,
    log: Logger,
    now_epoch: Callable[[], int],
    parse_x_datetime_to_epoch: Callable[[object], int | None],
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
    quote_tweet: dict[str, Any],
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


def quote_author_profile_text(quote_tweet: dict[str, Any]) -> str:
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
    state: BotState,
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
    log.info(
        "Marked author_id=%s as quote spam author. spam_author_count=%d",
        author_id,
        len(state["quote_spam_author_ids"]),
    )


@dataclass(frozen=True)
class _QuoteScanHistory:
    """Keep admission ledgers fixed while accumulating spam authors for one scan."""

    seen_quote_ids: frozenset[str]
    replied_quote_ids: frozenset[str]
    skipped_quote_ids: frozenset[str]
    spam_author_ids: set[str]
    replied_to_ids: frozenset[str]

    @classmethod
    def capture(cls, state: BotState) -> _QuoteScanHistory:
        """Snapshot each ledger in admission order without retaining caller state."""

        return cls(
            seen_quote_ids=frozenset(
                str(x) for x in state.get("seen_quote_post_ids", [])
            ),
            replied_quote_ids=frozenset(
                str(x) for x in state.get("replied_to_quote_post_ids", [])
            ),
            skipped_quote_ids=frozenset(
                str(x) for x in state.get("skipped_quote_post_ids", [])
            ),
            spam_author_ids=set(str(x) for x in state.get("quote_spam_author_ids", [])),
            replied_to_ids=frozenset(str(x) for x in state.get("replied_to_ids", [])),
        )


@dataclass(frozen=True)
class _QuoteCandidate:
    """Keep the candidate and its identity/text as first read during iteration."""

    tweet: dict[str, Any]
    quote_id: str
    author_id: str
    text: str


class QuoteReplyCycle:
    """Run one reply-lane pass with construction-time collaborators."""

    def __init__(
        self,
        *,
        delivery: ReplyCycleDelivery,
        ApiError: type[ApiErrorValue],
        ContextValidationError: type[Exception],
        config: QuoteReplyConfig,
        PipelineResult: type[PipelineResult],
        RemoteOperationsPaused: type[Exception],
        ReplyEvidenceUnavailable: type[Exception],
        SINGLE_CALL_STRATEGY_VERSION: str,
        ValidatedReply: type[ValidatedReplyValue],
        _log_validated_single_call_reply: LogValidatedReply,
        generation: ReplyGeneration,
        api_error_is_permanent_target_failure: Callable[[Exception], bool],
        watch_posts: QuoteWatchPosts,
        reply_contexts: ReplyContext,
        tweets: TweetLookupCache,
        persistence: ReplyCyclePersistence,
        conversational_reply_pipeline_enabled: Callable[[], bool],
        accounting: DailyReplyAccounting,
        get_quote_tweets_for_posts: QuoteTweetDiscovery,
        cooldowns: ApiCooldowns,
        is_probably_spam_or_not_worth_replying: Callable[[str], bool],
        controls: RuntimeControls,
        log: Logger,
        log_ai_reply_posting_outcome: LogReplyPostingOutcome,
        log_event: LogReplyEvent,
        now_epoch: Callable[[], int],
        parse_x_datetime_to_epoch: Callable[[object], int | None],
        reply_evaluations: ReplyEvaluations,
        history: ReplyHistory,
        reply_evidence_repository: Callable[[], object],
        valid_tweets_sorted_by_id: SortRawTweets,
    ) -> None:
        """Bind stable collaborators for one quote reply pass."""
        self.delivery = delivery
        self.ApiError = ApiError
        self.ContextValidationError = ContextValidationError
        self.config = config
        self.PipelineResult = PipelineResult
        self.RemoteOperationsPaused = RemoteOperationsPaused
        self.ReplyEvidenceUnavailable = ReplyEvidenceUnavailable
        self.SINGLE_CALL_STRATEGY_VERSION = SINGLE_CALL_STRATEGY_VERSION
        self.ValidatedReply = ValidatedReply
        self._log_validated_single_call_reply = _log_validated_single_call_reply
        self.generation = generation
        self.api_error_is_permanent_target_failure = (
            api_error_is_permanent_target_failure
        )
        self.watch_posts = watch_posts
        self.reply_contexts = reply_contexts
        self.tweets = tweets
        self.persistence = persistence
        self.conversational_reply_pipeline_enabled = (
            conversational_reply_pipeline_enabled
        )
        self.accounting = accounting
        self.get_quote_tweets_for_posts = get_quote_tweets_for_posts
        self.cooldowns = cooldowns
        self.is_probably_spam_or_not_worth_replying = (
            is_probably_spam_or_not_worth_replying
        )
        self.controls = controls
        self.log = log
        self.log_ai_reply_posting_outcome = log_ai_reply_posting_outcome
        self.log_event = log_event
        self.now_epoch = now_epoch
        self.parse_x_datetime_to_epoch = parse_x_datetime_to_epoch
        self.reply_evaluations = reply_evaluations
        self.history = history
        self.reply_evidence_repository = reply_evidence_repository
        self.valid_tweets_sorted_by_id = valid_tweets_sorted_by_id

    def run(self, state: BotState) -> str:
        """Process eligible quote-tweet candidates under all reply limits."""
        self.log.info("Starting quote-tweet reply check")
        # Finish an exact confirmed local transaction before the unresolved-
        # journal guard rejects every new remote lane.
        self.accounting.reset(state)
        self.accounting.reset_quotes(state)
        prior_reply_status, _prior_reply = self.delivery.load_receipt()
        if prior_reply_status == "valid" and self.delivery.reconcile_receipt(state):
            self.log.warning(
                "Reconciled confirmed reply receipt before checking new quote-tweet candidates"
            )
        self.delivery.block_ambiguous()

        if not self.config.quote_checks_enabled:
            self.log.info("Quote-tweet checks disabled")
            return QUOTE_CHECK_STATUS_DISABLED

        if not self.config.enabled:
            self.log.info("Auto replies disabled; skipping quote-tweet checks")
            return QUOTE_CHECK_STATUS_DISABLED

        if not self.conversational_reply_pipeline_enabled():
            self.log.info(
                "Conversational reply pipeline disabled; skipping quote-tweet checks"
            )
            return QUOTE_CHECK_STATUS_DISABLED

        if self.controls.lane_paused("disable_replies", "disable_quote_replies"):
            self.log.info("Skipping quote-tweet check due to runtime control file")
            return QUOTE_CHECK_STATUS_DISABLED

        if (
            self.cooldowns.active(state, scope="write")
            or self.cooldowns.active(state, scope="openai")
            or self.cooldowns.active(state, scope="quote")
        ):
            self.log.info("Skipping quote-tweet check due to API cooldown")
            return QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN

        if (
            int(state.get("daily_reply_count", 0) or 0)
            >= self.config.maximum_daily_replies
        ):
            self.log.info("Skipping quote-tweet check: total daily reply cap reached")
            return QUOTE_CHECK_STATUS_SKIPPED_CAP

        if (
            int(state.get("daily_quote_reply_count", 0) or 0)
            >= self.config.maximum_daily_quote_replies
        ):
            self.log.info("Skipping quote-tweet check: daily quote-reply cap reached")
            return QUOTE_CHECK_STATUS_SKIPPED_CAP

        current = self.now_epoch()
        seconds_since_last_reply = current - int(state.get("last_reply_epoch", 0) or 0)

        if seconds_since_last_reply < self.config.minimum_reply_spacing:
            self.log.info(
                "Skipping quote-tweet check: minimum interval between replies not reached. seconds_since=%s",
                seconds_since_last_reply,
            )
            return QUOTE_CHECK_STATUS_SKIPPED_SPACING

        own_post_ids_for_quote_lookup = self.watch_posts.lookup(state)

        if not own_post_ids_for_quote_lookup and not state.get(
            "quote_pending_candidates"
        ):
            self.log.info("No own posts available for quote lookup")
            return QUOTE_CHECK_STATUS_CHECKED

        scan_history = _QuoteScanHistory.capture(state)
        daily_author_reply_counts(state)

        try:
            quotes_by_post = self.get_quote_tweets_for_posts(
                own_post_ids_for_quote_lookup, state
            )
        except self.ApiError as exc:
            self.log.exception("Failed to search quote tweets for watched posts")
            self.cooldowns.record_error(state, exc, "x", scope="quote")
            self.persistence.save(state)
            return QUOTE_CHECK_STATUS_CHECKED
        except Exception:
            self.log.exception(
                "Unexpected failure searching quote tweets for watched posts"
            )
            self.persistence.save(state)
            return QUOTE_CHECK_STATUS_CHECKED

        # Only this counter spans originals; charge after context/media extraction,
        # even when evaluation makes no model call. Admission history stays fixed
        # except for newly discovered spam authors.
        processed_candidates = 0

        # Fetched work remains ours even after its original leaves the watched set.
        for original_post_id in dict.fromkeys(
            [*own_post_ids_for_quote_lookup, *quotes_by_post]
        ):
            if processed_candidates >= self.config.maximum_candidates:
                break
            quote_tweets = quotes_by_post.get(original_post_id, [])
            if not quote_tweets:
                continue

            lookup = self._lookup_quote_candidates(
                original_post_id, state, quote_tweets
            )
            if isinstance(lookup, FinishReplyCheck):
                return lookup.status
            if isinstance(lookup, SkipReplyCandidate):
                continue
            original_tweet, quote_tweets = lookup

            for quote_tweet in self.valid_tweets_sorted_by_id(
                quote_tweets, context="quote-tweet candidate"
            ):
                if processed_candidates >= self.config.maximum_candidates:
                    break
                if self.cooldowns.active(state, scope="openai"):
                    self.log.info(
                        "Stopping quote-tweet candidate iteration because the "
                        "OpenAI cooldown became active"
                    )
                    self.persistence.save(state, durable=True)
                    return QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN

                candidate = _QuoteCandidate(
                    quote_tweet,
                    str(quote_tweet.get("id", "")),
                    str(quote_tweet.get("author_id", "")),
                    quote_tweet.get("text", ""),
                )
                if not self._candidate_is_eligible(
                    candidate, original_post_id, state, scan_history
                ):
                    continue
                if not self._author_allows_evaluation(
                    candidate,
                    original_post_id,
                    original_tweet,
                    state,
                    scan_history.spam_author_ids,
                ):
                    continue

                prepared = self._prepare_reply_context(
                    candidate, original_post_id, state
                )
                if isinstance(prepared, FinishReplyCheck):
                    return prepared.status
                if isinstance(prepared, SkipReplyCandidate):
                    continue
                reply_context = prepared.context

                processed_candidates += 1
                evaluation = self._evaluate_reply(candidate, prepared, state)
                if isinstance(evaluation, FinishReplyCheck):
                    return evaluation.status
                decision = self._resolve_reply_evaluation(
                    candidate.quote_id, evaluation, state
                )
                if isinstance(decision, FinishReplyCheck):
                    return decision.status
                if isinstance(decision, SkipReplyCandidate):
                    continue

                disposition = outcome_disposition(evaluation)
                if disposition != "ready" or evaluation.status != "reply":
                    raise ValueError("reply evaluation passed resolution without a reply")
                reply_text = evaluation.reply

                receipt_template = self._prepare_reply_receipt(
                    candidate, original_post_id, reply_text, reply_context, state
                )
                if isinstance(receipt_template, FinishReplyCheck):
                    return receipt_template.status
                receipt = self._deliver_reply(
                    candidate.quote_id, reply_text, receipt_template, state
                )
                if isinstance(receipt, FinishReplyCheck):
                    return receipt.status
                return self._finalise_confirmed_reply(
                    candidate, original_post_id, receipt, state
                )

        self.persistence.save(state)
        self.log.info("Quote-tweet reply check finished with no reply generated/posted")
        return QUOTE_CHECK_STATUS_CHECKED

    def _lookup_quote_candidates(
        self, original_post_id: str, state: BotState, quote_tweets: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | SkipReplyCandidate | FinishReplyCheck:
        """Fetch context for an original with discovered quotes, preserving failure routing."""
        try:
            original_tweet = self.tweets.get_cached(original_post_id, state)
        except self.ApiError as e:
            if self.api_error_is_permanent_target_failure(e):
                self.log.info(
                    "Original own post %s is unavailable; skipping quote lookup",
                    original_post_id,
                )
                for quote in quote_tweets:
                    mark_quote_tweet_skipped(state, str(quote["id"]))
                self.persistence.save(state, durable=True)
                return SkipReplyCandidate()
            self.log.exception("Failed to fetch original own post %s", original_post_id)
            self.cooldowns.record_error(state, e, "x", scope="quote")
            self.persistence.save(state)
            if self.cooldowns.active(state, scope="quote"):
                return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
            return SkipReplyCandidate()
        except Exception:
            self.log.exception(
                "Unexpected failure fetching original own post %s", original_post_id
            )
            self.persistence.save(state)
            return SkipReplyCandidate()

        if not original_tweet:
            self.log.info("Could not find/fetch original own post %s", original_post_id)
            for quote in quote_tweets:
                mark_quote_tweet_skipped(state, str(quote["id"]))
            self.persistence.save(state, durable=True)
            return SkipReplyCandidate()

        # A frozen malformed timestamp must not block every subsequent search.
        # Refresh only affected queued targets; ordinary young quotes keep waiting.
        retained = []
        for quote in quote_tweets:
            quote_id = str(quote.get("id", ""))
            if (
                quote_id in state.get("quote_pending_candidates", {})
                and self.parse_x_datetime_to_epoch(quote.get("created_at")) is None
            ):
                try:
                    fresh = self.tweets.get_cached(quote_id, state, include_media=True)
                except self.ApiError as exc:
                    if not self.api_error_is_permanent_target_failure(exc):
                        self.cooldowns.record_error(state, exc, "x", scope="quote")
                        self.persistence.save(state, durable=True)
                        return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
                    fresh = None
                except Exception:
                    self.log.exception(
                        "Could not refresh queued quote timestamp target_id=%s",
                        quote_id,
                    )
                    self.persistence.save(state, durable=True)
                    return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
                if fresh is not None and str(fresh.get("id", "")) != quote_id:
                    raise ValueError("Queued quote refresh returned a different target")
                if (
                    fresh is None
                    or self.parse_x_datetime_to_epoch(fresh.get("created_at")) is None
                ):
                    self.log.warning(
                        "Retiring queued quote with unavailable creation time target_id=%s",
                        quote_id,
                    )
                    mark_quote_tweet_skipped(state, quote_id)
                    self.persistence.save(state, durable=True)
                    continue
                quote.update(fresh)
                self.persistence.save(state, durable=True)
            retained.append(quote)
        return original_tweet, retained

    def _candidate_is_eligible(
        self,
        candidate: _QuoteCandidate,
        original_post_id: str,
        state: BotState,
        scan_history: _QuoteScanHistory,
    ) -> bool:
        """Apply identity, relationship and age gates against the cycle's original snapshots."""
        quote_tweet = candidate.tweet
        quote_id = candidate.quote_id
        author_id = candidate.author_id
        quote_text = candidate.text

        if not quote_id:
            return False

        if author_id in scan_history.spam_author_ids:
            self.log.info(
                "Skipping quote tweet %s: author_id=%s is in quote spam author list",
                quote_id,
                author_id,
            )
            mark_quote_tweet_skipped(state, quote_id)
            self.persistence.save(state)
            return False

        self.log.info(
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
            self.log.info(
                "Skipping quote tweet %s: already seen/replied/skipped", quote_id
            )
            if remove_pending_quote_candidate(state, quote_id):
                self.persistence.save(state, durable=True)
            return False

        prior_evaluation = terminal_reply_evaluation(state, quote_id)
        if prior_evaluation is not None:
            self.log.info(
                "Skipping quote tweet %s: terminal %s evaluation already recorded reason=%s",
                quote_id,
                prior_evaluation.get("outcome", "no_reply"),
                prior_evaluation.get("reason", ""),
            )
            mark_quote_tweet_skipped(state, quote_id)
            self.persistence.save(state)
            return False

        if not quote_tweet_directly_quotes_original(quote_tweet, original_post_id):
            self.log.info(
                "Skipping quote tweet %s: not a direct quote of original post %s. referenced_tweets=%s",
                quote_id,
                original_post_id,
                quote_tweet.get("referenced_tweets", []),
            )
            mark_quote_tweet_skipped(state, quote_id)
            self.persistence.save(state)
            return False

        if quote_id in scan_history.replied_to_ids:
            self.log.info(
                "Skipping quote tweet %s: already handled by normal mention path",
                quote_id,
            )
            mark_quote_tweet_skipped(state, quote_id)
            self.persistence.save(state)
            return False

        if author_id == str(self.config.user_id):
            self.log.info("Skipping quote tweet %s: authored by own account", quote_id)
            mark_quote_tweet_skipped(state, quote_id)
            self.persistence.save(state)
            return False

        if not quote_tweet_is_old_enough(
            quote_tweet,
            QUOTE_REPLY_DELAY_SECONDS=self.config.minimum_quote_age_seconds,
            log=self.log,
            now_epoch=self.now_epoch,
            parse_x_datetime_to_epoch=self.parse_x_datetime_to_epoch,
        ):
            self.log.info(
                "Quote tweet %s is too recent; leaving unmarked so it can be checked later",
                quote_id,
            )
            return False
        return True

    def _author_allows_evaluation(
        self,
        candidate: _QuoteCandidate,
        original_post_id: str,
        original_tweet: dict[str, Any],
        state: BotState,
        quote_spam_author_ids: set[str],
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
        quote_is_usable = bool(
            cleaned_quote_text
        ) and not self.is_probably_spam_or_not_worth_replying(spam_check_text)

        if (
            self.accounting.author_count(state, author_id)
            >= self.config.maximum_daily_author_replies
        ):
            self.log.info(
                "Skipping quote tweet %s: already reached per-author daily cap for author_id=%s",
                quote_id,
                author_id,
            )
            if quote_is_usable:
                self.tweets.store(
                    state,
                    tweet_id=str(original_tweet.get("id", original_post_id)),
                    text=original_tweet.get("text", ""),
                    author_id=str(original_tweet.get("author_id", self.config.user_id)),
                    conversation_id=str(
                        original_tweet.get("conversation_id", original_post_id)
                    ),
                    referenced_tweets=original_tweet.get("referenced_tweets", []),
                    created_at=original_tweet.get("created_at"),
                    image_summary=original_tweet.get("image_summary"),
                    post_type=original_tweet.get("post_type"),
                )
                self.tweets.store(
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
            self.persistence.save(state)
            return False

        if not cleaned_quote_text:
            self.log.info(
                "Skipping quote tweet %s: no usable quote text after cleaning", quote_id
            )
            mark_quote_tweet_skipped(state, quote_id)
            self.log_event(
                "quote_tweet_skipped",
                quote_tweet_id=quote_id,
                reason="no_usable_quote_text",
            )
            self.persistence.save(state)
            return False

        if not quote_is_usable:
            self.log.info(
                "Skipping quote tweet %s: quote text/profile matched spam; marking author_id=%s as quote spam",
                quote_id,
                author_id,
            )
            self.log.debug(
                "Quote spam check text for quote_id=%s: %r", quote_id, spam_check_text
            )
            mark_quote_tweet_skipped(state, quote_id)
            mark_quote_spam_author(state, author_id, log=self.log)
            quote_spam_author_ids.add(author_id)
            self.persistence.save(state)
            return False
        return True

    def _prepare_reply_context(
        self, candidate: _QuoteCandidate, original_post_id: str, state: BotState
    ) -> PreparedReplyContext | SkipReplyCandidate | FinishReplyCheck:
        """Refetch media, cache the quote and build context before charging its candidate budget."""
        quote_tweet = candidate.tweet
        quote_id = candidate.quote_id
        author_id = candidate.author_id
        quote_text = candidate.text

        def retire_context_failure(
            reason: str, validation_status: str
        ) -> SkipReplyCandidate:
            """Record the context failure and durably retire this candidate."""
            self.generation.record_result(
                self.PipelineResult(
                    status="operational_failure",
                    reason=reason,
                    error_category="context_validation",
                    local_validation_status=validation_status,
                ),
                lane="quote_tweet",
                target_id=quote_id,
            )
            self.reply_evaluations.record(
                state,
                target_id=quote_id,
                lane="quote_tweet",
                reason=reason,
                outcome="operational_failure",
            )
            mark_quote_tweet_skipped(state, quote_id)
            self.persistence.save(state, durable=True)
            return SkipReplyCandidate()

        try:
            original_context_tweet = self.tweets.get_cached(
                original_post_id,
                state,
                include_media=True,
            )
        except self.ApiError as exc:
            if self.api_error_is_permanent_target_failure(exc):
                self.log.warning(
                    "Retiring quote tweet %s because its directly quoted "
                    "post is permanently unavailable",
                    quote_id,
                )
                return retire_context_failure(
                    "quoted_post_context_unavailable",
                    "not_run",
                )
            self.log.exception(
                "Could not collect directly quoted post context for quote tweet %s",
                quote_id,
            )
            self.cooldowns.record_error(state, exc, "x", scope="quote")
            self.persistence.save(state)
            return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
        if original_context_tweet is None:
            self.log.warning(
                "Retiring quote tweet %s because its directly quoted "
                "post could not be collected",
                quote_id,
            )
            return retire_context_failure(
                "quoted_post_context_unavailable",
                "not_run",
            )

        self.tweets.store(
            state,
            tweet_id=quote_id,
            text=quote_text,
            author_id=author_id,
            conversation_id=str(quote_tweet.get("conversation_id", quote_id)),
            referenced_tweets=quote_tweet.get("referenced_tweets", []),
            created_at=quote_tweet.get("created_at"),
            post_type="quote_tweet",
        )
        self.persistence.save(state)

        try:
            prepared = self.reply_contexts.build_quote(
                original_context_tweet,
                quote_tweet,
            )
        except (
            self.ContextValidationError,
            TypeError,
            ValueError,
            UnicodeError,
        ) as exc:
            self.log.warning(
                "Retiring quote tweet %s after permanent canonical-context "
                "validation failure: %s",
                quote_id,
                exc,
            )
            return retire_context_failure(
                "canonical_context_unavailable",
                "failed",
            )
        return prepared

    def _evaluate_reply(
        self, candidate: _QuoteCandidate, prepared: PreparedReplyContext, state: BotState
    ) -> PipelineOutcome | FinishReplyCheck:
        """Check evidence and recover or generate a draft with the original exception boundaries."""
        reply_context = prepared.context
        quote_id = candidate.quote_id

        try:
            self.reply_evidence_repository()
        except self.ReplyEvidenceUnavailable as exc:
            self.log.error(
                "Skipping quote-tweet reply target_id=%s because local evidence is unavailable: %s",
                quote_id,
                exc,
            )
            self.log_event(
                "reply_evidence_unavailable",
                lane="quote_tweet",
                target_id=quote_id,
            )
            return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)

        evaluation = self.persistence.recover(
            state,
            quote_id,
            "quote_tweet",
            context=reply_context,
            recent_replies=self.history.recovery_replies(
                state,
                context=reply_context,
            ),
        )
        try:
            if evaluation is None or evaluation.status == "draft_discarded":
                evaluation = self.generation.evaluate(
                    reply_context,
                    prepared.media_context,
                    state=state,
                )
            elif evaluation.reply is not None:
                self.log.info(
                    "Reusing persisted single-call reply draft "
                    "target_id=%s source=quote_tweet",
                    quote_id,
                )
        except self.RemoteOperationsPaused:
            self.log.info(
                "Deferring quote-tweet reply target_id=%s "
                "reason=global_runtime_control_pause",
                quote_id,
            )
            self.persistence.save(state, durable=True)
            return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
        except self.ApiError as exc:
            self.log.exception("OpenAI single-call quote-tweet reply failed")
            if exc.service == "openai":
                self.cooldowns.record_error(state, exc, "openai")
            self.persistence.save(state)
            return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
        except Exception:
            self.log.exception("Unexpected single-call quote-tweet reply failure")
            self.persistence.save(state)
            return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
        return evaluation

    def _resolve_reply_evaluation(
        self, quote_id: str, evaluation: PipelineOutcome, state: BotState
    ) -> SkipReplyCandidate | FinishReplyCheck | None:
        """Retire terminal decisions, defer retryable failures, or allow a validated reply through."""
        reply_text = evaluation.reply
        if not reply_text:
            if _is_terminal_candidate_local_failure(evaluation):
                failure_category = str(evaluation.error_category)
                failure_reason = str(evaluation.reason or failure_category)
                self.log.warning(
                    "Retiring quote tweet %s after permanent "
                    "candidate-local reply failure category=%s reason=%s",
                    quote_id,
                    failure_category,
                    failure_reason,
                )
                self.reply_evaluations.record(
                    state,
                    target_id=quote_id,
                    lane="quote_tweet",
                    reason=failure_reason,
                    outcome="operational_failure",
                )
                mark_quote_tweet_skipped(state, quote_id)
                self.persistence.save(state, durable=True)
                return SkipReplyCandidate()
            if outcome_disposition(evaluation) != "no_reply":
                self.log.warning(
                    "Deferring quote tweet %s after operational reply "
                    "failure reason=%s",
                    quote_id,
                    evaluation.reason or "unknown",
                )
                self.persistence.save(state, durable=True)
                return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
            reason_code = str(
                evaluation.reason_code or evaluation.reason or "model_selected_no_reply"
            )
            self.reply_evaluations.record(
                state,
                target_id=quote_id,
                lane="quote_tweet",
                reason=reason_code,
            )
            self.log.info(
                "Sol selected no_reply for quote tweet %s reason=%s",
                quote_id,
                reason_code,
            )
            mark_quote_tweet_skipped(state, quote_id)
            self.persistence.save(state, durable=True)
            return SkipReplyCandidate()
        if not isinstance(reply_text, self.ValidatedReply):
            self.log.error(
                "Single-call pipeline returned an unvalidated quote-tweet "
                "reply; deferring target_id=%s",
                quote_id,
            )
            self.persistence.save(state, durable=True)
            return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
        return None

    def _prepare_reply_receipt(
        self,
        candidate: _QuoteCandidate,
        original_post_id: str,
        reply_text: ValidatedReplyValue,
        reply_context: ReplyContextData,
        state: BotState,
    ) -> dict[str, Any] | FinishReplyCheck:
        """Persist the validated draft before copying and binding its sending receipt."""
        quote_tweet = candidate.tweet
        quote_id = candidate.quote_id
        author_id = candidate.author_id

        self._log_validated_single_call_reply(
            target_description="quote tweet",
            target_id=quote_id,
            reply=reply_text,
        )

        def log_validation_failure() -> None:
            """Retain the quote lane's persistence-validation log format."""
            self.log.error(
                "Single-call reply draft failed persistence validation; "
                "deferring target_id=%s source=quote_tweet",
                quote_id,
            )

        if not persist_validated_reply_draft(
            state,
            quote_id,
            "quote_tweet",
            reply_text,
            reply_context,
            SINGLE_CALL_STRATEGY_VERSION=self.SINGLE_CALL_STRATEGY_VERSION,
            persistence=self.persistence,
            log_validation_failure=log_validation_failure,
            log_event=self.log_event,
        ):
            return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
        receipt_template = build_sending_reply_receipt(
            {
                "target_id": quote_id,
                "author_id": author_id,
                "candidate_source": "quote_tweet",
                "conversation_id": str(quote_tweet.get("conversation_id", quote_id)),
                "reply_text": reply_text,
                "original_post_id": str(original_post_id),
            },
            reply_context,
        )
        receipt_template = self.delivery.bind_attempt(receipt_template)
        return receipt_template

    def _retire_terminal_target(
        self,
        state: BotState,
        quote_id: str,
        reply_text: ValidatedReplyValue,
        *,
        failure_reason: str,
        reason: str,
    ) -> None:
        """Record a terminal quote outcome and durably skip its target and draft."""
        self.log_ai_reply_posting_outcome(
            reply=reply_text,
            status="posting_failed_terminal",
            lane="quote_tweet",
            target_id=quote_id,
            failure_reason=failure_reason,
        )
        self.log_event(
            "reply_target_terminal",
            lane="quote_tweet",
            target_id=quote_id,
            outcome="reply_not_permitted",
            reason=reason,
        )
        self.reply_evaluations.record(
            state,
            target_id=quote_id,
            lane="quote_tweet",
            reason=reason,
            outcome="reply_not_permitted",
        )
        mark_quote_tweet_skipped(state, quote_id)
        self.persistence.clear(state, quote_id, "quote_tweet")
        self.persistence.save(state, durable=True)

    def _deliver_reply(
        self,
        quote_id: str,
        reply_text: ValidatedReplyValue,
        receipt_template: dict[str, Any],
        state: BotState,
    ) -> ConfirmedReplyReceipt | FinishReplyCheck:
        """Deliver through the shared boundary, retaining quote-lane retirement and statuses."""

        def retire_terminal_target(failure_reason: str) -> None:
            if failure_reason == "target_unavailable_pre_send":
                self.log.warning(
                    "Cannot reply to quote tweet %s because it disappeared "
                    "after evaluation; marking it skipped without consuming "
                    "reply quota",
                    quote_id,
                )
            else:
                self.log.warning(
                    "Cannot reply to quote tweet %s because X says replies are not allowed; "
                    "marking quote tweet as skipped without consuming reply quota",
                    quote_id,
                )
            self._retire_terminal_target(
                state,
                quote_id,
                reply_text,
                failure_reason=failure_reason,
                reason=f"x_{failure_reason}",
            )

        outcome = self.delivery.deliver(
            state,
            quote_id,
            reply_text,
            receipt_template,
            lane="quote_tweet",
            log_source="quote-tweet",
            mark_as_ai=self.config.mark_as_ai,
            read_error_scope="quote",
            retire_terminal_target=retire_terminal_target,
        )
        if isinstance(outcome, ReplyDeliveryStop):
            return FinishReplyCheck(QUOTE_CHECK_STATUS_CHECKED)
        return outcome

    def _finalise_confirmed_reply(
        self,
        candidate: _QuoteCandidate,
        original_post_id: str,
        receipt: ConfirmedReplyReceipt,
        state: BotState,
    ) -> str:
        """Finish the shared confirmation transaction and report this lane's success."""
        quote_id = candidate.quote_id
        author_id = candidate.author_id
        own_reply_id = self.delivery.finalise(
            state,
            receipt,
            target_id=quote_id,
            quote_reply=True,
        )

        self.log_event(
            "reply_posted",
            lane="quote_tweet",
            target_id=quote_id,
            author_id=author_id,
            original_post_id=original_post_id,
            reply_post_id=own_reply_id,
            daily_reply_count=state.get("daily_reply_count"),
            daily_quote_reply_count=state.get("daily_quote_reply_count"),
        )
        self.log.info("Quote-tweet reply posted successfully")
        return QUOTE_CHECK_STATUS_POSTED
