"""Validate state candidates and compatibility through current root dependencies.

Four explicit root adapters supply current settings, reader version,
error class, logger and nested normalization, recovery and schedule callbacks
on every call. Original bodies preserve reader and schedule error order, per-call
key sets, shallow references, partial recovery events and pending-authority order.
StateValues supplies scalar/collection operations through a fresh owner lookup at
each original normalization point. MentionAuthority is also resolved afresh for
queue normalization and recovery, including after terminal pruning. State loading,
defaults/schema, persistence
and higher-level recovery remain in existing locations. This owner retains no callbacks, configuration,
paths or state and performs no import-time runtime work or reverse bot import.
Fixed fingerprint hashing and receipt-commit grammar belong to their local owners.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING

from mrs_bot_state_generation import receipt_commit_records_are_valid

if TYPE_CHECKING:
    from mrs_bot_state_value_normalisation import StateValues
    from mrs_bot_mention_authority import MentionAuthority


def validate_meme_schedule_state(
    state: dict,
    *,
    path: Path,
    MAIN_POST_SCHEDULE_TIMEZONE: str,
    MEME_SCHEDULE_MODES: set[str],
    log: Logger,
    safe_bound_schedule_date_str: Callable[..., str | None],
    valid_receipt_epoch: Callable[..., bool],
) -> bool:
    """Validate meme schedule state."""
    next_epoch = int(state.get("next_meme_post_epoch", 0) or 0)
    if not next_epoch:
        return True
    if not valid_receipt_epoch(next_epoch):
        log.error("State candidate %s has receipt-incompatible active meme target epoch %s; ignoring", path, next_epoch)
        return False

    mode = str(state.get("next_meme_schedule_mode", "") or "")
    schedule_date = str(state.get("next_meme_schedule_date", "") or "")
    anchor_epoch = int(state.get("meme_anchor_quote_post_epoch", 0) or 0)

    if mode not in MEME_SCHEDULE_MODES or not mode:
        log.error("State candidate %s has invalid meme schedule mode %r; ignoring", path, mode)
        return False

    if mode == "after_first_quote_after_midday":
        if anchor_epoch <= 0:
            log.error("State candidate %s has quote-anchored meme schedule without anchor; ignoring", path)
            return False
        if not valid_receipt_epoch(anchor_epoch):
            log.error("State candidate %s has receipt-incompatible meme anchor epoch %s; ignoring", path, anchor_epoch)
            return False
        if next_epoch <= anchor_epoch:
            log.error("State candidate %s has quote-anchored meme target not after anchor; ignoring", path)
            return False
        expected_date = safe_bound_schedule_date_str(
            anchor_epoch,
            MAIN_POST_SCHEDULE_TIMEZONE,
        )
        if not expected_date or schedule_date != expected_date:
            log.error(
                "State candidate %s has quote-anchored meme schedule_date=%r expected=%r; ignoring",
                path,
                schedule_date,
                expected_date,
            )
            return False
        return True

    if anchor_epoch:
        log.error("State candidate %s has non-quote meme schedule with stale quote anchor; ignoring", path)
        return False
    expected_date = safe_bound_schedule_date_str(
        next_epoch,
        MAIN_POST_SCHEDULE_TIMEZONE,
    )
    if not expected_date or schedule_date != expected_date:
        log.error(
            "State candidate %s has meme schedule_date=%r expected=%r for mode=%s; ignoring",
            path,
            schedule_date,
            expected_date,
            mode,
        )
        return False
    return True


def validate_meme_schedule_version_for_candidate(
    state: dict,
    *,
    path: Path,
    MEME_SCHEDULE_VERSION: int,
    log: Logger,
    validate_meme_schedule_state: Callable[..., bool],
) -> bool:
    """Validate meme schedule version for candidate."""
    version = int(state.get("meme_schedule_version", 0) or 0)
    if version > MEME_SCHEDULE_VERSION:
        log.error(
            "State candidate %s has future meme_schedule_version=%s > supported=%s; ignoring",
            path,
            version,
            MEME_SCHEDULE_VERSION,
        )
        return False
    if version < MEME_SCHEDULE_VERSION:
        log.info(
            "State candidate %s has old meme_schedule_version=%s; deferring schedule validation to migration",
            path,
            version,
        )
        return True
    return validate_meme_schedule_state(state, path=path)


def require_compatible_state_reader(
    state: dict,
    *,
    path: Path,
    reader_version: int | None = None,
    IncompatibleStateReaderError: type[Exception],
    STATE_READER_VERSION: int,
) -> int:
    """Return the declared minimum after rejecting an incompatible reader."""
    raw_minimum = state.get("minimum_reader_version", 1)
    if type(raw_minimum) is not int or raw_minimum < 1:
        raise IncompatibleStateReaderError(
            f"State candidate {path} has invalid minimum reader version "
            f"{raw_minimum!r}"
        )
    supported = STATE_READER_VERSION if reader_version is None else reader_version
    if type(supported) is not int or supported < 1:
        raise ValueError("reader_version must be a positive integer")
    if raw_minimum > supported:
        raise IncompatibleStateReaderError(
            f"State candidate {path} requires minimum reader version "
            f"{raw_minimum}, but this executable supports {supported}; refusing "
            "state mutation and backup fallback"
        )
    return raw_minimum


def normalise_state_candidate(
    state: dict,
    *,
    path: Path,
    recovery_events: list[dict[str, object]] | None = None,
    recover_pending_identity: bool = False,
    MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT: int,
    STATE_MINIMUM_READER_VERSION: int,
    default_state: Callable[..., dict],
    log: Logger,
    normalise_author_evaluation_quarantines: Callable[..., dict | None],
    normalise_quote_repeated_cursor_suppressions: Callable[..., tuple[dict[str, dict[str, object]], int]],
    normalise_tweet_cache: Callable[..., dict[str, dict] | None],
    state_values: Callable[[], StateValues],
    mention_authority: Callable[[], MentionAuthority],
    prune_author_evaluation_quarantines: Callable[..., bool],
    prune_reply_evaluation_records: Callable[..., None],
    require_compatible_state_reader: Callable[..., int],
    validate_meme_schedule_version_for_candidate: Callable[..., bool],
) -> dict | None:
    """Normalise state candidate."""
    minimum_reader_version = require_compatible_state_reader(state, path=path)
    commits = state.get('_confirmed_receipt_commits', {})
    if not receipt_commit_records_are_valid(commits):
        log.error('State candidate %s has invalid confirmed receipt commit identities', path)
        return None
    list_keys = {
        "replied_to_ids",
        "dry_run_seen_mention_ids",
        "skipped_hot_reply_ids",
        "daily_replied_author_ids",
        "own_auto_reply_ids",
        "posted_meme_filenames",
        "recent_own_post_ids",
        "seen_quote_post_ids",
        "replied_to_quote_post_ids",
        "skipped_quote_post_ids",
        "quote_spam_author_ids",
    }
    epoch_list_keys = {
        "x_error_epochs",
        "x_write_error_epochs",
        "openai_error_epochs",
        "quote_x_error_epochs",
    }
    string_map_keys = {
        "hot_post_reply_since_ids",
        "hot_post_reply_pagination_tokens",
        "quote_lookup_pagination_tokens",
        "quote_search_pagination_tokens",
    }
    int_map_keys = {"hot_post_reply_check_counts", "daily_replied_author_counts"}
    record_map_keys = {
        "skipped_hot_reply_records",
        "pending_ai_reply_drafts",
        "reply_evaluation_records",
        "clarification_reply_records",
    }
    optional_scalar_keys = {
        "daily_reply_date",
        "daily_quote_reply_date",
        "last_regular_image_filename",
    }
    optional_numeric_id_keys = {
        "last_seen_mention_id",
        "last_main_post_id",
    }
    int_keys = {
        "daily_reply_count",
        "meme_schedule_version",
        "daily_quote_reply_count",
        "original_regular_posts_since_generated_image",  # Retained legacy state only.
    }
    epoch_keys = {
        "last_meme_post_epoch",
        "next_meme_post_epoch",
        "meme_anchor_quote_post_epoch",
        "last_reply_epoch",
        "last_quote_post_epoch",
        "next_quote_post_epoch",
        "api_cooldown_until_epoch",
        "x_write_api_cooldown_until_epoch",
        "openai_api_cooldown_until_epoch",
        "quote_api_cooldown_until_epoch",
    }

    normalised = default_state()
    normalised.update(state)
    normalised["minimum_reader_version"] = max(
        minimum_reader_version,
        STATE_MINIMUM_READER_VERSION,
    )

    for key in list_keys:
        if key in state:
            value = state[key]
            value = state_values().strings(value, key=key, path=path)
            if value is None:
                return None
            normalised[key] = value
    for key in epoch_list_keys:
        if key in state:
            value = state[key]
            value = state_values().epochs(value, key=key, path=path)
            if value is None:
                return None
            normalised[key] = value
    for key in string_map_keys:
        if key in state:
            value = state[key]
            value = state_values().string_map(value, key=key, path=path)
            if value is None:
                return None
            normalised[key] = value
    if "quote_lookup_repeated_cursor_suppressions" in state:
        value, discarded = normalise_quote_repeated_cursor_suppressions(
            state["quote_lookup_repeated_cursor_suppressions"]
        )
        normalised["quote_lookup_repeated_cursor_suppressions"] = value
        if discarded and recovery_events is not None:
            recovery_events.append({
                "kind": "quote_cursor_suppression_pruned",
                "discarded_entries": discarded,
            })
    for key in int_map_keys:
        if key in state:
            value = state[key]
            value = state_values().integer_map(value, key=key, path=path)
            if value is None:
                return None
            normalised[key] = value
    for key in record_map_keys:
        if key in state:
            value = state[key]
            value = state_values().record_map(value, key=key, path=path)
            if value is None:
                return None
            normalised[key] = value
    if "mention_pending_candidates" in state:
        pending_value = state["mention_pending_candidates"]
        pending_value = mention_authority().canonical_candidates(
            pending_value,
            path=path,
        )
        if pending_value is None and not recover_pending_identity:
            return None
        if pending_value is not None:
            normalised["mention_pending_candidates"] = pending_value
    for key in optional_scalar_keys:
        if key in state:
            value = state[key]
            value = state_values().optional_scalar(value, key=key, path=path)
            if value is None:
                return None
            normalised[key] = value or None
    for key in optional_numeric_id_keys:
        if key in state:
            value = state[key]
            value = state_values().optional_id(value, key=key, path=path)
            if value is None:
                return None
            normalised[key] = value or None
    if "tweet_cache" in state:
        value = normalise_tweet_cache(state["tweet_cache"], path=path)
        if value is None:
            return None
        normalised["tweet_cache"] = value
    if "reply_strategy_history" in state:
        history = state["reply_strategy_history"]
        if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
            log.error("State candidate %s has invalid reply_strategy_history; ignoring", path)
            return None
        normalised["reply_strategy_history"] = history[-1000:]
    if "ai_reply_history" in state:
        history = state["ai_reply_history"]
        if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
            log.error("State candidate %s has invalid ai_reply_history; ignoring", path)
            return None
        normalised["ai_reply_history"] = history[-1000:]
    if "mention_pagination" in state:
        value = state["mention_pagination"]
        value = mention_authority().normalise_pagination(value, path=path)
        if value is None:
            return None
        normalised["mention_pagination"] = value
    if "mention_backlog_reset_guard" in state:
        value = state["mention_backlog_reset_guard"]
        value = mention_authority().normalise_reset_guard(
            value,
            path=path,
        )
        if value is None:
            return None
        normalised["mention_backlog_reset_guard"] = value
    if "mention_backlog" in state:
        raw_backlog = state["mention_backlog"]
        token_overflow = (
            isinstance(raw_backlog, dict)
            and isinstance(raw_backlog.get("seen_tokens"), list)
            and len(raw_backlog["seen_tokens"])
            > MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT
        )
        value = mention_authority().normalise_backlog(
            raw_backlog,
            path=path,
            reset_token_overflow=token_overflow,
        )
        if value is None:
            return None
        normalised["mention_backlog"] = value
        if token_overflow:
            normalised["mention_pagination"] = {}
            normalised["mention_pending_candidates"] = {}
            normalised["mention_backlog_reset_guard"] = {
                "base_since_id": str(
                    normalised.get("last_seen_mention_id") or ""
                ),
                "head_traversal_started": False,
            }
            if recovery_events is not None:
                recovery_events.append({
                    "reason": "continuation_token_limit",
                    "since_id": str(raw_backlog.get("since_id") or "") or None,
                    "pages_completed": raw_backlog.get("pages_completed"),
                    "token_fingerprint": hashlib.sha256(
                        str(raw_backlog.get("next_token") or "").encode("utf-8")
                    ).hexdigest()[:16],
                })
    # Retire legacy quarantine-only terminal records before they can suppress
    # a completed-page pending candidate that the watermark will not refetch.
    prune_reply_evaluation_records(normalised)
    pending_authority_usable, _pending_authority_changed = (
        mention_authority().validate_pending(
            normalised,
            path=path,
            recover_pending_identity=recover_pending_identity,
            recovery_events=recovery_events,
        )
    )
    if not pending_authority_usable:
        return None
    if "author_evaluation_quarantines" in state:
        value = normalise_author_evaluation_quarantines(
            state["author_evaluation_quarantines"],
            path=path,
        )
        if value is None:
            return None
        normalised["author_evaluation_quarantines"] = value
    for key in int_keys:
        if key not in state:
            continue
        value = state[key]
        value = state_values().integer(value, key=key, path=path)
        if value is None:
            return None
        normalised[key] = value
    for key in epoch_keys:
        if key not in state:
            continue
        value = state[key]
        value = state_values().epoch(value, key=key, path=path)
        if value is None:
            return None
        normalised[key] = value

    if not validate_meme_schedule_version_for_candidate(normalised, path=path):
        return None

    prune_author_evaluation_quarantines(normalised)

    return normalised
