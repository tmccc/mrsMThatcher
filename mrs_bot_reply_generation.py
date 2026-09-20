"""Orchestrate the current single-call reply decision.

ReplyGeneration receives current media, history and model-transport owners on each
root invocation. Evaluation calls those owners directly, alongside fixed health
classification and owned telemetry. It collects images, selects confirmed history,
sends the existing Responses request, emits outcome and usage events, returns the
typed decision result and accounts for provider failures without returning through
root compatibility relays. Evidence lookup, draft helpers, cooldown persistence,
terminal evaluation, posting and durable state retain their existing authority.

Import uses the standard library and inert media and validation owners. It
performs no file, environment, provider or RNG work and retains no callbacks or
runtime state. Root constant names directly alias these same objects.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mrs_bot_reply_native_media import _REPLY_IMAGE_MIME_TYPES
from single_call_reply_validation import normalise_validation_error_codes


if TYPE_CHECKING:
    from mrs_bot_reply_history import ReplyHistory
    from mrs_bot_reply_model_transport import ReplyModelTransport
    from mrs_bot_reply_native_media import ReplyMedia
    from single_call_reply import PipelineResult


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


def _is_openai_provider_health_failure(category: object) -> bool:
    """Return whether a failure is evidence about OpenAI service health."""

    value = str(category or "")
    if value in _OPENAI_PROVIDER_HEALTH_FAILURE_CATEGORIES:
        return True
    prefix = "provider_http_"
    status = value.removeprefix(prefix)
    return value.startswith(prefix) and len(status) == 3 and status.isdigit()


def _is_terminal_candidate_local_failure(
    outcome: PipelineResult | dict[str, object],
) -> bool:
    """Return whether one permanent local failure should retire its candidate."""

    status = outcome.get("status") if isinstance(outcome, Mapping) else outcome.status
    if status != "operational_failure":
        return False
    category = outcome.get("error_category") if isinstance(outcome, Mapping) else outcome.error_category
    return category in _TERMINAL_CANDIDATE_LOCAL_FAILURE_CATEGORIES


@dataclass(frozen=True)
class ReplyGeneration:
    """Own decisions, media disposition, health and telemetry with current boundaries."""

    media: ReplyMedia
    remote_operations_paused: type[Exception]
    result_type: type
    log: logging.Logger
    history: ReplyHistory
    require_remote_operation_unpaused: Callable
    run_pipeline: Callable
    config: dict[str, object]
    evidence_repository: Callable
    model_transport: ReplyModelTransport
    record_api_error: Callable
    reply_type: type
    now_epoch: Callable
    decision_telemetry: Callable
    log_event: Callable
    strategy_version: str

    is_provider_health_failure = staticmethod(_is_openai_provider_health_failure)
    is_terminal_candidate_failure = staticmethod(_is_terminal_candidate_local_failure)

    def record_result(
        self, result: PipelineResult, *, lane: str, target_id: str,
    ) -> None:
        """Record the decision and bounded validation/provider usage details."""
        telemetry = self.decision_telemetry(result)
        self.log_event(
            "single_call_reply_decision",
            lane=lane,
            target_id=target_id,
            **telemetry,
        )
        validation_codes, _omitted = normalise_validation_error_codes(
            telemetry.get("validation_error_codes", [])
        )
        if validation_codes:
            self.log.info(
                "Single-call reply validation failed target_id=%s lane=%s "
                "category=%s rules=%s",
                target_id,
                lane,
                result.error_category,
                ",".join(validation_codes),
            )
        if result.provider_usage:
            usage = dict(result.provider_usage)
            self.log.info(
                "Single-call reply provider=OpenAI model=%s usage=%s",
                self.model_transport.model,
                usage,
            )
            self.log_event(
                "single_call_reply_provider_usage",
                lane=lane,
                target_id=target_id,
                strategy_version=self.strategy_version,
                model=self.model_transport.model,
                provider_response_id=result.provider_response_id,
                provider_latency_ms=result.provider_latency_ms,
                request_attempt_count=result.provider_request_attempt_count,
                **usage,
            )

    def evaluate(
        self, context: dict[str, object], media_context: dict | None = None, *,
        state: dict,
    ) -> PipelineResult:
        """Return the authoritative decision, retaining local and provider dispositions."""

        lane = str(context.get("lane") or "")
        target_id = str(context.get("target_id") or "")
        if int(state.get("openai_api_cooldown_until_epoch") or 0) > self.now_epoch():
            return self.result_type(status="operational_failure", reason="openai_cooldown",
                                  error_category="provider_cooldown", model_call_count=0)
        visible_turns = [
            turn
            for turn in (context.get("visible_conversation") or [])
            if isinstance(turn, dict)
        ]
        try:
            supplied_images = self.media.collect(media_context)
        except self.remote_operations_paused:
            raise
        except self.media.media_unavailable as exc:
            return self._media_failure_result(
                exc, visible_turns, lane=lane, target_id=target_id,
            )

        same_author, recent_replies = self.history.for_evaluation(
            state, context=context, target_id=target_id,
        )
        self.require_remote_operation_unpaused(
            f"OpenAI single-call reply preparation target {target_id}"
        )
        result = self.run_pipeline(
            context=context,
            config=self.config,
            repository=self.evidence_repository(),
            transport=self.model_transport.call,
            same_author_interactions=same_author,
            recent_account_replies=recent_replies,
            supplied_images=supplied_images,
            visual_description=context.get("visual_description"),
        )
        self._record_provider_health(state, result, lane=lane, target_id=target_id)
        if result.status == "operational_failure":
            self.record_result(result, lane=lane, target_id=target_id)
            return result
        self.record_result(result, lane=lane, target_id=target_id)
        if result.status in {"disabled", "no_reply"}:
            return result
        if result.status != "reply" or not isinstance(result.reply, self.reply_type):
            raise RuntimeError("single-call reply returned an impossible result")
        return result


    def _media_failure_result(
        self,
        exc: Exception,
        visible_turns: list[dict],
        *,
        lane: str,
        target_id: str,
    ) -> PipelineResult:
        """Classify failed material-image collection before any provider work."""
        error_category = (
            "image_transport"
            if isinstance(exc, self.media.media_transient_unavailable)
            else "image_input"
        )
        result = self.result_type(
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
        self.record_result(result, lane=lane, target_id=target_id)
        self.log.warning(
            "%s reply target_id=%s lane=%s because material image "
            "collection failed: %s",
            "Deferring" if error_category == "image_transport" else "Rejecting",
            target_id,
            lane,
            exc,
        )
        return result

    def _record_provider_health(
        self, state: dict, result: PipelineResult, *, lane: str, target_id: str,
    ) -> None:
        """Account for provider health before decision telemetry can fail."""
        if result.provider_status_code == 429 and result.status != "operational_failure":
            message = "single-call reply recovered after rate limit"
            category = "provider_http_429"
            status_code = 429
        elif result.status == "operational_failure":
            provider_health_failure = _is_openai_provider_health_failure(result.error_category)
            prior_rate_limit = result.provider_status_code == 429
            if not provider_health_failure and not prior_rate_limit:
                self.log.warning(
                    "Single-call reply operational failure did not affect OpenAI "
                    "health target_id=%s lane=%s category=%s",
                    target_id,
                    lane,
                    result.error_category or "uncategorised",
                )
                return
            message = f"single-call reply provider failure: {result.reason}"
            category = str(result.error_category) if provider_health_failure else "provider_http_429"
            status_code = result.provider_status_code
        else:
            return
        self.record_api_error(
            state,
            self.model_transport.error(
                message,
                category=category,
                status_code=status_code,
                reset_epoch=result.provider_reset_epoch,
                retry_after_seconds=result.provider_retry_after_seconds,
                request_attempt_count=result.provider_request_attempt_count,
            ),
            "openai",
        )
