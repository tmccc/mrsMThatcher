#!/usr/bin/env python3
"""Build a compact ChatGPT Deep Research pilot from the source-audit corpus.

The output is an offline research input only. It does not modify quotation
packets, eligibility, production state, or the active source-role audit.
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
from urllib.parse import urlparse


PACK_SCHEMA_VERSION = 1
PACK_POLICY_VERSION = "historical-context-chatgpt-deep-research-pilot-v1"

# Twenty records needing research that were not included in the previous
# OpenAI queue, followed by four source-specific book cases and one negative
# control from the previous no-result queue.
PILOT_COHORTS = {
    "previously_unsearched_research_gap": [
        "00426881d274",
        "053f974c97ba",
        "06629311412b",
        "073d64fa2701",
        "0acb6cd5c5ec",
        "0bde69606fd1",
        "2b6aa4dc33fc",
        "2f8ac7d9bbea",
        "34114f8f8fa5",
        "453007972cd0",
        "5a4bdeaf484b",
        "5fbdcee710fe",
        "7c3c83a5f13c",
        "80bfb8b6d1d7",
        "91fc151a519c",
        "9cff99eb9aca",
        "a7510dd49389",
        "c922a5806879",
        "e74879dd8293",
        "f6b4fb6ced97",
    ],
    "prior_search_no_result_with_precise_source_lead": [
        "ec0799ceb8ea",
        "db46e7519946",
        "f0d85c7301e8",
        "c0db67b6bec3",
    ],
    "prior_search_no_result_negative_control": ["eb1d2ebaac7e"],
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _resolve_prefix(prefix: str, quote_ids: set[str]) -> str:
    matches = sorted(quote_id for quote_id in quote_ids if quote_id.startswith(prefix))
    if len(matches) != 1:
        raise ValueError(f"quote prefix {prefix!r} resolved to {len(matches)} records")
    return matches[0]


def _compact_source(row: dict[str, Any]) -> dict[str, Any]:
    url = str(row.get("public_url") or row.get("source_url") or "").strip()
    parsed = urlparse(url)
    if parsed.hostname and (
        parsed.hostname.endswith("google.com")
        or parsed.hostname.endswith("googleusercontent.com")
    ):
        url = ""
    passages = [
        str(value).strip()
        for value in row.get("supporting_passages", [])
        if str(value).strip()
    ]
    return {
        "title": str(row.get("public_title") or row.get("source_title") or "").strip(),
        "url": url,
        "source_type": str(row.get("source_type") or "").strip(),
        "source_quality_class": str(row.get("source_quality_class") or "").strip(),
        "assigned_roles": list(row.get("assigned_roles") or []),
        "claims_supported": list(row.get("claims_supported") or []),
        "claims_not_supported": list(row.get("claims_not_supported") or []),
        "action": str(row.get("action") or "").strip(),
        "supporting_passages": passages[:3],
        "rationale": str(row.get("rationale") or "").strip(),
    }


def _deduplicated_sources(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        compact = _compact_source(row)
        key = json.dumps(compact, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(compact)
        if len(result) >= limit:
            break
    return result


def _research_objectives(audit_item: dict[str, Any]) -> list[str]:
    confidence = audit_item.get("confidence_after") or {}
    objectives: list[str] = []
    labels = {
        "attribution": "Establish source-grounded Thatcher attribution.",
        "wording": "Establish exact wording or a defensible historical variant.",
        "source_event": "Locate and verify the source event or publication occasion.",
        "date": "Locate and verify the event or publication date.",
        "historical_context": "Locate claim-bearing historical-context evidence.",
    }
    for field, label in labels.items():
        if str(confidence.get(field, "unknown")) != "high":
            objectives.append(label)
    return objectives


def _prior_search_summary(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    reasons = Counter(
        str(row.get("reason") or "unspecified")
        for row in item.get("rejected_sources", [])
        if isinstance(row, dict)
    )
    return {
        "final_outcome": item.get("final_outcome"),
        "search_call_count": item.get("search_call_count"),
        "cited_source_count": item.get("cited_source_count"),
        "validated_source_count": len(item.get("validated_sources", [])),
        "top_rejection_reasons": [
            {"reason": reason, "count": count}
            for reason, count in reasons.most_common(8)
        ],
    }


def _build_queue(project_dir: Path) -> tuple[dict[str, Any], dict[str, str]]:
    corpus_dir = project_dir / "semantic_alignment_research/quote_research_full_001"
    audit_dir = project_dir / "semantic_alignment_research/historical_context_source_audit_001"
    source_paths = {
        "research_packets": corpus_dir / "research_packets.json",
        "source_role_audit": corpus_dir / "historical_context_source_role_audit.json",
        "openai_research": corpus_dir / "historical_context_source_openai_research.json",
        "audit_summary": audit_dir / "audit_summary.json",
    }
    packets = _json(source_paths["research_packets"])["items"]
    source_audit_document = _json(source_paths["source_role_audit"])
    source_audit = source_audit_document["items"]
    prior_openai = _json(source_paths["openai_research"])
    summary = _json(source_paths["audit_summary"])

    quote_ids = set(packets)
    requires_research = set(summary["requires_research_quote_ids"])
    no_reliable_source = set(summary["no_reliable_source_quote_ids"])
    prior_queue = set(prior_openai["queue_quote_ids"])
    unresolved = {
        quote_id for quote_id, item in source_audit.items() if not item["attribution_eligible"]
    }

    selected: list[tuple[str, str]] = []
    for cohort, prefixes in PILOT_COHORTS.items():
        for prefix in prefixes:
            selected.append((cohort, _resolve_prefix(prefix, quote_ids)))
    selected_ids = [quote_id for _, quote_id in selected]
    if len(selected_ids) != 25 or len(set(selected_ids)) != 25:
        raise ValueError("pilot must contain exactly 25 unique quotation IDs")
    if not set(selected_ids) <= requires_research:
        raise ValueError("every pilot record must require further research")
    if set(selected_ids) & unresolved:
        raise ValueError("pilot must not contain unresolved or attribution-excluded records")

    unsearched = {
        quote_id
        for cohort, quote_id in selected
        if cohort == "previously_unsearched_research_gap"
    }
    retried = set(selected_ids) - unsearched
    if len(unsearched) != 20 or unsearched & prior_queue:
        raise ValueError("expected exactly 20 records absent from the prior OpenAI queue")
    if len(retried) != 5 or not retried <= prior_queue or not retried <= no_reliable_source:
        raise ValueError("expected exactly five prior no-result records")

    queue_items: list[dict[str, Any]] = []
    for sequence, (cohort, quote_id) in enumerate(selected, start=1):
        packet = packets[quote_id]
        audit_item = source_audit[quote_id]
        existing = _deduplicated_sources(
            list(audit_item.get("renderable_sources", [])), limit=8
        )
        leads = _deduplicated_sources(
            list(audit_item.get("model_proposed_source_leads", []))
            + [
                row
                for row in audit_item.get("sources", [])
                if row.get("action") != "keep"
            ],
            limit=10,
        )
        queue_items.append(
            {
                "sequence": sequence,
                "cohort": cohort,
                "quote_id": quote_id,
                "quote_text": packet["quote_text"],
                "verified_text": packet.get("verified_text"),
                "speaker": packet.get("speaker"),
                "wording_status": audit_item.get("current_wording_status"),
                "verification_status": packet.get("verification_status"),
                "research_confidence": packet.get("research_confidence"),
                "date": packet.get("date"),
                "source_event": packet.get("source_event"),
                "stable_locator": packet.get("stable_locator"),
                "immediate_subject": packet.get("immediate_subject"),
                "historical_context": packet.get("historical_context"),
                "text_variation_notes": packet.get("text_variation_notes"),
                "unresolved_questions": packet.get("unresolved_questions"),
                "current_public_verification_wording": audit_item.get(
                    "public_verification_wording"
                ),
                "current_field_confidence": audit_item.get("confidence_after"),
                "research_objectives": _research_objectives(audit_item),
                "existing_renderable_sources": existing,
                "existing_suppressed_sources_and_leads": leads,
                "previous_openai_search": _prior_search_summary(
                    prior_openai["items"].get(quote_id)
                ),
            }
        )

    hashes = {str(path.relative_to(project_dir)): _sha256(path) for path in source_paths.values()}
    queue = {
        "schema_version": PACK_SCHEMA_VERSION,
        "policy_version": PACK_POLICY_VERSION,
        "source_audit_date": source_audit_document["audit_date"],
        "purpose": "Source discovery only; every proposed source requires independent local verification.",
        "record_count": len(queue_items),
        "cohort_counts": dict(Counter(item["cohort"] for item in queue_items)),
        "source_file_hashes": hashes,
        "records": queue_items,
    }
    return queue, hashes


SOURCE_POLICY = """# Historical quotation source acceptance policy

