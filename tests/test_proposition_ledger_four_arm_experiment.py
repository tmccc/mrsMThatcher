from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tools import proposition_ledger_four_arm_experiment as runner


PROJECT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", "utf-8")
    path.chmod(0o600)


def _tracked(path: str, version: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"path": path, "sha256": _sha(PROJECT / path)}
    if version:
        result["version"] = version
    return result


def _refresh_sums(case: Path) -> str:
    paths = sorted(
        path for path in case.rglob("*") if path.is_file() and path.name != "SHA256SUMS"
    )
    source = "".join(
        f"{_sha(path)}  {path.relative_to(case).as_posix()}\n" for path in paths
    ).encode()
    target = case / "SHA256SUMS"
    target.write_bytes(source)
    target.chmod(0o600)
    return hashlib.sha256(source).hexdigest()


def _freeze(case_hash: str) -> dict[str, Any]:
    summary_prompt = "proposition_ledger_research/phase2_experiment/ordinary-summary-system-prompt.txt"
    summary_schema = "proposition_ledger_research/phase2_experiment/ordinary-summary-schema.json"
    ledger_prompt = "proposition_ledger_research/phase2e/incremental-ledger-system-prompt-v4.txt"
    transport_schema = "proposition_ledger_research/schema/proposition-ledger-xai-transport-delta-v2.schema.json"
    downstream_prompt = "proposition_ledger_research/phase2_experiment/downstream-reply-system-prompt.txt"
    downstream_schema = "proposition_ledger_research/phase2_experiment/downstream-reply-schema.json"
    completeness = {
        "reconstruction_grade": "B", "exact_text_complete": True,
        "parent_graph_complete": False, "chronology_complete": True,
        "account_publication_confirmed": True, "complete_prefix_through_turn": True,
        "limitations": [
            "Some direct-parent evidence was not retained.",
            "Referenced relationships were not retained.",
        ],
    }
    return {
        "freeze_version": "four-arm-supplemental-freeze-v2",
        "protocol_status": "frozen_post_specification_amendment",
        "source_commit": "7c6cdd6d4db71c96789a120ed39420e9714e5236",
        "branch": "research/proposition-ledger-experiment",
        "contracts": {
            "canonical_semantic_delta": _tracked(
                "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json",
                "proposition-ledger-semantic-delta-v1.1.1",
            ),
            "xai_transport": _tracked(
                transport_schema, "proposition-ledger-xai-transport-delta-v2.0.1"
            ),
            "persisted_ledger": _tracked(
                "proposition_ledger_research/schema/proposition-ledger-v1.schema.json",
                "proposition-ledger-v1.0.0",
            ),
            "evidence_transport": _tracked("tools/proposition_ledger_evidence_transport.py"),
            "materialiser": _tracked("tools/proposition_ledger_semantic_delta.py"),
            "ledger_prompt": _tracked(ledger_prompt),
        },
        "profiles": {
            "arm_b_summary": {
                "provider": "xAI",
                "model": "grok-4.6",
                "reasoning_effort": "low",
                "max_output_tokens": 8192,
                "tools": [],
                "prompt_path": summary_prompt,
                "prompt_sha256": _sha(PROJECT / summary_prompt),
                "response_schema_path": summary_schema,
                "response_schema_sha256": _sha(PROJECT / summary_schema),
            },
            "arm_c_ledger": {
                "provider": "xAI",
                "model": "grok-4.6",
                "reasoning_effort": "low",
                "max_output_tokens": 8192,
                "tools": [],
                "prompt_path": ledger_prompt,
                "prompt_sha256": _sha(PROJECT / ledger_prompt),
                "response_schema_path": transport_schema,
                "response_schema_sha256": _sha(PROJECT / transport_schema),
                "provider_response_schema_sha256": runner.XAI_PROVIDER_SCHEMA_SHA256,
            },
            "downstream": {
                "provider": "OpenAI",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "medium",
                "max_output_tokens": 900,
                "timeout_seconds": 180,
                "temperature": 1,
                "store": False,
                "stream": False,
                "retries": 0,
                "tools": [],
                "postvalidator": "downstream-reply-contract-v1",
                "prompt_path": downstream_prompt,
                "prompt_sha256": _sha(PROJECT / downstream_prompt),
                "response_schema_path": downstream_schema,
                "response_schema_sha256": _sha(PROJECT / downstream_schema),
            },
        },
        "arms": [{"arm_id": arm} for arm in runner.ARMS],
        "accepted_evaluation_aliases": list(runner.ALIASES),
        "observed_context_policy": {
            "missing_exact_payload": "not_retained",
            "replay_disposition": "blocked",
        },
        "leakage_policy": {
            "future_turns_forbidden": True,
            "diagnosis_hidden": True,
            "later_model_outputs_forbidden": True,
        },
        "arm_d_gate": {
            **runner.ARM_D_GATE,
            "status": "pending_single_human_reference",
            "awareness_fields_required": [
                *runner.HUMAN_AWARENESS_FIELDS, "independent_of_machine_ledger",
            ],
        },
        "provider_policy": {
            "at_most_once": True,
            "retries": 0,
            "repair_calls": 0,
            "fallback_calls": 0,
            "preflight_calls": 0,
            "live_execution_enabled": False,
            "live_adapter_status": "not_configured",
            "live_verification_performed": False,
        },
        "private_case_freeze_sha256": case_hash,
        "source_completeness": completeness,
        "source_completeness_sha256": hashlib.sha256(
            runner.private_io.canonical_json_bytes(completeness)
        ).hexdigest(),
        "original_results_untouched": True,
        "protocol_amendment": _tracked(
            "proposition_ledger_research/phase2_experiment/"
            "protocol-amendment-single-human-reference-v2.md"
        ),
        "supersedes_freeze": _tracked(
            "proposition_ledger_research/phase2_experiment/experiment-freeze.json",
            "four-arm-supplemental-freeze-v1",
        ),
    }


