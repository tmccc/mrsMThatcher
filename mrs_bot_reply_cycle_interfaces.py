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

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

# Compatibility import: the delivery boundary owns its executable routing.
from mrs_bot_reply_delivery import ReplyCycleDelivery

if TYPE_CHECKING:
    from single_call_reply import PipelineResult


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

    context: dict[str, object]
    media_context: dict | None


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


class ReplyCandidateDiscovery(Protocol):
    """Return the current ordered candidates for one normal-lane source."""

    def __call__(self, state: dict) -> list[dict]: ...


class QuoteTweetDiscovery(Protocol):
    """Return quote candidates grouped by the watched original-post identity."""

    def __call__(
        self,
        post_ids: list[str],
        state: dict | None = None,
    ) -> dict[str, list[dict]]: ...


@dataclass(frozen=True)
class ReplyCyclePersistence:
    """Local state and pending-draft operations used during a reply check."""

    save: SaveReplyState
    recover: RecoverReplyDraft
    store: StoreReplyDraft
    clear: Callable[[dict, str, str], None]
    retire_ineligible: Callable[[dict, str, str], None]


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
