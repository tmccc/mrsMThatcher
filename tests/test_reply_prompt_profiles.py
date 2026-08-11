"""Offline contracts for current and compact reply prompt profiles."""

from __future__ import annotations

import copy
import json

import pytest

import reply_strategy
from tools import reply_prompt_profiles as profiles


COMPACT_PROMPT_SHA256_AT_A465817 = {
    "proposer": "922aff370f775ff9c18e2e7a445600519f14bfba8610ee6db99f9daf99e0f8da",
    "reviewer": "36c0577d9b0ee8c536ea638591c9ce1a0e052a9e482f80aedc16b489b3cad089",
    "no_reply_review": "2b6677ed5766676acde2c9010ba04feda57b077e02daaabb103a319921643b67",
    "claim_auditor": "5a0ccdd28239b6eb5808870cfa6c6fe2cee0c32342f9243a9e1c8ec057ac05fe",
}
COMPACT_PROFILE_MANIFEST_SHA256_AT_A465817 = (
    "e37f4cb4f5abf6e44e95aea77cca36e486168c596480855e84633a65ec66ea0a"
)
CURRENT_PROMPT_SHA256_AT_A465817 = {
    "proposer": "06b00d02ce6c0182b9ec2e9ca52a22e9ca03f9b40ef45a3dc9f198ca30351f72",
    "reviewer": "778e9d6c325bdfb3d5f9b0a83814dd0f16acc355bd43d8c6fb817b7fb96d349e",
    "no_reply_review": "db578711a2f5ea36d7e4bc78e4997188e410407f57545680fe5498a4ee0e5b1d",
    "claim_auditor": "53aa8015b1ea90719d05578c2b2ba20fc9ddc939d23e5287255c44ded24f6e03",
}
CURRENT_PROFILE_MANIFEST_SHA256_AT_A465817 = (
    "edd2985d37c690c02556c518dd6e92ad39db8e379267a61b90ddb9d4650368f8"
)
CURRENT_PROMPT_VERSIONS_AT_A465817 = {
    "PROPOSER_PROMPT_VERSION": "ai-first-proposer-v15",
    "REVIEWER_PROMPT_VERSION": "independent-reply-reviewer-v13",
    "NO_REPLY_REVIEW_PROMPT_VERSION": "independent-no-reply-review-v1",
    "CLAIM_AUDITOR_PROMPT_VERSION": "claim-inventory-auditor-v5",
}


def context() -> dict[str, object]:
    return {
        "target_id": "target",
        "thread_id": "thread",
        "lane": "mention",
        "incoming_contribution": "A civil contribution.",
        "quoted_post": None,
        "parent_thread": [],
        "clarification_request": None,
        "current_date": "2026-08-10",
    }


def proposer() -> dict[str, object]:
    return {
        "interpretation": "Civil and relevant.",
        "no_reply_reason": "",
        "mode": "opinion_or_principle",
        "tone": "firm",
        "proposed_reply": "Principle deserves a direct answer.",
        "direct_factual_question_present": False,
        "requested_answer_type": "none",
        "direct_answer_text": "",
        "factual_claims": [],
        "exact_thatcher_wording_used": False,
        "exact_thatcher_wording": "",
    }


def prompt_state() -> dict[str, object]:
    return {name: getattr(reply_strategy, name) for name in profiles.PATCHED_NAMES}


def expected_reviewer_output_contract(proposed_reply: str) -> dict[str, object]:
    return {
        "required_sentence_texts_verbatim": reply_strategy.split_reply_sentences(
            proposed_reply
        ),
        "sentence_assessments_rule": (
            "Exactly one sentence_assessment for each required sentence, "
            "preserving the supplied sentence text and order."
        ),
        "sentence_factual_claims_rule": (
            "Each sentence factual_claims list must include every externally "
            "checkable clause visible in that sentence, verbatim and in "
            "reading order, whether supported or unsupported."
        ),
        "actual_factual_claims_rule": (
            "Exact ordered concatenation of every "
            "sentence_assessment.factual_claims list, preserving unsupported "
            "claims and duplicate claim text."
        ),
        "unsupported_claims_remain_in_actual_factual_claims": True,
        "evidence_must_not_filter_visible_claim_inventory": True,
    }


