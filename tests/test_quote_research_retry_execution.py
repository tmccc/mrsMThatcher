from __future__ import annotations

import json
from pathlib import Path

import pytest

import semantic_alignment.quote_research_retry_execution as retry_execution
from semantic_alignment.io import read_json
from semantic_alignment.quote_research_retry_execution import (
    RETRY_PACKET_SCHEMA, RetryValidationRunner, bind_immutable_identity, build_remaining_failed_recovery,
    build_retry_batch,
    build_failed_batch_recovery, retry_prompt, validate_retry_manifest,
    write_recovery_stage_meta_report,
)
from tests.test_quote_research_corpus import (
    FakeDeveloper, FakeVertex, manifest, packet, response,
)


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "semantic_alignment_research/quote_research_full_001"
RETRY_MANIFEST = PARENT / "retry_analysis/retry_manifest_20.json"
FULL_RETRY_MANIFEST = PARENT / "retry_analysis/full_retry_manifest.json"
BATCH_30 = PARENT / "retry_batches/retry_batch_030_001.json"
REMAINING_RECOVERY = PARENT / "retry_batches/retry_batch_recovery_remaining_020.json"


def identityless_response(record: dict, transport: str) -> dict:
    value = packet(record)
    value.pop("quote_id")
    value.pop("quote_text")
    result = response(record, transport)
    result["content"] = value
    result["raw"]["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(value)
    return result


def retry_runner(tmp_path: Path, record: dict, developer, vertex) -> RetryValidationRunner:
    data = manifest(1)
    data["records"] = [record]
    return RetryValidationRunner(tmp_path, data, developer, vertex, 5, 5, 5,
                                 developer_concurrency=1, vertex_concurrency=1,
                                 sleep=lambda _: None)


def test_retry_schema_removes_model_control_of_identity():
    assert "quote_id" not in RETRY_PACKET_SCHEMA["properties"]
    assert "quote_text" not in RETRY_PACKET_SCHEMA["properties"]
    assert "quote_id" not in RETRY_PACKET_SCHEMA["required"]
    assert "quote_text" not in RETRY_PACKET_SCHEMA["required"]


def test_immutable_identity_is_bound_locally_and_model_fields_rejected():
    record = manifest(1)["records"][0]
    value = packet(record)
    value.pop("quote_id"); value.pop("quote_text")
    bound = bind_immutable_identity(value, record)
    assert bound["quote_id"] == record["quote_id"] and bound["quote_text"] == record["quote_text"]
    value["quote_id"] = "model-controlled"
    with pytest.raises(ValueError, match="immutable identity"):
        bind_immutable_identity(value, record)


def test_missing_grounding_retries_full_grounded_request_not_schema_repair(tmp_path):
    record = manifest(1)["records"][0]
    missing = identityless_response(record, "developer")
    missing["grounding"] = {"queries": ["q"], "supports": [], "sources": [], "search_entry_point": "html"}
    prompts = []
    developer = FakeDeveloper([missing, lambda prompt: prompts.append(prompt) or identityless_response(record, "developer")])
    status = retry_runner(tmp_path, record, developer, FakeVertex([])).run()
    assert status["valid_packets"] == 1 and developer.calls == 2
    assert "grounded-repair" not in prompts[0]
    rows = [json.loads(line) for line in (tmp_path / "attempts.jsonl").read_text().splitlines()]
    assert not any(row.get("repair_attempt") for row in rows)


def test_schema_repair_requires_fresh_grounded_search(tmp_path):
    record = manifest(1)["records"][0]
    malformed = identityless_response(record, "developer")
    malformed["content"] = None; malformed["parse_error"] = "broken"
    prompts = []
    developer = FakeDeveloper([malformed, lambda prompt: prompts.append(prompt) or identityless_response(record, "developer")])
    assert retry_runner(tmp_path, record, developer, FakeVertex([])).run()["valid_packets"] == 1
    assert "fresh Google Search" in prompts[0] and "provider-linked grounding" in prompts[0]


def test_retry_manifest_is_deterministic_complete_and_below_exact_ceiling():
    first = validate_retry_manifest(PARENT, REMAINING_RECOVERY)
    second = validate_retry_manifest(PARENT, REMAINING_RECOVERY)
    assert first == second
    assert first["candidate_count"] == 20
    assert first["group_counts"] == {
        "transport_failures": 10, "missing_grounding": 7,
        "identity_and_structured_output": 3,
    }
    assert len(first["completed_overlap"]) == 16
    assert first["completed_overlap_provenance"] == "same_retry_run"
    assert first["conservative_cost_usd"] <= 5
    with pytest.raises(RuntimeError, match="exact.*ceiling"):
        validate_retry_manifest(PARENT, REMAINING_RECOVERY, 5.01)


def test_retry_prompt_has_immutable_envelope_and_requires_grounding():
    record = manifest(1)["records"][0]
    prompt = retry_prompt(record)
    assert record["quote_id"] in prompt and record["quote_text"] in prompt
    assert "do not emit these fields" in prompt
    assert "provider-linked grounding chunks and grounding supports" in prompt


def test_deterministic_30_item_batch_preserves_immutable_manifest():
    first = build_retry_batch(PARENT, FULL_RETRY_MANIFEST, 30, BATCH_30)
    second = build_retry_batch(PARENT, FULL_RETRY_MANIFEST, 30, BATCH_30)
    assert first == second
    assert first["class_counts"] == {
        "transport_failures": 10, "missing_grounding": 18,
        "identity_and_structured_output": 2,
    }
    ids = {row["quote_id"] for row in first["records"]}
    prior = {row["quote_id"] for row in read_json(RETRY_MANIFEST)["records"]}
    assert len(ids) == 30 and not ids & prior


def test_remaining_recovery_excludes_repeatedly_exhausted_cases():
    output = PARENT / "retry_batches/retry_batch_recovery_remaining_020.json"
    result = build_remaining_failed_recovery(
        PARENT, FULL_RETRY_MANIFEST, output)
    ids = {row["quote_id"] for row in result["records"]}
    assert ids
    assert not ids & set(result["excluded_repeatedly_exhausted_ids"])
    assert len(ids) == 20
    assert result["hard_combined_ceiling_usd"] == 5.0


def test_recovery_meta_report_accounts_for_all_candidates(tmp_path, monkeypatch):
    output_hashes = {
        path: path.read_bytes()
        for path in (
            PARENT / "retry_batches/recovery_stage_meta_report.json",
            PARENT / "retry_batches/recovery_stage_meta_report.md",
        )
    }
    real_write_json = retry_execution.atomic_write_json
    real_write_text = retry_execution.atomic_write_text
    monkeypatch.setattr(
        retry_execution,
        "atomic_write_json",
        lambda path, value: real_write_json(tmp_path / Path(path).name, value),
    )
    monkeypatch.setattr(
        retry_execution,
        "atomic_write_text",
        lambda path, value: real_write_text(tmp_path / Path(path).name, value),
    )
    result = write_recovery_stage_meta_report(PARENT)
    assert result["unique_candidates_targeted"] == 170
    assert result["unique_candidates_recovered"] + result["unique_candidates_unresolved"] == 170
    assert result["final_packet_count"] + result["final_unresolved_count"] == 632
    assert all(path.read_bytes() == contents for path, contents in output_hashes.items())


def test_30_item_batch_requires_exact_seven_fifty_ceiling():
    manifest_value = read_json(BATCH_30)
    assert manifest_value["record_count"] == 30
    assert manifest_value["expected_cost_usd"] == 5.4407
    assert manifest_value["hard_combined_ceiling_usd"] == 7.5


def test_failed_batch_recovery_contains_only_19_unresolved(tmp_path):
    retry_dir = tmp_path / "parent" / "retry_batches"
    retry_dir.mkdir(parents=True)
    source_manifest = retry_dir / "retry_batch_030_002.json"
    source_manifest.write_text(
        (PARENT / "retry_batches/retry_batch_030_002.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    reviewed_recovery = read_json(
        PARENT / "retry_batches/retry_batch_030_002_recovery_19.json"
    )
    failed_ids = {
        row["quote_id"] for row in reviewed_recovery["records"]
    }
    source_run = retry_dir / "retry_batch_030_002_run"
    source_run.mkdir()
    (source_run / "permanent_failures.json").write_text(
        json.dumps({"items": {quote_id: {} for quote_id in failed_ids}}),
        encoding="utf-8",
    )
    (source_run / "research_packets.json").write_text(
        json.dumps({"items": {}}),
        encoding="utf-8",
    )
    output = tmp_path / "recovery_19.json"
    result = build_failed_batch_recovery(
        tmp_path / "parent",
        source_manifest,
        source_run,
        output,
    )
    failed = set(read_json(source_run / "permanent_failures.json")["items"])
    completed = set(read_json(source_run / "research_packets.json")["items"])
    ids = {row["quote_id"] for row in result["records"]}
    assert len(ids) == 19 and ids == failed and not ids & completed
    assert result["class_counts"] == {
        "missing_grounding": 11, "transport_failures": 6,
        "identity_and_structured_output": 2,
    }
    recovery_manifest = PARENT / "retry_batches/retry_batch_030_002_recovery_19.json"
    assert validate_retry_manifest(PARENT, recovery_manifest, 5)["expected_cost_usd"] == 3.4458
