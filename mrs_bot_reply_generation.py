"""Orchestrate the current single-call reply decision.

Root adapters supply current callbacks, settings and application classes on each
call. Explicit calls collect images through the media boundary, send the existing
Responses request through the model transport, emit outcome and usage events,
return the typed decision result and account for provider failures through the
root callback. Evidence lookup, draft/history helpers, cooldown persistence,
terminal evaluation, posting and durable state retain their existing authority.

Import uses the standard library and inert media and validation owners. It
performs no file, environment, provider or RNG work and retains no callbacks or
runtime state. Root constant names directly alias these same objects.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from mrs_bot_reply_native_media import _REPLY_IMAGE_MIME_TYPES
from single_call_reply_validation import normalise_validation_error_codes


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
    history_for_evaluation: Callable,
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

    same_author, recent_replies = history_for_evaluation(
        state, context=context, target_id=target_id,
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
