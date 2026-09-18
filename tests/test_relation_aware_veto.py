from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.helpers.historical_corpus import (
    RESEARCH_RELATIVE,
    historical_corpus_root,
)

from semantic_alignment.relation_aware_veto import (
    CONTRACT_FIELDS,
    CONTRACT_SCHEMA,
    CostLedger,
    DeveloperBatchRunner,
    GeminiRelationClient,
    HARD_COMBINED_CEILING_USD,
    V2_HARD_COMBINED_CEILING_USD,
    RelationRouter,
    _is_explicit_safety_rejection,
    _resolve_quote_analysis_items,
    conservative_ambiguous_pair_vetoes,
    local_deterministic_pair_veto,
    merge_split_pair_results,
    _relationship_kind,
    _relationship_sentences,
    _is_operator_withdrawn_positive,
    _simulate_lane,
    _entity_is_explicit_in_quote,
    _all_safety_rejections_vetoed,
    _positive_retention_gate,
    attest_original_collection_identity,
    build_production_top5_batch_manifest,
    build_report,
    deterministic_contradictions,
    deterministic_material_contradictions,
    load_image_corpus,
    initialise_v2_cost_ledger,
    normalise_contract_for_material_veto,
    pair_response_schema,
    partition_frozen_evaluation_labels,
    select_production_top_k,
    require_transport_parity,
    run_pilot,
    run_material_veto_v2,
    run_production_top5,
    validate_contract,
    validate_pair_response,
)
from semantic_alignment.image_quote_eligibility import load_completed_corpus


QUOTE_ID = "a" * 64


def contract(**changes):
    value = {
        "quote_id": QUOTE_ID,
        "quote_text": "It pays to know the enemy - not least because at some time you may have the opportunity to turn him into a friend.",
        "verified_text": "",
        "verification_status": "exact",
        "research_confidence": "high",
        "dominant_proposition": "Understanding an adversary can enable a later change into cooperation.",
        "claim_type": "relationship_transformation",
        "actor_count_minimum": 2,
        "required_actor_roles": ["adversaries"],
        "named_entities_required": [],
        "initial_relationship": "enemy",
        "final_relationship": "friend",
        "required_transition": "enemy_to_friend",
        "required_action": "relationship transformation",
        "required_direction": "hostility to friendship",
        "required_before_state": "enemy",
        "required_after_state": "friend",
        "polarity": "positive",
        "historical_specificity": "general",
        "visual_strategies_allowed": ["historical_scene"],
        "neutral_portrait_allowed": False,
        "multi_person_image_risk": "high",
        "relationship_evidence_required": True,
        "hard_conflicts": ["established allies"],
        "misleading_implications_to_avoid": ["ordinary allied diplomacy"],
        "source_fields_used": ["quote_text", "intended_argument"],
        "confidence": "high",
    }
    value.update(changes)
    return value


def packet():
    value = contract()
    return {key: value[key] for key in ("quote_id", "quote_text", "verified_text", "verification_status", "research_confidence")}


def image(relationship="ally", people=2):
    return {
        "image_id": "discovered:test",
        "named_people": ["Margaret Thatcher", "Ronald Reagan"] if people > 1 else ["Margaret Thatcher"],
        "source_caption": "Margaret Thatcher meets Ronald Reagan",
        "source_event": "Official meeting",
        "relationship_assertions": [{"relationship": relationship}] if relationship else [],
        "visual": {"people_count_minimum": people, "shot_type": "group scene" if people > 1 else "portrait"},
    }


def valid_pair_record(pair_id, decision="veto"):
    return {
        "pair_id": pair_id,
        "quote_id": QUOTE_ID,
        "image_id": "discovered:test",
        "decision": decision,
        "confidence": "high",
        "materially_misleading": decision != "allow",
        "relationship_supported": "no" if decision != "allow" else "yes",
        "contradiction_types": ["ally_adversary_confusion"] if decision != "allow" else ["none"],
        "dominant_visual_message": "A meeting between established allies.",
        "reason": "Broad diplomacy does not establish the required relationship change.",
    }


def test_contract_schema_and_identity_are_strict():
    value = contract()
    assert set(value) == set(CONTRACT_FIELDS)
    assert validate_contract(value, packet()) == value
    changed = copy.deepcopy(value)
    changed["quote_text"] = "Different"
    with pytest.raises(ValueError, match="immutable quote_text"):
        validate_contract(changed, packet())


def test_relationship_contract_cannot_omit_relationship_evidence():
    with pytest.raises(ValueError, match="lacks evidence"):
        validate_contract(contract(relationship_evidence_required=False), packet())


def test_non_relationship_state_transition_does_not_require_relationship_evidence():
    value = contract(
        claim_type="abstract_principle",
        actor_count_minimum=1,
        required_actor_roles=["nation"],
        initial_relationship="none",
        final_relationship="none",
        required_transition="decline_to_recovery",
        relationship_evidence_required=False,
    )
    assert validate_contract(value, packet()) == value


