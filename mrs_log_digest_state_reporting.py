"""Prepare state, headline, reply-quality and mention-control reporting.

The digest supplies state/configuration, timestamps, current helper and clock
callbacks, and vocabulary.
Summaries preserve existing copying/sharing and use supplied record/state times;
refresh functions mutate the supplied report and observations use the supplied
event callback and statistics counter.
Loading, saved context, source tracking and report orchestration stay with their
current owners. This module performs no I/O or import-time runtime work, imports
no coordinator or bot, and stores no callbacks.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict

from mrs_log_digest_records import Record


AUTHOR_NO_REPLY_PROGRESS_MAX_AUTHORS = 25_000
AUTHOR_NO_REPLY_PROGRESS_MAX_THRESHOLD = 250_000
AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY = (
    "single_sol_explicit_spam_or_abuse_v2"
)
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
UNKNOWN_INVALID_STATE_FIELD = "unknown (invalid in latest snapshot)"
CURRENT_COOLDOWN_FIELDS = (
    ("api_cooldown_until_epoch", "X read API"),
    ("x_write_api_cooldown_until_epoch", "X write API"),
    ("openai_api_cooldown_until_epoch", "OpenAI"),
    ("quote_api_cooldown_until_epoch", "quote API"),
)


class _HeadlineComponents(TypedDict):
    """Keep replaceable health and cooldown claims separate from observations."""

    activity: List[str]
    reply_quality: List[str]
    current_health: str
    observations: List[str]
    cooldown: List[str]


def _headline_claims(
    components: _HeadlineComponents,
    *,
    health_claim: Optional[str] = None,
    cooldown_claims: Optional[List[str]] = None,
) -> List[str]:
    """Format headline components in their established presentation order."""
    return [
        *components["activity"],
        *components["reply_quality"],
        components["current_health"] if health_claim is None else health_claim,
        *components["observations"],
        *(components["cooldown"] if cooldown_claims is None else cooldown_claims),
    ]


def epoch_to_human(
    value: Any,
    *,
    fromtimestamp: Callable[..., datetime],
) -> Optional[str]:
    """Return the epoch to human."""
    try:
        n = int(value)
    except Exception:
        return None
    if n <= 0:
        return None
    return fromtimestamp(n).strftime("%Y-%m-%d %H:%M:%S")


def epoch_to_london_text(
    value: int,
    *,
    fromtimestamp: Callable[..., datetime],
    london_timezone: tzinfo,
) -> Optional[str]:
    """Render a validated epoch in the digest's explicit London timezone."""

    try:
        return fromtimestamp(value, tz=london_timezone).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (OverflowError, OSError, ValueError):
        return None


def state_list_count(
    state: Dict[str, Any],
    key: str,
    *,
    UNKNOWN_MISSING_STATE_FIELD: str,
    UNKNOWN_INVALID_STATE_FIELD: str,
) -> Any:
    """Return the state list count."""
    if key not in state:
        return UNKNOWN_MISSING_STATE_FIELD
    value = state.get(key)
    if isinstance(value, list):
        return len(value)
    return UNKNOWN_INVALID_STATE_FIELD


def state_list_tail(state: Dict[str, Any], key: str, count: int) -> Optional[List[Any]]:
    """Return the state list tail."""
    if key not in state:
        return None
    value = state.get(key)
    if isinstance(value, list):
        return value[-count:]
    return None


def state_list_head(state: Dict[str, Any], key: str, count: int) -> Optional[List[Any]]:
    """Return the state list head."""
    if key not in state:
        return None
    value = state.get(key)
    if isinstance(value, list):
        return value[:count]
    return None


