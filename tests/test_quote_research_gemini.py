from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

import analyse_quote_research_gemini as cli
from semantic_alignment.quote_research_gemini import (
    COMBINED_LIMIT,
    DEVELOPER_LIMIT,
    MODEL,
    VERTEX_LIMIT,
    DeveloperResearchClient,
    ResearchWorker,
    VertexResearchClient,
    build_pilot,
    preflight,
    research_prompt,
    validate_packet,
)


ROOT = Path(__file__).resolve().parents[1]


def record(index: int = 1) -> dict:
    text = f"A representative quotation number {index} about government, liberty and duty."
    import hashlib
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {"quote_id": digest, "quote_hash": digest, "quote_text": text,
            "source_occurrences": [{"line_number": index}], "research_status": "pending"}


def packet(row: dict) -> dict:
    return {
        "quote_id": row["quote_id"], "quote_text": row["quote_text"],
        "verification_status": "exact", "verified_text": row["quote_text"],
        "text_variation_notes": "No material variant found.", "speaker": "Margaret Thatcher",
        "date": "unknown", "source_event": "unknown", "stable_locator": "unknown",
        "historical_context": "Context remains to be verified.", "immediate_subject": "Public policy.",
        "intended_argument": "An argument about liberty.", "literal_meaning": "Liberty matters.",
        "broader_principle": "Individual responsibility.", "mechanism": "Limited government.",
        "claimed_consequence": "Greater liberty.", "entities": ["Margaret Thatcher"],
        "editorial_guidance": {"desired_first_impression": "Liberty and responsibility.",
            "historical_requirements": [], "must_be_visually_dominant": ["individual agency"],
            "must_not_dominate": ["generic conflict"], "common_visual_mistakes": ["unrelated symbolism"]},
        "research_confidence": "medium", "unresolved_questions": ["Exact event remains unknown."],
        "sources": [{"title": "Official transcript", "url": "https://example.test/source",
                     "source_type": "official", "supports": ["Context."]}],
    }


def successful(row: dict, transport: str) -> dict:
    return {"raw": {"candidates": []}, "content": packet(row),
            "grounding": {"queries": ["query"], "supports": [{"chunk_indices": [0], "text": "Context."}],
                          "sources": [{"index": 0, "title": "Official transcript",
                                       "url": "https://example.test/source", "supports": ["Context."]}],
                          "search_entry_point": None},
            "usage": {"input_tokens": 100, "cached_tokens": 0, "reasoning_tokens": 20, "output_tokens": 200},
            "token_cost_usd": .003, "search_cost_usd_conservative": .014,
            "cost_usd": .017, "latency_seconds": .1, "request_id": f"{transport}-request"}


def http_429() -> requests.HTTPError:
    response = requests.Response()
    response.status_code = 429
    response._content = b'{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","message":"daily quota exhausted"}}'
    return requests.HTTPError("429", response=response)


class FakeDeveloper(DeveloperResearchClient):
    def __init__(self, outcomes):
        super().__init__("not-a-real-key", request=lambda *args, **kwargs: None)
        self.outcomes = list(outcomes)
        self.calls = 0

    def call(self, prompt):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeVertex(VertexResearchClient):
    def __init__(self, outcomes):
        super().__init__("test-project", client=object())
        self.outcomes = list(outcomes)
        self.calls = 0

    def call(self, prompt):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_deterministic_selection_excludes_batch_one():
    manifest = json.loads((ROOT / "thatcher_quote_research_project/quote_manifest.json").read_text())
    excluded = json.loads((ROOT / "thatcher_quote_research_project/pilot_batch_001.json").read_text())
    first = build_pilot(manifest, excluded)
    second = build_pilot(manifest, excluded)
    excluded_ids = {row["quote_id"] for row in excluded["records"]}
    assert first == second
    assert len(first["records"]) == 20
    assert not excluded_ids.intersection(row["quote_id"] for row in first["records"])


def test_grounding_and_transport_parity_are_explicit():
    developer = DeveloperResearchClient("not-a-real-key", request=lambda *args, **kwargs: None)
    vertex = VertexResearchClient("test-project", client=object())
    assert developer.payload("prompt")["tools"] == [{"googleSearch": {}}]
    assert vertex.config().tools[0].google_search is not None
    assert developer.model == vertex.model == MODEL
    assert "Deep Research" not in research_prompt(record())


def test_vertex_none_text_is_returned_as_parse_error_not_transport_failure():
    class Response:
        parsed = None
        text = None
        response_id = "vertex-response"
        def model_dump(self, **_kwargs):
            return {"candidates": [], "usageMetadata": {"promptTokenCount": 10,
                    "candidatesTokenCount": 5, "thoughtsTokenCount": 2}}
    class Models:
        def generate_content(self, **_kwargs): return Response()
    client = type("Client", (), {"models": Models()})()
    result = VertexResearchClient("project", client=client).call("prompt")
    assert result["content"] is None
    assert result["parse_error"] == "ValueError: no JSON object found"
    assert result["usage"]["input_tokens"] == 10


