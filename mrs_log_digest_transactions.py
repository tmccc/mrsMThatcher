"""Prepare passive X request, transaction, media and receipt log observations.

The digest selects records and sources, invokes parsers, switches pending state
and retains event insertion and publication authority. Current formatting,
source, correlation and event callbacks are supplied explicitly. Legacy handlers
return whether the coordinator must continue at the original dispatch boundary;
confirmed-receipt preparation returns the exact replacement pending dictionary.
This module performs no I/O, clock sampling or runtime initialisation.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from mrs_log_digest_records import Record


def parse_x_request_start(
    message: str,
    *,
    classify_x_request_endpoint: Callable[[str, str], str],
) -> Optional[Dict[str, str]]:
    """Parse the request identity logged immediately before X transport."""

    match = re.fullmatch(r"X(?: bearer)? request: ([A-Z]+) (\S+)", str(message))
    if not match:
        return None
    method, url = match.groups()
    return {
        "method": method,
        "url": url,
        "endpoint": classify_x_request_endpoint(method, url),
    }


def classify_x_request_endpoint(method: str, url: str) -> str:
    """Map one logged X request to its exact operational endpoint class."""

    method = str(method or "").upper()
    try:
        path = urlsplit(str(url or "")).path
    except ValueError:
        path = ""
    if path == "/2/media/upload":
        return "media/upload"
    if path == "/2/tweets" and method == "POST":
        return "tweet/create"
    if re.fullmatch(r"/2/users/[^/]+/mentions", path):
        return "mentions"
    if path == "/2/tweets/search/recent":
        return "recent/search"
    if re.fullmatch(r"/2/tweets/[^/]+/quote_tweets", path):
        return "quote_tweets"
    if re.fullmatch(r"/2/tweets/[^/]+", path):
        return "tweet/lookup"
    if path:
        return path.lstrip("/") or "root"
    return "unknown"


def parse_remote_write_transaction_event(
    record: Record,
    *,
    short: Callable[[Any, int], str],
) -> Optional[Dict[str, Any]]:
    """Parse current receipt/media/transport lifecycle logs into one vocabulary."""

    message = str(record.msg or "")
    base: Dict[str, Any] = {
        "time": record.ts.strftime("%Y-%m-%d %H:%M:%S"),
        "level": record.level,
        "where": f"{record.src}:{record.line}",
        "message": short(message, 500),
    }
    match = re.search(r"Uploading receipt-bound media via X API v2: (.+)$", message)
    if match:
        return {
            **base,
            "kind": "media_upload",
            "phase": "request_started",
            "image": Path(match.group(1).strip()).name,
        }
    match = re.search(
        r"X media upload outcome is ambiguous; .* image=([^\s]+)",
        message,
    )
    if match:
        return {
            **base,
            "kind": "media_upload",
            "phase": "ambiguous",
            "image": Path(match.group(1)).name,
        }
    match = re.search(
        r"Creating X post with durable transport journal\. lane=([^\s]+) "
        r"transaction_id=([0-9a-f]{64}) reply_to_id=([^\s]*) "
        r"media_count=(\d+) made_with_ai=(\S+)",
        message,
    )
    if match:
        return {
            **base,
            "kind": "tweet_transport",
            "phase": "request_started",
            "lane": match.group(1),
            "transaction_id": match.group(2),
            "reply_to_id": match.group(3),
            "media_count": int(match.group(4)),
            "made_with_ai": match.group(5),
        }
    patterns = (
        (
            r"Wrote main-post sending receipt lane=([^\s]+) attempt_id=([^\s]+) path=(.+)$",
            "main_post_receipt",
            "sending_published",
        ),
        (
            r"Promoted main-post receipt to attempting lane=([^\s]+) attempt_id=([^\s]+) path=(.+)$",
            "main_post_receipt",
            "attempting",
        ),
        (
            r"Handed confirmed media upload to durable main-post attempt lane=([^\s]+) attempt_id=([^\s]+) media_id=([^\s]+)$",
            "media_upload",
            "confirmed_handoff",
        ),
        (
            r"Promoted main-post attempt to confirmed pending-schedule receipt lane=([^\s]+) attempt_id=([^\s]+) post_id=([^\s]+) path=(.+)$",
            "main_post_receipt",
            "confirmed_pending_schedule",
        ),
    )
    for expression, kind, phase in patterns:
        match = re.search(expression, message)
        if not match:
            continue
        result = {
            **base,
            "kind": kind,
            "phase": phase,
            "lane": match.group(1),
            "attempt_id": match.group(2),
        }
        if phase == "confirmed_handoff":
            result["media_id"] = match.group(3)
        elif phase == "confirmed_pending_schedule":
            result["post_id"] = match.group(3)
            result["path"] = match.group(4)
        else:
            result["path"] = match.group(3)
        return result
    match = re.search(
        r"Removed main-post sending receipt disposition=([^\s]+) "
        r"lane=([^\s]+) attempt_id=([^\s]+) path=(.+)$",
        message,
    )
    if match:
        return {
            **base,
            "kind": "main_post_receipt",
            "phase": "sending_retired",
            "disposition": match.group(1),
            "lane": match.group(2),
            "attempt_id": match.group(3),
            "path": match.group(4),
        }
    match = re.search(
        r"Finalised (?:confirmed )?pending-schedule receipt "
        r"lane=([^\s]+) post_id=([^\s]+) path=(.+)$",
        message,
    )
    if match:
        return {
            **base,
            "kind": "main_post_receipt",
            "phase": "schedule_finalised",
            "lane": match.group(1),
            "post_id": match.group(2),
            "path": match.group(3),
        }
    match = re.search(
        r"Resumed interrupted exact source-receipt retirement path=([^\s]+) "
        r"phase=([^\s]+)",
        message,
    )
    if match:
        return {
            **base,
            "kind": "source_receipt_retirement",
            "phase": match.group(2),
            "path": match.group(1),
        }
    match = re.search(
        r"Resumed interrupted confirmed-media fence retirement lane=([^\s]+) "
        r"media_transaction_id=([^\s]+) media_id=([^;\s]+)",
        message,
    )
    if match:
        return {
            **base,
            "kind": "media_retirement",
            "phase": "resumed",
            "lane": match.group(1),
            "transaction_id": match.group(2),
            "media_id": match.group(3),
        }
    if "Recovered crash-left permanent retirement-ledger exchanges" in message:
        return {
            **base,
            "kind": "retirement_ledger",
            "phase": "exchange_recovered",
        }
    return None


def summarise_main_post_receipt_lifecycle(
    receipt_events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Associate current and retained main-post receipt lifecycle events."""

    pending: Dict[str, List[Dict[str, Any]]] = {
        "quote_image": [],
        "daily_meme": [],
    }
    unresolved: List[Dict[str, Any]] = []
    completed_by_lane: Counter = Counter()
    boundary_by_lane: Counter = Counter()

    def normal_lane(item: Dict[str, Any]) -> str:
        lane = str(item.get("lane") or "")
        kind = str(item.get("kind") or "")
        if lane in pending:
            return lane
        if kind.startswith("regular_"):
            return "quote_image"
        if kind.startswith("meme_"):
            return "daily_meme"
        return lane

    def matching_index(lane: str, item: Dict[str, Any]) -> int | None:
        candidates = pending.get(lane, [])
        attempt_id = str(item.get("attempt_id") or "")
        path = str(item.get("path") or "")
        for index, candidate in enumerate(candidates):
            if attempt_id and candidate.get("attempt_id") == attempt_id:
                return index
            if path and candidate.get("path") == path:
                return index
        return 0 if candidates else None

    def observe_pending(
        lane: str,
        item: Dict[str, Any],
        *,
        opening_write_observed: bool,
    ) -> None:
        if lane not in pending:
            unresolved.append(item)
            return
        index = matching_index(lane, item)
        if index is None:
            pending[lane].append(
                {**item, "opening_write_observed": opening_write_observed}
            )
            return
        existing = pending[lane][index]
        pending[lane][index] = {
            **existing,
            **item,
            "opening_write_observed": bool(
                existing.get("opening_write_observed")
                or opening_write_observed
            ),
        }

    def terminal_removal(lane: str) -> None:
        candidates = pending.get(lane, [])
        if candidates:
            lifecycle = candidates.pop(0)
            if lifecycle.get("opening_write_observed"):
                completed_by_lane[lane] += 1
            else:
                boundary_by_lane[lane] += 1
        elif lane in pending:
            boundary_by_lane[lane] += 1

    for item in receipt_events:
        kind = str(item.get("kind") or "")
        phase = str(item.get("phase") or "")
        lane = normal_lane(item)
        if kind in {"regular_written", "meme_written"}:
            observe_pending(lane, item, opening_write_observed=True)
        elif kind == "main_post_receipt":
            if phase == "sending_published":
                observe_pending(lane, item, opening_write_observed=True)
            elif phase in {
                "attempting",
                "confirmed_pending_schedule",
                "schedule_finalised",
            }:
                observe_pending(lane, item, opening_write_observed=False)
            elif phase == "sending_retired":
                index = matching_index(lane, item)
                if index is not None and lane in pending:
                    pending[lane].pop(index)
            else:
                unresolved.append(item)
        elif kind in {"regular_reconciled", "meme_reconciled"}:
            observe_pending(lane, item, opening_write_observed=False)
        elif kind in {"regular_removed", "meme_removed"}:
            terminal_removal(lane)
        elif kind in {
            "regular_replay_suppressed_second_post",
            "meme_replay_suppressed_second_post",
        }:
            continue
        else:
            unresolved.append(item)

    for lane in ("quote_image", "daily_meme"):
        unresolved.extend(pending[lane])
    return {
        "completed_count": sum(completed_by_lane.values()),
        "regular_completed_count": completed_by_lane["quote_image"],
        "meme_completed_count": completed_by_lane["daily_meme"],
        "boundary_removal_count": sum(boundary_by_lane.values()),
        "regular_boundary_removal_count": boundary_by_lane["quote_image"],
        "meme_boundary_removal_count": boundary_by_lane["daily_meme"],
        "unresolved": unresolved,
    }


