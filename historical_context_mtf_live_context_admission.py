#!/usr/bin/env python3
"""Admit the human-reviewed live-MTF context bindings deterministically.

The private review pack and fetched bodies remain outside Git.  This tool binds
their hashes, revalidates every selected primary document, updates only the
reviewed packet/source fields, and writes a transition record containing no
private paths or fetched bodies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from historical_context_source_curated_evidence import curated_source_id


TRANSITION_DATE = "2026-07-27"
TRANSITION_TIMESTAMP = "2026-07-27T00:10:19Z"
PRODUCTION_BASE_COMMIT = "99c141d7fc9cd85478c1f70510db8311cdebd4ef"
REVIEW_PACK_SHA256 = (
    "7ae33a6bb5d54e70d936c96632c08b571329ec59e58dedde1cd79dfa779c2bb3"
)
COMPARISON_SHA256 = (
    "ebd1063b1e62c0f18c11caa3f75a6a8393019ee43e8a350139a619215292b756"
)
FINAL_VALIDATION_SHA256 = (
    "625800e4716e44fb120d0380589fd831d92010536a90f35aac27942cd5dd72a2"
)
EXPECTED_RECORD_COUNT = 422
EXPECTED_PRIORITY_COUNTS = {
    "A_BLOCKED_REMEDIATION": 8,
    "B_EXISTING_BINDING_UPGRADE": 80,
    "B_NEW_PRIMARY_BINDING": 258,
    "C_CONTEXT_REVIEW": 76,
}
EXPECTED_MATCH_COUNTS = {
    "exact_quotation": 327,
    "recorded_variant": 95,
}
REVIEWED_MTF_AUTHOR_LABELS = {
    "Margaret Thatcher",
    "Archive (Thatcher MSS)",
    "Thatcher memoirs",
}
DECISIONS_FILENAME = (
    "historical_context_mtf_live_context_admission_decisions.json"
)
AUDIT_FILENAME = "historical_context_mtf_live_context_admission_audit.json"
TRANSITION_FILENAME = (
    "historical_context_v9_mtf_live_context_transition_manifest.json"
)
SHADOW_REBIND_AUDIT_FILENAME = (
    "historical_context_mtf_live_context_shadow_rebind_audit.json"
)
SHADOW_MANIFEST_PATH = (
    Path("semantic_alignment_research")
    / "quote_attribution_cleanup_001"
    / "deployment_candidate"
    / "material_veto_v3_shadow_manifest.json"
)
SHADOW_REBIND_ALLOWED_SOURCES = {
    "completed_quote_research",
    "runtime_eligible_quote_manifest",
}


class AdmissionError(RuntimeError):
    """Raised when reviewed evidence or a canonical invariant differs."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AdmissionError(f"expected JSON object: {path}")
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
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


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
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


def _comparison_text(value: str) -> str:
    for old, new in (
        ("’", "'"),
        ("‘", "'"),
        ("“", '"'),
        ("”", '"'),
        ("–", "-"),
        ("—", "-"),
        ("\u00ad", ""),
    ):
        value = value.replace(old, new)
    value = re.sub(r"\[end p\d+\]", " ", value, flags=re.I)
    return _clean(value).casefold().rstrip(".")


def _candidate_id(row: dict[str, Any]) -> str:
    identity = {
        "canonical_url": row["canonical_url"],
        "match_type": row["match"]["match_type"],
        "matched_recorded_wording": row["matched_recorded_wording"],
        "page_text_sha256": row["page_text_sha256"],
        "quote_id": row["quote_id"],
        "retrieved_file_sha256": row["retrieved_file_sha256"],
    }
    return _sha256_bytes(_canonical_json_bytes(identity))


