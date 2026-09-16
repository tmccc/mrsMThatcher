"""Coordinate reply arbitration and blocked ticks through current root dependencies.

Three explicit root adapters supply current flags, status values, exception
classes, scheduler, persistence, lane and barrier callbacks, events and logger.
Original bodies preserve scheduler repair before arbitration, state identity,
spacing and priority predicates, separate save/event/log order and native
failures. Every blocked tick rechecks durability before one-shot logging exits;
only Exception is caught, preserving retained SIGINT delivery. Main/test loops,
lane implementations, durable barrier and signal authority stay in their
existing locations. This owner retains no callbacks, configuration, clients or
state and performs no import-time file, environment, provider, clock or RNG work.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def sanitize_next_reply_lane_priority(
    state: dict,
    *,
    log: Any,
) -> bool:
    """Sanitise next reply lane priority."""
    priority = str(state.get("next_reply_lane_priority", "normal") or "normal")
    if priority in {"normal", "quote"}:
        if state.get("next_reply_lane_priority") != priority:
            state["next_reply_lane_priority"] = priority
            return True
        return False

    log.warning("Invalid next_reply_lane_priority=%r; using normal", state.get("next_reply_lane_priority"))
    state["next_reply_lane_priority"] = "normal"
    return True


def run_reply_lane_checks_for_tick(
    state: dict,
    current: int,
    *,
    AmbiguousRemotePostOutcome: type[Exception],
    ENABLE_AUTO_REPLIES: bool,
    ENABLE_QUOTE_TWEET_CHECKS: bool,
    MIN_SECONDS_BETWEEN_REPLIES: int,
    NORMAL_CHECK_STATUS_POSTED: str,
    NORMAL_CHECK_STATUS_SKIPPED_SPACING: str,
    QUOTE_CHECK_EVERY_SECONDS: int,
    QUOTE_CHECK_SPACING_RETRY_SECONDS: int,
    QUOTE_CHECK_STATUS_POSTED: str,
    QUOTE_CHECK_STATUS_SKIPPED_SPACING: str,
    REPLY_CHECK_EVERY_SECONDS: int,
    UnrecoverableConfirmedReplyPersistenceError: type[Exception],
    ambiguous_remote_post_is_blocking: Callable[[], bool],
    log: Any,
    log_event: Callable[..., None],
    maybe_reply_to_mentions: Callable[[dict], str],
    maybe_reply_to_quote_tweets: Callable[[dict], str],
    save_state: Callable[[dict], None],
    scheduler_epoch_from_state: Callable[..., tuple[int, bool]],
) -> tuple[int, int]:
    """Run one reply-lane tick using the check epochs in canonical state."""
    ambiguity_blocked = False
    last_reply_check_epoch, reply_epoch_changed = scheduler_epoch_from_state(
        state,
        "last_reply_check_epoch",
        current=current,
    )
    last_quote_tweet_check_epoch, quote_epoch_changed = scheduler_epoch_from_state(
        state,
        "last_quote_tweet_check_epoch",
        current=current,
    )
    if reply_epoch_changed or quote_epoch_changed:
        save_state(state)

    reply_lane_priority = str(state.get("next_reply_lane_priority", "normal") or "normal")

    seconds_since_last_reply = current - int(state.get("last_reply_epoch", 0) or 0)
    reply_spacing_open = seconds_since_last_reply >= MIN_SECONDS_BETWEEN_REPLIES
    mention_check_due = ENABLE_AUTO_REPLIES and current - last_reply_check_epoch >= REPLY_CHECK_EVERY_SECONDS
    quote_check_due = (
        ENABLE_QUOTE_TWEET_CHECKS
        and current - last_quote_tweet_check_epoch >= QUOTE_CHECK_EVERY_SECONDS
    )

    def run_normal_check(*, forced: bool = False) -> bool:
        nonlocal last_reply_check_epoch, ambiguity_blocked

        if forced:
            log.info(
                "Quote-tweet check is due, but normal/hot-post reply lane has priority; "
                "running normal reply check first"
            )
        else:
            log.info("Due to check mentions")

        try:
            normal_check_status = maybe_reply_to_mentions(state)
        except (
            AmbiguousRemotePostOutcome,
            UnrecoverableConfirmedReplyPersistenceError,
        ):
            ambiguity_blocked = True
            log.critical(
                "Normal reply lane stopped by the global remote-write safety barrier"
            )
            return False
        log.info("Normal/hot-post reply check status=%s", normal_check_status)
        if ambiguous_remote_post_is_blocking():
            ambiguity_blocked = True
            log.critical("Normal reply lane created an ambiguous-post barrier; skipping all later lanes")
            return False

        if normal_check_status != NORMAL_CHECK_STATUS_SKIPPED_SPACING:
            last_reply_check_epoch = current
            state["last_reply_check_epoch"] = current
            save_state(state)
        else:
            log.info(
                "Normal/hot-post reply check skipped only because of reply spacing; "
                "normal check interval not consumed"
            )

        if normal_check_status == NORMAL_CHECK_STATUS_POSTED:
            state["next_reply_lane_priority"] = "quote"
            save_state(state)
            log.info("Normal/hot-post reply lane posted; next reply-lane priority=quote")
            return True

        if forced:
            log.info("Normal/hot-post reply lane did not post; quote-tweet lane may use this slot")

        return False

    def run_quote_check() -> bool:
        nonlocal last_quote_tweet_check_epoch, ambiguity_blocked

        log.info("Due to check quote tweets")
        priority_at_check = str(state.get("next_reply_lane_priority", reply_lane_priority) or reply_lane_priority)
        try:
            quote_check_status = maybe_reply_to_quote_tweets(state)
        except (
            AmbiguousRemotePostOutcome,
            UnrecoverableConfirmedReplyPersistenceError,
        ):
            ambiguity_blocked = True
            log.critical(
                "Quote-tweet lane stopped by the global remote-write safety barrier"
            )
            return False

        log.info("Quote-tweet check status=%s", quote_check_status)
        if ambiguous_remote_post_is_blocking():
            ambiguity_blocked = True
            log.critical("Quote-tweet lane created an ambiguous-post barrier; skipping all later lanes")
            return False
        log_event("quote_check_status", status=quote_check_status, priority=priority_at_check)

        if quote_check_status == QUOTE_CHECK_STATUS_POSTED:
            state["next_reply_lane_priority"] = "normal"
            save_state(state)
            log.info("Quote-tweet reply lane posted; next reply-lane priority=normal")

        if quote_check_status != QUOTE_CHECK_STATUS_SKIPPED_SPACING:
            last_quote_tweet_check_epoch = current
            state["last_quote_tweet_check_epoch"] = current
            save_state(state)
        else:
            retry_epoch = current - QUOTE_CHECK_EVERY_SECONDS + QUOTE_CHECK_SPACING_RETRY_SECONDS
            last_quote_tweet_check_epoch = retry_epoch
            state["last_quote_tweet_check_epoch"] = retry_epoch
            save_state(state)

            log.info(
                "Quote-tweet check skipped only because of reply spacing; "
                "will retry in about %d seconds",
                QUOTE_CHECK_SPACING_RETRY_SECONDS,
            )

        return quote_check_status == QUOTE_CHECK_STATUS_POSTED

    if reply_spacing_open and quote_check_due and reply_lane_priority == "quote":
        quote_posted = run_quote_check()
        if not quote_posted and mention_check_due and not ambiguity_blocked:
            run_normal_check()
    elif mention_check_due:
        normal_posted = run_normal_check()
        if not normal_posted and quote_check_due and not ambiguity_blocked:
            run_quote_check()
    elif (
        reply_spacing_open
        and quote_check_due
        and reply_lane_priority == "normal"
        and ENABLE_AUTO_REPLIES
    ):
        normal_posted = run_normal_check(forced=True)
        if not normal_posted and not ambiguity_blocked:
            run_quote_check()
    elif quote_check_due:
        run_quote_check()
    else:
        log.debug(
            "Not due to check mentions. seconds_until_next=%s",
            max(0, REPLY_CHECK_EVERY_SECONDS - (current - last_reply_check_epoch)),
        )
        log.debug(
            "Not due to check quote tweets. seconds_until_next=%s",
            max(0, QUOTE_CHECK_EVERY_SECONDS - (current - last_quote_tweet_check_epoch)),
        )

    return last_reply_check_epoch, last_quote_tweet_check_epoch


def maintain_global_remote_write_barrier_tick(
    *,
    already_logged: bool,
    ambiguous_remote_post_is_blocking: Callable[[], bool],
    durable_remote_write_safety_barrier_exists: Callable[[], bool],
    log: Any,
    remote_write_safety_protocol_is_active: Callable[[], bool],
) -> tuple[bool, bool]:
    """Maintain one fail-closed barrier tick and its one-shot logging state."""

    if not ambiguous_remote_post_is_blocking():
        return False, False
    protocol_active = remote_write_safety_protocol_is_active()
    try:
        # Recheck durability on every blocked tick. A previous marker-directory
        # fsync may have failed transiently, and this helper also restores and
        # delivers retained SIGINT after a restart-safe barrier is durable.
        durable_marker_confirmed = durable_remote_write_safety_barrier_exists()
    except Exception:
        durable_marker_confirmed = False
        log.critical(
            "The remote-write safety marker could not be inspected; the "
            "process will remain latched and must not be restarted",
            exc_info=True,
        )
    if already_logged:
        return True, True
    if not protocol_active:
        log.critical(
            "All remote posting and reply lanes are paused because the "
            "restart-persistent remote-write protocol is not activated or its "
            "sentinel is invalid; stopped clean-state activation or repair is "
            "required"
        )
    elif durable_marker_confirmed:
        log.critical(
            "All remote posting and reply lanes are paused by the durable "
            "remote-write safety barrier; manual reconciliation is required "
            "before a controlled restart"
        )
    else:
        log.critical(
            "All remote posting and reply lanes are paused by an unresolved "
            "transaction receipt, marker, or process latch; do not restart "
            "before exact reconciliation"
        )
    return True, True
