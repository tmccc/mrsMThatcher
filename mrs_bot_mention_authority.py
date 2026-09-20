"""Own durable mention queue authority and normalization.

A fresh MentionAuthority binds current paths, caps, diagnostics and external
ID/provenance/terminal checks for each root invocation. Validation calls owned
normalizers and recovery reporting directly; pure guard/reset root aliases
remain local primitives. No caller state is retained and no saves are added.

Strict validation, partial mutation and recovery ordering, shallow pending
copies, guard references and exact page ownership stay unchanged. Discovery,
receipt persistence and state loading keep their existing boundaries. Import
performs no file, environment, provider, clock or RNG work.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from pathlib import Path


def active_mention_backlog_reset_guard(state: dict) -> dict[str, object] | None:
    """Return the reset guard only while it is bound to the current watermark."""
    guard = state.get("mention_backlog_reset_guard")
    if (
        not isinstance(guard, dict)
        or set(guard) != {"base_since_id", "head_traversal_started"}
        or not isinstance(guard.get("base_since_id"), str)
        or type(guard.get("head_traversal_started")) is not bool
        or str(guard["base_since_id"])
        != str(state.get("last_seen_mention_id") or "")
    ):
        return None
    return guard


def _reset_mention_candidate_authority(
    state: dict,
    *,
    watermark: str,
) -> None:
    """Clear one unsafe traversal generation and require a head traversal."""
    state["mention_backlog"] = {}
    state["mention_pagination"] = {}
    state["mention_pending_candidates"] = {}
    state["mention_backlog_reset_guard"] = {
        "base_since_id": watermark,
        "head_traversal_started": False,
    }



@dataclass(frozen=True)
class MentionAuthority:
    """Own mention queue normalization, provenance validation and recovery."""

    state_file: Path
    maximum_epoch: int
    token_limit: int
    bounded_id: Callable[..., int | None]
    log: Logger
    valid_provenance: Callable[[object], bool]
    log_event: Callable
    terminal_evaluation: Callable

    def normalise_pagination(self, value: object, *, path: Path) -> dict[str, str] | None:
        """Normalise mention pagination."""
        if not isinstance(value, dict):
            self.log.error("State candidate %s has invalid mention_pagination type %s; ignoring", path, type(value).__name__)
            return None
        if not value:
            return {}
        if not set(value).issubset({"base_since_id", "next_token"}):
            self.log.error(
                "State candidate %s has unknown mention_pagination fields; ignoring",
                path,
            )
            return None
        base_since_id = value.get("base_since_id", "")
        next_token = value.get("next_token")
        if (
            not isinstance(base_since_id, str)
            or self.bounded_id(base_since_id, allow_empty=True) is None
        ):
            self.log.error(
                "State candidate %s has invalid mention pagination base; ignoring",
                path,
            )
            return None
        candidate = {
            "base_since_id": base_since_id,
            "next_token": next_token,
        }
        if not self.valid_provenance(candidate):
            self.log.error(
                "State candidate %s has invalid mention pagination token; ignoring",
                path,
            )
            return None
        return candidate

    def normalise_reset_guard(self, value: object, *, path: Path) -> dict[str, object] | None:
        """Validate the watermark guard installed when mention provenance is reset."""
        if not isinstance(value, dict):
            self.log.error(
                "State candidate %s has invalid mention_backlog_reset_guard type %s; ignoring",
                path,
                type(value).__name__,
            )
            return None
        if not value:
            return {}
        if set(value) != {"base_since_id", "head_traversal_started"}:
            self.log.error(
                "State candidate %s has invalid mention_backlog_reset_guard fields; ignoring",
                path,
            )
            return None
        base_since_id = value.get("base_since_id")
        head_traversal_started = value.get("head_traversal_started")
        if (
            not isinstance(base_since_id, str)
            or self.bounded_id(base_since_id, allow_empty=True) is None
            or type(head_traversal_started) is not bool
        ):
            self.log.error(
                "State candidate %s has invalid mention_backlog_reset_guard values; ignoring",
                path,
            )
            return None
        return {
            "base_since_id": base_since_id,
            "head_traversal_started": head_traversal_started,
        }

    def normalise_backlog(self, value: object, *, path: Path, reset_token_overflow: bool=False) -> dict | None:
        """Validate the durable state for one incomplete mention traversal."""
        if not isinstance(value, dict):
            self.log.error("State candidate %s has invalid mention_backlog type %s; ignoring", path, type(value).__name__)
            return None
        if not value:
            return {}
        expected = {
            "since_id",
            "next_token",
            "highest_mention_id",
            "pages_completed",
            "started_epoch",
            "seen_tokens",
            "announced",
        }
        if set(value) != expected:
            self.log.error("State candidate %s has invalid mention_backlog fields; ignoring", path)
            return None
        since_id = value.get("since_id")
        next_token = value.get("next_token")
        highest_id = value.get("highest_mention_id")
        pages = value.get("pages_completed")
        started = value.get("started_epoch")
        seen_tokens = value.get("seen_tokens")
        announced = value.get("announced")
        if (
            not isinstance(since_id, str)
            or self.bounded_id(since_id, allow_empty=True) is None
        ):
            return None
        if (
            not isinstance(next_token, str)
            or next_token != next_token.strip()
            or any(character.isspace() for character in next_token)
        ):
            return None
        if (
            not isinstance(highest_id, str)
            or self.bounded_id(highest_id, allow_empty=True) is None
        ):
            return None
        if type(pages) is not int or pages < 0:
            return None
        if type(started) is not int or started < 0 or started > self.maximum_epoch:
            return None
        if (
            not isinstance(seen_tokens, list)
            or any(
                not isinstance(token, str)
                or not token
                or token != token.strip()
                or any(character.isspace() for character in token)
                for token in seen_tokens
            )
            or len(set(seen_tokens)) != len(seen_tokens)
        ):
            return None
        if type(announced) is not bool:
            return None
        if len(seen_tokens) > self.token_limit:
            return {} if reset_token_overflow else None
        return {
            "since_id": since_id,
            "next_token": next_token,
            "highest_mention_id": highest_id,
            "pages_completed": pages,
            "started_epoch": started,
            "seen_tokens": list(seen_tokens),
            "announced": announced,
        }

    def canonical_candidates(self, value: object, *, path: Path) -> dict[str, dict] | None:
        """Validate exact, bounded identities for the durable mention queue."""
        if not isinstance(value, dict):
            self.log.error(
                "State candidate %s has invalid mention_pending_candidates type %s; ignoring",
                path,
                type(value).__name__,
            )
            return None
        canonical: dict[str, dict] = {}
        seen_ids: set[int] = set()
        for map_key, candidate in value.items():
            embedded_id = candidate.get("id") if isinstance(candidate, dict) else None
            map_id = self.bounded_id(map_key)
            candidate_id = self.bounded_id(embedded_id)
            if (
                type(map_key) is not str
                or not isinstance(candidate, dict)
                or type(embedded_id) is not str
                or map_id is None
                or candidate_id is None
                or map_key != embedded_id
            ):
                self.log.error(
                    "State candidate %s has a pending mention with invalid or mismatched identity; ignoring",
                    path,
                )
                return None
            if candidate_id in seen_ids:
                self.log.error(
                    "State candidate %s has duplicate pending mention identity; ignoring",
                    path,
                )
                return None
            seen_ids.add(candidate_id)
            canonical[map_key] = dict(candidate)
        return canonical

    def emit_recovery(
        self,
        recovery: dict[str, object],
        *,
        path: Path,
        recovery_events: list[dict[str, object]] | None,
    ) -> None:
        """Collect recovery evidence or emit the existing reset diagnostics."""
        if recovery_events is not None:
            recovery_events.append(recovery)
            return
        self.log.warning(
            "Resetting unsafe mention candidate authority reason=%s path=%s; "
            "watermark remains unchanged and %s pending candidate(s) were discarded",
            recovery.get("reason"),
            path,
            recovery.get("discarded_candidates", 0),
        )
        self.log_event("mention_backlog_reset", **recovery)

    def validate_pending(
        self,
        state: dict,
        *,
        path: Path,
        recover_pending_identity: bool,
        recovery_events: list[dict[str, object]] | None = None,
    ) -> tuple[bool, bool]:
        """Canonicalise the queue and reject candidates without coherent provenance.

        The boolean pair is ``(usable, changed)``.  A strict state-loader pass uses
        ``recover_pending_identity=False`` so a usable backup wins over recovery of
        a corrupt primary.  Runtime queue and receipt callers recover fail-closed.
        """
        raw_watermark = state.get("last_seen_mention_id")
        watermark = "" if raw_watermark in (None, "") else raw_watermark
        if (
            type(watermark) is not str
            or self.bounded_id(watermark, allow_empty=True) is None
        ):
            self.log.error(
                "State candidate %s has no bounded mention watermark for pending authority",
                path,
            )
            return False, False
        watermark_value = self.bounded_id(watermark, allow_empty=True)
        assert watermark_value is not None

        raw_pending = state.get("mention_pending_candidates", {})
        pending = self.canonical_candidates(raw_pending, path=path)
        if pending is None:
            if not recover_pending_identity:
                return False, False
            discarded = len(raw_pending) if isinstance(raw_pending, dict) else 0
            _reset_mention_candidate_authority(state, watermark=watermark)
            self.emit_recovery(
                {
                    "reason": "invalid_pending_candidate_identity",
                    "since_id": watermark or None,
                    "discarded_candidates": discarded,
                },
                path=path,
                recovery_events=recovery_events,
            )
            return True, True

        changed = pending != raw_pending
        replied_ids = {
            str(value)
            for key in ("replied_to_ids", "replied_to_quote_post_ids")
            for value in state.get(key, [])
        }
        deduplicated = {
            mention_id: candidate
            for mention_id, candidate in pending.items()
            if mention_id not in replied_ids
            and self.terminal_evaluation(state, mention_id) is None
        }
        if deduplicated != pending:
            self.log.info(
                "Disposed of %s already handled durable mention candidate(s) without provider work",
                len(pending) - len(deduplicated),
            )
            pending = deduplicated
            changed = True
        if pending != raw_pending:
            state["mention_pending_candidates"] = pending

        raw_backlog = state.get("mention_backlog", {})
        raw_pagination = state.get("mention_pagination", {})
        raw_guard = state.get("mention_backlog_reset_guard", {})
        authority_failure = ""

        backlog = (
            self.normalise_backlog(raw_backlog, path=path)
            if isinstance(raw_backlog, dict)
            else None
        )
        pagination = (
            self.normalise_pagination(raw_pagination, path=path)
            if isinstance(raw_pagination, dict)
            else None
        )
        guard = (
            self.normalise_reset_guard(raw_guard, path=path)
            if isinstance(raw_guard, dict)
            else None
        )
        if backlog is None:
            authority_failure = "invalid_backlog"
        elif pagination is None:
            authority_failure = "invalid_pagination"
        elif guard is None:
            authority_failure = "invalid_reset_guard"
        elif guard and active_mention_backlog_reset_guard(state) is None:
            authority_failure = "reset_guard_base_mismatch"

        if not authority_failure and backlog:
            base = backlog["since_id"]
            highest = backlog["highest_mention_id"]
            next_token = backlog["next_token"]
            base_value = self.bounded_id(base, allow_empty=True)
            highest_value = self.bounded_id(highest, allow_empty=True)
            if base_value is None or highest_value is None:
                authority_failure = "unbounded_backlog_identity"
            elif base != watermark:
                authority_failure = "backlog_base_mismatch"
            elif highest_value < watermark_value:
                authority_failure = "backlog_highest_before_watermark"
            elif next_token:
                expected_pagination = {
                    "base_since_id": base,
                    "next_token": next_token,
                }
                if int(backlog["pages_completed"]) <= 0:
                    authority_failure = "continuation_without_completed_page"
                elif pagination != expected_pagination:
                    authority_failure = "backlog_pagination_mismatch"
                elif pending and not highest:
                    authority_failure = "pending_without_highest_identity"
                elif any(
                    (candidate_value := self.bounded_id(mention_id)) is None
                    or candidate_value <= watermark_value
                    or candidate_value > highest_value
                    for mention_id in pending
                ):
                    authority_failure = "pending_outside_active_page_range"
            elif int(backlog["pages_completed"]) != 0:
                authority_failure = "completed_backlog_without_continuation"
            elif highest != watermark:
                authority_failure = "head_backlog_highest_mismatch"
            elif pagination:
                authority_failure = "pagination_without_backlog_continuation"
            elif pending:
                authority_failure = "pending_before_first_completed_page"

        if not authority_failure and not backlog and pagination:
            pagination_base = pagination["base_since_id"]
            if self.bounded_id(pagination_base, allow_empty=True) is None:
                authority_failure = "unbounded_pagination_base"
            elif pagination_base != watermark:
                authority_failure = "pagination_base_mismatch"
            else:
                # A cursor can recover the unseen tail, but it cannot prove page
                # ownership or authorize watermark advancement.  Always bind it
                # to a false reset guard and require a later head traversal.
                discarded = len(pending)
                state["mention_pending_candidates"] = {}
                state["mention_backlog_reset_guard"] = {
                    "base_since_id": watermark,
                    "head_traversal_started": False,
                }
                if pending or guard != state["mention_backlog_reset_guard"]:
                    self.emit_recovery(
                        {
                            "reason": "pagination_without_page_ownership",
                            "since_id": watermark or None,
                            "discarded_candidates": discarded,
                        },
                        path=path,
                        recovery_events=recovery_events,
                    )
                    return True, True

        if (
            not authority_failure
            and not backlog
            and not pagination
            and guard
            and guard["head_traversal_started"]
        ):
            # A true guard without its owning head backlog records an interrupted
            # traversal.  A false guard may legitimately own candidates fetched
            # from a restored tail cursor whose completion deferred the watermark.
            discarded = len(pending)
            state["mention_pending_candidates"] = {}
            state["mention_backlog_reset_guard"] = {
                "base_since_id": watermark,
                "head_traversal_started": False,
            }
            self.emit_recovery(
                {
                    "reason": "head_traversal_ownership_missing",
                    "since_id": watermark or None,
                    "discarded_candidates": discarded,
                },
                path=path,
                recovery_events=recovery_events,
            )
            return True, True

        if authority_failure:
            discarded = len(pending)
            _reset_mention_candidate_authority(state, watermark=watermark)
            self.emit_recovery(
                {
                    "reason": "stale_pending_candidate_authority",
                    "authority_failure": authority_failure,
                    "since_id": watermark or None,
                    "discarded_candidates": discarded,
                },
                path=path,
                recovery_events=recovery_events,
            )
            return True, True

        if not backlog and not pagination and pending and not guard:
            covered = {
                mention_id: candidate
                for mention_id, candidate in pending.items()
                if (
                    (candidate_value := self.bounded_id(mention_id))
                    is not None
                    and candidate_value <= watermark_value
                )
            }
            discarded = len(pending) - len(covered)
            if discarded:
                state["mention_pending_candidates"] = covered
                state["mention_backlog_reset_guard"] = {
                    "base_since_id": watermark,
                    "head_traversal_started": False,
                }
                self.emit_recovery(
                    {
                        "reason": "orphaned_pending_candidates",
                        "since_id": watermark or None,
                        "discarded_candidates": discarded,
                    },
                    path=path,
                    recovery_events=recovery_events,
                )
                return True, True

        return True, changed

    def owns_page(self, state: dict, pagination: object, *, target_id: str) -> bool:
        """Return whether canonical state owns the page bound into a receipt."""
        if not self.valid_provenance(pagination):
            return False
        if state.get("mention_pagination") != pagination:
            return False
        backlog = state.get("mention_backlog")
        if not isinstance(backlog, dict) or not backlog:
            return False
        normalised = self.normalise_backlog(backlog, path=self.state_file)
        if normalised is None or normalised != backlog:
            return False
        watermark = str(state.get("last_seen_mention_id") or "")
        base = str(normalised.get("since_id") or "")
        highest = str(normalised.get("highest_mention_id") or "")
        base_value = self.bounded_id(base, allow_empty=True)
        highest_value = self.bounded_id(highest)
        target_value = self.bounded_id(target_id)
        return bool(
            base == watermark == pagination["base_since_id"]
            and normalised.get("next_token") == pagination["next_token"]
            and int(normalised.get("pages_completed", 0)) > 0
            and base_value is not None
            and highest_value is not None
            and target_value is not None
            and base_value < target_value <= highest_value
        )


def mention_receipt_pagination(
    state: dict,
    candidate: dict,
    candidate_source: str,
    *,
    mention_pagination_provenance_is_valid: Callable[[object], bool],
) -> dict | None:
    """Copy a mention receipt's continuation only while it matches durable state.

    Candidate provenance takes precedence for a truncated batch. The lane calls
    this after its draft save and context/draft snapshots; this operation adds
    no durable writes and does not apply the stricter canonical-page check.
    """
    if candidate_source == "mention":
        pagination = (
            candidate.get("_mention_pagination")
            if candidate.get("_pagination_truncated")
            else state.get("mention_pagination")
        )
    else:
        pagination = None
    if candidate_source != "mention" or not pagination:
        return None
    if not mention_pagination_provenance_is_valid(pagination):
        raise RuntimeError(
            "Refusing to post a reply from a truncated mention batch "
            "without valid pagination provenance"
        )
    base_since_id = str(pagination["base_since_id"])
    current_since_id = str(state.get("last_seen_mention_id") or "")
    active_pagination = state.get("mention_pagination")
    if (
        current_since_id != base_since_id
        or not mention_pagination_provenance_is_valid(active_pagination)
        or active_pagination != pagination
    ):
        raise RuntimeError(
            "Refusing to post a reply whose mention pagination "
            "provenance no longer matches durable state"
        )
    return copy.deepcopy(pagination)