Research is source discovery, not adjudication. Do not change quotation text,
IDs, eligibility, wording status, or confidence merely because a model proposes
a source.

## Accepted source roles

- `wording_verification`: the accessible source contains the quotation wording.
- `attribution_support`: the accessible source attributes the words to Thatcher.
- `source_event_support`: the source establishes the speech, interview, book, or
  other publication occasion.
- `historical_context_support`: the source directly supports a specific context
  claim.
- `secondary_recollection`: a named later recollection, labelled as such.
- `discovery_only`: a lead that does not itself prove a displayed claim.
- `rejected_irrelevant`: unrelated, circular, corrupt, or non-verifying material.

## Acceptance rules

1. Prefer Thatcher Foundation transcripts, Hansard and other official records,
   Thatcher-authored books with a page/chapter locator, contemporary newspaper
   reports, and clearly labelled memoir evidence.
2. A book supports wording only when the relevant page or searchable passage is
   actually inspected. A catalogue entry or review is not wording evidence.
3. Use a direct canonical page URL. Never return a search-results URL, grounding
   redirect, tracking redirect, or generated citation.
4. Reject quotation aggregators, copied quotation lists, Goodreads, AZ Quotes,
   BrainyQuote, Wikiquote, Medium reposts, homework sites, and unattributed video
   compilations as verification evidence.