def is_media_v2_request_failure(record: Record) -> bool:
    """Return whether is media v2 request failure."""
    return (
        record.level in {"ERROR", "CRITICAL"}
        and record.src == "x_request"
        and "X request failed before receiving response" in record.msg
    )


def is_media_fallback_warning(record: Record) -> bool:
    """Return whether is media fallback warning."""
    return (
        record.level in {"ERROR", "CRITICAL", "WARNING"}
        and "v2 media upload failed; trying v1.1 fallback" in record.msg
    )


def is_media_v1_success(record: Record) -> bool:
    """Return whether is media v1 success."""
    return "Uploaded media via v1.1." in record.msg


def is_media_v1_failure(record: Record) -> bool:
    """Return whether is media v1 failure."""
    return (
        record.level in {"ERROR", "CRITICAL"}
        and record.src in {"upload_media", "upload_media_v1_1", "x_request"}
        and (
            "v1.1" in record.msg
            or "legacy v1.1" in record.msg
            or "media upload failed" in record.msg
        )
    )


def is_main_post_success(record: Record) -> bool:
    """Return whether is main post success."""
    return (
        "Quote/image posted successfully." in record.msg
        or "Daily meme posted successfully." in record.msg
        or ('EVENT {"event":"main_post_posted"' in record.msg)
    )


