"""Correlate prepared quote-publication evidence.

The per-analysis correlation owner retains evidence and bounded warning
state. The coordinator supplies parsed payloads, timestamps and current helper
callbacks. Operations retain and enrich the supplied evidence/events in place; publication authority, source
filtering and warning bounds retain the existing digest rules. No files, home,
configuration, clocks or providers are accessed by this module.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING, TypeGuard

if TYPE_CHECKING:
    from mrs_log_digest_contracts import SourceReference


def valid_account_root_publication_identity(
    event: Any,
    *,
    valid_string_public_post_id: Callable[[Any], bool],
) -> TypeGuard[Dict[str, Any]]:
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


@dataclass
class QuotePublicationCorrelation:
    """Own one analysis's production publication evidence and bounded warnings.

    Current-source and validation/reference callbacks are evaluated when needed.
    Evidence payloads and report rows retain their original shared references.
    """

    source_is_selftest: Callable[[], bool]
    valid_string_public_post_id: Callable[[Any], bool]
    valid_bounded_utf8_text: Callable[..., bool]
    bounded_source_refs: Callable[..., Tuple[List[Dict[str, Any]], int]]
    warning_limit: Callable[[], int]
    evidence: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict, init=False)
    invalid_evidence: Dict[str, set[str]] = field(default_factory=dict, init=False)
    warnings: List[Dict[str, Any]] = field(default_factory=list, init=False)
    warning_counts: Counter[str] = field(default_factory=Counter, init=False)
    warning_omitted_count: int = field(default=0, init=False)
    _warning_keys: set[Tuple[str, str, str, str, str]] = field(default_factory=set, init=False)

    def add_warning(
        self,
        *,
        time_text: str,
        post_id: str,
        field: str,
        left_event: str,
        right_event: str,
        status: str = "conflict",
    ) -> None:
        """Record each warning once, counting warnings beyond the display limit."""
        event_pair = tuple(sorted((left_event, right_event)))
        key = (post_id, field, event_pair[0], event_pair[1], status)
        if key in self._warning_keys:
            return
        self._warning_keys.add(key)
        self.warning_counts[post_id] += 1
        if len(self.warnings) >= self.warning_limit():
            self.warning_omitted_count += 1
            return
        self.warnings.append(
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

    def retain(
        self,
        post_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """Keep one fixed-shape evidence slot per structured event and post."""
        if self.source_is_selftest():
            return
        slots = self.evidence.setdefault(post_id, {})
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
                references, omitted = self.bounded_source_refs(
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
                self.add_warning(
                    time_text=time_text,
                    post_id=post_id,
                    field=field,
                    left_event=event_type,
                    right_event=event_type,
                )

    def note_invalid(
        self,
        post_id: Any,
        event_type: str,
        time_text: str,
    ) -> None:
        """Record malformed observability without letting it replace authority."""

        if (
            not self.valid_string_public_post_id(post_id)
            or self.source_is_selftest()
        ):
            return
        self.invalid_evidence.setdefault(post_id, set()).add(event_type)
        self.add_warning(
            time_text=time_text,
            post_id=post_id,
            field="producer_schema",
            left_event=event_type,
            right_event="required_contract",
            status="invalid",
        )

    def correlated_fields(
        self,
        post_id: str,
        *,
        legacy: Optional[Dict[str, Any]] = None,
        warning_time: str = "",
    ) -> Dict[str, Any]:
        """Resolve fixed structured evidence for one immutable post identity."""
        slots = self.evidence.get(post_id) or {}
        invalid_evidence = self.invalid_evidence.get(post_id) or set()
        main_event = slots.get("main_post_posted") or {}
        root_event = slots.get("account_root_posted") or {}
        legacy = legacy or {}
        effective_time = str(
            warning_time
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
                    self.add_warning(
                        time_text=effective_time,
                        post_id=post_id,
                        field=field,
                        left_event=first_event,
                        right_event=event_type,
                    )
                    return None
            return first_value

        resolved_post_id = resolve(
            "post_id",
            [
                ("main_post_posted", evidence_value(main_event, "post_id")),
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
            and self.valid_bounded_utf8_text(public_text)
            and (
                quote_text is None
                or self.valid_bounded_utf8_text(
                    quote_text,
                    allow_empty=True,
                )
            )
        )
        if account_root_usable:
            if not isinstance(quote_text, str):
                quote_text = ""
        else:
            public_text = ""
            quote_text = ""
        public_text_status = (
            "confirmed"
            if account_root_usable
            else "unavailable_inconsistent"
            if root_event or invalid_evidence
            else ""
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
        post_warning_count = self.warning_counts[post_id]
        source_refs, source_ref_omitted = self.bounded_source_refs(
            main_event.get("source_refs"),
            root_event.get("source_refs"),
        )
        source_ref_omitted += sum(
            omitted
            for item in (main_event, root_event)
            for omitted in [item.get("source_ref_omitted_count")]
            if type(omitted) is int and omitted >= 0
        )
        result = {
            **selection_fields,
            "quote_hash": quote_hash,
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

    def prepare_report(
        self,
        events: List[Dict[str, Any]],
        production_event_object_ids: set[int],
    ) -> None:
        """Enrich original production events and sort owned warnings in place."""
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
            correlated = self.correlated_fields(
                post_id,
                legacy=event,
                warning_time=str(event.get("time") or ""),
            )
            source_refs, source_ref_omitted = self.bounded_source_refs(
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

        self.warnings.sort(
            key=lambda item: (
                str(item.get("time") or ""),
                str(item.get("post_id") or ""),
                str(item.get("field") or ""),
            )
        )


def record_main_post_publication(
    event_obj: Dict[str, Any],
    strict_structured_event_obj: Optional[Dict[str, Any]],
    ts: datetime,
    *,
    valid_string_public_post_id: Callable[[Any], bool],
    SHA256_LOWER_RE: re.Pattern[str],
    retain_quote_post_evidence: Callable[..., None],
    note_invalid_quote_post_evidence: Callable[..., None],
    make_source_ref: Callable[[], SourceReference],
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
    if main_core_valid and authority_event_obj is not None:
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
    valid_account_root_publication_identity: Callable[[Any], TypeGuard[Dict[str, Any]]],
    SHA256_LOWER_RE: re.Pattern[str],
    valid_bounded_utf8_text: Callable[..., bool],
    retain_quote_post_evidence: Callable[..., None],
    note_invalid_quote_post_evidence: Callable[..., None],
    make_source_ref: Callable[[], SourceReference],
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
