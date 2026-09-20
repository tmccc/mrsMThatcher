"""Authenticated X request execution and response handling.

The root supplies current runtime dependencies and its collected request kwargs
explicitly on each call. Importing this module performs no runtime work and
retains no runtime authority.
"""
from __future__ import annotations

from typing import Any

from mrs_bot_post_creation import (
    media_upload_payload_metadata,
    validate_media_upload_payload_metadata,
)

from mrs_bot_x_response_diagnostics import raise_x_create_anomaly_outcome
from mrs_bot_request_route_values import exact_x_create_route


def x_request(
    method: str,
    path: str,
    *,
    ambiguous_write: bool = False,
    _remote_write_authorization: TransportAuthority | MediaUploadAuthority | None = None,
    _remote_media_payload: ReceiptBoundMediaPayload | None = None,
    _remote_media_payload_metadata: dict[str, object] | None = None,
    kwargs,
    AUTH: Any,
    AmbiguousRemotePostOutcome: Any,
    ApiError: Any,
    DeterministicReplyCreateRejectionProof: Any,
    MEDIA_UPLOAD_RECEIPT_FILE: Any,
    MediaUploadAuthority: Any,
    MediaUploadReceiptError: Any,
    Path: Any,
    ProvedRemotePostNonSuccess: Any,
    ReceiptBoundMediaPayload: Any,
    TransportAuthority: Any,
    TransportJournalError: Any,
    ValidatedXErrorResponse: Any,
    XErrorResponseValidationError: Any,
    _activate_coordinator_reply_create_rejection_proof: Any,
    _bind_transport_authority_to_configured_x_request: Any,
    block_if_unrelated_receipt_appeared_for_media_transport: Any,
    block_if_unrelated_receipt_appeared_for_tweet_transport: Any,
    canonical_transport_receipt_path_for_lane: Any,
    consume_media_upload_authority: Any,
    emit_x_create_response_anomaly: Any,
    frozen_strict_json_object: Any,
    invalidate_reply_create_rejection_proof: Any,
    json: Any,
    log: Any,
    log_json_debug: Any,
    parse_validated_x_error_response: Any,
    perform_consumed_x_request: Any,
    prepared_x_create_route: Any,
    print_rate_limit_headers: Any,
    report_bot_health_progress: Any,
    request_timeout: Any,
    requests: Any,
    require_remote_operation_unpaused: Any,
    sys: Any,
    x_create_response_anomaly_reason: Any,
    x_request_base_url: Any,
) -> dict:
    """Send an authenticated X API request without automatic retries."""
    url = f"{x_request_base_url(method, path)}{path}"
    prepared_create_route = prepared_x_create_route(method, path)
    exact_create_route = exact_x_create_route(method, path)
    if prepared_create_route != exact_create_route:
        raise AmbiguousRemotePostOutcome(
            "Prepared and literal X create-route classifications disagree",
            service="x",
            request_method=method,
            request_path=path,
        )
    is_post_create = prepared_create_route == "tweet"
    is_media_upload = prepared_create_route == "media"
    method_upper = str(method).upper()
    if (is_post_create or is_media_upload) and exact_create_route is None:
        raise AmbiguousRemotePostOutcome(
            "An X create route must use its exact literal method and path",
            service="x",
            request_method=method,
            request_path=path,
        )
    if method_upper not in {"GET", "HEAD", "OPTIONS"} and exact_create_route is None:
        raise AmbiguousRemotePostOutcome(
            "Generic or legacy X write routes have no durable transaction policy",
            service="x",
            request_method=method,
            request_path=path,
        )
    if ambiguous_write and exact_create_route is None:
        raise AmbiguousRemotePostOutcome(
            "Ambiguous-write handling is reserved for exact authorised create routes",
            service="x",
            request_method=method,
            request_path=path,
        )
    if isinstance(_remote_write_authorization, TransportAuthority) and not is_post_create:
        raise AmbiguousRemotePostOutcome(
            "A tweet-create transport authority cannot authorise another X endpoint",
            service="x",
            request_method=method,
            request_path=path,
        )
    if _remote_media_payload is not None and not is_media_upload:
        raise AmbiguousRemotePostOutcome(
            "A receipt-bound media body cannot authorise another X endpoint",
            service="x",
            request_method=method,
            request_path=path,
        )
    if _remote_media_payload_metadata is not None and not is_media_upload:
        raise AmbiguousRemotePostOutcome(
            "Media receipt metadata cannot authorise another X endpoint",
            service="x",
            request_method=method,
            request_path=path,
        )
    if isinstance(_remote_write_authorization, MediaUploadAuthority) and not is_media_upload:
        raise AmbiguousRemotePostOutcome(
            "A media-upload authority cannot authorise another X endpoint",
            service="x",
            request_method=method,
            request_path=path,
        )
    if _remote_write_authorization is not None and not isinstance(
        _remote_write_authorization,
        (TransportAuthority, MediaUploadAuthority),
    ):
        raise AmbiguousRemotePostOutcome(
            "X write authority has an unsupported type",
            service="x",
            request_method=method,
            request_path=path,
        )
    if is_post_create and (
        not isinstance(_remote_write_authorization, TransportAuthority)
        or not ambiguous_write
    ):
        raise AmbiguousRemotePostOutcome(
            "X post creation requires an exact durable transport-journal "
            "authorization and ambiguous-write handling",
            service="x",
            request_method=method,
            request_path=path,
        )

    expected_receipt_path: Path | None = None
    if is_post_create:
        if set(kwargs) != {"json"}:
            raise AmbiguousRemotePostOutcome(
                "X post creation accepts only one exact JSON body and no alternate "
                "body, query, header, file or redirect channel",
                service="x",
                request_method=method,
                request_path=path,
            )
        kwargs["json"] = frozen_strict_json_object(
            kwargs["json"],
            label="X post creation payload",
        )

    if is_media_upload and set(kwargs) != {"data", "files"}:
        raise AmbiguousRemotePostOutcome(
            "X media creation accepts only its exact form and media part",
            service="x",
            request_method=method,
            request_path=path,
        )

    log.debug("X request: %s %s", method, url)

    if "params" in kwargs:
        log_json_debug("X request params", kwargs["params"])

    if "json" in kwargs:
        log_json_debug("X request json", kwargs["json"])

    if "data" in kwargs:
        log_json_debug("X request form data", kwargs["data"])

    if "files" in kwargs:
        log.debug("X request includes files: %s", list(kwargs["files"].keys()))

    if method_upper not in {"GET", "HEAD", "OPTIONS"}:
        require_remote_operation_unpaused(
            f"X {method.upper()} {path}",
            transaction_authorization=_remote_write_authorization,
        )

    if ambiguous_write:
        # A redirect can turn one create into an untracked follow-up request.
        # Writes are therefore single-hop unless a future provider contract
        # explicitly proves a redirect safe.
        kwargs["allow_redirects"] = False

    if is_post_create:
        payload = kwargs.get("json")
        if not isinstance(payload, dict):
            raise AmbiguousRemotePostOutcome(
                "X post creation requires one exact JSON payload",
                service="x",
                request_method=method,
                request_path=path,
            )
        try:
            expected_receipt_path = canonical_transport_receipt_path_for_lane(
                _remote_write_authorization.lane
            )
            if expected_receipt_path is None:
                raise TransportJournalError(
                    "transport authority lane is not a canonical public-create lane"
                )
            block_if_unrelated_receipt_appeared_for_tweet_transport(
                expected_receipt_path
            )
        except TransportJournalError as exc:
            raise AmbiguousRemotePostOutcome(
                "X post creation lost its exact durable transport authority",
                service="x",
                request_method=method,
                request_path=path,
            ) from exc

    if is_media_upload and (
        not isinstance(_remote_write_authorization, MediaUploadAuthority)
        or not ambiguous_write
    ):
        raise AmbiguousRemotePostOutcome(
            "X media upload requires exact durable media authority and explicit "
            "ambiguous-write handling",
            service="x",
            request_method=method,
            request_path=path,
        )
    if is_media_upload:
        files = kwargs.get("files")
        form = kwargs.get("data")
        media_part = files.get("media") if isinstance(files, dict) else None
        if (
            not isinstance(media_part, tuple)
            or len(media_part) != 3
            or not isinstance(media_part[0], str)
            or type(media_part[1]) is not bytes
            or not isinstance(media_part[2], str)
            or not isinstance(form, dict)
            or not isinstance(_remote_media_payload, ReceiptBoundMediaPayload)
            or media_part
            != (
                _remote_media_payload.basename,
                _remote_media_payload.data,
                _remote_media_payload.mime_type,
            )
        ):
            raise AmbiguousRemotePostOutcome(
                "X media upload request is not an exact bound multipart payload",
                service="x",
                request_method=method,
                request_path=path,
            )
        try:
            receipt_payload_metadata = (
                media_upload_payload_metadata(form)
                if _remote_media_payload_metadata is None
                else validate_media_upload_payload_metadata(
                    _remote_media_payload_metadata,
                    form=form,
                )
            )
        except (TypeError, ValueError) as exc:
            raise AmbiguousRemotePostOutcome(
                "X media upload receipt metadata does not match its exact form",
                service="x",
                request_method=method,
                request_path=path,
            ) from exc
        try:
            block_if_unrelated_receipt_appeared_for_media_transport()
            consume_media_upload_authority(
                MEDIA_UPLOAD_RECEIPT_FILE,
                _remote_write_authorization,
                payload=_remote_media_payload,
                lane=_remote_write_authorization.lane,
                mime_type=media_part[2],
                payload_metadata=receipt_payload_metadata,
            )
        except MediaUploadReceiptError as exc:
            raise AmbiguousRemotePostOutcome(
                "X media upload lost its exact durable transport authority",
                service="x",
                request_method=method,
                request_path=path,
            ) from exc

    validated_error_response: ValidatedXErrorResponse | None = None
    rejection_proof: DeterministicReplyCreateRejectionProof | None = None
    read_only_request = method_upper in {"GET", "HEAD", "OPTIONS"}
    if read_only_request:
        report_bot_health_progress("x_read")
    try:
        if is_post_create:
            if expected_receipt_path is None:
                raise TransportJournalError(
                    "tweet transport has no canonical source receipt"
                )
            (
                response,
                coordinated_validated_error,
                coordinated_rejection_proof,
            ) = perform_consumed_x_request(
                Path(_remote_write_authorization.journal_path),
                _remote_write_authorization,
                request_authority=(
                    _bind_transport_authority_to_configured_x_request(
                        _remote_write_authorization,
                        payload=kwargs["json"],
                    )
                ),
                payload=kwargs["json"],
                expected_receipt_path=expected_receipt_path,
                request_kwargs=kwargs,
            )
            if isinstance(
                coordinated_validated_error,
                ValidatedXErrorResponse,
            ):
                validated_error_response = coordinated_validated_error
            if isinstance(
                coordinated_rejection_proof,
                DeterministicReplyCreateRejectionProof,
            ):
                rejection_proof = coordinated_rejection_proof
        else:
            response = requests.request(
                method,
                url,
                auth=AUTH,
                timeout=request_timeout(),
                **kwargs,
            )
    except TransportJournalError as e:
        log.exception("X request lost its exact transport authority")
        raise AmbiguousRemotePostOutcome(
            "X post creation lost its exact durable transport authority",
            service="x",
            status_code=getattr(e, "status_code", None),
            request_method=method,
            request_path=path,
        ) from e
    except requests.RequestException as e:
        log.exception("X request failed before receiving response")
        if ambiguous_write:
            raise AmbiguousRemotePostOutcome(
                str(e),
                service="x",
                request_method=method,
                request_path=path,
            ) from e
        raise ApiError(
            str(e),
            service="x",
            request_method=method,
            request_path=path,
        ) from e
    if read_only_request:
        report_bot_health_progress("x_read")

    def process_received_response() -> dict:
        nonlocal rejection_proof, validated_error_response

        log.debug("X response status: %s", response.status_code)
        log.debug(
            "X response headers: x-rate-limit-limit=%s remaining=%s reset=%s",
            response.headers.get("x-rate-limit-limit"),
            response.headers.get("x-rate-limit-remaining"),
            response.headers.get("x-rate-limit-reset"),
        )

        if not 200 <= response.status_code < 300:
            log.error("X API error %s: %s", response.status_code, response.text)
            reset_epoch = print_rate_limit_headers(response)
            if validated_error_response is None:
                try:
                    validated_error_response = parse_validated_x_error_response(
                        getattr(response, "content", None),
                        status_code=response.status_code,
                    )
                except XErrorResponseValidationError:
                    log.warning(
                        "X API non-success body was not one strict validated "
                        "error object; it cannot prove remote non-success",
                        exc_info=True,
                    )

            api_error = ApiError(
                f"X API error {response.status_code}: {response.text}",
                service="x",
                status_code=response.status_code,
                reset_epoch=reset_epoch,
                request_method=method,
                request_path=path,
                x_error_response=validated_error_response,
                x_error_message_fallback=not ambiguous_write,
            )

            if ambiguous_write:
                if rejection_proof is not None:
                    if not _activate_coordinator_reply_create_rejection_proof(
                        rejection_proof,
                        authority=_remote_write_authorization,
                    ):
                        invalidate_reply_create_rejection_proof(rejection_proof)
                        rejection_proof = None
                    else:
                        raise ProvedRemotePostNonSuccess(
                            str(api_error),
                            service="x",
                            status_code=response.status_code,
                            reset_epoch=reset_epoch,
                            request_method=method,
                            request_path=path,
                            x_error_response=validated_error_response,
                            x_error_message_fallback=False,
                            remote_non_success_proof=rejection_proof,
                        )
                raise AmbiguousRemotePostOutcome(
                    "X write outcome is not proved by HTTP status alone; "
                    f"received HTTP {response.status_code}: {response.text}",
                    service="x",
                    status_code=response.status_code,
                    reset_epoch=reset_epoch,
                    request_method=method,
                    request_path=path,
                    x_error_message_fallback=False,
                )

            raise api_error

        raw_create_body: bytes | None = None
        if is_post_create:
            # Requests has already populated its cached, decompressed content
            # for the non-streaming request.  This bytes() call cannot trigger
            # another network read.
            raw_create_body = bytes(response.content)

        def raise_create_response_anomaly(
            reason: str,
            *,
            json_decode_succeeded: bool,
            decoded: object,
            json_error: json.JSONDecodeError | None = None,
        ) -> None:
            if (
                raw_create_body is None
                or not isinstance(_remote_write_authorization, TransportAuthority)
            ):
                raise RuntimeError(
                    "tweet-create anomaly evidence requires its exact response "
                    "and transport authority"
                )
            diagnostic = emit_x_create_response_anomaly(
                response=response,
                transport_authority=_remote_write_authorization,
                request_payload=kwargs["json"],
                reason=reason,
                raw_body=raw_create_body,
                json_decode_succeeded=json_decode_succeeded,
                decoded=decoded,
                json_error=json_error,
            )
            raise_x_create_anomaly_outcome(
                reason,
                diagnostic=diagnostic,
                response=response,
                decoded=decoded,
                request_method=method,
                request_path=path,
                ambiguous_outcome=AmbiguousRemotePostOutcome,
                json_error=json_error,
            )

        if is_post_create and not raw_create_body:
            log.debug("X tweet-create response has empty body")
            raise_create_response_anomaly(
                "empty_response_body",
                json_decode_succeeded=False,
                decoded=None,
            )
        if not is_post_create and not response.text:
            log.debug("X response has empty body")
            return {}

        try:
            data = response.json()
        except json.JSONDecodeError as e:
            if is_post_create:
                raise_create_response_anomaly(
                    "json_decode_error",
                    json_decode_succeeded=False,
                    decoded=None,
                    json_error=e,
                )
            log.error(
                "X API returned non-JSON response: %s",
                response.text[:1000],
            )
            if ambiguous_write:
                raise AmbiguousRemotePostOutcome(
                    "X may have accepted the write but returned a non-JSON "
                    f"response: {response.text[:500]}",
                    service="x",
                    request_method=method,
                    request_path=path,
                ) from e
            raise ApiError(
                f"X API returned non-JSON response: {response.text[:500]}",
                service="x",
                request_method=method,
                request_path=path,
            ) from e
        if not isinstance(data, dict):
            if is_post_create:
                raise_create_response_anomaly(
                    "decoded_top_level_not_object",
                    json_decode_succeeded=True,
                    decoded=data,
                )
            message = (
                "X API response must be a JSON object, got "
                f"{type(data).__name__}"
            )
            if ambiguous_write:
                raise AmbiguousRemotePostOutcome(
                    "X may have accepted the write but its response was not "
                    f"a JSON object: {type(data).__name__}",
                    service="x",
                    request_method=method,
                    request_path=path,
                )
            raise ApiError(
                message,
                service="x",
                request_method=method,
                request_path=path,
            )

        if is_post_create:
            anomaly_reason = x_create_response_anomaly_reason(data)
            if anomaly_reason is not None:
                raise_create_response_anomaly(
                    anomaly_reason,
                    json_decode_succeeded=True,
                    decoded=data,
                )

        log_json_debug("X response json", data)
        return data

    try:
        return process_received_response()
    finally:
        escaping_error = sys.exc_info()[1]
        if rejection_proof is not None:
            preserve_proof = False
            try:
                preserve_proof = bool(
                    isinstance(
                        escaping_error,
                        ProvedRemotePostNonSuccess,
                    )
                    and getattr(
                        escaping_error,
                        "remote_non_success_proof",
                        None,
                    )
                    is rejection_proof
                )
            except BaseException:
                preserve_proof = False
            if not preserve_proof:
                invalidate_reply_create_rejection_proof(rejection_proof)