def valid_reviewer_document(
    *, asserts_actor_state_or_action: bool = True
) -> dict[str, object]:
    claim = "The council opened the library."
    return {
        "verdict": "approve",
        "summary": "The synthetic response satisfies the reviewer contract.",
        "reasons": [],
        "actual_factual_claims": [claim],
        "unsupported_factual_claims": [],
        "sentence_assessments": [{
            "sentence_text": claim,
            "classification": "factual_claim",
            "factual_claims": [claim],
            "non_factual_basis": "none",
            "world_claim_checks": {
                "asserts_actor_state_or_action": asserts_actor_state_or_action,
                "asserts_causal_or_predictive_relation": False,
                "asserts_comparison_or_outcome": False,
                "asserts_historical_date_or_quantity": False,
                "asserts_meaning_or_attribution": False,
                "purely_non_factual": False,
            },
        }],
        "direct_factual_question_present": False,
        "requested_answer_type": "none",
        "direct_answer_complete": False,
        "direct_answer_text": "",
        "topically_relevant": True,
        "endorses_unsupported_allegation": False,
        "contains_unsupported_factual_claims": False,
        "actor_action_relationship_correct": True,
        "direction_polarity_correct": True,
        "dates_quantities_correct": True,
        "quotation_attribution_correct": True,
        "original_prose_clearly_not_historical_quotation": True,
        "mode_and_tone_match": True,
        "suitable_for_account": True,
        "revision_instructions": "",
    }


def test_current_profile_points_to_exact_production_prompt_functions() -> None:
    captured = profiles.production_prompt_functions()
    assert all(getattr(reply_strategy, name) is function for name, function in captured.items())
    manifest = profiles.profile_manifest("current")
    assert manifest["uses_exact_production_prompt_functions"] is True


def test_current_activation_makes_no_function_or_constant_change() -> None:
    before = prompt_state()
    with profiles.activate_profile("current", recent_account_replies=["one"]):
        assert prompt_state() == before
    assert prompt_state() == before


def test_current_profile_functions_versions_and_hashes_match_a465817() -> None:
    assert all(
        getattr(reply_strategy, name) is function
        for name, function in profiles.production_prompt_functions().items()
    )
    manifest = profiles.profile_manifest("current")
    assert manifest["profile_version"] == "current-production-profile-v1"
    assert manifest["prompt_version_constants"] == CURRENT_PROMPT_VERSIONS_AT_A465817
    assert {
        name: row["sha256"] for name, row in manifest["prompts"].items()
    } == CURRENT_PROMPT_SHA256_AT_A465817
    assert manifest["manifest_sha256"] == CURRENT_PROFILE_MANIFEST_SHA256_AT_A465817


def test_compact_activation_patches_only_permitted_functions_and_constants() -> None:
    before_module = dict(vars(reply_strategy))
    before = prompt_state()
    with profiles.activate_profile("compact"):
        after_module = dict(vars(reply_strategy))
        changed = {
            name
            for name in before_module
            if after_module[name] is not before_module[name]
            and after_module[name] != before_module[name]
        }
        assert changed == set(profiles.PATCHED_NAMES)
        assert all(getattr(reply_strategy, name) is not before[name] for name in profiles.PROMPT_FUNCTION_NAMES)
        assert all(getattr(reply_strategy, name) != before[name] for name in profiles.PROMPT_VERSION_NAMES)


def test_activation_restores_everything_after_success() -> None:
    before = prompt_state()
    with profiles.activate_profile("compact"):
        pass
    assert prompt_state() == before
    profiles.verify_current_production_objects()


def test_activation_restores_everything_after_exception() -> None:
    before = prompt_state()
    with pytest.raises(LookupError, match="pipeline failed"):
        with profiles.activate_profile("compact", recent_account_replies=["case one"]):
            raise LookupError("pipeline failed")
    assert prompt_state() == before
    profiles.verify_current_production_objects()


def test_nested_incompatible_activation_is_refused() -> None:
    with profiles.activate_profile("compact"):
        with pytest.raises(RuntimeError, match="cannot activate current"):
            with profiles.activate_profile("current"):
                pytest.fail("incompatible activation must not start")


