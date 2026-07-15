from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from semantic_alignment.hybrid_gemini_review import review_prompt
from semantic_alignment.hybrid_multi_provider_review import (
    COMBINED_LIMIT_USD,
    MODELS,
    PROVIDER_LIMITS_USD,
    ReviewProviderClient,
    SharedCostGuard,
    compare_all_ai_reviews,
    prepare_multi_provider_review,
    run_multi_provider_reviews,
)


def _case(index: int) -> dict:
    case_id = f"case-{index:03d}"
    packet_a = {
        "quote_id": hashlib.sha256(f"a-{index}".encode()).hexdigest(),
        "quote_text": "Government does not itself create prosperity.",
        "intended_argument": "Enterprise creates the wealth government spends.",
        "broader_principle": "Government has a role but cannot substitute for enterprise.",
        "verification_status": "exact",
        "research_confidence": "high",
        "source_event": "Speech",
    }
    packet_b = {
        "quote_id": hashlib.sha256(f"b-{index}".encode()).hexdigest(),
        "quote_text": "An unrelated historical observation.",
        "intended_argument": "A separate argument.",
        "broader_principle": "A separate principle.",
        "verification_status": "normalised",
        "research_confidence": "medium",
        "source_event": "Interview",
    }
    return {
        "case_id": case_id,
        "target_id": case_id,
        "lane": "mention",
        "incoming": {
            "author_handle": "reader",
            "text": f"Government creates all wealth, example {index}.",
            "timestamp": "2026-07-01T12:00:00Z",
        },
        "direct_parent": {
            "post_id": f"parent-{index}",
            "author_handle": "MrsMThatcher",
            "text": "The state cannot create prosperity by decree.",
            "timestamp": "2026-07-01T11:59:00Z",
        },
        "quoted_post": None,
        "older_thread_context": [],
        "query_basis": {
            "substantive_query": True,
            "incoming_text_used": True,
            "direct_parent_used": True,
            "older_thread_used": False,
            "quoted_post_used": False,
        },
        "context_status": "complete",
        "missing_context": [],
        "A": [packet_a],
        "B": [packet_b],
    }


def _response(cases: list[dict]) -> dict:
    return {
        "reviews": [{
            "case_id": case["case_id"],
            "choice": "A_better",
            "intervention": "historical_correction",
            "evidence_desirability": "desirable",
            "confidence": "high",
            "factual_claim_present": True,
            "rationale": "Set A directly addresses the incoming factual claim.",
            "reason_tags": ["exact_match_stronger"],
            "packet_assessments": [
                {
                    "side": side,
                    "quote_id": packet["quote_id"],
                    "assessment": "relevant" if side == "A" else "irrelevant",
                    "reason": "Directly relevant." if side == "A" else "Not relevant.",
                }
                for side in ("A", "B")
                for packet in case[side]
            ],
        } for case in cases],
    }


def _write_trial(path: Path) -> list[dict]:
    cases = [_case(index) for index in range(100)]
    review_dir = path / "manual_review"
    review_dir.mkdir(parents=True)
    (path / "review_sample_100.json").write_text(json.dumps({
        "context_version": "hybrid-review-context-v2",
        "case_count": 100,
        "items": cases,
    }), encoding="utf-8")
    assignments = {
        case["case_id"]: ({"A": "hybrid", "B": "lexical"} if index % 2 else {"A": "lexical", "B": "hybrid"})
        for index, case in enumerate(cases)
    }
    (path / "blind_assignment_manifest.json").write_text(json.dumps({
        "assignments": assignments,
    }), encoding="utf-8")
    (review_dir / "human_reviews.json").write_text(json.dumps({
        "items": {"sentinel": {"choice": "roughly_equal"}},
    }), encoding="utf-8")
    batches = []
    for offset in range(0, 100, 5):
        values = cases[offset:offset + 5]
        prompt = review_prompt(values)
        batches.append({
            "batch_id": f"gemini-review-{offset // 5 + 1:03d}",
            "case_ids": [case["case_id"] for case in values],
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        })
    (review_dir / "gemini_review_manifest.json").write_text(json.dumps({
        "batches": batches,
    }), encoding="utf-8")
    return cases


