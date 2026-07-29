from __future__ import annotations

import copy
import json
import socket
from pathlib import Path

import pytest

import quote_image_metadata_remediation as rem
from semantic_alignment.relation_aware_veto import maximum_cost


RUN = Path("semantic_alignment_research/quote_image_metadata_remediation_001")


def quote(**updates):
    value = {
        "quote_id": "a" * 64,
        "quote_text": "Freedom requires responsibility.",
        "verified_text": "Freedom requires responsibility.",
        "verification_status": "exact",
        "research_confidence": "high",
        "canonical_speaker": "Margaret Thatcher",
        "thatcher_attribution_status": "confirmed_thatcher",
        "dominant_proposition": "Freedom and responsibility are inseparable.",
        "claim_type": "abstract_principle",
        "mentioned_entities": ["Margaret Thatcher"],
        "visually_required_entities": [],
        "mentioned_relationships": [],
        "visually_required_relationships": [],
        "source_occasion": "Speech",
        "source_date": "1996",
        "visual_event_requirement": None,
        "visual_period_requirement": None,
        "required_actor_roles": [],
        "required_actor_count_minimum": 0,
        "required_action": None,
        "required_transition": None,
        "literal_visualisation_required": False,
        "neutral_portrait_allowed": True,
        "symbolic_image_allowed": True,
        "multi_person_image_required": False,
        "relationship_evidence_required": False,
        "event_specific_image_required": False,
        "hard_visual_conflicts": [],
        "material_false_implications": [],
        "evidence_fields": ["quote_text"],
        "contract_confidence": "high",
        "contract_version": rem.QUOTE_CONTRACT_VERSION,
        "field_provenance": {},
    }
    value.update(updates)
    return value


def image(**updates):
    value = {
        "image_id": "original:t01.jpg",
        "image_hash": "b" * 64,
        "path": "/tmp/t01.jpg",
        "principal_subject": "Margaret Thatcher",
        "principal_identity_basis": "curated_collection",
        "source_class": "original",
        "source_caption": "",
        "event": None,
        "date_or_period": None,
        "known_participants": ["Margaret Thatcher"],
        "unknown_significant_participant_count": 0,
        "visible_action": "posing",
        "visible_interaction": "",
        "dominant_visual_story": "Neutral portrait of the principal subject.",
        "scene_types": ["formal_portrait"],
        "setting": {},
        "documented_relationships": [],
        "relationship_evidence_available": False,
        "safe_as_neutral_portrait": True,
        "possible_false_implications": [],
        "metadata_confidence": "high",
        "evidence_paths": ["curated collection"],
        "people_count_minimum": 1,
        "contract_version": rem.IMAGE_CONTRACT_VERSION,
        "field_provenance": {},
    }
    value.update(updates)
    return value


def test_module_import_has_no_network_or_production_side_effect() -> None:
    assert rem.ROOT.is_dir()
    assert rem.DEFAULT_RUN.name == "quote_image_metadata_remediation_001"


def test_official_model_policy_and_failed_hardware_gate_are_explicit() -> None:
    assert rem.OFFICIAL_MODEL_REPOSITORY == "Qwen/Qwen3-VL-8B-Instruct-GGUF"
    assert rem.OFFICIAL_MODEL_LICENSE == "Apache-2.0"
    assert "official" in rem.OFFICIAL_PROJECTOR_POLICY
    manifest = json.loads((RUN / "local_model_manifest.json").read_text())
    assert manifest["decision"] == "skip_local_model"
    assert manifest["revision"] is None
    assert manifest["safe_to_download_and_run"] is False


def test_prepare_model_refuses_unpinned_or_unsafe_download() -> None:
    with pytest.raises(rem.RemediationError, match="preflight failed"):
        rem.prepare_local_model(RUN, True, rem.OFFICIAL_MODEL_REPOSITORY)


def test_offline_network_guard_blocks_dns_and_socket() -> None:
    with rem.offline_network_guard():
        with pytest.raises(rem.RemediationError):
            socket.getaddrinfo("example.com", 443)
        with pytest.raises(rem.RemediationError):
            socket.socket().connect(("127.0.0.1", 9))


def test_corpus_contract_counts_and_unresolved_exclusion() -> None:
    packets = rem.load_packets()
    quotes, images = rem.load_contracts(RUN)
    assert len(packets) == 627
    assert len(quotes) == 626
    assert set(quotes) < set(packets)
    assert len(images) == 91
    unresolved = json.loads((rem.RESEARCH_DIR / "final_unresolved/final_research_status.json").read_text())
    assert len(unresolved["unresolved_quote_ids"]) == 5
    assert not set(unresolved["unresolved_quote_ids"]) & set(quotes)


