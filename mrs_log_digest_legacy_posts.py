"""Observe raw legacy quote/image, meme and conversational post/reply messages.

The coordinator keeps record/source selection, source-local state switching,
structured dispatch and event insertion. Named handlers receive only their
current inputs and helpers and return true at an original outer continue.
Pending/latest objects are mutated or replaced exactly at their legacy branches;
production event identities may be removed using the supplied evidence set.
There is no I/O, clock sampling, runtime access or provider/posting action here.
"""
from __future__ import annotations

import ast
from collections import Counter
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from mrs_log_digest_records import Record


def try_parse_response_id_text(msg: str) -> Tuple[Optional[str], Optional[str]]:
    """Return the try parse response ID text."""
    marker = "response="
    if marker not in msg:
        return None, None
    raw = msg.split(marker, 1)[1].strip()
    try:
        data = ast.literal_eval(raw)
        d = data.get("data") or {}
        return str(d.get("id")) if d.get("id") is not None else None, d.get("text")
    except Exception:
        m = re.search(r"'id': '([^']+)'", raw)
        return (m.group(1) if m else None), None


def response_post_id_is_canonical_string(
    msg: str,
    *,
    valid_string_public_post_id: Callable[[Any], bool],
) -> bool:
    """Return whether a legacy success response stores its ID as a string."""

    marker = "response="
    if marker not in msg:
        return False
    raw = msg.split(marker, 1)[1].strip()
    try:
        data = ast.literal_eval(raw)
        payload = data.get("data") if isinstance(data, dict) else None
        return bool(
            isinstance(payload, dict)
            and valid_string_public_post_id(payload.get("id"))
        )
    except Exception:
        # The compatibility parser may salvage an old display event below,
        # but malformed payload text is never immutable success authority.
        return False


def handle_legacy_quiet_message(
    r: Record,
    msg: str,
    *,
    stats: Counter,
    add_event: Callable[..., Dict[str, Any]],
) -> bool:
    """Count quiet/lane diagnostics; only fetched hot-post results end dispatch."""
    # General quiet counters.
    if "No mentions returned" in msg:
        stats["no_mentions_checks"] += 1
    if "Starting mention reply check" in msg:
        stats["mention_checks"] += 1
    if msg.startswith("Fetching mentions."):
        stats["mention_fetch_attempts"] += 1
    if "Starting quote-tweet reply check" in msg:
        stats["quote_tweet_checks"] += 1
    if msg == "Due to check mentions":
        stats["normal_lane_due_checks"] += 1
    if msg == "Due to check quote tweets":
        stats["quote_lane_due_checks"] += 1

    # Hot-post reply watch / alternating-lane diagnostics.
    if "Hot-post reply check loaded" in msg:
        stats["hot_post_reply_watch_loads"] += 1
    if "/2/tweets/search/recent" in msg:
        stats["recent_search_calls"] += 1
    if msg == "Quote recent-search request":
        stats["quote_recent_search_calls"] += 1
    if msg == "Hot-post recent-search request":
        stats["hot_post_recent_search_calls"] += 1
    m = re.search(r"Fetched (\d+) hot-post conversation candidate\(s\) for post_id=(\d+)", msg)
    if m:
        stats["hot_post_recent_search_successes"] += 1
        add_event(
            "hot_post_search_result",
            r.ts,
            original_post_id=m.group(2),
            candidates=int(m.group(1)),
        )
        return True
    m = re.search(r"Hot-post reply check returning (\d+) candidate\(s\)", msg)
    if m:
        stats["hot_post_reply_candidate_batches"] += 1
        stats["hot_post_reply_candidates_returned"] += int(m.group(1))
    if "Quote-tweet check is due, but normal/hot-post reply lane has priority" in msg:
        stats["priority_forced_normal_before_quote"] += 1
    if "Normal/hot-post reply lane posted; next reply-lane priority=quote" in msg:
        stats["priority_flipped_to_quote"] += 1
    if "Quote-tweet reply lane posted; next reply-lane priority=normal" in msg:
        stats["priority_flipped_to_normal"] += 1
    if "Normal/hot-post reply lane did not post; quote-tweet lane may use this slot" in msg:
        stats["priority_normal_first_refusal_no_post"] += 1
    m = re.search(r"Quote-tweet check status=([a-z_]+)", msg)
    if m:
        status = m.group(1)
        stats[f"quote_tweet_status_{status}"] += 1
        if status != "posted":
            stats["quote_tweet_checks_no_post"] += 1
    return False


