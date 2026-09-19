"""Observe errors/warnings, classify operational errors and report incidents.

The coordinator supplies current helper callbacks, scope vocabulary, clock/epoch
conversion and prepared safety evidence. Reporting preserves input error/event
identity and mutates supplied snapshot annotations through the caller's callback.
Per-record observation mutates supplied lists and calls the current root rejection
callback before error routing, without taking ownership of event provenance.

This module performs no file/home/configuration access or provider calls. Snapshot
loading, window annotation ownership, transaction parsing, media correlation,
event collection and report assembly remain with their existing owners.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

if TYPE_CHECKING:
    from mrs_log_digest_records import Record

from mrs_log_digest_snapshot_incidents import reconcile_current_snapshot_incidents
from mrs_log_digest_values import (
    _is_terminal_pipeline_failure,
    _normalise_lane,
    _terminal_local_rejection_outcome,
    dt_text,
    parse_dt,
    short,
)


REMOTE_OPERATION_SCOPE_LABELS = {
    "all_remote_writes": "all remote writes",
    "all_replies": "all replies",
    "normal_replies": "normal replies (mention and hot-post)",
    "hot_post_replies": "hot-post replies",
    "quote_replies": "quote-tweet replies",
    "quote_image_posts": "regular quote/image posts",
    "daily_meme_posts": "daily-meme posts",
    "historical_context_replies": "historical-context replies",
    "unknown": "unknown",
}
REMOTE_CONTROL_SCOPE_BY_KEY = {
    "disable_all": "all_remote_writes",
    "pause_all": "all_remote_writes",
    "disable_replies": "all_replies",
    "pause_replies": "all_replies",
    "disable_normal_replies": "normal_replies",
    "pause_normal_replies": "normal_replies",
    "disable_hot_post_replies": "hot_post_replies",
    "pause_hot_post_replies": "hot_post_replies",
    "disable_quote_replies": "quote_replies",
    "pause_quote_replies": "quote_replies",
    "disable_quote_posts": "quote_image_posts",
    "pause_quote_posts": "quote_image_posts",
    "disable_meme_posts": "daily_meme_posts",
    "pause_meme_posts": "daily_meme_posts",
}
REMOTE_LANE_SCOPE = {
    "mention": "normal_replies",
    "mention_reply": "normal_replies",
    "normal": "normal_replies",
    "normal_reply": "normal_replies",
    "normal_replies": "normal_replies",
    "hot_post": "hot_post_replies",
    "hot_post_reply": "hot_post_replies",
    "quote_tweet": "quote_replies",
    "quote_tweet_reply": "quote_replies",
    "quote_reply": "quote_replies",
    "conversational_reply": "all_replies",
    "replies": "all_replies",
    "quote_image": "quote_image_posts",
    "regular_post": "quote_image_posts",
    "daily_meme": "daily_meme_posts",
    "meme": "daily_meme_posts",
    "historical_context": "historical_context_replies",
    "historical_context_reply": "historical_context_replies",
}


def _incident_exception_line(message: str) -> str:
    """Return the final exception/result line from a traceback-like message."""
    lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith(("Traceback (most recent call last)", "File ")):
            continue
        return line
    return ""


def _normalise_incident_text(value: str) -> str:
    """Remove volatile identifiers while retaining a deterministic root signature."""
    text = str(value or "").lower()
    text = re.sub(r"/[^\s:]+", "<path>", text)
    text = re.sub(r"\b[0-9a-f]{64}\b", "<sha256>", text)
    text = re.sub(r"\b\d{12,}\b", "<id>", text)
    text = re.sub(r"\b\d+\b", "<n>", text)
    return re.sub(r"\s+", " ", text).strip()


def _local_media_preflight_reason(message: str) -> Optional[str]:
    """Recognise a typed preflight failure or the exact legacy source-read cause.

    A permission error elsewhere in receipt establishment can follow a durable
    write. Legacy exceptions are therefore reclassified only when the immediate
    cause proves that the initial image read failed before receipt publication.
    """
    text = str(message or "")
    final_line = _incident_exception_line(text)
    typed = re.fullmatch(
        r"(?:remote_media_upload_receipt\.)?MediaUploadPreflightError: (.+)",
        final_line,
    )
    if typed:
        return typed.group(1)
    if final_line != (
        "AmbiguousRemotePostOutcome: Could not establish the "
        "restart-persistent media-upload receipt"
    ):
        return None
    tracebacks = text.split("Traceback (most recent call last):\n")
    if len(tracebacks) < 3:
        return None
    cause = tracebacks[-2]
    frames = re.findall(r'  File "([^"\n]+)", line \d+, in ([^\n]+)\n([^\n]*)', cause)
    if [(path.rsplit("/", 1)[-1], name) for path, name, _code in frames] != [
        ("mrs_bot_post_creation.py", "upload_media"),
        ("remote_media_upload_receipt.py", "begin_media_upload"),
        ("remote_media_upload_receipt.py", "_read_stable_regular"),
        ("remote_media_upload_receipt.py", "_open_directory"),
    ]:
        return None
    if (
        frames[1][2].strip()
        != "image = _read_stable_regular(image_path, maximum=IMAGE_MAX_BYTES)"
        or frames[2][2].strip() != "directory_fd = _open_directory(path.parent)"
        or not cause.rstrip().endswith(
            "remote_media_upload_receipt.MediaUploadReceiptError: unsafe durable transaction directory\n\n"
            "The above exception was the direct cause of the following exception:"
        )
    ):
        return None
    return "source image directory ownership or permissions failed local preflight"


def _media_preflight_lane(message: str) -> str:
    """Return the image-posting lane explicitly identified in retained text."""
    lowered = str(message or "").lower()
    explicit = re.search(r"\blane=[\"']?(daily_meme|quote_image)\b", lowered)
    if explicit:
        return explicit.group(1)
    if "daily meme" in lowered:
        return "daily_meme"
    if "quote/image" in lowered:
        return "quote_image"
    return "unknown"


def _media_preflight_image(message: str) -> str:
    """Decode the explicit repr-quoted source basename from a typed failure."""
    reason = _local_media_preflight_reason(message) or ""
    match = re.search(r'''\bimage=("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''', reason)
    if match is None:
        return ""
    try:
        return str(ast.literal_eval(match.group(1)))
    except (SyntaxError, ValueError):
        return ""


def _remote_write_barrier_category(message: str) -> Optional[str]:
    """Recognise outer safety diagnoses before handled errors in chained tracebacks."""
    lowered = str(message or "").lower()
    if any(
        marker in lowered
        for marker in (
            "ambiguous remote x post outcome",
            "ambiguousremotepostoutcome",
            "media upload outcome is ambiguous",
            "remote outcome is ambiguous",
            "durable remote-write safety barrier",
            "unresolved transaction receipt, marker, or process latch",
        )
    ):
        return "remote_write_ambiguity_barrier"
    if any(
        marker in lowered
        for marker in (
            "remote-write protocol is not activated",
            "protocol activation sentinel",
            "restart-persistent remote-write protocol",
        )
    ):
        return "remote_write_protocol_barrier"
    if any(
        marker in lowered
        for marker in (
            "remote-write receipt cannot be inspected",
            "transport journal",
            "transport fence",
            "source-receipt retirement",
            "confirmed-media retirement",
            "pending-schedule receipt",
            "sending receipt as a global manual-reconciliation barrier",
            "locally confirmed remote transaction could not be reconciled",
        )
    ):
        return "remote_write_transaction_barrier"
    return None


def classify_operational_error(
    message: str,
    *,
    incident_exception_line: Callable[[str], str],
    normalise_incident_text: Callable[[str], str],
    is_deleted_or_inaccessible_tweet_403: Callable[[str], bool],
) -> str:
    """Classify a traceback/error by its root operational concern."""
    text = str(message or "")
    lowered = text.lower()
    exception_line = incident_exception_line(text).lower()
    if _local_media_preflight_reason(text) is not None:
        return "local_media_preflight_failure"
    barrier_category = _remote_write_barrier_category(text)
    if barrier_category is not None:
        return barrier_category
    if "clarification reply lacks direct_factual_answer mode" in lowered:
        return "clarification_mode_local_rejection"
    if (
        any(marker in lowered for marker in ("readtimeout", "read timed out"))
        and any(marker in lowered for marker in ("xai", "grok", "api.x.ai"))
    ):
        return "xai_provider_timeout"
    if (
        re.search(r"\bx(?: bearer)? api error 429\b", lowered)
        or "entering api cooldown after 429" in lowered
    ):
        return "x_api_rate_limit"
    if (
        "paginationcursorprotocolerror" in lowered
        and "quote tweets" in lowered
        and "repeated pagination token" in lowered
    ):
        return "quote_pagination_protocol_anomaly"
    if is_deleted_or_inaccessible_tweet_403(text):
        return "deleted_or_inaccessible_tweet"
    if (
        "remoteoperationspaused" in lowered
        or "global runtime control pause blocks remote operation" in lowered
    ):
        return "remote_operations_paused"
    if any(
        marker in lowered
        for marker in (
            "another mrsmthatcher instance owns",
            "instance lock cannot be opened safely",
        )
    ):
        return "instance_lock_conflict"
    if re.search(r"\bx(?: bearer)? api error 5\d\d\b", lowered):
        return "x_api_transient_failure"
    if "source-role audit policy is incompatible" in lowered:
        return "historical_context_source_role_incompatibility"
    if "bot crashed with unhandled exception" in lowered:
        return "process_crash"
    if (
        "unresolvedregularpostreceipt" in lowered
        or "unresolved regular-post receipt" in lowered
    ):
        return "legacy_regular_receipt_barrier"
    if "historical context reply failed independently" in lowered:
        return "historical_context_reply_failure"
    if "daily meme posting failed" in lowered:
        return "daily_meme_failure"
    if "quote/image posting failed" in lowered:
        return "quote_image_posting_failure"
    if (
        "unresolved conversational reply sending receipt" in lowered
        or "unresolved confirmed reply receipt reconciliation" in lowered
    ):
        return "conversational_reply_receipt_barrier"
    if "failed to post generated reply" in lowered:
        return "conversational_reply_posting_failure"
    if exception_line:
        return normalise_incident_text(exception_line).split(":", 1)[0] or "operational_error"
    return "operational_error"


def observe_error_warning(
    r: Record,
    msg: str,
    *,
    self_test_errors: List[Dict[str, Any]],
    confirmed_post_recovery: List[Dict[str, Any]],
    confirmed_reply_recovery: List[Dict[str, Any]],
    errors: List[Dict[str, Any]],
    pending_mention: Dict[str, Any],
    pending_qt: Dict[str, Any],
    pending_meme: Dict[str, Any],
    pending_quote: Dict[str, Any],
    is_reply_visual_description_event: bool,
    input_file_indexes: Optional[Dict[str, int]],
    is_reply_target_eligibility_restriction: Callable[[str], bool],
    is_deleted_or_inaccessible_tweet_403: Callable[[str], bool],
    add_or_merge_local_rejection: Callable[..., Dict[str, Any]],
    short: Callable[[str, int], str],
    record_source_ref: Callable[[Record, Optional[Dict[str, int]]], Dict[str, Any]],
    record_fingerprint: Callable[[Record], str],
    classify_operational_error: Callable[[str], str],
) -> Tuple[bool, bool]:
    """Observe one record's errors/recoveries; return asset and restriction flags."""
    is_self_test_error = (
        msg.startswith("SELFTEST FAIL:")
        or msg.startswith("Self-test finished with ")
        or ("Missing X credentials." in msg and any(e.get("message", "").startswith("SELFTEST FAIL:") for e in self_test_errors))
        or ("ENABLE_AUTO_REPLIES is True, but XAI_API_KEY is not set." in msg and any(e.get("message", "").startswith("SELFTEST FAIL:") for e in self_test_errors))
    )
    is_remote_write_barrier = _remote_write_barrier_category(msg) is not None
    is_handled_reply_restriction = not is_remote_write_barrier and (
        is_reply_target_eligibility_restriction(msg)
        or is_deleted_or_inaccessible_tweet_403(msg)
        or "reply not allowed" in msg.lower()
        or "marking quote tweet as skipped without consuming reply quota" in msg.lower()
        or "not allowed to reply" in msg.lower()
        or "author has restricted who can reply" in msg.lower()
    )
    is_receipt_routine = (
        "Wrote confirmed regular-post receipt pending local reconciliation" in msg
        or "Wrote confirmed meme-post receipt pending local reconciliation" in msg
        or "Wrote confirmed reply receipt pending local reconciliation" in msg
        or "Wrote conversational reply sending receipt" in msg
        or "Promoted conversational reply receipt to confirmed" in msg
        or "Removed conversational reply sending receipt after definite non-success" in msg
        or "Removed conversational reply sending receipt after confirmed identity" in msg
        or "Removed reconciled regular-post receipt" in msg
        or "Removed reconciled meme-post receipt" in msg
        or "Removed reconciled confirmed-reply receipt" in msg
        or "Reconciling confirmed regular quote/image post receipt" in msg
        or "Reconciling confirmed meme post receipt" in msg
        or "Reconciling confirmed reply receipt" in msg
        or "Reconciled confirmed reply receipt before checking" in msg
        or "Reconciled regular quote/image receipt; not creating a second regular post" in msg
        or "Reconciled meme post receipt; not creating a second meme post" in msg
        or "Wrote main-post sending receipt" in msg
        or "Handed confirmed media upload to durable main-post attempt" in msg
        or "Promoted main-post receipt to attempting" in msg
        or "Removed main-post sending receipt" in msg
        or "Promoted main-post attempt to confirmed pending-schedule receipt" in msg
        or "Re-established confirmed pending-schedule receipt durability" in msg
        or "Finalised confirmed pending-schedule receipt" in msg
        or "Wrote confirmed regular pending-schedule receipt" in msg
        or "Finalised regular-post pending schedule" in msg
        or "Promoted regular-post sending receipt to confirmed" in msg
        or "Wrote confirmed meme pending-schedule receipt" in msg
        or "Finalised meme-post pending schedule" in msg
        or "Promoted meme-post sending receipt to confirmed" in msg
        or "Removed conversational reply sending receipt disposition=" in msg
        or "Resumed interrupted exact source-receipt retirement" in msg
        or "Resumed interrupted confirmed-media fence retirement" in msg
        or "Recovered crash-left permanent retirement-ledger exchanges" in msg
    )
    is_confirmed_post_recovery = (
        "Confirmed regular quote/image post_id=" in msg
        or "Confirmed meme post_id=" in msg
        or "Confirmed regular quote/image post " in msg
        or "Confirmed meme post " in msg
        or "REMOTE X POST WAS CONFIRMED; DO NOT RETRY MANUALLY" in msg
    )
    is_confirmed_reply_recovery = (
        "Malformed confirmed-reply receipt blocks" in msg
        or "Invalid confirmed-reply receipt blocks" in msg
        or "Semantically invalid confirmed-reply receipt blocks" in msg
        or "Confirmed reply receipt was applied in memory but state save failed" in msg
        or "Confirmed reply receipt state was saved but receipt removal failed" in msg
        or "Confirmed reply id=" in msg
        or "Confirmed quote-tweet reply id=" in msg
        or "reply required its durable state fallback" in msg
    )
    is_asset_metadata_warning = (
        "Quote analysis" in msg
        or "quote analysis" in msg
        or "Image analysis" in msg
        or "image analysis" in msg
        or "Skipping unanalysed current quote" in msg
        or "Image metadata stale" in msg
        or "absent from image analysis" in msg
        or "no valid per-image analysis" in msg
        or "Could not hash current image" in msg
        or "No analysed currently eligible regular-post images" in msg
        or "Image used-history still contains legacy integer entries" in msg
    )
    is_reply_media_context = msg.startswith("Reply media context")
    clarification_mode_refusal = re.search(
        r"Clarification reply lacks direct_factual_answer mode; refusing target_id=(\d+)",
        msg,
    )
    if clarification_mode_refusal is not None:
        add_or_merge_local_rejection(
            r.ts,
            lane=pending_mention.get("source") or "unavailable",
            target_id=clarification_mode_refusal.group(1),
            reason="clarification_not_direct_factual_answer",
            original_local_rejection_reason=(
                "clarification_not_direct_factual_answer"
            ),
            pipeline_stage_status="approved",
            effective_status="local_rejection",
            effective_reason="clarification_not_direct_factual_answer",
            direct_answer_repair_attempted=False,
            direct_answer_repair_outcome="not_available_legacy_telemetry",
            incoming_contribution=pending_mention.get("incoming_text", ""),
            proposed_draft=None,
            repaired_draft=None,
        )

    # Error/warning collection. Exclude routine KeyboardInterrupt, expected
    # self-test failures, and handled target restrictions from operational errors.
    if is_self_test_error:
        self_test_errors.append({
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "level": r.level,
            "where": f"{r.src}:{r.line}",
            "message": short(msg, 900),
            "source_refs": [record_source_ref(r, input_file_indexes)],
        })
    elif is_confirmed_post_recovery and r.level in {"ERROR", "CRITICAL", "WARNING"}:
        confirmed_post_recovery.append({
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "level": r.level,
            "where": f"{r.src}:{r.line}",
            "message": short(msg, 900),
            "source_refs": [record_source_ref(r, input_file_indexes)],
        })
    elif is_confirmed_reply_recovery and r.level in {"ERROR", "CRITICAL", "WARNING"}:
        confirmed_reply_recovery.append({
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "level": r.level,
            "where": f"{r.src}:{r.line}",
            "message": short(msg, 900),
            "source_refs": [record_source_ref(r, input_file_indexes)],
        })
    elif is_receipt_routine and r.level in {"ERROR", "CRITICAL", "WARNING"}:
        pass
    elif is_asset_metadata_warning and r.level in {"ERROR", "CRITICAL", "WARNING"}:
        pass
    elif is_reply_media_context and r.level in {"ERROR", "CRITICAL", "WARNING"}:
        pass
    elif is_reply_visual_description_event and r.level in {
        "ERROR",
        "CRITICAL",
        "WARNING",
    }:
        pass
    elif is_handled_reply_restriction and r.level in {"ERROR", "CRITICAL", "WARNING"}:
        # The raw X API 403 is classified below. Follow-up warnings such as
        # "marking skipped without consuming quota" are expected handling.
        pass
    elif r.level in {"ERROR", "CRITICAL"} or (r.level == "WARNING" and "Bot stopped by KeyboardInterrupt" not in msg):
        error_item = {
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "level": r.level,
            "where": f"{r.src}:{r.line}",
            "message": short(msg, 900),
            "_raw_message": msg,
            "_fingerprint": record_fingerprint(r),
            "source_refs": [record_source_ref(r, input_file_indexes)],
        }
        error_category = classify_operational_error(msg)
        if error_category == "local_media_preflight_failure":
            lane = _media_preflight_lane(msg)
            pending = {"daily_meme": pending_meme, "quote_image": pending_quote}.get(lane, {})
            error_item["_media_preflight_lane"] = lane
            error_item["_media_preflight_image"] = (
                _media_preflight_image(msg) or str(pending.get("image") or "")
            )
        if error_category == "remote_operations_paused":
            source = str(r.src or "").lower()
            pending_lane = ""
            if "historical_context" in source:
                pending_lane = "historical_context_reply"
            elif "quote_tweet" in source and pending_qt:
                pending_lane = "quote_tweet"
            elif (
                any(token in source for token in ("mention", "normal", "reply"))
                and pending_mention
            ):
                pending_lane = str(
                    pending_mention.get("source") or "mention"
                )
            elif "meme" in source and pending_meme:
                pending_lane = "daily_meme"
            elif "quote" in source and pending_quote:
                pending_lane = "quote_image"
            if pending_lane:
                error_item["_pause_pending_lane"] = pending_lane
        errors.append(error_item)
    return is_asset_metadata_warning, is_handled_reply_restriction


