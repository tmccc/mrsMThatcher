from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from semantic_alignment.io import atomic_write_json, read_json
from semantic_alignment.pipeline import (
    CostLedger, MissingAuthoritativeCost, XAIClient, XAIResult, critic_pairs, estimate_cost, generated_image_inventory,
    pending_images, pending_quotes, require_execution_approval, run_quote_analysis,
)
from semantic_alignment.prompts import critic_prompt, image_prompt, quote_prompt
from semantic_alignment.replay import replay_candidate_cache, select_formula
from semantic_alignment.schemas import validate_critic_result, validate_image_fingerprint, validate_quote_fingerprint
from semantic_alignment.shortlist import build_shortlist, pre_score


QHASH = "a" * 64
IHASH = "b" * 64


def quote(**changes):
    value = {"schema_version": 2, "analysis_kind": "quote_semantic_fingerprint", "quote_hash": QHASH, "quote_text": "Free trade benefits consumers.", "dominant_message": "Free trade increases prosperity and consumer benefit.", "core_claim": "Freer trade benefits consumers.", "claims": [{"claim": "Free trade lowers consumer costs.", "importance": "primary", "confidence": 0.95}], "primary_issue": "free_trade", "primary_themes": ["free_trade"], "secondary_themes": ["competition", "growth"], "specific_concepts": ["trade", "consumers"], "desired_visual_evidence": ["ports", "cargo", "consumer goods"], "explicit_contrasts": [], "not_about": ["generic capitalism versus socialism"], "confidence": 0.95}
    value.update(changes); return value


def image(name="tg_test.png", **changes):
    value = {"schema_version": 2, "analysis_kind": "image_implied_message", "image_basename": name, "sha256": IHASH, "dominant_message": "International commerce moves goods to consumers.", "core_implied_claim": "International commerce supplies consumer goods.", "implied_claims": [{"claim": "Trade supplies consumer goods.", "salience": "dominant", "confidence": 0.9, "visual_support": ["container port", "consumer goods"]}], "primary_issue": "free_trade", "primary_themes": ["free_trade"], "secondary_messages": ["competition"], "specific_concepts": ["cargo", "trade"], "visual_evidence": ["container port", "consumer goods"], "emotional_tone": ["optimism"], "ambiguity": "low", "confidence": 0.9}
    value.update(changes); return value


def critic(name="tg_test.png", **changes):
    value = {"schema_version": 2, "analysis_kind": "quote_image_semantic_alignment", "quote_hash": QHASH, "image_basename": name, "quote_core_claim": "Freer trade benefits consumers.", "image_core_implied_claim": "International commerce supplies consumer goods.", "claim_relationship": "strong_support", "matched_claims": [{"quote_claim": "Free trade lowers consumer costs.", "image_claim": "Trade supplies consumer goods.", "match_strength": 0.8}], "unillustrated_primary_claims": [], "image_claims_not_required_by_quote": [], "semantic_alignment_score": 88, "directness_score": 84, "editorial_power_score": 75, "visual_specificity_score": 80, "primary_issue_match": True, "dominant_message_match": "strong", "mismatch_type": "strong_support", "explanation": "The visual evidence directly depicts international commerce.", "stronger_visual_direction": [], "confidence": 0.9}
    value.update(changes); return value


def test_schema_validation_and_invalid_scores():
    assert validate_quote_fingerprint(quote())["quote_hash"] == QHASH
    assert validate_image_fingerprint(image())["sha256"] == IHASH
    assert validate_critic_result(critic())["semantic_alignment_score"] == 88
    with pytest.raises(ValueError): validate_critic_result(critic(semantic_alignment_score=101))
    with pytest.raises(ValueError): validate_quote_fingerprint(quote(schema_version=1))
    with pytest.raises(ValueError): validate_image_fingerprint(image(implied_claims=[{"claim": "Unsupported", "salience": "dominant", "confidence": .5, "visual_support": []}]))


def test_atomic_write_replaces_complete_json(tmp_path):
    path = tmp_path / "db.json"; atomic_write_json(path, {"a": 1}); atomic_write_json(path, {"b": 2})
    assert read_json(path) == {"b": 2} and not path.with_suffix(".json.tmp").exists()


def test_prompt_isolation():
    assert "image" not in quote_prompt("Trade matters").lower().split("quotation:")[1]
    prompt = image_prompt().lower()
    assert "origin quotation" in prompt and "free trade" not in prompt
    q = quote(origin_quote="SECRET", production_winner="SECRET2", expected_category="SECRET4")
    i = image(generation_prompt="SECRET3", baseline_score=99)
    rendered = critic_prompt(q, i)
    assert "SECRET" not in rendered and "baseline_score" not in rendered and "expected_category" not in rendered
    assert QHASH in rendered and "tg_test.png" in rendered


