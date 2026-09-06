"""Own terminal reply evaluation retention and mention author quarantine state.

Eight root adapters supply current callbacks, policy, limits, clock and logger on
each call; the three dependency-free watermark, clear-history and lookup helpers
are root aliases. Original bodies retain replay protection, exact old-policy
migrations, event/mutation order and shallow-copy versus in-place references.

Shared mention authority/discovery/watermark commits, configuration, persistence
and reply orchestration remain in their current locations. This standard-library
owner adds no saves or provider work, retains no callbacks, configuration, clients
or state, and performs no import-time file, environment, clock, provider or RNG
work or reverse application import.
"""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger
from pathlib import Path


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
    if not isinstance(records, dict):
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


def author_no_reply_epoch_limit(
    *,
    AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD: int,
) -> int:
    """Return retention sized from the effective runtime quarantine threshold."""
    return max(100, int(AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD) * 4)


def prune_author_evaluation_quarantines(
    state: dict,
    *,
    current_epoch: int | None = None,
    AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY: str,
    AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS: int,
    MAX_REASONABLE_STATE_EPOCH: int,
    author_no_reply_epoch_limit: Callable,
    log_event: Callable,
    now_epoch: Callable,
) -> bool:
    """Expire quarantines and discard author strikes outside the live window."""
    records = state.get("author_evaluation_quarantines")
    if not isinstance(records, dict):
        state["author_evaluation_quarantines"] = {}
        return True
    current = now_epoch() if current_epoch is None else int(current_epoch)
    cutoff = current - AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS
    retained: dict[str, dict[str, object]] = {}
    changed = False
    for author_id, raw_record in records.items():
        if not isinstance(raw_record, dict):
            changed = True
            continue
        if (
            raw_record.get("evidence_policy")
            != AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY
        ):
            changed = True
            continue
        explicit_epoch = raw_record.get(
            "latest_explicit_spam_or_abuse_epoch"
        )
        if (
            type(explicit_epoch) is not int
            or explicit_epoch < 0
            or explicit_epoch > MAX_REASONABLE_STATE_EPOCH
        ):
            changed = True
            continue
        recent = [
            int(epoch)
            for epoch in raw_record.get("recent_no_reply_epochs", [])
            if type(epoch) is int and cutoff < epoch <= current
        ]
        recent.sort()
        recent = recent[-author_no_reply_epoch_limit():]
        until = int(raw_record.get("quarantine_until_epoch", 0) or 0)
        updated = int(raw_record.get("last_updated_epoch", 0) or 0)
        if until and until <= current:
            log_event(
                "author_evaluation_quarantine_expired",
                author_id=str(author_id),
                quarantine_until_epoch=until,
                expired_epoch=current,
            )
            until = 0
            recent = []
            explicit_epoch = 0
            updated = current
            changed = True
        if until > current or recent:
            record = {
                "recent_no_reply_epochs": recent,
                "quarantine_until_epoch": until,
                "last_updated_epoch": updated,
                "latest_explicit_spam_or_abuse_epoch": explicit_epoch,
                "evidence_policy": AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
            }
            retained[str(author_id)] = record
            if record != raw_record or str(author_id) != author_id:
                changed = True
        else:
            changed = True
    if retained != records:
        state["author_evaluation_quarantines"] = retained
        changed = True
    return changed


def active_author_evaluation_quarantine(
    state: dict,
    author_id: str,
    *,
    current_epoch: int | None = None,
    now_epoch: Callable,
    prune_author_evaluation_quarantines: Callable,
) -> dict | None:
    """Return an active mention-lane author quarantine, expiring old state first."""
    current = now_epoch() if current_epoch is None else int(current_epoch)
    prune_author_evaluation_quarantines(state, current_epoch=current)
    records = state.get("author_evaluation_quarantines", {})
    record = records.get(str(author_id)) if isinstance(records, dict) else None
    if (
        isinstance(record, dict)
        and int(record.get("quarantine_until_epoch", 0) or 0) > current
    ):
        return record
    return None