def test_abstract_conflict_to_peace_transition_is_not_forced_into_person_relationship_schema():
    value = contract(
        claim_type="abstract_principle",
        actor_count_minimum=1,
        required_actor_roles=["government"],
        initial_relationship="none",
        final_relationship="none",
        required_transition="conflict_to_peace",
        relationship_evidence_required=False,
        required_before_state="social discord",
        required_after_state="social harmony",
    )
    assert validate_contract(value, packet()) == value


def test_established_ally_cannot_represent_enemy_to_friend_transition():
    rules = deterministic_contradictions(contract(), image("ally"))
    names = {row["rule"] for row in rules}
    assert "ally_adversary_confusion" in names
    assert "missing_transition" in names


def test_relationship_evidence_may_be_in_the_adjacent_canonical_sentence():
    packets = [{
        "quote_id": QUOTE_ID,
        "historical_context": (
            "Thatcher delivered a eulogy for Ronald Reagan. "
            "The two leaders had shared a close political alliance and collaborated during the 1980s."
        ),
    }]
    evidence = _relationship_sentences(packets, "Ronald Reagan")
    assert evidence
    assert _relationship_kind(evidence) == "ally"


def test_relationship_evidence_does_not_attach_an_unrelated_adversary_to_bush():
    packets = [{
        "quote_id": QUOTE_ID,
        "historical_context": (
            "Thatcher and George H. W. Bush discussed Saddam Hussein's invasion of Kuwait. "
            "She warned that delaying action against an aggressor would strengthen the enemy."
        ),
    }]
    assert _relationship_sentences(packets, "George H. W. Bush") == []


def test_relationship_evidence_uses_full_name_not_shared_thatcher_surname():
    packets = [{
        "quote_id": QUOTE_ID,
        "historical_context": (
            "Margaret Thatcher opposed socialist coercion and later cooperated with allies."
        ),
    }]
    assert _relationship_sentences(packets, "Denis Thatcher") == []


def test_documented_adversary_to_partner_does_not_trigger_relationship_veto():
    rules = deterministic_contradictions(contract(), image("adversary_to_partner"))
    assert not {"ally_adversary_confusion", "missing_transition", "missing_relationship_evidence"}.intersection(
        row["rule"] for row in rules
    )


def test_unknown_relationship_is_never_compatible():
    rules = deterministic_contradictions(contract(), image("unknown"))
    assert any(row["rule"] == "missing_relationship_evidence" for row in rules)


def test_named_soviet_counterparty_cannot_be_replaced_by_established_ally():
    value = contract(
        quote_text=(
            "The history of negotiation with the Soviet Union teaches that unilateral "
            "concessions are treated as weakness."
        ),
        verified_text="",
        claim_type="warning",
        actor_count_minimum=1,
        required_actor_roles=[],
        initial_relationship="none",
        final_relationship="none",
        required_transition="none",
        required_action="negotiation and reciprocal concessions",
        relationship_evidence_required=False,
        neutral_portrait_allowed=True,
        multi_person_image_risk="high",
    )
    value["mentioned_entities"] = ["Margaret Thatcher", "Soviet Union"]
    value["visually_required_entities"] = []
    rules = deterministic_material_contradictions(value, image("ally"))
    assert any(row["rule"] == "wrong_geopolitical_counterparty" for row in rules)


def test_collection_attestation_names_only_thatcher_for_original_images():
    images = [{
        "image_id": f"original:t{number:02d}.jpg",
        "image_sha256": f"{number:064x}",
        "corpus": "original",
        "identity_basis": "unknown",
        "identity_confidence": "low",
        "named_people": [],
        "source_evidence": [],
    } for number in range(69)] + [{
        "image_id": "discovered:test",
        "image_sha256": "f" * 64,
        "corpus": "discovered_production_ready",
        "identity_basis": "source_caption",
        "identity_confidence": "high",
        "named_people": ["Margaret Thatcher", "Ronald Reagan"],
        "source_evidence": ["Archive caption"],
    }]
    attested, manifest = attest_original_collection_identity(images)
    original = attested[0]
    assert original["named_people"] == ["Margaret Thatcher"]
    assert original["identity_basis"] == "curated_collection_attestation"
    assert manifest["attested_image_count"] == 69
    assert manifest["facial_identification_used"] is False
    assert attested[-1] == images[-1]


def test_context_entity_is_not_promoted_to_visual_requirement():
    assert _entity_is_explicit_in_quote(
        "Mikhail Gorbachev", "We discussed how peace might be secured.", "",
    ) is False
    assert _entity_is_explicit_in_quote(
        "Mikhail Gorbachev", "Mr Gorbachev is a man we can do business with.", "",
    ) is True
    assert _entity_is_explicit_in_quote(
        "United Kingdom", "Britain must defend her liberties.", "",
    ) is True


