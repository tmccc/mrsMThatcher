"""Recover direct source provenance from saved quotation-research responses.

The canonical packet export retained opaque grounding redirects.  The original
provider responses also contain direct citation metadata and model-proposed
source URLs.  This module recovers the former as evidence and the latter only
as research leads.  It performs no network access.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


RECOVERY_SCHEMA_VERSION = 1
RECOVERY_POLICY_VERSION = "saved-provider-citation-recovery-v1"
RECOVERY_FILENAME = "historical_context_source_recovery.json"
_REDIRECT_HOST = "vertexaisearch.cloud.google.com"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tokens(value: Any) -> list[str]:
    text = str(value or "").casefold().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.findall(r"[a-z0-9]+", text)


def _direct_http_url(value: Any) -> str | None:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.netloc.casefold() == _REDIRECT_HOST and parsed.path.startswith("/grounding-api-redirect/"):
        return None
    return text


def _candidate_text(candidate: dict[str, Any]) -> str:
    parts = (candidate.get("content") or {}).get("parts") or []
    return "".join(
        str(part.get("text") or "")
        for part in parts
        if isinstance(part, dict)
    )


def _parsed_payload(document: dict[str, Any]) -> dict[str, Any] | None:
    parsed = document.get("parsed")
    if isinstance(parsed, dict):
        return parsed
    for candidate in document.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        text = _candidate_text(candidate).strip()
        if text.startswith("```json") and text.endswith("```"):
            text = text[7:-3].strip()
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _candidate_payload(candidate: dict[str, Any]) -> dict[str, Any] | None:
    """Decode one candidate payload without trusting a top-level parsed cache."""
    text = _candidate_text(candidate).strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _citations(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = candidate.get("citation_metadata") or candidate.get("citationMetadata") or {}
    rows = metadata.get("citations") or metadata.get("citationSources") or []
    return [row for row in rows if isinstance(row, dict)]


def _citation_offsets(citation: dict[str, Any]) -> tuple[Any, Any]:
    return (
        citation.get("start_index", citation.get("startIndex")),
        citation.get("end_index", citation.get("endIndex")),
    )


def _source_title(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.netloc.casefold().removeprefix("www.")
    mtf = re.fullmatch(r"/document/(\d{5,})/?", parsed.path)
    if host == "margaretthatcher.org" and mtf:
        return f"Margaret Thatcher Foundation document {mtf.group(1)}"
    return f"{host}{parsed.path}".rstrip("/")


def recover_saved_source_evidence(
    research_dir: Path,
    packets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Recover direct citations and untrusted source leads from raw responses."""
    raw_dir = research_dir / "raw_responses"
    response_paths = sorted(
        path for path in raw_dir.glob("*/*.json")
        if not path.name.endswith("_extracted.json")
    )
    files: dict[str, dict[str, Any]] = {}
    citations_by_quote: dict[str, dict[str, dict[str, Any]]] = {}
    leads_by_quote: dict[str, dict[str, dict[str, Any]]] = {}
    matching_response_count = 0
    rejected_identity_count = 0

    for path in response_paths:
        relative = str(path.relative_to(research_dir))
        raw_sha256 = _file_sha256(path)
        files[relative] = {"size": path.stat().st_size, "sha256": raw_sha256}
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(document, dict):
            continue
        quote_id = path.parent.name
        packet = packets.get(quote_id)
        parsed = _parsed_payload(document)
        if (
            packet is None
            or not isinstance(parsed, dict)
            or parsed.get("quote_id") != quote_id
            or _tokens(parsed.get("quote_text")) != _tokens(packet.get("quote_text"))
        ):
            rejected_identity_count += 1
            continue
        matching_response_count += 1

        for source in parsed.get("sources", []):
            if not isinstance(source, dict):
                continue
            url = _direct_http_url(source.get("url"))
            if not url:
                continue
            lead = {
                "lead_id": _sha256_bytes(_canonical_json({
                    "quote_id": quote_id,
                    "url": url,
                    "title": str(source.get("title") or "").strip(),
                })),
                "title": str(source.get("title") or "").strip() or _source_title(url),
                "url": url,
                "source_type": str(source.get("source_type") or "").strip(),
                "supports": [
                    " ".join(str(value).split())
                    for value in source.get("supports", [])
                    if str(value).strip()
                ],
                "provenance": {
                    "kind": "model_proposed_source_lead",
                    "raw_response_path": relative,
                    "raw_response_sha256": raw_sha256,
                },
            }
            leads_by_quote.setdefault(quote_id, {})[lead["lead_id"]] = lead

        for candidate_index, candidate in enumerate(document.get("candidates", [])):
            if not isinstance(candidate, dict):
                continue
            candidate_payload = _candidate_payload(candidate)
            if (
                candidate_payload is not None
                and (
                    candidate_payload.get("quote_id") != quote_id
                    or _tokens(candidate_payload.get("quote_text"))
                    != _tokens(packet.get("quote_text"))
                )
            ):
                continue
            response_text = _candidate_text(candidate)
            for citation_index, citation in enumerate(_citations(candidate)):
                url = _direct_http_url(citation.get("uri"))
                start, end = _citation_offsets(citation)
                if (
                    not url
                    or type(start) is not int
                    or type(end) is not int
                    or not 0 <= start < end <= len(response_text)
                ):
                    continue
                segment = response_text[start:end]
                citation_id = _sha256_bytes(_canonical_json({
                    "quote_id": quote_id,
                    "url": url,
                    "segment": segment,
                }))
                row = {
                    "citation_id": citation_id,
                    "title": _source_title(url),
                    "url": url,
                    "cited_response_segment": segment,
                    "cited_response_segment_sha256": _sha256_bytes(segment.encode()),
                    "provenance": {
                        "kind": "provider_citation_metadata",
                        "raw_response_path": relative,
                        "raw_response_sha256": raw_sha256,
                        "candidate_index": candidate_index,
                        "citation_index": citation_index,
                        "start_index": start,
                        "end_index": end,
                    },
                }
                citations_by_quote.setdefault(quote_id, {})[citation_id] = row

    items = {
        quote_id: {
            "quote_id": quote_id,
            "quote_text_sha256": _sha256_bytes(packets[quote_id]["quote_text"].encode()),
            "citation_sources": sorted(
                citations_by_quote.get(quote_id, {}).values(),
                key=lambda row: (row["url"], row["citation_id"]),
            ),
            "model_proposed_source_leads": sorted(
                leads_by_quote.get(quote_id, {}).values(),
                key=lambda row: (row["url"], row["lead_id"]),
            ),
        }
        for quote_id in sorted(packets)
    }
    citation_count = sum(len(row["citation_sources"]) for row in items.values())
    lead_count = sum(len(row["model_proposed_source_leads"]) for row in items.values())
    response_set_hash = _sha256_bytes(_canonical_json(files))
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "policy_version": RECOVERY_POLICY_VERSION,
        "research_packet_count": len(packets),
        "raw_response_file_count": len(response_paths),
        "matching_response_count": matching_response_count,
        "identity_rejected_response_count": rejected_identity_count,
        "raw_response_set_sha256": response_set_hash,
        "raw_response_files": files,
        "citation_source_count": citation_count,
        "citation_quote_count": sum(bool(row["citation_sources"]) for row in items.values()),
        "model_proposed_lead_count": lead_count,
        "model_proposed_lead_quote_count": sum(
            bool(row["model_proposed_source_leads"]) for row in items.values()
        ),
        "items": items,
    }


def validate_recovery(
    recovery: dict[str, Any],
    packets: dict[str, dict[str, Any]],
) -> None:
    """Fail closed if a saved recovery manifest does not match the corpus."""
    if (
        not isinstance(recovery, dict)
        or recovery.get("schema_version") != RECOVERY_SCHEMA_VERSION
        or recovery.get("policy_version") != RECOVERY_POLICY_VERSION
        or recovery.get("research_packet_count") != len(packets)
        or set(recovery.get("items", {})) != set(packets)
    ):
        raise RuntimeError("historical-context source recovery manifest is incompatible")
    for quote_id, packet in packets.items():
        row = recovery["items"][quote_id]
        if row.get("quote_text_sha256") != _sha256_bytes(packet["quote_text"].encode()):
            raise RuntimeError(f"historical-context source recovery changed quote identity: {quote_id}")
        for citation in row.get("citation_sources", []):
            if (
                not _direct_http_url(citation.get("url"))
                or not citation.get("cited_response_segment")
                or citation.get("cited_response_segment_sha256")
                != _sha256_bytes(citation["cited_response_segment"].encode())
            ):
                raise RuntimeError(f"invalid recovered source citation: {quote_id}")
