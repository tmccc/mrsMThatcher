"""Read-only observations of the historical-context research corpus.

The project path, strict JSON parser and file-hash reader are explicit inputs.
Importing this module performs no runtime reads, home lookup or initialisation;
it imports neither the digest, Markdown renderer nor production bot.
"""
from __future__ import annotations

import ast
import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List


def _gate_constants(source: str) -> Dict[str, Any]:
    """Inspect literal gate pins without importing or executing project code."""
    wanted = {
        "EXPECTED_LEDGER_SHA256", "EXPECTED_PROJECTION_SHA256",
        "EXPECTED_BLOCKED_COUNT", "EXPECTED_DISPOSITION_COUNTS", "POLICY_VERSION",
    }
    result = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    if target.id in result:
                        raise ValueError("duplicate semantic gate constant")
                    result[target.id] = ast.literal_eval(node.value)
    if set(result) != wanted:
        raise ValueError("semantic gate constants are missing")
    return result


def _sequence_sha256(values: Any) -> str:
    """Reproduce the gate audit's length-delimited identity hash."""
    return hashlib.sha256(
        "".join(f"{len(value)}:{value}\n" for value in sorted(values)).encode()
    ).hexdigest()


def _validate_snapshot(
    loaded: Dict[str, Dict[str, Any]],
    hashes: Dict[str, str],
    gate_constants: Dict[str, Any],
    *,
    project_dir: Path,
    sha256_file: Callable[[Path], str],
) -> None:
    """Reject stale audit metadata against observed inputs and the gate pins."""
    import json

    packets = loaded["research_packets"]["items"]
    roles = loaded["source_role_audit"]
    runtime = loaded["runtime_eligible_manifest"]
    unresolved = loaded["unresolved_cases"]
    audit = loaded["semantic_gate_audit"]
    ledger = loaded["semantic_review_ledger"]
    manifest = loaded["corpus_manifest"]
    status = loaded["research_status"]
    research = project_dir / "semantic_alignment_research/quote_research_full_001"

    def require(condition: bool, reason: str) -> None:
        """Fail with a useful diagnostic instead of advertising a stale snapshot."""
        if not condition:
            raise ValueError(reason)

    def count(value: Any, expected: int, name: str) -> None:
        """Require an actual integer count; booleans are not accepted as counts."""
        require(type(value) is int and value == expected, f"{name} count differs")

    def identities(values: Any, name: str) -> set[str]:
        """Require a sequence of unique nonempty quote identities."""
        require(isinstance(values, list), f"{name} identities invalid")
        require(all(isinstance(value, str) and value for value in values), f"{name} identities invalid")
        require(len(values) == len(set(values)), f"{name} duplicate identities")
        return set(values)

    require(isinstance(packets, dict) and bool(packets), "packet mapping invalid")
    role_items = roles["items"]
    require(isinstance(role_items, dict) and set(role_items) == set(packets), "source-role packet identities differ")
    eligible_ids = set()
    for quote_id, packet in packets.items():
        require(packet["quote_id"] == quote_id, "packet identity differs")
        speaker = re.sub(r"\s+", " ", str(packet.get("speaker") or "").strip())
        principal = re.split(r"\s*(?:\(|/)\s*", speaker, maxsplit=1)[0].strip().casefold()
        eligible = principal == "margaret thatcher" and str(packet.get("verification_status") or "").strip().casefold() != "misattributed"
        require(role_items[quote_id]["attribution_eligible"] is eligible, "source-role attribution differs")
        if eligible:
            eligible_ids.add(quote_id)
    unresolved_ids = identities([row["quote_id"] for row in unresolved["cases"]], "unresolved")
    manifest_ids = identities([row["quote_id"] for row in manifest["records"]], "manifest")
    require(set(packets).isdisjoint(unresolved_ids) and set(packets) | unresolved_ids == manifest_ids, "corpus partition differs")
    count(manifest["record_count"], len(manifest_ids), "manifest")
    manifest_content = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    manifest_hash = hashlib.sha256(json.dumps(
        manifest_content, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    require(manifest.get("manifest_sha256") == manifest_hash, "manifest content hash differs")
    count(unresolved["case_count"], len(unresolved_ids), "unresolved")
    require(status["corpus_hash"] == hashes["research_packets"], "research status corpus hash differs")
    count(status["completed_quotes"], len(packets), "research status completed")
    count(status["unresolved_quotes"], len(unresolved_ids), "research status unresolved")
    count(status["total_manifest_quotes"], len(manifest_ids), "research status total")
    count(roles["packet_count"], len(packets), "source-role packet")
    count(roles["attribution_eligible_quote_count"], len(eligible_ids), "source-role eligible")
    eligible_hash = hashlib.sha256(("\n".join(sorted(eligible_ids)) + "\n").encode()).hexdigest()
    require(roles["attribution_eligible_quote_ids_sha256"] == eligible_hash, "source-role eligible identity hash differs")
    count(roles["unresolved_quote_count"], len(unresolved_ids), "source-role unresolved")
    require(identities(roles["unresolved_quote_ids"], "source-role unresolved") == unresolved_ids, "source-role unresolved identities differ")

    runtime_ids = identities(runtime["runtime_eligible_quote_ids"], "runtime")
    resolved_ids = identities(runtime["resolved_manifest_quote_ids"], "resolved runtime")
    aliases = runtime["runtime_quote_aliases"]
    require(isinstance(aliases, dict) and set(aliases) <= runtime_ids, "runtime aliases invalid")
    require({aliases.get(value, value) for value in runtime_ids} == resolved_ids == eligible_ids, "runtime attribution partition differs")
    count(runtime["runtime_eligible_quote_count"], len(eligible_ids), "runtime eligible")
    require(len(runtime_ids) == len(eligible_ids), "runtime identity count differs")

    audit_inputs = {
        "research_packets.json": "research_packets",
        "runtime_eligible_quote_manifest.json": "runtime_eligible_manifest",
        "historical_context_source_role_audit.json": "source_role_audit",
        "historical_context_published_reply_semantic_review.json": "semantic_review_ledger",
        "mrsMThatcher.txt": "active_source",
        "historical_context_reply_semantic_gate.py": "semantic_gate",
        "corpus_manifest.json": "corpus_manifest",
        "final_unresolved/final_research_status.json": "research_status",
        "final_unresolved/unresolved_cases.json": "unresolved_cases",
    }
    require(set(audit["input_hashes"]) == set(audit_inputs), "semantic audit input set differs")
    for name, label in audit_inputs.items():
        require(audit["input_hashes"][name] == hashes[label], f"semantic audit input hash differs: {name}")
    for name, expected in roles["source_file_hashes"].items():
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts, "unsafe source-role input path")
        path = research / relative
        require(path.resolve().is_relative_to(research.resolve()), "unsafe source-role input path")
        label = audit_inputs.get(name)
        actual = hashes[label] if label else sha256_file(path)
        require(expected == actual, f"source-role input hash differs: {name}")
    require(roles["source_file_hashes"].get("research_packets.json") == hashes["research_packets"], "source-role packet hash missing")
    require(runtime["source_file_hashes"]["active_source"] == hashes["active_source"], "runtime active-source hash differs")
    require(runtime["source_file_hashes"]["completed_quote_research"] == hashes["research_packets"], "runtime packet hash differs")
    require(ledger["input_evidence"]["source_role_audit"]["sha256"] == hashes["source_role_audit"], "ledger source-role hash differs")

    gate = audit["gate"]
    require(gate["available"] is True and not gate["failure_reason"], "audited semantic gate unavailable")
    require(gate["semantic_review_ledger_sha256"] == hashes["semantic_review_ledger"] == gate_constants["EXPECTED_LEDGER_SHA256"], "semantic ledger pin differs")
    require(audit["policy_version"] == gate_constants["POLICY_VERSION"], "semantic gate policy differs")
    open_records = [row for row in ledger["records"] if row["follow_up_status"] == "remains_open"]
    blocked_ids = identities([row["quote_id"] for row in open_records], "blocked review")
    blocked = {row["quote_id"]: row["disposition"] for row in open_records}
    require(blocked_ids <= eligible_ids, "blocked quote is attribution-ineligible")
    require({row["quote_id"]: row["disposition"] for row in ledger["remaining_items"]} == blocked, "ledger blocked projection differs")
    projection = hashlib.sha256("".join(f"{quote_id}\t{blocked[quote_id]}\n" for quote_id in sorted(blocked)).encode()).hexdigest()
    require(gate["blocked_projection_sha256"] == projection == gate_constants["EXPECTED_PROJECTION_SHA256"], "semantic projection pin differs")
    counts = dict(Counter(blocked.values()))
    require(gate["disposition_counts"] == counts == gate_constants["EXPECTED_DISPOSITION_COUNTS"], "semantic dispositions differ")
    for name in ("blocked_quote_count", "reviewed_blocked_quote_count"):
        count(gate[name], len(blocked_ids), name)
    count(gate_constants["EXPECTED_BLOCKED_COUNT"], len(blocked_ids), "gate blocked pin")
    expected_coverage = {
        "completed_packet_count": len(packets),
        "attribution_eligible_count": len(eligible_ids),
        "completed_attribution_ineligible_count": len(packets) - len(eligible_ids),
        "unresolved_quote_count": len(unresolved_ids),
    }
    for name, expected in expected_coverage.items():
        count(audit["coverage"][name], expected, name)
    for name, values in (
        ("regular_post_eligible_quote_ids_sha256", eligible_ids),
        ("runtime_cycle_quote_ids_sha256", runtime_ids),
        ("runtime_cycle_resolved_quote_ids_sha256", resolved_ids),
    ):
        require(audit["coverage"][name] == _sequence_sha256(values), f"{name} differs")
    expected_decisions = {
        "blocked_open_semantic_review": len(blocked_ids),
        "eligible_allow": len(eligible_ids) - len(blocked_ids),
        "ineligible_not_regular_post": len(packets) - len(eligible_ids),
    }
    require(audit["decision_counts"] == {key: value for key, value in expected_decisions.items() if value}, "semantic audit decision counts differ")
    require(all(type(value) is int for value in audit["decision_counts"].values()), "semantic audit decision counts are not integers")
    records = audit["records"]
    require(identities([row["quote_id"] for row in records], "audit record") == set(packets), "semantic audit packet identities differ")
    for row in records:
        quote_id = row["quote_id"]
        decision = "blocked_open_semantic_review" if quote_id in blocked_ids else "eligible_allow" if quote_id in eligible_ids else "ineligible_not_regular_post"
        require(row["attribution_eligible"] is (quote_id in eligible_ids) and row["public_reply_decision"] == decision and row["open_review_disposition"] == blocked.get(quote_id), "semantic audit packet decision differs")
    require(audit["invariant_failure_count"] == 0 and bool(audit["invariants"]) and all(value is True for value in audit["invariants"].values()) and audit["blocked_render_failures"] == [], "semantic audit invariants failed")


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
    research = project_dir / "semantic_alignment_research/quote_research_full_001"
    paths.update({
        "corpus_manifest": research / "corpus_manifest.json",
        "research_status": research / "final_unresolved/final_research_status.json",
    })
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

    gate_constants = {}
    for label, path in {
        "active_source": project_dir / "mrsMThatcher.txt",
        "semantic_gate": project_dir / "historical_context_reply_semantic_gate.py",
    }.items():
        try:
            if label == "semantic_gate":
                gate_constants = _gate_constants(path.read_text(encoding="utf-8"))
            hashes[label] = sha256_file(path)
        except FileNotFoundError:
            missing.append(label)
        except Exception as exc:
            malformed.append(f"{label}:{type(exc).__name__}")
    contradictions = []
    if not missing and not malformed:
        try:
            _validate_snapshot(
                loaded, hashes, gate_constants,
                project_dir=project_dir, sha256_file=sha256_file,
            )
        except Exception as exc:
            contradictions.append(f"inconsistent corpus: {exc}")

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
        "available": not missing and not malformed and not contradictions and all(type(value) is int for value in required_counts),
        "reason": (
            "; ".join(
                ([f"missing: {', '.join(missing)}"] if missing else [])
                + ([f"malformed: {', '.join(malformed)}"] if malformed else [])
                + contradictions
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
