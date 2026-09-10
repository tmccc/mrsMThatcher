"""Daily meme selection, calendar scheduling and transactional posting.

The coordinator supplies current callbacks, configuration, logger and exception
authority on every call. Original workflow bodies and nested closure references
are preserved; metadata loading, shared state helpers, durable attempts, receipts,
transport and persistence remain in their existing owners. Explicit runtime calls
may scan the supplied meme directory and mutate/save the caller's state or publish
through supplied callbacks. Imports perform no runtime work or configuration
access, and no callbacks are retained. Standard-library regex, calendar and random
imports preserve the existing behavior and shared random stream.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from logging import Logger
from pathlib import Path


# A callback may return None before a later step fails. Keep that distinct from
# a recovery value whose producing step never completed.
_RECOVERY_VALUE_UNAVAILABLE = object()


def _confirmed_meme_schedule_fields(receipt: dict) -> dict:
    """Project a confirmed meme's required schedule fields in their read order."""
    return {
        "next_meme_post_epoch": int(receipt["next_meme_post_epoch"]),
        "meme_schedule_version": int(receipt["meme_schedule_version"]),
        "next_meme_schedule_mode": str(receipt["next_meme_schedule_mode"]),
        "next_meme_schedule_date": str(receipt["next_meme_schedule_date"]),
        "meme_anchor_quote_post_epoch": 0,
    }


def original_meme_filename(shortlist_path: Path) -> str:
    """Return the original meme filename."""
    name = shortlist_path.name

    prefix_patterns = [
        r"^\d{3}_impact\d+_share\d+_grade[A-D]_post_as_is_(.+)$",
        r"^\d{3}_score\d+_(?:high|medium|low)_(.+)$",
    ]

    for pattern in prefix_patterns:
        match = re.match(pattern, name)
        if match:
            return match.group(1)

    return name


def build_meme_cache_summary(
    shortlist_path: Path,
    analysis_index: dict[str, dict],
    *,
    original_meme_filename: Callable,
    log: Logger,
) -> str:
    """Build meme cache summary."""
    original_name = original_meme_filename(shortlist_path)
    item = analysis_index.get(original_name)

    if not item:
        log.warning(
            "No meme analysis found for shortlist file=%s original_name=%s",
            shortlist_path.name,
            original_name,
        )
        return f"Anti-socialist meme image. Original filename: {original_name}."

    description = str(item.get("grok_description", "")).strip()
    message = str(item.get("anti_socialist_message", "")).strip()
    ranking = item.get("ranking")
    shareability = str(item.get("shareability", "")).strip()

    parts: list[str] = []

    if description:
        parts.append(description)

    if message:
        parts.append(f"Anti-socialist message: {message}")

    metadata_parts: list[str] = []

    if ranking is not None:
        metadata_parts.append(f"ranking {ranking}")

    if shareability:
        metadata_parts.append(f"shareability {shareability}")

    if metadata_parts:
        parts.append("Analysis metadata: " + ", ".join(metadata_parts) + ".")

    return " ".join(parts).strip()


def list_meme_candidates(
    *,
    MEME_DIR: Path,
    log: Logger,
) -> list[Path]:
    """List meme candidates."""
    if not MEME_DIR.exists():
        log.warning("Meme directory does not exist: %s", MEME_DIR)
        return []

    files = [
        p for p in MEME_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ]

    files.sort(key=lambda p: p.name)
    log.info("Found %d meme candidates in %s", len(files), MEME_DIR)
    return files


def choose_next_meme(
    state: dict,
    *,
    list_meme_candidates: Callable,
    log: Logger,
    RESET_MEME_CYCLE_WHEN_ALL_POSTED: bool,
    save_state: Callable,
) -> Path | None:
    """Select next meme."""
    candidates = list_meme_candidates()

    if not candidates:
        return None

    posted = set(str(x) for x in state.get("posted_meme_filenames", []))
    available = [p for p in candidates if p.name not in posted]

    if not available:
        log.info("All meme candidates have already been posted")

        if RESET_MEME_CYCLE_WHEN_ALL_POSTED:
            log.info("RESET_MEME_CYCLE_WHEN_ALL_POSTED=True, clearing meme history")
            state["posted_meme_filenames"] = []
            save_state(state)
            return candidates[0]

        return None

    return available[0]