def test_strict_packet_validation_rejects_missing_or_extra_fields():
    row = record()
    validate_packet(packet(row), row)
    bad = packet(row)
    bad["invented"] = True
    with pytest.raises(ValueError, match="fields mismatch"):
        validate_packet(bad, row)


def test_operator_bibliographic_source_may_omit_public_url():
    row = record()
    value = packet(row)
    value["sources"] = [{
        "title": "Margaret Thatcher, Statecraft, first edition (2002), p. 427",
        "url": "",
        "source_type": "operator_supplied_bibliographic_citation",
        "supports": [row["quote_text"]],
    }]
    assert validate_packet(value, row) == value


def test_other_source_types_still_require_public_url():
    row = record()
    value = packet(row)
    value["sources"][0]["url"] = ""
    with pytest.raises(ValueError, match="identity fields"):
        validate_packet(value, row)


def test_first_429_then_success_does_not_pause(tmp_path):
    row = record()
    developer = FakeDeveloper([http_429(), successful(row, "developer")])
    vertex = FakeVertex([])
    status = ResearchWorker(tmp_path, [row], developer, vertex, 3, 3, 5, sleep=lambda _: None).run()
    assert developer.calls == 2 and vertex.calls == 0
    assert status["developer_paused"] is False
    assert status["developer_completed"] == 1


def test_malformed_response_is_persisted_and_retried_once(tmp_path):
    row = record()
    malformed = successful(row, "developer")
    malformed.update({"content": None, "parse_error": "JSONDecodeError: truncated"})
    developer = FakeDeveloper([malformed, successful(row, "developer")])
    vertex = FakeVertex([])
    status = ResearchWorker(tmp_path, [row], developer, vertex, 3, 3, 5, sleep=lambda _: None).run()
    assert developer.calls == 2 and status["completed"] == 1
    assert (tmp_path / "raw_responses" / row["quote_id"] / "developer_api_attempt_1.json").exists()
    assert len(json.loads((tmp_path / "cost_ledger.json").read_text())["calls"]) == 2


def test_keyboard_interrupt_is_not_swallowed_or_retried(tmp_path):
    row = record()
    developer = FakeDeveloper([KeyboardInterrupt()])
    vertex = FakeVertex([])
    with pytest.raises(KeyboardInterrupt):
        ResearchWorker(tmp_path, [row], developer, vertex, 3, 3, 5).run()
    assert developer.calls == 1 and vertex.calls == 0


def test_two_429s_pause_once_and_all_later_quotes_use_vertex(tmp_path):
    rows = [record(1), record(2), record(3)]
    developer = FakeDeveloper([http_429(), http_429()])
    vertex = FakeVertex([successful(row, "vertex") for row in rows])
    status = ResearchWorker(tmp_path, rows, developer, vertex, 3, 3, 5, sleep=lambda _: None).run()
    assert developer.calls == 2
    assert vertex.calls == 3
    assert status["developer_paused"] is True
    assert status["direct_to_vertex_count"] == 2
    assert len(status["trigger_attempts"]) == 2
    assert status["completed"] == 3


def test_resume_skips_completed_and_preserves_quota_pause(tmp_path):
    rows = [record(1), record(2)]
    first_dev = FakeDeveloper([http_429(), http_429()])
    first_vertex = FakeVertex([successful(rows[0], "vertex"), successful(rows[1], "vertex")])
    ResearchWorker(tmp_path, rows, first_dev, first_vertex, 3, 3, 5, sleep=lambda _: None).run()
    second_dev = FakeDeveloper([])
    second_vertex = FakeVertex([])
    status = ResearchWorker(tmp_path, rows, second_dev, second_vertex, 3, 3, 5, sleep=lambda _: None).run()
    assert second_dev.calls == second_vertex.calls == 0
    assert status["completed"] == 2 and status["developer_paused"] is True


def test_unterminated_sending_is_ambiguous_and_not_retried(tmp_path):
    row = record()
    (tmp_path / "attempts.jsonl").write_text(json.dumps({"quote_id": row["quote_id"],
        "transport": "developer_api", "attempt_number": 1, "state": "sending"}) + "\n")
    developer = FakeDeveloper([])
    vertex = FakeVertex([])
    status = ResearchWorker(tmp_path, [row], developer, vertex, 3, 3, 5).run()
    assert developer.calls == vertex.calls == 0
    assert status["remaining"] == 1
    assert "ambiguous_outcome" in (tmp_path / "attempts.jsonl").read_text()


def test_preflight_budgets_retry_and_enforces_cost_ceiling():
    result = preflight([record(index) for index in range(20)])
    assert result["guarded_retry_cost_usd"] == pytest.approx(result["guarded_base_cost_usd"] * 2)
    assert result["guarded_retry_cost_usd"] <= min(DEVELOPER_LIMIT, VERTEX_LIMIT, COMBINED_LIMIT)
    assert result["allowed"] is True


def test_cli_cannot_call_without_explicit_execution(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    rows = [record(index) for index in range(20)]
    (run / "pilot_manifest.json").write_text(json.dumps({"records": rows}))
    args = cli.parser().parse_args(["run", "--run-dir", str(run)])
    with pytest.raises(RuntimeError, match="--execute"):
        cli.run_command(args)