def handle_legacy_quote_image_selection(
    r: Record,
    msg: str,
    *,
    pending_quote: Dict[str, Any],
    regular_image_usage_events: List[Dict[str, Any]],
    add_event: Callable[..., Dict[str, Any]],
) -> bool:
    """Observe quote/image selection, retaining the supplied pending map and rows."""
    # Quote/image posts.
    m = re.search(
        r"Selected quote line_no=(\d+) quote_hash=([0-9a-fA-F]+) weight=([0-9.]+) seasonal_boost=(True|False)",
        msg,
    )
    if m:
        pending_quote.update({
            "line_no": int(m.group(1)),
            "quote_hash": m.group(2),
            "quote_weight": m.group(3),
            "seasonal_boost": m.group(4),
        })
        add_event(
            "quote_selected",
            r.ts,
            line_no=int(m.group(1)),
            quote_hash=m.group(2),
            weight=m.group(3),
            seasonal_boost=m.group(4),
        )
        return True

    m = re.search(
        r"Image cycle status: used_count=(\d+) currently_eligible=(\d+) remaining_count=(\d+) seasonally_excluded=(\d+) stale_excluded=(\d+) cycle_reset=(True|False)",
        msg,
    )
    if m:
        add_event(
            "image_cycle_status",
            r.ts,
            used_count=int(m.group(1)),
            currently_eligible=int(m.group(2)),
            remaining_count=int(m.group(3)),
            seasonally_excluded=int(m.group(4)),
            stale_excluded=int(m.group(5)),
            cycle_reset=m.group(6),
        )
        return True

    m = re.search(r"Selected matched image basename=([^\s]+) image_no=(\d+) score=([^\s]+) components=(.*)$", msg)
    if m:
        pending_quote.update({
            "image_basename": m.group(1),
            "image_no": int(m.group(2)),
            "image_score": m.group(3),
            "image_components": m.group(4).strip(),
        })
        add_event(
            "matched_image_selected",
            r.ts,
            image=m.group(1),
            image_no=int(m.group(2)),
            score=m.group(3),
            components=m.group(4).strip(),
        )
        return True

    m = re.search(
        r"REGULAR_IMAGE_SELECTED source=(original|generated) basename=([^\s]+) score=([^\s]+) "
        r"origin_quote_hash=([0-9a-fA-F]{64}|) origin_quote_match=(true|false) origin_quote_boost=([^\s]+)",
        msg,
    )
    if m:
        item = {
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "source": m.group(1),
            "basename": m.group(2),
            "score": m.group(3),
            "origin_quote_hash": m.group(4),
            "origin_quote_match": m.group(5),
            "origin_quote_boost": m.group(6),
        }
        regular_image_usage_events.append(item)
        add_event(
            "regular_image_selected",
            r.ts,
            source=item["source"],
            basename=item["basename"],
            score=item["score"],
            origin_quote_hash=item["origin_quote_hash"],
            origin_quote_match=item["origin_quote_match"],
            origin_quote_boost=item["origin_quote_boost"],
        )
        return True
    return False