def meme_schedule_datetime(
    epoch: int,
    *,
    bound_schedule_datetime: Callable,
    MAIN_POST_SCHEDULE_TIMEZONE: str,
) -> datetime:
    """Interpret a meme schedule epoch in the production calendar zone."""

    return bound_schedule_datetime(int(epoch), MAIN_POST_SCHEDULE_TIMEZONE)


def meme_schedule_date_str(
    epoch: int | None = None,
    *,
    now_epoch: Callable,
    meme_schedule_datetime: Callable,
) -> str:
    """Return a meme schedule date independent of the process's ambient TZ."""

    if epoch is None:
        epoch = now_epoch()
    return meme_schedule_datetime(int(epoch)).strftime("%Y-%m-%d")


def meme_posted_on_date(
    state: dict,
    date_text: str,
    *,
    meme_schedule_date_str: Callable,
) -> bool:
    """Return the meme posted on date."""
    last_epoch = int(state.get("last_meme_post_epoch", 0) or 0)
    if not last_epoch:
        return False
    return meme_schedule_date_str(last_epoch) == date_text


def next_meme_fallback_epoch(
    state: dict,
    from_epoch: int | None = None,
    *,
    now_epoch: Callable,
    meme_schedule_datetime: Callable,
    MEME_FALLBACK_HOUR: int,
    MEME_FALLBACK_MINUTE: int,
    meme_posted_on_date: Callable,
) -> int:
    """Return the next meme fallback epoch."""
    if from_epoch is None:
        from_epoch = now_epoch()

    now_dt = meme_schedule_datetime(int(from_epoch))
    target = now_dt.replace(
        hour=MEME_FALLBACK_HOUR,
        minute=MEME_FALLBACK_MINUTE,
        second=0,
        microsecond=0,
    )

    target_date = target.strftime("%Y-%m-%d")

    if int(target.timestamp()) <= from_epoch or meme_posted_on_date(state, target_date):
        target = target + timedelta(days=1)

    return int(target.timestamp())


def next_meme_schedule_fields(
    state: dict,
    from_epoch: int | None = None,
    mode: str = 'fallback',
    *,
    next_meme_fallback_epoch: Callable,
    MEME_SCHEDULE_VERSION: int,
    meme_schedule_date_str: Callable,
) -> dict:
    """Return the next meme schedule fields."""
    next_epoch = next_meme_fallback_epoch(state, from_epoch)
    return {
        "next_meme_post_epoch": next_epoch,
        "meme_schedule_version": MEME_SCHEDULE_VERSION,
        "next_meme_schedule_mode": mode,
        "next_meme_schedule_date": meme_schedule_date_str(next_epoch),
        "meme_anchor_quote_post_epoch": 0,
    }


def meme_delay_schedule_fields(
    epoch: int,
    mode: str,
    *,
    MEME_SCHEDULE_MODES: set[str],
    MEME_SCHEDULE_VERSION: int,
    meme_schedule_date_str: Callable,
) -> dict:
    """Return the meme delay schedule fields."""
    if mode not in MEME_SCHEDULE_MODES or mode in {"", "after_first_quote_after_midday"}:
        raise ValueError(f"Unsupported non-quote meme delay schedule mode: {mode}")
    return {
        "next_meme_post_epoch": int(epoch),
        "meme_schedule_version": MEME_SCHEDULE_VERSION,
        "next_meme_schedule_mode": mode,
        "next_meme_schedule_date": meme_schedule_date_str(int(epoch)),
        "meme_anchor_quote_post_epoch": 0,
    }


def set_meme_delay_schedule(
    state: dict,
    *,
    epoch: int,
    mode: str,
    save: bool = True,
    apply_state_fields: Callable,
    meme_delay_schedule_fields: Callable,
    save_state: Callable,
) -> None:
    """Set meme delay schedule."""
    apply_state_fields(state, meme_delay_schedule_fields(epoch, mode))
    if save:
        save_state(state)