5. Exact wording requires an exact source passage. Similar wording can only be a
   `variant`, and only when actor, action, relationship, direction, polarity,
   dates and quantities remain materially unchanged.
6. A later memoir or recollection cannot establish an exact primary transcript.
7. A source supporting only a subsidiary fact must not be labelled as quotation
   verification.
8. If a page cannot be opened and inspected, treat it as a lead, not evidence.
9. Never infer that a source supports a claim from its title, snippet or domain.
10. `No reliable source located` is a valid and preferred result when evidence
    is insufficient.

All accepted results will be fetched and independently checked locally before
they can affect public historical-context output.
"""


PROMPT = """# ChatGPT Deep Research task: Margaret Thatcher quotation sources

Use Deep Research with the public web and the two attached files:

- `pilot_queue.json`
- `source_acceptance_policy.md`

Research every one of the 25 queue records. This is a source-discovery task, not
a request to rewrite quotations or decide production eligibility.

## Required method

1. Start from the exact quotation, but also use the recorded source event, date,
   book, chapter, document number and historically plausible wording variants.
2. Inspect every proposed page itself. Do not rely on search snippets or another
   model's assertion.
3. Prioritise primary and claim-bearing sources, especially
   `margaretthatcher.org`, Parliament/Hansard, publisher or accessible book
   pages, contemporary newspaper archives, and clearly identified memoirs.
4. Apply `source_acceptance_policy.md` strictly. Previously rejected or
   suppressed links are warnings, not evidence.
5. Do not omit difficult records. Return `no_reliable_source_located` when that
   is the honest result.
6. Do not treat an 80% lexical match as sufficient by itself. For a historical
   variant, compare actor, action, relationship, direction, polarity, date and
   quantities and explain every material difference.
7. Do not call any wording exact unless the inspected source contains it.

## Required result for every record

