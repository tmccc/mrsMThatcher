"""Collect reply images and orchestrate the current single-call reply decision.

Root adapters supply current callbacks, settings, application classes and the
requests object on each call. Explicit calls may fetch bounded native images,
send the existing Responses request with its bounded retry policy, emit outcome
and usage events, return the typed decision result and account for provider
failures through the root callback. The actual reply pipeline, evidence lookup,
draft/history helpers, cooldown persistence, terminal evaluation, posting and
durable state authority remain in their existing locations.

Import uses the standard library and pure validation vocabulary. It
performs no file, environment, provider or RNG work and retains no callbacks or
runtime state. Root constant names directly alias these same objects.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Mapping

from single_call_reply_validation import normalise_validation_error_codes


_REPLY_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}


_OPENAI_PROVIDER_HEALTH_FAILURE_CATEGORIES = frozenset(
    {
        "provider_ambiguous_timeout",
        "provider_envelope",
        "provider_incomplete",
        "provider_schema",
        "provider_transport",
        # Strict provider-side structured output makes a schema-invalid model
        # output a provider contract failure rather than a candidate-local
        # prose rule failure.
        "schema_validation",
    }
)


_TERMINAL_CANDIDATE_LOCAL_FAILURE_CATEGORIES = frozenset(
    {
        "context_validation",
        "draft_validation",
        "image_input",
        "local_validation",
        "provider_incomplete_content_filter",
        "provider_incomplete_max_output_tokens",
        "provider_refusal",
    }
)


def log_ai_reply_posting_outcome(
    *,
    reply: str,
    status: str,
    lane: str,
    target_id: str,
    failure_reason: str,
    reply_post_id: str = '',
    log_event: Callable,
) -> None:
    """Emit a bounded posting outcome without model inputs or reasoning."""

    metadata = getattr(reply, "pipeline_metadata", None)
    if not isinstance(metadata, dict):
        draft = getattr(reply, "draft_record", None)
        metadata = draft if isinstance(draft, dict) else {}
    log_event(
        "single_call_reply_posting_outcome",
        status=status,
        lane=lane,
        target_id=target_id,
        reply_post_id=reply_post_id,
        strategy_version=metadata.get("strategy_version"),
        reply_kind=metadata.get("reply_kind"),
        reason_code=metadata.get("reason_code"),
        validated_draft_hash=metadata.get("validated_draft_hash"),
        failure_reason=failure_reason,
    )


def _safe_reply_image_url(
    value: object,
    *,
    urlsplit: Callable,
    ReplyMediaUnavailable: type,
    TEST_MODE: bool,
    endpoint_is_loopback: Callable,
) -> str:
    url = str(value or "").strip()
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ReplyMediaUnavailable("candidate image URL has an invalid port") from exc
    trusted_production_origin = bool(
        parsed.scheme == "https"
        and parsed.hostname == "pbs.twimg.com"
        and port in {None, 443}
    )
    trusted_test_origin = bool(
        TEST_MODE
        and parsed.scheme == "http"
        and endpoint_is_loopback(url)
        and parsed.path.startswith("/media/")
    )
    if (
        not (trusted_production_origin or trusted_test_origin)
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/media/")
        or parsed.fragment
    ):
        raise ReplyMediaUnavailable("candidate image URL is outside the trusted X media origin")
    return url


def collect_reply_images(
    media_context: dict | None,
    *,
    MAX_SUPPLIED_IMAGES: int,
    ReplyMediaUnavailable: type,
    _safe_reply_image_url: Callable,
    require_remote_operation_unpaused: Callable,
    requests: object,
    request_timeout: Callable,
    ReplyMediaTransientUnavailable: type,
    _REPLY_IMAGE_MIME_TYPES: set[str],
    SINGLE_CALL_MAX_IMAGE_BYTES: int,
    validate_supplied_images: Callable,
) -> list[dict[str, object]]:
    """Collect up to two already-identified native X images with hard bounds."""

    if not isinstance(media_context, dict):
        return []
    status = media_context.get("status")
    expected = int(media_context.get("photos_expected", 0) or 0)
    if status == "none" and expected == 0:
        return []
    photos = media_context.get("photos")
    required_count = min(expected, MAX_SUPPLIED_IMAGES)
    if (
        status != "supplied"
        or not isinstance(photos, list)
        or required_count < 1
        or len(photos) != required_count
    ):
        raise ReplyMediaUnavailable("material candidate image metadata is incomplete")
    collected: list[dict[str, object]] = []
    for index, photo in enumerate(photos, 1):
        if not isinstance(photo, dict):
            raise ReplyMediaUnavailable("candidate image metadata is invalid")
        identity = str(photo.get("media_key") or "").strip()
        if not identity:
            raise ReplyMediaUnavailable("candidate image lacks a stable identity")
        url = _safe_reply_image_url(photo.get("url"))
        require_remote_operation_unpaused(
            f"candidate image collection {index}/{len(photos)}"
        )
        response = None
        try:
            response = requests.get(
                url,
                stream=True,
                allow_redirects=False,
                timeout=request_timeout(),
                headers={"Accept": "image/jpeg,image/png,image/webp,image/gif", "Accept-Encoding": "identity"},
            )
            if response.status_code != 200:
                failure_type = (
                    ReplyMediaTransientUnavailable
                    if response.status_code in {408, 425, 429}
                    or 500 <= response.status_code < 600
                    else ReplyMediaUnavailable
                )
                raise failure_type(
                    f"candidate image returned HTTP {response.status_code}"
                )
            if str(response.headers.get("Content-Encoding") or "identity").lower() != "identity":
                raise ReplyMediaUnavailable("candidate image transfer encoding is unsupported")
            if response.headers.get("Location"):
                raise ReplyMediaUnavailable("candidate image attempted a redirect")
            mime_type = str(
                response.headers.get("Content-Type") or ""
            ).split(";", 1)[0].strip().lower()
            if mime_type not in _REPLY_IMAGE_MIME_TYPES:
                raise ReplyMediaUnavailable("candidate image type is unsupported")
            raw_length = response.headers.get("Content-Length")
            content_length = None
            if raw_length is not None:
                try:
                    content_length = int(raw_length)
                except ValueError as exc:
                    raise ReplyMediaUnavailable(
                        "candidate image length is invalid"
                    ) from exc
                if not 1 <= content_length <= SINGLE_CALL_MAX_IMAGE_BYTES:
                    raise ReplyMediaUnavailable(
                        "candidate image length is outside the safe bound"
                    )
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not isinstance(chunk, bytes) or not chunk:
                    continue
                total += len(chunk)
                if total > SINGLE_CALL_MAX_IMAGE_BYTES:
                    raise ReplyMediaUnavailable("candidate image exceeds the safe bound")
                chunks.append(chunk)
            if content_length is not None and total != content_length:
                failure_type = (ReplyMediaTransientUnavailable if total < content_length else ReplyMediaUnavailable)
                raise failure_type("candidate image body differs from declared length")
            image_bytes = b"".join(chunks)
        except ReplyMediaUnavailable:
            raise
        except requests.RequestException as exc:
            raise ReplyMediaTransientUnavailable(
                "candidate image could not be obtained safely"
            ) from exc
        finally:
            if response is not None:
                close_response = getattr(response, "close", None)
                if callable(close_response):
                    close_response()
        collected.append(
            {
                "identity": identity,
                "mime_type": mime_type,
                "data": image_bytes,
                "attachment_role": str(photo.get("attachment_role") or ""),
                "source_post_id": str(photo.get("source_post_id") or ""),
            }
        )
    try:
        return validate_supplied_images(collected)
    except (RuntimeError, TypeError, ValueError) as exc:
        raise ReplyMediaUnavailable("candidate image bytes failed validation") from exc


def _definite_connection_failure_before_transmission(
    error: requests.RequestException,
    *,
    requests: object,
) -> bool:
    if isinstance(error, requests.ConnectTimeout):
        return True
    if not isinstance(error, requests.ConnectionError):
        return False
    current: BaseException | None = error
    while current is not None:
        if type(current).__name__ in {
            "NewConnectionError",
            "NameResolutionError",
            "ConnectionRefusedError",
            "gaierror",
        }:
            return True
        current = current.__cause__ or current.__context__
    return False


def _openai_api_error(
    message: str,
    *,
    category: str,
    status_code: int | None = None,
    reset_epoch: int | None = None,
    retry_after_seconds: int | None = None,
    request_attempt_count: int = 1,
    ApiError: type,
) -> ApiError:
    error = ApiError(
        message,
        service="openai",
        status_code=status_code,
        reset_epoch=reset_epoch,
    )
    error.error_category = category
    error.retry_after_seconds = retry_after_seconds
    error.request_attempt_count = request_attempt_count
    return error


def _openai_retry_metadata(
    response: requests.Response,
    *,
    now_epoch: Callable,
    parsedate_to_datetime: Callable,
) -> tuple[int | None, int | None]:
    """Return bounded Retry-After metadata for provider cooldown accounting."""

    headers = getattr(response, "headers", {})
    if not isinstance(headers, Mapping):
        headers = {}
    current = now_epoch()
    delays: list[int] = []
    raw = str(headers.get("Retry-After") or "").strip()
    if raw:
        try:
            numeric = float(raw)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw)
                if parsed.tzinfo is not None:
                    numeric = parsed.timestamp() - current
                else:
                    numeric = float("nan")
            except (TypeError, ValueError, OverflowError):
                numeric = float("nan")
        if math.isfinite(numeric) and numeric >= 0:
            # Bound durable cooldown metadata, never turn a long delay into an
            # immediate retry merely because it exceeds our accepted range.
            delays.append(min(math.ceil(numeric), 7 * 24 * 60 * 60))
    for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        raw_reset = str(headers.get(key) or "").strip()
        parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", raw_reset)
        if parts and "".join(number + unit for number, unit in parts) == raw_reset:
            delay = sum(float(number) * {"ms": .001, "s": 1, "m": 60, "h": 3600}[unit] for number, unit in parts)
            if math.isfinite(delay):
                delays.append(min(math.ceil(delay), 7 * 24 * 60 * 60))
    seconds = max(delays) if delays else None
    return (current + seconds if seconds is not None else None, seconds)



def _is_openai_provider_health_failure(
    category: object,
    *,
    _OPENAI_PROVIDER_HEALTH_FAILURE_CATEGORIES: frozenset[str],
) -> bool:
    """Return whether a failure is evidence about OpenAI service health."""

    value = str(category or "")
    if value in _OPENAI_PROVIDER_HEALTH_FAILURE_CATEGORIES:
        return True
    prefix = "provider_http_"
    status = value.removeprefix(prefix)
    return value.startswith(prefix) and len(status) == 3 and status.isdigit()


def _is_terminal_candidate_local_failure(
    outcome: PipelineResult | Mapping[str, object],
    *,
    _TERMINAL_CANDIDATE_LOCAL_FAILURE_CATEGORIES: frozenset[str],
) -> bool:
    """Return whether one permanent local failure should retire its candidate."""

    status = outcome.get("status") if isinstance(outcome, Mapping) else outcome.status
    if status != "operational_failure":
        return False
    category = outcome.get("error_category") if isinstance(outcome, Mapping) else outcome.error_category
    return category in _TERMINAL_CANDIDATE_LOCAL_FAILURE_CATEGORIES


def openai_responses_reply_call(
    *,
    request: dict[str, object],
    timeout_seconds: int,
    lane: str,
    target_id: str,
    log: logging.Logger,
    SINGLE_CALL_MODEL: str,
    SINGLE_CALL_REASONING_EFFORT: str,
    monotonic: Callable,
    require_remote_operation_unpaused: Callable,
    report_bot_health_progress: Callable,
    requests: object,
    OPENAI_BASE: str,
    OPENAI_API_KEY: str,
    _definite_connection_failure_before_transmission: Callable,
    _openai_api_error: Callable,
    _openai_retry_metadata: Callable,
    sleep: Callable,
) -> dict[str, object]:
    """Send one executable Responses request, retrying only proved non-execution."""

    log.info(
        "Calling single-call reply provider=OpenAI model=%s "
        "reasoning_effort=%s lane=%s target_id=%s",
        SINGLE_CALL_MODEL,
        SINGLE_CALL_REASONING_EFFORT,
        lane,
        target_id,
    )
    started = monotonic()
    first_429_seen = False
    first_429_retry_metadata: tuple[int | None, int | None] = (None, None)
    for attempt in (1, 2):
        require_remote_operation_unpaused(
            f"OpenAI single-call reply target {target_id}"
        )
        report_bot_health_progress("ai_call")
        try:
            response = requests.post(
                f"{OPENAI_BASE}/responses",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=request,
                timeout=timeout_seconds,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            if (
                attempt == 1
                and _definite_connection_failure_before_transmission(exc)
            ):
                log.warning(
                    "OpenAI single-call reply had a definite pre-transmission "
                    "connection failure; retrying once target_id=%s",
                    target_id,
                )
                continue
            if first_429_seen:
                reset_epoch, retry_after_seconds = first_429_retry_metadata
                raise _openai_api_error(
                    "OpenAI single-call reply transport failed after an "
                    "earlier HTTP 429",
                    category=(
                        "provider_ambiguous_timeout"
                        if isinstance(exc, requests.Timeout)
                        else "provider_transport"
                    ),
                    status_code=429,
                    reset_epoch=reset_epoch,
                    retry_after_seconds=retry_after_seconds,
                    request_attempt_count=attempt,
                ) from exc
            raise _openai_api_error(
                "OpenAI single-call reply transport failed",
                category=(
                    "provider_ambiguous_timeout"
                    if isinstance(exc, requests.Timeout)
                    else "provider_transport"
                ),
                request_attempt_count=attempt,
            ) from exc
        finally:
            report_bot_health_progress("ai_call")
        if response.status_code == 429:
            if attempt == 1:
                first_429_seen = True
                first_429_retry_metadata = _openai_retry_metadata(response)
                log.warning(
                    "OpenAI single-call reply returned pre-execution HTTP %s; "
                    "respecting bounded retry delay target_id=%s",
                    response.status_code,
                    target_id,
                )
                close_response = getattr(response, "close", None)
                if callable(close_response):
                    close_response()
                # A one-second maximum keeps the bot responsive. Longer or
                # unknown provider delays become durable cooldowns upstream.
                delay = first_429_retry_metadata[1]
                if delay is not None and delay <= 1:
                    sleep(delay)
                    continue
                raise _openai_api_error(
                    "OpenAI rate limit requires candidate deferral",
                    category="provider_http_429", status_code=429,
                    reset_epoch=first_429_retry_metadata[0],
                    retry_after_seconds=delay, request_attempt_count=attempt,
                )
        if not 200 <= response.status_code < 300:
            observed_status_code = response.status_code
            reset_epoch, retry_after_seconds = _openai_retry_metadata(response)
            status_code = observed_status_code
            if first_429_seen:
                status_code = 429
                if observed_status_code != 429 or reset_epoch is None:
                    reset_epoch, retry_after_seconds = first_429_retry_metadata
            close_response = getattr(response, "close", None)
            if callable(close_response):
                close_response()
            raise _openai_api_error(
                (
                    f"OpenAI single-call reply returned HTTP {observed_status_code}"
                    + (
                        " after an earlier HTTP 429"
                        if observed_status_code != status_code
                        else ""
                    )
                ),
                category=f"provider_http_{observed_status_code}",
                status_code=status_code,
                reset_epoch=reset_epoch,
                retry_after_seconds=retry_after_seconds,
                request_attempt_count=attempt,
            )
        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            close_response = getattr(response, "close", None)
            if callable(close_response):
                close_response()
            raise _openai_api_error(
                "OpenAI single-call reply returned malformed JSON",
                category="provider_envelope",
                status_code=429 if first_429_seen else None,
                reset_epoch=(
                    first_429_retry_metadata[0] if first_429_seen else None
                ),
                retry_after_seconds=(
                    first_429_retry_metadata[1] if first_429_seen else None
                ),
                request_attempt_count=attempt,
            ) from exc
        close_response = getattr(response, "close", None)
        if callable(close_response):
            close_response()
        if not isinstance(data, dict):
            raise _openai_api_error(
                "OpenAI single-call reply response is not an object",
                category="provider_envelope",
                status_code=429 if first_429_seen else None,
                reset_epoch=(
                    first_429_retry_metadata[0] if first_429_seen else None
                ),
                retry_after_seconds=(
                    first_429_retry_metadata[1] if first_429_seen else None
                ),
                request_attempt_count=attempt,
            )
        result = {
            "response": data,
            "latency_ms": max(0, round((monotonic() - started) * 1000)),
            "request_attempt_count": attempt,
        }
        if first_429_seen:
            result.update(
                {
                    "provider_status_code": 429,
                    "provider_reset_epoch": first_429_retry_metadata[0],
                    "provider_retry_after_seconds": first_429_retry_metadata[1],
                }
            )
        return result
    raise AssertionError("unreachable OpenAI request retry state")


def _record_single_call_result(
    result: PipelineResult,
    *,
    lane: str,
    target_id: str,
    single_call_decision_telemetry: Callable,
    log: logging.Logger,
    log_event: Callable,
    SINGLE_CALL_MODEL: str,
    SINGLE_CALL_STRATEGY_VERSION: str,
) -> None:
    telemetry = single_call_decision_telemetry(result)
    log_event(
        "single_call_reply_decision",
        lane=lane,
        target_id=target_id,
        **telemetry,
    )
    validation_codes, _omitted = normalise_validation_error_codes(
        telemetry.get("validation_error_codes", [])
    )
    if validation_codes:
        log.info(
            "Single-call reply validation failed target_id=%s lane=%s "
            "category=%s rules=%s",
            target_id,
            lane,
            result.error_category,
            ",".join(validation_codes),
        )
    if result.provider_usage:
        usage = dict(result.provider_usage)
        log.info(
            "Single-call reply provider=OpenAI model=%s usage=%s",
            SINGLE_CALL_MODEL,
            usage,
        )
        log_event(
            "single_call_reply_provider_usage",
            lane=lane,
            target_id=target_id,
            strategy_version=SINGLE_CALL_STRATEGY_VERSION,
            model=SINGLE_CALL_MODEL,
            provider_response_id=result.provider_response_id,
            provider_latency_ms=result.provider_latency_ms,
            request_attempt_count=result.provider_request_attempt_count,
            **usage,
        )


def evaluate_single_call_reply(
    context: dict[str, object],
    media_context: dict | None = None,
    *,
    state: dict,
    collect_reply_images: Callable,
    RemoteOperationsPaused: type,
    ReplyMediaUnavailable: type,
    ReplyMediaTransientUnavailable: type,
    PipelineResult: type,
    _record_single_call_result: Callable,
    log: logging.Logger,
    _reply_target_epoch: Callable,
    _reply_context_history_excluded_post_ids: Callable,
    _same_author_confirmed_history_rows: Callable,
    recent_confirmed_account_replies: Callable,
    require_remote_operation_unpaused: Callable,
    run_single_call_reply_pipeline: Callable,
    single_call_reply: dict[str, object],
    reply_evidence_repository: Callable,
    openai_responses_reply_call: Callable,
    _is_openai_provider_health_failure: Callable,
    record_api_error: Callable,
    _openai_api_error: Callable,
    ValidatedReply: type,
    now_epoch: Callable,
) -> PipelineResult:
    """Return the authoritative decision, retaining local and provider dispositions."""

    lane = str(context.get("lane") or "")
    target_id = str(context.get("target_id") or "")
    if int(state.get("openai_api_cooldown_until_epoch") or 0) > now_epoch():
        return PipelineResult(status="operational_failure", reason="openai_cooldown",
                              error_category="provider_cooldown", model_call_count=0)
    visible_turns = [
        turn
        for turn in (context.get("visible_conversation") or [])
        if isinstance(turn, dict)
    ]
    try:
        supplied_images = collect_reply_images(media_context)
    except RemoteOperationsPaused:
        raise
    except ReplyMediaUnavailable as exc:
        error_category = (
            "image_transport"
            if isinstance(exc, ReplyMediaTransientUnavailable)
            else "image_input"
        )
        result = PipelineResult(
            status="operational_failure",
            reason="material_image_unavailable",
            error_category=error_category,
            local_validation_status="not_run",
            visible_turn_count=len(visible_turns),
            visible_character_count=sum(
                len(str(turn.get("text") or "")) for turn in visible_turns
            ),
            supplied_image_count=0,
        )
        _record_single_call_result(result, lane=lane, target_id=target_id)
        log.warning(
            "%s reply target_id=%s lane=%s because material image "
            "collection failed: %s",
            "Deferring" if error_category == "image_transport" else "Rejecting",
            target_id,
            lane,
            exc,
        )
        return result

    before_epoch = _reply_target_epoch(context)
    current_thread_post_ids = _reply_context_history_excluded_post_ids(context)
    same_author_rows = _same_author_confirmed_history_rows(
        state,
        author_id=context.get("target_author_id"),
        current_thread_post_ids=current_thread_post_ids,
        target_id=target_id,
        before_epoch=before_epoch,
    )
    same_author = [
        {
            "contributor": str(row["incoming_contribution"]).strip(),
            "account_reply": str(row["proposed_reply"]).strip(),
        }
        for row in same_author_rows
    ]
    recent_replies = recent_confirmed_account_replies(
        state,
        before_epoch=before_epoch,
        excluded_post_ids=current_thread_post_ids,
        excluded_reply_post_ids={
            str(row["reply_post_id"]) for row in same_author_rows
        },
    )
    require_remote_operation_unpaused(
        f"OpenAI single-call reply preparation target {target_id}"
    )
    result = run_single_call_reply_pipeline(
        context=context,
        config=single_call_reply,
        repository=reply_evidence_repository(),
        transport=openai_responses_reply_call,
        same_author_interactions=same_author,
        recent_account_replies=recent_replies,
        supplied_images=supplied_images,
        visual_description=context.get("visual_description"),
    )
    if result.provider_status_code == 429 and result.status != "operational_failure":
        record_api_error(
            state,
            _openai_api_error(
                "single-call reply recovered after rate limit", category="provider_http_429",
                status_code=429, reset_epoch=result.provider_reset_epoch,
                retry_after_seconds=result.provider_retry_after_seconds,
                request_attempt_count=result.provider_request_attempt_count,
            ),
            "openai",
        )
    if result.status == "operational_failure":
        provider_health_failure = _is_openai_provider_health_failure(
            result.error_category
        )
        prior_rate_limit = result.provider_status_code == 429
        if provider_health_failure or prior_rate_limit:
            record_api_error(
                state,
                _openai_api_error(
                    f"single-call reply provider failure: {result.reason}",
                    category=(
                        str(result.error_category)
                        if provider_health_failure
                        else "provider_http_429"
                    ),
                    status_code=result.provider_status_code,
                    reset_epoch=result.provider_reset_epoch,
                    retry_after_seconds=result.provider_retry_after_seconds,
                    request_attempt_count=result.provider_request_attempt_count,
                ),
                "openai",
            )
        else:
            log.warning(
                "Single-call reply operational failure did not affect OpenAI "
                "health target_id=%s lane=%s category=%s",
                target_id,
                lane,
                result.error_category or "uncategorised",
            )
        _record_single_call_result(result, lane=lane, target_id=target_id)
        return result
    _record_single_call_result(result, lane=lane, target_id=target_id)
    if result.status in {"disabled", "no_reply"}:
        return result
    if result.status != "reply" or not isinstance(result.reply, ValidatedReply):
        raise RuntimeError("single-call reply returned an impossible result")
    return result
