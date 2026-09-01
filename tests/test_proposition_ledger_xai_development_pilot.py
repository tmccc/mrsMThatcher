from __future__ import annotations

import copy
import importlib.util
import json
import os
import random
import socket
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from tools import proposition_ledger_semantic_delta as semantic
from tools import proposition_ledger_xai_development_pilot as pilot
from tools import proposition_ledger_xai_live_probe as live_probe
from tools import proposition_ledger_xai_provider_preflight as preflight


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_DIR / "tools/proposition_ledger_xai_development_pilot.py"

STRESSOR_LABELS = (
    "existence versus security",
    "valid distinction",
    "counterfactual",
    "compound allegation",
    "explicit correction",
    "account clarification",
    "rhetorical expressive",
    "healthy sustained debate",
)


def _turns(case_number: int, count: int = 3) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for index in range(count):
        turn_id = f"source-turn-{case_number:02d}-{index:02d}"
        turns.append(
            {
                "turn_id": turn_id,
                "turn_index": index,
                "parent_turn_id": turns[-1]["turn_id"] if turns else None,
                "post_id": f"source-post-{case_number:02d}-{index:02d}",
                "text": f"Synthetic visible turn {case_number}/{index} — café.",
                "publication_status": "observed",
                "timestamp": f"2026-08-{20 + case_number:02d}T12:{index:02d}:00Z",
                "author_role": "account" if index % 2 == 0 else "contributor",
                "author_key": (
                    "synthetic-account"
                    if index % 2 == 0
                    else f"raw-contributor-{case_number:02d}"
                ),
            }
        )
    return turns


def _record(
    case_number: int,
    *,
    count: int = 3,
    stressor: str | None = None,
    exposure: Sequence[str] = ("calibration",),
    grade: str = "A",
    group: str | None = None,
    prospective: bool | None = None,
) -> dict[str, Any]:
    turns = _turns(case_number, count)
    if group is not None:
        turns[-1]["author_role"] = "contributor"
        turns[-1]["author_key"] = group
    if prospective is None:
        prospective = case_number % 2 == 0
    value = {
        "calibration_case_id": f"synthetic-case-{case_number:02d}",
        "conversation_key": f"synthetic-conversation-{case_number:02d}",
        "target_turn_id": turns[-1]["turn_id"],
        "target_post_id": f"source-target-{case_number:02d}",
        "transcript_prefix": turns,
        "source_grade": grade,
        "reconstruction_grade": grade,
        "stability_status": "frozen_historical",
        "frozen_historical_or_quiescent_at_cutoff": True,
        "complete_target_ancestry": True,
        "target_author_identity_status": "available",
        "target_author_conflict_count": 0,
        "exposure_categories": list(exposure),
        "prior_exposure_reasons": ["synthetic prior review"],
        "preliminary_within_family_held_out_eligibility": False,
        "effective_exposure_status": "directly_exposed",
        "conversation_exposure_status": "directly_exposed",
        "target_exposure_status": "directly_exposed",
        "calibration_container_all_grade_a": True,
        "calibration_container_all_previously_exposed": True,
        "intended_schema_stressors": [
            stressor or STRESSOR_LABELS[(case_number - 1) % len(STRESSOR_LABELS)]
        ],
        "source_provenance": ([{"family": "prospective-v4"}] if prospective else []),
        "record_sha256": f"{case_number:064x}",
        # Deliberate secrets outside the selected prefix.  They must never enter a
        # provider payload or the localised case manifest.
        "future_turns": [
            {
                "turn_id": f"forbidden-future-{case_number}",
                "text": f"FORBIDDEN_FUTURE_TEXT_{case_number}",
            }
        ],
        "historical_reply_after_target": f"FORBIDDEN_HISTORICAL_REPLY_{case_number}",
        "production_outcome": f"FORBIDDEN_PRODUCTION_OUTCOME_{case_number}",
        "prior_audit_label": f"FORBIDDEN_AUDIT_LABEL_{case_number}",
    }
    return value


def _eligible_records(*, count_per_case: int = 3) -> list[dict[str, Any]]:
    return [
        _record(
            index,
            count=count_per_case,
            stressor=STRESSOR_LABELS[index - 1],
            group=f"raw-contributor-group-{index:02d}",
            prospective=index in {2, 4, 6, 8},
        )
        for index in range(1, 9)
    ]


def _source_integrity() -> dict[str, Any]:
    return {
        "artifact_evidence": "frozen input",
        "frozen_source_manifest_sha256": pilot.EXPECTED_SOURCE_MANIFEST_SHA256,
        "prospective_batch_manifest_sha256": (
            pilot.EXPECTED_PROSPECTIVE_MANIFEST_SHA256
        ),
        "calibration_manifest_sha256": "3" * 64,
        "calibration_index_semantic_sha256": "4" * 64,
        "calibration_index_file_sha256": "5" * 64,
        "calibration_records_sha256": "6" * 64,
        "target_prefix_metadata_sha256": "e" * 64,
        "cutoff": pilot.FROZEN_CUTOFF,
        "selection_policy_version": pilot.SELECTION_POLICY_VERSION,
        "development_stability_policy_version": (
            pilot.DEVELOPMENT_STABILITY_POLICY_VERSION
        ),
        "held_out_seal_policy_version": pilot.HELD_OUT_SEAL_POLICY_VERSION,
        "unexposed_or_held_out_source_files_opened": 0,
    }


def _metadata_row(
    label: str,
    *,
    effective: str = "exposed",
    conversation: str | None = None,
    target: str | None = None,
    held_out: bool = False,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "conversation_key": conversation or f"metadata-conversation-{label}",
        "target_turn_id": target or f"metadata-target-{label}",
        "target_sequence_class": "synthetic-target-sequence",
        "source_family": "synthetic-benchmark",
        "prefix_turn_count": 3,
        "reconstruction_grade": "A",
        "complete_target_ancestry": True,
        "stability_status": "frozen_historical",
        "activity_status_at_frozen_cutoff": "quiescent",
        "effective_exposure_status": effective,
        "conversation_exposure_status": effective,
        "target_exposure_status": effective,
        "author_group_exposure_categories": (
            ["structurally_mined_only"]
            if effective == "structurally_mined_only"
            else ["prior_human_review"]
        ),
        "preliminary_within_family_held_out_eligibility": held_out,
        "preliminary_held_out_eligibility": False,
        "target_author_identity_status": "available",
        "review_candidate_principal_conflict_count": 0,
        "within_family_author_group_key": f"metadata-group-{label}",
    }
    row["row_sha256"] = pilot.value_sha256(row)
    return row


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(pilot.canonical_json_bytes(row) + b"\n" for row in rows)


def _tracked() -> dict[str, Any]:
    canonical_schema = json.loads(
        pilot.CANONICAL_SCHEMA_PATH.read_text(encoding="utf-8")
    )
    provider_schema, _transformations = preflight.transform_provider_schema(
        canonical_schema
    )
    return {
        "protocol": {"synthetic": True},
        "system_prompt": "Synthetic model-neutral incremental prompt.",
        "rubric": {"synthetic": True},
        "canonical_schema": canonical_schema,
        "provider_schema": provider_schema,
        "hashes": {
            "canonical_schema_sha256": pilot.EXPECTED_CANONICAL_SCHEMA_SHA256,
            "provider_schema_sha256": pilot.EXPECTED_PROVIDER_SCHEMA_SHA256,
            "system_prompt_sha256": "7" * 64,
            "review_rubric_sha256": "8" * 64,
            "protocol_sha256": "9" * 64,
        },
    }


@pytest.fixture(scope="module")
def schemas() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    canonical = json.loads(pilot.CANONICAL_SCHEMA_PATH.read_text(encoding="utf-8"))
    provider, _ = preflight.transform_provider_schema(canonical)
    persisted = json.loads(
        pilot.PERSISTED_LEDGER_SCHEMA_PATH.read_text(encoding="utf-8")
    )
    return canonical, provider, persisted


@pytest.fixture()
def selected_cases() -> list[dict[str, Any]]:
    return pilot.select_development_cases(_eligible_records())