def summarize_latest_state(
    latest_state: Dict[str, Any],
    latest_state_ts: Optional[datetime],
    *,
    source: str = "log snapshot",
    source_path: Optional[Path] = None,
    clock_now: Callable[[], datetime],
    epoch_to_human: Callable[[Any], Optional[str]],
    state_list_count: Callable[[Dict[str, Any], str], Any],
    state_list_tail: Callable[[Dict[str, Any], str, int], Optional[List[Any]]],
    state_list_head: Callable[[Dict[str, Any], str, int], Optional[List[Any]]],
    UNKNOWN_INVALID_STATE_FIELD: str,
) -> Dict[str, Any]:
    """Summarise latest state."""
    observed_epoch = int(clock_now().timestamp())
    backlog = latest_state.get("mention_backlog")
    if not isinstance(backlog, dict):
        backlog = {}
    quarantine_records = latest_state.get("author_evaluation_quarantines")
    if not isinstance(quarantine_records, dict):
        quarantine_records = {}
    active_quarantine_author_ids = sorted(
        str(author_id)
        for author_id, record in quarantine_records.items()
        if isinstance(record, dict)
        and type(record.get("quarantine_until_epoch")) is int
        and record["quarantine_until_epoch"] > observed_epoch
    )
    backlog_started_epoch = backlog.get("started_epoch")
    summary = {
        "time": latest_state_ts.strftime("%Y-%m-%d %H:%M:%S") if latest_state_ts else None,
        "_state_source": source,
        "daily_reply_date": latest_state.get("daily_reply_date"),
        "daily_reply_count": latest_state.get("daily_reply_count"),
        "daily_quote_reply_date": latest_state.get("daily_quote_reply_date"),
        "daily_quote_reply_count": latest_state.get("daily_quote_reply_count"),
        "last_seen_mention_id": latest_state.get("last_seen_mention_id"),
        "mention_backlog_active": bool(backlog),
        "mention_backlog_age_seconds": (
            max(0, observed_epoch - backlog_started_epoch)
            if type(backlog_started_epoch) is int and backlog_started_epoch > 0
            else None
        ),
        "mention_backlog_pages_completed": backlog.get("pages_completed", 0),
        "mention_backlog_highest_mention_id": backlog.get("highest_mention_id"),
        "mention_backlog_continuation_token_present": bool(backlog.get("next_token")),
        "mention_pending_candidate_count": (
            len(latest_state.get("mention_pending_candidates", {}))
            if isinstance(latest_state.get("mention_pending_candidates"), dict)
            else UNKNOWN_INVALID_STATE_FIELD
        ),
        "active_author_evaluation_quarantine_count": len(active_quarantine_author_ids),
        "active_author_evaluation_quarantine_author_ids": active_quarantine_author_ids,
        "last_main_post_id": latest_state.get("last_main_post_id"),
        "last_reply_epoch": latest_state.get("last_reply_epoch"),
        "last_reply_human": epoch_to_human(latest_state.get("last_reply_epoch")),
        "last_quote_post_epoch": latest_state.get("last_quote_post_epoch"),
        "last_quote_post_human": epoch_to_human(latest_state.get("last_quote_post_epoch")),
        "last_meme_post_epoch": latest_state.get("last_meme_post_epoch"),
        "last_meme_post_human": epoch_to_human(latest_state.get("last_meme_post_epoch")),
        "next_quote_post_epoch": latest_state.get("next_quote_post_epoch"),
        "next_quote_post_human": epoch_to_human(latest_state.get("next_quote_post_epoch")),
        "next_meme_post_epoch": latest_state.get("next_meme_post_epoch"),
        "next_meme_post_human": epoch_to_human(latest_state.get("next_meme_post_epoch")),
        "next_meme_schedule_mode": latest_state.get("next_meme_schedule_mode"),
        "next_meme_schedule_date": latest_state.get("next_meme_schedule_date"),
        "meme_anchor_quote_post_epoch": latest_state.get("meme_anchor_quote_post_epoch"),
        "meme_anchor_quote_post_human": epoch_to_human(latest_state.get("meme_anchor_quote_post_epoch")),
        "meme_schedule_version": latest_state.get("meme_schedule_version"),
        "api_cooldown_until_epoch": latest_state.get("api_cooldown_until_epoch"),
        "api_cooldown_until_human": epoch_to_human(latest_state.get("api_cooldown_until_epoch")),
        "api_cooldown_reason": latest_state.get("api_cooldown_reason"),
        "x_write_api_cooldown_until_epoch": latest_state.get("x_write_api_cooldown_until_epoch"),
        "x_write_api_cooldown_until_human": epoch_to_human(latest_state.get("x_write_api_cooldown_until_epoch")),
        "x_write_api_cooldown_reason": latest_state.get("x_write_api_cooldown_reason"),
        "openai_api_cooldown_until_epoch": latest_state.get("openai_api_cooldown_until_epoch"),
        "openai_api_cooldown_until_human": epoch_to_human(latest_state.get("openai_api_cooldown_until_epoch")),
        "openai_api_cooldown_reason": latest_state.get("openai_api_cooldown_reason"),
        "quote_api_cooldown_until_epoch": latest_state.get("quote_api_cooldown_until_epoch"),
        "quote_api_cooldown_until_human": epoch_to_human(latest_state.get("quote_api_cooldown_until_epoch")),
        "quote_api_cooldown_reason": latest_state.get("quote_api_cooldown_reason"),
        "quote_spam_author_count": state_list_count(latest_state, "quote_spam_author_ids"),
        "posted_meme_count": state_list_count(latest_state, "posted_meme_filenames"),
        "posted_meme_filenames_tail": state_list_tail(latest_state, "posted_meme_filenames", 8),
        "recent_own_post_ids_head": state_list_head(latest_state, "recent_own_post_ids", 5),
        "next_reply_lane_priority": latest_state.get("next_reply_lane_priority"),
        "skipped_hot_reply_count": state_list_count(latest_state, "skipped_hot_reply_ids"),
    }
    if latest_state.get("_partial"):
        summary["_partial"] = True
    if source_path is not None:
        summary["_state_source_path"] = str(source_path)
    return summary


