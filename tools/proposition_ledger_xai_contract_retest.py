#!/usr/bin/env python3
"""Offline contract recheck and bounded four-call Phase 2E xAI retest."""

from __future__ import annotations

import argparse
import copy
import json
import os
import stat
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from tools import proposition_ledger_evidence_transport as evidence  # noqa: E402
from tools import proposition_ledger_semantic_delta as semantic  # noqa: E402
from tools import proposition_ledger_xai_semantic_probe as phase2d  # noqa: E402
from tools import proposition_ledger_xai_transport_live_probe as base  # noqa: E402

RESEARCH = PROJECT_DIR / "proposition_ledger_research"
PROMPT_PATH = RESEARCH / "phase2e/incremental-ledger-system-prompt-v4.txt"
PHASE2D_RUN_PARENT = Path("/disks/disk1/research/private-runs")
PHASE2D_RUN_GLOB = "proposition-ledger-phase2d-semantic-hardening-*"
PROTOCOL_VERSION = "proposition-ledger-phase2e-contract-retest-v1"
CALL_LOG_VERSION = "proposition-ledger-phase2e-call-log-v1"
VALIDATION_VERSION = "proposition-ledger-phase2e-validation-v1"
SUMMARY_VERSION = "proposition-ledger-phase2e-result-summary-v1"
OFFLINE_RECHECK_VERSION = "proposition-ledger-phase2e-offline-recheck-v1"
MAX_OUTPUT_TOKENS = 8192
PROVIDER_CALL_BUDGET = 4
PINNED_PYTHON = base.PINNED_PYTHON
PINNED_XAI_SDK_VERSION = base.PINNED_XAI_SDK_VERSION
ROOT_OFFLINE_FILE = "offline-recheck.json"
HISTORICAL_TRANSPORT_VERSION = "proposition-ledger-xai-transport-delta-v2.0.0"
HISTORICAL_CANONICAL_VERSION = "proposition-ledger-semantic-delta-v1.1.0"

CALL_SPECS = (
    {"order": 1, "case_id": "semantic-chain", "turn_index": 0, "model": "grok-4.6", "dependency_order": None},
    {"order": 2, "case_id": "semantic-chain", "turn_index": 0, "model": "grok-4.3", "dependency_order": None},
    {"order": 3, "case_id": "semantic-chain", "turn_index": 1, "model": "grok-4.3", "dependency_order": 2},
    {"order": 4, "case_id": "semantic-chain", "turn_index": 2, "model": "grok-4.3", "dependency_order": 3},
)
PHASE2D_RESPONSE_ORDERS = (1, 2, 3, 4, 6)
PHASE2D_PRIOR_ORDERS = {1: None, 2: None, 3: 1, 4: 2, 6: 4}
PHASE2D_EXPECTED = {
    1: "speaker_commitment_consistency_rejected",
    2: "passed",
    3: "semantic_delta_schema_rejected",
    4: "passed",
    6: "passed",
}

ProbeError = base.ProbeError
ProviderObservation = base.ProviderObservation
canonical_json_bytes = base.canonical_json_bytes
pretty_json_bytes = base.pretty_json_bytes
sha256_bytes = base.sha256_bytes
strict_json_loads = base.strict_json_loads
XaiTransport = phase2d.XaiTransport
compile_local_sdk_requests = phase2d.compile_local_sdk_requests


def validate_tracked_inputs() -> dict[str, Any]:
    """Reuse Phase 2D inputs, replacing only its prompt and identity hashes."""

    tracked = phase2d.validate_tracked_inputs()
    try:
        prompt = base._read_regular_bytes(PROMPT_PATH, "v4 prompt").decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ProbeError("v4 prompt is not strict UTF-8") from exc
    required = (
        "`question_presupposition`", "`epistemic_status = presupposed_only`",
        "`epistemic_status = questioned`", "`commitment_status`", "`speaker_committed`",
        "participant-commitment", "`no_stable_issue` is an issue-level conclusion",
        "propositions and commitments",
    )
    if any(fragment not in prompt for fragment in required):
        raise ProbeError("v4 prompt is missing a contract-alignment rule")
    hashes = dict(tracked["input_hashes"])
    hashes.pop("phase2d_system_prompt", None)
    hashes.pop("probe_tool", None)
    hashes["phase2e_system_prompt"] = sha256_bytes(prompt.encode("utf-8"))
    hashes["contract_retest_tool"] = sha256_bytes(base._read_regular_bytes(__file__, "retest tool"))
    tracked = copy.deepcopy(tracked)
    tracked.update(system_prompt=prompt, protocol_hash=hashes["phase2e_system_prompt"], input_hashes=dict(sorted(hashes.items())))
    return tracked


