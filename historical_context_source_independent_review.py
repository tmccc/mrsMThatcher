#!/usr/bin/env python3
"""Build and validate the frozen independent review of AI-located sources."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


REVIEW_SCHEMA_VERSION = 1
REVIEW_POLICY_VERSION = "historical-context-ai-source-independent-review-v1"
REVIEW_FILENAME = "historical_context_source_independent_review.json"

_ROLES = {
    "wording_verification",
    "attribution_support",
    "source_event_support",
    "historical_context_support",
    "secondary_recollection",
    "discovery_only",
    "rejected_irrelevant",
}
_CLAIMS = {"wording", "attribution", "source_event", "date", "historical_context"}
_QUALITIES = {
    "strong_primary_evidence",
    "reliable_secondary_evidence",
    "secondary_recollection",
    "discovery_lead_only",
    "irrelevant_or_corrupt",
    "circular_attribution",
    "broken_or_non_verifying_url",
    "insufficiently_located_evidence",
}
_ACTIONS = {
    "keep",
    "relabel",
    "downgrade",
    "remove_from_rendered_output",
    "reject_as_irrelevant",
    "requires_further_research",
}
_DECISIONS = {"keep", "relabel", "downgrade", "reject"}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_map(*manifests: dict[str, Any] | None) -> dict[str, tuple[str, dict[str, Any]]]:
    sources: dict[str, tuple[str, dict[str, Any]]] = {}
    for manifest in manifests:
        for quote_id, item in (manifest or {}).get("items", {}).items():
            for source in item.get("validated_sources", []):
                source_id = source["source_id"]
                if source_id in sources:
                    raise RuntimeError(f"duplicate researched source ID: {source_id}")
                sources[source_id] = (quote_id, source)
    return sources


_SOURCE_OVERRIDES: dict[str, dict[str, Any]] = {
    # An OUP blog round-up repeats a familiar quotation but supplies no source
    # locator. The independently verified MTF transcript remains available.
    "5daed0133a71b6a1cea243187f7ce47060c95362fc2375d8bbf54b8c90241e80": {
        "decision": "reject",
        "final_assigned_roles": ["discovery_only"],
        "final_source_quality_class": "circular_attribution",
        "final_claims_supported": [],
        "final_action": "remove_from_rendered_output",
        "rationale": (
            "The OUP blog is an unsourced quotation round-up. It documents later "
            "circulation but does not independently verify Thatcher's wording."
        ),
    },
    # The page labels the question as an interviewer paraphrase and the answer
    # as Thatcher's. The combined corpus wording therefore cannot be verified.
    "825ab7405ec0907948c94590e1aa95bc3c896aa7e0a6cf05b0495c61cc3eca83": {
        "decision": "downgrade",
        "final_assigned_roles": ["attribution_support"],
        "final_source_quality_class": "strong_primary_evidence",
        "final_claims_supported": ["attribution"],
        "final_action": "downgrade",
        "rationale": (
            "The transcript supports Thatcher's answer, but 'What is success?' "
            "is an interviewer question paraphrased by the archive."
        ),
    },
    # Thatcher says "It is our job to do just that" after quoting Ted Short.
    # The packet is correctly labelled a paraphrase, not her exact wording.
    "63baba4fe352ef619e80410c420eb4b788d79a3cc137419ee8d87235a35e2ba7": {
        "decision": "downgrade",
        "final_assigned_roles": ["attribution_support"],
        "final_source_quality_class": "strong_primary_evidence",
        "final_claims_supported": ["attribution"],
        "final_action": "downgrade",
        "rationale": (
            "The packet paraphrase combines Ted Short's wording with Thatcher's "
            "reply; the page supports the attribution and meaning, not exact wording."
        ),
    },
    # The Independent explicitly reports a private 1982 remark to Paul Johnson;
    # it is a later recollection and there is no contemporary transcript.
    "f89fca2576eab8c81cc3184dda1acfe1a687c1d1319b7c72a9ae82276dccfdf9": {
        "decision": "relabel",
        "final_assigned_roles": [
            "wording_verification", "attribution_support", "secondary_recollection"
        ],
        "final_source_quality_class": "secondary_recollection",
        "final_claims_supported": ["wording", "attribution"],
        "final_action": "relabel",
        "rationale": (
            "The page repeats Paul Johnson's later account of a private 1982 "
            "conversation. It is recollection evidence, not a primary transcript."
        ),
    },
}


_APPROXIMATE_REVIEWS: dict[str, dict[str, Any]] = {
    "7ddc1f02b2b8f620262375aa91dd1867b12babb44d6cb3b286cde838feed6df4": {
        "actor_preserved": True, "meaning_preserved": True,
        "direction_preserved": True, "polarity_preserved": True,
        "dates_preserved": True, "numbers_preserved": True,
        "historically_defensible": True,
        "notes": "The source says 'We have' rather than 'We shall have'; the warning and direction are unchanged.",
    },
    "81942922ff3fba67706d97453ca057414ca6a1682e90f42e90b113d9004efb66": {
        "actor_preserved": True, "meaning_preserved": True,
        "direction_preserved": True, "polarity_preserved": True,
        "dates_preserved": True, "numbers_preserved": True,
        "historically_defensible": True,
        "notes": "The difference is an archive page-break marker; the attributed proposition is unchanged.",
    },
    "825ab7405ec0907948c94590e1aa95bc3c896aa7e0a6cf05b0495c61cc3eca83": {
        "actor_preserved": False, "meaning_preserved": True,
        "direction_preserved": True, "polarity_preserved": True,
        "dates_preserved": True, "numbers_preserved": True,
        "historically_defensible": False,
        "notes": "The question belongs to the interviewer; only the following answer is Thatcher's wording.",
    },
    "3741616d1bc5ffba5b1676b4b631fe7e1b6581dd6c9e478e02fe12466a428287": {
        "actor_preserved": True, "meaning_preserved": True,
        "direction_preserved": True, "polarity_preserved": True,
        "dates_preserved": True, "numbers_preserved": True,
        "historically_defensible": True,
        "notes": "The only inserted text is an archive page-break marker.",
    },
    "f569d184ec62b1a4468bc0707578ea9567c7eb7387e300d9b6e8803b18e49cc9": {
        "actor_preserved": True, "meaning_preserved": True,
        "direction_preserved": True, "polarity_preserved": True,
        "dates_preserved": True, "numbers_preserved": True,
        "historically_defensible": True,
        "notes": "The only inserted text is an archive page-break marker.",
    },
    "63baba4fe352ef619e80410c420eb4b788d79a3cc137419ee8d87235a35e2ba7": {
        "actor_preserved": False, "meaning_preserved": True,
        "direction_preserved": True, "polarity_preserved": True,
        "dates_preserved": True, "numbers_preserved": True,
        "historically_defensible": False,
        "notes": "The paraphrase borrows Ted Short's wording and combines it with Thatcher's reply.",
    },
    "4585540959f1bd41ad50d5b7c607c3efc7765183e2ce983c2cf99bbafae896cb": {
        "actor_preserved": True, "meaning_preserved": True,
        "direction_preserved": True, "polarity_preserved": True,
        "dates_preserved": True, "numbers_preserved": True,
        "historically_defensible": True,
        "notes": "Minor omissions and normalisations preserve 1989, 1979, actors, direction and positive polarity.",
    },
    "227e53f652720fefe010f07e1e9e7d48549faca69e90919d3000fe5caaad7aac": {
        "actor_preserved": True, "meaning_preserved": True,
        "direction_preserved": True, "polarity_preserved": True,
        "dates_preserved": True, "numbers_preserved": True,
        "historically_defensible": True,
        "notes": "Ampersands, punctuation and an applause marker differ; actors and proposition do not.",
    },
    "7b2c51a92899baf0a06431342ca9d17086e4a9a2af42bb4b17e45cfdc9bcb3fd": {
        "actor_preserved": True, "meaning_preserved": True,
        "direction_preserved": True, "polarity_preserved": True,
        "dates_preserved": True, "numbers_preserved": True,
        "historically_defensible": True,
        "notes": "The corpus omits the article 'a'; the source is a 1950 Dartford report, not the packet's later book occasion.",
    },
}


def validate_independent_review(
    manifest: dict[str, Any],
    packets: dict[str, dict[str, Any]],
    researched_evidence: dict[str, Any] | None,
    openai_researched_evidence: dict[str, Any] | None,
) -> None:
    """Fail closed unless every accepted AI-located source has one frozen review."""
    if not isinstance(manifest, dict):
        raise RuntimeError("historical-context independent source review is invalid")
    sources = _source_map(researched_evidence, openai_researched_evidence)
    items = manifest.get("items")
    if (
        manifest.get("schema_version") != REVIEW_SCHEMA_VERSION
        or manifest.get("policy_version") != REVIEW_POLICY_VERSION
        or not isinstance(items, dict)
        or set(items) != set(sources)
        or manifest.get("reviewed_source_count") != len(items)
        or manifest.get("reviewed_source_count") != 34
        or manifest.get("unique_page_count") != len({s[1]["url"] for s in sources.values()})
        or manifest.get("unique_page_count") != 28
    ):
        raise RuntimeError("historical-context independent source review coverage differs")
    approximate_count = 0
    for source_id, review in items.items():
        quote_id, source = sources[source_id]
        approximate = source.get("wording_match_kind") == "approximate_semantic_guarded"
        approximate_count += int(approximate)
        match_review = review.get("approximate_match_review")
        parsed = urlsplit(str(review.get("final_url") or ""))
        if (
            review.get("source_id") != source_id
            or review.get("quote_id") != quote_id
            or quote_id not in packets
            or review.get("quote_text_sha256") != _sha256(packets[quote_id]["quote_text"].encode())
            or review.get("researched_source_sha256") != _sha256(_canonical_json(source))
            or review.get("source_url") != source["url"]
            or review.get("http_status") != 200
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not str(review.get("page_title") or "").strip()
            or not str(review.get("stable_locator") or "").strip()
            or review.get("exact_supporting_passage") != source["exact_supporting_passage"]
            or review.get("exact_supporting_passage_sha256")
            != source["exact_supporting_passage_sha256"]
            or review.get("provider_fetched_body_sha256")
            != source["fetched_body_sha256"]
            or not all(
                isinstance(review.get(field), str)
                and len(review[field]) == 64
                and set(review[field]) <= set("0123456789abcdef")
                for field in ("current_page_body_sha256", "researched_source_sha256")
            )
            or review.get("decision") not in _DECISIONS
            or not set(review.get("final_assigned_roles", [])) <= _ROLES
            or not review.get("final_assigned_roles")
            or review.get("final_source_quality_class") not in _QUALITIES
            or not set(review.get("final_claims_supported", [])) <= _CLAIMS
            or review.get("final_action") not in _ACTIONS
            or not str(review.get("rationale") or "").strip()
        ):
            raise RuntimeError(f"invalid independent source review: {source_id}")
        if review["decision"] == "keep" and (
            review["final_assigned_roles"] != source["assigned_roles"]
            or review["final_source_quality_class"]
            != source["source_quality_class"]
            or review["final_claims_supported"] != source["claims_supported"]
            or review["final_action"] != "keep"
        ):
            raise RuntimeError(f"unchanged source review differs: {source_id}")
        if approximate:
            required = {
                "actor_preserved", "meaning_preserved", "direction_preserved",
                "polarity_preserved", "dates_preserved", "numbers_preserved",
                "historically_defensible", "exact_wording_verified", "notes",
            }
            if (
                not isinstance(match_review, dict)
                or set(match_review) != required
                or any(type(match_review[field]) is not bool for field in required - {"notes"})
                or match_review["exact_wording_verified"]
                or not str(match_review["notes"]).strip()
                or (
                    not match_review["historically_defensible"]
                    and "wording" in review["final_claims_supported"]
                )
            ):
                raise RuntimeError(f"invalid approximate source review: {source_id}")
        elif match_review is not None:
            raise RuntimeError(f"unexpected approximate source review: {source_id}")
        if review["decision"] == "reject" and (
            review["final_claims_supported"]
            or set(review["final_assigned_roles"]) not in (
                {"discovery_only"}, {"rejected_irrelevant"}
            )
        ):
            raise RuntimeError(f"rejected source remains evidentiary: {source_id}")
        if "secondary_recollection" in review["final_assigned_roles"] and (
            review["final_source_quality_class"] != "secondary_recollection"
        ):
            raise RuntimeError(f"recollection quality differs: {source_id}")
    if approximate_count != 9 or manifest.get("approximate_match_count") != 9:
        raise RuntimeError("independent approximate source review count differs")


def build_independent_review(
    research_dir: Path, fetch_snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Build the frozen review from a fresh, bounded page-fetch snapshot."""
    gemini_path = research_dir / "historical_context_source_research.json"
    openai_path = research_dir / "historical_context_source_openai_research.json"
    gemini = json.loads(gemini_path.read_text(encoding="utf-8")) if gemini_path.exists() else None
    openai = json.loads(openai_path.read_text(encoding="utf-8")) if openai_path.exists() else None
    packet_data = json.loads((research_dir / "research_packets.json").read_text(encoding="utf-8"))
    packets = packet_data.get("items", packet_data)
    sources = _source_map(gemini, openai)
    fetched_rows: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for page in fetch_snapshot.get("items", []):
        for row in page.get("rows", []):
            source_id = row["source_id"]
            if source_id in fetched_rows:
                raise RuntimeError(f"duplicate fetched review source: {source_id}")
            fetched_rows[source_id] = (page, row)
    if set(fetched_rows) != set(sources):
        raise RuntimeError("fresh page fetch does not cover every researched source")
    items: dict[str, Any] = {}
    for source_id, (quote_id, source) in sorted(sources.items()):
        page, fetched = fetched_rows[source_id]
        if not fetched.get("stored_passage_on_page"):
            raise RuntimeError(f"supporting passage missing from current page: {source_id}")
        override = _SOURCE_OVERRIDES.get(source_id, {})
        quality = override.get("final_source_quality_class", source["source_quality_class"])
        roles = override.get("final_assigned_roles", source["assigned_roles"])
        claims = override.get("final_claims_supported", source["claims_supported"])
        if override:
            decision = override["decision"]
            action = override["final_action"]
            rationale = override["rationale"]
        else:
            decision = "keep"
            action = "keep"
            if quality == "strong_primary_evidence":
                rationale = (
                    "The archive page identifies the speech, interview or article, "
                    "records its source metadata and contains the supporting passage."
                )
            else:
                rationale = (
                    "The named publication explicitly attributes and reproduces the "
                    "recorded passage; it is retained only as secondary evidence."
                )
        match_review = None
        if source.get("wording_match_kind") == "approximate_semantic_guarded":
            match_review = {
                **_APPROXIMATE_REVIEWS[source_id],
                "exact_wording_verified": False,
            }
        title = str(page["page_title"]).strip()
        items[source_id] = {
            "source_id": source_id,
            "quote_id": quote_id,
            "quote_text_sha256": _sha256(packets[quote_id]["quote_text"].encode()),
            "researched_source_sha256": _sha256(_canonical_json(source)),
            "source_url": source["url"],
            "final_url": page["final_url"],
            "http_status": page["http_status"],
            "page_title": title,
            "checked_at": fetch_snapshot["checked_at"],
            "current_page_body_sha256": page["body_sha256"],
            "provider_fetched_body_sha256": source["fetched_body_sha256"],
            "exact_supporting_passage": source["exact_supporting_passage"],
            "exact_supporting_passage_sha256": source["exact_supporting_passage_sha256"],
            "stable_locator": f"{title} — {page['final_url']}",
            "decision": decision,
            "final_assigned_roles": roles,
            "final_source_quality_class": quality,
            "final_claims_supported": claims,
            "final_action": action,
            "rationale": rationale,
            "approximate_match_review": match_review,
        }
    manifest = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "policy_version": REVIEW_POLICY_VERSION,
        "reviewed_at": fetch_snapshot["checked_at"],
        "reviewed_source_count": len(items),
        "unique_page_count": len({item["source_url"] for item in items.values()}),
        "approximate_match_count": sum(
            item["approximate_match_review"] is not None for item in items.values()
        ),
        "items": items,
    }
    validate_independent_review(manifest, packets, gemini, openai)
    return manifest


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically save canonical review JSON with an fsync boundary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str] | None = None) -> int:
    """Build the independent-review manifest from an already fetched snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", type=Path, required=True)
    parser.add_argument("--fetch-snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    snapshot = json.loads(args.fetch_snapshot.read_text(encoding="utf-8"))
    manifest = build_independent_review(args.research_dir, snapshot)
    output = args.output or args.research_dir / REVIEW_FILENAME
    atomic_write_json(output, manifest)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
