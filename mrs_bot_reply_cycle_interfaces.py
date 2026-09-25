"""Fixed check statuses, per-invocation settings and shared reply-cycle boundaries.

These records describe configuration, draft persistence, candidate
control flow and prepared context/media results. The reply assembly supplies current
owners and narrow callbacks for each check, and builders return transient
context/media references without adding them to bot state. Import and
construction perform no I/O. Candidate policy and discovery stay explicit
dependencies of their respective cycle owners. The delivery boundary is
re-exported from its inert behavior owner.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol


# Compatibility import: the delivery boundary owns its executable routing.
from mrs_bot_reply_delivery import ReplyCycleDelivery as ReplyCycleDelivery

if TYPE_CHECKING:
    from mrs_bot_core_contracts import ConfirmedReplyReceipt, ReplyContextData, ReplyMediaContext
    from mrs_bot_state_generation import StateCommitProof
    from single_call_reply import PipelineOutcome


NORMAL_CHECK_STATUS_CHECKED = "checked"
NORMAL_CHECK_STATUS_POSTED = "posted"
NORMAL_CHECK_STATUS_SKIPPED_SPACING = "skipped_spacing"
NORMAL_CHECK_STATUS_SKIPPED_CAP = "skipped_cap"
NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN = "skipped_cooldown"
NORMAL_CHECK_STATUS_DISABLED = "disabled"
NORMAL_CHECK_STATUS_API_ERROR = "api_error"


QUOTE_CHECK_STATUS_CHECKED = "checked"
QUOTE_CHECK_STATUS_POSTED = "posted"
QUOTE_CHECK_STATUS_SKIPPED_SPACING = "skipped_spacing"
QUOTE_CHECK_STATUS_SKIPPED_CAP = "skipped_cap"
QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN = "skipped_cooldown"
QUOTE_CHECK_STATUS_DISABLED = "disabled"


@dataclass(frozen=True)
class SkipReplyCandidate:
    """Continue scanning after the current candidate or original-post batch."""


@dataclass(frozen=True)
class FinishReplyCheck:
    """End the current reply check with an explicit lane status."""

    status: str


@dataclass(frozen=True)
class PreparedReplyContext:
    """Canonical model/persistence context and separately collected native media."""

    context: ReplyContextData
    media_context: ReplyMediaContext | None


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
    """Quote lookup activation, age and candidate/daily limits."""

    quote_checks_enabled: bool
    minimum_quote_age_seconds: int
    maximum_candidates: int
    maximum_daily_quote_replies: int


class SaveReplyState(Protocol):
    """Persist the original caller state, optionally requiring durability."""

    def __call__(self, state: dict[str, object], *, durable: bool = False) -> StateCommitProof: ...


class RecoverReplyDraft(Protocol):
    """Recover an explicit decision without a provider call."""

    def __call__(
        self, state: dict[str, object], target_id: str, candidate_source: str, *,
        context: ReplyContextData, recent_replies: Sequence[object] | None = None,
    ) -> PipelineOutcome | None: ...


class StoreReplyDraft(Protocol):
    """Validate and store a pending draft in caller state."""

    def __call__(
        self, state: dict[str, object], target_id: str, candidate_source: str, reply: str, *,
        context: ReplyContextData,
    ) -> bool: ...


class ReplyCandidateDiscovery(Protocol):
    """Return an untrusted provider page; context building validates its values."""

    def __call__(self, state: dict[str, object]) -> list[dict[str, Any]]: ...


class QuoteTweetDiscovery(Protocol):
    """Return quote candidates grouped by the watched original-post identity."""

    def __call__(
        self,
        post_ids: list[str],
        state: dict[str, object] | None = None,
    ) -> dict[str, list[dict[str, Any]]]: ...


class LogReplyEvent(Protocol):
    """Emit one bounded event with named values."""

    def __call__(self, event: str, **fields: object) -> None: ...


class LogValidatedReply(Protocol):
    """Report validated reply metadata without retaining its prose."""

    def __call__(
        self, *, target_description: str, target_id: str, reply: object,
    ) -> None: ...


class LogReplyPostingOutcome(Protocol):
    """Emit one posting result with the optional remote identity."""

    def __call__(
        self, *, reply: str, status: str, lane: str, target_id: str,
        failure_reason: str, reply_post_id: str | None = None,
    ) -> None: ...


class SortRawTweets(Protocol):
    """Deduplicate and order one untrusted provider page."""

    def __call__(
        self, tweets: list[dict[str, Any]], *, context: str,
    ) -> list[dict[str, Any]]: ...


class MarkHotPostSkipped(Protocol):
    """Record a candidate skip against the mutable live state."""

    def __call__(
        self, state: dict[str, Any], candidate: dict[str, Any],
        reason: str = "unspecified",
    ) -> None: ...


@dataclass(frozen=True)
class ReplyCyclePersistence:
    """Local state and pending-draft operations used during a reply check."""

    save: SaveReplyState
    recover: RecoverReplyDraft
    store: StoreReplyDraft
    clear: Callable[[dict[str, object], str, str], None]
    retire_ineligible: Callable[[dict[str, object], str, str], None]


class EvaluateReply(Protocol):
    """Return the entire model/local evaluation and its accounting metadata."""

    def __call__(
        self, context: ReplyContextData, media_context: ReplyMediaContext | None = None, *, state: dict[str, object],
    ) -> PipelineOutcome: ...


class PostReply(Protocol):
    """Publish and durably bind the remote identity to its receipt."""

    def __call__(
        self, *, state: dict[str, object], receipt_template: dict[str, object], reply_text: str,
        reply_to_id: str, made_with_ai: bool, lane: str,
    ) -> tuple[dict[str, object], ConfirmedReplyReceipt]: ...


class FinaliseReply(Protocol):
    """Commit a confirmation and retire recovery records before lane reporting."""

    def __call__(
        self, state: dict[str, object], receipt: ConfirmedReplyReceipt, *, target_id: str, quote_reply: bool,
    ) -> str: ...