def current_author_no_reply_strike_progress(
    runtime_state: Any,
    runtime_state_status: str,
    runtime_config: Any,
    runtime_config_status: str,
    generation_time: datetime,
    *,
    state_observed_at: Optional[datetime] = None,
    epoch_to_london_text: Callable[[int], Optional[str]],
    valid_public_post_id: Callable[[Any], bool],
    MAX_REASONABLE_STATE_EPOCH: int,
    AUTHOR_NO_REPLY_PROGRESS_MAX_AUTHORS: int,
    AUTHOR_NO_REPLY_PROGRESS_MAX_THRESHOLD: int,
    AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY: str,
    AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY: str,
    AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY: str,
    AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY: str,
    AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY: str,
) -> Dict[str, Any]:
    """Project current durable author strikes without mutating runtime state."""

    observation_time = state_observed_at or generation_time
    try:
        as_of_epoch = int(observation_time.timestamp())
    except (OverflowError, OSError, ValueError):
        as_of_epoch = -1
    as_of_time = epoch_to_london_text(as_of_epoch)
    limitation = (
        "Expired or cleared sub-threshold strikes cannot be reconstructed when "
        "current durable state and retained structured logs no longer contain them."
    )
    result: Dict[str, Any] = {
        "available": False,
        "reason": "",
        "source": "bot_state.json",
        "authority_scope": (
            "authoritative current state at JSON generation time, independent of "
            "selected log window"
        ),
        "as_of_epoch": as_of_epoch,
        "as_of_time": as_of_time,
        "time_zone": "Europe/London",
        "rolling_window_semantics": (
            "retain strikes where cutoff_epoch < strike_epoch <= as_of_epoch"
        ),
        "quarantine_active_semantics": (
            "quarantine_until_epoch > as_of_epoch; exact expiry is inactive and "
            "clears retained strikes"
        ),
        "threshold": None,
        "window_seconds": None,
        "quarantine_seconds": None,
        "authors": None,
        "author_count": None,
        "omitted_author_count": None,
        "discarded_legacy_author_count": None,
        "migrated_prior_policy_author_count": None,
        "limitation": limitation,
    }

    config_keys = (
        "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD",
        "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS",
        "AUTHOR_NO_REPLY_QUARANTINE_SECONDS",
    )
    if runtime_config_status != "available" or not isinstance(
        runtime_config, dict
    ):
        result["reason"] = (
            "current quarantine configuration unavailable: "
            + str(runtime_config_status or "unknown")[:320]
        )[:512]
        return result
    config_values = [runtime_config.get(key) for key in config_keys]
    if any(type(value) is not int or value <= 0 for value in config_values):
        result["reason"] = (
            "current quarantine configuration is missing or malformed"
        )
        return result
    threshold, window_seconds, quarantine_seconds = config_values
    result.update(
        {
            "threshold": threshold,
            "window_seconds": window_seconds,
            "quarantine_seconds": quarantine_seconds,
        }
    )
    if (
        as_of_epoch < 0
        or as_of_epoch > MAX_REASONABLE_STATE_EPOCH
        or as_of_time is None
    ):
        result["reason"] = "current state observation time is not representable"
        return result
    if (
        threshold > AUTHOR_NO_REPLY_PROGRESS_MAX_THRESHOLD
        or window_seconds > MAX_REASONABLE_STATE_EPOCH
        or quarantine_seconds > MAX_REASONABLE_STATE_EPOCH
        or as_of_epoch + window_seconds > MAX_REASONABLE_STATE_EPOCH
        or as_of_epoch + quarantine_seconds > MAX_REASONABLE_STATE_EPOCH
    ):
        result["reason"] = (
            "current quarantine configuration exceeds bounded reporting limits"
        )
        return result

    if runtime_state_status != "available" or not isinstance(
        runtime_state, dict
    ):
        result["reason"] = (
            "current bot state unavailable: "
            + str(runtime_state_status or "unknown")[:360]
        )[:512]
        return result
    if "author_evaluation_quarantines" not in runtime_state:
        result["reason"] = (
            "current bot state does not contain author_evaluation_quarantines"
        )
        return result
    records = runtime_state.get("author_evaluation_quarantines")
    if not isinstance(records, dict):
        result["reason"] = (
            "current author_evaluation_quarantines value is malformed"
        )
        return result

    required_fields = {
        "recent_no_reply_epochs",
        "quarantine_until_epoch",
        "last_updated_epoch",
        "latest_explicit_spam_or_abuse_epoch",
        "evidence_policy",
    }
    legacy_fields = {
        "recent_no_reply_epochs",
        "quarantine_until_epoch",
        "last_updated_epoch",
    }
    previous_policy_fields = legacy_fields | {"evidence_policy"}
    epoch_limit = max(100, threshold * 4)
    validated: List[Tuple[str, List[int], int]] = []
    discarded_legacy_author_count = 0
    migrated_prior_policy_author_count = 0
    ordered_records = sorted(
        records.items(),
        key=lambda item: (
            not valid_public_post_id(item[0]),
            len(str(item[0])),
            str(item[0]),
        ),
    )
    for position, (raw_author_id, raw_record) in enumerate(ordered_records, 1):
        author_id = str(raw_author_id)
        if not valid_public_post_id(author_id) or not isinstance(raw_record, dict):
            result["reason"] = (
                f"malformed author quarantine record at position {position}"
            )
            return result
        record_fields = set(raw_record)
        if record_fields == legacy_fields:
            # Production deliberately discards this pre-policy broad evidence
            # instead of treating it as current qualifying no-reply history.
            discarded_legacy_author_count += 1
            continue
        if (
            record_fields != previous_policy_fields
            and record_fields != required_fields
        ):
            result["reason"] = (
                f"malformed author quarantine record at position {position}"
            )
            return result
        timestamps = raw_record.get("recent_no_reply_epochs")
        until = raw_record.get("quarantine_until_epoch")
        updated = raw_record.get("last_updated_epoch")
        evidence_policy = raw_record.get("evidence_policy")
        is_legacy_policy = (
            record_fields == previous_policy_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY
        )
        is_seeded_policy = (
            record_fields == required_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY
        )
        is_current_policy = (
            record_fields == required_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY
        )
        is_previous_policy = (
            record_fields == required_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY
        )
        is_single_sol_v1_policy = (
            record_fields == required_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY
        )
        malformed = (
            not (
                is_legacy_policy
                or is_seeded_policy
                or is_previous_policy
                or is_single_sol_v1_policy
                or is_current_policy
            )
            or not isinstance(timestamps, list)
            or len(timestamps) > epoch_limit
            or any(
                type(epoch) is not int
                or epoch < 0
                or epoch > MAX_REASONABLE_STATE_EPOCH
                for epoch in timestamps
            )
            or timestamps != sorted(timestamps)
            or type(until) is not int
            or until < 0
            or until > MAX_REASONABLE_STATE_EPOCH
            or type(updated) is not int
            or updated < 0
            or updated > MAX_REASONABLE_STATE_EPOCH
        )
        if malformed:
            result["reason"] = (
                f"malformed author quarantine record at position {position}"
            )
            return result
        if is_legacy_policy:
            if not timestamps and not until:
                discarded_legacy_author_count += 1
                continue
            migrated_prior_policy_author_count += 1
        else:
            explicit_epoch = raw_record.get(
                "latest_explicit_spam_or_abuse_epoch"
            )
            if (
                type(explicit_epoch) is not int
                or explicit_epoch < 0
                or explicit_epoch > MAX_REASONABLE_STATE_EPOCH
                or explicit_epoch > updated
                or (
                    is_seeded_policy
                    and not until
                    and explicit_epoch
                    and explicit_epoch not in timestamps
                )
            ):
                result["reason"] = (
                    f"malformed author quarantine record at position {position}"
                )
                return result
            if is_seeded_policy or is_previous_policy or is_single_sol_v1_policy:
                migrated_prior_policy_author_count += 1
            if is_single_sol_v1_policy:
                timestamps = (
                    [explicit_epoch]
                    if explicit_epoch and explicit_epoch in timestamps
                    else []
                )
                until = 0
                if not timestamps:
                    continue
        validated.append((author_id, list(timestamps), until))

    cutoff = as_of_epoch - window_seconds
    authors: List[Dict[str, Any]] = []
    for author_id, timestamps, until in validated:
        recent = [
            epoch
            for epoch in timestamps
            if cutoff < epoch <= as_of_epoch
        ][-epoch_limit:]
        if until and until <= as_of_epoch:
            # Production pruning treats exact expiry as terminal and clears the
            # retained strike window together with the quarantine.
            recent = []
            until = 0
        quarantine_active = until > as_of_epoch
        if not recent and not quarantine_active:
            continue
        oldest_expiry = recent[0] + window_seconds if recent else None
        authors.append(
            {
                "author_id": author_id,
                "recent_qualifying_no_reply_epochs": recent,
                "recent_qualifying_no_reply_times": [
                    epoch_to_london_text(epoch) for epoch in recent
                ],
                "strike_count": len(recent),
                "strikes_remaining": (
                    0
                    if quarantine_active
                    else max(0, threshold - len(recent))
                ),
                "oldest_strike_expires_epoch": oldest_expiry,
                "oldest_strike_expires_time": (
                    epoch_to_london_text(oldest_expiry)
                    if oldest_expiry is not None
                    else None
                ),
                "quarantine_active": quarantine_active,
                "quarantine_until_epoch": until if quarantine_active else None,
                "quarantine_until_time": (
                    epoch_to_london_text(until)
                    if quarantine_active
                    else None
                ),
            }
        )
    omitted = max(0, len(authors) - AUTHOR_NO_REPLY_PROGRESS_MAX_AUTHORS)
    result.update(
        {
            "available": True,
            "reason": "",
            "authors": authors[:AUTHOR_NO_REPLY_PROGRESS_MAX_AUTHORS],
            "author_count": min(
                len(authors), AUTHOR_NO_REPLY_PROGRESS_MAX_AUTHORS
            ),
            "omitted_author_count": omitted,
            "discarded_legacy_author_count": discarded_legacy_author_count,
            "migrated_prior_policy_author_count": (
                migrated_prior_policy_author_count
            ),
        }
    )
    return result


