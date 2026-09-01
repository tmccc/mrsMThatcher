#!/usr/bin/env python3
"""Bounded six-call Phase 2D semantic probe; import, prepare, and verify are offline."""

from __future__ import annotations

import argparse
import copy
import json
import os
import stat
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from tools import proposition_ledger_evidence_transport as evidence  # noqa: E402
from tools import proposition_ledger_semantic_delta as semantic  # noqa: E402
from tools import proposition_ledger_xai_provider_preflight as preflight  # noqa: E402
from tools import proposition_ledger_xai_transport_live_probe as base  # noqa: E402

RESEARCH = PROJECT_DIR / "proposition_ledger_research"
PROMPT_PATH = RESEARCH / "phase2d/incremental-ledger-system-prompt-v3.txt"
CHAIN_PATH = RESEARCH / "phase2d/synthetic-semantic-chain.json"
CANONICAL_SCHEMA_PATH = RESEARCH / "schema/proposition-ledger-semantic-delta-v1.schema.json"
TRANSPORT_SCHEMA_PATH = RESEARCH / "schema/proposition-ledger-xai-transport-delta-v2.schema.json"
LEDGER_SCHEMA_PATH = semantic.DEFAULT_LEDGER_SCHEMA_PATH
MANIFEST_PATH = RESEARCH / "phase2b/transport-contract-manifest.json"

PINNED_PYTHON = base.PINNED_PYTHON
PINNED_XAI_SDK_VERSION = base.PINNED_XAI_SDK_VERSION
PROTOCOL_VERSION = "proposition-ledger-phase2d-semantic-probe-v1"
CALL_LOG_VERSION = "proposition-ledger-phase2d-call-log-v1"
VALIDATION_VERSION = "proposition-ledger-phase2d-validation-v1"
SUMMARY_VERSION = "proposition-ledger-phase2d-result-summary-v1"
MAX_OUTPUT_TOKENS = 8192
PROVIDER_CALL_BUDGET = 6
CLIENT_TIMEOUT_SECONDS = base.CLIENT_TIMEOUT_SECONDS
NO_RETRY_CHANNEL_OPTIONS = base.NO_RETRY_CHANNEL_OPTIONS
CALL_SPECS = (
    {"order": 1, "case_id": "semantic-chain", "turn_index": 0, "model": "grok-4.3", "dependency_order": None},
    {"order": 2, "case_id": "semantic-chain", "turn_index": 0, "model": "grok-4.6", "dependency_order": None},
    {"order": 3, "case_id": "semantic-chain", "turn_index": 1, "model": "grok-4.3", "dependency_order": 1},
    {"order": 4, "case_id": "semantic-chain", "turn_index": 1, "model": "grok-4.6", "dependency_order": 2},
    {"order": 5, "case_id": "semantic-chain", "turn_index": 2, "model": "grok-4.3", "dependency_order": 3},
    {"order": 6, "case_id": "semantic-chain", "turn_index": 2, "model": "grok-4.6", "dependency_order": 4},
)

ProbeError = base.ProbeError
ProviderObservation = base.ProviderObservation
canonical_json_bytes = base.canonical_json_bytes
pretty_json_bytes = base.pretty_json_bytes
sha256_bytes = base.sha256_bytes
strict_json_loads = base.strict_json_loads
_PROCESS_LOCK = threading.Lock()


def _expected_chain() -> dict[str, Any]:
    contributor = {"participant_id": "participant-contributor", "role": "contributor", "author_key": "synthetic-author-participant-contributor", "identity_confidence": 1.0}
    account = {"participant_id": "participant-account", "role": "account", "author_key": "synthetic-author-participant-account", "identity_confidence": 1.0}
    return {
        "format_version": "proposition-ledger-phase2d-synthetic-semantic-chain-v1",
        "synthetic": True,
        "conversation_key": "synthetic-semantic-chain",
        "turns": [
            {"turn_index": 0, "turn_id": "synthetic-semantic-turn-0", "parent_turn_id": None, "participant": contributor, "exact_text": "The bridge is closed today.", "exact_text_sha256": "2ce68d867776c3e5e3a9bd587c50fd56bb9d350a200ea45c9bd213428a50510e"},
            {"turn_index": 1, "turn_id": "synthetic-semantic-turn-1", "parent_turn_id": "synthetic-semantic-turn-0", "participant": account, "exact_text": "No—the bridge is open today, but the east entrance is blocked. Which entrance should pedestrians use?", "exact_text_sha256": "24ff6faa72ba0c9b8882ffe0a1cb8a760c7e74a0cf06b6707de32b264a0d4c89"},
            {"turn_index": 2, "turn_id": "synthetic-semantic-turn-2", "parent_turn_id": "synthetic-semantic-turn-1", "participant": contributor, "exact_text": "You're right about the bridge. Pedestrians should use the west entrance, and I withdraw my claim that the bridge is closed.", "exact_text_sha256": "ddbabe8d7cac2c3c7253578d439abf73cd25a3686ba6ddf42a3b113fa7b57ae3"},
        ],
    }


def validate_chain(value: Any) -> dict[str, Any]:
    """Validate the exact synthetic conversation and its text hashes."""
    expected = _expected_chain()
    if value != expected:
        raise ProbeError("synthetic semantic chain differs from the frozen input")
    for index, turn in enumerate(expected["turns"]):
        if sha256_bytes(turn["exact_text"].encode()) != turn["exact_text_sha256"]:
            raise ProbeError(f"synthetic turn {index} text hash differs")
    return expected


def _tracked_hashes() -> dict[str, str]:
    paths = {
        "canonical_schema": CANONICAL_SCHEMA_PATH, "evidence_transport": Path(evidence.__file__),
        "phase2c_reuse_module": Path(base.__file__), "phase2d_system_prompt": PROMPT_PATH,
        "probe_tool": Path(__file__), "semantic_materialiser": Path(semantic.__file__),
        "synthetic_semantic_chain": CHAIN_PATH, "transport_contract_manifest": MANIFEST_PATH,
        "transport_schema": TRANSPORT_SCHEMA_PATH,
    }
    return {name: sha256_bytes(base._read_regular_bytes(path, name)) for name, path in sorted(paths.items())}


