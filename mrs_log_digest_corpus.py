"""Read-only observations of the historical-context research corpus.

The project path, strict JSON parser and file-hash reader are explicit inputs.
Importing this module performs no runtime reads, home lookup or initialisation;
it imports neither the digest, Markdown renderer nor production bot.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List


def historical_context_corpus_snapshot(
    project_dir: Path,
    *,
    parse_json_object: Callable[..., Dict[str, Any]],
    sha256_file: Callable[[Path], str],
) -> Dict[str, Any]:
    """Read current corpus counts, policies and hashes beneath ``project_dir``.

    Reads use the supplied strict object parser and file-hash callback. Parsing
    and hashing intentionally remain separate reads, in that order; this reader
    does not bind them to one filesystem observation or mutate the corpus.
    """
    paths = {
        "research_packets": (
            project_dir
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "research_packets.json"
        ),
        "unresolved_cases": (
            project_dir
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "final_unresolved"
            / "unresolved_cases.json"
        ),
        "runtime_eligible_manifest": (
            project_dir
            / "semantic_alignment_research"
            / "quote_attribution_cleanup_001"
            / "deployment_candidate"
            / "runtime_eligible_quote_manifest.json"
        ),
        "source_role_audit": (
            project_dir
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "historical_context_source_role_audit.json"
        ),
        "semantic_gate_audit": project_dir / "historical_context_reply_semantic_gate_audit.json",
        "semantic_review_ledger": (
            project_dir / "historical_context_published_reply_semantic_review.json"
        ),
    }
    loaded: Dict[str, Dict[str, Any]] = {}
    hashes: Dict[str, str] = {}
    missing: List[str] = []
    malformed: List[str] = []
    for label, path in paths.items():
        try:
            value = parse_json_object(
                path.read_bytes(), label=f"historical corpus {label}"
            )
            loaded[label] = value
            hashes[label] = sha256_file(path)
        except FileNotFoundError:
            missing.append(label)
        except Exception as exc:
            malformed.append(f"{label}:{type(exc).__name__}")

    packets = loaded.get("research_packets", {}).get("items")
    unresolved = loaded.get("unresolved_cases", {})
    eligible = loaded.get("runtime_eligible_manifest", {})
    role_audit = loaded.get("source_role_audit", {})
    gate_audit = loaded.get("semantic_gate_audit", {})
    gate = gate_audit.get("gate") if isinstance(gate_audit.get("gate"), dict) else {}
    coverage = (
        gate_audit.get("coverage")
        if isinstance(gate_audit.get("coverage"), dict)
        else {}
    )
    decision_counts = (
        gate_audit.get("decision_counts")
        if isinstance(gate_audit.get("decision_counts"), dict)
        else {}
    )
    packet_count = len(packets) if isinstance(packets, (dict, list)) else None
    unresolved_count = unresolved.get("case_count")
    if type(unresolved_count) is not int:
        cases = unresolved.get("cases")
        unresolved_count = len(cases) if isinstance(cases, list) else None
    ordinary_count = eligible.get("runtime_eligible_quote_count")
    if type(ordinary_count) is not int:
        ordinary_ids = eligible.get("runtime_eligible_quote_ids")
        ordinary_count = len(ordinary_ids) if isinstance(ordinary_ids, list) else None
    blocked_count = gate.get("blocked_quote_count")
    if type(blocked_count) is not int:
        blocked_count = decision_counts.get("blocked_open_semantic_review")

    required_counts = (packet_count, unresolved_count, ordinary_count, blocked_count)
    return {
        "available": not missing and not malformed and all(type(value) is int for value in required_counts),
        "reason": (
            "; ".join(
                ([f"missing: {', '.join(missing)}"] if missing else [])
                + ([f"malformed: {', '.join(malformed)}"] if malformed else [])
            )
        ),
        "completed_packet_count": packet_count,
        "unresolved_quote_count": unresolved_count,
        "ordinary_post_cycle_count": ordinary_count,
        "attribution_eligible_count": coverage.get(
            "attribution_eligible_count",
            role_audit.get("attribution_eligible_quote_count"),
        ),
        "completed_attribution_ineligible_count": coverage.get(
            "completed_attribution_ineligible_count"
        ),
        "historical_context_blocked_count": blocked_count,
        "historical_context_allowed_count": decision_counts.get("eligible_allow"),
        "source_role_policy_version": role_audit.get("policy_version"),
        "semantic_gate_policy_version": gate_audit.get("policy_version"),
        "semantic_review_ledger_sha256": gate.get("semantic_review_ledger_sha256"),
        "semantic_gate_projection_sha256": gate.get("blocked_projection_sha256"),
        "file_sha256": dict(sorted(hashes.items())),
    }