def test_material_contract_relaxes_source_context_but_preserves_explicit_person():
    quote_text = "Liberty must be defended."
    source = contract(
        quote_text=quote_text,
        verified_text=quote_text,
        claim_type="abstract_principle",
        named_entities_required=["Margaret Thatcher", "United Kingdom"],
        relationship_evidence_required=False,
        actor_count_minimum=2,
        required_actor_roles=["government", "public"],
        historical_specificity="event_specific",
        neutral_portrait_allowed=False,
        required_transition="none",
        initial_relationship="none",
        final_relationship="none",
    )
    canonical = {
        "quote_id": QUOTE_ID,
        "quote_text": quote_text,
        "verified_text": quote_text,
        "entities": ["Margaret Thatcher", "United Kingdom"],
    }
    revised = normalise_contract_for_material_veto(source, canonical)
    assert revised["visually_required_entities"] == []
    assert revised["actor_count_minimum"] == 1
    assert revised["historical_specificity"] == "general"
    assert revised["neutral_portrait_allowed"] is True
    assert revised["source_event_advisory_only"] is True

    source["claim_type"] = "named_entity"
    source["quote_text"] = "Mr Gorbachev is a man we can do business with."
    source["verified_text"] = source["quote_text"]
    source["named_entities_required"] = ["Mikhail Gorbachev"]
    canonical.update({
        "quote_text": source["quote_text"],
        "verified_text": source["verified_text"],
        "entities": ["Mikhail Gorbachev"],
    })
    revised = normalise_contract_for_material_veto(source, canonical)
    assert revised["visually_required_entities"] == ["Mikhail Gorbachev"]
    assert revised["neutral_portrait_allowed"] is False


def test_explicit_concept_is_not_treated_as_a_person_who_must_appear():
    quote_text = "Communism failed because it denied human freedom."
    source = contract(
        quote_text=quote_text,
        verified_text=quote_text,
        claim_type="warning",
        named_entities_required=["Communism"],
        relationship_evidence_required=False,
        actor_count_minimum=0,
        required_actor_roles=[],
        required_transition="none",
        initial_relationship="none",
        final_relationship="none",
    )
    revised = normalise_contract_for_material_veto(source, {
        "quote_id": QUOTE_ID,
        "quote_text": quote_text,
        "verified_text": quote_text,
        "entities": ["Communism"],
    })
    assert revised["visually_required_entities"] == []


def test_named_person_substitution_is_vetoed_but_neutral_thatcher_portrait_is_not():
    quote_text = "Mr Gorbachev is a man we can do business with."
    source = contract(
        quote_text=quote_text,
        verified_text=quote_text,
        claim_type="named_entity",
        named_entities_required=["Mikhail Gorbachev"],
        relationship_evidence_required=False,
        actor_count_minimum=1,
        required_actor_roles=[],
        required_transition="none",
        initial_relationship="none",
        final_relationship="none",
    )
    revised = normalise_contract_for_material_veto(source, {
        "quote_id": QUOTE_ID,
        "quote_text": quote_text,
        "verified_text": quote_text,
        "entities": ["Mikhail Gorbachev"],
    })
    solo = image(None, people=1)
    solo["named_people"] = ["Margaret Thatcher"]
    assert deterministic_material_contradictions(revised, solo) == []
    reagan = image("ally", people=2)
    assert any(
        row["rule"] == "wrong_named_entity"
        for row in deterministic_material_contradictions(revised, reagan)
    )


def test_material_rules_do_not_veto_non_literal_portrait_for_abstract_claim():
    quote_text = "Freedom requires responsibility."
    source = contract(
        quote_text=quote_text,
        verified_text=quote_text,
        claim_type="abstract_principle",
        named_entities_required=["Margaret Thatcher"],
        relationship_evidence_required=False,
        actor_count_minimum=2,
        required_actor_roles=["speaker", "audience"],
        required_transition="none",
        initial_relationship="none",
        final_relationship="none",
    )
    canonical = {
        "quote_id": QUOTE_ID,
        "quote_text": quote_text,
        "verified_text": quote_text,
        "entities": ["Margaret Thatcher"],
    }
    revised = normalise_contract_for_material_veto(source, canonical)
    portrait = image(None, people=1)
    portrait["named_people"] = ["Margaret Thatcher"]
    assert deterministic_material_contradictions(revised, portrait) == []


def test_material_rules_still_veto_established_allies_for_enemy_to_friend():
    revised = normalise_contract_for_material_veto(
        contract(),
        {
            "quote_id": QUOTE_ID,
            "quote_text": contract()["quote_text"],
            "verified_text": "",
            "entities": [],
        },
    )
    names = {row["rule"] for row in deterministic_material_contradictions(revised, image("ally"))}
    assert {"ally_adversary_confusion", "missing_transition"}.issubset(names)


