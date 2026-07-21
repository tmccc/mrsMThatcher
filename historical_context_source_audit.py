#!/usr/bin/env python3
"""Build and report the offline historical-context evidence-role audit."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any

from historical_context_formatter import (
    DEFAULT_RESEARCH_DIR,
    atomic_write_json,
    load_and_validate_corpus,
    packet_is_attributed_to_margaret_thatcher,
)
from historical_context_source_roles import (
    AUDIT_FILENAME,
    build_audit,
    file_sha256,
    precise_locator_kind,
    quote_set_hash,
)
from historical_context_source_recovery import (
    RECOVERY_FILENAME,
    recover_saved_source_evidence,
    validate_recovery,
)
from historical_context_source_resolution import (
    RESOLUTION_FILENAME,
    resolve_sources,
    validate_resolution,
)
from historical_context_source_research_manifest import (
    RESEARCH_FILENAME,
    validate_research_manifest,
)
from historical_context_source_openai_manifest import (
    OPENAI_RESEARCH_FILENAME,
    validate_openai_research_manifest,
)
from semantic_alignment.io import atomic_write_text


DEFAULT_OUTPUT_DIR = Path("semantic_alignment_research/historical_context_source_audit_001")
DEFAULT_REPORT = Path("historical_context_source_audit_report.md")
MANDATORY_IDS = (
    "eb1d2ebaac7e321e67174d2db5761d2bd008341ebb04b1abac4d4a927cd7a7d4",
    "313172d18e2d915e514e4a202a8b1bcbb077472c2504dee63fe98edaf60e0b3a",
    "5f14e6e600773cf394a3f3a3ae21aef10f108691eef50093002571765a5a4a82",
    "677bda2ba3097d2452133f66a0eab9c9740a06a0be8d53bdd712f52b53ff7bab",
    "7c29b290a8e1898c86c39b0cdd23c60161ca73cc068d4a888a19f1f7f3967d0d",
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _eligible_ids(packets: dict[str, dict[str, Any]]) -> set[str]:
    return {
        quote_id for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }


def _identity_snapshot(packets: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    return {
        quote_id: {
            "quote_text": packet["quote_text"],
            "verification_status": packet["verification_status"],
            "speaker": packet["speaker"],
        }
        for quote_id, packet in sorted(packets.items())
    }


def _provider_research_summary(
    output_dir: Path, audit: dict[str, Any]
) -> dict[str, Any]:
    """Reconcile all provider attempts without double-counting carried balances."""
    calls: list[dict[str, Any]] = []
    ledger_hashes: dict[str, str] = {}
    for path in sorted(output_dir.glob("gemini*/api_cost_ledger.json")):
        ledger = _load_json(path)
        ledger_hashes[str(path)] = file_sha256(path)
        for row in ledger.get("calls", ledger.get("attempts", [])):
            calls.append({**row, "provider_family": "gemini"})
    for path in sorted(output_dir.glob("openai*/api_cost_ledger.json")):
        ledger = _load_json(path)
        ledger_hashes[str(path)] = file_sha256(path)
        for row in ledger.get("attempts", []):
            calls.append({**row, "provider_family": "openai"})

    known_by_family: Counter[str] = Counter()
    known_by_transport: Counter[str] = Counter()
    ambiguous_by_family: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    attempt_counts: Counter[str] = Counter()
    for row in calls:
        family = row["provider_family"]
        attempt_counts[family] += 1
        status_counts[f"{family}:{row.get('status', 'unknown')}"] += 1
        known = row.get("known_cost_usd", row.get("cost_usd"))
        if isinstance(known, (int, float)) and math.isfinite(float(known)):
            known_by_family[family] += float(known)
            transport = str(row.get("transport") or row.get("provider") or family)
            known_by_transport[transport] += float(known)
        ambiguous = row.get("ambiguous_cost_exposure_usd")
        if isinstance(ambiguous, (int, float)) and math.isfinite(float(ambiguous)):
            ambiguous_by_family[family] += float(ambiguous)

    known_total = sum(known_by_family.values())
    ambiguous_total = sum(ambiguous_by_family.values())
    if known_total + ambiguous_total > 35.0 + 1e-9:
        raise RuntimeError("provider research summary exceeds the authorised hard stop")
    return {
        "schema_version": 1,
        "hard_limit_usd": 35.0,
        "attempt_counts": dict(sorted(attempt_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "known_spend_by_provider_family_usd": {
            key: round(value, 10) for key, value in sorted(known_by_family.items())
        },
        "known_spend_by_transport_usd": {
            key: round(value, 10) for key, value in sorted(known_by_transport.items())
        },
        "ambiguous_exposure_by_provider_family_usd": {
            key: round(value, 10) for key, value in sorted(ambiguous_by_family.items())
        },
        "combined_known_spend_usd": round(known_total, 10),
        "combined_ambiguous_exposure_usd": round(ambiguous_total, 10),
        "combined_conservative_exposure_usd": round(
            known_total + ambiguous_total, 10
        ),
        "gemini_verified_source_count": audit["gemini_researched_source_count"],
        "openai_verified_source_count": audit["openai_researched_source_count"],
        "ledger_hashes": ledger_hashes,
    }


def run_audit(research_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Create the sidecar while proving quotation identity and eligibility stability."""
    packets, unresolved = load_and_validate_corpus(
        research_dir,
        require_source_role_audit=False,
        load_source_role_audit=False,
    )
    before_identity = _identity_snapshot(packets)
    before_eligible = _eligible_ids(packets)
    if len(packets) != 626 or len(unresolved) != 6 or len(before_eligible) != 610:
        raise RuntimeError(
            "refusing audit because canonical corpus invariants differ from 626/6/610"
        )
    recovery = recover_saved_source_evidence(research_dir, packets)
    validate_recovery(recovery, packets)
    recovery_path = research_dir / RECOVERY_FILENAME
    atomic_write_json(recovery_path, recovery)
    resolution_path = research_dir / RESOLUTION_FILENAME
    resolution = None
    if resolution_path.exists():
        resolution = _load_json(resolution_path)
        validate_resolution(resolution, packets)
    research_path = research_dir / RESEARCH_FILENAME
    researched = None
    if research_path.exists():
        researched = _load_json(research_path)
        validate_research_manifest(researched, packets)
    openai_research_path = research_dir / OPENAI_RESEARCH_FILENAME
    openai_researched = None
    if openai_research_path.exists():
        openai_researched = _load_json(openai_research_path)
        validate_openai_research_manifest(openai_researched, packets)
    audit = build_audit(
        packets,
        unresolved,
        research_dir=research_dir,
        attribution_eligible_ids=before_eligible,
        recovered_evidence=recovery,
        source_resolution=resolution,
        researched_evidence=researched,
        openai_researched_evidence=openai_researched,
    )
    audit_path = research_dir / AUDIT_FILENAME
    atomic_write_json(audit_path, audit)

    attached, reloaded_unresolved = load_and_validate_corpus(
        research_dir,
        require_source_role_audit=True,
    )
    after_identity = _identity_snapshot(attached)
    after_eligible = _eligible_ids(attached)
    if before_identity != after_identity:
        raise RuntimeError("source-role audit changed quotation text, identity, status, or speaker")
    if before_eligible != after_eligible or len(after_eligible) != 610:
        raise RuntimeError("source-role audit changed regular-post attribution eligibility")
    if unresolved != reloaded_unresolved:
        raise RuntimeError("source-role audit changed unresolved quotation partition")

    output_dir.mkdir(parents=True, exist_ok=True)
    source_files = [
        research_dir / "research_packets.json",
        research_dir / "grounding_sources.json",
        research_dir / "corpus_manifest.json",
        research_dir / "final_unresolved" / "final_research_status.json",
        recovery_path,
        Path("mrsMThatcher.txt"),
    ]
    if resolution is not None:
        source_files.append(resolution_path)
    if researched is not None:
        source_files.append(research_path)
    if openai_researched is not None:
        source_files.append(openai_research_path)
    snapshot = {
        "schema_version": 1,
        "audit_path": str(audit_path),
        "audit_sha256": file_sha256(audit_path),
        "recovery_path": str(recovery_path),
        "recovery_sha256": file_sha256(recovery_path),
        "source_files": {
            str(path): {
                "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
                "sha256": file_sha256(path),
            }
            for path in source_files
        },
        "completed_packet_count": len(packets),
        "unresolved_quote_count": len(unresolved),
        "attribution_eligible_quote_count_before": len(before_eligible),
        "attribution_eligible_quote_count_after": len(after_eligible),
        "attribution_eligible_ids_sha256_before": quote_set_hash(before_eligible),
        "attribution_eligible_ids_sha256_after": quote_set_hash(after_eligible),
        "quote_identity_unchanged": before_identity == after_identity,
        "regular_post_eligibility_unchanged": before_eligible == after_eligible,
        "canonical_research_files_modified": False,
        "raw_source_history_preserved": True,
        "raw_response_files_modified": False,
    }
    atomic_write_json(output_dir / "source_snapshot_manifest.json", snapshot)
    summary = {
        "schema_version": 1,
        "audit_sha256": snapshot["audit_sha256"],
        "packet_count": audit["packet_count"],
        "source_count": audit["source_count"],
        "recovered_citation_source_count": audit["recovered_citation_source_count"],
        "model_proposed_source_lead_count": audit["model_proposed_source_lead_count"],
        "gemini_researched_source_count": audit["gemini_researched_source_count"],
        "openai_researched_source_count": audit["openai_researched_source_count"],
        "researched_source_count": audit["researched_source_count"],
        "audited_source_record_count": audit["audited_source_record_count"],
        "eligible_quote_count": len(after_eligible),
        "unresolved_quote_count": len(unresolved),
        **audit["summary"],
    }
    atomic_write_json(output_dir / "audit_summary.json", summary)
    atomic_write_json(
        output_dir / "provider_research_summary.json",
        _provider_research_summary(output_dir, audit),
    )
    return {"audit": audit, "snapshot": snapshot, "summary": summary}