def test_compact_proposer_word_cap() -> None:
    row = profiles.profile_manifest("compact")["prompts"]["proposer"]
    assert row["word_count"] == profiles.prompt_word_count(profiles.COMPACT_PROPOSER_SYSTEM_PROMPT)
    assert row["word_count"] <= 500


def test_compact_v4_changes_only_profile_and_reviewer_version_metadata() -> None:
    manifest = profiles.profile_manifest("compact")
    assert manifest["profile_version"] == "compact-reply-profile-v4"
    assert manifest["prompt_version_constants"] == {
        "PROPOSER_PROMPT_VERSION": "compact-proposer-v1",
        "REVIEWER_PROMPT_VERSION": "compact-reviewer-v4",
        "NO_REPLY_REVIEW_PROMPT_VERSION": "compact-no-reply-review-v1",
        "CLAIM_AUDITOR_PROMPT_VERSION": "compact-claim-auditor-v1",
    }
    assert manifest["manifest_sha256"] != COMPACT_PROFILE_MANIFEST_SHA256_AT_A465817


def test_all_compact_system_prompts_are_byte_identical_to_a465817() -> None:
    assert {
        name: profiles.sha256_text(prompt)
        for name, prompt in profiles.COMPACT_PROMPTS.items()
    } == COMPACT_PROMPT_SHA256_AT_A465817


def test_compact_reviewer_user_payload_has_exact_output_contract() -> None:
    draft = proposer()
    proposed_reply = "First “quoted” clause? Second clause — exactly!"
    draft["proposed_reply"] = proposed_reply
    with profiles.activate_profile("compact"):
        _, reviewer_user = reply_strategy._reviewer_prompts(
            context(), draft, [{"verdict": "insufficient"}], None
        )
    payload = json.loads(reviewer_user)
    contract = payload["reviewer_output_contract"]
    assert contract == expected_reviewer_output_contract(proposed_reply)
    assert contract["required_sentence_texts_verbatim"] == [
        "First “quoted” clause?",
        "Second clause — exactly!",
    ]
    assert contract["unsupported_claims_remain_in_actual_factual_claims"] is True
    assert contract["evidence_must_not_filter_visible_claim_inventory"] is True
    assert contract["actual_factual_claims_rule"] == (
        "Exact ordered concatenation of every "
        "sentence_assessment.factual_claims list, preserving unsupported "
        "claims and duplicate claim text."
    )
    assert set(contract) == {
        "required_sentence_texts_verbatim",
        "sentence_assessments_rule",
        "sentence_factual_claims_rule",
        "actual_factual_claims_rule",
        "unsupported_claims_remain_in_actual_factual_claims",
        "evidence_must_not_filter_visible_claim_inventory",
    }
    assert {
        "historical_output",
        "candidate_stratum",
        "calibration_metadata",
    }.isdisjoint(payload)


def test_compact_initial_and_revision_reviewers_receive_same_output_contract() -> None:
    initial_proposer = proposer()
    initial_proposer["proposed_reply"] = "Initial first sentence. Initial second sentence."
    revision_proposer = proposer()
    revision_proposer["proposed_reply"] = "Revised first sentence. Revised second sentence."
    with profiles.activate_profile("compact"):
        _, initial_user = reply_strategy._reviewer_prompts(
            context(), initial_proposer, [], None
        )
        _, revision_user = reply_strategy._reviewer_prompts(
            context(), revision_proposer, [], None
        )
    initial_contract = json.loads(initial_user)["reviewer_output_contract"]
    revision_contract = json.loads(revision_user)["reviewer_output_contract"]
    assert initial_contract == expected_reviewer_output_contract(
        initial_proposer["proposed_reply"]
    )
    assert revision_contract == expected_reviewer_output_contract(
        revision_proposer["proposed_reply"]
    )
    assert {
        key: value
        for key, value in initial_contract.items()
        if key != "required_sentence_texts_verbatim"
    } == {
        key: value
        for key, value in revision_contract.items()
        if key != "required_sentence_texts_verbatim"
    }


def test_compact_final_reviewer_word_cap() -> None:
    row = profiles.profile_manifest("compact")["prompts"]["reviewer"]
    assert row["word_count"] == profiles.prompt_word_count(
        profiles.COMPACT_REVIEWER_SYSTEM_PROMPT
    )
    assert row["word_count"] <= 430