def handle_legacy_generated_image_spacing(
    r: Record,
    msg: str,
    *,
    latest_generated_image_spacing: Dict[str, Any],
    generated_image_spacing_events: List[Dict[str, Any]],
) -> Tuple[bool, Dict[str, Any]]:
    """Return handled status and latest spacing; blocked rows only append."""
    m = re.search(
        r"GENERATED_IMAGE_SPACING_STATUS pool_enabled=(true|false) allowed=(true|false) "
        r"original_posts_since_generated=(\d+) required=(\d+)",
        msg,
    )
    if m:
        item = {
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": "status",
            "pool_enabled": m.group(1),
            "allowed": m.group(2),
            "original_posts_since_generated": int(m.group(3)),
            "required": int(m.group(4)),
        }
        latest_generated_image_spacing = item
        generated_image_spacing_events.append(item)
        return True, latest_generated_image_spacing

    m = re.search(
        r"GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=(true|false) allowed=(true|false) "
        r"original_posts_since_generated=(\d+) required=(\d+) image_source=(\S+) image=(\S+)",
        msg,
    )
    if m:
        item = {
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": "state_updated",
            "pool_enabled": m.group(1),
            "allowed": m.group(2),
            "original_posts_since_generated": int(m.group(3)),
            "required": int(m.group(4)),
            "image_source": m.group(5),
            "image": m.group(6),
        }
        latest_generated_image_spacing = item
        generated_image_spacing_events.append(item)
        return True, latest_generated_image_spacing

    m = re.search(
        r"GENERATED_IMAGE_POOL_BLOCKED_BY_SPACING original_posts_since_generated=(\d+) required=(\d+)",
        msg,
    )
    if m:
        generated_image_spacing_events.append(
            {
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "kind": "blocked",
                "original_posts_since_generated": int(m.group(1)),
                "required": int(m.group(2)),
            }
        )
        return True, latest_generated_image_spacing
    return False, latest_generated_image_spacing


def handle_legacy_quote_image_posting(
    r: Record,
    msg: str,
    *,
    pending_quote: Dict[str, Any],
    regular_image_usage_events: List[Dict[str, Any]],
    add_event: Callable[..., Dict[str, Any]],
    lit: Callable[[str], str],
) -> Tuple[bool, Dict[str, Any]]:
    """Observe quote cycles/text/posting and return the original or new pending map."""
    m = re.search(r"Quote cycle is seasonally exhausted: (\d+) unused quote\(s\) are hard-excluded today; resetting quote cycle", msg)
    if m:
        add_event("quote_cycle_reset", r.ts, reason="seasonal_exhaustion", affected=m.group(1))
        return True, pending_quote

    m = re.search(r"Quote cycle is exhausted by currently nonselectable quote\(s\); resetting quote cycle\. unused_non_empty=(\d+) full_selectable=(\d+) full_hard_excluded=(\d+)", msg)
    if m:
        add_event(
            "quote_cycle_reset",
            r.ts,
            reason="nonselectable_exhaustion",
            affected=m.group(1),
            full_selectable=m.group(2),
            full_hard_excluded=m.group(3),
        )
        return True, pending_quote

    m = re.search(r"Selected line_no=(\d+) text=(.*)$", msg, re.S)
    if m:
        pending_quote["line_no"] = int(m.group(1))
        pending_quote["text"] = lit(m.group(2))
        return True, pending_quote

    m = re.search(r"Posting quote/image\. line_no=(\d+) image_no=(\d+) image=(.*)$", msg)
    if m:
        pending_quote.update({
            "start_time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "line_no": int(m.group(1)),
            "image_no": int(m.group(2)),
            "image": m.group(3).strip(),
        })
        return True, pending_quote

    if msg.startswith("Quote text="):
        pending_quote["text"] = lit(msg.split("=", 1)[1])
        return True, pending_quote

    m = re.search(r"Creating X post\. reply_to_id=([^\s]+) media_count=(\d+) made_with_ai=(True|False)\b", msg)
    if m:
        reply_to_id = m.group(1)
        media_count = int(m.group(2))
        made_with_ai = m.group(3).lower()
        if pending_quote and reply_to_id == "None" and media_count > 0:
            pending_quote["made_with_ai"] = made_with_ai
            image_basename = pending_quote.get("image_basename")
            for item in reversed(regular_image_usage_events):
                if item.get("basename") == image_basename and not item.get("made_with_ai"):
                    item["made_with_ai"] = made_with_ai
                    break
        return True, pending_quote

    m = re.search(r"Quote/image posted successfully\. posted_id=(\d+)", msg)
    if m:
        add_event(
            "quote_image_posted",
            r.ts,
            post_id=m.group(1),
            line_no=pending_quote.get("line_no"),
            image_no=pending_quote.get("image_no"),
            image_basename=pending_quote.get("image_basename"),
            image_score=pending_quote.get("image_score"),
            quote_hash=pending_quote.get("quote_hash"),
            text=pending_quote.get("text", ""),
            image=pending_quote.get("image", ""),
            made_with_ai=pending_quote.get("made_with_ai", ""),
        )
        pending_quote = {}
        return True, pending_quote
    return False, pending_quote


