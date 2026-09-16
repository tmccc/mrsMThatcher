"""Receipt-bound media upload and public-post creation.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def validate_media_upload_payload_metadata(
    value: object,
    *,
    form: dict[str, object],
    copy: Any,
) -> dict[str, object]:
    """Validate local receipt metadata against one exact remote media form."""

    base: dict[str, object] = {
        "request_method": "POST",
        "request_path": "/2/media/upload",
        "form": dict(form),
    }
    if not isinstance(value, dict):
        raise TypeError("media receipt payload metadata is not an object")
    if set(value) != set(base):
        raise ValueError("media receipt payload metadata fields are invalid")
    if any(value.get(field) != expected for field, expected in base.items()):
        raise ValueError("media receipt payload metadata changed its remote form")
    return copy.deepcopy(value)


def media_upload_payload_metadata(
    form: dict[str, object],
    *,
    validate_media_upload_payload_metadata: Any,
) -> dict[str, object]:
    """Bind the durable media receipt to its remote form."""

    metadata: dict[str, object] = {
        "request_method": "POST",
        "request_path": "/2/media/upload",
        "form": dict(form),
    }
    return validate_media_upload_payload_metadata(metadata, form=form)


def upload_media_v2(
    *,
    authority: MediaUploadAuthority,
    payload: ReceiptBoundMediaPayload,
    payload_metadata: dict[str, object] | None = None,
    AmbiguousRemotePostOutcome: Any,
    log: Any,
    validate_media_upload_payload_metadata: Any,
    x_request: Any,
) -> str:
    """Upload once through v2 and return its confirmed media identity."""
    log.info("Uploading receipt-bound media via X API v2: %s", payload.basename)
    files = {
        "media": (payload.basename, payload.data, payload.mime_type),
    }
    data = {
        "media_category": "tweet_image",
        "media_type": payload.mime_type,
    }
    if payload_metadata is None:
        result = x_request(
            "POST",
            "/2/media/upload",
            files=files,
            data=data,
            ambiguous_write=True,
            _remote_write_authorization=authority,
            _remote_media_payload=payload,
        )
    else:
        result = x_request(
            "POST",
            "/2/media/upload",
            files=files,
            data=data,
            ambiguous_write=True,
            _remote_write_authorization=authority,
            _remote_media_payload=payload,
            _remote_media_payload_metadata=validate_media_upload_payload_metadata(
                payload_metadata,
                form=data,
            ),
        )

    response_data = result.get("data") if isinstance(result, dict) else None
    raw_media_id = (
        response_data.get("id") if isinstance(response_data, dict) else None
    )
    if (
        isinstance(raw_media_id, bool)
        or not isinstance(raw_media_id, (str, int))
        or not str(raw_media_id).strip()
    ):
        raise AmbiguousRemotePostOutcome(
            "X may have accepted the v2 media upload but its response did not "
            "include a valid data.id",
            service="x",
            request_method="POST",
            request_path="/2/media/upload",
        )
    media_id = str(raw_media_id).strip()
    log.info("Uploaded media via v2. media_id=%s", media_id)
    return media_id


def upload_media(
    image_path: str,
    *,
    lane: str,
    AmbiguousRemotePostOutcome: Any,
    MEDIA_UPLOAD_RECEIPT_FILE: Any,
    MediaUploadReceiptError: Any,
    Path: Any,
    RemoteOperationsPaused: Any,
    abort_untransmitted_media_upload: Any,
    begin_confirmed_post_sigint_deferral: Any,
    begin_media_upload: Any,
    bind_media_upload_payload: Any,
    block_if_ambiguous_remote_post: Any,
    confirm_media_upload: Any,
    end_confirmed_post_sigint_deferral: Any,
    log: Any,
    media_upload_payload_metadata: Any,
    mimetypes: Any,
    record_ambiguous_remote_post: Any,
    require_remote_operation_unpaused: Any,
    transaction_mutation_authority: Any,
    upload_media_v2: Any,
) -> str:
    """Upload once under a restart-visible, image-bound sending receipt."""
    require_remote_operation_unpaused("X media upload")
    block_if_ambiguous_remote_post()
    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type:
        mime_type = "image/jpeg"
    form: dict[str, object] = {
        "media_category": "tweet_image",
        "media_type": mime_type,
    }
    payload_metadata = media_upload_payload_metadata(form)
    try:
        authority = begin_media_upload(
            receipt_path=MEDIA_UPLOAD_RECEIPT_FILE,
            image_path=Path(image_path),
            lane=lane,
            mime_type=mime_type,
            payload_metadata=payload_metadata,
        )
        bound_payload = bind_media_upload_payload(
            MEDIA_UPLOAD_RECEIPT_FILE,
            authority,
            image_path=Path(image_path),
            lane=lane,
            mime_type=mime_type,
            payload_metadata=payload_metadata,
        )
    except MediaUploadReceiptError as exc:
        raise AmbiguousRemotePostOutcome(
            "Could not establish the restart-persistent media-upload receipt",
            service="x",
            request_method="POST",
            request_path="/2/media/upload",
        ) from exc
    media_sigint_guard = begin_confirmed_post_sigint_deferral()
    try:
        try:
            media_id = upload_media_v2(
                authority=authority,
                payload=bound_payload,
            )
            confirm_media_upload(
                MEDIA_UPLOAD_RECEIPT_FILE,
                authority,
                mutation_authority=transaction_mutation_authority(
                    "media upload confirmation"
                ),
                media_id=media_id,
            )
            return media_id
        except RemoteOperationsPaused:
            # The final transport preflight runs before media authority is
            # consumed.  Only that exact local non-transmission proof may
            # retire this process-issued sending pair.
            try:
                abort_untransmitted_media_upload(
                    MEDIA_UPLOAD_RECEIPT_FILE,
                    authority,
                    mutation_authority=transaction_mutation_authority(
                        "untransmitted media-upload abort"
                    ),
                )
            except Exception as abort_exc:
                record_ambiguous_remote_post(
                    {"text": "", "media": {"media_ids": []}}
                )
                raise AmbiguousRemotePostOutcome(
                    "A locally paused media upload left an unresolved "
                    "media-upload barrier",
                    service="x",
                    request_method="POST",
                    request_path="/2/media/upload",
                ) from abort_exc
            raise
        except AmbiguousRemotePostOutcome:
            # The upload precedes the public-post sending receipt.  If its
            # response is lost, its media receipt and the ordinary incident
            # marker both suppress every later upload or public post.
            log.critical(
                "X media upload outcome is ambiguous; blocking every subsequent "
                "remote write pending manual reconciliation. image=%s",
                Path(image_path).name,
            )
            record_ambiguous_remote_post({"text": "", "media": {"media_ids": []}})
            raise
        except MediaUploadReceiptError as exc:
            record_ambiguous_remote_post({"text": "", "media": {"media_ids": []}})
            raise AmbiguousRemotePostOutcome(
                "X confirmed a media upload but its durable receipt could not be confirmed",
                service="x",
                request_method="POST",
                request_path="/2/media/upload",
            ) from exc
    finally:
        # The receipt is durable before this guard begins and remains the
        # restart barrier until confirmed media is handed to a tweet journal.
        end_confirmed_post_sigint_deferral(media_sigint_guard)


def create_post(
    text: str,
    media_ids: list[str] | None = None,
    reply_to_id: str | None = None,
    made_with_ai: bool = False,
    *,
    prepared_conversational_reply_receipt: dict | None = None,
    prepared_historical_context_reply_receipt: dict | None = None,
    prepared_main_post_attempt: dict | None = None,
    prepared_transport_authority: TransportAuthority | None = None,
    prepared_transport_source: SourceReceiptBinding | None = None,
    on_remote_transaction_started: Callable[[], None] | None = None,
    AmbiguousRemotePostOutcome: Any,
    CONFIRMED_REPLY_RECEIPT_FILE: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    Path: Any,
    ProvedRemotePostNonSuccess: Any,
    RemoteOperationsPaused: Any,
    TransportJournalError: Any,
    abort_untransmitted_transport_transaction: Any,
    arm_transport_transaction: Any,
    begin_transport_transaction: Any,
    bind_lane_transport_source: Any,
    block_if_ambiguous_remote_post: Any,
    block_if_remote_write_safety_incident_latched: Any,
    confirm_transport_transaction: Any,
    confirmation_epoch_after_remote_success: Any,
    current_main_post_attempt_is_semantically_valid: Any,
    freeze_tweet_request: Any,
    global_remote_writes_paused: Any,
    journal_path_for_receipt: Any,
    log: Any,
    main_post_attempt_binds_payload: Any,
    main_post_attempt_path: Any,
    mark_main_post_attempt_attempting: Any,
    record_ambiguous_remote_post: Any,
    require_instance_lock_for_remote_write: Any,
    retire_consumed_transport_transaction_after_proved_remote_non_success: Any,
    sending_reply_receipt_is_semantically_valid: Any,
    transaction_mutation_authority: Any,
    valid_post_id: Any,
    x_request: Any,
) -> dict:
    """Create an X post with transactional ambiguity handling."""
    prepared_receipt_count = sum(
        item is not None
        for item in (
            prepared_conversational_reply_receipt,
            prepared_historical_context_reply_receipt,
            prepared_main_post_attempt,
        )
    )
    if prepared_receipt_count != 1:
        # Preserve the strongest existing failure reason.  An unresolved
        # transaction or process-wide incident must remain visible as the
        # reason this unbound call cannot proceed; only a genuinely clean
        # process reports the missing transaction authority below.
        block_if_ambiguous_remote_post()
        raise AmbiguousRemotePostOutcome(
            "X post creation requires exactly one prepared durable transaction "
            "receipt",
            service="x",
        )
    if on_remote_transaction_started is not None and (
        prepared_historical_context_reply_receipt is None
        or not callable(on_remote_transaction_started)
    ):
        raise AmbiguousRemotePostOutcome(
            "Only a historical-context transaction may publish its remote "
            "phase through this callback",
            service="x",
        )
    if prepared_conversational_reply_receipt is not None:
        prepared = prepared_conversational_reply_receipt
        if (
            not sending_reply_receipt_is_semantically_valid(prepared)
            or str(prepared.get("target_id") or "") != str(reply_to_id or "")
            or str(prepared.get("reply_text") or "") != str(text)
            or bool(media_ids)
        ):
            raise AmbiguousRemotePostOutcome(
                "Prepared conversational-reply receipt does not exactly bind the "
                "requested remote write",
                service="x",
            )
    if prepared_main_post_attempt is not None:
        expected_lifecycle = (
            "attempting"
            if prepared_transport_authority is not None
            else "sending"
        )
        if (
            not current_main_post_attempt_is_semantically_valid(
                prepared_main_post_attempt
            )
            or prepared_main_post_attempt.get("lifecycle_state")
            != expected_lifecycle
            or reply_to_id is not None
            or not media_ids
        ):
            raise AmbiguousRemotePostOutcome(
                "Prepared main-post attempt is invalid for the requested remote "
                "write",
                service="x",
            )
    if prepared_historical_context_reply_receipt is not None:
        prepared = prepared_historical_context_reply_receipt
        from historical_context_formatter import HistoricalContextReplyStore

        if (
            not HistoricalContextReplyStore._valid_sending_receipt(prepared)
            or str(prepared.get("parent_post_id") or "") != str(reply_to_id or "")
            or str(prepared.get("reply_text") or "") != str(text)
            or bool(media_ids)
            or made_with_ai
        ):
            raise AmbiguousRemotePostOutcome(
                "Prepared historical-context receipt does not exactly bind the "
                "requested remote write",
                service="x",
            )
    block_if_ambiguous_remote_post(
        prepared_conversational_reply_receipt=(
            prepared_conversational_reply_receipt
        ),
        prepared_historical_context_reply_receipt=(
            prepared_historical_context_reply_receipt
        ),
        prepared_main_post_attempt=prepared_main_post_attempt,
        prepared_transport_authority=prepared_transport_authority,
    )
    payload: dict = {}

    if text:
        payload["text"] = text

    if media_ids:
        payload["media"] = {
            "media_ids": [str(x) for x in media_ids],
        }

    if reply_to_id:
        payload["reply"] = {
            "in_reply_to_tweet_id": str(reply_to_id),
        }

    if made_with_ai:
        payload["made_with_ai"] = True

    if not payload.get("text") and not payload.get("media"):
        raise ValueError("Cannot create X post without text or media")
    try:
        payload = freeze_tweet_request(
            method="POST",
            request_path="/2/tweets",
            payload=payload,
        ).payload()
    except TransportJournalError as exc:
        raise AmbiguousRemotePostOutcome(
            "X post creation payload is not an authorised immutable request",
            service="x",
            request_method="POST",
            request_path="/2/tweets",
        ) from exc
    if (
        prepared_main_post_attempt is not None
        and not main_post_attempt_binds_payload(
            prepared_main_post_attempt,
            payload,
        )
    ):
        raise AmbiguousRemotePostOutcome(
            "Prepared main-post attempt does not exactly bind the requested "
            "remote payload",
            service="x",
        )

    # The final transport helper repeats the pause and instance-lock checks.
    # Main-image lanes may already have published a ``prepared`` transport pair
    # so confirmed media can be handed off before this call.  That exact pair
    # is still provably untransmitted and must be retired before a local pause
    # is allowed to escape; otherwise a temporary maintenance pause strands the
    # source receipt and journal for manual recovery.
    require_instance_lock_for_remote_write("X post creation")
    block_if_remote_write_safety_incident_latched()
    if global_remote_writes_paused():
        if prepared_transport_authority is not None:
            try:
                if prepared_transport_source is None:
                    raise TransportJournalError(
                        "prepared transport authority has no semantic source"
                    )
                abort_untransmitted_transport_transaction(
                    source_binding=prepared_transport_source,
                    authority=prepared_transport_authority,
                    mutation_authority=transaction_mutation_authority(
                        "initially paused transport journal abort"
                    ),
                )
            except Exception as abort_exc:
                record_ambiguous_remote_post(payload)
                raise AmbiguousRemotePostOutcome(
                    "An initially paused tweet left an unresolved transport "
                    "barrier",
                    service="x",
                    request_method="POST",
                    request_path="/2/tweets",
                ) from abort_exc
        raise RemoteOperationsPaused(
            "Global runtime control pause blocks operation: X post creation"
        )

    if prepared_main_post_attempt is not None and prepared_transport_authority is None:
        attempting = mark_main_post_attempt_attempting(
            prepared_main_post_attempt
        )
        prepared_main_post_attempt.clear()
        prepared_main_post_attempt.update(attempting)

    if prepared_conversational_reply_receipt is not None:
        transaction_receipt_path = CONFIRMED_REPLY_RECEIPT_FILE
        transaction_receipt = prepared_conversational_reply_receipt
        transaction_lane = "conversational_reply"
    elif prepared_historical_context_reply_receipt is not None:
        transaction_receipt_path = HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        transaction_receipt = prepared_historical_context_reply_receipt
        transaction_lane = "historical_context_reply"
    else:
        assert prepared_main_post_attempt is not None
        transaction_receipt_path = main_post_attempt_path(
            prepared_main_post_attempt
        )
        transaction_receipt = prepared_main_post_attempt
        transaction_lane = str(prepared_main_post_attempt["lane"])

    try:
        if (prepared_transport_authority is None) != (
            prepared_transport_source is None
        ):
            raise TransportJournalError(
                "prepared transport authority and semantic source must be supplied together"
            )
        if prepared_transport_authority is None:
            transport_source = bind_lane_transport_source(
                receipt_path=transaction_receipt_path,
                receipt=transaction_receipt,
                lane=transaction_lane,
                payload=payload,
            )
            transport_authority = begin_transport_transaction(
                receipt_path=transaction_receipt_path,
                source_binding=transport_source,
            )
        else:
            transport_authority = prepared_transport_authority
            transport_source = prepared_transport_source
            assert transport_source is not None
            if (
                transport_authority.lifecycle_state != "prepared"
                or Path(transport_authority.journal_path)
                != journal_path_for_receipt(transaction_receipt_path).absolute()
                or transport_source.receipt_document != transaction_receipt
                or transport_source.request.payload() != payload
                or transport_source.lane != transaction_lane
            ):
                raise TransportJournalError(
                    "prepublished transport pair does not bind this transaction"
                )
        transport_authority = arm_transport_transaction(
            Path(transport_authority.journal_path),
            transport_authority,
            mutation_authority=transaction_mutation_authority(
                "transport journal arming"
            ),
        )
    except TransportJournalError as exc:
        record_ambiguous_remote_post(payload)
        raise AmbiguousRemotePostOutcome(
            "Could not establish the restart-persistent transport journal",
            service="x",
            request_method="POST",
            request_path="/2/tweets",
        ) from exc

    log.info(
        "Creating X post with durable transport journal. "
        "lane=%s transaction_id=%s reply_to_id=%s media_count=%d "
        "made_with_ai=%s text=%r",
        transaction_lane,
        transport_authority.transaction_id,
        reply_to_id,
        len(media_ids or []),
        made_with_ai,
        text,
    )

    def validate_created_post_response(result: dict) -> str:
        response_data = result.get("data") if isinstance(result, dict) else None
        post_id = response_data.get("id") if isinstance(response_data, dict) else None
        if not valid_post_id(post_id):
            raise AmbiguousRemotePostOutcome(
                f"X may have accepted the post but its response did not include a valid numeric data.id: {result}",
                service="x",
            )
        return str(post_id)

    try:
        if on_remote_transaction_started is not None:
            # The outbox phase is published only after the restart-persistent
            # transport pair is armed, and directly before the sole remote
            # request boundary.  If this durable callback fails, the armed
            # journal remains a global barrier and no request is attempted.
            on_remote_transaction_started()
        result = x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=transport_authority,
        )
        post_id = validate_created_post_response(result)
        confirm_transport_transaction(
            Path(transport_authority.journal_path),
            transport_authority,
            mutation_authority=transaction_mutation_authority(
                "transport journal confirmation"
            ),
            post_id=post_id,
            confirmation_epoch=confirmation_epoch_after_remote_success(
                transaction_receipt
            ),
        )
        log.info("Created X post successfully. response=%s", result)
        return result
    except ProvedRemotePostNonSuccess as remote_rejection:
        try:
            retire_consumed_transport_transaction_after_proved_remote_non_success(
                source_binding=transport_source,
                authority=transport_authority,
                remote_non_success_proof=(
                    remote_rejection.remote_non_success_proof
                ),
                mutation_authority=transaction_mutation_authority(
                    "proved remote non-success transport retirement"
                ),
            )
        except BaseException as retirement_error:
            # A rejected remote create is useful only if its exact consumed
            # transaction can also be retired without uncertainty.  Preserve
            # the source receipt and latch a durable global barrier whenever
            # either journal generation cannot be retired exactly.
            record_ambiguous_remote_post(payload)
            if not isinstance(retirement_error, Exception):
                raise
            raise AmbiguousRemotePostOutcome(
                "X rejected the reply create, but its consumed transport "
                "transaction could not be retired safely",
                service="x",
                request_method="POST",
                request_path="/2/tweets",
            ) from retirement_error
        raise
    except TransportJournalError as exc:
        record_ambiguous_remote_post(payload)
        raise AmbiguousRemotePostOutcome(
            "X returned a post identity but its durable transport journal "
            "could not be confirmed",
            service="x",
            request_method="POST",
            request_path="/2/tweets",
        ) from exc
    except RemoteOperationsPaused:
        try:
            abort_untransmitted_transport_transaction(
                source_binding=transport_source,
                authority=transport_authority,
                mutation_authority=transaction_mutation_authority(
                    "untransmitted transport journal abort"
                ),
            )
        except Exception as abort_exc:
            record_ambiguous_remote_post(payload)
            raise AmbiguousRemotePostOutcome(
                "A locally paused tweet left an unresolved transport barrier",
                service="x",
                request_method="POST",
                request_path="/2/tweets",
            ) from abort_exc
        raise
    except AmbiguousRemotePostOutcome:
        record_ambiguous_remote_post(payload)
        raise


def handoff_confirmed_media_upload_to_main_attempt(
    attempt: dict,
    transport_authority: TransportAuthority,
    *,
    MEDIA_UPLOAD_RECEIPT_FILE: Any,
    MediaUploadReceiptError: Any,
    Path: Any,
    bind_media_handoff_to_transport: Any,
    validate_confirmed_media_upload_metadata: Any,
    load_confirmed_media_upload: Any,
    log: Any,
    main_post_attempt_path: Any,
    retire_confirmed_media_upload: Any,
    transaction_mutation_authority: Any,
) -> None:
    """Retire media state only beneath an independent prepared tweet pair."""

    confirmation = load_confirmed_media_upload(MEDIA_UPLOAD_RECEIPT_FILE)
    if confirmation is None:
        raise MediaUploadReceiptError(
            "main-post attempt has no confirmed media-upload receipt"
        )
    validate_confirmed_media_upload_metadata(confirmation)
    path = main_post_attempt_path(attempt)
    handoff = bind_media_handoff_to_transport(
        MEDIA_UPLOAD_RECEIPT_FILE,
        confirmation,
        transport_journal_path=Path(transport_authority.journal_path),
        transport_fence_path=Path(transport_authority.fence_path),
        source_receipt_path=path,
    )
    retire_confirmed_media_upload(
        MEDIA_UPLOAD_RECEIPT_FILE,
        handoff,
        mutation_authority=transaction_mutation_authority(
            "confirmed media upload retirement"
        ),
    )
    log.warning(
        "Handed confirmed media upload to durable main-post attempt "
        "lane=%s attempt_id=%s media_id=%s",
        attempt["lane"],
        attempt["attempt_id"],
        confirmation.media_id,
    )