def _event_time(value: Dict[str, Any]) -> Optional[datetime]:
    try:
        return parse_dt(str(value.get("time") or ""))
    except ValueError:
        return None


def _base_remote_control_key(
    value: Any,
    *,
    remote_control_scope_by_key: Mapping[str, str],
) -> str:
    """Return a recognised runtime-control key without its timed suffix."""

    key = str(value or "").strip().lower()
    if key.endswith("_until"):
        key = key.removesuffix("_until")
    return key if key in remote_control_scope_by_key else ""


def _remote_control_scope(
    value: Any,
    *,
    base_remote_control_key: Callable[[Any], str],
    remote_control_scope_by_key: Mapping[str, str],
) -> str:
    """Map one exact control key to the operation scope it pauses."""

    return remote_control_scope_by_key.get(
        base_remote_control_key(value),
        "unknown",
    )


def _remote_operation_scope_for_lane(
    value: Any,
    *,
    remote_lane_scope: Mapping[str, str],
) -> str:
    """Map one runtime/log lane to its successful-operation scope."""

    lane = str(value or "").strip().lower().replace("-", "_")
    return remote_lane_scope.get(lane, "unknown")


def _explicit_remote_pause_scope(
    message: Any,
    *,
    base_remote_control_key: Callable[[Any], str],
    remote_control_scope: Callable[[Any], str],
    remote_operation_scope_for_lane: Callable[[Any], str],
) -> Tuple[str, str, List[str]]:
    """Extract an explicit control key or operation lane from one exception."""

    lowered = str(message or "").lower()
    key_matches = [
        match.group(0).lower()
        for match in re.finditer(
            r"\b(?:disable|pause)_(?:all|replies|normal_replies|"
            r"quote_replies|hot_post_replies|quote_posts|meme_posts)"
            r"(?:_until)?\b",
            lowered,
        )
    ]
    if key_matches:
        keys = sorted({base_remote_control_key(key) for key in key_matches})
        return remote_control_scope(keys[0]), f"explicit control key {key_matches[0]}", keys
    if "global runtime control pause" in lowered:
        return "all_remote_writes", "explicit global runtime-control wording", []
    lane_match = re.search(
        r"\b(?:lane|source)\s*[=:]\s*([a-z][a-z0-9_-]*)",
        lowered,
    )
    if lane_match is not None:
        scope = remote_operation_scope_for_lane(lane_match.group(1))
        if scope != "unknown":
            return scope, f"explicit lane {lane_match.group(1)} in the exception", []
    for phrase, scope in {
        "historical context": "historical_context_replies",
        "historical-context": "historical_context_replies",
        "quote/image": "quote_image_posts",
        "quote image": "quote_image_posts",
        "daily meme": "daily_meme_posts",
        "quote-tweet reply": "quote_replies",
        "quote tweet reply": "quote_replies",
        "hot-post reply": "hot_post_replies",
        "hot post reply": "hot_post_replies",
        "mention reply": "normal_replies",
    }.items():
        if phrase in lowered:
            return scope, f"explicit {phrase} wording in the exception", []
    return "unknown", "", []


