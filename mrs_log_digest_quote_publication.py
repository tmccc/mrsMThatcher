"""Correlate prepared quote-publication and engagement-experiment evidence.

The coordinator supplies parsed payloads, timestamps, correlation maps, invalid
evidence, warning storage and current helper callbacks. Operations retain and
enrich the supplied evidence/events in place; publication authority, source
filtering and warning bounds retain the existing digest rules. No files, home,
configuration, clocks or providers are accessed by this module.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple


ENGAGEMENT_QUESTION_PUBLIC_TEXT_SEPARATOR = "\n\nQuestion — "
ENGAGEMENT_QUESTION_EXPERIMENT_ID = "substantive-question-v1"
ENGAGEMENT_QUESTION_EXPERIMENT_STATE_SCHEMA_VERSION = 1
ENGAGEMENT_QUESTION_EXPERIMENT_STATUSES = {
    "not_started",
    "active",
    "paused",
    "completed",
    "invalid",
}
ENGAGEMENT_PAIR_ID_RE = re.compile(r"pair-[0-9a-f]{24}\Z")
ENGAGEMENT_PUBLICATION_ORDERS = {"control_first", "treatment_first"}
ENGAGEMENT_ARMS = {"control", "treatment"}
ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS = 60


def valid_account_root_publication_identity(
    event: Any,
    *,
    valid_string_public_post_id: Callable[[Any], bool],
) -> bool:
    """Return whether an account-root event has the producer's core contract."""

    if not isinstance(event, dict):
        return False
    post_id = event.get("post_id")
    return bool(
        event.get("event") == "account_root_posted"
        and type(event.get("event_version")) is int
        and event.get("event_version") == 1
        and type(event.get("lane")) is str
        and event.get("lane") in {"quote_image", "daily_meme"}
        and valid_string_public_post_id(post_id)
        and event.get("root_post_id") == post_id
        and event.get("conversation_id") == post_id
        and event.get("publication_authority") == "confirmed_transport"
    )


def valid_engagement_confirmation_event(
    event: Any,
    *,
    valid_string_public_post_id: Callable[[Any], bool],
    SHA256_LOWER_RE: re.Pattern[str],
    ENGAGEMENT_PAIR_ID_RE: re.Pattern[str],
    ENGAGEMENT_ARMS: set[str],
    ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS: int,
) -> bool:
    """Return whether a trial confirmation matches its producer schema."""

    return bool(
        isinstance(event, dict)
        and event.get("event")
        == "engagement_question_experimental_member_confirmed"
        and valid_string_public_post_id(event.get("post_id"))
        and isinstance(event.get("plan_sha256"), str)
        and SHA256_LOWER_RE.fullmatch(event["plan_sha256"]) is not None
        and isinstance(event.get("pair_id"), str)
        and ENGAGEMENT_PAIR_ID_RE.fullmatch(event["pair_id"]) is not None
        and type(event.get("member_position")) is int
        and event.get("member_position") in {1, 2}
        and type(event.get("arm")) is str
        and event.get("arm") in ENGAGEMENT_ARMS
        and type(event.get("publication_sequence")) is int
        and 1
        <= event.get("publication_sequence")
        <= ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS
    )


def engagement_main_metadata_status(
    event: Any,
    *,
    SHA256_LOWER_RE: re.Pattern[str],
    ENGAGEMENT_QUESTION_EXPERIMENT_ID: str,
    ENGAGEMENT_PAIR_ID_RE: re.Pattern[str],
    ENGAGEMENT_ARMS: set[str],
    ENGAGEMENT_PUBLICATION_ORDERS: set[str],
    ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS: int,
) -> str:
    """Return absent, valid, or invalid for main-post experiment metadata."""

    fields = (
        "engagement_experiment_id",
        "engagement_experiment_plan_sha256",
        "engagement_experiment_pair_id",
        "engagement_experiment_arm",
        "engagement_experiment_member_position",
        "engagement_experiment_publication_order",
        "engagement_experiment_sequence",
        "engagement_question_present",
        "engagement_approved_question_sha256",
        "engagement_public_text_sha256",
    )
    if not isinstance(event, dict) or not any(field in event for field in fields):
        return "absent"
    arm = event.get("engagement_experiment_arm")
    valid = bool(
        event.get("engagement_experiment_id")
        == ENGAGEMENT_QUESTION_EXPERIMENT_ID
        and isinstance(event.get("engagement_experiment_plan_sha256"), str)
        and SHA256_LOWER_RE.fullmatch(
            event["engagement_experiment_plan_sha256"]
        )
        is not None
        and isinstance(event.get("engagement_experiment_pair_id"), str)
        and ENGAGEMENT_PAIR_ID_RE.fullmatch(
            event["engagement_experiment_pair_id"]
        )
        is not None
        and type(event.get("engagement_experiment_member_position")) is int
        and event.get("engagement_experiment_member_position") in {1, 2}
        and type(arm) is str
        and arm in ENGAGEMENT_ARMS
        and type(event.get("engagement_experiment_publication_order")) is str
        and event.get("engagement_experiment_publication_order")
        in ENGAGEMENT_PUBLICATION_ORDERS
        and type(event.get("engagement_experiment_sequence")) is int
        and 1
        <= event.get("engagement_experiment_sequence")
        <= ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS
        and type(event.get("engagement_question_present")) is bool
        and event.get("engagement_question_present")
        is (arm == "treatment")
        and isinstance(
            event.get("engagement_approved_question_sha256"), str
        )
        and SHA256_LOWER_RE.fullmatch(
            event["engagement_approved_question_sha256"]
        )
        is not None
        and isinstance(event.get("engagement_public_text_sha256"), str)
        and SHA256_LOWER_RE.fullmatch(
            event["engagement_public_text_sha256"]
        )
        is not None
    )
    return "valid" if valid else "invalid"