def test_single_actor_rule_uses_schema_supported_enum():
    rules = deterministic_contradictions(contract(), image(None, people=1))
    assert any(row["rule"] == "insufficient_actor_count" for row in rules)
    enum = pair_response_schema(1)["properties"]["records"]["items"]["properties"]["contradiction_types"]["items"]["enum"]
    assert "insufficient_actor_count" in enum


def test_model_cannot_override_deterministic_veto():
    pair_id = "pair-1"
    source = [{
        "pair_id": pair_id, "quote_id": QUOTE_ID, "contract": contract(),
        "deterministic_contradictions": deterministic_contradictions(contract(), image("ally")),
    }]
    with pytest.raises(ValueError, match="override deterministic"):
        validate_pair_response(
            {"records": [valid_pair_record(pair_id, decision="allow")]}, image("ally"), source,
        )


def test_pair_count_remains_strictly_enforced_locally():
    pairs = [
        {"pair_id": "pair-1", "quote_id": QUOTE_ID, "contract": contract(),
         "deterministic_contradictions": []},
        {"pair_id": "pair-2", "quote_id": QUOTE_ID, "contract": contract(),
         "deterministic_contradictions": []},
    ]
    with pytest.raises(ValueError, match="count mismatch"):
        validate_pair_response(
            {"records": [valid_pair_record("pair-1")]}, image("ally"), pairs,
        )


def test_ambiguous_pair_outcome_is_conservatively_vetoed_without_resubmission():
    pairs = [{
        "pair_id": "pair-1",
        "quote_id": QUOTE_ID,
        "contract": contract(),
        "deterministic_contradictions": [],
    }]
    result = conservative_ambiguous_pair_vetoes(image("unknown"), pairs)
    assert result[0]["decision"] == "uncertain"
    assert result[0]["final_decision"] == "veto"
    assert result[0]["contradiction_types"] == ["insufficient_evidence"]


def test_split_pair_recovery_requires_exact_complete_identity_set():
    first = valid_pair_record("pair-1")
    second = valid_pair_record("pair-2")
    assert [row["pair_id"] for row in merge_split_pair_results(
        [{"response": [second]}, {"response": [first]}], ["pair-1", "pair-2"],
    )] == ["pair-1", "pair-2"]
    with pytest.raises(RuntimeError, match="exact expected pair IDs"):
        merge_split_pair_results([{"response": [first]}], ["pair-1", "pair-2"])


def test_truncated_split_with_deterministic_contradiction_recovers_as_local_veto():
    pair = {
        "pair_id": "pair-1",
        "quote_id": QUOTE_ID,
        "contract": contract(),
        "deterministic_contradictions": [{
            "rule": "ally_adversary_confusion",
            "detail": "Established allies cannot depict an enemy-to-friend transition.",
        }],
    }
    result = local_deterministic_pair_veto("split-1", image("ally"), pair)
    assert result["transport"] == "local_deterministic_recovery"
    assert result["cost_usd"] == 0
    assert result["response"][0]["decision"] == "veto"
    assert result["response"][0]["final_decision"] == "veto"
    assert result["response"][0]["final_reason"] == "deterministic_contradiction"


def test_frozen_evaluation_excludes_labels_for_images_outside_authorised_corpus():
    labels = [
        {"candidate_id": "included", "quote_hash": "q1"},
        {"candidate_id": "rights-pending", "quote_hash": "q2"},
    ]
    eligible, excluded = partition_frozen_evaluation_labels(
        labels, {"included": "image-1"},
    )
    assert eligible == [labels[0]]
    assert excluded == [{
        "candidate_id": "rights-pending",
        "quote_id": "q2",
        "reason": "image_not_in_authorised_corpus",
    }]


def test_final_report_treats_human_approval_retention_as_mandatory(tmp_path):
    fixtures = {
        "run_manifest.json": {
            "image_count": 91,
            "contract_prompt_version": "initial-contract",
            "contract_schema_version": "initial-contract-schema",
            "pair_prompt_version": "initial-pair",
            "pair_schema_version": "initial-pair-schema",
            "sdk_version": "test-sdk",
        },
        "cost_preflight.json": {
            "expected_combined_cost_usd": 1.0,
            "conservative_planned_maximum_usd": 2.0,
        },
        "pilot_evaluation.json": {"passed": True, "metrics": {}},
        "full_evaluation.json": {
            "passed": True,
            "metrics": {
                "positive_retention_rate": 0.25,
                "evaluated_eligible_pair_count": 73,
                "eligible_frozen_pair_count": 73,
                "excluded_frozen_pair_count": 3,
            },
        },
        "cost_ledger.json": {},
        "provider_route_state.json": {},
        "veto_decisions.json": {
            "quote_contract_count": 626, "image_count": 91, "pair_count": 1,
            "veto_count": 1, "allow_count": 0,
        },
        "production_isolation_audit.json": {"unchanged": True},
        "batch_jobs.json": {"jobs": {}},
    }
    for name, value in fixtures.items():
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    build_report(tmp_path)
    report = (tmp_path / "relation_aware_semantic_veto_report.md").read_text(encoding="utf-8")
    assert report.count("Full positive retention") == 1
    assert "diagnostic only" not in report
    assert "mandatory promotion gate" in report
    assert (tmp_path / "execution_provenance.json").exists()