@pytest.mark.parametrize(
    "speaker",
    [
        "Abi Morgan (spoken by Meryl Streep as Margaret Thatcher)",
        "Alexander Dubcek (quoted by Margaret Thatcher)",
        "Unknown (Misattributed to Margaret Thatcher)",
    ],
)
def test_speaker_attribution_does_not_confuse_context_with_the_speaker(speaker: str) -> None:
    _canonical_speaker, status = rem.speaker_attribution_status({
        "speaker": speaker,
        "verification_status": "misattributed",
    })
    assert status == "contradicted_non_thatcher"


@pytest.mark.parametrize(
    "speaker",
    [
        "Margaret Thatcher",
        "Margaret Thatcher (as Margaret Roberts)",
    ],
)
def test_speaker_attribution_accepts_thatchers_own_canonical_identity(speaker: str) -> None:
    _canonical_speaker, status = rem.speaker_attribution_status({
        "speaker": speaker,
        "verification_status": "exact",
    })
    assert status == "confirmed_thatcher"


def test_speaker_attribution_does_not_infer_thatcher_from_bare_maiden_name() -> None:
    _canonical_speaker, status = rem.speaker_attribution_status({
        "speaker": "Margaret Roberts",
        "verification_status": "exact",
    })
    assert status == "contradicted_non_thatcher"


def test_collection_level_thatcher_identity_attestation() -> None:
    _, images = rem.load_contracts(RUN)
    originals = [row for row in images.values() if row["source_class"] == "original"]
    assert len(originals) == 69
    assert all(row["principal_subject"] == "Margaret Thatcher" for row in originals)
    assert all(row["principal_identity_basis"] == "curated_collection" for row in originals)


def test_neutral_portrait_allowed_for_abstract_quote() -> None:
    result = rem.deterministic_pair_decision(quote(), image())
    assert result["decision"] == "allow"
    assert result["basis"] == "deterministic_neutral_portrait_policy"


def test_confirmed_non_thatcher_speaker_is_affirmative_portrait_substitution() -> None:
    result = rem.deterministic_pair_decision(
        quote(
            canonical_speaker="Michael Jordan",
            thatcher_attribution_status="contradicted_non_thatcher",
            neutral_portrait_allowed=False,
        ),
        image(),
    )
    assert result["decision"] == "veto"
    assert result["reasons"][0]["rule"] == "non_thatcher_speaker_portrait_substitution"


def test_unknown_canonical_speaker_abstains() -> None:
    result = rem.deterministic_pair_decision(
        quote(
            canonical_speaker="Unknown",
            thatcher_attribution_status="unavailable",
            neutral_portrait_allowed=False,
        ),
        image(),
    )
    assert result["decision"] == "unknown"
    assert "canonical_speaker_not_source_grounded" in result["reasons"]


def test_missing_required_named_participant_is_detected_despite_thatcher_presence() -> None:
    result = rem.deterministic_pair_decision(
        quote(
            visually_required_entities=["Mikhail Gorbachev"],
            neutral_portrait_allowed=False,
        ),
        image(known_participants=["Margaret Thatcher"]),
    )
    assert result["decision"] == "unknown"
    assert "required_participant_identity_not_source_grounded" in result["reasons"]


def test_source_occasion_and_date_are_not_visual_requirements() -> None:
    row = quote(source_occasion="A 1996 speech", source_date="1996")
    assert row["visual_event_requirement"] is None
    assert row["visual_period_requirement"] is None
    assert rem.deterministic_pair_decision(row, image(date_or_period="1984"))["decision"] == "allow"


def test_named_institution_is_not_automatically_visually_required() -> None:
    packets = rem.load_packets()
    old = json.loads((rem.V2_DIR / "semantic_contracts_v2.json").read_text())["records"]
    quote_id = "8143e19d5c4d4e159aa40941118a0aeadf1ea316ed4b0f4ba9f93345326fc407"
    contract, defects = rem.quote_contract_v3(packets[quote_id], old[quote_id])
    assert contract["visually_required_entities"] == []
    assert contract["neutral_portrait_allowed"] is True
    assert "quote_entity_overreach" in {row["code"] for row in defects}


