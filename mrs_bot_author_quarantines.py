"""Own mention-author quarantine records, expiry and policy migrations.

AuthorQuarantines binds current policy, thresholds, clock and logging without
retaining caller state. Strike recording, active lookup and expiry call owned
methods directly. Normalization preserves historical policy migrations without
consulting the clock. Pure clearing retains surviving record references.
Candidate decisions, terminal evaluations, durable saves and remote operations
stay with their existing owners. Import and construction perform no runtime
access.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mrs_bot_core_contracts import BotState


AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY = (
    "majority_resolvable_terminal_no_reply_v3"
)

AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY = (
    "single_sol_editorial_no_reply_v1"
)

AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY = (
    "majority_spam_or_abuse_seeded_corroboration_v2"
)

AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY = (
    "majority_spam_or_abuse_v1"
)


def clear_author_evaluation_quarantine_history(state: BotState, author_id: str) -> bool:
    """Clear prior strikes when a mention receives a validated reply."""
    records = state.get("author_evaluation_quarantines")
    if not isinstance(records, dict) or str(author_id) not in records:
        return False
    records = dict(records)
    records.pop(str(author_id), None)
    state["author_evaluation_quarantines"] = records
    return True


@dataclass(frozen=True)
class AuthorQuarantines:
    """Manage author quarantine state using current policy and explicit state."""

    now_epoch: Callable
    log: Logger
    log_event: Callable
    maximum_state_epoch: int
    threshold: int
    window_seconds: int
    quarantine_seconds: int
    evidence_policy: str

    def epoch_limit(self) -> int:
        """Return retention sized from the effective runtime quarantine threshold."""
        return max(100, int(self.threshold) * 4)

    def prune(self, state: BotState | dict, *, current_epoch: int | None=None) -> bool:
        """Expire quarantines and discard author strikes outside the live window."""
        records = state.get("author_evaluation_quarantines")
        if not isinstance(records, dict):
            state["author_evaluation_quarantines"] = {}
            return True
        if not records:
            return False
        current = self.now_epoch() if current_epoch is None else int(current_epoch)
        cutoff = current - self.window_seconds
        retained: dict[str, dict[str, object]] = {}
        changed = False
        for author_id, raw_record in records.items():
            if not isinstance(raw_record, dict):
                changed = True
                continue
            if (
                raw_record.get("evidence_policy")
                != self.evidence_policy
            ):
                changed = True
                continue
            explicit_epoch = raw_record.get(
                "latest_explicit_spam_or_abuse_epoch"
            )
            if (
                type(explicit_epoch) is not int
                or explicit_epoch < 0
                or explicit_epoch > self.maximum_state_epoch
            ):
                changed = True
                continue
            recent = [
                int(epoch)
                for epoch in raw_record.get("recent_no_reply_epochs", [])
                if type(epoch) is int and cutoff < epoch <= current
            ]
            recent.sort()
            recent = recent[-self.epoch_limit():]
            until = int(raw_record.get("quarantine_until_epoch", 0) or 0)
            updated = int(raw_record.get("last_updated_epoch", 0) or 0)
            if until and until <= current:
                self.log_event(
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
                    "evidence_policy": self.evidence_policy,
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

    def active(
        self,
        state: BotState,
        author_id: str,
        *,
        current_epoch: int | None = None,
    ) -> dict | None:
        """Return an active mention-lane author quarantine, expiring old state first."""
        current = self.now_epoch() if current_epoch is None else int(current_epoch)
        self.prune(state, current_epoch=current)
        records = state.get("author_evaluation_quarantines", {})
        record = records.get(str(author_id)) if isinstance(records, dict) else None
        if (
            isinstance(record, dict)
            and int(record.get("quarantine_until_epoch", 0) or 0) > current
        ):
            return record
        return None

    def record_no_reply(
        self,
        state: BotState,
        author_id: str,
        *,
        current_epoch: int | None = None,
        explicit_spam_or_abuse: bool = True,
    ) -> bool:
        """Add one mechanically valid editorial no-reply strike for an author."""
        author_id = str(author_id)
        if not author_id or not author_id.isdigit():
            return False
        current = self.now_epoch() if current_epoch is None else int(current_epoch)
        self.prune(state, current_epoch=current)
        records = state.get("author_evaluation_quarantines", {})
        if not isinstance(records, dict):
            records = {}
        records = dict(records)
        existing = records.get(author_id, {})
        if not isinstance(existing, dict):
            existing = {}
        cutoff = current - self.window_seconds
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
        recent = recent[-self.epoch_limit():]
        until = int(existing.get("quarantine_until_epoch", 0) or 0)
        started = False
        if until <= current and len(recent) >= self.threshold:
            until = current + self.quarantine_seconds
            started = True
            self.log_event(
                "author_evaluation_quarantine_started",
                author_id=author_id,
                strike_count=len(recent),
                threshold=self.threshold,
                window_seconds=self.window_seconds,
                quarantine_seconds=self.quarantine_seconds,
                quarantine_until_epoch=until,
            )
        records[author_id] = {
            "recent_no_reply_epochs": recent,
            "quarantine_until_epoch": until,
            "last_updated_epoch": current,
            "latest_explicit_spam_or_abuse_epoch": explicit_epoch,
            "evidence_policy": self.evidence_policy,
        }
        state["author_evaluation_quarantines"] = records
        return started

    def normalise(self, value: object, *, path: Path) -> dict | None:
        """Validate compact per-author no-reply strike and quarantine records."""
        if not isinstance(value, dict):
            self.log.error(
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
                self.log.warning(
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
                == self.evidence_policy
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
                    or timestamp > self.maximum_state_epoch
                    for timestamp in timestamps
                )
                or len(timestamps) > self.epoch_limit()
                or timestamps != sorted(timestamps)
            ):
                return None
            if (
                type(until) is not int
                or until < 0
                or until > self.maximum_state_epoch
                or type(updated) is not int
                or updated < 0
                or updated > self.maximum_state_epoch
            ):
                return None
            if is_legacy_policy:
                if not timestamps:
                    if not until:
                        self.log.warning(
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
                self.log.info(
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
                    or explicit_epoch > self.maximum_state_epoch
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
                    self.log.info(
                        "Migrating seeded-corroboration author-evaluation history "
                        "for author_id=%s from %s",
                        author_id,
                        path,
                    )
                elif is_previous_policy:
                    self.log.info(
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
                    self.log.info(
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
                "evidence_policy": self.evidence_policy,
            }
        return result
