"""Focused tests for the transport-only source-resolution transition."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import historical_context_transport_url_redaction_transition as transition


SECRET_VALUE = "fixture-signature-value-must-not-survive"
OLD_URL = (
    "https://s3.eu-central-1.amazonaws.com/reviewed/file.pdf?"
    "response-content-disposition=a%20b"
    "&X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-Date=20260721T143430Z"
    "&X-Amz-SignedHeaders=host"
    "&X-Amz-Credential=fixture%2Fscope"
    "&X-Amz-Expires=119"
    f"&X-Amz-Signature={SECRET_VALUE}"
)
NEW_URL = (
    "https://s3.eu-central-1.amazonaws.com/reviewed/file.pdf?"
    "response-content-disposition=a%20b"
)
AUTH_SELECTOR_URL = "https://best-quotations.com/item?auth=Margaret%20Thatcher"
OPENAI_SECRET_VALUE = "openai-fixture-signing-value-must-not-survive"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> dict[str, Any]:
    before_resolution = {
        "complete": True,
        "items": {
            transition.EXPECTED_RESOLUTION_ITEM_ID: {
                "final_url": OLD_URL,
                "original_url": "https://vertex.example/opaque",
                "original_url_sha256": transition.EXPECTED_RESOLUTION_ITEM_ID,
                "quote_ids": [transition.EXPECTED_QUOTE_ID],
                "redirect_chain": [
                    "https://vertex.example/opaque",
                    OLD_URL,
                ],
                "status": "resolved",
                "wording_matches": {
                    transition.EXPECTED_QUOTE_ID: {
                        "coverage": "none",
                        "matched_passage": None,
                    },
                },
            },
            "b" * 64: {
                "final_url": AUTH_SELECTOR_URL,
                "original_url": "https://vertex.example/other",
                "original_url_sha256": "b" * 64,
                "quote_ids": ["c" * 64],
                "redirect_chain": [
                    "https://vertex.example/other",
                    AUTH_SELECTOR_URL,
                ],
                "status": "resolved",
                "wording_matches": {
                    "c" * 64: {
                        "coverage": "none",
                        "matched_passage": None,
                    },
                },
            },
        },
        "policy_version": transition.BEFORE_POLICY,
        "schema_version": 1,
    }
    after_resolution = copy.deepcopy(before_resolution)
    after_resolution["policy_version"] = transition.AFTER_POLICY
    affected = after_resolution["items"][
        transition.EXPECTED_RESOLUTION_ITEM_ID
    ]
    affected["final_url"] = NEW_URL
    affected["redirect_chain"][1] = NEW_URL

    openai_before = {
        "items": {},
        "policy_version": (
            "source-role-openai-research-v1-forced-search-and-fetched-passage"
        ),
        "schema_version": 1,
    }
    for quote_id, index in transition.EXPECTED_OPENAI_RESEARCH_PATHS:
        item = openai_before["items"].setdefault(
            quote_id, {"rejected_sources": []}
        )
        while len(item["rejected_sources"]) <= index:
            item["rejected_sources"].append({
                "reason": "synthetic rejection",
                "source": {
                    "title": "Synthetic source",
                    "url": "https://example.test/ordinary",
                },
            })
        if quote_id.startswith("eca9"):
            suffix = (
                "&utm_source=review"
                if index == 14 else ""
            )
            signed = (
                "https://storage.freidok.ub.uni-freiburg.de/file.pdf?"
                "X-Amz-Algorithm=AWS4-HMAC-SHA256"
                "&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD"
                "&X-Amz-Credential=fixture%2Fscope"
                "&X-Amz-Date=20260721T143430Z"
                "&X-Amz-Expires=300"
                f"&X-Amz-Signature={OPENAI_SECRET_VALUE}"
                "&X-Amz-SignedHeaders=host"
                "&response-content-disposition=a%20b"
                f"{suffix}"
            )
        else:
            signed = (
                "https://s3-euw1-ap-pe-df-pch-content-store-p."
                "s3.eu-west-1.amazonaws.com/file.pdf?"
                "AWSAccessKeyId=fixture"
                "&Expires=1999999999"
                f"&Signature={OPENAI_SECRET_VALUE}"
                "&response-content-disposition=a%20b"
                "&x-amz-security-token=fixture-token"
            )
        item["rejected_sources"][index]["source"]["url"] = signed
    openai_after = copy.deepcopy(openai_before)
    for quote_id, index in transition.EXPECTED_OPENAI_RESEARCH_PATHS:
        source = openai_after["items"][quote_id][
            "rejected_sources"
        ][index]["source"]
        old = source["url"]
        parts = old.split("?")
        retained = [
            pair for pair in parts[1].split("&")
            if not transition._is_signed_query_key(
                transition._query_key(pair)
            )
        ]
        source["url"] = f"{parts[0]}?{'&'.join(retained)}"
    before_openai_path = _write(
        tmp_path / "before" / transition.OPENAI_RESEARCH_FILENAME,
        openai_before,
    )
    after_openai_path = _write(
        tmp_path / "current" / transition.OPENAI_RESEARCH_FILENAME,
        openai_after,
    )

    old_row_hash = hashlib.sha256(_canonical(
        before_resolution["items"][transition.EXPECTED_RESOLUTION_ITEM_ID]
    )).hexdigest()
    new_row_hash = hashlib.sha256(_canonical(
        after_resolution["items"][transition.EXPECTED_RESOLUTION_ITEM_ID]
    )).hexdigest()
    sources = [
        {
            "resolved_redirect_record": False,
            "source_id": f"{index:064x}",
            "source_index": index,
        }
        for index in range(transition.EXPECTED_SOURCE_INDEX)
    ]
    sources.append({
        "action": "retain_as_discovery_lead_only",
        "assigned_roles": [],
        "claims_supported": [],
        "public_title": None,
        "public_url": None,
        "resolution_record_sha256": old_row_hash,
        "resolved_redirect_record": True,
        "resolved_url": OLD_URL,
        "source_id": transition.EXPECTED_SOURCE_ID,
        "source_index": transition.EXPECTED_SOURCE_INDEX,
        "supporting_passages": [],
    })
    before_audit = {
        "items": {
            transition.EXPECTED_QUOTE_ID: {
                "attribution_eligible": True,
                "confidence_after": {"historical_context": "unknown"},
                "date": "unknown",
                "public_context_supported_fields": [],
                "public_output_changes": [],
                "public_verification_wording": "Exact wording not verified",
                "quote_id": transition.EXPECTED_QUOTE_ID,
                "quote_text": "Synthetic fixture quotation.",
                "quote_text_sha256": transition.EXPECTED_QUOTE_ID,
                "renderable_sources": [],
                "requires_further_research": True,
                "source_event": "unknown",
                "sources": sources,
                "speaker": "Margaret Thatcher (attributed)",
                "stable_locator": "unknown",
                "supported_public_roles": [],
                "wording_status_after": "unverified",
            },
        },
        "policy_version": transition.SOURCE_ROLE_POLICY,
        "schema_version": 5,
        "source_file_hashes": {},
    }

    before_resolution_path = _write(
        tmp_path / "before" / transition.RESOLUTION_FILENAME,
        before_resolution,
    )
    after_resolution_path = _write(
        tmp_path / "current" / transition.RESOLUTION_FILENAME,
        after_resolution,
    )
    before_audit["source_file_hashes"][
        transition.RESOLUTION_FILENAME
    ] = _sha(before_resolution_path)
    before_audit["source_file_hashes"][
        transition.OPENAI_RESEARCH_FILENAME
    ] = _sha(before_openai_path)
    before_audit_path = _write(
        tmp_path / "before" / transition.AUDIT_FILENAME,
        before_audit,
    )
    after_audit = copy.deepcopy(before_audit)
    after_audit["source_file_hashes"][
        transition.RESOLUTION_FILENAME
    ] = _sha(after_resolution_path)
    after_audit["source_file_hashes"][
        transition.OPENAI_RESEARCH_FILENAME
    ] = _sha(after_openai_path)
    current_source = after_audit["items"][
        transition.EXPECTED_QUOTE_ID
    ]["sources"][transition.EXPECTED_SOURCE_INDEX]
    current_source["resolved_url"] = NEW_URL
    current_source["resolution_record_sha256"] = new_row_hash
    after_audit_path = _write(
        tmp_path / "current" / transition.AUDIT_FILENAME,
        after_audit,
    )
    predecessor_transition_path = _write(
        tmp_path / transition.PREDECESSOR_TRANSITION_FILENAME,
        {
            "input_hashes": {
                transition.AUDIT_FILENAME: _sha(before_audit_path),
            },
            "items": {transition.EXPECTED_QUOTE_ID: {}},
            "manifest_kind": (
                "historical_context_v9_reviewed_evidence_transition"
            ),
            "schema_version": 1,
        },
    )
    invariant_values = {
        "corpus": {
            "record_count": 1,
            "records": [{
                "quote_id": transition.EXPECTED_QUOTE_ID,
                "quote_text": "Synthetic fixture quotation.",
            }],
        },
        "ordinary_cycle": {
            "runtime_eligible_quote_count": 1,
            "runtime_eligible_quote_ids": [transition.EXPECTED_QUOTE_ID],
        },
        "historical_context_gate": {
            "gate": {
                "blocked_quote_count": 1,
                "semantic_review_ledger_sha256": "a" * 64,
            },
            "input_hashes": {
                "historical_context_published_reply_semantic_review.json":
                    "b" * 64,
                "historical_context_source_role_audit.json": "c" * 64,
                "runtime_eligible_quote_manifest.json": "d" * 64,
            },
            "policy_version": "fixture-gate-v1",
            "records": [{
                "attribution_eligible": True,
                "open_review_disposition": "fixture_open",
                "public_reply_decision": "blocked_open_semantic_review",
                "quote_id": transition.EXPECTED_QUOTE_ID,
                "suppressed_reply": "Synthetic suppressed reply.",
            }],
        },
        "semantic_veto": {
            "mode": "shadow",
            "pair_count": 1,
            "policy_version": "fixture-veto-v1",
        },
        "unresolved": {
            "unresolved_count": 1,
            "unresolved_quote_ids": ["e" * 64],
        },
    }
    invariant_pairs: dict[str, transition.InvariantPair] = {}
    for label, value in invariant_values.items():
        before_value = copy.deepcopy(value)
        after_value = copy.deepcopy(value)
        if label == "historical_context_gate":
            after_value["gate"]["semantic_review_ledger_sha256"] = "1" * 64
            after_value["input_hashes"][
                "historical_context_published_reply_semantic_review.json"
            ] = "2" * 64
            after_value["input_hashes"][
                "historical_context_source_role_audit.json"
            ] = "3" * 64
        repository_path = f"invariants/{label}.json"
        before_path = _write(
            tmp_path / "invariants-before" / f"{label}.json",
            before_value,
        )
        after_path = _write(
            tmp_path / "invariants-current" / f"{label}.json",
            after_value,
        )
        invariant_pairs[label] = transition.InvariantPair(
            before_path,
            after_path,
            repository_path,
        )
    return {
        "before_resolution": before_resolution_path,
        "current_resolution": after_resolution_path,
        "before_openai": before_openai_path,
        "current_openai": after_openai_path,
        "before_audit": before_audit_path,
        "current_audit": after_audit_path,
        "predecessor_transition": predecessor_transition_path,
        "invariant_pairs": invariant_pairs,
    }


def _build(paths: dict[str, Any], **kwargs):
    kwargs.setdefault("unchanged_invariants", paths["invariant_pairs"])
    return transition.build_transition(
        predecessor_resolution=paths["before_resolution"],
        current_resolution=paths["current_resolution"],
        predecessor_openai_research=paths["before_openai"],
        current_openai_research=paths["current_openai"],
        predecessor_source_role_audit=paths["before_audit"],
        current_source_role_audit=paths["current_audit"],
        predecessor_transition=paths["predecessor_transition"],
        **kwargs,
    )


def _rebind_fixture(tmp_path: Path) -> dict[str, Path]:
    paths = _fixture(tmp_path / "inputs")
    root = tmp_path / "root"
    research = root / "research"
    research.mkdir(parents=True)
    for name, source in (
        (transition.RESOLUTION_FILENAME, paths["current_resolution"]),
        (transition.OPENAI_RESEARCH_FILENAME, paths["current_openai"]),
        (transition.AUDIT_FILENAME, paths["current_audit"]),
    ):
        (research / name).write_bytes(source.read_bytes())
    (root / transition.PREDECESSOR_TRANSITION_FILENAME).write_bytes(
        paths["predecessor_transition"].read_bytes()
    )
    for pair in paths["invariant_pairs"].values():
        target = root / pair.repository_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(pair.after.read_bytes())
    manifest_path = _write(
        root / transition.MANIFEST_FILENAME,
        _build(paths),
    )
    truth_path = _write(
        root / transition.EVIDENCE_TRUTH_FILENAME,
        {
            "audit_kind": transition.EVIDENCE_TRUTH_AUDIT_KIND,
            "counts": {"invariant_failure_count": 0},
            "input_hashes": {
                transition.AUDIT_FILENAME: _sha(paths["before_audit"]),
                "historical_context_reply_history.json": "4" * 64,
                "research_packets.json": "5" * 64,
            },
            "invariants": {"published_history_is_immutable": True},
            "records": {
                "published_reply_reviews": [{
                    "parent_post_id": "synthetic-parent",
                    "quote_id": transition.EXPECTED_QUOTE_ID,
                }],
            },
            "schema_version": transition.EVIDENCE_TRUTH_SCHEMA_VERSION,
        },
    )
    return {
        **paths,
        "root": root,
        "research": research,
        "manifest": manifest_path,
        "truth": truth_path,
    }


def _provenance_successor_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Create a small exact analogue of the source-pin-only successor."""
    paths = _fixture(tmp_path / "inputs")
    root = tmp_path / "root"
    research = root / "research"
    research.mkdir(parents=True)
    for name, source in (
        (transition.RESOLUTION_FILENAME, paths["current_resolution"]),
        (transition.OPENAI_RESEARCH_FILENAME, paths["current_openai"]),
        (transition.AUDIT_FILENAME, paths["current_audit"]),
    ):
        (research / name).write_bytes(source.read_bytes())
    (root / transition.PREDECESSOR_TRANSITION_FILENAME).write_bytes(
        paths["predecessor_transition"].read_bytes()
    )
    for pair in paths["invariant_pairs"].values():
        target = root / pair.repository_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(pair.after.read_bytes())

    predicate_path = "fixture_formatter.py"
    predicate = root / predicate_path
    predicate_body = (
        '_MARGARET_THATCHER_CANONICAL_SPEAKER = "margaret thatcher"\n'
        'THATCHER_ATTRIBUTION_RULE_VERSION = "fixture-rule-v1"\n'
        "\n"
        "def packet_is_attributed_to_margaret_thatcher(packet):\n"
        "    return bool(packet)\n"
    )
    historical_predicate_bytes = (
        "# historical formatter bytes\n" + predicate_body
    ).encode("utf-8")
    predicate.write_text(
        "# successor receipt-only bytes\n" + predicate_body,
        encoding="utf-8",
    )
    successor_pin = _sha(predicate)
    historical_pin = hashlib.sha256(historical_predicate_bytes).hexdigest()
    runtime_eligibility_path = "fixture_runtime_eligibility.json"
    historical_runtime_eligibility_pin = "4" * 64
    successor_runtime_eligibility_pin = "5" * 64
    semantic_pair = paths["invariant_pairs"]["semantic_veto"]
    historical_value = json.loads(semantic_pair.before.read_text())
    historical_value["source_file_hashes"] = {
        "attribution_predicate": {
            "path": predicate_path,
            "sha256": historical_pin,
        },
        "runtime_eligible_quote_manifest": {
            "path": runtime_eligibility_path,
            "sha256": historical_runtime_eligibility_pin,
        },
    }
    successor_value = copy.deepcopy(historical_value)
    successor_value["source_file_hashes"]["attribution_predicate"][
        "sha256"
    ] = successor_pin
    successor_value["source_file_hashes"][
        "runtime_eligible_quote_manifest"
    ]["sha256"] = successor_runtime_eligibility_pin
    historical_path = _write(
        tmp_path / "historical-semantic-veto.json",
        historical_value,
    )
    current_path = root / semantic_pair.repository_path
    _write(current_path, successor_value)

    manifest = _build(paths)
    historical_binding = {
        "after_sha256": _sha(historical_path),
        "before_sha256": _sha(historical_path),
        "bytes_unchanged": True,
        "repository_path": semantic_pair.repository_path,
        "semantic_summary": {
            "canonical_json_sha256": hashlib.sha256(
                _canonical(historical_value)
            ).hexdigest(),
        },
        "unchanged": True,
    }
    manifest["unchanged_invariants"]["semantic_veto"] = historical_binding
    manifest_path = _write(root / transition.MANIFEST_FILENAME, manifest)

    monkeypatch.setattr(
        transition,
        "SEMANTIC_VETO_REPOSITORY_PATH",
        semantic_pair.repository_path,
    )
    monkeypatch.setattr(
        transition,
        "SEMANTIC_VETO_HISTORICAL_SHA256",
        _sha(historical_path),
    )
    monkeypatch.setattr(
        transition,
        "SEMANTIC_VETO_SUCCESSOR_SHA256",
        _sha(current_path),
    )
    monkeypatch.setattr(
        transition,
        "SEMANTIC_VETO_HISTORICAL_CANONICAL_SHA256",
        historical_binding["semantic_summary"]["canonical_json_sha256"],
    )
    monkeypatch.setattr(
        transition,
        "ATTRIBUTION_PREDICATE_PATH",
        predicate_path,
    )
    monkeypatch.setattr(
        transition,
        "ATTRIBUTION_PREDICATE_HISTORICAL_SHA256",
        historical_pin,
    )
    monkeypatch.setattr(
        transition,
        "ATTRIBUTION_PREDICATE_SUCCESSOR_SHA256",
        successor_pin,
    )
    monkeypatch.setattr(
        transition,
        "RUNTIME_ELIGIBILITY_REPOSITORY_PATH",
        runtime_eligibility_path,
    )
    monkeypatch.setattr(
        transition,
        "RUNTIME_ELIGIBILITY_HISTORICAL_SHA256",
        historical_runtime_eligibility_pin,
    )
    monkeypatch.setattr(
        transition,
        "RUNTIME_ELIGIBILITY_SUCCESSOR_SHA256",
        successor_runtime_eligibility_pin,
    )
    monkeypatch.setattr(
        transition,
        "ATTRIBUTION_PREDICATE_AST_PROJECTION_SHA256",
        transition._attribution_predicate_ast_projection_sha256(
            predicate.read_bytes()
        ),
    )
    return {
        **paths,
        "root": root,
        "research": research,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "historical_value": historical_value,
        "current_path": current_path,
        "predicate": predicate,
        "historical_predicate_bytes": historical_predicate_bytes,
    }