def _variant_notes(differences: list[dict[str, Any]]) -> str:
    if not differences:
        return (
            "Primary variant. The reviewed MTF wording differs materially "
            "from the stored quotation; the structured transition record "
            "preserves the reviewed source and stored forms."
        )
    descriptions: list[str] = []
    for change in differences:
        operation = _clean(change.get("operation"))
        stored = " ".join(str(value) for value in change.get("stored_tokens", []))
        matched = " ".join(
            str(value) for value in change.get("matched_tokens", [])
        )
        if operation == "insert":
            descriptions.append(f"the source inserts “{matched}”")
        elif operation == "delete":
            descriptions.append(f"the stored quotation adds “{stored}”")
        else:
            descriptions.append(
                f"the source has “{matched}” where the stored quotation has "
                f"“{stored}”"
            )
    return (
        "Primary variant. Reviewed differences: "
        + "; ".join(descriptions)
        + "."
    )


def _verify_review_inputs(
    review_dir: Path,
    runs_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    paths = {
        "human_review_pack.md": review_dir / "human_review_pack.md",
        "canonical_comparison.json": review_dir / "canonical_comparison.json",
        "final_validation.json": review_dir / "final_validation.json",
    }
    expected = {
        "human_review_pack.md": REVIEW_PACK_SHA256,
        "canonical_comparison.json": COMPARISON_SHA256,
        "final_validation.json": FINAL_VALIDATION_SHA256,
    }
    hashes = {name: _file_sha256(path) for name, path in paths.items()}
    if hashes != expected:
        raise AdmissionError("reviewed live-MTF inputs differ")
    final = _load(paths["final_validation.json"])
    comparison = _load(paths["canonical_comparison.json"])
    rows = comparison.get("records")
    if (
        final.get("status") != "complete"
        or final.get("automatic_admissions") != 0
        or final.get("snippets_used_as_evidence") is not False
        or final.get("accepted_candidate_quote_count") != EXPECTED_RECORD_COUNT
        or not isinstance(rows, list)
        or len(rows) != EXPECTED_RECORD_COUNT
        or comparison.get("priority_counts") != EXPECTED_PRIORITY_COUNTS
    ):
        raise AdmissionError("reviewed live-MTF scope differs")
    if Counter(row.get("priority") for row in rows) != Counter(
        EXPECTED_PRIORITY_COUNTS
    ):
        raise AdmissionError("live-MTF priority records differ")
    if Counter(
        row.get("match", {}).get("match_type") for row in rows
    ) != Counter(EXPECTED_MATCH_COUNTS):
        raise AdmissionError("live-MTF wording decisions differ")

    verified_rows: list[dict[str, Any]] = []
    seen_quote_ids: set[str] = set()
    for row in rows:
        quote_id = _clean(row.get("quote_id"))
        metadata = row.get("document_metadata")
        match = row.get("match")
        document_number = _clean(row.get("document_number"))
        canonical_url = (
            f"https://www.margaretthatcher.org/document/{document_number}"
        )
        if (
            not re.fullmatch(r"[0-9a-f]{64}", quote_id)
            or quote_id in seen_quote_ids
            or not isinstance(metadata, dict)
            or not isinstance(match, dict)
            or row.get("canonical_url") != canonical_url
            or metadata.get("canonical_url") != canonical_url
            or metadata.get("document_number") != document_number
            or metadata.get("author") not in REVIEWED_MTF_AUTHOR_LABELS
            or metadata.get("valid") is not True
            or metadata.get("status") != "validated_official_document"
            or not _clean(metadata.get("title"))
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", _clean(metadata.get("date")))
            or match.get("match_type") not in EXPECTED_MATCH_COUNTS
            or not _clean(match.get("supporting_passage"))
            or not _clean(match.get("surrounding_context"))
            or not re.fullmatch(
                r"[0-9a-f]{64}", _clean(row.get("retrieved_file_sha256"))
            )
            or not re.fullmatch(
                r"[0-9a-f]{64}", _clean(row.get("page_text_sha256"))
            )
        ):
            raise AdmissionError(f"reviewed live-MTF row is invalid: {quote_id}")
        artifact = runs_root / _clean(row.get("source_body_artifact"))
        if (
            not artifact.is_file()
            or artifact.is_symlink()
            or runs_root.resolve() not in artifact.resolve().parents
        ):
            raise AdmissionError(f"reviewed source body is unavailable: {quote_id}")
        before = artifact.stat()
        body = artifact.read_bytes()
        after = artifact.stat()
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or _sha256_bytes(body) != row["retrieved_file_sha256"]
        ):
            raise AdmissionError(f"reviewed source body changed: {quote_id}")
        soup = BeautifulSoup(body, "lxml")
        article = (
            soup.select_one("article.node-archive-document")
            or soup.select_one("#documentbody")
        )
        if article is None:
            raise AdmissionError(f"reviewed MTF article body is absent: {quote_id}")
        article_text = _clean(article.get_text(" ", strip=True))
        if _sha256_text(article_text) != row["page_text_sha256"]:
            raise AdmissionError(f"reviewed MTF article text changed: {quote_id}")
        if (
            _comparison_text(match["supporting_passage"])
            not in _comparison_text(article_text)
        ):
            raise AdmissionError(f"reviewed passage is absent: {quote_id}")
        seen_quote_ids.add(quote_id)
        verified_rows.append({**row, "review_candidate_id": _candidate_id(row)})
    return verified_rows, hashes