@pytest.fixture()
def local_cases(selected_cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cases, _mapping = pilot._localise_cases(selected_cases)
    return cases


def _freeze(turn_count: int = 24) -> dict[str, Any]:
    tracked = _tracked()
    original = pilot._source_commit_for_freeze
    pilot._source_commit_for_freeze = lambda: pilot.SOURCE_COMMIT
    try:
        return pilot.build_protocol_freeze("a" * 64, turn_count, tracked)
    finally:
        pilot._source_commit_for_freeze = original


def _noop_delta(
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
    *,
    abstentions: Sequence[str] = (),
    warnings: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    return {
        "schema_version": semantic.SEMANTIC_DELTA_SCHEMA_VERSION,
        "conversation_key": case["pilot_conversation_id"],
        "target_turn_id": turn["turn_id"],
        "as_of_turn_index": turn["turn_index"],
        "prior_ledger_reference": (
            None
            if prior_ledger is None
            else {
                "ledger_id": prior_ledger["ledger_id"],
                "as_of_turn_index": prior_ledger["as_of_turn_index"],
            }
        ),
        "new_propositions": [],
        "proposition_updates": [],
        "new_proposition_groups": [],
        "proposition_group_updates": [],
        "new_issue_states": [],
        "issue_state_updates": [],
        "commitment_changes": [],
        "obligation_changes": [],
        "new_relations": [],
        "answer_target_changes": [],
        "rejected_answer_target_changes": [],
        "repair_records": [],
        "resolved_items": [],
        "extraction_status": "complete",
        "abstentions": list(abstentions),
        "unsupported_inferences_rejected": 0,
        "warnings": copy.deepcopy(list(warnings)),
    }


def _delta_with_proposition(
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
) -> dict[str, Any]:
    delta = _noop_delta(case, turn, prior_ledger)
    text = turn["text"]
    span = {
        "turn_id": turn["turn_id"],
        "start_char": 0,
        "end_char": len(text),
        "exact_text": text,
    }
    delta["new_propositions"] = [
        {
            "local_ref": "new-proposition-1",
            "canonical_text": text,
            "speaker_or_attributor": {
                "kind": "speaker",
                "participant_id": turn["speaker"]["participant_id"],
                "attributed_participant_id": None,
            },
            "exact_evidence_spans": [span],
            "original_language": "en",
            "speech_act": "assertion",
            "proposition_kind": "descriptive",
            "polarity": "positive",
            "modality": {"type": "none", "strength": "none"},
            "quantification": {"type": "none", "surface_marker": None},
            "temporal_scope": {
                "type": "present",
                "start": None,
                "end": None,
                "surface_marker": None,
            },
            "epistemic_status": "asserted",
            "commitment_status": "speaker_committed",
            "lifecycle_status": "live",
            "proposition_group_ref": None,
            "derivation": {
                "kind": "direct_span",
                "source_proposition_refs": [],
                "normalisation_note": None,
            },
            "confidence": 1.0,
            "uncertainty_reason": None,
        }
    ]
    return delta


def _raw(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _process(
    value: Mapping[str, Any] | bytes,
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> dict[str, Any]:
    canonical, provider, persisted = schemas
    return pilot.process_response_bytes(
        value if isinstance(value, bytes) else _raw(value),
        case=case,
        turn=turn,
        prior_ledger=prior,
        canonical_schema=canonical,
        provider_schema=provider,
        persisted_ledger_schema=persisted,
    )


class _ProtectedRecord(dict[str, Any]):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.text_accesses = 0

    def get(self, key: str, default: Any = None) -> Any:
        if key == "transcript_prefix":
            self.text_accesses += 1
            raise AssertionError("protected transcript content was accessed")
        return super().get(key, default)


class _RpcCode:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeRpcError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__("synthetic provider failure")
        self._code = code
        self._detail = detail

    def code(self) -> _RpcCode:
        return _RpcCode(self._code)

    def details(self) -> str:
        return self._detail


class _DynamicTransport:
    def __init__(
        self,
        *,
        fail_at: int | None = None,
        failure: BaseException | None = None,
        invalid_at: int | None = None,
        warning_at: int | None = None,
    ) -> None:
        self.fail_at = fail_at
        self.failure = failure
        self.invalid_at = invalid_at
        self.warning_at = warning_at
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def sample(
        self, profile: Mapping[str, Any], request_context: Mapping[str, Any]
    ) -> pilot.ProviderObservation:
        payload = json.loads(str(request_context["user_payload"]))
        call_number = len(self.calls)
        self.calls.append(
            {
                "profile": copy.deepcopy(dict(profile)),
                "payload": copy.deepcopy(payload),
                "request": copy.deepcopy(request_context["request_representation"]),
            }
        )
        if call_number == self.fail_at:
            assert self.failure is not None
            raise self.failure
        if call_number == self.invalid_at:
            raw_text = "not strict JSON"
        else:
            prior = payload["validated_prior_persisted_ledger"]
            delta = {
                "schema_version": semantic.SEMANTIC_DELTA_SCHEMA_VERSION,
                "conversation_key": payload["pilot_local_conversation_key"],
                "target_turn_id": payload["pilot_local_current_turn_id"],
                "as_of_turn_index": payload["turn_index"],
                "prior_ledger_reference": (
                    None
                    if prior is None
                    else {
                        "ledger_id": prior["ledger_id"],
                        "as_of_turn_index": prior["as_of_turn_index"],
                    }
                ),
                "new_propositions": [],
                "proposition_updates": [],
                "new_proposition_groups": [],
                "proposition_group_updates": [],
                "new_issue_states": [],
                "issue_state_updates": [],
                "commitment_changes": [],
                "obligation_changes": [],
                "new_relations": [],
                "answer_target_changes": [],
                "rejected_answer_target_changes": [],
                "repair_records": [],
                "resolved_items": [],
                "extraction_status": "complete",
                "abstentions": [],
                "unsupported_inferences_rejected": 0,
                "warnings": [],
            }
            if call_number == self.warning_at:
                text = payload["exact_current_visible_text"]
                delta["warnings"] = [
                    {
                        "local_ref": "new-warning-1",
                        "code": "other",
                        "message": "Synthetic diagnostic warning.",
                        "severity": "warning",
                        "exact_evidence_spans": [
                            {
                                "turn_id": payload["pilot_local_current_turn_id"],
                                "start_char": 0,
                                "end_char": len(text),
                                "exact_text": text,
                            }
                        ],
                    }
                ]
            raw_text = _raw(delta).decode("utf-8")
        return pilot.ProviderObservation(
            raw_text=raw_text,
            returned_model_id=str(profile["model"]),
            provider_response_id=f"synthetic-response-{call_number:03d}",
            finish_reason="stop",
            usage={
                "prompt_tokens": 100 + call_number,
                "cached_prompt_tokens": 10,
                "reasoning_tokens": 5,
                "completion_tokens": 20,
                "total_tokens": 120 + call_number,
                "cost_in_usd_ticks": 7,
            },
        )

    def close(self) -> None:
        self.closed = True


def _prepare_synthetic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str = "run",
    *,
    key: str = "synthetic-key-never-sent",
) -> Path:
    monkeypatch.setenv("XAI_API_KEY", key)
    selected = pilot.select_development_cases(_eligible_records())
    synthetic_sidecar = []
    for case in selected:
        sidecar = {
            "artifact_evidence": "frozen input",
            "source_metadata_row_sha256": case["record_sha256"],
            "conversation_key": case["conversation_key"],
            "target_turn_id": case["turns"][-1]["turn_id"],
            "target_sequence_class": "synthetic-exposed-target",
            "source_family": case["source_family"],
            "prefix_turn_count": case["turn_count"],
            "reconstruction_grade": "A",
            "complete_target_ancestry": True,
            "stability_status": case["stability_status"],
            "activity_status_at_frozen_cutoff": "quiescent",
            "effective_exposure_status": "exposed",
            "conversation_exposure_status": "exposed",
            "target_exposure_status": "exposed",
            "exposure_categories": ["calibration"],
            "preliminary_within_family_held_out_eligibility": False,
            "preliminary_held_out_eligibility": False,
            "target_author_identity_status": "available",
            "target_author_conflict_count": 0,
            "within_family_author_group_key": case[
                "contributor_group_source_key"
            ],
            "administrative_protected_metadata_scan_only": False,
        }
        sidecar["sidecar_row_sha256"] = pilot.value_sha256(sidecar)
        synthetic_sidecar.append(sidecar)
    synthetic_seal_audit = {
        "artifact_evidence": "deterministic derivation",
        "policy_version": pilot.HELD_OUT_SEAL_POLICY_VERSION,
        "source_metadata_file_sha256": "e" * 64,
        "source_metadata_rows_scanned": 10,
        "protected_metadata_rows_scanned_for_administration": 2,
        "protected_metadata_rows_rendered_to_operator": 0,
        "protected_rows_written_to_development_sidecar": 0,
        "protected_transcript_records_opened": 0,
        "protected_transcript_text_bytes_read": 0,
        "protected_source_mapping_records_opened": 0,
        "protected_provider_payload_count": 0,
        "protected_model_output_count": 0,
        "protected_human_review_count": 0,
        "directly_exposed_rows_written_to_sidecar": 8,
        "structurally_mined_only_rows_excluded": 1,
        "genuinely_unexposed_rows_excluded": 0,
        "preliminarily_held_out_rows_excluded": 1,
        "substantive_seal_breached": False,
        "prior_protected_metadata_administrative_scan": True,
        "prior_scan_report": "synthetic-early-stop-report.md",
        "prior_protected_metadata_rows_known_minimum": 2,
        "prior_genuinely_unexposed_metadata_row_count": "unknown",
        "prior_protected_transcript_text_bytes_read": 0,
        "prior_protected_provider_payload_count": 0,
        "prior_protected_model_output_count": 0,
        "prior_substantive_seal_breached": False,
    }
    monkeypatch.setattr(
        pilot,
        "_prepare_selection_v2",
        lambda: (
            copy.deepcopy(selected),
            _source_integrity(),
            copy.deepcopy(synthetic_sidecar),
            copy.deepcopy(synthetic_seal_audit),
        ),
    )
    monkeypatch.setattr(pilot, "_tracked_input_record", _tracked)
    monkeypatch.setattr(pilot, "_source_commit_for_freeze", lambda: pilot.SOURCE_COMMIT)
    output = tmp_path / name
    result = pilot.prepare_run(output)
    assert result["provider_calls"] == 0
    return output


def _patch_live_preconditions(
    monkeypatch: pytest.MonkeyPatch,
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    monkeypatch.setattr(pilot, "_verify_pushed_clean_freeze", lambda _prepared: "d" * 40)
    monkeypatch.setattr(
        pilot,
        "_record_phase14_carry_forward",
        lambda _output: {
            "diagnostic_id": "phase1_4_grok_4_3_carry_forward_diagnostic",
            "exact_failed_invariants": ["synthetic_preexisting_invariant"],
            "recurrence_result": "phase1_4_failure_pattern_not_assessable",
        },
    )
    monkeypatch.setattr(pilot.live_probe, "_remove_unrelated_credentials", lambda: None)
    monkeypatch.setattr(
        pilot.live_probe,
        "verify_pinned_environment",
        lambda: {"status": "passed", "synthetic": True},
    )
    monkeypatch.setattr(pilot, "_load_schemas", lambda: schemas)


def _patch_synthetic_git(
    monkeypatch: pytest.MonkeyPatch,
    overrides: Mapping[tuple[str, ...], str] | None = None,
) -> tuple[str, list[tuple[str, ...]]]:
    """Install a complete read-only fake for protocol-freeze Git guards."""

    head = "d" * 40
    branch = "research/proposition-ledger-phase2a-development-pilot"
    answers: dict[tuple[str, ...], str] = {
        ("status", "--porcelain"): "",
        ("branch", "--show-current"): branch,
        ("rev-parse", "HEAD"): head,
        ("rev-parse", f"origin/{branch}"): head,
        ("show", "-s", "--format=%s", "HEAD"): (
            "Freeze proposition ledger development pilot"
        ),
        ("rev-parse", "HEAD^"): pilot.SOURCE_COMMIT,
        ("merge-base", head, pilot.SOURCE_COMMIT): pilot.SOURCE_COMMIT,
    }
    answers.update(dict(overrides or {}))
    calls: list[tuple[str, ...]] = []

    def synthetic_git(*args: str) -> str:
        calls.append(args)
        if args not in answers:
            raise AssertionError(f"unexpected synthetic Git query: {args!r}")
        return answers[args]

    monkeypatch.setattr(pilot, "_git_output", synthetic_git)
    return head, calls


# 1. Import is inert, even when every ordinary socket construction is forbidden.
def test_importing_pilot_module_performs_no_network_access() -> None:
    script = f"""
import importlib.util, sys
def audit(event, args):
    if event in {{'socket.connect', 'socket.getaddrinfo', 'http.client.connect'}}:
        raise AssertionError('network access during import: ' + event)
sys.addaudithook(audit)
spec = importlib.util.spec_from_file_location('isolated_phase2a_pilot', {str(MODULE_PATH)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
if 'mrsMThatcher2' in sys.modules or any(
    name.startswith('mrsMThatcher2.') for name in sys.modules
):
    raise AssertionError('production bot module imported by research pilot')
print('imported-without-network')
"""
    environment = dict(os.environ)
    environment.pop("XAI_API_KEY", None)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_DIR,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "imported-without-network"


# 2. Help and every offline API avoid construction of the live transport.
def test_help_and_offline_modes_make_zero_provider_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    creations = 0

    def forbidden_transport() -> None:
        nonlocal creations
        creations += 1
        raise AssertionError("offline mode created provider transport")

    monkeypatch.setattr(pilot, "XaiPilotTransport", forbidden_transport)
    with pytest.raises(SystemExit) as raised:
        pilot._build_parser().parse_args(["--help"])
    assert raised.value.code == 0
    output = _prepare_synthetic(monkeypatch, tmp_path)
    assert pilot.prepare_run  # prepare completed in helper without a transport
    # Prepared-only verification is explicitly an offline/incomplete result.
    verification = pilot.verify_only(output)
    assert verification["provider_calls"] == 0
    assert creations == 0


def test_prepare_review_and_verify_cli_modes_are_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Install the wholly synthetic V2 selection seam, then exercise fresh paths
    # through main rather than calling any live-mode function.
    _prepare_synthetic(monkeypatch, tmp_path, "selection-seam-seed")
    transport_creations = 0

    def forbidden_transport() -> None:
        nonlocal transport_creations
        transport_creations += 1
        raise AssertionError("offline CLI created xAI transport")

    monkeypatch.setattr(pilot, "XaiPilotTransport", forbidden_transport)
    output = tmp_path / "cli-run"
    assert pilot.main(["--prepare", "--private-output", str(output)]) == 0
    prepared_stdout = capsys.readouterr()
    assert json.loads(prepared_stdout.out)["provider_calls"] == 0
    assert prepared_stdout.err == ""

    assert pilot.main(["--build-review-pack", "--private-output", str(output)]) == 0
    review_stdout = capsys.readouterr()
    assert json.loads(review_stdout.out)["provider_calls"] == 0
    assert review_stdout.err == ""

    assert pilot.main(["--verify-only", "--private-output", str(output)]) == 0
    verify_stdout = capsys.readouterr()
    assert json.loads(verify_stdout.out)["provider_calls"] == 0
    assert verify_stdout.err == ""
    assert transport_creations == 0


# 3. Only directly exposed Grade-A records pass the selection boundary.
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(source_grade="B", reconstruction_grade="B"), "Grade-A"),
        (
            lambda value: value.update(
                exposure_categories=[],
                calibration_container_all_previously_exposed=False,
                prior_exposure_reasons=[],
            ),
            "exposure",
        ),
    ],
)
def test_only_directly_exposed_grade_a_records_are_selectable(mutation: Any, message: str) -> None:
    record = _record(1)
    mutation(record)
    with pytest.raises(pilot.SelectionBoundaryError, match=message):
        pilot.enforce_selection_boundary(record)


# 4-5. Sealed metadata rejects before any protected text accessor is touched.
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("effective_exposure_status", "genuinely_unexposed", "genuinely unexposed"),
        ("conversation_exposure_status", "genuinely_unexposed", "genuinely unexposed"),
        ("target_exposure_status", "genuinely_unexposed", "genuinely unexposed"),
        ("preliminary_within_family_held_out_eligibility", True, "held-out"),
    ],
)
def test_sealed_records_are_rejected_before_text_access(
    field: str, value: Any, message: str
) -> None:
    record = _ProtectedRecord(_record(1))
    record[field] = value
    with pytest.raises(pilot.SelectionBoundaryError, match=message):
        pilot.enforce_selection_boundary(record)
    assert record.text_accesses == 0