def _observed(alias: str, target: str, as_of: str, cutoff: str) -> dict[str, Any]:
    return {
        "replay_input_id": f"observed-{alias}",
        "evaluation_alias": alias,
        "context_variant": "observed_production",
        "reply_target_turn_id": target,
        "ledger_as_of_turn_id": as_of,
        "context_cutoff_utc": cutoff,
        "exact_production_payload_status": "not_retained",
        "exact_production_payload": None,
        "turn_ids": [],
        "required_relevant_turn_ids": [],
        "excluded_turn_ids": [f"published-{alias}"],
        "no_future_validation": {"status": "not_verifiable_exact_payload_missing"},
        "canonical_input_sha256": None,
        "replay_eligibility": "blocked_exact_payload_not_retained",
    }


def _chronology(
    alias: str, target: str, ids: list[str], cutoff: str
) -> dict[str, Any]:
    return {
        "replay_input_id": f"chronology-{alias}",
        "evaluation_alias": alias,
        "context_variant": "chronology_aware",
        "reply_target_turn_id": target,
        "ledger_as_of_turn_id": ids[-1],
        "context_cutoff_utc": cutoff,
        "exact_production_payload_status": "diagnostic_reconstruction",
        "exact_production_payload": None,
        "turn_ids": ids,
        "required_relevant_turn_ids": ids,
        "excluded_turn_ids": [f"published-{alias}"],
        "no_future_validation": {"status": "passed"},
        "canonical_input_sha256": "",
        "replay_eligibility": "runnable",
    }


def _source_blank_chain(conversation_key: str, turn_ids: list[str]) -> dict[str, Any]:
    chain = []
    for index, turn_id in enumerate(turn_ids):
        chain.append({
            "turn_id": turn_id,
            "semantic_delta_template": {
                "schema_version": "proposition-ledger-semantic-delta-v1.1.1",
                "conversation_key": conversation_key, "target_turn_id": turn_id,
                "as_of_turn_index": index,
                "prior_ledger_reference": None if index == 0 else {
                    "ledger_id": "FILL_WITH_PREVIOUS_MATERIALISED_LEDGER_ID",
                    "as_of_turn_index": index - 1,
                },
                "new_propositions": [], "proposition_updates": [],
                "new_proposition_groups": [], "proposition_group_updates": [],
                "new_issue_states": [], "issue_state_updates": [],
                "commitment_changes": [], "obligation_changes": [], "new_relations": [],
                "answer_target_changes": [], "rejected_answer_target_changes": [],
                "repair_records": [], "resolved_items": [], "extraction_status": "complete",
                "abstentions": [], "unsupported_inferences_rejected": 0, "warnings": [],
            },
        })
    return {"template_version": "synthetic-reference-v1", "chain": chain}


def _add_transport_evidence(delta: dict[str, Any], selector: dict[str, Any]) -> None:
    delta["new_propositions"] = [{
        "local_ref": "new-proposition-1", "canonical_text": "The north gate is open.",
        "speaker_or_attributor": {
            "kind": "speaker", "participant_id": "participant-user",
            "attributed_participant_id": None,
        },
        "exact_evidence_spans": [selector], "original_language": "en",
        "speech_act": "assertion", "proposition_kind": "descriptive",
        "polarity": "positive", "modality": {"type": "none", "strength": "none"},
        "quantification": {"type": "none", "surface_marker": None},
        "temporal_scope": {"type": "timeless", "start": None, "end": None, "surface_marker": None},
        "epistemic_status": "asserted", "commitment_status": "speaker_committed",
        "lifecycle_status": "introduced", "proposition_group_ref": None,
        "derivation": {"kind": "direct_span", "source_proposition_refs": [], "normalisation_note": None},
        "confidence": 0.95, "uncertainty_reason": None,
    }]
    delta["commitment_changes"] = [{
        "operation": "add", "local_ref": "new-commitment-1",
        "participant_id": "participant-user", "proposition_ref": "new-proposition-1",
        "stance": "asserted", "basis": "explicit_speech_act", "confidence": 0.95,
        "uncertainty_reason": None, "exact_evidence_spans": [selector],
    }]


