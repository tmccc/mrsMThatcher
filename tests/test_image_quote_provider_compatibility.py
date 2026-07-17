from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
import requests

from semantic_alignment import image_quote_provider_compatibility as compatibility
from semantic_alignment import image_quote_shortlist_rerank as rerank


ROOT = Path(__file__).resolve().parents[1]
SOURCE_TRIAL = ROOT / "semantic_alignment_research/image_quote_shortlist_rerank_001"


def _prepared(tmp_path: Path) -> Path:
    output = tmp_path / "compatibility"
    compatibility.prepare_trial(SOURCE_TRIAL, output)
    return output


def _shortlist(candidate_id: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "image_sha256": "a" * 64,
        "prompt_quotes": [{"quote_hash": f"{index:064x}"} for index in range(25)],
    }


def _response(candidate_id: str, shortlist: dict) -> dict:
    return {"records": [{
        "candidate_id": candidate_id,
        "image_sha256": shortlist["image_sha256"],
        "assessments": [{
            "quote_hash": quote["quote_hash"],
            "decision": "unsuitable",
            "confidence": "high",
            "match_basis": "no_valid_match",
            "reason": "No suitable editorial relationship.",
            "risk_flags": [],
        } for quote in shortlist["prompt_quotes"]],
        "summary": "All pairs assessed.",
    }]}


def test_sample_is_deterministic_stratified_and_independent_of_human_pair_labels():
    shortlists = json.loads((SOURCE_TRIAL / "shortlists.json").read_text())["records"]
    split = json.loads((SOURCE_TRIAL / "calibration_split.json").read_text())
    prior = json.loads((SOURCE_TRIAL / "provider_results.json").read_text())
    first = compatibility.select_pilot_images(shortlists, split, prior)
    mutated = json.loads(json.dumps(split))
    for row in mutated["calibration_pairs"] + mutated["evaluation_pairs"]:
        row["human_positive"] = not row["human_positive"]
        row["human_decision"] = "changed_for_test"
    second = compatibility.select_pilot_images(shortlists, mutated, prior)

    assert first == second
    assert len(first) == 8
    assert sum(row["partition"] == "calibration" for row in first) == 2
    assert sum(row["partition"] == "evaluation" for row in first) == 6
    assert {row["provider_stratum"] for row in first} >= {
        "grok_only", "gemini_only", "no_match", "shared_only", "mixed_disagreement",
    }


def test_prepare_uses_one_image_prompts_with_exact_provider_parity_and_bounded_cost(tmp_path: Path):
    output = _prepared(tmp_path)
    manifest = json.loads((output / "compatibility_manifest.json").read_text())
    preflight = json.loads((output / "preflight.json").read_text())
    schema = json.loads((output / "response_schema.json").read_text())

    assert manifest["image_count"] == 8
    assert manifest["provider_prompt_parity"] is True
    assert manifest["tools_enabled"] is False
    assert schema["properties"]["records"]["minItems"] == 1
    assert schema["properties"]["records"]["maxItems"] == 1
    assert rerank.BATCH_SCHEMA["properties"]["records"]["minItems"] == 6
    assert preflight["planned_calls"] == 16
    assert preflight["conservative_maximum_cost_usd"] <= 6
    assert preflight["prior_trial_ambiguous_exposure_usd"] == pytest.approx(7.785713)
    assert preflight["prior_trial_exposure_is_outside_this_new_ceiling"] is True
    assert (output / "preflight.md").exists()
    for item in manifest["items"]:
        prompt = (output / item["prompt_path"]).read_text()
        assert compatibility.text_hash(prompt) == item["prompt_sha256"]
        assert '"minItems":1' in prompt
        assert "Google Search" not in prompt


def test_execution_requires_explicit_flag_and_exact_ceiling(tmp_path: Path):
    with pytest.raises(RuntimeError, match="--execute"):
        compatibility.run_trial(ROOT, tmp_path, execute=False, confirmed_cost=6)
    with pytest.raises(RuntimeError, match="exact --confirm-max-cost-usd 6"):
        compatibility.run_trial(ROOT, tmp_path, execute=True, confirmed_cost=5.99)


def test_provider_client_uses_configured_read_timeout():
    observed = {}

    class Response:
        headers = {"request-id": "request-1"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "request-1",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "output_text": '{"records":[]}',
            }

    def transport(*_args, **kwargs):
        observed["timeout"] = kwargs["timeout"]
        return Response()

    client = compatibility.ProviderClient("openai", "key", transport=transport, timeout_seconds=360)
    client.call("prompt", schema={"type": "object"}, max_output_tokens=10)
    assert observed["timeout"] == 360


def test_ambiguous_timeout_stops_provider_and_is_not_retried_on_resume(tmp_path: Path, monkeypatch):
    output = _prepared(tmp_path)

    class Client:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def call(self, *_args, **_kwargs):
            type(self).calls += 1
            raise requests.ReadTimeout("ambiguous")

    monkeypatch.setattr(compatibility, "ProviderClient", Client)
    compatibility.run_provider(output, "openai", "key")
    compatibility.run_provider(output, "openai", "key")
    state = compatibility._provider_state(output, "openai")

    assert Client.calls == 1
    assert state["provider_stopped"] is True
    assert state["stop_reason"] == "ambiguous_read_timeout"
    assert state["ambiguous_exposure_usd"] > 0