def _ordinary_cycle_successor_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Create an exact analogue of the ordinary-cycle source-pin successor."""
    paths = _fixture(tmp_path / "inputs")
    root = tmp_path / "root"
    research = root / "research"
    research.mkdir(parents=True)
    for name, source in (
        (transition.RESOLUTION_FILENAME, paths["current_resolution"]),
        (transition.OPENAI_RESEARCH_FILENAME, paths["current_openai"]),
        (transition.AUDIT_FILENAME, paths["current_audit"]),
    ):
        (research / name).write_bytes(source.read_bytes())
    (root / transition.PREDECESSOR_TRANSITION_FILENAME).write_bytes(
        paths["predecessor_transition"].read_bytes()
    )
    for pair in paths["invariant_pairs"].values():
        target = root / pair.repository_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(pair.after.read_bytes())

    predicate_path = "fixture_formatter.py"
    predicate = root / predicate_path
    predicate_body = (
        '_MARGARET_THATCHER_CANONICAL_SPEAKER = "margaret thatcher"\n'
        'THATCHER_ATTRIBUTION_RULE_VERSION = "fixture-rule-v1"\n'
        "\n"
        "def packet_is_attributed_to_margaret_thatcher(packet):\n"
        "    return bool(packet)\n"
    )
    historical_predicate = (
        "# historical formatter bytes\n" + predicate_body
    ).encode("utf-8")
    predicate.write_text(
        "# successor receipt-only bytes\n" + predicate_body,
        encoding="utf-8",
    )
    historical_pin = hashlib.sha256(historical_predicate).hexdigest()
    successor_pin = _sha(predicate)

    cycle_pair = paths["invariant_pairs"]["ordinary_cycle"]
    historical_value = json.loads(cycle_pair.before.read_text())
    historical_value["source_file_hashes"] = {
        "attribution_predicate": historical_pin,
    }
    successor_value = copy.deepcopy(historical_value)
    successor_value["source_file_hashes"]["attribution_predicate"] = (
        successor_pin
    )
    historical_path = _write(
        tmp_path / "historical-runtime-eligibility.json",
        historical_value,
    )
    current_path = root / cycle_pair.repository_path
    _write(current_path, successor_value)

    manifest = _build(paths)
    historical_binding = {
        "after_sha256": _sha(historical_path),
        "before_sha256": _sha(historical_path),
        "bytes_unchanged": True,
        "repository_path": cycle_pair.repository_path,
        "semantic_summary": transition._invariant_summary(
            "ordinary_cycle", historical_value
        ),
        "unchanged": True,
    }
    manifest["unchanged_invariants"]["ordinary_cycle"] = historical_binding
    manifest_path = _write(root / transition.MANIFEST_FILENAME, manifest)

    monkeypatch.setattr(
        transition,
        "RUNTIME_ELIGIBILITY_REPOSITORY_PATH",
        cycle_pair.repository_path,
    )
    monkeypatch.setattr(
        transition,
        "RUNTIME_ELIGIBILITY_HISTORICAL_SHA256",
        _sha(historical_path),
    )
    monkeypatch.setattr(
        transition,
        "RUNTIME_ELIGIBILITY_SUCCESSOR_SHA256",
        _sha(current_path),
    )
    monkeypatch.setattr(
        transition,
        "RUNTIME_ELIGIBILITY_HISTORICAL_PREDICATE_SHA256",
        historical_pin,
    )
    monkeypatch.setattr(
        transition,
        "RUNTIME_ELIGIBILITY_SEMANTIC_SUMMARY",
        historical_binding["semantic_summary"],
    )
    monkeypatch.setattr(
        transition,
        "ATTRIBUTION_PREDICATE_PATH",
        predicate_path,
    )
    monkeypatch.setattr(
        transition,
        "ATTRIBUTION_PREDICATE_SUCCESSOR_SHA256",
        successor_pin,
    )
    monkeypatch.setattr(
        transition,
        "ATTRIBUTION_PREDICATE_AST_PROJECTION_SHA256",
        transition._attribution_predicate_ast_projection_sha256(
            predicate.read_bytes()
        ),
    )
    return {
        **paths,
        "root": root,
        "research": research,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "current_path": current_path,
    }


def _historical_context_gate_successor_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Create an exact analogue of the gate ledger-pin-only successor."""
    paths = _rebind_fixture(tmp_path / "fixture")
    root = paths["root"]
    gate_pair = paths["invariant_pairs"]["historical_context_gate"]

    historical_ledger_bytes = b"historical semantic review ledger\n"
    successor_ledger_bytes = b"successor semantic review ledger\n"
    historical_ledger_sha256 = hashlib.sha256(
        historical_ledger_bytes
    ).hexdigest()
    successor_ledger_path = (
        root / transition.HISTORICAL_CONTEXT_GATE_LEDGER_REPOSITORY_PATH
    )
    successor_ledger_path.write_bytes(successor_ledger_bytes)
    successor_ledger_sha256 = _sha(successor_ledger_path)

    historical_value = json.loads(gate_pair.before.read_text())
    historical_value["gate"]["semantic_review_ledger_sha256"] = (
        historical_ledger_sha256
    )
    historical_value["input_hashes"][
        transition.HISTORICAL_CONTEXT_GATE_LEDGER_REPOSITORY_PATH
    ] = historical_ledger_sha256
    successor_value = copy.deepcopy(historical_value)
    successor_value["gate"]["semantic_review_ledger_sha256"] = (
        successor_ledger_sha256
    )
    successor_value["input_hashes"][
        transition.HISTORICAL_CONTEXT_GATE_LEDGER_REPOSITORY_PATH
    ] = successor_ledger_sha256

    historical_path = _write(
        tmp_path / "historical-context-gate.json",
        historical_value,
    )
    current_path = root / gate_pair.repository_path
    _write(current_path, successor_value)
    semantic_summary = transition._invariant_summary(
        "historical_context_gate", historical_value
    )
    historical_before_sha256 = "6" * 64
    historical_binding = {
        "after_sha256": _sha(historical_path),
        "before_sha256": historical_before_sha256,
        "bytes_unchanged": False,
        "repository_path": gate_pair.repository_path,
        "semantic_summary": semantic_summary,
        "unchanged": True,
    }
    manifest = json.loads(paths["manifest"].read_text())
    manifest["unchanged_invariants"][
        "historical_context_gate"
    ] = historical_binding
    _write(paths["manifest"], manifest)

    monkeypatch.setattr(
        transition,
        "HISTORICAL_CONTEXT_GATE_REPOSITORY_PATH",
        gate_pair.repository_path,
    )
    monkeypatch.setattr(
        transition,
        "HISTORICAL_CONTEXT_GATE_HISTORICAL_BEFORE_SHA256",
        historical_before_sha256,
    )
    monkeypatch.setattr(
        transition,
        "HISTORICAL_CONTEXT_GATE_HISTORICAL_SHA256",
        _sha(historical_path),
    )
    monkeypatch.setattr(
        transition,
        "HISTORICAL_CONTEXT_GATE_SUCCESSOR_SHA256",
        _sha(current_path),
    )
    monkeypatch.setattr(
        transition,
        "HISTORICAL_CONTEXT_GATE_HISTORICAL_LEDGER_SHA256",
        historical_ledger_sha256,
    )
    monkeypatch.setattr(
        transition,
        "HISTORICAL_CONTEXT_GATE_SUCCESSOR_LEDGER_SHA256",
        successor_ledger_sha256,
    )
    monkeypatch.setattr(
        transition,
        "HISTORICAL_CONTEXT_GATE_SEMANTIC_SUMMARY",
        semantic_summary,
    )
    return {
        **paths,
        "manifest_value": manifest,
        "current_path": current_path,
        "historical_bytes": historical_path.read_bytes(),
        "successor_ledger_path": successor_ledger_path,
    }