@pytest.fixture()
def frozen_case(tmp_path: Path) -> tuple[Path, Path]:
    case = tmp_path / "private-case"
    case.mkdir(mode=0o700)
    human = case / "human-arm-d-pack"
    human.mkdir(mode=0o700)
    turns = [
        {
            "turn_id": "turn-root", "turn_index": 0, "post_id": "source-post-alpha",
            "speaker_id": "participant-user", "role": "contributor",
            "created_at_utc": "2026-09-01T10:00:00Z", "parent_turn_id": None,
            "referenced_tweets": [],
            "text": "The north gate is open.",
            "relevant_evaluation_aliases": list(runner.ALIASES), "scope": "core",
        },
        {
            "turn_id": "turn-one", "turn_index": 1, "post_id": "source-post-beta",
            "speaker_id": "participant-user", "role": "contributor",
            "created_at_utc": "2026-09-01T10:01:00Z", "parent_turn_id": "turn-root",
            "referenced_tweets": [{"type": "replied_to", "id": "source-post-alpha"}],
            "text": "That premise is disputed.", "relevant_evaluation_aliases": list(runner.ALIASES),
            "scope": "core",
        },
        {
            "turn_id": "turn-two", "turn_index": 2, "post_id": "source-post-gamma",
            "speaker_id": "participant-user", "role": "contributor",
            "created_at_utc": "2026-09-01T10:02:00Z", "parent_turn_id": "turn-one",
            "referenced_tweets": [{"type": "replied_to", "id": "source-post-beta"}],
            "text": "Three checks will follow:",
            "relevant_evaluation_aliases": list(runner.ALIASES), "scope": "core",
        },
        {
            "turn_id": "turn-three", "turn_index": 3, "post_id": "source-post-delta",
            "speaker_id": "participant-user", "role": "contributor",
            "created_at_utc": "2026-09-01T10:03:00Z", "parent_turn_id": "turn-two",
            "referenced_tweets": [{"type": "replied_to", "id": "source-post-gamma"}],
            "text": "The second latch remains closed.",
            "relevant_evaluation_aliases": ["evaluation-03"], "scope": "core",
        },
        {
            "turn_id": "turn-separate", "turn_index": 4, "post_id": "source-post-epsilon",
            "speaker_id": "participant-user", "role": "contributor",
            "created_at_utc": "2026-09-01T12:00:00Z", "parent_turn_id": None,
            "referenced_tweets": [],
            "text": "A separate topic begins here.",
            "relevant_evaluation_aliases": [], "scope": "operational_only",
        },
    ]
    for turn in turns:
        turn["text_sha256"] = hashlib.sha256(turn["text"].encode()).hexdigest()
    transcript = {
        "case_slug": runner.CASE_SLUG,
        "conversation_key": "case-conversation-001",
        "participants": [],
        "turns": turns,
    }
    contexts = [
        _chronology("evaluation-01", "turn-one", ["turn-root", "turn-one", "turn-two"], "2026-09-01T10:02:30Z"),
        _chronology("evaluation-02", "turn-two", ["turn-root", "turn-one", "turn-two"], "2026-09-01T10:02:30Z"),
        _chronology("evaluation-03", "turn-three", ["turn-root", "turn-one", "turn-two", "turn-three"], "2026-09-01T10:03:30Z"),
    ]
    by_id = {turn["turn_id"]: turn for turn in turns}
    for context in contexts:
        payload = runner._context_payload(context, transcript, by_id)
        context["canonical_input_sha256"] = hashlib.sha256(
            runner.private_io.canonical_json_bytes(payload)
        ).hexdigest()
    evaluations = []
    for alias, target, as_of, cutoff in (
        ("evaluation-01", "turn-one", "turn-two", "2026-09-01T10:02:30Z"),
        ("evaluation-02", "turn-two", "turn-two", "2026-09-01T10:02:30Z"),
        ("evaluation-03", "turn-three", "turn-three", "2026-09-01T10:03:30Z"),
    ):
        evaluations.append({
            "evaluation_alias": alias, "accepted": True, "reply_target_turn_id": target,
            "ledger_as_of_turn_id": as_of, "context_cutoff_utc": cutoff,
            "observed_context_id": f"observed-{alias}",
            "chronology_aware_context_id": f"chronology-{alias}",
            "generation": {
                "context_build_at_utc": cutoff, "pipeline_approved_at_utc": cutoff,
                "generated_text_observed_at_utc": cutoff, "publication_at_utc": cutoff,
            },
        })
    common = {
        "case_slug": runner.CASE_SLUG,
        "status_labels": sorted(runner.STATUS_LABELS),
    }
    files = {
        "source-manifest.json": {
            **common, "source_hashes": {},
            "equality_side_effect": {
                "scope": "operational_only", "included_in_core": False,
                "included_in_semantic_ledger": False,
            },
            "original_prespecified_results_untouched": True,
        },
        "relationship-graph.json": {
            "case_slug": runner.CASE_SLUG,
            "nodes": [
                {"turn_id": turn["turn_id"], "scope": turn["scope"]} for turn in turns
            ],
            "edges": [
                {
                    "source_turn_id": child,
                    "target_turn_id": parent,
                    "relationship_type": relationship,
                    "evidence_basis": "synthetic fixture",
                }
                for child, parent, relationship in sorted(
                    runner._reconstructed_edges({turn["turn_id"]: turn for turn in turns})
                )
            ],
            "core_turn_ids": [turn["turn_id"] for turn in turns[:4]],
            "equality_turn_id": "turn-separate",
        },
        "chronology.json": {
            "case_slug": runner.CASE_SLUG,
            "events": [
                {"turn_id": "turn-one", "event_type": kind, "at_utc": f"2026-09-01T10:02:0{index}Z"}
                for index, kind in enumerate(
                    ("post_creation", "candidate_consideration", "model_generation", "publication")
                )
            ],
        },
        "evaluation-points.json": {
            "case_slug": runner.CASE_SLUG, "evaluation_points": evaluations
        },
        "observed-production-contexts.json": {
            "case_slug": runner.CASE_SLUG,
            "contexts": [
                _observed(row["evaluation_alias"], row["reply_target_turn_id"], row["ledger_as_of_turn_id"], row["context_cutoff_utc"])
                for row in evaluations
            ],
        },
        "chronology-aware-contexts.json": {
            "case_slug": runner.CASE_SLUG, "contexts": contexts
        },
        "replay-manifest.json": {
            **common, "accepted_evaluation_aliases": list(runner.ALIASES),
            "bindings": [
                {
                    "evaluation_alias": alias,
                    "observed_context_id": f"observed-{alias}",
                    "chronology_aware_context_id": f"chronology-{alias}",
                }
                for alias in runner.ALIASES
            ],
            "unique_chronology_replay_input_count": 3,
            "unique_representation_snapshot_count": 2,
            "prospective_downstream_arm_evaluation_count": 12,
            "equality_operational_side_effect_only": True,
            "original_prespecified_results_untouched": True,
        },
        "transcript.json": transcript,
        "post-annotation-adjudication.json": {
            "case_slug": runner.CASE_SLUG,
            "status": "sealed_until_blind_outputs_frozen",
            "diagnosis_visible_to_models": False,
        },
    }
    for name, value in files.items():
        _write_json(case / name, value)
    _write_json(human / "index.json", {
        "case_slug": runner.CASE_SLUG,
        "status": "pending_human_annotation",
        "not_gold": True,
        "snapshots": [],
    })
    _write_json(human / "annotation-guide.json", {
        "guide_version": "synthetic-neutral-guide-v1",
        "annotation_boundary": "Use only the supplied snapshot transcript.",
    })
    for snapshot_id, ids in (
        ("snapshot-01", ["turn-root", "turn-one", "turn-two"]),
        ("snapshot-02", ["turn-root", "turn-one", "turn-two", "turn-three"]),
    ):
        snapshot_dir = human / snapshot_id
        snapshot_dir.mkdir(mode=0o700)
        _write_json(
            snapshot_dir / "blank-semantic-delta-chain.json",
            _source_blank_chain(transcript["conversation_key"], ids),
        )
    case_hash = _refresh_sums(case)
    freeze = tmp_path / "experiment-freeze.json"
    _write_json(freeze, _freeze(case_hash))
    return case, freeze