def _incident_health_claim(
    current_incidents: int,
    unavailable_incidents: int,
    *,
    plural_count: Callable[..., str],
) -> str:
    """Describe incident health before any authoritative safety override."""
    if current_incidents:
        return "current health: " + plural_count(
            current_incidents, "unresolved operational incident"
        )
    if unavailable_incidents:
        return "current health: no active incident established"
    return "current health: no unresolved operational incidents"


def _remote_write_health_override(
    safety: Dict[str, Any],
    current_incidents: int,
) -> Optional[str]:
    """Return a stronger current-health claim only from authoritative safety."""
    if safety.get("current_health_snapshot_authoritative") is not True:
        return None
    if safety.get("configured") is True and safety.get("available") is not True:
        return "current health: remote-write safety unknown (inspection unavailable)"
    if safety.get("blocking") is True and not current_incidents:
        return "current health: remote writes blocked; no independent operational incident established"
    return None


def _reply_budget_values(
    configs: Dict[str, Any],
    state: Dict[str, Any],
    *,
    int_or_none: Callable[[Any], Optional[int]],
) -> Dict[str, Optional[int]]:
    """Calculate shared budget fields without adding source/availability metadata."""
    max_auto = int_or_none(configs.get("MAX_AUTO_REPLIES_PER_DAY"))
    max_per_author = int_or_none(configs.get("MAX_REPLIES_PER_AUTHOR_PER_DAY"))
    max_quote = int_or_none(configs.get("MAX_QUOTE_REPLIES_PER_DAY"))
    used_auto = int_or_none(state.get("daily_reply_count"))
    used_quote = int_or_none(state.get("daily_quote_reply_count"))
    return {
        "auto_used": used_auto,
        "auto_limit": max_auto,
        "per_author_limit": max_per_author,
        "auto_remaining": (max_auto - used_auto) if max_auto is not None and used_auto is not None else None,
        "quote_used": used_quote,
        "quote_limit": max_quote,
        "quote_remaining": (max_quote - used_quote) if max_quote is not None and used_quote is not None else None,
    }