def x_bearer_request(
    method: str,
    path: str,
    *,
    kwargs,
    AmbiguousRemotePostOutcome: Any,
    ApiError: Any,
    X_BASE: Any,
    X_BEARER_TOKEN: Any,
    json: Any,
    log: Any,
    log_json_debug: Any,
    print_rate_limit_headers: Any,
    report_bot_health_progress: Any,
    request_timeout: Any,
    requests: Any,
) -> dict:
    """Send a bearer-authenticated X API request without automatic retries."""
    if str(method).upper() not in {"GET", "HEAD", "OPTIONS"}:
        raise AmbiguousRemotePostOutcome(
            "Bearer-authenticated X writes have no durable transaction authority",
            service="x",
            request_method=method,
            request_path=path,
        )
    if not X_BEARER_TOKEN:
        raise ApiError("X_BEARER_TOKEN is not set", service="x")

    url = f"{X_BASE}{path}"

    log.debug("X bearer request: %s %s", method, url)

    if "params" in kwargs:
        log_json_debug("X bearer request params", kwargs["params"])

    report_bot_health_progress("x_read")
    try:
        response = requests.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {X_BEARER_TOKEN}",
            },
            timeout=request_timeout(),
            **kwargs,
        )
    except requests.RequestException as e:
        log.exception("X bearer request failed before receiving response")
        raise ApiError(
            str(e),
            service="x",
            request_method=method,
            request_path=path,
        ) from e
    finally:
        report_bot_health_progress("x_read")

    log.debug("X bearer response status: %s", response.status_code)
    log.debug(
        "X bearer response headers: x-rate-limit-limit=%s remaining=%s reset=%s",
        response.headers.get("x-rate-limit-limit"),
        response.headers.get("x-rate-limit-remaining"),
        response.headers.get("x-rate-limit-reset"),
    )

    if response.status_code >= 400:
        log.error("X bearer API error %s: %s", response.status_code, response.text)
        reset_epoch = print_rate_limit_headers(response)

        raise ApiError(
            f"X bearer API error {response.status_code}: {response.text}",
            service="x",
            status_code=response.status_code,
            reset_epoch=reset_epoch,
            request_method=method,
            request_path=path,
        )

    if not response.text:
        return {}

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        log.error("X bearer API returned non-JSON response: %s", response.text[:1000])
        raise ApiError(
            f"X bearer API returned non-JSON response: {response.text[:500]}",
            service="x",
            request_method=method,
            request_path=path,
        ) from e
    if not isinstance(data, dict):
        raise ApiError(
            f"X bearer API response must be a JSON object, got {type(data).__name__}",
            service="x",
            request_method=method,
            request_path=path,
        )

    log_json_debug("X bearer response json", data)
    return data