def _rewrite(case: Path, freeze: Path, name: str, mutator: Any) -> None:
    path = case / name
    value = json.loads(path.read_text("utf-8"))
    mutator(value)
    _write_json(path, value)
    case_hash = _refresh_sums(case)
    frozen = json.loads(freeze.read_text("utf-8"))
    frozen["private_case_freeze_sha256"] = case_hash
    _write_json(freeze, frozen)


def _enable_live(freeze: Path) -> None:
    value = json.loads(freeze.read_text("utf-8"))
    value["provider_policy"].update(
        live_execution_enabled=True,
        live_adapter_status="implemented_offline_reviewed_not_live_verified",
        live_verification_performed=False,
    )
    _write_json(freeze, value)


def _fake_response(request: dict[str, Any]) -> dict[str, Any]:
    if request["operation"] == "arm_b_ordinary_summary":
        return {"summary": "A neutral synthetic summary."}
    if request["operation"] == "downstream_arm_evaluation":
        return {"status": "reply", "reply": "A short synthetic reply."}
    supplied = json.loads(request["user_payload"])
    prior = supplied["validated_prior_persisted_ledger"]
    return {
        "schema_version": "proposition-ledger-xai-transport-delta-v2.0.1",
        "canonical_schema_version": "proposition-ledger-semantic-delta-v1.1.1",
        "conversation_key": supplied["pilot_local_conversation_key"],
        "target_turn_id": supplied["pilot_local_current_turn_id"],
        "as_of_turn_index": supplied["turn_index"],
        "prior_ledger_reference": None if prior is None else {
            "ledger_id": prior["ledger_id"], "as_of_turn_index": prior["as_of_turn_index"],
        },
        "new_propositions": [], "proposition_updates": [],
        "new_proposition_groups": [], "proposition_group_updates": [],
        "new_issue_states": [], "issue_state_updates": [], "commitment_changes": [],
        "obligation_changes": [], "new_relations": [], "answer_target_changes": [],
        "rejected_answer_target_changes": [], "repair_records": [], "resolved_items": [],
        "extraction_status": "complete", "abstentions": [],
        "unsupported_inferences_rejected": 0, "warnings": [],
    }


def _complete_single_human_reference(
    output: Path, *, annotator_id: str = "investigator-user",
    knew_working_hypothesis: bool = True,
    previously_seen_published_replies: bool = True,
    was_investigator: bool = True,
    independent_of_machine_ledger: bool = True,
    machine_output_revealed_before_lock: bool = False,
) -> None:
    human_root = output / runner.PREPARED_HUMAN_DIR
    provenance_path = human_root / "provenance.json"
    provenance = json.loads(provenance_path.read_text("utf-8"))
    provenance.update({
        "status": "completed_human_provenance", "actor_type": "human",
        "annotator_id": annotator_id,
        "knew_working_hypothesis": knew_working_hypothesis,
        "previously_seen_published_replies": previously_seen_published_replies,
        "was_investigator": was_investigator,
        "independent_of_machine_ledger": independent_of_machine_ledger,
        "machine_output_revealed_before_lock": machine_output_revealed_before_lock,
        "completed_at_utc": "2020-01-01T00:02:00Z",
    })
    _write_json(provenance_path, provenance)
    for number, snapshot_id in enumerate(("snapshot-01", "snapshot-02"), start=1):
        directory = human_root / snapshot_id
        chain = json.loads(
            (directory / "semantic-delta-chain-template.json").read_text("utf-8")
        )["semantic_delta_chain"]
        reference_path = directory / "reference.json"
        reference = json.loads(reference_path.read_text("utf-8"))
        reference.update({
            "status": "completed_human_reference", "actor_type": "human",
            "annotator_id": annotator_id,
            "semantic_judgements_authored_by_human": True,
            "semantic_delta_chain": chain,
            "completed_at_utc": f"2020-01-01T00:0{number - 1}:00Z",
        })
        _write_json(reference_path, reference)