def prepare_headline_and_derived(
    *,
    stats: Counter,
    error_health: Dict[str, Any],
    current_remote_write_safety: Optional[Dict[str, Any]],
    handled_api_restrictions: List[Dict[str, Any]],
    media_upload_incidents: List[Dict[str, Any]],
    self_test_errors: List[Dict[str, Any]],
    confirmed_post_recovery: List[Dict[str, Any]],
    confirmed_reply_recovery: List[Dict[str, Any]],
    receipt_events: List[Dict[str, Any]],
    asset_health: List[Dict[str, Any]],
    latest_state_summary: Dict[str, Any],
    records: List[Record],
    configs: Dict[str, Any],
    plural_count: Callable[..., str],
    int_or_none: Callable[[Any], Optional[int]],
    parse_dt: Callable[[Any], Optional[datetime]],
) -> Tuple[
    _HeadlineComponents, int, List[Dict[str, Any]], List[Dict[str, Any]],
    List[Dict[str, Any]], Dict[str, Any],
]:
    """Prepare initial health, media, cooldown and budget claims from supplied data."""
    activity = []
    activity.append(plural_count(stats.get("quote_image_posted", 0), "quote/image post"))
    activity.append(plural_count(stats.get("daily_meme_posted", 0), "daily meme"))
    activity.append(plural_count(stats.get("mention_reply_posted", 0), "mention reply", "mention replies"))
    activity.append(plural_count(stats.get("hot_post_reply_posted", 0), "hot-post reply", "hot-post replies"))
    activity.append(plural_count(stats.get("quote_tweet_reply_posted", 0), "quote-tweet reply", "quote-tweet replies"))
    activity.append(
        plural_count(
            stats.get("historical_context_reply_status_completed", 0),
            "historical-context reply",
            "historical-context replies",
        )
        + " completed"
    )
    current_incidents = int(error_health["current_independent_incident_count"])
    resolved_incidents = int(error_health["historical_resolved_incident_count"])
    unavailable_incidents = int(
        error_health.get("resolution_unavailable_incident_count", 0)
    )
    transient_provider_timeouts = int(
        error_health.get("transient_provider_timeout_count", 0)
    )
    safety = current_remote_write_safety or {}
    health_claim = _remote_write_health_override(safety, current_incidents)
    if health_claim is None:
        health_claim = _incident_health_claim(
            current_incidents, unavailable_incidents, plural_count=plural_count,
        )
    observations: List[str] = []
    if unavailable_incidents:
        observations.append(
            plural_count(
                unavailable_incidents,
                "incident with current status unavailable from retained evidence",
                "incidents with current status unavailable from retained evidence",
            )
        )
    if transient_provider_timeouts:
        observations.append(
            f"{plural_count(transient_provider_timeouts, 'transient provider timeout')} "
            "observed (provider recovery unverified)"
        )
    non_transient_resolved_incidents = resolved_incidents
    if non_transient_resolved_incidents:
        observations.append(
            plural_count(
                non_transient_resolved_incidents,
                "historical/resolved incident",
            )
            + " in window"
        )
    safety = current_remote_write_safety or {}
    if safety.get("configured") is True and safety.get("available") is not True:
        observations.append("current remote-write safety: UNKNOWN / unavailable")
    elif safety.get("configured") is True and safety.get("available") is True:
        safety_status = str(safety.get("status") or "unavailable")
        if safety.get("blocking") is True:
            observations.append("remote-write safety: BLOCKED")
        elif safety_status == "paused_fail_closed_control":
            observations.append("remote writes fail-closed by invalid control")
        elif safety_status == "operator_paused":
            observations.append("remote writes operator-paused")
        else:
            observations.append("remote-write safety ready")
    if handled_api_restrictions:
        deleted_incidents = {
            (
                str(item.get("service") or ""),
                str(item.get("target_id") or item.get("message") or ""),
            )
            for item in handled_api_restrictions
            if item.get("restriction_kind") == "deleted_or_inaccessible_tweet"
        }
        other_handled_incidents = {
            (
                str(item.get("service") or ""),
                str(item.get("status") or ""),
                str(item.get("target_id") or item.get("message") or ""),
            )
            for item in handled_api_restrictions
            if item.get("restriction_kind") != "deleted_or_inaccessible_tweet"
        }
        if deleted_incidents:
            observations.append(
                plural_count(
                    len(deleted_incidents),
                    "deleted/inaccessible-target 403",
                    "deleted/inaccessible-target 403s",
                )
                + " handled"
            )
        if other_handled_incidents:
            observations.append(
                plural_count(
                    len(other_handled_incidents),
                    "handled API restriction incident",
                )
            )
    handled_media_fallbacks = [
        item
        for item in media_upload_incidents
        if item.get("status") == "handled"
    ]
    reconciled_media_uploads = [
        item
        for item in media_upload_incidents
        if item.get("status") == "reconciled"
    ]
    unrecovered_media = [
        item
        for item in media_upload_incidents
        if item.get("status") not in {"handled", "reconciled"}
    ]
    if handled_media_fallbacks:
        observations.append(plural_count(len(handled_media_fallbacks), "handled media-upload fallback"))
    if reconciled_media_uploads:
        observations.append(
            plural_count(
                len(reconciled_media_uploads),
                "durably reconciled media-upload ambiguity",
                "durably reconciled media-upload ambiguities",
            )
        )
    if unrecovered_media:
        observations.append(plural_count(len(unrecovered_media), "unrecovered media-upload failure"))
    if self_test_errors:
        selftest_fail_checks = sum(1 for e in self_test_errors if str(e.get("message", "")).startswith("SELFTEST FAIL:"))
        observations.append(f"self-test failures: {selftest_fail_checks} check(s)")
    if confirmed_post_recovery:
        observations.append(
            plural_count(
                len(confirmed_post_recovery),
                "confirmed-post recovery record",
            )
            + " in window"
        )
    if confirmed_reply_recovery:
        observations.append(
            plural_count(
                len(confirmed_reply_recovery),
                "confirmed-reply recovery record",
            )
            + " in window"
        )
    blocking_receipts = [
        item for item in receipt_events
        if item.get("kind") in {"invalid_or_unresolved_blocked", "simultaneous_receipts_blocked"}
    ]
    if blocking_receipts:
        observations.append(
            plural_count(len(blocking_receipts), "receipt-block record") + " in window"
        )
    if asset_health:
        observations.append(
            plural_count(len(asset_health), "asset-metadata warning") + " in window"
        )
    cooldown_until_epoch = int_or_none(latest_state_summary.get("api_cooldown_until_epoch"))
    x_write_cooldown_until_epoch = int_or_none(latest_state_summary.get("x_write_api_cooldown_until_epoch"))
    openai_cooldown_until_epoch = int_or_none(latest_state_summary.get("openai_api_cooldown_until_epoch"))
    quote_cooldown_until_epoch = int_or_none(latest_state_summary.get("quote_api_cooldown_until_epoch"))
    latest_state_time = parse_dt(latest_state_summary.get("time"))
    window_start_epoch = int(records[0].ts.timestamp()) if records else None

    def cooldown_headline(until_epoch: int | None, *, label: str) -> str | None:
        if not until_epoch or not latest_state_time:
            return None
        latest_state_epoch = int(latest_state_time.timestamp())
        if latest_state_epoch < until_epoch:
            return f"{label} cooldown active now"
        if window_start_epoch is not None and until_epoch >= window_start_epoch:
            return f"{label} cooldown occurred, now expired"
        return None

    cooldown_labels = [
        label
        for label in (
            cooldown_headline(cooldown_until_epoch, label="X read API"),
            cooldown_headline(x_write_cooldown_until_epoch, label="X write API"),
            cooldown_headline(openai_cooldown_until_epoch, label="OpenAI"),
            cooldown_headline(quote_cooldown_until_epoch, label="quote API"),
        )
        if label
    ]
    if not cooldown_labels:
        cooldown_labels.append(
            "API cooldown occurred"
            if stats.get("api_cooldown_entered", 0)
            else "no API cooldown"
        )

    headline: _HeadlineComponents = {
        "activity": activity,
        "reply_quality": [],
        "current_health": health_claim,
        "observations": observations,
        "cooldown": cooldown_labels,
    }

    derived = {
        "reply_budget": _reply_budget_values(
            configs, latest_state_summary, int_or_none=int_or_none,
        ),
        "reply_lane_priority": {
            "current_next_priority": latest_state_summary.get("next_reply_lane_priority"),
            "flipped_to_quote": stats.get("priority_flipped_to_quote", 0),
            "flipped_to_normal": stats.get("priority_flipped_to_normal", 0),
            "forced_normal_before_quote": stats.get("priority_forced_normal_before_quote", 0),
            "normal_first_refusal_no_post": stats.get("priority_normal_first_refusal_no_post", 0),
        },
    }

    return (
        headline, transient_provider_timeouts, handled_media_fallbacks,
        reconciled_media_uploads, unrecovered_media, derived,
    )


