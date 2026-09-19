"""Own terminal reply evaluation retention and completed-watermark cleanup.

Root adapters supply current callbacks, policy, limits, clock and logger on each
call. Terminal ledger bodies retain replay protection, event/mutation order and
shallow-copy versus in-place references. Author quarantine lifecycle rules live
in mrs_bot_author_quarantines; its pure clear helper is re-exported here for
compatibility. Mention authority, configuration, persistence and reply
orchestration remain with their existing owners. Import performs no runtime
access or reverse application import, and this module retains no callbacks or
caller state.
"""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger

from mrs_bot_author_quarantines import clear_author_evaluation_quarantine_history


def completed_mention_watermark_covers_target(state: dict, target_id: str) -> bool:
    """Return whether a completed mention traversal prevents refetching one target."""
    target_id = str(target_id)
    watermark = str(state.get("last_seen_mention_id") or "")
    pending = state.get("mention_pending_candidates")
    if (
        not target_id.isdigit()
        or not watermark.isdigit()
        or not isinstance(pending, dict)
        or target_id in pending
    ):
        return False
    return int(target_id) <= int(watermark)


def prune_completed_mention_quarantine_evaluations(
    state: dict,
    *,
    AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY: str,
    completed_mention_watermark_covers_target: Callable,
    log: Logger,
) -> int:
    """Drop unsafe legacy or watermark-covered local quarantine skips."""
    records = state.get("reply_evaluation_records")
    if not isinstance(records, dict):
        return 0
    removable = {
        str(target_id)
        for target_id, record in records.items()
        if isinstance(record, dict)
        and record.get("lane") == "mention"
        and record.get("outcome") == "no_reply"
        and record.get("reason") == "author_evaluation_quarantine"
        and (
            record.get("evidence_policy")
            != AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY
            or completed_mention_watermark_covers_target(state, str(target_id))
        )
    }
    if not removable:
        return 0
    state["reply_evaluation_records"] = {
        str(target_id): record
        for target_id, record in records.items()
        if str(target_id) not in removable
    }
    log.info(
        "Retired %s legacy or completed-watermark mention quarantine evaluations",
        len(removable),
    )
    return len(removable)


def prune_reply_evaluation_records(
    state: dict,
    *,
    current_epoch: int | None = None,
    MAX_REASONABLE_STATE_EPOCH: int,
    REPLY_EVALUATION_MAX_RECORDS: int,
    REPLY_EVALUATION_MIN_RETENTION_SECONDS: int,
    log: Logger,
    now_epoch: Callable,
    prune_completed_mention_quarantine_evaluations: Callable,
) -> None:
    """Prune old terminal evaluations while preserving recent replay protection."""
    prune_completed_mention_quarantine_evaluations(state)
    records = state.get("reply_evaluation_records")
    if not isinstance(records, dict) or not records:
        return
    if current_epoch is None:
        current_epoch = now_epoch()
    current_epoch = int(current_epoch)
    cutoff = current_epoch - REPLY_EVALUATION_MIN_RETENTION_SECONDS

    def evaluated_epoch(item: tuple[str, dict]) -> int | None:
        value = item[1].get("evaluated_epoch")
        if type(value) is not int or value < 0 or value > MAX_REASONABLE_STATE_EPOCH:
            return None
        return value

    items = [
        (str(target_id), dict(record))
        for target_id, record in records.items()
        if isinstance(record, dict)
    ]
    protected = [
        item
        for item in items
        if evaluated_epoch(item) is None or evaluated_epoch(item) > cutoff
    ]
    older = [
        item
        for item in items
        if evaluated_epoch(item) is not None and evaluated_epoch(item) <= cutoff
    ]

    def sort_key(item: tuple[str, dict]) -> tuple[int, str]:
        epoch = evaluated_epoch(item)
        return (epoch if epoch is not None else MAX_REASONABLE_STATE_EPOCH + 1, item[0])

    protected.sort(key=sort_key)
    older.sort(key=sort_key)
    protected_overflow = max(0, len(protected) - REPLY_EVALUATION_MAX_RECORDS)
    if protected_overflow:
        evictable_quarantine = [
            item
            for item in protected
            if evaluated_epoch(item) is not None
            and item[1].get("lane") == "mention"
            and item[1].get("outcome") == "no_reply"
            and item[1].get("reason") == "author_evaluation_quarantine"
        ]
        evicted_ids = {
            target_id
            for target_id, _record in evictable_quarantine[:protected_overflow]
        }
        if evicted_ids:
            protected = [
                item for item in protected if item[0] not in evicted_ids
            ]
            log.warning(
                "Pruned %s oldest recent mention quarantine evaluations to bound "
                "reply-evaluation state; an incomplete traversal may refetch them",
                len(evicted_ids),
            )
    if len(protected) > REPLY_EVALUATION_MAX_RECORDS:
        retained = protected
        log.warning(
            "Recent or conservatively protected reply evaluations exceed nominal cap: "
            "protected=%s cap=%s; retaining all protected records",
            len(protected),
            REPLY_EVALUATION_MAX_RECORDS,
        )
    else:
        available_older_slots = max(0, REPLY_EVALUATION_MAX_RECORDS - len(protected))
        retained = protected + older[-available_older_slots:] if available_older_slots else protected
    retained.sort(key=sort_key)
    state["reply_evaluation_records"] = {
        target_id: record for target_id, record in retained
    }
    removed = len(records) - len(retained)
    if removed:
        log.info(
            "Pruned %s reply evaluation records; retained=%s cap=%s",
            removed,
            len(retained),
            REPLY_EVALUATION_MAX_RECORDS,
        )


def terminal_reply_evaluation(state: dict, target_id: str) -> dict | None:
    """Return the terminal reply evaluation."""
    records = state.get("reply_evaluation_records", {})
    if not isinstance(records, dict):
        return None
    record = records.get(str(target_id))
    if isinstance(record, dict) and record.get("outcome") in {
        "no_reply",
        "operational_failure",
        "reply_not_permitted",
    }:
        return record
    return None


def record_terminal_reply_evaluation(
    state: dict,
    *,
    target_id: str,
    lane: str,
    reason: str,
    outcome: str = 'no_reply',
    prune_records: bool = True,
    evidence_policy: str | None = None,
    now_epoch: Callable,
    prune_reply_evaluation_records: Callable,
) -> None:
    """Record terminal reply evaluation."""
    if outcome not in {"no_reply", "operational_failure", "reply_not_permitted"}:
        raise ValueError(f"Unsupported terminal reply outcome: {outcome}")
    records = state.get("reply_evaluation_records", {})
    if not isinstance(records, dict):
        records = {}
    if prune_records:
        records = dict(records)
    record = {
        "target_id": str(target_id),
        "lane": str(lane),
        "outcome": outcome,
        "reason": str(reason or "model_selected_no_reply"),
        "evaluated_epoch": now_epoch(),
    }
    if evidence_policy is not None:
        record["evidence_policy"] = str(evidence_policy)
    records[str(target_id)] = record
    state["reply_evaluation_records"] = records
    if prune_records:
        prune_reply_evaluation_records(state)
