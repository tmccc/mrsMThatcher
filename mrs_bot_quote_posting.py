"""Ordinary quotation posting orchestration.

The coordinator supplies current owners, callbacks, settings, logger and
exception/type authority on every call. This owner retains selection, history rollback, schedule
projections and lane-specific local recovery. MainPostPublication owns the shared
durable publication steps and partial transaction progress; its authorities are
bound at cycle entry. State-field application uses its inert owner directly.
Arguments and nested closure references are not copied by the
adapter. Imports perform no runtime work or configuration access, and callbacks
are never retained beyond the call. The standard-library random stream is shared.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from logging import Logger
from pathlib import Path
from typing import NamedTuple, TYPE_CHECKING

from mrs_bot_regular_post_completion import complete_regular_post_persistence
from mrs_bot_runtime_state_helpers import apply_state_fields


if TYPE_CHECKING:
    from mrs_bot_image_selection import ImageSelection
    from mrs_bot_main_post_publication import MainPostPublication


# Availability is separate from a callback's value, including an assigned None.
_RECOVERY_VALUE_UNAVAILABLE = object()


def _confirmed_quote_schedule_fields(receipt: dict) -> dict:
    """Project the confirmed quote schedule before reading its meme schedule."""
    return {"next_quote_post_epoch": int(receipt["next_quote_post_epoch"])}


def _confirmed_meme_schedule_fields(receipt: dict) -> dict:
    """Project a regular post's optional meme schedule with its bound anchor."""
    return {
        "next_meme_post_epoch": int(receipt.get("next_meme_post_epoch", 0) or 0),
        "meme_schedule_version": int(receipt["meme_schedule_version"]),
        "next_meme_schedule_mode": str(receipt.get("next_meme_schedule_mode") or ""),
        "next_meme_schedule_date": str(receipt.get("next_meme_schedule_date") or ""),
        "meme_anchor_quote_post_epoch": int(receipt.get("meme_anchor_quote_post_epoch") or 0),
    }


def _select_regular_quote_image_pair(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    log: Logger,
    selection: ImageSelection,
    NoViableQuoteImagePair: type[Exception],
) -> tuple[dict, dict]:
    """Select an ordinary pair with the existing bounded image-cycle fallbacks."""
    try:
        quote_choice, image_choice, attempts = selection.choose_pair(
            lines_used,
            images_used,
            state,
        )
    except NoViableQuoteImagePair as exc:
        log.warning(
            "No viable regular quote/image pair found within current image cycle after %d attempt(s); "
            "resetting image cycle and retrying once",
            exc.attempts,
        )
        try:
            quote_choice, image_choice, attempts = selection.choose_pair(
                lines_used,
                images_used,
                state,
                force_image_cycle_reset=True,
            )
        except NoViableQuoteImagePair as reset_exc:
            if not reset_exc.excluded_last_image:
                log.error("No viable regular quote/image pair found after image-cycle recovery; giving up for this post attempt")
                raise RuntimeError(str(reset_exc)) from reset_exc
            log.warning(
                "No viable regular quote/image pair found after image-cycle recovery while excluding last regular image %s; "
                "retrying once with last image permitted",
                reset_exc.excluded_last_image,
            )
            try:
                quote_choice, image_choice, attempts = selection.choose_pair(
                    lines_used,
                    images_used,
                    state,
                    force_image_cycle_reset=True,
                    avoid_last_image_at_cycle_boundary=False,
                )
            except NoViableQuoteImagePair as final_exc:
                log.error(
                    "No viable regular quote/image pair found after final last-image recovery fallback; "
                    "giving up for this post attempt"
                )
                raise RuntimeError(str(final_exc)) from final_exc
            log.info("Regular quote/image pairing succeeded after permitting last regular image as final recovery fallback")
        else:
            log.info("Regular quote/image pairing succeeded after image-cycle recovery")
    return quote_choice, image_choice


class _QuotePostPreparation(NamedTuple):
    """Derived publication content, image identity and sampled schedule delays."""

    line_no: int
    quote_hash: str
    canonical_quote_text: str
    tweet: str
    image_no: int
    image: str
    image_basename: str
    image_made_with_ai: bool
    quote_delay: int
    meme_delay: int | None