Return a brief human-readable findings section followed by one fenced JSON
object with this shape:

```json
{
  "schema_version": 1,
  "completed_record_count": 25,
  "results": [
    {
      "sequence": 1,
      "quote_id": "",
      "quote_text": "",
      "outcome": "reliable_source_found|defensible_variant_found|secondary_recollection_only|no_reliable_source_located",
      "wording_assessment": "exact|variant|recollection|unverified",
      "sources": [
        {
          "direct_url": "",
          "title": "",
          "publisher_or_archive": "",
          "publication_or_event_date": "",
          "source_roles": ["wording_verification"],
          "source_quality": "strong_primary|reliable_secondary|secondary_recollection|discovery_only|rejected",
          "exact_supporting_passage": "",
          "stable_locator": "page, chapter, speech date, column, document ID, or paragraph",
          "claims_supported": [],
          "claims_not_supported": [],
          "wording_differences": [],
          "rationale": ""
        }
      ],
      "rejected_candidates": [
        {"url": "", "reason": ""}
      ],
      "remaining_uncertainty": [],
      "confidence": "high|medium|low"
    }
  ]
}
```

Constraints:

- Preserve each supplied `quote_id` and `quote_text` byte-for-byte in the JSON.
- Include exactly 25 results, in queue sequence order, with no duplicate IDs.
- Include no more than three accepted sources per quotation.
- A source URL must lead directly to the inspected page.
- A short exact passage and stable locator are mandatory for any accepted role.
- Do not invent page numbers, dates, titles, passages or URLs.
- Do not recommend changing quotation eligibility.

Before finishing, count the input and output IDs and explicitly confirm that all
25 were handled.
"""


README = """# ChatGPT Deep Research pilot

This directory contains a 25-record historical-source research pilot. It is
deliberately separate from production and does not modify quotation eligibility.

## Files to upload

Upload these three files to one ChatGPT conversation:

1. `CHATGPT_DEEP_RESEARCH_PROMPT.md`
2. `source_acceptance_policy.md`
3. `pilot_queue.json`

In the iOS app, select Deep Research from the tools menu. Use GPT-5.6 Sol Pro if
the interface permits that model for the task; otherwise use Deep Research's
current default research model. Ensure public-web research is enabled.

Send this message after attaching the files:

> Execute the attached Deep Research task exactly as specified. Research all 25
> records, inspect each cited page, and return the complete findings plus the
> required fenced JSON object. Do not omit records and do not treat search
> snippets or quotation aggregators as evidence.

Download the completed report as Markdown. The report must be independently
verified locally before any source can be accepted into the audit.
"""


def build(project_dir: Path, output_dir: Path) -> None:
    """Build the deterministic ChatGPT research pack in ``output_dir``."""
    queue, source_hashes = _build_queue(project_dir)
    queue_text = json.dumps(queue, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    outputs = {
        "pilot_queue.json": queue_text,
        "source_acceptance_policy.md": SOURCE_POLICY,
        "CHATGPT_DEEP_RESEARCH_PROMPT.md": PROMPT,
        "README.md": README,
    }
    for name, content in outputs.items():
        _atomic_write(output_dir / name, content)

    output_hashes = {name: _sha256(output_dir / name) for name in outputs}
    manifest = {
        "schema_version": PACK_SCHEMA_VERSION,
        "policy_version": PACK_POLICY_VERSION,
        "source_audit_date": queue["source_audit_date"],
        "record_count": queue["record_count"],
        "cohort_counts": queue["cohort_counts"],
        "source_file_hashes": source_hashes,
        "output_file_hashes": output_hashes,
        "production_files_modified": False,
    }
    _atomic_write(
        output_dir / "pilot_manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )


def main() -> int:
    """Parse command-line arguments and build the research pack."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "semantic_alignment_research/historical_context_source_audit_001/"
            "chatgpt_deep_research_pilot_001"
        ),
    )
    args = parser.parse_args()
    project_dir = args.project_dir.resolve()
    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = project_dir / output_dir
    build(project_dir, output_dir)
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