def record_qualifying_author_no_reply(
    state: dict,
    author_id: str,
    *,
    current_epoch: int | None = None,
    explicit_spam_or_abuse: bool = True,
    AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY: str,
    AUTHOR_NO_REPLY_QUARANTINE_SECONDS: int,
    AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD: int,
    AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS: int,
    author_no_reply_epoch_limit: Callable,
    log_event: Callable,
    now_epoch: Callable,
    prune_author_evaluation_quarantines: Callable,
) -> bool:
    """Add one mechanically valid editorial no-reply strike for an author."""
    author_id = str(author_id)
    if not author_id or not author_id.isdigit():
        return False
    current = now_epoch() if current_epoch is None else int(current_epoch)
    prune_author_evaluation_quarantines(state, current_epoch=current)
    records = state.get("author_evaluation_quarantines", {})
    if not isinstance(records, dict):
        records = {}
    records = dict(records)
    existing = records.get(author_id, {})
    if not isinstance(existing, dict):
        existing = {}
    cutoff = current - AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS
    recent = [
        int(epoch)
        for epoch in existing.get("recent_no_reply_epochs", [])
        if type(epoch) is int and cutoff < epoch <= current
    ]
    explicit_epoch = existing.get("latest_explicit_spam_or_abuse_epoch", 0)
    if explicit_spam_or_abuse is True:
        explicit_epoch = current
    recent.append(current)
    recent.sort()
    recent = recent[-author_no_reply_epoch_limit():]
    until = int(existing.get("quarantine_until_epoch", 0) or 0)
    started = False
    if until <= current and len(recent) >= AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD:
        until = current + AUTHOR_NO_REPLY_QUARANTINE_SECONDS
        started = True
        log_event(
            "author_evaluation_quarantine_started",
            author_id=author_id,
            strike_count=len(recent),
            threshold=AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD,
            window_seconds=AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS,
            quarantine_seconds=AUTHOR_NO_REPLY_QUARANTINE_SECONDS,
            quarantine_until_epoch=until,
        )
    records[author_id] = {
        "recent_no_reply_epochs": recent,
        "quarantine_until_epoch": until,
        "last_updated_epoch": current,
        "latest_explicit_spam_or_abuse_epoch": explicit_epoch,
        "evidence_policy": AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
    }
    state["author_evaluation_quarantines"] = records
    return started


def clear_author_evaluation_quarantine_history(state: dict, author_id: str) -> bool:
    """Clear prior strikes when a mention receives a validated reply."""
    records = state.get("author_evaluation_quarantines")
    if not isinstance(records, dict) or str(author_id) not in records:
        return False
    records = dict(records)
    records.pop(str(author_id), None)
    state["author_evaluation_quarantines"] = records
    return True


