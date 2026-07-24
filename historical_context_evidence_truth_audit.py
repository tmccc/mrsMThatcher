#!/usr/bin/env python3
"""Build a deterministic, read-only historical-context evidence triage audit.

The audit correlates every completed research packet with its source-role
record, current public rendering, and any completed production reply-history
record.  Its findings are editorial review leads, not evidence adjudications:
the program never changes a packet, source role, history entry, or bot state.

Only an explicitly supplied ``--output`` path is written.  No bot module,
network client, provider, or reply store is imported or instantiated.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from historical_context_formatter import (
    DEFAULT_RESEARCH_DIR,
    _v2_british_date,
    _v2_british_dates_in_text,
    _v2_clean,
    _v2_one_sentence,
    format_context_reply_public,
    load_and_validate_corpus,
    packet_is_attributed_to_margaret_thatcher,
)
from historical_context_packet_corrections import PACKET_CORRECTIONS_FILENAME
from historical_context_source_curated_evidence import CURATED_EVIDENCE_FILENAME
from historical_context_source_roles import (
    AUDIT_FILENAME,
    canonical_source_identity,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_HISTORY_PATH = ROOT / "historical_context_reply_history.json"
AUDIT_KIND = "historical_context_evidence_truth_triage_audit"
AUDIT_SCHEMA_VERSION = 2
EXPECTED_COMPLETED_PACKET_COUNT = 626
REVIEW_BASELINE_PUBLISHED_HISTORY_COUNT = 77
EXPECTED_ATTRIBUTION_ELIGIBLE_COUNT = 610

GENERIC_CONTEXT = (
    "The surviving attribution does not establish an occasion, date or "
    "immediate historical issue."
)
_CLAIM_FIELDS = ("source_event", "date", "historical_context")
_AUDITED_SOURCE_COLLECTIONS = (
    "sources",
    "recovered_sources",
    "model_proposed_source_leads",
    "researched_sources",
    "curated_sources",
    "virtual_locator_sources",
    "renderable_sources",
)
_PUBLIC_SURFACES = ("context", "meaning", "source_title", "source_url")
_PUBLIC_REACHABILITY_VALUES = (
    "currently_rendered_exact_occurrence",
    "formatter_reachable_but_suppressed",
    "internal_only_or_unreachable",
)
_CONTEXT_SLOT_REACHABILITY_VALUES = (
    "currently_rendered_exact_occurrence",
    "formatter_slot_reachable_but_not_currently_exposed",
    "production_ineligible",
    "formatter_slot_unreachable",
)
_PUBLIC_EVIDENCE_ROLES = frozenset({
    "wording_verification",
    "attribution_support",
    "source_event_support",
    "historical_context_support",
})
_UNKNOWN_VALUE = re.compile(
    r"^(?:unknown|not (?:known|established|located|available)|unresolved|"
    r"none|n/?a|undated)(?:\b|\s*\()",
    re.I,
)
_CALENDAR_DATE = re.compile(
    r"(?:\b(?:18|19|20)\d{2}-\d{2}-\d{2}\b|"
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?[,]?\s+"
    r"(?:18|19|20)\d{2}\b|"
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+"
    r"\d{1,2}(?:st|nd|rd|th)?[,]?\s+(?:18|19|20)\d{2}\b|"
    r"\b(?:18|19|20)\d{2}\b)",
    re.I,
)
_SECTION_HEADER = re.compile(
    r"^(Context|Meaning|Verification|Sources?|Secondary recollection)"
    r"(?:\s*[—:-]\s*(.*))?$",
    re.I,
)
_MTF_IDENTITY = re.compile(
    r"^margaret_thatcher_foundation:document:(\d{5,9})$"
)

# These are intentionally high-recall editorial indicators.  A match means
# "compare this paraphrase with the primary passage", never "the paraphrase is
# false".  Each concept is ignored when the quotation itself uses that concept.
_MEANING_STRENGTHENING_PATTERNS: dict[str, re.Pattern[str]] = {
    "inevitability": re.compile(
        r"\b(?:inevitabl(?:e|y)|inexorabl(?:e|y)|invariabl(?:e|y)|"
        r"unavoidabl(?:e|y)|necessarily)\b", re.I,
    ),
    "inherence": re.compile(r"\binherent(?:ly)?\b", re.I),
    "guarantee_or_certainty": re.compile(
        r"\b(?:guarantee(?:d|s)?|certain(?:ly)?|proves?|proof that)\b", re.I,
    ),
    "absolute_scope": re.compile(
        r"\b(?:always|entirely|completely|universally|without exception)\b",
        re.I,
    ),
    "core_or_fundamental": re.compile(
        r"\b(?:core|fundamental(?:ly)?|essentially)\b", re.I,
    ),
    "suppression_or_destruction": re.compile(
        r"\b(?:suppress(?:es|ed|ion)?|destroy(?:s|ed)?|eliminat(?:e|es|ed|ion))\b",
        re.I,
    ),
    "state_power": re.compile(r"\bstate power\b", re.I),
}


def _clean(value: Any) -> str:
    """Return one whitespace-normalised scalar."""
    return " ".join(str(value or "").split()).strip()


def _known(value: Any) -> bool:
    """Return whether a packet field contains a substantive known value."""
    text = _clean(value)
    return bool(text and not _UNKNOWN_VALUE.match(text))


def _file_sha256(path: Path) -> str:
    """Hash one input without modifying it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    """Return stable UTF-8 JSON bytes."""
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sequence_sha256(values: Iterable[str]) -> str:
    """Hash an ordered sequence with an unambiguous separator."""
    return hashlib.sha256(
        "".join(f"{len(value)}:{value}\n" for value in values).encode("utf-8")
    ).hexdigest()


