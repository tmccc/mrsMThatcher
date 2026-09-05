"""Report prepared current state, author progress and mention-control observations.

The digest supplies state/configuration, timestamps, current helper and clock
callbacks, and vocabulary (including the quote-publication experiment labels).
Summaries preserve existing copying/sharing; refresh functions mutate the supplied
report and observations use the supplied event callback and statistics counter.
Loading, saved context, source tracking and report orchestration stay with their
current owners. This module performs no I/O or import-time runtime work, imports
no coordinator or bot, and stores no callbacks.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


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


def summarize_engagement_question_experiment_state(
    value: Any,
    *,
    ENGAGEMENT_QUESTION_EXPERIMENT_STATE_SCHEMA_VERSION: int,
    ENGAGEMENT_QUESTION_EXPERIMENT_ID: str,
    ENGAGEMENT_QUESTION_EXPERIMENT_STATUSES: set[str],
    UNKNOWN_MISSING_STATE_FIELD: str,
    UNKNOWN_INVALID_STATE_FIELD: str,
) -> Optional[Dict[str, Any]]:
    """Return a bounded summary of protected engagement-question state."""
    if (
        not isinstance(value, dict)
        or value.get("schema_version")
        != ENGAGEMENT_QUESTION_EXPERIMENT_STATE_SCHEMA_VERSION
        or value.get("experiment_id") != ENGAGEMENT_QUESTION_EXPERIMENT_ID
    ):
        return None

    def scalar(key: str, expected_type: Any = None) -> Any:
        if key not in value:
            return UNKNOWN_MISSING_STATE_FIELD
        item = value.get(key)
        if expected_type is not None and type(item) is not expected_type:
            return UNKNOWN_INVALID_STATE_FIELD
        return item

    status = scalar("status", str)
    if (
        isinstance(status, str)
        and status not in ENGAGEMENT_QUESTION_EXPERIMENT_STATUSES
    ):
        status = UNKNOWN_INVALID_STATE_FIELD

    publications = value.get("confirmed_publications")
    if "confirmed_publications" not in value:
        confirmed_publication_count: Any = UNKNOWN_MISSING_STATE_FIELD
    elif isinstance(publications, list):
        confirmed_publication_count = len(publications)
    else:
        confirmed_publication_count = UNKNOWN_INVALID_STATE_FIELD

    if "current_deferral_reason" not in value:
        current_deferral_reason: Any = UNKNOWN_MISSING_STATE_FIELD
    else:
        raw_deferral = value.get("current_deferral_reason")
        if raw_deferral is None:
            current_deferral_reason = None
        elif isinstance(raw_deferral, dict):
            current_deferral_reason = {
                key: raw_deferral.get(key)
                for key in ("code", "pair_id", "member_position", "recorded_epoch")
                if key in raw_deferral
            }
        else:
            current_deferral_reason = UNKNOWN_INVALID_STATE_FIELD

    return {
        "experiment_id": scalar("experiment_id", str),
        "active_plan_sha256": scalar("active_plan_sha256", str),
        "status": status,
        "current_pair_index": scalar("current_pair_index", int),
        "active_pair_id": scalar("active_pair_id"),
        "next_pair_member_position": scalar("next_pair_member_position", int),
        "completed_pair_count": scalar("completed_pair_count", int),
        "confirmed_publication_count": confirmed_publication_count,
        "treatment_publication_count": scalar(
            "treatment_publication_count", int
        ),
        "last_experimental_publication_local_date": scalar(
            "last_experimental_publication_local_date"
        ),
        "current_deferral_reason": current_deferral_reason,
    }


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
    summarize_engagement_question_experiment_state: Callable[[Any], Optional[Dict[str, Any]]],
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
    experiment_summary = summarize_engagement_question_experiment_state(
        latest_state.get("engagement_question_experiment")
    )
    if experiment_summary is not None:
        summary["engagement_question_experiment"] = experiment_summary
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


def refresh_current_health_headline(
    report: Dict[str, Any],
    *,
    int_or_none: Callable[[Any], Optional[int]],
    plural_count: Callable[..., str],
    cooldown_state_text: Callable[[Any, int], str],
    CURRENT_COOLDOWN_FIELDS: Tuple[Tuple[str, str], ...],
) -> None:
    """Rebuild current-health and cooldown claims after runtime overlay."""
    if "runtime_state_status" not in report:
        return
    summary = report.get("summary") or {}
    base = summary.get("_headline_without_current_cooldown")
    if not isinstance(base, list):
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
    health_claim = (
        "current health: "
        + plural_count(current_incidents, "unresolved operational incident")
        if current_incidents
        else "current health: no active incident established"
        if unavailable_incidents
        else "current health: no unresolved operational incidents"
    )
    rebuilt_base = [
        health_claim if str(item).startswith("current health:") else item
        for item in base
    ]
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
    summary["headline"] = "; ".join([*rebuilt_base, *claims])


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

    max_auto = int_or_none(configs.get("MAX_AUTO_REPLIES_PER_DAY"))
    max_per_author = int_or_none(
        configs.get("MAX_REPLIES_PER_AUTHOR_PER_DAY")
    )
    max_quote = int_or_none(configs.get("MAX_QUOTE_REPLIES_PER_DAY"))
    used_auto = int_or_none(st.get("daily_reply_count"))
    used_quote = int_or_none(st.get("daily_quote_reply_count"))

    report["derived"] = {
        "reply_budget": {
            "auto_used": used_auto,
            "auto_limit": max_auto,
            "per_author_limit": max_per_author,
            "auto_remaining": (max_auto - used_auto) if max_auto is not None and used_auto is not None else None,
            "quote_used": used_quote,
            "quote_limit": max_quote,
            "quote_remaining": (max_quote - used_quote) if max_quote is not None and used_quote is not None else None,
            "has_any_budget_input": any(
                x is not None
                for x in (
                    used_auto,
                    max_auto,
                    max_per_author,
                    used_quote,
                    max_quote,
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