# 6. Structural-mining-only exposure is a reserve, never pilot material.
def test_structurally_mined_only_record_is_not_selected() -> None:
    record = _ProtectedRecord(_record(1, exposure=("structurally_mined_only",)))
    record["calibration_container_all_previously_exposed"] = False
    with pytest.raises(pilot.SelectionBoundaryError, match="structurally-mined-only"):
        pilot.enforce_selection_boundary(record)
    assert record.text_accesses == 0


# V2 corpus guard: an ordinary prefix open at cutoff is rejected.  The narrowly
# frozen metadata-sidecar exception is separately tested when its seam is present.
def test_open_at_frozen_cutoff_fails_closed_without_explicit_exception() -> None:
    record = _record(1)
    record["stability_status"] = "open_at_frozen_cutoff"
    record["frozen_historical_or_quiescent_at_cutoff"] = False
    with pytest.raises(pilot.SelectionBoundaryError, match="open|stability|quiescent"):
        pilot.enforce_selection_boundary(record)


def test_stability_metadata_is_checked_before_transcript_content() -> None:
    record = _ProtectedRecord(_record(1))
    record["stability_status"] = "open_at_frozen_cutoff"
    with pytest.raises(pilot.SelectionBoundaryError, match="open-prefix exception"):
        pilot.enforce_selection_boundary(record)
    assert record.text_accesses == 0


def test_explicit_frozen_open_prefix_exception_is_metadata_bound_and_narrow() -> None:
    record = _record(4, stressor="compound allegation account clarification")
    record.update(
        {
            "stability_status": "open_at_frozen_cutoff",
            "effective_exposure_status": "exposed",
            "development_stability_policy_version": pilot.DEVELOPMENT_STABILITY_POLICY_VERSION,
            "authorised_open_prefix_exception_case_id": record["calibration_case_id"],
            "frozen_open_prefix_exception": True,
            "open_prefix_exception_reason": pilot.OPEN_PREFIX_EXCEPTION_REASON,
            "selected_prefix_immutability_status": "frozen_at_cutoff",
            "post_target_extension_withheld": True,
            "post_cutoff_extension_withheld": True,
            "frozen_cutoff": pilot.FROZEN_CUTOFF,
            "complete_target_ancestry": True,
            "target_author_identity_status": "available",
            "target_author_conflict_count": 0,
            "stressor_tags": [
                "compound_allegation",
                "clarification_then_apparent_answer",
            ],
            "stable_direct_compound_substitute_count": 0,
            "source_metadata_row_sha256": "c" * 64,
            "prefix_turn_count": 3,
        }
    )
    record["open_prefix_exception_binding_sha256"] = (
        pilot.open_prefix_exception_binding(record)
    )

    accepted = pilot.enforce_selection_boundary(record)
    assert accepted["turns"][-1]["turn_id"] == record["target_turn_id"]

    for field in (
        "authorised_open_prefix_exception_case_id",
        "source_metadata_row_sha256",
        "post_cutoff_extension_withheld",
        "open_prefix_exception_binding_sha256",
    ):
        tampered = copy.deepcopy(record)
        tampered[field] = False if isinstance(tampered[field], bool) else "tampered"
        with pytest.raises(pilot.SelectionBoundaryError, match="exception invalid"):
            pilot.enforce_selection_boundary(tampered)


def test_stable_direct_compound_substitute_prevents_open_prefix_exception() -> None:
    open_admin = _metadata_row("open-compound")
    open_admin["stability_status"] = "open_at_frozen_cutoff"
    open_admin_material = copy.deepcopy(open_admin)
    open_admin_material.pop("row_sha256")
    open_admin["row_sha256"] = pilot.value_sha256(open_admin_material)
    stable_admin = _metadata_row("stable-compound")
    open_sidecar = pilot._sidecar_projection(open_admin)
    stable_sidecar = pilot._sidecar_projection(stable_admin)
    index = [
        {
            "calibration_case_id": "metadata-case-open-compound",
            "conversation_key": open_sidecar["conversation_key"],
            "target_turn_id": open_sidecar["target_turn_id"],
            "prefix_turn_count": 3,
            "source_grade": "A",
            "record_sha256": "1" * 64,
            "prior_exposure_reason_count": 1,
            "intended_schema_stressors": [
                "compound allegation",
                "account clarification",
            ],
        },
        {
            "calibration_case_id": "metadata-case-stable-compound",
            "conversation_key": stable_sidecar["conversation_key"],
            "target_turn_id": stable_sidecar["target_turn_id"],
            "prefix_turn_count": 3,
            "source_grade": "A",
            "record_sha256": "2" * 64,
            "prior_exposure_reason_count": 1,
            "intended_schema_stressors": ["compound allegation"],
        },
    ]

    with pytest.raises(pilot.PilotError, match="without a stable directly exposed substitute"):
        pilot.select_development_case_metadata(
            index, [open_sidecar, stable_sidecar]
        )


