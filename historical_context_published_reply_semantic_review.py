#!/usr/bin/env python3
"""Build and validate the 77-case published historical-reply semantic review.

The classification constants below are reviewed editorial judgements.  The
builder joins them to the immutable published-history rows and retained source
evidence so every aggregate count can be traced back to an individual reply.
It never changes published history, research packets, or production state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
TRUTH_AUDIT_PATH = ROOT / "historical_context_evidence_truth_audit.json"
MTF_REVIEW_PATH = ROOT / "historical_context_mtf_primary_review.json"
SOURCE_ROLE_AUDIT_PATH = (
    ROOT
    / "semantic_alignment_research"
    / "quote_research_full_001"
    / "historical_context_source_role_audit.json"
)
OUTPUT_PATH = ROOT / "historical_context_published_reply_semantic_review.json"

SCHEMA_VERSION = 2
REVIEW_KIND = "historical_context_published_reply_primary_source_semantic_review"
DISPOSITIONS = {
    "supported_as_published",
    "future_correction_needed",
    "insufficient_to_assess",
}
FOLLOW_UP_STATUSES = {
    "no_change_required",
    "resolved_by_current_rendering",
    "remains_open",
}
_SUCCESSFUL_MTF_PAGE_STATUSES = {
    "no_machine_detected_discrepancy",
    "manual_review_required",
}


def _is_successful_mtf_page_record(row: Any) -> bool:
    """Exclude transport failures and deterministic archive 404 records."""
    return (
        isinstance(row, dict)
        and row.get("status") in _SUCCESSFUL_MTF_PAGE_STATUSES
    )


# These 22 findings remain open.  The text is deliberately claim-specific and
# is also used as the public worklist in the JSON artefact.
OPEN_FINDINGS = {
    "04d26fbb2aa5ad3824e1f452699b6e9265298b84a61458f524e19db273a70550": (
        "Meaning adds unsupported claims about leadership, rational thought, "
        "conviction and vulnerability; no primary transcript is retained."
    ),
    "38805634a94b830357ca31de921357af37ce17c69a295c26dfc4c091979549ad": (
        "Meaning mislabels the rival Liberal-SDP alternative as a populist "
        "distraction."
    ),
    "3f2d87ee2926c027067089062d8a9ef5e28291258ca0cc7632f0796c0e927631": (
        "Document 105774 still renders twice and one entry is inaccurately "
        "described as an interview transcript; Meaning over-narrows politics "
        "to a left-wing agenda."
    ),
    "4f5e783f4957dc615742df2b827214e539a5123af1b4863822ba2e52684a0d80": (
        "Meaning introduces market-clearing terminology and categorical "
        "causation without retained evidence."
    ),
    "52f9b9f99f66ff3bc786183803f3a8d68277604471cd411027441989337c9351": (
        "Meaning adds national-interest reasoning and a prescriptive "
        "ideological break not established by the secondary recollection."
    ),
    "5ccc6c754f38343430f3ebc49a7903cce40b9ce86482dc1a4a66b05e13f71202": (
        "The Santiago source is document 108331; the current source set still "
        "includes the later Mexico document 108332 and renders two entries."
    ),
    "63a705d3b574f9294af663894a908053d17df31c1b177fca7997caa36299a5f5": (
        "Meaning adds inherent private-sector superiority; the source states "
        "that taxpayers bear state planners' mistakes."
    ),
    "685ddfab242fe45cafc203a937769a4fe925423baf80e022b6e2e4411dd3ce90": (
        "A primary-edition locator for Statecraft is still needed before "
        "source and verification can be upgraded."
    ),
    "6cc1934843f9e7ab1ee3baf477359078f1b17a45b630dc779ee5561b3b9128f7": (
        "Meaning says penal taxation ultimately destroys outcomes where the "
        "source says it has adverse or harmful results."
    ),
    "9c84eb3fbb816db3d01e30e4ab2c09b4c57ade38214abaf4d9db4aedb94da8f4": (
        "Meaning adds individual self-reliance as the mechanism despite "
        "unresolved attribution and no reliable source."
    ),
    "9efcca12a991db11a019126881080676677742613499ab9623b2048120c23997": (
        "Meaning generalises a recommendation of two Hayek books into "
        "endorsement of his philosophy and specific arguments."
    ),
    "a4f1d422097a48114bf30a587c04cf05859ff030d2df3d5d9051c6ca57a7943c": (
        "Meaning strengthens a threat to liberty into inevitable coercion and "
        "destruction; primary book evidence is not retained."
    ),
    "abcd58e8e8ff8fa5fa8e3c37fcefb55702beb9f07029fec7c3cc38b966acc5cc": (
        "Meaning turns a comparison with tyrants into a categorical claim "
        "about intent; document 108364 also still renders twice."
    ),
    "b572fbd2723c05ecef8f4e3eaa3a897834f612b7965ac72a62676c09dde6fdcd": (
        "Meaning universalises the Polish collectivist example into "
        "inevitable failure of all state-run systems."
    ),
    "cac5746ca684f9611a25dcfb6b024ed63bfb3d41b2fa4c5c3d6e44290378d4ea": (
        "Meaning adds totalitarian and anti-democratic claims without an "
        "edition-specific primary memoir page."
    ),
    "d9028da9c6518f578ea0840ab4ae6ed5e3a94028cfb0d4c924a476d19df838c9": (
        "The correct Lord Mayor source is document 105472, not the currently "
        "rendered unrelated document 105484; Meaning also overstates the "
        "direct claim."
    ),
    "db46e7519946d4312907a8b7c7337eea0daaf3689850c2ef35035a6bda062173": (
        "Meaning adds freedom, democracy and inherence without a reliable "
        "retained source."
    ),
    "dd317acd2b79a2e22aa2c73507484f512ad9646e10fc570590fc074f30d5e374": (
        "Exact verification ignores an editorial [James Callaghan] insertion, "
        "and Meaning overstates the source as equality destroying both liberty "
        "and equality."
    ),
    "dff8aab30bb4fd825ead87707ce7bf811989926bce9afdeab7b1030a29293d09": (
        "Meaning adds inevitability to the source's claim that communism "
        "collapsed under the weight of failure."
    ),
    "eb1d2ebaac7e321e67174d2db5761d2bd008341ebb04b1abac4d4a927cd7a7d4": (
        "Meaning implies confirmed Thatcher authorship even though attribution "
        "remains unresolved."
    ),
    "f0d85c7301e8b27bc694ac030d7c5f6b1d15ff3bcdf31cbdfb03a1c05bbe83ea": (
        "Meaning adds a causal justification for later free-market reforms "
        "without an edition-specific primary memoir page."
    ),
    "f4323817daee5cef16fa5d83879823f2da5506152fcb7b1b1ce5c777ac036d4d": (
        "Meaning adds artificiality, coercion and national sovereignty beyond "
        "the retained secondary wording."
    ),
}


RESOLVED_CORRECTION_FINDINGS = {
    "1244294a90af385fe940a432fab296f43725644d318cb011c7080482115a0236": (
        "Published exact verification relied on secondary Statecraft evidence; "
        "current output discloses that exact wording was not independently verified."
    ),
    "1a73e33d64ecdbdcce0ced1691e91457efd50720116168b28a2371d6b8a8eec6": (
        "Published exact verification and occasion exceeded the retained "
        "secondary evidence; current output limits context to the supported "
        "date and discloses wording uncertainty."
    ),
    "1b536f12fed2ac7418caccc2070b34cc84c0b3bb5b4e5e3328da0a432711ed5b": (
        "Published exact verification and detailed occasion exceeded explicit "
        "source claims; current output suppresses the occasion and downgrades "
        "wording verification."
    ),
    "4229308295bc2b967b2ef17596473f6dec8d9f1ae5c12bf9a80e75a522b38ae7": (
        "Published exact memoir verification lacked a reliable renderable "
        "source; current output fails closed to Research incomplete."
    ),
    "4c950f10a42b15df1d14e37a5f431c0f20f99ef520033c4a152cd44e3e751621": (
        "Published exact verification and broadcast context lacked retained "
        "wording and event support; current output suppresses the event and "
        "discloses wording uncertainty."
    ),
    "866738ba29cb509f048a2a1a8215e8f7e729c266e88d8fcaab482dd97ab8cd4a": (
        "Published exact verification exceeded retained secondary interview "
        "evidence; current output retains the supported occasion but discloses "
        "that wording was not independently verified."
    ),
    "8a34e3ac5cf6fdcb916ef5973463f9f4b809eaa23e16eafa68d2babfdd9a4cf8": (
        "Published exact verification and lecture context exceeded "
        "medium-confidence evidence; current output suppresses unsupported "
        "context and discloses wording uncertainty."
    ),
    "8ef96d03f90989bfab8874de43795820bd16986fa8b56781fe44d08b5b693201": (
        "Published exact verification exceeded medium-confidence evidence; "
        "current output suppresses unsupported occasion and date and discloses "
        "wording uncertainty."
    ),
    "b5fa3f26b28013136d5f8f590096eb78f0e0c7f8960c1c15868491059feb0c1a": (
        "Published exact verification and Fraser context lacked reliable "
        "retained evidence; current output fails closed to Research incomplete."
    ),
    "d4aad379bbd7cc9d628a74001a748644653e9663de2da836733402fa824e6ba3": (
        "Published exact verification exceeded retained evidence; current "
        "output suppresses the unsupported occasion and discloses wording uncertainty."
    ),
    "e74879dd8293ee588c19b84c8921b4a5b467725cfd2121c991b2701d10d195fa": (
        "Published normalised-wording verification was stronger than retained "
        "secondary evidence; current output discloses attribution without "
        "independent exact-wording verification."
    ),
    "e9d8f7dff356e2364bd2250fe6a4f4b5aecfde205cf7e66c316e0850a20bf316": (
        "Published exact memoir verification lacked wording-support evidence; "
        "current output discloses attribution without independent exact verification."
    ),
}


INSUFFICIENT_FINDINGS = {
    "04d26fbb2aa5ad3824e1f452699b6e9265298b84a61458f524e19db273a70550": OPEN_FINDINGS[
        "04d26fbb2aa5ad3824e1f452699b6e9265298b84a61458f524e19db273a70550"
    ],
    "685ddfab242fe45cafc203a937769a4fe925423baf80e022b6e2e4411dd3ce90": OPEN_FINDINGS[
        "685ddfab242fe45cafc203a937769a4fe925423baf80e022b6e2e4411dd3ce90"
    ],
    "7514859e06c16389eab799267fdd69dfa29ef15be82abf4127561661d176cd2b": (
        "Retained evidence did not establish exact wording, occasion or a "
        "reliable source; current output now fails closed to Research incomplete."
    ),
    "9c84eb3fbb816db3d01e30e4ab2c09b4c57ade38214abaf4d9db4aedb94da8f4": OPEN_FINDINGS[
        "9c84eb3fbb816db3d01e30e4ab2c09b4c57ade38214abaf4d9db4aedb94da8f4"
    ],
    "e1d78bc63369145f6cf7462d8ad5f15dceef929c0469aff64d3bde1e7a188f18": (
        "At review time admitted evidence did not support the occasion and date "
        "or restrain the Meaning; inspected official document 104653 and its "
        "hash-bound correction now resolve future rendering."
    ),
    "eb1d2ebaac7e321e67174d2db5761d2bd008341ebb04b1abac4d4a927cd7a7d4": OPEN_FINDINGS[
        "eb1d2ebaac7e321e67174d2db5761d2bd008341ebb04b1abac4d4a927cd7a7d4"
    ],
}

RESOLVED_IDS = set(RESOLVED_CORRECTION_FINDINGS) | {
    "7514859e06c16389eab799267fdd69dfa29ef15be82abf4127561661d176cd2b",
    "e1d78bc63369145f6cf7462d8ad5f15dceef929c0469aff64d3bde1e7a188f18",
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _supported_reason(row: dict[str, Any], source_rows: list[dict[str, Any]]) -> str:
    verification = str(row.get("published_sections", {}).get("verification") or "")
    meaning = str(row.get("published_sections", {}).get("meaning") or "")
    if "misattributed" in verification.casefold():
        return (
            "The published reply explicitly disclosed the common "
            "misattribution and did not present the wording as verified Thatcher speech."
        )
    if not source_rows and any(
        marker in verification.casefold()
        for marker in ("not verified", "paraphrase", "composite")
    ):
        return (
            "The published reply explicitly disclosed wording uncertainty; its "
            "Meaning is a restrained explanation of the displayed wording."
        )
    if not meaning.strip():
        return (
            "The published reply added no interpretive Meaning, and the retained "
            "bibliographic evidence disclosed no material contradiction."
        )
    return (
        "The published Meaning stays within the quotation's stated claim; the "
        "retained review evidence disclosed no material context, verification "
        "or source contradiction."
    )


def _disposition(quote_id: str) -> str:
    if quote_id in INSUFFICIENT_FINDINGS:
        return "insufficient_to_assess"
    if quote_id in OPEN_FINDINGS or quote_id in RESOLVED_CORRECTION_FINDINGS:
        return "future_correction_needed"
    return "supported_as_published"


def _finding_reason(
    quote_id: str,
    row: dict[str, Any],
    source_rows: list[dict[str, Any]],
) -> str:
    if quote_id in INSUFFICIENT_FINDINGS:
        return INSUFFICIENT_FINDINGS[quote_id]
    if quote_id in OPEN_FINDINGS:
        return OPEN_FINDINGS[quote_id]
    if quote_id in RESOLVED_CORRECTION_FINDINGS:
        return RESOLVED_CORRECTION_FINDINGS[quote_id]
    return _supported_reason(row, source_rows)


def _follow_up(quote_id: str, disposition: str) -> str:
    if disposition == "supported_as_published":
        return "no_change_required"
    if quote_id in RESOLVED_IDS:
        return "resolved_by_current_rendering"
    return "remains_open"


def build_review(
    truth_audit_path: Path = TRUTH_AUDIT_PATH,
    source_role_audit_path: Path = SOURCE_ROLE_AUDIT_PATH,
    mtf_review_path: Path = MTF_REVIEW_PATH,
) -> dict[str, Any]:
    """Return the deterministic per-record manual-review ledger."""
    truth = _load(truth_audit_path)
    source_audit = _load(source_role_audit_path)
    mtf = _load(mtf_review_path)
    rows = truth.get("records", {}).get("published_reply_reviews")
    source_items = source_audit.get("items")
    mtf_rows = mtf.get("records")
    if not isinstance(rows, list) or not isinstance(source_items, dict) or not isinstance(mtf_rows, list):
        raise RuntimeError("semantic-review inputs have incompatible schemas")

    mtf_by_quote: dict[str, list[dict[str, Any]]] = {}
    for mtf_row in mtf_rows:
        if not _is_successful_mtf_page_record(mtf_row):
            continue
        mtf_by_quote.setdefault(str(mtf_row.get("quote_id") or ""), []).append(mtf_row)

    records = []
    for history_index, row in enumerate(rows):
        quote_id = str(row.get("quote_id") or "")
        source_item = source_items.get(quote_id)
        if not isinstance(source_item, dict):
            raise RuntimeError(f"missing source-role item for published reply: {quote_id}")
        renderable = [
            source
            for source in source_item.get("renderable_sources", [])
            if isinstance(source, dict)
        ]
        mtf_checks = sorted(
            {
                (
                    str(check.get("document_number") or ""),
                    str(check.get("status") or ""),
                )
                for check in mtf_by_quote.get(quote_id, [])
                if check.get("document_number")
            }
        )
        disposition = _disposition(quote_id)
        records.append({
            "history_index": history_index,
            "quote_id": quote_id,
            "quote_text": row.get("quote_text"),
            "quote_text_sha256": _sha256_text(str(row.get("quote_text") or "")),
            "parent_post_id": row.get("parent_post_id"),
            "reply_post_id": row.get("reply_post_id"),
            "published_reply_sha256": _sha256_text(str(row.get("published_reply_text") or "")),
            "current_reply_sha256": _sha256_text(str(row.get("current_public_reply_text") or "")),
            "disposition": disposition,
            "follow_up_status": _follow_up(quote_id, disposition),
            "reason": _finding_reason(quote_id, row, renderable),
            "evidence_basis": {
                "renderable_source_ids": sorted({
                    str(source.get("source_id") or "")
                    for source in renderable
                    if source.get("source_id")
                }),
                "renderable_source_quality_classes": sorted({
                    str(source.get("source_quality_class") or "")
                    for source in renderable
                    if source.get("source_quality_class")
                }),
                "public_context_supported_fields": list(
                    source_item.get("public_context_supported_fields", [])
                ),
                "wording_confidence": source_item.get("confidence_after", {}).get("wording"),
                "official_mtf_document_checks": [
                    {"document_number": number, "machine_status": status}
                    for number, status in mtf_checks
                ],
            },
        })

    disposition_counts = Counter(record["disposition"] for record in records)
    slices = []
    for start, end in ((0, 26), (26, 52), (52, 77)):
        counts = Counter(record["disposition"] for record in records[start:end])
        slices.append({
            "sorted_history_slice": f"[{start}:{end}]",
            "reviewed": end - start,
            "supported_as_published": counts["supported_as_published"],
            "future_correction_needed": counts["future_correction_needed"],
            "insufficient_to_assess": counts["insufficient_to_assess"],
        })
    follow_up_counts = Counter(record["follow_up_status"] for record in records)
    remaining = [record for record in records if record["follow_up_status"] == "remains_open"]
    remaining_dispositions = Counter(record["disposition"] for record in remaining)

    return {
        "schema_version": SCHEMA_VERSION,
        "review_kind": REVIEW_KIND,
        "reviewed_at": "2026-07-22",
        "history_policy": (
            "Published history is immutable; every recommendation applies only "
            "to future rendering."
        ),
        "input_evidence": {
            "truth_audit": {
                "path": str(truth_audit_path.relative_to(ROOT)),
                "sha256": _sha256_bytes(truth_audit_path.read_bytes()),
            },
            "source_role_audit": {
                "path": str(source_role_audit_path.relative_to(ROOT)),
                "sha256": _sha256_bytes(source_role_audit_path.read_bytes()),
            },
            "mtf_primary_review": {
                "path": str(mtf_review_path.relative_to(ROOT)),
                "sha256": _sha256_bytes(mtf_review_path.read_bytes()),
                "successful_document_count": mtf.get("counts", {}).get(
                    "page_retrieval_success_count"
                ),
                "failed_document_count": mtf.get("counts", {}).get(
                    "page_retrieval_failure_count"
                ),
                "not_found_document_count": mtf.get("counts", {}).get(
                    "official_document_not_found_count", 0
                ),
            },
            "published_history": {
                "path": "historical_context_reply_history.json",
                "sha256": truth.get("input_hashes", {}).get(
                    "historical_context_reply_history.json"
                ),
                "completed_entry_count": len(rows),
                "unique_quote_count": len({record["quote_id"] for record in records}),
                "parent_post_ids_sha256": truth.get("coverage", {}).get(
                    "published_history_parent_post_ids_sha256"
                ),
                "quote_ids_sha256": truth.get("coverage", {}).get(
                    "published_history_quote_ids_sha256"
                ),
            },
        },
        "review_method": {
            "source_of_review_rows": (
                "historical_context_evidence_truth_audit.json "
                "records.published_reply_reviews"
            ),
            "fields_compared": ["Context", "Meaning", "Verification", "Source"],
            "evidence_used": [
                "retained source-role records and supporting passages",
                "official Margaret Thatcher Foundation page comparisons where available",
                "retained Hansard, newspaper and bibliographic evidence",
                "the quotation's own semantic scope",
            ],
            "machine_status_is_not_semantic_adjudication": True,
            "automatic_evidence_promotion_authorised": False,
        },
        "manual_review_totals": {
            "reviewed": len(records),
            "supported_as_published": disposition_counts["supported_as_published"],
            "future_correction_needed": disposition_counts["future_correction_needed"],
            "insufficient_to_assess": disposition_counts["insufficient_to_assess"],
        },
        "review_slices": slices,
        "current_render_follow_up": {
            "reviewed_non_supported_or_uncertain": (
                disposition_counts["future_correction_needed"]
                + disposition_counts["insufficient_to_assess"]
            ),
            "already_resolved_by_current_conservative_rendering": follow_up_counts[
                "resolved_by_current_rendering"
            ],
            "remaining_future_correction_or_research_items": follow_up_counts[
                "remains_open"
            ],
            "remaining_original_future_correction_items": remaining_dispositions[
                "future_correction_needed"
            ],
            "remaining_original_insufficient_evidence_items": remaining_dispositions[
                "insufficient_to_assess"
            ],
            "known_104653_corrected_for_future_rendering": (
                next(
                    record for record in records
                    if record["quote_id"].startswith("e1d78bc6")
                )["follow_up_status"]
                == "resolved_by_current_rendering"
            ),
            "published_history_modified": False,
        },
        "records": records,
        "remaining_items": [
            {
                "quote_id": record["quote_id"],
                "disposition": record["disposition"],
                "issue": record["reason"],
            }
            for record in remaining
        ],
    }


def validate_review(
    review: dict[str, Any],
    truth_audit_path: Path = TRUTH_AUDIT_PATH,
    source_role_audit_path: Path = SOURCE_ROLE_AUDIT_PATH,
    mtf_review_path: Path = MTF_REVIEW_PATH,
) -> dict[str, int]:
    """Fail closed unless the stored ledger equals its reviewed source mapping."""
    expected = build_review(truth_audit_path, source_role_audit_path, mtf_review_path)
    if review != expected:
        raise RuntimeError("published-reply semantic review differs from reviewed evidence ledger")
    records = review["records"]
    for record in records:
        if (
            record.get("disposition") not in DISPOSITIONS
            or record.get("follow_up_status") not in FOLLOW_UP_STATUSES
            or not str(record.get("reason") or "").strip()
            or not isinstance(record.get("evidence_basis"), dict)
        ):
            raise RuntimeError("published-reply semantic review record is invalid")
    return {
        "reviewed": len(records),
        "supported_as_published": review["manual_review_totals"]["supported_as_published"],
        "future_correction_needed": review["manual_review_totals"]["future_correction_needed"],
        "insufficient_to_assess": review["manual_review_totals"]["insufficient_to_assess"],
        "resolved": review["current_render_follow_up"][
            "already_resolved_by_current_conservative_rendering"
        ],
        "remaining": review["current_render_follow_up"][
            "remaining_future_correction_or_research_items"
        ],
    }


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    """Build or validate the published-reply semantic-review ledger."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        counts = validate_review(_load(args.output))
    else:
        review = build_review()
        _atomic_write(args.output, review)
        counts = validate_review(review)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
