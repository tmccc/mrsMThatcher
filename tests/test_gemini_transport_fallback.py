from __future__ import annotations

import json
import os
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from google.genai import errors

from semantic_alignment.bakeoff import BAKEOFF_OUTPUT_SCHEMA, MAX_OUTPUT_TOKENS
from semantic_alignment.gemini_fallback import (
    GeminiFallbackWorker,
    clear_expired_quota_pause,
    classify_developer_failure,
    fallback_status,
    format_fallback_status,
    quota_reset_metadata,
    require_transport_parity,
    verify_adc_access,
)


def result(decision="keep"):
    scores = {name: 70 for name in (
        "relevance_score", "directness_score", "mechanism_alignment_score",
        "consequence_alignment_score", "principle_alignment_score", "specificity_score",
        "editorial_power_score", "overall_suitability_score",
    )}
    return {
        "primary_relationship": "direct_illustration", "secondary_relationship": None,
        **scores, "keep_or_replace": decision, "quote_mechanism": ["trade"],
        "quote_claimed_consequences": ["prosperity"], "quote_broader_principles": ["freedom"],
        "image_depicted_subject": ["port"], "image_implied_mechanism": ["trade"],
        "image_depicted_consequences": ["prosperity"], "image_ideological_framing": [],
        "matched_elements": ["trade"], "unillustrated_primary_elements": [],
        "extraneous_image_arguments": [], "explanation": "Direct illustration.",
        "stronger_visual_direction": [],
    }


def case(number=1):
    return {"case_id": f"c{number}", "quote_hash": "q", "image_basename": "i",
            "normalised_input_hash": f"h{number}", "estimated_input_tokens": 100}


def response():
    return {"content": result(), "usage": {"input_tokens": 10, "cached_tokens": 0,
            "reasoning_tokens": 2, "output_tokens": 12}, "cost_usd": .001,
            "latency_seconds": .1, "request_id": "request-1", "model_version": "gemini-3.1-pro-preview",
            "traffic_type": "ON_DEMAND"}


def http_error(status, message="quota exceeded per day", provider_status="RESOURCE_EXHAUSTED", headers=None):
    raw = requests.Response(); raw.status_code = status
    raw.headers.update(headers or {})
    raw._content = json.dumps({"error": {"code": status, "status": provider_status, "message": message}}).encode()
    exc = requests.HTTPError(f"HTTP {status}"); exc.response = raw
    return exc


class Developer:
    model = "gemini-3.1-pro-preview"
    def __init__(self, outcomes): self.outcomes = list(outcomes); self.calls = 0
    def payload(self, prompt):
        return {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {
            "responseMimeType": "application/json", "responseJsonSchema": BAKEOFF_OUTPUT_SCHEMA,
            "maxOutputTokens": MAX_OUTPUT_TOKENS, "thinkingConfig": {"thinkingBudget": 512}}}
    def call(self, prompt):
        self.calls += 1; value = self.outcomes.pop(0)
        if isinstance(value, BaseException): raise value
        return value


class Vertex:
    model = "gemini-3.1-pro-preview"
    def __init__(self, outcomes): self.outcomes = list(outcomes); self.calls = 0
    def config(self):
        return SimpleNamespace(max_output_tokens=MAX_OUTPUT_TOKENS, response_mime_type="application/json",
            response_json_schema=BAKEOFF_OUTPUT_SCHEMA, thinking_config=SimpleNamespace(thinking_budget=512),
            temperature=None, safety_settings=None, tools=None)
    def call(self, prompt):
        self.calls += 1; value = self.outcomes.pop(0)
        if isinstance(value, BaseException): raise value
        return value


def worker(tmp_path, developer, vertex=None, *, enabled=True, available=True, cases=None, threshold=3):
    return GeminiFallbackWorker(cases=cases or [case()], quotes={"q": {"quote_text": "Trade."}},
        images={"i": {"dominant_message": "A port."}}, run_dir=tmp_path,
        developer_client=developer, vertex_client=vertex, developer_limit=10, vertex_limit=3,
        combined_limit=45, fallback_enabled=enabled, fallback_available=available,
        quota_pause_threshold=threshold, sleep=lambda _: None)


def test_fallback_disabled_by_default(tmp_path):
    dev = Developer([http_error(429), http_error(429)]); vertex = Vertex([response()])
    summary = worker(tmp_path, dev, vertex, enabled=False).run()
    assert summary["completed"] == 0 and vertex.calls == 0