def prepare_reply_quality_headline(
    *,
    events: List[Dict[str, Any]],
    headline: _HeadlineComponents,
    single_call_quality: Dict[str, Any],
    plural_count: Callable[..., str],
) -> Tuple[Dict[str, int], List[str], List[str], _HeadlineComponents]:
    """Add reply-quality claims and retain components for authoritative refresh."""
    legacy_multi_stage = {
        "decision_count": sum(
            item.get("kind") == "reply_strategy_decision" for item in events
        ),
        "stage_summary_event_count": sum(
            item.get("kind") == "reply_pipeline_stage_summary" for item in events
        ),
    }
    tested_decisions = int(legacy_multi_stage["decision_count"])
    reply_quality = []
    single_candidates = int(
        single_call_quality.get("candidate_evaluation_count", 0) or 0
    )
    if single_candidates:
        reply_quality.append(
            f"{plural_count(single_candidates, 'single-call candidate')} evaluated; "
            f"{plural_count(single_call_quality.get('replies_posted_count', 0), 'reply', 'replies')} posted; "
            f"{plural_count(single_call_quality.get('editorial_no_reply_count', 0), 'editorial no-reply decision')}; "
            f"{plural_count(single_call_quality.get('operational_failure_count', 0), 'operational failure')}; "
            f"one-call compliance {single_call_quality.get('one_call_compliance')}",
        )
    if tested_decisions or legacy_multi_stage["stage_summary_event_count"]:
        reply_quality.append(
            f"legacy multi-stage decisions {tested_decisions}; "
            f"legacy stage summaries {legacy_multi_stage['stage_summary_event_count']}",
        )
    components: _HeadlineComponents = {**headline, "reply_quality": reply_quality}
    return (
        legacy_multi_stage,
        _headline_claims(components),
        _headline_claims(components, cooldown_claims=[]),
        components,
    )


