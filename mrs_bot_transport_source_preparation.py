"""Transport source preparation and final receipt gates.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from typing import Any
from pathlib import Path

from mrs_bot_post_creation import validate_media_upload_payload_metadata
from mrs_bot_main_post_attempt_values import main_post_attempt_payload
from mrs_bot_durable_json_io import canonical_atomic_json_bytes


def remote_write_transport_journal_paths(
    *,
    CONFIRMED_REPLY_RECEIPT_FILE: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    MEME_POST_RECEIPT_FILE: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
    journal_path_for_receipt: Any,
) -> tuple[Path, ...]:
    """Return every distinct transaction-journal path used by active lanes."""

    return tuple(
        sorted(
            {
                journal_path_for_receipt(path)
                for path in (
                    REGULAR_POST_RECEIPT_FILE,
                    MEME_POST_RECEIPT_FILE,
                    CONFIRMED_REPLY_RECEIPT_FILE,
                    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
                )
            },
            key=str,
        )
    )


def remote_source_receipt_paths(
    *,
    CONFIRMED_REPLY_RECEIPT_FILE: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    MEME_POST_RECEIPT_FILE: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
) -> tuple[Path, ...]:
    """Return the four current public-create source receipt paths."""

    return (
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
        CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )


def canonical_transport_receipt_path_for_lane(
    lane: str,
    *,
    CONFIRMED_REPLY_RECEIPT_FILE: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    MEME_POST_RECEIPT_FILE: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
) -> Path | None:
    """Return the only receipt pathname allowed to authorise one public lane."""

    return {
        "quote_image": REGULAR_POST_RECEIPT_FILE,
        "daily_meme": MEME_POST_RECEIPT_FILE,
        "conversational_reply": CONFIRMED_REPLY_RECEIPT_FILE,
        "historical_context_reply": HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    }.get(str(lane))


def _reply_payload_matches_receipt(
    receipt: dict,
    payload: dict,
    expected_keys: set[str],
) -> bool:
    """Match one validated reply receipt to its exact allowed tweet fields."""
    return (
        set(payload) == expected_keys
        and payload.get("text") == receipt.get("reply_text")
        and payload.get("reply")
        == {"in_reply_to_tweet_id": str(receipt.get("target_id"))}
    )


def transport_source_semantic_validator(
    lane: str,
    receipt: dict,
    payload: dict,
    *,
    main_post_attempt_binds_payload: Any,
    sending_reply_receipt_is_semantically_valid: Any,
) -> bool:
    """Prove that one lane-owned source receipt authorises one tweet body."""

    if lane in {"quote_image", "daily_meme"}:
        return bool(
            receipt.get("lane") == lane
            and receipt.get("lifecycle_state") == "attempting"
            and main_post_attempt_binds_payload(receipt, payload)
        )
    if lane == "conversational_reply":
        expected_keys = {"text", "reply"}
        if payload.get("made_with_ai") is True:
            expected_keys.add("made_with_ai")
        return bool(
            sending_reply_receipt_is_semantically_valid(receipt)
            and _reply_payload_matches_receipt(receipt, payload, expected_keys)
        )
    if lane == "historical_context_reply":
        from historical_context_formatter import HistoricalContextReplyStore

        return bool(
            HistoricalContextReplyStore._valid_sending_receipt(receipt)
            and set(payload) == {"text", "reply"}
            and payload.get("text") == receipt.get("reply_text")
            and payload.get("reply")
            == {"in_reply_to_tweet_id": str(receipt.get("parent_post_id"))}
        )
    return False


def _legacy_conversational_transport_source_semantic_validator(
    lane: str,
    receipt: dict,
    payload: dict,
    *,
    _legacy_sending_reply_receipt_is_semantically_valid: Any,
) -> bool:
    """Validate a frozen reply source solely for confirmed-journal recovery."""

    if lane != "conversational_reply":
        return False
    expected_keys = {"text", "reply"}
    if payload.get("made_with_ai") is True:
        expected_keys.add("made_with_ai")
    return bool(
        _legacy_sending_reply_receipt_is_semantically_valid(receipt)
        and _reply_payload_matches_receipt(receipt, payload, expected_keys)
    )


def bind_lane_transport_source(
    *,
    receipt_path: Path,
    receipt: dict,
    lane: str,
    payload: dict,
    TRANSPORT_SOURCE_VALIDATOR_ID: Any,
    bind_transport_source: Any,
    transport_source_semantic_validator: Any,
) -> SourceReceiptBinding:
    """Create the only accepted semantic source binding for a public tweet."""

    if lane == "historical_context_reply":
        from historical_context_formatter import (
            canonical_json_bytes as canonical_context_receipt_bytes,
        )

        expected_receipt_bytes = canonical_context_receipt_bytes(receipt)
    else:
        expected_receipt_bytes = canonical_atomic_json_bytes(receipt)

    return bind_transport_source(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        expected_receipt_bytes=expected_receipt_bytes,
        lane=lane,
        payload=payload,
        validator_id=TRANSPORT_SOURCE_VALIDATOR_ID,
        validator=transport_source_semantic_validator,
    )


def block_if_unrelated_receipt_appeared_for_tweet_transport(
    expected_receipt_path: Path,
    *,
    CONFIRMED_REPLY_RECEIPT_FILE: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    MEDIA_UPLOAD_RECEIPT_FILE: Any,
    MEME_POST_RECEIPT_FILE: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
    TransportJournalError: Any,
    media_upload_receipt_is_blocking: Any,
    receipt_namespace_entry_exists: Any,
    remote_receipt_retirement_is_blocking: Any,
) -> None:
    """Reject a lane which appeared after the transaction's initial preflight."""

    if remote_receipt_retirement_is_blocking():
        raise TransportJournalError(
            "a source-receipt retirement appeared before tweet transport"
        )

    for path in (
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
        CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ):
        if path == expected_receipt_path:
            continue
        try:
            receipt_present = receipt_namespace_entry_exists(path)
        except OSError as exc:
            raise TransportJournalError(
                "an unrelated receipt namespace could not be inspected "
                "before tweet transport"
            ) from exc
        if receipt_present:
            raise TransportJournalError(
                "an unrelated durable receipt appeared before tweet transport"
            )
    if media_upload_receipt_is_blocking(MEDIA_UPLOAD_RECEIPT_FILE):
        raise TransportJournalError(
            "an unresolved media receipt appeared before tweet transport"
        )