def test_falklands_source_occasion_does_not_require_commons_scene() -> None:
    packets = rem.load_packets()
    old = json.loads((rem.V2_DIR / "semantic_contracts_v2.json").read_text())["records"]
    quote_id = "c748bcf2c3e7304875dbeebc43d02084393225e0ca9c48a668375c1d276751e7"
    contract, _defects = rem.quote_contract_v3(packets[quote_id], old[quote_id])
    assert contract["visual_event_requirement"] == "Falklands military operation"
    assert contract["neutral_portrait_allowed"] is True
    assert rem.deterministic_pair_decision(contract, image())["decision"] == "allow"


def test_irrelevant_entity_does_not_change_visual_requirements() -> None:
    before = quote(mentioned_entities=["Margaret Thatcher"])
    after = copy.deepcopy(before)
    after["mentioned_entities"].append("An irrelevant institution")
    assert before["visually_required_entities"] == after["visually_required_entities"] == []
    assert rem.deterministic_pair_decision(before, image()) == rem.deterministic_pair_decision(after, image())


def test_ally_cannot_depict_enemy_to_friend_transition() -> None:
    contract = quote(
        claim_type="relationship_transformation",
        visually_required_relationships=[{"initial": "enemy", "final": "friend", "transition": "enemy_to_friend"}],
        required_transition="enemy_to_friend",
        neutral_portrait_allowed=False,
        multi_person_image_required=True,
        relationship_evidence_required=True,
        required_actor_count_minimum=2,
    )
    picture = image(
        known_participants=["Margaret Thatcher", "Ronald Reagan"],
        documented_relationships=[{"participants": ["Margaret Thatcher", "Ronald Reagan"], "relationship": "ally", "evidence": ["source"]}],
        relationship_evidence_available=True,
        safe_as_neutral_portrait=False,
        people_count_minimum=2,
    )
    result = rem.deterministic_pair_decision(contract, picture)
    assert result["decision"] == "veto"
    assert {row["rule"] for row in result["reasons"]} >= {"ally_adversary_confusion", "missing_transition"}


def test_unknown_relationship_is_not_treated_as_safe_or_as_adversary() -> None:
    contract = quote(
        claim_type="relationship_transformation",
        visually_required_relationships=[{"initial": "enemy", "final": "friend", "transition": "enemy_to_friend"}],
        required_transition="enemy_to_friend",
        neutral_portrait_allowed=False,
        relationship_evidence_required=True,
        multi_person_image_required=True,
        required_actor_count_minimum=2,
    )
    result = rem.deterministic_pair_decision(contract, image(safe_as_neutral_portrait=False))
    assert result["decision"] == "unknown"
    assert result["basis"] == "source_evidence_gap"


def test_wrong_named_participant_is_affirmative_veto() -> None:
    contract = quote(
        claim_type="named_entity", mentioned_entities=["Margaret Thatcher", "Mikhail Gorbachev"],
        visually_required_entities=["Mikhail Gorbachev"], neutral_portrait_allowed=False,
    )
    picture = image(known_participants=["Margaret Thatcher", "Ronald Reagan"], safe_as_neutral_portrait=False)
    result = rem.deterministic_pair_decision(contract, picture)
    assert result["decision"] == "veto"
    assert result["reasons"][0]["rule"] == "wrong_named_entity"


def test_textual_cooccurrence_without_evidence_cannot_create_relationship() -> None:
    source = {
        "image_id": "discovered:x", "image_sha256": "c" * 64, "local_path": "/tmp/x.jpg",
        "corpus": "discovered_production_ready", "identity_basis": "source_caption",
        "named_people": ["Margaret Thatcher", "Ronald Reagan"], "source_caption": "Both people pictured",
        "source_event": "", "source_date": "", "source_evidence": [],
        "relationship_assertions": [{
            "participants": ["Margaret Thatcher", "Ronald Reagan"],
            "relationship": "ally", "evidence": [],
        }],
        "visual": {"people_count_minimum": 2, "scene_types": ["meeting"], "activities": [], "scene_summary": "Meeting"},
    }
    contract, defects = rem.image_contract_v3(source)
    assert contract["documented_relationships"][0]["relationship"] == "unknown"
    assert "image_relationship_inferred_from_cooccurrence" in {row["code"] for row in defects}


def test_contract_lint_has_no_unsupported_identity_relationship_or_date_rules() -> None:
    lint = json.loads((RUN / "contract_lint_report.json").read_text())
    assert lint["passed"] is True
    assert lint["unsupported_identity_count"] == 0
    assert lint["unsupported_relationship_count"] == 0
    assert lint["source_date_only_visual_restriction_count"] == 0