def _new_curated_source(
    quote_id: str,
    packet: dict[str, Any],
    row: dict[str, Any],
    *,
    supports_event: bool,
) -> dict[str, Any]:
    match_kind = (
        "exact"
        if row["match"]["match_type"] == "exact_quotation"
        else "historical_variant"
    )
    context = _clean(packet["historical_context"])
    passage = _clean(row["match"]["supporting_passage"])
    source_type = (
        "official_primary_transcript"
        if row["document_metadata"]["author"] == "Margaret Thatcher"
        else "official_primary_archive_catalogue"
    )
    assigned_roles = [
        "wording_verification",
        "attribution_support",
        *(['source_event_support'] if supports_event else []),
        "historical_context_support",
    ]
    claims_supported = [
        "wording",
        "attribution",
        *(['source_event', 'date'] if supports_event else []),
        "historical_context",
    ]
    source = {
        "assigned_roles": assigned_roles,
        "author_or_speaker": "Margaret Thatcher",
        "canonical_url": row["canonical_url"],
        "claims_supported": claims_supported,
        "date": row["document_metadata"]["date"],
        "evidence_origin": "independently_reviewed_public_retrieval",
        "exact_supporting_passage": passage,
        "exact_supporting_passage_sha256": _sha256_text(passage),
        "page_independently_inspected": True,
        "page_sha256": row["retrieved_file_sha256"],
        "page_text_sha256": row["page_text_sha256"],
        "publisher_author_label": row["document_metadata"]["author"],
        "rationale": (
            "The human-reviewed live MTF document was reverified against its "
            "retained body and article-text hashes. It identifies Margaret "
            "Thatcher, the event and date, contains the reviewed wording, and "
            "supports the approved packet context. The public MTF URL remains "
            "the source identity; no private retrieval path is retained."
        ),
        "recorded_at": TRANSITION_DATE,
        "source_date": packet["date"],
        "source_date_raw": row["document_metadata"]["date"],
        "source_event": packet["source_event"],
        "source_publisher": "Margaret Thatcher Foundation",
        "source_quality_class": "strong_primary_evidence",
        "source_review_candidate_id": row["review_candidate_id"],
        "source_type": source_type,
        "stable_locator": (
            f"Margaret Thatcher Foundation document {row['document_number']}"
        ),
        "supporting_context": context,
        "supporting_context_sha256": _sha256_text(context),
        "title": (
            f"Margaret Thatcher Foundation, "
            f"{row['document_metadata']['title']}, "
            f"{row['document_metadata']['date']} "
            f"(Document {row['document_number']})"
        ),
        "url": row["canonical_url"],
        "wording_match_kind": match_kind,
    }
    source["source_id"] = curated_source_id(quote_id, source)
    return source