def find_recent_media_path(records: List[Record], index: int) -> Optional[str]:
    """Find recent media path."""
    for earlier in reversed(records[max(0, index - 20):index + 1]):
        m = re.search(r"Uploading media via X API v2: (.+)$", earlier.msg)
        if m:
            return m.group(1).strip()
        m = re.search(r"Detected MIME type for (.+?):", earlier.msg)
        if m:
            return m.group(1).strip()
    return None


def correlate_media_upload_incidents(
    records: List[Record],
    max_text: int,
    input_file_indexes: Optional[Dict[str, int]] = None,
    *,
    short: Callable[[Any, int], str],
    seconds_between: Callable[[datetime, datetime], float],
    record_source_ref: Callable[..., Dict[str, Any]],
    record_fingerprint: Callable[[Record], str],
    bounded_source_refs: Callable[..., Tuple[List[Dict[str, Any]], int]],
    is_media_fallback_warning: Callable[[Record], bool],
    find_recent_media_path: Callable[[List[Record], int], Optional[str]],
    is_media_v2_request_failure: Callable[[Record], bool],
    is_media_v1_success: Callable[[Record], bool],
    is_main_post_success: Callable[[Record], bool],
    is_media_v1_failure: Callable[[Record], bool],
) -> Tuple[List[Dict[str, Any]], set[str]]:
    """Return the correlate media upload incidents."""
    incidents: List[Dict[str, Any]] = []
    suppressed: set[str] = set()
    used_fallbacks: set[int] = set()

    for idx, record in enumerate(records):
        if not is_media_fallback_warning(record) or idx in used_fallbacks:
            continue
        used_fallbacks.add(idx)
        media_path = find_recent_media_path(records, idx)
        prior_failures = [
            candidate
            for candidate in records[max(0, idx - 8):idx]
            if is_media_v2_request_failure(candidate) and seconds_between(candidate.ts, record.ts) <= 90
        ]
        later = [
            candidate
            for candidate in records[idx + 1:idx + 40]
            if seconds_between(candidate.ts, record.ts) <= 180
        ]
        v1_success = next((candidate for candidate in later if is_media_v1_success(candidate)), None)
        post_success = next((candidate for candidate in later if is_main_post_success(candidate)), None)
        v1_failures = [candidate for candidate in later if is_media_v1_failure(candidate) and candidate is not v1_success]

        chain_records = [record, *prior_failures]
        if v1_success:
            chain_records.append(v1_success)
        if post_success:
            chain_records.append(post_success)
        chain_records.extend(v1_failures)
        for item in chain_records:
            suppressed.add(record_fingerprint(item))
        source_refs, source_ref_omitted = bounded_source_refs(
            *[
                record_source_ref(item, input_file_indexes)
                for item in chain_records
            ]
        )

        handled = bool(v1_success and post_success and not v1_failures)
        status = "handled" if handled else "unrecovered"
        detail = "v2 upload failed"
        if prior_failures:
            detail = short(prior_failures[-1].msg, max_text)
        incidents.append({
            "time": record.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "media": media_path or "",
            "v2_failure": detail,
            "fallback": short(record.msg, max_text),
            "v1_result": "succeeded" if v1_success else ("failed" if v1_failures else "not observed"),
            "post_result": "succeeded" if post_success else "not observed",
            "summary": (
                "v2 upload failed; v1.1 fallback succeeded and final post completed"
                if handled
                else "v2 upload failed and media/post completion was not observed"
            ),
            **({"source_refs": source_refs} if source_refs else {}),
            **(
                {"source_ref_omitted_count": source_ref_omitted}
                if source_ref_omitted
                else {}
            ),
        })

    return incidents, suppressed


def add_receipt_event(
    kind: str,
    r: Record,
    kwargs: Dict[str, Any],
    *,
    input_file_indexes: Optional[Dict[str, int]],
    stats: Counter,
    short: Callable[[Any, int], str],
    is_selftest_log_path: Callable[[Path | str], bool],
    record_source_ref: Callable[..., Dict[str, Any]],
    receipt_events: List[Dict[str, Any]],
) -> None:
    """Append a receipt observation using current formatting and source callbacks."""
    item = {
        "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
        "level": r.level,
        "message": short(r.msg, 500),
        "source_class": (
            "selftest" if is_selftest_log_path(r.path) else "production"
        ),
    }
    item.update(kwargs)
    item["source_refs"] = [record_source_ref(r, input_file_indexes)]
    receipt_events.append(item)
    stats[f"receipt_{kind}"] += 1


def add_confirmed_reply_receipt_event(
    kind: str,
    r: Record,
    kwargs: Dict[str, Any],
    *,
    input_file_indexes: Optional[Dict[str, int]],
    stats: Counter,
    short: Callable[[Any, int], str],
    is_selftest_log_path: Callable[[Path | str], bool],
    record_source_ref: Callable[..., Dict[str, Any]],
    confirmed_reply_receipts: List[Dict[str, Any]],
    pending_confirmed_reply_receipt: Dict[str, Any],
) -> Dict[str, Any]:
    """Append an observation and return the exact current or replacement state."""
    if kind == "removed" and pending_confirmed_reply_receipt:
        for key in ("lane", "target_id", "reply_post_id"):
            kwargs.setdefault(key, pending_confirmed_reply_receipt.get(key, ""))
    item = {
        "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
        "level": r.level,
        "message": short(r.msg, 500),
        "source_class": (
            "selftest" if is_selftest_log_path(r.path) else "production"
        ),
    }
    item.update(kwargs)
    item["source_refs"] = [record_source_ref(r, input_file_indexes)]
    confirmed_reply_receipts.append(item)
    stats[f"confirmed_reply_receipt_{kind}"] += 1
    if kind in {"written", "reconciled"}:
        pending_confirmed_reply_receipt = {
            key: item.get(key, "")
            for key in ("lane", "target_id", "reply_post_id")
            if item.get(key, "")
        }
    elif kind == "removed":
        pending_confirmed_reply_receipt = {}
    return pending_confirmed_reply_receipt


