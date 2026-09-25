"""Own an ordinary quotation transaction from selection through completion.

The assembly binds stable collaborators for each runner. Caller histories,
prepared content and publication progress remain local to ``post``. The runner
keeps quote-specific rollback and emergency branches; ``MainPostPublication``
owns the durable send, and ``MainPostRecovery`` owns receipt completion. Imports
perform no runtime work or configuration access.
Preparation turns the selected image's generated source into the existing
``made_with_ai`` value shared by the attempt and requested payload.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import NamedTuple, TYPE_CHECKING

from mrs_bot_regular_post_completion import complete_regular_post_persistence
from mrs_bot_runtime_state_helpers import apply_state_fields


if TYPE_CHECKING:
    from mrs_bot_image_selection import ImageSelection
    from mrs_bot_main_post_publication import MainPostPublication
    from mrs_bot_main_post_receipt_storage import MainPostReceipts
    from mrs_bot_main_post_receipts import MainPostReceiptValues
    from mrs_bot_tweet_lookup_cache import TweetLookupCache
    from mrs_bot_state_generation import StateCommitProof
    from mrs_bot_main_post_reconciliation import MainPostRecovery
    from mrs_bot_main_post_assembly import (MainPostPolicy, MainPostErrors, MainPostTransport, MainPostApplication)


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


@dataclass(frozen=True)
class QuotePostRunner:
    """Own one ordinary quotation workflow with cycle-bound collaborators."""

    publication: MainPostPublication
    receipts: MainPostReceipts
    receipt_values: MainPostReceiptValues
    tweets: TweetLookupCache
    selection: ImageSelection
    recovery: MainPostRecovery
    policy: MainPostPolicy
    errors: MainPostErrors
    transport: MainPostTransport
    application: MainPostApplication
    build_attempt: Callable
    bound_meme_state: Callable
    remove_attempt: Callable

    def _select_pair(self, lines_used: set, images_used: set, state: dict) -> tuple[dict, dict]:
        """Select an ordinary pair with the bounded image-cycle fallbacks."""
        selection = self.selection
        log = self.application.log
        NoViableQuoteImagePair = self.errors.no_viable_quote_pair
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

    def _prepare(self, quote_choice: dict, image_choice: dict) -> _QuotePostPreparation:
        """Derive publication content and sample the two delays before upload."""
        log = self.application.log
        POST_SLEEP_MIN = self.policy.quote_delay_minimum
        POST_SLEEP_MAX = self.policy.quote_delay_maximum
        MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS = self.policy.meme_delay_minimum
        MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS = self.policy.meme_delay_maximum
        ENABLE_DAILY_MEME_POSTS = self.policy.enable_daily_memes
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

    def _complete_post(
        self, lines_used: set, images_used: set, state: dict,
        *, preparation: _QuotePostPreparation, posted_id: str,
        quote_post_epoch: int, quote_schedule_fields: dict,
        meme_schedule_fields: dict, receipt: dict, image_choice: dict,
    ) -> None:
        """Persist confirmed state and retire recovery authority before final events."""
        log = self.application.log
        tweets = self.tweets
        MY_USER_ID = self.policy.user_id
        ConfirmedPostLocalPersistenceError = self.errors.confirmed_local_failure
        log_event = self.application.log_event
        emit_account_root_posted = self.application.emit_account_root_posted
        safely_process_due_historical_context_obligations = self.application.process_due_context

        try:
            lines_used.add(preparation.quote_hash)
            images_used.add(preparation.image_basename)
            state["last_main_post_id"] = str(posted_id)
            state["last_quote_post_epoch"] = quote_post_epoch
            state["last_regular_image_filename"] = preparation.image_basename
            apply_state_fields(state, quote_schedule_fields)
            apply_state_fields(state, meme_schedule_fields)
            tweets.store(
                state,
                tweet_id=str(posted_id),
                text=preparation.tweet,
                author_id=str(MY_USER_ID),
                conversation_id=str(posted_id),
                referenced_tweets=[],
                post_type="quote",
            )
            tweets.record_recent_own_post(state, str(posted_id))
            self.recovery.complete_regular(
                lines_used, images_used, state, receipt, posted_id=str(posted_id),
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
            line_no=preparation.line_no,
            image_no=preparation.image_no,
            image_basename=preparation.image_basename,
            image_hash=image_choice.get("image_hash"),
            image_score=image_choice.get("score"),
            quote_hash=preparation.quote_hash,
        )
        emit_account_root_posted(
            lane="quote_image",
            post_id=posted_id,
            public_text=preparation.tweet,
            quote_id=preparation.quote_hash,
            quote_text=preparation.canonical_quote_text,
        )
        safely_process_due_historical_context_obligations(
            parent_post_id=str(posted_id),
            runtime_state=state,
        )
        log.info("Quote/image posted successfully. posted_id=%s", posted_id)



    def post(self, lines_used: set, images_used: set, state: dict) -> None:
        """Select and post one quotation-image pair transactionally."""
        publication = self.publication
        receipts = self.receipts
        receipt_values = self.receipt_values
        tweets = self.tweets
        selection = self.selection
        log = self.application.log
        block_if_ambiguous_remote_post = self.transport.block_if_ambiguous
        now_epoch = self.application.now_epoch
        reconcile_main_post_receipts = self.recovery.reconcile
        require_historical_context_outbox_writable = self.application.require_context_outbox_writable
        CorruptUsedHistoryError = self.errors.corrupt_used_history
        NoViableQuoteImagePair = self.errors.no_viable_quote_pair
        POST_SLEEP_MIN = self.policy.quote_delay_minimum
        POST_SLEEP_MAX = self.policy.quote_delay_maximum
        MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS = self.policy.meme_delay_minimum
        MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS = self.policy.meme_delay_maximum
        ENABLE_DAILY_MEME_POSTS = self.policy.enable_daily_memes
        upload_media = self.transport.upload_media
        build_main_post_attempt = self.build_attempt
        MEME_TRIGGER_AFTER_HOUR = self.policy.meme_trigger_after_hour
        MEME_SCHEDULE_VERSION = self.policy.schedule_version
        MAIN_POST_SCHEDULE_TIMEZONE = self.policy.schedule_timezone
        bound_meme_schedule_state = self.bound_meme_state
        remove_main_post_attempt = self.remove_attempt
        durable_remote_write_safety_barrier_exists = self.transport.durable_barrier_exists
        REGULAR_POST_RECEIPT_FILE = self.policy.regular_receipt_file
        ConfirmedPendingScheduleDurabilityUncertain = self.errors.confirmed_pending_uncertain
        ConfirmedPostLocalPersistenceError = self.errors.confirmed_local_failure
        MY_USER_ID = self.policy.user_id
        emergency_persist_confirmed_regular_post = self.recovery.emergency_regular
        confirmed_regular_emergency_representation_is_complete = self.recovery.regular_emergency_complete
        latch_confirmed_post_persistence_failure = self.transport.latch_confirmed_failure
        UnrecoverableConfirmedPostPersistenceError = self.errors.unrecoverable_confirmed
        enqueue_historical_context_obligation = self.application.enqueue_context
        retire_lane_transport_journal_if_present = self.application.retire_transport_journal
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

            quote_choice, image_choice = self._select_pair(lines_used, images_used, state)
            preparation = self._prepare(quote_choice, image_choice)

            media_id = upload_media(preparation.image, lane="quote_image")
            main_post_attempt = build_main_post_attempt(
                lane="quote_image",
                text=preparation.tweet,
                media_ids=[media_id],
                made_with_ai=preparation.image_made_with_ai,
                selected_identity={
                    "quote_hash": preparation.quote_hash,
                    "line_no": preparation.line_no,
                    "source_line_number": preparation.line_no + 1,
                    "image_basename": preparation.image_basename,
                    "image_no": preparation.image_no,
                },
                recovery_plan={
                    "quote_delay_seconds": preparation.quote_delay,
                    "meme_delay_seconds": preparation.meme_delay,
                    "meme_scheduling_enabled": bool(ENABLE_DAILY_MEME_POSTS),
                    "meme_trigger_after_hour": int(MEME_TRIGGER_AFTER_HOUR),
                    "meme_schedule_version": int(MEME_SCHEDULE_VERSION),
                    "schedule_timezone": MAIN_POST_SCHEDULE_TIMEZONE,
                    "meme_schedule_before": bound_meme_schedule_state(
                        state,
                        schedule_timezone=MAIN_POST_SCHEDULE_TIMEZONE,
                    ),
                    "quote_history_after": sorted(set(lines_used) | {preparation.quote_hash}),
                    "image_history_after": sorted(
                        set(images_used) | {preparation.image_basename}
                    ),
                },
                attempt_epoch=transaction_preflight_epoch,
            )
            publication.prepare(main_post_attempt)
            publication.begin_guard()
            posted_id = publication.send(
                text=preparation.tweet, media_id=media_id,
                made_with_ai=preparation.image_made_with_ai,
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
                "quote_hash": preparation.quote_hash,
                "quote_post_epoch": quote_post_epoch,
                "text": preparation.tweet,
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
                    fallback_receipt = receipt_values.current().materialize_regular(
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
                lines_used.add(preparation.quote_hash)
                images_used.add(preparation.image_basename)
                state["last_main_post_id"] = str(posted_id)
                if quote_post_epoch is not _RECOVERY_VALUE_UNAVAILABLE:
                    state["last_quote_post_epoch"] = quote_post_epoch
                state["last_regular_image_filename"] = preparation.image_basename
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
                tweets.store(
                    state,
                    tweet_id=str(posted_id),
                    text=preparation.tweet,
                    author_id=str(MY_USER_ID),
                    conversation_id=str(posted_id),
                    referenced_tweets=[],
                    post_type="quote",
                )
                tweets.record_recent_own_post(state, str(posted_id))
            except Exception:
                log.critical("Emergency in-memory cache/recent update failed after confirmed regular post", exc_info=True)
            from mrs_bot_state_generation import record_receipt_commit
            record_receipt_commit(state, publication.attempt)
            persistence = emergency_persist_confirmed_regular_post(lines_used, images_used, state)
            failures = [*fallback_failures, *persistence.failures]
            if not confirmed_regular_emergency_representation_is_complete(
                post_id=str(posted_id),
                post_epoch=quote_post_epoch if quote_post_epoch is not _RECOVERY_VALUE_UNAVAILABLE else None,
                quote_hash=preparation.quote_hash,
                image_basename=preparation.image_basename,
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
            status_after_fallback, _current_after_fallback = (
                receipts.current().load_regular()
            )
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

        self._complete_post(
            lines_used, images_used, state,
            preparation=preparation,
            posted_id=posted_id,
            quote_post_epoch=quote_post_epoch,
            quote_schedule_fields=quote_schedule_fields,
            meme_schedule_fields=meme_schedule_fields,
            receipt=receipt,
            image_choice=image_choice,
        )
