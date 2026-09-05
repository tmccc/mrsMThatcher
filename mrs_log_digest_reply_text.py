"""Prepare exact confirmed public reply text from supplied publication evidence.

The coordinator supplies prepared runtime/receipt/history evidence, report events,
source-reference bounding, epoch conversion and current helper/validator callbacks.
Enrichment mutates the supplied report and event objects, preserving production
object identity, evidence precedence, conflicts, warnings and synthesis order.

This module loads no evidence, samples no clock and performs no runtime I/O on
import or invocation. Publication/recovery authority, event collection, analysis,
pipeline reconciliation and report assembly remain with their existing owners.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Optional, Tuple

from mrs_log_digest_values import (
    SHA256_LOWER_RE,
    valid_bounded_utf8_text,
    valid_public_post_id,
    valid_string_public_post_id,
)


PUBLISHED_REPLY_WARNING_LIMIT = 100


def _public_reply_text_result(
    candidates: List[Dict[str, Any]],
    *,
    unavailable_reason: str,
    bounded_source_refs: Callable[..., Tuple[List[Dict[str, Any]], int]],
) -> Dict[str, Any]:
    """Resolve exact authoritative text candidates without selecting conflicts."""

    valid = [
        candidate
        for candidate in candidates
        if valid_bounded_utf8_text(candidate.get("text"))
    ]
    invalid_count = len(candidates) - len(valid)
    invalid_reasons = list(
        dict.fromkeys(
            str(candidate.get("invalid_reason") or "").strip()
            for candidate in candidates
            if not (
                valid_bounded_utf8_text(candidate.get("text"))
            )
            and str(candidate.get("invalid_reason") or "").strip()
        )
    )
    texts = list(dict.fromkeys(candidate["text"] for candidate in valid))
    sources = list(
        dict.fromkeys(
            str(candidate.get("source") or "authoritative evidence")
            for candidate in candidates
        )
    )
    references, omitted = bounded_source_refs(
        *[candidate.get("source_refs") for candidate in candidates]
    )
    if invalid_count or len(texts) > 1:
        result = {
            "public_reply_text": None,
            "public_reply_text_sha256": None,
            "public_reply_text_character_count": None,
            "public_reply_text_complete": False,
            "public_reply_text_status": "conflict",
            "public_reply_text_source": " + ".join(sources) or None,
            "public_reply_text_reason": (
                "; ".join(invalid_reasons)[:320]
                or "authoritative text evidence is malformed or exceeds the supported bound"
                if invalid_count
                else "authoritative text sources disagree"
            ),
            "correlation_status": "conflict",
        }
    elif not texts:
        result = {
            "public_reply_text": None,
            "public_reply_text_sha256": None,
            "public_reply_text_character_count": None,
            "public_reply_text_complete": False,
            "public_reply_text_status": "unavailable",
            "public_reply_text_source": None,
            "public_reply_text_reason": unavailable_reason[:320],
            "correlation_status": "unavailable",
        }
    else:
        text = texts[0]
        result = {
            "public_reply_text": text,
            "public_reply_text_sha256": hashlib.sha256(
                text.encode("utf-8")
            ).hexdigest(),
            "public_reply_text_character_count": len(text),
            "public_reply_text_complete": True,
            "public_reply_text_status": "confirmed",
            "public_reply_text_source": " + ".join(sources),
            "public_reply_text_reason": "",
            "correlation_status": "exact",
        }
    if references:
        result["source_refs"] = references
    if omitted:
        result["source_ref_omitted_count"] = omitted
    return result


def _durable_public_reply_text_candidates(
    runtime_state: Any,
    *,
    lane: str,
    target_id: str,
    reply_post_id: str,
    original_post_id: str = "",
    confirmed_receipt_evidence: Optional[List[Dict[str, Any]]] = None,
    valid_public_text: Callable[[Any], bool],
) -> List[Dict[str, Any]]:
    """Read narrowly validated exact text for one immutable confirmed identity."""

    candidates: List[Dict[str, Any]] = []
    for item in confirmed_receipt_evidence or []:
        if (
            not isinstance(item, dict)
            or item.get("reply_post_id") != reply_post_id
        ):
            continue
        identity_matches = (
            item.get("target_id") == target_id
            and item.get("lane") == lane
            and (item.get("original_post_id") or "")
            == (original_post_id if lane == "quote_tweet" else "")
        )
        text_valid = valid_public_text(
            item.get("reply_text")
        )
        candidates.append(
            {
                "text": (
                    item.get("reply_text")
                    if identity_matches and text_valid
                    else None
                ),
                "source": str(
                    item.get("source") or "confirmed_reply_receipt.json"
                ),
                "invalid_reason": (
                    "confirmed reply receipt identity disagrees with structured confirmation"
                    if not identity_matches
                    else "confirmed reply receipt text violates the conversational public-text contract"
                    if not text_valid
                    else ""
                ),
            }
        )
    if not isinstance(runtime_state, dict):
        return candidates
    history = runtime_state.get("ai_reply_history")
    if isinstance(history, list):
        for item in history[-1000:]:
            if not isinstance(item, dict):
                continue
            if item.get("reply_post_id") != reply_post_id:
                continue
            identity_matches = (
                item.get("target_id") == target_id
                and item.get("candidate_source") == lane
                and (
                    lane != "quote_tweet"
                    or item.get("original_post_id") in (None, "")
                    or item.get("original_post_id") == original_post_id
                )
            )
            text_valid = valid_public_text(
                item.get("proposed_reply")
            )
            candidates.append(
                {
                    "text": (
                        item.get("proposed_reply")
                        if identity_matches and text_valid
                        else None
                    ),
                    "source": "bot_state.json.ai_reply_history",
                    "invalid_reason": (
                        "ai_reply_history identity disagrees with structured confirmation"
                        if not identity_matches
                        else "ai_reply_history text violates the conversational public-text contract"
                        if not text_valid
                        else ""
                    ),
                }
            )
    cache = runtime_state.get("tweet_cache")
    cached = cache.get(reply_post_id) if isinstance(cache, dict) else None
    if isinstance(cached, dict):
        references = cached.get("referenced_tweets")
        exact_reference = (
            references[0]
            if isinstance(references, list)
            and len(references) == 1
            and isinstance(references[0], dict)
            else None
        )
        identity_matches = (
            cached.get("id") == reply_post_id
            and cached.get("post_type") == "auto_reply"
            and isinstance(exact_reference, dict)
            and exact_reference.get("type") == "replied_to"
            and exact_reference.get("id") == target_id
            and valid_string_public_post_id(exact_reference.get("id"))
        )
        text_valid = valid_public_text(
            cached.get("text")
        )
        candidates.append(
            {
                "text": (
                    cached.get("text")
                    if identity_matches and text_valid
                    else None
                ),
                "source": "bot_state.json.tweet_cache",
                "invalid_reason": (
                    "tweet_cache identity disagrees with structured confirmation"
                    if not identity_matches
                    else "tweet_cache text violates the conversational public-text contract"
                    if not text_valid
                    else ""
                ),
            }
        )
    return candidates


def _normalised_structured_reply_confirmation(
    value: Any,
) -> Optional[Dict[str, Any]]:
    """Validate the immutable identity carried by one reply-posted event."""

    if not isinstance(value, dict):
        return None
    logged_lane = value.get("lane")
    lane = (
        "mention"
        if logged_lane == "mention+hot_post_reply"
        else logged_lane
    )
    target_value = value.get("target_id")
    reply_post_value = value.get("reply_post_id")
    original_post_value = value.get("original_post_id")
    if (
        type(logged_lane) is not str
        or logged_lane
        not in {
            "mention",
            "mention+hot_post_reply",
            "hot_post_reply",
            "quote_tweet",
        }
        or not valid_string_public_post_id(target_value)
        or not valid_string_public_post_id(reply_post_value)
        or (
            lane == "quote_tweet"
            and not valid_string_public_post_id(original_post_value)
        )
        or (
            lane != "quote_tweet"
            and original_post_value is not None
            and original_post_value != ""
        )
    ):
        return None
    target_id = str(target_value)
    reply_post_id = str(reply_post_value)
    original_post_id = (
        str(original_post_value) if lane == "quote_tweet" else ""
    )
    return {
        **value,
        "lane": lane,
        "target_id": target_id,
        "reply_post_id": reply_post_id,
        "original_post_id": (
            original_post_id if lane == "quote_tweet" else ""
        ),
    }


def enrich_published_reply_text(
    report: Dict[str, Any],
    *,
    runtime_state: Any,
    structured_reply_confirmations: List[Dict[str, Any]],
    historical_reply_text_evidence: List[Dict[str, Any]],
    confirmed_receipt_evidence: Optional[List[Dict[str, Any]]] = None,
    durable_evidence_status: Optional[Dict[str, Any]] = None,
    production_event_object_ids: Optional[set[int]] = None,
    bounded_source_refs: Callable[..., Tuple[List[Dict[str, Any]], int]],
    epoch_to_london_text: Callable[[int], Optional[str]],
    normalise_confirmation: Callable[[Any], Optional[Dict[str, Any]]],
    durable_candidates: Callable[..., List[Dict[str, Any]]],
    resolve_text: Callable[..., Dict[str, Any]],
    warning_limit: int,
) -> None:
    """Enrich confirmed reply records from exact immutable publication evidence."""

    events = report.get("events")
    if not isinstance(events, list):
        return
    warnings: List[Dict[str, Any]] = []
    warning_omitted_count = 0
    synthesized_events: List[Tuple[int, int, Dict[str, Any]]] = []
    production_ids = (
        {id(event) for event in events if isinstance(event, dict)}
        if production_event_object_ids is None
        else production_event_object_ids
    )

    def warn(
        *,
        reply_post_id: str,
        target_id: str,
        lane: str,
        reason: str,
    ) -> None:
        nonlocal warning_omitted_count
        item = {
            "reply_post_id": reply_post_id,
            "target_id": target_id,
            "lane": lane,
            "status": "conflict",
            "reason": reason[:240],
        }
        if item in warnings:
            return
        if len(warnings) >= warning_limit:
            warning_omitted_count += 1
            return
        warnings.append(item)

    representation_specs = {
        "mention_reply_posted": ("mention", "mention_id"),
        "hot_post_reply_posted": ("hot_post_reply", "hot_post_reply_id"),
        "quote_tweet_reply_posted": ("quote_tweet", "quote_tweet_id"),
    }
    legacy_records: List[Dict[str, Any]] = []
    legacy_records_by_reply: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in representation_specs:
            continue
        legacy_records.append(event)
        reply_post_id = str(event.get("reply_post_id") or "")
        if (
            id(event) in production_ids
            and valid_public_post_id(reply_post_id)
        ):
            legacy_records_by_reply.setdefault(reply_post_id, []).append(event)

    def legacy_identity(event: Dict[str, Any]) -> Tuple[str, str, str]:
        expected_lane, target_field = representation_specs[str(event["kind"])]
        return (
            expected_lane,
            str(event.get(target_field) or ""),
            (
                str(event.get("original_post_id") or "")
                if expected_lane == "quote_tweet"
                else ""
            ),
        )

    confirmations_by_reply: Dict[str, List[Dict[str, Any]]] = {}
    for raw_confirmation in structured_reply_confirmations:
        confirmation = normalise_confirmation(
            raw_confirmation
        )
        if confirmation is None:
            continue
        reply_post_id = str(confirmation["reply_post_id"])
        confirmations_by_reply.setdefault(reply_post_id, []).append(
            confirmation
        )
    for receipt_index, receipt in enumerate(confirmed_receipt_evidence or []):
        confirmation = normalise_confirmation(
            {
                "time": epoch_to_london_text(
                    int(receipt.get("reply_epoch") or 0)
                )
                or "",
                "lane": receipt.get("lane"),
                "target_id": receipt.get("target_id"),
                "reply_post_id": receipt.get("reply_post_id"),
                "original_post_id": receipt.get("original_post_id"),
                "publication_authority": "confirmed_reply_receipt.json",
                "current_snapshot_authority": True,
                "_event_insertion_index": len(events),
                "_source_sequence": len(events) + receipt_index,
            }
        )
        if confirmation is None:
            continue
        reply_post_id = str(confirmation["reply_post_id"])
        if reply_post_id not in confirmations_by_reply:
            confirmations_by_reply[reply_post_id] = [confirmation]

    enriched_records: set[int] = set()
    for reply_post_id, confirmations in confirmations_by_reply.items():
        identities = {
            (
                str(item.get("lane")),
                str(item.get("target_id")),
                str(item.get("original_post_id") or ""),
            )
            for item in confirmations
        }
        identity_conflict = len(identities) != 1
        lane, target_id, original_post_id = sorted(identities)[0]
        same_reply_records = legacy_records_by_reply.get(reply_post_id, [])
        matching_records = (
            [
                event
                for event in same_reply_records
                if legacy_identity(event)
                == (lane, target_id, original_post_id)
            ]
            if not identity_conflict
            else []
        )
        matching_record_ids = {id(event) for event in matching_records}
        mismatched_records = [
            event
            for event in same_reply_records
            if id(event) not in matching_record_ids
        ]
        confirmation_refs, confirmation_refs_omitted = bounded_source_refs(
            *[item.get("source_refs") for item in confirmations]
        )
        publication_authorities = list(
            dict.fromkeys(
                str(
                    item.get("publication_authority")
                    or "structured reply_posted"
                )
                for item in confirmations
            )
        )
        current_snapshot_authority = any(
            item.get("current_snapshot_authority") is True
            for item in confirmations
        )
        if not matching_records:
            matching_records = [
                {
                    "kind": "confirmed_public_reply",
                    "time": min(
                        str(item.get("time") or "") for item in confirmations
                    ),
                    "status": "confirmed",
                    "publication_authority": " + ".join(
                        publication_authorities
                    ),
                    **(
                        {
                            "evidence_scope": (
                                "authoritative current durable snapshot, independent "
                                "of the selected log window"
                            )
                        }
                        if current_snapshot_authority
                        else {}
                    ),
                    "lane": lane if not identity_conflict else None,
                    "target_id": target_id if not identity_conflict else None,
                    "reply_post_id": reply_post_id,
                    **(
                        {"original_post_id": original_post_id}
                        if not identity_conflict and original_post_id
                        else {}
                    ),
                    **(
                        {"source_refs": confirmation_refs}
                        if confirmation_refs
                        else {}
                    ),
                }
            ]
            synthesized_events.append(
                (
                    min(
                        int(item.get("_event_insertion_index") or 0)
                        for item in confirmations
                    ),
                    min(
                        int(item.get("_source_sequence") or 0)
                        for item in confirmations
                    ),
                    matching_records[0],
                )
            )
        if identity_conflict:
            text_result = resolve_text(
                [
                    {
                        "text": None,
                        "source": "structured reply_posted",
                        "source_refs": confirmation_refs,
                    }
                ],
                unavailable_reason="",
            )
            text_result["public_reply_text_reason"] = (
                "structured reply confirmations disagree on immutable identity"
            )
            warn(
                reply_post_id=reply_post_id,
                target_id=target_id,
                lane=lane,
                reason=text_result["public_reply_text_reason"],
            )
        else:
            candidates = durable_candidates(
                runtime_state,
                lane=lane,
                target_id=target_id,
                reply_post_id=reply_post_id,
                original_post_id=original_post_id,
                confirmed_receipt_evidence=confirmed_receipt_evidence,
            )
            text_result = resolve_text(
                candidates,
                unavailable_reason=(
                    "exact confirmed text is no longer retained in current durable state"
                ),
            )
            if text_result["public_reply_text_status"] == "conflict":
                warn(
                    reply_post_id=reply_post_id,
                    target_id=target_id,
                    lane=lane,
                    reason=str(text_result["public_reply_text_reason"]),
                )
        for event in matching_records:
            legacy_kind = str(event["kind"])
            expected_lane: Optional[str] = None
            target_field: Optional[str] = None
            legacy_target_id = ""
            legacy_original_post_id = ""
            if legacy_kind in representation_specs:
                expected_lane, target_field = representation_specs[legacy_kind]
                legacy_target_id = str(event.get(target_field) or "")
                if expected_lane == "quote_tweet":
                    legacy_original_post_id = str(
                        event.get("original_post_id") or ""
                    )
            if not identity_conflict:
                event["lane"] = lane
                event["target_id"] = target_id
                if original_post_id:
                    event["original_post_id"] = (
                        legacy_original_post_id or original_post_id
                    )
            event["reply_post_id"] = reply_post_id
            combined_refs, omitted = bounded_source_refs(
                event.get("source_refs"),
                confirmation_refs,
                text_result.get("source_refs"),
            )
            event.update(
                {
                    key: value
                    for key, value in text_result.items()
                    if key not in {"source_refs", "source_ref_omitted_count"}
                }
            )
            if combined_refs:
                event["source_refs"] = combined_refs
            total_omitted = omitted + confirmation_refs_omitted + int(
                text_result.get("source_ref_omitted_count") or 0
            )
            if total_omitted:
                event["source_ref_omitted_count"] = total_omitted
            if target_field is not None and not identity_conflict:
                event.setdefault(target_field, target_id)
            enriched_records.add(id(event))

        for event in mismatched_records:
            expected_lane, target_field = representation_specs[str(event["kind"])]
            legacy_target_id = str(event.get(target_field) or "")
            event.setdefault("lane", expected_lane)
            event.setdefault("target_id", legacy_target_id or None)
            event["reply_post_id"] = reply_post_id
            combined_refs, omitted = bounded_source_refs(
                event.get("source_refs"),
                confirmation_refs,
            )
            if combined_refs:
                event["source_refs"] = combined_refs
            total_omitted = omitted + confirmation_refs_omitted
            if total_omitted:
                event["source_ref_omitted_count"] = total_omitted
            event.update(
                {
                    "correlation_status": "conflict",
                    "public_reply_text": None,
                    "public_reply_text_sha256": None,
                    "public_reply_text_character_count": None,
                    "public_reply_text_complete": False,
                    "public_reply_text_status": "conflict",
                    "public_reply_text_source": None,
                    "public_reply_text_reason": (
                        "legacy posted record identity disagrees with structured confirmation"
                    ),
                }
            )
            warn(
                reply_post_id=reply_post_id,
                target_id=target_id,
                lane=lane,
                reason=str(event["public_reply_text_reason"]),
            )
            enriched_records.add(id(event))

    consumed_historical_evidence: set[int] = set()
    for event in legacy_records:
        if (
            not isinstance(event, dict)
            or event.get("kind") not in representation_specs
            or id(event) in enriched_records
            or id(event) not in production_ids
        ):
            continue
        expected_lane, target_field = representation_specs[str(event["kind"])]
        target_id = str(event.get(target_field) or "")
        reply_post_id = str(event.get("reply_post_id") or "")
        event["lane"] = expected_lane
        event["target_id"] = target_id or None
        event["reply_post_id"] = reply_post_id or None
        event.update(
            resolve_text(
                [],
                unavailable_reason=(
                    "no retained structured reply_posted confirmation binds this "
                    "legacy posted record to exact durable text"
                ),
            )
        )
        enriched_records.add(id(event))

    evidence_by_identity: Dict[
        Tuple[str, str], List[Dict[str, Any]]
    ] = {}
    historical_evidence_by_reply: Dict[str, List[Dict[str, Any]]] = {}
    for evidence in historical_reply_text_evidence:
        if evidence.get("authoritative") is not True:
            continue
        parent_id = evidence.get("parent_post_id")
        quote_id = evidence.get("quote_id")
        reply_post_id = evidence.get("reply_post_id")
        if (
            not valid_string_public_post_id(parent_id)
            or not valid_string_public_post_id(reply_post_id)
            or not isinstance(quote_id, str)
            or SHA256_LOWER_RE.fullmatch(quote_id) is None
        ):
            continue
        evidence_by_identity.setdefault((parent_id, quote_id), []).append(
            evidence
        )
        historical_evidence_by_reply.setdefault(reply_post_id, []).append(
            evidence
        )
    for event in events:
        if (
            not isinstance(event, dict)
            or event.get("kind") != "historical_context_reply"
            or event.get("status") not in {"completed", "already_completed"}
            or id(event) not in production_ids
        ):
            continue
        parent_value = event.get("parent_post_id")
        quote_value = event.get("quote_id")
        selected_identity_valid = bool(
            valid_string_public_post_id(parent_value)
            and isinstance(quote_value, str)
            and SHA256_LOWER_RE.fullmatch(quote_value) is not None
        )
        parent_id = parent_value if selected_identity_valid else ""
        quote_id = quote_value if selected_identity_valid else ""
        evidence = (
            evidence_by_identity.get((parent_id, quote_id), [])
            if selected_identity_valid
            else []
        )
        reply_ids = {
            item["reply_post_id"] for item in evidence
        }
        identity_conflict = False
        if len(reply_ids) == 1:
            evidence = historical_evidence_by_reply.get(
                next(iter(reply_ids)),
                evidence,
            )
            identity_conflict = len(
                {
                    (
                        item.get("parent_post_id"),
                        item.get("quote_id"),
                    )
                    for item in evidence
                }
            ) != 1
        elif reply_ids:
            evidence = [
                item
                for reply_post_id in reply_ids
                for item in historical_evidence_by_reply.get(
                    reply_post_id,
                    [],
                )
            ]
        consumed_historical_evidence.update(id(item) for item in evidence)
        candidates = [
            {
                "text": item.get("reply_text"),
                "source": str(
                    item.get("source")
                    or "structured historical_context_reply_posted"
                ),
                "source_refs": item.get("source_refs"),
            }
            for item in evidence
        ]
        text_result = resolve_text(
            candidates,
            unavailable_reason=(
                "selected historical-context identity is not canonical"
                if not selected_identity_valid
                else "no retained exact historical_context_reply_posted evidence"
            ),
        )
        if len(reply_ids) > 1 or identity_conflict:
            text_result.update(
                {
                    "public_reply_text": None,
                    "public_reply_text_sha256": None,
                    "public_reply_text_character_count": None,
                    "public_reply_text_complete": False,
                    "public_reply_text_status": "conflict",
                    "public_reply_text_reason": (
                        "structured historical-context evidence disagrees on "
                        "reply or parent identity"
                    ),
                    "correlation_status": "conflict",
                }
            )
        event["reply_post_id"] = (
            next(iter(reply_ids)) if len(reply_ids) == 1 else None
        )
        combined_refs, omitted = bounded_source_refs(
            event.get("source_refs"),
            text_result.get("source_refs"),
        )
        event.update(
            {
                key: value
                for key, value in text_result.items()
                if key not in {"source_refs", "source_ref_omitted_count"}
            }
        )
        if combined_refs:
            event["source_refs"] = combined_refs
        total_omitted = omitted + int(
            text_result.get("source_ref_omitted_count") or 0
        )
        if total_omitted:
            event["source_ref_omitted_count"] = total_omitted
        if event["public_reply_text_status"] == "conflict":
            warn(
                reply_post_id=str(event.get("reply_post_id") or ""),
                target_id=parent_id,
                lane="historical_context_reply",
                reason=str(event["public_reply_text_reason"]),
            )
        enriched_records.add(id(event))

    remaining_historical_by_reply: Dict[str, List[Dict[str, Any]]] = {}
    for evidence in historical_reply_text_evidence:
        parent_id = str(evidence.get("parent_post_id") or "")
        quote_id = str(evidence.get("quote_id") or "")
        reply_post_id = str(evidence.get("reply_post_id") or "")
        if (
            id(evidence) in consumed_historical_evidence
            or evidence.get("authoritative") is not True
            or evidence.get("durable_only") is True
            or not valid_public_post_id(parent_id)
            or not valid_public_post_id(reply_post_id)
            or not quote_id
        ):
            continue
        remaining_historical_by_reply.setdefault(reply_post_id, []).append(
            evidence
        )
    for reply_post_id, evidence_items in remaining_historical_by_reply.items():
        identities = {
            (
                str(item.get("parent_post_id") or ""),
                str(item.get("quote_id") or ""),
            )
            for item in evidence_items
        }
        identity_conflict = len(identities) != 1
        parent_id, quote_id = sorted(identities)[0]
        text_result = resolve_text(
            [
                {
                    "text": item.get("reply_text"),
                    "source": str(
                        item.get("source")
                        or "structured historical_context_reply_posted"
                    ),
                    "source_refs": item.get("source_refs"),
                }
                for item in evidence_items
            ],
            unavailable_reason=(
                "confirmed historical-context text is unavailable"
            ),
        )
        if identity_conflict:
            text_result.update(
                {
                    "public_reply_text": None,
                    "public_reply_text_sha256": None,
                    "public_reply_text_character_count": None,
                    "public_reply_text_complete": False,
                    "public_reply_text_status": "conflict",
                    "public_reply_text_reason": (
                        "structured historical-context confirmations disagree "
                        "on immutable identity"
                    ),
                    "correlation_status": "conflict",
                }
            )
        normalized_event: Dict[str, Any] = {
            "kind": "confirmed_public_reply",
            "time": min(str(item.get("time") or "") for item in evidence_items),
            "status": "confirmed",
            "publication_authority": (
                "structured historical_context_reply_posted"
            ),
            "lane": "historical_context_reply",
            "target_id": parent_id if not identity_conflict else None,
            "parent_post_id": parent_id if not identity_conflict else None,
            "reply_post_id": reply_post_id,
            "quote_id": quote_id if not identity_conflict else None,
        }
        normalized_event.update(text_result)
        synthesized_events.append(
            (
                min(
                    int(item.get("_event_insertion_index") or 0)
                    for item in evidence_items
                ),
                min(
                    int(item.get("_source_sequence") or 0)
                    for item in evidence_items
                ),
                normalized_event,
            )
        )
        enriched_records.add(id(normalized_event))
        if normalized_event["public_reply_text_status"] == "conflict":
            warn(
                reply_post_id=reply_post_id,
                target_id=parent_id,
                lane="historical_context_reply",
                reason=str(normalized_event["public_reply_text_reason"]),
            )

    for offset, (insertion_index, _source_sequence, event) in enumerate(
        sorted(synthesized_events, key=lambda item: (item[0], item[1]))
    ):
        events.insert(min(insertion_index + offset, len(events)), event)
    historical_section = report.get("historical_context_replies")
    if isinstance(historical_section, dict):
        historical_section["events"] = [
            event
            for event in events
            if event.get("kind") == "historical_context_reply"
            or (
                event.get("kind") == "confirmed_public_reply"
                and event.get("lane") == "historical_context_reply"
            )
        ]

    report["published_reply_text_health"] = {
        "confirmed_record_count": len(enriched_records),
        "complete_text_record_count": sum(
            id(event) in enriched_records
            and event.get("public_reply_text_complete") is True
            for event in events
            if isinstance(event, dict)
        ),
        "conflict_count": sum(
            id(event) in enriched_records
            and event.get("public_reply_text_status") == "conflict"
            for event in events
            if isinstance(event, dict)
        ),
        "warnings": warnings,
        "warning_omitted_count": warning_omitted_count,
        "state_evidence_scope": (
            "current bounded bot_state.json retention, not reconstructed drafts"
        ),
        "durable_evidence": dict(durable_evidence_status or {}),
    }