def test_more_than_one_open_prefix_exception_is_rejected() -> None:
    candidates = pilot.select_development_cases(_eligible_records())
    candidates[0]["frozen_open_prefix_exception"] = True
    candidates[1]["frozen_open_prefix_exception"] = True
    with pytest.raises(pilot.PilotError, match="more than one"):
        pilot._choose_candidate_combination(candidates)


@pytest.mark.parametrize(
    ("access_class", "counter_field"),
    [
        ("protected_transcript_open_attempt", "protected_transcript_records_opened"),
        (
            "protected_source_mapping_open_attempt",
            "protected_source_mapping_records_opened",
        ),
    ],
)
def test_protected_transcript_and_source_mapping_access_block_before_opener(
    access_class: str,
    counter_field: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path, access_class)
    opener_calls = 0

    def forbidden_opener() -> None:
        nonlocal opener_calls
        opener_calls += 1

    with pytest.raises(pilot.PilotError, match="access blocked and recorded"):
        pilot._record_protected_content_access_attempt(output, access_class)
        forbidden_opener()
    assert opener_calls == 0
    audit = pilot._load_json(
        output / "held-out-content-seal-audit.json", "synthetic breach audit"
    )
    assert audit["substantive_seal_breached"] is True
    assert audit["substantive_access_guard_event"] == access_class
    assert audit[counter_field] == 1


# 7-12. Frozen deterministic selection/planning properties.
def test_selects_exactly_eight_independent_conversations(selected_cases: Sequence[Mapping[str, Any]]) -> None:
    assert len(selected_cases) == 8
    assert len({case["conversation_key"] for case in selected_cases}) == 8


@pytest.mark.parametrize("turns", [2, 5])
def test_unique_turn_count_must_be_between_24_and_32(turns: int) -> None:
    with pytest.raises(pilot.PilotError, match="24-32-turn cap"):
        pilot.select_development_cases(_eligible_records(count_per_case=turns))


def test_planned_calls_equal_twice_unique_turns_and_never_exceed_64(local_cases: Sequence[Mapping[str, Any]]) -> None:
    freeze = _freeze(24)
    plan = pilot.build_call_plan(local_cases, freeze)
    assert sum(case["unique_turn_count"] for case in local_cases) == 24
    assert plan["planned_provider_call_count"] == 48
    assert len(plan["entries"]) == 48
    assert plan["maximum_provider_call_budget"] == 64
    with pytest.raises(pilot.PilotError, match="48-64"):
        _freeze(33)


def test_selection_is_deterministic_and_input_order_independent() -> None:
    records = _eligible_records()
    shuffled = copy.deepcopy(records)
    random.Random(7321).shuffle(shuffled)
    first = pilot.select_development_cases(records)
    second = pilot.select_development_cases(shuffled)
    assert pilot.canonical_json_bytes(first) == pilot.canonical_json_bytes(second)


def test_selection_covers_all_frozen_stressor_priorities(selected_cases: Sequence[Mapping[str, Any]]) -> None:
    covered = {tag for case in selected_cases for tag in case["stressor_tags"]}
    assert covered == set(pilot.STRESSOR_IDS)


# 13-17. Payload construction exposes only the exact current localised turn.
def test_payload_withholds_future_outcome_reply_and_labels(
    local_cases: Sequence[Mapping[str, Any]], schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
) -> None:
    case = local_cases[0]
    turn = case["exact_private_transcript_prefix"][0]
    payload = pilot.build_turn_payload(
        case,
        turn,
        None,
        protocol_freeze_sha256="f" * 64,
        provider_schema=schemas[1],
    )
    serialised = pilot.canonical_json_bytes(payload).decode("utf-8")
    assert payload["exact_current_visible_text"] == turn["text"]
    assert payload["pilot_local_current_turn_id"] == turn["turn_id"]
    assert payload["validated_prior_persisted_ledger"] is None
    for marker in (
        "FORBIDDEN_FUTURE_TEXT",
        "FORBIDDEN_HISTORICAL_REPLY",
        "FORBIDDEN_PRODUCTION_OUTCOME",
        "FORBIDDEN_AUDIT_LABEL",
        "stressor_tags",
        "source-post",
        "raw-contributor",
    ):
        assert marker not in serialised


def test_real_source_ids_are_localised_but_unicode_text_and_offsets_are_exact(
    local_cases: Sequence[Mapping[str, Any]], schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
) -> None:
    case = local_cases[0]
    turn = case["exact_private_transcript_prefix"][0]
    assert case["pilot_conversation_id"].startswith("dev-conversation-")
    assert turn["turn_id"].startswith("dev-turn-")
    assert turn["speaker"]["participant_id"].startswith("participant-")
    assert "source-turn" not in json.dumps(case)
    assert turn["text"].endswith("— café.")
    delta = _delta_with_proposition(case, turn, None)
    span = delta["new_propositions"][0]["exact_evidence_spans"][0]
    assert turn["text"][span["start_char"] : span["end_char"]] == span["exact_text"]
    assert _process(delta, case, turn, None, schemas)["validation"]["evidence_span_status"] == "passed"


# 18-26. Profile symmetry, chain independence, order, and request envelope.
def test_genesis_requests_differ_only_by_model_identity(
    local_cases: Sequence[Mapping[str, Any]], schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
) -> None:
    case, turn = local_cases[0], local_cases[0]["exact_private_transcript_prefix"][0]
    payload = pilot.build_turn_payload(
        case, turn, None, protocol_freeze_sha256="a" * 64, provider_schema=schemas[1]
    )
    requests = [
        pilot.build_request_representation(
            profile, payload, system_prompt="synthetic prompt", provider_schema=schemas[1]
        )
        for profile in pilot.PROFILES
    ]
    differing = {key for key in requests[0] if requests[0][key] != requests[1][key]}
    assert differing == {"model"}


def test_later_payloads_keep_model_ledgers_independent(
    local_cases: Sequence[Mapping[str, Any]], schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
) -> None:
    case = local_cases[0]
    turn = case["exact_private_transcript_prefix"][1]
    ledger43 = {"ledger_sha256": "43" * 32, "profile_marker": "only-43"}
    ledger46 = {"ledger_sha256": "46" * 32, "profile_marker": "only-46"}
    payload43 = pilot.build_turn_payload(
        case, turn, ledger43, protocol_freeze_sha256="a" * 64, provider_schema=schemas[1]
    )
    payload46 = pilot.build_turn_payload(
        case, turn, ledger46, protocol_freeze_sha256="a" * 64, provider_schema=schemas[1]
    )
    assert payload43["validated_prior_persisted_ledger"] == ledger43
    assert payload46["validated_prior_persisted_ledger"] == ledger46
    assert "only-46" not in json.dumps(payload43)
    assert "only-43" not in json.dumps(payload46)


def test_each_profile_is_chronological_and_profile_order_is_counterbalanced(local_cases: Sequence[Mapping[str, Any]]) -> None:
    plan = pilot.build_call_plan(local_cases, _freeze())
    by_chain: dict[tuple[str, str], list[int]] = {}
    first_models: set[str] = set()
    for entry in plan["entries"]:
        key = (entry["pilot_conversation_id"], entry["profile_id"])
        by_chain.setdefault(key, []).append(entry["turn_index"])
    for case in local_cases:
        entries = [
            entry
            for entry in plan["entries"]
            if entry["pilot_conversation_id"] == case["pilot_conversation_id"]
        ]
        first_models.add(entries[0]["model"])
        expected = list(range(case["unique_turn_count"]))
        for profile in pilot.PROFILES:
            assert by_chain[(case["pilot_conversation_id"], profile["profile_id"])] == expected
    # Eight deterministic conversation keys exercise both parity assignments.
    assert first_models == {"grok-4.3", "grok-4.6"}


def test_request_disables_tools_search_execution_streaming_storage_and_retries(
    local_cases: Sequence[Mapping[str, Any]], schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
) -> None:
    case, turn = local_cases[0], local_cases[0]["exact_private_transcript_prefix"][0]
    payload = pilot.build_turn_payload(
        case, turn, None, protocol_freeze_sha256="a" * 64, provider_schema=schemas[1]
    )
    request = pilot.build_request_representation(
        pilot.PROFILES[0], payload, system_prompt="prompt", provider_schema=schemas[1]
    )
    assert request["tools"] == []
    assert "tool_choice" not in request
    assert request["tool_choice_parameter_sent"] is False
    assert request["search_parameters"] is None
    assert request["code_execution"] is False
    assert request["streaming"] is False
    assert request["store_messages"] is False
    assert request["fallback_model"] is None
    assert request["sampling_parameters_set"] == []
    assert request["application_retry_count"] == 0
    assert request["sdk_grpc_retries"] is False
    assert live_probe.NO_RETRY_CHANNEL_OPTIONS == (
        ("grpc.enable_retries", 0),
        ("grpc.service_config", "{}"),
    )


# 27-28. The operator must acknowledge the exact frozen count, never merely a
# looser ceiling.
@pytest.mark.parametrize("acknowledgement", [None, 0, 47, 49, 64, 65])
def test_live_execution_rejects_any_nonexact_budget_acknowledgement(
    acknowledgement: int | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path)
    factory_calls = 0

    def forbidden_factory() -> None:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("budget guard instantiated transport")

    with pytest.raises(pilot.PilotError, match="exactly 48"):
        pilot.execute_live_pilot(
            output,
            confirm_provider_call_budget=acknowledgement,
            transport_factory=forbidden_factory,
        )
    assert factory_calls == 0