def _prepare_quote_post(
    quote_choice: dict,
    image_choice: dict,
    *,
    log: Logger,
    POST_SLEEP_MIN: int,
    POST_SLEEP_MAX: int,
    MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS: int,
    MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS: int,
    ENABLE_DAILY_MEME_POSTS: bool,
) -> _QuotePostPreparation:
    """Derive and log the publication inputs before any upload or attempt write."""
    line_no = int(quote_choice["line_no"])
    quote_hash = str(quote_choice["quote_hash"])
    canonical_quote_text = str(quote_choice["text"])
    tweet = canonical_quote_text
    image_no = int(image_choice["image_no"])
    image = str(image_choice["path"])
    image_basename = str(image_choice["basename"])
    image_made_with_ai = image_choice.get("image_source") == "generated"
    quote_delay = random.randint(POST_SLEEP_MIN, POST_SLEEP_MAX)
    meme_delay = (
        random.randint(MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS, MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS)
        if ENABLE_DAILY_MEME_POSTS
        else None
    )

    log.info(
        "Posting quote/image. line_no=%d quote_hash=%s image_no=%d image=%s image_score=%s",
        line_no,
        quote_hash,
        image_no,
        image,
        image_choice.get("score"),
    )
    log.debug("Quote text=%r", tweet)
    return _QuotePostPreparation(
        line_no,
        quote_hash,
        canonical_quote_text,
        tweet,
        image_no,
        image,
        image_basename,
        image_made_with_ai,
        quote_delay,
        meme_delay,
    )


def _complete_quote_post(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    quote_hash: str,
    image_basename: str,
    posted_id: str,
    quote_post_epoch: int,
    quote_schedule_fields: dict,
    meme_schedule_fields: dict,
    tweet: str,
    receipt: dict,
    line_no: int,
    image_no: int,
    image_choice: dict,
    canonical_quote_text: str,
    log: Logger,
    cache_tweet: Callable,
    MY_USER_ID: str,
    record_recent_own_post: Callable,
    save_regular_post_protected_state: Callable,
    log_event: Callable,
    enqueue_historical_context_obligation: Callable,
    retire_lane_transport_journal_if_present: Callable,
    REGULAR_POST_RECEIPT_FILE: Path,
    remove_regular_post_receipt: Callable,
    ConfirmedPostLocalPersistenceError: type[Exception],
    emit_account_root_posted: Callable,
    safely_process_due_historical_context_obligations: Callable,
) -> None:
    """Persist confirmed state and retire recovery authority before final events."""

    def retire_transport_journal(commit_proof) -> None:
        """Retire live transport authority only after the outbox is durable."""
        retire_lane_transport_journal_if_present(
            commit_proof=commit_proof,
            receipt_path=REGULAR_POST_RECEIPT_FILE,
            receipt=receipt,
            lane="quote_image",
            post_id=str(posted_id),
        )

    try:
        lines_used.add(quote_hash)
        images_used.add(image_basename)
        state["last_main_post_id"] = str(posted_id)
        state["last_quote_post_epoch"] = quote_post_epoch
        state["last_regular_image_filename"] = image_basename
        apply_state_fields(state, quote_schedule_fields)
        apply_state_fields(state, meme_schedule_fields)
        cache_tweet(
            state,
            tweet_id=str(posted_id),
            text=tweet,
            author_id=str(MY_USER_ID),
            conversation_id=str(posted_id),
            referenced_tweets=[],
            post_type="quote",
        )
        record_recent_own_post(state, str(posted_id))
        complete_regular_post_persistence(
            lines_used, images_used, state, receipt,
            save_regular_post_protected_state=save_regular_post_protected_state,
            enqueue_historical_context_obligation=enqueue_historical_context_obligation,
            retire_transport_journal=retire_transport_journal,
            remove_regular_post_receipt=remove_regular_post_receipt,
        )
    except Exception as exc:
        log.critical("Confirmed regular quote/image post_id=%s but protected local persistence failed", posted_id, exc_info=True)
        raise ConfirmedPostLocalPersistenceError(
            f"Confirmed regular quote/image post {posted_id} but protected local persistence failed"
        ) from exc

    log_event(
        "main_post_posted",
        lane="quote_image",
        post_id=posted_id,
        line_no=line_no,
        image_no=image_no,
        image_basename=image_basename,
        image_hash=image_choice.get("image_hash"),
        image_score=image_choice.get("score"),
        quote_hash=quote_hash,
    )
    emit_account_root_posted(
        lane="quote_image",
        post_id=posted_id,
        public_text=tweet,
        quote_id=quote_hash,
        quote_text=canonical_quote_text,
    )
    safely_process_due_historical_context_obligations(
        parent_post_id=str(posted_id),
        runtime_state=state,
    )
    log.info("Quote/image posted successfully. posted_id=%s", posted_id)