def test_manifest_is_deterministic_scoped_and_secret_free(tmp_path):
    paths = _fixture(tmp_path)
    first = _build(paths)
    second = _build(paths)

    assert first == second
    assert _canonical(first) == _canonical(second)
    assert first["scope"]["quote_ids"] == [transition.EXPECTED_QUOTE_ID]
    assert first["scope"]["resolution_item_ids"] == [
        transition.EXPECTED_RESOLUTION_ITEM_ID
    ]
    assert first["scope"]["source_ids"] == [transition.EXPECTED_SOURCE_ID]
    assert first["scope"][
        "openai_research_rejected_source_paths"
    ] == [
        {"quote_id": quote_id, "rejected_source_index": index}
        for quote_id, index in transition.EXPECTED_OPENAI_RESEARCH_PATHS
    ]
    assert first["transport_redaction"]["removed_query_keys"] == list(
        transition.EXPECTED_REMOVED_QUERY_KEYS
    )
    encoded = _canonical(first)
    assert SECRET_VALUE.encode() not in encoded
    assert OLD_URL.encode() not in encoded
    assert NEW_URL.encode() not in encoded
    assert AUTH_SELECTOR_URL.encode() not in encoded
    assert OPENAI_SECRET_VALUE.encode() not in encoded


def test_retained_query_bytes_cannot_be_reencoded(tmp_path):
    paths = _fixture(tmp_path)
    current = json.loads(paths["current_resolution"].read_text())
    row = current["items"][transition.EXPECTED_RESOLUTION_ITEM_ID]
    row["final_url"] = row["final_url"].replace("%20", "+")
    row["redirect_chain"][1] = row["redirect_chain"][1].replace("%20", "+")
    _write(paths["current_resolution"], current)

    with pytest.raises(
        transition.TransitionError,
        match="retained query bytes or order",
    ):
        _build(paths)