def test_positive_retention_gate_preserves_original_five_percent_limit():
    assert _positive_retention_gate(3, 9) is False
    assert _positive_retention_gate(19, 20) is True
    assert _positive_retention_gate(0, 0) is False


def test_unknown_is_not_misreported_as_a_completed_safety_veto():
    assert _all_safety_rejections_vetoed([
        {"system_decision": "veto"},
        {"system_decision": "unknown"},
    ]) is False
    assert _all_safety_rejections_vetoed([
        {"system_decision": "veto"},
        {"system_decision": "veto"},
    ]) is True


def test_overconstrained_v1_production_continuation_is_retired(tmp_path):
    with pytest.raises(RuntimeError, match="retired v1 production-top5"):
        run_production_top5(
            tmp_path, tmp_path / "v1", execute=True, confirmed_limit=60,
        )


def test_production_top_five_is_selected_per_quote_for_current_and_expanded_corpora():
    scored = [
        {"image_id": f"image-{number}", "selector_score": 100 - number,
         "selector_components": {}}
        for number in range(8)
    ]
    current, expanded, union = select_production_top_k(
        scored, {"image-1", "image-3", "image-4", "image-6", "image-7"}, top_k=3,
    )
    assert [row["image_id"] for row in expanded] == ["image-0", "image-1", "image-2"]
    assert [row["image_id"] for row in current] == ["image-1", "image-3", "image-4"]
    assert {row["image_id"] for row in union} == {"image-0", "image-1", "image-2", "image-3", "image-4"}
    shared = next(row for row in union if row["image_id"] == "image-1")
    assert shared["ranks"] == {"current_69_top5": 1, "expanded_91_top5": 2}


def test_production_batch_manifest_reuses_completed_pairs_and_is_resume_stable(tmp_path):
    pairs = [
        {"pair_id": "pair-1", "image_id": "image-1", "quote_id": "quote-1"},
        {"pair_id": "pair-2", "image_id": "image-1", "quote_id": "quote-2"},
    ]
    candidates = {
        "pair_id_set_sha256": "set-hash", "union_pair_count": 2,
        "records": [{"pairs": pairs}],
    }
    first = build_production_top5_batch_manifest(
        tmp_path, candidates, {"records": {"pair-1": {"final_decision": "veto"}}},
    )
    assert first["reused_pair_count"] == 1
    assert first["missing_pair_count"] == 1
    assert first["items"][0]["pair_ids"] == ["pair-2"]
    second = build_production_top5_batch_manifest(
        tmp_path, candidates, {"records": {"pair-1": {}, "pair-2": {}}},
    )
    assert second == first


def test_production_selector_simulation_uses_first_allowed_ranked_fallback():
    pairs = {
        "pair-1": {"pair_id": "pair-1", "image_id": "image-1", "selector_score": 10},
        "pair-2": {"pair_id": "pair-2", "image_id": "image-2", "selector_score": 9},
        "pair-3": {"pair_id": "pair-3", "image_id": "image-3", "selector_score": 8},
    }
    judgements = {
        "pair-1": {"final_decision": "veto"},
        "pair-2": {"final_decision": "allow"},
        "pair-3": {"final_decision": "allow"},
    }
    result = _simulate_lane(["pair-1", "pair-2", "pair-3"], pairs, judgements)
    assert result["selector_winner_vetoed"] is True
    assert result["selected_pair_id"] == "pair-2"
    assert result["fallback_used"] is True
    assert result["no_allowed_image"] is False


def test_allow_cannot_claim_material_misleading_or_a_contradiction():
    pair_id = "pair-1"
    source = [{
        "pair_id": pair_id, "quote_id": QUOTE_ID,
        "contract": contract(claim_type="abstract_principle", relationship_evidence_required=False,
                             required_transition="none", actor_count_minimum=0),
        "deterministic_contradictions": [],
    }]
    record = valid_pair_record(pair_id, decision="allow")
    record["relationship_supported"] = "not_required"
    record["contradiction_types"] = ["none"]
    record["materially_misleading"] = True
    with pytest.raises(ValueError, match="contradictory safety metadata"):
        validate_pair_response({"records": [record]}, image("ally"), source)
    record["materially_misleading"] = False
    record["contradiction_types"] = ["none", "generic_theme_only"]
    with pytest.raises(ValueError, match="none cannot coexist"):
        validate_pair_response({"records": [record]}, image("ally"), source)


