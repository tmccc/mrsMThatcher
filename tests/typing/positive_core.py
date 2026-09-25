"""Static-only examples using real production contracts and implementations."""

from collections.abc import Callable

import mrsMThatcher2 as bot
from mrs_bot_core_contracts import (
    AttemptingMainPostAttempt, BotState, ConfirmedReplyReceipt,
    ReplyContextData, SendingMainPostAttempt,
)
from mrs_bot_main_post_assembly import (
    BeginMainPostTransport, BindMainPostTransportSource, BuildCurrentMainPostAttempt,
    CaptureBoundMemeState, MainPostApplication, MainPostAssembly,
    MainPostReceiptIO, MainPostTransport, RemoveMainPostAttemptWithProof,
)
from mrs_bot_main_post_publication import CreatePreparedMainPost, PrepareMainPostTransport
from mrs_bot_main_post_reconciliation import MainPostRecoveryPersistence
from mrs_bot_reply_cycle_interfaces import FinaliseReply, ReplyCyclePersistence
from mrs_bot_reply_delivery import ReplyCycleDelivery, ReplyReceipts
from mrs_bot_reply_generation import ReplyGeneration, RunReplyPipeline
from mrs_bot_state_generation import StateCommitProof
from remote_write_transport_journal import begin_transport_transaction
from single_call_reply import PipelineOutcome, ValidatedReply, run_reply_pipeline


def check_concrete_callbacks(owner: MainPostAssembly) -> None:
    preparation: PrepareMainPostTransport = owner.prepare_transport
    evaluation: RunReplyPipeline = run_reply_pipeline
    created: CreatePreparedMainPost = bot.create_post
    begin: BeginMainPostTransport = begin_transport_transaction
    bind: BindMainPostTransportSource = bot.bind_lane_transport_source
    _ = (preparation, evaluation, created, begin, bind)


def check_root_state_and_assemblies(
    state: BotState, sending: SendingMainPostAttempt,
    attempting: AttemptingMainPostAttempt, confirmed: ConfirmedReplyReceipt,
) -> StateCommitProof:
    load: Callable[[], BotState] = bot.load_runtime_state
    reply = bot._reply_assembly()
    main = bot._main_post_assembly()
    persistence: ReplyCyclePersistence = reply._reply_cycle_persistence()
    delivery: ReplyCycleDelivery = reply._reply_cycle_delivery()
    generation: ReplyGeneration = reply._reply_generation_owner()
    receipts: ReplyReceipts = reply.reply_receipts()
    receipt_io: MainPostReceiptIO = main.receipt_io
    transport: MainPostTransport = main.transport
    application: MainPostApplication = main.application
    recovery: MainPostRecoveryPersistence = main.recovery_operation().persistence
    quote_runner = main.quote_runner()
    build_attempt: BuildCurrentMainPostAttempt = quote_runner.build_attempt
    bound_meme: CaptureBoundMemeState = quote_runner.bound_meme_state
    remove_attempt: RemoveMainPostAttemptWithProof = quote_runner.remove_attempt
    _ = (load, generation, receipts, receipt_io, transport, application,
         recovery, build_attempt, bound_meme, remove_attempt)
    _ = main.prepare_transport(sending)
    _ = main.promote_pending(attempting, post_id="1", confirmation_epoch=1)
    _ = delivery.finalise(state, confirmed, target_id="1", quote_reply=False)
    return persistence.save(state, durable=True)


def consume_ready(outcome: PipelineOutcome) -> ValidatedReply | None:
    if outcome.status == "reply":
        return outcome.reply
    return None


def consume_confirmed(
    finalise: FinaliseReply, state: BotState, receipt: ConfirmedReplyReceipt,
    context: ReplyContextData,
) -> str:
    count: int = state["daily_reply_count"]
    target: str = context["target_id"]
    if count < 0:
        return target
    return finalise(state, receipt, target_id=target, quote_reply=False)