def test_compact_reviewer_schema_paragraph_states_world_claim_boolean_contract() -> None:
    paragraph = (
        "Schema discipline:"
        + profiles.COMPACT_REVIEWER_SYSTEM_PROMPT.split("Schema discipline:", 1)[1]
        .split("\n\n", 1)[0]
    )
    compact = " ".join(paragraph.split())
    assert (
        "direct-question classification from incoming contribution, not proposed reply"
        in compact
    )
    assert (
        'direct_factual_question_present=false, requested_answer_type="none", '
        'direct_answer_complete=false and direct_answer_text=""'
        in compact
    )
    assert "Non-direct:" in compact and 'direct_answer_text=""' in compact
    assert "narrowest permitted answer type" in compact
    assert "copy the complete first reply sentence exactly into direct_answer_text" in compact
    assert (
        "Assess each reply sentence once, verbatim and in order"
        in compact
    )
    assert "List every checkable clause, supported or not" in compact
    assert "Empty factual_claims means all five specific world-claim flags false" in compact
    assert "all five specific world-claim flags false and purely_non_factual=true" in compact
    assert "otherwise at least one specific flag true" in compact
    assert "at least one specific flag true and purely_non_factual=false" in compact
    assert "actual_factual_claims exactly concatenates sentence lists" in compact


def test_compact_no_reply_reviewer_word_cap() -> None:
    row = profiles.profile_manifest("compact")["prompts"]["no_reply_review"]
    assert row["word_count"] <= 150


def test_compact_claim_auditor_word_cap() -> None:
    row = profiles.profile_manifest("compact")["prompts"]["claim_auditor"]
    assert row["word_count"] <= 160


def test_required_compact_semantic_invariants_are_explicit() -> None:
    proposer_prompt = profiles.COMPACT_PROPOSER_SYSTEM_PROMPT
    reviewer_prompt = profiles.COMPACT_REVIEWER_SYSTEM_PROMPT
    no_reply_prompt = profiles.COMPACT_NO_REPLY_REVIEW_SYSTEM_PROMPT
    auditor_prompt = profiles.COMPACT_CLAIM_AUDITOR_SYSTEM_PROMPT
    assert "Use courtesy only\nfor essentially social contributions" in proposer_prompt
    assert "prefer opinion_or_principle" in proposer_prompt
    assert "Specificity: it could not fit\nseveral unrelated posts" in proposer_prompt
    assert "Added value: it does more than paraphrase and\nacknowledge" in proposer_prompt
    assert "“well noted”" in proposer_prompt and "is not enough" in proposer_prompt
    assert "correctable defects" in reviewer_prompt and "use revise" in reviewer_prompt
    assert "never force humour" in proposer_prompt
    assert "Do\nnot force wit" in reviewer_prompt
    assert "spam or advertising" in no_reply_prompt
    assert "copy that\nsentence to direct_answer_text" in proposer_prompt
    assert "Every listed claim requires evidence" in proposer_prompt
    assert "Complete every world-claim check" in auditor_prompt


def test_obsolete_permissive_formulations_are_absent() -> None:
    combined = "\n".join(profiles.COMPACT_PROMPTS.values())
    assert "A concise, topically relevant courtesy response is suitable account behaviour" not in combined
    assert "Do not confuse brevity with an unrelated platitude" not in combined


def test_strategy_and_evidence_prompt_versions_remain_unchanged() -> None:
    strategy_version = reply_strategy.STRATEGY_VERSION
    evidence_version = reply_strategy.EVIDENCE_PROMPT_VERSION
    with profiles.activate_profile("compact"):
        assert reply_strategy.STRATEGY_VERSION == strategy_version
        assert reply_strategy.EVIDENCE_PROMPT_VERSION == evidence_version
    manifest = profiles.profile_manifest("compact")
    assert manifest["strategy_version"] == strategy_version
    assert manifest["evidence_prompt_version"] == evidence_version


def test_production_reviewer_rejects_non_direct_answer_declaration() -> None:
    document = copy.deepcopy(valid_reviewer_document())
    document["direct_answer_complete"] = True
    with pytest.raises(
        ValueError,
        match="reviewer non-direct contribution cannot declare a direct answer",
    ):
        reply_strategy.validate_reviewer(
            document,
            maximum_claims=8,
            proposed_reply="The council opened the library.",
        )