def test_deterministic_shortlist_and_forced_inclusion():
    weak = image("weak.png", primary_issue="socialism", primary_themes=["socialism"], specific_concepts=[])
    strong = image("strong.png")
    first = build_shortlist(quote(), [weak, strong], limit=1, forced={"weak.png": "production_winner"})
    second = build_shortlist(quote(), [strong, weak], limit=1, forced={"weak.png": "production_winner"})
    assert first == second and {row["image_basename"] for row in first} == {"strong.png", "weak.png"}


def test_not_about_conflict_penalises_generic_ideology():
    direct_score, _ = pre_score(quote(), image())
    generic_score, signals = pre_score(quote(), image(primary_issue="capitalism_vs_socialism", primary_themes=["capitalism", "socialism"], specific_concepts=["capitalism", "socialism"]))
    assert generic_score < direct_score and signals["not_about_conflict"] > 0


def test_pending_hash_change_and_resume():
    qdb = {"items": {QHASH: quote()}}
    assert pending_quotes([{"quote_hash": QHASH, "quote_text": quote()["quote_text"]}], qdb) == []
    assert pending_quotes([{"quote_hash": QHASH, "quote_text": "changed"}], qdb)
    idb = {"items": {"tg_test.png": image()}}
    assert pending_images([{"image_basename": "tg_test.png", "sha256": IHASH}], idb) == []
    assert pending_images([{"image_basename": "tg_test.png", "sha256": "c" * 64}], idb)


def test_active_and_quarantined_inventories_are_separate(tmp_path):
    active = tmp_path / "generated_review_approved_images"; active.mkdir()
    (active / "tg_active.png").write_bytes(b"active")
    quarantine = tmp_path / "generated_image_quarantine/transactions/t1/images"; quarantine.mkdir(parents=True)
    (quarantine / "tg_quarantined.png").write_bytes(b"quarantine")
    assert [row["image_basename"] for row in generated_image_inventory(tmp_path)] == ["tg_active.png"]
    assert [row["image_basename"] for row in generated_image_inventory(tmp_path, quarantined=True)] == ["tg_quarantined.png"]


def test_cost_limit_and_no_implicit_external_call():
    estimate = estimate_cost(quotes=2, images=3, critic=4)
    with pytest.raises(RuntimeError, match="--execute-xai"): require_execution_approval(estimate, execute_xai=False, cost_limit=99)
    with pytest.raises(RuntimeError, match="exceeds"): require_execution_approval(estimate, execute_xai=True, cost_limit=0.01)
    require_execution_approval(estimate, execute_xai=True, cost_limit=99)


def test_mocked_xai_and_interrupted_resume_checkpoint(tmp_path):
    calls = []
    class Client:
        model = "mock"
        def structured(self, prompt, **_kwargs):
            calls.append(prompt)
            if len(calls) == 2: raise RuntimeError("interrupted")
            content = {key: value for key, value in quote().items() if key not in {"schema_version", "analysis_kind", "quote_hash", "quote_text"}}
            return XAIResult(content=content, usage={"cost_in_usd_ticks": 1000, "prompt_tokens": 10, "completion_tokens": 10}, model="mock", raw={})
    rows = [{"quote_hash": QHASH, "quote_text": "Free trade benefits consumers."}, {"quote_hash": "c" * 64, "quote_text": "Second quote"}]
    db = {"schema_version": 2, "analysis_kind": "quote_semantic_fingerprint_database", "prompt_version": "quote-semantic-v2-claims", "items": {}, "failures": {}, "updated_at": ""}
    output = tmp_path / "quotes.json"
    assert run_quote_analysis(rows, db, output, Client(), checkpoint_every=1) == 1
    saved = read_json(output)
    assert QHASH in saved["items"] and "c" * 64 in saved["failures"]


def test_duplicate_pair_suppression():
    rows = [{"quote_hash": QHASH, "image_basename": "a.png"}, {"quote_hash": QHASH, "image_basename": "a.png"}]
    assert critic_pairs(rows, {"items": rows}) == [(QHASH, "a.png")]


