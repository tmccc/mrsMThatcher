"""Project historical-context log events and summarise their emitted metadata.

The coordinator supplies parsed fields, invocation-local counters and event
insertion. Parsing, truncation, provenance and publication authority remain in
the digest; display metadata alone never confirms a publication. Imports and
quality summaries perform no I/O or runtime initialisation.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from mrs_log_digest_values import (
    SHA256_LOWER_RE,
    _count_optional,
    bounded_event_boolean,
    bounded_event_nonnegative_integer,
    bounded_event_text,
    valid_string_public_post_id,
)


_HISTORICAL_CONTEXT_VERIFICATION_LABELS = (
    # Formatter v4 public labels.
    "Exact wording verified",
    "Historically verified variant",
    "Verified excerpt",
    "Attributed, but exact wording not independently verified",
    "Exact wording not independently verified",
    "Research incomplete",
    # Audited internal-rendering label retained by formatter v4.
    "Normalised wording verified",
    # Source-role audit labels emitted by formatter v3 internal metadata.
    "Paraphrase; exact wording not verified",
    "Composite wording assembled from related material",
    "Historically misattributed; not Thatcher wording",
    "Reported wording; no primary Thatcher transcript located",
    "Secondary recollection; no primary Thatcher transcript located",
    "Wording partially supported by a retained source citation; exact wording not independently verified",
    "Historical variant not independently verified by the retained evidence",
    "Exact wording not independently verified by the retained evidence",
    # Frozen labels retained for older structured logs.
    "Exact wording",
    "Normalised wording",
    "Historical paraphrase",
    "Composite wording",
    "Commonly misattributed wording",
    "Exact wording not verified",
    "unavailable",
)
_HISTORICAL_CONTEXT_FORMATTER_VERSIONS = (
    "historical_context_reply_schema_v1",
    "historical_context_reply_schema_v2",
    "historical_context_reply_schema_v3",
    "historical_context_reply_schema_v4",
    "historical_context_reply_schema_v5",
    "unavailable",
)
_HISTORICAL_CONTEXT_SOURCE_ROLE_AUDIT_VERSIONS = (
    "historical-context-source-roles-v2-recovered-citations",
    "historical-context-source-roles-v3",
    "historical-context-source-roles-v4",
    "historical-context-source-roles-v5-independent-review-and-exclusive-counts",
    "historical-context-source-roles-v6-curated-evidence",
    "historical-context-source-roles-v7-curated-source-adjudications",
    "historical-context-source-roles-v8-claim-specific-public-context",
    "historical-context-source-roles-v9-archive-provenance",
    "unavailable",
)
_HISTORICAL_CONTEXT_CONFIDENCE_DIMENSIONS = (
    "attribution",
    "wording",
    "source_event",
    "date",
    "historical_context",
    "interpretation",
)
_HISTORICAL_CONTEXT_CONFIDENCE_VALUES = ("high", "medium", "low", "unknown", "unavailable")


def _historical_context_verification_counts(
    events: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Count controlled labels, grouping source-specific recollection wording."""
    counts = Counter({value: 0 for value in _HISTORICAL_CONTEXT_VERIFICATION_LABELS})
    for event in events:
        value = event.get("verification_label")
        if (
            isinstance(value, str)
            and value.startswith("Reported in ")
            and value.endswith("; no primary Thatcher transcript located")
        ):
            key = "Secondary recollection; no primary Thatcher transcript located"
        elif isinstance(value, str) and value in counts:
            key = value
        else:
            key = "unavailable"
        counts[key] += 1
    return dict(sorted(counts.items()))


def _historical_context_confidence_dimension_counts(
    events: List[Dict[str, Any]],
) -> Dict[str, Dict[str, int]]:
    """Count validated v3/v4 confidence dimensions without flattening them."""
    result: Dict[str, Dict[str, int]] = {}
    for field in _HISTORICAL_CONTEXT_CONFIDENCE_DIMENSIONS:
        counts = Counter({value: 0 for value in _HISTORICAL_CONTEXT_CONFIDENCE_VALUES})
        for event in events:
            dimensions = event.get("confidence_dimensions")
            value = dimensions.get(field) if isinstance(dimensions, dict) else None
            key = value if isinstance(value, str) and value in counts else "unavailable"
            counts[key] += 1
        result[field] = dict(sorted(counts.items()))
    return result


