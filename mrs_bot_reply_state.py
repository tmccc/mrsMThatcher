"""Manage current local reply drafts and query already-confirmed reply history.

Root adapters supply current helpers, limits, configuration, logger, exception
and result classes on each call. The fixed conversational-lane set lives here;
its root name remains a direct alias. Bodies preserve existing validation,
reference, copying, ordering and recovery boundaries. Provider transport, durable
history writes, state persistence and receipt lifecycle authority remain with
their existing owners. Import uses only the standard library, constructs the
fixed frozenset and performs no file, environment, provider or RNG work. No
callbacks or mutable state are retained.
"""

from __future__ import annotations

import copy
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime


CONVERSATIONAL_REPLY_HISTORY_LANES = frozenset(
    {"mention", "hot_post_reply", "quote_tweet", "conversational_reply"}
)


def pending_ai_reply_draft_key(target_id: object, candidate_source: object) -> str:
    """Return the current single-call pending reply draft key."""

    return f"{str(candidate_source or 'mention')}:{str(target_id)}"


def validate_current_ai_reply_draft(
    draft: object,
    *,
    context: dict[str, object],
    recent_replies: list[object] | None = None,
    validate_single_call_persisted_draft: Callable,
    reply_evidence_repository: Callable,
) -> dict:
    """Validate only a current single-call durable reply draft."""

    return validate_single_call_persisted_draft(
        draft,
        context=context,
        repository=reply_evidence_repository(),
        recent_account_replies=recent_replies or [],
    )