def refresh_current_health_headline(
    report: Dict[str, Any],
    *,
    int_or_none: Callable[[Any], Optional[int]],
    plural_count: Callable[..., str],
    cooldown_state_text: Callable[[Any, int], str],
    CURRENT_COOLDOWN_FIELDS: Tuple[Tuple[str, str], ...],
) -> None:
    """Rebuild current claims from components after runtime overlay.

    The compatibility headline list remains report data, not a source of claim
    roles. Older or hand-built reports without components are left unchanged.
    """
    if "runtime_state_status" not in report:
        return
    summary = report.get("summary") or {}
    components = summary.get("_headline_components")
    if not isinstance(components, dict):
        return
    runtime_status = str(
        (report.get("runtime_state_status") or {}).get("status") or ""
    )
    generated = int_or_none(report.get("generation_epoch"))
    state = report.get("latest_state") or {}
    current_incidents = int(
        (report.get("error_health") or {}).get(
            "current_independent_incident_count", 0
        )
        or 0
    )
    unavailable_incidents = int(
        (report.get("error_health") or {}).get(
            "resolution_unavailable_incident_count", 0
        )
        or 0
    )
    # Refresh has always formatted incidents before inspecting the safety
    # override; initial preparation skips formatting when an override applies.
    health_claim = _incident_health_claim(
        current_incidents, unavailable_incidents, plural_count=plural_count,
    )
    safety = report.get("remote_write_safety") or {}
    safety_claim = _remote_write_health_override(safety, current_incidents)
    if safety_claim is not None:
        health_claim = safety_claim
    claims: List[str] = []
    statuses: Dict[str, str] = {}
    if runtime_status != "available" or generated is None:
        claims.append("current API cooldown state unavailable")
    else:
        for field, label in CURRENT_COOLDOWN_FIELDS:
            status = cooldown_state_text(state.get(field), generated)
            statuses[field] = status
            if status == "active":
                claims.append(f"{label} cooldown active now")
            elif status == "expired":
                claims.append(f"{label} cooldown occurred, now expired")
            elif status == "unavailable":
                claims.append(f"{label} cooldown state unavailable")
        if statuses and all(value == "cleared" for value in statuses.values()):
            claims.append("no API cooldown")
    report["current_cooldown_status"] = statuses
    summary["headline"] = "; ".join(
        _headline_claims(
            components, health_claim=health_claim, cooldown_claims=claims,
        )
    )


def refresh_derived(
    report: Dict[str, Any],
    *,
    int_or_none: Callable[[Any], Optional[int]],
    epoch_to_human: Callable[[Any], Optional[str]],
    refresh_current_health_headline: Callable[[Dict[str, Any]], None],
) -> None:
    """Recalculate derived sections after any carried-forward context is applied."""
    configs = report.get("latest_config") or {}
    st = report.get("latest_state") or {}
    stats = report.get("summary", {}).get("stats", {}) or {}

    # Cooldown human timestamps are derived from the epoch. Recompute after
    # saved-context merging so a cleared epoch=0 cannot keep an old date/reason.
    for prefix in ("api_cooldown", "x_write_api_cooldown", "openai_api_cooldown", "quote_api_cooldown"):
        epoch_key = f"{prefix}_until_epoch"
        human_key = f"{prefix}_until_human"
        reason_key = f"{prefix}_reason"
        if epoch_key not in st:
            continue

        until = int_or_none(st.get(epoch_key))
        if until and until > 0:
            st[human_key] = epoch_to_human(until)
        else:
            st[human_key] = None
            st[reason_key] = ""

    reply_budget = _reply_budget_values(configs, st, int_or_none=int_or_none)

    report["derived"] = {
        "reply_budget": {
            **reply_budget,
            "has_any_budget_input": any(
                reply_budget[field] is not None
                for field in (
                    "auto_used",
                    "auto_limit",
                    "per_author_limit",
                    "quote_used",
                    "quote_limit",
                )
            ),
            "state_carried_forward": bool(st.get("_carried_forward")),
            "config_carried_forward": bool(configs.get("_carried_forward")),
            "config_carried_from_log_backscan": bool(configs.get("_carried_from_log_backscan")),
            "config_backscan_timestamp": configs.get("_log_backscan_timestamp"),
            "state_filled_from_previous": bool(st.get("_filled_from_previous")),
            "config_filled_from_previous": bool(configs.get("_filled_from_previous")),
            "config_filled_from_log_backscan": bool(configs.get("_filled_from_log_backscan")),
            "config_is_on_disk_override": (
                configs.get("_config_source") == "mrsMThatcher.local.json"
            ),
        },
        "reply_lane_priority": {
            "current_next_priority": st.get("next_reply_lane_priority"),
            "has_priority_state": st.get("next_reply_lane_priority") is not None,
            "state_carried_forward": bool(st.get("_carried_forward")),
            "state_filled_from_previous": bool(st.get("_filled_from_previous")),
            "config_carried_forward": bool(configs.get("_carried_forward")),
            "config_carried_from_log_backscan": bool(configs.get("_carried_from_log_backscan")),
            "config_backscan_timestamp": configs.get("_log_backscan_timestamp"),
            "config_filled_from_previous": bool(configs.get("_filled_from_previous")),
            "config_filled_from_log_backscan": bool(configs.get("_filled_from_log_backscan")),
            "normal_lane_due_checks": stats.get("normal_lane_due_checks", 0),
            "mention_function_entries": stats.get("mention_checks", 0),
            "mention_fetch_attempts": stats.get("mention_fetch_attempts", 0),
            "mention_checks_skipped_spacing": stats.get("mention_checks_skipped_spacing", 0),
            "mention_checks_skipped_cooldown": stats.get("cooldown_mentions", 0),
            "quote_lane_due_checks": stats.get("quote_lane_due_checks", 0),
            "flipped_to_quote": stats.get("priority_flipped_to_quote", 0),
            "flipped_to_normal": stats.get("priority_flipped_to_normal", 0),
            "forced_normal_before_quote": stats.get("priority_forced_normal_before_quote", 0),
            "normal_first_refusal_no_post": stats.get("priority_normal_first_refusal_no_post", 0),
            "quote_tweet_status_posted": stats.get("quote_tweet_status_posted", 0),
            "quote_tweet_status_checked": stats.get("quote_tweet_status_checked", 0),
            "quote_tweet_status_skipped_spacing": stats.get("quote_tweet_status_skipped_spacing", 0),
            "quote_tweet_status_skipped_cap": stats.get("quote_tweet_status_skipped_cap", 0),
            "quote_tweet_status_skipped_cooldown": stats.get("quote_tweet_status_skipped_cooldown", 0),
            "quote_tweet_checks_no_post": stats.get("quote_tweet_checks_no_post", 0),
        },
    }
    refresh_current_health_headline(report)