def historical_context_quality_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the historical context quality summary."""
    items = [event for event in events if event.get("kind") == "historical_context_reply"]
    statuses = Counter({key: 0 for key in ("completed", "already_completed", "failed", "skipped", "dry_run", "unavailable")})
    skip_reasons = Counter()
    for event in items:
        status = str(event.get("status") or "unavailable")
        if status.startswith("skipped"):
            statuses["skipped"] += 1
            skip_reasons[str(event.get("reason") or status)] += 1
        elif status in statuses:
            statuses[status] += 1
        else:
            statuses["unavailable"] += 1
    rendered_items = [event for event in items if event.get("status") in {"completed", "dry_run"}]
    weighted = [int(event["character_count"]) for event in rendered_items
                if type(event.get("character_count")) is int and event["character_count"] > 0]
    raw = [int(event["raw_character_count"]) for event in rendered_items
           if type(event.get("raw_character_count")) is int and event["raw_character_count"] > 0]
    attempted = sum(str(event.get("status") or "") in {"completed", "failed", "dry_run"} for event in items)
    rendering_context_counts = Counter({
        "concrete_event_or_date_context_included": 0,
        "date_only_qualified_context_included": 0,
        "context_omitted_no_useful_event_or_date": 0,
        "old_generic_fallback_used": 0,
        "rendering_metadata_unavailable": 0,
    })
    generic_fallback = (
        "Context — The surviving attribution does not establish an occasion, "
        "date or immediate historical issue."
    )
    for event in rendered_items:
        preview = str(event.get("reply_preview") or "")
        variant = str(event.get("template_variant") or "")
        if generic_fallback in preview:
            rendering_context_counts["old_generic_fallback_used"] += 1
        elif variant == "compact_generic_context_omitted" or (
            preview and not preview.startswith("Context —")
        ):
            rendering_context_counts["context_omitted_no_useful_event_or_date"] += 1
        elif preview.startswith("Context — The surviving record dates this wording to "):
            rendering_context_counts["date_only_qualified_context_included"] += 1
        elif preview.startswith("Context —"):
            rendering_context_counts["concrete_event_or_date_context_included"] += 1
        else:
            rendering_context_counts["rendering_metadata_unavailable"] += 1
    return {
        "attempted_count": attempted,
        "status_counts": dict(sorted(statuses.items())),
        "skip_reason_counts": dict(skip_reasons.most_common()),
        "verification_counts": _historical_context_verification_counts(rendered_items),
        "source_class_counts": _count_optional(rendered_items, "source_class", (
            "Margaret Thatcher Foundation", "Hansard", "original speech transcript",
            "Thatcher-authored publication", "contemporary interview", "official Conservative publication",
            "other authoritative source", "canonical locator only", "no public URL", "unavailable",
        )),
        "confidence_counts": _count_optional(rendered_items, "historical_confidence", ("high", "medium", "low", "unavailable")),
        "rendering_context_counts": dict(rendering_context_counts),
        "formatter_version_counts": _count_optional(
            rendered_items,
            "formatter_version",
            _HISTORICAL_CONTEXT_FORMATTER_VERSIONS,
        ),
        "rendering_mode_counts": _count_optional(
            rendered_items,
            "rendering_mode",
            ("public", "internal", "unavailable"),
        ),
        "source_role_audit_version_counts": _count_optional(
            rendered_items,
            "source_role_audit_version",
            _HISTORICAL_CONTEXT_SOURCE_ROLE_AUDIT_VERSIONS,
        ),
        "confidence_dimension_counts": _historical_context_confidence_dimension_counts(rendered_items),
        "average_raw_characters": (sum(raw) / len(raw)) if raw else None,
        "average_weighted_characters": (sum(weighted) / len(weighted)) if weighted else None,
        "raw_length_observation_count": len(raw),
        "raw_length_metadata_unavailable_count": len(rendered_items) - len(raw),
        "weighted_length_observation_count": len(weighted),
        "weighted_length_metadata_unavailable_count": len(rendered_items) - len(weighted),
        "minimum_weighted_characters": min(weighted) if weighted else None,
        "maximum_weighted_characters": max(weighted) if weighted else None,
        "shortened_count": sum(event.get("shortening_applied") is True for event in rendered_items),
        "meaning_omitted_count": sum(event.get("meaning_omitted") is True for event in rendered_items),
        "source_omitted_count": sum(event.get("source_omitted") is True for event in rendered_items),
        "verification_omitted_count": sum(event.get("verification_omitted") is True for event in rendered_items),
        "shortening_metadata_unavailable_count": sum(type(event.get("shortening_applied")) is not bool for event in rendered_items),
        "meaning_omitted_metadata_unavailable_count": sum(type(event.get("meaning_omitted")) is not bool for event in rendered_items),
        "source_omitted_metadata_unavailable_count": sum(type(event.get("source_omitted")) is not bool for event in rendered_items),
        "verification_omitted_metadata_unavailable_count": sum(type(event.get("verification_omitted")) is not bool for event in rendered_items),
        "omission_metadata_unavailable_count": sum(
            any(type(event.get(field)) is not bool for field in ("meaning_omitted", "source_omitted", "verification_omitted"))
            for event in rendered_items
        ),
        "metadata_unavailable_count": sum(event.get("verification_label") in (None, "", "unavailable") for event in rendered_items),
    }


def record_historical_context_semantic_gate(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
) -> None:
    """Project semantic gate fields and emit through the coordinator."""
    add_event(
        "historical_context_semantic_gate",
        ts,
        status=bounded_event_text(
            event_obj.get("status"),
            default="unavailable",
            max_characters=100,
        ),
        policy_version=bounded_event_text(
            event_obj.get("policy_version"),
            default="unavailable",
            max_characters=200,
        ),
        ledger_sha256=(
            event_obj.get("ledger_sha256")
            if isinstance(event_obj.get("ledger_sha256"), str)
            and SHA256_LOWER_RE.fullmatch(
                event_obj["ledger_sha256"]
            )
            else ""
        ),
        projection_sha256=(
            event_obj.get("projection_sha256")
            if isinstance(event_obj.get("projection_sha256"), str)
            and SHA256_LOWER_RE.fullmatch(
                event_obj["projection_sha256"]
            )
            else ""
        ),
        blocked_quote_count=bounded_event_nonnegative_integer(
            event_obj.get("blocked_quote_count"), maximum=1_000_000
        ),
        reason=bounded_event_text(
            event_obj.get("reason"),
            default="",
            max_characters=1000,
        ),
    )


def record_historical_context_runtime(
    event_obj: Dict[str, Any],
    ts: datetime,
    stats: Counter,
    *,
    add_event: Callable[..., Dict[str, Any]],
) -> None:
    """Project runtime fields and emit through the coordinator; update family counts."""
    status = bounded_event_text(
        event_obj.get("status"),
        default="unavailable",
        max_characters=100,
    )
    add_event(
        "historical_context_runtime",
        ts,
        status=status,
        reason=bounded_event_text(
            event_obj.get("reason"),
            default="",
            max_characters=1000,
        ),
        regular_post_eligibility_unchanged=bounded_event_boolean(
            event_obj.get("regular_post_eligibility_unchanged")
        ),
    )
    stats[f"historical_context_runtime_status_{status}"] += 1


def record_historical_context_obligation(
    event_obj: Dict[str, Any],
    ts: datetime,
    stats: Counter,
    *,
    add_event: Callable[..., Dict[str, Any]],
) -> None:
    """Project obligation fields and emit through the coordinator; update family counts."""
    context_state = bounded_event_text(
        event_obj.get("context_reply_state"),
        default="unavailable",
        max_characters=100,
    )
    status = bounded_event_text(
        event_obj.get("status"),
        default="unknown",
        max_characters=100,
    )
    add_event(
        "historical_context_obligation",
        ts,
        status=status,
        parent_post_id=(
            event_obj.get("parent_post_id")
            if valid_string_public_post_id(
                event_obj.get("parent_post_id")
            )
            else ""
        ),
        context_reply_state=context_state,
        attempt_number=bounded_event_nonnegative_integer(
            event_obj.get("attempt_number"), maximum=1_000_000
        ),
        remote_work_repeated=bounded_event_boolean(
            event_obj.get("remote_work_repeated")
        ),
        error_type=bounded_event_text(
            event_obj.get("error_type"),
            default="",
            max_characters=200,
        ),
        reason=bounded_event_text(
            event_obj.get("reason"),
            default="",
            max_characters=1000,
        ),
    )
    stats[f"context_obligation_state_{context_state}"] += 1
    stats[f"context_obligation_status_{status}"] += 1


def record_historical_context_outbox(
    event_obj: Dict[str, Any],
    ts: datetime,
    stats: Counter,
    *,
    add_event: Callable[..., Dict[str, Any]],
) -> None:
    """Project outbox fields and emit through the coordinator; update family counts."""
    status = bounded_event_text(
        event_obj.get("status"),
        default="unknown",
        max_characters=100,
    )
    add_event(
        "historical_context_outbox",
        ts,
        status=status,
        parent_post_id=(
            event_obj.get("parent_post_id")
            if valid_string_public_post_id(
                event_obj.get("parent_post_id")
            )
            else ""
        ),
        error_type=bounded_event_text(
            event_obj.get("error_type"),
            default="",
            max_characters=200,
        ),
        reason=bounded_event_text(
            event_obj.get("reason"),
            default="",
            max_characters=1000,
        ),
        main_post_success_preserved=bounded_event_boolean(
            event_obj.get("main_post_success_preserved")
        ),
        unrelated_lanes_available=bounded_event_boolean(
            event_obj.get("unrelated_lanes_available")
        ),
    )
    stats[f"historical_context_outbox_status_{status}"] += 1


def _reply_confidence_dimensions(value: Any) -> Optional[Dict[str, str]]:
    if (
        isinstance(value, dict)
        and set(value) == set(_HISTORICAL_CONTEXT_CONFIDENCE_DIMENSIONS)
        and all(
            type(item) is str and item in _HISTORICAL_CONTEXT_CONFIDENCE_VALUES
            for item in value.values()
        )
    ):
        return dict(value)
    return None


def _reply_rendering_fields(event_obj: Dict[str, Any]) -> Dict[str, Any]:
    character_count = bounded_event_nonnegative_integer(
        event_obj.get("character_count"), maximum=25_000
    )
    raw_character_count = bounded_event_nonnegative_integer(
        event_obj.get("raw_character_count"), maximum=25_000
    )
    confidence_dimensions = _reply_confidence_dimensions(
        event_obj.get("confidence_dimensions")
    )
    return {
        "character_count": character_count,
        "weighted_character_count": character_count,
        "raw_character_count": raw_character_count,
        "verification_label": bounded_event_text(
            event_obj.get("verification_label"),
            default="unavailable",
            max_characters=500,
        ),
        "source_class": bounded_event_text(
            event_obj.get("source_class"),
            default="unavailable",
            max_characters=500,
        ),
        "historical_confidence": (
            event_obj.get("historical_confidence")
            if event_obj.get("historical_confidence")
            in {"high", "medium", "low", "unavailable"}
            else "unavailable"
        ),
        "formatter_version": bounded_event_text(
            event_obj.get("formatter_version"),
            default="unavailable",
            max_characters=200,
        ),
        "rendering_mode": bounded_event_text(
            event_obj.get("rendering_mode"),
            default="unavailable",
            max_characters=100,
        ),
        "confidence_dimensions": confidence_dimensions,
        "source_role_audit_version": bounded_event_text(
            event_obj.get("source_role_audit_version"),
            default="unavailable",
            max_characters=200,
        ),
        "template_variant": bounded_event_text(
            event_obj.get("template_variant"),
            default="",
            max_characters=200,
        ),
        "shortening_applied": bounded_event_boolean(
            event_obj.get("shortening_applied")
        ),
        "meaning_omitted": bounded_event_boolean(
            event_obj.get("meaning_omitted")
        ),
        "source_omitted": bounded_event_boolean(
            event_obj.get("source_omitted")
        ),
        "verification_omitted": bounded_event_boolean(
            event_obj.get("verification_omitted")
        ),
    }


def _reply_semantic_fields(event_obj: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "semantic_review_disposition": bounded_event_text(
            event_obj.get("semantic_review_disposition"),
            default="unavailable",
            max_characters=200,
        ),
        "semantic_review_ledger_sha256": (
            event_obj.get("semantic_review_ledger_sha256")
            if isinstance(
                event_obj.get("semantic_review_ledger_sha256"),
                str,
            )
            and SHA256_LOWER_RE.fullmatch(
                event_obj["semantic_review_ledger_sha256"]
            )
            else "unavailable"
        ),
        "semantic_review_projection_sha256": (
            event_obj.get("semantic_review_projection_sha256")
            if isinstance(
                event_obj.get("semantic_review_projection_sha256"),
                str,
            )
            and SHA256_LOWER_RE.fullmatch(
                event_obj["semantic_review_projection_sha256"]
            )
            else "unavailable"
        ),
    }


def prepare_historical_context_reply(event_obj: Dict[str, Any]) -> Dict[str, Any]:
    """Return ordered display fields without deciding publication authority.

    The caller must emit through its usual insertion path, retain that returned
    object, and check the original canonical anchor before publication enrichment.
    Quality aggregation must consume the emitted, truncated fields.
    """
    return {
        "status": bounded_event_text(
            event_obj.get("status"), default="unknown", max_characters=100
        ),
        "parent_post_id": (
            event_obj.get("parent_post_id")
            if valid_string_public_post_id(
                event_obj.get("parent_post_id")
            )
            else None
        ),
        "quote_id": (
            event_obj.get("quote_id")
            if isinstance(event_obj.get("quote_id"), str)
            and SHA256_LOWER_RE.fullmatch(event_obj["quote_id"])
            else None
        ),
        **_reply_rendering_fields(event_obj),
        "reason": bounded_event_text(
            event_obj.get("reason"),
            default="",
            max_characters=1000,
        ),
        **_reply_semantic_fields(event_obj),
        "reply_preview": bounded_event_text(
            event_obj.get("reply_preview"),
            default="",
            max_characters=25_000,
        ),
    }


def count_historical_context_reply(status: str, stats: Counter) -> None:
    """Count the projected status once, after the coordinator checks the anchor.

    The status precedes generic event truncation, as for other family counters.
    """
    stats[f"historical_context_reply_status_{status}"] += 1