def test_auth_author_selector_is_not_a_signed_credential(tmp_path):
    paths = _fixture(tmp_path)
    manifest = _build(paths)
    assert "auth" not in {
        value.casefold()
        for value in manifest["transport_redaction"]["removed_query_keys"]
    }

    current = json.loads(paths["current_resolution"].read_text())
    current["items"]["b" * 64]["final_url"] = (
        "https://best-quotations.com/item"
    )
    _write(paths["current_resolution"], current)
    with pytest.raises(
        transition.TransitionError,
        match="unreviewed transport host",
    ):
        _build(paths)


def test_non_transport_audit_change_is_rejected(tmp_path):
    paths = _fixture(tmp_path)
    current = json.loads(paths["current_audit"].read_text())
    current["items"][transition.EXPECTED_QUOTE_ID][
        "public_context_supported_fields"
    ] = ["historical_context"]
    _write(paths["current_audit"], current)

    with pytest.raises(
        transition.TransitionError,
        match="non-transport semantic changes",
    ):
        _build(paths)


def test_non_url_openai_research_change_is_rejected(tmp_path):
    paths = _fixture(tmp_path)
    current = json.loads(paths["current_openai"].read_text())
    quote_id, index = transition.EXPECTED_OPENAI_RESEARCH_PATHS[0]
    current["items"][quote_id]["rejected_sources"][index][
        "reason"
    ] = "changed rejection"
    _write(paths["current_openai"], current)

    with pytest.raises(
        transition.TransitionError,
        match="changes beyond reviewed URL redaction",
    ):
        _build(paths)