def test_charged_validation_failure_persists_cost_and_is_not_retried(tmp_path: Path, monkeypatch):
    output = _prepared(tmp_path)

    class Client:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def call(self, *_args, **_kwargs):
            type(self).calls += 1
            return {
                "content": {"records": []},
                "raw": {"id": "request-1", "output_text": '{"records":[]}'},
                "request_id": "request-1",
                "cost_usd": 0.25,
                "latency_seconds": 1.0,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }

    monkeypatch.setattr(compatibility, "ProviderClient", Client)
    compatibility.run_provider(output, "openai", "key")
    compatibility.run_provider(output, "openai", "key")
    state = compatibility._provider_state(output, "openai")

    assert Client.calls == 1
    assert state["known_cost_usd"] == pytest.approx(0.25)
    failed = next(iter(state["failed_items"].values()))
    assert failed["charged_response"] is True
    assert len(state["received_items"]) == 1


def test_provider_response_parse_failure_is_conservatively_ambiguous(tmp_path: Path, monkeypatch):
    output = _prepared(tmp_path)

    class Client:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def call(self, *_args, **_kwargs):
            type(self).calls += 1
            raise ValueError("HTTP 200 response could not be decoded")

    monkeypatch.setattr(compatibility, "ProviderClient", Client)
    compatibility.run_provider(output, "anthropic", "key")
    state = compatibility._provider_state(output, "anthropic")

    assert Client.calls == 1
    assert state["provider_stopped"] is True
    assert state["ambiguous_exposure_usd"] > 0
    failed = next(iter(state["failed_items"].values()))
    assert failed["ambiguous_outcome"] is True


def test_anthropic_long_reason_is_normalised_and_saved_response_recovers_offline(tmp_path: Path):
    output = _prepared(tmp_path)
    manifest = json.loads((output / "compatibility_manifest.json").read_text())
    candidate_id = manifest["items"][0]["candidate_id"]
    source_rows = json.loads((SOURCE_TRIAL / "shortlists.json").read_text())["records"]
    shortlist = next(row for row in source_rows if row["candidate_id"] == candidate_id)
    assessments = [{
        "quote_hash": quote["quote_hash"],
        "decision": "unsuitable",
        "confidence": "high",
        "match_basis": "no_valid_match",
        "reason": "A " + ("long explanation " * 30),
        "risk_flags": [],
    } for quote in shortlist["prompt_quotes"]]
    content = {"records": [{
        "candidate_id": candidate_id,
        "image_sha256": shortlist["image_sha256"],
        "assessments": assessments,
        "summary": "All pairs assessed.",
    }]}
    provider_dir = output / "providers/anthropic"
    raw_path = provider_dir / f"raw/{candidate_id}.json"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(json.dumps({"content": [{"type": "text", "text": json.dumps(content)}]}))
    state = compatibility._provider_state(output, "anthropic")
    state["received_items"][candidate_id] = {
        "raw_path": str(raw_path.relative_to(output)), "raw_persisted": True,
        "request_id": "request-1", "cost_usd": 0.1, "usage": {},
        "latency_seconds": 1.0, "prompt_sha256": manifest["items"][0]["prompt_sha256"],
    }
    state["failed_items"][candidate_id] = {"charged_response": True}
    state["provider_stopped"] = True
    state["stop_reason"] = "charged_validation_failure"
    compatibility._save_provider_state(output, "anthropic", state)

    recovered = compatibility.recover_received_items(
        output, "anthropic", state, {candidate_id: shortlist},
    )

    assert candidate_id in recovered["completed_items"]
    assert candidate_id not in recovered["failed_items"]
    assert recovered["provider_stopped"] is False
    changes = recovered["completed_items"][candidate_id]["normalisations"]
    assert len(changes) == 25
    normalised = json.loads((output / recovered["completed_items"][candidate_id]["path"]).read_text())
    assert max(len(row["reason"]) for row in normalised["records"][0]["assessments"]) <= 280


