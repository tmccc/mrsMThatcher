"""Regression coverage for the reviewed 422-source MTF context transition."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from historical_context_formatter import (
    load_and_validate_corpus_core,
    packet_is_attributed_to_margaret_thatcher,
    quote_text_hash,
)


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "semantic_alignment_research" / "quote_research_full_001"
PRIORITY_A_IDS = {
    "00a61fc4f76648e2ccbf07fbdadec99afb0000789e85390bae28f11cb3f230ae",
    "01d50c556a2d6283599e8c1eaa04925d42a5b499cc1c5a22925c7cb44097e1ea",
    "21db3d129f143edca731ac38704b1add8ef666662555fbbd3a91bd217d09f6a7",
    "283636fc2526ccd22c302f80f6c494c36ec1e612d16f9b5a145d4eca70b14e9b",
    "9ec3e9ca9ac00dac4619d19fe5312503eeef8fdca7b1b22f2b9fe2866b79bcdc",
    "a9426dce186893768be1d61ea3ca82d90d05667d085c5a3d217e3a08059eba5b",
    "b2af0519a698004f70a0bb37e506a55513af31f07b745bc953728b54078f6a10",
    "cb0389dcbc1d4532742f73271d0e6b26f62d6b3ada40db984d3a0fa7d25a7a1f",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_reviewed_transition_is_exactly_scoped_and_hash_bound():
    decisions = _load(
        ROOT / "historical_context_mtf_live_context_admission_decisions.json"
    )
    audit = _load(
        ROOT / "historical_context_mtf_live_context_admission_audit.json"
    )
    transition_path = (
        ROOT / "historical_context_v9_mtf_live_context_transition_manifest.json"
    )
    transition = _load(transition_path)

    decision_ids = {item["quote_id"] for item in decisions["items"]}
    assert len(decision_ids) == 422
    assert Counter(item["priority"] for item in decisions["items"]) == {
        "A_BLOCKED_REMEDIATION": 8,
        "B_NEW_PRIMARY_BINDING": 258,
        "B_EXISTING_BINDING_UPGRADE": 80,
        "C_CONTEXT_REVIEW": 76,
    }
    assert {
        item["quote_id"]
        for item in decisions["items"]
        if item["priority"] == "A_BLOCKED_REMEDIATION"
    } == PRIORITY_A_IDS
    assert set(audit["bindings"]) == decision_ids
    assert set(transition["items"]) == decision_ids
    assert transition["counts"] == {
        "new_curated_sources": 407,
        "public_field_changes": 422,
        "source_bindings": 422,
        "transition_packets": 422,
        "upgraded_curated_sources": 15,
    }
    assert hashlib.sha256(transition_path.read_bytes()).hexdigest() == (
        "ebcc1e793ec710067084831f84236b7599edf08b5236eca2dd98be8ae4124436"
    )
    assert len(
        audit["baseline"]["v7_public_context_supported_fields"]
    ) == 422


def test_curated_bindings_are_present_without_private_paths():
    audit = _load(
        ROOT / "historical_context_mtf_live_context_admission_audit.json"
    )
    curated = _load(
        RESEARCH / "historical_context_source_curated_evidence.json"
    )
    encoded = json.dumps(curated, sort_keys=True)

    assert "/disks/" not in encoded
    assert audit["private_paths_persisted"] == 0
    for quote_id, binding in audit["bindings"].items():
        matches = [
            source
            for source in curated["items"][quote_id]["sources"]
            if source["source_id"] == binding["source_id"]
        ]
        assert len(matches) == 1
        source = matches[0]
        assert "historical_context_support" in source["assigned_roles"]
        assert "historical_context" in source["claims_supported"]


def test_priority_a_gate_resolution_preserves_historical_and_current_partitions():
    gate = _load(ROOT / "historical_context_reply_semantic_gate_audit.json")
    runtime = _load(
        ROOT
        / "semantic_alignment_research"
        / "quote_attribution_cleanup_001"
        / "deployment_candidate"
        / "runtime_eligible_quote_manifest.json"
    )
    records = {item["quote_id"]: item for item in gate["records"]}

    packets, _unresolved = load_and_validate_corpus_core(RESEARCH)
    eligible_count = sum(packet_is_attributed_to_margaret_thatcher(packet) for packet in packets.values())
    assert gate["gate"]["blocked_quote_count"] == 13
    assert gate["decision_counts"] == {
        "blocked_open_semantic_review": 13,
        "eligible_allow": eligible_count - 13,
        "ineligible_not_regular_post": len(packets) - eligible_count,
    }
    assert all(
        records[quote_id]["public_reply_decision"] == "eligible_allow"
        for quote_id in PRIORITY_A_IDS
    )
    expected_ids = {
        quote_id for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    assert PRIORITY_A_IDS <= expected_ids
    assert set(runtime["resolved_manifest_quote_ids"]) == expected_ids
    assert len(runtime["resolved_manifest_quote_ids"]) == len(expected_ids)
    assert runtime["runtime_eligible_quote_count"] == len(expected_ids)
    assert len(runtime["runtime_eligible_quote_ids"]) == len(expected_ids)
    assert set(runtime["runtime_eligible_quote_ids"]) == {
        quote_text_hash(packets[quote_id]["quote_text"]) for quote_id in expected_ids
    }


def test_semantic_veto_shadow_rebind_preserves_every_matrix_entry():
    audit = _load(
        ROOT / "historical_context_mtf_live_context_shadow_rebind_audit.json"
    )
    manifest = _load(
        ROOT
        / "semantic_alignment_research"
        / "quote_attribution_cleanup_001"
        / "deployment_candidate"
        / "material_veto_v3_shadow_manifest.json"
    )

    assert audit["changed_source_hashes"] == [
        "completed_quote_research",
        "runtime_eligible_quote_manifest",
    ]
    assert audit["deterministic_double_build"] is True
    assert audit["matrix_entries_unchanged"] is True
    assert audit["matrix_before_sha256"] == audit["matrix_after_sha256"]
    assert audit["runtime_available"] is True
    assert manifest["quote_count"] == 611
    assert manifest["image_count"] == 91
    assert manifest["total_authorised_pair_count"] == 55_601