class HttpError(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


class FakeClient:
    def __init__(self, transport, outcomes):
        self.transport = transport
        self.outcomes = list(outcomes)
        self.calls = 0

    def call(self, prompt, schema, max_output_tokens):
        self.calls += 1
        value = self.outcomes.pop(0)
        if isinstance(value, BaseException):
            raise value
        return {
            "raw": {"value": value}, "parsed": value, "parse_error": None,
            "normalisation": [],
            "usage": {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "thinking_tokens": 0},
            "cost_usd": 0.0, "latency_seconds": 0.0,
            "request_id": "request", "model_version": "gemini-3.1-pro-preview",
        }


class TruncatedFakeClient(FakeClient):
    def call(self, prompt, schema, max_output_tokens):
        self.calls += 1
        return {
            "raw": {"candidates": [{"finishReason": "MAX_TOKENS"}]},
            "parsed": None, "parse_error": "truncated", "normalisation": [],
            "usage": {"input_tokens": 1, "cached_tokens": 0, "output_tokens": 1, "thinking_tokens": 1},
            "cost_usd": 0.0, "latency_seconds": 0.0,
            "request_id": "request", "model_version": "gemini-3.1-pro-preview",
        }


def test_two_developer_429s_pause_and_fall_back_once(tmp_path):
    developer = FakeClient("developer_api", [HttpError(429), HttpError(429)])
    vertex = FakeClient("vertex_ai", [{"ok": True}])
    router = RelationRouter(tmp_path, developer, vertex, CostLedger(tmp_path), sleep=lambda _seconds: None)
    result = router.call(
        logical_id="logical", prompt="prompt", schema={"type": "object"}, max_output_tokens=1,
        validator=lambda value: value,
    )
    assert result["transport"] == "vertex_ai"
    assert developer.calls == 2
    assert vertex.calls == 1
    assert router.state["developer_paused"] is True


def test_non_429_does_not_trigger_vertex(tmp_path):
    developer = FakeClient("developer_api", [HttpError(503)])
    vertex = FakeClient("vertex_ai", [{"ok": True}])
    router = RelationRouter(tmp_path, developer, vertex, CostLedger(tmp_path), sleep=lambda _seconds: None)
    with pytest.raises(RuntimeError, match="ineligible transport failure"):
        router.call(
            logical_id="logical", prompt="prompt", schema={"type": "object"}, max_output_tokens=1,
            validator=lambda value: value,
        )
    assert vertex.calls == 0


def test_max_tokens_does_not_spend_same_size_repair(tmp_path):
    developer = TruncatedFakeClient("developer_api", [])
    router = RelationRouter(tmp_path, developer, None, CostLedger(tmp_path), sleep=lambda _seconds: None)
    with pytest.raises(RuntimeError, match="same-size repair suppressed"):
        router.call(
            logical_id="logical", prompt="prompt", schema={"type": "object"}, max_output_tokens=1,
            validator=lambda value: (_ for _ in ()).throw(ValueError("invalid")),
        )
    assert developer.calls == 1


def test_hard_cost_ceiling_is_checked_before_send(tmp_path):
    ledger = CostLedger(tmp_path)
    with pytest.raises(RuntimeError, match=r"US\$60"):
        ledger.reserve("too-expensive", HARD_COMBINED_CEILING_USD + 0.01, "developer_api", repair=False)
    assert ledger.value["pending_reservations"] == {}


def test_completed_batch_cost_can_be_reconciled_idempotently(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.reserve("batch:test", 2.0, "developer_api", repair=False)
    ledger.complete("batch:test", 1.25)
    assert ledger.completed_cost("batch:test") == 1.25
    assert ledger.value["known_spend_usd"] == 1.25


def test_v2_ledger_preserves_v1_spend_and_enforces_authorised_us100_ceiling(tmp_path):
    source = tmp_path / "v1"
    target = tmp_path / "v2"
    source.mkdir()
    target.mkdir()
    ledger = CostLedger(source)
    ledger.reserve("old", 46.0, "developer_api", repair=False)
    ledger.complete("old", 45.0)
    revised = initialise_v2_cost_ledger(source, target)
    assert revised.value["known_spend_usd"] == 45.0
    assert revised.value["hard_combined_ceiling_usd"] == V2_HARD_COMBINED_CEILING_USD
    assert revised.value["v2_budget_authorisation"]["increase_usd"] == 40.0
    revised.reserve("new", 40.0, "developer_api", repair=False)
    revised.release("new", reason="test")
    with pytest.raises(RuntimeError, match=r"US\$100"):
        revised.reserve("too-much", 56.0, "developer_api", repair=False)


def test_v2_ledger_initialisation_is_idempotent_and_source_linked(tmp_path):
    source = tmp_path / "v1"
    target = tmp_path / "v2"
    source.mkdir()
    target.mkdir()
    CostLedger(source)
    first = initialise_v2_cost_ledger(source, target)
    second = initialise_v2_cost_ledger(source, target)
    assert first.value["v2_budget_authorisation"] == second.value["v2_budget_authorisation"]


def test_resumed_completed_batch_repairs_observability_count_without_network(tmp_path):
    (tmp_path / "normalised_responses").mkdir()
    items = []
    for logical_id in ("one", "two"):
        value = {"logical_call_id": logical_id, "status": "completed", "response": []}
        (tmp_path / "normalised_responses" / f"{logical_id}.json").write_text(
            json.dumps(value), encoding="utf-8",
        )
        items.append({"logical_id": logical_id})
    runner = DeveloperBatchRunner(tmp_path, object(), CostLedger(tmp_path))
    runner.jobs["jobs"]["batch"] = {"status": "completed", "completed_item_count": 1}
    runner._save()
    result = runner.run(batch_id="batch", items=items, validators={})
    assert set(result) == {"one", "two"}
    assert runner.jobs["jobs"]["batch"]["completed_item_count"] == 2


def test_v2_paid_execution_requires_exact_new_project_ceiling(tmp_path):
    with pytest.raises(RuntimeError, match="exact --confirm-combined-limit-usd 100"):
        run_material_veto_v2(
            tmp_path, tmp_path / "v1", tmp_path / "v2",
            execute=False, confirmed_limit=V2_HARD_COMBINED_CEILING_USD,
        )


def test_saved_response_is_recovered_before_another_paid_call(tmp_path):
    value = {"ok": True}
    prompt = "same prompt"
    logical_id = "saved"
    raw_dir = tmp_path / "raw_responses"
    raw_dir.mkdir()
    from semantic_alignment.relation_aware_veto import text_hash

    (raw_dir / f"{logical_id}--developer_api--1.json").write_text(json.dumps({
        "logical_call_id": logical_id,
        "transport": "developer_api",
        "model": "gemini-3.1-pro-preview",
        "prompt_hash": text_hash(prompt),
        "response": {"candidates": [{"content": {"parts": [{"text": json.dumps(value)}]}}]},
        "usage": {"input_tokens": 1, "output_tokens": 1, "thinking_tokens": 0},
        "cost_usd": 0.01,
        "received_at": "2026-07-16T00:00:00Z",
    }), encoding="utf-8")
    developer = FakeClient("developer_api", [])
    router = RelationRouter(tmp_path, developer, None, CostLedger(tmp_path), sleep=lambda _seconds: None)
    result = router.call(
        logical_id=logical_id,
        prompt=prompt,
        schema={"type": "object"},
        max_output_tokens=10,
        validator=lambda parsed: parsed,
    )
    assert result["response"] == value
    assert result["normalisation"][-1] == "recovered_from_saved_response"
    assert developer.calls == 0


def test_no_paid_call_without_explicit_execution(tmp_path):
    with pytest.raises(RuntimeError, match="requires --execute"):
        run_pilot(tmp_path, tmp_path / "output", execute=False, confirmed_limit=60)


def test_generation_config_has_no_tools_and_high_thinking():
    fake = object()
    client = GeminiRelationClient("developer_api", client=fake)
    config = client.config(CONTRACT_SCHEMA, 10)
    assert config.tools is None
    assert config.temperature == 0
    assert str(config.thinking_config.thinking_level).endswith("HIGH")


def test_batch_uses_typed_response_schema_not_native_json_schema_transport():
    client = GeminiRelationClient("developer_api", client=object())
    config = client.config(pair_response_schema(5), 100, batch=True)
    assert config.response_schema is not None
    assert config.response_json_schema is None
    assert config.response_schema.properties["records"].items.type.value == "OBJECT"


def test_transport_parity_rejects_setting_drift():
    developer = GeminiRelationClient("developer_api", client=object())
    vertex = GeminiRelationClient("vertex_ai", client=object())
    require_transport_parity(developer, vertex, CONTRACT_SCHEMA, 10)
    vertex.transport = "vertex_ai"
    original = vertex.settings_signature
    vertex.settings_signature = lambda schema, maximum: {**original(schema, maximum), "temperature": 1}
    with pytest.raises(RuntimeError, match="settings differ"):
        require_transport_parity(developer, vertex, CONTRACT_SCHEMA, 10)


def test_real_corpora_reconcile_without_unresolved_or_ineligible_images(historical_corpus_root):
    project = Path(__file__).resolve().parents[1]
    # Reproduce the original relation-veto trial and its authorised image set.
    packets, metadata = load_completed_corpus(
        historical_corpus_root / RESEARCH_RELATIVE
    )
    images, image_metadata = load_image_corpus(
        project,
        project / "image_discovery_research/thatcher_image_hunt_002/integration_preparation",
        packets,
    )
    assert len(packets) == 627
    assert metadata["unresolved_count"] == 5
    assert not set(metadata["unresolved_quote_ids"]).intersection(row["quote_id"] for row in packets)
    assert len(images) == 91
    assert image_metadata["baseline_count"] == 69
    assert image_metadata["discovered_count"] == 22
    assert {row["corpus"] for row in images} == {"original", "discovered_production_ready"}
    by_candidate = {row.get("candidate_id"): row for row in images if row.get("candidate_id")}
    relationships = {
        candidate_id: {
            tuple(assertion["participants"]): assertion["relationship"]
            for assertion in row["relationship_assertions"]
        }
        for candidate_id, row in by_candidate.items()
    }
    assert relationships["0b15f11312a31033907d"][("Margaret Thatcher", "Ronald Reagan")] == "ally"
    assert relationships["62fe0c8d105bf2ad7c95"][("Margaret Thatcher", "Mikhail Gorbachev")] == "adversary_to_partner"
    assert relationships["00f4566964e0c88838ad"][("Margaret Thatcher", "George H. W. Bush")] == "unknown"
    assert relationships["7720de19eda682fe608d"][("Margaret Thatcher", "Denis Thatcher")] == "unknown"


def test_legacy_quote_analysis_hash_is_resolved_only_by_exact_text():
    old_id = "b" * 64
    item = {"text": "Exact immutable text", "analysis": {"primary_topics": ["liberty"]}}
    resolved, aliases = _resolve_quote_analysis_items(
        [{"quote_id": QUOTE_ID, "quote_text": "Exact immutable text"}],
        {"items": {old_id: item}},
    )
    assert resolved == {QUOTE_ID: item}
    assert aliases == {QUOTE_ID: old_id}


def test_quote_analysis_alias_fails_closed_for_missing_or_conflicting_text():
    with pytest.raises(RuntimeError, match="lacks canonical quote"):
        _resolve_quote_analysis_items(
            [{"quote_id": QUOTE_ID, "quote_text": "Missing"}],
            {"items": {}},
        )
    with pytest.raises(RuntimeError, match="conflicting exact-text"):
        _resolve_quote_analysis_items(
            [{"quote_id": QUOTE_ID, "quote_text": "Same"}],
            {"items": {
                "b" * 64: {"text": "Same", "analysis": {"tone": ["warm"]}},
                "c" * 64: {"text": "Same", "analysis": {"tone": ["stern"]}},
            }},
        )


def test_prompts_do_not_contain_frozen_human_labels():
    from semantic_alignment.relation_aware_veto import contract_prompt, pair_prompt

    prompt = contract_prompt({
        **packet(),
        "date": "", "source_event": "", "historical_context": "", "immediate_subject": "",
        "intended_argument": "", "literal_meaning": "", "broader_principle": "",
        "mechanism": "", "claimed_consequence": "", "entities": [],
        "editorial_guidance": {
            "desired_first_impression": "", "historical_requirements": [],
            "must_be_visually_dominant": [], "must_not_dominate": [], "common_visual_mistakes": [],
        },
    })
    pair = pair_prompt(image("ally"), [{
        "pair_id": "pair", "quote_id": QUOTE_ID, "contract": contract(),
        "deterministic_contradictions": deterministic_contradictions(contract(), image("ally")),
    }])
    assert "prefer_existing_image" not in prompt + pair
    assert "human_positive" not in prompt + pair


def test_prefer_existing_image_is_not_automatically_a_safety_rejection():
    assert not _is_explicit_safety_rejection({
        "human_decision": "prefer_existing_image",
        "note": "",
    })


def test_generic_reject_is_not_automatically_severe_safety_evidence():
    assert not _is_explicit_safety_rejection({
        "human_decision": "reject_pairing",
        "note": "",
    })


def test_explicit_contradiction_note_is_safety_evidence():
    assert _is_explicit_safety_rejection({
        "human_decision": "prefer_existing_image",
        "note": "America was an ally of the UK; this is inappropriate for enemies becoming friends.",
    })


def test_operator_withdrawn_reagan_approval_is_not_positive_calibration():
    assert _is_operator_withdrawn_positive(
        {"human_positive": True},
        {"named_people": ["Margaret Thatcher", "Ronald Reagan"]},
    )
    assert not _is_operator_withdrawn_positive(
        {"human_positive": False},
        {"named_people": ["Margaret Thatcher", "Ronald Reagan"]},
    )
    assert not _is_operator_withdrawn_positive(
        {"human_positive": True},
        {"named_people": ["Margaret Thatcher"]},
    )
