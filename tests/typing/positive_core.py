"""Static-only examples using real production contracts and implementations."""

from mrs_bot_core_contracts import BotState, ConfirmedReplyReceipt, ReplyContextData
from mrs_bot_main_post_assembly import MainPostAssembly
from mrs_bot_main_post_publication import PrepareMainPostTransport
from mrs_bot_reply_cycle_interfaces import FinaliseReply
from mrs_bot_reply_generation import RunReplyPipeline
from single_call_reply import PipelineOutcome, ValidatedReply, run_reply_pipeline


def check_concrete_callbacks(owner: MainPostAssembly) -> None:
    preparation: PrepareMainPostTransport = owner.prepare_transport
    evaluation: RunReplyPipeline = run_reply_pipeline
    _ = (preparation, evaluation)


def consume_ready(outcome: PipelineOutcome) -> ValidatedReply | None:
    if outcome.status == "reply":
        return outcome.reply
    return None


def consume_confirmed(
    finalise: FinaliseReply, state: BotState, receipt: ConfirmedReplyReceipt,
    mutable_state: dict[str, object],
    context: ReplyContextData,
) -> str:
    count: int = state["daily_reply_count"]
    target: str = context["target_id"]
    if count < 0:
        return target
    return finalise(mutable_state, receipt, target_id=target, quote_reply=False)