def test_validate_prepare_verify_are_offline_and_deduplicated(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    validation = runner.validate_case(case, experiment_freeze=freeze)
    assert validation["provider_calls"] == 0
    assert validation["unique_arm_evaluations"] == 12
    assert validation["unique_representation_snapshots"] == 2
    output = tmp_path / "prepared-run"
    prepared = runner.prepare_run(case, output, experiment_freeze=freeze)
    assert prepared["provider_calls"] == 0
    assert prepared["planned_provider_calls"] == 18  # four C turns + two B + 12 downstream
    verified = runner.verify_run(output)
    assert verified["provider_calls"] == 0
    assert verified["arm_d_status"] == "pending_human_reference"


def test_freeze_rejects_obsolete_multi_human_gate_fields(
    frozen_case: tuple[Path, Path]
) -> None:
    _, freeze = frozen_case
    value = json.loads(freeze.read_text("utf-8"))
    value["arm_d_gate"]["human_rater_count"] = 2
    _write_json(freeze, value)
    with pytest.raises(runner.FourArmError, match="single-human reference gate differs"):
        runner.validate_freeze(freeze)


def test_observed_payload_is_never_invented(
    frozen_case: tuple[Path, Path]
) -> None:
    case, freeze = frozen_case
    _rewrite(
        case,
        freeze,
        "observed-production-contexts.json",
        lambda value: value["contexts"][0].update(
            exact_production_payload={"invented": True}
        ),
    )
    with pytest.raises(runner.FourArmError, match="incorrectly replayable"):
        runner.validate_case(case, experiment_freeze=freeze)


def test_future_or_missing_relevant_turn_is_rejected(
    frozen_case: tuple[Path, Path]
) -> None:
    case, freeze = frozen_case
    _rewrite(
        case,
        freeze,
        "chronology-aware-contexts.json",
        lambda value: value["contexts"][0]["turn_ids"].remove("turn-two"),
    )
    with pytest.raises(runner.FourArmError):
        runner.validate_case(case, experiment_freeze=freeze)


def test_arm_b_is_independent_and_raw_post_ids_are_not_model_bound(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    plan = json.loads((output / "call-plan.json").read_text("utf-8"))
    summaries = [row for row in plan["entries"] if row["operation"] == "arm_b_ordinary_summary"]
    assert len(summaries) == 2
    assert all(row["dependency_call_ids"] == [] for row in summaries)
    replay = (output / "replay-plan.json").read_text("utf-8")
    assert "source-post-alpha" not in replay
    assert set(json.loads(replay)["runnable_replays"][0]["arm_inputs"]) == set(runner.ARMS)


def test_raw_provider_bound_speaker_id_is_rejected(
    frozen_case: tuple[Path, Path]
) -> None:
    case, freeze = frozen_case
    def mutate(value: dict[str, Any]) -> None:
        value["turns"][0]["speaker_id"] = "9" * 18
    _rewrite(case, freeze, "transcript.json", mutate)
    with pytest.raises(runner.FourArmError, match="raw source identity"):
        runner.validate_case(case, experiment_freeze=freeze)


def test_pending_human_gate_precedes_provider_factory(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    prepared = runner.prepare_run(case, output, experiment_freeze=freeze)
    constructions = 0
    def factory() -> Any:
        nonlocal constructions
        constructions += 1
        raise AssertionError("provider constructed")
    with pytest.raises(runner.FourArmError, match="genuine human"):
        runner.run_experiment(
            output,
            confirm_arm_evaluations=12,
            confirm_provider_call_budget=prepared["planned_provider_calls"],
            provider_factory=factory,
        )
    assert constructions == 0


def test_downstream_postvalidation_and_zero_retry_are_hash_bound(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    plan = json.loads((output / "call-plan.json").read_text("utf-8"))
    downstream = next(
        row for row in plan["entries"] if row["operation"] == "downstream_arm_evaluation"
    )
    validator = downstream["postvalidation"][-1]
    assert validator["retry_on_failure"] is False
    assert "<= 270" in validator["status_reply"]
    assert "empty string" in validator["status_cannot_compose_safely"]
    assert plan["automatic_retry_count"] == plan["repair_call_count"] == 0


def test_permission_or_checksum_tampering_fails_closed(
    frozen_case: tuple[Path, Path]
) -> None:
    case, freeze = frozen_case
    (case / "transcript.json").chmod(0o644)
    with pytest.raises(runner.FourArmError, match="0600"):
        runner.validate_case(case, experiment_freeze=freeze)


def test_graph_requires_both_parent_and_referenced_tweet_edges(
    frozen_case: tuple[Path, Path]
) -> None:
    case, freeze = frozen_case
    _rewrite(
        case,
        freeze,
        "relationship-graph.json",
        lambda value: value["edges"].pop(0),
    )
    with pytest.raises(runner.FourArmError, match="referenced-tweet evidence"):
        runner.validate_case(case, experiment_freeze=freeze)


@pytest.mark.parametrize("defect", ["index", "parent"])
def test_transcript_indices_and_parents_are_strict(
    frozen_case: tuple[Path, Path], defect: str
) -> None:
    case, freeze = frozen_case
    def mutate(value: dict[str, Any]) -> None:
        if defect == "index":
            value["turns"][2]["turn_index"] = 9
        else:
            value["turns"][2]["parent_turn_id"] = "missing-parent"
    _rewrite(case, freeze, "transcript.json", mutate)
    with pytest.raises(runner.FourArmError, match="indices|parent"):
        runner.validate_case(case, experiment_freeze=freeze)


def test_chronology_keeps_four_timing_stages_distinct(
    frozen_case: tuple[Path, Path]
) -> None:
    case, freeze = frozen_case
    _rewrite(
        case,
        freeze,
        "chronology.json",
        lambda value: value["events"][2].update(event_type="candidate_consideration"),
    )
    with pytest.raises(runner.FourArmError, match="collapses creation"):
        runner.validate_case(case, experiment_freeze=freeze)


def test_equality_side_effect_cannot_link_into_core(
    frozen_case: tuple[Path, Path]
) -> None:
    case, freeze = frozen_case
    _rewrite(
        case,
        freeze,
        "relationship-graph.json",
        lambda value: value["edges"].append(
            {
                "source_turn_id": "turn-separate",
                "target_turn_id": "turn-root",
                "relationship_type": "direct_parent",
                "evidence_basis": "invalid synthetic edge",
            }
        ),
    )
    with pytest.raises(runner.FourArmError, match="linked into core"):
        runner.validate_case(case, experiment_freeze=freeze)


def test_fixed_three_evaluations_arm_isolation_and_incremental_reuse(
    frozen_case: tuple[Path, Path]
) -> None:
    case, freeze = frozen_case
    result = runner.validate_case(case, experiment_freeze=freeze)
    plan = result["replay_plan"]
    assert [row["evaluation_alias"] for row in plan["runnable_replays"]] == list(
        runner.ALIASES
    )
    assert len(plan["representation_snapshots"]) == 2
    shorter, longer = plan["representation_snapshots"]
    assert longer["turn_ids"][: len(shorter["turn_ids"])] == shorter["turn_ids"]
    for replay in plan["runnable_replays"]:
        assert tuple(replay["arm_inputs"]) == runner.ARMS
        assert set(replay["arm_inputs"]["A"]) == {"transcript_sha256", "representation"}
        assert "ordinary_summary_snapshot" in replay["arm_inputs"]["B"]
        assert "machine_ledger_snapshot" in replay["arm_inputs"]["C"]
        assert "human_ledger_snapshot" in replay["arm_inputs"]["D"]


def test_tracked_sanitized_case_shape_preserves_the_29_call_plan() -> None:
    context_path = (
        PROJECT / "proposition_ledger_research/phase2_experiment/cases/"
        "market-planning-live-20260901/chronology-aware-contexts.json"
    )
    contexts = json.loads(context_path.read_text("utf-8"))["contexts"]
    snapshots: dict[str, dict[str, Any]] = {}
    for context in contexts:
        snapshot_id = context["representation_snapshot_id"]
        snapshot = snapshots.setdefault(snapshot_id, {
            "snapshot_id": snapshot_id,
            "turn_ids": context["turn_ids"],
            "evaluation_aliases": [],
        })
        assert snapshot["turn_ids"] == context["turn_ids"]
        snapshot["evaluation_aliases"].append(context["evaluation_alias"])
    replay_shape = {
        "representation_snapshots": list(snapshots.values()),
        "runnable_replays": [{
            "evaluation_alias": context["evaluation_alias"],
            "representation_snapshot_id": context["representation_snapshot_id"],
        } for context in contexts],
    }
    freeze_raw = runner.DEFAULT_FREEZE.read_bytes()
    plan = runner._call_plan(
        replay_shape, runner.validate_freeze(), hashlib.sha256(freeze_raw).hexdigest()
    )
    operations = [row["operation"] for row in plan["entries"]]
    assert operations == (
        ["arm_c_incremental_delta"] * 15
        + ["arm_b_ordinary_summary"] * 2
        + ["downstream_arm_evaluation"] * 12
    )
    assert plan["planned_provider_call_count"] == 29
    assert plan["incremental_arm_c_call_count"] == 15
    assert plan["arm_b_summary_call_count"] == 2
    assert plan["unique_arm_evaluations"] == 12
    assert [
        row["identity"] for row in plan["entries"] if row["operation"] == "downstream_arm_evaluation"
    ] == [f"{alias}-arm-{arm}" for alias in runner.ALIASES for arm in runner.ARMS]
    ledger = runner._call_ledger(plan)
    assert ledger["provider_call_count"] == 0
    assert {row["state"] for row in ledger["entries"]} == {"planned"}
    assert {row["provider_call_count"] for row in ledger["entries"]} == {0}


def test_call_state_cannot_claim_sending_without_recorded_call(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    path = output / "call-ledger.json"
    ledger = json.loads(path.read_text("utf-8"))
    ledger["entries"][0]["state"] = "sending"
    ledger["entries"][0]["state_history"].append(
        {"state": "sending", "at_utc": "2026-09-01T00:00:00Z", "reason": "invalid"}
    )
    _write_json(path, ledger)
    with pytest.raises(runner.FourArmError, match="recorded provider count"):
        runner.verify_run(output)


def test_verify_derives_nonpristine_provider_count_from_durable_ledger(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    requests = output / "requests"
    requests.mkdir(mode=0o700)
    request = requests / "call-001-ledger-turn-root.json"
    request.write_bytes(b"{}")
    request.chmod(0o600)
    path = output / "call-ledger.json"
    ledger = json.loads(path.read_text("utf-8"))
    row = ledger["entries"][0]
    row.update({
        "state": "failed_provider", "attempt_number": 1, "provider_call_count": 1,
        "request_path": "requests/call-001-ledger-turn-root.json",
        "request_sha256": hashlib.sha256(b"{}").hexdigest(),
        "started_at_utc": "2026-09-01T00:00:00Z",
        "completed_at_utc": "2026-09-01T00:00:01Z",
    })
    row["state_history"].extend([
        {"state": "sending", "at_utc": "2026-09-01T00:00:00Z", "reason": "test"},
        {"state": "failed_provider", "at_utc": "2026-09-01T00:00:01Z", "reason": "test"},
    ])
    ledger["provider_call_count"] = 1
    _write_json(path, ledger)
    verified = runner.verify_run(output)
    assert verified["provider_calls"] == verified["recorded_provider_calls"] == 1
    assert verified["verification_provider_calls"] == 0
    assert verified["call_ledger_unchanged"] is False


def test_fake_adapter_executes_exact_plan_at_most_once(
    frozen_case: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, freeze = frozen_case
    _enable_live(freeze)
    output = tmp_path / "prepared-run"
    prepared = runner.prepare_run(case, output, experiment_freeze=freeze)
    monkeypatch.setattr(runner, "_human_gate", lambda *_args, **_kwargs: {"status": "locked"})
    monkeypatch.setattr(runner, "_human_ledgers", lambda _root: {
        "snapshot-01": {"ledger_id": "human-ledger-one", "propositions": []},
        "snapshot-02": {"ledger_id": "human-ledger-two", "propositions": []},
    })
    monkeypatch.setattr(runner, "_live_git_gate", lambda _freeze: None)
    calls: list[dict[str, Any]] = []
    constructions = 0

    def factory() -> Any:
        nonlocal constructions
        constructions += 1
        def sample(request: dict[str, Any]) -> dict[str, Any]:
            calls.append(request)
            return _fake_response(request)
        return sample

    result = runner.run_experiment(
        output, confirm_arm_evaluations=12,
        confirm_provider_call_budget=prepared["planned_provider_calls"],
        provider_factory=factory,
    )
    assert constructions == 1
    assert result["status"] == "completed"
    assert result["provider_calls"] == len(calls) == 18
    assert result["downstream_completed"] == 12
    c_request = calls[0]
    c_payload = json.loads(c_request["user_payload"])
    assert set(c_payload) == {
        "protocol_version", "protocol_hash", "pilot_local_conversation_key",
        "pilot_local_current_turn_id", "turn_index", "pilot_local_parent_turn_id",
        "exact_current_visible_text", "trusted_current_speaker_participant_descriptor",
        "validated_prior_persisted_ledger", "response_contract_manifest",
    }
    assert c_payload["response_contract_manifest"]["xai_provider_schema_sha256"] == (
        runner.XAI_PROVIDER_SCHEMA_SHA256
    )
    assert hashlib.sha256(runner.private_io.canonical_json_bytes(c_request["response_schema"])).hexdigest() == (
        runner.XAI_PROVIDER_SCHEMA_SHA256
    )
    ledger = json.loads((output / "call-ledger.json").read_text("utf-8"))
    assert {row["state"] for row in ledger["entries"]} == {"completed"}
    assert all(
        isinstance(row["response_utf8_bytes"], int)
        and isinstance(row["response_characters"], int)
        for row in ledger["entries"]
        if row["operation"] in {"arm_b_ordinary_summary", "arm_c_incremental_delta"}
    )
    assert len(list((output / "products").glob("*.json"))) == 18
    verified = runner.verify_run(output)
    assert verified["provider_calls"] == 18
    assert verified["call_ledger_unchanged"] is False
    with pytest.raises(runner.FourArmError, match="not pristine"):
        runner.run_experiment(
            output, confirm_arm_evaluations=12,
            confirm_provider_call_budget=prepared["planned_provider_calls"],
            provider_factory=factory,
        )
    assert constructions == 1


def test_failed_c_blocks_only_its_dependants(
    frozen_case: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, freeze = frozen_case
    _enable_live(freeze)
    output = tmp_path / "prepared-run"
    prepared = runner.prepare_run(case, output, experiment_freeze=freeze)
    monkeypatch.setattr(runner, "_human_gate", lambda *_args, **_kwargs: {"status": "locked"})
    monkeypatch.setattr(runner, "_human_ledgers", lambda _root: {
        "snapshot-01": {"ledger_id": "human-ledger-one"},
        "snapshot-02": {"ledger_id": "human-ledger-two"},
    })
    monkeypatch.setattr(runner, "_live_git_gate", lambda _freeze: None)

    def sample(request: dict[str, Any]) -> dict[str, Any]:
        return {} if request["operation"] == "arm_c_incremental_delta" else _fake_response(request)

    result = runner.run_experiment(
        output, confirm_arm_evaluations=12,
        confirm_provider_call_budget=prepared["planned_provider_calls"],
        provider_factory=lambda: sample,
    )
    assert result["provider_calls"] == 12
    assert result["downstream_completed"] == 9
    assert result["states"] == {
        "failed_validation": 1, "blocked_dependency": 6, "completed": 11,
    }


def test_single_human_pack_is_brief_separate_by_snapshot_and_blind_to_machine_outputs(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    human_root = output / runner.PREPARED_HUMAN_DIR
    pack = json.loads(
        (human_root / "snapshot-01/reference-pack.json").read_text("utf-8")
    )
    assert set(pack["blank_semantic_delta_sections"]) == {
        "new_propositions", "new_issue_states", "commitment_changes",
        "obligation_changes", "new_relations",
    }
    command = pack["annotation_guidance"]["validation_and_lock_command"]
    assert command == (
        "python3 -m tools.proposition_ledger_four_arm_experiment --verify "
        f"--lock-human-reference --output {output}"
    )
    assert pack["source_completeness"]["reconstruction_grade"] == "B"
    assert pack["source_completeness"]["parent_graph_complete"] is False
    readme = (human_root / "README.md").read_text("utf-8")
    assert "provenance.json" in readme and "snapshot-NN/reference.json" in readme
    assert "exact text" in readme and "zero-based" in readme
    assert "--verify --lock-human-reference" in readme
    assert "No machine output may be opened before" in readme
    assert "copy its completed `semantic_delta_chain` unchanged" in readme
    assert "rejects any retroactive prefix rewrite" in readme
    assert "rater-2" not in readme and "adjudication" not in readme and "gold" not in readme
    assert {path.name for path in (human_root / "frozen-guidance").iterdir()} == {
        "annotation-guide.json", "REFERENCE-ONLY.txt",
    }
    guide = json.loads(
        (human_root / "frozen-guidance/annotation-guide.json").read_text("utf-8")
    )
    assert guide["guide_version"] == "single-human-transcript-first-guide-v1"
    assert "shared prefix" in guide["incremental_rule"]
    snapshot_one = json.loads(
        (human_root / "snapshot-01/semantic-delta-chain-template.json").read_text("utf-8")
    )["semantic_delta_chain"]
    snapshot_two = json.loads(
        (human_root / "snapshot-02/semantic-delta-chain-template.json").read_text("utf-8")
    )["semantic_delta_chain"]
    assert len(snapshot_one) < len(snapshot_two)
    assert snapshot_two[: len(snapshot_one)] == snapshot_one
    visible = "\n".join(
        f"{path.relative_to(human_root).as_posix()}\n{path.read_text('utf-8')}"
        for path in sorted(human_root.rglob("*")) if path.is_file()
    )
    assert runner.HUMAN_VISIBLE_FORBIDDEN.search(visible) is None


def test_pending_model_authorship_and_partial_human_input_fail_closed(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    path = output / runner.PREPARED_HUMAN_DIR / "snapshot-01/reference.json"
    reference = json.loads(path.read_text("utf-8"))
    reference["actor_type"] = "model"
    _write_json(path, reference)
    with pytest.raises(runner.FourArmError, match="model/non-human"):
        runner.verify_run(output)

    reference["actor_type"] = "human"
    reference["status"] = "completed_human_reference"
    _write_json(path, reference)
    with pytest.raises(runner.FourArmError, match="incomplete"):
        runner.verify_run(output)


def test_one_investigator_human_can_lock_both_snapshot_references_offline(
    frozen_case: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    prepared = runner.prepare_run(case, output, experiment_freeze=freeze)
    original_plan = (output / "call-plan.json").read_bytes()
    _complete_single_human_reference(output)
    submitted = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [
            output / runner.PREPARED_HUMAN_DIR / "provenance.json",
            output / runner.PREPARED_HUMAN_DIR / "snapshot-01/reference.json",
            output / runner.PREPARED_HUMAN_DIR / "snapshot-02/reference.json",
        ]
    }
    monkeypatch.setattr(
        runner, "ReviewedProviderAdapter",
        lambda: (_ for _ in ()).throw(AssertionError("provider constructed")),
    )
    assert runner.main([
        "--verify", "--lock-human-reference", "--output", str(output),
        "--experiment-freeze", str(freeze),
    ]) == 0
    verified = runner.verify_run(output)
    assert verified["arm_d_status"] == "locked"
    assert verified["provider_calls"] == 0
    assert prepared["planned_provider_calls"] == 18
    assert (output / "call-plan.json").read_bytes() == original_plan
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in submitted.items())
    materialised = json.loads(
        (output / runner.PREPARED_HUMAN_DIR / "materialised-reference.json").read_text("utf-8")
    )
    assert [row["snapshot_id"] for row in materialised["snapshot_ledgers"]] == [
        "snapshot-01", "snapshot-02",
    ]
    provenance = json.loads(
        (output / runner.PREPARED_HUMAN_DIR / "provenance.json").read_text("utf-8")
    )
    assert provenance["knew_working_hypothesis"] is True
    assert provenance["previously_seen_published_replies"] is True
    assert provenance["was_investigator"] is True
    assert provenance["independent_of_machine_ledger"] is True


def test_snapshot_two_cannot_retroactively_rewrite_completed_shared_prefix(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    _complete_single_human_reference(output)
    path = output / runner.PREPARED_HUMAN_DIR / "snapshot-02/reference.json"
    reference = json.loads(path.read_text("utf-8"))
    reference["semantic_delta_chain"][0]["warnings"] = ["retroactive rewrite"]
    _write_json(path, reference)
    with pytest.raises(runner.FourArmError, match="shared semantic-delta prefix differs"):
        runner.lock_human_reference(output)
    human_root = output / runner.PREPARED_HUMAN_DIR
    assert not (human_root / "materialised-reference.json").exists()
    assert not (human_root / "reference-lock.json").exists()


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("machine_reveal", "invalid or conceals awareness"),
        ("machine_actor", "not authored by the provenance human"),
        ("codex_identity", "genuine human authorship"),
        ("missing_awareness", "invalid or conceals awareness"),
        ("machine_ledger_dependence", "invalid or conceals awareness"),
    ],
)
def test_invalid_human_provenance_cannot_lock(
    frozen_case: tuple[Path, Path], tmp_path: Path, defect: str, message: str
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    _complete_single_human_reference(output)
    human_root = output / runner.PREPARED_HUMAN_DIR
    provenance_path = human_root / "provenance.json"
    provenance = json.loads(provenance_path.read_text("utf-8"))
    if defect == "machine_reveal":
        provenance["machine_output_revealed_before_lock"] = True
    elif defect == "codex_identity":
        provenance["annotator_id"] = "Codex"
    elif defect == "missing_awareness":
        provenance["knew_working_hypothesis"] = None
    elif defect == "machine_ledger_dependence":
        provenance["independent_of_machine_ledger"] = False
    else:
        reference_path = human_root / "snapshot-01/reference.json"
        reference = json.loads(reference_path.read_text("utf-8"))
        reference["actor_type"] = "model"
        _write_json(reference_path, reference)
    _write_json(provenance_path, provenance)
    with pytest.raises(runner.FourArmError, match=message):
        runner.lock_human_reference(output)
    assert not (human_root / "materialised-reference.json").exists()
    assert not (human_root / "reference-lock.json").exists()


def test_existing_machine_artifact_prevents_reference_lock(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    _complete_single_human_reference(output)
    products = output / "products"
    products.mkdir(mode=0o700)
    artifact = products / "machine-output.json"
    artifact.write_bytes(b"{}")
    artifact.chmod(0o600)
    with pytest.raises(runner.FourArmError, match="before any experiment machine output"):
        runner.lock_human_reference(output)
    assert not (output / runner.PREPARED_HUMAN_DIR / "reference-lock.json").exists()


def test_template_chain_prior_sentinel_is_resolved_without_mutating_submission(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    _complete_single_human_reference(output)
    snapshot = output / runner.PREPARED_HUMAN_DIR / "snapshot-01"
    path = snapshot / "reference.json"
    reference = json.loads(path.read_text("utf-8"))
    chain = reference["semantic_delta_chain"]
    assert chain[1]["prior_ledger_reference"]["ledger_id"] == runner.PRIOR_LEDGER_SENTINEL
    submitted_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    runner.lock_human_reference(output)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == submitted_sha


def test_human_transport_selector_resolves_before_canonical_materialisation(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    snapshot = output / runner.PREPARED_HUMAN_DIR / "snapshot-01"
    pack = json.loads((snapshot / "reference-pack.json").read_text("utf-8"))
    template = json.loads((snapshot / "semantic-delta-chain-template.json").read_text("utf-8"))
    chain = template["semantic_delta_chain"]
    assert pack["annotation_contract"]["input_schema_version"] == runner.HUMAN_SELECTOR_VERSION
    assert pack["annotation_contract"]["evidence_selector_contract_version"] == (
        "exact-text-occurrence-index-v2.0.1"
    )
    assert "transport_schema" not in pack["annotation_contract"]
    assert chain[0]["schema_version"] == runner.HUMAN_SELECTOR_VERSION
    _add_transport_evidence(chain[0], {"exact_text": "north gate", "occurrence_index": 0})
    ledger = runner._materialised_ledger(
        pack, chain,
        transport_binding=runner.validate_freeze(freeze)["contracts"]["xai_transport"],
    )
    span = ledger["propositions"][0]["exact_evidence_spans"][0]
    assert span == {
        "turn_id": "turn-root", "start_char": 4, "end_char": 14,
        "exact_text": "north gate",
    }
    assert "occurrence_index" not in json.dumps(ledger)


@pytest.mark.parametrize(
    "selector,status",
    [
        ({"exact_text": "north gate", "occurrence_index": 9}, "evidence_occurrence_index_out_of_range"),
        ({"exact_text": "absent phrase", "occurrence_index": 0}, "evidence_exact_text_not_found"),
    ],
)
def test_human_transport_selector_rejects_unresolved_evidence(
    frozen_case: tuple[Path, Path], tmp_path: Path,
    selector: dict[str, Any], status: str,
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    snapshot = output / runner.PREPARED_HUMAN_DIR / "snapshot-01"
    pack = json.loads((snapshot / "reference-pack.json").read_text("utf-8"))
    chain = json.loads(
        (snapshot / "semantic-delta-chain-template.json").read_text("utf-8")
    )["semantic_delta_chain"]
    _add_transport_evidence(chain[0], selector)
    with pytest.raises(runner.FourArmError, match=status):
        runner._materialised_ledger(
            pack, chain,
            transport_binding=runner.validate_freeze(freeze)["contracts"]["xai_transport"],
        )