def test_developer_retry_success_does_not_use_vertex(tmp_path):
    dev = Developer([http_error(429, "temporary rate limit", "RESOURCE_EXHAUSTED"), response()])
    vertex = Vertex([response()]); summary = worker(tmp_path, dev, vertex).run()
    assert summary["completed_via_developer_api"] == 1 and vertex.calls == 0


def test_two_eligible_429s_trigger_vertex_and_preserve_provenance(tmp_path):
    dev = Developer([http_error(429), http_error(429)]); vertex = Vertex([response()])
    summary = worker(tmp_path, dev, vertex).run()
    assert summary["completed_via_vertex_fallback"] == 1 and dev.calls == 2 and vertex.calls == 1
    item = json.loads((tmp_path / "gemini_results.json").read_text())["items"]["c1"]
    assert item["transport"] == "vertex_ai" and item["final_status"] == "completed_via_vertex_fallback"
    assert len(json.loads((tmp_path / "gemini_ledger.json").read_text())["attempts"]) == 2


@pytest.mark.parametrize("failure", [http_error(400, "invalid request", "INVALID_ARGUMENT"),
    http_error(403, "safety refusal", "PERMISSION_DENIED")])
def test_ineligible_http_failures_do_not_fallback(tmp_path, failure):
    vertex = Vertex([response()]); worker(tmp_path, Developer([failure]), vertex).run()
    assert vertex.calls == 0


def test_schema_failure_does_not_fallback(tmp_path):
    bad = response(); bad["content"] = {"primary_relationship": "direct_illustration"}
    vertex = Vertex([response()]); worker(tmp_path, Developer([bad, bad]), vertex).run()
    assert vertex.calls == 0


def test_ambiguous_developer_outcome_never_falls_back(tmp_path):
    vertex = Vertex([response()]); worker(tmp_path, Developer([TimeoutError("unknown transmission")]), vertex).run()
    assert vertex.calls == 0
    assert json.loads((tmp_path / "gemini_ledger.json").read_text())["ambiguous_outcomes"]


def test_model_and_settings_parity_are_required():
    require_transport_parity(Developer([]), Vertex([]))
    wrong = Vertex([]); wrong.model = "gemini-flash"
    with pytest.raises(RuntimeError, match="parity"): require_transport_parity(Developer([]), wrong)
    wrong = Vertex([])
    wrong.config = lambda: SimpleNamespace(max_output_tokens=MAX_OUTPUT_TOKENS,
        response_mime_type="application/json", response_json_schema=BAKEOFF_OUTPUT_SCHEMA,
        thinking_config=SimpleNamespace(thinking_budget=0), temperature=None, safety_settings=None, tools=None)
    with pytest.raises(RuntimeError, match="thinking_budget"): require_transport_parity(Developer([]), wrong)


def test_vertex_preflight_failure_is_explicit(monkeypatch, tmp_path):
    import semantic_alignment.vertex_recovery as recovery
    adc = tmp_path / "adc.json"; adc.write_text("{}")
    monkeypatch.setattr(recovery, "adc_path", lambda: adc)
    run = lambda *a, **k: SimpleNamespace(returncode=1)
    with pytest.raises(RuntimeError, match="mint"): verify_adc_access({"GOOGLE_CLOUD_PROJECT": "p", "GOOGLE_GENAI_USE_VERTEXAI": "true"}, run=run)


def test_vertex_retry_once_and_third_attempt_impossible(tmp_path):
    dev = Developer([http_error(429), http_error(429)])
    vertex = Vertex([errors.ServerError(503, {"error": {"message": "capacity"}}),
                     errors.ServerError(503, {"error": {"message": "capacity"}})])
    summary = worker(tmp_path, dev, vertex).run(); worker(tmp_path, dev, vertex).run()
    assert summary["still_missing"] == 1 and vertex.calls == 2


def test_provider_wide_daily_quota_pause_routes_later_cases_directly(tmp_path):
    cases = [case(n) for n in range(1, 5)]
    dev = Developer([http_error(429), http_error(429), http_error(429), http_error(429)])
    vertex = Vertex([response(), response(), response(), response()])
    summary = worker(tmp_path, dev, vertex, cases=cases, threshold=2).run()
    assert summary["provider_wide_quota_pause_activated"] is True
    assert summary["routed_directly_after_pause"] == 2 and dev.calls == 4 and vertex.calls == 4