def add_engagement_correlation_warning(
    *,
    engagement_correlation_warnings: List[Dict[str, Any]],
    engagement_correlation_warning_keys: set[Tuple[str, str, str, str, str]],
    engagement_correlation_warning_counts: Counter[str],
    engagement_correlation_warning_omitted_count: int,
    ENGAGEMENT_CORRELATION_WARNING_LIMIT: int,
    time_text: str,
    post_id: str,
    field: str,
    left_event: str,
    right_event: str,
    status: str = "conflict",
) -> int:
    """Deduplicate and bound warnings in place, returning the omission count."""
    event_pair = tuple(sorted((left_event, right_event)))
    key = (post_id, field, event_pair[0], event_pair[1], status)
    if key in engagement_correlation_warning_keys:
        return engagement_correlation_warning_omitted_count
    engagement_correlation_warning_keys.add(key)
    engagement_correlation_warning_counts[post_id] += 1
    if (
        len(engagement_correlation_warnings)
        >= ENGAGEMENT_CORRELATION_WARNING_LIMIT
    ):
        engagement_correlation_warning_omitted_count += 1
        return engagement_correlation_warning_omitted_count
    engagement_correlation_warnings.append(
        {
            "time": time_text,
            "post_id": post_id,
            "field": field,
            "event_types": list(event_pair),
            "status": status,
            "message": (
                f"post_id={post_id} field={field} {status} between "
                f"{event_pair[0]} and {event_pair[1]}"
            ),
        }
    )
    return engagement_correlation_warning_omitted_count


def retain_quote_post_evidence(
    post_id: str,
    event_type: str,
    payload: Dict[str, Any],
    *,
    quote_post_correlations: Dict[str, Dict[str, Dict[str, Any]]],
    source_is_selftest: Callable[[], bool],
    bounded_source_refs: Callable[..., Tuple[List[Dict[str, Any]], int]],
    add_engagement_correlation_warning: Callable[..., None],
) -> None:
    """Keep one fixed-shape evidence slot per structured event and post."""
    if source_is_selftest():
        return
    slots = quote_post_correlations.setdefault(post_id, {})
    existing = slots.get(event_type)
    if existing is None:
        payload["_conflicted_fields"] = set()
        slots[event_type] = payload
        return
    time_text = str(payload.get("time") or existing.get("time") or "")
    conflicted_fields = existing.setdefault("_conflicted_fields", set())
    for field, value in payload.items():
        if field in {"event", "time"} or value is None or value == "":
            continue
        if field == "source_refs":
            references, omitted = bounded_source_refs(
                existing.get("source_refs"), value
            )
            existing["source_refs"] = references
            prior_omitted = existing.get("source_ref_omitted_count")
            cumulative_omitted = (
                prior_omitted
                if type(prior_omitted) is int and prior_omitted >= 0
                else 0
            ) + omitted
            if cumulative_omitted:
                existing["source_ref_omitted_count"] = (
                    cumulative_omitted
                )
            continue
        if field in conflicted_fields:
            continue
        old_value = existing.get(field)
        if old_value is None or old_value == "":
            existing[field] = value
        elif old_value != value:
            conflicted_fields.add(field)
            existing[field] = None
            add_engagement_correlation_warning(
                time_text=time_text,
                post_id=post_id,
                field=field,
                left_event=event_type,
                right_event=event_type,
            )


