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
        "freeze_version": "four-arm-supplemental-freeze-v4",
        "protocol_status": "frozen_post_specification_amendment",
        "source_commit": "7c6cdd6d4db71c96789a120ed39420e9714e5236",
        "branch": "research/proposition-ledger-experiment",
        "contracts": {
            "canonical_semantic_delta": _tracked(
                "proposition_ledger_research/schema/proposition-ledger-semantic-delta-v1.schema.json",
                runner.VERSIONS["canonical_semantic_delta"],
            ),
            "xai_transport": _tracked(
                transport_schema, runner.VERSIONS["xai_transport"]
            ),
            "persisted_ledger": _tracked(
                "proposition_ledger_research/schema/proposition-ledger-v1.schema.json",
                runner.VERSIONS["persisted_ledger"],
            ),
            "evidence_transport": _tracked("tools/proposition_ledger_evidence_transport.py"),
            "materialiser": _tracked(
                "tools/proposition_ledger_semantic_delta.py",
                runner.VERSIONS["materialiser"],
            ),
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
        "human_reference_workflow": copy.deepcopy(runner.HUMAN_REFERENCE_WORKFLOW),
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
            "protocol-amendment-complete-human-reference-v3.md"
        ),
        "supersedes_freeze": _tracked(
            "proposition_ledger_research/phase2_experiment/experiment-freeze-v3.json",
            "four-arm-supplemental-freeze-v3",
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
                "schema_version": runner.VERSIONS["canonical_semantic_delta"],
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
        "canonical_text": "The north gate is open.",
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
        "operation": "add",
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
        "schema_version": runner.VERSIONS["xai_transport"],
        "canonical_schema_version": runner.VERSIONS["canonical_semantic_delta"],
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


def _empty_human_judgements(pack: dict[str, Any]) -> dict[str, Any]:
    transport = json.loads(
        (PROJECT / "proposition_ledger_research/schema/"
         "proposition-ledger-xai-transport-delta-v2.schema.json").read_text("utf-8")
    )
    return {
        field: (
            [] if transport["properties"][field].get("type") == "array"
            else 0 if transport["properties"][field].get("type") == "integer"
            else "complete"
        ) for field in pack["annotation_contract"]["editable_semantic_fields"]
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
    pack = json.loads(
        (human_root / "frozen-guidance" / runner.HUMAN_REFERENCE_PACK_FILE).read_text("utf-8")
    )
    judgements = _empty_human_judgements(pack)
    chain = [
        runner._human_delta(pack, index, judgements)
        for index in range(len(pack["transcript"]["turns"]))
    ]
    reference_path = human_root / runner.HUMAN_REFERENCE_CHAIN_FILE
    reference = json.loads(reference_path.read_text("utf-8"))
    reference.update({
        "status": "completed_human_reference",
        "semantic_judgements_authored_by_human": True,
        "semantic_delta_chain": chain,
        "completed_at_utc": "2020-01-01T00:01:00Z",
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


def _prepared_human_pack(output: Path) -> tuple[Path, dict[str, Any]]:
    human_root = output / runner.PREPARED_HUMAN_DIR
    pack = json.loads(
        (human_root / "frozen-guidance" / runner.HUMAN_REFERENCE_PACK_FILE).read_text(
            "utf-8"
        )
    )
    return human_root, pack


def test_single_human_pack_is_brief_complete_one_chain_and_machine_blind(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    human_root, pack = _prepared_human_pack(output)
    editable = set(pack["annotation_contract"]["editable_semantic_fields"])
    assert editable == {
        "new_propositions", "proposition_updates", "new_proposition_groups",
        "proposition_group_updates", "new_issue_states", "issue_state_updates",
        "commitment_changes", "obligation_changes", "new_relations",
        "answer_target_changes", "rejected_answer_target_changes", "repair_records",
        "resolved_items", "extraction_status", "abstentions",
        "unsupported_inferences_rejected", "warnings",
    }
    assert pack["source_completeness"]["reconstruction_grade"] == "B"
    assert pack["source_completeness"]["parent_graph_complete"] is False
    assert [row["terminal_turn_id"] for row in pack["snapshot_bindings"]] == [
        "turn-two", "turn-three",
    ]
    reference = json.loads(
        (human_root / runner.HUMAN_REFERENCE_CHAIN_FILE).read_text("utf-8")
    )
    assert reference["semantic_delta_chain"] == []
    assert not list(human_root.glob("snapshot-*"))
    readme = (human_root / "README.md").read_text("utf-8")
    assert "provenance.json" in readme and "reference-chain.json" in readme
    assert "exact current-turn text" in readme and "zero-based" in readme
    assert "--annotate-human-reference" in readme
    assert "--verify --lock-human-reference" in readme
    assert "Do not open any experiment-generated machine output" in readme
    assert "rater-2" not in readme and "adjudication" not in readme and "gold" not in readme
    assert {path.name for path in (human_root / "frozen-guidance").iterdir()} == {
        "annotation-guide.json", "REFERENCE-ONLY.txt", runner.HUMAN_REFERENCE_PACK_FILE,
    }
    guide = json.loads(
        (human_root / "frozen-guidance/annotation-guide.json").read_text("utf-8")
    )
    assert guide["guide_version"] == "single-human-incremental-guide-v2"
    assert set(guide["human_judgement_fields"]) == editable
    assert guide["derived_local_ref_formats"]["new_propositions"] == (
        "new-proposition-N in collection order"
    )
    visible = "\n".join(
        f"{path.relative_to(human_root).as_posix()}\n{path.read_text('utf-8')}"
        for path in sorted(human_root.rglob("*")) if path.is_file()
    )
    assert runner.HUMAN_VISIBLE_FORBIDDEN.search(visible) is None


def test_partial_human_chain_cannot_lock_and_model_provenance_fails_closed(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    human_root, pack = _prepared_human_pack(output)
    reference_path = human_root / runner.HUMAN_REFERENCE_CHAIN_FILE
    reference = json.loads(reference_path.read_text("utf-8"))
    reference.update({
        "status": "in_progress_human_reference",
        "semantic_judgements_authored_by_human": True,
        "semantic_delta_chain": [runner._human_delta(pack, 0, _empty_human_judgements(pack))],
    })
    _write_json(reference_path, reference)
    with pytest.raises(runner.FourArmError, match="genuine human single-reference gate"):
        runner.lock_human_reference(output)

    provenance_path = human_root / "provenance.json"
    provenance = json.loads(provenance_path.read_text("utf-8"))
    provenance.update({
        "status": "completed_human_provenance", "actor_type": "model",
        "annotator_id": "named-human", "knew_working_hypothesis": False,
        "previously_seen_published_replies": False, "was_investigator": False,
        "independent_of_machine_ledger": True,
        "machine_output_revealed_before_lock": False,
        "completed_at_utc": "2020-01-01T00:02:00Z",
    })
    _write_json(provenance_path, provenance)
    with pytest.raises(runner.FourArmError, match="human provenance is invalid"):
        runner.verify_run(output)


def test_one_investigator_human_chain_locks_two_derived_snapshots_offline(
    frozen_case: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    prepared = runner.prepare_run(case, output, experiment_freeze=freeze)
    original_plan = (output / "call-plan.json").read_bytes()
    _complete_single_human_reference(output)
    human_root, pack = _prepared_human_pack(output)
    submitted = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [
            human_root / "provenance.json",
            human_root / runner.HUMAN_REFERENCE_CHAIN_FILE,
        ]
    }
    reference = json.loads(
        (human_root / runner.HUMAN_REFERENCE_CHAIN_FILE).read_text("utf-8")
    )
    all_ledgers = runner._materialised_ledgers(
        pack, reference["semantic_delta_chain"],
        transport_binding=runner.validate_freeze(freeze)["contracts"]["xai_transport"],
    )
    monkeypatch.setattr(
        runner, "ReviewedProviderAdapter",
        lambda: (_ for _ in ()).throw(AssertionError("provider constructed")),
    )
    locked = runner.lock_human_reference(output)
    verified = runner.verify_run(output)
    assert locked["provider_calls"] == 0
    assert verified["arm_d_status"] == "locked"
    assert verified["provider_calls"] == 0
    assert prepared["planned_provider_calls"] == 18
    assert (output / "call-plan.json").read_bytes() == original_plan
    assert all(
        hashlib.sha256(path.read_bytes()).hexdigest() == digest
        for path, digest in submitted.items()
    )
    materialised = json.loads(
        (human_root / "materialised-reference.json").read_text("utf-8")
    )
    rows = materialised["snapshot_ledgers"]
    assert [row["snapshot_id"] for row in rows] == ["snapshot-01", "snapshot-02"]
    for row, binding in zip(rows, pack["snapshot_bindings"]):
        assert row["ledger"] == all_ledgers[binding["prefix_length"] - 1]
    assert rows[0]["ledger"]["as_of_turn_index"] < rows[1]["ledger"]["as_of_turn_index"]
    provenance = json.loads((human_root / "provenance.json").read_text("utf-8"))
    assert provenance["knew_working_hypothesis"] is True
    assert provenance["previously_seen_published_replies"] is True
    assert provenance["was_investigator"] is True
    assert provenance["independent_of_machine_ledger"] is True


def test_human_chain_materialises_actual_parent_unavailable_suffix_pattern(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    _, pack = _prepared_human_pack(output)
    parent_pattern = [
        ("T000", None),
        ("T001", "T000"),
        ("T002", "T000"),
        ("T003", "T002"),
        ("T004", "T003"),
        ("T005", "T004"),
        ("T006", "T005"),
        ("T007", "T006"),
        ("T008", "T007"),
        ("T009", "T007"),
        ("T010", "T007"),
        ("T011", None),
        ("T012", None),
        ("T013", None),
        ("T016", None),
    ]
    participant = copy.deepcopy(pack["transcript"]["turns"][0]["participant"])
    pack["transcript"]["turns"] = [
        {
            "conversation_key": pack["transcript"]["conversation_key"],
            "turn_id": turn_id,
            "turn_index": index,
            "post_id": f"local-post:{turn_id}",
            "speaker_id": participant["participant_id"],
            "parent_turn_id": parent_turn_id,
            "text": f"Wholly invented materialisation turn {turn_id}.",
            "participant": copy.deepcopy(participant),
        }
        for index, (turn_id, parent_turn_id) in enumerate(parent_pattern)
    ]
    chain = [
        runner._human_delta(pack, index, _empty_human_judgements(pack))
        for index in range(len(parent_pattern))
    ]

    ledgers = runner._materialised_ledgers(
        pack,
        chain,
        transport_binding=runner.validate_freeze(freeze)["contracts"]["xai_transport"],
    )

    assert len(ledgers) == len(parent_pattern)
    assert [
        (turn["turn_id"], turn["parent_turn_id"])
        for turn in ledgers[-1]["turn_refs"]
    ] == parent_pattern


def test_two_snapshot_prefixes_have_no_independent_editable_copy(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    human_root, pack = _prepared_human_pack(output)
    editable_json = sorted(
        path.relative_to(human_root).as_posix()
        for path in human_root.glob("*.json")
        if path.name in {"provenance.json", runner.HUMAN_REFERENCE_CHAIN_FILE}
    )
    assert editable_json == ["provenance.json", runner.HUMAN_REFERENCE_CHAIN_FILE]
    lengths = [row["prefix_length"] for row in pack["snapshot_bindings"]]
    assert len(lengths) == 2 and lengths[0] < lengths[1]
    assert len(pack["transcript"]["turns"][: lengths[0]]) == lengths[0]
    assert pack["transcript"]["turns"][: lengths[0]] == (
        pack["transcript"]["turns"][: lengths[1]][: lengths[0]]
    )


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("machine_reveal", "invalid or conceals awareness"),
        ("machine_actor", "invalid or conceals awareness"),
        ("machine_reference", "classification differs"),
        ("codex_identity", "genuine human authorship"),
        ("missing_awareness", "invalid or conceals awareness"),
        ("machine_ledger_dependence", "invalid or conceals awareness"),
    ],
)
def test_invalid_human_provenance_or_authorship_cannot_lock(
    frozen_case: tuple[Path, Path], tmp_path: Path, defect: str, message: str
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    _complete_single_human_reference(output)
    human_root, _ = _prepared_human_pack(output)
    provenance_path = human_root / "provenance.json"
    provenance = json.loads(provenance_path.read_text("utf-8"))
    if defect == "machine_reveal":
        provenance["machine_output_revealed_before_lock"] = True
    elif defect == "machine_actor":
        provenance["actor_type"] = "model"
    elif defect == "codex_identity":
        provenance["annotator_id"] = "Codex"
    elif defect == "missing_awareness":
        provenance["knew_working_hypothesis"] = None
    elif defect == "machine_ledger_dependence":
        provenance["independent_of_machine_ledger"] = False
    else:
        reference_path = human_root / runner.HUMAN_REFERENCE_CHAIN_FILE
        reference = json.loads(reference_path.read_text("utf-8"))
        reference["semantic_judgements_authored_by_human"] = False
        _write_json(reference_path, reference)
    _write_json(provenance_path, provenance)
    with pytest.raises(runner.FourArmError, match=message):
        runner.lock_human_reference(output)
    assert not (human_root / "materialised-reference.json").exists()
    assert not (human_root / "reference-lock.json").exists()


def test_existing_machine_artifact_prevents_annotation_and_reference_lock(
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
    with pytest.raises(runner.FourArmError, match="before any experiment machine output"):
        runner.annotate_human_reference(output, input_fn=lambda _prompt: "")
    assert not (output / runner.PREPARED_HUMAN_DIR / "reference-lock.json").exists()


def test_annotation_ui_shows_one_turn_and_only_permitted_prior_state(
    frozen_case: tuple[Path, Path], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    human_root, pack = _prepared_human_pack(output)
    answers = iter([json.dumps(_empty_human_judgements(pack)), "APPROVE"])
    monkeypatch.setattr(
        runner, "ReviewedProviderAdapter",
        lambda: (_ for _ in ()).throw(AssertionError("provider constructed")),
    )
    result = runner.annotate_human_reference(
        output, input_fn=lambda _prompt: next(answers),
    )
    displayed = capsys.readouterr().out
    assert result == {
        "status": "in_progress_human_reference", "provider_calls": 0,
        "approved_turn_id": "turn-root", "approved_turn_index": 0,
        "completed_turn_count": 1, "total_turn_count": 4,
    }
    assert "turn-root" in displayed and "The north gate is open." in displayed
    assert "participant-user" in displayed
    assert "That premise is disputed." not in displayed
    assert "Three checks will follow:" not in displayed
    first_display, _ = json.JSONDecoder().raw_decode(displayed)
    assert set(first_display) == {
        "current_turn_id", "speaker", "exact_text",
        "prior_materialised_semantic_objects",
    }
    assert first_display["prior_materialised_semantic_objects"] is None
    reference = json.loads(
        (human_root / runner.HUMAN_REFERENCE_CHAIN_FILE).read_text("utf-8")
    )
    assert len(reference["semantic_delta_chain"]) == 1
    answers = iter([json.dumps(_empty_human_judgements(pack)), "APPROVE"])
    runner.annotate_human_reference(
        output, turn_id="turn-one", input_fn=lambda _prompt: next(answers),
    )
    second_output = capsys.readouterr().out
    second_display, _ = json.JSONDecoder().raw_decode(second_output)
    assert second_display["current_turn_id"] == "turn-one"
    assert set(second_display["prior_materialised_semantic_objects"]) == set(
        runner.PRIOR_SEMANTIC_OBJECT_FIELDS
    )
    assert "Three checks will follow:" not in json.dumps(second_display)
    assert "The second latch remains closed." not in json.dumps(second_display)
    ledger = json.loads((output / "call-ledger.json").read_text("utf-8"))
    assert ledger["provider_call_count"] == 0
    assert {row["state"] for row in ledger["entries"]} == {"planned"}


def test_annotation_wrong_turn_missing_fields_or_no_approval_never_writes(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    human_root, pack = _prepared_human_pack(output)
    path = human_root / runner.HUMAN_REFERENCE_CHAIN_FILE
    original = path.read_bytes()
    values = _empty_human_judgements(pack)
    with pytest.raises(runner.FourArmError, match="next uncompleted turn"):
        answers = iter((json.dumps(values), "APPROVE"))
        runner.annotate_human_reference(
            output, turn_id="turn-one",
            input_fn=lambda _prompt: next(answers),
        )
    with pytest.raises(runner.FourArmError, match="fields differ"):
        answers = iter(("{}", "APPROVE"))
        runner.annotate_human_reference(
            output, input_fn=lambda _prompt: next(answers),
        )
    with pytest.raises(runner.FourArmError, match="not literally approved"):
        answers = iter((json.dumps(values), "NO"))
        runner.annotate_human_reference(
            output, input_fn=lambda _prompt: next(answers),
        )
    assert path.read_bytes() == original


def test_contract_help_aliases_expose_full_surface_without_provider(
    frozen_case: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, freeze = frozen_case
    monkeypatch.setattr(
        runner, "ReviewedProviderAdapter",
        lambda: (_ for _ in ()).throw(AssertionError("provider constructed")),
    )
    proposition = runner.human_field_help("new_proposition", experiment_freeze=freeze)
    issue_update = runner.human_field_help("issue_state_update", experiment_freeze=freeze)
    relation = runner.human_field_help("new_relation", experiment_freeze=freeze)
    target = runner.human_field_help("answer_target", experiment_freeze=freeze)
    all_fields = runner.human_field_help("all", experiment_freeze=freeze)
    assert proposition["field"] == "new_propositions"
    assert issue_update["field"] == "issue_state_updates"
    assert target["field"] == "answer_target_changes"
    assert proposition["provider_calls"] == 0
    assert proposition["local_ref_formats"]["new_issue_states"] == (
        "new-issue-N in collection order"
    )
    changes = issue_update["shape"]["items"]["properties"]["changes"]
    assert changes["minProperties"] == 1
    relation_condition = relation["shape"]["items"]["allOf"][0]
    assert relation_condition["if"]["properties"]["analysis_basis"]["const"] == (
        "evaluator_diagnosis"
    )
    assert relation_condition["then"]["properties"]["asserted_or_analysed_by"] == {
        "type": "null"
    }
    assert relation_condition["else"]["properties"]["asserted_or_analysed_by"][
        "type"
    ] == "string"
    assert set(all_fields["editable_fields"]) == {
        "new_propositions", "proposition_updates", "new_proposition_groups",
        "proposition_group_updates", "new_issue_states", "issue_state_updates",
        "commitment_changes", "obligation_changes", "new_relations",
        "answer_target_changes", "rejected_answer_target_changes", "repair_records",
        "resolved_items", "extraction_status", "abstentions",
        "unsupported_inferences_rejected", "warnings",
    }


def test_local_refs_and_unambiguous_occurrences_are_mechanically_derived(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    _, pack = _prepared_human_pack(output)
    values = _empty_human_judgements(pack)
    _add_transport_evidence(values, {"exact_text": "north gate"})
    values["new_issue_states"] = [{
        "initiating_speaker": "participant-user", "canonical_question": "Which gate?",
        "issue_type": "wh", "live_alternatives": [{
            "label": "North", "proposition_refs": ["new-proposition-1"], "status": "live",
        }], "addressed_participant": None, "answer_requirements": [],
        "related_proposition_refs": ["new-proposition-1"], "status": "open",
        "resolution_type": None, "confidence": 0.9,
        "exact_evidence_spans": [{"exact_text": "north gate"}],
    }]
    delta = runner._human_delta(pack, 0, values)
    assert delta["new_propositions"][0]["local_ref"] == "new-proposition-1"
    assert delta["commitment_changes"][0]["local_ref"] == "new-commitment-1"
    issue = delta["new_issue_states"][0]
    assert issue["local_ref"] == "new-issue-1"
    assert issue["live_alternatives"][0]["local_ref"] == "new-alternative-1"
    assert delta["new_propositions"][0]["exact_evidence_spans"][0][
        "occurrence_index"
    ] == 0
    values["new_propositions"][0]["local_ref"] = "new-proposition-9"
    with pytest.raises(runner.FourArmError, match="local_ref is deterministic"):
        runner._human_delta(pack, 0, values)
    with pytest.raises(runner.FourArmError, match="repeated exact_text requires human"):
        runner._derive_occurrence_indexes({"exact_text": "aa"}, "aaaa")


def test_issue_update_live_alternatives_receive_deterministic_local_refs() -> None:
    values = {
        "new_issue_states": [{"live_alternatives": [{"label": "first"}]}],
        "issue_state_updates": [{
            "changes": {"live_alternatives": [{"label": "second"}]},
        }],
    }
    runner._inject_local_refs(values)
    assert values["new_issue_states"][0]["live_alternatives"][0]["local_ref"] == (
        "new-alternative-1"
    )
    assert values["issue_state_updates"][0]["changes"]["live_alternatives"][0][
        "local_ref"
    ] == "new-alternative-2"


def test_groups_answer_targets_and_later_issue_update_materialise(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    _, pack = _prepared_human_pack(output)
    selector = {"exact_text": "north gate", "occurrence_index": 0}
    first = _empty_human_judgements(pack)
    seed: dict[str, Any] = {"new_propositions": [], "commitment_changes": []}
    _add_transport_evidence(seed, selector)
    second_proposition = copy.deepcopy(seed["new_propositions"][0])
    second_proposition.update(
        canonical_text="The gate has an observed state."
    )
    seed["new_propositions"][0]["proposition_group_ref"] = "new-proposition-group-1"
    second_proposition["proposition_group_ref"] = "new-proposition-group-1"
    second_commitment = copy.deepcopy(seed["commitment_changes"][0])
    second_commitment.update(
        proposition_ref="new-proposition-2"
    )
    first["new_propositions"] = [seed["new_propositions"][0], second_proposition]
    first["commitment_changes"] = [seed["commitment_changes"][0], second_commitment]
    first["new_proposition_groups"] = [{
        "structure_type": "conjunction",
        "members": [
            {"proposition_ref": "new-proposition-1", "role": "conjunct", "ordinal": 0},
            {"proposition_ref": "new-proposition-2", "role": "conjunct", "ordinal": 1},
        ],
        "exact_evidence_spans": [selector], "decomposition_complete": True,
        "confidence": 0.9, "uncertainty_reason": None,
    }]
    first["new_issue_states"] = [{
        "initiating_speaker": "participant-user",
        "canonical_question": "Is the gate open?", "issue_type": "polar",
        "live_alternatives": [], "addressed_participant": "participant-user",
        "answer_requirements": [{
            "requirement_type": "yes_no", "description": "State whether it is open."
        }],
        "related_proposition_refs": ["new-proposition-1"], "status": "open",
        "resolution_type": None, "confidence": 0.9,
        "exact_evidence_spans": [selector],
    }]
    first["answer_target_changes"] = [{
        "operation": "add",
        "issue_refs": ["new-issue-1"], "proposition_refs": [],
        "target_status": "confirmed", "confidence": 0.9,
        "exact_evidence_spans": [selector],
    }]
    first_delta = runner._human_delta(pack, 0, first)
    first_ledger = runner._materialised_ledgers(
        pack, [first_delta],
        transport_binding=runner.validate_freeze(freeze)["contracts"]["xai_transport"],
        require_complete=False,
    )[0]
    issue_id = first_ledger["issue_states"][0]["issue_id"]
    proposition_id = first_ledger["propositions"][0]["proposition_id"]
    later = _empty_human_judgements(pack)
    later["issue_state_updates"] = [{
        "issue_id": issue_id, "changes": {
            "confidence": 0.8,
            "live_alternatives": [{
                "label": "The selected alternative", "proposition_refs": [proposition_id],
                "status": "live",
            }],
        },
        "reason": "The human selected a later confidence update.",
        "exact_evidence_spans": [{"exact_text": "premise", "occurrence_index": 0}],
    }]
    second_delta = runner._human_delta(pack, 1, later)
    ledgers = runner._materialised_ledgers(
        pack, [first_delta, second_delta],
        transport_binding=runner.validate_freeze(freeze)["contracts"]["xai_transport"],
        require_complete=False,
    )
    assert len(first_ledger["proposition_groups"]) == 1
    assert len(first_ledger["answer_targets"]) == 1
    assert ledgers[-1]["issue_states"][0]["issue_id"] == issue_id
    assert ledgers[-1]["issue_states"][0]["confidence"] == 0.8
    assert len(ledgers[-1]["issue_states"][0]["live_alternatives"]) == 1


def test_chain_sentinel_resolves_without_mutating_human_submission(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    _complete_single_human_reference(output)
    human_root, _ = _prepared_human_pack(output)
    path = human_root / runner.HUMAN_REFERENCE_CHAIN_FILE
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
    _, pack = _prepared_human_pack(output)
    first = _empty_human_judgements(pack)
    _add_transport_evidence(first, {"exact_text": "north gate"})
    delta = runner._human_delta(pack, 0, first)
    assert pack["annotation_contract"]["human_selector_version"] == (
        runner.HUMAN_SELECTOR_VERSION
    )
    assert pack["annotation_contract"]["evidence_selector_contract_version"] == (
        "exact-text-occurrence-index-v2.0.2"
    )
    assert delta["new_propositions"][0]["local_ref"] == "new-proposition-1"
    assert delta["new_propositions"][0]["exact_evidence_spans"][0][
        "occurrence_index"
    ] == 0
    ledger = runner._materialised_ledgers(
        pack, [delta],
        transport_binding=runner.validate_freeze(freeze)["contracts"]["xai_transport"],
        require_complete=False,
    )[0]
    span = ledger["propositions"][0]["exact_evidence_spans"][0]
    assert span == {
        "turn_id": "turn-root", "start_char": 4, "end_char": 14,
        "exact_text": "north gate",
    }
    assert "occurrence_index" not in json.dumps(ledger)


def test_human_semantic_consistency_error_surfaces_pointer_and_rule(
    frozen_case: tuple[Path, Path], tmp_path: Path
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    _, pack = _prepared_human_pack(output)
    values = _empty_human_judgements(pack)
    _add_transport_evidence(values, {"exact_text": "north gate"})
    values["commitment_changes"] = []
    delta = runner._human_delta(pack, 0, values)
    with pytest.raises(runner.FourArmError) as caught:
        runner._materialised_ledgers(
            pack, [delta],
            transport_binding=runner.validate_freeze(freeze)["contracts"]["xai_transport"],
            require_complete=False,
        )
    message = str(caught.value)
    assert "/semantic_delta_chain/0" in message
    assert "speaker_committed" in message and "commitment" in message


@pytest.mark.parametrize(
    "selector,status",
    [
        ({"exact_text": "north gate", "occurrence_index": 9}, "evidence_occurrence_index_out_of_range"),
        ({"exact_text": "absent phrase", "occurrence_index": 0}, "evidence_exact_text_not_found"),
    ],
)
def test_human_transport_selector_rejects_unresolved_evidence_with_pointer(
    frozen_case: tuple[Path, Path], tmp_path: Path,
    selector: dict[str, Any], status: str,
) -> None:
    case, freeze = frozen_case
    output = tmp_path / "prepared-run"
    runner.prepare_run(case, output, experiment_freeze=freeze)
    _, pack = _prepared_human_pack(output)
    first = _empty_human_judgements(pack)
    _add_transport_evidence(first, selector)
    delta = runner._human_delta(pack, 0, first)
    with pytest.raises(
        runner.FourArmError,
        match=rf"/semantic_delta_chain/0.*{status}.*exact_evidence_spans",
    ):
        runner._materialised_ledgers(
            pack, [delta],
            transport_binding=runner.validate_freeze(freeze)["contracts"]["xai_transport"],
            require_complete=False,
        )