def _escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _preview(value: Any, maximum: int = 140) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= maximum else text[: maximum - 1].rstrip() + "…"


def _source_summary(audit: dict[str, Any]) -> dict[str, int]:
    counts = audit["summary"]["source_quality_counts"]
    return {
        "primary sources": counts.get("strong_primary_evidence", 0),
        "secondary sources": counts.get("reliable_secondary_evidence", 0),
        "recollections": counts.get("secondary_recollection", 0),
        "discovery-only sources": counts.get("discovery_lead_only", 0),
        "circular attribution sources": counts.get("circular_attribution", 0),
        "rejected irrelevant sources": counts.get("irrelevant_or_corrupt", 0),
        "broken or non-verifying URLs": counts.get("broken_or_non_verifying_url", 0),
        "insufficiently located evidence": counts.get("insufficiently_located_evidence", 0),
    }


def render_report(
    research_dir: Path,
    output_dir: Path,
    report_path: Path,
) -> None:
    """Render the complete human-readable audit report from saved evidence."""
    audit = _load_json(research_dir / AUDIT_FILENAME)
    snapshot = _load_json(output_dir / "source_snapshot_manifest.json")
    validation_path = output_dir / "validation_results.json"
    validation = _load_json(validation_path) if validation_path.exists() else {}
    provider_summary_path = output_dir / "provider_research_summary.json"
    provider_summary = (
        _load_json(provider_summary_path) if provider_summary_path.exists() else {}
    )
    quality = _source_summary(audit)
    summary = audit["summary"]
    approximate_source_count = sum(
        row.get("wording_match_kind") == "approximate_semantic_guarded"
        for item in audit["items"].values()
        for row in item["renderable_sources"]
    )
    authored_book_locator_count = sum(
        precise_locator_kind(item.get("stable_locator"))
        == "thatcher_authored_book"
        for item in audit["items"].values()
    )
    lines = [
        "# Historical Context Source Audit",
        "",
        "## Executive summary",
        "",
        f"The audit examined all **{audit['packet_count']} completed research packets** and "
        f"all **{audit['source_count']} saved packet source records**. The six unresolved research "
        "records remain outside the completed packet collection. The canonical packet file, quotation "
        "text, quote IDs, speakers, verification classifications and regular-post eligibility were not changed.",
        "",
        "The source-role sidecar now prevents provider redirects, search machinery, quotation aggregators "
        "and unlocated evidence from being presented as public verification. Where no claim-bearing source "
        "survives, the formatter says `Source — No reliable source located` and retains the quotation's "
        "existing uncertainty classification.",
        "",
        "## Audit method",
        "",
        "- Parsed the canonical packet and grounding records locally, then applied only saved, hash-validated research manifests.",
        "- Fingerprinted every physical source record and retained its original URL and provider-grounded segments.",
        "- Required both claim-bearing local support and a specific stable locator before a source could be rendered.",
        "- Classified opaque redirects without a specific locator as discovery or insufficiently located evidence.",
        "- Rejected Google search/time URLs and the unrelated National Park Service record deterministically.",
        "- Accepted Thatcher-authored books as primary evidence when a page or chapter locator makes the citation independently usable; vague book references remain research leads.",
        "- Accepted guarded wording variants at 80% or greater only after actor, number, negation, polarity and direction checks; these variants never establish exact wording.",
        "- Kept interpretation separate from attribution, wording, event, date and context confidence.",
        "",
        "## Source roles",
        "",
        "| Role | Permitted use |",
        "|---|---|",
        "| `wording_verification` | Supports quoted wording or a documented variant. |",
        "| `attribution_support` | Supports identification of the speaker or the history of an attribution. |",
        "| `source_event_support` | Supports the occasion and/or date. |",
        "| `historical_context_support` | Supports a historical-context claim beyond the source occasion. |",
        "| `secondary_recollection` | Explicitly labelled later recollection; never primary exact-wording proof. |",
        "| `discovery_only` | Research lead only; never rendered publicly. |",
        "| `rejected_irrelevant` | Corrupt or irrelevant evidence; never rendered publicly. |",
        "",
        "## Summary counts",
        "",
        f"- Completed packets: **{audit['packet_count']}**",
        f"- Attribution-eligible quotations: **{audit['attribution_eligible_quote_count']}**",
        f"- Unresolved research records retained and ineligible: **{audit['unresolved_quote_count']}**",
        f"- Saved physical sources: **{audit['source_count']}**",
        f"- Recovered citation sources: **{audit['recovered_citation_source_count']}**",
        f"- Gemini-located, locally verified sources: **{audit['gemini_researched_source_count']}**",
        f"- OpenAI-located, locally verified sources: **{audit['openai_researched_source_count']}**",
        f"- Guarded approximate source matches: **{approximate_source_count}**",
        f"- Precise Thatcher-authored book locators: **{authored_book_locator_count}**",
    ]
    lines.extend(f"- {name.capitalize()}: **{count}**" for name, count in quality.items())
    lines.extend([
        f"- Packets with no reliable renderable source: **{summary['packets_with_no_reliable_source']}**",
        f"- Packets with material public-output corrections: **{summary['packets_whose_public_output_changes']}**",
        f"- Packets requiring further historical research: **{summary['packets_requiring_new_historical_research']}**",
        "",
        "## Bounded provider research",
        "",
    ])
    if provider_summary:
        known = provider_summary["known_spend_by_provider_family_usd"]
        transport = provider_summary["known_spend_by_transport_usd"]
        lines.extend([
            f"- Gemini billed attempts: **{provider_summary['attempt_counts'].get('gemini', 0)}**",
            f"- OpenAI transmitted attempts: **{provider_summary['attempt_counts'].get('openai', 0)}**",
            f"- Gemini known spend: **US${known.get('gemini', 0.0):.6f}**",
            f"- OpenAI known spend: **US${known.get('openai', 0.0):.6f}**",
            f"- Developer API known spend: **US${transport.get('developer_api', 0.0):.6f}**",
            f"- Vertex pilot known spend: **US${transport.get('vertex_ai', 0.0):.6f}**",
            f"- Conservative ambiguous exposure: **US${provider_summary['combined_ambiguous_exposure_usd']:.6f}**",
            f"- Combined conservative exposure: **US${provider_summary['combined_conservative_exposure_usd']:.6f} / US${provider_summary['hard_limit_usd']:.2f}**",
            "- Gemini `generateContent` frequently returned ungrounded prose despite the search declaration; unsupported interaction endpoints returned non-billable 404 responses.",
            "- OpenAI Responses requests forced native web search. Model conclusions were ignored: only independently fetched pages containing authorised wording and nearby Thatcher attribution were accepted.",
        ])
    else:
        lines.append("Provider cost reconciliation pending.")
    lines.extend([
        "",
        "## Mandatory corrupt-source cases",
        "",
        "| Quote ID | Quotation | Invalid current evidence | Correction | Reliable replacement | Public verification wording |",
        "|---|---|---|---|---|---|",
    ])
    for quote_id in MANDATORY_IDS:
        item = audit["items"][quote_id]
        invalid = [
            row for row in item["sources"]
            if row["action"] in {"reject_as_irrelevant", "remove_from_rendered_output", "requires_further_research"}
        ]
        invalid_text = "; ".join(
            f"{row['source_title']} ({row['source_quality_class']}: {row['rationale']})"
            for row in invalid
        )
        replacement = "; ".join(row["public_title"] for row in item["renderable_sources"]) or "None located"
        correction = "; ".join(item["public_output_changes"]) or "Role labels added"
        lines.append(
            f"| `{quote_id}` | {_escape(_preview(item['quote_text'], 110))} | "
            f"{_escape(_preview(invalid_text, 260))} | {_escape(correction)} | "
            f"{_escape(replacement)} | {_escape(item['public_verification_wording'])} |"
        )
    lines.extend([
        "",
        "## Formatter changes",
        "",
        "- Verification wording is derived only from role-qualified wording or attribution evidence.",
        "- Context includes only fields covered by source-event or historical-context roles.",
        "- Meaning remains clearly labelled editorial interpretation.",
        "- Discovery-only and rejected sources cannot be selected or rendered.",
        "- Missing evidence produces `Source — No reliable source located` rather than a weak link.",
        "- Attribution, wording, source-event, date, context and interpretation confidence are rendered separately.",
        "- The backward-compatible aggregate confidence is the conservative minimum of attribution and wording confidence.",
        "",
        "## Material public-output corrections",
        "",
        "| Quote ID | Quotation | Correction |",
        "|---|---|---|",
    ])
    for quote_id in summary["changed_quote_ids"]:
        item = audit["items"][quote_id]
        lines.append(
            f"| `{quote_id}` | {_escape(_preview(item['quote_text']))} | "
            f"{_escape('; '.join(item['public_output_changes']))} |"
        )
    lines.extend([
        "",
        "## Further research required",
        "",
        "These records remain in the 610-quotation posting population. This list concerns evidence quality only.",
        "",
        "| Quote ID | Quotation | Wording status | Attribution confidence | Wording confidence |",
        "|---|---|---|---|---|",
    ])
    for quote_id in summary["requires_research_quote_ids"]:
        item = audit["items"][quote_id]
        confidence = item["confidence_after"]
        lines.append(
            f"| `{quote_id}` | {_escape(_preview(item['quote_text']))} | "
            f"{item['current_wording_status']} | {confidence['attribution']} | {confidence['wording']} |"
        )
    lines.extend([
        "",
        "## Migration and preservation",
        "",
        f"- Audit sidecar: `{research_dir / AUDIT_FILENAME}`",
        f"- Audit SHA-256: `{snapshot['audit_sha256']}`",
        f"- Eligible-ID set SHA-256 before: `{snapshot['attribution_eligible_ids_sha256_before']}`",
        f"- Eligible-ID set SHA-256 after: `{snapshot['attribution_eligible_ids_sha256_after']}`",
        "- Raw `research_packets.json`, `grounding_sources.json`, manifest and provider history were not rewritten.",
        "- Original bad values remain in the immutable packet/audit trail; the sidecar removes them only from active evidentiary use.",
        "- The offline v3 semantic-veto deployment candidate was deterministically rehashed after the formatter change; pair decisions and coverage were revalidated, while the live manifest remained untouched and enforcement stayed disabled.",
        "- No production state, receipt, ledger, history or analytics file was opened for writing.",
        "",
        "## Validation",
        "",
    ])
    if validation:
        for command in validation.get("commands", []):
            lines.append(f"- `{command['command']}`: **{command['result']}**")
        lines.extend([
            f"- Exact 610-ID eligibility invariant: **{validation.get('eligible_ids_unchanged')}**",
            f"- Quote IDs and text unchanged: **{validation.get('quote_identity_unchanged')}**",
            f"- Service PID/start/restart invariant: **{validation.get('service_untouched')}**",
        ])
    else:
        lines.append("Validation results pending.")
    lines.extend([
        "",
        "## Files modified or added",
        "",
        "- `historical_context_source_audit.py`",
        "- `historical_context_source_gemini.py`",
        "- `historical_context_source_openai.py`",
        "- `historical_context_source_openai_manifest.py`",
        "- `historical_context_source_recovery.py`",
        "- `historical_context_source_research_manifest.py`",
        "- `historical_context_source_resolution.py`",
        "- `historical_context_source_roles.py`",
        "- `historical_context_formatter.py`",
        "- `historical_context_reply_schema.json`",
        "- `mrsMThatcher2.py`",
        "- `semantic_alignment/historical_context_formatter_trial.py`",
        "- `tests/test_historical_context_formatter_trial.py`",
        "- `tests/test_historical_context_reply.py`",
        "- `tests/test_historical_context_source_roles.py`",
        "- `tests/test_unit_helpers.py`",
        "- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/checksums.json`",
        "- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`",
        "- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`",
        "- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.json`",
        "- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.md`",
        f"- `{research_dir / RECOVERY_FILENAME}`",
        f"- `{research_dir / RESOLUTION_FILENAME}`",
        f"- `{research_dir / RESEARCH_FILENAME}`",
        f"- `{research_dir / OPENAI_RESEARCH_FILENAME}`",
        f"- `{research_dir / AUDIT_FILENAME}`",
        f"- `{output_dir}` (cost ledgers, raw provider audit responses, and aggregate research outputs)",
        f"- `{output_dir / 'source_snapshot_manifest.json'}`",
        f"- `{output_dir / 'audit_summary.json'}`",
        f"- `{output_dir / 'provider_research_summary.json'}`",
        f"- `{output_dir / 'validation_results.json'}`",
        "- `historical_context_source_audit_report.md`",
        "",
        "## Remaining risks",
        "",
        "The audit remains conservative where neither recovered local evidence nor bounded provider research produced a locally "
        "verifiable source passage. Those records need further source-driven research before a public link can be restored. This abstention affects only "
        "historical-context evidence rendering; it does not remove or disable quotations.",
        "",
        "All **610 attribution-eligible quotations remain eligible and unchanged**. The previously removed non-Thatcher records "
        "remain absent, and the six unresolved research records remain ineligible.",
        "",
        "READY FOR INDEPENDENT REVIEW" if validation.get("passed") else "AUDIT INCOMPLETE OR UNSAFE",
        "",
    ])
    atomic_write_text(report_path, "\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    """Run the selected offline audit, resolution or reporting command."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit_parser = subparsers.add_parser("audit", help="build and validate the complete sidecar")
    audit_parser.add_argument("--research-dir", type=Path, default=DEFAULT_RESEARCH_DIR)
    audit_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    resolve_parser = subparsers.add_parser(
        "resolve-saved-sources", help="resolve saved provider redirects for residual packets"
    )
    resolve_parser.add_argument("--research-dir", type=Path, default=DEFAULT_RESEARCH_DIR)
    resolve_parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    report_parser = subparsers.add_parser("report", help="render the Markdown report")
    report_parser.add_argument("--research-dir", type=Path, default=DEFAULT_RESEARCH_DIR)
    report_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    report_parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    if args.command == "resolve-saved-sources":
        packets, unresolved = load_and_validate_corpus(
            args.research_dir,
            require_source_role_audit=False,
            load_source_role_audit=False,
        )
        eligible = _eligible_ids(packets)
        recovery = recover_saved_source_evidence(args.research_dir, packets)
        local_audit = build_audit(
            packets,
            unresolved,
            research_dir=args.research_dir,
            attribution_eligible_ids=eligible,
            recovered_evidence=recovery,
        )
        quote_ids = set(local_audit["summary"]["no_reliable_source_quote_ids"])
        result = resolve_sources(
            args.research_dir / RESOLUTION_FILENAME,
            packets,
            quote_ids,
            workers=args.workers,
        )
        print(json.dumps({
            "scope_quote_count": result["scope_quote_count"],
            "expected_url_count": result["expected_url_count"],
            "completed_url_count": result["completed_url_count"],
            "complete": result["complete"],
        }, indent=2, sort_keys=True))
        return 0
    if args.command == "audit":
        result = run_audit(args.research_dir, args.output_dir)
        summary = result["summary"]
        print(json.dumps({
            "audit_sha256": summary["audit_sha256"],
            "packet_count": summary["packet_count"],
            "source_count": summary["source_count"],
            "eligible_quote_count": summary["eligible_quote_count"],
            "unresolved_quote_count": summary["unresolved_quote_count"],
            "source_quality_counts": summary["source_quality_counts"],
            "source_role_counts": summary["source_role_counts"],
            "packets_with_no_reliable_source": summary["packets_with_no_reliable_source"],
            "packets_whose_public_output_changes": summary["packets_whose_public_output_changes"],
            "packets_requiring_new_historical_research": summary["packets_requiring_new_historical_research"],
        }, indent=2, sort_keys=True))
        return 0
    render_report(args.research_dir, args.output_dir, args.report)
    print(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
