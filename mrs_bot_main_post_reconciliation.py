"""Main-post receipt application, emergency completeness and local reconciliation.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from typing import Any

from mrs_bot_regular_post_completion import complete_regular_post_persistence

from mrs_bot_receipt_primitives import receipt_int, valid_post_id


def apply_meme_post_receipt(
    receipt: dict,
    state: dict,
    *,
    MEME_POST_TEXT: Any,
    MEME_SCHEDULE_VERSION: Any,
    MY_USER_ID: Any,
    cache_tweet: Any,
    log: Any,
    meme_schedule_date_str: Any,
    record_recent_own_post: Any,
) -> None:
    """Apply meme post receipt."""
    post_id = str(receipt["post_id"])
    meme_basename = str(receipt["meme_basename"])
    meme_post_epoch = int(receipt["meme_post_epoch"])
    next_meme_post_epoch = int(receipt["next_meme_post_epoch"])
    text = str(receipt.get("text", MEME_POST_TEXT))
    image_summary = str(receipt.get("image_summary") or "")

    last_quote_epoch = int(state.get("last_quote_post_epoch", 0) or 0)
    last_meme_epoch = int(state.get("last_meme_post_epoch", 0) or 0)
    last_main_post_id = str(state.get("last_main_post_id") or "")
    newest_known_main_epoch = max(last_quote_epoch, last_meme_epoch)
    receipt_is_newest_main = bool(
        meme_post_epoch > newest_known_main_epoch
        or (
            meme_post_epoch == newest_known_main_epoch
            and last_main_post_id in {"", post_id}
        )
    )
    if receipt_is_newest_main:
        state["last_main_post_id"] = post_id
    elif last_main_post_id != post_id:
        log.warning(
            "Meme receipt post_id=%s epoch=%s is older than known main-post "
            "state quote=%s meme=%s; preserving last_main_post_id=%s",
            post_id,
            meme_post_epoch,
            last_quote_epoch,
            last_meme_epoch,
            last_main_post_id,
        )
    posted = set(str(x) for x in state.get("posted_meme_filenames", []))
    posted.add(meme_basename)
    state["posted_meme_filenames"] = sorted(posted)
    current_next_meme_epoch = int(state.get("next_meme_post_epoch", 0) or 0)
    apply_bound_schedule = bool(
        meme_post_epoch > last_meme_epoch
        or (
            meme_post_epoch == last_meme_epoch
            and last_main_post_id in {"", post_id}
            and next_meme_post_epoch > current_next_meme_epoch
        )
    )
    if meme_post_epoch >= last_meme_epoch:
        state["last_meme_post_epoch"] = meme_post_epoch
    if apply_bound_schedule:
        state["next_meme_post_epoch"] = next_meme_post_epoch
        state["meme_schedule_version"] = (
            int(receipt["meme_schedule_version"])
            if receipt.get("schema_version") == 2
            else int(
                receipt.get("meme_schedule_version") or MEME_SCHEDULE_VERSION
            )
        )
        state["next_meme_schedule_mode"] = str(
            receipt.get("next_meme_schedule_mode") or "fallback"
        )
        source_attempt = receipt.get("source_attempt")
        if (
            isinstance(source_attempt, dict)
            and source_attempt.get("schema_version") == 5
        ):
            # Current receipts carry the date derived under their bound zone;
            # never reinterpret the epoch through this process's ambient TZ.
            state["next_meme_schedule_date"] = str(
                receipt["next_meme_schedule_date"]
            )
        else:
            state["next_meme_schedule_date"] = meme_schedule_date_str(
                next_meme_post_epoch
            )
        state["meme_anchor_quote_post_epoch"] = 0
    elif meme_post_epoch <= last_meme_epoch:
        log.warning(
            "Meme receipt post_id=%s epoch=%s is already reflected by newer "
            "meme state epoch=%s next=%s; preserving the current schedule",
            post_id,
            meme_post_epoch,
            last_meme_epoch,
            current_next_meme_epoch,
        )
    if receipt_is_newest_main:
        cache_tweet(
            state,
            tweet_id=post_id,
            text=text,
            author_id=str(MY_USER_ID),
            conversation_id=post_id,
            referenced_tweets=[],
            image_summary=image_summary,
            post_type="daily_meme",
        )
        record_recent_own_post(state, post_id)


def reconcile_meme_post_receipt(
    state: dict,
    *,
    InvalidMemePostReceipt: Any,
    MEME_POST_RECEIPT_FILE: Any,
    MEME_POST_TEXT: Any,
    apply_meme_post_receipt: Any,
    emit_account_root_posted: Any,
    finalize_confirmed_pending_schedule_receipt: Any,
    load_meme_post_receipt: Any,
    log: Any,
    remove_meme_post_receipt: Any,
    retire_lane_transport_journal_if_present: Any,
    save_state: Any,
    verify_lane_transport_source_lineage_if_present: Any,
) -> bool:
    """Reconcile a durable meme receipt without duplicating a remote post."""
    status, receipt = load_meme_post_receipt()
    if status == "absent":
        return False
    if status == "sending":
        log.critical(
            "A meme post was interrupted with an uncertain remote outcome; "
            "leaving its sending receipt as a global manual-reconciliation barrier"
        )
        return False
    if receipt is not None and status in {"pending_schedule", "valid"}:
        verify_lane_transport_source_lineage_if_present(
            receipt_path=MEME_POST_RECEIPT_FILE,
            receipt=receipt,
            lane="daily_meme",
            post_id=str(receipt.get("post_id") or ""),
        )
    if status == "pending_schedule" and receipt is not None:
        log.warning(
            "Finalising local schedule for already-confirmed meme post_id=%s",
            receipt.get("post_id"),
        )
        receipt = finalize_confirmed_pending_schedule_receipt(receipt)
        status = "valid"
    if status == "invalid" or receipt is None:
        raise InvalidMemePostReceipt(f"Invalid meme-post receipt blocks the bot: {MEME_POST_RECEIPT_FILE}")
    log.warning(
        "Reconciling confirmed meme post receipt post_id=%s meme=%s",
        receipt.get("post_id"),
        receipt.get("meme_basename"),
    )
    apply_meme_post_receipt(receipt, state)
    from mrs_bot_state_generation import record_receipt_commit
    record_receipt_commit(state, receipt)
    commit_proof = save_state(state, durable=True)
    retire_lane_transport_journal_if_present(
        commit_proof=commit_proof,
        receipt_path=MEME_POST_RECEIPT_FILE,
        receipt=receipt,
        lane="daily_meme",
        post_id=str(receipt["post_id"]),
    )
    remove_meme_post_receipt(receipt, commit_proof=commit_proof)
    emit_account_root_posted(
        lane="daily_meme",
        post_id=receipt["post_id"],
        public_text=receipt.get("text", MEME_POST_TEXT),
        image_summary=receipt.get("image_summary"),
    )
    return True


def apply_regular_post_receipt(
    receipt: dict,
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    MEME_SCHEDULE_VERSION: Any,
    MY_USER_ID: Any,
    cache_tweet: Any,
    log: Any,
    maybe_schedule_meme_after_quote_post: Any,
    meme_schedule_date_str: Any,
    record_recent_own_post: Any,
) -> None:
    """Apply regular post receipt."""
    post_id = str(receipt["post_id"])
    quote_hash = str(receipt["quote_hash"])
    image_basename = str(receipt["image_basename"])
    quote_post_epoch = int(receipt["quote_post_epoch"])
    next_quote_post_epoch = int(receipt["next_quote_post_epoch"])
    text = str(receipt.get("text") or "")

    last_quote_epoch = int(state.get("last_quote_post_epoch", 0) or 0)
    if receipt.get("schema_version") in {2, 3} and quote_post_epoch > last_quote_epoch:
        # Only a strictly newer receipt may install its exact post-cycle
        # snapshot.  Replaying an older snapshot after newer local state would
        # erase duplicate-suppression identities and could permit reuse.
        lines_used.clear()
        lines_used.update(str(value) for value in receipt["quote_history_after"])
        images_used.clear()
        images_used.update(str(value) for value in receipt["image_history_after"])
    elif receipt.get("schema_version") in {2, 3}:
        # Equal or stale replay is monotonic.  Unioning the receipt's identities
        # can conservatively delay reuse, but can never discard newer evidence.
        lines_used.update(str(value) for value in receipt["quote_history_after"])
        images_used.update(str(value) for value in receipt["image_history_after"])
    else:
        # Schema v1 did not preserve cycle-boundary resets.  Retain its
        # historical additive interpretation for backward compatibility.
        lines_used.add(quote_hash)
        images_used.add(image_basename)
    last_meme_epoch = int(state.get("last_meme_post_epoch", 0) or 0)
    newest_known_main_epoch = max(last_quote_epoch, last_meme_epoch)
    last_main_post_id = str(state.get("last_main_post_id") or "")
    receipt_is_newest_main = bool(
        quote_post_epoch > newest_known_main_epoch
        or (
            quote_post_epoch == newest_known_main_epoch
            and last_main_post_id in {"", post_id}
        )
    )
    if receipt_is_newest_main:
        state["last_main_post_id"] = post_id
    else:
        log.warning(
            "Receipt post_id=%s epoch=%s is older than known main-post state quote=%s meme=%s; not moving last_main_post_id backwards",
            post_id,
            quote_post_epoch,
            last_quote_epoch,
            last_meme_epoch,
        )
    if quote_post_epoch >= last_quote_epoch:
        state["last_quote_post_epoch"] = quote_post_epoch
        state["last_regular_image_filename"] = image_basename
        current_next_quote_post_epoch = int(state.get("next_quote_post_epoch", 0) or 0)
        if (
            quote_post_epoch == last_quote_epoch
            and current_next_quote_post_epoch > next_quote_post_epoch
        ):
            log.warning(
                "Receipt post_id=%s is already reflected with a newer quote schedule current=%s receipt=%s; preserving current schedule",
                post_id,
                current_next_quote_post_epoch,
                next_quote_post_epoch,
            )
        else:
            state["next_quote_post_epoch"] = next_quote_post_epoch
    if receipt_is_newest_main:
        if receipt.get("schema_version") in {2, 3}:
            # Current schema-v3 receipts carry the exact bound schedule and
            # its policy version. Schema v2 retains the earlier exact-time
            # interpretation with a backward-compatible version fallback.
            # Apply even an intentionally empty schedule verbatim; invoking
            # the legacy random helper here would make restart recovery differ
            # from the bound transaction.
            state["next_meme_post_epoch"] = int(
                receipt.get("next_meme_post_epoch", 0) or 0
            )
            state["meme_schedule_version"] = (
                int(receipt["meme_schedule_version"])
                if receipt.get("schema_version") in {3}
                else int(
                    receipt.get("meme_schedule_version")
                    or MEME_SCHEDULE_VERSION
                )
            )
            state["next_meme_schedule_mode"] = str(
                receipt.get("next_meme_schedule_mode") or ""
            )
            state["next_meme_schedule_date"] = str(
                receipt.get("next_meme_schedule_date") or ""
            )
            state["meme_anchor_quote_post_epoch"] = int(
                receipt.get("meme_anchor_quote_post_epoch") or 0
            )
        elif receipt.get("next_meme_post_epoch"):
            state["next_meme_post_epoch"] = int(receipt["next_meme_post_epoch"])
            state["meme_schedule_version"] = int(
                receipt.get("meme_schedule_version") or MEME_SCHEDULE_VERSION
            )
            state["next_meme_schedule_mode"] = str(receipt.get("next_meme_schedule_mode") or state.get("next_meme_schedule_mode") or "")
            state["next_meme_schedule_date"] = str(
                receipt.get("next_meme_schedule_date")
                or meme_schedule_date_str(int(receipt["next_meme_post_epoch"]))
            )
            state["meme_anchor_quote_post_epoch"] = int(receipt.get("meme_anchor_quote_post_epoch") or 0)
        else:
            maybe_schedule_meme_after_quote_post(state, quote_post_epoch, save=False)
    if text and receipt_is_newest_main:
        cache_tweet(
            state,
            tweet_id=post_id,
            text=text,
            author_id=str(MY_USER_ID),
            conversation_id=post_id,
            referenced_tweets=[],
            post_type="quote",
        )
    if receipt_is_newest_main:
        record_recent_own_post(state, post_id)


def confirmed_regular_emergency_representation_is_complete(
    *,
    post_id: str,
    post_epoch: int | None,
    quote_hash: str,
    image_basename: str,
    lines_used: set,
    images_used: set,
    state: dict,
    main_post_attempt: dict,
    build_confirmed_pending_schedule_receipt: Any,
    materialize_bound_regular_schedule_receipt: Any,
    valid_receipt_epoch: Any,
) -> bool:
    """Return whether fallback state exactly implements the pre-send plan."""
    state_post_epoch = receipt_int(state.get("last_quote_post_epoch"))
    try:
        pending = build_confirmed_pending_schedule_receipt(
            main_post_attempt,
            post_id=str(post_id),
            confirmation_epoch=int(post_epoch or 0),
        )
        expected = materialize_bound_regular_schedule_receipt(pending)
    except Exception:
        return False
    expected_meme_epoch = int(expected.get("next_meme_post_epoch", 0) or 0)
    return bool(
        valid_post_id(post_id)
        and post_epoch is not None
        and valid_receipt_epoch(post_epoch)
        and str(state.get("last_main_post_id") or "") == str(post_id)
        and lines_used == set(expected["quote_history_after"])
        and images_used == set(expected["image_history_after"])
        and quote_hash in lines_used
        and image_basename in images_used
        and state_post_epoch == post_epoch
        and receipt_int(state.get("next_quote_post_epoch"))
        == int(expected["next_quote_post_epoch"])
        and int(state.get("next_meme_post_epoch", 0) or 0)
        == expected_meme_epoch
        and int(state.get("meme_schedule_version", 0) or 0)
        == int(expected["meme_schedule_version"])
        and (
            not expected_meme_epoch
            or (
                str(state.get("next_meme_schedule_mode") or "")
                == str(expected["next_meme_schedule_mode"])
                and str(state.get("next_meme_schedule_date") or "")
                == str(expected["next_meme_schedule_date"])
                and int(state.get("meme_anchor_quote_post_epoch", 0) or 0)
                == int(expected["meme_anchor_quote_post_epoch"])
            )
        )
    )


def confirmed_meme_emergency_representation_is_complete(
    *,
    post_id: str,
    post_epoch: int | None,
    meme_basename: str,
    state: dict,
    main_post_attempt: dict,
    build_confirmed_pending_schedule_receipt: Any,
    materialize_bound_meme_schedule_receipt: Any,
    safe_bound_schedule_date_str: Any,
    valid_receipt_epoch: Any,
) -> bool:
    """Return whether fallback state exactly implements the pre-send plan."""
    state_post_epoch = receipt_int(state.get("last_meme_post_epoch"))
    posted = {str(item) for item in state.get("posted_meme_filenames", [])}
    image_summary = str(
        main_post_attempt.get("recovery_plan", {}).get("image_summary") or ""
    )
    try:
        pending = build_confirmed_pending_schedule_receipt(
            main_post_attempt,
            post_id=str(post_id),
            confirmation_epoch=int(post_epoch or 0),
            image_summary=image_summary,
        )
        expected = materialize_bound_meme_schedule_receipt(pending)
    except Exception:
        return False
    expected_next_epoch = int(expected["next_meme_post_epoch"])
    schedule_timezone = main_post_attempt.get("recovery_plan", {}).get(
        "schedule_timezone"
    )
    expected_post_date = safe_bound_schedule_date_str(
        int(post_epoch or 0),
        schedule_timezone,
    )
    expected_next_date = safe_bound_schedule_date_str(
        expected_next_epoch,
        schedule_timezone,
    )
    cached = state.get("tweet_cache", {}).get(str(post_id))
    return bool(
        valid_post_id(post_id)
        and post_epoch is not None
        and valid_receipt_epoch(post_epoch)
        and str(state.get("last_main_post_id") or "") == str(post_id)
        and meme_basename in posted
        and state_post_epoch == post_epoch
        and receipt_int(state.get("next_meme_post_epoch"))
        == expected_next_epoch
        and int(state.get("meme_schedule_version", 0) or 0)
        == int(expected["meme_schedule_version"])
        and expected_post_date is not None
        and expected_next_date is not None
        and expected_next_date > expected_post_date
        and str(state.get("next_meme_schedule_mode") or "")
        == str(expected["next_meme_schedule_mode"])
        and str(state.get("next_meme_schedule_date") or "")
        == expected_next_date
        and int(state.get("meme_anchor_quote_post_epoch", 0) or 0) == 0
        and isinstance(cached, dict)
        and str(cached.get("text") or "") == str(main_post_attempt["text"])
        and str(cached.get("post_type") or "") == "daily_meme"
        and str(cached.get("image_summary") or "") == image_summary
    )


def reconcile_regular_post_receipt(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    minimum_next_quote_epoch: int | None = None,
    process_auxiliary_context: bool = True,
    InvalidRegularPostReceipt: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
    apply_regular_post_receipt: Any,
    emit_account_root_posted: Any,
    enqueue_historical_context_obligation: Any,
    ensure_reconciled_regular_receipt_schedule_is_future: Any,
    finalize_confirmed_pending_schedule_receipt: Any,
    load_regular_post_receipt: Any,
    log: Any,
    remove_regular_post_receipt: Any,
    retire_lane_transport_journal_if_present: Any,
    safely_process_due_historical_context_obligations: Any,
    save_regular_post_protected_state: Any,
    verify_lane_transport_source_lineage_if_present: Any,
) -> bool:
    """Reconcile a durable regular-post receipt without duplicating a remote post."""
    status, receipt = load_regular_post_receipt()
    if status == "absent":
        return False
    if status == "sending":
        log.critical(
            "A regular post was interrupted with an uncertain remote outcome; "
            "leaving its sending receipt as a global manual-reconciliation barrier"
        )
        return False
    if receipt is not None and status in {"pending_schedule", "valid"}:
        verify_lane_transport_source_lineage_if_present(
            receipt_path=REGULAR_POST_RECEIPT_FILE,
            receipt=receipt,
            lane="quote_image",
            post_id=str(receipt.get("post_id") or ""),
        )
    if status == "pending_schedule" and receipt is not None:
        log.warning(
            "Finalising local schedule for already-confirmed regular post_id=%s",
            receipt.get("post_id"),
        )
        receipt = finalize_confirmed_pending_schedule_receipt(receipt)
        status = "valid"
    if status == "invalid" or receipt is None:
        raise InvalidRegularPostReceipt(f"Invalid regular-post receipt blocks main posting: {REGULAR_POST_RECEIPT_FILE}")
    log.warning(
        "Reconciling confirmed regular quote/image post receipt post_id=%s quote_hash=%s image=%s",
        receipt.get("post_id"),
        receipt.get("quote_hash"),
        receipt.get("image_basename"),
    )
    apply_regular_post_receipt(receipt, lines_used, images_used, state)
    if minimum_next_quote_epoch is not None:
        ensure_reconciled_regular_receipt_schedule_is_future(
            receipt,
            state,
            minimum_next_quote_epoch,
        )


    def retire_transport_journal(commit_proof) -> None:
        """Read recovered transport identity after the outbox is durable."""
        retire_lane_transport_journal_if_present(
            commit_proof=commit_proof,
            receipt_path=REGULAR_POST_RECEIPT_FILE,
            receipt=receipt,
            lane="quote_image",
            post_id=str(receipt["post_id"]),
        )

    complete_regular_post_persistence(
        lines_used, images_used, state, receipt,
        save_regular_post_protected_state=save_regular_post_protected_state,
        enqueue_historical_context_obligation=enqueue_historical_context_obligation,
        retire_transport_journal=retire_transport_journal,
        remove_regular_post_receipt=remove_regular_post_receipt,
    )
    emit_account_root_posted(
        lane="quote_image",
        post_id=receipt["post_id"],
        public_text=receipt.get("text"),
        quote_id=receipt.get("quote_hash"),
        quote_text=receipt.get("quote_text", receipt.get("text")),
    )
    log.info(
        "Confirmed main post reconciliation is complete; auxiliary context "
        "obligation is independent. post_id=%s",
        receipt["post_id"],
    )
    if process_auxiliary_context:
        safely_process_due_historical_context_obligations(
            parent_post_id=str(receipt["post_id"]),
            runtime_state=state,
        )
    return True


def reconcile_main_post_receipts(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    minimum_next_quote_epoch: int | None = None,
    process_auxiliary_context: bool = True,
    InvalidRegularPostReceipt: Any,
    MEME_POST_RECEIPT_FILE: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
    both_main_post_receipts_exist: Any,
    log: Any,
    reconcile_meme_post_receipt: Any,
    reconcile_regular_post_receipt: Any,
) -> dict[str, bool]:
    """Reconcile regular and meme receipts before any new main post."""
    if both_main_post_receipts_exist():
        log.critical(
            "Both regular and meme confirmed-post receipts exist; refusing automatic reconciliation until manually inspected: %s %s",
            REGULAR_POST_RECEIPT_FILE,
            MEME_POST_RECEIPT_FILE,
        )
        raise InvalidRegularPostReceipt("Both main-post receipts exist; manual recovery required")
    return {
        "regular": reconcile_regular_post_receipt(
            lines_used,
            images_used,
            state,
            minimum_next_quote_epoch=minimum_next_quote_epoch,
            process_auxiliary_context=process_auxiliary_context,
        ),
        "meme": reconcile_meme_post_receipt(state),
    }