def test_unchanged_corpus_cycle_and_gate_are_bound_and_rechecked(tmp_path):
    paths = _fixture(tmp_path)
    root = tmp_path / "root"
    research = root / "research"
    research.mkdir(parents=True)
    for name, source in (
        (transition.RESOLUTION_FILENAME, paths["current_resolution"]),
        (transition.OPENAI_RESEARCH_FILENAME, paths["current_openai"]),
        (transition.AUDIT_FILENAME, paths["current_audit"]),
    ):
        (research / name).write_bytes(source.read_bytes())
    predecessor = root / transition.PREDECESSOR_TRANSITION_FILENAME
    predecessor.write_bytes(paths["predecessor_transition"].read_bytes())
    for pair in paths["invariant_pairs"].values():
        target = root / pair.repository_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(pair.after.read_bytes())

    manifest = _build(paths)
    manifest_path = _write(
        root / transition.MANIFEST_FILENAME, manifest
    )
    assert transition.load_and_validate_transition(
        manifest_path, research_dir=research, root=root
    ) == manifest
    assert manifest["predecessor_transition"][
        "source_role_audit_sha256"
    ] == _sha(paths["before_audit"])
    assert manifest["unchanged_invariants"]["historical_context_gate"][
        "bytes_unchanged"
    ] is False

    cycle_pair = paths["invariant_pairs"]["ordinary_cycle"]
    cycle_path = root / cycle_pair.repository_path
    cycle = json.loads(cycle_path.read_text())
    cycle["runtime_eligible_quote_ids"].append("d" * 64)
    _write(cycle_path, cycle)
    with pytest.raises(transition.TransitionError, match="invariant hash"):
        transition.load_and_validate_transition(
            manifest_path, research_dir=research, root=root
        )