def note_invalid_quote_post_evidence(
    post_id: Any,
    event_type: str,
    time_text: str,
    *,
    invalid_quote_post_evidence: Dict[str, set[str]],
    valid_string_public_post_id: Callable[[Any], bool],
    source_is_selftest: Callable[[], bool],
    add_engagement_correlation_warning: Callable[..., None],
) -> None:
    """Record malformed observability without letting it replace authority."""

    if (
        not valid_string_public_post_id(post_id)
        or source_is_selftest()
    ):
        return
    invalid_quote_post_evidence.setdefault(post_id, set()).add(event_type)
    add_engagement_correlation_warning(
        time_text=time_text,
        post_id=post_id,
        field="producer_schema",
        left_event=event_type,
        right_event="required_contract",
        status="invalid",
    )


def record_main_post_publication(
    event_obj: Dict[str, Any],
    strict_structured_event_obj: Optional[Dict[str, Any]],
    ts: datetime,
    *,
    valid_string_public_post_id: Callable[[Any], bool],
    SHA256_LOWER_RE: re.Pattern[str],
    engagement_main_metadata_status: Callable[[Any], str],
    retain_quote_post_evidence: Callable[..., None],
    note_invalid_quote_post_evidence: Callable[..., None],
    make_source_ref: Callable[[], Dict[str, Any]],
) -> None:
    """Prepare and retain main-post evidence; the caller updates pending meme state."""
    authority_event_obj = (
        strict_structured_event_obj
        if strict_structured_event_obj
        and strict_structured_event_obj.get("event")
        == "main_post_posted"
        else None
    )
    raw_post_id = event_obj.get("post_id")
    main_core_valid = bool(
        authority_event_obj is not None
        and valid_string_public_post_id(
            authority_event_obj.get("post_id")
        )
        and type(authority_event_obj.get("lane")) is str
        and authority_event_obj.get("lane")
        in {"quote_image", "daily_meme"}
    )
    if main_core_valid:
        raw_post_id = authority_event_obj.get("post_id")
        post_id = raw_post_id
        payload: Dict[str, Any] = {
            "event": "main_post_posted",
            "time": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "post_id": post_id,
            "lane": authority_event_obj.get("lane"),
            "source_refs": [
                make_source_ref()
            ],
        }
        for key in ("line_no", "image_no"):
            value = authority_event_obj.get(key)
            if type(value) is int and value >= 0:
                payload[key] = value
        for key in ("image_basename",):
            value = authority_event_obj.get(key)
            if isinstance(value, str) and len(value) <= 500:
                payload[key] = value
        for key in ("image_hash", "quote_hash"):
            value = authority_event_obj.get(key)
            if (
                isinstance(value, str)
                and SHA256_LOWER_RE.fullmatch(value) is not None
            ):
                payload[key] = value
        image_score = authority_event_obj.get("image_score")
        if (
            (
                type(image_score) is int
                and abs(image_score) <= 1_000_000_000
            )
            or (
                type(image_score) is float
                and math.isfinite(image_score)
                and abs(image_score) <= 1_000_000_000
            )
        ):
            payload["image_score"] = image_score
        experiment_status = engagement_main_metadata_status(
            authority_event_obj
        )
        if experiment_status == "valid":
            payload.update(
                {
                    key: authority_event_obj.get(key)
                    for key in (
                        "engagement_experiment_id",
                        "engagement_experiment_plan_sha256",
                        "engagement_experiment_pair_id",
                        "engagement_experiment_arm",
                        "engagement_experiment_member_position",
                        "engagement_experiment_publication_order",
                        "engagement_experiment_sequence",
                        "engagement_question_present",
                        "engagement_approved_question_sha256",
                        "engagement_public_text_sha256",
                    )
                }
            )
        elif experiment_status == "invalid":
            note_invalid_quote_post_evidence(
                post_id,
                "main_post_posted.engagement_metadata",
                ts.strftime("%Y-%m-%d %H:%M:%S"),
            )
        retain_quote_post_evidence(
            post_id,
            "main_post_posted",
            payload,
        )
    else:
        note_invalid_quote_post_evidence(
            raw_post_id,
            "main_post_posted",
            ts.strftime("%Y-%m-%d %H:%M:%S"),
        )