def add_reply_media_context_event(
    r: Record,
    kwargs: Dict[str, Any],
    *,
    reply_media_context: List[Dict[str, Any]],
    stats: Counter,
    short: Callable[[Any, int], str],
) -> None:
    """Append a legacy reply-media observation without publication authority."""
    item = {
        "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
        "level": r.level,
        "message": short(r.msg, 500),
    }
    item.update(kwargs)
    reply_media_context.append(item)
    stats["reply_media_context_events"] += 1


def record_x_request_start(
    request_start: Dict[str, str],
    r: Record,
    *,
    input_file_indexes: Optional[Dict[str, int]],
    x_requests: List[Dict[str, Any]],
    latest_x_request_by_source: Dict[str, Dict[str, Any]],
    stats: Counter,
    record_source_ref: Callable[..., Dict[str, Any]],
) -> None:
    """Project a selected parsed request, sharing its event with the source index."""
    request_event: Dict[str, Any] = {
        "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
        "source": r.src,
        **request_start,
        "source_refs": [record_source_ref(r, input_file_indexes)],
    }
    x_requests.append(request_event)
    latest_x_request_by_source[r.src] = request_event
    stats[f"x_request_endpoint_{request_start['endpoint'].replace('/', '_')}"] += 1


def record_remote_write_transaction(
    transaction_event: Dict[str, Any],
    r: Record,
    *,
    input_file_indexes: Optional[Dict[str, int]],
    remote_write_transactions: List[Dict[str, Any]],
    stats: Counter,
    record_source_ref: Callable[..., Dict[str, Any]],
    add_receipt_event: Callable[..., None],
) -> None:
    """Retain the parser's event and project only its existing receipt fields."""
    transaction_event["source_refs"] = [
        record_source_ref(r, input_file_indexes)
    ]
    remote_write_transactions.append(transaction_event)
    stats[
        "remote_write_transaction_"
        + str(transaction_event.get("phase") or "observed")
    ] += 1
    if transaction_event.get("kind") == "main_post_receipt":
        add_receipt_event(
            "main_post_receipt",
            r,
            **{
                key: value
                for key, value in transaction_event.items()
                if key
                in {
                    "phase",
                    "lane",
                    "attempt_id",
                    "post_id",
                    "path",
                    "disposition",
                }
            },
        )


def handle_legacy_receipt_message(
    r: Record,
    msg: str,
    *,
    pending_confirmed_reply_receipt: Dict[str, Any],
    add_receipt_event: Callable[..., None],
    add_confirmed_reply_receipt_event: Callable[..., None],
) -> bool:
    """Dispatch the first legacy receipt match and preserve its continue boundary."""
    if "Wrote confirmed regular-post receipt pending local reconciliation" in msg:
        add_receipt_event("regular_written", r, lane="quote_image")
        return True
    if "Wrote confirmed meme-post receipt pending local reconciliation" in msg:
        add_receipt_event("meme_written", r, lane="daily_meme")
        return True
    m = re.search(
        r"Wrote confirmed reply receipt pending local reconciliation"
        r"(?: source=([^\s]+) target_id=([^\s]+) reply_post_id=([^\s]+))?",
        msg,
    )
    if m:
        kwargs: Dict[str, Any] = {}
        if m.group(1):
            kwargs.update({"lane": m.group(1), "target_id": m.group(2), "reply_post_id": m.group(3)})
        add_confirmed_reply_receipt_event("written", r, **kwargs)
        return True
    m = re.search(
        r"Wrote conversational reply sending receipt"
        r" source=([^\s]+) target_id=([^\s]+)",
        msg,
    )
    if m:
        add_confirmed_reply_receipt_event(
            "sending",
            r,
            lane=m.group(1),
            target_id=m.group(2),
        )
        return True
    m = re.search(
        r"Promoted conversational reply receipt to confirmed"
        r" source=([^\s]+) target_id=([^\s]+) reply_post_id=([^\s]+)",
        msg,
    )
    if m:
        add_confirmed_reply_receipt_event(
            "promoted",
            r,
            lane=m.group(1),
            target_id=m.group(2),
            reply_post_id=m.group(3),
        )
        return True
    m = re.search(
        r"Removed conversational reply sending receipt after definite "
        r"non-success source=([^\s]+) target_id=([^\s]+)",
        msg,
    )
    if m:
        add_confirmed_reply_receipt_event(
            "sending_removed",
            r,
            lane=m.group(1),
            target_id=m.group(2),
            disposition="definite_non_success",
        )
        return True
    m = re.search(
        r"Removed conversational reply sending receipt after confirmed identity "
        r"was preserved in canonical state source=([^\s]+) target_id=([^\s]+)",
        msg,
    )
    if m:
        add_confirmed_reply_receipt_event(
            "confirmed_state_fallback_removed",
            r,
            lane=m.group(1),
            target_id=m.group(2),
            disposition="confirmed_state_fallback",
        )
        return True
    m = re.search(r"Removed reconciled regular-post receipt:\s*(.+)$", msg)
    if m:
        add_receipt_event(
            "regular_removed",
            r,
            lane="quote_image",
            path=m.group(1).strip(),
        )
        return True
    m = re.search(r"Removed reconciled meme-post receipt:\s*(.+)$", msg)
    if m:
        add_receipt_event(
            "meme_removed",
            r,
            lane="daily_meme",
            path=m.group(1).strip(),
        )
        return True
    m = re.search(
        r"Removed reconciled confirmed-reply receipt"
        r"(?: source=([^\s]+) target_id=([^\s]+) reply_post_id=([^\s]+))?",
        msg,
    )
    if m:
        kwargs = {}
        if m.group(1):
            kwargs.update({"lane": m.group(1), "target_id": m.group(2), "reply_post_id": m.group(3)})
        add_confirmed_reply_receipt_event("removed", r, **kwargs)
        return True
    m = re.search(r"Reconciling confirmed regular quote/image post receipt post_id=([^\s]+) quote_hash=([^\s]+) image=([^\s]+)", msg)
    if m:
        add_receipt_event("regular_reconciled", r, lane="quote_image", post_id=m.group(1), quote_hash=m.group(2), image=m.group(3))
        return True
    m = re.search(r"Reconciling confirmed meme post receipt post_id=([^\s]+) meme=([^\s]+)", msg)
    if m:
        add_receipt_event("meme_reconciled", r, lane="daily_meme", post_id=m.group(1), file=m.group(2))
        return True
    m = re.search(
        r"Reconciling confirmed reply receipt"
        r"(?: source=([^\s]+))? target_id=([^\s]+) reply_post_id=([^\s]+)",
        msg,
    )
    if m:
        lane = m.group(1) or pending_confirmed_reply_receipt.get("lane", "")
        add_confirmed_reply_receipt_event(
            "reconciled",
            r,
            lane=lane,
            target_id=m.group(2),
            reply_post_id=m.group(3),
        )
        return True
    if "Reconciled confirmed reply receipt before checking new mention candidates" in msg:
        add_confirmed_reply_receipt_event("replay_suppressed_mention_check", r, lane="mention")
        return True
    if "Reconciled confirmed reply receipt before checking new quote-tweet candidates" in msg:
        add_confirmed_reply_receipt_event("replay_suppressed_quote_tweet_check", r, lane="quote_tweet")
        return True
    if "Reconciled regular quote/image receipt; not creating a second regular post" in msg:
        add_receipt_event("regular_replay_suppressed_second_post", r, lane="quote_image")
        return True
    if "Reconciled meme post receipt; not creating a second meme post" in msg:
        add_receipt_event("meme_replay_suppressed_second_post", r, lane="daily_meme")
        return True
    if "Both regular and meme confirmed-post receipts exist" in msg:
        add_receipt_event("simultaneous_receipts_blocked", r, lane="main")
        return True
    if "regular-post receipt blocks" in msg or "meme-post receipt blocks" in msg:
        lane = "daily_meme" if "meme-post" in msg else "quote_image"
        add_receipt_event("invalid_or_unresolved_blocked", r, lane=lane)
        return True
    if "confirmed-reply receipt blocks" in msg:
        add_confirmed_reply_receipt_event("invalid_or_malformed_blocked", r)
        return True
    return False