def post_random_quote(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    publication: MainPostPublication,
    log: Logger,
    block_if_ambiguous_remote_post: Callable,
    now_epoch: Callable,
    reconcile_main_post_receipts: Callable,
    require_historical_context_outbox_writable: Callable,
    selection: ImageSelection,
    CorruptUsedHistoryError: type[Exception],
    NoViableQuoteImagePair: type[Exception],
    POST_SLEEP_MIN: int,
    POST_SLEEP_MAX: int,
    MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS: int,
    MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS: int,
    ENABLE_DAILY_MEME_POSTS: bool,
    upload_media: Callable,
    build_main_post_attempt: Callable,
    MEME_TRIGGER_AFTER_HOUR: int,
    MEME_SCHEDULE_VERSION: int,
    MAIN_POST_SCHEDULE_TIMEZONE: str,
    bound_meme_schedule_state: Callable,
    remove_main_post_attempt: Callable,
    durable_remote_write_safety_barrier_exists: Callable,
    REGULAR_POST_RECEIPT_FILE: Path,
    ConfirmedPendingScheduleDurabilityUncertain: type[Exception],
    ConfirmedPostLocalPersistenceError: type[Exception],
    materialize_bound_regular_schedule_receipt: Callable,
    cache_tweet: Callable,
    MY_USER_ID: str,
    record_recent_own_post: Callable,
    emergency_persist_confirmed_regular_post: Callable,
    confirmed_regular_emergency_representation_is_complete: Callable,
    latch_confirmed_post_persistence_failure: Callable,
    UnrecoverableConfirmedPostPersistenceError: type[Exception],
    load_regular_post_receipt: Callable,
    enqueue_historical_context_obligation: Callable,
    log_event: Callable,
    retire_lane_transport_journal_if_present: Callable,
    emit_account_root_posted: Callable,
    save_regular_post_protected_state: Callable,
    remove_regular_post_receipt: Callable,
    safely_process_due_historical_context_obligations: Callable,
) -> None:
    """Select and post one quotation-image pair transactionally."""
    log.info("Starting quote/image post cycle")
    block_if_ambiguous_remote_post(
        allow_confirmed_pending_schedule_reconciliation=True
    )

    transaction_preflight_epoch = now_epoch()
    receipt_status = reconcile_main_post_receipts(
        lines_used,
        images_used,
        state,
        minimum_next_quote_epoch=transaction_preflight_epoch,
    )
    if receipt_status.get("regular"):
        log.warning("Reconciled regular quote/image receipt; not creating a second regular post in the same call")
        return
    original_lines_used = set(lines_used)
    original_images_used = set(images_used)

    try:
        require_historical_context_outbox_writable()
        if selection.used_history.quote_used_history_has_legacy_indices(lines_used):
            raise CorruptUsedHistoryError(
                "Quote used-history still contains legacy integer entries; refusing regular quote posting until source-verified migration is possible"
            )

        quote_choice, image_choice = _select_regular_quote_image_pair(
            lines_used, images_used, state,
            log=log,
            selection=selection,
            NoViableQuoteImagePair=NoViableQuoteImagePair,
        )

        (
            line_no,
            quote_hash,
            canonical_quote_text,
            tweet,
            image_no,
            image,
            image_basename,
            image_made_with_ai,
            quote_delay,
            meme_delay,
        ) = _prepare_quote_post(
            quote_choice, image_choice,
            log=log,
            POST_SLEEP_MIN=POST_SLEEP_MIN,
            POST_SLEEP_MAX=POST_SLEEP_MAX,
            MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS=MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS,
            MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS=MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS,
            ENABLE_DAILY_MEME_POSTS=ENABLE_DAILY_MEME_POSTS,
        )

        media_id = upload_media(image, lane="quote_image")
        main_post_attempt = build_main_post_attempt(
            lane="quote_image",
            text=tweet,
            media_ids=[media_id],
            made_with_ai=image_made_with_ai,
            selected_identity={
                "quote_hash": quote_hash,
                "line_no": line_no,
                "source_line_number": line_no + 1,
                "image_basename": image_basename,
                "image_no": image_no,
            },
            recovery_plan={
                "quote_delay_seconds": quote_delay,
                "meme_delay_seconds": meme_delay,
                "meme_scheduling_enabled": bool(ENABLE_DAILY_MEME_POSTS),
                "meme_trigger_after_hour": int(MEME_TRIGGER_AFTER_HOUR),
                "meme_schedule_version": int(MEME_SCHEDULE_VERSION),
                "schedule_timezone": MAIN_POST_SCHEDULE_TIMEZONE,
                "meme_schedule_before": bound_meme_schedule_state(
                    state,
                    schedule_timezone=MAIN_POST_SCHEDULE_TIMEZONE,
                ),
                "quote_history_after": sorted(set(lines_used) | {quote_hash}),
                "image_history_after": sorted(
                    set(images_used) | {image_basename}
                ),
            },
            attempt_epoch=transaction_preflight_epoch,
        )
        publication.prepare(main_post_attempt)
        publication.begin_guard()
        posted_id = publication.send(
            text=tweet, media_id=media_id, made_with_ai=image_made_with_ai,
        )
    except BaseException as remote_exc:
        lines_used.clear()
        lines_used.update(original_lines_used)
        images_used.clear()
        images_used.update(original_images_used)
        publication.handle_remote_failure(remote_exc)
        raise

    quote_post_epoch = _RECOVERY_VALUE_UNAVAILABLE
    fallback_receipt = _RECOVERY_VALUE_UNAVAILABLE
    quote_schedule_fields = _RECOVERY_VALUE_UNAVAILABLE
    meme_schedule_fields = _RECOVERY_VALUE_UNAVAILABLE
    try:
        quote_post_epoch = publication.read_confirmation_epoch(posted_id)
        context_obligation_receipt = {
            "post_id": str(posted_id),
            "quote_hash": quote_hash,
            "quote_post_epoch": quote_post_epoch,
            "text": tweet,
        }
        receipt = publication.confirm_pending_schedule(posted_id, quote_post_epoch)
        quote_schedule_fields = _confirmed_quote_schedule_fields(receipt)
        meme_schedule_fields = _confirmed_meme_schedule_fields(receipt)
    except BaseException as receipt_exc:
        log.critical(
            "Confirmed regular quote/image post_id=%s but failed writing recovery receipt; in-memory used histories remain marked",
            posted_id,
            exc_info=True,
        )
        if isinstance(
            receipt_exc,
            ConfirmedPendingScheduleDurabilityUncertain,
        ):
            if receipt_exc.durable_barrier:
                publication.release_guard()
            else:
                publication.retain_guard()
            raise
        if publication.pending_promoted:
            # The remote identity is already durable.  Leave this receipt in
            # place so startup/current-loop reconciliation retries only local
            # schedule materialisation and can never recreate the X post.
            publication.release_guard()
            if not isinstance(receipt_exc, Exception):
                raise
            raise ConfirmedPostLocalPersistenceError(
                f"Confirmed regular quote/image post {posted_id}; its durable "
                "pending-schedule receipt remains for local-only reconciliation"
            ) from receipt_exc
        fallback_failures: list[str] = []
        if publication.pending_available:
            try:
                fallback_receipt = materialize_bound_regular_schedule_receipt(
                    publication.pending_receipt
                )
                quote_schedule_fields = _confirmed_quote_schedule_fields(fallback_receipt)
                meme_schedule_fields = _confirmed_meme_schedule_fields(fallback_receipt)
            except Exception:
                fallback_failures.append("bound_schedule_materialisation")
                log.critical(
                    "Emergency bound schedule materialisation failed after "
                    "confirmed regular post",
                    exc_info=True,
                )
        try:
            lines_used.add(quote_hash)
            images_used.add(image_basename)
            state["last_main_post_id"] = str(posted_id)
            if quote_post_epoch is not _RECOVERY_VALUE_UNAVAILABLE:
                state["last_quote_post_epoch"] = quote_post_epoch
            state["last_regular_image_filename"] = image_basename
        except Exception:
            fallback_failures.append("in_memory_regular_post_state")
            log.critical(
                "Emergency in-memory core state update failed after confirmed regular post",
                exc_info=True,
            )
        if quote_schedule_fields is not _RECOVERY_VALUE_UNAVAILABLE:
            try:
                apply_state_fields(state, quote_schedule_fields)
            except Exception:
                fallback_failures.append("quote_schedule_state")
                log.critical(
                    "Emergency quote schedule update failed after confirmed regular post",
                    exc_info=True,
                )
        if meme_schedule_fields is not _RECOVERY_VALUE_UNAVAILABLE:
            try:
                apply_state_fields(state, meme_schedule_fields)
            except Exception:
                fallback_failures.append("meme_schedule_state")
                log.critical(
                    "Emergency meme schedule update failed after confirmed regular post",
                    exc_info=True,
                )
        try:
            cache_tweet(
                state,
                tweet_id=str(posted_id),
                text=tweet,
                author_id=str(MY_USER_ID),
                conversation_id=str(posted_id),
                referenced_tweets=[],
                post_type="quote",
            )
            record_recent_own_post(state, str(posted_id))
        except Exception:
            log.critical("Emergency in-memory cache/recent update failed after confirmed regular post", exc_info=True)
        from mrs_bot_state_generation import record_receipt_commit
        record_receipt_commit(state, publication.attempt)
        persistence = emergency_persist_confirmed_regular_post(lines_used, images_used, state)
        failures = [*fallback_failures, *persistence.failures]
        if not confirmed_regular_emergency_representation_is_complete(
            post_id=str(posted_id),
            post_epoch=quote_post_epoch if quote_post_epoch is not _RECOVERY_VALUE_UNAVAILABLE else None,
            quote_hash=quote_hash,
            image_basename=image_basename,
            lines_used=lines_used,
            images_used=images_used,
            state=state,
            main_post_attempt=publication.attempt,
        ):
            failures.append("incomplete_regular_post_state")
        failure_text = ", ".join(failures) if failures else "receipt"
        if failures:
            durable_barrier = latch_confirmed_post_persistence_failure(
                lane="quote_image",
                post_id=str(posted_id),
                failure_components=["regular_post_receipt", *failures],
            )
            if durable_barrier or durable_remote_write_safety_barrier_exists():
                publication.release_guard()
            else:
                publication.retain_guard()
            raise UnrecoverableConfirmedPostPersistenceError(
                f"Confirmed regular quote/image post {posted_id} has no complete "
                f"durable recovery representation: {failure_text}"
            ) from receipt_exc
        status_after_fallback, _current_after_fallback = load_regular_post_receipt()
        if status_after_fallback == "sending":
            try:
                enqueue_historical_context_obligation(
                    context_obligation_receipt
                )
            except Exception as context_exc:
                log.critical(
                    "Confirmed regular quote/image post_id=%s has durable "
                    "protected state, but its historical-context disposition "
                    "could not be persisted; retaining the main-post attempt "
                    "receipt as a manual-reconciliation barrier",
                    posted_id,
                    exc_info=True,
                )
                publication.release_guard()
                raise ConfirmedPostLocalPersistenceError(
                    f"Confirmed regular quote/image post {posted_id} but failed "
                    "persisting its historical-context disposition; the durable "
                    "attempt receipt remains unresolved"
                ) from context_exc
            retire_lane_transport_journal_if_present(
                receipt_path=REGULAR_POST_RECEIPT_FILE,
                receipt=publication.attempt,
                lane="quote_image",
                post_id=str(posted_id),
                commit_proof=persistence.commit_proof,
            )
            remove_main_post_attempt(
                publication.attempt,
                sending_disposition="confirmed_state_fallback",
                commit_proof=persistence.commit_proof,
            )
        elif status_after_fallback != "valid":
            raise UnrecoverableConfirmedPostPersistenceError(
                f"Confirmed regular quote/image post {posted_id} has no stable "
                "receipt state after fallback persistence"
            ) from receipt_exc
        publication.release_guard()
        if not isinstance(receipt_exc, Exception):
            raise
        raise ConfirmedPostLocalPersistenceError(
            f"Confirmed regular quote/image post {posted_id} but failed local recovery receipt/persistence: {failure_text}"
        ) from receipt_exc

    publication.release_guard()

    _complete_quote_post(
        lines_used, images_used, state,
        quote_hash=quote_hash,
        image_basename=image_basename,
        posted_id=posted_id,
        quote_post_epoch=quote_post_epoch,
        quote_schedule_fields=quote_schedule_fields,
        meme_schedule_fields=meme_schedule_fields,
        tweet=tweet,
        receipt=receipt,
        line_no=line_no,
        image_no=image_no,
        image_choice=image_choice,
        canonical_quote_text=canonical_quote_text,
        log=log,
        cache_tweet=cache_tweet,
        MY_USER_ID=MY_USER_ID,
        record_recent_own_post=record_recent_own_post,
        save_regular_post_protected_state=save_regular_post_protected_state,
        log_event=log_event,
        enqueue_historical_context_obligation=enqueue_historical_context_obligation,
        retire_lane_transport_journal_if_present=retire_lane_transport_journal_if_present,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        remove_regular_post_receipt=remove_regular_post_receipt,
        ConfirmedPostLocalPersistenceError=ConfirmedPostLocalPersistenceError,
        emit_account_root_posted=emit_account_root_posted,
        safely_process_due_historical_context_obligations=safely_process_due_historical_context_obligations,
    )
