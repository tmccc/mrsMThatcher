"""Own durable mention queue authority and normalization.

Seven root adapters supply current callbacks, settings, logger and path authority
on each call; the two dependency-free guard/reset helpers are root aliases.
Original bodies preserve strict validation, recovery and mutation order, shallow
pending copies, guard references and exact canonical page ownership.

Shared bounded ID/provenance primitives, terminal policy, state loading and
persistence, receipts, discovery and reply cycles remain in existing locations.
This owner adds no saves or provider work, retains no callbacks, configuration,
clients or state, and performs no import-time file, environment, provider, clock
or RNG work or reverse application import.
"""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger
from pathlib import Path


def normalise_mention_pagination(
    value: object,
    *,
    path: Path,
    bounded_tweet_id_value: Callable,
    log: Logger,
    mention_pagination_provenance_is_valid: Callable,
) -> dict[str, str] | None:
    """Normalise mention pagination."""
    if not isinstance(value, dict):
        log.error("State candidate %s has invalid mention_pagination type %s; ignoring", path, type(value).__name__)
        return None
    if not value:
        return {}
    if not set(value).issubset({"base_since_id", "next_token"}):
        log.error(
            "State candidate %s has unknown mention_pagination fields; ignoring",
            path,
        )
        return None
    base_since_id = value.get("base_since_id", "")
    next_token = value.get("next_token")
    if (
        not isinstance(base_since_id, str)
        or bounded_tweet_id_value(base_since_id, allow_empty=True) is None
    ):
        log.error(
            "State candidate %s has invalid mention pagination base; ignoring",
            path,
        )
        return None
    candidate = {
        "base_since_id": base_since_id,
        "next_token": next_token,
    }
    if not mention_pagination_provenance_is_valid(candidate):
        log.error(
            "State candidate %s has invalid mention pagination token; ignoring",
            path,
        )
        return None
    return candidate


def normalise_mention_backlog_reset_guard(
    value: object,
    *,
    path: Path,
    bounded_tweet_id_value: Callable,
    log: Logger,
) -> dict[str, object] | None:
    """Validate the watermark guard installed when mention provenance is reset."""
    if not isinstance(value, dict):
        log.error(
            "State candidate %s has invalid mention_backlog_reset_guard type %s; ignoring",
            path,
            type(value).__name__,
        )
        return None
    if not value:
        return {}
    if set(value) != {"base_since_id", "head_traversal_started"}:
        log.error(
            "State candidate %s has invalid mention_backlog_reset_guard fields; ignoring",
            path,
        )
        return None
    base_since_id = value.get("base_since_id")
    head_traversal_started = value.get("head_traversal_started")
    if (
        not isinstance(base_since_id, str)
        or bounded_tweet_id_value(base_since_id, allow_empty=True) is None
        or type(head_traversal_started) is not bool
    ):
        log.error(
            "State candidate %s has invalid mention_backlog_reset_guard values; ignoring",
            path,
        )
        return None
    return {
        "base_since_id": base_since_id,
        "head_traversal_started": head_traversal_started,
    }


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


def normalise_mention_backlog(
    value: object,
    *,
    path: Path,
    reset_token_overflow: bool = False,
    MAX_REASONABLE_STATE_EPOCH: int,
    MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT: int,
    bounded_tweet_id_value: Callable,
    log: Logger,
) -> dict | None:
    """Validate the durable state for one incomplete mention traversal."""
    if not isinstance(value, dict):
        log.error("State candidate %s has invalid mention_backlog type %s; ignoring", path, type(value).__name__)
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
        log.error("State candidate %s has invalid mention_backlog fields; ignoring", path)
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
        or bounded_tweet_id_value(since_id, allow_empty=True) is None
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
        or bounded_tweet_id_value(highest_id, allow_empty=True) is None
    ):
        return None
    if type(pages) is not int or pages < 0:
        return None
    if type(started) is not int or started < 0 or started > MAX_REASONABLE_STATE_EPOCH:
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
    if len(seen_tokens) > MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT:
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


def canonical_mention_pending_candidates(
    value: object,
    *,
    path: Path,
    bounded_tweet_id_value: Callable,
    log: Logger,
) -> dict[str, dict] | None:
    """Validate exact, bounded identities for the durable mention queue."""
    if not isinstance(value, dict):
        log.error(
            "State candidate %s has invalid mention_pending_candidates type %s; ignoring",
            path,
            type(value).__name__,
        )
        return None
    canonical: dict[str, dict] = {}
    seen_ids: set[int] = set()
    for map_key, candidate in value.items():
        embedded_id = candidate.get("id") if isinstance(candidate, dict) else None
        map_id = bounded_tweet_id_value(map_key)
        candidate_id = bounded_tweet_id_value(embedded_id)
        if (
            type(map_key) is not str
            or not isinstance(candidate, dict)
            or type(embedded_id) is not str
            or map_id is None
            or candidate_id is None
            or map_key != embedded_id
        ):
            log.error(
                "State candidate %s has a pending mention with invalid or mismatched identity; ignoring",
                path,
            )
            return None
        if candidate_id in seen_ids:
            log.error(
                "State candidate %s has duplicate pending mention identity; ignoring",
                path,
            )
            return None
        seen_ids.add(candidate_id)
        canonical[map_key] = dict(candidate)
    return canonical