def record_account_root_publication(
    event_obj: Dict[str, Any],
    strict_structured_event_obj: Optional[Dict[str, Any]],
    ts: datetime,
    *,
    valid_account_root_publication_identity: Callable[[Any], bool],
    SHA256_LOWER_RE: re.Pattern[str],
    valid_bounded_utf8_text: Callable[..., bool],
    retain_quote_post_evidence: Callable[..., None],
    note_invalid_quote_post_evidence: Callable[..., None],
    make_source_ref: Callable[[], Dict[str, Any]],
) -> None:
    """Prepare account-root text and identity without replacing invalid evidence."""
    authority_event_obj = (
        strict_structured_event_obj
        if strict_structured_event_obj
        and strict_structured_event_obj.get("event")
        == "account_root_posted"
        else None
    )
    raw_post_id = event_obj.get("post_id")
    if valid_account_root_publication_identity(
        authority_event_obj
    ):
        raw_post_id = authority_event_obj.get("post_id")
        post_id = raw_post_id
        payload = {
            "event": "account_root_posted",
            "time": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "post_id": post_id,
            "event_version": authority_event_obj.get(
                "event_version"
            ),
            "root_post_id": authority_event_obj.get(
                "root_post_id"
            ),
            "conversation_id": authority_event_obj.get(
                "conversation_id"
            ),
            "lane": authority_event_obj.get("lane"),
            "publication_authority": authority_event_obj.get(
                "publication_authority"
            ),
            "source_refs": [
                make_source_ref()
            ],
        }
        optional_fields_valid = True
        quote_id = authority_event_obj.get("quote_id")
        if quote_id is None:
            payload["quote_id"] = None
        elif (
            isinstance(quote_id, str)
            and SHA256_LOWER_RE.fullmatch(quote_id) is not None
        ):
            payload["quote_id"] = quote_id
        else:
            optional_fields_valid = False
        for key in ("quote_text", "public_text"):
            value = authority_event_obj.get(key)
            if value is None:
                payload[key] = None
            elif valid_bounded_utf8_text(
                value,
                allow_empty=True,
            ):
                payload[key] = value
            else:
                optional_fields_valid = False
        visible_source = authority_event_obj.get(
            "visible_text_source"
        )
        if (
            visible_source is None
            or (
                isinstance(visible_source, str)
                and len(visible_source) <= 100
            )
        ):
            payload["visible_text_source"] = visible_source
        else:
            optional_fields_valid = False
        if not optional_fields_valid:
            for key in (
                "quote_id",
                "quote_text",
                "public_text",
                "visible_text_source",
            ):
                payload.pop(key, None)
            note_invalid_quote_post_evidence(
                post_id,
                "account_root_posted.optional_fields",
                ts.strftime("%Y-%m-%d %H:%M:%S"),
            )
        retain_quote_post_evidence(
            post_id,
            "account_root_posted",
            payload,
        )
    else:
        note_invalid_quote_post_evidence(
            raw_post_id,
            "account_root_posted",
            ts.strftime("%Y-%m-%d %H:%M:%S"),
        )


def record_engagement_confirmation(
    event_obj: Dict[str, Any],
    strict_structured_event_obj: Optional[Dict[str, Any]],
    ts: datetime,
    *,
    valid_engagement_confirmation_event: Callable[[Any], bool],
    retain_quote_post_evidence: Callable[..., None],
    note_invalid_quote_post_evidence: Callable[..., None],
    make_source_ref: Callable[[], Dict[str, Any]],
) -> None:
    """Retain a prepared experimental-member confirmation under its public post ID."""
    authority_event_obj = (
        strict_structured_event_obj
        if strict_structured_event_obj
        and strict_structured_event_obj.get("event")
        == "engagement_question_experimental_member_confirmed"
        else None
    )
    raw_post_id = (
        authority_event_obj.get("post_id")
        if authority_event_obj is not None
        else None
    )
    if valid_engagement_confirmation_event(authority_event_obj):
        post_id = raw_post_id
        retain_quote_post_evidence(
            post_id,
            "engagement_question_experimental_member_confirmed",
            {
                "event": (
                    "engagement_question_experimental_member_confirmed"
                ),
                "time": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "post_id": post_id,
                "source_refs": [
                    make_source_ref()
                ],
                **{
                    key: authority_event_obj.get(key)
                    for key in (
                        "plan_sha256",
                        "pair_id",
                        "member_position",
                        "arm",
                        "publication_sequence",
                    )
                },
            },
        )
    else:
        note_invalid_quote_post_evidence(
            event_obj.get("post_id"),
            "engagement_question_experimental_member_confirmed",
            ts.strftime("%Y-%m-%d %H:%M:%S"),
        )