def store_pending_ai_reply(
    state: dict,
    target_id: str,
    candidate_source: str,
    reply: str,
    *,
    context: dict[str, object],
    ValidatedReply: type,
    validate_current_ai_reply_draft: Callable,
    log: logging.Logger,
    pending_ai_reply_draft_key: Callable,
) -> bool:
    """Store a mechanically validated single-call draft."""

    if not isinstance(reply, ValidatedReply):
        return False
    try:
        validated = validate_current_ai_reply_draft(
            reply.draft_record,
            context=context,
        )
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.warning(
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


def recover_pending_ai_reply(
    state: dict,
    target_id: str,
    candidate_source: str,
    *,
    context: dict[str, object],
    recent_replies: list[object] | None = None,
    pending_ai_reply_draft_key: Callable,
    validate_current_ai_reply_draft: Callable,
    ReplyEvidenceUnavailable: type,
    ReplyValidationError: type,
    log: logging.Logger,
    _record_single_call_result: Callable,
    PipelineResult: type,
    log_event: Callable,
    SINGLE_CALL_STRATEGY_VERSION: str,
    SINGLE_CALL_MODEL: str,
    ValidatedReply: type,
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
        validated = validate_current_ai_reply_draft(
            record,
            context=context,
            recent_replies=recent_replies,
        )
    except ReplyEvidenceUnavailable:
        raise
    except ReplyValidationError as exc:
        if record is not None:
            log.warning(
                "Retiring pending reply draft that fails current local "
                "validation target_id=%s source=%s reason=%s",
                target_id,
                candidate_source,
                exc,
            )
            drafts.pop(key, None)
            if not drafts:
                state.pop("pending_ai_reply_drafts", None)
        visible = [
            turn
            for turn in (context.get("visible_conversation") or [])
            if isinstance(turn, dict)
        ]
        result = PipelineResult(
            status="operational_failure",
            reason="persisted_draft_local_validation_failed",
            error_category="local_validation",
            model_call_count=0,
            local_validation_status="failed",
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
        _record_single_call_result(result, lane=candidate_source, target_id=target_id)
        return result
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        if record is not None:
            log.warning(
                "Discarding obsolete or invalid pending reply draft "
                "target_id=%s source=%s reason=%s",
                target_id,
                candidate_source,
                exc,
            )
            drafts.pop(key, None)
            if not drafts:
                state.pop("pending_ai_reply_drafts", None)
        return PipelineResult(
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
    log_event(
        "single_call_reply_draft_recovered",
        lane=str(candidate_source),
        target_id=str(target_id),
        strategy_version=SINGLE_CALL_STRATEGY_VERSION,
        model=SINGLE_CALL_MODEL,
        validated_draft_hash=validated["validated_draft_hash"],
        model_call_count=0,
    )
    return PipelineResult(
        status="reply",
        reason="persisted_draft_recovered",
        decision="reply",
        reply_kind=validated["reply_kind"],
        reason_code=validated["reason_code"],
        reply=ValidatedReply(validated["proposed_reply"], copy.deepcopy(validated), metadata),
        used_fact_ids=tuple(validated["used_fact_ids"]),
        model_call_count=0,
        local_validation_status="passed",
        payload_sha256=validated["model_payload_sha256"],
        trusted_fact_count=len(validated["trusted_fact_ids"]),
        supplied_image_count=len(validated.get("supplied_images") or []),
    )


def clear_pending_ai_reply(
    state: dict,
    target_id: str,
    candidate_source: str,
    *,
    pending_ai_reply_draft_key: Callable,
) -> None:
    """Clear one pending reply draft after a terminal outcome or reconciliation."""

    drafts = state.get("pending_ai_reply_drafts")
    if not isinstance(drafts, dict):
        return
    drafts.pop(pending_ai_reply_draft_key(target_id, candidate_source), None)
    if not drafts:
        state.pop("pending_ai_reply_drafts", None)


def _confirmed_conversational_history_rows(
    state: dict,
    *,
    CONVERSATIONAL_REPLY_HISTORY_LANES: frozenset[str],
    valid_string_post_id: Callable,
    MAX_REASONABLE_STATE_EPOCH: int,
) -> list[dict]:
    """Return positively identified rows from the durable confirmation history."""

    history = state.get("ai_reply_history", [])
    if not isinstance(history, list):
        return []
    rows: list[dict] = []
    for raw in history:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("candidate_source") or "") not in (
            CONVERSATIONAL_REPLY_HISTORY_LANES
        ):
            continue
        if raw.get("deleted") is True or str(raw.get("status") or "") in {
            "deleted",
            "failed",
            "pending",
        }:
            continue
        target_id = str(raw.get("target_id") or "")
        reply_post_id = str(raw.get("reply_post_id") or "")
        reply = str(raw.get("proposed_reply") or "").strip()
        epoch = raw.get("reply_epoch")
        if (
            not valid_string_post_id(target_id)
            or not valid_string_post_id(reply_post_id)
            or not reply
            or type(epoch) is not int
            or epoch <= 0
            or epoch > MAX_REASONABLE_STATE_EPOCH
        ):
            continue
        rows.append(raw)
    return rows


def _confirmed_history_sort_key(row: dict) -> tuple[int, int]:
    """Order confirmed replies by time and numeric X post identity."""

    return (int(row["reply_epoch"]), int(str(row["reply_post_id"])))


def recent_confirmed_account_replies(
    state: dict,
    limit: int,
    *,
    before_epoch: int | None = None,
    excluded_post_ids: set[str] | None = None,
    excluded_reply_post_ids: set[str] | None = None,
    _confirmed_conversational_history_rows: Callable,
    _confirmed_history_sort_key: Callable,
    MAX_RECENT_ACCOUNT_REPLIES: int,
) -> list[dict[str, str]]:
    """Return only prior remotely confirmed conversational account replies."""

    if before_epoch is None:
        return []
    excluded = {str(value) for value in (excluded_post_ids or set())}
    excluded.update(str(value) for value in (excluded_reply_post_ids or set()))
    rows = []
    for row in _confirmed_conversational_history_rows(state):
        reply_post_id = str(row["reply_post_id"])
        if reply_post_id in excluded:
            continue
        if row["reply_epoch"] >= before_epoch:
            continue
        rows.append(row)
    rows.sort(key=_confirmed_history_sort_key)
    by_reply_id: dict[str, dict] = {}
    for row in rows:
        by_reply_id[str(row["reply_post_id"])] = row
    rows = sorted(by_reply_id.values(), key=_confirmed_history_sort_key)
    bounded_limit = max(0, min(int(limit), MAX_RECENT_ACCOUNT_REPLIES))
    return [
        {
            "post_id": str(row["reply_post_id"]),
            "text": str(row["proposed_reply"]).strip(),
        }
        for row in rows[-bounded_limit:]
    ] if bounded_limit else []


def _reply_context_history_excluded_post_ids(
    context: dict[str, object],
    *,
    quoted_post_reference_id: Callable,
) -> set[str]:
    """Return every current subject identity excluded from history fields."""

    excluded_post_ids = {
        str(turn.get("post_id") or "")
        for turn in (context.get("visible_conversation") or [])
        if isinstance(turn, dict)
    }
    excluded_post_ids.update(
        {
            str(context.get("thread_id") or ""),
            str(context.get("root_post_id") or ""),
        }
    )
    quoted_post_id = quoted_post_reference_id(context)
    if quoted_post_id is not None:
        excluded_post_ids.add(quoted_post_id)
    excluded_post_ids.discard("")
    return excluded_post_ids


def recovery_comparison_account_replies(
    state: dict,
    *,
    context: dict[str, object],
    _reply_context_history_excluded_post_ids: Callable,
    now_epoch: Callable,
    MAX_REASONABLE_STATE_EPOCH: int,
    recent_confirmed_account_replies: Callable,
    _same_author_confirmed_history_rows: Callable,
    _confirmed_conversational_history_rows: Callable,
) -> list[dict[str, str]]:
    """Return current confirmed prose used only to revalidate an unsent draft."""

    excluded_post_ids = _reply_context_history_excluded_post_ids(context)
    before_epoch = min(now_epoch() + 1, MAX_REASONABLE_STATE_EPOCH + 1)
    recent = recent_confirmed_account_replies(
        state,
        before_epoch=before_epoch,
        excluded_post_ids=excluded_post_ids,
    )
    same_author_rows = _same_author_confirmed_history_rows(
        state,
        author_id=context.get("target_author_id"),
        current_thread_post_ids=excluded_post_ids,
        target_id=str(context.get("target_id") or ""),
        before_epoch=before_epoch,
    )
    by_reply_id = {
        str(row.get("post_id") or ""): row
        for row in recent
        if isinstance(row, dict)
    }
    for row in same_author_rows:
        reply_id = str(row.get("reply_post_id") or "")
        if reply_id and reply_id not in by_reply_id:
            by_reply_id[reply_id] = {
                "post_id": reply_id,
                "text": str(row.get("proposed_reply") or ""),
                "_reply_epoch": int(row.get("reply_epoch") or 0),
            }
    history_epochs = {
        str(row.get("reply_post_id") or ""): int(row.get("reply_epoch") or 0)
        for row in _confirmed_conversational_history_rows(state)
    }
    return [
        {"post_id": reply_id, "text": str(row.get("text") or "")}
        for reply_id, row in sorted(
            by_reply_id.items(),
            key=lambda item: (
                int(item[1].get("_reply_epoch") or history_epochs.get(item[0], 0)),
                item[0],
            ),
        )
    ]


def _same_author_confirmed_history_rows(
    state: dict,
    *,
    author_id: object,
    current_thread_post_ids: set[str],
    target_id: object,
    before_epoch: int | None,
    AI_REPLY_HISTORY_MAX_AGE_SECONDS: int,
    _confirmed_conversational_history_rows: Callable,
    _confirmed_history_sort_key: Callable,
    MAX_SAME_AUTHOR_INTERACTIONS: int,
) -> list[dict]:
    """Select genuine prior same-author pairs before dropping local identity."""

    wanted_author = str(author_id or "")
    current_target = str(target_id or "")
    if not wanted_author or before_epoch is None:
        return []
    latest_allowed = before_epoch
    earliest_allowed = latest_allowed - AI_REPLY_HISTORY_MAX_AGE_SECONDS
    rows: list[dict] = []
    for row in _confirmed_conversational_history_rows(state):
        if str(row.get("author_id") or "") != wanted_author:
            continue
        identity_fields = {
            str(row.get("target_id") or ""),
            str(row.get("reply_post_id") or ""),
            str(row.get("conversation_id") or ""),
            str(row.get("root_post_id") or ""),
        }
        identity_fields.discard("")
        if (
            str(row.get("target_id") or "") == current_target
            or identity_fields.intersection(current_thread_post_ids)
        ):
            continue
        epoch = int(row["reply_epoch"])
        if epoch < earliest_allowed:
            continue
        if epoch >= before_epoch:
            continue
        contributor = str(row.get("incoming_contribution") or "").strip()
        if (
            not str(row.get("conversation_id") or "")
            or not contributor
            or hashlib.sha256(contributor.encode("utf-8")).hexdigest()
            != row.get("incoming_contribution_sha256")
        ):
            continue
        rows.append(row)
    rows.sort(key=_confirmed_history_sort_key)
    by_interaction: dict[tuple[str, str], dict] = {}
    for row in rows:
        identity = (str(row["target_id"]), str(row["reply_post_id"]))
        by_interaction[identity] = row
    return sorted(
        by_interaction.values(),
        key=_confirmed_history_sort_key,
    )[-MAX_SAME_AUTHOR_INTERACTIONS:]


def recent_same_author_account_interactions(
    state: dict,
    *,
    author_id: object,
    conversation_id: object,
    target_id: object,
    before_epoch: int | None = None,
    visible_post_ids: set[str] | None = None,
    _same_author_confirmed_history_rows: Callable,
    MAX_SAME_AUTHOR_INTERACTIONS: int,
) -> list[dict[str, str]]:
    """Return confirmed pairs from earlier conversations by this contributor."""

    current_thread_post_ids = {
        str(value) for value in (visible_post_ids or set()) if str(value)
    }
    current_thread_post_ids.add(str(conversation_id or ""))
    current_thread_post_ids.discard("")
    rows = _same_author_confirmed_history_rows(
        state,
        author_id=author_id,
        current_thread_post_ids=current_thread_post_ids,
        target_id=target_id,
        before_epoch=before_epoch,
    )
    return [
        {
            "contributor": str(row["incoming_contribution"]).strip(),
            "account_reply": str(row["proposed_reply"]).strip(),
        }
        for row in rows[-MAX_SAME_AUTHOR_INTERACTIONS:]
    ]


def _reply_target_epoch(context: dict[str, object]) -> int | None:
    value = context.get("target_created_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp())


def ai_reply_receipt_draft_is_valid(
    data: dict,
    text: object,
    *,
    validate_current_ai_reply_draft: Callable,
) -> bool:
    """Return whether a receipt carries a valid current single-call draft."""
    context = data.get("reply_context")
    draft = data.get("ai_reply_draft")
    if not isinstance(context, dict) or not isinstance(draft, dict):
        return False
    try:
        validated = validate_current_ai_reply_draft(draft, context=context)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return False
    return validated["proposed_reply"] == text
