"""Static-only mistakes; never import or execute this as bot code."""

from dataclasses import replace
from typing import Any, Literal

from mrs_bot_core_contracts import BotState, ReplyContextData, SendingReplyReceipt
from mrs_bot_main_post_assembly import MainPostTransport
from mrs_bot_quote_posting import QuotePostRunner
from mrs_bot_reply_cycle_interfaces import FinaliseReply, StoreReplyDraft
from mrs_bot_reply_generation import RunReplyPipeline
from mrs_bot_reply_outcomes import _unsupported_outcome
from single_call_reply import PipelineOutcome, ValidatedReply


def bad_state_key(state: BotState) -> object:
    return state["daily_repl_count"]


def bad_state_value(state: BotState) -> None:
    state["daily_reply_count"] = "one"


def bad_context_value(context: ReplyContextData) -> None:
    context["target_id"] = 7


def bad_receipt_lifecycle(
    finalise: FinaliseReply, state: BotState, receipt: SendingReplyReceipt,
) -> str:
    return finalise(state, receipt, target_id="1", quote_reply=False)


def bad_reply_narrowing(outcome: PipelineOutcome) -> ValidatedReply:
    return outcome.reply


def wrong_keyword_callback(
    state: BotState, target_id: str, candidate_source: str,
    reply: str, *, reply_context: ReplyContextData,
) -> bool:
    return True


bad_storage: StoreReplyDraft = wrong_keyword_callback


class IncompleteTelemetryReply:
    """The old outcome view's fields without generation's needed telemetry."""

    status: Literal["reply"]
    reply: ValidatedReply
    reason: str
    model_call_count: int
    error_category: str | None
    reply_kind: str | None
    reason_code: str | None
    provider_status_code: int | None
    provider_reset_epoch: int | None
    provider_retry_after_seconds: int | None
    provider_request_attempt_count: int
    provider_usage: dict[str, int]
    provider_response_id: str | None
    provider_latency_ms: int | None
    call_id: str | None


def incomplete_pipeline(**kwargs: object) -> IncompleteTelemetryReply:
    raise AssertionError


bad_pipeline: RunReplyPipeline = incomplete_pipeline


def wrong_main_create(**kwargs: Any) -> None:
    raise AssertionError


def bad_main_transport(owner: MainPostTransport) -> MainPostTransport:
    return replace(owner, create_post=wrong_main_create)


def bad_live_state(runner: QuotePostRunner, state: dict[str, object]) -> None:
    runner.post(set(), set(), state)


def incomplete_outcome(outcome: PipelineOutcome) -> str:
    if outcome.status == "reply":
        return "ready"
    if outcome.status == "no_reply":
        return "silent"
    return _unsupported_outcome(outcome)