def validate_tracked_inputs() -> dict[str, Any]:
    """Load and reconcile the bounded tracked prompt, chain, and schemas."""
    canonical_raw = base._read_regular_bytes(CANONICAL_SCHEMA_PATH, "canonical schema")
    transport_raw = base._read_regular_bytes(TRANSPORT_SCHEMA_PATH, "transport schema")
    canonical, transport = strict_json_loads(canonical_raw), strict_json_loads(transport_raw)
    manifest = base._load_json(MANIFEST_PATH, "transport manifest")
    chain = validate_chain(base._load_json(CHAIN_PATH, "synthetic chain"))
    provider, transformations = preflight.transform_provider_schema(transport)
    derived = evidence.build_response_contract_manifest(transport_schema=transport, xai_provider_schema=provider)
    if manifest != derived or len(transformations) != 23:
        raise ProbeError("Phase 2B transport contract differs")
    if sha256_bytes(canonical_raw) != manifest["canonical_semantic_schema_sha256"] or sha256_bytes(transport_raw) != manifest["transport_schema_sha256"] or preflight.value_sha256(provider) != manifest["xai_provider_schema_sha256"]:
        raise ProbeError("tracked schema hash differs")
    try:
        prompt = base._read_regular_bytes(PROMPT_PATH, "v3 prompt").decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ProbeError("v3 prompt is not strict UTF-8") from exc
    required = ("`no_stable_issue` means", "does not permit omission of explicit propositions", "explicit assertions must still be represented", "`resolved_items` may contain only existing live objects", "must reference real, compatible identifiers", "Do not invent a prior object", "Prefer a concise delta", "`presupposed_only` is not a speaker commitment")
    if any(fragment not in prompt for fragment in required):
        raise ProbeError("v3 prompt is missing a hardening rule")
    return {"canonical_schema": canonical, "transport_schema": transport, "provider_schema": provider,
            "persisted_ledger_schema": base._load_json(LEDGER_SCHEMA_PATH, "ledger schema"),
            "manifest": manifest, "chain": chain, "system_prompt": prompt,
            "protocol_hash": sha256_bytes(prompt.encode()), "input_hashes": _tracked_hashes()}