def _pipeline_recovered_after(
    identity: Tuple[str, str], last_time: datetime,
    *,
    events: List[Dict[str, Any]],
    get_event_time: Callable[[Dict[str, Any]], Optional[datetime]],
) -> Tuple[bool, str, Optional[datetime]]:
    """Find the earliest later terminal recovery for the supplied lane/target."""
    lane, target_id = identity
    candidates: List[Tuple[datetime, str]] = []
    for event in events:
        ts = get_event_time(event)
        if ts is None or ts <= last_time:
            continue
        if (
            _normalise_lane(event.get("lane")) != lane
            or str(event.get("target_id") or "") != target_id
        ):
            continue
        kind = event.get("kind")
        if kind == "reply_strategy_decision":
            if _is_terminal_pipeline_failure(
                event.get("reason") or event.get("no_reply_reason"),
                event.get("status"),
            ):
                continue
            terminal_local_outcome = _terminal_local_rejection_outcome(
                event.get("reason")
            ) or _terminal_local_rejection_outcome(
                event.get("no_reply_reason")
            )
            if terminal_local_outcome is not None:
                candidates.append(
                    (
                        ts,
                        "later terminal local decision observed for "
                        f"{lane} target {target_id}",
                    )
                )
            elif event.get("mode") == "no_reply":
                candidates.append(
                    (
                        ts,
                        "later terminal no-reply decision observed for "
                        f"{lane} target {target_id}",
                    )
                )
        elif kind == "reply_strategy_outcome" and str(
            event.get("status") or "confirmed"
        ) in {"confirmed", "posted"}:
            candidates.append(
                (
                    ts,
                    "later confirmed reply outcome observed for "
                    f"{lane} target {target_id}",
                )
            )
        elif (
            kind == "reply_strategy_local_rejection"
            and _terminal_local_rejection_outcome(event.get("reason"))
            is not None
        ):
            candidates.append(
                (
                    ts,
                    "later terminal local rejection observed for "
                    f"{lane} target {target_id}",
                )
            )
    if not candidates:
        return False, "", None
    recovery_time, reason = min(candidates, key=lambda item: (item[0], item[1]))
    return True, reason, recovery_time


def _remote_pause_recovery_status(
    scope: str,
    control_keys: List[str],
    last_time: datetime,
    *,
    safety: Dict[str, Any],
    events: List[Dict[str, Any]],
    lifecycle: List[Dict[str, Any]],
    remote_operation_successes: List[Dict[str, Any]],
    base_remote_control_key: Callable[[Any], str],
    remote_control_scope: Callable[[Any], str],
    explicit_remote_pause_scope: Callable[[Any], Tuple[str, str, List[str]]],
    get_event_time: Callable[[Dict[str, Any]], Optional[datetime]],
) -> Tuple[str, str, Optional[datetime]]:
    """Require both scope-matched control clearance and later success."""

    if scope == "unknown":
        return (
            "resolution_unavailable",
            "affected pause scope is unavailable from retained evidence; unrelated remote-write success cannot establish recovery",
            None,
        )

    normalised_keys = {key for value in control_keys if (key := base_remote_control_key(value))}
    control = safety.get("control") or {}
    current_control_clear = bool(
        safety.get("available") is True
        and control.get("valid") is True
        and not any(
            remote_control_scope(value)
            in (
                {scope}
                if normalised_keys
                else {
                    "all_remote_writes": {"all_remote_writes"},
                    "all_replies": {"all_remote_writes", "all_replies"},
                    "normal_replies": {"all_remote_writes", "all_replies", "normal_replies"},
                    "hot_post_replies": {"all_remote_writes", "all_replies", "normal_replies", "hot_post_replies"},
                    "quote_replies": {"all_remote_writes", "all_replies", "quote_replies"},
                    "quote_image_posts": {"all_remote_writes", "quote_image_posts"},
                    "daily_meme_posts": {"all_remote_writes", "daily_meme_posts"},
                    "historical_context_replies": {"all_remote_writes", "all_replies", "historical_context_replies"},
                }.get(scope, set())
            )
            for value in control.get("active_keys") or []
        )
    )

    explicit_clears = []
    for item in [*events, *lifecycle]:
        clear_time = get_event_time(item)
        message = str(item.get("message") or "")
        if item.get("kind") == "runtime_control_clear":
            clear_scope = remote_control_scope(item.get("key"))
        elif "runtime control pause cleared" in message.lower():
            clear_scope = explicit_remote_pause_scope(message)[0]
        else:
            continue
        if clear_time is not None and clear_time > last_time and clear_scope == scope:
            explicit_clears.append(clear_time)

    for recovery in sorted(remote_operation_successes, key=lambda item: (item["time"], item["kind"])):
        if recovery["time"] <= last_time or scope not in recovery["scopes"]:
            continue
        if current_control_clear or any(clear <= recovery["time"] for clear in explicit_clears):
            return (
                "historical_resolved",
                "the affected control scope cleared and later successful "
                + str(recovery["kind"]).replace("_", " ")
                + " occurred in the same scope",
                recovery["time"],
            )
    return "current_unresolved", "", None


def _matching_ambiguity_identity(
    raw: str,
    item_time: Optional[datetime],
    *,
    ambiguous_reply_outcomes: List[Dict[str, Any]],
    transport_attempts: List[Dict[str, Any]],
    ambiguous_media_outcomes: List[Dict[str, Any]],
    seconds_between: Callable[[datetime, datetime], float],
) -> Optional[Dict[str, Any]]:
    """Return the strongest uniquely associated ambiguity identity."""

    if item_time is None:
        return None
    direct = re.search(r"\blane=([^\s]+) target_id=([^\s]+)", raw)
    if direct is not None:
        lane = _normalise_lane(direct.group(1))
        target_id = direct.group(2)
        matches = [
            outcome
            for outcome in ambiguous_reply_outcomes
            if outcome["lane"] == lane and outcome["target_id"] == target_id
            and seconds_between(item_time, outcome["time"]) <= 300
        ]
        if matches:
            return min(
                matches,
                key=lambda outcome: seconds_between(item_time, outcome["time"]),
            )
        attempts = [
            attempt
            for attempt in transport_attempts
            if attempt["target_id"] == target_id
            and seconds_between(item_time, attempt["time"]) <= 300
        ]
        attempt = min(
            attempts,
            key=lambda value: seconds_between(item_time, value["time"]),
            default={},
        )
        return {
            "time": item_time,
            "lane": lane,
            "target_id": target_id,
            "transaction_id": str(attempt.get("transaction_id") or ""),
        }

    transaction_match = re.search(
        r"\btransaction_id=([0-9a-f]{64})\b", raw
    )
    if transaction_match is not None:
        transaction_id = transaction_match.group(1)
        for outcome in ambiguous_reply_outcomes:
            if outcome.get("transaction_id") == transaction_id:
                return outcome

    near_reply = [
        outcome
        for outcome in ambiguous_reply_outcomes
        if seconds_between(item_time, outcome["time"]) <= 10
    ]
    if near_reply:
        nearest_delta = min(
            seconds_between(item_time, outcome["time"])
            for outcome in near_reply
        )
        nearest = [
            outcome
            for outcome in near_reply
            if seconds_between(item_time, outcome["time"]) == nearest_delta
        ]
        identities = {
            (
                str(outcome.get("transaction_id") or ""),
                outcome["lane"],
                outcome["target_id"],
            )
            for outcome in nearest
        }
        if len(identities) == 1:
            return nearest[0]

    near_media = [
        outcome
        for outcome in ambiguous_media_outcomes
        if seconds_between(item_time, outcome["time"]) <= 10
    ]
    if len(near_media) == 1:
        return near_media[0]

    first_line = raw.splitlines()[0].strip() if raw else ""
    persistent_barrier = bool(
        "All remote posting and reply lanes are paused by the durable "
        "remote-write safety barrier" in first_line
        or "lane stopped by the global remote-write safety barrier" in first_line
        or "reply stopped after an ambiguous remote outcome" in first_line
        or "lane created an ambiguous-post barrier" in first_line
    )
    if persistent_barrier:
        prior = [
            outcome
            for outcome in [*ambiguous_reply_outcomes, *ambiguous_media_outcomes]
            if outcome["time"] <= item_time
        ]
        if prior:
            return max(prior, key=lambda outcome: outcome["time"])
    return None


def _is_subordinate_remote_write_symptom(
    *,
    category: str,
    raw: str,
    item_time: Optional[datetime],
    ambiguous_reply_outcomes: List[Dict[str, Any]],
    ambiguity_times: List[datetime],
    seconds_between: Callable[[datetime, datetime], float],
) -> bool:
    """Bind exact receipt/lane symptoms to a logged reply ambiguity root."""

    if item_time is None:
        return False
    if category == "conversational_reply_receipt_barrier":
        identity = re.search(
            r"\blane=([^\s]+) target_id=([^\s]+)",
            raw,
        )
        if identity is None:
            return False
        lane = _normalise_lane(identity.group(1))
        target_id = identity.group(2)
        return any(
            outcome["lane"] == lane
            and outcome["target_id"] == target_id
            and 0
            <= (outcome["time"] - item_time).total_seconds()
            <= 300
            for outcome in ambiguous_reply_outcomes
        )
    if category == "remote_write_transaction_barrier":
        return any(
            seconds_between(item_time, root_time) <= 5
            for root_time in ambiguity_times
        )
    first_line = raw.splitlines()[0].strip() if raw else ""
    exact_lane_barrier = bool(
        re.fullmatch(
            r"(?:Normal reply|Quote-tweet) lane (?:stopped by the global "
            r"remote-write safety barrier|created an ambiguous-post barrier; "
            r"skipping all later lanes)",
            first_line,
        )
        or re.fullmatch(
            r"Test-cycle (?:normal|quote_tweet) reply lane stopped by the "
            r"global remote-write safety barrier",
            first_line,
        )
        or first_line
        == "Test cycle stopped after an ambiguous remote post; no later lane will run"
        or re.fullmatch(
            r"(?:Mention|Hot-post|Quote-tweet) reply stopped after an "
            r"ambiguous remote outcome; the global remote-write safety "
            r"barrier remains active",
            first_line,
        )
    )
    if not exact_lane_barrier:
        return False
    return any(
        0 <= (item_time - outcome["time"]).total_seconds() <= 5
        for outcome in ambiguous_reply_outcomes
    )