def test_formula_replay_and_candidate_exhaustion():
    cache = {(QHASH, "a.png"): critic("a.png", semantic_alignment_score=20), (QHASH, "b.png"): critic("b.png", semantic_alignment_score=90)}
    candidates = [{"quote_hash": QHASH, "image_basename": "a.png", "baseline_score": 20}, {"quote_hash": QHASH, "image_basename": "b.png", "baseline_score": 10}]
    assert select_formula(candidates, cache, "semantic_only")["image_basename"] == "b.png"
    events = [{"quote_hash": QHASH, "production_winner": "a.png", "candidates": candidates}, {"quote_hash": "d" * 64, "production_winner": "x.png", "candidates": []}]
    result = replay_candidate_cache(events, cache)
    assert result["formulas"]["semantic_only"]["winner_changes"] == 1
    assert result["formulas"]["semantic_only"]["candidate_exhaustion"] == 1
    assert result["formulas"]["semantic_only"]["unique_images"] == 1


def test_known_free_trade_case_classifies_generic_overlap_lower():
    direct, _ = pre_score(quote(), image())
    generic, _ = pre_score(quote(), image(primary_issue="capitalism_vs_socialism", primary_themes=["capitalism", "socialism"], dominant_message="Capitalism produces prosperity while socialism produces decline.", visual_evidence=["modern skyline", "urban decay"]))
    assert direct > generic


@pytest.mark.parametrize("relationship", ["direct_equivalence", "strong_support", "partial_support", "related_but_not_equivalent", "generic_ideological_substitution", "secondary_theme_only", "contradiction", "unrelated", "ambiguous"])
def test_critic_relationship_taxonomy(relationship):
    assert validate_critic_result(critic(claim_relationship=relationship, mismatch_type=relationship))["claim_relationship"] == relationship


def test_claim_level_prompts_penalise_ideological_substitution():
    rendered = critic_prompt(quote(), image()).lower()
    assert "specific policy" in rendered and "unillustrated" in rendered and "generic_ideological_substitution" in rendered


def test_xai_client_uses_injected_transport_only():
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"model": "grok-4.5", "usage": {"cost_in_usd_ticks": 1000}, "choices": [{"message": {"content": json.dumps({"ok": True})}}]}
    calls = []
    client = XAIClient(api_key="fake", transport=lambda *a, **k: (calls.append((a, k)), Response())[1])
    result = client.structured("prompt", stage="quote", schema={"type": "object"})
    assert result.content == {"ok": True} and result.usage["cost_in_usd_ticks"] == 1000 and len(calls) == 1
    payload = calls[0][1]["json"]
    assert payload["model"] == "grok-4.5"
    assert payload["reasoning_effort"] == "low" and payload["max_tokens"] == 1000
    assert payload["response_format"]["type"] == "json_schema" and "tools" not in payload


def test_cost_ledger_persists_exact_ticks_and_blocks_missing_cost(tmp_path):
    path = tmp_path / "cost.json"; ledger = CostLedger(path)
    row = ledger.record(call_id="quote:a", stage="quote", item_key="a", usage={"cost_in_usd_ticks": 25_000_000, "prompt_tokens": 10, "completion_tokens": 4, "prompt_tokens_details": {"cached_tokens": 2}, "completion_tokens_details": {"reasoning_tokens": 1}}, model="grok-4.5")
    assert row["cost_usd"] == 0.0025 and row["cached_tokens"] == 2 and row["reasoning_tokens"] == 1
    assert CostLedger(path).ticks() == 25_000_000
    with pytest.raises(MissingAuthoritativeCost):
        ledger.record(call_id="quote:b", stage="quote", item_key="b", usage={}, model="grok-4.5")
    with pytest.raises(MissingAuthoritativeCost):
        CostLedger(path)


def test_ambiguous_transport_blocks_fresh_ledger_without_retry(tmp_path):
    path = tmp_path / "cost.json"; ledger = CostLedger(path, run_id="fresh")
    ledger.block_ambiguous(stage="quote", item_key="x", model="grok-4.5", error=TimeoutError("lost"))
    saved = read_json(path)
    assert saved["status"] == "blocked_ambiguous_cost" and len(saved["ambiguous_requests"]) == 1
    with pytest.raises(MissingAuthoritativeCost): CostLedger(path)


def test_two_fresh_ledgers_are_isolated(tmp_path):
    first = CostLedger(tmp_path / "a.json", run_id="a")
    second = CostLedger(tmp_path / "b.json", run_id="b")
    first.record(call_id="quote:a", stage="quote", item_key="a", usage={"cost_in_usd_ticks": 100}, model="grok-4.5")
    assert second.ticks() == 0