def schedule_next_meme_post(
    state: dict,
    from_epoch: int | None = None,
    mode: str = 'fallback',
    *,
    save: bool = True,
    next_meme_schedule_fields: Callable,
    apply_state_fields: Callable,
    save_state: Callable,
    log: Logger,
) -> None:
    """
    Schedule the fallback daily meme time. This is deliberately later than the
    preferred organic timing. If a quote/image post happens after midday first,
    maybe_schedule_meme_after_quote_post() will replace this fallback with a
    random 35-75 minute delay after that post.
    """
    fields = next_meme_schedule_fields(state, from_epoch, mode)
    apply_state_fields(state, fields)
    next_epoch = int(fields["next_meme_post_epoch"])
    if save:
        save_state(state)

    log.info(
        "Next meme fallback scheduled at %s mode=%s",
        datetime.fromtimestamp(next_epoch).strftime("%Y-%m-%d %H:%M:%S"),
        mode,
    )


def ensure_meme_schedule_initialized(
    state: dict,
    *,
    ENABLE_DAILY_MEME_POSTS: bool,
    MEME_SCHEDULE_VERSION: int,
    log: Logger,
    MEME_TRIGGER_AFTER_HOUR: int,
    MEME_FALLBACK_HOUR: int,
    MEME_FALLBACK_MINUTE: int,
    schedule_next_meme_post: Callable,
    now_epoch: Callable,
) -> None:
    """Ensure meme schedule initialized."""
    if not ENABLE_DAILY_MEME_POSTS:
        return

    next_epoch = int(state.get("next_meme_post_epoch", 0) or 0)
    schedule_version = int(state.get("meme_schedule_version", 0) or 0)

    if schedule_version != MEME_SCHEDULE_VERSION:
        log.info(
            "Migrating meme schedule state to version %s: after first quote/image post after %02d:00, fallback %02d:%02d",
            MEME_SCHEDULE_VERSION,
            MEME_TRIGGER_AFTER_HOUR,
            MEME_FALLBACK_HOUR,
            MEME_FALLBACK_MINUTE,
        )
        schedule_next_meme_post(state, now_epoch(), mode="fallback_migrated")
        return

    if not next_epoch:
        log.info("No next_meme_post_epoch found; scheduling meme fallback")
        schedule_next_meme_post(state, now_epoch(), mode="fallback_startup")
        return

    log.info(
        "Existing next_meme_post_epoch=%s, human=%s, mode=%s, schedule_date=%s",
        next_epoch,
        datetime.fromtimestamp(next_epoch).strftime("%Y-%m-%d %H:%M:%S"),
        state.get("next_meme_schedule_mode"),
        state.get("next_meme_schedule_date"),
    )


def meme_schedule_fields_after_quote_post(
    state: dict,
    quote_post_epoch: int | None = None,
    *,
    delay: int | None = None,
    ENABLE_DAILY_MEME_POSTS: bool,
    now_epoch: Callable,
    meme_schedule_datetime: Callable,
    MEME_TRIGGER_AFTER_HOUR: int,
    log: Logger,
    meme_posted_on_date: Callable,
    MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS: int,
    MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS: int,
    MEME_SCHEDULE_VERSION: int,
) -> dict:
    """Return the meme schedule fields after quote post."""
    if not ENABLE_DAILY_MEME_POSTS:
        return {}

    if quote_post_epoch is None:
        quote_post_epoch = now_epoch()

    quote_dt = meme_schedule_datetime(int(quote_post_epoch))
    quote_date = quote_dt.strftime("%Y-%m-%d")

    if quote_dt.hour < MEME_TRIGGER_AFTER_HOUR:
        log.info(
            "Quote/image post was before meme trigger hour %02d:00; not scheduling daily meme from it",
            MEME_TRIGGER_AFTER_HOUR,
        )
        return {}

    if meme_posted_on_date(state, quote_date):
        log.info("Daily meme already posted on %s; not scheduling another", quote_date)
        return {}

    next_epoch = int(state.get("next_meme_post_epoch", 0) or 0)
    next_mode = str(state.get("next_meme_schedule_mode", "") or "")

    if next_epoch:
        next_schedule_date = str(state.get("next_meme_schedule_date", "") or "")
        if next_schedule_date == quote_date and next_mode == "after_first_quote_after_midday":
            log.info(
                "Daily meme already scheduled from first post after midday at %s; not rescheduling",
                datetime.fromtimestamp(next_epoch).strftime("%Y-%m-%d %H:%M:%S"),
            )
            return {}

    if delay is None:
        delay = random.randint(MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS, MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS)
    scheduled_epoch = int(quote_post_epoch) + delay
    return {
        "next_meme_post_epoch": scheduled_epoch,
        "meme_schedule_version": MEME_SCHEDULE_VERSION,
        "next_meme_schedule_mode": "after_first_quote_after_midday",
        "next_meme_schedule_date": quote_date,
        "meme_anchor_quote_post_epoch": int(quote_post_epoch),
    }


