from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from semantic_alignment import hybrid_reply_retrieval as hybrid
from semantic_alignment import image_quote_shortlist_rerank as rerank
from semantic_alignment.bakeoff import anthropic_output_schema


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "semantic_alignment_research/quote_research_full_001"
WORK = ROOT / "image_discovery_research/thatcher_image_hunt_002/integration_preparation"
RETRIEVAL = ROOT / "semantic_alignment_research/hybrid_reply_retrieval_001"


@pytest.fixture(autouse=True)
def isolated_embedding_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the tracked index with deterministic model-free query vectors."""
    matrix = np.load(
        RETRIEVAL / "index/embeddings.npy",
        mmap_mode="r",
        allow_pickle=False,
    )

    class DeterministicEmbedder:
        def __init__(self, _model_dir: Path) -> None:
            pass

        def encode(
            self,
            texts: list[str],
            *,
            query: bool,
            batch_size: int,
        ) -> np.ndarray:
            assert query is True
            assert batch_size == 8
            vectors = [
                np.asarray(
                    matrix[
                        int(rerank.text_hash(text), 16) % matrix.shape[0]
                    ],
                    dtype=np.float32,
                )
                for text in texts
            ]
            return np.stack(vectors)

    monkeypatch.setattr(hybrid, "model_is_complete", lambda _path: True)
    monkeypatch.setattr(rerank, "LocalE5Embedder", DeterministicEmbedder)


def assessment(quote_hash: str, decision: str = "unsuitable") -> dict:
    return {
        "quote_hash": quote_hash,
        "decision": decision,
        "confidence": "high",
        "match_basis": "no_valid_match" if decision == "unsuitable" else "exact_event",
        "reason": "The visible event does not communicate the quotation." if decision == "unsuitable" else "Exact event.",
        "risk_flags": [],
    }


def batch_fixture() -> list[dict]:
    rows = []
    for image_index in range(6):
        quotes = [{"quote_hash": f"{image_index * 25 + index:064x}"} for index in range(25)]
        rows.append({
            "candidate_id": f"image-{image_index}",
            "image_sha256": f"{image_index:064x}",
            "image_metadata": {"source_event": "Archive event", "named_people": ["Margaret Thatcher"]},
            "prompt_quotes": quotes,
        })
    return rows


def response_fixture(batch: list[dict]) -> dict:
    return {"records": [{
        "candidate_id": row["candidate_id"],
        "image_sha256": row["image_sha256"],
        "assessments": [assessment(quote["quote_hash"]) for quote in row["prompt_quotes"]],
        "summary": "All pairs were considered individually.",
    } for row in batch]}


def test_corpus_backed_shortlists_are_complete_deterministic_and_exclude_unresolved():
    first, first_meta = rerank.build_shortlists(RESEARCH, WORK, RETRIEVAL)
    second, second_meta = rerank.build_shortlists(RESEARCH, WORK, RETRIEVAL)
    assert first == second
    assert len(first) == 24
    assert {len(row["prompt_quotes"]) for row in first} == {25}
    unresolved = set(first_meta["corpus"]["unresolved_quote_ids"])
    assert not unresolved.intersection(
        quote["quote_hash"] for row in first for quote in row["prompt_quotes"]
    )
    assert first_meta["all_review_candidates_retained"] is True
    assert first_meta["human_decisions_used"] is False
    assert first_meta["embedding_model"] == second_meta["embedding_model"]


def test_image_level_calibration_and_evaluation_are_disjoint():
    packets, _metadata = rerank.load_completed_corpus(RESEARCH)
    split = rerank.calibration_split(WORK, {row["quote_id"] for row in packets})
    assert len(split["calibration_image_ids"]) == 6
    assert len(split["evaluation_image_ids"]) == 18
    assert set(split["calibration_image_ids"]).isdisjoint(split["evaluation_image_ids"])
    assert all(row["candidate_id"] in split["calibration_image_ids"] for row in split["calibration_pairs"])
    assert all(row["candidate_id"] in split["evaluation_image_ids"] for row in split["evaluation_pairs"])
    assert sum(row["human_positive"] for row in split["evaluation_pairs"]) == 12


def test_prompt_forces_every_shortlist_pair_and_uses_no_tools():
    batch = batch_fixture()
    prompt = rerank.build_prompt(batch, [])
    assert "Classify EVERY image/quote pair" in prompt
    assert "Return exactly 25 assessments" in prompt
    assert "Google Search" not in prompt
    assert "approve_pairing" not in prompt
    assert "prefer_existing_image" not in prompt


def test_strict_response_accepts_complete_batch():
    batch = batch_fixture()
    value = response_fixture(batch)
    assert rerank.validate_response(value, batch)["records"][0]["candidate_id"] == "image-0"


def test_strict_response_rejects_missing_duplicate_and_unknown_quotes():
    batch = batch_fixture()
    value = response_fixture(batch)
    value["records"][0]["assessments"].pop()
    with pytest.raises(ValueError, match="assessment count"):
        rerank.validate_response(value, batch)
    value = response_fixture(batch)
    value["records"][0]["assessments"][1]["quote_hash"] = value["records"][0]["assessments"][0]["quote_hash"]
    with pytest.raises(ValueError, match="unknown or duplicate"):
        rerank.validate_response(value, batch)


def test_unsuitable_may_record_attempted_basis_but_suitable_requires_positive_basis():
    batch = batch_fixture()
    value = response_fixture(batch)
    value["records"][0]["assessments"][0]["match_basis"] = "direct_principle"
    assert rerank.validate_response(value, batch)["records"][0]["assessments"]
    value = response_fixture(batch)
    value["records"][0]["assessments"][0].update({"decision": "suitable", "match_basis": "no_valid_match"})
    with pytest.raises(ValueError, match="non-unsuitable"):
        rerank.validate_response(value, batch)


def test_prepare_has_four_provider_prompt_parity_and_bounded_preflight(tmp_path):
    output = tmp_path / "trial"
    value = rerank.prepare_trial(RESEARCH, WORK, RETRIEVAL, output)
    manifest = value["manifest"]
    hashes = list(manifest["provider_prompt_hashes"].values())
    assert len(manifest["batches"]) == 4
    assert all(item == hashes[0] for item in hashes)
    assert value["preflight"]["planned_calls_without_retries"] == 16
    assert value["preflight"]["within_combined_limit"] is True
    assert value["preflight"]["guarded_base_combined_cost_usd"] <= 12


def test_execution_requires_explicit_flag_and_exact_ceiling(tmp_path):
    with pytest.raises(RuntimeError, match="--execute"):
        rerank.run_trial(ROOT, tmp_path, execute=False, confirmed_cost=12)
    with pytest.raises(RuntimeError, match="exact --confirm-max-cost-usd 12"):
        rerank.run_trial(ROOT, tmp_path, execute=True, confirmed_cost=11.99)


def test_metrics_separate_false_positive_damage():
    rows = [
        {"candidate_id": "a", "quote_hash": "1", "human_positive": True},
        {"candidate_id": "a", "quote_hash": "2", "human_positive": False},
    ]
    value = rerank._metrics(rows, {("a", "1"), ("a", "2")})
    assert value["true_positive"] == 1
    assert value["false_positive"] == 1
    assert value["harmful_eligibility_rate"] == 1.0


def test_paid_invalid_response_is_not_repeated_on_resume(tmp_path, monkeypatch):
    batch = batch_fixture()
    invalid = response_fixture(batch)
    invalid["records"][0]["assessments"].pop()

    class FakeClient:
        calls = 0

        def __init__(self, provider, api_key):
            self.model = f"fake-{provider}"

        def call(self, *args, **kwargs):
            type(self).calls += 1
            return {
                "content": invalid,
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "cost_usd": 0.2,
                "latency_seconds": 1.0,
                "request_id": "request-1",
                "raw": {"id": "request-1", "content": invalid},
            }

    manifest = {"batches": [{"batch_id": "batch-01", "prompt_sha256": "prompt-hash"}]}
    monkeypatch.setattr(rerank, "ProviderClient", FakeClient)
    monkeypatch.setattr(rerank, "_load_batch", lambda *args: ("prompt", batch))
    monkeypatch.setattr(rerank, "_guard_cost", lambda *args: None)

    rerank.run_http_provider(tmp_path, manifest, "grok", "key")
    rerank.run_http_provider(tmp_path, manifest, "grok", "key")

    state = rerank._state(tmp_path, "grok")
    assert FakeClient.calls == 1
    assert state["known_cost_usd"] == pytest.approx(0.2)
    assert state["failed_batches"]["batch-01"]["charged_response"] is True


def test_anthropic_transport_schema_removes_unsupported_array_minimums():
    serialised = anthropic_output_schema(rerank.BATCH_SCHEMA)
    assert "minItems" not in serialised["properties"]["records"]
    assessments = serialised["properties"]["records"]["items"]["properties"]["assessments"]
    assert "minItems" not in assessments
    assert rerank.BATCH_SCHEMA["properties"]["records"]["minItems"] == 6


def test_incomplete_provider_is_not_scored_as_rejecting_every_pair():
    rows = [{"candidate_id": "a", "quote_hash": "1", "human_positive": True}]
    value = rerank._metrics(rows, set(), available=False)
    assert value["available"] is False
    assert value["false_negative"] is None
    assert value["recall"] is None


def test_read_timeout_is_recorded_as_ambiguous_and_not_retried(tmp_path, monkeypatch):
    batch = batch_fixture()

    class FakeClient:
        calls = 0

        def __init__(self, provider, api_key):
            self.model = f"fake-{provider}"

        def call(self, *args, **kwargs):
            type(self).calls += 1
            raise rerank.requests.ReadTimeout("response timed out after transmission")

    manifest = {"batches": [{"batch_id": "batch-01", "prompt_sha256": "prompt-hash"}]}
    monkeypatch.setattr(rerank, "ProviderClient", FakeClient)
    monkeypatch.setattr(rerank, "_load_batch", lambda *args: ("prompt", batch))
    monkeypatch.setattr(rerank, "_guard_cost", lambda *args: None)
    monkeypatch.setattr(rerank, "_next_maximum_cost", lambda *args: 1.25)

    rerank.run_http_provider(tmp_path, manifest, "openai", "key")
    rerank.run_http_provider(tmp_path, manifest, "openai", "key")

    state = rerank._state(tmp_path, "openai")
    assert FakeClient.calls == 1
    assert state["ambiguous_exposure_usd"] == pytest.approx(1.25)
    assert state["failed_batches"]["batch-01"]["ambiguous_outcome"] is True
