#!/usr/bin/env python3
"""Apply the reviewed Statecraft primary-source admission deterministically.

This is an offline, operator-authorised canonical transition.  It verifies the
reviewed private book and matcher artefacts, adds exactly seven curated source
records, corrects only source-verification metadata for six completed packets,
and promotes one previously unresolved quotation to a complete packet.

The programme never reads credentials, contacts a network service, changes the
quotation corpus, or writes runtime state outside the supplied project root.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from historical_context_source_curated_evidence import curated_source_id


TRANSITION_DATE = "2026-07-24"
TRANSITION_TIMESTAMP = "2026-07-24T20:01:06Z"
BOOK_ID = "ebfa359ba0cc3c5fd9c9a82e9a4b906e60be5b258489d2c50a3df5216845b322"
PDF_SHA256 = "6619eb3401336585ef5d27c46fdd2dbc101a66c484620b57c8cda62ab1140872"
OCR_SHA256 = "24582e795eec4a7e46b674563ab53c05e1dbdc2c500f688fd7393a01f6490fe1"
SOURCE_EVENT = "Publication of Statecraft: Strategies for a Changing World"
BOOK_TITLE = "Statecraft: Strategies for a Changing World"
UNRESOLVED_QUOTE_ID = (
    "0a67f403a7ac02347e43791d2daf3057aabdcfd64b62edbe1b3484a3a4b66729"
)


SOURCE_RECORDS: dict[str, dict[str, Any]] = {
    UNRESOLVED_QUOTE_ID: {
        "candidate_id": "75fd4da816b92425452a48ce4ffd8dab2c538f857c8de0de5c18d085bd329456",
        "printed_page": "427",
        "pdf_page_index": 465,
        "wording_match_kind": "exact",
        "passage": (
            "Socialists have always spent much of their time seeking new titles "
            "for their beliefs, because the old versions so quickly become "
            "outdated and discredited."
        ),
        "context": (
            "In a discussion of the political 'Third Way', Thatcher presents "
            "the sentence as criticism of relabelling socialist beliefs after "
            "earlier versions have become discredited."
        ),
        "rationale": (
            "The inspected first-edition page contains the complete sentence "
            "contiguously and identifies it as Thatcher's own authored text."
        ),
        "variant_notes": "The stored quotation matches the inspected primary text.",
    },
    "685ddfab242fe45cafc203a937769a4fe925423baf80e022b6e2e4411dd3ce90": {
        "candidate_id": "192e8d138fa01dbf160ede438db59ca5c1f26321df2d3b04ffeb60d11b396b95",
        "printed_page": "449",
        "pdf_page_index": 487,
        "wording_match_kind": "exact",
        "passage": (
            "When socialist countries are to be found helping out capitalist "
            "countries in their hour of need rather than vice-versa, then - and "
            "only then - should we question the system which makes us rich, "
            "healthy and secure."
        ),
        "context": (
            "Thatcher contrasts the performance of free societies and "
            "economies with socialist systems and answers contemporary "
            "predictions of capitalist decline."
        ),
        "rationale": (
            "The inspected first-edition page contains the complete quotation "
            "contiguously in Thatcher's authored argument."
        ),
        "variant_notes": "The stored quotation matches the inspected primary text.",
    },
    "928a6686bc6bb6d35cd1ec139373cb73b85ba9fa40807098d5572ae153dab144": {
        "candidate_id": "1541cd79b74e97ede1bfd089ad8e0fe73f3096463d3c44edc5425ac8be9d7947",
        "printed_page": "432",
        "pdf_page_index": 470,
        "wording_match_kind": "exact",
        "passage": (
            "'Social justice' can take a free society into still deeper and "
            "more treacherous waters if it is applied not only to equality of "
            "opportunity but also to equality of outcomes."
        ),
        "context": (
            "The surrounding discussion distinguishes equality before the law "
            "and equality of opportunity from government pursuit of equality "
            "of outcomes."
        ),
        "rationale": (
            "The inspected first-edition page contains the complete quotation "
            "contiguously in Thatcher's authored discussion."
        ),
        "variant_notes": "The stored quotation matches the inspected primary text.",
    },
    "a4f1d422097a48114bf30a587c04cf05859ff030d2df3d5d9051c6ca57a7943c": {
        "candidate_id": "67610f7a0dcd4474e811f05f0b5e0a1e8fbc6632afa85553acd43cc0281466ad",
        "printed_page": "433",
        "pdf_page_index": 471,
        "wording_match_kind": "historical_variant",
        "passage": (
            "When the objectives of government include the achievement of "
            "equality - other than equality before the law - that government "
            "poses a threat to liberty."
        ),
        "context": (
            "The sentence concludes Thatcher's discussion of equality before "
            "the law, equality of opportunity and equality of outcomes."
        ),
        "rationale": (
            "The inspected first-edition page establishes a primary wording "
            "variant: the stored quotation inserts 'all' after 'When', while "
            "the primary text does not."
        ),
        "variant_notes": (
            "Primary variant: the stored quotation begins 'When all the "
            "objectives'; the inspected source begins 'When the objectives'."
        ),
    },
    "a97e6dd2f444ecfbba67977a34be91db40d17eb09c8566fe714e48bffddb11f7": {
        "candidate_id": "9016f29ef388fe954005aa090add8fe86bd9fd8b1b1db4997548cd200257adf4",
        "printed_page": "425",
        "pdf_page_index": 463,
        "wording_match_kind": "historical_variant",
        "passage": (
            "The larger the slice taken by government, the smaller the cake "
            "available for everyone."
        ),
        "context": (
            "The sentence follows Thatcher's discussion of the ratio of public "
            "spending to national income and its relationship to economic growth."
        ),
        "rationale": (
            "The inspected first-edition page contains the sentence "
            "contiguously; the stored public quotation adds a leading editorial "
            "ellipsis which is absent from the primary text."
        ),
        "variant_notes": (
            "Primary variant: the stored quotation has a leading editorial "
            "ellipsis; the inspected primary sentence does not."
        ),
    },
    "db46e7519946d4312907a8b7c7337eea0daaf3689850c2ef35035a6bda062173": {
        "candidate_id": "38efb1e0e9571009544c592a48385902e02d0d4d3fcb453eb31f073bcb1f7416",
        "printed_page": "256",
        "pdf_page_index": 294,
        "wording_match_kind": "exact",
        "passage": "Constitutions have to be written on hearts, not just paper.",
        "context": (
            "Thatcher argues that constitutional and human-rights documents "
            "must be grounded in national institutions, habits and culture."
        ),
        "rationale": (
            "The inspected first-edition page contains the complete sentence "
            "contiguously in Thatcher's authored discussion."
        ),
        "variant_notes": "The stored quotation matches the inspected primary text.",
    },
    "f4323817daee5cef16fa5d83879823f2da5506152fcb7b1b1ce5c777ac036d4d": {
        "candidate_id": "66276ffca1cca9b2b00344a329a7cc850652b700d26e033ca43ea659f25eb897",
        "printed_page": "327",
        "pdf_page_index": 365,
        "wording_match_kind": "historical_variant",
        "passage": (
            "What we should grasp, however, from the lessons of European "
            "history is that, first, there is nothing necessarily benevolent "
            "about programmes of European integration; second, the desire to "
            "achieve grand Utopian plans often poses a grave threat to freedom; "
            "and third, European unity has been tried before, and the outcome "
            "was far from happy."
        ),
        "context": (
            "The passage follows discussion of earlier coercive projects for "
            "European unity and states three conclusions Thatcher draws from "
            "that history."
        ),
        "rationale": (
            "The inspected first-edition page establishes the complete primary "
            "passage and the documented differences from the shortened stored "
            "quotation."
        ),
        "variant_notes": (
            "Primary variant: the stored wording shortens the opening, uses "
            "the contraction 'there's', omits 'the' and 'grand', and changes "
            "connective punctuation while preserving the three stated points."
        ),
    },
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
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


def _verify_private_inputs(scan_dir: Path, books_root: Path) -> dict[str, Any]:
    manifest = _json(scan_dir / "book_manifest.json")
    records = manifest.get("books")
    if not isinstance(records, list):
        records = manifest.get("items")
    if not isinstance(records, list):
        raise RuntimeError("private book manifest has no records")
    matches = [
        item for item in records
        if isinstance(item, dict) and item.get("book_id") == BOOK_ID
    ]
    if len(matches) != 1:
        raise RuntimeError("reviewed Statecraft bibliographic record is ambiguous")
    book = matches[0]
    if (
        book.get("title") != BOOK_TITLE
        or book.get("authors") != ["Margaret Thatcher"]
        or book.get("publisher") != "HarperCollins"
        or book.get("edition") != "First edition"
        or book.get("dates") != {
            "edition_publication": "2002",
            "original_work_publication": "2002",
        }
        or book.get("primary_secondary_status") != "primary"
        or book.get("pdf_sha256") != PDF_SHA256
        or book.get("ocr_sha256") != OCR_SHA256
    ):
        raise RuntimeError("reviewed Statecraft bibliographic metadata differs")
    pdf = books_root / Path(str(book["pdf_path"])).relative_to("books")
    ocr = books_root / Path(str(book["text_path"])).relative_to("books")
    if (
        _sha256_bytes(pdf.read_bytes()) != PDF_SHA256
        or _sha256_bytes(ocr.read_bytes()) != OCR_SHA256
    ):
        raise RuntimeError("reviewed Statecraft source bytes differ")

    candidates = _json(scan_dir / "local_book_match_candidates.json")
    rows = candidates.get("candidates")
    if not isinstance(rows, list):
        rows = candidates.get("items")
    if not isinstance(rows, list):
        raise RuntimeError("private candidate manifest has no candidates")
    expected = {
        (quote_id, record["candidate_id"], record["pdf_page_index"])
        for quote_id, record in SOURCE_RECORDS.items()
    }
    found = {
        (
            str(row.get("quote_id") or ""),
            str(row.get("candidate_id") or ""),
            (row.get("page_indices") or [None])[0],
        )
        for row in rows
        if isinstance(row, dict)
        and str(row.get("quote_id") or "") in SOURCE_RECORDS
        and str(row.get("candidate_id") or "")
        == SOURCE_RECORDS[str(row.get("quote_id"))]["candidate_id"]
    }
    if found != expected:
        raise RuntimeError("reviewed Statecraft candidate bindings differ")
    return {
        "bibliographical_manifest_sha256": _sha256_bytes(
            (scan_dir / "book_manifest.json").read_bytes()
        ),
        "candidate_manifest_sha256": _sha256_bytes(
            (scan_dir / "local_book_match_candidates.json").read_bytes()
        ),
        "pdf_sha256": PDF_SHA256,
        "ocr_sha256": OCR_SHA256,
    }


def _curated_source(quote_id: str, record: dict[str, Any]) -> dict[str, Any]:
    passage = record["passage"]
    context = record["context"]
    source = {
        "assigned_roles": [
            "wording_verification",
            "attribution_support",
            "source_event_support",
        ],
        "author_or_speaker": "Margaret Thatcher",
        "claims_supported": [
            "wording",
            "attribution",
            "source_event",
            "date",
        ],
        "edition": "First edition, 2002",
        "evidence_origin": "operator_supplied_bibliographic_citation",
        "exact_supporting_passage": passage,
        "exact_supporting_passage_sha256": _sha256_text(passage),
        "isbn": "978-0-00-715064-9",
        "ocr_text_sha256": OCR_SHA256,
        "page_independently_inspected": True,
        "pdf_page_index": record["pdf_page_index"],
        "pdf_sha256": PDF_SHA256,
        "printed_page": record["printed_page"],
        "printed_page_visually_verified": True,
        "publisher": "HarperCollins",
        "rationale": record["rationale"],
        "recorded_at": TRANSITION_DATE,
        "source_date": "2002",
        "source_event": SOURCE_EVENT,
        "source_quality_class": "strong_primary_evidence",
        "source_review_candidate_id": record["candidate_id"],
        "source_type": "thatcher_authored_primary_book",
        "stable_locator": (
            f"Statecraft: Strategies for a Changing World, first edition "
            f"(2002), printed page {record['printed_page']}"
        ),
        "supporting_context": context,
        "supporting_context_sha256": _sha256_text(context),
        "title": (
            f"Margaret Thatcher, Statecraft: Strategies for a Changing World "
            f"(first edition, 2002), p. {record['printed_page']}"
        ),
        "url": "",
        "wording_match_kind": record["wording_match_kind"],
    }
    source["source_id"] = curated_source_id(quote_id, source)
    return source


def _new_packet(quote_text: str, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "broader_principle": (
            "Changing a political label does not by itself change the substance "
            "or record of the ideas being relabelled."
        ),
        "claimed_consequence": (
            "Discredited socialist ideas may be presented again under new names."
        ),
        "completed_at": TRANSITION_TIMESTAMP,
        "date": "2002",
        "editorial_guidance": {
            "common_visual_mistakes": [
                "Presenting a renamed label as proof that the underlying policy changed."
            ],
            "desired_first_impression": (
                "A pointed criticism of political rebranding."
            ),
            "historical_requirements": [
                "Identify the source as Thatcher's 2002 authored book Statecraft."
            ],
            "must_be_visually_dominant": [
                "The contrast between new titles and discredited older versions."
            ],
            "must_not_dominate": [
                "Unrelated election or Cold War imagery."
            ],
        },
        "entities": ["Margaret Thatcher", "socialists"],
        "grounding_source_count": 1,
        "historical_context": (
            "In Statecraft, Thatcher used the sentence while criticising the "
            "political 'Third Way' as another relabelling of socialist beliefs."
        ),
        "immediate_subject": "The repeated relabelling of socialist beliefs.",
        "intended_argument": (
            "Thatcher argued that socialists repeatedly renamed their beliefs "
            "because earlier versions had become outdated and discredited."
        ),
        "literal_meaning": (
            "Socialist ideas receive new titles after their previous versions "
            "lose credibility."
        ),
        "mechanism": (
            "New terminology distances a political programme from the record "
            "of its earlier labels."
        ),
        "model": "operator-reviewed-primary-source-admission",
        "prompt_version": "statecraft-primary-admission-v1",
        "quote_id": UNRESOLVED_QUOTE_ID,
        "quote_text": quote_text,
        "research_confidence": "high",
        "source_event": SOURCE_EVENT,
        "sources": [{
            "source_type": "operator_supplied_bibliographic_citation",
            "supports": [quote_text],
            "title": source["title"],
            "url": "",
        }],
        "speaker": "Margaret Thatcher",
        "stable_locator": source["stable_locator"],
        "text_variation_notes": SOURCE_RECORDS[UNRESOLVED_QUOTE_ID][
            "variant_notes"
        ],
        "transport": "offline_operator_review",
        "unresolved_questions": [],
        "validation_status": "valid",
        "verification_status": "exact",
        "verified_text": quote_text,
    }


def _apply_packet_source_metadata(
    packet: dict[str, Any],
    source: dict[str, Any],
    record: dict[str, Any],
) -> None:
    packet["speaker"] = "Margaret Thatcher"
    packet["date"] = "2002"
    packet["source_event"] = SOURCE_EVENT
    packet["stable_locator"] = source["stable_locator"]
    packet["verified_text"] = record["passage"]
    packet["text_variation_notes"] = record["variant_notes"]
    packet["verification_status"] = (
        "exact" if record["wording_match_kind"] == "exact" else "variant"
    )


def apply(project_root: Path, scan_dir: Path, books_root: Path) -> dict[str, Any]:
    """Verify the reviewed book inputs and apply the seven-record transition."""
    root = project_root.resolve()
    research = (
        root / "semantic_alignment_research" / "quote_research_full_001"
    )
    input_hashes = _verify_private_inputs(scan_dir.resolve(), books_root.resolve())
    packets_path = research / "research_packets.json"
    status_path = research / "final_unresolved" / "final_research_status.json"
    curated_path = research / "historical_context_source_curated_evidence.json"
    packets_doc = _json(packets_path)
    packets = packets_doc.get("items")
    final_status = _json(status_path)
    curated = _json(curated_path)
    if (
        not isinstance(packets, dict)
        or len(packets) != 626
        or final_status.get("completed_quotes") != 626
        or final_status.get("unresolved_quotes") != 6
        or UNRESOLVED_QUOTE_ID in packets
        or UNRESOLVED_QUOTE_ID not in final_status.get("unresolved_quote_ids", [])
    ):
        raise RuntimeError("canonical Statecraft admission baseline differs")
    corpus_manifest = _json(research / "corpus_manifest.json")
    manifest_records = {
        item["quote_id"]: item
        for item in corpus_manifest.get("records", [])
        if isinstance(item, dict)
    }
    if set(SOURCE_RECORDS) - set(manifest_records):
        raise RuntimeError("Statecraft quotation identity is absent from corpus")
    for quote_id, record in SOURCE_RECORDS.items():
        if manifest_records[quote_id].get("quote_text") is None:
            raise RuntimeError(f"Statecraft quotation text is absent: {quote_id}")
        if quote_id in curated.get("items", {}):
            raise RuntimeError(f"Statecraft curated source already exists: {quote_id}")

    before = {
        "research_packets.json": _sha256_bytes(packets_path.read_bytes()),
        "final_research_status.json": _sha256_bytes(status_path.read_bytes()),
        "historical_context_source_curated_evidence.json": _sha256_bytes(
            curated_path.read_bytes()
        ),
    }
    sources: dict[str, dict[str, Any]] = {
        quote_id: _curated_source(quote_id, record)
        for quote_id, record in SOURCE_RECORDS.items()
    }
    for quote_id, record in SOURCE_RECORDS.items():
        quote_text = manifest_records[quote_id]["quote_text"]
        source = sources[quote_id]
        if quote_id == UNRESOLVED_QUOTE_ID:
            packets[quote_id] = _new_packet(quote_text, source)
        else:
            packet = packets.get(quote_id)
            if not isinstance(packet, dict):
                raise RuntimeError(f"completed packet is absent: {quote_id}")
            _apply_packet_source_metadata(packet, source, record)
        curated.setdefault("items", {})[quote_id] = {
            "quote_id": quote_id,
            "quote_text": quote_text,
            "quote_text_sha256": _sha256_text(quote_text),
            "sources": [source],
        }

    packets_doc["items"] = dict(sorted(packets.items()))
    curated["items"] = dict(sorted(curated["items"].items()))
    curated["quote_count"] = len(curated["items"])
    curated["source_count"] = sum(
        len(item["sources"]) for item in curated["items"].values()
    )
    _atomic_json(packets_path, packets_doc)
    _atomic_json(curated_path, curated)

    unresolved_ids = sorted(
        set(final_status["unresolved_quote_ids"]) - {UNRESOLVED_QUOTE_ID}
    )
    final_status["completed_quotes"] = len(packets)
    final_status["unresolved_quote_ids"] = unresolved_ids
    final_status["unresolved_quotes"] = len(unresolved_ids)
    final_status["completion_percentage"] = (
        len(packets) * 100 / final_status["total_manifest_quotes"]
    )
    final_status["corpus_hash"] = _sha256_bytes(packets_path.read_bytes())
    final_status["generated_timestamp"] = TRANSITION_TIMESTAMP
    _atomic_json(status_path, final_status)

    failures_path = research / "permanent_failures.json"
    failures = _json(failures_path)
    prior_failure = copy.deepcopy(failures["items"].pop(UNRESOLVED_QUOTE_ID))
    _atomic_json(failures_path, failures)

    unresolved_cases_path = research / "final_unresolved" / "unresolved_cases.json"
    unresolved_cases = _json(unresolved_cases_path)
    unresolved_cases["cases"] = [
        item for item in unresolved_cases["cases"]
        if item.get("quote_id") != UNRESOLVED_QUOTE_ID
    ]
    unresolved_cases["case_count"] = len(unresolved_cases["cases"])
    unresolved_cases["generated_at"] = TRANSITION_TIMESTAMP
    _atomic_json(unresolved_cases_path, unresolved_cases)

    taxonomy_path = (
        research / "final_unresolved" / "unresolved_failure_taxonomy.json"
    )
    taxonomy = _json(taxonomy_path)
    taxonomy["items"].pop(UNRESOLVED_QUOTE_ID)
    taxonomy["counts"] = {}
    for item in taxonomy["items"].values():
        classification = item["classification"]
        taxonomy["counts"][classification] = (
            taxonomy["counts"].get(classification, 0) + 1
        )
    _atomic_json(taxonomy_path, taxonomy)

    run_state_path = research / "run_state.json"
    run_state = _json(run_state_path)
    prior_state = copy.deepcopy(run_state["items"][UNRESOLVED_QUOTE_ID])
    run_state["items"][UNRESOLVED_QUOTE_ID] = {
        "active_transport": "offline_operator_review",
        "attempt_epoch": prior_state.get("attempt_epoch", 0),
        "completed_transport": "offline_operator_review",
        "last_error": None,
        "status": "valid",
        "transport_attempts": prior_state.get("transport_attempts", {}),
        "updated_at": TRANSITION_TIMESTAMP,
    }
    run_state["updated_at"] = TRANSITION_TIMESTAMP
    _atomic_json(run_state_path, run_state)

    summary_path = research / "status.json"
    summary = _json(summary_path)
    summary["valid_packets"] = len(packets)
    summary["permanent_failures"] = len(unresolved_ids)
    summary["average_cost_per_valid_quote"] = (
        summary["combined_known_spend_usd"] / len(packets)
    )
    summary["resume_plan"]["completed_will_be_skipped"] = len(packets)
    summary["resume_plan"]["permanent_failures_will_be_skipped"] = len(
        unresolved_ids
    )
    summary["updated_at"] = TRANSITION_TIMESTAMP
    _atomic_json(summary_path, summary)

    output = {
        "schema_version": 1,
        "audit_kind": "historical_context_statecraft_primary_admission",
        "generated_at": TRANSITION_TIMESTAMP,
        "book": {
            "book_id": BOOK_ID,
            "title": BOOK_TITLE,
            "author": "Margaret Thatcher",
            "publisher": "HarperCollins",
            "edition": "First edition",
            "publication_year": "2002",
            "pdf_sha256": PDF_SHA256,
            "ocr_sha256": OCR_SHA256,
        },
        "private_input_hashes": input_hashes,
        "quote_ids": sorted(SOURCE_RECORDS),
        "source_bindings": {
            quote_id: {
                "candidate_id": SOURCE_RECORDS[quote_id]["candidate_id"],
                "source_id": sources[quote_id]["source_id"],
                "printed_page": SOURCE_RECORDS[quote_id]["printed_page"],
                "verification_status": packets[quote_id]["verification_status"],
            }
            for quote_id in sorted(SOURCE_RECORDS)
        },
        "counts": {
            "source_records_admitted": len(SOURCE_RECORDS),
            "exact_primary": sum(
                packet["verification_status"] == "exact"
                for quote_id, packet in packets.items()
                if quote_id in SOURCE_RECORDS
            ),
            "primary_variant": sum(
                packet["verification_status"] == "variant"
                for quote_id, packet in packets.items()
                if quote_id in SOURCE_RECORDS
            ),
            "completed_before": 626,
            "completed_after": len(packets),
            "unresolved_before": 6,
            "unresolved_after": len(unresolved_ids),
        },
        "resolved_failure": {
            "quote_id": UNRESOLVED_QUOTE_ID,
            "prior_permanent_failure": prior_failure,
            "prior_run_state": prior_state,
            "resolution": "operator_reviewed_primary_book",
        },
        "before_hashes": before,
        "after_hashes": {
            "research_packets.json": _sha256_bytes(packets_path.read_bytes()),
            "final_research_status.json": _sha256_bytes(status_path.read_bytes()),
            "historical_context_source_curated_evidence.json": _sha256_bytes(
                curated_path.read_bytes()
            ),
        },
        "quotation_text_changed": False,
        "automatic_gate_change": False,
        "network_requests": 0,
        "provider_requests": 0,
    }
    _atomic_json(root / "historical_context_statecraft_primary_admission_audit.json", output)
    return output


def main(argv: list[str] | None = None) -> int:
    """Run the offline Statecraft admission command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--scan-dir", type=Path, required=True)
    parser.add_argument("--books-root", type=Path, required=True)
    args = parser.parse_args(argv)
    result = apply(args.project_root, args.scan_dir, args.books_root)
    print(json.dumps(result["counts"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
