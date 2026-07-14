from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from semantic_alignment.io import atomic_write_json, sha256_file
from semantic_alignment.quote_research_gemini import extract_grounding, parse_response_packet
from semantic_alignment.quote_research_recovery import (
    apply_recovery, audit_failures, enforce_quote_identity, inspect_raw_response,
)


def record() -> dict:
    text = "Uploaded wording"
    return {"quote_id": hashlib.sha256(text.encode()).hexdigest(), "quote_hash": hashlib.sha256(text.encode()).hexdigest(),
            "quote_text": text, "input_hash": "input", "source_occurrences": [{"line_number": 1}]}


def packet(row: dict) -> dict:
    return {
        "quote_id": row["quote_id"], "quote_text": row["quote_text"], "verification_status": "exact",
        "verified_text": row["quote_text"], "text_variation_notes": "none", "speaker": "Margaret Thatcher",
        "date": "1980", "source_event": "Speech", "stable_locator": "archive",
        "historical_context": "Context", "immediate_subject": "Subject", "intended_argument": "Argument",
        "literal_meaning": "Meaning", "broader_principle": "Principle", "mechanism": "Mechanism",
        "claimed_consequence": "Consequence", "entities": ["Margaret Thatcher"],
        "editorial_guidance": {"desired_first_impression": "Intent", "historical_requirements": ["1980"],
                               "must_be_visually_dominant": ["Subject"], "must_not_dominate": ["Other"],
                               "common_visual_mistakes": ["Generic"]},
        "research_confidence": "high", "unresolved_questions": [],
        "sources": [{"title": "model source", "url": "https://invalid.example", "source_type": "other",
                     "supports": ["model claim"]}],
    }


def raw(row: dict, snake: bool = False, *, grounded: bool = True, content: dict | None = None) -> dict:
    metadata = {}
    if grounded:
        metadata = {
            ("grounding_chunks" if snake else "groundingChunks"): [
                {"web": {"title": "Hansard", "uri": "https://hansard.parliament.uk/a"}}],
            ("grounding_supports" if snake else "groundingSupports"): [{
                ("grounding_chunk_indices" if snake else "groundingChunkIndices"): [0],
                "segment": {("start_index" if snake else "startIndex"): 0,
                            ("end_index" if snake else "endIndex"): 7, "text": "Context"},
            }],
            ("web_search_queries" if snake else "webSearchQueries"): ["query"],
        }
    candidate = {"content": {"parts": [{"text": json.dumps(content or packet(row))}]},
                 ("grounding_metadata" if snake else "groundingMetadata"): metadata}
    response = {"candidates": [candidate]}
    if snake:
        response["parsed"] = content or packet(row)
    return response


def run_fixture(tmp_path: Path, response: dict) -> tuple[Path, dict]:
    row = record(); run = tmp_path / "research"
    manifest = {"manifest_sha256": "manifest", "records": [row]}
    atomic_write_json(run / "corpus_manifest.json", manifest)
    atomic_write_json(run / "research_packets.json", {"schema_version": 1, "items": {}})
    atomic_write_json(run / "permanent_failures.json", {"schema_version": 1, "items": {
        row["quote_id"]: {"failure": {"kind": "exhausted_validation_failure",
                                       "message": "quote identity changed"}, "transport": "vertex_ai"}},
        "reset_audit": []})
    atomic_write_json(run / "grounding_sources.json", {"schema_version": 1, "items": {}})
    atomic_write_json(run / "run_state.json", {"schema_version": 1, "items": {
        row["quote_id"]: {"status": "permanent_failure"}}})
    atomic_write_json(run / "status.json", {"valid_packets": 0, "permanent_failures": 1,
                                              "developer": {"completed": 0}, "vertex": {"completed": 0},
                                              "resume_plan": {}})
    raw_path = run / "raw_responses" / row["quote_id"] / "vertex_ai_attempt_1.json"
    atomic_write_json(raw_path, response)
    attempt = {"quote_id": row["quote_id"], "state": "validation_failure", "transport": "vertex_ai",
               "timestamp": "2026-01-01T00:00:00Z", "failure": {"message": "quote identity changed"}}
    (run / "attempts.jsonl").write_text(json.dumps(attempt) + "\n")
    return run, row


def test_developer_and_vertex_grounding_layouts():
    row = record()
    for response in (raw(row), raw(row, snake=True)):
        grounding = extract_grounding(response)
        assert grounding["sources"][0]["supports"] == ["Context"]
        assert grounding["queries"] == ["query"]