def test_production_reviewer_rejects_incomplete_flattened_claim_inventory() -> None:
    document = copy.deepcopy(valid_reviewer_document())
    document["actual_factual_claims"] = []
    with pytest.raises(
        reply_strategy.NonRetryableReviewerResponseError,
        match="reviewer sentence claim inventory is incomplete or out of order",
    ):
        reply_strategy.validate_reviewer(
            document,
            maximum_claims=8,
            proposed_reply="The council opened the library.",
        )


def test_production_reviewer_rejects_factual_sentence_without_specific_world_claim_flag() -> None:
    document = valid_reviewer_document(asserts_actor_state_or_action=False)
    assessment = document["sentence_assessments"][0]
    assert assessment["classification"] == "factual_claim"
    assert len(assessment["factual_claims"]) == 1
    assert not any(
        value
        for name, value in assessment["world_claim_checks"].items()
        if name != "purely_non_factual"
    )
    assert assessment["world_claim_checks"]["purely_non_factual"] is False
    with pytest.raises(
        reply_strategy.NonRetryableReviewerResponseError,
        match="^reviewer sentence world-claim checks contradict each other$",
    ):
        reply_strategy.validate_reviewer(
            document,
            maximum_claims=8,
            proposed_reply="The council opened the library.",
        )


def test_production_reviewer_accepts_exact_flattened_claim_inventory() -> None:
    document = valid_reviewer_document()
    assert reply_strategy.validate_reviewer(
        document,
        maximum_claims=8,
        proposed_reply="The council opened the library.",
    ) == document


def test_production_reviewer_preserves_claim_order_across_two_sentences() -> None:
    first_claim = "The council opened the library."
    second_claim = "The trust restored the roof."
    document = valid_reviewer_document()
    first_assessment = document["sentence_assessments"][0]
    first_assessment["sentence_text"] = first_claim
    first_assessment["factual_claims"] = [first_claim]
    second_assessment = copy.deepcopy(first_assessment)
    second_assessment["sentence_text"] = second_claim
    second_assessment["factual_claims"] = [second_claim]
    document["sentence_assessments"] = [first_assessment, second_assessment]
    document["actual_factual_claims"] = [first_claim, second_claim]
    proposed_reply = f"{first_claim} {second_claim}"
    assert reply_strategy.validate_reviewer(
        document,
        maximum_claims=8,
        proposed_reply=proposed_reply,
    ) == document
    out_of_order = copy.deepcopy(document)
    out_of_order["actual_factual_claims"] = [second_claim, first_claim]
    with pytest.raises(
        reply_strategy.NonRetryableReviewerResponseError,
        match="reviewer sentence claim inventory is incomplete or out of order",
    ):
        reply_strategy.validate_reviewer(
            out_of_order,
            maximum_claims=8,
            proposed_reply=proposed_reply,
        )


def test_production_reviewer_retains_duplicate_claim_text_across_sentences() -> None:
    claim = "The council opened the library."
    document = valid_reviewer_document()
    assessment = document["sentence_assessments"][0]
    document["sentence_assessments"] = [assessment, copy.deepcopy(assessment)]
    document["actual_factual_claims"] = [claim, claim]
    proposed_reply = f"{claim} {claim}"
    assert reply_strategy.validate_reviewer(
        document,
        maximum_claims=8,
        proposed_reply=proposed_reply,
    ) == document
    deduplicated = copy.deepcopy(document)
    deduplicated["actual_factual_claims"] = [claim]
    with pytest.raises(
        reply_strategy.NonRetryableReviewerResponseError,
        match="reviewer sentence claim inventory is incomplete or out of order",
    ):
        reply_strategy.validate_reviewer(
            deduplicated,
            maximum_claims=8,
            proposed_reply=proposed_reply,
        )