def test_aggregation_deduplicates_pairs_and_corrects_unknown_taxonomy() -> None:
    data = json.loads((RUN / "simulator_pair_aggregation.json").read_text())
    keys = [row["pair_key"] for row in data["records"]]
    assert len(keys) == len(set(keys)) == 18767
    unknown = [row for row in data["records"] if row["taxonomy"] == "production_pair_unknown"]
    assert unknown and all(row["unknown_count"] > 0 for row in unknown)
    assert all(row["taxonomy"] != "genuine_no_safe_known_image" for row in unknown)


def test_offline_rejudgement_unknowns_are_not_safe() -> None:
    summary = json.loads((RUN / "offline_rejudgement_summary.json").read_text())
    manifest = json.loads((RUN / "offline_pair_decisions.json").read_text())
    assert summary["residual_count"] == summary["decision_counts"]["unknown"]
    assert all(row["decision"] in {"allow", "veto", "unknown"} for row in manifest["records"].values())


def test_multimodal_cost_reserves_image_tokens(tmp_path: Path) -> None:
    image_path = tmp_path / "x.jpg"
    image_path.write_bytes(b"not-decoded-by-constructor")
    prompt = rem.MultimodalPrompt("prompt", image_path, "d" * 64, extra_input_tokens=4096)
    assert prompt.gemini_contents
    assert maximum_cost(prompt, 1000, batch=True) > maximum_cost("prompt", 1000, batch=True)


def test_cost_preflight_is_below_planned_and_hard_limits() -> None:
    value = json.loads((RUN / "api_cost_preflight.json").read_text())
    assert value["typed_response_schema"] is True
    assert value["planned_work_conservative_maximum_usd"] <= 85
    assert value["combined_conservative_exposure_usd"] <= 100
    assert value["tools"] is value["google_search"] is value["url_context"] is False


def test_no_human_decision_or_mutation_endpoint() -> None:
    source = Path(rem.__file__).read_text()
    assert "Keep" not in source
    assert "Approve pairing" not in source
    assert "def do_POST" in source and "405" in source


def test_live_shadow_manifest_hash_is_still_snapshot_value() -> None:
    manifest = json.loads((RUN / "run_manifest.json").read_text())
    assert rem.sha256_file(rem.SHADOW_DIR / "material_veto_v2_shadow_manifest.json") == manifest["live_shadow_manifest_sha256"]


def test_complete_input_change_invalidates_pair_hash() -> None:
    q1 = quote()
    q2 = copy.deepcopy(q1); q2["neutral_portrait_allowed"] = False
    assert rem.digest({"quote": q1, "image": image(), "rule": rem.RULE_VERSION}) != rem.digest({"quote": q2, "image": image(), "rule": rem.RULE_VERSION})


def test_semantic_pair_hash_ignores_batching_but_covers_all_material_inputs() -> None:
    baseline = rem.semantic_pair_input_hash(quote(), image(), second_pass=False)
    assert baseline == rem.semantic_pair_input_hash(copy.deepcopy(quote()), copy.deepcopy(image()), second_pass=False)
    assert baseline != rem.semantic_pair_input_hash(
        quote(neutral_portrait_allowed=False), image(), second_pass=False,
    )
    assert baseline != rem.semantic_pair_input_hash(quote(), image(visible_action="speaking"), second_pass=False)
    assert baseline != rem.semantic_pair_input_hash(quote(), image(), second_pass=True)


def test_paid_judgement_reuse_is_limited_to_byte_identical_semantic_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[Path, dict]] = []

    def capture_write(path: Path, payload: dict) -> None:
        writes.append((Path(path), payload))

    monkeypatch.setattr(rem, "atomic_write_json", capture_write)
    eligible, _source_gaps = rem._ai_eligible_residuals(RUN)
    reuse = rem.reusable_saved_judgements(RUN, eligible)
    assert len(eligible) == 320
    assert reuse["first_reused_count"] == 320
    assert reuse["pending_first_count"] == 0
    assert reuse["second_reused_count"] == 320
    assert len(writes) == 1
    assert writes[0][0] == RUN / "reused_ai_judgements.json"
    assert writes[0][1]["records"]


