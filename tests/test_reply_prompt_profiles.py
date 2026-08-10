"""Offline contracts for current and compact reply prompt profiles."""

from __future__ import annotations

import json

import pytest

import reply_strategy
from tools import reply_prompt_profiles as profiles


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


def test_compact_final_reviewer_word_cap() -> None:
    row = profiles.profile_manifest("compact")["prompts"]["reviewer"]
    assert row["word_count"] <= 430


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


def test_compact_reviewer_receives_only_five_most_recent_replies() -> None:
    recent = [f"reply-{number}" for number in range(8)]
    with profiles.activate_profile("compact", recent_account_replies=recent):
        _, user = reply_strategy._reviewer_prompts(context(), proposer(), [], None)
    payload = json.loads(user)
    assert payload["recent_account_replies_for_style_check"] == recent[-5:]


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


def test_current_reviewer_receives_no_additional_recent_reply_field() -> None:
    with profiles.activate_profile("current", recent_account_replies=["must-not-appear"]):
        _, user = reply_strategy._reviewer_prompts(context(), proposer(), [], None)
    payload = json.loads(user)
    assert "recent_account_replies_for_style_check" not in payload
    assert "must-not-appear" not in user