def record_engagement_trial_outcome(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    engagement_trial_outcomes: List[Dict[str, Any]],
    bounded_event_text: Callable[..., Any],
    SHA256_LOWER_RE: re.Pattern[str],
    valid_string_public_post_id: Callable[[Any], bool],
    ENGAGEMENT_PAIR_ID_RE: re.Pattern[str],
    bounded_event_nonnegative_integer: Callable[..., Any],
    bounded_event_boolean: Callable[[Any], Any],
    make_source_ref: Callable[[], Dict[str, Any]],
) -> None:
    """Append an invalid, deferred or notification-failed trial observation."""
    event_name = str(event_obj["event"])
    outcome = {
        "time": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "event": event_name,
        "source_refs": [
            make_source_ref()
        ],
        "experiment_id": bounded_event_text(
            event_obj.get("experiment_id"), max_characters=200
        ),
        "plan_sha256": (
            event_obj.get("plan_sha256")
            if isinstance(event_obj.get("plan_sha256"), str)
            and SHA256_LOWER_RE.fullmatch(
                event_obj["plan_sha256"]
            )
            else None
        ),
        "post_id": (
            event_obj.get("post_id")
            if valid_string_public_post_id(
                event_obj.get("post_id")
            )
            else None
        ),
        "pair_id": (
            event_obj.get("pair_id")
            if isinstance(event_obj.get("pair_id"), str)
            and ENGAGEMENT_PAIR_ID_RE.fullmatch(
                event_obj["pair_id"]
            )
            else None
        ),
        "member_position": bounded_event_nonnegative_integer(
            event_obj.get("member_position"), maximum=2
        ),
        "reason": bounded_event_text(
            event_obj.get("reason"), max_characters=240
        ),
        "started": bounded_event_boolean(
            event_obj.get("started")
        ),
        "exception_class": bounded_event_text(
            event_obj.get("exception_class"), max_characters=200
        ),
        "authority_component": bounded_event_text(
            event_obj.get("authority_component"),
            max_characters=200,
        ),
    }
    engagement_trial_outcomes.append(outcome)