def handle_legacy_meme_posting(
    r: Record,
    msg: str,
    *,
    pending_meme: Dict[str, Any],
    add_event: Callable[..., Dict[str, Any]],
    lit: Callable[[str], str],
) -> Tuple[bool, Dict[str, Any]]:
    """Observe meme preparation/success and return the current pending map."""
    # Daily meme posts.
    if msg.startswith("Posting meme image:"):
        pending_meme = {"start_time": r.ts.strftime("%Y-%m-%d %H:%M:%S"), "image": msg.split(":", 1)[1].strip()}
        return True, pending_meme
    if msg.startswith("Meme image summary for cache:"):
        pending_meme["summary"] = lit(msg.split(":", 1)[1])
        return True, pending_meme
    m = re.search(r"Daily meme posted successfully\. posted_id=(\d+) file=(.+)$", msg)
    if m:
        add_event(
            "daily_meme_posted",
            r.ts,
            post_id=m.group(1),
            file=m.group(2).strip(),
            summary=pending_meme.get("summary", ""),
            image=pending_meme.get("image", ""),
        )
        pending_meme = {}
        return True, pending_meme
    return False, pending_meme


def handle_legacy_created_post(
    r: Record,
    msg: str,
    *,
    production_record: bool,
    last_created_post: Dict[str, Any],
    stats: Counter,
    production_event_object_ids: set[int],
    add_event: Callable[..., Dict[str, Any]],
    try_parse_response_id_text: Callable[[str], Tuple[Optional[str], Optional[str]]],
    response_post_id_is_canonical_string: Callable[[str], bool],
) -> Tuple[bool, Dict[str, Any]]:
    """Return created-post evidence while keeping compatibility and authority distinct."""
    # Created X post: remember it so reply/post events can attach if needed.
    if "Created X post successfully" in msg:
        post_id, post_text = try_parse_response_id_text(msg)
        last_created_post = {
            "time": r.ts,
            "post_id": post_id,
            "post_text": post_text,
            "canonical_post_id": response_post_id_is_canonical_string(msg),
            "production_identity": production_record,
        }
        stats["created_x_posts"] += 1
        if post_id and re.fullmatch(r"\d+", post_id):
            success_event = add_event(
                "remote_write_succeeded",
                r.ts,
                post_id=post_id,
            )
            if not response_post_id_is_canonical_string(msg):
                production_event_object_ids.discard(id(success_event))
        return True, last_created_post
    return False, last_created_post


def _prepare_legacy_reply_success(
    pending: Dict[str, Any],
    last_created_post: Dict[str, Any],
) -> Tuple[Dict[str, Any], bool]:
    """Adopt a missing reply ID in place and prepare public fields and authority.

    Existing cached IDs keep their own authority. The returned boolean only
    permits retaining production authority; event insertion, lane-specific
    public fields and pending-state replacement remain with the caller.
    """
    reply_id_from_nonauthoritative_response = False
    if not pending.get("reply_post_id") and last_created_post.get("post_id"):
        pending["reply_post_id"] = last_created_post.get("post_id")
        reply_id_from_nonauthoritative_response = not bool(
            last_created_post.get("canonical_post_id")
        )
        pending["_reply_post_id_production"] = bool(
            last_created_post.get("production_identity")
        )
    reply_identity_is_production = bool(
        pending.get("_identity_production", True)
        and pending.get("_reply_post_id_production", True)
    )
    public_fields = {
        key: value
        for key, value in pending.items()
        if not key.startswith("_")
    }
    keep_production_authority = (
        not reply_id_from_nonauthoritative_response
        and reply_identity_is_production
    )
    return public_fields, keep_production_authority