def handle_legacy_reply_media_context_message(
    r: Record,
    msg: str,
    *,
    add_reply_media_context_event: Callable[..., None],
) -> bool:
    """Dispatch the first legacy reply-media match at its original boundary."""
    m = re.search(
        r"Reply media context fallback lane=([^\s]+) target_id=([^\s]+) "
        r"photos_expected=(\d+) initial_mode=([^\s]+) final_mode=([^\s]+) "
        r"status=([^\s]+) http_status=([^\s]+)",
        msg,
    )
    if m:
        add_reply_media_context_event(
            r,
            lane=m.group(1),
            target_id=m.group(2),
            photos=m.group(3),
            mode=m.group(5),
            status=m.group(6),
            http_status=m.group(7),
        )
        return True

    m = re.search(
        r"Reply media context(?: unavailable)? lane=([^\s]+) target_id=([^\s]+) "
        r"(?:photos=(\d+)|photos_expected=(\d+)) mode=([^\s]+) status=([^\s]+)",
        msg,
    )
    if m:
        add_reply_media_context_event(
            r,
            lane=m.group(1),
            target_id=m.group(2),
            photos=m.group(3) or m.group(4) or "",
            mode=m.group(5),
            status=m.group(6),
        )
        return True

    return False


def prepare_media_incidents_and_errors(
    *,
    records: List[Record],
    max_text: int,
    input_file_indexes: Optional[Dict[str, int]],
    remote_write_transactions: List[Dict[str, Any]],
    x_requests: List[Dict[str, Any]],
    current_remote_write_safety: Optional[Dict[str, Any]],
    errors: List[Dict[str, Any]],
    self_test_errors: List[Dict[str, Any]],
    self_test_times: set[str],
    api_error_times: set[str],
    handled_restriction_times: List[datetime],
    correlate_media_upload_incidents: Callable[..., Tuple[List[Dict[str, Any]], set[str]]],
    parse_dt: Callable[[str], Optional[datetime]],
    strptime: Callable[[str, str], datetime],
    seconds_between: Callable[[datetime, datetime], float],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return media incidents and a new error list, retaining original error rows."""
    media_upload_incidents, media_suppressed_fingerprints = (
        correlate_media_upload_incidents(
            records,
            max_text,
            input_file_indexes,
        )
    )
    for event in remote_write_transactions:
        if event.get("kind") != "media_upload" or event.get("phase") != "ambiguous":
            continue
        incident_time = parse_dt(str(event.get("time") or ""))
        later_tweet_create = any(
            request.get("endpoint") == "tweet/create"
            and (
                incident_time is None
                or (
                    (parse_dt(str(request.get("time") or "")) or incident_time)
                    >= incident_time
                )
            )
            for request in x_requests
        )
        reconciliation_archive = (
            (current_remote_write_safety or {}).get("reconciliation_archive")
            or {}
        )
        media_reconciliations = (
            reconciliation_archive.get("media_reconciliations") or []
        )
        if not media_reconciliations:
            latest_media_reconciliation = reconciliation_archive.get(
                "latest_media_reconciliation"
            )
            media_reconciliations = (
                [latest_media_reconciliation]
                if latest_media_reconciliation
                else []
            )
        matching_media_reconciliations = [
            item
            for item in media_reconciliations
            if type(item.get("archived_at_epoch")) is int
            and incident_time is not None
            and item["archived_at_epoch"] >= int(incident_time.timestamp())
            and item.get("image_basename") == event.get("image")
        ]
        reconciled = bool(
            (current_remote_write_safety or {}).get(
                "media_reconciliation_proven"
            )
            and (current_remote_write_safety or {}).get("blocking") is False
            and matching_media_reconciliations
        )
        media_upload_incidents.append(
            {
                "time": event.get("time"),
                "status": "reconciled" if reconciled else "blocked",
                "media": event.get("image") or "",
                "v2_failure": event.get("message") or "",
                "fallback": "legacy fallback prohibited by receipt-bound v2 protocol",
                "v1_result": "not applicable",
                "post_result": (
                    "uncorrelated tweet-create request observed in window"
                    if later_tweet_create
                    else "no later tweet-create request observed in window (uncorrelated)"
                ),
                "summary": (
                    "ambiguous receipt-bound media upload was durably reconciled offline"
                    if reconciled
                    else "ambiguous receipt-bound media upload remains blocked"
                ),
                "protocol": "receipt_bound_v2",
                **(
                    {"source_refs": list(event.get("source_refs") or [])}
                    if event.get("source_refs")
                    else {}
                ),
                **(
                    {
                        "source_ref_omitted_count": int(
                            event["source_ref_omitted_count"]
                        )
                    }
                    if event.get("source_ref_omitted_count")
                    else {}
                ),
            }
        )
    remaining_errors: List[Dict[str, Any]] = []
    for item in errors:
        message = str(item.get("message", ""))
        timestamp = str(item.get("time", ""))
        if item.get("_fingerprint") in media_suppressed_fingerprints:
            continue
        if timestamp in self_test_times and (
            "Missing X credentials." in message
            or "ENABLE_AUTO_REPLIES is True, but XAI_API_KEY is not set." in message
        ):
            self_test_errors.append(item)
            continue
        if timestamp in api_error_times and (
            message.startswith("Failed to get mention")
            or message.startswith("Failed to get quote")
            or message.startswith("Failed to fetch quote")
        ):
            continue
        if message.startswith(("Failed to post generated reply", "Unexpected failure posting generated reply")):
            try:
                error_time = strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                error_time = None
            if error_time is not None and any(
                seconds_between(error_time, restriction_time) <= 5
                for restriction_time in handled_restriction_times
            ):
                continue
        if message.startswith("Entering API cooldown after repeated errors"):
            try:
                error_time = strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                error_time = None
            if error_time is not None and any(
                seconds_between(error_time, restriction_time) <= 5
                for restriction_time in handled_restriction_times
            ):
                continue
        remaining_errors.append(item)
    return media_upload_incidents, remaining_errors


@dataclass
class _ReplyReceiptLifecycle:
    """Retain ordered pending receipt observations and terminal event counts."""

    pending_sending: Dict[Tuple[str, str], List[Dict[str, Any]]] = field(default_factory=dict)
    pending_confirmed: Counter[Tuple[str, str, str]] = field(default_factory=Counter)
    pending_reconciliations: List[
        Tuple[Tuple[str, str, str], Dict[str, Any]]
    ] = field(default_factory=list)
    unmatched: List[Dict[str, Any]] = field(default_factory=list)
    normal_reply_pairs: int = 0
    terminal_removals_outside_window: int = 0
    definite_non_success_clears: int = 0
    confirmed_state_fallback_clears: int = 0


def _scan_reply_receipt_lifecycle(
    receipt_events: Iterable[Dict[str, Any]],
) -> _ReplyReceiptLifecycle:
    """Scan supplied events without filtering, copying or mutating their rows."""
    lifecycle = _ReplyReceiptLifecycle()

    def clear_latest_reconciliation(
        *,
        identity: Tuple[str, str, str] | None = None,
        lane: str | None = None,
    ) -> Tuple[str, str, str] | None:
        for index in range(len(lifecycle.pending_reconciliations) - 1, -1, -1):
            candidate_identity, _item = lifecycle.pending_reconciliations[index]
            if identity is not None and candidate_identity != identity:
                continue
            if lane is not None and candidate_identity[0] != lane:
                continue
            lifecycle.pending_reconciliations.pop(index)
            return candidate_identity
        return None

    for item in receipt_events:
        sending_identity = (
            str(item.get("lane") or ""),
            str(item.get("target_id") or ""),
        )
        identity = (
            *sending_identity,
            str(item.get("reply_post_id") or ""),
        )
        kind = str(item.get("kind") or "")
        if kind == "sending":
            lifecycle.pending_sending.setdefault(
                sending_identity, []
            ).append(item)
        elif kind == "promoted":
            pending_for_identity = lifecycle.pending_sending.get(
                sending_identity, []
            )
            if pending_for_identity:
                pending_for_identity.pop()
            lifecycle.pending_confirmed[identity] += 1
        elif kind == "sending_removed":
            pending_for_identity = lifecycle.pending_sending.get(
                sending_identity, []
            )
            if pending_for_identity:
                pending_for_identity.pop()
            # The matching pre-send event may be outside the selected log
            # window.  This terminal event still proves that the receipt
            # was cleared after a definite non-success.
            lifecycle.definite_non_success_clears += 1
        elif kind == "confirmed_state_fallback_removed":
            pending_for_identity = lifecycle.pending_sending.get(
                sending_identity, []
            )
            if pending_for_identity:
                pending_for_identity.pop()
            # Likewise, a digest window can begin after the sending event.
            # The terminal fallback event is self-contained evidence that
            # the confirmed reply identity was durably preserved.
            lifecycle.confirmed_state_fallback_clears += 1
        elif kind == "written":
            lifecycle.pending_confirmed[identity] += 1
        elif kind == "reconciled":
            lifecycle.pending_reconciliations.append((identity, item))
        elif kind == "removed":
            clear_latest_reconciliation(identity=identity)
            if lifecycle.pending_confirmed[identity] > 0:
                lifecycle.pending_confirmed[identity] -= 1
                lifecycle.normal_reply_pairs += 1
            else:
                # The opening write can legitimately precede the selected
                # window.  A removal is nevertheless terminal evidence,
                # not an unresolved receipt.
                lifecycle.terminal_removals_outside_window += 1
        elif kind in {
            "replay_suppressed_mention_check",
            "replay_suppressed_quote_tweet_check",
        }:
            completed_identity = clear_latest_reconciliation(
                lane=sending_identity[0]
            )
            if (
                completed_identity is not None
                and lifecycle.pending_confirmed[completed_identity] > 0
            ):
                lifecycle.pending_confirmed[completed_identity] -= 1
        else:
            lifecycle.unmatched.append(item)
    return lifecycle


def summarise_reply_receipt_lifecycle(
    receipt_events: List[Dict[str, Any]],
    *,
    reconciled_ambiguity_receipts: Iterable[Dict[str, Any]] = (),
    unavailable_receipts: Iterable[Dict[str, Any]] = (),
    normalise_lane: Callable[[Any], str],
) -> Dict[str, Any]:
    """Prepare renderable receipt counts and unresolved rows from supplied evidence.

    All supplied source classes remain visible. Matching snapshot evidence
    consumes one pending sending observation per exact lane, target and time.
    Inputs and nested source references retain their original identities.
    """
    reconciled_ambiguity_event_counts = Counter(
        (
            normalise_lane(item.get("lane")),
            str(item.get("target_id") or ""),
            str(item.get("source_time") or ""),
        )
        for item in reconciled_ambiguity_receipts
        if isinstance(item, dict)
        and item.get("lane")
        and item.get("target_id")
        and item.get("source_time")
    )
    unavailable_receipt_event_counts = Counter(
        (
            normalise_lane(item.get("lane")),
            str(item.get("target_id") or ""),
            str(item.get("source_time") or ""),
        )
        for item in unavailable_receipts
        if isinstance(item, dict)
        and item.get("lane")
        and item.get("target_id")
        and item.get("source_time")
    )
    lifecycle = _scan_reply_receipt_lifecycle(receipt_events)
    unavailable_reply_receipt_rows: List[Dict[str, Any]] = []
    reconciled_ambiguity_sending_receipts = 0
    unresolved_reply_receipts = list(lifecycle.unmatched)
    for (lane, target_id), pending_events in sorted(
        lifecycle.pending_sending.items()
    ):
        for source in pending_events:
            event_identity = (
                normalise_lane(lane),
                target_id,
                str(source.get("time") or ""),
            )
            if reconciled_ambiguity_event_counts[event_identity] > 0:
                reconciled_ambiguity_event_counts[event_identity] -= 1
                reconciled_ambiguity_sending_receipts += 1
                continue
            if unavailable_receipt_event_counts[event_identity] > 0:
                unavailable_receipt_event_counts[event_identity] -= 1
                unavailable_reply_receipt_rows.append(
                    {
                        **source,
                        "lane": lane,
                        "target_id": target_id,
                        "kind": "current_status_unavailable",
                        "message": (
                            "Present receipt status cannot be established "
                            "from retained evidence"
                        ),
                    }
                )
                continue
            unresolved_reply_receipts.append(
                {
                    **source,
                    "lane": lane,
                    "target_id": target_id,
                    "kind": "sending_unresolved",
                    "message": (
                        "Pre-send reply receipt remains unresolved at the end "
                        "of the observed window"
                    ),
                }
            )
    pending_reconciliation_counts = Counter(
        identity for identity, _item in lifecycle.pending_reconciliations
    )
    for identity, source in lifecycle.pending_reconciliations:
        unresolved_reply_receipts.append(
            {
                **source,
                "lane": identity[0],
                "target_id": identity[1],
                "reply_post_id": identity[2],
                "kind": "reconciliation_unresolved",
                "message": (
                    "Confirmed-reply reconciliation began, but no terminal "
                    "receipt removal or completion was observed"
                ),
            }
        )
    for (lane, target_id, reply_post_id), count in sorted(
        lifecycle.pending_confirmed.items()
    ):
        if count <= 0:
            continue
        identity = (lane, target_id, reply_post_id)
        if pending_reconciliation_counts[identity] >= count:
            continue
        source = next(
            (
                item
                for item in reversed(receipt_events)
                if str(item.get("kind") or "") in {"written", "promoted"}
                and str(item.get("lane") or "") == lane
                and str(item.get("target_id") or "") == target_id
                and str(item.get("reply_post_id") or "") == reply_post_id
            ),
            {},
        )
        unresolved_reply_receipts.append(
            {
                **source,
                "lane": lane,
                "target_id": target_id,
                "reply_post_id": reply_post_id,
                "kind": "confirmed_unresolved",
                "message": (
                    "Confirmed reply receipt remains unresolved at the end "
                    "of the observed window"
                ),
            }
        )
    return {
        "normal_reply_pairs": lifecycle.normal_reply_pairs,
        "terminal_reply_removals_outside_window": (
            lifecycle.terminal_removals_outside_window
        ),
        "definite_non_success_clears": lifecycle.definite_non_success_clears,
        "confirmed_state_fallback_clears": lifecycle.confirmed_state_fallback_clears,
        "reconciled_ambiguity_sending_receipts": reconciled_ambiguity_sending_receipts,
        "unresolved_reply_receipts": unresolved_reply_receipts,
        "unavailable_reply_receipt_rows": unavailable_reply_receipt_rows,
    }


def append_unresolved_reply_receipt_errors(
    *,
    confirmed_reply_receipts: List[Dict[str, Any]],
    errors: List[Dict[str, Any]],
) -> None:
    """Append unresolved sending/reconciliation errors in their existing order."""
    lifecycle = _scan_reply_receipt_lifecycle(
        item for item in confirmed_reply_receipts
        if item.get("source_class") != "selftest"
    )
    for identity, pending_events in sorted(lifecycle.pending_sending.items()):
        for source in pending_events:
            raw_message = (
                "Unresolved conversational reply sending receipt remains at the end "
                f"of the observed window lane={identity[0]} target_id={identity[1]}"
            )
            errors.append(
                {
                    "time": str(source.get("time") or ""),
                    "level": "CRITICAL",
                    "where": "confirmed_reply_receipt_lifecycle",
                    "message": raw_message,
                    "_raw_message": raw_message,
                    "source_refs": list(source.get("source_refs") or []),
                }
            )
    for identity, source in lifecycle.pending_reconciliations:
        raw_message = (
            "Unresolved confirmed reply receipt reconciliation remains at the "
            "end of the observed window "
            f"lane={identity[0]} target_id={identity[1]} "
            f"reply_post_id={identity[2]}"
        )
        errors.append(
            {
                "time": str(source.get("time") or ""),
                "level": "CRITICAL",
                "where": "confirmed_reply_receipt_lifecycle",
                "message": raw_message,
                "_raw_message": raw_message,
                "source_refs": list(source.get("source_refs") or []),
            }
        )


def prepare_reply_receipt_recovery_reporting(
    *,
    error_health: Dict[str, Any],
    current_remote_write_safety: Optional[Dict[str, Any]],
    confirmed_reply_receipts: List[Dict[str, Any]],
    parse_dt: Callable[[str], Optional[datetime]],
    _normalise_lane: Callable[[Any], str],
    REMOTE_WRITE_RECEIPT_ROLE_LABELS: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return reconciled, unavailable and active receipt evidence for reporting."""
    durably_reconciled_reply_receipts: List[Dict[str, Any]] = []
    for incident in error_health.get("historical_resolved_incidents") or []:
        if incident.get("category") != "remote_write_ambiguity_barrier":
            continue
        resolution_time = str(incident.get("resolution_time") or "")
        try:
            resolved_at = parse_dt(resolution_time)
        except ValueError:
            continue
        for receipt_event in incident.get("correlated_reply_receipt_events") or []:
            if not isinstance(receipt_event, dict):
                continue
            source_time = str(receipt_event.get("source_time") or "")
            try:
                source_at = parse_dt(source_time)
            except ValueError:
                continue
            if source_at > resolved_at:
                continue
            durably_reconciled_reply_receipts.append(
                {
                    "lane": str(receipt_event.get("lane") or ""),
                    "target_id": str(receipt_event.get("target_id") or ""),
                    "source_time": source_time,
                    "resolution_time": resolution_time,
                    "resolution_reason": incident.get("resolution_reason"),
                }
            )
    status_unavailable_reply_receipts: List[Dict[str, Any]] = []
    for incident in error_health.get("resolution_unavailable_incidents") or []:
        receipt_evidence = list(
            incident.get("correlated_reply_receipt_events") or []
        )
        if (
            not receipt_evidence
            and incident.get("target_id")
            and incident.get("lane")
        ):
            receipt_evidence = [
                {
                    "lane": str(item.get("lane") or ""),
                    "target_id": str(item.get("target_id") or ""),
                    "source_time": str(item.get("time") or ""),
                }
                for item in confirmed_reply_receipts
                if item.get("kind") == "sending"
                and str(item.get("target_id") or "")
                == str(incident.get("target_id") or "")
                and _normalise_lane(item.get("lane"))
                == _normalise_lane(incident.get("lane"))
            ]
        for receipt_event in receipt_evidence:
            status_unavailable_reply_receipts.append(
                {
                    "lane": str(receipt_event.get("lane") or ""),
                    "target_id": str(receipt_event.get("target_id") or ""),
                    "source_time": str(receipt_event.get("source_time") or ""),
                    "reason": incident.get("resolution_reason"),
                }
            )
    active_snapshot_reply_receipts: List[Dict[str, Any]] = []
    for component in (
        (current_remote_write_safety or {}).get(
            "active_transaction_identities", []
        )
        or []
    ):
        if "conversational_confirmed_reply" not in (
            component.get("receipt_roles") or []
        ):
            continue
        lanes = [
            str(lane)
            for lane in component.get("lanes") or []
            if str(lane) != "conversational_reply"
        ] or [str(lane) for lane in component.get("lanes") or []]
        active_snapshot_reply_receipts.append(
            {
                "transaction_ids": component.get("transaction_ids") or [],
                "target_ids": component.get("target_ids") or [],
                "lane": lanes[0] if len(lanes) == 1 else ", ".join(lanes),
                "artifact_names": component.get("artifact_names") or [],
                "receipt_role": "conversational_confirmed_reply",
                "receipt_role_label": REMOTE_WRITE_RECEIPT_ROLE_LABELS[
                    "conversational_confirmed_reply"
                ],
                "selected_window_relationship": component.get(
                    "selected_window_relationship"
                ),
            }
        )
    return (
        durably_reconciled_reply_receipts,
        status_unavailable_reply_receipts,
        active_snapshot_reply_receipts,
    )
