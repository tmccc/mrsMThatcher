"""Parse and correlate passive conversational provider call, usage and errors.

The digest supplies the selected record, source-local pending/active state,
observation lists, statistics and current helpers, including shared cost/value
converters. Observation returns the active context and attempt index for the
coordinator's source switching and resume decisions. No I/O, clock sampling,
runtime access or publication authority belongs here. Later error/completion
observation returns the active context alone without clearing the attempt index.
"""
from __future__ import annotations

import ast
from collections import Counter
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from mrs_log_digest_contracts import SourceReference

from mrs_log_digest_records import Record


def xai_usage_stage_from_msg(msg: str) -> str:
    """Return the provider pipeline stage recorded on a usage line."""
    tested = re.match(
        r"^Tested reply stage=([^\s]+)\s+provider=(?:xAI|OpenAI)\s+usage=",
        msg,
    )
    if tested:
        return tested.group(1)
    match = re.match(r"^xAI reply stage=([^\s]+)\s+usage=", msg)
    if match:
        return match.group(1)
    if "xAI usage=" in msg:
        return "legacy_or_unavailable"
    return "unavailable"


def provider_usage_provider_from_msg(msg: str) -> str:
    """Return the provider named by a legacy or tested-pipeline usage line."""
    tested = re.match(
        r"^Tested reply stage=[^\s]+\s+provider=(xAI|OpenAI)\s+usage=",
        msg,
    )
    if tested:
        return tested.group(1)
    if msg.startswith("xAI reply stage=") or "xAI usage=" in msg:
        return "xAI"
    return "unavailable"


def parse_xai_call_start(msg: str) -> Optional[Dict[str, str]]:
    """Parse a structured provider call-start line."""
    tested = re.match(
        r"^Calling tested reply pipeline stage=([^\s]+)\s+"
        r"provider=(xAI|OpenAI)\s+model=([^\s]+)\s+"
        r"reasoning_effort=([^\s]+)",
        msg,
    )
    if tested:
        return {
            "stage": tested.group(1),
            "provider": tested.group(2),
            "model": tested.group(3),
            "reasoning_effort": tested.group(4),
        }
    match = re.match(
        r"^Calling AI-first reply stage=([^\s]+)\s+model=([^\s]+)",
        msg,
    )
    if not match:
        return None
    return {"stage": match.group(1), "model": match.group(2)}