def _turn(tracked: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    return tracked["chain"]["turns"][int(spec["turn_index"])]


def genesis_context(chain: Mapping[str, Any], turn: Mapping[str, Any]) -> dict[str, Any]:
    """Build the trusted wholly synthetic genesis context."""
    return {"conversation_key": chain["conversation_key"], "current_participant": copy.deepcopy(turn["participant"]), "root_post_id": turn["turn_id"],
            "source_completeness": {"reconstruction_grade": "A", "exact_text_complete": True, "parent_graph_complete": True, "chronology_complete": True, "account_publication_confirmed": True, "complete_prefix_through_turn": True, "limitations": ["Wholly synthetic Phase 2D semantic probe."]}}


def build_request(tracked: Mapping[str, Any], spec: Mapping[str, Any], prior: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build one credential-free request using the Phase 2B transport."""
    turn = _turn(tracked, spec)
    payload = evidence.build_phase2b_user_payload(protocol_version=PROTOCOL_VERSION, protocol_hash=tracked["protocol_hash"], conversation_key=tracked["chain"]["conversation_key"], current_turn_id=turn["turn_id"], turn_index=turn["turn_index"], parent_turn_id=turn["parent_turn_id"], current_turn_text=turn["exact_text"], speaker_descriptor=turn["participant"], prior_ledger=prior, response_contract_manifest=tracked["manifest"])
    request = evidence.build_request_representation(model=spec["model"], user_payload=payload, system_prompt=tracked["system_prompt"], xai_provider_schema=tracked["provider_schema"])
    request.update(max_tokens=MAX_OUTPUT_TOKENS, client_timeout_seconds=CLIENT_TIMEOUT_SECONDS, no_retry_channel_options=[list(x) for x in NO_RETRY_CHANNEL_OPTIONS])
    messages = request["messages"]
    schemas = [canonical_json_bytes(tracked[name]) for name in ("canonical_schema", "transport_schema", "provider_schema")]
    if len(messages) != 2 or any(schema in message["content"].encode() for schema in schemas for message in messages):
        raise ProbeError("schema leaked into conversational messages")
    required = {"max_tokens": 8192, "reasoning_effort": "low", "tools": [], "store_messages": False, "streaming": False, "fallback_model": None, "application_retry_count": 0, "sdk_grpc_retries": False, "tool_choice_parameter_sent": False}
    if any(request.get(key) != value for key, value in required.items()) or "tool_choice" in request:
        raise ProbeError("request controls differ")
    return request


def _proposition(ref: str, text: str, speaker: str, spans: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    value = {"local_ref": ref, "canonical_text": text, "speaker_or_attributor": {"kind": "speaker", "participant_id": speaker, "attributed_participant_id": None}, "exact_evidence_spans": copy.deepcopy(spans), "original_language": "en", "speech_act": "assertion", "proposition_kind": "descriptive", "polarity": "positive", "modality": {"type": "none", "strength": "none"}, "quantification": {"type": "none", "surface_marker": None}, "temporal_scope": {"type": "present", "start": None, "end": None, "surface_marker": None}, "epistemic_status": "asserted", "commitment_status": "speaker_committed", "lifecycle_status": "live", "proposition_group_ref": None, "derivation": {"kind": "direct_span", "source_proposition_refs": [], "normalisation_note": None}, "confidence": 1.0, "uncertainty_reason": None}
    value.update(overrides)
    return value


def _commitment(ref: str, speaker: str, proposition: str, spans: list[dict[str, Any]]) -> dict[str, Any]:
    return {"operation": "add", "local_ref": ref, "participant_id": speaker, "proposition_ref": proposition, "stance": "asserted", "basis": "explicit_speech_act", "confidence": 1.0, "uncertainty_reason": None, "exact_evidence_spans": copy.deepcopy(spans)}


def _find(prior: Mapping[str, Any], collection: str, field: str, *terms: str) -> dict[str, Any]:
    matches = [item for item in prior.get(collection, []) if all(term in str(item.get(field, "")).lower() for term in terms)]
    if len(matches) != 1:
        raise ProbeError(f"witness lookup is ambiguous: {collection}:{terms}")
    return matches[0]


def build_expected_transport_delta(tracked: Mapping[str, Any], turn: Mapping[str, Any], prior: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build a local-only schema-valid witness for fake clients and prepare."""
    spans, speaker = [{"exact_text": turn["exact_text"], "occurrence_index": 0}], turn["participant"]["participant_id"]
    delta = {"schema_version": evidence.TRANSPORT_SCHEMA_VERSION, "canonical_schema_version": evidence.CANONICAL_SCHEMA_VERSION, "conversation_key": tracked["chain"]["conversation_key"], "target_turn_id": turn["turn_id"], "as_of_turn_index": turn["turn_index"], "prior_ledger_reference": None if prior is None else {"ledger_id": prior["ledger_id"], "as_of_turn_index": prior["as_of_turn_index"]}, "new_propositions": [], "proposition_updates": [], "new_proposition_groups": [], "proposition_group_updates": [], "new_issue_states": [], "issue_state_updates": [], "commitment_changes": [], "obligation_changes": [], "new_relations": [], "answer_target_changes": [], "rejected_answer_target_changes": [], "repair_records": [], "resolved_items": [], "extraction_status": "complete", "abstentions": [], "unsupported_inferences_rejected": 0, "warnings": []}
    if turn["turn_index"] == 0:
        delta["new_propositions"] = [_proposition("new-proposition-1", "The bridge is closed today.", speaker, spans, temporal_scope={"type": "present", "start": None, "end": None, "surface_marker": "today"})]
        delta["commitment_changes"] = [_commitment("new-commitment-1", speaker, "new-proposition-1", spans)]
        return delta
    if prior is None:
        raise ProbeError("dependent witness lacks prior ledger")
    closed = _find(prior, "propositions", "canonical_text", "bridge", "closed")
    if turn["turn_index"] == 1:
        delta["new_propositions"] = [_proposition("new-proposition-1", "The bridge is open today.", speaker, spans, speech_act="correction", temporal_scope={"type": "present", "start": None, "end": None, "surface_marker": "today"}), _proposition("new-proposition-2", "The east entrance is blocked.", speaker, spans)]
        delta["commitment_changes"] = [_commitment("new-commitment-1", speaker, "new-proposition-1", spans), _commitment("new-commitment-2", speaker, "new-proposition-2", spans)]
        delta["new_relations"] = [{"local_ref": "new-relation-1", "source_proposition_refs": ["new-proposition-1"], "target_proposition_refs": [closed["proposition_id"]], "relation_type": "corrects", "asserted_or_analysed_by": speaker, "exact_evidence_spans": copy.deepcopy(spans), "confidence": 1.0, "uncertainty_reason": None, "provenance_kind": "transcript_extraction", "analysis_basis": "direct_semantic_content"}]
        delta["new_issue_states"] = [{"local_ref": "new-issue-1", "initiating_speaker": speaker, "canonical_question": "Which entrance should pedestrians use?", "issue_type": "wh", "live_alternatives": [], "addressed_participant": "participant-contributor", "answer_requirements": [{"requirement_type": "supply_value", "description": "Identify the entrance pedestrians should use."}], "related_proposition_refs": ["new-proposition-2"], "status": "open", "resolution_type": None, "confidence": 1.0, "exact_evidence_spans": copy.deepcopy(spans)}]
        return delta
    issue = _find(prior, "issue_states", "canonical_question", "entrance")
    commitments = [item for item in prior["participant_commitments"] if item["participant_id"] == speaker and item["proposition_id"] == closed["proposition_id"]]
    if len(commitments) != 1:
        raise ProbeError("witness commitment lookup is ambiguous")
    delta["new_propositions"] = [_proposition("new-proposition-1", "Pedestrians should use the west entrance.", speaker, spans, speech_act="proposal", proposition_kind="normative", modality={"type": "obligatory", "strength": "strong"})]
    delta["commitment_changes"] = [_commitment("new-commitment-1", speaker, "new-proposition-1", spans), {"operation": "update", "commitment_id": commitments[0]["commitment_id"], "changes": {"stance": "withdrawn", "basis": "explicit_withdrawal"}, "reason": "The speaker explicitly withdraws the earlier claim.", "exact_evidence_spans": copy.deepcopy(spans)}]
    delta["issue_state_updates"] = [{"issue_id": issue["issue_id"], "changes": {"status": "answered", "resolution_type": "direct_answer"}, "reason": "The turn supplies the requested entrance.", "exact_evidence_spans": copy.deepcopy(spans)}]
    delta["answer_target_changes"] = [{"operation": "add", "local_ref": "new-answer-target-1", "issue_refs": [issue["issue_id"]], "proposition_refs": ["new-proposition-1"], "target_status": "confirmed", "confidence": 1.0, "exact_evidence_spans": copy.deepcopy(spans)}]
    delta["resolved_items"] = [{"item_type": "issue", "item_ref": issue["issue_id"], "resolution_type": "direct_answer", "exact_evidence_spans": copy.deepcopy(spans)}]
    return delta


def semantic_reference_errors(delta: Mapping[str, Any], prior: Mapping[str, Any] | None) -> list[str]:
    """Enforce the v3 prior-only rule for explicit resolved-item records."""
    namespaces = {}
    collections = {"proposition": ("propositions", "proposition_id"), "issue": ("issue_states", "issue_id"), "commitment": ("participant_commitments", "commitment_id"), "obligation": ("conversational_obligations", "obligation_id"), "answer_target": ("answer_targets", "answer_target_id"), "repair": ("repair_records", "repair_id")}
    for kind, (name, field) in collections.items():
        namespaces[kind] = {str(item[field]) for item in (prior or {}).get(name, []) if field in item}
    all_ids = set().union(*namespaces.values()) if namespaces else set()
    already = {(str(item.get("item_type")), str(item.get("item_id"))) for item in (prior or {}).get("resolved_items", [])}
    errors = []
    for item in delta.get("resolved_items", []):
        kind, ref = str(item.get("item_type")), str(item.get("item_ref"))
        if ref.startswith("new-") or prior is None:
            errors.append(f"resolved_item_not_from_prior:{kind}:{ref}")
        elif (kind, ref) in already:
            errors.append(f"resolved_item_already_resolved:{kind}:{ref}")
        elif kind in namespaces and ref not in namespaces[kind]:
            errors.append(f"resolved_item_{'wrong_namespace' if ref in all_ids else 'missing'}:{kind}:{ref}")
        elif kind == "issue":
            record = next((x for x in prior.get("issue_states", []) if x.get("issue_id") == ref), None)
            if record is None or record.get("status") not in {"open", "partly_answered", "challenged"}:
                errors.append(f"resolved_item_not_live:{kind}:{ref}")
    return sorted(set(errors))


def _matches(delta: Mapping[str, Any], *terms: str) -> list[Mapping[str, Any]]:
    return [item for item in delta.get("new_propositions", []) if all(term in str(item.get("canonical_text", "")).lower() for term in terms)]


def _semantic_check(delta: Mapping[str, Any], turn: Mapping[str, Any], prior: Mapping[str, Any] | None, manifest: Mapping[str, Any]) -> dict[str, Any]:
    fields = base.SEMANTIC_COLLECTIONS
    count = sum(len(delta.get(field, [])) for field in fields if isinstance(delta.get(field), list))
    speaker, index = turn["participant"]["participant_id"], turn["turn_index"]
    abstentions = delta.get("abstentions")
    issue_only_abstention = (
        index == 0
        and abstentions == ["no_stable_issue"]
        and delta.get("extraction_status") == "complete"
    )
    checks = {"extraction_complete": delta.get("extraction_status") == "complete", "not_abstained": abstentions == [] or issue_only_abstention, "semantic_records_present": count > 0, "transport_resolver_used": int(manifest.get("selector_count", 0)) > 0}
    if index == 0:
        closed = _matches(delta, "bridge", "closed"); refs = {item.get("local_ref") for item in closed}
        checks.update(bridge_closed_proposition=bool(closed), explicit_proposition_without_issue_valid=bool(closed), speaker_commitment_to_closed_proposition=any(item.get("operation") == "add" and item.get("participant_id") == speaker and item.get("proposition_ref") in refs and item.get("stance") == "asserted" for item in delta.get("commitment_changes", [])))
    elif index == 1:
        opened, east = _matches(delta, "bridge", "open"), _matches(delta, "east", "entrance", "blocked")
        open_refs = {item.get("local_ref") for item in opened}; closed_ids = {item["proposition_id"] for item in (prior or {}).get("propositions", []) if "bridge" in item.get("canonical_text", "").lower() and "closed" in item.get("canonical_text", "").lower()}
        correction = any(item.get("relation_type") in {"corrects", "contradicts", "supersedes"} and open_refs.intersection(item.get("source_proposition_refs", [])) and closed_ids.intersection(item.get("target_proposition_refs", [])) for item in delta.get("new_relations", []))
        question = any(item.get("status") == "open" and item.get("issue_type") != "no_stable_issue" and "entrance" in str(item.get("canonical_question", "")).lower() for item in delta.get("new_issue_states", []))
        checks.update(bridge_open_proposition=bool(opened), east_entrance_blocked_proposition=bool(east), compound_content_separate=bool(opened and east and opened[0] is not east[0]), correction_references_prior_closed_proposition=bool(correction), open_entrance_question=question)
    else:
        west = _matches(delta, "west", "entrance")
        closed_ids = {item["proposition_id"] for item in (prior or {}).get("propositions", []) if "bridge" in item.get("canonical_text", "").lower() and "closed" in item.get("canonical_text", "").lower()}
        commitment_ids = {item["commitment_id"] for item in (prior or {}).get("participant_commitments", []) if item.get("participant_id") == speaker and item.get("proposition_id") in closed_ids}
        withdrawal = any(item.get("operation") == "update" and item.get("commitment_id") in commitment_ids and item.get("changes", {}).get("stance") == "withdrawn" and item.get("changes", {}).get("basis") == "explicit_withdrawal" for item in delta.get("commitment_changes", []))
        issue_ids = {item["issue_id"] for item in (prior or {}).get("issue_states", []) if "entrance" in str(item.get("canonical_question", "")).lower() and item.get("status") in {"open", "partly_answered", "challenged"}}
        answered = {item.get("issue_id") for item in delta.get("issue_state_updates", []) if item.get("issue_id") in issue_ids and item.get("changes", {}).get("status") == "answered"}
        resolved = {item.get("item_ref") for item in delta.get("resolved_items", []) if item.get("item_type") == "issue" and item.get("item_ref") in issue_ids}
        checks.update(west_entrance_proposition=bool(west), withdrawal_references_prior_commitment=withdrawal, entrance_issue_answered=bool(answered), entrance_issue_resolved_by_existing_id=bool(answered & resolved))
    failures = sorted(key for key, value in checks.items() if not value)
    return {"status": "passed" if not failures else "failed", "checks": checks, "failed_checks": failures, "semantic_record_count": count}


def _reference_failure_before_materialisation(raw: bytes, turn: Mapping[str, Any], prior: Mapping[str, Any] | None, tracked: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return an ordered semantic-reference failure, or defer to the base pipeline."""
    try:
        parsed = strict_json_loads(raw)
    except base.StrictJSONError:
        return None
    if preflight.intended_validation_errors(tracked["provider_schema"], parsed, pattern_mode="xai_full_string"):
        return None
    if not parsed.get("resolved_items"):
        return None
    resolution = evidence.resolve_transport_delta(parsed, current_turn_id=turn["turn_id"], current_turn_text=turn["exact_text"], transport_schema=tracked["transport_schema"], canonical_schema=tracked["canonical_schema"])
    if not resolution.succeeded or resolution.canonical_delta is None:
        return None
    canonical = resolution.canonical_delta
    if preflight.intended_validation_errors(tracked["canonical_schema"], canonical, pattern_mode="canonical_outer_anchors"):
        return None
    errors = semantic_reference_errors(canonical, prior)
    if not errors:
        return None
    manifest = resolution.resolution_manifest or {}
    validation = base._validation_template(raw, "accepted")
    validation["validation_version"] = VALIDATION_VERSION; validation["validation_sequence"][-1] = "turn_semantic_expectation"
    validation["semantic_expectation_status"] = validation.pop("hidden_expectation_status"); validation.pop("hidden_checks", None); validation["semantic_checks"] = {}
    for stage in ("strict_json", "provider_transport_schema", "evidence_resolution", "canonical_semantic_schema"):
        validation[stage] = {"status": "passed", "errors": []}
    validation["resolution_summary"] = {"resolver_version": manifest.get("resolver_version"), "selector_count": manifest.get("selector_count"), "resolution_manifest_sha256": manifest.get("resolution_manifest_sha256"), "selectors": copy.deepcopy(manifest.get("selectors", []))}
    validation["semantic_reference"] = {"status": "failed", "errors": errors}
    validation.update(structural_validity_status="failed", semantic_expectation_status="not_run", overall_validation_status="failed")
    return {"parsed": parsed, "canonical": canonical, "ledger": None, "validation": validation}


def process_response_bytes(raw: bytes, *, turn: Mapping[str, Any], prior_ledger: Mapping[str, Any] | None, tracked: Mapping[str, Any]) -> dict[str, Any]:
    """Reuse the tested Phase 2C structural pipeline, replacing only its case check."""
    reference_failure = _reference_failure_before_materialisation(raw, turn, prior_ledger, tracked)
    if reference_failure is not None:
        return reference_failure
    def no_hidden_check(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "passed", "checks": {}}
    with _PROCESS_LOCK:
        old_genesis, old_hidden = base.genesis_context, base.hidden_expectation_check
        try:
            base.genesis_context, base.hidden_expectation_check = genesis_context, no_hidden_check
            result = base.process_response_bytes(raw, case=tracked["chain"], turn=turn, prior_ledger=prior_ledger, tracked=tracked)
        finally:
            base.genesis_context, base.hidden_expectation_check = old_genesis, old_hidden
    validation = result["validation"]
    validation["validation_version"] = VALIDATION_VERSION
    validation["validation_sequence"][-1] = "turn_semantic_expectation"
    validation["semantic_expectation_status"] = validation.pop("hidden_expectation_status")
    validation.pop("hidden_checks", None)
    validation["semantic_checks"] = {}
    if validation["structural_validity_status"] == "passed" and result["canonical"] is not None:
        errors = semantic_reference_errors(result["canonical"], prior_ledger)
        if errors:
            validation["semantic_reference"] = {"status": "failed", "errors": errors}
            for stage in ("deterministic_materialisation", "persisted_ledger"):
                validation[stage] = {"status": "not_run", "errors": []}
            validation.update(structural_validity_status="failed", semantic_expectation_status="not_run", overall_validation_status="failed")
            result["ledger"] = None
        else:
            check = _semantic_check(result["canonical"], turn, prior_ledger, result["validation"].get("resolution_summary") or {})
            validation["semantic_checks"] = check
            validation["semantic_expectation_status"] = check["status"]
            validation["overall_validation_status"] = check["status"]
    else:
        validation["semantic_expectation_status"] = "not_run"
    return result


def construct_six_local_requests(tracked: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Construct all six requests with two independent witness-ledger chains."""
    requests, priors = [], {}
    for spec in CALL_SPECS:
        prior = priors.get(spec["model"])
        requests.append(build_request(tracked, spec, prior))
        turn = _turn(tracked, spec); witness = build_expected_transport_delta(tracked, turn, prior)
        result = process_response_bytes(canonical_json_bytes(witness), turn=turn, prior_ledger=prior, tracked=tracked)
        if result["validation"]["overall_validation_status"] != "passed" or result["ledger"] is None:
            raise ProbeError("local semantic witness failed")
        priors[spec["model"]] = result["ledger"]
    return requests, priors


def compile_local_sdk_requests(requests: Sequence[Mapping[str, Any]], tracked: Mapping[str, Any]) -> dict[str, Any]:
    """Compile requests through the pinned SDK while denying transport."""
    guard, compiled = preflight.NetworkDenialGuard(), 0
    with guard:
        try:
            import grpc
            guard.patch_grpc(grpc)
            from xai_sdk.chat import BaseChat, system, user
            from xai_sdk.proto import chat_pb2
            from xai_sdk.sync.chat import Client as ChatClient
        except ImportError as exc:
            raise ProbeError("pinned xai-sdk import failed") from exc
        for item in requests:
            channel = preflight._RegistrationOnlyChannel(); client = ChatClient(channel)
            response_format = chat_pb2.ResponseFormat(format_type=chat_pb2.FORMAT_TYPE_JSON_SCHEMA, schema=canonical_json_bytes(tracked["provider_schema"]).decode())
            chat = client.create(model=item["model"], messages=[system(item["messages"][0]["content"]), user(item["messages"][1]["content"])], max_tokens=MAX_OUTPUT_TOKENS, reasoning_effort="low", tools=[], parallel_tool_calls=False, response_format=response_format, search_parameters=None, store_messages=False)
            request = BaseChat._make_request(chat, 1)
            if channel.rpc_invocation_count or request.max_tokens != 8192 or request.tools or request.HasField("tool_choice") or request.HasField("search_parameters"):
                raise ProbeError("local SDK request contract differs")
            compiled += 1
    return {"requests_constructed": compiled, "provider_calls_made": 0, "transport_rpc_invocations": 0}


def _identity(spec: Mapping[str, Any], tracked: Mapping[str, Any]) -> str:
    turn = _turn(tracked, spec)
    return sha256_bytes(canonical_json_bytes({"spec": spec, "turn_id": turn["turn_id"], "conversation_key": tracked["chain"]["conversation_key"], "input_hashes": tracked["input_hashes"]}))


def make_call_log(tracked: Mapping[str, Any]) -> dict[str, Any]:
    """Return the durable, ordered six-entry call plan."""
    entries = []
    for spec in CALL_SPECS:
        turn = _turn(tracked, spec)
        entries.append({**copy.deepcopy(spec), "conversation_key": tracked["chain"]["conversation_key"], "turn_id": turn["turn_id"], "call_identity": _identity(spec, tracked), "state": "planned", "attempt_number": 0, "provider_call_count": 0, "attempted_at_utc": None, "finished_at_utc": None})
    return {"call_log_version": CALL_LOG_VERSION, "prepared_at_utc": base.utc_now(), "planned_call_count": 6, "attempted_call_count": 0, "provider_call_count": 0, "retry_call_count": 0, "repair_call_count": 0, "fallback_call_count": 0, "input_hashes": copy.deepcopy(tracked["input_hashes"]), "entries": entries}


def _validate_log(log: Any, tracked: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(log, dict) or log.get("call_log_version") != CALL_LOG_VERSION or log.get("input_hashes") != tracked["input_hashes"] or len(log.get("entries", [])) != 6:
        raise ProbeError("call log identity differs")
    if any(log.get(key) != 0 for key in ("retry_call_count", "repair_call_count", "fallback_call_count")):
        raise ProbeError("call log contains retry, repair, or fallback")
    attempted = 0
    for spec, entry in zip(CALL_SPECS, log["entries"]):
        if any(entry.get(key) != spec[key] for key in ("order", "case_id", "turn_index", "model", "dependency_order")) or entry.get("call_identity") != _identity(spec, tracked):
            raise ProbeError("call log entry differs")
        if entry.get("state") not in {"planned", "attempted", "completed", "failed", "blocked"}:
            raise ProbeError("call state differs")
        was_attempted = entry["state"] in {"attempted", "completed", "failed"}
        if entry.get("provider_call_count") != int(was_attempted) or entry.get("attempt_number") != int(was_attempted):
            raise ProbeError("entry attempt count differs")
        attempted += int(was_attempted)
    if log.get("attempted_call_count") != attempted or log.get("provider_call_count") != attempted:
        raise ProbeError("aggregate attempt count differs")
    return log


def _load_log(output: Path, tracked: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_log(base._load_json(output / "call-log.json", "call log"), tracked)


def _write_log(output: Path, log: Mapping[str, Any]) -> None:
    base._write_private_json(output / "call-log.json", log)


def _call_dir(output: Path, entry: Mapping[str, Any]) -> Path:
    return base._call_directory(output, entry)


def _validation_no_response(status: str, dependency: int | None = None) -> dict[str, Any]:
    value = base._validation_template(None, "not_attempted" if dependency else "failed")
    value["validation_version"] = VALIDATION_VERSION; value["validation_sequence"][-1] = "turn_semantic_expectation"
    value["semantic_expectation_status"] = value.pop("hidden_expectation_status"); value.pop("hidden_checks", None); value["semantic_checks"] = {}
    value.update(structural_validity_status="not_run_blocked" if dependency else "not_run_no_response", semantic_expectation_status="not_run_blocked" if dependency else "not_run_no_response", overall_validation_status="blocked" if dependency else "failed")
    if dependency:
        value["blocked_by_order"] = dependency
    return value


def _usage(observation: ProviderObservation | None, latency: float | None) -> dict[str, Any]:
    return {"provider_response_received": observation is not None, "returned_model_id": None if observation is None else observation.returned_model_id, "finish_reason": None if observation is None else observation.finish_reason, "latency_seconds": latency, "usage": {} if observation is None else copy.deepcopy(dict(observation.usage))}


def _persist(call_dir: Path, raw: bytes, result: Mapping[str, Any]) -> None:
    base._ensure_private_directory(call_dir); base._write_private_bytes(call_dir / "raw-response.txt", raw)
    for key, name in (("parsed", "parsed-transport.json"), ("canonical", "resolved-canonical-delta.json"), ("ledger", "materialised-ledger.json")):
        if result.get(key) is not None:
            base._write_private_json(call_dir / name, result[key])
    base._write_private_json(call_dir / "validation.json", result["validation"])


class XaiTransport(base.XaiTransport):
    """Use the tested official-SDK transport with an 8,192-token ceiling."""

    def sample(self, entry: Mapping[str, Any], request: Mapping[str, Any], _context: Mapping[str, Any]) -> ProviderObservation:
        """Perform exactly one non-streaming structured-output sample."""
        response_format = self._chat_pb2.ResponseFormat(format_type=self._chat_pb2.FORMAT_TYPE_JSON_SCHEMA, schema=canonical_json_bytes(request["response_format"]["schema"]).decode())
        chat = self._client.chat.create(model=entry["model"], messages=[self._system(request["messages"][0]["content"]), self._user(request["messages"][1]["content"])], max_tokens=request["max_tokens"], reasoning_effort="low", tools=[], parallel_tool_calls=False, response_format=response_format, search_parameters=None, store_messages=False)
        response = chat.sample()
        if not isinstance(response.content, str):
            raise ProbeError("provider response content was not text")
        return ProviderObservation(response.content, response.proto.model or None, response.finish_reason or None, self._message_to_dict(response.usage, preserving_proto_field_name=True))


def _prepare_output(path: str | Path) -> Path:
    output = Path(path)
    if not os.path.lexists(output):
        return base._create_private_output(output)
    metadata = output.lstat()
    if not output.is_absolute() or stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700 or any(output.iterdir()):
        raise ProbeError("existing private output must be empty mode 0700")
    return output


def prepare_run(output: str | Path, *, environment_check: Callable[[], Mapping[str, Any]] = base.verify_pinned_environment, local_compiler: Callable[[Sequence[Mapping[str, Any]], Mapping[str, Any]], Mapping[str, Any]] = compile_local_sdk_requests) -> dict[str, Any]:
    """Prepare six requests and a call log without provider capability."""
    environment_check(); tracked = validate_tracked_inputs(); requests, priors = construct_six_local_requests(tracked)
    compilation = local_compiler(requests, tracked)
    if set(priors) != {"grok-4.3", "grok-4.6"} or compilation.get("requests_constructed") != 6 or compilation.get("provider_calls_made") != 0 or compilation.get("transport_rpc_invocations") != 0:
        raise ProbeError("offline prepare contract differs")
    output_dir = _prepare_output(output); _write_log(output_dir, make_call_log(tracked)); base.audit_private_run(output_dir, prepared=True)
    return {"status": "prepared", "output": str(output_dir), "planned_calls": 6, "provider_calls": 0, "requests_constructed": 6, "model_chain_count": 2, "schema_absent_from_messages": True, "schema_present_in_response_format": True, "maximum_output_tokens": 8192}


def _valid_prior(output: Path, log: Mapping[str, Any], entry: Mapping[str, Any], tracked: Mapping[str, Any]) -> dict[str, Any] | None:
    dependency = entry.get("dependency_order")
    if dependency is None:
        return None
    dependency_entry = log["entries"][int(dependency) - 1]; path = _call_dir(output, dependency_entry) / "materialised-ledger.json"
    if not path.exists():
        return None
    prior = base._load_json(path, "same-model prior")
    turn = _turn(tracked, entry)
    if dependency_entry["model"] != entry["model"] or prior.get("target_turn_id") != turn["parent_turn_id"] or prior.get("ledger_sha256") != semantic.phase1.ledger_sha256(prior) or semantic.phase1._jsonschema_errors(prior, tracked["persisted_ledger_schema"]):
        raise ProbeError("same-model predecessor ledger differs")
    return prior


def run_probe(output: str | Path, *, confirm_calls: int | None, transport: Any | None = None, environment_check: Callable[[], Mapping[str, Any]] = base.verify_pinned_environment) -> dict[str, Any]:
    """Attempt every eligible planned call once, without retry or fallback."""
    if confirm_calls != 6:
        raise ProbeError("--run requires --confirm-calls 6")
    output_dir = base._require_private_output(output)
    with base._exclusive_execution_lock(output_dir):
        environment_check(); tracked = validate_tracked_inputs(); log = _load_log(output_dir, tracked)
        if os.path.lexists(output_dir / "SHA256SUMS") or not any(e["state"] == "planned" for e in log["entries"]):
            raise ProbeError("run is finalised or has no planned call")
        active, owned = (XaiTransport(), True) if transport is None else (transport, False)
        try:
            for index, entry in enumerate(log["entries"]):
                if entry["state"] != "planned":
                    continue
                prior = _valid_prior(output_dir, log, entry, tracked); dependency = entry["dependency_order"]
                call_dir = _call_dir(output_dir, entry); base._ensure_private_directory(call_dir)
                if dependency is not None and prior is None:
                    entry.update(state="blocked", blocked_by_order=dependency, finished_at_utc=base.utc_now()); _write_log(output_dir, log)
                    base._write_private_json(call_dir / "validation.json", _validation_no_response("blocked", dependency)); base._write_private_json(call_dir / "usage.json", _usage(None, None)); continue
                entry.update(state="attempted", attempt_number=1, provider_call_count=1, attempted_at_utc=base.utc_now()); log["attempted_call_count"] += 1; log["provider_call_count"] += 1; _write_log(output_dir, log)
                request, turn, began = build_request(tracked, entry, prior), _turn(tracked, entry), time.monotonic()
                try:
                    observation = active.sample(copy.deepcopy(entry), request, {"turn": copy.deepcopy(turn), "prior_ledger": copy.deepcopy(prior)})
                except Exception as exc:
                    validation = _validation_no_response("failed"); validation["provider_error_type"] = type(exc).__name__[:128]
                    base._write_private_json(call_dir / "validation.json", validation); base._write_private_json(call_dir / "usage.json", _usage(None, round(time.monotonic() - began, 6))); state = "failed"
                else:
                    latency = round(time.monotonic() - began, 6)
                    try:
                        raw = observation.raw_text.encode("utf-8", "strict"); base._write_private_bytes(call_dir / "raw-response.txt", raw)
                        result = process_response_bytes(raw, turn=turn, prior_ledger=prior, tracked=tracked); _persist(call_dir, raw, result)
                        base._write_private_json(call_dir / "usage.json", _usage(observation, latency)); state = "completed" if result["validation"]["overall_validation_status"] == "passed" else "failed"
                    except Exception as exc:
                        validation = _validation_no_response("failed"); validation.update(server_acceptance_status="accepted", structural_validity_status="failed", provider_error_type=f"local_validation_{type(exc).__name__}"[:128])
                        base._write_private_json(call_dir / "validation.json", validation); base._write_private_json(call_dir / "usage.json", _usage(observation, latency)); state = "failed"
                entry.update(state=state, finished_at_utc=base.utc_now()); _write_log(output_dir, log)
        finally:
            if owned:
                try: active.close()
                except Exception: pass
        summary = build_result_summary(output_dir, _load_log(output_dir, tracked), tracked)
        base._write_private_json(output_dir / "result-summary.json", summary); base.write_checksums(output_dir); base.audit_private_run(output_dir, prepared=False)
        return summary


def _token(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else int(value) if isinstance(value, str) and value.isdigit() else 0


def build_result_summary(output: Path, log: Mapping[str, Any], tracked: Mapping[str, Any]) -> dict[str, Any]:
    """Derive aggregate and per-call results from the saved artifacts."""
    calls, totals = [], {key: 0 for key in ("prompt_tokens", "prompt_text_tokens", "cached_prompt_tokens", "cached_prompt_text_tokens", "reasoning_tokens", "completion_tokens", "total_tokens")}
    for entry in log["entries"]:
        call_dir = _call_dir(output, entry); validation = base._load_json(call_dir / "validation.json", "validation"); usage_record = base._load_json(call_dir / "usage.json", "usage"); usage = usage_record.get("usage", {})
        for key in totals:
            totals[key] += _token(usage.get(key)) if isinstance(usage, Mapping) else 0
        finish = usage_record.get("finish_reason"); ceiling = finish == "REASON_MAX_LEN" or (isinstance(usage, Mapping) and _token(usage.get("completion_tokens")) >= 8192)
        calls.append({"order": entry["order"], "turn_id": entry["turn_id"], "turn_index": entry["turn_index"], "model": entry["model"], "state": entry["state"], "provider_call_count": entry["provider_call_count"], "server_acceptance_status": validation.get("server_acceptance_status"), "structural_validity_status": validation.get("structural_validity_status"), "semantic_expectation_status": validation.get("semantic_expectation_status"), "transport_resolution_status": validation.get("evidence_resolution", {}).get("status"), "canonical_validation_status": validation.get("canonical_semantic_schema", {}).get("status"), "semantic_reference_status": validation.get("semantic_reference", {}).get("status"), "materialisation_status": validation.get("deterministic_materialisation", {}).get("status"), "persisted_ledger_status": validation.get("persisted_ledger", {}).get("status"), "finish_reason": finish, "reached_output_token_ceiling": ceiling, "latency_seconds": usage_record.get("latency_seconds"), "usage": copy.deepcopy(usage)})
    count = lambda field, expected="passed": sum(item[field] == expected for item in calls)
    by_model = {model: [item for item in calls if item["model"] == model] for model in ("grok-4.3", "grok-4.6")}
    return {"summary_version": SUMMARY_VERSION, "planned_call_count": 6, "attempted_call_count": log["attempted_call_count"], "provider_call_count": log["provider_call_count"], "completed_call_count": count("state", "completed"), "failed_call_count": count("state", "failed"), "blocked_call_count": count("state", "blocked"), "retry_call_count": 0, "repair_call_count": 0, "fallback_call_count": 0, "server_acceptance_success_count": count("server_acceptance_status", "accepted"), "structural_success_count": count("structural_validity_status"), "transport_success_count": count("transport_resolution_status"), "canonical_success_count": count("canonical_validation_status"), "semantic_reference_success_count": count("semantic_reference_status"), "materialisation_success_count": count("materialisation_status"), "persisted_ledger_success_count": count("persisted_ledger_status"), "semantic_expectation_success_count": count("semantic_expectation_status"), "three_turn_chain_structurally_valid_by_model": {model: all(x["structural_validity_status"] == "passed" for x in rows) for model, rows in by_model.items()}, "three_turn_semantic_expectations_passed_by_model": {model: all(x["semantic_expectation_status"] == "passed" for x in rows) for model, rows in by_model.items()}, "output_ceiling_reached_count": sum(x["reached_output_token_ceiling"] for x in calls), "maximum_output_tokens": 8192, "token_totals": totals, "calls": calls, "input_hashes": copy.deepcopy(tracked["input_hashes"]), "real_conversation_records_read_by_probe": 0, "held_out_records_read": 0, "model_winner_selected": False, "phase2a_pilot_rerun": False, "production_touched": False, "merged": False, "deployed": False}


def _compare(path: Path, expected: Any, label: str) -> None:
    if expected is None:
        if os.path.lexists(path):
            raise ProbeError(f"unexpected {label}")
    elif base._read_regular_bytes(path, label) != pretty_json_bytes(expected):
        raise ProbeError(f"saved {label} differs")


def verify_run(output: str | Path, *, environment_check: Callable[[], Mapping[str, Any]] = base.verify_pinned_environment) -> dict[str, Any]:
    """Reprocess saved responses deterministically without API access."""
    output_dir = base._require_private_output(output)
    with base._exclusive_execution_lock(output_dir):
        environment_check(); tracked = validate_tracked_inputs(); before = base._read_regular_bytes(output_dir / "call-log.json", "call log"); log = _load_log(output_dir, tracked)
        if all(entry["state"] == "planned" for entry in log["entries"]):
            base.audit_private_run(output_dir, prepared=True)
            return {"status": "passed_prepared", "provider_calls_made": 0, "saved_responses_reprocessed": 0, "api_key_required": False}
        checksum = base.verify_checksums(output_dir); base.audit_private_run(output_dir, prepared=False)
        priors, reprocessed = {}, 0
        for entry in log["entries"]:
            call_dir = _call_dir(output_dir, entry); prior = priors.get(entry["model"]) if entry["dependency_order"] else None; raw_path = call_dir / "raw-response.txt"
            if raw_path.exists():
                result = process_response_bytes(base._read_regular_bytes(raw_path, "raw response"), turn=_turn(tracked, entry), prior_ledger=prior, tracked=tracked)
                for key, name, label in (("parsed", "parsed-transport.json", "parsed transport"), ("canonical", "resolved-canonical-delta.json", "canonical delta"), ("ledger", "materialised-ledger.json", "materialised ledger"), ("validation", "validation.json", "validation")):
                    _compare(call_dir / name, result[key], label)
                expected_state = "completed" if result["validation"]["overall_validation_status"] == "passed" else "failed"
                if entry["state"] != expected_state:
                    raise ProbeError("call state differs from replay")
                if result["ledger"] is not None:
                    priors[entry["model"]] = result["ledger"]
                reprocessed += 1
            elif entry["state"] == "blocked":
                available = priors.get(entry["model"])
                if available is not None and available.get("target_turn_id") == _turn(tracked, entry)["parent_turn_id"]:
                    raise ProbeError("blocked call has a valid predecessor")
                _compare(call_dir / "validation.json", _validation_no_response("blocked", entry["dependency_order"]), "blocked validation")
            elif entry["state"] == "failed":
                validation = base._load_json(call_dir / "validation.json", "provider failure")
                if validation.get("server_acceptance_status") != "failed":
                    raise ProbeError("raw-less failure differs")
            else:
                raise ProbeError("final run contains nonterminal call")
            usage = base._load_json(call_dir / "usage.json", "usage")
            if set(usage) != {"provider_response_received", "returned_model_id", "finish_reason", "latency_seconds", "usage"}:
                raise ProbeError("usage shape differs")
        expected_summary = build_result_summary(output_dir, log, tracked)
        if base._read_regular_bytes(output_dir / "result-summary.json", "summary") != pretty_json_bytes(expected_summary) or base._read_regular_bytes(output_dir / "call-log.json", "call log") != before:
            raise ProbeError("summary or call log differs")
        final = base.verify_checksums(output_dir)
        return {"status": "passed", "provider_calls_made": 0, "saved_responses_reprocessed": reprocessed, "api_key_required": False, "call_log_unchanged": True, "checksum_status": final["status"], "checked_files": checksum["checked_files"]}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 2D six-call semantic probe")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", action="store_true"); modes.add_argument("--run", action="store_true"); modes.add_argument("--verify", action="store_true")
    parser.add_argument("--output", type=Path, required=True); parser.add_argument("--confirm-calls", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch exactly one of prepare, run, or verify."""
    args = _parser().parse_args(argv)
    try:
        if args.prepare:
            if args.confirm_calls is not None: raise ProbeError("prepare does not accept confirm-calls")
            result = prepare_run(args.output)
        elif args.run:
            result = run_probe(args.output, confirm_calls=args.confirm_calls)
        else:
            if args.confirm_calls is not None: raise ProbeError("verify does not accept confirm-calls")
            result = verify_run(args.output)
    except ProbeError as exc:
        print(f"error: {exc}", file=sys.stderr); return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