def test_production_reviewer_accepts_purely_non_factual_courtesy_sentence() -> None:
    sentence = "Thank you for writing."
    document = copy.deepcopy(valid_reviewer_document())
    document["actual_factual_claims"] = []
    document["sentence_assessments"] = [{
        "sentence_text": sentence,
        "classification": "courtesy",
        "factual_claims": [],
        "non_factual_basis": "courtesy",
        "world_claim_checks": {
            "asserts_actor_state_or_action": False,
            "asserts_causal_or_predictive_relation": False,
            "asserts_comparison_or_outcome": False,
            "asserts_historical_date_or_quantity": False,
            "asserts_meaning_or_attribution": False,
            "purely_non_factual": True,
        },
    }]
    assert reply_strategy.validate_reviewer(
        document,
        maximum_claims=8,
        proposed_reply=sentence,
    ) == document


def test_compact_reviewer_receives_only_five_most_recent_replies() -> None:
    recent = [f"reply-{number}" for number in range(8)]
    with profiles.activate_profile("compact", recent_account_replies=recent):
        _, user = reply_strategy._reviewer_prompts(context(), proposer(), [], None)
    payload = json.loads(user)
    assert payload["recent_account_replies_for_style_check"] == recent[-5:]


def test_compact_no_reply_reviewer_receives_same_last_five_recent_replies() -> None:
    recent = [f"reply-{number}" for number in range(8)]
    with profiles.activate_profile("compact", recent_account_replies=recent):
        _, no_reply_user = reply_strategy._no_reply_review_prompts(context(), proposer())
        _, final_user = reply_strategy._reviewer_prompts(context(), proposer(), [], None)
    no_reply_payload = json.loads(no_reply_user)
    final_payload = json.loads(final_user)
    assert no_reply_payload["recent_account_replies_for_repetition_check"] == recent[-5:]
    assert final_payload["recent_account_replies_for_style_check"] == recent[-5:]


def test_compact_reviewer_recent_replies_do_not_leak_between_cases() -> None:
    with profiles.activate_profile("compact", recent_account_replies=["case-one-only"]):
        _, first_user = reply_strategy._reviewer_prompts(context(), proposer(), [], None)
    with profiles.activate_profile("compact", recent_account_replies=["case-two-only"]):
        _, second_user = reply_strategy._reviewer_prompts(context(), proposer(), [], None)
    with profiles.activate_profile("compact"):
        _, empty_user = reply_strategy._reviewer_prompts(context(), proposer(), [], None)
    assert json.loads(first_user)["recent_account_replies_for_style_check"] == ["case-one-only"]
    assert json.loads(second_user)["recent_account_replies_for_style_check"] == ["case-two-only"]
    assert "case-one-only" not in second_user
    assert json.loads(empty_user)["recent_account_replies_for_style_check"] == []


def test_compact_no_reply_recent_replies_do_not_leak_between_cases() -> None:
    with profiles.activate_profile("compact", recent_account_replies=["case-one-only"]):
        _, first_user = reply_strategy._no_reply_review_prompts(context(), proposer())
    with profiles.activate_profile("compact", recent_account_replies=["case-two-only"]):
        _, second_user = reply_strategy._no_reply_review_prompts(context(), proposer())
    with profiles.activate_profile("compact"):
        _, empty_user = reply_strategy._no_reply_review_prompts(context(), proposer())
    assert json.loads(first_user)["recent_account_replies_for_repetition_check"] == [
        "case-one-only"
    ]
    assert json.loads(second_user)["recent_account_replies_for_repetition_check"] == [
        "case-two-only"
    ]
    assert "case-one-only" not in second_user
    assert json.loads(empty_user)["recent_account_replies_for_repetition_check"] == []


def test_current_reviewer_receives_no_compact_payload_fields() -> None:
    with profiles.activate_profile("current", recent_account_replies=["must-not-appear"]):
        _, reviewer_user = reply_strategy._reviewer_prompts(context(), proposer(), [], None)
        _, no_reply_user = reply_strategy._no_reply_review_prompts(context(), proposer())
    reviewer_payload = json.loads(reviewer_user)
    no_reply_payload = json.loads(no_reply_user)
    assert "reviewer_output_contract" not in reviewer_payload
    assert "recent_account_replies_for_style_check" not in reviewer_payload
    assert "recent_account_replies_for_repetition_check" not in no_reply_payload
    assert "must-not-appear" not in reviewer_user
    assert "must-not-appear" not in no_reply_user