def correlated_quote_post_fields(
    post_id: str,
    *,
    quote_post_correlations: Dict[str, Dict[str, Dict[str, Any]]],
    invalid_quote_post_evidence: Dict[str, set[str]],
    engagement_correlation_warning_counts: Counter[str],
    add_engagement_correlation_warning: Callable[..., None],
    valid_bounded_utf8_text: Callable[..., bool],
    bounded_source_refs: Callable[..., Tuple[List[Dict[str, Any]], int]],
    ENGAGEMENT_QUESTION_PUBLIC_TEXT_SEPARATOR: str,
    legacy: Optional[Dict[str, Any]] = None,
    warning_time: str = "",
) -> Dict[str, Any]:
    """Resolve fixed structured evidence for one immutable post identity."""
    slots = quote_post_correlations.get(post_id) or {}
    invalid_evidence = invalid_quote_post_evidence.get(post_id) or set()
    main_event = slots.get("main_post_posted") or {}
    root_event = slots.get("account_root_posted") or {}
    confirmation = slots.get(
        "engagement_question_experimental_member_confirmed"
    ) or {}
    legacy = legacy or {}
    effective_time = str(
        warning_time
        or confirmation.get("time")
        or root_event.get("time")
        or main_event.get("time")
        or ""
    )
    conflicted_evidence = object()

    def evidence_value(event: Dict[str, Any], field: str) -> Any:
        if field in (event.get("_conflicted_fields") or set()):
            return conflicted_evidence
        return event.get(field)

    def present(value: Any) -> bool:
        return value is not None and value != ""

    def resolve(
        field: str,
        candidates: List[Tuple[str, Any]],
    ) -> Any:
        if any(value is conflicted_evidence for _, value in candidates):
            return None
        available = [
            (event_type, value)
            for event_type, value in candidates
            if present(value)
        ]
        if not available:
            return None
        first_event, first_value = available[0]
        for event_type, value in available[1:]:
            if value != first_value:
                add_engagement_correlation_warning(
                    time_text=effective_time,
                    post_id=post_id,
                    field=field,
                    left_event=first_event,
                    right_event=event_type,
                )
                return None
        return first_value

    main_or_confirmation_type = (
        "main_post_posted" if main_event else
        "engagement_question_experimental_member_confirmed"
    )
    main_or_confirmation_post_id = (
        evidence_value(main_event, "post_id")
        if main_event
        else evidence_value(confirmation, "post_id")
    )
    resolved_post_id = resolve(
        "post_id",
        [
            (main_or_confirmation_type, main_or_confirmation_post_id),
            (
                "account_root_posted",
                evidence_value(root_event, "root_post_id"),
            ),
        ],
    )
    lane = resolve(
        "lane",
        [
            ("main_post_posted", evidence_value(main_event, "lane")),
            ("account_root_posted", evidence_value(root_event, "lane")),
        ],
    )
    quote_hash = resolve(
        "quote_hash",
        [
            (
                "main_post_posted",
                evidence_value(main_event, "quote_hash"),
            ),
            (
                "account_root_posted",
                evidence_value(root_event, "quote_id"),
            ),
        ],
    )
    if quote_hash is None and not any(
        present(value)
        for value in (
            evidence_value(main_event, "quote_hash"),
            evidence_value(root_event, "quote_id"),
        )
    ):
        quote_hash = legacy.get("quote_hash")

    public_text = evidence_value(root_event, "public_text")
    quote_text = evidence_value(root_event, "quote_text")
    experimental_evidence = bool(
        confirmation
        or any("engagement" in item for item in invalid_evidence)
        or any(
            present(evidence_value(main_event, key))
            for key in (
                "engagement_experiment_id",
                "engagement_experiment_plan_sha256",
                "engagement_experiment_pair_id",
                "engagement_experiment_arm",
                "engagement_question_present",
            )
        )
    )
    account_root_authoritative = bool(
        root_event
        and type(root_event.get("event_version")) is int
        and root_event.get("event_version") == 1
        and root_event.get("post_id") == post_id
        and root_event.get("root_post_id") == post_id
        and root_event.get("conversation_id") == post_id
        and type(root_event.get("lane")) is str
        and root_event.get("lane") == "quote_image"
        and root_event.get("publication_authority")
        == "confirmed_transport"
    )
    account_root_usable = bool(
        account_root_authoritative
        and lane == "quote_image"
        and resolved_post_id == post_id
        and valid_bounded_utf8_text(public_text)
        and (
            quote_text is None
            or valid_bounded_utf8_text(
                quote_text,
                allow_empty=True,
            )
        )
    )
    public_text_sha256 = ""
    resolved_public_text_sha256: Optional[str] = None
    if account_root_usable:
        if not isinstance(quote_text, str):
            quote_text = ""
        public_text_sha256 = hashlib.sha256(
            public_text.encode("utf-8")
        ).hexdigest()
        resolved_public_text_sha256 = resolve(
            "public_text_sha256",
            [
                (
                    "main_post_posted",
                    evidence_value(
                        main_event, "engagement_public_text_sha256"
                    ),
                ),
                ("account_root_posted", public_text_sha256),
            ],
        )
    else:
        public_text = ""
        quote_text = ""
    public_text_status = (
        "confirmed"
        if account_root_usable
        else "unavailable_inconsistent"
        if root_event or invalid_evidence or experimental_evidence
        else ""
    )

    engagement_experiment_id = resolve(
        "engagement_experiment_id",
        [
            (
                "main_post_posted",
                evidence_value(main_event, "engagement_experiment_id"),
            ),
        ],
    )
    engagement_plan_sha256 = resolve(
        "engagement_plan_sha256",
        [
            (
                "main_post_posted",
                evidence_value(
                    main_event, "engagement_experiment_plan_sha256"
                ),
            ),
            (
                "engagement_question_experimental_member_confirmed",
                evidence_value(confirmation, "plan_sha256"),
            ),
        ],
    )
    engagement_pair_id = resolve(
        "engagement_pair_id",
        [
            (
                "main_post_posted",
                evidence_value(
                    main_event, "engagement_experiment_pair_id"
                ),
            ),
            (
                "engagement_question_experimental_member_confirmed",
                evidence_value(confirmation, "pair_id"),
            ),
        ],
    )
    engagement_member_position = resolve(
        "engagement_member_position",
        [
            (
                "main_post_posted",
                evidence_value(
                    main_event, "engagement_experiment_member_position"
                ),
            ),
            (
                "engagement_question_experimental_member_confirmed",
                evidence_value(confirmation, "member_position"),
            ),
        ],
    )
    engagement_arm = resolve(
        "engagement_arm",
        [
            (
                "main_post_posted",
                evidence_value(main_event, "engagement_experiment_arm"),
            ),
            (
                "engagement_question_experimental_member_confirmed",
                evidence_value(confirmation, "arm"),
            ),
        ],
    )
    engagement_publication_sequence = resolve(
        "engagement_publication_sequence",
        [
            (
                "main_post_posted",
                evidence_value(
                    main_event, "engagement_experiment_sequence"
                ),
            ),
            (
                "engagement_question_experimental_member_confirmed",
                evidence_value(confirmation, "publication_sequence"),
            ),
        ],
    )
    engagement_publication_order = resolve(
        "engagement_publication_order",
        [
            (
                "main_post_posted",
                evidence_value(
                    main_event, "engagement_experiment_publication_order"
                ),
            ),
        ],
    )
    engagement_question_present = resolve(
        "engagement_question_present",
        [
            (
                "main_post_posted",
                evidence_value(main_event, "engagement_question_present"),
            ),
        ],
    )
    engagement_approved_question_sha256 = resolve(
        "engagement_approved_question_sha256",
        [
            (
                "main_post_posted",
                evidence_value(
                    main_event,
                    "engagement_approved_question_sha256",
                ),
            ),
        ],
    )

    engagement_question_text: Any = ""
    engagement_question_text_status = ""
    if engagement_question_present is True:
        prefix = quote_text + ENGAGEMENT_QUESTION_PUBLIC_TEXT_SEPARATOR
        candidate_question = (
            public_text[len(prefix):]
            if account_root_usable
            and bool(quote_text)
            and public_text.startswith(prefix)
            and len(public_text) > len(prefix)
            else None
        )
        candidate_question_sha256 = (
            hashlib.sha256(
                candidate_question.encode("utf-8")
            ).hexdigest()
            if isinstance(candidate_question, str)
            else None
        )
        if (
            engagement_arm == "treatment"
            and candidate_question is not None
            and resolved_public_text_sha256 == public_text_sha256
            and engagement_approved_question_sha256
            == candidate_question_sha256
        ):
            engagement_question_text = candidate_question
            engagement_question_text_status = "validated"
        else:
            engagement_question_text = None
            engagement_question_text_status = "unavailable_inconsistent"
            add_engagement_correlation_warning(
                time_text=effective_time,
                post_id=post_id,
                field="engagement_question_text",
                left_event="main_post_posted",
                right_event="account_root_posted",
                status=("conflict" if root_event else "unavailable"),
            )
    elif engagement_question_present is False:
        engagement_question_text_status = "not_present"
        if account_root_usable and public_text != quote_text:
            engagement_question_text_status = "unavailable_inconsistent"
            add_engagement_correlation_warning(
                time_text=effective_time,
                post_id=post_id,
                field="engagement_question_text",
                left_event="main_post_posted",
                right_event="account_root_posted",
            )

    selection_fields = {
        key: (
            None
            if evidence_value(main_event, key) is conflicted_evidence
            else evidence_value(main_event, key)
            if present(evidence_value(main_event, key))
            else legacy.get(key)
        )
        for key in (
            "line_no",
            "image_no",
            "image_basename",
            "image_score",
        )
    }
    post_warning_count = engagement_correlation_warning_counts[post_id]
    source_refs, source_ref_omitted = bounded_source_refs(
        main_event.get("source_refs"),
        root_event.get("source_refs"),
        confirmation.get("source_refs"),
    )
    source_ref_omitted += sum(
        omitted
        for item in (main_event, root_event, confirmation)
        for omitted in [item.get("source_ref_omitted_count")]
        if type(omitted) is int and omitted >= 0
    )
    result = {
        **selection_fields,
        "quote_hash": quote_hash,
        "engagement_experiment_id": engagement_experiment_id or "",
        "engagement_plan_sha256": engagement_plan_sha256 or "",
        "engagement_pair_id": engagement_pair_id or "",
        "engagement_member_position": engagement_member_position,
        "engagement_arm": engagement_arm or "",
        "engagement_publication_order": engagement_publication_order or "",
        "engagement_publication_sequence": engagement_publication_sequence,
        "engagement_question_present": engagement_question_present,
        "engagement_approved_question_sha256": (
            engagement_approved_question_sha256 or ""
        ),
        "engagement_public_text_sha256": (
            resolved_public_text_sha256 or ""
        ),
        "engagement_question_text": engagement_question_text,
        "engagement_question_text_status": engagement_question_text_status,
        "engagement_question_display": (
            "unavailable/inconsistent"
            if engagement_question_present is True
            and engagement_question_text_status == "unavailable_inconsistent"
            else engagement_question_text or ""
        ),
        "quote_text": quote_text,
        "public_text": public_text,
        "public_text_status": public_text_status,
        "correlation_status": (
            "inconsistent" if post_warning_count else "consistent"
        ),
        "correlation_warning_count": post_warning_count,
    }
    if source_refs:
        result["source_refs"] = source_refs
    if source_ref_omitted:
        result["source_ref_omitted_count"] = source_ref_omitted
    return result