def _remote_write_recovery_status(
    category: str,
    identity: Dict[str, Any],
    first_time: datetime,
    last_time: datetime,
    *,
    identity_snapshot_available: bool,
    active_component_matches: Callable[[str, Dict[str, Any]], bool],
    active_remote_components: List[Dict[str, Any]],
    component_is_related_to_selected_window: Callable[[Dict[str, Any]], bool],
    component_is_relevant_to_category: Callable[[Dict[str, Any], str], bool],
    identity_snapshot_explicitly_unavailable: bool,
    safety: Dict[str, Any],
    terminal_reply_receipts: List[Dict[str, Any]],
    handled_api_restrictions: List[Dict[str, Any]],
    remote_write_success_times: List[datetime],
    fromtimestamp: Callable[[int], datetime],
    get_event_time: Callable[[Dict[str, Any]], Optional[datetime]],
) -> Tuple[str, str, Optional[datetime]]:
    """Reconcile one identified receipt/ambiguity transaction conservatively."""

    if identity_snapshot_available and active_component_matches(
        category,
        identity,
    ):
        return "current_unresolved", "", None
    unidentified_active = any(
        component.get("identity_available") is not True
        for component in active_remote_components
        if component_is_related_to_selected_window(component)
        and component_is_relevant_to_category(component, category)
    )
    if identity_snapshot_available and unidentified_active:
        return (
            "resolution_unavailable",
            "current barrier artefacts lack enough identity to establish whether they match this transaction",
            None,
        )
    if identity_snapshot_explicitly_unavailable:
        return (
            "resolution_unavailable",
            "current status cannot be established from retained evidence because no usable filesystem snapshot is available",
            None,
        )
    if not identity_snapshot_available:
        return "legacy_fallback", "", None

    transaction_id = str(identity.get("transaction_id") or "")
    target_id = str(identity.get("target_id") or "")
    lane = _normalise_lane(identity.get("lane"))
    archive = safety.get("reconciliation_archive") or {}
    marker_audits = archive.get("marker_reconciliations") or []
    if not marker_audits:
        latest_audit = archive.get("latest_marker_reconciliation")
        marker_audits = [latest_audit] if latest_audit else []
    def audit_matches(audit: Dict[str, Any]) -> bool:
        audit_epoch = audit.get("archived_at_epoch")
        if (
            archive.get("valid") is not True
            or type(audit_epoch) is not int
            or audit_epoch < int(last_time.timestamp())
        ):
            return False
        audit_transaction_id = str(audit.get("transaction_id") or "")
        audit_target_id = str(audit.get("target_id") or "")
        if transaction_id and audit_transaction_id:
            return bool(
                transaction_id == audit_transaction_id
                and (
                    not target_id
                    or not audit_target_id
                    or target_id == audit_target_id
                )
            )
        return bool(
            target_id
            and audit_target_id == target_id
            and audit_epoch
            <= int((last_time + timedelta(hours=6)).timestamp())
        )

    matching_audits = [audit for audit in marker_audits if audit_matches(audit)]
    if category == "remote_write_ambiguity_barrier" and matching_audits:
        resolution_audit = min(
            matching_audits,
            key=lambda audit: (
                audit["archived_at_epoch"],
                str(audit.get("audit_path") or ""),
            ),
        )
        return (
            "historical_resolved",
            "matching durable offline reconciliation audit retired this transaction; current active barriers belong to another identity",
            fromtimestamp(resolution_audit["archived_at_epoch"]),
        )

    terminal_matches = [
        item
        for item in terminal_reply_receipts
        if str(item.get("target_id") or "") == target_id
        and (
            lane == "unavailable"
            or _normalise_lane(item.get("lane")) == lane
        )
        and item["_time"] >= first_time
    ]
    handled_deleted = [
        item
        for item in handled_api_restrictions
        if item.get("restriction_kind") == "deleted_or_inaccessible_tweet"
        and str(item.get("target_id") or "") == target_id
        and (
            lane == "unavailable"
            or _normalise_lane(item.get("lane")) == lane
        )
        and (restriction_time := get_event_time(item)) is not None
        # The handled 403 is logged immediately before the ambiguity
        # wrapper, so allow it to precede the root record narrowly.
        and first_time - timedelta(minutes=5)
        <= restriction_time
        <= last_time + timedelta(minutes=5)
    ]
    if terminal_matches and handled_deleted:
        terminal_time = min(item["_time"] for item in terminal_matches)
        later_successes = [
            ts for ts in remote_write_success_times if ts > terminal_time
        ]
        if later_successes:
            return (
                "historical_resolved",
                "deleted/inaccessible target was handled, its sending receipt was retired, and a later remote write succeeded",
                terminal_time,
            )

    return (
        "resolution_unavailable",
        "no matching active artefact remains, but terminal resolution is unavailable from retained evidence",
        None,
    )


def _recovered_after(
    category: str,
    last_time: datetime,
    *,
    events: List[Dict[str, Any]],
    event_times: Dict[str, List[datetime]],
    receipt_removed_times: List[datetime],
    successful_restart_times: List[datetime],
    safety: Dict[str, Any],
    get_event_time: Callable[[Dict[str, Any]], Optional[datetime]],
    fromtimestamp: Callable[[int], datetime],
    clock_now: Callable[[], datetime],
) -> Tuple[bool, str, Optional[datetime]]:
    """Find category recovery from prepared history and conditional safety evidence."""
    candidates: List[Tuple[datetime, str]] = []
    recovery_kinds: Tuple[str, ...] = ()
    if category in {
        "historical_context_source_role_incompatibility",
        "historical_context_reply_failure",
    }:
        for event in events:
            ts = get_event_time(event)
            if (
                ts is not None
                and ts > last_time
                and event.get("kind") == "historical_context_reply"
                and event.get("status") in {"completed", "already_completed"}
            ):
                candidates.append((ts, "later historical-context reply completed"))
        for event in events:
            ts = get_event_time(event)
            if (
                ts is not None
                and ts > last_time
                and event.get("kind") == "historical_context_semantic_gate"
                and event.get("status") == "loaded"
            ):
                candidates.append(
                    (ts, "later historical-context semantic gate loaded successfully")
                )
    elif category == "legacy_regular_receipt_barrier":
        candidates.extend(
            (ts, "regular receipt reconciled or retired")
            for ts in receipt_removed_times
            if ts > last_time
        )
        recovery_kinds = ("daily_meme_posted", "quote_image_posted")
    elif category == "daily_meme_failure":
        recovery_kinds = ("daily_meme_posted",)
    elif category == "quote_image_posting_failure":
        recovery_kinds = ("quote_image_posted",)
    elif category == "conversational_reply_posting_failure":
        recovery_kinds = (
            "mention_reply_posted",
            "hot_post_reply_posted",
            "quote_tweet_reply_posted",
        )
    elif category == "quote_pagination_protocol_anomaly":
        recovery_kinds = (
            "quote_lane_activity_succeeded",
            "quote_pagination_repeated_token",
        )
    elif category == "process_crash":
        candidates.extend(
            (ts, "later successful bot startup observed")
            for ts in successful_restart_times
            if ts > last_time
        )
    elif category == "instance_lock_conflict":
        candidates.extend(
            (ts, "later successful single-instance bot startup observed")
            for ts in successful_restart_times
            if ts > last_time
        )
    elif category in {
        "remote_write_ambiguity_barrier",
        "remote_write_protocol_barrier",
    }:
        if safety.get("configured") is True and safety.get("available") is True:
            protocol_valid = (
                (safety.get("protocol") or {}).get("valid") is True
            )
            active_entries = safety.get("active_entries")
            active_marker_names = safety.get("active_marker_names")
            transport = safety.get("transport") or {}
            current_clear = safety.get("blocking") is False
            authoritative_barrier_namespace_clear = bool(
                safety.get("blocking") is False
                and isinstance(active_entries, list)
                and not active_entries
                and isinstance(active_marker_names, list)
                and not active_marker_names
                and transport.get("blocking") is False
                and transport.get("classification") == "clear"
            )
            if category == "remote_write_ambiguity_barrier":
                proved = safety.get("reconciliation_proven") is True
                archive = safety.get("reconciliation_archive") or {}
                marker_audits = archive.get("marker_reconciliations") or []
                if not marker_audits:
                    latest = archive.get("latest_marker_reconciliation")
                    marker_audits = [latest] if latest else []
                following_audits = [
                    item
                    for item in marker_audits
                    if type(item.get("archived_at_epoch")) is int
                    and item["archived_at_epoch"]
                    >= int(last_time.timestamp())
                ]
                if (
                    authoritative_barrier_namespace_clear
                    and protocol_valid
                    and proved
                    and archive.get("valid") is True
                    and following_audits
                ):
                    resolution_audit = min(
                        following_audits,
                        key=lambda item: (
                            item["archived_at_epoch"],
                            str(item.get("audit_path") or ""),
                        ),
                    )
                    resolved_at = fromtimestamp(
                        resolution_audit["archived_at_epoch"]
                    )
                    return (
                        True,
                        "durable offline reconciliation audit is valid and the current barrier namespace is clear",
                        resolved_at,
                    )
            elif current_clear and protocol_valid:
                return (
                    True,
                    "current protocol snapshot is valid with no active transaction barrier",
                    clock_now(),
                )
    for kind in recovery_kinds:
        candidates.extend(
            (ts, f"later {kind.replace('_', ' ')} observed")
            for ts in event_times.get(kind, [])
            if ts > last_time
        )
    if not candidates:
        return False, "", None
    recovery_time, reason = min(candidates, key=lambda item: (item[0], item[1]))
    return True, reason, recovery_time