def test_veto_reason_codes_include_model_contradictions() -> None:
    pair = {
        "decision": "veto", "deterministic_reasons": [],
        "first_pass": {"contradiction_types": ["wrong_named_entity", "none"]},
    }
    assert rem.pair_veto_reason_codes(pair) == ["wrong_named_entity"]


def test_model_cannot_turn_missing_literal_event_depiction_into_veto() -> None:
    model_row = {
        "decision": "veto", "final_decision": "veto",
        "final_reason": "model_veto_or_uncertainty",
        "contradiction_types": ["wrong_event"],
    }
    result = rem.normalise_unsupported_model_literalism(
        model_row,
        quote(
            claim_type="named_event", visual_event_requirement="Falklands military operation",
            event_specific_image_required=True, neutral_portrait_allowed=True,
        ),
        image(event=None, documented_relationships=[]),
        [],
    )
    assert result["decision"] == "veto"  # Raw model verdict is retained.
    assert result["model_final_decision_before_source_policy"] == "veto"
    assert result["final_decision"] == "allow"
    assert "unsupported_literal_depiction_veto_rejected" in result["normalisation"]


def test_source_grounded_wrong_event_veto_is_not_normalised_away() -> None:
    model_row = {
        "decision": "veto", "final_decision": "veto",
        "contradiction_types": ["wrong_event"],
    }
    result = rem.normalise_unsupported_model_literalism(
        model_row,
        quote(visual_event_requirement="Falklands military operation"),
        image(event="European Council summit"),
        [],
    )
    assert result["final_decision"] == "veto"


def test_unrelated_specific_event_is_not_an_affirmative_contradiction() -> None:
    model_row = {
        "decision": "veto",
        "final_decision": "veto",
        "final_reason": "model_veto_or_uncertainty",
        "contradiction_types": ["misleading_dominant_message"],
    }
    result = rem.normalise_unsupported_model_literalism(
        model_row,
        quote(),
        image(
            event="A source-grounded diplomatic signing",
            documented_relationships=[
                {
                    "participants": ["Margaret Thatcher", "A counterpart"],
                    "relationship": "unknown",
                    "evidence": [],
                }
            ],
            safe_as_neutral_portrait=False,
        ),
        [],
    )
    assert result["decision"] == "veto"  # Raw provider output remains auditable.
    assert result["final_decision"] == "allow"
    assert "unsupported_literal_depiction_veto_rejected" in result["normalisation"]


def test_generic_theme_only_is_not_an_affirmative_contradiction() -> None:
    model_row = {
        "decision": "veto",
        "final_decision": "veto",
        "final_reason": "model_veto_or_uncertainty",
        "contradiction_types": ["generic_theme_only"],
    }
    result = rem.normalise_unsupported_model_literalism(
        model_row,
        quote(),
        image(
            event="A source-grounded summit",
            known_participants=["Margaret Thatcher", "A counterpart"],
            safe_as_neutral_portrait=False,
        ),
        [],
    )
    assert result["final_decision"] == "allow"


def test_deterministic_contradiction_cannot_be_overridden_as_literalism() -> None:
    model_row = {
        "decision": "veto", "final_decision": "veto",
        "contradiction_types": ["insufficient_actor_count"],
    }
    result = rem.normalise_unsupported_model_literalism(
        model_row, quote(), image(), [{"rule": "wrong_named_entity"}],
    )
    assert result["final_decision"] == "veto"


def test_missing_required_actor_is_unknown_not_affirmative_veto() -> None:
    model_row = {
        "decision": "veto", "final_decision": "veto",
        "final_reason": "model_veto_or_uncertainty",
        "contradiction_types": ["insufficient_actor_count"],
    }
    result = rem.normalise_unsupported_model_literalism(
        model_row,
        quote(visually_required_entities=["Mikhail Gorbachev"], neutral_portrait_allowed=False),
        image(),
        [],
    )
    assert result["decision"] == "veto"
    assert result["final_decision"] == "unknown"
    assert "missing_evidence_veto_downgraded_to_unknown" in result["normalisation"]


def test_absent_named_actor_is_not_wrong_named_entity_substitution() -> None:
    model_row = {
        "decision": "veto", "final_decision": "veto",
        "contradiction_types": ["portrait_substitution", "wrong_named_entity"],
    }
    result = rem.normalise_unsupported_model_literalism(
        model_row,
        quote(visually_required_entities=["Mikhail Gorbachev"], neutral_portrait_allowed=False),
        image(known_participants=["Margaret Thatcher"]),
        [],
    )
    assert result["final_decision"] == "unknown"


