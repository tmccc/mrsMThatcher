"""Typed, per-invocation settings and boundaries shared by reply cycles.

These small records hold configuration, draft persistence and delivery only.
The root builds them from current callbacks for each check; they retain no bot
state and perform no I/O at import or construction. Candidate policy and
discovery stay explicit dependencies of their respective cycle owners.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from single_call_reply import PipelineResult


@dataclass(frozen=True)
class ReplyCycleConfig:
    """Limits common to both conversational reply lanes."""

    enabled: bool
    mark_as_ai: bool
    maximum_daily_replies: int
    maximum_daily_author_replies: int
    minimum_reply_spacing: int
    user_id: str


@dataclass(frozen=True)
class NormalReplyConfig(ReplyCycleConfig):
    """Mention model budget and incoming context bound."""

    maximum_fresh_evaluations: int
    incoming_max_chars: int


@dataclass(frozen=True)
class QuoteReplyConfig(ReplyCycleConfig):
    """Quote lookup activation and candidate/daily limits."""

    quote_checks_enabled: bool
    maximum_candidates: int
    maximum_daily_quote_replies: int


class SaveReplyState(Protocol):
    """Persist the original caller state, optionally requiring durability."""

    def __call__(self, state: dict, *, durable: bool = False) -> None: ...


class RecoverReplyDraft(Protocol):
    """Recover an explicit decision without a provider call."""

    def __call__(
        self, state: dict, target_id: str, candidate_source: str, *,
        context: dict[str, object], recent_replies: list[object] | None = None,
    ) -> PipelineResult | None: ...


class StoreReplyDraft(Protocol):
    """Validate and store a pending draft in caller state."""

    def __call__(
        self, state: dict, target_id: str, candidate_source: str, reply: str, *,
        context: dict[str, object],
    ) -> bool: ...


@dataclass(frozen=True)
class ReplyCyclePersistence:
    """Local state and pending-draft operations used during a reply check."""

    save: SaveReplyState
    recover: RecoverReplyDraft
    store: StoreReplyDraft
    clear: Callable[[dict, str, str], None]


class EvaluateReply(Protocol):
    """Return the entire model/local evaluation and its accounting metadata."""

    def __call__(
        self, context: dict[str, object], media_context: dict | None = None, *, state: dict,
    ) -> PipelineResult: ...


class PostReply(Protocol):
    """Publish and durably bind the remote identity to its receipt."""

    def __call__(
        self, *, state: dict, receipt_template: dict, reply_text: str,
        reply_to_id: str, made_with_ai: bool, lane: str,
    ) -> tuple[dict, dict]: ...


class FinaliseReply(Protocol):
    """Commit a confirmation and retire recovery records before lane reporting."""

    def __call__(
        self, state: dict, receipt: dict, *, target_id: str, quote_reply: bool,
    ) -> str: ...


@dataclass(frozen=True)
class ReplyCycleDelivery:
    """Receipt recovery, preflight, sending and confirmed transaction boundaries."""

    load_receipt: Callable[[], tuple[str, dict | None]]
    reconcile_receipt: Callable[[dict], bool]
    block_ambiguous: Callable[[], None]
    bind_attempt: Callable[[dict], dict]
    target_available: Callable[[str], bool]
    post: PostReply
    retire_rejected: Callable[[dict, Exception], None]
    finalise: FinaliseReply