def normalise_author_evaluation_quarantines(
    value: object,
    *,
    path: Path,
    AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY: str,
    AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY: str,
    AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY: str,
    AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY: str,
    AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY: str,
    MAX_REASONABLE_STATE_EPOCH: int,
    author_no_reply_epoch_limit: Callable,
    log: Logger,
) -> dict | None:
    """Validate compact per-author no-reply strike and quarantine records."""
    if not isinstance(value, dict):
        log.error(
            "State candidate %s has invalid author_evaluation_quarantines type %s; ignoring",
            path,
            type(value).__name__,
        )
        return None
    result: dict[str, dict[str, object]] = {}
    legacy_fields = {
        "recent_no_reply_epochs",
        "quarantine_until_epoch",
        "last_updated_epoch",
    }
    previous_policy_fields = legacy_fields | {"evidence_policy"}
    current_fields = previous_policy_fields | {
        "latest_explicit_spam_or_abuse_epoch"
    }
    for raw_author_id, raw_record in value.items():
        author_id = str(raw_author_id)
        if not author_id.isdigit() or not isinstance(raw_record, dict):
            return None
        record_fields = set(raw_record)
        if record_fields == legacy_fields:
            log.warning(
                "Discarding legacy broad author-evaluation quarantine for author_id=%s from %s",
                author_id,
                path,
            )
            continue
        if (
            record_fields != previous_policy_fields
            and record_fields != current_fields
        ):
            return None
        evidence_policy = raw_record.get("evidence_policy")
        is_legacy_policy = (
            record_fields == previous_policy_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY
        )
        is_seeded_policy = (
            record_fields == current_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY
        )
        is_previous_policy = (
            record_fields == current_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY
        )
        is_single_sol_v1_policy = (
            record_fields == current_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY
        )
        is_current_policy = (
            record_fields == current_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY
        )
        if not (
            is_legacy_policy
            or is_seeded_policy
            or is_previous_policy
            or is_single_sol_v1_policy
            or is_current_policy
        ):
            return None
        timestamps = raw_record.get("recent_no_reply_epochs")
        until = raw_record.get("quarantine_until_epoch")
        updated = raw_record.get("last_updated_epoch")
        if (
            not isinstance(timestamps, list)
            or any(
                type(timestamp) is not int
                or timestamp < 0
                or timestamp > MAX_REASONABLE_STATE_EPOCH
                for timestamp in timestamps
            )
            or len(timestamps) > author_no_reply_epoch_limit()
            or timestamps != sorted(timestamps)
        ):
            return None
        if (
            type(until) is not int
            or until < 0
            or until > MAX_REASONABLE_STATE_EPOCH
            or type(updated) is not int
            or updated < 0
            or updated > MAX_REASONABLE_STATE_EPOCH
        ):
            return None
        if is_legacy_policy:
            if not timestamps:
                if not until:
                    log.warning(
                        "Discarding empty prior-policy author-evaluation history "
                        "for author_id=%s from %s",
                        author_id,
                        path,
                    )
                    continue
                # A v1 quarantine can outlive its six-hour strike history.
                # Preserve that active-until value without manufacturing an
                # explicit-spam timestamp that the old record did not store.
                explicit_epoch = 0
            else:
                explicit_epoch = timestamps[-1]
            log.info(
                "Migrating prior explicit-spam author-evaluation history "
                "for author_id=%s from %s",
                author_id,
                path,
            )
        else:
            explicit_epoch = raw_record.get(
                "latest_explicit_spam_or_abuse_epoch"
            )
            if (
                type(explicit_epoch) is not int
                or explicit_epoch < 0
                or explicit_epoch > MAX_REASONABLE_STATE_EPOCH
                or explicit_epoch > updated
            ):
                return None
            if (
                is_seeded_policy
                and not until
                and explicit_epoch
                and explicit_epoch not in timestamps
            ):
                return None
            if is_seeded_policy:
                log.info(
                    "Migrating seeded-corroboration author-evaluation history "
                    "for author_id=%s from %s",
                    author_id,
                    path,
                )
            elif is_previous_policy:
                log.info(
                    "Migrating immediately preceding author-evaluation history "
                    "for author_id=%s from %s",
                    author_id,
                    path,
                )
            elif is_single_sol_v1_policy:
                # f9099605 recorded every editorial no_reply in ``timestamps``
                # but separately retained the latest explicit spam/abuse
                # decision.  Its normal pruning could age that explicit epoch
                # out of the timestamp list while retaining newer broad
                # editorial declines, so absence from the list is a valid old
                # state rather than corruption.  The current policy qualifies
                # only a still-represented explicit reason.
                timestamps = (
                    [explicit_epoch]
                    if explicit_epoch and explicit_epoch in timestamps
                    else []
                )
                until = 0
                log.info(
                    "Migrating preceding single-Sol author-evaluation history "
                    "for author_id=%s from %s",
                    author_id,
                    path,
                )
                if not timestamps:
                    continue
        result[author_id] = {
            "recent_no_reply_epochs": list(timestamps),
            "quarantine_until_epoch": until,
            "last_updated_epoch": updated,
            "latest_explicit_spam_or_abuse_epoch": explicit_epoch,
            "evidence_policy": AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        }
    return result


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