class FakeClient:
    def __init__(self, provider: str, barrier: threading.Barrier | None = None):
        self.provider = provider
        self.model = MODELS[provider]
        self.barrier = barrier
        self.calls = 0

    def call(self, prompt: str) -> dict:
        self.calls += 1
        if self.calls == 1 and self.barrier is not None:
            self.barrier.wait(timeout=2)
        return {
            "raw": {"prompt": prompt},
            "latency_seconds": 0.001,
            "request_id": f"{self.provider}-{self.calls}",
        }

    def usage_and_cost(self, raw: dict) -> tuple[dict, float]:
        return {
            "input_tokens": 100,
            "cached_tokens": 0,
            "reasoning_tokens": 10,
            "output_tokens": 100,
        }, 0.001

    def parsed_content(self, raw: dict) -> dict:
        values = json.loads(raw["prompt"].split("CASES\n", 1)[1])
        cases = [{
            "case_id": value["case_id"],
            "A": value["retrieval_set_A"],
            "B": value["retrieval_set_B"],
        } for value in values]
        return _response(cases)


def test_preflight_preserves_gemini_prompt_hashes_and_stays_below_cost_limits(tmp_path: Path):
    _write_trial(tmp_path)

    preflight = prepare_multi_provider_review(tmp_path)

    assert preflight["case_count"] == 100
    assert preflight["planned_calls_without_retries"] == 60
    assert preflight["exact_prompt_parity_with_gemini"] is True
    assert preflight["combined_conservative_base_cost_usd"] < COMBINED_LIMIT_USD
    assert all(
        row["conservative_base_cost_usd"] + row["single_retry_reserve_usd"]
        < PROVIDER_LIMITS_USD[provider]
        for provider, row in preflight["providers"].items()
    )


@pytest.mark.parametrize("provider", ["grok", "openai", "anthropic"])
def test_provider_payload_has_pinned_model_strict_schema_and_no_tools(provider: str):
    client = ReviewProviderClient(provider, "test-key", transport=lambda *args, **kwargs: None)

    payload = client.payload("same prompt")

    assert payload["model"] == MODELS[provider]
    assert "tools" not in json.dumps(payload).casefold()
    assert "json_schema" in json.dumps(payload)
    if provider == "openai":
        assert payload["store"] is False
    if provider == "anthropic":
        assert payload["output_config"]["effort"] == "low"


def test_three_providers_run_concurrently_and_do_not_modify_human_review(tmp_path: Path):
    _write_trial(tmp_path)
    prepare_multi_provider_review(tmp_path)
    human_path = tmp_path / "manual_review" / "human_reviews.json"
    human_before = human_path.read_bytes()
    barrier = threading.Barrier(3)
    clients = {provider: FakeClient(provider, barrier) for provider in ("grok", "openai", "anthropic")}

    execution = run_multi_provider_reviews(tmp_path, clients, sleep=lambda _: None)

    assert execution["concurrent"] is True
    assert all(row["completed_reviews"] == 100 for row in execution["providers"].values())
    assert all(client.calls == 20 for client in clients.values())
    assert human_path.read_bytes() == human_before

    openai_items = json.loads((tmp_path / "manual_review" / "openai_blind_reviews.json").read_text())["items"]
    gemini_items = {
        case_id: {**row, "reviewer": "gemini", "model": "gemini-3.1-pro-preview", "transport": "developer_api"}
        for case_id, row in openai_items.items()
    }
    (tmp_path / "manual_review" / "gemini_reviews.json").write_text(json.dumps({
        "items": gemini_items,
    }), encoding="utf-8")
    (tmp_path / "manual_review" / "gemini_review_cost_ledger.json").write_text(json.dumps({
        "combined_known_spend_usd": 0.02,
    }), encoding="utf-8")

    comparison = compare_all_ai_reviews(tmp_path)

    assert comparison["case_count"] == 100
    assert comparison["choice_consensus_counts"] == {"unanimous": 100}
    assert comparison["strong_evidence_case_count"] == 100
    assert comparison["strong_evidence_retriever_majority_counts"] == {"hybrid": 50, "lexical": 50}
    assert comparison["recommendation"] == "retain_hybrid_shadow_only"
    assert (tmp_path / "manual_review" / "all_ai_review_comparison.md").exists()


def test_shared_cost_guard_refuses_provider_and_combined_ceiling(tmp_path: Path):
    review_dir = tmp_path / "manual_review"
    review_dir.mkdir(parents=True)
    guard = SharedCostGuard(review_dir, combined_limit=1.0)

    with pytest.raises(RuntimeError, match="grok.*ceiling"):
        guard.guard("grok", provider_spend=0.9, next_maximum=0.2, provider_limit=1.0)

    (review_dir / "openai_review_cost_ledger.json").write_text(json.dumps({
        "known_spend_usd": 0.9,
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="combined"):
        guard.guard("grok", provider_spend=0, next_maximum=0.2, provider_limit=1.0)
