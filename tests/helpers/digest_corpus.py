"""Small internally coherent historical-corpus files for digest snapshot tests."""

from __future__ import annotations

import hashlib
import json
import re


def write_corpus(tmp_path):
    """Write a two-packet corpus with real hashes and one reviewed blocked ID."""
    research = tmp_path / "semantic_alignment_research/quote_research_full_001"
    paths = {
        "packets": research / "research_packets.json",
        "unresolved": research / "final_unresolved/unresolved_cases.json",
        "eligible": tmp_path / "semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json",
        "roles": research / "historical_context_source_role_audit.json",
        "gate": tmp_path / "historical_context_reply_semantic_gate_audit.json",
        "ledger": tmp_path / "historical_context_published_reply_semantic_review.json",
        "manifest": research / "corpus_manifest.json",
        "status": research / "final_unresolved/final_research_status.json",
        "source": tmp_path / "mrsMThatcher.txt",
        "gate_code": tmp_path / "historical_context_reply_semantic_gate.py",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(label, value):
        """Write deterministic synthetic JSON."""
        paths[label].write_text(json.dumps(value, sort_keys=True))

    def sha(label):
        """Hash the actual bytes retained by this fixture."""
        return hashlib.sha256(paths[label].read_bytes()).hexdigest()

    def sequence(values):
        """Use the semantic gate audit's identity-hash encoding."""
        return hashlib.sha256("".join(f"{len(value)}:{value}\n" for value in sorted(values)).encode()).hexdigest()

    write("packets", {"items": {
        value: {"quote_id": value, "speaker": "Margaret Thatcher", "verification_status": "verified"}
        for value in ("a", "b")
    }})
    paths["source"].write_text("Quote a\nQuote b\n")
    write("unresolved", {"case_count": 1, "cases": [{"quote_id": "c"}]})
    manifest = {"record_count": 3, "records": [{"quote_id": value} for value in ("a", "b", "c")]}
    manifest["manifest_sha256"] = hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    write("manifest", manifest)
    write("status", {"completed_quotes": 2, "unresolved_quotes": 1, "total_manifest_quotes": 3, "corpus_hash": sha("packets")})
    write("eligible", {
        "runtime_eligible_quote_count": 2,
        "runtime_eligible_quote_ids": ["a", "b"],
        "resolved_manifest_quote_ids": ["a", "b"],
        "runtime_quote_aliases": {},
        "source_file_hashes": {"active_source": sha("source"), "completed_quote_research": sha("packets")},
    })
    write("roles", {
        "policy_version": "roles-v9", "packet_count": 2,
        "attribution_eligible_quote_count": 2,
        "attribution_eligible_quote_ids_sha256": hashlib.sha256(b"a\nb\n").hexdigest(),
        "unresolved_quote_count": 1, "unresolved_quote_ids": ["c"],
        "items": {value: {"attribution_eligible": True} for value in ("a", "b")},
        "source_file_hashes": {"research_packets.json": sha("packets"), "final_unresolved/final_research_status.json": sha("status")},
    })
    write("ledger", {
        "input_evidence": {"source_role_audit": {"sha256": sha("roles")}},
        "records": [{"quote_id": "a", "follow_up_status": "remains_open", "disposition": "insufficient_to_assess"}],
        "remaining_items": [{"quote_id": "a", "disposition": "insufficient_to_assess"}],
    })
    projection = hashlib.sha256(b"a\tinsufficient_to_assess\n").hexdigest()
    constants = {
        "EXPECTED_LEDGER_SHA256": sha("ledger"),
        "EXPECTED_PROJECTION_SHA256": projection,
        "EXPECTED_BLOCKED_COUNT": 1,
        "EXPECTED_DISPOSITION_COUNTS": {"insufficient_to_assess": 1},
        "POLICY_VERSION": "gate-v1",
    }
    paths["gate_code"].write_text("\n".join(f"{key} = {value!r}" for key, value in constants.items()) + "\n")
    write("gate", {
        "policy_version": "gate-v1",
        "input_hashes": {name: sha(label) for name, label in {
            "research_packets.json": "packets",
            "runtime_eligible_quote_manifest.json": "eligible",
            "historical_context_source_role_audit.json": "roles",
            "historical_context_published_reply_semantic_review.json": "ledger",
            "mrsMThatcher.txt": "source",
            "historical_context_reply_semantic_gate.py": "gate_code",
            "corpus_manifest.json": "manifest",
            "final_unresolved/final_research_status.json": "status",
            "final_unresolved/unresolved_cases.json": "unresolved",
        }.items()},
        "coverage": {
            "completed_packet_count": 2,
            "attribution_eligible_count": 2,
            "completed_attribution_ineligible_count": 0,
            "unresolved_quote_count": 1,
            "regular_post_eligible_quote_ids_sha256": sequence(["a", "b"]),
            "runtime_cycle_quote_ids_sha256": sequence(["a", "b"]),
            "runtime_cycle_resolved_quote_ids_sha256": sequence(["a", "b"]),
        },
        "decision_counts": {"eligible_allow": 1, "blocked_open_semantic_review": 1},
        "gate": {
            "available": True, "failure_reason": "",
            "blocked_quote_count": 1, "reviewed_blocked_quote_count": 1,
            "semantic_review_ledger_sha256": sha("ledger"),
            "blocked_projection_sha256": projection,
            "disposition_counts": {"insufficient_to_assess": 1},
        },
        "records": [{
            "quote_id": value, "attribution_eligible": True,
            "public_reply_decision": "blocked_open_semantic_review" if value == "a" else "eligible_allow",
            "open_review_disposition": "insufficient_to_assess" if value == "a" else None,
        } for value in ("a", "b")],
        "invariants": {"test_corpus_matches": True},
        "invariant_failure_count": 0,
        "blocked_render_failures": [],
    })
    return paths


def rebind_corpus_hashes(paths):
    """Refresh external hash bindings after an intentional synthetic contradiction."""
    def read(label):
        """Read one synthetic JSON input."""
        return json.loads(paths[label].read_text())

    def write(label, value):
        """Preserve the deterministic synthetic JSON encoding."""
        paths[label].write_text(json.dumps(value, sort_keys=True))

    def sha(label):
        """Hash the synthetic file's current bytes."""
        return hashlib.sha256(paths[label].read_bytes()).hexdigest()

    by_name = {path.name: label for label, path in paths.items()}
    roles = read("roles")
    roles["source_file_hashes"] = {
        name: sha(by_name[name.rsplit("/", 1)[-1]]) for name in roles["source_file_hashes"]
    }
    write("roles", roles)
    ledger = read("ledger")
    ledger["input_evidence"]["source_role_audit"]["sha256"] = sha("roles")
    write("ledger", ledger)
    paths["gate_code"].write_text(re.sub(
        r"(?m)^EXPECTED_LEDGER_SHA256 = .+$",
        f"EXPECTED_LEDGER_SHA256 = {sha('ledger')!r}", paths["gate_code"].read_text(),
    ))
    audit = read("gate")
    audit["input_hashes"] = {
        name: sha(by_name[name.rsplit("/", 1)[-1]]) for name in audit["input_hashes"]
    }
    audit["gate"]["semantic_review_ledger_sha256"] = sha("ledger")
    write("gate", audit)