def _turn(tracked: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    return tracked["chain"]["turns"][int(spec["turn_index"])]


def build_request(tracked: Mapping[str, Any], spec: Mapping[str, Any], prior: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build one v4 request through the unchanged Phase 2B transport."""

    turn = _turn(tracked, spec)
    payload = evidence.build_phase2b_user_payload(
        protocol_version=PROTOCOL_VERSION, protocol_hash=tracked["protocol_hash"],
        conversation_key=tracked["chain"]["conversation_key"], current_turn_id=turn["turn_id"],
        turn_index=turn["turn_index"], parent_turn_id=turn["parent_turn_id"],
        current_turn_text=turn["exact_text"], speaker_descriptor=turn["participant"],
        prior_ledger=prior, response_contract_manifest=tracked["manifest"],
    )
    request = evidence.build_request_representation(
        model=spec["model"], user_payload=payload, system_prompt=tracked["system_prompt"],
        xai_provider_schema=tracked["provider_schema"],
    )
    request.update(max_tokens=MAX_OUTPUT_TOKENS, client_timeout_seconds=base.CLIENT_TIMEOUT_SECONDS,
                   no_retry_channel_options=[list(item) for item in base.NO_RETRY_CHANNEL_OPTIONS])
    required = {"reasoning_effort": "low", "tools": [], "store_messages": False,
                "streaming": False, "fallback_model": None, "application_retry_count": 0,
                "sdk_grpc_retries": False, "tool_choice_parameter_sent": False}
    if any(request.get(key) != value for key, value in required.items()) or "tool_choice" in request:
        raise ProbeError("request controls differ")
    encoded = [canonical_json_bytes(tracked[name]).decode() for name in ("canonical_schema", "transport_schema", "provider_schema")]
    if any(schema in message["content"] for schema in encoded for message in request["messages"]):
        raise ProbeError("schema leaked into conversational messages")
    return request


def process_response_bytes(raw: bytes, *, turn: Mapping[str, Any], prior_ledger: Mapping[str, Any] | None,
                           tracked: Mapping[str, Any]) -> dict[str, Any]:
    """Use the corrected Phase 2D structural path and semantic checker."""

    result = phase2d.process_response_bytes(raw, turn=turn, prior_ledger=prior_ledger, tracked=tracked)
    result["validation"]["validation_version"] = VALIDATION_VERSION
    result["validation"]["validation_sequence"][-1] = "contract_semantic_expectation"
    return result


def construct_four_local_requests(tracked: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Construct four requests using independent per-model witness ledgers."""

    requests, priors = [], {}
    for spec in CALL_SPECS:
        prior = priors.get(spec["model"])
        requests.append(build_request(tracked, spec, prior))
        turn = _turn(tracked, spec)
        witness = phase2d.build_expected_transport_delta(tracked, turn, prior)
        result = process_response_bytes(canonical_json_bytes(witness), turn=turn, prior_ledger=prior, tracked=tracked)
        if result["ledger"] is None or result["validation"]["overall_validation_status"] != "passed":
            raise ProbeError("local Phase 2E semantic witness failed")
        priors[spec["model"]] = result["ledger"]
    return requests, priors


def _identity(spec: Mapping[str, Any], tracked: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes({"spec": spec, "turn_id": _turn(tracked, spec)["turn_id"],
                                               "conversation_key": tracked["chain"]["conversation_key"],
                                               "input_hashes": tracked["input_hashes"]}))


def make_call_log(tracked: Mapping[str, Any]) -> dict[str, Any]:
    """Build the immutable ordered four-call plan."""

    entries = []
    for spec in CALL_SPECS:
        entries.append({**copy.deepcopy(spec), "conversation_key": tracked["chain"]["conversation_key"],
                        "turn_id": _turn(tracked, spec)["turn_id"], "call_identity": _identity(spec, tracked),
                        "state": "planned", "attempt_number": 0, "provider_call_count": 0,
                        "attempted_at_utc": None, "finished_at_utc": None})
    return {"call_log_version": CALL_LOG_VERSION, "prepared_at_utc": base.utc_now(),
            "planned_call_count": 4, "attempted_call_count": 0, "provider_call_count": 0,
            "retry_call_count": 0, "repair_call_count": 0, "fallback_call_count": 0,
            "input_hashes": copy.deepcopy(tracked["input_hashes"]), "entries": entries}


def _validate_log(log: Any, tracked: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(log, dict) or log.get("call_log_version") != CALL_LOG_VERSION or log.get("input_hashes") != tracked["input_hashes"] or len(log.get("entries", [])) != 4:
        raise ProbeError("call log identity differs")
    if any(log.get(key) for key in ("retry_call_count", "repair_call_count", "fallback_call_count")):
        raise ProbeError("call log contains retry, repair, or fallback")
    attempted = 0
    for spec, entry in zip(CALL_SPECS, log["entries"]):
        keys = ("order", "case_id", "turn_index", "model", "dependency_order")
        if any(entry.get(key) != spec[key] for key in keys) or entry.get("call_identity") != _identity(spec, tracked) or entry.get("state") not in base.VALID_STATES:
            raise ProbeError("call log entry differs")
        was_attempted = entry["state"] in {"attempted", "completed", "failed"}
        if entry.get("attempt_number") != int(was_attempted) or entry.get("provider_call_count") != int(was_attempted):
            raise ProbeError("call attempt count differs")
        attempted += int(was_attempted)
    if log.get("planned_call_count") != 4 or log.get("attempted_call_count") != attempted or log.get("provider_call_count") != attempted:
        raise ProbeError("aggregate attempt count differs")
    return log


def _load_log(output: Path, tracked: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_log(base._load_json(output / "call-log.json", "call log"), tracked)


def _call_dir(output: Path, entry: Mapping[str, Any]) -> Path:
    return base._call_directory(output, entry)


def _audit(output: Path, stage: str) -> None:
    output = base._require_private_output(output)
    if stat.S_IMODE(output.stat().st_mode) != 0o700 or any(path.is_symlink() for path in output.rglob("*")):
        raise ProbeError("unsafe private output")
    offline = {ROOT_OFFLINE_FILE} if (output / ROOT_OFFLINE_FILE).exists() else set()
    files = {path.name for path in output.iterdir() if path.is_file()}
    dirs = {path.name for path in output.iterdir() if path.is_dir()}
    if stage == "offline": expected_files, expected_dirs = offline, set()
    elif stage == "prepared": expected_files, expected_dirs = {"call-log.json"} | offline, set()
    elif stage == "final":
        expected_files = {"call-log.json", "result-summary.json", "SHA256SUMS"} | offline
        expected_dirs = {_call_dir(output, entry).name for entry in base._load_json(output / "call-log.json", "call log")["entries"]}
    else: raise ProbeError("unknown private audit stage")
    if files != expected_files or dirs != expected_dirs:
        raise ProbeError(f"{stage} private inventory differs")
    for path in output.rglob("*"):
        if path.is_dir() and stat.S_IMODE(path.stat().st_mode) != 0o700: raise ProbeError("private directory mode differs")
        if path.is_file() and stat.S_IMODE(path.stat().st_mode) != 0o600: raise ProbeError("private file mode differs")
        if path.is_dir() and not {child.name for child in path.iterdir()} <= base.ALLOWED_CALL_FILES: raise ProbeError("unapproved call artifact")


def _prepare_output(path: str | Path) -> Path:
    output = Path(path)
    if not os.path.lexists(output): return base._create_private_output(output)
    output = base._require_private_output(output)
    if stat.S_IMODE(output.stat().st_mode) != 0o700 or any(child.name != ROOT_OFFLINE_FILE or not child.is_file() for child in output.iterdir()):
        raise ProbeError("output is not at the pre-prepare boundary")
    return output


def prepare_run(output: str | Path, *, environment_check: Callable[[], Mapping[str, Any]] = base.verify_pinned_environment,
                local_compiler: Callable[[Sequence[Mapping[str, Any]], Mapping[str, Any]], Mapping[str, Any]] = compile_local_sdk_requests) -> dict[str, Any]:
    """Prepare four SDK requests while transport is denied."""

    environment_check(); tracked = validate_tracked_inputs()
    requests, priors = construct_four_local_requests(tracked); compilation = local_compiler(requests, tracked)
    if set(priors) != {"grok-4.3", "grok-4.6"} or compilation.get("requests_constructed") != 4 or compilation.get("provider_calls_made") != 0 or compilation.get("transport_rpc_invocations") != 0:
        raise ProbeError("offline prepare contract differs")
    output_dir = _prepare_output(output); base._write_private_json(output_dir / "call-log.json", make_call_log(tracked)); _audit(output_dir, "prepared")
    return {"status": "prepared", "output": str(output_dir), "planned_calls": 4, "provider_calls": 0,
            "requests_constructed": 4, "maximum_output_tokens": 8192}


def _no_response(dependency: int | None = None) -> dict[str, Any]:
    value = phase2d._validation_no_response("blocked" if dependency else "failed", dependency)
    value["validation_version"] = VALIDATION_VERSION; value["validation_sequence"][-1] = "contract_semantic_expectation"
    return value


def _valid_prior(output: Path, log: Mapping[str, Any], entry: Mapping[str, Any], tracked: Mapping[str, Any]) -> dict[str, Any] | None:
    dependency = entry.get("dependency_order")
    if dependency is None: return None
    predecessor = log["entries"][int(dependency) - 1]; path = _call_dir(output, predecessor) / "materialised-ledger.json"
    if not path.exists(): return None
    prior = base._load_json(path, "same-model predecessor")
    if predecessor["model"] != entry["model"] or prior.get("target_turn_id") != _turn(tracked, entry)["parent_turn_id"] or prior.get("ledger_sha256") != semantic.phase1.ledger_sha256(prior) or semantic.phase1._jsonschema_errors(prior, tracked["persisted_ledger_schema"]):
        raise ProbeError("same-model predecessor ledger differs")
    return prior


def run_probe(output: str | Path, *, confirm_calls: int | None, transport: Any | None = None,
              environment_check: Callable[[], Mapping[str, Any]] = base.verify_pinned_environment) -> dict[str, Any]:
    """Attempt each eligible call once and block only dependent 4.3 turns."""

    if confirm_calls != 4: raise ProbeError("--run requires --confirm-calls 4")
    output_dir = base._require_private_output(output)
    with base._exclusive_execution_lock(output_dir):
        environment_check(); tracked = validate_tracked_inputs(); log = _load_log(output_dir, tracked); _audit(output_dir, "prepared")
        if os.path.lexists(output_dir / "SHA256SUMS") or not any(item["state"] == "planned" for item in log["entries"]): raise ProbeError("run is finalised or exhausted")
        active, owned = (XaiTransport(), True) if transport is None else (transport, False)
        try:
            for entry in log["entries"]:
                if entry["state"] != "planned": continue
                prior = _valid_prior(output_dir, log, entry, tracked); dependency = entry.get("dependency_order"); call_dir = _call_dir(output_dir, entry); base._ensure_private_directory(call_dir)
                if dependency is not None and prior is None:
                    entry.update(state="blocked", blocked_by_order=dependency, finished_at_utc=base.utc_now()); base._write_private_json(output_dir / "call-log.json", log)
                    base._write_private_json(call_dir / "validation.json", _no_response(int(dependency))); base._write_private_json(call_dir / "usage.json", phase2d._usage(None, None)); continue
                entry.update(state="attempted", attempt_number=1, provider_call_count=1, attempted_at_utc=base.utc_now()); log["attempted_call_count"] += 1; log["provider_call_count"] += 1; base._write_private_json(output_dir / "call-log.json", log)
                turn = _turn(tracked, entry); began = time.monotonic()
                try: observation = active.sample(copy.deepcopy(entry), build_request(tracked, entry, prior), {"turn": copy.deepcopy(turn), "prior_ledger": copy.deepcopy(prior)})
                except Exception as exc:
                    validation = _no_response(); validation["provider_error_type"] = type(exc).__name__[:128]
                    base._write_private_json(call_dir / "validation.json", validation); base._write_private_json(call_dir / "usage.json", phase2d._usage(None, round(time.monotonic() - began, 6))); state = "failed"
                else:
                    latency = round(time.monotonic() - began, 6)
                    try:
                        raw = observation.raw_text.encode("utf-8", "strict"); base._write_private_bytes(call_dir / "raw-response.txt", raw)
                        result = process_response_bytes(raw, turn=turn, prior_ledger=prior, tracked=tracked); phase2d._persist(call_dir, raw, result)
                        base._write_private_json(call_dir / "usage.json", phase2d._usage(observation, latency)); state = "completed" if result["validation"]["overall_validation_status"] == "passed" else "failed"
                    except Exception as exc:
                        validation = _no_response(); validation.update(server_acceptance_status="accepted", structural_validity_status="failed", provider_error_type=f"local_validation_{type(exc).__name__}"[:128])
                        base._write_private_json(call_dir / "validation.json", validation); base._write_private_json(call_dir / "usage.json", phase2d._usage(observation, latency)); state = "failed"
                entry.update(state=state, finished_at_utc=base.utc_now()); base._write_private_json(output_dir / "call-log.json", log)
        finally:
            if owned:
                try: active.close()
                except Exception: pass
        summary = build_result_summary(output_dir, _load_log(output_dir, tracked), tracked)
        base._write_private_json(output_dir / "result-summary.json", summary); base.write_checksums(output_dir); _audit(output_dir, "final"); return summary


def _token(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else int(value) if isinstance(value, str) and value.isdigit() else 0


def build_result_summary(output: Path, log: Mapping[str, Any], tracked: Mapping[str, Any]) -> dict[str, Any]:
    """Summarise outcomes, usage, latency, and fixed research boundaries."""

    calls, token_fields = [], ("prompt_tokens", "prompt_text_tokens", "cached_prompt_tokens", "cached_prompt_text_tokens", "reasoning_tokens", "completion_tokens", "total_tokens")
    totals, latency_total, latency_count = {key: 0 for key in token_fields}, 0.0, 0
    for entry in log["entries"]:
        call_dir = _call_dir(output, entry); validation = base._load_json(call_dir / "validation.json", "validation"); usage_record = base._load_json(call_dir / "usage.json", "usage"); usage = usage_record.get("usage", {})
        for key in totals: totals[key] += _token(usage.get(key)) if isinstance(usage, Mapping) else 0
        latency = usage_record.get("latency_seconds")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool): latency_total += latency; latency_count += 1
        finish = usage_record.get("finish_reason")
        calls.append({"order": entry["order"], "turn_id": entry["turn_id"], "turn_index": entry["turn_index"], "model": entry["model"], "state": entry["state"], "provider_call_count": entry["provider_call_count"],
                      "server_acceptance_status": validation.get("server_acceptance_status"), "structural_validity_status": validation.get("structural_validity_status"), "semantic_expectation_status": validation.get("semantic_expectation_status"),
                      "transport_resolution_status": validation.get("evidence_resolution", {}).get("status"), "canonical_validation_status": validation.get("canonical_semantic_schema", {}).get("status"),
                      "semantic_reference_status": validation.get("semantic_reference", {}).get("status"), "materialisation_status": validation.get("deterministic_materialisation", {}).get("status"),
                      "persisted_ledger_status": validation.get("persisted_ledger", {}).get("status"), "materialiser_status": validation.get("materialiser_status"), "finish_reason": finish,
                      "reached_output_token_ceiling": finish == "REASON_MAX_LEN" or (isinstance(usage, Mapping) and _token(usage.get("completion_tokens")) >= 8192), "latency_seconds": latency, "usage": copy.deepcopy(usage)})
    count = lambda field, expected="passed": sum(row[field] == expected for row in calls)  # noqa: E731
    return {"summary_version": SUMMARY_VERSION, "planned_call_count": 4, "attempted_call_count": log["attempted_call_count"], "provider_call_count": log["provider_call_count"],
            "completed_call_count": count("state", "completed"), "failed_call_count": count("state", "failed"), "blocked_call_count": count("state", "blocked"),
            "retry_call_count": 0, "repair_call_count": 0, "fallback_call_count": 0, "server_acceptance_success_count": count("server_acceptance_status", "accepted"),
            "structural_success_count": count("structural_validity_status"), "transport_success_count": count("transport_resolution_status"), "canonical_success_count": count("canonical_validation_status"),
            "semantic_reference_success_count": count("semantic_reference_status"), "materialisation_success_count": count("materialisation_status"), "persisted_ledger_success_count": count("persisted_ledger_status"),
            "semantic_expectation_success_count": count("semantic_expectation_status"), "output_ceiling_reached_count": sum(row["reached_output_token_ceiling"] for row in calls),
            "maximum_output_tokens": 8192, "token_totals": totals, "latency": {"count": latency_count, "total_seconds": round(latency_total, 6)}, "calls": calls,
            "input_hashes": copy.deepcopy(tracked["input_hashes"]), "offline_recheck_recorded": (output / ROOT_OFFLINE_FILE).exists(), "real_conversation_records_read": 0,
            "held_out_records_read": 0, "model_winner_selected": False, "phase2a_pilot_rerun": False, "production_touched": False, "merged": False, "deployed": False}


def _source_run(source_run: str | Path | None) -> Path:
    if source_run is not None: return base._require_private_output(source_run)
    matches = sorted(path for path in PHASE2D_RUN_PARENT.glob(PHASE2D_RUN_GLOB) if path.is_dir() and not path.is_symlink())
    if len(matches) != 1: raise ProbeError("expected exactly one completed Phase 2D private run")
    return base._require_private_output(matches[0])


def _phase2d_entries(source: Path) -> dict[int, dict[str, Any]]:
    log = base._load_json(source / "call-log.json", "Phase 2D call log")
    if not isinstance(log, dict) or log.get("call_log_version") != phase2d.CALL_LOG_VERSION or log.get("provider_call_count") != 5 or any(log.get(key) for key in ("retry_call_count", "repair_call_count", "fallback_call_count")) or len(log.get("entries", [])) != 6:
        raise ProbeError("Phase 2D call log differs")
    entries = {int(item["order"]): item for item in log["entries"]}; expected = {1: ("grok-4.3", 0), 2: ("grok-4.6", 0), 3: ("grok-4.3", 1), 4: ("grok-4.6", 1), 5: ("grok-4.3", 2), 6: ("grok-4.6", 2)}
    if any((entries.get(order, {}).get("model"), entries.get(order, {}).get("turn_index")) != value for order, value in expected.items()) or entries[5].get("state") != "blocked":
        raise ProbeError("Phase 2D call identities differ")
    return entries


def _historical_prior(source: Path, entries: Mapping[int, Mapping[str, Any]], order: int | None, tracked: Mapping[str, Any]) -> dict[str, Any] | None:
    if order is None: return None
    prior = base._load_json(_call_dir(source, entries[order]) / "materialised-ledger.json", "historical predecessor")
    if prior.get("ledger_sha256") != semantic.phase1.ledger_sha256(prior) or semantic.phase1._jsonschema_errors(prior, tracked["persisted_ledger_schema"]): raise ProbeError("historical predecessor is invalid")
    return prior


def _rebind(raw: bytes) -> tuple[bytes, dict[str, str]]:
    parsed = strict_json_loads(raw)
    original = {"schema_version": str(parsed.get("schema_version")), "canonical_schema_version": str(parsed.get("canonical_schema_version"))} if isinstance(parsed, dict) else {}
    if original != {"schema_version": HISTORICAL_TRANSPORT_VERSION, "canonical_schema_version": HISTORICAL_CANONICAL_VERSION}: raise ProbeError("historical response versions differ")
    rebound = copy.deepcopy(parsed); rebound["schema_version"] = evidence.TRANSPORT_SCHEMA_VERSION; rebound["canonical_schema_version"] = evidence.CANONICAL_SCHEMA_VERSION
    return canonical_json_bytes(rebound), original


def _classification(result: Mapping[str, Any]) -> str:
    validation = result["validation"]
    if validation.get("overall_validation_status") == "passed": return "passed"
    errors = [str(error) for stage in ("provider_transport_schema", "canonical_semantic_schema", "deterministic_materialisation") for error in validation.get(stage, {}).get("errors", [])]
    if any("speaker_committed_proposition_missing_commitment_record" in error for error in errors): return "speaker_commitment_consistency_rejected"
    if validation.get("provider_transport_schema", {}).get("status") == "failed" or validation.get("canonical_semantic_schema", {}).get("status") == "failed": return "semantic_delta_schema_rejected"
    return "unexpected_failure"


def build_offline_recheck(tracked: Mapping[str, Any], *, source_run: str | Path | None = None) -> dict[str, Any]:
    """Reclassify five saved observations with their historical predecessors."""

    source = _source_run(source_run); checksum = base.verify_checksums(source); entries = _phase2d_entries(source); rows = []
    for order in PHASE2D_RESPONSE_ORDERS:
        entry = entries[order]; raw = base._read_regular_bytes(_call_dir(source, entry) / "raw-response.txt", "Phase 2D response"); rebound, original = _rebind(raw)
        prior_order = PHASE2D_PRIOR_ORDERS[order]; result = process_response_bytes(rebound, turn=tracked["chain"]["turns"][entry["turn_index"]], prior_ledger=_historical_prior(source, entries, prior_order, tracked), tracked=tracked)
        classification = _classification(result); validation = result["validation"]
        if classification != PHASE2D_EXPECTED[order]: raise ProbeError(f"corrected Phase 2D classification differs at order {order}: {classification}")
        errors = [error for stage in ("provider_transport_schema", "canonical_semantic_schema", "deterministic_materialisation") for error in validation.get(stage, {}).get("errors", [])]
        rows.append({"source_order": order, "model": entry["model"], "turn_index": entry["turn_index"], "historical_predecessor_order": prior_order,
                     "source_raw_sha256": sha256_bytes(raw), "reprocessed_input_sha256": sha256_bytes(rebound), "original_versions": original,
                     "rebound_versions": {"schema_version": evidence.TRANSPORT_SCHEMA_VERSION, "canonical_schema_version": evidence.CANONICAL_SCHEMA_VERSION},
                     "corrected_classification": classification, "expected_classification": PHASE2D_EXPECTED[order], "structural_validity_status": validation.get("structural_validity_status"),
                     "semantic_expectation_status": validation.get("semantic_expectation_status"), "materialiser_status": validation.get("materialiser_status"),
                     "provider_transport_schema_status": validation.get("provider_transport_schema", {}).get("status"), "canonical_semantic_schema_status": validation.get("canonical_semantic_schema", {}).get("status"),
                     "deterministic_materialisation_status": validation.get("deterministic_materialisation", {}).get("status"), "errors": [str(value).replace("\n", " ")[:512] for value in errors[:32]]})
    return {"offline_recheck_version": OFFLINE_RECHECK_VERSION, "status": "passed", "source_run_name": source.name,
            "source_checksum_manifest_sha256": sha256_bytes(base._read_regular_bytes(source / "SHA256SUMS", "Phase 2D checksums")), "source_checksum_status": checksum["status"],
            "saved_response_count": 5, "provider_calls_made": 0, "historical_files_modified": False, "root_version_rebinding_only": True,
            "expected_interpretations_matched": True, "classifications": rows, "input_hashes": copy.deepcopy(tracked["input_hashes"])}


def offline_recheck(output: str | Path, *, source_run: str | Path | None = None,
                    environment_check: Callable[[], Mapping[str, Any]] = base.verify_pinned_environment) -> dict[str, Any]:
    """Record the corrected offline interpretation in the one private output."""

    environment_check(); tracked = validate_tracked_inputs(); output_path = Path(output)
    output_dir = base._create_private_output(output_path) if not os.path.lexists(output_path) else base._require_private_output(output_path)
    with base._exclusive_execution_lock(output_dir):
        if stat.S_IMODE(output_dir.stat().st_mode) != 0o700 or os.path.lexists(output_dir / "SHA256SUMS") or any(child.name not in {"call-log.json", ROOT_OFFLINE_FILE} or not child.is_file() for child in output_dir.iterdir()): raise ProbeError("output is not at the offline-recheck boundary")
        report = build_offline_recheck(tracked, source_run=source_run); path = output_dir / ROOT_OFFLINE_FILE
        if path.exists() and base._read_regular_bytes(path, "offline recheck") != pretty_json_bytes(report): raise ProbeError("saved offline recheck differs")
        if not path.exists(): base._write_private_json(path, report)
        _audit(output_dir, "prepared" if (output_dir / "call-log.json").exists() else "offline"); return report


def _compare(path: Path, expected: Any, label: str) -> None:
    if expected is None:
        if os.path.lexists(path): raise ProbeError(f"unexpected {label}")
    elif base._read_regular_bytes(path, label) != pretty_json_bytes(expected): raise ProbeError(f"saved {label} differs")


def _replay_prior(output: Path, log: Mapping[str, Any], entry: Mapping[str, Any], tracked: Mapping[str, Any]) -> dict[str, Any] | None:
    """Load only the declared dependency ledger, never a stale model prior."""

    return _valid_prior(output, log, entry, tracked)


def verify_run(output: str | Path, *, source_run: str | Path | None = None,
               environment_check: Callable[[], Mapping[str, Any]] = base.verify_pinned_environment) -> dict[str, Any]:
    """Replay offline and live artifacts without credentials or provider calls."""

    output_dir = base._require_private_output(output)
    with base._exclusive_execution_lock(output_dir):
        environment_check(); tracked = validate_tracked_inputs(); before = base._read_regular_bytes(output_dir / "call-log.json", "call log"); log = _load_log(output_dir, tracked); offline_count = 0
        if (output_dir / ROOT_OFFLINE_FILE).exists():
            expected_offline = build_offline_recheck(tracked, source_run=source_run); _compare(output_dir / ROOT_OFFLINE_FILE, expected_offline, "offline recheck"); offline_count = 5
        if all(entry["state"] == "planned" for entry in log["entries"]):
            _audit(output_dir, "prepared"); return {"status": "passed_prepared", "provider_calls_made": 0, "saved_responses_reprocessed": 0, "offline_responses_reprocessed": offline_count, "api_key_required": False}
        checksum = base.verify_checksums(output_dir); _audit(output_dir, "final"); reprocessed = 0
        for entry in log["entries"]:
            call_dir = _call_dir(output_dir, entry); prior = _replay_prior(output_dir, log, entry, tracked); raw_path = call_dir / "raw-response.txt"
            if raw_path.exists():
                result = process_response_bytes(base._read_regular_bytes(raw_path, "raw response"), turn=_turn(tracked, entry), prior_ledger=prior, tracked=tracked)
                for key, name, label in (("parsed", "parsed-transport.json", "parsed transport"), ("canonical", "resolved-canonical-delta.json", "canonical delta"), ("ledger", "materialised-ledger.json", "materialised ledger"), ("validation", "validation.json", "validation")): _compare(call_dir / name, result[key], label)
                expected_state = "completed" if result["validation"]["overall_validation_status"] == "passed" else "failed"
                if entry["state"] != expected_state: raise ProbeError("call state differs from replay")
                reprocessed += 1
            elif entry["state"] == "blocked":
                if prior is not None: raise ProbeError("blocked call has its declared predecessor")
                _compare(call_dir / "validation.json", _no_response(int(entry["dependency_order"])), "blocked validation")
            elif entry["state"] == "failed":
                if base._load_json(call_dir / "validation.json", "provider failure").get("server_acceptance_status") != "failed": raise ProbeError("raw-less failure differs")
            else: raise ProbeError("final run contains nonterminal call")
            if set(base._load_json(call_dir / "usage.json", "usage")) != {"provider_response_received", "returned_model_id", "finish_reason", "latency_seconds", "usage"}: raise ProbeError("usage shape differs")
        expected_summary = build_result_summary(output_dir, log, tracked)
        if base._read_regular_bytes(output_dir / "result-summary.json", "summary") != pretty_json_bytes(expected_summary) or base._read_regular_bytes(output_dir / "call-log.json", "call log") != before: raise ProbeError("summary or call log differs")
        final = base.verify_checksums(output_dir)
        return {"status": "passed", "provider_calls_made": 0, "saved_responses_reprocessed": reprocessed, "offline_responses_reprocessed": offline_count,
                "api_key_required": False, "call_log_unchanged": True, "checksum_status": final["status"], "checked_files": checksum["checked_files"]}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 2E four-call contract retest"); modes = parser.add_mutually_exclusive_group(required=True)
    for name in ("offline-recheck", "prepare", "run", "verify"): modes.add_argument(f"--{name}", action="store_true")
    parser.add_argument("--output", type=Path, required=True); parser.add_argument("--confirm-calls", type=int); return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch exactly one offline recheck, prepare, run, or verify mode."""

    args = _parser().parse_args(argv)
    try:
        if args.offline_recheck:
            if args.confirm_calls is not None: raise ProbeError("--offline-recheck does not accept --confirm-calls")
            result = offline_recheck(args.output)
        elif args.prepare:
            if args.confirm_calls is not None: raise ProbeError("--prepare does not accept --confirm-calls")
            result = prepare_run(args.output)
        elif args.run: result = run_probe(args.output, confirm_calls=args.confirm_calls)
        else:
            if args.confirm_calls is not None: raise ProbeError("--verify does not accept --confirm-calls")
            result = verify_run(args.output)
    except ProbeError as exc:
        print(f"phase2e_retest_error: {exc}", file=sys.stderr); return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