def handle_legacy_mention_reply(
    r: Record,
    msg: str,
    *,
    record_index: int,
    production_record: bool,
    pending_mention: Dict[str, Any],
    active_xai_context: Optional[Dict[str, Any]],
    last_created_post: Dict[str, Any],
    production_event_object_ids: set[int],
    routine_skip_counts: Counter,
    add_event: Callable[..., Dict[str, Any]],
    lit: Callable[[str], str],
) -> Tuple[bool, Dict[str, Any], Optional[Dict[str, Any]]]:
    """Observe normal/hot-post replies and return pending state and provider context."""
    # Normal mention lane, including synthetic hot-post reply candidates.
    m = re.search(r"Considering (mention|hot_post_reply) id=(\d+) author_id=([^\s]+) text=(.*)$", msg, re.S)
    if m:
        source = m.group(1)
        id_key = "mention_id" if source == "mention" else "hot_post_reply_id"
        pending_mention = {
            "source": source,
            id_key: m.group(2),
            "mention_id": m.group(2),  # kept for backward-compatible post/reply matching
            "author_id": m.group(3),
            "incoming_text": lit(m.group(4)),
            "considered_at": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "considered_seq": record_index,
            "_identity_production": production_record,
        }
        return True, pending_mention, active_xai_context

    m = re.search(r"Generated reply to mention (\d+): (.*)$", msg, re.S)
    if m:
        if pending_mention.get("mention_id") != m.group(1):
            pending_mention = {
                "mention_id": m.group(1),
                "source": "unknown",
                "_identity_production": production_record,
            }
        pending_mention["reply"] = lit(m.group(2))
        pending_mention["generated_at"] = r.ts.strftime("%Y-%m-%d %H:%M:%S")
        active_xai_context = None
        return True, pending_mention, active_xai_context

    m = re.search(r"Recorded and cached own auto-reply id=(\d+)", msg)
    if m and pending_mention:
        pending_mention["reply_post_id"] = m.group(1)
        pending_mention["_reply_post_id_production"] = production_record
        return True, pending_mention, active_xai_context

    if msg == "Reply posted successfully" and pending_mention:
        data, keep_production_authority = _prepare_legacy_reply_success(
            pending_mention, last_created_post,
        )
        source = pending_mention.get("source", "mention")
        data.pop("source", None)
        if source == "hot_post_reply":
            data.pop("mention_id", None)
            posted_event = add_event(
                "hot_post_reply_posted", r.ts, **data
            )
        else:
            posted_event = add_event(
                "mention_reply_posted", r.ts, **data
            )
        if not keep_production_authority:
            production_event_object_ids.discard(id(posted_event))
        pending_mention = {}
        return True, pending_mention, active_xai_context

    m = re.search(r"No usable reply generated for (mention|hot_post_reply) (\d+)", msg)
    if m:
        source = m.group(1)
        if source == "hot_post_reply":
            add_event(
                "hot_post_reply_grok_skip",
                r.ts,
                hot_post_reply_id=m.group(2),
                author_id=pending_mention.get("author_id"),
                incoming_text=pending_mention.get("incoming_text", ""),
            )
        else:
            add_event(
                "mention_grok_skip",
                r.ts,
                mention_id=m.group(2),
                author_id=pending_mention.get("author_id"),
                incoming_text=pending_mention.get("incoming_text", ""),
            )
        pending_mention = {}
        active_xai_context = None
        return True, pending_mention, active_xai_context

    m = re.search(r"Skipping (mention|hot_post_reply) (\d+): (.*)$", msg)
    if m:
        source, ident, reason = m.group(1), m.group(2), m.group(3).strip()
        if source == "hot_post_reply":
            if "already replied/skipped" in reason:
                routine_skip_counts["hot_post_reply_already_handled"] += 1
            else:
                add_event(
                    "hot_post_reply_skipped",
                    r.ts,
                    hot_post_reply_id=ident,
                    author_id=pending_mention.get("author_id"),
                    incoming_text=pending_mention.get("incoming_text", ""),
                    reason=reason,
                )
        else:
            add_event(
                "mention_skipped",
                r.ts,
                mention_id=ident,
                author_id=pending_mention.get("author_id"),
                incoming_text=pending_mention.get("incoming_text", ""),
                reason=reason,
            )
        pending_mention = {}
        active_xai_context = None
        return True, pending_mention, active_xai_context

    m = re.search(r"Skipping hot-post candidate (\d+): (.*)$", msg, re.S)
    if m:
        add_event("hot_post_reply_skipped", r.ts, hot_post_reply_id=m.group(1), reason=m.group(2).strip())
        return True, pending_mention, active_xai_context
    return False, pending_mention, active_xai_context