def _prepare_pipeline_incident_evidence(
    events: List[Dict[str, Any]],
    operational: List[Dict[str, Any]],
    *,
    get_event_time: Callable[[Dict[str, Any]], Optional[datetime]],
) -> Tuple[
    Dict[Tuple[str, str], List[Dict[str, Any]]],
    Dict[int, Tuple[Tuple[str, str], str]],
]:
    """Associate structured pipeline failures and their exact raw error evidence."""
    pipeline_failures_by_identity: Dict[
        Tuple[str, str], List[Dict[str, Any]]
    ] = {}
    for event in events:
        if event.get("kind") != "reply_strategy_failure":
            continue
        lane = _normalise_lane(event.get("lane"))
        target_id = str(event.get("target_id") or "")
        if lane == "unavailable" or not target_id:
            continue
        pipeline_failures_by_identity.setdefault((lane, target_id), []).append(
            event
        )

    raw_pipeline_evidence: Dict[
        int, Tuple[Tuple[str, str], str]
    ] = {}
    for item in operational:
        raw = str(item.get("_raw_message") or item.get("message") or "")
        item_time = get_event_time(item)
        if item_time is None:
            continue
        pipeline_ended_match = re.fullmatch(
            r"AI-first reply pipeline ended\s+"
            r"status=(?P<status>\S+)\s+lane=(?P<lane>\S+)\s+"
            r"target_id=(?P<target_id>[A-Za-z0-9_-]+)\s+"
            r"reason=(?P<reason>\S+)\s+calls=\d+\s+revisions=\d+",
            raw.strip(),
        )
        if pipeline_ended_match is not None:
            lane = _normalise_lane(pipeline_ended_match.group("lane"))
            target_id = pipeline_ended_match.group("target_id")
            reason = pipeline_ended_match.group("reason")
            matching_failures: List[Tuple[str, str]] = []
            if (
                pipeline_ended_match.group("status") == "operational_failure"
                and lane in {"mention", "hot-post", "quote-tweet"}
                and target_id
                and reason
            ):
                identity = (lane, target_id)
                for failure in pipeline_failures_by_identity.get(identity, []):
                    failure_time = get_event_time(failure)
                    if (
                        failure_time is not None
                        and str(failure.get("reason") or "") == reason
                        and abs((item_time - failure_time).total_seconds()) <= 5
                    ):
                        matching_failures.append(identity)
            if len(matching_failures) == 1:
                raw_pipeline_evidence[id(item)] = (
                    matching_failures[0],
                    "pipeline_error",
                )
            continue
        lowered = raw.lower()
        if "failed to ask grok for reply" not in lowered or "apierror" not in lowered:
            continue
        where = str(item.get("where") or "").lower()
        lane_hint: Optional[str] = None
        lane_match = re.search(r"\blane[=:]\s*([a-z_-]+)", raw, re.IGNORECASE)
        if lane_match:
            parsed_lane = _normalise_lane(lane_match.group(1))
            if parsed_lane != "unavailable":
                lane_hint = parsed_lane
        target_match = re.search(
            r"\btarget_id[=:]\s*([A-Za-z0-9_-]+)", raw, re.IGNORECASE
        )
        target_hint = target_match.group(1) if target_match else None
        if lane_hint is None and target_hint is None:
            if "quote_tweet" in where or "quote-tweet" in where:
                lane_hint = "quote-tweet"
            elif "hot_post" in where or "hot-post" in where:
                lane_hint = "hot-post"
            elif "maybe_reply_to_mentions" not in where and "mention" in where:
                lane_hint = "mention"
        candidates: List[Tuple[float, Tuple[str, str]]] = []
        for identity, failures_for_target in pipeline_failures_by_identity.items():
            lane, target_id = identity
            if lane_hint is not None and lane != lane_hint:
                continue
            if target_hint is not None and target_id != target_hint:
                continue
            deltas = [
                (item_time - failure_time).total_seconds()
                for failure in failures_for_target
                if (failure_time := get_event_time(failure)) is not None
            ]
            causal_deltas = [delta for delta in deltas if 0 <= delta <= 5]
            if causal_deltas:
                candidates.append((min(causal_deltas), identity))
        if candidates:
            nearest_delta = min(delta for delta, _identity in candidates)
            nearest_identities = sorted(
                {
                    identity
                    for delta, identity in candidates
                    if delta == nearest_delta
                }
            )
            if len(nearest_identities) == 1:
                raw_pipeline_evidence[id(item)] = (
                    nearest_identities[0],
                    "outer_wrapper",
                )
    return pipeline_failures_by_identity, raw_pipeline_evidence


def _prepare_remote_ambiguity_evidence(
    serious: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    remote_write_transactions: List[Dict[str, Any]],
    *,
    classify_operational_error: Callable[[str], str],
    get_event_time: Callable[[Dict[str, Any]], Optional[datetime]],
    seconds_between: Callable[[datetime, datetime], float],
) -> Tuple[
    List[datetime], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]],
]:
    """Prepare ambiguity times and causal reply, transport and media evidence."""
    ambiguity_times = [
        get_event_time(item)
        for item in serious
        if classify_operational_error(
            str(item.get("_raw_message") or item.get("message") or "")
        )
        == "remote_write_ambiguity_barrier"
    ]
    ambiguity_times = [item for item in ambiguity_times if item is not None]
    transport_attempts: List[Dict[str, Any]] = []
    for transaction in remote_write_transactions:
        if (
            transaction.get("kind") != "tweet_transport"
            or transaction.get("phase") != "request_started"
        ):
            continue
        transaction_time = get_event_time(transaction)
        target_id = str(transaction.get("reply_to_id") or "")
        transaction_id = str(transaction.get("transaction_id") or "")
        if (
            transaction_time is None
            or not target_id
            or target_id.lower() in {"none", "null"}
        ):
            continue
        transport_attempts.append(
            {
                "time": transaction_time,
                "target_id": target_id,
                "transaction_id": transaction_id,
                "lane": str(transaction.get("lane") or ""),
            }
        )

    ambiguous_reply_outcomes: List[Dict[str, Any]] = []
    for event in events:
        if (
            event.get("kind") != "reply_strategy_outcome"
            or event.get("status") != "posting_failed_retryable"
            or event.get("failure_reason") != "ambiguous_remote_outcome"
        ):
            continue
        outcome_time = get_event_time(event)
        lane = _normalise_lane(event.get("lane"))
        target_id = str(event.get("target_id") or "")
        if outcome_time is None or lane == "unavailable" or not target_id:
            continue
        if not any(
            seconds_between(outcome_time, root_time) <= 5
            for root_time in ambiguity_times
        ):
            continue
        matching_attempts = [
            attempt
            for attempt in transport_attempts
            if attempt["target_id"] == target_id
            and 0 <= (outcome_time - attempt["time"]).total_seconds() <= 10
        ]
        matching_attempts.sort(
            key=lambda attempt: (
                (outcome_time - attempt["time"]).total_seconds(),
                attempt["transaction_id"],
            )
        )
        transaction_id = (
            matching_attempts[0]["transaction_id"] if matching_attempts else ""
        )
        ambiguous_reply_outcomes.append(
            {
                "time": outcome_time,
                "lane": lane,
                "target_id": target_id,
                "transaction_id": transaction_id,
            }
        )

    ambiguous_media_outcomes: List[Dict[str, Any]] = []
    for transaction in remote_write_transactions:
        if (
            transaction.get("kind") != "media_upload"
            or transaction.get("phase") != "ambiguous"
        ):
            continue
        transaction_time = get_event_time(transaction)
        if transaction_time is None:
            continue
        ambiguous_media_outcomes.append(
            {
                "time": transaction_time,
                "image": str(transaction.get("image") or ""),
            }
        )
    return (
        ambiguity_times,
        transport_attempts,
        ambiguous_reply_outcomes,
        ambiguous_media_outcomes,
    )


def _prepare_recovery_evidence(
    events: List[Dict[str, Any]],
    receipt_events: List[Dict[str, Any]],
    lifecycle: List[Dict[str, Any]],
    confirmed_reply_receipt_events: List[Dict[str, Any]],
    *,
    get_event_time: Callable[[Dict[str, Any]], Optional[datetime]],
) -> Tuple[
    Dict[str, List[datetime]], List[datetime], List[datetime], List[datetime],
    List[Dict[str, Any]], List[Dict[str, Any]],
]:
    """Prepare timed recovery evidence, shared operation scopes and terminal receipts."""
    event_times: Dict[str, List[datetime]] = {}
    for event in events:
        ts = get_event_time(event)
        if ts is not None:
            event_times.setdefault(str(event.get("kind") or ""), []).append(ts)
    receipt_removed_times: List[datetime] = []
    for item in receipt_events:
        if item.get("kind") not in {"regular_removed", "regular_reconciled"}:
            continue
        ts = get_event_time(item)
        if ts is not None:
            receipt_removed_times.append(ts)
    successful_restart_times: List[datetime] = []
    for item in lifecycle:
        message = str(item.get("message") or "")
        ts = get_event_time(item)
        if ts is not None and "Bot started successfully" in message:
            successful_restart_times.append(ts)

    remote_write_success_times = sorted(
        ts
        for kind in (
            "remote_write_succeeded",
            "daily_meme_posted",
            "quote_image_posted",
            "mention_reply_posted",
            "hot_post_reply_posted",
            "quote_tweet_reply_posted",
        )
        for ts in event_times.get(kind, [])
    )
    remote_operation_successes: List[Dict[str, Any]] = []
    success_scopes = {
        "daily_meme_posted": {"daily_meme_posts", "all_remote_writes"},
        "quote_image_posted": {"quote_image_posts", "all_remote_writes"},
        "mention_reply_posted": {"normal_replies", "all_replies", "all_remote_writes"},
        "hot_post_reply_posted": {"hot_post_replies", "normal_replies", "all_replies", "all_remote_writes"},
        "quote_tweet_reply_posted": {"quote_replies", "all_replies", "all_remote_writes"},
        # This generic transport confirmation has no lane identity.  It can
        # prove only that a process-wide pause cleared, never a lane pause.
        "remote_write_succeeded": {"all_remote_writes"},
    }
    for event in events:
        event_time = get_event_time(event)
        kind = str(event.get("kind") or "")
        scopes = success_scopes.get(kind)
        if (
            kind == "historical_context_reply"
            and event.get("status") in {"completed", "already_completed"}
        ):
            scopes = {"historical_context_replies", "all_replies", "all_remote_writes"}
        if event_time is not None and scopes:
            remote_operation_successes.append(
                {"time": event_time, "kind": kind, "scopes": scopes}
            )
    terminal_reply_receipts: List[Dict[str, Any]] = []
    for item in confirmed_reply_receipt_events:
        if item.get("source_class") == "selftest":
            continue
        if item.get("kind") not in {
            "sending_removed",
            "confirmed_state_fallback_removed",
            "removed",
        }:
            continue
        ts = get_event_time(item)
        if ts is not None:
            terminal_reply_receipts.append({**item, "_time": ts})
    return (
        event_times,
        receipt_removed_times,
        successful_restart_times,
        remote_write_success_times,
        remote_operation_successes,
        terminal_reply_receipts,
    )