def block_if_unrelated_receipt_appeared_for_media_transport(
    *,
    CONFIRMED_REPLY_RECEIPT_FILE: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    MEME_POST_RECEIPT_FILE: Any,
    MediaUploadReceiptError: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
    receipt_namespace_entry_exists: Any,
    remote_receipt_retirement_is_blocking: Any,
    remote_write_transport_journal_is_blocking: Any,
) -> None:
    """Reject media transport if any other transaction owns remote writes."""

    if remote_receipt_retirement_is_blocking():
        raise MediaUploadReceiptError(
            "a source-receipt retirement appeared before media transport"
        )

    if remote_write_transport_journal_is_blocking():
        raise MediaUploadReceiptError(
            "a public-create transport journal appeared before media upload"
        )
    for path in (
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
        CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ):
        try:
            receipt_present = receipt_namespace_entry_exists(path)
        except OSError as exc:
            raise MediaUploadReceiptError(
                "an unrelated receipt namespace could not be inspected "
                "before media upload"
            ) from exc
        if receipt_present:
            raise MediaUploadReceiptError(
                "an unrelated durable receipt appeared before media upload"
            )


def prepare_main_tweet_transport(
    attempt: dict,
    *,
    TransportJournalError: Any,
    begin_transport_transaction: Any,
    bind_lane_transport_source: Any,
    current_main_post_attempt_is_semantically_valid: Any,
    main_post_attempt_path: Any,
    mark_main_post_attempt_attempting: Any,
) -> tuple[dict, SourceReceiptBinding, TransportAuthority]:
    """Publish a prepared tweet owner before retiring confirmed media state."""

    if (
        not current_main_post_attempt_is_semantically_valid(attempt)
        or attempt.get("lifecycle_state") != "sending"
    ):
        raise TransportJournalError(
            "only a current-schema sending main-post attempt may prepare transport"
        )
    attempting = mark_main_post_attempt_attempting(attempt)
    if (
        attempting.get("lifecycle_state") != "attempting"
        or not current_main_post_attempt_is_semantically_valid(attempting)
    ):
        raise TransportJournalError("main post attempt is not transport-ready")
    path = main_post_attempt_path(attempting)
    payload = main_post_attempt_payload(attempting)
    source = bind_lane_transport_source(
        receipt_path=path,
        receipt=attempting,
        lane=str(attempting["lane"]),
        payload=payload,
    )
    authority = begin_transport_transaction(
        receipt_path=path,
        source_binding=source,
    )
    attempt.clear()
    attempt.update(attempting)
    return attempt, source, authority


def validate_confirmed_media_upload_metadata(
    confirmation: ConfirmedMediaUpload,
    *,
    ConfirmedMediaUpload: Any,
    MediaUploadReceiptError: Any,
    inspect_media_upload_receipt: Any,
) -> None:
    """Validate the remote form bound to the exact confirmed media generation."""

    if not isinstance(confirmation, ConfirmedMediaUpload):
        raise MediaUploadReceiptError("confirmed media identity is invalid")
    snapshot = inspect_media_upload_receipt(Path(confirmation.receipt_path))
    if snapshot is None or (
        snapshot.device != confirmation.receipt_device
        or snapshot.inode != confirmation.receipt_inode
        or snapshot.ctime_ns != confirmation.receipt_ctime_ns
        or snapshot.sha256 != confirmation.receipt_sha256
        or snapshot.document.get("transaction_id")
        != confirmation.transaction_id
        or snapshot.document.get("lifecycle_state") != "confirmed"
        or snapshot.document.get("remote_media_id") != confirmation.media_id
    ):
        raise MediaUploadReceiptError(
            "confirmed media receipt changed before main-post handoff"
        )
    metadata = snapshot.document.get("payload_metadata")
    form = metadata.get("form") if isinstance(metadata, dict) else None
    if not isinstance(form, dict):
        raise MediaUploadReceiptError("confirmed media receipt form is invalid")
    try:
        validate_media_upload_payload_metadata(metadata, form=form)
    except (TypeError, ValueError) as exc:
        raise MediaUploadReceiptError(
            "confirmed media receipt payload authority is invalid"
        ) from exc
