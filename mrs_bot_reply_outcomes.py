"""Inert, exhaustive routing of checked reply-evaluation outcomes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias

if TYPE_CHECKING:
    from single_call_reply import PipelineOutcome


ReplyDisposition: TypeAlias = Literal["ready", "no_reply", "defer"]


def outcome_disposition(outcome: PipelineOutcome) -> ReplyDisposition:
    """Map every checked decision to the lanes' existing execution branches."""
    if outcome.status == "reply":
        return "ready"
    if outcome.status == "no_reply":
        return "no_reply"
    if outcome.status == "operational_failure":
        return "defer"
    if outcome.status == "disabled":
        return "defer"
    if outcome.status == "draft_discarded":
        return "defer"
    return _unsupported_outcome(outcome)


def _unsupported_outcome(outcome: NoReturn) -> NoReturn:
    """Force a type error when a new alternative lacks lane routing."""
    raise ValueError(f"Unsupported checked evaluation outcome: {outcome!r}")