def handle_legacy_quote_reply(
    r: Record,
    msg: str,
    *,
    record_index: int,
    production_record: bool,
    pending_qt: Dict[str, Any],
    active_xai_context: Optional[Dict[str, Any]],
    last_created_post: Dict[str, Any],
    production_event_object_ids: set[int],
    routine_skip_counts: Counter,
    add_event: Callable[..., Dict[str, Any]],
    lit: Callable[[str], str],
) -> Tuple[bool, Dict[str, Any], Optional[Dict[str, Any]]]:
    """Observe quote replies with their existing lane-specific state and skip rules."""
    # Quote tweet lane.
    m = re.search(r"Considering quote tweet id=(\d+) author_id=([^\s]+) original_post_id=(\d+) text=(.*)$", msg, re.S)
    if m:
        pending_qt = {
            "quote_tweet_id": m.group(1),
            "author_id": m.group(2),
            "original_post_id": m.group(3),
            "incoming_text": lit(m.group(4)),
            "considered_at": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "considered_seq": record_index,
            "_identity_production": production_record,
        }
        return True, pending_qt, active_xai_context

    m = re.search(r"Generated reply to quote tweet (\d+): (.*)$", msg, re.S)
    if m:
        if pending_qt.get("quote_tweet_id") != m.group(1):
            pending_qt = {
                "quote_tweet_id": m.group(1),
                "_identity_production": production_record,
            }
        pending_qt["reply"] = lit(m.group(2))
        pending_qt["generated_at"] = r.ts.strftime("%Y-%m-%d %H:%M:%S")
        active_xai_context = None
        return True, pending_qt, active_xai_context

    m = re.search(r"Recorded and cached own quote-tweet auto-reply id=(\d+)", msg)
    if m and pending_qt:
        pending_qt["reply_post_id"] = m.group(1)
        pending_qt["_reply_post_id_production"] = production_record
        return True, pending_qt, active_xai_context

    if msg == "Quote-tweet reply posted successfully" and pending_qt:
        posted_data, keep_production_authority = _prepare_legacy_reply_success(
            pending_qt, last_created_post,
        )
        posted_event = add_event(
            "quote_tweet_reply_posted", r.ts, **posted_data
        )
        if not keep_production_authority:
            production_event_object_ids.discard(id(posted_event))
        pending_qt = {}
        return True, pending_qt, active_xai_context

    m = re.search(r"No usable reply generated for quote tweet (\d+)", msg)
    if m:
        add_event(
            "quote_tweet_grok_skip",
            r.ts,
            quote_tweet_id=m.group(1),
            author_id=pending_qt.get("author_id"),
            original_post_id=pending_qt.get("original_post_id"),
            incoming_text=pending_qt.get("incoming_text", ""),
        )
        pending_qt = {}
        active_xai_context = None
        return True, pending_qt, active_xai_context

    m = re.search(r"Skipping quote tweet (\d+): (.*)$", msg, re.S)
    if m:
        reason = m.group(2).strip()
        if reason == "already seen/replied/skipped":
            routine_skip_counts["quote_tweet_already_seen"] += 1
        elif "not a direct quote" in reason:
            routine_skip_counts["quote_tweet_not_direct"] += 1
            add_event("quote_tweet_skipped", r.ts, quote_tweet_id=m.group(1), reason=reason)
        elif "authored by own account" in reason:
            routine_skip_counts["quote_tweet_self_authored"] += 1
            add_event("quote_tweet_skipped", r.ts, quote_tweet_id=m.group(1), reason=reason)
        else:
            add_event("quote_tweet_skipped", r.ts, quote_tweet_id=m.group(1), reason=reason)
        pending_qt = {}
        active_xai_context = None
        return True, pending_qt, active_xai_context
    return False, pending_qt, active_xai_context