def test_vertex_wrapped_metadata_layout():
    row = record()
    assert extract_grounding({"response": raw(row, snake=True)})["sources"][0]["url"].startswith("https://hansard")


def test_no_false_grounding_from_generated_url(tmp_path):
    row = record(); response = raw(row, grounded=False)
    response["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(packet(row)).replace(
        "https://invalid.example", "https://hansard.parliament.uk/not-metadata")
    assert extract_grounding(response)["sources"] == []
    inspected = inspect_raw_response(_write_raw(tmp_path, response), row)
    assert not inspected["valid_packet"] and "grounded source" in inspected["error"]


def _write_raw(root: Path, response: dict) -> Path:
    path = root / "vertex_ai_attempt_1.json"
    atomic_write_json(path, response)
    return path


def test_quote_identity_is_immutable_but_outer_quotes_are_harmless():
    row = record(); value = packet(row); value["quote_text"] = '"Uploaded wording"'
    fixed, repairs = enforce_quote_identity(value, row)
    assert fixed["quote_text"] == row["quote_text"] and repairs
    changed = packet(row); changed["quote_id"] = "different"
    with pytest.raises(ValueError, match="quote_id changed"):
        enforce_quote_identity(changed, row)
    changed = packet(row); changed["quote_text"] = "Different wording"
    with pytest.raises(ValueError, match="quote identity changed"):
        enforce_quote_identity(changed, row)


def test_harmless_json_repairs_and_truncation_refusal():
    row = record(); text = "```json\n" + json.dumps(packet(row))[:-1] + ",}\n```"
    value, repairs = parse_response_packet({"candidates": [{"content": {"parts": [{"text": text}]}}]})
    assert value["quote_id"] == row["quote_id"] and "stripped_markdown_fence" in repairs
    with pytest.raises(ValueError, match="truncated"):
        parse_response_packet({"candidates": [{"content": {"parts": [{"text": '{"quote_id": "x"'}]}}]})


def test_failed_raw_response_recovers_offline_and_apply_is_idempotent(tmp_path, monkeypatch):
    row = record(); value = packet(row); value["quote_text"] = '"Uploaded wording"'
    run, row = run_fixture(tmp_path, raw(row, snake=True, content=value))
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("network must not be used")))
    attempts_hash = sha256_file(run / "attempts.jsonl")
    raw_hash = sha256_file(run / "raw_responses" / row["quote_id"] / "vertex_ai_attempt_1.json")
    audit = audit_failures(run)
    assert audit["summary"]["recoverable_offline"] == 1
    result = apply_recovery(run, audit)
    assert result["applied"] == 1 and Path(result["backup_dir"]).exists()
    assert sha256_file(run / "attempts.jsonl") == attempts_hash
    assert sha256_file(run / "raw_responses" / row["quote_id"] / "vertex_ai_attempt_1.json") == raw_hash
    second_audit = audit_failures(run)
    second = apply_recovery(run, second_audit)
    assert second["applied"] == 0 and second["idempotent"]
    assert second_audit["summary"]["non_completed_audited"] == 1


def test_missing_grounding_is_not_invented(tmp_path):
    run, row = run_fixture(tmp_path, raw(record(), snake=True, grounded=False))
    audit = audit_failures(run)
    assert audit["summary"]["recoverable_offline"] == 0
    assert audit["summary"]["paid_retry_candidates"] == 1
    assert row["quote_id"] not in audit["recovered"]


def test_truncated_final_model_sources_are_rebuilt_only_from_grounding(tmp_path):
    row = record(); response = raw(row)
    text = response["candidates"][0]["content"]["parts"][0]["text"]
    response["candidates"][0]["content"]["parts"][0]["text"] = text.split('"sources":', 1)[0] + '"sources": [{"title"'
    run, _ = run_fixture(tmp_path, response)
    audit = audit_failures(run)
    recovered = audit["recovered"][row["quote_id"]]["packet"]
    assert recovered["sources"][0]["url"] == "https://hansard.parliament.uk/a"
    assert recovered["offline_recovery"]["repairs"] == [
        "discarded_malformed_final_model_sources_rebuilt_from_grounding_metadata"]


def test_recovery_writes_only_research_fixture(tmp_path):
    row = record(); value = packet(row); value["quote_text"] = '"Uploaded wording"'
    run, _ = run_fixture(tmp_path, raw(row, snake=True, content=value))
    before = set(tmp_path.rglob("*"))
    apply_recovery(run, audit_failures(run))
    added = set(tmp_path.rglob("*")) - before
    assert added and all(run in path.parents for path in added)
