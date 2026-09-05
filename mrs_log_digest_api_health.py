"""Observe passive X/cooldown health and prepare the existing API report fields.

The coordinator supplies selected observations, production event identities and
current helpers. Preparation retains computed counters and shared error rows;
report materialisation remains separate so intervening callbacks keep their
original effects. No I/O, clock sampling, source selection or publication
authority belongs here.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from mrs_log_digest_records import Record


def is_reply_target_eligibility_restriction(message: str) -> bool:
    """Return whether is reply target eligibility restriction."""
    text = str(message or "").lower()
    return any(
        marker in text
        for marker in (
            "only reply to or quote posts where you are mentioned or are the author",
            "reply to this conversation is not allowed",
            "not been mentioned or otherwise engaged by the author",
            "not allowed to reply",
        )
    )


def is_deleted_or_inaccessible_tweet_403(message: str) -> bool:
    """Return whether a 403 says the target tweet was deleted or inaccessible."""
    text = str(message or "").lower()
    return "403" in text and any(
        marker in text
        for marker in (
            "tweet that is deleted or not visible to you",
            "post that is deleted or not visible to you",
            "tweet is deleted or not visible",
            "post is deleted or not visible",
            "tweet is unavailable",
            "post is unavailable",
        )
    )


def handle_cooldown_message(
    r: Record,
    msg: str,
    *,
    stats: Counter,
    cooldown_active: List[Dict[str, Any]],
    add_event: Callable[..., Dict[str, Any]],
) -> bool:
    """Observe raw cooldown messages; handle only an entered-cooldown event."""
    if ("API cooldown active" in msg or "due to API cooldown" in msg or "Skipping quote-tweet check due to API cooldown" in msg or "Skipping mention check due to API cooldown" in msg):
        stats["cooldown_mentions"] += 1
    m = re.search(r"API cooldown active until ([^:]+:\d{2}:\d{2}): (.+)$", msg)
    if m:
        cooldown_active.append({
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "until": m.group(1).strip(),
            "reason": m.group(2).strip(),
        })
    m = re.search(r"Entering API cooldown after (429|repeated errors) until (.+)$", msg)
    if m:
        add_event("api_cooldown_entered", r.ts, reason=m.group(1), until=m.group(2).strip())
        return True
    return False


def handle_x_api_error(
    r: Record,
    msg: str,
    *,
    latest_x_request_by_source: Dict[str, Dict[str, Any]],
    pending_mention: Dict[str, Any],
    pending_qt: Dict[str, Any],
    is_handled_reply_restriction: bool,
    api_errors: List[Dict[str, Any]],
    handled_api_restrictions: List[Dict[str, Any]],
    stats: Counter,
    input_file_indexes: Optional[Dict[str, int]],
    parse_dt: Callable[[str], Optional[datetime]],
    seconds_between: Callable[[datetime, datetime], float],
    short: Callable[..., str],
    record_source_ref: Callable[..., Dict[str, Any]],
    is_deleted_or_inaccessible_tweet_403: Callable[[str], bool],
) -> bool:
    """Observe an X error, handling only the deleted/inaccessible 403 exit."""
    x_error_match = None
    if r.src in {"x_request", "x_bearer_request"}:
        x_error_match = re.search(r"^X(?: bearer)? API error (\d+):", msg)
    if x_error_match:
        stats["x_api_errors"] += 1
        service = "X bearer" if "X bearer API error" in msg else "X OAuth"
        request_context = latest_x_request_by_source.get(r.src)
        if request_context is not None:
            try:
                request_time = parse_dt(str(request_context.get("time") or ""))
            except ValueError:
                request_time = None
            if request_time is None or seconds_between(request_time, r.ts) > 300:
                request_context = None
        endpoint = (
            str(request_context.get("endpoint") or "unknown")
            if request_context is not None
            else "quote_tweets"
            if service == "X bearer"
            else "unknown_oauth"
        )
        status_code = x_error_match.group(1)
        if status_code == "403" and is_handled_reply_restriction:
            endpoint = "post/reply"
        target_id = str(
            pending_mention.get("mention_id")
            or pending_qt.get("quote_tweet_id")
            or ""
        )
        lane = (
            str(pending_mention.get("source") or "mention")
            if pending_mention
            else ("quote_tweet" if pending_qt else "unavailable")
        )
        api_error = {
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "service": service,
            "endpoint": endpoint,
            "status": status_code,
            "target_id": target_id,
            "lane": lane,
            "message": short(msg, 240),
            "request_method": (
                request_context.get("method") if request_context else ""
            ),
            "request_url": (
                request_context.get("url") if request_context else ""
            ),
            "source_refs": [record_source_ref(r, input_file_indexes)],
        }
        if request_context is not None:
            request_context["status"] = status_code
            request_context["failed"] = True
        if status_code == "403" and is_deleted_or_inaccessible_tweet_403(msg):
            api_error["restriction_kind"] = "deleted_or_inaccessible_tweet"
            endpoint = "post/reply"
            api_error["endpoint"] = endpoint
            handled_api_restrictions.append(api_error)
            return True
        if status_code == "403" and is_handled_reply_restriction:
            api_error["restriction_kind"] = "reply_target_eligibility"
            handled_api_restrictions.append(api_error)
        else:
            api_errors.append(api_error)
        if status_code == "503":
            stats[f"x_api_503_{endpoint.replace('/', '_').replace('-', '_')}"] += 1
        elif status_code == "429":
            stats["x_api_429_rate_limit"] += 1
    return False


def enrich_latest_api_error(
    msg: str,
    *,
    api_errors: List[Dict[str, Any]],
    stats: Counter,
) -> None:
    """Enrich the latest shared API error and count tracebacks at dispatch."""
    if api_errors:
        if msg.startswith("Rate Limit:"):
            api_errors[-1]["rate_limit"] = msg.split(":", 1)[1].strip()
        elif msg.startswith("Remaining:"):
            api_errors[-1]["remaining"] = msg.split(":", 1)[1].strip()
        else:
            m = re.search(r"Recorded (?:quote/)?x API error\. status_code=(\d+) errors_in_window=(\d+/\d+)", msg)
            if m:
                api_errors[-1]["errors_in_window"] = m.group(2)
    if "Traceback" in msg:
        stats["tracebacks"] += 1


@dataclass
class ApiHealthPreparation:
    """Keep existing post-scan results until their report-field evaluation."""

    not_rate_limited: bool
    all_api_failures: List[Dict[str, Any]]
    api_status_counts: Counter[str]
    target_eligibility_403_count: int
    deleted_or_inaccessible_tweet_403_count: int
    posting_attempt_count: int
    tweet_create_request_count: int
    media_upload_request_count: int
    observed_tweet_transport_by_id: Dict[str, Dict[str, Any]]
    observed_tweet_transport_lane_conflict_ids: List[str]
    observed_tweet_transport_lane_counts: Counter[str]
    observed_tweet_transport_main_post_request_count: int
    observed_tweet_transport_reply_request_count: int
    observed_tweet_transport_unclassified_request_count: int
    observed_media_upload_request_count: int
    observed_media_upload_successes: set[Tuple[str, str]]
    observed_success_post_ids: set[str]
    api_counter_semantics: Dict[str, Dict[str, Any]]
    transient_failure_count: int
    rate_limit_failure_count: int
    legacy_cooldown_from_target_restriction_count: int
    unique_api_incidents: set[Tuple[str, str, str, str]]
    post_cooldown_errors: List[Dict[str, Any]]


def prepare_api_health(
    *,
    api_errors: List[Dict[str, Any]],
    handled_api_restrictions: List[Dict[str, Any]],
    x_requests: List[Dict[str, Any]],
    remote_write_transactions: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    production_event_object_ids: set[int],
    quote_post_correlations: Dict[str, Dict[str, Dict[str, Any]]],
    structured_reply_confirmations: List[Dict[str, Any]],
    historical_reply_text_evidence: List[Dict[str, Any]],
    transient_provider_timeouts: int,
    handled_restriction_times: List[datetime],
    event_counter: Callable[..., Counter],
    bounded_event_text: Callable[..., str],
    SHA256_LOWER_RE: re.Pattern[str],
    valid_string_public_post_id: Callable[[Any], bool],
    _normalised_structured_reply_confirmation: Callable[..., Optional[Dict[str, Any]]],
    seconds_between: Callable[[datetime, datetime], float],
    parse_dt: Callable[[str], Optional[datetime]],
    strptime: Callable[[str, str], datetime],
    datetime_min: datetime,
) -> ApiHealthPreparation:
    """Prepare API counters, semantics and cooldown failures in scan order."""
    not_rate_limited = any(
        str(item.get("remaining", "")).isdigit()
        and int(str(item.get("remaining"))) > 0
        and str(item.get("status")) != "429"
        for item in api_errors
    )
    all_api_failures = [*api_errors, *handled_api_restrictions]
    api_status_counts = event_counter(str(item.get("status") or "unavailable") for item in all_api_failures)
    target_eligibility_403_count = sum(
        str(item.get("status")) == "403"
        and item.get("restriction_kind") == "reply_target_eligibility"
        for item in all_api_failures
    )
    deleted_or_inaccessible_tweet_403_count = sum(
        str(item.get("status")) == "403"
        and item.get("restriction_kind") == "deleted_or_inaccessible_tweet"
        for item in all_api_failures
    )
    posting_attempt_count = sum(
        item.get("endpoint") in {"post/reply", "tweet/create"}
        for item in all_api_failures
    )
    tweet_create_request_count = sum(
        item.get("endpoint") == "tweet/create" for item in x_requests
    )
    media_upload_request_count = sum(
        item.get("endpoint") == "media/upload" for item in x_requests
    )
    observed_tweet_transport_by_id: Dict[str, Dict[str, Any]] = {}
    observed_tweet_transport_lanes_by_id: Dict[str, set[str]] = {}
    for item in remote_write_transactions:
        if (
            item.get("kind") == "tweet_transport"
            and item.get("phase") == "request_started"
            and re.fullmatch(
                r"[0-9a-f]{64}", str(item.get("transaction_id") or "")
            )
        ):
            transaction_id = str(item["transaction_id"])
            observed_tweet_transport_by_id.setdefault(transaction_id, item)
            lane = bounded_event_text(
                item.get("lane"), default="unavailable", max_characters=100
            )
            observed_tweet_transport_lanes_by_id.setdefault(
                transaction_id, set()
            ).add(lane or "unavailable")
    observed_tweet_transport_lane_conflict_ids = sorted(
        transaction_id
        for transaction_id, lanes in observed_tweet_transport_lanes_by_id.items()
        if len(lanes) > 1
    )
    observed_tweet_transport_lane_counts = event_counter(
        (
            next(iter(lanes))
            if len(lanes) == 1
            else "conflicted"
        )
        for lanes in observed_tweet_transport_lanes_by_id.values()
    )
    observed_tweet_transport_main_post_lanes = (
        "quote_image",
        "daily_meme",
    )
    observed_tweet_transport_reply_lanes = (
        "conversational_reply",
        "historical_context_reply",
        "mention",
        "hot_post_reply",
        "quote_tweet",
    )
    observed_tweet_transport_main_post_request_count = sum(
        observed_tweet_transport_lane_counts[lane]
        for lane in observed_tweet_transport_main_post_lanes
    )
    observed_tweet_transport_reply_request_count = sum(
        observed_tweet_transport_lane_counts[lane]
        for lane in observed_tweet_transport_reply_lanes
    )
    observed_tweet_transport_unclassified_request_count = (
        len(observed_tweet_transport_by_id)
        - observed_tweet_transport_main_post_request_count
        - observed_tweet_transport_reply_request_count
    )
    observed_media_upload_request_count = sum(
        item.get("kind") == "media_upload"
        and item.get("phase") == "request_started"
        for item in remote_write_transactions
    )
    observed_media_upload_successes = {
        (str(item.get("attempt_id")), str(item.get("media_id")))
        for item in remote_write_transactions
        if item.get("kind") == "media_upload"
        and item.get("phase") == "confirmed_handoff"
        and SHA256_LOWER_RE.fullmatch(str(item.get("attempt_id") or ""))
        and valid_string_public_post_id(item.get("media_id"))
    }
    observed_success_post_ids = {
        str(event.get("post_id") or event.get("reply_post_id") or "")
        for event in events
        if id(event) in production_event_object_ids
        and event.get("kind")
        in {
            "remote_write_succeeded",
            "quote_image_posted",
            "daily_meme_posted",
            "mention_reply_posted",
            "hot_post_reply_posted",
            "quote_tweet_reply_posted",
        }
        and valid_string_public_post_id(
            event.get("post_id") or event.get("reply_post_id")
        )
    }
    for declared_post_id, slots in quote_post_correlations.items():
        if not valid_string_public_post_id(declared_post_id):
            continue
        main_confirmation = slots.get("main_post_posted") or {}
        if (
            main_confirmation.get("post_id") == declared_post_id
            and type(main_confirmation.get("lane")) is str
            and main_confirmation.get("lane") in ("quote_image", "daily_meme")
        ):
            observed_success_post_ids.add(declared_post_id)
        root_confirmation = slots.get("account_root_posted") or {}
        if (
            type(root_confirmation.get("event_version")) is int
            and root_confirmation.get("event_version") == 1
            and root_confirmation.get("post_id") == declared_post_id
            and root_confirmation.get("root_post_id") == declared_post_id
            and root_confirmation.get("conversation_id") == declared_post_id
            and type(root_confirmation.get("lane")) is str
            and root_confirmation.get("lane")
            in ("quote_image", "daily_meme")
            and root_confirmation.get("publication_authority")
            == "confirmed_transport"
        ):
            observed_success_post_ids.add(declared_post_id)
    observed_success_post_ids.update(
        str(confirmation.get("reply_post_id") or "")
        for item in structured_reply_confirmations
        for confirmation in [_normalised_structured_reply_confirmation(item)]
        if confirmation is not None
    )
    observed_success_post_ids.update(
        item["reply_post_id"]
        for item in historical_reply_text_evidence
        if item.get("durable_only") is not True
        and item.get("authoritative") is True
        and valid_string_public_post_id(item.get("reply_post_id"))
        and isinstance(item.get("reply_text"), str)
    )
    api_counter_semantics = {
        "posting_attempt_count": {
            "retained_compatibility_field": True,
            "scope": (
                "failed or handled-restriction X post/reply API records only; "
                "not all posting attempts"
            ),
            "preferred_field": "observed_tweet_transport_request_count",
        },
        "tweet_create_request_count": {
            "retained_compatibility_field": True,
            "scope": (
                "X request-start message-pattern records parsed as POST /2/tweets; "
                "parsed regardless of log level and not complete when low-level "
                "transport messages are absent"
            ),
            "preferred_field": "observed_tweet_transport_request_count",
        },
        "media_upload_request_count": {
            "retained_compatibility_field": True,
            "scope": (
                "X request-start message-pattern records whose parsed URL path is "
                "/2/media/upload; the legacy classifier does not constrain the "
                "method; parsed regardless of log level, and incomplete when "
                "low-level transport messages are absent"
            ),
            "preferred_field": "observed_media_upload_request_count",
        },
        "observed_tweet_transport_request_count": {
            "scope": (
                "selected production log records only: unique durable tweet-transport "
                "start message patterns immediately before the request callback; "
                "self-test logs are excluded; evidence of an observed pre-request "
                "boundary, not proof that X received the request; parsed regardless "
                "of log level and incomplete when confirmation logs are absent"
            ),
            "deduplication": "unique durable transaction_id",
        },
        "observed_tweet_transport_request_counts_by_lane": {
            "scope": (
                "the same unique durable tweet-transport starts, grouped by the "
                "literal bounded lane recorded at that boundary"
            ),
            "deduplication": "unique durable transaction_id before grouping",
        },
        "observed_tweet_transport_main_post_request_count": {
            "scope": (
                "subset of observed_tweet_transport_request_count whose lane is "
                "quote_image or daily_meme"
            ),
            "classification": list(observed_tweet_transport_main_post_lanes),
        },
        "observed_tweet_transport_reply_request_count": {
            "scope": (
                "subset of observed_tweet_transport_request_count whose lane is "
                "conversational_reply, historical_context_reply, mention, "
                "hot_post_reply or quote_tweet"
            ),
            "classification": list(observed_tweet_transport_reply_lanes),
        },
        "observed_tweet_transport_unclassified_request_count": {
            "scope": (
                "subset of observed_tweet_transport_request_count whose literal "
                "lane is not in either documented main-post or reply lane set, "
                "including transaction identities observed with conflicting lanes"
            ),
            "relationship": (
                "main-post plus reply plus unclassified equals the observed "
                "tweet-transport request total"
            ),
        },
        "observed_tweet_transport_lane_conflict_count": {
            "scope": (
                "unique durable transaction_id values whose selected production "
                "request-start records disagree on the literal lane"
            ),
            "classification": (
                "conflicts remain in the request total but are classified as "
                "unclassified rather than choosing a log-order-dependent lane"
            ),
        },
        "observed_media_upload_request_count": {
            "scope": (
                "selected production log records only: receipt-bound media-upload "
                "start message patterns before X API v2 transport; self-test logs "
                "are excluded; parsed regardless of log level and incomplete when "
                "transport logs are absent"
            ),
            "deduplication": "retained physical log-record identity",
        },
        "observed_media_upload_success_count": {
            "scope": (
                "selected production log records only: durable confirmed media "
                "handoffs to a bound main-post attempt; self-test logs are excluded; "
                "not a request-start count or proof of final post publication"
            ),
            "deduplication": "unique validated attempt_id and media_id pair",
        },
        "observed_remote_write_success_count": {
            "scope": (
                "selected production log records only: unique immutable post IDs in "
                "validated confirmed remote-write evidence; self-test records, "
                "durable-only historical history and current durable receipt/state "
                "snapshots are excluded; incomplete when confirmation logs are absent"
            ),
            "deduplication": "unique post or reply post ID across lane and generic confirmations",
        },
    }
    transient_failure_count = sum(
        str(item.get("status") or "") in {"408", "425"}
        or str(item.get("status") or "").startswith("5")
        for item in all_api_failures
    ) + transient_provider_timeouts
    rate_limit_failure_count = sum(str(item.get("status") or "") == "429" for item in all_api_failures)
    legacy_cooldown_from_target_restriction_count = sum(
        any(
            seconds_between(
                parse_dt(str(event.get("time") or "")) or datetime_min,
                restriction_time,
            ) <= 5
            for restriction_time in handled_restriction_times
        )
        for event in events
        if event.get("kind") == "api_cooldown_entered" and event.get("reason") == "repeated errors"
    )
    unique_api_incidents = {
        (
            str(item.get("service") or ""),
            str(item.get("status") or ""),
            str(item.get("target_id") or item.get("endpoint") or ""),
            "" if item.get("target_id") else str(item.get("message") or ""),
        )
        for item in all_api_failures
    }
    post_cooldown_errors: List[Dict[str, Any]] = []
    cooldown_events = [ev for ev in events if ev.get("kind") == "api_cooldown_entered"]
    for item in api_errors:
        try:
            item_ts = strptime(item["time"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, TypeError, ValueError):
            continue
        for ev in cooldown_events:
            until = parse_dt(str(ev.get("until", "")))
            if until and item_ts > until:
                post_cooldown_errors.append(item)
                break
    return ApiHealthPreparation(
        not_rate_limited=not_rate_limited,
        all_api_failures=all_api_failures,
        api_status_counts=api_status_counts,
        target_eligibility_403_count=target_eligibility_403_count,
        deleted_or_inaccessible_tweet_403_count=deleted_or_inaccessible_tweet_403_count,
        posting_attempt_count=posting_attempt_count,
        tweet_create_request_count=tweet_create_request_count,
        media_upload_request_count=media_upload_request_count,
        observed_tweet_transport_by_id=observed_tweet_transport_by_id,
        observed_tweet_transport_lane_conflict_ids=observed_tweet_transport_lane_conflict_ids,
        observed_tweet_transport_lane_counts=observed_tweet_transport_lane_counts,
        observed_tweet_transport_main_post_request_count=observed_tweet_transport_main_post_request_count,
        observed_tweet_transport_reply_request_count=observed_tweet_transport_reply_request_count,
        observed_tweet_transport_unclassified_request_count=observed_tweet_transport_unclassified_request_count,
        observed_media_upload_request_count=observed_media_upload_request_count,
        observed_media_upload_successes=observed_media_upload_successes,
        observed_success_post_ids=observed_success_post_ids,
        api_counter_semantics=api_counter_semantics,
        transient_failure_count=transient_failure_count,
        rate_limit_failure_count=rate_limit_failure_count,
        legacy_cooldown_from_target_restriction_count=legacy_cooldown_from_target_restriction_count,
        unique_api_incidents=unique_api_incidents,
        post_cooldown_errors=post_cooldown_errors,
    )


def api_health_report(
    prepared: ApiHealthPreparation,
    *,
    api_errors: List[Dict[str, Any]],
    handled_api_restrictions: List[Dict[str, Any]],
    cooldown_active: List[Dict[str, Any]],
    x_requests: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Materialise API fields at the report literal, preserving shared lists."""
    return {
        "errors": api_errors,
        "handled_restrictions": handled_api_restrictions,
        "cooldown_active": cooldown_active,
        "post_cooldown_errors": prepared.post_cooldown_errors,
        "not_rate_limited": prepared.not_rate_limited,
        "has_5xx_failures": any(str(item.get("status") or "").startswith("5") for item in prepared.all_api_failures),
        "status_counts": dict(sorted(prepared.api_status_counts.items())),
        "unique_incident_count": len(prepared.unique_api_incidents),
        "posting_attempt_count": prepared.posting_attempt_count,
        "tweet_create_request_count": prepared.tweet_create_request_count,
        "media_upload_request_count": prepared.media_upload_request_count,
        "counter_semantics": prepared.api_counter_semantics,
        "observed_tweet_transport_request_count": len(
            prepared.observed_tweet_transport_by_id
        ),
        "observed_tweet_transport_request_counts_by_lane": dict(
            sorted(prepared.observed_tweet_transport_lane_counts.items())
        ),
        "observed_tweet_transport_main_post_request_count": (
            prepared.observed_tweet_transport_main_post_request_count
        ),
        "observed_tweet_transport_reply_request_count": (
            prepared.observed_tweet_transport_reply_request_count
        ),
        "observed_tweet_transport_unclassified_request_count": (
            prepared.observed_tweet_transport_unclassified_request_count
        ),
        "observed_tweet_transport_lane_conflict_count": len(
            prepared.observed_tweet_transport_lane_conflict_ids
        ),
        "observed_tweet_transport_lane_conflict_transaction_ids": (
            prepared.observed_tweet_transport_lane_conflict_ids[:100]
        ),
        "observed_tweet_transport_lane_conflict_transaction_id_omitted_count": max(
            0,
            len(prepared.observed_tweet_transport_lane_conflict_ids) - 100,
        ),
        "observed_media_upload_request_count": (
            prepared.observed_media_upload_request_count
        ),
        "observed_media_upload_success_count": len(
            prepared.observed_media_upload_successes
        ),
        "observed_remote_write_success_count": len(
            prepared.observed_success_post_ids
        ),
        "x_requests": x_requests,
        "target_eligibility_403_count": prepared.target_eligibility_403_count,
        "deleted_or_inaccessible_tweet_403_count": (
            prepared.deleted_or_inaccessible_tweet_403_count
        ),
        "transient_failure_count": prepared.transient_failure_count,
        "rate_limit_failure_count": prepared.rate_limit_failure_count,
        "legacy_cooldown_from_target_restriction_count": prepared.legacy_cooldown_from_target_restriction_count,
    }