def record_mention_backlog(
    event_obj: Dict[str, Any],
    timestamp: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
    stats: Counter[str],
    valid_string_public_post_id: Callable[..., Any],
    bounded_event_nonnegative_integer: Callable[..., Any],
    bounded_event_boolean: Callable[..., Any],
    bounded_event_text: Callable[..., Any],
) -> None:
    """Project the selected structured observation and increment its counter."""
    kind = str(event_obj["event"])
    add_event(
        kind,
        timestamp,
        since_id=(
            event_obj.get("since_id")
            if event_obj.get("since_id") is None
            or valid_string_public_post_id(event_obj.get("since_id"))
            else None
        ),
        pages_completed=bounded_event_nonnegative_integer(
            event_obj.get("pages_completed"), maximum=1_000_000
        ),
        highest_mention_id=(
            event_obj.get("highest_mention_id")
            if event_obj.get("highest_mention_id") is None
            or valid_string_public_post_id(
                event_obj.get("highest_mention_id")
            )
            else None
        ),
        continuation_token_present=bounded_event_boolean(
            event_obj.get("continuation_token_present")
        ),
        backlog_age_seconds=bounded_event_nonnegative_integer(
            event_obj.get("backlog_age_seconds")
        ),
        reason=bounded_event_text(
            event_obj.get("reason"), default="", max_characters=240
        ),
    )
    stats[kind] += 1


def record_author_evaluation_quarantine(
    event_obj: Dict[str, Any],
    timestamp: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
    stats: Counter[str],
    valid_string_public_post_id: Callable[..., Any],
    bounded_event_nonnegative_integer: Callable[..., Any],
) -> None:
    """Project the selected structured observation and increment its counter."""
    kind = str(event_obj["event"])
    add_event(
        kind,
        timestamp,
        author_id=(
            event_obj.get("author_id")
            if valid_string_public_post_id(
                event_obj.get("author_id")
            )
            else ""
        ),
        target_id=(
            event_obj.get("target_id")
            if valid_string_public_post_id(
                event_obj.get("target_id")
            )
            else ""
        ),
        strike_count=bounded_event_nonnegative_integer(
            event_obj.get("strike_count"), maximum=250_000
        ),
        quarantine_until_epoch=bounded_event_nonnegative_integer(
            event_obj.get("quarantine_until_epoch")
        ),
        pipeline_evaluations_skipped=bounded_event_nonnegative_integer(
            event_obj.get("pipeline_evaluations_skipped"),
            maximum=1_000_000,
        ),
    )
    stats[kind] += 1


def prepare_mention_control_observations(
    events: List[Dict[str, Any]],
    *,
    event_counter: Callable[..., Counter[str]],
) -> Tuple[List[Dict[str, Any]], Counter[str], int]:
    """Select original control events, their counts and explicit skipped evaluations."""
    mention_control_kinds = {
        "mention_backlog_started",
        "mention_backlog_progress",
        "mention_backlog_completed",
        "mention_backlog_reset",
        "author_evaluation_quarantine_started",
        "author_evaluation_quarantine_skip",
        "author_evaluation_quarantine_expired",
    }
    mention_control_events = [
        item for item in events if item.get("kind") in mention_control_kinds
    ]
    mention_control_counts = event_counter(
        str(item.get("kind")) for item in mention_control_events
    )
    pipeline_evaluations_skipped = sum(
        int(item["pipeline_evaluations_skipped"])
        for item in mention_control_events
        if item.get("kind") == "author_evaluation_quarantine_skip"
        and type(item.get("pipeline_evaluations_skipped")) is int
        and item["pipeline_evaluations_skipped"] >= 0
    )
    return mention_control_events, mention_control_counts, pipeline_evaluations_skipped