# 29. Durable sending/uncertain evidence is non-repeatable.
def test_sent_or_uncertain_call_cannot_be_repeated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path)
    pilot._transition_call(
        output,
        0,
        "sending",
        started_at_utc="2026-09-01T00:00:00Z",
        attempt_number=1,
        provider_call_count=1,
    )
    calls = 0

    def forbidden_factory() -> None:
        nonlocal calls
        calls += 1
        raise AssertionError("nonrepeatable call reached transport")

    with pytest.raises(pilot.PilotError, match="uncertain sent call"):
        pilot.execute_live_pilot(
            output,
            confirm_provider_call_budget=48,
            transport_factory=forbidden_factory,
        )
    ledger = pilot._load_json(output / "call-ledger.json", "test ledger")
    assert ledger["entries"][0]["state"] == "uncertain_after_send"
    assert calls == 0


def test_response_received_is_sent_but_unclosed_and_never_repeated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path)
    pilot._transition_call(
        output,
        0,
        "sending",
        started_at_utc="2026-09-01T00:00:00Z",
        attempt_number=1,
        provider_call_count=1,
    )
    pilot._transition_call(
        output,
        0,
        "response_received",
        completed_at_utc="2026-09-01T00:00:01Z",
        raw_response_sha256="b" * 64,
    )
    factory_calls = 0

    def forbidden_factory() -> None:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("response-received call was repeated")

    with pytest.raises(pilot.PilotError, match="response_received|sent.*unclosed"):
        pilot.execute_live_pilot(
            output,
            confirm_provider_call_budget=48,
            transport_factory=forbidden_factory,
        )
    assert factory_calls == 0


def test_terminal_ledger_normalisation_accepts_validation_disposition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path)
    pilot._transition_call(
        output,
        0,
        "sending",
        started_at_utc="2026-09-01T00:00:00Z",
        attempt_number=1,
        provider_call_count=1,
    )
    pilot._transition_call(output, 0, "response_received", raw_response_sha256="c" * 64)
    pilot._transition_call(
        output,
        0,
        "validated_and_materialised",
        validation_disposition="validated_and_materialised",
    )
    prepared = pilot._load_prepared(output)
    assert prepared["ledger"]["entries"][0]["validation_disposition"] == (
        "validated_and_materialised"
    )


# 30-32. A chain failure blocks only later turns in that exact chain, whereas
# a diagnostic warning preserves the chain.
def test_chain_failure_blocks_only_same_conversation_and_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path)
    ledger = pilot._load_json(output / "call-ledger.json", "test ledger")
    first = ledger["entries"][0]
    pilot._transition_call(
        output,
        0,
        "sending",
        started_at_utc="2026-09-01T00:00:00Z",
        attempt_number=1,
        provider_call_count=1,
    )
    pilot._transition_call(output, 0, "response_received")
    ledger = pilot._transition_call(output, 0, "materialisation_failed")
    failed = ledger["entries"][0]
    ledger = pilot._mark_chain_blocked(output, ledger, failed)

    same_future = [
        item
        for item in ledger["entries"]
        if item["pilot_conversation_id"] == first["pilot_conversation_id"]
        and item["profile_id"] == first["profile_id"]
        and item["turn_index"] > first["turn_index"]
    ]
    other_profile = [
        item
        for item in ledger["entries"]
        if item["pilot_conversation_id"] == first["pilot_conversation_id"]
        and item["profile_id"] != first["profile_id"]
    ]
    unrelated = [
        item
        for item in ledger["entries"]
        if item["pilot_conversation_id"] != first["pilot_conversation_id"]
    ]
    assert same_future and {item["state"] for item in same_future} == {
        "blocked_by_prior_turn_failure"
    }
    assert other_profile and {item["state"] for item in other_profile} == {"planned"}
    assert unrelated and {item["state"] for item in unrelated} == {"planned"}

    valid = {
        "strict_json_status": "passed",
        "provider_schema_status": "passed",
        "intended_canonical_status": "passed",
        "binding_status": "passed",
        "evidence_span_status": "passed",
        "semantic_reference_status": "passed",
        "materialisation_status": "passed",
        "persisted_ledger_status": "passed",
    }
    assert pilot.call_state_from_validation(
        valid, {"diagnostic_flags": ["provider_warning_present"]}
    ) == "validated_with_diagnostic_flags"


# 33. Global auth/billing/quota/transport failure stops every remaining call.
def test_global_failure_marks_all_remaining_calls_not_attempted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path)
    ledger = pilot._load_json(output / "call-ledger.json", "test ledger")
    stopped = pilot._mark_global_stop(output, ledger, "authentication_failure")
    assert len(stopped["entries"]) == 48
    assert {entry["state"] for entry in stopped["entries"]} == {
        "not_attempted_due_to_global_failure"
    }
    assert stopped["provider_call_count"] == 0