def _sections(text: str) -> dict[str, str]:
    """Parse both legacy and current historical-reply section styles."""
    result: dict[str, str] = {}
    for block in re.split(r"\n\s*\n", str(text or "").strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        match = _SECTION_HEADER.fullmatch(lines[0])
        if match is None:
            continue
        label = match.group(1).casefold()
        if label.startswith("source") or label == "secondary recollection":
            continue
        inline = _clean(match.group(2))
        remainder = _clean(" ".join(lines[1:]))
        result[label] = _clean(" ".join(filter(None, (inline, remainder))))
    return result


def _context_flags(context: str, prefix: str) -> list[str]:
    """Return generic/no-date flags for one public context string."""
    flags: list[str] = []
    folded = _clean(context).casefold()
    if GENERIC_CONTEXT.casefold() in folded:
        flags.append(f"{prefix}_generic_context")
    if not _CALENDAR_DATE.search(context):
        flags.append(f"{prefix}_context_without_calendar_date")
    return flags


def _meaning_indicators(quote_text: str, meaning: str) -> list[str]:
    """Find strengthening concepts introduced by a meaning paraphrase."""
    if not _clean(meaning):
        return []
    return sorted(
        name for name, pattern in _MEANING_STRENGTHENING_PATTERNS.items()
        if pattern.search(meaning) and not pattern.search(quote_text)
    )


def _mtf_documents(source: dict[str, Any]) -> list[str]:
    """Return exact MTF document identities derived by production logic."""
    identity = canonical_source_identity(source)
    match = _MTF_IDENTITY.fullmatch(identity)
    return [] if match is None else [match.group(1)]


def _collection_mtf_documents(rows: Any) -> list[str]:
    """Return sorted exact MTF document numbers for source rows."""
    if not isinstance(rows, list):
        return []
    return sorted({
        number
        for row in rows if isinstance(row, dict)
        for number in _mtf_documents(row)
    })


def _packet_locator_mtf_documents(packet: dict[str, Any]) -> list[str]:
    """Project a packet locator through the canonical identity function."""
    locator = _clean(packet.get("stable_locator"))
    if not locator:
        return []
    return _mtf_documents({
        "stable_locator": locator,
        "source_title": locator,
    })


def _explicit_renderable_claims(audit: dict[str, Any]) -> set[str]:
    """Return claim names explicitly supported by public-eligible evidence."""
    claims: set[str] = set()
    for row in audit.get("renderable_sources", []):
        if not isinstance(row, dict):
            continue
        roles = set(row.get("assigned_roles", []))
        if not roles & _PUBLIC_EVIDENCE_ROLES:
            continue
        claims.update(
            field for field in row.get("claims_supported", [])
            if field in _CLAIM_FIELDS
        )
    return claims


def _claim_field_findings(
    packet: dict[str, Any], audit: dict[str, Any],
) -> list[dict[str, str]]:
    """Compare packet claims, evidence claims, and public field admission."""
    public_fields = set(audit.get("public_context_supported_fields", []))
    evidence_claims = _explicit_renderable_claims(audit)
    findings: list[dict[str, str]] = []
    for field in _CLAIM_FIELDS:
        packet_has_value = _known(packet.get(field))
        admitted = field in public_fields
        evidenced = field in evidence_claims
        if admitted and not packet_has_value:
            findings.append({
                "kind": "public_field_without_packet_value", "field": field,
            })
        if admitted and not evidenced:
            findings.append({
                "kind": "public_field_without_explicit_source_claim", "field": field,
            })
        if evidenced and not admitted:
            findings.append({
                "kind": "explicit_source_claim_not_admitted", "field": field,
            })
        if packet_has_value and not admitted:
            findings.append({
                "kind": "packet_claim_not_publicly_supported", "field": field,
            })
    return sorted(findings, key=lambda row: (row["kind"], row["field"]))


def _text_sha256(value: Any) -> str:
    """Hash one whitespace-normalised text value."""
    return hashlib.sha256(_clean(value).encode("utf-8")).hexdigest()


def _exact_tokens(value: Any) -> list[str]:
    """Return deterministic tokens for literal public-occurrence checks."""
    normalised = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.findall(r"[^\W_]+", normalised, flags=re.UNICODE)


def _contains_exact_token_sequence(haystack: Any, needle: Any) -> bool:
    """Return whether every projected claim token occurs contiguously."""
    haystack_tokens = _exact_tokens(haystack)
    needle_tokens = _exact_tokens(needle)
    if not needle_tokens or len(needle_tokens) > len(haystack_tokens):
        return False
    width = len(needle_tokens)
    return any(
        haystack_tokens[index:index + width] == needle_tokens
        for index in range(len(haystack_tokens) - width + 1)
    )


def _source_event_projection(value: Any) -> str:
    """Project a packet event exactly as the public Context formatter does."""
    event = _v2_clean(value)
    uncertain = re.fullmatch(r"unknown\s*\((.+)\)", event, re.I)
    if uncertain:
        qualifier = uncertain.group(1).strip()
        event = (
            "" if qualifier.casefold() == "attributed"
            else qualifier[0].upper() + qualifier[1:]
        )
    elif re.match(r"unknown\s+", event, re.I):
        remainder = re.sub(r"^unknown\s+", "", event, flags=re.I).strip()
        event = f"Attributed to a {remainder}" if remainder else ""
    return _v2_british_dates_in_text(event).rstrip(". :;-")


def _claim_projection(packet: dict[str, Any], field: str) -> str:
    """Return the formatter-visible projection of one packet claim value."""
    if field == "source_event":
        return _source_event_projection(packet.get(field))
    if field == "date":
        return _v2_british_date(packet.get(field))
    if field == "historical_context":
        return _v2_one_sentence(packet.get(field))
    raise ValueError(f"unsupported claim field: {field}")


def _current_public_exposures(
    projection: str,
    rendered: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Locate literal claim projections in each current public surface.

    A bibliographic occurrence is deliberately only an exposure diagnostic;
    it is not treated as evidence that the source supports the packet claim.
    """
    if rendered is None or not projection:
        return []
    sections = _sections(rendered.get("text", ""))
    candidates: list[tuple[str, str, int | None]] = [
        ("context", sections.get("context", ""), None),
        ("meaning", sections.get("meaning", ""), None),
    ]
    for index, source in enumerate(rendered.get("sources", [])):
        if not isinstance(source, dict):
            continue
        candidates.extend((
            ("source_title", _clean(source.get("title")), index),
            ("source_url", _clean(source.get("url")), index),
        ))
    exposure_kinds = {
        "context": "context_via_other_admitted_field",
        "meaning": "meaning_exact_occurrence",
        "source_title": "bibliographic_exact_occurrence",
        "source_url": "bibliographic_url_exact_occurrence",
    }
    exposures: list[dict[str, Any]] = []
    for surface, text, source_index in candidates:
        if not _contains_exact_token_sequence(text, projection):
            continue
        exposure: dict[str, Any] = {
            "surface": surface,
            "basis": "contiguous_nfkc_casefolded_alphanumeric_token_sequence",
            "exposure_kind": exposure_kinds[surface],
            "surface_text_sha256": _text_sha256(text),
        }
        if source_index is not None:
            exposure["public_source_index"] = source_index
        exposures.append(exposure)
    return sorted(
        exposures,
        key=lambda row: (
            _PUBLIC_SURFACES.index(row["surface"]),
            row.get("public_source_index", -1),
        ),
    )


def _source_record_id(row: dict[str, Any]) -> str:
    """Return a stable identifier for one audited source record."""
    for field in ("source_id", "source_fingerprint"):
        value = _clean(row.get(field))
        if value:
            return value
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "anonymous:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_claim_state(
    audit: dict[str, Any], field: str,
) -> tuple[str, list[str], list[str]]:
    """Classify explicit audited source claims without inferring from prose."""
    supporting_ids: set[str] = set()
    renderable_ids: set[str] = set()
    for collection in _AUDITED_SOURCE_COLLECTIONS:
        rows = audit.get(collection, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or field not in row.get(
                "claims_supported", []
            ):
                continue
            source_id = _source_record_id(row)
            supporting_ids.add(source_id)
            if collection == "renderable_sources":
                renderable_ids.add(source_id)
    if renderable_ids:
        state = "renderable_source_claim_not_admitted"
    elif supporting_ids:
        state = "internal_source_claim_only"
    else:
        state = "no_audited_source_claim"
    return state, sorted(supporting_ids), sorted(renderable_ids)


def _counterfactual_field_admission(
    packet: dict[str, Any],
    field: str,
    current_rendered: dict[str, Any] | None,
) -> dict[str, Any]:
    """Render a copied packet after admitting one field to the Context slot."""
    current_sections = _sections(
        "" if current_rendered is None else current_rendered.get("text", "")
    )
    current_context = current_sections.get("context", "")
    candidate = copy.deepcopy(packet)
    source_audit = candidate.get("_source_role_audit")
    if not isinstance(source_audit, dict):
        counterfactual = None
    else:
        fields = set(source_audit.get("public_context_supported_fields", []))
        fields.add(field)
        source_audit["public_context_supported_fields"] = sorted(fields)
        counterfactual = format_context_reply_public(candidate)
    counterfactual_context = _sections(
        "" if counterfactual is None else counterfactual.get("text", "")
    ).get("context", "")
    if field == "historical_context":
        if _v2_one_sentence(packet.get("immediate_subject")):
            effective_origin = "immediate_subject"
        elif _contains_exact_token_sequence(
            counterfactual_context,
            _claim_projection(packet, field),
        ):
            effective_origin = "historical_context"
        else:
            effective_origin = "none"
    else:
        projection = _claim_projection(packet, field)
        if _contains_exact_token_sequence(counterfactual_context, projection):
            effective_origin = field
        elif counterfactual is not None and counterfactual_context != current_context:
            effective_origin = "public_fallback"
        else:
            effective_origin = "none"
    return {
        "admitted_field": field,
        "public_render_succeeded": counterfactual is not None,
        "context_slot_changed": (
            counterfactual is not None
            and counterfactual_context != current_context
        ),
        "effective_value_origin": effective_origin,
        "direct_claim_value_selected": effective_origin == field,
        "current_context": current_context,
        "counterfactual_context": counterfactual_context,
        "current_context_sha256": _text_sha256(current_context),
        "counterfactual_context_sha256": _text_sha256(counterfactual_context),
    }


def _unsupported_claim_record(
    quote_id: str,
    packet: dict[str, Any],
    source_audit: dict[str, Any],
    finding: dict[str, str],
    current_rendered: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build one deterministic unsupported packet-claim decomposition row."""
    field = finding["field"]
    eligible = packet_is_attributed_to_margaret_thatcher(packet)
    claim_value = _clean(packet.get(field))
    projection = _claim_projection(packet, field)
    exposures = _current_public_exposures(projection, current_rendered)
    evidence_state, supporting_ids, renderable_ids = _source_claim_state(
        source_audit, field
    )
    counterfactual = _counterfactual_field_admission(
        packet, field, current_rendered
    )
    if exposures:
        reachability = "currently_rendered_exact_occurrence"
    elif (
        eligible
        and counterfactual["public_render_succeeded"]
        and counterfactual["context_slot_changed"]
        and counterfactual["direct_claim_value_selected"]
    ):
        reachability = "formatter_reachable_but_suppressed"
    else:
        reachability = "internal_only_or_unreachable"

    if exposures:
        slot_reachability = "currently_rendered_exact_occurrence"
    elif not eligible:
        slot_reachability = "production_ineligible"
    elif (
        counterfactual["public_render_succeeded"]
        and counterfactual["context_slot_changed"]
    ):
        slot_reachability = (
            "formatter_slot_reachable_but_not_currently_exposed"
        )
    else:
        slot_reachability = "formatter_slot_unreachable"

    reasons = ["field_not_admitted_by_source_role_audit"]
    if exposures:
        reasons.append("exact_claim_projection_occurs_in_current_public_output")
    if not eligible:
        reasons.append("attribution_ineligible")
    if (
        field == "historical_context"
        and counterfactual["effective_value_origin"] == "immediate_subject"
    ):
        reasons.append("shadowed_by_immediate_subject")
    if not counterfactual["public_render_succeeded"]:
        reasons.append("counterfactual_public_render_failed")
    elif not counterfactual["context_slot_changed"]:
        reasons.append("counterfactual_context_unchanged")
    if not counterfactual["direct_claim_value_selected"]:
        reasons.append("direct_claim_value_not_selected")

    unreachable_reason = None
    if reachability == "internal_only_or_unreachable":
        if not eligible:
            unreachable_reason = "attribution_ineligible"
        elif (
            field == "historical_context"
            and counterfactual["effective_value_origin"] == "immediate_subject"
        ):
            unreachable_reason = "shadowed_by_immediate_subject"
        elif not counterfactual["public_render_succeeded"]:
            unreachable_reason = "counterfactual_public_render_failed"
        elif not counterfactual["context_slot_changed"]:
            unreachable_reason = "counterfactual_context_unchanged"
        else:
            unreachable_reason = "direct_claim_value_not_selected"

    confidence_after = source_audit.get("confidence_after", {})
    return {
        "claim_key": f"{quote_id}:{field}:{finding['kind']}",
        "quote_id": quote_id,
        "quote_text_sha256": _text_sha256(packet.get("quote_text")),
        "attribution_eligible": eligible,
        "finding_kind": finding["kind"],
        "field": field,
        "claim_value": claim_value,
        "claim_value_sha256": _text_sha256(claim_value),
        "formatter_projection": projection,
        "confidence_after": _clean(
            confidence_after.get(field) if isinstance(confidence_after, dict) else ""
        ),
        "evidence_state": evidence_state,
        "supporting_internal_source_ids": supporting_ids,
        "supporting_renderable_source_ids": renderable_ids,
        "current_public_exposures": exposures,
        "current_public_context_supported_fields": sorted(set(
            source_audit.get("public_context_supported_fields", [])
        )),
        "counterfactual_projection": counterfactual,
        "public_reachability": reachability,
        "context_slot_reachability": slot_reachability,
        "exclusive_unreachable_reason": unreachable_reason,
        "reachability_reasons": sorted(set(reasons)),
    }


def _counter_matrix(
    rows: list[dict[str, Any]],
    outer_values: Iterable[str],
    inner_values: Iterable[str],
    outer_key: str,
    inner_key: str,
) -> dict[str, dict[str, int]]:
    """Return a complete deterministic two-dimensional count matrix."""
    counts = Counter((str(row[outer_key]), str(row[inner_key])) for row in rows)
    return {
        outer: {inner: counts[(outer, inner)] for inner in inner_values}
        for outer in outer_values
    }


def _unsupported_claim_counts(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate every explicit claim-decomposition dimension."""
    eligibility_rows = [
        {
            **row,
            "eligibility": (
                "eligible" if row["attribution_eligible"] else "ineligible"
            ),
        }
        for row in records
    ]
    evidence_states = (
        "no_audited_source_claim",
        "internal_source_claim_only",
        "renderable_source_claim_not_admitted",
    )
    public_surface_claim_counts: Counter[str] = Counter()
    exposure_kind_claim_counts: Counter[str] = Counter()
    public_surface_instance_counts: Counter[str] = Counter()
    exposure_kind_instance_counts: Counter[str] = Counter()
    field_surface_counts: Counter[tuple[str, str]] = Counter()
    for row in records:
        surfaces = {item["surface"] for item in row["current_public_exposures"]}
        kinds = {
            item["exposure_kind"] for item in row["current_public_exposures"]
        }
        public_surface_claim_counts.update(surfaces)
        exposure_kind_claim_counts.update(kinds)
        field_surface_counts.update((row["field"], surface) for surface in surfaces)
        public_surface_instance_counts.update(
            item["surface"] for item in row["current_public_exposures"]
        )
        exposure_kind_instance_counts.update(
            item["exposure_kind"] for item in row["current_public_exposures"]
        )
    return {
        "total": len(records),
        "by_finding_kind": dict(sorted(Counter(
            row["finding_kind"] for row in records
        ).items())),
        # Retain the historical machine-readable key for schema-v2
        # compatibility; the enclosing ``total`` is the authoritative count.
        "intended_argument_or_meaning_claims_in_this_1539_count": 0,
        "by_field": {
            field: sum(row["field"] == field for row in records)
            for field in _CLAIM_FIELDS
        },
        "by_attribution_eligibility": {
            label: sum(row["eligibility"] == label for row in eligibility_rows)
            for label in ("eligible", "ineligible")
        },
        "by_public_reachability": {
            value: sum(row["public_reachability"] == value for row in records)
            for value in _PUBLIC_REACHABILITY_VALUES
        },
        "by_field_and_public_reachability": _counter_matrix(
            records,
            _CLAIM_FIELDS,
            _PUBLIC_REACHABILITY_VALUES,
            "field",
            "public_reachability",
        ),
        "by_attribution_eligibility_and_public_reachability": _counter_matrix(
            eligibility_rows,
            ("eligible", "ineligible"),
            _PUBLIC_REACHABILITY_VALUES,
            "eligibility",
            "public_reachability",
        ),
        "by_current_public_surface": {
            surface: public_surface_claim_counts[surface]
            for surface in _PUBLIC_SURFACES
        },
        "by_field_and_current_public_surface": {
            field: {
                surface: field_surface_counts[(field, surface)]
                for surface in _PUBLIC_SURFACES
            }
            for field in _CLAIM_FIELDS
        },
        "by_current_public_exposure_kind": dict(sorted(
            exposure_kind_claim_counts.items()
        )),
        "current_public_exposure_instance_count": sum(
            public_surface_instance_counts.values()
        ),
        "by_current_public_surface_instance": {
            surface: public_surface_instance_counts[surface]
            for surface in _PUBLIC_SURFACES
        },
        "by_current_public_exposure_kind_instance": dict(sorted(
            exposure_kind_instance_counts.items()
        )),
        "by_evidence_state": {
            state: sum(row["evidence_state"] == state for row in records)
            for state in evidence_states
        },
        "by_field_and_evidence_state": _counter_matrix(
            records,
            _CLAIM_FIELDS,
            evidence_states,
            "field",
            "evidence_state",
        ),
        "by_context_slot_reachability": {
            value: sum(row["context_slot_reachability"] == value for row in records)
            for value in _CONTEXT_SLOT_REACHABILITY_VALUES
        },
        "by_exclusive_unreachable_reason": dict(sorted(Counter(
            row["exclusive_unreachable_reason"] for row in records
            if row["exclusive_unreachable_reason"] is not None
        ).items())),
    }


def _history_document(history_path: Path) -> dict[str, Any]:
    """Load and minimally validate the immutable production history."""
    value = json.loads(history_path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("items"), dict)
    ):
        raise RuntimeError("historical-context reply history is invalid")
    return value


def build_audit(
    research_dir: Path = DEFAULT_RESEARCH_DIR,
    history_path: Path = DEFAULT_HISTORY_PATH,
    *,
    reference_root: Path | None = None,
) -> dict[str, Any]:
    """Build the complete deterministic evidence-truth triage audit."""
    research_dir = research_dir.resolve()
    history_path = history_path.resolve()
    root = ROOT if reference_root is None else reference_root.resolve()
    packets, unresolved = load_and_validate_corpus(
        research_dir,
        require_source_role_audit=True,
    )
    history = _history_document(history_path)
    history_items = history["items"]

    completed_history: list[tuple[str, dict[str, Any]]] = []
    correlation_errors: list[dict[str, Any]] = []
    history_by_quote: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for parent_post_id, item in sorted(history_items.items()):
        if not isinstance(item, dict):
            correlation_errors.append({
                "kind": "malformed_history_item",
                "parent_post_id": str(parent_post_id),
            })
            continue
        if item.get("status") != "completed":
            correlation_errors.append({
                "kind": "noncompleted_history_item",
                "parent_post_id": str(parent_post_id),
                "status": _clean(item.get("status")),
            })
            continue
        completed_history.append((str(parent_post_id), item))
        quote_id = _clean(item.get("quote_id"))
        if quote_id not in packets:
            correlation_errors.append({
                "kind": "history_quote_missing_from_completed_packets",
                "parent_post_id": str(parent_post_id),
                "quote_id": quote_id,
            })
            continue
        history_by_quote.setdefault(quote_id, []).append((str(parent_post_id), item))

    packet_renderings: dict[str, dict[str, Any] | None] = {}
    render_failures: list[dict[str, str]] = []
    for quote_id in sorted(packets):
        rendered = format_context_reply_public(packets[quote_id])
        packet_renderings[quote_id] = rendered
        if rendered is None:
            render_failures.append({"quote_id": quote_id})

    history_context_records: list[dict[str, Any]] = []
    published_reply_records: list[dict[str, Any]] = []
    published_meanings: dict[str, list[tuple[str, str]]] = {}
    for parent_post_id, item in completed_history:
        quote_id = _clean(item.get("quote_id"))
        if quote_id not in packets:
            continue
        sections = _sections(str(item.get("reply_text") or ""))
        context = sections.get("context", "")
        meaning = sections.get("meaning", "")
        flags = _context_flags(context, "published")
        if flags:
            history_context_records.append({
                "parent_post_id": parent_post_id,
                "reply_post_id": _clean(item.get("reply_post_id")),
                "quote_id": quote_id,
                "quote_text": packets[quote_id]["quote_text"],
                "context": context,
                "flags": flags,
            })
        if meaning:
            published_meanings.setdefault(quote_id, []).append((parent_post_id, meaning))
        current = packet_renderings[quote_id]
        current_sections = _sections("" if current is None else current["text"])
        published_reply_records.append({
            "parent_post_id": parent_post_id,
            "reply_post_id": _clean(item.get("reply_post_id")),
            "quote_id": quote_id,
            "quote_text": packets[quote_id]["quote_text"],
            "published_reply_text": str(item.get("reply_text") or ""),
            "published_sections": sections,
            "current_public_reply_text": "" if current is None else current["text"],
            "current_sections": current_sections,
            "published_meaning_strengthening_indicators": _meaning_indicators(
                packets[quote_id]["quote_text"], meaning
            ),
            "current_meaning_strengthening_indicators": _meaning_indicators(
                packets[quote_id]["quote_text"],
                current_sections.get("meaning", ""),
            ),
        })

    claim_records: list[dict[str, Any]] = []
    unsupported_claim_records: list[dict[str, Any]] = []
    precise_mtf_records: list[dict[str, Any]] = []
    same_document_records: list[dict[str, Any]] = []
    meaning_records: list[dict[str, Any]] = []
    current_context_records: list[dict[str, Any]] = []
    current_render_records: list[dict[str, Any]] = []
    field_issue_counts: Counter[str] = Counter()
    indicator_counts: Counter[str] = Counter()

    for quote_id in sorted(packets):
        packet = packets[quote_id]
        source_audit = packet["_source_role_audit"]
        eligible = packet_is_attributed_to_margaret_thatcher(packet)
        rendered = packet_renderings[quote_id]
        public_fields = sorted(set(source_audit.get("public_context_supported_fields", [])))
        field_findings = _claim_field_findings(packet, source_audit)
        if field_findings:
            field_issue_counts.update(row["kind"] for row in field_findings)
            claim_records.append({
                "quote_id": quote_id,
                "attribution_eligible": eligible,
                "source_event": _clean(packet.get("source_event")),
                "date": _clean(packet.get("date")),
                "public_context_supported_fields": public_fields,
                "findings": field_findings,
            })
            unsupported_claim_records.extend(
                _unsupported_claim_record(
                    quote_id,
                    packet,
                    source_audit,
                    finding,
                    rendered,
                )
                for finding in field_findings
                if finding["kind"] == "packet_claim_not_publicly_supported"
            )

        rendered_sections = _sections("" if rendered is None else rendered["text"])
        current_render_records.append({
            "quote_id": quote_id,
            "attribution_eligible": eligible,
            "quote_text": packet["quote_text"],
            "public_reply_text": "" if rendered is None else rendered["text"],
            "public_context_supported_fields": public_fields,
            "public_sources": [] if rendered is None else [
                {
                    "title": _clean(source.get("title")),
                    "url": _clean(source.get("url")),
                    "claims_supported": list(source.get("claims_supported", [])),
                }
                for source in rendered.get("sources", [])
            ],
        })
        current_context = rendered_sections.get("context", "")
        current_flags = _context_flags(current_context, "current")
        if current_flags:
            current_context_records.append({
                "quote_id": quote_id,
                "attribution_eligible": eligible,
                "context": current_context,
                "flags": current_flags,
            })

        # Admission and packet metadata are deliberately separate.  A bare
        # packet locator is a useful research candidate, but it is not itself
        # evidence that passed the source-role gate.
        accepted_documents = _collection_mtf_documents(
            source_audit.get("renderable_sources")
        )
        packet_locator_candidates = _packet_locator_mtf_documents(packet)
        lead_documents = _collection_mtf_documents(
            source_audit.get("model_proposed_source_leads")
        )
        public_documents = _collection_mtf_documents(
            [] if rendered is None else rendered.get("sources", [])
        )
        if (
            accepted_documents
            or packet_locator_candidates
            or lead_documents
            or public_documents
        ):
            precise_mtf_records.append({
                "quote_id": quote_id,
                "attribution_eligible": eligible,
                "accepted_document_numbers": accepted_documents,
                "packet_locator_candidate_document_numbers": (
                    packet_locator_candidates
                ),
                "lead_document_numbers": lead_documents,
                "public_document_numbers": public_documents,
            })
        shared_documents = sorted(set(accepted_documents) & set(lead_documents))
        if shared_documents:
            suppressed = sorted(
                field for field in ("source_event", "date")
                if _known(packet.get(field)) and field not in public_fields
            )
            same_document_records.append({
                "quote_id": quote_id,
                "attribution_eligible": eligible,
                "quote_text": packet["quote_text"],
                "document_numbers": shared_documents,
                "source_event": _clean(packet.get("source_event")),
                "date": _clean(packet.get("date")),
                "public_context_supported_fields": public_fields,
                "suppressed_event_or_date_fields": suppressed,
                "published_parent_post_ids": [
                    parent for parent, _item in history_by_quote.get(quote_id, [])
                ],
            })

        surfaces: list[dict[str, Any]] = []
        for surface, meaning in (
            ("packet_intended_argument", _clean(packet.get("intended_argument"))),
            ("current_public_meaning", rendered_sections.get("meaning", "")),
        ):
            indicators = _meaning_indicators(packet["quote_text"], meaning)
            if indicators:
                surfaces.append({
                    "surface": surface,
                    "meaning": meaning,
                    "indicators": indicators,
                })
        for parent_post_id, meaning in published_meanings.get(quote_id, []):
            indicators = _meaning_indicators(packet["quote_text"], meaning)
            if indicators:
                surfaces.append({
                    "surface": "published_public_meaning",
                    "parent_post_id": parent_post_id,
                    "meaning": meaning,
                    "indicators": indicators,
                })
        if surfaces:
            all_indicators = sorted({
                indicator for surface in surfaces
                for indicator in surface["indicators"]
            })
            indicator_counts.update(all_indicators)
            meaning_records.append({
                "quote_id": quote_id,
                "attribution_eligible": eligible,
                "quote_text": packet["quote_text"],
                "indicators": all_indicators,
                "surfaces": surfaces,
            })

    completed_history_quote_ids = sorted(
        _clean(item.get("quote_id")) for _parent, item in completed_history
    )
    eligible_ids = sorted(
        quote_id for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    )
    same_document_priority_records = [
        row for row in same_document_records
        if row["attribution_eligible"] and row["suppressed_event_or_date_fields"]
    ]
    published_meaning_ids = {
        row["quote_id"] for row in meaning_records
        if any(
            surface["surface"] == "published_public_meaning"
            for surface in row["surfaces"]
        )
    }
    history_generic_count = sum(
        "published_generic_context" in row["flags"]
        for row in history_context_records
    )
    history_no_date_count = sum(
        "published_context_without_calendar_date" in row["flags"]
        for row in history_context_records
    )
    current_generic_count = sum(
        "current_generic_context" in row["flags"]
        for row in current_context_records
    )
    current_no_date_count = sum(
        "current_context_without_calendar_date" in row["flags"]
        for row in current_context_records
    )
    unsupported_claim_records.sort(key=lambda row: row["claim_key"])
    unsupported_claim_counts = _unsupported_claim_counts(
        unsupported_claim_records
    )

    invariants = {
        "completed_packet_count_is_626": len(packets) == EXPECTED_COMPLETED_PACKET_COUNT,
        "published_history_includes_77_entry_review_baseline": (
            len(completed_history) >= REVIEW_BASELINE_PUBLISHED_HISTORY_COUNT
        ),
        "attribution_eligible_count_is_610": (
            len(eligible_ids) == EXPECTED_ATTRIBUTION_ELIGIBLE_COUNT
        ),
        "every_history_quote_correlates_to_a_completed_packet": not correlation_errors,
        "every_completed_packet_has_a_current_public_render": not render_failures,
        "history_parent_post_ids_are_unique": (
            len(completed_history)
            == len({parent for parent, _item in completed_history})
        ),
    }
    source_role_path = research_dir / AUDIT_FILENAME
    input_hashes = {
        "research_packets.json": _file_sha256(research_dir / "research_packets.json"),
        AUDIT_FILENAME: _file_sha256(source_role_path),
        "historical_context_reply_history.json": _file_sha256(history_path),
        "corpus_manifest.json": _file_sha256(research_dir / "corpus_manifest.json"),
        "quote_analysis.json": _file_sha256(root / "quote_analysis.json"),
        "mrsMThatcher.txt": _file_sha256(root / "mrsMThatcher.txt"),
    }
    for relative in (PACKET_CORRECTIONS_FILENAME, CURATED_EVIDENCE_FILENAME):
        path = research_dir / relative
        if path.exists():
            input_hashes[relative] = _file_sha256(path)
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_kind": AUDIT_KIND,
        "interpretation": {
            "finding_status": "editorial_triage_only",
            "automatic_evidence_rewrite_authorised": False,
            "meaning_indicators_are_semantic_review_leads_not_error_findings": True,
            "packet_claim_presence_is_not_treated_as_source_support": True,
            "literal_public_occurrence_is_not_treated_as_source_support": True,
            "source_title_occurrence_classification": (
                "bibliographic_occurrence_risk_not_semantic_adjudication"
            ),
        },
        "unsupported_claim_decomposition_definitions": {
            "scope": (
                "packet_claim_not_publicly_supported findings for source_event, "
                "date, and historical_context only"
            ),
            "intended_argument_or_meaning_scope": (
                "The 1,539 figure contains zero intended_argument or Meaning "
                "claims; Meaning is a separate semantic-review surface."
            ),
            "currently_rendered_exact_occurrence": (
                "The formatter-visible packet-field projection occurs as one "
                "contiguous NFKC-casefolded alphanumeric token sequence in "
                "current public Context, Meaning, source title, or source URL."
            ),
            "formatter_reachable_but_suppressed": (
                "The packet is attribution-eligible and counterfactually "
                "admitting the field changes Context through the actual public "
                "formatter, with that packet field selected as the value origin."
            ),
            "internal_only_or_unreachable": (
                "The claim is neither a current literal occurrence nor a "
                "direct formatter-reachable eligible packet-field value."
            ),
            "context_slot_reachability": (
                "A separate gate-level measure: historical_context admission "
                "may change Context by selecting immediate_subject rather than "
                "the packet historical_context value."
            ),
            "bibliographic_exact_occurrence": (
                "A public source-title occurrence is an editorial exposure risk, "
                "not proof that an audited source supports the packet claim."
            ),
        },
        "input_hashes": input_hashes,
        "coverage": {
            "completed_packet_count": len(packets),
            "unresolved_quote_count": len(unresolved),
            "attribution_eligible_packet_count": len(eligible_ids),
            "published_history_entry_count": len(completed_history),
            "published_reply_review_record_count": len(published_reply_records),
            "current_public_render_record_count": len(current_render_records),
            "published_history_unique_quote_count": len(set(completed_history_quote_ids)),
            "completed_quote_ids_sha256": _sequence_sha256(sorted(packets)),
            "published_history_parent_post_ids_sha256": _sequence_sha256(
                parent for parent, _item in completed_history
            ),
            "published_history_quote_ids_sha256": _sequence_sha256(
                completed_history_quote_ids
            ),
        },
        "counts": {
            "correlation_error_count": len(correlation_errors),
            "current_render_failure_count": len(render_failures),
            "published_generic_context_count": history_generic_count,
            "published_context_without_calendar_date_count": history_no_date_count,
            "current_generic_context_count": current_generic_count,
            "current_context_without_calendar_date_count": current_no_date_count,
            "claim_public_field_triage_packet_count": len(claim_records),
            "claim_public_field_triage_finding_count": sum(
                len(row["findings"]) for row in claim_records
            ),
            "claim_public_field_finding_kind_counts": dict(sorted(field_issue_counts.items())),
            "unsupported_claim_decomposition_count": len(
                unsupported_claim_records
            ),
            "precise_mtf_identity_packet_count": len(precise_mtf_records),
            "accepted_and_lead_same_mtf_document_packet_count": len(same_document_records),
            "eligible_same_document_event_or_date_priority_count": (
                len(same_document_priority_records)
            ),
            "meaning_strengthening_indicator_packet_count": len(meaning_records),
            "published_meaning_strengthening_indicator_packet_count": (
                len(published_meaning_ids)
            ),
            "meaning_strengthening_indicator_counts": dict(sorted(indicator_counts.items())),
            "invariant_failure_count": sum(not value for value in invariants.values()),
        },
        "records": {
            "correlation_errors": correlation_errors,
            "render_failures": render_failures,
            "published_reply_reviews": published_reply_records,
            "current_public_renderings": current_render_records,
            "published_generic_or_no_date_contexts": history_context_records,
            "current_generic_or_no_date_contexts": current_context_records,
            "claim_public_field_triage": claim_records,
            "unsupported_claim_decomposition": unsupported_claim_records,
            "precise_mtf_identities": precise_mtf_records,
            "accepted_and_lead_same_mtf_document": same_document_records,
            "eligible_same_document_event_or_date_priorities": (
                same_document_priority_records
            ),
            "meaning_strengthening_indicators": meaning_records,
        },
        "unsupported_claim_counts": unsupported_claim_counts,
        "invariants": invariants,
    }


def _validated_output_path(
    output: Path,
    *,
    research_dir: Path,
    history_path: Path,
    overwrite: bool,
) -> Path:
    """Reject protected, ambiguous, or accidental output targets."""
    if output.is_symlink():
        raise ValueError("--output must not be a symbolic link")
    resolved = output.resolve(strict=False)
    research = research_dir.resolve()
    protected = {
        history_path.resolve(),
        (ROOT / "mrsMThatcher.txt").resolve(),
        (ROOT / "quote_analysis.json").resolve(),
        Path(__file__).resolve(),
    }
    if resolved in protected or resolved == research or research in resolved.parents:
        raise ValueError("--output must be outside immutable production inputs")
    if resolved.exists():
        if not resolved.is_file():
            raise ValueError("--output must be a regular file")
        if not overwrite:
            raise FileExistsError("--output already exists; pass --overwrite")
        try:
            prior = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("--overwrite may replace only a prior audit artifact") from exc
        if not isinstance(prior, dict) or prior.get("audit_kind") != AUDIT_KIND:
            raise ValueError("--overwrite may replace only a prior audit artifact")
    return resolved


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    """Atomically write the sole explicit output artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    """Run the offline audit and write its deterministic JSON output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", type=Path, default=DEFAULT_RESEARCH_DIR)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    output = _validated_output_path(
        args.output,
        research_dir=args.research_dir,
        history_path=args.history,
        overwrite=args.overwrite,
    )
    audit = build_audit(args.research_dir, args.history)
    _atomic_write(output, audit)
    print(json.dumps({
        "output": str(output),
        "completed_packets": audit["coverage"]["completed_packet_count"],
        "published_history_entries": audit["coverage"]["published_history_entry_count"],
        "triage_records": sum(
            len(records) for records in audit["records"].values()
        ),
        "invariant_failures": audit["counts"]["invariant_failure_count"],
    }, sort_keys=True))
    return 0 if audit["counts"]["invariant_failure_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
