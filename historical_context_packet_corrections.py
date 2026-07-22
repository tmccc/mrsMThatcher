"""Validate and apply reviewed corrections to historical-context packet prose.

The canonical research packets remain immutable.  This sidecar permits a
small, hash-bound correction to editorial prose used for future rendering
without rewriting provider research or changing quotation identity.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from historical_context_source_curated_evidence import validate_curated_evidence


PACKET_CORRECTIONS_SCHEMA_VERSION = 1
PACKET_CORRECTIONS_POLICY_VERSION = (
    "historical-context-packet-corrections-v1-intended-argument-only"
)
PACKET_CORRECTIONS_FILENAME = "historical_context_packet_corrections.json"

_HEX64 = re.compile(r"[0-9a-f]{64}")
_MANIFEST_FIELDS = {
    "schema_version",
    "policy_version",
    "created_at",
    "correction_count",
    "items",
}
_ITEM_FIELDS = {
    "correction_id",
    "quote_id",
    "quote_text_sha256",
    "field",
    "original_value_sha256",
    "corrected_value",
    "corrected_value_sha256",
    "evidence_source_ids",
    "rationale",
    "reviewed_at",
}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def packet_correction_id(item: dict[str, Any]) -> str:
    """Return the stable identity of a reviewed packet correction."""
    identity = {
        field: item.get(field)
        for field in (
            "quote_id",
            "quote_text_sha256",
            "field",
            "original_value_sha256",
            "corrected_value_sha256",
            "evidence_source_ids",
        )
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_packet_corrections(
    manifest: dict[str, Any],
    packets: dict[str, dict[str, Any]],
    curated_evidence: dict[str, Any],
) -> None:
    """Fail closed on stale, broad or unsupported editorial corrections."""
    validate_curated_evidence(curated_evidence, packets)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != _MANIFEST_FIELDS
        or manifest.get("schema_version") != PACKET_CORRECTIONS_SCHEMA_VERSION
        or manifest.get("policy_version") != PACKET_CORRECTIONS_POLICY_VERSION
        or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", str(manifest.get("created_at") or "")
        )
        or not isinstance(manifest.get("items"), dict)
    ):
        raise RuntimeError("historical-context packet correction manifest is invalid")

    if not isinstance(curated_evidence, dict):
        raise RuntimeError("historical-context packet correction evidence is invalid")
    curated_items = curated_evidence.get("items")
    if not isinstance(curated_items, dict):
        raise RuntimeError("historical-context packet correction evidence is invalid")

    items = manifest["items"]
    if (
        type(manifest.get("correction_count")) is not int
        or manifest.get("correction_count") != len(items)
    ):
        raise RuntimeError("historical-context packet correction count differs")

    correction_ids: set[str] = set()
    for quote_id, item in items.items():
        packet = packets.get(quote_id)
        if (
            packet is None
            or not isinstance(item, dict)
            or set(item) != _ITEM_FIELDS
            or item.get("quote_id") != quote_id
            or item.get("quote_text_sha256")
            != _sha256_text(str(packet.get("quote_text") or ""))
            or item.get("field") != "intended_argument"
            or item.get("original_value_sha256")
            != _sha256_text(str(packet.get("intended_argument") or ""))
        ):
            raise RuntimeError(
                f"historical-context packet correction identity differs: {quote_id}"
            )

        corrected = item.get("corrected_value")
        evidence_source_ids = item.get("evidence_source_ids")
        correction_id = item.get("correction_id")
        if (
            not isinstance(corrected, str)
            or not corrected.strip()
            or corrected != corrected.strip()
            or corrected == packet.get("intended_argument")
            or "\n" in corrected
            or len(corrected) > 500
            or item.get("corrected_value_sha256") != _sha256_text(corrected)
            or not isinstance(evidence_source_ids, list)
            or not evidence_source_ids
            or len(set(evidence_source_ids)) != len(evidence_source_ids)
            or any(not _HEX64.fullmatch(str(value or "")) for value in evidence_source_ids)
            or not str(item.get("rationale") or "").strip()
            or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", str(item.get("reviewed_at") or "")
            )
            or correction_id != packet_correction_id(item)
            or correction_id in correction_ids
        ):
            raise RuntimeError(
                f"historical-context packet correction is invalid: {quote_id}"
            )

        curated_sources = (
            curated_items.get(quote_id, {}).get("sources", [])
            if isinstance(curated_items.get(quote_id), dict)
            else []
        )
        sources_by_id = {
            source.get("source_id"): source
            for source in curated_sources
            if isinstance(source, dict)
        }
        for source_id in evidence_source_ids:
            source = sources_by_id.get(source_id)
            if (
                source is None
                or source.get("source_quality_class") != "strong_primary_evidence"
                or source.get("page_independently_inspected") is not True
                or not {
                    "wording_verification",
                    "attribution_support",
                }.issubset(set(source.get("assigned_roles", [])))
                or not {
                    "wording",
                    "attribution",
                }.issubset(set(source.get("claims_supported", [])))
            ):
                raise RuntimeError(
                    "historical-context packet correction lacks reviewed primary "
                    f"evidence: {quote_id}"
                )
        correction_ids.add(correction_id)


def apply_packet_corrections(
    manifest: dict[str, Any],
    packets: dict[str, dict[str, Any]],
    curated_evidence: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return a rendering view with only reviewed intended arguments replaced."""
    validate_packet_corrections(manifest, packets, curated_evidence)
    corrected_packets = dict(packets)
    for quote_id, correction in manifest["items"].items():
        corrected_packets[quote_id] = {
            **packets[quote_id],
            "intended_argument": correction["corrected_value"],
        }
    return corrected_packets