def test_anthropic_long_summary_is_normalised_during_offline_recovery(tmp_path: Path):
    output = _prepared(tmp_path)
    manifest = json.loads((output / "compatibility_manifest.json").read_text())
    candidate_id = manifest["items"][0]["candidate_id"]
    source_rows = json.loads((SOURCE_TRIAL / "shortlists.json").read_text())["records"]
    shortlist = next(row for row in source_rows if row["candidate_id"] == candidate_id)
    assessments = [{
        "quote_hash": quote["quote_hash"],
        "decision": "unsuitable",
        "confidence": "high",
        "match_basis": "no_valid_match",
        "reason": "No suitable editorial relationship.",
        "risk_flags": [],
    } for quote in shortlist["prompt_quotes"]]
    content = {"records": [{
        "candidate_id": candidate_id,
        "image_sha256": shortlist["image_sha256"],
        "assessments": assessments,
        "summary": "Long summary. " * 40,
    }]}
    provider_dir = output / "providers/anthropic"
    raw_path = provider_dir / f"raw/{candidate_id}.json"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(json.dumps({"content": [{"type": "text", "text": json.dumps(content)}]}))
    state = compatibility._provider_state(output, "anthropic")
    state["received_items"][candidate_id] = {
        "raw_path": str(raw_path.relative_to(output)), "raw_persisted": True,
        "request_id": "request-1", "cost_usd": 0.1, "usage": {},
        "latency_seconds": 1.0, "prompt_sha256": manifest["items"][0]["prompt_sha256"],
    }
    state["failed_items"][candidate_id] = {"charged_response": True}
    state["provider_stopped"] = True
    state["stop_reason"] = "charged_validation_failure"

    recovered = compatibility.recover_received_items(
        output, "anthropic", state, {candidate_id: shortlist},
    )

    assert candidate_id in recovered["completed_items"]
    assert recovered["provider_stopped"] is False
    normalised = json.loads((output / recovered["completed_items"][candidate_id]["path"]).read_text())
    assert len(normalised["records"][0]["summary"]) <= 400
    assert recovered["completed_items"][candidate_id]["normalisations"] == [{
        "normalisation": "anthropic_summary_max_length",
        "candidate_id": candidate_id,
        "original_length": 560,
        "normalised_length": 399,
    }]


def test_anthropic_single_ordered_quote_hash_copy_error_is_normalised():
    shortlist = _shortlist("candidate-one")
    content = _response("candidate-one", shortlist)
    wanted = content["records"][0]["assessments"][13]["quote_hash"]
    copied_badly = wanted[:49] + ("f" * 15)
    assert copied_badly != wanted
    content["records"][0]["assessments"][13]["quote_hash"] = copied_badly

    normalised, changes = compatibility.normalise_provider_content(
        "anthropic", content, [shortlist],
    )

    assert normalised["records"][0]["assessments"][13]["quote_hash"] == wanted
    assert changes == [{
        "normalisation": "anthropic_single_quote_hash_copy_error",
        "candidate_id": "candidate-one",
        "assessment_position": 13,
        "original_quote_hash": copied_badly,
        "normalised_quote_hash": wanted,
        "common_prefix_length": 49,
    }]


def test_anthropic_arbitrary_unknown_quote_hash_is_not_normalised():
    shortlist = _shortlist("candidate-one")
    content = _response("candidate-one", shortlist)
    content["records"][0]["assessments"][13]["quote_hash"] = "f" * 64

    normalised, changes = compatibility.normalise_provider_content(
        "anthropic", content, [shortlist],
    )

    assert normalised["records"][0]["assessments"][13]["quote_hash"] == "f" * 64
    assert changes == []


def test_openai_saved_response_output_array_is_decoded_once():
    payload = {"records": []}
    raw = {"output": [{
        "type": "message",
        "content": [{"type": "output_text", "text": json.dumps(payload)}],
    }]}

    assert compatibility._saved_response_content("openai", raw) == payload


def test_openai_and_anthropic_workers_start_concurrently(tmp_path: Path, monkeypatch):
    output = _prepared(tmp_path)
    loaded_paths = []
    monkeypatch.setattr(compatibility, "load_project_environment", loaded_paths.append)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    barrier = threading.Barrier(2, timeout=2)
    providers = []

    def fake_run_provider(_output, provider, _key):
        providers.append(provider)
        barrier.wait()
        return {"provider": provider}

    monkeypatch.setattr(compatibility, "run_provider", fake_run_provider)
    result = compatibility.run_trial(ROOT, output, execute=True, confirmed_cost=6)
    assert loaded_paths == [ROOT / "mrsMThatcher.env"]
    assert set(result) == {"openai", "anthropic"}
    assert set(providers) == {"openai", "anthropic"}


def test_preparation_does_not_write_production_files(tmp_path: Path):
    watched = [ROOT / "image_analysis.json", ROOT / "bot_state.json"]
    before = {path: compatibility.text_hash(path.read_text()) for path in watched}
    _prepared(tmp_path)
    after = {path: compatibility.text_hash(path.read_text()) for path in watched}
    assert after == before


def test_report_keeps_prior_grok_and_gemini_as_same_sample_baselines(tmp_path: Path):
    output = _prepared(tmp_path)
    result = compatibility.build_report(output)
    assert result["baseline_providers"]["grok"]["completed_images"] == 8
    assert result["baseline_providers"]["gemini"]["completed_images"] == 8
    assert result["providers"]["openai"]["completed_images"] == 0
    assert result["providers"]["anthropic"]["completed_images"] == 0
    assert result["all_four_provider_complete"] is False
    assert result["three_of_four_majority_pair_count"] is None
    assert set(result["consensus_thresholds"]) == {
        "1_of_4", "2_of_4", "3_of_4", "4_of_4",
    }
    assert result["incremental_provider_value"]["held_out_human_positive_pairs"] == 9