def _pause_scope_for_item(
    item: Dict[str, Any],
    *,
    events: List[Dict[str, Any]],
    transport_attempts: List[Dict[str, Any]],
    explicit_remote_pause_scope: Callable[[Any], Tuple[str, str, List[str]]],
    get_event_time: Callable[[Dict[str, Any]], Optional[datetime]],
    base_remote_control_key: Callable[[Any], str],
    remote_control_scope: Callable[[Any], str],
    remote_operation_scope_for_lane: Callable[[Any], str],
) -> Tuple[str, str, List[str]]:
    """Use ordered, transaction-local evidence to scope one pause error."""

    raw = str(item.get("_raw_message") or item.get("message") or "")
    explicit = explicit_remote_pause_scope(raw)
    if explicit[0] != "unknown":
        return explicit

    item_time = get_event_time(item)
    if item_time is not None:
        nearby = sorted(
            (
                (abs((item_time - event_time).total_seconds()), event)
                for event in events
                if event.get("kind") == "runtime_control_pause"
                and (event_time := get_event_time(event)) is not None
                and abs((item_time - event_time).total_seconds()) <= 60
            ),
            key=lambda pair: (pair[0], str(pair[1].get("time") or "")),
        )
    else:
        nearby = []
    for _distance, event in nearby:
        key = base_remote_control_key(event.get("key"))
        if key:
            return remote_control_scope(key), "nearby structured runtime_control_pause event", [key]
        raw_lanes = event.get("control_lanes") or event.get("lanes")
        lanes = (
            raw_lanes
            if isinstance(raw_lanes, list)
            else re.split(r"\s*,\s*", str(raw_lanes or ""))
        )
        event_scopes = set()
        for lane in lanes:
            scope = remote_control_scope(lane)
            event_scopes.add(
                remote_operation_scope_for_lane(lane)
                if scope == "unknown"
                else scope
            )
        event_scopes.discard("unknown")
        if len(event_scopes) == 1:
            return event_scopes.pop(), "nearby structured runtime_control_pause lane", []

    pending_lane = str(item.get("_pause_pending_lane") or "")
    pending_scope = remote_operation_scope_for_lane(pending_lane)
    if pending_scope != "unknown":
        return pending_scope, f"exact pending lane {pending_lane} associated with the exception", []

    if item_time is not None:
        attempt_scopes = {
            remote_operation_scope_for_lane(attempt.get("lane"))
            for attempt in transport_attempts
            if 0
            <= (item_time - attempt["time"]).total_seconds()
            <= 10
        } - {"unknown"}
        if len(attempt_scopes) == 1:
            return attempt_scopes.pop(), "exact pending transport-request lane associated with the exception", []
    return "unknown", "scope unavailable from retained evidence", []


def _group_operational_incidents(
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]],
    operational: List[Dict[str, Any]],
    raw_pipeline_evidence: Dict[int, Tuple[Tuple[str, str], str]],
    ambiguity_times: List[datetime],
    stable_root_categories: set[str],
    pipeline_failures_by_identity: Dict[Tuple[str, str], List[Dict[str, Any]]],
    *,
    classify_operational_error: Callable[[str], str],
    get_event_time: Callable[[Dict[str, Any]], Optional[datetime]],
    seconds_between: Callable[[datetime, datetime], float],
    is_subordinate_remote_write_symptom: Callable[..., bool],
    incident_exception_line: Callable[[str], str],
    matching_ambiguity_identity: Callable[..., Optional[Dict[str, Any]]],
    pause_scope_for_item: Callable[[Dict[str, Any]], Tuple[str, str, List[str]]],
    normalise_incident_text: Callable[[str], str],
) -> Dict[Tuple[str, str], Tuple[str, str]]:
    """Mutate incident groups and shared error rows, then seed pipeline-only groups."""
    for item in operational:
        raw = str(item.get("_raw_message") or item.get("message") or "")
        pipeline_evidence = raw_pipeline_evidence.get(id(item))
        evidence_identity = pipeline_evidence[0] if pipeline_evidence else None
        category = (
            "reply_strategy_pipeline_failure"
            if evidence_identity is not None
            else classify_operational_error(raw)
        )
        item_time = get_event_time(item)
        if (
            category == "x_api_transient_failure"
            and item_time is not None
            and any(seconds_between(item_time, other) <= 10 for other in ambiguity_times)
        ):
            category = "remote_write_ambiguity_barrier"
        elif is_subordinate_remote_write_symptom(
            category=category,
            raw=raw,
            item_time=item_time,
        ):
            item["_remote_write_subordinate_category"] = category
            if category == "conversational_reply_receipt_barrier":
                identity = re.search(
                    r"\blane=([^\s]+) target_id=([^\s]+)",
                    raw,
                )
                if identity is not None:
                    item["_remote_write_subordinate_reply_identity"] = {
                        "lane": _normalise_lane(identity.group(1)),
                        "target_id": identity.group(2),
                        "source_time": str(item.get("time") or ""),
                    }
            category = "remote_write_ambiguity_barrier"
        root = (incident_exception_line(raw) or raw.splitlines()[0]) if raw else category
        remote_identity: Optional[Dict[str, Any]] = None
        if category == "remote_write_ambiguity_barrier":
            remote_identity = matching_ambiguity_identity(raw, item_time)
        elif category == "conversational_reply_receipt_barrier":
            remote_identity = matching_ambiguity_identity(raw, item_time)
        if remote_identity is not None:
            item["_remote_write_identity"] = {
                "transaction_id": str(
                    remote_identity.get("transaction_id") or ""
                ),
                "lane": str(remote_identity.get("lane") or ""),
                "target_id": str(remote_identity.get("target_id") or ""),
                "image": str(remote_identity.get("image") or ""),
            }
            if remote_identity.get("transaction_id"):
                signature = "transaction:" + str(
                    remote_identity["transaction_id"]
                )
            elif remote_identity.get("target_id"):
                signature = (
                    "reply:"
                    + str(remote_identity.get("lane") or "unavailable")
                    + ":"
                    + str(remote_identity["target_id"])
                )
                if category == "conversational_reply_receipt_barrier":
                    signature += ":" + dt_text(item_time)
            else:
                signature = (
                    "media:"
                    + str(remote_identity.get("image") or "unavailable")
                    + ":"
                    + dt_text(remote_identity.get("time"))
                )
        elif category == "remote_write_ambiguity_barrier" and item_time is not None:
            signature = f"{category}:{dt_text(item_time)}"
            for (candidate_category, candidate_signature), rows in reversed(
                list(groups.items())
            ):
                previous_time = get_event_time(rows[-1])
                if (
                    candidate_category == category
                    and previous_time is not None
                    and seconds_between(item_time, previous_time) <= 10
                    and not rows[-1].get("_remote_write_identity")
                ):
                    signature = candidate_signature
                    break
        elif category == "xai_provider_timeout" and item_time is not None:
            signature = ""
            for (candidate_category, candidate_signature), rows in reversed(
                list(groups.items())
            ):
                previous_time = get_event_time(rows[-1])
                if (
                    candidate_category == category
                    and previous_time is not None
                    and seconds_between(item_time, previous_time) <= 5
                ):
                    signature = candidate_signature
                    break
            if not signature:
                signature = f"{category}:{dt_text(item_time)}"
        elif evidence_identity is not None:
            signature = f"{evidence_identity[0]}:{evidence_identity[1]}"
        elif category == "remote_operations_paused":
            pause_scope, pause_evidence, pause_keys = pause_scope_for_item(item)
            item["_pause_scope"] = pause_scope
            item["_pause_scope_evidence"] = pause_evidence
            item["_pause_control_keys"] = pause_keys
            signature = f"{category}:{pause_scope}"
        elif category == "local_media_preflight_failure":
            lane = str(item.get("_media_preflight_lane") or _media_preflight_lane(raw))
            image = _media_preflight_image(raw) or str(item.get("_media_preflight_image") or "")
            reason = normalise_incident_text(_local_media_preflight_reason(raw) or root)
            item["_media_preflight_lane"] = lane
            item["_media_preflight_image"] = image
            signature = f"{category}:{lane}:{image}:{reason}"
        else:
            signature = (
                category
                if category in stable_root_categories
                else normalise_incident_text(root)
            )
        groups.setdefault((category, signature), []).append(item)

    pipeline_identity_by_group: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for identity in pipeline_failures_by_identity:
        group_key = (
            "reply_strategy_pipeline_failure",
            f"{identity[0]}:{identity[1]}",
        )
        groups.setdefault(group_key, [])
        pipeline_identity_by_group[group_key] = identity
    return pipeline_identity_by_group


def _snapshot_identity_tokens(identity: Mapping[str, Any]) -> set[str]:
    """Combine explicit and document tokens with source-qualified retirement hashes."""
    tokens = set(identity.get("snapshot_identity_tokens") or [])
    tokens.update(
        "document_sha256:" + str(value)
        for value in identity.get("document_sha256s") or []
    )
    tokens.update(
        "retirement_expected_sha256:" + str(source) + ":" + str(expected)
        for source in identity.get("retirement_source_basenames") or []
        for expected in identity.get("retirement_expected_sha256s") or []
    )
    return tokens


