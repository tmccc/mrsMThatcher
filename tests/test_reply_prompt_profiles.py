"""Offline contracts for current and compact reply prompt profiles."""

from __future__ import annotations

import copy
import json

import pytest

import reply_strategy
from tools import reply_prompt_profiles as profiles


COMPACT_PROMPT_SHA256_AT_FBAC2478 = {
    "proposer": "922aff370f775ff9c18e2e7a445600519f14bfba8610ee6db99f9daf99e0f8da",
    "reviewer": "26447ccf142376a7b56cf07e8aab49f4ed68f2a2ef3fb61546a22d1a5c915696",
    "no_reply_review": "2b6677ed5766676acde2c9010ba04feda57b077e02daaabb103a319921643b67",
    "claim_auditor": "5a0ccdd28239b6eb5808870cfa6c6fe2cee0c32342f9243a9e1c8ec057ac05fe",
}
CURRENT_PROMPT_SHA256_AT_FBAC2478 = {
    "proposer": "06b00d02ce6c0182b9ec2e9ca52a22e9ca03f9b40ef45a3dc9f198ca30351f72",
    "reviewer": "778e9d6c325bdfb3d5f9b0a83814dd0f16acc355bd43d8c6fb817b7fb96d349e",
    "no_reply_review": "db578711a2f5ea36d7e4bc78e4997188e410407f57545680fe5498a4ee0e5b1d",
    "claim_auditor": "53aa8015b1ea90719d05578c2b2ba20fc9ddc939d23e5287255c44ded24f6e03",
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


def valid_reviewer_document() -> dict[str, object]:
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
                "asserts_actor_state_or_action": True,
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


def test_current_profile_functions_and_hashes_match_fbac2478() -> None:
    assert all(
        getattr(reply_strategy, name) is function
        for name, function in profiles.production_prompt_functions().items()
    )
    manifest = profiles.profile_manifest("current")
    assert {
        name: row["sha256"] for name, row in manifest["prompts"].items()
    } == CURRENT_PROMPT_SHA256_AT_FBAC2478


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


def test_compact_v2_changes_only_the_final_reviewer_prompt() -> None:
    manifest = profiles.profile_manifest("compact")
    current_hashes = {
        name: row["sha256"] for name, row in manifest["prompts"].items()
    }
    changed = {
        name
        for name, old_hash in COMPACT_PROMPT_SHA256_AT_FBAC2478.items()
        if current_hashes[name] != old_hash
    }
    assert manifest["profile_version"] == "compact-reply-profile-v2"
    assert manifest["prompt_version_constants"] == {
        "PROPOSER_PROMPT_VERSION": "compact-proposer-v1",
        "REVIEWER_PROMPT_VERSION": "compact-reviewer-v2",
        "NO_REPLY_REVIEW_PROMPT_VERSION": "compact-no-reply-review-v1",
        "CLAIM_AUDITOR_PROMPT_VERSION": "compact-claim-auditor-v1",
    }
    assert changed == {"reviewer"}
    assert current_hashes["proposer"] == COMPACT_PROMPT_SHA256_AT_FBAC2478["proposer"]
    assert current_hashes["no_reply_review"] == COMPACT_PROMPT_SHA256_AT_FBAC2478[
        "no_reply_review"
    ]
    assert current_hashes["claim_auditor"] == COMPACT_PROMPT_SHA256_AT_FBAC2478[
        "claim_auditor"
    ]


def test_compact_final_reviewer_word_cap() -> None:
    row = profiles.profile_manifest("compact")["prompts"]["reviewer"]
    assert row["word_count"] <= 430


def test_compact_reviewer_schema_paragraph_is_bounded_and_explicit() -> None:
    paragraph = (
        "Schema discipline:"
        + profiles.COMPACT_REVIEWER_SYSTEM_PROMPT.split("Schema discipline:", 1)[1]
        .split("\n\n", 1)[0]
    )
    compact = " ".join(paragraph.split())
    assert profiles.prompt_word_count(paragraph) <= 90
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
        "each proposed_reply sentence exactly one verbatim sentence_assessment in order"
        in compact
    )
    assert (
        "factual_claims includes every externally checkable clause verbatim and in order"
        in compact
    )
    assert "unsupported included" in compact
    assert "actual_factual_claims exactly concatenates those lists" in compact
    assert "Evidence affects support/verdict, never permits inventory omission" in compact


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


def test_production_reviewer_accepts_corrected_synthetic_contract() -> None:
    document = valid_reviewer_document()
    assert reply_strategy.validate_reviewer(
        document,
        maximum_claims=8,
        proposed_reply="The council opened the library.",
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


def test_current_reviewer_receives_no_additional_recent_reply_field() -> None:
    with profiles.activate_profile("current", recent_account_replies=["must-not-appear"]):
        _, reviewer_user = reply_strategy._reviewer_prompts(context(), proposer(), [], None)
        _, no_reply_user = reply_strategy._no_reply_review_prompts(context(), proposer())
    reviewer_payload = json.loads(reviewer_user)
    no_reply_payload = json.loads(no_reply_user)
    assert "recent_account_replies_for_style_check" not in reviewer_payload
    assert "recent_account_replies_for_repetition_check" not in no_reply_payload
    assert "must-not-appear" not in reviewer_user
    assert "must-not-appear" not in no_reply_user