# 34. Strict JSON never extracts or repairs a response.
@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'```json\n{"x":1}\n```',
        b'prefix {"x":1}',
        b'{"x":1} {"y":2}',
        b'\xff',
    ],
)
def test_strict_json_rejects_forbidden_response_forms(
    raw: bytes,
    local_cases: Sequence[Mapping[str, Any]],
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    case, turn = local_cases[0], local_cases[0]["exact_private_transcript_prefix"][0]
    processed = _process(raw, case, turn, None, schemas)
    assert processed["parsed"] is None
    assert processed["ledger"] is None
    assert processed["validation"]["strict_json_status"] == "failed"


# 35. Corrected intended pattern semantics are authoritative; ordinary
# jsonschema remains diagnostic only.
def test_intended_canonical_pattern_layer_is_authoritative(
    monkeypatch: pytest.MonkeyPatch,
    local_cases: Sequence[Mapping[str, Any]],
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    case, turn = local_cases[0], local_cases[0]["exact_private_transcript_prefix"][0]
    modes: list[str] = []

    def intended(_schema: Mapping[str, Any], _value: Any, *, pattern_mode: str) -> list[str]:
        modes.append(pattern_mode)
        return []

    monkeypatch.setattr(pilot.preflight, "intended_validation_errors", intended)
    monkeypatch.setattr(
        pilot.preflight,
        "validation_errors",
        lambda _schema, _value: ["synthetic ordinary-regex divergence"],
    )
    result = _process(_noop_delta(case, turn, None), case, turn, None, schemas)
    validation = result["validation"]
    assert modes == ["xai_full_string", "canonical_outer_anchors"]
    assert validation["intended_canonical_status"] == "passed"
    assert validation["ordinary_python_jsonschema_status"] == "failed"
    assert validation["ordinary_python_jsonschema_is_authority"] is False
    assert validation["materialisation_status"] == "passed"


# 36. Evidence binds to the exact current turn and exact Unicode substring.
@pytest.mark.parametrize("mutation", ["turn", "range", "text"])
def test_evidence_spans_fail_closed_outside_exact_current_turn(
    mutation: str,
    local_cases: Sequence[Mapping[str, Any]],
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    case, turn = local_cases[0], local_cases[0]["exact_private_transcript_prefix"][0]
    delta = _delta_with_proposition(case, turn, None)
    span = delta["new_propositions"][0]["exact_evidence_spans"][0]
    if mutation == "turn":
        span["turn_id"] = "dev-turn-future"
    elif mutation == "range":
        span["end_char"] = len(turn["text"]) + 1
    else:
        span["exact_text"] = "not the exact substring"
    result = _process(delta, case, turn, None, schemas)
    assert result["ledger"] is None
    assert result["validation"]["evidence_span_status"] == "failed"


# 37. Unknown/future/wrong-namespace references fail closed.
@pytest.mark.parametrize("reference", ["unknown-existing", "future-proposition", "new-issue-1"])
def test_unknown_future_and_wrong_namespace_references_fail_closed(
    reference: str,
    local_cases: Sequence[Mapping[str, Any]],
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    case, turn = local_cases[0], local_cases[0]["exact_private_transcript_prefix"][0]
    delta = _noop_delta(case, turn, None)
    delta["proposition_updates"] = [
        {
            "proposition_id": reference,
            "changes": {"lifecycle_status": "resolved"},
            "reason": "Synthetic invalid reference.",
            "exact_evidence_spans": [
                {
                    "turn_id": turn["turn_id"],
                    "start_char": 0,
                    "end_char": len(turn["text"]),
                    "exact_text": turn["text"],
                }
            ],
        }
    ]
    result = _process(delta, case, turn, None, schemas)
    assert result["ledger"] is None
    assert result["validation"]["semantic_reference_status"] == "failed"


# 38. Persistence fields remain harness-owned.
@pytest.mark.parametrize("field", ["ledger_sha256", "state_patch", "participants", "turn_refs"])
def test_provider_persistence_fields_fail_closed(
    field: str,
    local_cases: Sequence[Mapping[str, Any]],
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    case, turn = local_cases[0], local_cases[0]["exact_private_transcript_prefix"][0]
    delta = _noop_delta(case, turn, None)
    delta[field] = [] if field != "ledger_sha256" else "0" * 64
    result = _process(delta, case, turn, None, schemas)
    assert result["ledger"] is None
    assert result["validation"]["provider_schema_status"] == "failed"


# 39-41. Several turns materialise incrementally and deterministically; trusted
# first appearances register participants, and the harness owns hashes/patches.
def test_valid_deltas_materialise_across_turns_with_deterministic_participants_and_hashes(
    local_cases: Sequence[Mapping[str, Any]],
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    case = copy.deepcopy(local_cases[0])

    def run() -> list[dict[str, Any]]:
        prior: dict[str, Any] | None = None
        snapshots: list[dict[str, Any]] = []
        for turn in case["exact_private_transcript_prefix"]:
            result = _process(_noop_delta(case, turn, prior), case, turn, prior, schemas)
            assert result["validation"]["persisted_ledger_status"] == "passed"
            prior = result["ledger"]
            assert prior is not None
            snapshots.append(prior)
        return snapshots

    first = run()
    second = run()
    assert pilot.canonical_json_bytes(first) == pilot.canonical_json_bytes(second)
    assert [item["ledger_sha256"] for item in first] == [
        item["ledger_sha256"] for item in second
    ]
    assert len(first[0]["participants"]) == 1
    assert len(first[1]["participants"]) == 2
    assert all(item["state_transitions"] for item in first)
    assert all(item["ledger_sha256"] == semantic.phase1.ledger_sha256(item) for item in first)


# 42. Reprocessing the same saved bytes is byte-identical and offline.
def test_saved_response_reprocessing_is_byte_identical(
    local_cases: Sequence[Mapping[str, Any]],
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    case, turn = local_cases[0], local_cases[0]["exact_private_transcript_prefix"][0]
    canonical, provider, persisted = schemas
    kwargs = {
        "raw": _raw(_delta_with_proposition(case, turn, None)),
        "case": case,
        "turn": turn,
        "prior_ledger": None,
        "canonical_schema": canonical,
        "provider_schema": provider,
        "persisted_ledger_schema": persisted,
    }
    first = pilot.process_response_twice(**kwargs)
    second = pilot.process_response_twice(**kwargs)
    assert pilot.canonical_json_bytes(first) == pilot.canonical_json_bytes(second)


def test_fake_live_execution_completes_all_frozen_calls_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path)
    _patch_live_preconditions(monkeypatch, schemas)
    transport = _DynamicTransport(warning_at=1)

    summary = pilot.execute_live_pilot(
        output,
        confirm_provider_call_budget=48,
        transport_factory=lambda: transport,
    )

    ledger = pilot._load_json(output / "call-ledger.json", "test call ledger")
    states = [entry["state"] for entry in ledger["entries"]]
    assert len(transport.calls) == 48
    assert ledger["provider_call_count"] == 48
    assert ledger["automatic_retry_count"] == 0
    assert ledger["fallback_call_count"] == 0
    assert ledger["repair_call_count"] == 0
    assert states.count("validated_and_materialised") == 47
    assert states.count("validated_with_diagnostic_flags") == 1
    assert transport.closed is True
    assert summary["attempted_provider_calls"] == 48
    # 43-44. Metrics and paired counts reconcile exactly to the durable ledger.
    for profile in pilot.PROFILES:
        metrics = summary["profile_metrics"]["profiles"][profile["profile_id"]]
        assert metrics["planned_calls"] == 24
        assert metrics["attempted_calls"] == 24
        assert metrics["definite_responses"] == 24
        assert metrics["strict_json_success_count"] == 24
        assert metrics["intended_canonical_success_count"] == 24
        assert metrics["materialisation_success_count"] == 24
        assert metrics["complete_conversation_chain_count"] == 8
        assert metrics["maximum_ledger_size_bytes"] >= metrics[
            "median_ledger_size_bytes"
        ]
        assert metrics["average_state_growth_bytes_per_turn"] is not None
        assert metrics["total_tokens"] > 0
        assert metrics["raw_provider_cost_field"]["currency_conversion_performed"] is False
    paired = summary["paired_operational_comparison"]
    assert paired["both_profiles_completed_conversation"] == 8
    assert paired["both_reached_deepest_target"] == 8
    assert paired["only_grok_4_3_completed"] == 0
    assert paired["only_grok_4_6_completed"] == 0
    assert paired["neither_completed"] == 0
    assert paired["semantic_winner_selected"] is False
    assert summary["phase2a_disposition"] == (
        "phase2a_development_pilot_completed_review_pending"
    )
    assert summary["model_profile_selected"] is False
    assert summary["ledger_effectiveness_established"] is False
    # Fake transport sees no future/outcome labels and each chain receives only
    # its own immediate validated predecessor.
    for call in transport.calls:
        serialised = json.dumps(call["payload"], sort_keys=True)
        assert "FORBIDDEN_" not in serialised
        assert call["request"]["tools"] == []
        assert "tool_choice" not in call["request"]
    by_chain: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for call in transport.calls:
        key = (
            call["payload"]["pilot_local_conversation_key"],
            call["profile"]["profile_id"],
        )
        by_chain.setdefault(key, []).append(call)
    for calls in by_chain.values():
        assert [item["payload"]["turn_index"] for item in calls] == [0, 1, 2]
        assert calls[0]["payload"]["validated_prior_persisted_ledger"] is None
        assert calls[1]["payload"]["validated_prior_persisted_ledger"][
            "as_of_turn_index"
        ] == 0
        assert calls[2]["payload"]["validated_prior_persisted_ledger"][
            "as_of_turn_index"
        ] == 1

    # 45-49. The offline review pack is deterministic, blinded, and empty; its
    # adjacent sample planning treats conversations as the independent unit.
    review = pilot.build_review_pack(output)
    assert review["provider_calls"] == 0
    assert review["case_count"] == 8
    review_dir = output / "human-review-pack"
    index = pilot._load_json(review_dir / "index.json", "test review index")
    assert index["blinded"] is True
    assert index["scored"] is False
    assert index["not_arm_d_gold_annotation"] is True
    blinded_text = "".join(
        path.read_text(encoding="utf-8")
        for path in review_dir.rglob("*.json")
        if path.name != "unblinding.json"
    )
    assert "grok-4.3" not in blinded_text
    assert "grok-4.6" not in blinded_text
    assert "xai-grok" not in blinded_text
    assert "provider_response_id" not in blinded_text
    unblinding = pilot._load_json(
        review_dir / "unblinding.json", "test unblinding"
    )
    assert set(unblinding["cases"]) == {
        item["review_case_id"] for item in index["cases"]
    }
    assert "unblinding" not in json.dumps(index).lower()
    for path in review_dir.rglob("scoring-form.json"):
        form = pilot._load_json(path, "test score form")
        assert form["review_status"] == "unscored"
        assert form["scores"]
        assert set(form["scores"].values()) == {None}
    second_review = pilot.build_review_pack(output)
    assert second_review["blinded_review_pack_sha256"] == review[
        "blinded_review_pack_sha256"
    ]
    planning = pilot._load_json(output / "sample-planning.json", "test planning")
    assert planning["primary_independent_unit"] == "conversation"
    assert planning["clustering_sensitivity_unit"] == "contributor group"
    assert [item["plausible_absolute_difference"] for item in planning["scenarios"]] == [
        0.1,
        0.15,
        0.2,
    ]
    assert planning["sealed_clean_prefix_aggregate_count"] == 2
    assert planning["sealed_cases_used_in_calculation"] == 0
    assert planning["model_profile_selected"] is False
    assert planning["ledger_effectiveness_established"] is False
    assert pilot.verify_checksums(output)["status"] == "passed"
    verification = pilot.verify_only(output)
    assert verification["status"] == "phase2a_verify_only_passed"
    assert verification["saved_responses_reprocessed"] == 48
    assert verification["provider_calls"] == 0
    assert verification["checksums"]["status"] == "passed"


def test_raw_observation_is_durable_before_processing_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path)
    _patch_live_preconditions(monkeypatch, schemas)
    transport = _DynamicTransport()

    def synthetic_processing_crash(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("synthetic post-receipt processing defect")

    monkeypatch.setattr(pilot, "process_response_twice", synthetic_processing_crash)
    pilot.execute_live_pilot(
        output,
        confirm_provider_call_budget=48,
        transport_factory=lambda: transport,
    )

    ledger = pilot._load_json(output / "call-ledger.json", "test call ledger")
    first = ledger["entries"][0]
    raw_path = pilot._turn_output_dir(output, first) / "response.raw.txt"
    raw = raw_path.read_bytes()
    assert len(transport.calls) == 1
    assert first["state"] == "materialisation_failed"
    assert raw
    assert pilot.sha256_bytes(raw) == first["raw_response_sha256"]
    assert (raw_path.parent / "request-metadata.json").is_file()
    assert (raw_path.parent / "usage.json").is_file()
    assert not (raw_path.parent / "response.parsed.json").exists()


def test_fake_invalid_response_blocks_one_chain_but_other_chains_continue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path)
    _patch_live_preconditions(monkeypatch, schemas)
    transport = _DynamicTransport(invalid_at=0)

    pilot.execute_live_pilot(
        output,
        confirm_provider_call_budget=48,
        transport_factory=lambda: transport,
    )

    ledger = pilot._load_json(output / "call-ledger.json", "test call ledger")
    first = ledger["entries"][0]
    assert first["state"] == "strict_validation_failed"
    same_chain = [
        item
        for item in ledger["entries"]
        if item["pilot_conversation_id"] == first["pilot_conversation_id"]
        and item["profile_id"] == first["profile_id"]
    ]
    assert [item["state"] for item in same_chain] == [
        "strict_validation_failed",
        "blocked_by_prior_turn_failure",
        "blocked_by_prior_turn_failure",
    ]
    assert len(transport.calls) == 46
    assert ledger["provider_call_count"] == 46
    assert any(
        item["state"] == "validated_and_materialised"
        and item["profile_id"] != first["profile_id"]
        for item in ledger["entries"]
    )


def test_fake_global_authentication_failure_stops_remaining_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path)
    _patch_live_preconditions(monkeypatch, schemas)
    transport = _DynamicTransport(
        fail_at=0,
        failure=_FakeRpcError("UNAUTHENTICATED", "synthetic authentication failure"),
    )

    pilot.execute_live_pilot(
        output,
        confirm_provider_call_budget=48,
        transport_factory=lambda: transport,
    )

    ledger = pilot._load_json(output / "call-ledger.json", "test call ledger")
    assert len(transport.calls) == 1
    assert ledger["provider_call_count"] == 1
    assert ledger["entries"][0]["state"] == "provider_error_received"
    assert {entry["state"] for entry in ledger["entries"][1:]} == {
        "not_attempted_due_to_global_failure"
    }


# Mechanical diagnostics are observations, not declarations of correctness.
def test_structural_diagnostics_measure_growth_density_and_flags(
    local_cases: Sequence[Mapping[str, Any]],
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    case, turn = local_cases[0], local_cases[0]["exact_private_transcript_prefix"][0]
    delta = _delta_with_proposition(case, turn, None)
    result = _process(delta, case, turn, None, schemas)
    diagnostics = pilot.semantic_diagnostics(
        result["parsed"], result["ledger"], None, turn, provider_payload_size=1234
    )
    assert diagnostics["ledger_canonical_byte_size"] > 0
    assert diagnostics["state_growth_delta_bytes"] > 0
    assert diagnostics["provider_payload_byte_size"] == 1234
    assert diagnostics["evidence_span_count"] == 1
    assert diagnostics["evidence_coverage_fraction"] == 1.0
    assert diagnostics["current_turn_semantic_density"] > 0
    assert diagnostics["structural_signals"]["live_proposition_retained"] is True
    assert "semantic_correctness" not in diagnostics


# 50-51. No evidence state selects a model; the top-level disposition is a
# pure function of the durable call evidence.
@pytest.mark.parametrize(
    ("states", "attempted", "expected"),
    [
        (["planned", "planned"], 0, "phase2a_development_pilot_blocked_before_calls"),
        (
            ["uncertain_after_send", "not_attempted_due_to_global_failure"],
            1,
            "phase2a_development_pilot_uncertain_after_send",
        ),
        (
            ["provider_error_received", "not_attempted_due_to_global_failure"],
            1,
            "phase2a_development_pilot_inconclusive_operational_failure",
        ),
        (
            ["validated_and_materialised", "materialisation_failed"],
            2,
            "phase2a_development_pilot_completed_with_profile_attrition_review_pending",
        ),
    ],
)
def test_final_disposition_is_derived_without_selecting_a_winner(
    states: Sequence[str], attempted: int, expected: str
) -> None:
    entries = []
    for index, state in enumerate(states):
        entries.append(
            {
                "profile_id": pilot.PROFILES[index % 2]["profile_id"],
                "pilot_conversation_id": "synthetic-disposition-conversation",
                "turn_index": 0,
                "state": state,
                "provider_call_count": 1 if index < attempted else 0,
            }
        )
    ledger = {"entries": entries}
    assert pilot.derive_phase2a_disposition(ledger) == expected
    paired = pilot.derive_paired_comparison(ledger)
    assert paired["semantic_winner_selected"] is False


def test_metadata_sidecar_projection_excludes_content_outcomes_and_protected_rows() -> None:
    administrative = _metadata_row("clean-projection")
    assert pilot._direct_metadata_row(administrative) is True
    sidecar = pilot._sidecar_projection(administrative)
    serialised = json.dumps(sidecar, sort_keys=True)
    assert "transcript_text" not in sidecar
    assert "production_outcome" not in sidecar
    assert sidecar["preliminary_within_family_held_out_eligibility"] is False
    assert sidecar["preliminary_held_out_eligibility"] is False

    substantive = copy.deepcopy(administrative)
    substantive["transcript_text"] = "FORBIDDEN_ADMIN_TRANSCRIPT_TEXT"
    assert pilot._contains_substantive_metadata_field(substantive) is True

    protected = copy.deepcopy(administrative)
    protected["preliminary_within_family_held_out_eligibility"] = True
    assert pilot._protected_metadata_row(protected) is True
    assert pilot._direct_metadata_row(protected) is False


def test_real_metadata_builder_counts_exclusions_without_rendering_protected_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    protected_markers = (
        "SEALED_HELDOUT_CONVERSATION",
        "SEALED_UNEXPOSED_CONVERSATION",
        "SEALED_STRUCTURAL_CONVERSATION",
    )
    rows = [
        _metadata_row("direct"),
        _metadata_row(
            "heldout",
            conversation=protected_markers[0],
            target="SEALED_HELDOUT_TARGET",
            held_out=True,
        ),
        _metadata_row(
            "unexposed",
            effective="genuinely_unexposed",
            conversation=protected_markers[1],
            target="SEALED_UNEXPOSED_TARGET",
        ),
        _metadata_row(
            "structural",
            effective="structurally_mined_only",
            conversation=protected_markers[2],
            target="SEALED_STRUCTURAL_TARGET",
        ),
    ]
    raw = _jsonl(rows)

    def synthetic_metadata(path: str | Path, label: str) -> bytes:
        assert Path(path) == pilot.TARGET_PREFIX_METADATA_PATH
        assert label == "target-prefix administrative metadata"
        return raw

    monkeypatch.setattr(pilot, "_read_bytes", synthetic_metadata)
    output = tmp_path / "metadata-boundary"
    output.mkdir(mode=0o700)
    sidecar, audit = pilot.build_exposed_metadata_sidecar(output)

    assert len(sidecar) == 1
    assert sidecar[0]["conversation_key"] == "metadata-conversation-direct"
    assert sidecar[0]["preliminary_within_family_held_out_eligibility"] is False
    assert sidecar[0]["preliminary_held_out_eligibility"] is False
    assert audit["source_metadata_rows_scanned"] == 4
    assert audit["protected_metadata_rows_scanned_for_administration"] == 3
    assert audit["directly_exposed_rows_written_to_sidecar"] == 1
    assert audit["preliminarily_held_out_rows_excluded"] == 1
    assert audit["genuinely_unexposed_rows_excluded"] == 1
    assert audit["structurally_mined_only_rows_excluded"] == 1
    assert audit["protected_metadata_rows_rendered_to_operator"] == 0
    assert audit["protected_rows_written_to_development_sidecar"] == 0
    emitted = (
        pilot.canonical_json_bytes(sidecar)
        + pilot.canonical_json_bytes(audit)
        + (output / "exposed-development-candidate-index.jsonl").read_bytes()
        + (output / "held-out-content-seal-audit.json").read_bytes()
        + capsys.readouterr().out.encode()
    )
    for marker in protected_markers:
        assert marker.encode() not in emitted


def test_unexpected_substantive_metadata_persists_breach_evidence_and_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret_text = "SEALED_SUBSTANTIVE_TEXT_MUST_NOT_BE_COPIED"
    row = _metadata_row("substantive")
    row["transcript_text"] = secret_text
    row_material = copy.deepcopy(row)
    row_material.pop("row_sha256")
    row["row_sha256"] = pilot.value_sha256(row_material)
    raw = _jsonl([row])
    monkeypatch.setattr(
        pilot,
        "_read_bytes",
        lambda path, _label: raw
        if Path(path) == pilot.TARGET_PREFIX_METADATA_PATH
        else (_ for _ in ()).throw(AssertionError("unexpected file read")),
    )
    output = tmp_path / "metadata-breach"
    output.mkdir(mode=0o700)

    with pytest.raises(pilot.PilotError, match="substantive content"):
        pilot.build_exposed_metadata_sidecar(output)

    sidecar_path = output / "exposed-development-candidate-index.jsonl"
    audit_path = output / "held-out-content-seal-audit.json"
    assert sidecar_path.read_bytes() == b""
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["substantive_seal_breached"] is True
    assert audit["unexpected_substantive_metadata_rows"] == 1
    assert audit["protected_transcript_records_opened"] == 1
    assert secret_text not in audit_path.read_text(encoding="utf-8")


def test_protocol_wrapper_binds_non_circular_exact_call_plan(
    local_cases: Sequence[Mapping[str, Any]],
) -> None:
    execution_core = _freeze()
    call_plan = pilot.build_call_plan(local_cases, execution_core)
    wrapper = pilot.wrap_tracked_protocol_freeze(execution_core, call_plan)

    assert wrapper["execution_freeze_core_sha256"] == pilot.value_sha256(
        execution_core
    )
    assert wrapper["call_plan_sha256"] == pilot.sha256_bytes(
        pilot.pretty_json_bytes(call_plan)
    )
    assert pilot._execution_freeze_core(wrapper) == execution_core
    assert "call_plan_sha256" not in execution_core

    tampered = copy.deepcopy(wrapper)
    tampered["selected_unique_turn_count"] = 25
    with pytest.raises(pilot.PilotError, match="core SHA-256 mismatch"):
        pilot._execution_freeze_core(tampered)


def test_tracked_and_private_freeze_wrappers_must_match_exactly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path, "wrapper-equality")
    private_wrapper = pilot.tracked_protocol_freeze_from_private(output)
    tracked_path = tmp_path / "synthetic-tracked-protocol-freeze.json"
    pilot._write_json(tracked_path, private_wrapper)
    monkeypatch.setattr(pilot, "TRACKED_FREEZE_PATH", tracked_path)
    head, calls = _patch_synthetic_git(monkeypatch)

    prepared = pilot._load_prepared(output)
    assert pilot._verify_pushed_clean_freeze(prepared) == head
    assert ("status", "--porcelain") in calls
    assert pilot.canonical_json_bytes(private_wrapper) == pilot.canonical_json_bytes(
        pilot._load_json(tracked_path, "synthetic tracked freeze")
    )

    tampered = copy.deepcopy(private_wrapper)
    tampered["call_plan_sha256"] = "0" * 64
    pilot._write_json(tracked_path, tampered)
    with pytest.raises(pilot.PilotError, match="wrappers differ"):
        pilot._verify_pushed_clean_freeze(prepared)


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ("dirty", "dirty"),
        ("unpushed", "not equal to remote"),
        ("prompt", "private protocol freeze mismatch: system_prompt_sha256"),
    ],
)
def test_execution_preflight_rejects_dirty_unpushed_or_prompt_changed_state(
    scenario: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path, f"preflight-{scenario}")
    prepared = pilot._load_prepared(output)
    tracked_path = tmp_path / f"tracked-{scenario}.json"
    pilot._write_json(tracked_path, prepared["freeze"])
    monkeypatch.setattr(pilot, "TRACKED_FREEZE_PATH", tracked_path)

    if scenario == "dirty":
        _patch_synthetic_git(
            monkeypatch,
            {("status", "--porcelain"): " M synthetic-tracked-file"},
        )
    elif scenario == "unpushed":
        branch = "research/proposition-ledger-phase2a-development-pilot"
        _patch_synthetic_git(
            monkeypatch,
            {("rev-parse", f"origin/{branch}"): "e" * 40},
        )
    else:
        _patch_synthetic_git(monkeypatch)
        changed = _tracked()
        changed["hashes"]["system_prompt_sha256"] = "f" * 64
        monkeypatch.setattr(pilot, "_tracked_input_record", lambda: changed)

    with pytest.raises(pilot.PilotError, match=message):
        pilot._verify_pushed_clean_freeze(prepared)


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        ("prompt-manifest.json", "private prompt manifest mismatch"),
        ("development-case-manifest.json", "case-manifest content SHA-256"),
        ("exposed-development-candidate-index.jsonl", "exposed-sidecar SHA-256"),
        ("held-out-content-seal-audit.json", "held-out-seal audit SHA-256"),
        ("call-plan.json", "call-plan SHA-256"),
    ],
)
def test_prepared_input_tampering_fails_closed_before_execution(
    artifact: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = _prepare_synthetic(
        monkeypatch,
        tmp_path,
        f"tamper-{artifact.replace('.', '-')}",
    )
    path = output / artifact
    if artifact == "exposed-development-candidate-index.jsonl":
        pilot._atomic_write(path, path.read_bytes() + b" ")
    else:
        value = pilot._load_json(path, f"synthetic {artifact}")
        if artifact == "prompt-manifest.json":
            value["system_prompt_sha256"] = "0" * 64
        elif artifact == "development-case-manifest.json":
            value["cases"][0]["source_family"] = "synthetic-tampered-family"
        elif artifact == "held-out-content-seal-audit.json":
            value["protected_transcript_records_opened"] = 1
        else:
            value["planned_provider_call_count"] += 1
        pilot._write_json(path, value)

    with pytest.raises(pilot.PilotError, match=message):
        pilot._load_prepared(output)


def test_live_execution_uses_an_exclusive_nonblocking_private_run_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = _prepare_synthetic(monkeypatch, tmp_path, "exclusive-lock")
    with pilot._exclusive_execution_lock(output):
        with pytest.raises(
            pilot.PilotError, match="another live-pilot execution"
        ):
            with pilot._exclusive_execution_lock(output):
                raise AssertionError("second lock acquisition unexpectedly succeeded")


@pytest.mark.parametrize(
    ("filename", "leak", "message"),
    [
        ("index.json", {"profile_id": "hidden"}, "forbidden metadata"),
        ("case.json", {"projection": "grok-4.3"}, "model/provider identity"),
        ("grok-4.6-ledger.json", {"projection": {}}, "filename discloses"),
        ("scores.json", {"latency_seconds": 1.0}, "forbidden metadata"),
        ("audit.json", {"stressor_tags": ["synthetic"]}, "forbidden metadata"),
    ],
)
def test_blinded_review_pack_leak_scan_rejects_operational_or_model_identity(
    filename: str,
    leak: Mapping[str, Any],
    message: str,
    tmp_path: Path,
) -> None:
    review_dir = tmp_path / "review-pack"
    review_dir.mkdir(mode=0o700)
    review_dir.chmod(0o700)
    pilot._write_json(review_dir / "unblinding.json", {"Ledger A": "grok-4.3"})
    pilot._write_json(review_dir / filename, leak)
    with pytest.raises(pilot.PilotError, match=message):
        pilot._verify_blinded_review_pack(review_dir)


def test_blinded_review_pack_scan_allows_identity_only_in_root_unblinding(
    tmp_path: Path,
) -> None:
    review_dir = tmp_path / "clean-review-pack"
    review_dir.mkdir(mode=0o700)
    review_dir.chmod(0o700)
    pilot._write_json(
        review_dir / "index.json",
        {"cases": [{"case": "review-case-01", "labels": ["Ledger A", "Ledger B"]}]},
    )
    pilot._write_json(
        review_dir / "scoring-form.json",
        {"scores": {"proposition_completeness": None}, "review_status": "unscored"},
    )
    pilot._write_json(
        review_dir / "unblinding.json",
        {"review-case-01": {"Ledger A": "grok-4.3", "Ledger B": "grok-4.6"}},
    )
    pilot._verify_blinded_review_pack(review_dir)


def test_failed_prepare_preserves_existing_synthetic_early_stop_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "synthetic-early-stop-run"
    output.mkdir(mode=0o700)
    output.chmod(0o700)
    sentinel = output / "early-stop-evidence.json"
    sentinel_bytes = pilot.pretty_json_bytes(
        {"synthetic": True, "disposition": "blocked_before_calls"}
    )
    pilot._atomic_write(sentinel, sentinel_bytes)
    before_names = sorted(path.name for path in output.iterdir())
    before_mode = sentinel.stat().st_mode & 0o777
    selection_calls = 0

    def forbidden_selection() -> Any:
        nonlocal selection_calls
        selection_calls += 1
        raise AssertionError("selection ran before existing-run preservation guard")

    monkeypatch.setenv("XAI_API_KEY", "synthetic-key-never-sent")
    monkeypatch.setattr(pilot, "_tracked_input_record", _tracked)
    monkeypatch.setattr(pilot, "_prepare_selection_v2", forbidden_selection)
    with pytest.raises(pilot.PilotError, match="existing private run is not empty"):
        pilot.prepare_run(output)

    assert selection_calls == 0
    assert sentinel.read_bytes() == sentinel_bytes
    assert sentinel.stat().st_mode & 0o777 == before_mode == 0o600
    assert sorted(path.name for path in output.iterdir()) == before_names


def test_prior_ledger_is_revalidated_before_any_non_genesis_send(
    local_cases: Sequence[Mapping[str, Any]],
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    case = local_cases[0]
    first_turn = case["exact_private_transcript_prefix"][0]
    persisted_schema = schemas[2]
    valid = _process(
        _noop_delta(case, first_turn, None), case, first_turn, None, schemas
    )["ledger"]

    pilot._validate_prior_ledger_before_request(valid, case, 1, persisted_schema)
    pilot._validate_prior_ledger_before_request(None, case, 0, persisted_schema)

    invalid_values: list[tuple[Mapping[str, Any] | None, str]] = []
    bad_hash = copy.deepcopy(valid)
    bad_hash["ledger_sha256"] = "0" * 64
    invalid_values.append((bad_hash, "self-hash"))
    wrong_conversation = copy.deepcopy(valid)
    wrong_conversation["conversation_key"] = "dev-conversation-wrong-binding"
    wrong_conversation["ledger_sha256"] = semantic.phase1.ledger_sha256(
        wrong_conversation
    )
    invalid_values.append((wrong_conversation, "conversation binding"))
    wrong_turn = copy.deepcopy(valid)
    wrong_turn["as_of_turn_index"] = 7
    wrong_turn["ledger_sha256"] = semantic.phase1.ledger_sha256(wrong_turn)
    invalid_values.append((wrong_turn, "turn binding"))
    malformed = copy.deepcopy(valid)
    malformed.pop("ledger_id")
    invalid_values.append((malformed, "schema validation"))
    invalid_values.append((None, "lacks a validated prior ledger"))

    send_count = 0
    for invalid, message in invalid_values:
        with pytest.raises(pilot.PilotError, match=message):
            pilot._validate_prior_ledger_before_request(
                invalid, case, 1, persisted_schema
            )
            send_count += 1
    with pytest.raises(pilot.PilotError, match="genesis request unexpectedly"):
        pilot._validate_prior_ledger_before_request(
            valid, case, 0, persisted_schema
        )
        send_count += 1
    assert send_count == 0


# 52-53. Synthetic private/tracked artefacts contain neither the credential nor
# sealed/future content; protected text is never accessed to prove exclusion.
def test_prepared_private_outputs_exclude_credentials_future_and_sealed_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = "SYNTHETIC_XAI_SECRET_MUST_NOT_BE_RETAINED"
    output = _prepare_synthetic(monkeypatch, tmp_path, "privacy-run", key=secret)
    for path in output.rglob("*"):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        assert secret.encode() not in raw
        assert b"FORBIDDEN_FUTURE_TEXT" not in raw
        assert b"FORBIDDEN_HISTORICAL_REPLY" not in raw
        assert b"FORBIDDEN_PRODUCTION_OUTCOME" not in raw
        assert b"FORBIDDEN_AUDIT_LABEL" not in raw
    audit = pilot._load_json(
        output / "exposure-exclusion-audit.json", "test exclusion audit"
    )
    assert audit["protected_substantive_rows_read"] == 0
    assert audit["genuinely_unexposed_transcript_rows_read"] == 0
    assert audit["protected_transcript_text_bytes_read"] == 0
    assert audit["structurally_mined_only_cases_selected"] == 0
    assert audit["sealed_clean_prefixes_remained_unopened"] is True


# 54. The Phase 2A wrapper preserves the established helper semantics rather
# than altering the Phase 1.3/1.4 implementation.
def test_existing_preflight_live_probe_and_materialiser_semantics_are_preserved(
    local_cases: Sequence[Mapping[str, Any]],
    schemas: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    with pytest.raises(live_probe.StrictJSONError):
        live_probe.strict_json_loads(b'{"duplicate":1,"duplicate":2}')
    assert pilot.REQUEST_CONTRACT_REVISION == "phase1.4-no-tools-omit-tool-choice-v2"
    assert pilot.semantic.materialise_semantic_delta is semantic.materialise_semantic_delta
    assert pilot.preflight.intended_validation_errors is preflight.intended_validation_errors
    case, turn = local_cases[0], local_cases[0]["exact_private_transcript_prefix"][0]
    result = _process(_noop_delta(case, turn, None), case, turn, None, schemas)
    assert result["validation"]["persisted_ledger_status"] == "passed"