def test_exact_semantic_veto_provenance_successor_is_accepted_deterministically(
    tmp_path,
    monkeypatch,
):
    paths = _provenance_successor_fixture(tmp_path, monkeypatch)

    first = transition.load_and_validate_transition(
        paths["manifest_path"],
        research_dir=paths["research"],
        root=paths["root"],
    )
    second = transition.load_and_validate_transition(
        paths["manifest_path"],
        research_dir=paths["research"],
        root=paths["root"],
    )

    assert first == second == paths["manifest"]


def test_exact_ordinary_cycle_provenance_successor_is_accepted_deterministically(
    tmp_path,
    monkeypatch,
):
    paths = _ordinary_cycle_successor_fixture(tmp_path, monkeypatch)

    first = transition.load_and_validate_transition(
        paths["manifest_path"],
        research_dir=paths["research"],
        root=paths["root"],
    )
    second = transition.load_and_validate_transition(
        paths["manifest_path"],
        research_dir=paths["research"],
        root=paths["root"],
    )

    assert first == second == paths["manifest"]


def test_exact_historical_context_gate_provenance_successor_is_accepted(
    tmp_path,
    monkeypatch,
):
    paths = _historical_context_gate_successor_fixture(
        tmp_path, monkeypatch
    )

    first = transition.load_and_validate_transition(
        paths["manifest"],
        research_dir=paths["research"],
        root=paths["root"],
    )
    second = transition.load_and_validate_transition(
        paths["manifest"],
        research_dir=paths["research"],
        root=paths["root"],
    )

    assert first == second == paths["manifest_value"]


def test_historical_context_gate_successor_rejects_unrelated_byte_change(
    tmp_path,
    monkeypatch,
):
    paths = _historical_context_gate_successor_fixture(
        tmp_path, monkeypatch
    )
    current = json.loads(paths["current_path"].read_text())
    current["policy_version"] = "changed-policy"
    _write(paths["current_path"], current)
    monkeypatch.setattr(
        transition,
        "HISTORICAL_CONTEXT_GATE_SUCCESSOR_SHA256",
        _sha(paths["current_path"]),
    )

    with pytest.raises(
        transition.TransitionError,
        match="changes additional bytes",
    ):
        transition.load_and_validate_transition(
            paths["manifest"],
            research_dir=paths["research"],
            root=paths["root"],
        )


def test_historical_context_gate_successor_rejects_only_one_changed_pin(
    tmp_path,
    monkeypatch,
):
    paths = _historical_context_gate_successor_fixture(
        tmp_path, monkeypatch
    )
    current = json.loads(paths["current_path"].read_text())
    current["gate"]["semantic_review_ledger_sha256"] = (
        transition.HISTORICAL_CONTEXT_GATE_HISTORICAL_LEDGER_SHA256
    )
    _write(paths["current_path"], current)
    monkeypatch.setattr(
        transition,
        "HISTORICAL_CONTEXT_GATE_SUCCESSOR_SHA256",
        _sha(paths["current_path"]),
    )

    with pytest.raises(
        transition.TransitionError,
        match="successor ledger binding differs",
    ):
        transition.load_and_validate_transition(
            paths["manifest"],
            research_dir=paths["research"],
            root=paths["root"],
        )


def test_historical_context_gate_successor_rejects_ambiguous_extra_pin(
    tmp_path,
    monkeypatch,
):
    paths = _historical_context_gate_successor_fixture(
        tmp_path, monkeypatch
    )
    current = json.loads(paths["current_path"].read_text())
    current["unrelated_duplicate_pin"] = (
        transition.HISTORICAL_CONTEXT_GATE_SUCCESSOR_LEDGER_SHA256
    )
    _write(paths["current_path"], current)
    monkeypatch.setattr(
        transition,
        "HISTORICAL_CONTEXT_GATE_SUCCESSOR_SHA256",
        _sha(paths["current_path"]),
    )

    with pytest.raises(
        transition.TransitionError,
        match="byte binding is ambiguous",
    ):
        transition.load_and_validate_transition(
            paths["manifest"],
            research_dir=paths["research"],
            root=paths["root"],
        )


def test_historical_context_gate_successor_rejects_wrong_ledger_bytes(
    tmp_path,
    monkeypatch,
):
    paths = _historical_context_gate_successor_fixture(
        tmp_path, monkeypatch
    )
    paths["successor_ledger_path"].write_bytes(b"wrong ledger bytes\n")

    with pytest.raises(
        transition.TransitionError,
        match="successor ledger hash differs",
    ):
        transition.load_and_validate_transition(
            paths["manifest"],
            research_dir=paths["research"],
            root=paths["root"],
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("open_review_disposition", "changed-disposition"),
        ("suppressed_reply", "Changed suppressed reply."),
        ("quote_id", "7" * 64),
        ("public_reply_decision", "eligible_allow"),
    ),
)
def test_historical_context_gate_successor_rejects_semantic_field_change(
    tmp_path,
    monkeypatch,
    field,
    value,
):
    paths = _historical_context_gate_successor_fixture(
        tmp_path, monkeypatch
    )
    current = json.loads(paths["current_path"].read_text())
    current["records"][0][field] = value
    _write(paths["current_path"], current)
    monkeypatch.setattr(
        transition,
        "HISTORICAL_CONTEXT_GATE_SUCCESSOR_SHA256",
        _sha(paths["current_path"]),
    )

    with pytest.raises(
        transition.TransitionError,
        match="changes additional bytes",
    ):
        transition.load_and_validate_transition(
            paths["manifest"],
            research_dir=paths["research"],
            root=paths["root"],
        )


def test_historical_context_gate_historical_source_is_still_accepted(
    tmp_path,
    monkeypatch,
):
    paths = _historical_context_gate_successor_fixture(
        tmp_path, monkeypatch
    )
    paths["current_path"].write_bytes(paths["historical_bytes"])

    assert transition.load_and_validate_transition(
        paths["manifest"],
        research_dir=paths["research"],
        root=paths["root"],
    ) == paths["manifest_value"]