def test_generic_rate_limits_do_not_activate_global_quota_pause(tmp_path):
    cases = [case(n) for n in range(1, 3)]
    generic = lambda: http_error(429, "requests too fast", "RESOURCE_EXHAUSTED")
    dev = Developer([generic(), generic(), generic(), generic()]); vertex = Vertex([response(), response()])
    summary = worker(tmp_path, dev, vertex, cases=cases, threshold=1).run()
    assert summary["provider_wide_quota_pause_activated"] is False and dev.calls == 4


def test_quota_pause_survives_resume_and_probe_override(tmp_path):
    cases = [case(1), case(2)]
    dev = Developer([http_error(429), http_error(429), response()]); vertex = Vertex([response()])
    worker(tmp_path, dev, vertex, cases=[cases[0]], threshold=1).run()
    resumed = worker(tmp_path, dev, vertex, cases=cases, threshold=1)
    assert resumed.run()["routed_directly_after_pause"] == 1 and dev.calls == 2
    probe_dir = tmp_path / "probe"; probe_dev = Developer([http_error(429), http_error(429), response()])
    first = worker(probe_dir, probe_dev, Vertex([response()]), cases=[cases[0]], threshold=1); first.run()
    probing = worker(probe_dir, probe_dev, Vertex([]), cases=cases, threshold=1); probing.probe_after_pause = True
    assert probing.run()["completed_via_developer_api"] == 1 and probe_dev.calls == 3


def test_resume_skips_completed_and_does_not_duplicate_vote(tmp_path):
    dev = Developer([response()]); vertex = Vertex([]); w = worker(tmp_path, dev, vertex)
    w.run(); w.run()
    assert dev.calls == 1 and len(json.loads((tmp_path / "gemini_results.json").read_text())["items"]) == 1


def test_fallback_unavailable_records_failure_without_affecting_developer(tmp_path):
    dev = Developer([http_error(429), http_error(429)])
    summary = worker(tmp_path, dev, None, available=False).run()
    assert summary["still_missing"] == 1
    failure = json.loads((tmp_path / "gemini_results.json").read_text())["failures"]["c1"]
    assert failure == {"reason": "developer_quota_exhausted", "fallback_available": False}


def test_ambiguous_vertex_outcome_is_not_retried(tmp_path):
    dev = Developer([http_error(429), http_error(429)]); vertex = Vertex([TimeoutError("unknown")])
    worker(tmp_path, dev, vertex).run(); worker(tmp_path, dev, vertex).run()
    assert vertex.calls == 1
    assert json.loads((tmp_path / "gemini_vertex_fallback_ledger.json").read_text())["ambiguous_outcomes"]


def test_summary_contains_auditable_case_table(tmp_path):
    worker(tmp_path, Developer([http_error(429), http_error(429)]), Vertex([response()])).run()
    summary = json.loads((tmp_path / "gemini_transport_summary.json").read_text())
    assert summary["cases"][0]["final_status"] == "completed_via_vertex_fallback"
    assert (tmp_path / "gemini_worker_summary.json").exists()


def test_separate_transport_and_combined_cost_ceilings(tmp_path):
    dev = Developer([http_error(429), http_error(429)]); vertex = Vertex([response()])
    w = worker(tmp_path, dev, vertex); w.vertex_limit = 0
    with pytest.raises(RuntimeError, match="vertex_ai"): w.run()


def test_classification_requires_confirmed_429():
    assert classify_developer_failure(http_error(429))["daily_quota"] is True
    assert classify_developer_failure(http_error(503))["fallback_eligible"] is False
    assert classify_developer_failure(TimeoutError())["classification"] == "ambiguous"


def test_confirmed_reset_header_and_whitelisted_preservation():
    now = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
    meta = quota_reset_metadata({"Retry-After": "3600"}, now=now)
    assert meta["expected_reset_at"] == "2026-07-12T13:00:00Z"
    assert meta["reset_time_confidence"] == "confirmed_provider_metadata"
    classified = classify_developer_failure(http_error(429, headers={"Retry-After": "3600", "Authorization": "secret"}))
    assert classified["reset_headers"] == {"Retry-After": "3600"}