def test_affirmatively_wrong_named_actor_remains_veto() -> None:
    model_row = {
        "decision": "veto", "final_decision": "veto",
        "contradiction_types": ["wrong_named_entity"],
    }
    result = rem.normalise_unsupported_model_literalism(
        model_row,
        quote(visually_required_entities=["Mikhail Gorbachev"], neutral_portrait_allowed=False),
        image(known_participants=["Margaret Thatcher", "Ronald Reagan"]),
        [],
    )
    assert result["final_decision"] == "veto"


def test_first_and_second_prompts_are_independently_worded() -> None:
    pair = {"pair_id": "p", "quote_id": quote()["quote_id"], "contract": rem._adjudication_contract(quote()), "deterministic_contradictions": []}
    first = rem.adjudication_prompt(image(), [pair], second_pass=False)
    second = rem.adjudication_prompt(image(), [pair], second_pass=True)
    assert rem.FIRST_PROMPT_VERSION in first
    assert rem.SECOND_PROMPT_VERSION in second
    assert first != second
    assert "another judge's decision" in second


def test_residual_batch_items_have_single_pair_max_token_recovery() -> None:
    residual = list(rem.jsonl(RUN / "residual_ai_queue.jsonl"))[:2]
    items = rem.group_residual_requests(RUN, residual, second_pass=False)
    assert items
    for item in items:
        assert item["expected_pair_ids"] == item["pair_ids"]
        assert len(item["recovery_splits"]) == len(item["pair_ids"])
        assert all(split["pair"]["pair_id"] in str(split["prompt"]) for split in item["recovery_splits"])
        assert all(callable(split["validator"]) for split in item["recovery_splits"])


def test_truncated_json_salvage_returns_only_complete_records() -> None:
    text = '{"records":[{"pair_id":"one","decision":"allow"},{"pair_id":"two","decision":'
    assert rem.extract_complete_record_objects(text) == [{"pair_id": "one", "decision": "allow"}]


def test_second_pass_uses_smaller_batches_than_first_pass() -> None:
    assert rem.SECOND_PASS_BATCH_SIZE < rem.PAIR_BATCH_SIZE


def test_generated_images_are_outside_historical_contracts() -> None:
    _, images = rem.load_contracts(RUN)
    assert all(not row["image_id"].startswith("generated:") for row in images.values())


def test_live_production_sources_are_not_task_outputs() -> None:
    run = RUN.resolve()
    assert all(run not in path.resolve().parents and path.resolve() != run for path in rem.SOURCE_FILES.values())


def test_candidate_lookup_keeps_unknown_distinct_from_safe() -> None:
    manifest = {
        "records": {
            "one": {"quote_id": "q", "image_hash": "i", "decision": "unknown"},
            "two": {"quote_id": "q", "image_hash": "j", "decision": "allow"},
        }
    }
    lookup, by_quote = rem._manifest_lookup(manifest)
    assert lookup[("q", "i")]["decision"] == "unknown"
    assert [row["decision"] for row in by_quote["q"]] == ["unknown", "allow"]


def test_candidate_lookup_rejects_duplicate_quote_image_identity() -> None:
    manifest = {
        "records": {
            "one": {"quote_id": "q", "image_hash": "i", "decision": "allow"},
            "two": {"quote_id": "q", "image_hash": "i", "decision": "veto"},
        }
    }
    with pytest.raises(rem.RemediationError, match="duplicate quote/image"):
        rem._manifest_lookup(manifest)


def test_generated_profiles_remain_out_of_scope_and_do_not_change_live_config(tmp_path: Path) -> None:
    config_path = rem.ROOT / "mrsMThatcher.local.json"
    config_hash = rem.sha256_file(config_path) if config_path.is_file() else None
    fixture_config_path = tmp_path / "mrsMThatcher.local.json"
    fixture_config_path.write_text(
        json.dumps(
            {
                "ENABLE_GENERATED_IMAGE_POOL": False,
                "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 10,
            }
        ),
        encoding="utf-8",
    )
    payload = rem._generated_profiles(
        tmp_path,
        config_path=fixture_config_path,
        generated_analysis_path=rem.ROOT / "generated_image_analysis.json",
    )
    assert len(payload["profiles"]) == 7
    assert all(row["semantic_veto_status"] == "out_of_scope_generated" for row in payload["profiles"])
    assert payload["production_configuration_changed"] is False
    assert (
        rem.sha256_file(config_path) if config_path.is_file() else None
    ) == config_hash
