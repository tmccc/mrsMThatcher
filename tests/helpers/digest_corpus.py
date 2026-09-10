"""Synthetic historical-corpus files used by digest snapshot tests."""

from __future__ import annotations

import json


def write_corpus(tmp_path):
    paths = {
        "packets": (
            tmp_path
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "research_packets.json"
        ),
        "unresolved": (
            tmp_path
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "final_unresolved"
            / "unresolved_cases.json"
        ),
        "eligible": (
            tmp_path
            / "semantic_alignment_research"
            / "quote_attribution_cleanup_001"
            / "deployment_candidate"
            / "runtime_eligible_quote_manifest.json"
        ),
        "roles": (
            tmp_path
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "historical_context_source_role_audit.json"
        ),
        "gate": tmp_path / "historical_context_reply_semantic_gate_audit.json",
        "ledger": tmp_path / "historical_context_published_reply_semantic_review.json",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    paths["packets"].write_text(json.dumps({"items": [{"id": 1}, {"id": 2}]}))
    paths["unresolved"].write_text(json.dumps({"case_count": 1, "cases": [{}]}))
    paths["eligible"].write_text(
        json.dumps({"runtime_eligible_quote_count": 2, "runtime_eligible_quote_ids": ["a", "b"]})
    )
    paths["roles"].write_text(
        json.dumps({"policy_version": "roles-v9", "attribution_eligible_quote_count": 2})
    )
    paths["gate"].write_text(
        json.dumps(
            {
                "policy_version": "gate-v1",
                "coverage": {
                    "attribution_eligible_count": 2,
                    "completed_attribution_ineligible_count": 0,
                },
                "decision_counts": {
                    "eligible_allow": 1,
                    "blocked_open_semantic_review": 1,
                },
                "gate": {
                    "blocked_quote_count": 1,
                    "semantic_review_ledger_sha256": "a" * 64,
                    "blocked_projection_sha256": "b" * 64,
                },
            }
        )
    )
    paths["ledger"].write_text(json.dumps({"records": []}))
    return paths