def test_estimated_and_unknown_reset_are_explicit_and_timezone_aware():
    now = datetime(2026, 7, 12, 18, tzinfo=timezone.utc)
    estimated = quota_reset_metadata({}, now=now, timezone_name="Europe/London", estimated_daily_reset_hour=8)
    assert estimated["expected_reset_at"].endswith("Z") and estimated["reset_time_confidence"] == "estimated"
    assert estimated["expected_reset_at"] == "2026-07-13T07:00:00Z"
    unknown = quota_reset_metadata({}, now=now)
    assert unknown["expected_reset_at"] is None and unknown["reset_time_confidence"] == "unknown"


def test_status_display_distinguishes_transport_and_logical_totals(tmp_path):
    worker(tmp_path, Developer([response()]), Vertex([])).run()
    status = fallback_status(tmp_path)
    assert status["developer_api"]["completed"] == 1 and status["vertex_ai"]["completed"] == 0
    assert status["logical_gemini"] == {"completed": 1, "total": 1, "still_missing": 0}
    text = format_fallback_status(status)
    assert "active transport: developer_api" in text and "Logical Gemini: completed=1/1" in text


def test_status_display_shows_pause_vertex_and_resume_plan(tmp_path):
    w = worker(tmp_path, Developer([http_error(429), http_error(429)]), Vertex([response()]), threshold=1)
    w.estimated_daily_reset_hour = 8; w.run()
    status = fallback_status(tmp_path, now=datetime(2026, 7, 14, tzinfo=timezone.utc))
    assert status["quota_pause"]["paused"] is True and status["vertex_ai"]["completed"] == 1
    assert status["fallback"]["direct_to_vertex_after_pause"] is True
    assert "expired estimate does not re-enable" in " ".join(status["resume_plan"])


def test_machine_readable_status_needs_no_credentials(tmp_path, capsys):
    from analyse_semantic_alignment_xai import main
    research = tmp_path / "research"; run = research / "fallback-run"; run.mkdir(parents=True)
    worker(run, Developer([response()]), Vertex([])).run()
    assert main(["--project-dir", str(tmp_path), "--research-dir", str(research),
                 "gemini-fallback-status", "--run-id", "fallback-run", "--json"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["logical_gemini"]["completed"] == 1


def test_expired_estimate_does_not_clear_or_probe(tmp_path):
    w = worker(tmp_path, Developer([http_error(429), http_error(429)]), Vertex([response()]), threshold=1)
    w.estimated_daily_reset_hour = 0; w.run()
    before = json.loads((tmp_path / "gemini_transport_state.json").read_text())
    status = fallback_status(tmp_path, now=datetime.now(timezone.utc) + timedelta(days=2))
    after = json.loads((tmp_path / "gemini_transport_state.json").read_text())
    assert status["quota_pause"]["estimate_expired"] is True
    assert before["developer_quota_exhausted"] is after["developer_quota_exhausted"] is True


def test_clear_expired_pause_audits_without_deleting_history(tmp_path):
    w = worker(tmp_path, Developer([http_error(429), http_error(429)]), Vertex([response()]), threshold=1)
    w.estimated_daily_reset_hour = 0; w.run()
    ledger = tmp_path / "gemini_ledger.json"; digest = hashlib.sha256(ledger.read_bytes()).hexdigest()
    audit = clear_expired_quota_pause(tmp_path, now=datetime.now(timezone.utc) + timedelta(days=2))
    state = json.loads((tmp_path / "gemini_transport_state.json").read_text())
    assert audit["action"] == "clear_expired_quota_pause" and state["developer_quota_exhausted"] is False
    assert state["pause_audit"] and hashlib.sha256(ledger.read_bytes()).hexdigest() == digest


def test_clear_pause_refuses_active_run(tmp_path):
    state = {"developer_quota_exhausted": True, "expected_reset_at": "2020-01-01T00:00:00Z",
             "run_active": True, "active_pid": os.getpid()}
    (tmp_path / "gemini_transport_state.json").write_text(json.dumps(state))
    with pytest.raises(RuntimeError, match="active"): clear_expired_quota_pause(tmp_path)


def test_cli_fallback_is_explicitly_disabled_by_default():
    from analyse_semantic_alignment_large import args_parser
    parsed = args_parser().parse_args(["execute", "--providers", "gemini"])
    assert parsed.enable_gemini_vertex_fallback is False
