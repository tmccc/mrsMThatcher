"""Ordinary quotation posting orchestration, including experimental members.

The coordinator supplies current callbacks, settings, logger, exception/type
authority and the existing engagement-question module on every call. This owner
preserves the complete selection, publication and local recovery workflow;
transactions, transport, receipts, persistence and scheduling helpers remain in
the coordinator. Arguments and nested closure references are not copied by the
adapter. Imports perform no runtime work or configuration access, and callbacks
are never retained beyond the call. The standard-library random stream is shared.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from logging import Logger
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

from mrs_bot_regular_post_completion import complete_regular_post_persistence


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
    reserved_quote_hashes: set,
    *,
    log: Logger,
    choose_regular_quote_image_pair: Callable,
    NoViableQuoteImagePair: type[Exception],
) -> tuple[dict, dict]:
    """Select an ordinary pair with the existing bounded image-cycle fallbacks."""
    ordinary_selection_options = (
        {"excluded_quote_hashes": reserved_quote_hashes}
        if reserved_quote_hashes
        else {}
    )
    try:
        quote_choice, image_choice, attempts = choose_regular_quote_image_pair(
            lines_used,
            images_used,
            state,
            **ordinary_selection_options,
        )
    except NoViableQuoteImagePair as exc:
        log.warning(
            "No viable regular quote/image pair found within current image cycle after %d attempt(s); "
            "resetting image cycle and retrying once",
            exc.attempts,
        )
        try:
            quote_choice, image_choice, attempts = choose_regular_quote_image_pair(
                lines_used,
                images_used,
                state,
                force_image_cycle_reset=True,
                **ordinary_selection_options,
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
                quote_choice, image_choice, attempts = choose_regular_quote_image_pair(
                    lines_used,
                    images_used,
                    state,
                    force_image_cycle_reset=True,
                    avoid_last_image_at_cycle_boundary=False,
                    **ordinary_selection_options,
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


def _select_quote_image_pair(
    lines_used: set,
    images_used: set,
    state: dict,
    transaction_preflight_epoch: int,
    *,
    log: Logger,
    engagement_question_opportunity: Callable,
    load_engagement_question_runtime_plan: Callable,
    invalidate_engagement_question_experiment: Callable,
    engagement_question_trial: ModuleType,
    resolve_engagement_question_quote_choice: Callable,
    engagement_experiment_attempt_envelope_is_valid: Callable,
    choose_engagement_question_image: Callable,
    QuoteSpecificImageMismatch: type[Exception],
    defer_engagement_question_member: Callable,
    choose_regular_quote_image_pair: Callable,
    NoViableQuoteImagePair: type[Exception],
) -> tuple[dict, dict, str | None, dict | None]:
    """Try the planned experimental member, then ordinary selection if needed."""
    experiment_plan, experiment_member, reserved_quote_hashes = (
        engagement_question_opportunity(
            state,
            current_epoch=transaction_preflight_epoch,
        )
    )
    engagement_experiment_envelope: dict | None = None
    experimental_public_text: str | None = None
    quote_choice: dict | None = None
    image_choice: dict | None = None

    if experiment_member is not None and experiment_plan is not None:
        try:
            current_plan, catalogue, _quote_text_by_id = (
                load_engagement_question_runtime_plan()
            )
            if current_plan["plan_sha256"] != experiment_plan["plan_sha256"]:
                raise RuntimeError("active plan changed during opportunity")
        except Exception:
            invalidate_engagement_question_experiment(
                state,
                code="active_plan_changed_during_opportunity",
                recorded_epoch=transaction_preflight_epoch,
            )
            experiment_plan = None
            experiment_member = None
            reserved_quote_hashes = set()
        else:
            try:
                if str(experiment_member["quote_id"]) in lines_used:
                    raise engagement_question_trial.ExperimentValidationError(
                        "pending planned quotation is already in used history"
                    )
                quote_choice, experimental_public_text = (
                    resolve_engagement_question_quote_choice(
                        experiment_member,
                        catalogue=catalogue,
                    )
                )
                binding = engagement_question_trial.build_attempt_binding(
                    plan=experiment_plan,
                    state=state["engagement_question_experiment"],
                    member=experiment_member,
                    exact_quote_text=str(quote_choice["text"]),
                    public_text=experimental_public_text,
                )
                engagement_experiment_envelope = {
                    "binding": binding,
                    "canonical_quote_text": str(quote_choice["text"]),
                    "approved_question_body": str(
                        experiment_member["approved_question_body"]
                    ),
                    "complete_treatment_sha256": str(
                        experiment_member["complete_treatment_sha256"]
                    ),
                    "complete_treatment_weighted_length": int(
                        experiment_member[
                            "complete_treatment_weighted_length"
                        ]
                    ),
                }
                if not engagement_experiment_attempt_envelope_is_valid(
                    engagement_experiment_envelope,
                    public_text=experimental_public_text,
                    quote_hash=quote_choice["quote_hash"],
                    plan=experiment_plan,
                ):
                    raise engagement_question_trial.ExperimentValidationError(
                        "experimental pre-write envelope validation failed"
                    )
                image_choice = choose_engagement_question_image(
                    images_used,
                    quote_choice,
                    state,
                )
            except QuoteSpecificImageMismatch:
                defer_engagement_question_member(
                    state,
                    code="quote_specific_image_unavailable",
                    recorded_epoch=transaction_preflight_epoch,
                )
                quote_choice = None
                image_choice = None
                experimental_public_text = None
                engagement_experiment_envelope = None
            except engagement_question_trial.ExperimentValidationError:
                log.error(
                    "Experimental member failed immediate pre-post validation",
                    exc_info=True,
                )
                defer_engagement_question_member(
                    state,
                    code="immediate_member_validation_failed",
                    recorded_epoch=transaction_preflight_epoch,
                )
                quote_choice = None
                image_choice = None
                experimental_public_text = None
                engagement_experiment_envelope = None

    if quote_choice is None or image_choice is None:
        quote_choice, image_choice = _select_regular_quote_image_pair(
            lines_used, images_used, state, reserved_quote_hashes,
            log=log,
            choose_regular_quote_image_pair=choose_regular_quote_image_pair,
            NoViableQuoteImagePair=NoViableQuoteImagePair,
        )
    return quote_choice, image_choice, experimental_public_text, engagement_experiment_envelope


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
    experimental_public_text: str | None,
    engagement_experiment_envelope: dict | None,
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
    tweet = (
        experimental_public_text
        if engagement_experiment_envelope is not None
        else canonical_quote_text
    )
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
    apply_state_fields: Callable,
    cache_tweet: Callable,
    MY_USER_ID: str,
    record_recent_own_post: Callable,
    apply_confirmed_engagement_experiment_receipt: Callable,
    save_regular_post_protected_state: Callable,
    log_confirmed_engagement_experiment_receipt: Callable,
    engagement_experiment_envelope_from_receipt: Callable,
    log_event: Callable,
    engagement_experiment_event_fields: Callable,
    enqueue_historical_context_obligation: Callable,
    retire_lane_transport_journal_if_present: Callable,
    REGULAR_POST_RECEIPT_FILE: Path,
    remove_regular_post_receipt: Callable,
    publish_pending_engagement_question_notification: Callable,
    ConfirmedPostLocalPersistenceError: type[Exception],
    emit_account_root_posted: Callable,
    safely_process_due_historical_context_obligations: Callable,
) -> None:
    """Persist confirmed state and retire recovery authority before final events."""
    def emit_experiment_event() -> None:
        """Read live event fields after protected persistence and evidence logging."""
        if engagement_experiment_envelope_from_receipt(receipt) is not None:
            # Keep confirmed experiment evidence recoverable until after its
            # structured analytics event has been emitted.
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
                **engagement_experiment_event_fields(receipt),
            )

    def retire_transport_journal() -> None:
        """Retire live transport authority only after the outbox is durable."""
        retire_lane_transport_journal_if_present(
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
        apply_confirmed_engagement_experiment_receipt(receipt, state)
        complete_regular_post_persistence(
            lines_used, images_used, state, receipt,
            save_regular_post_protected_state=save_regular_post_protected_state,
            log_confirmed_engagement_experiment_receipt=log_confirmed_engagement_experiment_receipt,
            emit_experiment_event=emit_experiment_event,
            enqueue_historical_context_obligation=enqueue_historical_context_obligation,
            retire_transport_journal=retire_transport_journal,
            remove_regular_post_receipt=remove_regular_post_receipt,
            publish_pending_engagement_question_notification=publish_pending_engagement_question_notification,
        )
    except Exception as exc:
        log.critical("Confirmed regular quote/image post_id=%s but protected local persistence failed", posted_id, exc_info=True)
        raise ConfirmedPostLocalPersistenceError(
            f"Confirmed regular quote/image post {posted_id} but protected local persistence failed"
        ) from exc

    if engagement_experiment_envelope_from_receipt(receipt) is None:
        # Preserve the ordinary-post event path and fields byte-for-byte.
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
    log: Logger,
    block_if_ambiguous_remote_post: Callable,
    now_epoch: Callable,
    reconcile_main_post_receipts: Callable,
    ConfirmedPostSigintDeferral: type,
    require_historical_context_outbox_writable: Callable,
    quote_used_history_has_legacy_indices: Callable,
    CorruptUsedHistoryError: type[Exception],
    engagement_question_opportunity: Callable,
    load_engagement_question_runtime_plan: Callable,
    invalidate_engagement_question_experiment: Callable,
    engagement_question_trial: ModuleType,
    resolve_engagement_question_quote_choice: Callable,
    engagement_experiment_attempt_envelope_is_valid: Callable,
    choose_engagement_question_image: Callable,
    QuoteSpecificImageMismatch: type[Exception],
    defer_engagement_question_member: Callable,
    choose_regular_quote_image_pair: Callable,
    NoViableQuoteImagePair: type[Exception],
    POST_SLEEP_MIN: int,
    POST_SLEEP_MAX: int,
    MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS: int,
    MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS: int,
    ENABLE_DAILY_MEME_POSTS: bool,
    upload_media: Callable,
    revalidate_or_invalidate_engagement_question_publication: Callable,
    build_main_post_attempt: Callable,
    MEME_TRIGGER_AFTER_HOUR: int,
    MEME_SCHEDULE_VERSION: int,
    MAIN_POST_SCHEDULE_TIMEZONE: str,
    bound_meme_schedule_state: Callable,
    write_main_post_attempt: Callable,
    prepare_main_tweet_transport: Callable,
    handoff_confirmed_media_upload_to_main_attempt: Callable,
    begin_confirmed_post_sigint_deferral: Callable,
    create_post: Callable,
    valid_post_id: Callable,
    api_error_proves_remote_non_success: Callable,
    remove_main_post_attempt: Callable,
    end_confirmed_post_sigint_deferral: Callable,
    AmbiguousRemotePostOutcome: type[Exception],
    remote_write_safety_incident_is_latched: Callable,
    durable_remote_write_safety_barrier_exists: Callable,
    retain_sigint_deferral_without_durable_barrier: Callable,
    inspect_confirmed_transport_transaction: Callable,
    journal_path_for_receipt: Callable,
    REGULAR_POST_RECEIPT_FILE: Path,
    confirmation_epoch_for_main_attempt: Callable,
    build_confirmed_pending_schedule_receipt: Callable,
    promote_main_post_attempt_to_confirmed_pending_schedule: Callable,
    finalize_confirmed_pending_schedule_receipt: Callable,
    ConfirmedPendingScheduleDurabilityUncertain: type[Exception],
    ConfirmedPostLocalPersistenceError: type[Exception],
    materialize_bound_regular_schedule_receipt: Callable,
    apply_confirmed_engagement_experiment_receipt: Callable,
    apply_state_fields: Callable,
    cache_tweet: Callable,
    MY_USER_ID: str,
    record_recent_own_post: Callable,
    emergency_persist_confirmed_regular_post: Callable,
    confirmed_regular_emergency_representation_is_complete: Callable,
    latch_confirmed_post_persistence_failure: Callable,
    UnrecoverableConfirmedPostPersistenceError: type[Exception],
    load_regular_post_receipt: Callable,
    enqueue_historical_context_obligation: Callable,
    engagement_experiment_envelope_from_receipt: Callable,
    log_confirmed_engagement_experiment_receipt: Callable,
    log_event: Callable,
    engagement_experiment_event_fields: Callable,
    retire_lane_transport_journal_if_present: Callable,
    publish_pending_engagement_question_notification: Callable,
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
    confirmed_post_sigint_guard: ConfirmedPostSigintDeferral | None = None
    main_post_attempt = _RECOVERY_VALUE_UNAVAILABLE

    try:
        require_historical_context_outbox_writable()
        if quote_used_history_has_legacy_indices(lines_used):
            raise CorruptUsedHistoryError(
                "Quote used-history still contains legacy integer entries; refusing regular quote posting until source-verified migration is possible"
            )

        (
            quote_choice,
            image_choice,
            experimental_public_text,
            engagement_experiment_envelope,
        ) = _select_quote_image_pair(
            lines_used, images_used, state, transaction_preflight_epoch,
            log=log,
            engagement_question_opportunity=engagement_question_opportunity,
            load_engagement_question_runtime_plan=load_engagement_question_runtime_plan,
            invalidate_engagement_question_experiment=invalidate_engagement_question_experiment,
            engagement_question_trial=engagement_question_trial,
            resolve_engagement_question_quote_choice=resolve_engagement_question_quote_choice,
            engagement_experiment_attempt_envelope_is_valid=engagement_experiment_attempt_envelope_is_valid,
            choose_engagement_question_image=choose_engagement_question_image,
            QuoteSpecificImageMismatch=QuoteSpecificImageMismatch,
            defer_engagement_question_member=defer_engagement_question_member,
            choose_regular_quote_image_pair=choose_regular_quote_image_pair,
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
            quote_choice, image_choice, experimental_public_text,
            engagement_experiment_envelope,
            log=log,
            POST_SLEEP_MIN=POST_SLEEP_MIN,
            POST_SLEEP_MAX=POST_SLEEP_MAX,
            MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS=MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS,
            MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS=MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS,
            ENABLE_DAILY_MEME_POSTS=ENABLE_DAILY_MEME_POSTS,
        )

        if engagement_experiment_envelope is None:
            # Keep the ordinary path's call shape and receipt bytes unchanged.
            media_id = upload_media(image, lane="quote_image")
        else:
            def revalidate_experimental_root() -> None:
                revalidate_or_invalidate_engagement_question_publication(
                    state=state,
                    lines_used=lines_used,
                    envelope=engagement_experiment_envelope,
                    quote_choice=quote_choice,
                    public_text=tweet,
                )

            media_id = upload_media(
                image,
                lane="quote_image",
                engagement_experiment=engagement_experiment_envelope,
                pre_transport_validation=revalidate_experimental_root,
            )
            # The media receipt remains the durable barrier if an immutable
            # input changes after upload; no root transport is then prepared.
            revalidate_experimental_root()
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
            engagement_experiment=engagement_experiment_envelope,
        )
        write_main_post_attempt(main_post_attempt)
        if engagement_experiment_envelope is not None:
            # Recheck after the durable main owner exists and immediately
            # before preparing any root transport.  A concurrent immutable
            # input change leaves both existing receipts as barriers.
            revalidate_experimental_root()
        (
            main_post_attempt,
            transport_source,
            transport_authority,
        ) = prepare_main_tweet_transport(main_post_attempt)
        handoff_confirmed_media_upload_to_main_attempt(
            main_post_attempt,
            transport_authority,
        )
        confirmed_post_sigint_guard = begin_confirmed_post_sigint_deferral()
        response = create_post(
            text=tweet,
            media_ids=[media_id],
            reply_to_id=None,
            made_with_ai=image_made_with_ai,
            prepared_main_post_attempt=main_post_attempt,
            prepared_transport_authority=transport_authority,
            prepared_transport_source=transport_source,
        )
        posted_id = response.get("data", {}).get("id") if isinstance(response, dict) else None
        log.debug("Posted_id=%s", posted_id)

        if not valid_post_id(posted_id):
            raise RuntimeError("Quote/image post did not return a valid post id; used histories unchanged")
    except BaseException as remote_exc:
        lines_used.clear()
        lines_used.update(original_lines_used)
        images_used.clear()
        images_used.update(original_images_used)
        if (
            main_post_attempt is not _RECOVERY_VALUE_UNAVAILABLE
            and api_error_proves_remote_non_success(remote_exc)
        ):
            try:
                remove_main_post_attempt(
                    main_post_attempt,
                    sending_disposition="definite_non_success",
                )
            except BaseException as removal_exc:
                end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
                confirmed_post_sigint_guard = None
                raise AmbiguousRemotePostOutcome(
                    "A definitely unsuccessful regular post left its durable "
                    "sending receipt unresolved",
                    service="x",
                ) from removal_exc
        if (
            isinstance(remote_exc, AmbiguousRemotePostOutcome)
            and remote_write_safety_incident_is_latched()
            and not durable_remote_write_safety_barrier_exists()
        ):
            retain_sigint_deferral_without_durable_barrier(
                lane="quote_image",
                guard=confirmed_post_sigint_guard,
            )
        else:
            end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
            confirmed_post_sigint_guard = None
        raise

    quote_post_epoch = _RECOVERY_VALUE_UNAVAILABLE
    pending_schedule_receipt = _RECOVERY_VALUE_UNAVAILABLE
    fallback_receipt = _RECOVERY_VALUE_UNAVAILABLE
    quote_schedule_fields = _RECOVERY_VALUE_UNAVAILABLE
    meme_schedule_fields = _RECOVERY_VALUE_UNAVAILABLE
    pending_schedule_promoted = False
    try:
        transport_confirmation = inspect_confirmed_transport_transaction(
            journal_path_for_receipt(REGULAR_POST_RECEIPT_FILE)
        )
        if transport_confirmation.post_id != str(posted_id):
            raise AmbiguousRemotePostOutcome(
                "Confirmed regular-post identity differs from its journal",
                service="x",
            )
        quote_post_epoch = confirmation_epoch_for_main_attempt(
            main_post_attempt,
            transport_confirmation.confirmation_epoch,
        )
        context_obligation_receipt = {
            "post_id": str(posted_id),
            "quote_hash": quote_hash,
            "quote_post_epoch": quote_post_epoch,
            "text": tweet,
        }
        if engagement_experiment_envelope is not None:
            context_obligation_receipt["quote_text"] = canonical_quote_text
        pending_schedule_receipt = build_confirmed_pending_schedule_receipt(
            main_post_attempt,
            post_id=str(posted_id),
            confirmation_epoch=quote_post_epoch,
        )
        pending_schedule_receipt = (
            promote_main_post_attempt_to_confirmed_pending_schedule(
                main_post_attempt,
                post_id=str(posted_id),
                confirmation_epoch=quote_post_epoch,
            )
        )
        pending_schedule_promoted = True
        receipt = finalize_confirmed_pending_schedule_receipt(
            pending_schedule_receipt
        )
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
                end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
                confirmed_post_sigint_guard = None
            else:
                retain_sigint_deferral_without_durable_barrier(
                    lane="quote_image",
                    guard=confirmed_post_sigint_guard,
                )
            raise
        if pending_schedule_promoted:
            # The remote identity is already durable.  Leave this receipt in
            # place so startup/current-loop reconciliation retries only local
            # schedule materialisation and can never recreate the X post.
            end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
            confirmed_post_sigint_guard = None
            if not isinstance(receipt_exc, Exception):
                raise
            raise ConfirmedPostLocalPersistenceError(
                f"Confirmed regular quote/image post {posted_id}; its durable "
                "pending-schedule receipt remains for local-only reconciliation"
            ) from receipt_exc
        fallback_failures: list[str] = []
        if pending_schedule_receipt is not _RECOVERY_VALUE_UNAVAILABLE:
            try:
                fallback_receipt = materialize_bound_regular_schedule_receipt(
                    pending_schedule_receipt
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
            if fallback_receipt is not _RECOVERY_VALUE_UNAVAILABLE:
                apply_confirmed_engagement_experiment_receipt(
                    fallback_receipt,
                    state,
                )
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
        failures = [
            *fallback_failures,
            *emergency_persist_confirmed_regular_post(lines_used, images_used, state),
        ]
        if not confirmed_regular_emergency_representation_is_complete(
            post_id=str(posted_id),
            post_epoch=quote_post_epoch if quote_post_epoch is not _RECOVERY_VALUE_UNAVAILABLE else None,
            quote_hash=quote_hash,
            image_basename=image_basename,
            lines_used=lines_used,
            images_used=images_used,
            state=state,
            main_post_attempt=main_post_attempt,
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
                end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
                confirmed_post_sigint_guard = None
            else:
                retain_sigint_deferral_without_durable_barrier(
                    lane="quote_image",
                    guard=confirmed_post_sigint_guard,
                )
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
                end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
                confirmed_post_sigint_guard = None
                raise ConfirmedPostLocalPersistenceError(
                    f"Confirmed regular quote/image post {posted_id} but failed "
                    "persisting its historical-context disposition; the durable "
                    "attempt receipt remains unresolved"
                ) from context_exc
            if (
                fallback_receipt is not _RECOVERY_VALUE_UNAVAILABLE
                and engagement_experiment_envelope_from_receipt(
                    fallback_receipt
                )
                is not None
            ):
                # Protected state is now durable and the sending receipt still
                # exists as replay authority.  Emit the experiment evidence
                # before retiring that final authority, just as the ordinary
                # confirmed-receipt path does.
                log_confirmed_engagement_experiment_receipt(fallback_receipt)
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
                    **engagement_experiment_event_fields(fallback_receipt),
                )
            retire_lane_transport_journal_if_present(
                receipt_path=REGULAR_POST_RECEIPT_FILE,
                receipt=main_post_attempt,
                lane="quote_image",
                post_id=str(posted_id),
            )
            remove_main_post_attempt(
                main_post_attempt,
                sending_disposition="confirmed_state_fallback",
            )
            if (
                fallback_receipt is not _RECOVERY_VALUE_UNAVAILABLE
                and engagement_experiment_envelope_from_receipt(
                    fallback_receipt
                )
                is not None
            ):
                publish_pending_engagement_question_notification(state)
                emit_account_root_posted(
                    lane="quote_image",
                    post_id=posted_id,
                    public_text=tweet,
                    quote_id=quote_hash,
                    quote_text=canonical_quote_text,
                )
        elif status_after_fallback != "valid":
            raise UnrecoverableConfirmedPostPersistenceError(
                f"Confirmed regular quote/image post {posted_id} has no stable "
                "receipt state after fallback persistence"
            ) from receipt_exc
        end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
        confirmed_post_sigint_guard = None
        if not isinstance(receipt_exc, Exception):
            raise
        raise ConfirmedPostLocalPersistenceError(
            f"Confirmed regular quote/image post {posted_id} but failed local recovery receipt/persistence: {failure_text}"
        ) from receipt_exc

    end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
    confirmed_post_sigint_guard = None

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
        apply_state_fields=apply_state_fields,
        cache_tweet=cache_tweet,
        MY_USER_ID=MY_USER_ID,
        record_recent_own_post=record_recent_own_post,
        apply_confirmed_engagement_experiment_receipt=apply_confirmed_engagement_experiment_receipt,
        save_regular_post_protected_state=save_regular_post_protected_state,
        log_confirmed_engagement_experiment_receipt=log_confirmed_engagement_experiment_receipt,
        engagement_experiment_envelope_from_receipt=engagement_experiment_envelope_from_receipt,
        log_event=log_event,
        engagement_experiment_event_fields=engagement_experiment_event_fields,
        enqueue_historical_context_obligation=enqueue_historical_context_obligation,
        retire_lane_transport_journal_if_present=retire_lane_transport_journal_if_present,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        remove_regular_post_receipt=remove_regular_post_receipt,
        publish_pending_engagement_question_notification=publish_pending_engagement_question_notification,
        ConfirmedPostLocalPersistenceError=ConfirmedPostLocalPersistenceError,
        emit_account_root_posted=emit_account_root_posted,
        safely_process_due_historical_context_obligations=safely_process_due_historical_context_obligations,
    )