def summarise_operational_error_health(
    errors: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    receipt_events: List[Dict[str, Any]],
    lifecycle: Iterable[Dict[str, Any]] = (),
    remote_write_transactions: Iterable[Dict[str, Any]] = (),
    handled_api_restrictions: Iterable[Dict[str, Any]] = (),
    confirmed_reply_receipt_events: Iterable[Dict[str, Any]] = (),
    current_remote_write_safety: Optional[Dict[str, Any]] = None,
    generation_time: Optional[datetime] = None,
    selected_window_end: Optional[datetime] = None,
    current_snapshot_authoritative: bool = False,
    *,
    bounded_source_refs: Callable[..., Tuple[List[Dict[str, Any]], int]],
    seconds_between: Callable[[datetime, datetime], float],
    annotate_remote_write_snapshot_window: Callable[..., None],
    classify_operational_error: Callable[[str], str],
    incident_exception_line: Callable[[str], str],
    normalise_incident_text: Callable[[str], str],
    get_event_time: Callable[[Dict[str, Any]], Optional[datetime]],
    base_remote_control_key: Callable[[Any], str],
    remote_control_scope: Callable[[Any], str],
    remote_operation_scope_for_lane: Callable[[Any], str],
    explicit_remote_pause_scope: Callable[[Any], Tuple[str, str, List[str]]],
    remote_operation_scope_labels: Mapping[str, str],
    clock_now: Callable[[], datetime],
    fromtimestamp: Callable[[int], datetime],
    datetime_min: datetime,
) -> Dict[str, Any]:
    """Group traceback cascades and distinguish recovered from current incidents."""
    generated_at = generation_time or clock_now()
    lifecycle = list(lifecycle)
    remote_write_transactions = list(remote_write_transactions)
    handled_api_restrictions = list(handled_api_restrictions)
    confirmed_reply_receipt_events = list(confirmed_reply_receipt_events)
    serious = [item for item in errors if item.get("level") in {"ERROR", "CRITICAL"}]
    operational = [
        item
        for item in serious
        if classify_operational_error(
            str(item.get("_raw_message") or item.get("message") or "")
        )
        != "clarification_mode_local_rejection"
    ]
    pipeline_failures_by_identity, raw_pipeline_evidence = (
        _prepare_pipeline_incident_evidence(
            events, operational, get_event_time=get_event_time,
        )
    )

    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    stable_root_categories = {
        "historical_context_source_role_incompatibility",
        "historical_context_reply_failure",
        "legacy_regular_receipt_barrier",
        "conversational_reply_receipt_barrier",
        "remote_operations_paused",
        "process_crash",
        "remote_write_ambiguity_barrier",
        "remote_write_protocol_barrier",
        "remote_write_transaction_barrier",
        "instance_lock_conflict",
        "x_api_transient_failure",
        "x_api_rate_limit",
        "quote_pagination_protocol_anomaly",
        "xai_provider_timeout",
    }
    (
        ambiguity_times, transport_attempts,
        ambiguous_reply_outcomes, ambiguous_media_outcomes,
    ) = _prepare_remote_ambiguity_evidence(
        serious, events, remote_write_transactions,
        classify_operational_error=classify_operational_error,
        get_event_time=get_event_time, seconds_between=seconds_between,
    )

    def matching_ambiguity_identity(
        raw: str,
        item_time: Optional[datetime],
    ) -> Optional[Dict[str, Any]]:
        """Return the strongest uniquely associated ambiguity identity."""
        return _matching_ambiguity_identity(
            raw, item_time,
            ambiguous_reply_outcomes=ambiguous_reply_outcomes,
            transport_attempts=transport_attempts,
            ambiguous_media_outcomes=ambiguous_media_outcomes,
            seconds_between=seconds_between,
        )

    def is_subordinate_remote_write_symptom(
        *,
        category: str,
        raw: str,
        item_time: Optional[datetime],
    ) -> bool:
        """Bind exact receipt/lane symptoms to a logged reply ambiguity root."""
        return _is_subordinate_remote_write_symptom(
            category=category, raw=raw, item_time=item_time,
            ambiguous_reply_outcomes=ambiguous_reply_outcomes,
            ambiguity_times=ambiguity_times,
            seconds_between=seconds_between,
        )

    def pause_scope_for_item(
        item: Dict[str, Any],
    ) -> Tuple[str, str, List[str]]:
        """Use ordered, transaction-local evidence to scope one pause error."""
        return _pause_scope_for_item(
            item, events=events, transport_attempts=transport_attempts,
            explicit_remote_pause_scope=explicit_remote_pause_scope,
            get_event_time=get_event_time,
            base_remote_control_key=base_remote_control_key,
            remote_control_scope=remote_control_scope,
            remote_operation_scope_for_lane=remote_operation_scope_for_lane,
        )

    pipeline_identity_by_group = _group_operational_incidents(
        groups, operational, raw_pipeline_evidence, ambiguity_times,
        stable_root_categories, pipeline_failures_by_identity,
        classify_operational_error=classify_operational_error,
        get_event_time=get_event_time, seconds_between=seconds_between,
        is_subordinate_remote_write_symptom=is_subordinate_remote_write_symptom,
        incident_exception_line=incident_exception_line,
        matching_ambiguity_identity=matching_ambiguity_identity,
        pause_scope_for_item=pause_scope_for_item,
        normalise_incident_text=normalise_incident_text,
    )

    (
        event_times, receipt_removed_times, successful_restart_times,
        remote_write_success_times, remote_operation_successes,
        terminal_reply_receipts,
    ) = _prepare_recovery_evidence(
        events, receipt_events, lifecycle, confirmed_reply_receipt_events,
        get_event_time=get_event_time,
    )

    safety = current_remote_write_safety or {}
    if current_remote_write_safety is not None:
        annotate_remote_write_snapshot_window(
            safety,
            selected_window_end,
            current_snapshot_authoritative=current_snapshot_authoritative,
        )
    active_remote_components = (
        safety.get("active_transaction_identities")
        if isinstance(safety.get("active_transaction_identities"), list)
        else []
    )
    snapshot_incident_evidence = (
        safety.get("snapshot_incident_evidence")
        if isinstance(safety.get("snapshot_incident_evidence"), list)
        else []
    )
    identity_snapshot_available = bool(
        safety.get("configured") is True
        and safety.get("available") is True
        and safety.get("identity_snapshot_available") is True
    )
    identity_snapshot_explicitly_unavailable = bool(
        current_remote_write_safety is not None
        and not identity_snapshot_available
        and (
            safety.get("configured") is False
            or safety.get("available") is False
        )
    )

    def component_matches_identity(
        component: Dict[str, Any],
        identity: Dict[str, Any],
    ) -> bool:
        transaction_id = str(identity.get("transaction_id") or "")
        target_id = str(identity.get("target_id") or "")
        component_transactions = component.get("transaction_ids") or []
        if transaction_id and component_transactions:
            return transaction_id in component_transactions
        if target_id and target_id in (component.get("target_ids") or []):
            identity_lane = _normalise_lane(identity.get("lane"))
            component_lanes = {
                _normalise_lane(value)
                for value in component.get("lanes") or []
            } - {"unavailable"}
            if (
                identity_lane == "unavailable"
                or not component_lanes
                or identity_lane in component_lanes
            ):
                return True
        return bool(
            _snapshot_identity_tokens(component) & _snapshot_identity_tokens(identity)
        )

    def component_is_related_to_selected_window(
        component: Dict[str, Any],
    ) -> bool:
        return bool(
            current_snapshot_authoritative
            or component.get("selected_window_relationship") in {
            "recorded_at_or_before_selected_window_end",
            "snapshot_observed_at_or_before_selected_window_end",
            }
        )

    def component_is_relevant_to_category(
        component: Dict[str, Any],
        category: str,
    ) -> bool:
        if category == "remote_write_ambiguity_barrier":
            return "ambiguity_marker" in (component.get("artifact_kinds") or [])
        if category != "conversational_reply_receipt_barrier":
            return True
        return "conversational_confirmed_reply" in (
            component.get("receipt_roles") or []
        )

    def active_component_matches(
        category: str,
        identity: Dict[str, Any],
    ) -> bool:
        return any(
            component_is_related_to_selected_window(component)
            and component_is_relevant_to_category(component, category)
            and component_matches_identity(component, identity)
            for component in active_remote_components
        )

    def remote_write_recovery_status(
        category: str,
        identity: Dict[str, Any],
        first_time: datetime,
        last_time: datetime,
    ) -> Tuple[str, str, Optional[datetime]]:
        """Reconcile one identified receipt/ambiguity transaction conservatively."""
        return _remote_write_recovery_status(
            category, identity, first_time, last_time,
            identity_snapshot_available=identity_snapshot_available,
            active_component_matches=active_component_matches,
            active_remote_components=active_remote_components,
            component_is_related_to_selected_window=component_is_related_to_selected_window,
            component_is_relevant_to_category=component_is_relevant_to_category,
            identity_snapshot_explicitly_unavailable=identity_snapshot_explicitly_unavailable,
            safety=safety, terminal_reply_receipts=terminal_reply_receipts,
            handled_api_restrictions=handled_api_restrictions,
            remote_write_success_times=remote_write_success_times,
            fromtimestamp=fromtimestamp, get_event_time=get_event_time,
        )

    def pipeline_recovered_after(
        identity: Tuple[str, str], last_time: datetime
    ) -> Tuple[bool, str, Optional[datetime]]:
        return _pipeline_recovered_after(
            identity, last_time, events=events, get_event_time=get_event_time,
        )

    def remote_pause_recovery_status(
        scope: str,
        control_keys: List[str],
        last_time: datetime,
    ) -> Tuple[str, str, Optional[datetime]]:
        """Require both scope-matched control clearance and later success."""
        return _remote_pause_recovery_status(
            scope, control_keys, last_time,
            safety=safety, events=events, lifecycle=lifecycle,
            remote_operation_successes=remote_operation_successes,
            base_remote_control_key=base_remote_control_key,
            remote_control_scope=remote_control_scope,
            explicit_remote_pause_scope=explicit_remote_pause_scope,
            get_event_time=get_event_time,
        )

    def recovered_after(category: str, last_time: datetime) -> Tuple[bool, str, Optional[datetime]]:
        return _recovered_after(
            category, last_time,
            events=events, event_times=event_times,
            receipt_removed_times=receipt_removed_times,
            successful_restart_times=successful_restart_times,
            safety=safety, get_event_time=get_event_time,
            fromtimestamp=fromtimestamp, clock_now=clock_now,
        )

    incidents: List[Dict[str, Any]] = []
    for (category, signature), rows in groups.items():
        ordered = sorted(
            rows,
            key=lambda item: (str(item.get("time") or ""), str(item.get("where") or "")),
        )
        group_key = (category, signature)
        pipeline_identity = pipeline_identity_by_group.get(group_key)
        pipeline_failure_events = (
            pipeline_failures_by_identity.get(pipeline_identity, [])
            if pipeline_identity is not None
            else []
        )
        evidence_times = [
            ts
            for item in [*ordered, *pipeline_failure_events]
            if (ts := get_event_time(item)) is not None
        ]
        first_time = min(evidence_times) if evidence_times else datetime_min
        last_time = max(evidence_times) if evidence_times else first_time
        remote_identities = [
            identity
            for item in ordered
            if isinstance(identity := item.get("_remote_write_identity"), dict)
        ]
        remote_identity: Dict[str, Any] = {}
        for field in ("transaction_id", "lane", "target_id", "image"):
            values = sorted(
                {
                    str(identity.get(field) or "")
                    for identity in remote_identities
                    if identity.get(field)
                }
            )
            if values:
                remote_identity[field] = values[0]
        pause_scopes = {
            str(item.get("_pause_scope") or "unknown") for item in ordered
        }
        pause_scope = (
            next(iter(pause_scopes))
            if len(pause_scopes) == 1
            else "unknown"
        )
        pause_scope_evidence = sorted(
            {
                str(item.get("_pause_scope_evidence") or "")
                for item in ordered
                if item.get("_pause_scope_evidence")
            }
        )
        pause_control_keys = sorted(
            {
                str(key)
                for item in ordered
                for key in item.get("_pause_control_keys") or []
                if key
            }
        )
        transient_observation = category in {
            "x_api_transient_failure",
            "xai_provider_timeout",
        }
        if pipeline_identity is not None:
            resolved, resolution_reason, resolution_time = pipeline_recovered_after(
                pipeline_identity, last_time
            )
            status = "historical_resolved" if resolved else "current_unresolved"
        elif category == "local_media_preflight_failure":
            lane = str(ordered[0].get("_media_preflight_lane") or "unknown")
            image = str(ordered[0].get("_media_preflight_image") or "")
            success_kind = {
                "daily_meme": "daily_meme_posted",
                "quote_image": "quote_image_posted",
            }.get(lane)
            later_successes = [
                ts for event in events
                if success_kind is not None and image
                and event.get("kind") == success_kind
                and (ts := get_event_time(event)) is not None
                and ts > last_time
                and str(event.get("file") or event.get("image_basename") or event.get("image") or "").rsplit("/", 1)[-1]
                == image.rsplit("/", 1)[-1]
            ]
            resolved = bool(later_successes)
            resolution_time = min(later_successes) if resolved else None
            resolution_reason = (
                f"later {success_kind.replace('_', ' ')} observed after local source-image preflight failure"
                if resolved else ""
            )
            status = "historical_resolved" if resolved else "current_unresolved"
        elif category == "x_api_rate_limit":
            cooldown_deadlines: List[datetime] = []
            for item in ordered:
                raw = str(
                    item.get("_raw_message") or item.get("message") or ""
                )
                match = re.search(
                    r"Entering API cooldown after 429 until "
                    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
                    raw,
                )
                if match:
                    try:
                        cooldown_deadlines.append(parse_dt(match.group(1)))
                    except ValueError:
                        pass
            cooldown_deadline = max(cooldown_deadlines, default=None)
            later_x_successes = [
                ts
                for ts in event_times.get("x_activity_succeeded", [])
                if cooldown_deadline is not None
                and ts > last_time
                and ts > cooldown_deadline
            ]
            resolved = bool(
                cooldown_deadline is not None
                and cooldown_deadline < generated_at
                and later_x_successes
            )
            resolution_time = min(later_x_successes) if resolved else None
            resolution_reason = (
                "cooldown deadline passed and later successful X activity was observed"
                if resolved
                else ""
            )
            status = "historical_resolved" if resolved else "current_unresolved"
        elif transient_observation:
            resolved, resolution_reason, resolution_time = False, "", None
            status = "transient_observation_recovery_unverified"
        elif category == "remote_operations_paused":
            status, resolution_reason, resolution_time = (
                remote_pause_recovery_status(
                    pause_scope,
                    pause_control_keys,
                    last_time,
                )
            )
            resolved = status == "historical_resolved"
        elif category in {
            "remote_write_ambiguity_barrier",
            "conversational_reply_receipt_barrier",
        } and remote_identity:
            status, resolution_reason, resolution_time = remote_write_recovery_status(
                category,
                remote_identity,
                first_time,
                last_time,
            )
            if status == "legacy_fallback":
                resolved, resolution_reason, resolution_time = recovered_after(
                    category, last_time
                )
                status = (
                    "historical_resolved" if resolved else "current_unresolved"
                )
            else:
                resolved = status == "historical_resolved"
        elif (
            category
            in {
                "remote_write_ambiguity_barrier",
                "conversational_reply_receipt_barrier",
            }
            and identity_snapshot_explicitly_unavailable
        ):
            resolved, resolution_time = False, None
            status = "resolution_unavailable"
            resolution_reason = (
                "current status cannot be established from retained evidence "
                "because no usable filesystem snapshot is available"
            )
        else:
            resolved, resolution_reason, resolution_time = recovered_after(category, last_time)
            status = "historical_resolved" if resolved else "current_unresolved"
        if remote_identity and category in {
            "remote_write_ambiguity_barrier",
            "conversational_reply_receipt_barrier",
        }:
            identity_parts = []
            if remote_identity.get("transaction_id"):
                identity_parts.append(
                    f"transaction {remote_identity['transaction_id']}"
                )
            if remote_identity.get("lane"):
                identity_parts.append(f"lane {remote_identity['lane']}")
            if remote_identity.get("target_id"):
                identity_parts.append(f"target {remote_identity['target_id']}")
            if remote_identity.get("image"):
                identity_parts.append(f"media {remote_identity['image']}")
            representative = (
                "Remote-write ambiguity: "
                if category == "remote_write_ambiguity_barrier"
                else "Conversational reply receipt: "
            ) + ", ".join(identity_parts)
        elif category == "remote_operations_paused":
            representative = (
                "Remote operations paused for "
                + remote_operation_scope_labels.get(pause_scope, pause_scope)
            )
        elif category == "local_media_preflight_failure":
            representative = (
                "Local media preflight failed before receipt publication or upload; "
                + str(ordered[0].get("_media_preflight_lane") or "unknown")
                + ": "
                + str(_local_media_preflight_reason(str(ordered[0].get("_raw_message") or ordered[0].get("message") or "")) or "")
            )
        elif pipeline_identity is not None:
            reasons = Counter(
                str(event.get("reason") or "unknown_pipeline_failure")
                for event in pipeline_failure_events
            )
            reason_text = ", ".join(
                f"{reason}={count}" for reason, count in sorted(reasons.items())
            )
            representative = (
                f"AI reply pipeline failure for {pipeline_identity[0]} target "
                f"{pipeline_identity[1]}: {reason_text}"
            )
        else:
            representative = str(
                ordered[0].get("_raw_message")
                or ordered[0].get("message")
                or ""
            ).splitlines()[0]
        incident = {
                "category": category,
                "signature": signature,
                "status": status,
                "first_seen": dt_text(first_time),
                "last_seen": dt_text(last_time),
                "record_count": len(ordered),
                "traceback_count": sum(
                    "Traceback" in str(item.get("_raw_message") or item.get("message") or "")
                    for item in ordered
                ),
                "affected_locations": sorted(
                    {
                        str(item.get("where") or "")
                        for item in ordered
                        if item.get("where")
                    }
                ),
                "summary": short(representative, 300),
                "resolution_reason": resolution_reason,
                "resolution_time": dt_text(resolution_time) if resolution_time else None,
            }
        incident_source_refs, incident_source_ref_omitted = bounded_source_refs(
            *[item.get("source_refs") for item in ordered]
        )
        if incident_source_refs:
            incident["source_refs"] = incident_source_refs
        if incident_source_ref_omitted:
            incident["source_ref_omitted_count"] = (
                incident_source_ref_omitted
            )
        subordinate_symptoms = Counter(
            str(item.get("_remote_write_subordinate_category") or "")
            for item in ordered
            if item.get("_remote_write_subordinate_category")
        )
        subordinate_reply_identities = sorted(
            {
                (
                    str(identity.get("lane") or ""),
                    str(identity.get("target_id") or ""),
                )
                for item in ordered
                if isinstance(
                    identity := item.get(
                        "_remote_write_subordinate_reply_identity"
                    ),
                    dict,
                )
                and identity.get("lane")
                and identity.get("target_id")
            }
        )
        subordinate_reply_events = [
            {
                "lane": str(identity.get("lane") or ""),
                "target_id": str(identity.get("target_id") or ""),
                "source_time": str(identity.get("source_time") or ""),
            }
            for item in ordered
            if isinstance(
                identity := item.get(
                    "_remote_write_subordinate_reply_identity"
                ),
                dict,
            )
            and identity.get("lane")
            and identity.get("target_id")
            and identity.get("source_time")
        ]
        if subordinate_symptoms:
            incident["correlated_subordinate_symptom_counts"] = dict(
                sorted(subordinate_symptoms.items())
            )
        if subordinate_reply_identities:
            incident["correlated_reply_receipt_identities"] = [
                {"lane": lane, "target_id": target_id}
                for lane, target_id in subordinate_reply_identities
            ]
        if subordinate_reply_events:
            incident["correlated_reply_receipt_events"] = (
                subordinate_reply_events
            )
        if category == "remote_operations_paused":
            incident["pause_scope"] = pause_scope
            incident["pause_scope_label"] = remote_operation_scope_labels.get(
                pause_scope,
                pause_scope,
            )
            incident["pause_scope_evidence"] = pause_scope_evidence
            incident["pause_control_keys"] = pause_control_keys
        if remote_identity:
            incident.update(
                {
                    key: value
                    for key, value in remote_identity.items()
                    if value
                }
            )
            matching_components = [
                component
                for component in active_remote_components
                if component_matches_identity(component, remote_identity)
            ]
            if matching_components:
                incident["active_artifact_names"] = sorted(
                    {
                        name
                        for component in matching_components
                        for name in component.get("artifact_names") or []
                    }
                )
        if pipeline_identity is not None:
            incident.update(
                {
                    "lane": pipeline_identity[0],
                    "target_id": pipeline_identity[1],
                    "pipeline_failure_event_count": len(pipeline_failure_events),
                    "wrapper_record_count": sum(
                        raw_pipeline_evidence.get(id(item), (pipeline_identity, ""))[1]
                        == "outer_wrapper"
                        for item in ordered
                    ),
                    "pipeline_failure_reason_counts": dict(reasons.most_common()),
                }
            )
        incidents.append(incident)

    reconcile_current_snapshot_incidents(
        incidents,
        identity_snapshot_available=identity_snapshot_available,
        active_remote_components=active_remote_components,
        snapshot_incident_evidence=snapshot_incident_evidence,
        safety=safety,
        component_is_related_to_selected_window=component_is_related_to_selected_window,
        component_matches_identity=component_matches_identity,
        fromtimestamp=fromtimestamp,
        get_event_time=get_event_time,
        dt_text=dt_text,
        short=short,
    )
    incidents.sort(key=lambda item: (item["first_seen"], item["category"], item["signature"]))
    current = [item for item in incidents if item["status"] == "current_unresolved"]
    resolved = [item for item in incidents if item["status"] == "historical_resolved"]
    resolution_unavailable = [
        item for item in incidents if item["status"] == "resolution_unavailable"
    ]
    transient_provider_observations = [
        item
        for item in incidents
        if item["status"] == "transient_observation_recovery_unverified"
    ]
    transient_provider_timeouts = [
        item
        for item in transient_provider_observations
        if item["category"] == "xai_provider_timeout"
    ]
    return {
        "current_independent_incident_count": len(current),
        "historical_resolved_incident_count": len(resolved),
        "resolution_unavailable_incident_count": len(resolution_unavailable),
        "raw_serious_error_record_count": len(serious),
        "raw_traceback_count": sum(item.get("traceback_count", 0) for item in incidents),
        "transient_provider_timeout_count": len(transient_provider_timeouts),
        "transient_provider_timeout_record_count": sum(
            item.get("record_count", 0) for item in transient_provider_timeouts
        ),
        "transient_provider_observation_count": len(transient_provider_observations),
        "transient_provider_observation_record_count": sum(
            item.get("record_count", 0) for item in transient_provider_observations
        ),
        "current_incidents": current,
        "historical_resolved_incidents": resolved,
        "resolution_unavailable_incidents": resolution_unavailable,
        "transient_provider_observations": transient_provider_observations,
        "selected_window_end": (
            dt_text(selected_window_end) if selected_window_end else None
        ),
        "safety_snapshot_observed_at": safety.get("observed_at"),
        "current_health_snapshot_authoritative": bool(
            current_snapshot_authoritative
        ),
    }