def maybe_schedule_meme_after_quote_post(
    state: dict,
    quote_post_epoch: int | None = None,
    *,
    save: bool = True,
    meme_schedule_fields_after_quote_post: Callable,
    apply_state_fields: Callable,
    save_state: Callable,
    now_epoch: Callable,
    log: Logger,
    MEME_TRIGGER_AFTER_HOUR: int,
) -> None:
    """Attempt to schedule meme after quote post."""
    fields = meme_schedule_fields_after_quote_post(state, quote_post_epoch)
    if not fields:
        return
    apply_state_fields(state, fields)
    if save:
        save_state(state)

    if quote_post_epoch is None:
        quote_post_epoch = now_epoch()
    scheduled_epoch = int(fields["next_meme_post_epoch"])
    delay = scheduled_epoch - int(quote_post_epoch)

    log.info(
        "Daily meme scheduled for %s: %d seconds after first quote/image post after %02d:00",
        datetime.fromtimestamp(scheduled_epoch).strftime("%Y-%m-%d %H:%M:%S"),
        delay,
        MEME_TRIGGER_AFTER_HOUR,
    )


def run_daily_meme_stage(
    stage: str,
    operation,
    *,
    log: Logger,
    log_event: Callable,
):
    """Run one meme stage while preserving the original exception type."""
    try:
        return operation()
    except Exception as exc:
        log.error(
            "Daily meme stage failed. stage=%s error_type=%s error=%s",
            stage,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        log_event(
            "daily_meme_failure",
            status="failed",
            stage=stage,
            error_type=type(exc).__name__,
            reason=str(exc)[:500],
        )
        raise


def require_valid_meme_post_id(
    posted_id: object,
    *,
    valid_post_id: Callable,
) -> None:
    """Reject a meme response that lacks a confirmed numeric post identity."""
    if not valid_post_id(posted_id):
        raise RuntimeError(
            "Daily meme post did not return a valid post id; meme state unchanged"
        )


def post_next_meme(
    state: dict,
    *,
    log: Logger,
    run_daily_meme_stage: Callable,
    block_if_ambiguous_remote_post: Callable,
    both_main_post_receipts_exist: Callable,
    REGULAR_POST_RECEIPT_FILE: Path,
    MEME_POST_RECEIPT_FILE: Path,
    InvalidMemePostReceipt: type[Exception],
    reconcile_meme_post_receipt: Callable,
    now_epoch: Callable,
    meme_posted_on_date: Callable,
    meme_schedule_date_str: Callable,
    schedule_next_meme_post: Callable,
    block_if_unresolved_regular_post_receipt: Callable,
    choose_next_meme: Callable,
    load_meme_analysis_index: Callable,
    build_meme_cache_summary: Callable,
    upload_media: Callable,
    build_main_post_attempt: Callable,
    MEME_POST_TEXT: str,
    MEME_SCHEDULE_VERSION: int,
    MEME_FALLBACK_HOUR: int,
    MEME_FALLBACK_MINUTE: int,
    MAIN_POST_SCHEDULE_TIMEZONE: str,
    write_main_post_attempt: Callable,
    prepare_main_tweet_transport: Callable,
    handoff_confirmed_media_upload_to_main_attempt: Callable,
    begin_confirmed_post_sigint_deferral: Callable,
    create_post: Callable,
    require_valid_meme_post_id: Callable,
    api_error_proves_remote_non_success: Callable,
    remove_main_post_attempt: Callable,
    end_confirmed_post_sigint_deferral: Callable,
    AmbiguousRemotePostOutcome: type[Exception],
    remote_write_safety_incident_is_latched: Callable,
    durable_remote_write_safety_barrier_exists: Callable,
    retain_sigint_deferral_without_durable_barrier: Callable,
    inspect_confirmed_transport_transaction: Callable,
    journal_path_for_receipt: Callable,
    confirmation_epoch_for_main_attempt: Callable,
    build_confirmed_pending_schedule_receipt: Callable,
    promote_main_post_attempt_to_confirmed_pending_schedule: Callable,
    finalize_confirmed_pending_schedule_receipt: Callable,
    log_event: Callable,
    ConfirmedPendingScheduleDurabilityUncertain: type[Exception],
    ConfirmedPostLocalPersistenceError: type[Exception],
    materialize_bound_meme_schedule_receipt: Callable,
    apply_state_fields: Callable,
    cache_tweet: Callable,
    MY_USER_ID: str,
    record_recent_own_post: Callable,
    save_state: Callable,
    confirmed_meme_emergency_representation_is_complete: Callable,
    StateBackupWriteError: type[Exception],
    json_file_matches: Callable,
    STATE_FILE: Path,
    latch_confirmed_post_persistence_failure: Callable,
    UnrecoverableConfirmedPostPersistenceError: type[Exception],
    load_meme_post_receipt: Callable,
    retire_lane_transport_journal_if_present: Callable,
    remove_meme_post_receipt: Callable,
    emit_account_root_posted: Callable,
) -> None:
    """Select and post the next daily meme transactionally."""
    log.info("Starting daily meme post cycle")
    run_daily_meme_stage(
        "remote_write_barrier",
        lambda: block_if_ambiguous_remote_post(
            allow_confirmed_pending_schedule_reconciliation=True
        ),
    )

    def validate_receipt_barriers() -> None:
        if both_main_post_receipts_exist():
            log.critical(
                "Both regular and meme confirmed-post receipts exist; refusing meme posting until manually inspected: %s %s",
                REGULAR_POST_RECEIPT_FILE,
                MEME_POST_RECEIPT_FILE,
            )
            raise InvalidMemePostReceipt("Both main-post receipts exist; manual recovery required")

    run_daily_meme_stage("receipt_barrier", validate_receipt_barriers)
    if run_daily_meme_stage(
        "meme_receipt_reconciliation",
        lambda: reconcile_meme_post_receipt(state),
    ):
        log.warning("Reconciled meme post receipt; not creating a second meme post in the same call")
        return
    current_meme_epoch = now_epoch()
    if meme_posted_on_date(state, meme_schedule_date_str(current_meme_epoch)):
        log.warning(
            "Daily meme already confirmed on the current local date; "
            "scheduling the next fallback without another X request"
        )
        run_daily_meme_stage(
            "same_day_duplicate_barrier",
            lambda: schedule_next_meme_post(
                state,
                current_meme_epoch,
                mode="fallback",
            ),
        )
        return
    run_daily_meme_stage(
        "main_receipt_barrier",
        block_if_unresolved_regular_post_receipt,
    )

    meme_path = run_daily_meme_stage(
        "meme_eligibility_and_asset_selection",
        lambda: choose_next_meme(state),
    )

    if not meme_path:
        log.info("No meme available to post")
        run_daily_meme_stage(
            "schedule_update",
            lambda: schedule_next_meme_post(state),
        )
        return

    analysis_index = run_daily_meme_stage(
        "x_request_preparation",
        load_meme_analysis_index,
    )
    image_summary = run_daily_meme_stage(
        "x_request_preparation",
        lambda: build_meme_cache_summary(meme_path, analysis_index),
    )

    log.info("Posting meme image: %s", meme_path)
    log.debug("Meme image summary for cache: %r", image_summary)

    media_id = run_daily_meme_stage(
        "media_upload",
        lambda: upload_media(str(meme_path), lane="daily_meme"),
    )

    main_post_attempt = build_main_post_attempt(
        lane="daily_meme",
        text=MEME_POST_TEXT,
        media_ids=[media_id],
        made_with_ai=False,
        selected_identity={"meme_basename": meme_path.name},
        recovery_plan={
            "next_schedule_mode": "fallback",
            "meme_schedule_version": MEME_SCHEDULE_VERSION,
            "fallback_hour": MEME_FALLBACK_HOUR,
            "fallback_minute": MEME_FALLBACK_MINUTE,
            "image_summary": image_summary,
            "schedule_timezone": MAIN_POST_SCHEDULE_TIMEZONE,
        },
        attempt_epoch=current_meme_epoch,
    )
    run_daily_meme_stage(
        "main_post_attempt_persistence",
        lambda: write_main_post_attempt(main_post_attempt),
    )
    (
        main_post_attempt,
        transport_source,
        transport_authority,
    ) = run_daily_meme_stage(
        "tweet_transport_preparation",
        lambda: prepare_main_tweet_transport(main_post_attempt),
    )
    run_daily_meme_stage(
        "media_upload_handoff",
        lambda: handoff_confirmed_media_upload_to_main_attempt(
            main_post_attempt,
            transport_authority,
        ),
    )
    confirmed_post_sigint_guard = begin_confirmed_post_sigint_deferral()
    try:
        response = run_daily_meme_stage(
            "x_post_request",
            lambda: create_post(
                text=MEME_POST_TEXT,
                media_ids=[media_id],
                reply_to_id=None,
                made_with_ai=False,
                prepared_main_post_attempt=main_post_attempt,
                prepared_transport_authority=transport_authority,
                prepared_transport_source=transport_source,
            ),
        )

        posted_id = response.get("data", {}).get("id") if isinstance(response, dict) else None
        log.debug("Posted meme id=%s", posted_id)

        run_daily_meme_stage(
            "x_post_response_validation",
            lambda: require_valid_meme_post_id(posted_id),
        )
    except BaseException as remote_exc:
        if api_error_proves_remote_non_success(remote_exc):
            try:
                remove_main_post_attempt(
                    main_post_attempt,
                    sending_disposition="definite_non_success",
                )
            except BaseException as removal_exc:
                end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
                confirmed_post_sigint_guard = None
                raise AmbiguousRemotePostOutcome(
                    "A definitely unsuccessful meme post left its durable "
                    "sending receipt unresolved",
                    service="x",
                ) from removal_exc
        if (
            isinstance(remote_exc, AmbiguousRemotePostOutcome)
            and remote_write_safety_incident_is_latched()
            and not durable_remote_write_safety_barrier_exists()
        ):
            retain_sigint_deferral_without_durable_barrier(
                lane="daily_meme",
                guard=confirmed_post_sigint_guard,
            )
        else:
            end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
            confirmed_post_sigint_guard = None
        raise

    meme_post_epoch = _RECOVERY_VALUE_UNAVAILABLE
    pending_schedule_receipt = _RECOVERY_VALUE_UNAVAILABLE
    meme_schedule_fields = _RECOVERY_VALUE_UNAVAILABLE
    pending_schedule_promoted = False
    try:
        transport_confirmation = inspect_confirmed_transport_transaction(
            journal_path_for_receipt(MEME_POST_RECEIPT_FILE)
        )
        if transport_confirmation.post_id != str(posted_id):
            raise AmbiguousRemotePostOutcome(
                "Confirmed meme-post identity differs from its journal",
                service="x",
            )
        meme_post_epoch = confirmation_epoch_for_main_attempt(
            main_post_attempt,
            transport_confirmation.confirmation_epoch,
        )
        pending_schedule_receipt = build_confirmed_pending_schedule_receipt(
            main_post_attempt,
            post_id=str(posted_id),
            confirmation_epoch=meme_post_epoch,
            image_summary=image_summary,
        )
        pending_schedule_receipt = (
            promote_main_post_attempt_to_confirmed_pending_schedule(
                main_post_attempt,
                post_id=str(posted_id),
                confirmation_epoch=meme_post_epoch,
                image_summary=image_summary,
            )
        )
        pending_schedule_promoted = True
        receipt = finalize_confirmed_pending_schedule_receipt(
            pending_schedule_receipt
        )
        meme_schedule_fields = _confirmed_meme_schedule_fields(receipt)
    except BaseException as receipt_exc:
        log.critical(
            "Confirmed meme post_id=%s but stage=meme_receipt_creation failed; attempting direct durable state save",
            posted_id,
            exc_info=True,
        )
        log_event(
            "daily_meme_failure",
            status="failed",
            stage="meme_receipt_creation",
            post_id=str(posted_id),
            error_type=type(receipt_exc).__name__,
            reason=str(receipt_exc)[:500],
        )
        if isinstance(
            receipt_exc,
            ConfirmedPendingScheduleDurabilityUncertain,
        ):
            if receipt_exc.durable_barrier:
                end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
                confirmed_post_sigint_guard = None
            else:
                retain_sigint_deferral_without_durable_barrier(
                    lane="daily_meme",
                    guard=confirmed_post_sigint_guard,
                )
            raise
        if pending_schedule_promoted:
            # Confirmation is already durable.  Leave the pending receipt for
            # local-only reconciliation; no scheduler path may create another
            # meme while it remains.
            end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
            confirmed_post_sigint_guard = None
            if not isinstance(receipt_exc, Exception):
                raise
            raise ConfirmedPostLocalPersistenceError(
                f"Confirmed meme post {posted_id}; its durable pending-schedule "
                "receipt remains for local-only reconciliation"
            ) from receipt_exc
        emergency_state_write_succeeded = False
        emergency_state_complete = False
        try:
            if pending_schedule_receipt is not _RECOVERY_VALUE_UNAVAILABLE:
                fallback_receipt = materialize_bound_meme_schedule_receipt(
                    pending_schedule_receipt
                )
                meme_schedule_fields = _confirmed_meme_schedule_fields(fallback_receipt)
            state["last_main_post_id"] = str(posted_id)
            if meme_post_epoch is not _RECOVERY_VALUE_UNAVAILABLE:
                state["last_meme_post_epoch"] = meme_post_epoch
            posted = set(str(x) for x in state.get("posted_meme_filenames", []))
            posted.add(meme_path.name)
            state["posted_meme_filenames"] = sorted(posted)
            if meme_schedule_fields is not _RECOVERY_VALUE_UNAVAILABLE:
                apply_state_fields(state, meme_schedule_fields)
            try:
                cache_tweet(
                    state,
                    tweet_id=str(posted_id),
                    text=MEME_POST_TEXT,
                    author_id=str(MY_USER_ID),
                    conversation_id=str(posted_id),
                    referenced_tweets=[],
                    image_summary=image_summary,
                    post_type="daily_meme",
                )
                record_recent_own_post(state, str(posted_id))
            except Exception:
                log.critical("Emergency in-memory cache/recent update failed after confirmed meme post", exc_info=True)
            save_state(state, durable=True)
            emergency_state_write_succeeded = True
            emergency_state_complete = confirmed_meme_emergency_representation_is_complete(
                post_id=str(posted_id),
                post_epoch=meme_post_epoch if meme_post_epoch is not _RECOVERY_VALUE_UNAVAILABLE else None,
                meme_basename=meme_path.name,
                state=state,
                main_post_attempt=main_post_attempt,
            )
        except Exception as emergency_exc:
            if isinstance(emergency_exc, StateBackupWriteError) and json_file_matches(STATE_FILE, state):
                emergency_state_write_succeeded = True
                emergency_state_complete = confirmed_meme_emergency_representation_is_complete(
                    post_id=str(posted_id),
                    post_epoch=meme_post_epoch if meme_post_epoch is not _RECOVERY_VALUE_UNAVAILABLE else None,
                    meme_basename=meme_path.name,
                    state=state,
                    main_post_attempt=main_post_attempt,
                )
                log.warning(
                    "Emergency canonical state was committed after confirmed meme post, "
                    "but a later backup/finalisation step failed; using the canonical "
                    "durable state as the recovery representation",
                    exc_info=True,
                )
            else:
                log.critical("Emergency state persistence failed after confirmed meme post", exc_info=True)
        if not emergency_state_complete:
            incomplete_component = (
                "incomplete_meme_post_state"
                if emergency_state_write_succeeded
                else "state"
            )
            durable_barrier = latch_confirmed_post_persistence_failure(
                lane="daily_meme",
                post_id=str(posted_id),
                failure_components=["meme_post_receipt", incomplete_component],
            )
            if durable_barrier or durable_remote_write_safety_barrier_exists():
                end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
                confirmed_post_sigint_guard = None
            else:
                retain_sigint_deferral_without_durable_barrier(
                    lane="daily_meme",
                    guard=confirmed_post_sigint_guard,
                )
            raise UnrecoverableConfirmedPostPersistenceError(
                f"Confirmed meme post {posted_id} has no complete durable recovery representation"
            ) from receipt_exc
        status_after_fallback, _current_after_fallback = load_meme_post_receipt()
        if status_after_fallback == "sending":
            retire_lane_transport_journal_if_present(
                receipt_path=MEME_POST_RECEIPT_FILE,
                receipt=main_post_attempt,
                lane="daily_meme",
                post_id=str(posted_id),
            )
            remove_main_post_attempt(
                main_post_attempt,
                sending_disposition="confirmed_state_fallback",
            )
        elif status_after_fallback != "valid":
            raise UnrecoverableConfirmedPostPersistenceError(
                f"Confirmed meme post {posted_id} has no stable receipt state "
                "after fallback persistence"
            ) from receipt_exc
        end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
        confirmed_post_sigint_guard = None
        if not isinstance(receipt_exc, Exception):
            raise
        raise ConfirmedPostLocalPersistenceError(
            f"Confirmed meme post {posted_id} but failed writing recovery receipt"
        ) from receipt_exc

    end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
    confirmed_post_sigint_guard = None

    try:
        state["last_main_post_id"] = str(posted_id)
        state["last_meme_post_epoch"] = meme_post_epoch
        posted = set(str(x) for x in state.get("posted_meme_filenames", []))
        posted.add(meme_path.name)
        state["posted_meme_filenames"] = sorted(posted)
        apply_state_fields(state, meme_schedule_fields)
        cache_tweet(
            state,
            tweet_id=str(posted_id),
            text=MEME_POST_TEXT,
            author_id=str(MY_USER_ID),
            conversation_id=str(posted_id),
            referenced_tweets=[],
            image_summary=image_summary,
            post_type="daily_meme",
        )
        record_recent_own_post(state, str(posted_id))
        save_state(state, durable=True)
    except Exception as exc:
        log.critical(
            "Confirmed meme post_id=%s but stage=durable_state_and_schedule_update failed; receipt remains for reconciliation",
            posted_id,
            exc_info=True,
        )
        log_event(
            "daily_meme_failure",
            status="failed",
            stage="durable_state_and_schedule_update",
            post_id=str(posted_id),
            error_type=type(exc).__name__,
            reason=str(exc)[:500],
        )
        raise ConfirmedPostLocalPersistenceError(f"Confirmed meme post {posted_id} but durable state save failed")
    try:
        retire_lane_transport_journal_if_present(
            receipt_path=MEME_POST_RECEIPT_FILE,
            receipt=receipt,
            lane="daily_meme",
            post_id=str(posted_id),
        )
        remove_meme_post_receipt(receipt)
    except Exception as exc:
        log.critical(
            "Confirmed meme post_id=%s but stage=meme_receipt_confirmation failed after durable state save",
            posted_id,
            exc_info=True,
        )
        log_event(
            "daily_meme_failure",
            status="failed",
            stage="meme_receipt_confirmation",
            post_id=str(posted_id),
            error_type=type(exc).__name__,
            reason=str(exc)[:500],
        )
        raise ConfirmedPostLocalPersistenceError(f"Confirmed meme post {posted_id} but receipt removal failed") from exc

    log_event("main_post_posted", lane="daily_meme", post_id=posted_id, filename=meme_path.name)
    emit_account_root_posted(
        lane="daily_meme",
        post_id=posted_id,
        public_text=MEME_POST_TEXT,
        image_summary=image_summary,
    )
    log.info("Daily meme posted successfully. posted_id=%s file=%s", posted_id, meme_path.name)