def parse_xai_usage_from_msg(msg: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse legacy and AI-first xAI usage messages."""
    tested = re.match(
        r"^Tested reply stage=[^\s]+\s+provider=(?:xAI|OpenAI)\s+usage=(.+)$",
        msg,
    )
    marker = "xAI usage="
    if tested:
        raw = tested.group(1).strip()
    elif marker in msg:
        raw = msg.split(marker, 1)[1].strip()
    elif msg.startswith("xAI reply stage=") and " usage=" in msg:
        raw = msg.split(" usage=", 1)[1].strip()
    else:
        return None, None
    try:
        parsed = ast.literal_eval(raw)
    except Exception as exc:
        return None, f"could not parse xAI usage dictionary: {exc}"
    if not isinstance(parsed, dict):
        return None, f"xAI usage payload was {type(parsed).__name__}, not dict"
    return parsed, None


def xai_usage_context_from_pending(pending_mention: Dict[str, Any], pending_qt: Dict[str, Any]) -> Dict[str, Any]:
    """Return the xAI usage context from pending."""
    mention_seq = pending_mention.get("considered_seq", -1) if pending_mention else -1
    quote_seq = pending_qt.get("considered_seq", -1) if pending_qt else -1
    if pending_qt and quote_seq >= mention_seq:
        return {
            "lane": "quote-tweet",
            "context_id": pending_qt.get("quote_tweet_id", ""),
            "author_id": pending_qt.get("author_id", ""),
        }
    if pending_mention:
        source = str(pending_mention.get("source") or "mention")
        lane = "hot-post" if source == "hot_post_reply" else "mention"
        return {
            "lane": lane,
            "context_id": pending_mention.get("mention_id") or pending_mention.get("hot_post_reply_id") or "",
            "author_id": pending_mention.get("author_id", ""),
        }
    return {"lane": "unknown", "context_id": "", "author_id": ""}


def unknown_xai_usage_context() -> Dict[str, Any]:
    """Return the unknown xAI usage context."""
    return {"lane": "unknown", "context_id": "", "author_id": ""}


def normalise_active_xai_call_attempt(
    value: Any,
    *,
    normalise_reply_lane: Callable[[Any], str],
) -> Optional[Dict[str, Any]]:
    """Return safe resumable metadata for one provider call still awaiting usage."""
    if not isinstance(value, dict) or value.get("usage_observed") is True:
        return None
    stage = str(value.get("stage") or "").strip()
    model = str(value.get("model") or "").strip()
    if not stage or not model:
        return None
    result = {
        "time": str(value.get("time") or ""),
        "lane": normalise_reply_lane(value.get("lane")),
        "context_id": str(value.get("context_id") or ""),
        "author_id": str(value.get("author_id") or ""),
        "stage": stage,
        "model": model,
        "usage_observed": False,
    }
    if value.get("provider") in {"xAI", "OpenAI"}:
        result["provider"] = str(value["provider"])
    if value.get("reasoning_effort"):
        result["reasoning_effort"] = str(value["reasoning_effort"])
    return result


def _cache_input_metric(
    usage: Dict[str, Any],
    prompt_details: Dict[str, Any],
    input_details: Dict[str, Any],
    field_names: Tuple[str, ...],
    *,
    optional_int_usage_value: Callable[[Any], Optional[int]],
) -> Optional[int]:
    """Return one explicitly reported cache metric without inventing zero."""
    for container in (usage, prompt_details, input_details):
        for field in field_names:
            if field in container:
                return optional_int_usage_value(container.get(field))
    return None


def summarize_xai_usage_event(
    record: Record,
    usage: Dict[str, Any],
    context: Dict[str, Any],
    *,
    model: str = "",
    provider: str = "xAI",
    call_start_matched: bool = False,
    xai_usage_stage_from_msg: Callable[[str], str],
    _cache_input_metric: Callable[..., Optional[int]],
    int_usage_value: Callable[[Any], int],
    optional_int_usage_value: Callable[[Any], Optional[int]],
) -> Dict[str, Any]:
    """Summarise one legacy or tested-pipeline provider usage event."""
    prompt_details = usage.get("prompt_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    completion_details = usage.get("completion_tokens_details")
    if not isinstance(completion_details, dict):
        completion_details = {}
    input_details = usage.get("input_tokens_details")
    if not isinstance(input_details, dict):
        input_details = {}
    cache_read_input = _cache_input_metric(
        usage,
        prompt_details,
        input_details,
        ("cache_read_input_tokens", "cached_tokens"),
    )
    cache_creation_input = _cache_input_metric(
        usage,
        prompt_details,
        input_details,
        ("cache_creation_input_tokens", "cache_creation_tokens"),
    )
    cache_write_input = _cache_input_metric(
        usage,
        prompt_details,
        input_details,
        ("cache_write_input_tokens", "cache_write_tokens"),
    )
    return {
        "time": record.ts.strftime("%Y-%m-%d %H:%M:%S"),
        "lane": context.get("lane", "unknown"),
        "context_id": context.get("context_id", ""),
        "author_id": context.get("author_id", ""),
        "stage": xai_usage_stage_from_msg(record.msg),
        "provider": provider if provider in {"xAI", "OpenAI"} else "unavailable",
        "model": model,
        "call_start_matched": bool(call_start_matched),
        "prompt_tokens": int_usage_value(usage.get("prompt_tokens")),
        # Retain cached_tokens for JSON compatibility. Provider cached-token
        # usage is an input-cache read, not evidence of cache creation/writes.
        "cached_tokens": int_usage_value(cache_read_input),
        "cache_read_input_tokens": int_usage_value(cache_read_input),
        "cache_creation_input_tokens": cache_creation_input,
        "cache_write_input_tokens": cache_write_input,
        "image_tokens": int_usage_value(prompt_details.get("image_tokens")),
        "reasoning_tokens": int_usage_value(completion_details.get("reasoning_tokens")),
        "completion_tokens": int_usage_value(usage.get("completion_tokens")),
        "total_tokens": int_usage_value(usage.get("total_tokens")),
        "num_sources_used": int_usage_value(usage.get("num_sources_used")),
        "cost_in_usd_ticks": optional_int_usage_value(
            usage.get("cost_in_usd_ticks")
        ),
    }


def observe_provider_message(
    r: Record,
    msg: str,
    *,
    pending_mention: Dict[str, Any],
    pending_qt: Dict[str, Any],
    active_xai_context: Optional[Dict[str, Any]],
    active_xai_call_attempt_index: Optional[int],
    xai_call_attempts: List[Dict[str, Any]],
    xai_usage_events: List[Dict[str, Any]],
    xai_usage_parse_errors: List[Dict[str, Any]],
    stats: Counter[str],
    xai_usage_context_from_pending: Callable[..., Dict[str, Any]],
    parse_xai_call_start: Callable[[str], Optional[Dict[str, str]]],
    unknown_xai_usage_context: Callable[[], Dict[str, Any]],
    parse_xai_usage_from_msg: Callable[[str], Tuple[Optional[Dict[str, Any]], Optional[str]]],
    xai_usage_stage_from_msg: Callable[[str], str],
    provider_usage_provider_from_msg: Callable[[str], str],
    normalise_reply_lane: Callable[[Any], str],
    summarize_xai_usage_event: Callable[..., Dict[str, Any]],
    short: Callable[..., str],
) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    """Observe one selected message and return its updated active call state."""
    if r.src == "ask_grok_for_reply" and msg.startswith("Asking Grok for reply."):
        active_xai_context = xai_usage_context_from_pending(pending_mention, pending_qt)
    call_start = (
        parse_xai_call_start(msg)
        if r.src in {
            "xai_structured_reply_call",
            "tested_pipeline_structured_call",
        }
        else None
    )
    if call_start is not None:
        active_xai_context = xai_usage_context_from_pending(pending_mention, pending_qt)
        context = active_xai_context or unknown_xai_usage_context()
        attempt_row = {
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "lane": context.get("lane", "unknown"),
            "context_id": context.get("context_id", ""),
            "author_id": context.get("author_id", ""),
            "stage": call_start["stage"],
            "model": call_start["model"],
            "usage_observed": False,
        }
        if call_start.get("provider") in {"xAI", "OpenAI"}:
            attempt_row["provider"] = call_start["provider"]
        if call_start.get("reasoning_effort"):
            attempt_row["reasoning_effort"] = call_start["reasoning_effort"]
        xai_call_attempts.append(attempt_row)
        active_xai_call_attempt_index = len(xai_call_attempts) - 1

    usage, usage_error = parse_xai_usage_from_msg(msg)
    if usage is not None:
        model = ""
        call_start_matched = False
        usage_stage = xai_usage_stage_from_msg(msg)
        usage_provider = provider_usage_provider_from_msg(msg)
        if active_xai_call_attempt_index is not None:
            attempt = xai_call_attempts[active_xai_call_attempt_index]
            if (
                attempt.get("stage") == usage_stage
                and str(attempt.get("provider") or "xAI") == usage_provider
                and normalise_reply_lane(attempt.get("lane"))
                == normalise_reply_lane(
                    (active_xai_context or {}).get("lane")
                )
                and str(attempt.get("context_id") or "")
                == str(
                    (active_xai_context or {}).get("context_id") or ""
                )
            ):
                attempt["usage_observed"] = True
                attempt["usage_time"] = r.ts.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                model = str(attempt.get("model") or "")
                call_start_matched = True
                active_xai_call_attempt_index = None
        xai_usage_events.append(
            summarize_xai_usage_event(
                r,
                usage,
                active_xai_context or unknown_xai_usage_context(),
                model=model,
                provider=usage_provider,
                call_start_matched=call_start_matched,
            )
        )
        stats["provider_usage_successes"] += 1
        if usage_provider == "xAI":
            stats["xai_usage_successes"] += 1
        elif usage_provider == "OpenAI":
            stats["openai_usage_successes"] += 1
    elif usage_error is not None:
        xai_usage_parse_errors.append({
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "where": f"{r.src}:{r.line}",
            "message": short(msg, 500),
            "error": usage_error,
        })
        stats["xai_usage_parse_errors"] += 1
    return active_xai_context, active_xai_call_attempt_index


def observe_provider_error(
    r: Record,
    msg: str,
    *,
    active_xai_context: Optional[Dict[str, Any]],
    api_errors: List[Dict[str, Any]],
    stats: Counter[str],
    input_file_indexes: Optional[Dict[str, int]],
    short: Callable[..., str],
    record_source_ref: Callable[..., SourceReference],
) -> Optional[Dict[str, Any]]:
    """Observe provider errors/completion and return the current context."""
    if r.src in {"ask_grok_for_reply", "xai_request"} and msg.startswith("xAI error"):
        stats["xai_errors"] += 1
        m = re.search(r"xAI error (\d+):", msg)
        api_errors.append({
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "service": "xAI",
            "endpoint": "chat",
            "status": m.group(1) if m else "",
            "message": short(msg, 240),
            "source_refs": [record_source_ref(r, input_file_indexes)],
        })
        active_xai_context = None
    if r.src == "ask_grok_for_reply" and (
        msg.startswith("Grok generated usable reply:")
        or msg.startswith("Grok chose to skip")
    ):
        active_xai_context = None
    return active_xai_context
