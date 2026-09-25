"""Static-only mistakes; never import or execute this as bot code."""

from mrs_bot_core_contracts import BotState, ReplyContextData, SendingReplyReceipt
from mrs_bot_reply_cycle_interfaces import FinaliseReply, StoreReplyDraft
from mrs_bot_reply_outcomes import _unsupported_outcome
from single_call_reply import PipelineOutcome, ValidatedReply


def bad_state_key(state: BotState) -> object:
    return state["daily_repl_count"]


def bad_context_value(context: ReplyContextData) -> None:
    context["target_id"] = 7


def bad_receipt_lifecycle(
    finalise: FinaliseReply, state: dict[str, object], receipt: SendingReplyReceipt,
) -> str:
    return finalise(state, receipt, target_id="1", quote_reply=False)


def bad_reply_narrowing(outcome: PipelineOutcome) -> ValidatedReply:
    return outcome.reply


def wrong_keyword_callback(
    state: dict[str, object], target_id: str, candidate_source: str,
    reply: str, *, reply_context: ReplyContextData,
) -> bool:
    return True


bad_storage: StoreReplyDraft = wrong_keyword_callback


def incomplete_outcome(outcome: PipelineOutcome) -> str:
    if outcome.status == "reply":
        return "ready"
    if outcome.status == "no_reply":
        return "silent"
    return _unsupported_outcome(outcome)
