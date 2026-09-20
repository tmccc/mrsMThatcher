"""Own the validation, storage, recovery and clearing of pending reply drafts.

ReplyDrafts receives the current validator, evidence access, telemetry and result
vocabulary when composed by the root. Its operations call each other directly,
acquire current evidence for each validation and mutate only explicit caller
state. Durable saves, candidate retirement and receipt lifecycle authority stay
with their existing owners. Import and construction perform no runtime access.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from single_call_reply_validation import (
    normalise_validation_error_codes,
    rejected_reply_text_fields,
)

if TYPE_CHECKING:
    from single_call_reply import PipelineResult


def pending_ai_reply_draft_key(target_id: object, candidate_source: object) -> str:
    """Return the current single-call pending reply draft key."""

    return f"{str(candidate_source or 'mention')}:{str(target_id)}"


def _target_draft_sources(candidate_source: str) -> set[str]:
    """Include the receipt's source and every conversational reply lane."""
    return {candidate_source, "mention", "hot_post_reply", "quote_tweet", "conversational_reply"}


@dataclass(frozen=True)
class ReplyDrafts:
    """Keep current draft rules together without retaining caller state."""

    validate_persisted_draft: Callable
    evidence_repository: Callable
    record_result: Callable
    log_event: Callable
    log: logging.Logger
    strategy_version: str
    model: str
    result_type: type
    reply_type: type
    evidence_unavailable: type[Exception]
    validation_error: type[Exception]

    def validate(
        self,
        draft: object,
        *,
        context: dict[str, object],
        recent_replies: list[object] | None = None,
    ) -> dict:
        """Validate only a current single-call durable reply draft."""

        return self.validate_persisted_draft(
            draft,
            context=context,
            repository=self.evidence_repository(),
            recent_account_replies=recent_replies or [],
        )

    def store(
        self,
        state: dict,
        target_id: str,
        candidate_source: str,
        reply: str,
        *,
        context: dict[str, object],
    ) -> bool:
        """Store a mechanically validated single-call draft."""

        if not isinstance(reply, self.reply_type):
            return False
        try:
            validated = self.validate(
                reply.draft_record,
                context=context,
            )
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self.log.warning(
                "Refusing invalid single-call pending reply draft target_id=%s "
                "source=%s reason=%s",
                target_id,
                candidate_source,
                exc,
            )
            return False
        if (
            validated["target_id"] != str(target_id)
            or validated["candidate_source"] != str(candidate_source)
            or validated["proposed_reply"] != str(reply)
        ):
            return False
        drafts = state.setdefault("pending_ai_reply_drafts", {})
        if not isinstance(drafts, dict):
            return False
        drafts[pending_ai_reply_draft_key(target_id, candidate_source)] = (
            copy.deepcopy(validated)
        )
        while len(drafts) > 100:
            drafts.pop(next(iter(drafts)))
        return True

    def recover(
        self,
        state: dict,
        target_id: str,
        candidate_source: str,
        *,
        context: dict[str, object],
        recent_replies: list[object] | None = None,
    ) -> PipelineResult | None:
        """Return a recovered decision, a discarded/failed draft result, or no draft.

        Obsolete draft shapes may be regenerated. A current draft that fails local
        reply validation is a terminal zero-call evaluation for this candidate.
        """

        drafts = state.get("pending_ai_reply_drafts", {})
        if not isinstance(drafts, dict):
            return None
        key = pending_ai_reply_draft_key(target_id, candidate_source)
        record = drafts.get(key)
        if record is None:
            return None
        try:
            validated = self.validate(
                record,
                context=context,
                recent_replies=recent_replies,
            )
        except self.evidence_unavailable:
            raise
        except self.validation_error as exc:
            validation_codes, _omitted = normalise_validation_error_codes(
                getattr(exc, "errors", ())
            )
            rejected_text = rejected_reply_text_fields(
                record.get("proposed_reply") if isinstance(record, dict) else None
            )
            if record is not None:
                self.log.warning(
                    "Retiring pending reply draft that fails current local "
                    "validation target_id=%s source=%s reason=%s",
                    target_id,
                    candidate_source,
                    ",".join(validation_codes) or "validation_details_unavailable",
                )
                drafts.pop(key, None)
                if not drafts:
                    state.pop("pending_ai_reply_drafts", None)
            visible = [
                turn
                for turn in (context.get("visible_conversation") or [])
                if isinstance(turn, dict)
            ]
            result = self.result_type(
                status="operational_failure",
                reason="persisted_draft_local_validation_failed",
                error_category="local_validation",
                model_call_count=0,
                local_validation_status="failed",
                validation_error_codes=validation_codes,
                rejected_reply_text=rejected_text["rejected_reply_text"],
                rejected_reply_text_character_count=(
                    rejected_text["rejected_reply_text_character_count"]
                ),
                payload_sha256=(
                    str(record.get("model_payload_sha256"))
                    if isinstance(record, dict)
                    else None
                ),
                visible_turn_count=len(visible),
                visible_character_count=sum(
                    len(str(turn.get("text") or "")) for turn in visible
                ),
                recent_conversational_reply_count=len(recent_replies or []),
                supplied_image_count=(
                    len(record.get("supplied_images") or [])
                    if isinstance(record, dict)
                    else 0
                ),
            )
            self.record_result(result, lane=candidate_source, target_id=target_id)
            return result
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            if record is not None:
                self.log.warning(
                    "Discarding obsolete or invalid pending reply draft "
                    "target_id=%s source=%s reason=%s",
                    target_id,
                    candidate_source,
                    exc,
                )
                drafts.pop(key, None)
                if not drafts:
                    state.pop("pending_ai_reply_drafts", None)
            return self.result_type(
                status="draft_discarded",
                reason="obsolete_or_invalid_persisted_draft",
                error_category="draft_validation",
            )
        metadata = {
            "strategy_version": validated["strategy_version"],
            "reply_kind": validated["reply_kind"],
            "reason_code": validated["reason_code"],
            "used_fact_ids": list(validated["used_fact_ids"]),
            "used_fact_count": len(validated["used_fact_ids"]),
            "trusted_fact_count": len(validated["trusted_fact_ids"]),
            "model_call_count": validated["model_call_count"],
            "validated_draft_hash": validated["validated_draft_hash"],
            "recovered_without_provider_call": True,
        }
        self.log_event(
            "single_call_reply_draft_recovered",
            lane=str(candidate_source),
            target_id=str(target_id),
            strategy_version=self.strategy_version,
            model=self.model,
            validated_draft_hash=validated["validated_draft_hash"],
            model_call_count=0,
        )
        return self.result_type(
            status="reply",
            reason="persisted_draft_recovered",
            decision="reply",
            reply_kind=validated["reply_kind"],
            reason_code=validated["reason_code"],
            reply=self.reply_type(
                validated["proposed_reply"], copy.deepcopy(validated), metadata
            ),
            used_fact_ids=tuple(validated["used_fact_ids"]),
            model_call_count=0,
            local_validation_status="passed",
            payload_sha256=validated["model_payload_sha256"],
            trusted_fact_count=len(validated["trusted_fact_ids"]),
            supplied_image_count=len(validated.get("supplied_images") or []),
        )

    def clear(
        self,
        state: dict,
        target_id: str,
        candidate_source: str,
    ) -> None:
        """Clear one pending draft after a terminal outcome or reconciliation."""

        drafts = state.get("pending_ai_reply_drafts")
        if not isinstance(drafts, dict):
            return
        drafts.pop(pending_ai_reply_draft_key(target_id, candidate_source), None)
        if not drafts:
            state.pop("pending_ai_reply_drafts", None)

    def receipt_draft_is_valid(self, data: dict, text: object) -> bool:
        """Return whether a receipt carries a valid current single-call draft."""

        context = data.get("reply_context")
        draft = data.get("ai_reply_draft")
        if not isinstance(context, dict) or not isinstance(draft, dict):
            return False
        try:
            validated = self.validate(draft, context=context)
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            return False
        return validated["proposed_reply"] == text

    def clear_target(self, state: dict, target_id: str, candidate_source: str) -> None:
        """Retire every lane's draft after one public reply confirms this target."""
        for source in _target_draft_sources(candidate_source):
            self.clear(state, target_id, source)

    def has_target(self, state: dict, target_id: str, candidate_source: str) -> bool:
        """Check whether any lane still carries a draft for a confirmed target."""
        drafts = state.get("pending_ai_reply_drafts")
        pending_keys = {
            pending_ai_reply_draft_key(target_id, source)
            for source in _target_draft_sources(candidate_source)
        }
        return isinstance(drafts, dict) and bool(pending_keys.intersection(drafts))