def test_ordinary_cycle_provenance_successor_rejects_another_change(
    tmp_path,
    monkeypatch,
):
    paths = _ordinary_cycle_successor_fixture(tmp_path, monkeypatch)
    current = json.loads(paths["current_path"].read_text())
    current["runtime_eligible_quote_count"] = 2
    _write(paths["current_path"], current)
    monkeypatch.setattr(
        transition,
        "RUNTIME_ELIGIBILITY_SUCCESSOR_SHA256",
        _sha(paths["current_path"]),
    )

    with pytest.raises(
        transition.TransitionError,
        match="changes additional bytes",
    ):
        transition.load_and_validate_transition(
            paths["manifest_path"],
            research_dir=paths["research"],
            root=paths["root"],
        )


def test_semantic_veto_provenance_successor_rejects_another_field_change(
    tmp_path,
    monkeypatch,
):
    paths = _provenance_successor_fixture(tmp_path, monkeypatch)
    current = json.loads(paths["current_path"].read_text())
    current["mode"] = "changed"
    _write(paths["current_path"], current)
    monkeypatch.setattr(
        transition,
        "SEMANTIC_VETO_SUCCESSOR_SHA256",
        _sha(paths["current_path"]),
    )

    with pytest.raises(
        transition.TransitionError,
        match="changes additional bytes",
    ):
        transition.load_and_validate_transition(
            paths["manifest_path"],
            research_dir=paths["research"],
            root=paths["root"],
        )


def test_historical_semantic_veto_manifest_rejects_successor_formatter(
    tmp_path,
    monkeypatch,
):
    paths = _provenance_successor_fixture(tmp_path, monkeypatch)
    historical_bytes = _write(
        tmp_path / "historical-copy.json",
        paths["historical_value"],
    ).read_bytes()
    paths["current_path"].write_bytes(historical_bytes)

    with pytest.raises(
        transition.TransitionError,
        match="source hash differs",
    ):
        transition.load_and_validate_transition(
            paths["manifest_path"],
            research_dir=paths["research"],
            root=paths["root"],
        )


def test_historical_semantic_veto_manifest_accepts_historical_formatter(
    tmp_path,
    monkeypatch,
):
    paths = _provenance_successor_fixture(tmp_path, monkeypatch)
    paths["current_path"].write_bytes(
        _write(
            tmp_path / "historical-copy.json",
            paths["historical_value"],
        ).read_bytes()
    )
    paths["predicate"].write_bytes(paths["historical_predicate_bytes"])

    assert transition.load_and_validate_transition(
        paths["manifest_path"],
        research_dir=paths["research"],
        root=paths["root"],
    ) == paths["manifest"]


def test_semantic_veto_successor_rejects_predicate_ast_drift(
    tmp_path,
    monkeypatch,
):
    paths = _provenance_successor_fixture(tmp_path, monkeypatch)
    paths["predicate"].write_text(
        '_MARGARET_THATCHER_CANONICAL_SPEAKER = "margaret thatcher"\n'
        'THATCHER_ATTRIBUTION_RULE_VERSION = "fixture-rule-v1"\n'
        "\n"
        "def packet_is_attributed_to_margaret_thatcher(packet):\n"
        "    return not bool(packet)\n",
        encoding="utf-8",
    )
    changed_pin = _sha(paths["predicate"])
    current = json.loads(paths["current_path"].read_text())
    current["source_file_hashes"]["attribution_predicate"]["sha256"] = (
        changed_pin
    )
    _write(paths["current_path"], current)
    monkeypatch.setattr(
        transition,
        "ATTRIBUTION_PREDICATE_SUCCESSOR_SHA256",
        changed_pin,
    )
    monkeypatch.setattr(
        transition,
        "SEMANTIC_VETO_SUCCESSOR_SHA256",
        _sha(paths["current_path"]),
    )

    with pytest.raises(
        transition.TransitionError,
        match="predicate semantics differ",
    ):
        transition.load_and_validate_transition(
            paths["manifest_path"],
            research_dir=paths["research"],
            root=paths["root"],
        )


@pytest.mark.parametrize(
    ("mutation", "error"),
    (
        ("source_path", "successor binding differs"),
        ("source_pin", "successor binding differs"),
        ("formatter_bytes", "source hash differs"),
        ("ambiguous_pin", "byte binding is ambiguous"),
    ),
)
def test_semantic_veto_provenance_successor_rejects_binding_drift(
    tmp_path,
    monkeypatch,
    mutation,
    error,
):
    paths = _provenance_successor_fixture(tmp_path, monkeypatch)
    current = json.loads(paths["current_path"].read_text())
    if mutation == "source_path":
        current["source_file_hashes"]["attribution_predicate"]["path"] = (
            "other.py"
        )
        _write(paths["current_path"], current)
        monkeypatch.setattr(
            transition,
            "SEMANTIC_VETO_SUCCESSOR_SHA256",
            _sha(paths["current_path"]),
        )
    elif mutation == "source_pin":
        current["source_file_hashes"]["attribution_predicate"]["sha256"] = (
            "7" * 64
        )
        _write(paths["current_path"], current)
        monkeypatch.setattr(
            transition,
            "SEMANTIC_VETO_SUCCESSOR_SHA256",
            _sha(paths["current_path"]),
        )
    elif mutation == "formatter_bytes":
        paths["predicate"].write_bytes(b"changed formatter bytes\n")
    else:
        current["unrelated_duplicate_pin"] = (
            transition.ATTRIBUTION_PREDICATE_SUCCESSOR_SHA256
        )
        _write(paths["current_path"], current)
        monkeypatch.setattr(
            transition,
            "SEMANTIC_VETO_SUCCESSOR_SHA256",
            _sha(paths["current_path"]),
        )

    with pytest.raises(transition.TransitionError, match=error):
        transition.load_and_validate_transition(
            paths["manifest_path"],
            research_dir=paths["research"],
            root=paths["root"],
        )


def test_current_repository_uses_exact_source_pin_successor_only():
    manifest = transition.ROOT / transition.MANIFEST_FILENAME
    assert _sha(manifest) == (
        "4a08dda2241ac9cef89659f68bf99b27307f9525cbfdf4aee5290b9788058180"
    )
    assert transition.load_and_validate_transition(
        manifest,
        research_dir=(
            transition.ROOT
            / "semantic_alignment_research/quote_research_full_001"
        ),
        root=transition.ROOT,
    )