def prepare_quote_publication_report(
    events: List[Dict[str, Any]],
    production_event_object_ids: set[int],
    quote_post_correlations: Dict[str, Dict[str, Dict[str, Any]]],
    engagement_trial_outcomes: List[Dict[str, Any]],
    engagement_correlation_warnings: List[Dict[str, Any]],
    *,
    correlated_quote_post_fields: Callable[..., Dict[str, Any]],
    bounded_source_refs: Callable[..., Tuple[List[Dict[str, Any]], int]],
) -> List[Dict[str, Any]]:
    """Enrich original production events and assemble sorted trial publications.

    Mutate events and supplied outcome/warning lists in place. The returned
    publication rows share projected values with the correlation results.
    """
    quote_image_events = [
        event
        for event in events
        if event.get("kind") == "quote_image_posted"
        and id(event) in production_event_object_ids
    ]
    for event in quote_image_events:
        post_id = str(event.get("post_id") or "")
        if not post_id:
            continue
        correlated = correlated_quote_post_fields(
            post_id,
            legacy=event,
            warning_time=str(event.get("time") or ""),
        )
        source_refs, source_ref_omitted = bounded_source_refs(
            event.get("source_refs"), correlated.get("source_refs")
        )
        event.update(
            {
                key: value
                for key, value in correlated.items()
                if key not in {"source_refs", "source_ref_omitted_count"}
            }
        )
        if source_refs:
            event["source_refs"] = source_refs
        total_source_ref_omitted = source_ref_omitted + int(
            correlated.get("source_ref_omitted_count") or 0
        )
        if total_source_ref_omitted:
            event["source_ref_omitted_count"] = total_source_ref_omitted
        if correlated.get("public_text_status") == "confirmed":
            # Preserve exact structured text, including real newlines, in JSON.
            event["text"] = correlated["public_text"]
        elif correlated.get("public_text_status") == "unavailable_inconsistent":
            # Do not present mutable selection text as confirmed public text.
            event["text"] = ""

    confirmed_experimental_publications: List[Dict[str, Any]] = []
    for post_id, slots in quote_post_correlations.items():
        confirmation = slots.get(
            "engagement_question_experimental_member_confirmed"
        )
        if not confirmation:
            continue
        correlated = correlated_quote_post_fields(
            post_id,
            warning_time=str(confirmation.get("time") or ""),
        )
        confirmed_experimental_publications.append(
            {
                "time": confirmation.get("time") or "",
                "post_id": post_id,
                "experiment_id": correlated.get("engagement_experiment_id") or "",
                "plan_sha256": correlated.get("engagement_plan_sha256") or "",
                "pair_id": correlated.get("engagement_pair_id") or "",
                "member_position": correlated.get("engagement_member_position"),
                "arm": correlated.get("engagement_arm") or "",
                "publication_order": (
                    correlated.get("engagement_publication_order") or ""
                ),
                "publication_sequence": correlated.get(
                    "engagement_publication_sequence"
                ),
                "question_present": correlated.get(
                    "engagement_question_present"
                ),
                "question": correlated.get("engagement_question_text"),
                "question_status": correlated.get(
                    "engagement_question_text_status"
                ),
                "quote_hash": correlated.get("quote_hash") or "",
                "quote_text": correlated.get("quote_text") or "",
                "public_text": correlated.get("public_text") or "",
                "correlation_status": correlated.get("correlation_status"),
                **(
                    {"source_refs": correlated["source_refs"]}
                    if correlated.get("source_refs")
                    else {}
                ),
                **(
                    {
                        "source_ref_omitted_count": correlated[
                            "source_ref_omitted_count"
                        ]
                    }
                    if correlated.get("source_ref_omitted_count")
                    else {}
                ),
            }
        )
    confirmed_experimental_publications.sort(
        key=lambda item: (str(item.get("time") or ""), str(item.get("post_id") or ""))
    )
    engagement_trial_outcomes.sort(
        key=lambda item: (str(item.get("time") or ""), str(item.get("event") or ""))
    )
    engagement_correlation_warnings.sort(
        key=lambda item: (
            str(item.get("time") or ""),
            str(item.get("post_id") or ""),
            str(item.get("field") or ""),
        )
    )
    return confirmed_experimental_publications