def _upgrade_curated_source(
    source: dict[str, Any],
    packet: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    upgraded = dict(source)
    prior_candidate_id = _clean(source.get("source_review_candidate_id"))
    prior_page_sha256 = _clean(source.get("page_sha256"))
    prior_page_text_sha256 = _clean(source.get("page_text_sha256"))
    upgraded["assigned_roles"] = [
        role
        for role in (
            "wording_verification",
            "attribution_support",
            "source_event_support",
            "historical_context_support",
            "secondary_recollection",
        )
        if role in set(source.get("assigned_roles", []))
        or role == "historical_context_support"
    ]
    upgraded["claims_supported"] = [
        claim
        for claim in (
            "wording",
            "attribution",
            "source_event",
            "date",
            "historical_context",
        )
        if claim in set(source.get("claims_supported", []))
        or claim == "historical_context"
    ]
    context = _clean(packet["historical_context"])
    upgraded.update({
        "page_sha256": row["retrieved_file_sha256"],
        "page_text_sha256": row["page_text_sha256"],
        "prior_source_review_candidate_id": prior_candidate_id,
        "source_review_candidate_id": row["review_candidate_id"],
        "supporting_context": context,
        "supporting_context_sha256": _sha256_text(context),
    })
    if prior_page_sha256 != upgraded["page_sha256"]:
        upgraded["prior_page_sha256"] = prior_page_sha256
    if prior_page_text_sha256 != upgraded["page_text_sha256"]:
        upgraded["prior_page_text_sha256"] = prior_page_text_sha256
    if upgraded.get("source_id") != curated_source_id(
        row["quote_id"], upgraded
    ):
        raise AdmissionError(
            f"existing curated source identity would change: {row['quote_id']}"
        )
    return upgraded


def _refresh_status_and_closure(project_root: Path) -> None:
    from semantic_alignment.quote_research_closure import corpus_closure_audit

    research = (
        project_root / "semantic_alignment_research" / "quote_research_full_001"
    )
    packets_path = research / "research_packets.json"
    status_path = research / "final_unresolved" / "final_research_status.json"
    status = _load(status_path)
    if (
        status.get("completed_quotes") != 627
        or status.get("unresolved_quotes") != 5
    ):
        raise AdmissionError("canonical research partition differs")
    status["corpus_hash"] = _file_sha256(packets_path)
    status["generated_timestamp"] = TRANSITION_TIMESTAMP
    _atomic_json(status_path, status)
    closure = corpus_closure_audit(research, strict=True)
    closure["generated_at"] = TRANSITION_TIMESTAMP
    _atomic_json(
        research / "final_unresolved" / "corpus_closure_audit.json",
        closure,
    )


def _historical_v7_fields(
    baseline_fields: dict[str, list[str]],
    historical_projection: dict[str, Any],
) -> dict[str, list[str]]:
    """Preserve the reviewed pre-admission v7 projection for later rebuilds."""
    fields = {
        quote_id: list(value)
        for quote_id, value in baseline_fields.items()
    }
    for record in (
        historical_projection.get("downgraded_records", [])
        + historical_projection.get("upgraded_records", [])
    ):
        quote_id = record["quote_id"]
        if quote_id in fields:
            fields[quote_id] = list(
                record["v7_public_context_supported_fields"]
            )
    return dict(sorted(fields.items()))


def apply(
    project_root: Path,
    review_dir: Path,
    runs_root: Path,
) -> dict[str, Any]:
    """Apply the exact reviewed transition to canonical evidence inputs."""
    rows, review_hashes = _verify_review_inputs(review_dir, runs_root)
    research = (
        project_root / "semantic_alignment_research" / "quote_research_full_001"
    )
    packets_path = research / "research_packets.json"
    curated_path = research / "historical_context_source_curated_evidence.json"
    source_audit_path = research / "historical_context_source_role_audit.json"
    packets_doc = _load(packets_path)
    curated = _load(curated_path)
    source_audit = _load(source_audit_path)
    historical_projection_path = (
        project_root / "historical_context_public_projection_review.json"
    )
    historical_projection = _load(historical_projection_path)
    packets = packets_doc.get("items")
    if (
        not isinstance(packets, dict)
        or len(packets) != 627
        or curated.get("source_count") != 36
        or len(source_audit.get("items", {})) != 627
    ):
        raise AdmissionError("canonical admission baseline differs")
    expected_input_hashes = _load(
        review_dir / "canonical_comparison.json"
    )["production_input_hashes"]
    actual_input_hashes = {
        "final_research_status.json": _file_sha256(
            research / "final_unresolved" / "final_research_status.json"
        ),
        "historical_context_reply_semantic_gate_audit.json": _file_sha256(
            project_root / "historical_context_reply_semantic_gate_audit.json"
        ),
        "historical_context_source_role_audit.json": _file_sha256(
            source_audit_path
        ),
        "research_packets.json": _file_sha256(packets_path),
    }
    if actual_input_hashes != expected_input_hashes:
        raise AdmissionError("canonical inputs changed after human review")

    before_quote_text = {
        quote_id: packet["quote_text"] for quote_id, packet in packets.items()
    }
    before_meanings = {
        quote_id: packet["intended_argument"]
        for quote_id, packet in packets.items()
    }
    before_fields = {
        row["quote_id"]: list(
            source_audit["items"][row["quote_id"]].get(
                "public_context_supported_fields", []
            )
        )
        for row in rows
    }
    before_packet_hashes = {
        row["quote_id"]: _sha256_bytes(
            _canonical_json_bytes(packets[row["quote_id"]])
        )
        for row in rows
    }
    baseline_curated_source_ids = sorted(
        source["source_id"]
        for item in curated.get("items", {}).values()
        for source in item.get("sources", [])
    )
    decisions: list[dict[str, Any]] = []
    bindings: dict[str, dict[str, str]] = {}
    action_counts: Counter[str] = Counter()

    for row in rows:
        quote_id = row["quote_id"]
        packet = packets.get(quote_id)
        if (
            not isinstance(packet, dict)
            or packet.get("quote_text") != row["quote_text"]
            or _sha256_text(packet["quote_text"]) != quote_id
            or not _clean(packet.get("historical_context"))
        ):
            raise AdmissionError(f"canonical packet differs: {quote_id}")
        item = curated.get("items", {}).get(quote_id)
        existing_sources = (
            item.get("sources", []) if isinstance(item, dict) else []
        )
        same_url = [
            source
            for source in existing_sources
            if _clean(source.get("url")).rstrip("/")
            == row["canonical_url"].rstrip("/")
        ]
        if len(same_url) > 1:
            raise AdmissionError(f"duplicate curated MTF identity: {quote_id}")

        packet["speaker"] = "Margaret Thatcher"
        if not existing_sources:
            packet["date"] = row["document_metadata"]["date"]
            packet["source_event"] = row["document_metadata"]["title"]
            packet["stable_locator"] = (
                f"Margaret Thatcher Foundation Document "
                f"{row['document_number']}"
            )
        packet["immediate_subject"] = packet["historical_context"]

        if same_url:
            selected = same_url[0]
            upgraded = _upgrade_curated_source(selected, packet, row)
            existing_sources = [
                upgraded if source is selected else source
                for source in existing_sources
            ]
            source = upgraded
            action = "upgraded_existing_curated_source"
        else:
            source = _new_curated_source(
                quote_id,
                packet,
                row,
                supports_event=not existing_sources,
            )
            existing_sources = [*existing_sources, source]
            action = "admitted_new_curated_source"
        existing_sources.sort(key=lambda value: value["source_id"])
        curated.setdefault("items", {})[quote_id] = {
            "quote_id": quote_id,
            "quote_text": packet["quote_text"],
            "quote_text_sha256": quote_id,
            "sources": existing_sources,
        }
        action_counts[action] += 1
        bindings[quote_id] = {
            "action": action,
            "source_id": source["source_id"],
            "source_review_candidate_id": row["review_candidate_id"],
        }
        decisions.append({
            "canonical_action": action,
            "canonical_url": row["canonical_url"],
            "context_decision": (
                "approved_packet_historical_context_supported_by_reviewed_"
                "primary_document"
            ),
            "document_number": row["document_number"],
            "gate_action": (
                "resolve_after_regeneration_and_fail_closed_validation"
                if row["priority"] == "A_BLOCKED_REMEDIATION"
                else "no_manual_gate_change"
            ),
            "match_type": row["match"]["match_type"],
            "priority": row["priority"],
            "quote_id": quote_id,
            "review_candidate_id": row["review_candidate_id"],
            "retrieved_file_sha256": row["retrieved_file_sha256"],
            "wording_differences": row["wording_differences"],
        })

    if (
        {quote_id: packet["quote_text"] for quote_id, packet in packets.items()}
        != before_quote_text
        or {
            quote_id: packet["intended_argument"]
            for quote_id, packet in packets.items()
        }
        != before_meanings
    ):
        raise AdmissionError("quotation text or Meaning changed")

    packets_doc["items"] = dict(sorted(packets.items()))
    curated["items"] = dict(sorted(curated["items"].items()))
    curated["quote_count"] = len(curated["items"])
    curated["source_count"] = sum(
        len(item["sources"]) for item in curated["items"].values()
    )
    _atomic_json(packets_path, packets_doc)
    _atomic_json(curated_path, curated)
    _refresh_status_and_closure(project_root)

    decisions_document = {
        "authorisation": (
            "Operator reviewed the complete human review pack and authorised "
            "Priority A unblocking after validation plus historical-context "
            "support/update for the remaining reviewed records."
        ),
        "counts": {
            **dict(sorted(action_counts.items())),
            "priority": EXPECTED_PRIORITY_COUNTS,
            "records": len(decisions),
        },
        "decision_kind": "historical_context_mtf_live_context_admission",
        "items": decisions,
        "review_input_hashes": review_hashes,
        "reviewed_at": TRANSITION_DATE,
        "schema_version": 1,
    }
    _atomic_json(project_root / DECISIONS_FILENAME, decisions_document)
    audit = {
        "admission_kind": "historical_context_mtf_live_context_admission",
        "baseline": {
            "curated_source_count": len(baseline_curated_source_ids),
            "curated_source_ids_sha256": _sha256_text(
                "".join(f"{value}\n" for value in baseline_curated_source_ids)
            ),
            "packet_hashes": before_packet_hashes,
            "public_context_supported_fields": before_fields,
            "historical_projection_sha256": _file_sha256(
                historical_projection_path
            ),
            "v7_public_context_supported_fields": _historical_v7_fields(
                before_fields, historical_projection
            ),
        },
        "bindings": dict(sorted(bindings.items())),
        "counts": {
            **dict(sorted(action_counts.items())),
            "exact_phrase_matches": EXPECTED_MATCH_COUNTS["exact_quotation"],
            "reviewed_variants": EXPECTED_MATCH_COUNTS["recorded_variant"],
            "source_bindings": len(bindings),
        },
        "generated_at": TRANSITION_TIMESTAMP,
        "meaning_fields_changed": 0,
        "network_requests": 0,
        "private_paths_persisted": 0,
        "production_base_commit": PRODUCTION_BASE_COMMIT,
        "provider_requests": 0,
        "quotation_text_changed": False,
        "review_input_hashes": review_hashes,
        "schema_version": 1,
    }
    _atomic_json(project_root / AUDIT_FILENAME, audit)
    return audit


def write_transition_manifest(project_root: Path) -> dict[str, Any]:
    """Write the hash-bound transition after source-role regeneration."""
    from historical_context_formatter import load_and_validate_corpus
    from historical_context_public_projection_review import (
        POST_V9_TRANSITION_KIND,
        POST_V9_TRANSITION_STATUS,
        V9_POLICY,
        _post_v9_input_hashes,
    )

    research = (
        project_root / "semantic_alignment_research" / "quote_research_full_001"
    )
    audit = _load(project_root / AUDIT_FILENAME)
    decisions = _load(project_root / DECISIONS_FILENAME)
    baseline = audit["baseline"]
    if (
        "v7_public_context_supported_fields" not in baseline
        or "historical_projection_sha256" not in baseline
    ):
        existing_manifest_path = project_root / TRANSITION_FILENAME
        existing_manifest = _load(existing_manifest_path)
        existing_items = existing_manifest.get("items", {})
        if set(existing_items) != set(audit["bindings"]):
            raise AdmissionError(
                "cannot backfill historical projection from a different "
                "transition scope"
            )
        baseline["v7_public_context_supported_fields"] = {
            quote_id: list(
                existing_items[quote_id][
                    "v7_baseline_public_context_supported_fields"
                ]
            )
            for quote_id in sorted(existing_items)
        }
        baseline["historical_projection_sha256"] = existing_manifest[
            "historical_projection_baseline_sha256"
        ]
        _atomic_json(project_root / AUDIT_FILENAME, audit)
    historical_v7_fields = baseline[
        "v7_public_context_supported_fields"
    ]
    packets, _ = load_and_validate_corpus(
        research, require_source_role_audit=True
    )
    bindings = audit["bindings"]
    items: dict[str, Any] = {}
    field_changes = 0
    for decision in decisions["items"]:
        quote_id = decision["quote_id"]
        binding = bindings[quote_id]
        baseline_fields = audit["baseline"][
            "public_context_supported_fields"
        ][quote_id]
        current_fields = packets[quote_id]["_source_role_audit"][
            "public_context_supported_fields"
        ]
        field_changes += baseline_fields != current_fields
        items[quote_id] = {
            "binding_action": binding["action"],
            "current_public_context_supported_fields": current_fields,
            "quote_text_sha256": quote_id,
            "source_bindings": [{
                "source_id": binding["source_id"],
                "source_review_candidate_id": (
                    binding["source_review_candidate_id"]
                ),
            }],
            "v7_baseline_public_context_supported_fields": (
                historical_v7_fields[quote_id]
            ),
            "v9_baseline_public_context_supported_fields": baseline_fields,
        }
    manifest = {
        "baseline_curated_source_count": audit["baseline"][
            "curated_source_count"
        ],
        "baseline_curated_source_ids_sha256": audit["baseline"][
            "curated_source_ids_sha256"
        ],
        "counts": {
            "new_curated_sources": audit["counts"][
                "admitted_new_curated_source"
            ],
            "public_field_changes": field_changes,
            "source_bindings": len(items),
            "upgraded_curated_sources": audit["counts"][
                "upgraded_existing_curated_source"
            ],
            "transition_packets": len(items),
        },
        "human_review": {
            "decision": (
                "all_422_reviewed_sources_and_contexts_accepted;_priority_a_"
                "approved_for_fail_closed_gate_resolution"
            ),
            "decisions_sha256": _file_sha256(
                project_root / DECISIONS_FILENAME
            ),
            "review_pack_sha256": REVIEW_PACK_SHA256,
            "reviewed_at": TRANSITION_DATE,
        },
        "input_hashes": _post_v9_input_hashes(research),
        "items": dict(sorted(items.items())),
        "manifest_kind": POST_V9_TRANSITION_KIND,
        "policy_transition": {"from": V9_POLICY, "to": V9_POLICY},
        "historical_projection_baseline_sha256": baseline[
            "historical_projection_sha256"
        ],
        "production_base_commit": PRODUCTION_BASE_COMMIT,
        "schema_version": 1,
        "transition_quote_ids_sha256": _sha256_text(
            "".join(f"{quote_id}\n" for quote_id in sorted(items))
        ),
        "transition_status": POST_V9_TRANSITION_STATUS,
    }
    _atomic_json(project_root / TRANSITION_FILENAME, manifest)
    return manifest


def _shadow_matrix_hash(manifest: dict[str, Any]) -> str:
    """Hash every adjudication row without depending on JSON formatting."""
    return _sha256_bytes(_canonical_json_bytes({
        "adjudicated_unknown_pairs": manifest.get(
            "adjudicated_unknown_pairs", {}
        ),
        "pairs": manifest.get("pairs", {}),
    }))


def _rebound_shadow_manifest(
    project_root: Path,
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Refresh only changed source hashes in an otherwise frozen manifest."""
    rebound = json.loads(json.dumps(manifest))
    source_hashes = rebound.get("source_file_hashes")
    if not isinstance(source_hashes, dict):
        raise AdmissionError("semantic-veto shadow source hashes are absent")
    changed: list[str] = []
    for name, record in sorted(source_hashes.items()):
        if not isinstance(record, dict):
            raise AdmissionError(f"invalid semantic-veto source record: {name}")
        relative = Path(_clean(record.get("path")))
        if relative.is_absolute() or ".." in relative.parts:
            raise AdmissionError(
                f"unsafe semantic-veto source path: {name}"
            )
        path = project_root / relative
        if not path.is_file():
            raise AdmissionError(
                f"semantic-veto source file is absent: {name}"
            )
        current = _file_sha256(path)
        if current != record.get("sha256"):
            changed.append(name)
            record["sha256"] = current
    if set(changed) != SHADOW_REBIND_ALLOWED_SOURCES:
        raise AdmissionError(
            "semantic-veto source drift is not the reviewed packet/runtime "
            f"transition: {changed}"
        )
    return rebound, changed


def rebind_shadow_manifest(project_root: Path) -> dict[str, Any]:
    """Rebind the shadow manifest after an evidence-only packet transition."""
    from semantic_alignment.quote_image_semantic_veto import (
        ShadowRuntime,
        validate_compiled_manifest,
    )

    manifest_path = project_root / SHADOW_MANIFEST_PATH
    before_bytes = manifest_path.read_bytes()
    before = _load(manifest_path)
    before_validation = validate_compiled_manifest(before, strict=True)
    before_matrix_hash = _shadow_matrix_hash(before)
    first, changed = _rebound_shadow_manifest(project_root, before)
    second, second_changed = _rebound_shadow_manifest(project_root, before)
    if (
        first != second
        or changed != second_changed
        or _shadow_matrix_hash(first) != before_matrix_hash
    ):
        raise AdmissionError(
            "semantic-veto shadow source rebind is not deterministic or "
            "changed adjudications"
        )
    after_validation = validate_compiled_manifest(first, strict=True)
    _atomic_bytes(manifest_path, _canonical_json_bytes(first) + b"\n")
    runtime = ShadowRuntime.load(
        project_root,
        {
            "enabled": True,
            "fail_open": True,
            "manifest_path": str(SHADOW_MANIFEST_PATH),
            "maximum_shadow_history": 10_000,
            "mode": "shadow",
            "record_best_allowed_alternative": True,
        },
        verify_source_hashes=True,
        enable_history=False,
    )
    if not runtime.available:
        raise AdmissionError(
            f"rebound semantic-veto manifest is unavailable: {runtime.reason}"
        )
    audit = {
        "after_manifest_sha256": _file_sha256(manifest_path),
        "before_manifest_sha256": _sha256_bytes(before_bytes),
        "changed_source_hashes": changed,
        "deterministic_double_build": True,
        "enforcement_enabled": False,
        "matrix_after_sha256": _shadow_matrix_hash(first),
        "matrix_before_sha256": before_matrix_hash,
        "matrix_entries_unchanged": True,
        "policy_version": first.get("policy_version"),
        "post_rebind_validation": after_validation,
        "pre_rebind_validation": before_validation,
        "runtime_available": True,
        "schema_version": 1,
    }
    _atomic_json(project_root / SHADOW_REBIND_AUDIT_FILENAME, audit)
    return audit


def main(argv: list[str] | None = None) -> int:
    """Run the admission, transition writer, or shadow source rebind."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--review-dir", type=Path)
    parser.add_argument("--runs-root", type=Path)
    parser.add_argument("--write-transition-only", action="store_true")
    parser.add_argument("--rebind-shadow-only", action="store_true")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    if args.write_transition_only and args.rebind_shadow_only:
        parser.error("select only one post-admission operation")
    if args.rebind_shadow_only:
        result = rebind_shadow_manifest(root)
        counts = {
            "changed_source_hashes": result["changed_source_hashes"],
            "matrix_entries_unchanged": result[
                "matrix_entries_unchanged"
            ],
        }
    elif args.write_transition_only:
        result = write_transition_manifest(root)
        counts = result["counts"]
    else:
        if args.review_dir is None or args.runs_root is None:
            parser.error("--review-dir and --runs-root are required")
        result = apply(
            root,
            args.review_dir.resolve(),
            args.runs_root.resolve(),
        )
        counts = result["counts"]
    print(json.dumps(counts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