def test_gate_disposition_and_suppressed_reply_changes_are_rejected(tmp_path):
    paths = _fixture(tmp_path)
    pairs = dict(paths["invariant_pairs"])
    gate_pair = pairs["historical_context_gate"]
    changed = json.loads(gate_pair.after.read_text())
    changed["records"][0]["open_review_disposition"] = "changed"
    changed["records"][0]["suppressed_reply"] = "Changed reply."
    changed_path = _write(tmp_path / "changed-gate.json", changed)
    pairs["historical_context_gate"] = transition.InvariantPair(
        gate_pair.before,
        changed_path,
        gate_pair.repository_path,
    )

    with pytest.raises(
        transition.TransitionError,
        match="historical_context_gate invariant semantics changed",
    ):
        _build(paths, unchanged_invariants=pairs)


def test_invariant_set_is_required_and_exact(tmp_path):
    paths = _fixture(tmp_path)
    incomplete = dict(paths["invariant_pairs"])
    incomplete.pop("semantic_veto")

    with pytest.raises(
        transition.TransitionError,
        match="labels differ from the required set",
    ):
        _build(paths, unchanged_invariants=incomplete)


def test_predecessor_audit_binding_and_current_hash_fail_closed(tmp_path):
    paths = _fixture(tmp_path)
    predecessor = json.loads(paths["predecessor_transition"].read_text())
    predecessor["input_hashes"][transition.AUDIT_FILENAME] = "0" * 64
    _write(paths["predecessor_transition"], predecessor)
    with pytest.raises(
        transition.TransitionError,
        match="does not bind predecessor",
    ):
        _build(paths)

    paths = _fixture(tmp_path / "second")
    manifest = _build(paths)
    root = tmp_path / "root"
    research = root / "research"
    research.mkdir(parents=True)
    (research / transition.RESOLUTION_FILENAME).write_bytes(
        paths["current_resolution"].read_bytes()
    )
    (research / transition.OPENAI_RESEARCH_FILENAME).write_bytes(
        paths["current_openai"].read_bytes()
    )
    (research / transition.AUDIT_FILENAME).write_bytes(
        paths["current_audit"].read_bytes()
    )
    (root / transition.PREDECESSOR_TRANSITION_FILENAME).write_bytes(
        paths["predecessor_transition"].read_bytes()
    )
    manifest_path = _write(root / transition.MANIFEST_FILENAME, manifest)
    audit = json.loads(
        (research / transition.AUDIT_FILENAME).read_text()
    )
    audit["schema_version"] = 99
    _write(research / transition.AUDIT_FILENAME, audit)
    with pytest.raises(transition.TransitionError, match="input hash"):
        transition.load_and_validate_transition(
            manifest_path, research_dir=research, root=root
        )


def test_cli_build_is_byte_deterministic(tmp_path, capsys):
    paths = _fixture(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    common = [
        "build",
        "--predecessor-resolution", str(paths["before_resolution"]),
        "--current-resolution", str(paths["current_resolution"]),
        "--predecessor-openai-research", str(paths["before_openai"]),
        "--current-openai-research", str(paths["current_openai"]),
        "--predecessor-source-role-audit", str(paths["before_audit"]),
        "--current-source-role-audit", str(paths["current_audit"]),
        "--predecessor-transition", str(paths["predecessor_transition"]),
    ]
    for label, pair in sorted(paths["invariant_pairs"].items()):
        common.extend([
            "--unchanged-invariant",
            label,
            str(pair.before),
            str(pair.after),
            pair.repository_path,
        ])
    assert transition.main([*common, "--output", str(first)]) == 0
    capsys.readouterr()
    assert transition.main([*common, "--output", str(second)]) == 0
    capsys.readouterr()
    assert first.read_bytes() == second.read_bytes()


def test_evidence_truth_rebind_is_deterministic_and_nonsemantic(tmp_path):
    paths = _rebind_fixture(tmp_path)
    before_bytes = paths["truth"].read_bytes()
    before = json.loads(before_bytes)

    first = transition.rebind_evidence_truth_audit(
        paths["truth"],
        paths["manifest"],
        research_dir=paths["research"],
        root=paths["root"],
    )
    second = transition.rebind_evidence_truth_audit(
        paths["truth"],
        paths["manifest"],
        research_dir=paths["research"],
        root=paths["root"],
    )

    expected = copy.deepcopy(before)
    expected["input_hashes"][transition.AUDIT_FILENAME] = _sha(
        paths["current_audit"]
    )
    assert first == second == expected
    assert _canonical(first) == _canonical(second)
    assert paths["truth"].read_bytes() == before_bytes
    before["input_hashes"].pop(transition.AUDIT_FILENAME)
    first["input_hashes"].pop(transition.AUDIT_FILENAME)
    assert first == before


def test_evidence_truth_rebind_rejects_predecessor_hash_mismatch(tmp_path):
    paths = _rebind_fixture(tmp_path)
    truth = json.loads(paths["truth"].read_text())
    truth["input_hashes"][transition.AUDIT_FILENAME] = "0" * 64
    _write(paths["truth"], truth)

    with pytest.raises(
        transition.TransitionError,
        match="does not match transition predecessor",
    ):
        transition.rebind_evidence_truth_audit(
            paths["truth"],
            paths["manifest"],
            research_dir=paths["research"],
            root=paths["root"],
        )


def test_evidence_truth_rebind_cli_is_byte_deterministic(tmp_path, capsys):
    paths = _rebind_fixture(tmp_path)
    first = tmp_path / "truth-first.json"
    second = tmp_path / "truth-second.json"
    common = [
        "rebind-evidence-truth",
        "--truth-audit",
        str(paths["truth"]),
        "--manifest",
        str(paths["manifest"]),
        "--research-dir",
        str(paths["research"]),
        "--root",
        str(paths["root"]),
    ]

    assert transition.main([*common, "--output", str(first)]) == 0
    capsys.readouterr()
    assert transition.main([*common, "--output", str(second)]) == 0
    capsys.readouterr()

    assert first.read_bytes() == second.read_bytes()
    old_hash = _sha(paths["before_audit"]).encode()
    new_hash = _sha(paths["current_audit"]).encode()
    original = paths["truth"].read_bytes()
    assert original.count(old_hash) == 1
    assert first.read_bytes() == original.replace(old_hash, new_hash)


def test_duplicate_manifest_object_name_is_rejected(tmp_path):
    manifest = tmp_path / "duplicate.json"
    manifest.write_text(
        '{"schema_version":1,"schema_version":1}\n',
        encoding="utf-8",
    )
    with pytest.raises(
        transition.TransitionError, match="duplicate JSON object name"
    ):
        transition.load_and_validate_transition(
            manifest,
            research_dir=tmp_path,
            root=tmp_path,
        )