def _emit_mention_authority_recovery(
    recovery: dict[str, object],
    *,
    path: Path,
    recovery_events: list[dict[str, object]] | None,
    log: Logger,
    log_event: Callable,
) -> None:
    if recovery_events is not None:
        recovery_events.append(recovery)
        return
    log.warning(
        "Resetting unsafe mention candidate authority reason=%s path=%s; "
        "watermark remains unchanged and %s pending candidate(s) were discarded",
        recovery.get("reason"),
        path,
        recovery.get("discarded_candidates", 0),
    )
    log_event("mention_backlog_reset", **recovery)


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


def validate_pending_mention_candidate_authority(
    state: dict,
    *,
    path: Path,
    recover_pending_identity: bool,
    recovery_events: list[dict[str, object]] | None = None,
    _emit_mention_authority_recovery: Callable,
    _reset_mention_candidate_authority: Callable,
    active_mention_backlog_reset_guard: Callable,
    bounded_tweet_id_value: Callable,
    canonical_mention_pending_candidates: Callable,
    log: Logger,
    normalise_mention_backlog: Callable,
    normalise_mention_backlog_reset_guard: Callable,
    normalise_mention_pagination: Callable,
    terminal_reply_evaluation: Callable,
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
        or bounded_tweet_id_value(watermark, allow_empty=True) is None
    ):
        log.error(
            "State candidate %s has no bounded mention watermark for pending authority",
            path,
        )
        return False, False
    watermark_value = bounded_tweet_id_value(watermark, allow_empty=True)
    assert watermark_value is not None

    raw_pending = state.get("mention_pending_candidates", {})
    pending = canonical_mention_pending_candidates(raw_pending, path=path)
    if pending is None:
        if not recover_pending_identity:
            return False, False
        discarded = len(raw_pending) if isinstance(raw_pending, dict) else 0
        _reset_mention_candidate_authority(state, watermark=watermark)
        _emit_mention_authority_recovery(
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
        and terminal_reply_evaluation(state, mention_id) is None
    }
    if deduplicated != pending:
        log.info(
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
        normalise_mention_backlog(raw_backlog, path=path)
        if isinstance(raw_backlog, dict)
        else None
    )
    pagination = (
        normalise_mention_pagination(raw_pagination, path=path)
        if isinstance(raw_pagination, dict)
        else None
    )
    guard = (
        normalise_mention_backlog_reset_guard(raw_guard, path=path)
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
        base_value = bounded_tweet_id_value(base, allow_empty=True)
        highest_value = bounded_tweet_id_value(highest, allow_empty=True)
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
                (candidate_value := bounded_tweet_id_value(mention_id)) is None
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
        if bounded_tweet_id_value(pagination_base, allow_empty=True) is None:
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
                _emit_mention_authority_recovery(
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
        _emit_mention_authority_recovery(
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
        _emit_mention_authority_recovery(
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
                (candidate_value := bounded_tweet_id_value(mention_id))
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
            _emit_mention_authority_recovery(
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


def mention_pagination_has_canonical_page_ownership(
    state: dict,
    pagination: object,
    *,
    target_id: str,
    STATE_FILE: Path,
    bounded_tweet_id_value: Callable,
    mention_pagination_provenance_is_valid: Callable,
    normalise_mention_backlog: Callable,
) -> bool:
    """Return whether canonical state owns the page bound into a receipt."""
    if not mention_pagination_provenance_is_valid(pagination):
        return False
    if state.get("mention_pagination") != pagination:
        return False
    backlog = state.get("mention_backlog")
    if not isinstance(backlog, dict) or not backlog:
        return False
    normalised = normalise_mention_backlog(backlog, path=STATE_FILE)
    if normalised is None or normalised != backlog:
        return False
    watermark = str(state.get("last_seen_mention_id") or "")
    base = str(normalised.get("since_id") or "")
    highest = str(normalised.get("highest_mention_id") or "")
    base_value = bounded_tweet_id_value(base, allow_empty=True)
    highest_value = bounded_tweet_id_value(highest)
    target_value = bounded_tweet_id_value(target_id)
    return bool(
        base == watermark == pagination["base_since_id"]
        and normalised.get("next_token") == pagination["next_token"]
        and int(normalised.get("pages_completed", 0)) > 0
        and base_value is not None
        and highest_value is not None
        and target_value is not None
        and base_value < target_value <= highest_value
    )
