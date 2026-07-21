"""Offline safety tests for the separately executed AI-first provider pilot."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from tools import pilot_ai_first_reply_strategy as pilot


class FakeResponse:
    """Minimal requests-compatible response."""

    def __init__(
        self,
        document: dict,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.document = document
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> dict:
        return self.document

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def model_metadata() -> dict:
    return {
        "model": "grok-4.3",
        "retrieved_at": "2026-07-20T00:00:00Z",
        "usd_ticks_per_dollar": pilot.USD_TICKS_PER_DOLLAR,
        "prompt_text_token_price": 12_500,
        "cached_prompt_text_token_price": 2_000,
        "completion_text_token_price": 25_000,
    }


def test_pilot_endpoint_is_restricted_to_xai() -> None:
    assert pilot.validate_xai_base("https://api.x.ai/v1/") == "https://api.x.ai/v1"
    for value in (
        "https://api.x.com/2",
        "http://api.x.ai/v1",
        "https://example.com/v1",
        "https://api.x.ai/v1/chat/completions",
    ):
        with pytest.raises(pilot.PilotError):
            pilot.validate_xai_base(value)


def test_transport_records_cost_and_never_repeats_completed_call(tmp_path: Path) -> None:
    calls: list[tuple[str, dict]] = []

    def post(url: str, **kwargs: object) -> FakeResponse:
        calls.append((url, kwargs))
        return FakeResponse({
            "id": "response-1",
            "model": "grok-4.3",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "cost_in_usd_ticks": 1_000_000,
            },
            "choices": [{"message": {"content": json.dumps({"ok": True})}}],
        })

    ledger = pilot.PilotLedger(tmp_path / "cost_ledger.json", model="grok-4.3", hard_limit_usd=1.0)
    transport = pilot.PilotTransport(
        api_key="not-a-real-key",
        base_url=pilot.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "raw_responses",
        post=post,
    )
    transport.set_case("case-a")
    arguments = {
        "stage": "proposer",
        "model": "grok-4.3",
        "system_prompt": "system",
        "user_prompt": "user",
        "response_schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        "timeout_seconds": 10,
        "max_output_tokens": 20,
        "media_context": None,
    }
    assert json.loads(transport(**arguments)) == {"ok": True}
    transport.set_case("case-a")
    assert json.loads(transport(**arguments)) == {"ok": True}
    assert len(calls) == 1
    assert calls[0][0] == "https://api.x.ai/v1/chat/completions"
    assert set(calls[0][1]["json"]) == {
        "model", "messages", "temperature", "max_tokens", "response_format",
    }
    saved = json.loads((tmp_path / "cost_ledger.json").read_text(encoding="utf-8"))
    assert saved["known_cost_in_usd_ticks"] == 1_000_000
    assert saved["ambiguous_exposure_in_usd_ticks"] == 0
    assert saved["operations"][0]["status"] == "completed"


def test_response_cache_recovers_crash_before_ledger_completion(tmp_path: Path) -> None:
    calls: list[str] = []

    def post(url: str, **_kwargs: object) -> FakeResponse:
        calls.append(url)
        return FakeResponse({
            "id": "response-1",
            "model": "grok-4.3",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "cost_in_usd_ticks": 1_000_000,
            },
            "choices": [{"message": {"content": json.dumps({"ok": True})}}],
        })

    ledger_path = tmp_path / "cost_ledger.json"
    arguments = {
        "stage": "proposer",
        "model": "grok-4.3",
        "system_prompt": "system",
        "user_prompt": "user",
        "response_schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        "timeout_seconds": 10,
        "max_output_tokens": 20,
        "media_context": None,
    }
    first_ledger = pilot.PilotLedger(ledger_path, model="grok-4.3", hard_limit_usd=1.0)
    first = pilot.PilotTransport(
        api_key="not-a-real-key",
        base_url=pilot.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=first_ledger,
        response_dir=tmp_path / "raw_responses",
        post=post,
    )
    first.set_case("case-a")
    assert json.loads(first(**arguments)) == {"ok": True}

    saved = json.loads(ledger_path.read_text(encoding="utf-8"))
    operation = saved["operations"][0]
    operation.update({
        "status": "sending",
        "response_hash": None,
        "request_id": None,
        "input_tokens": None,
        "completion_tokens": None,
        "reasoning_tokens": None,
        "cost_in_usd_ticks": None,
        "cost_usd": None,
        "latency_seconds": None,
    })
    pilot.atomic_json(ledger_path, saved)

    recovered_ledger = pilot.PilotLedger(ledger_path, model="grok-4.3", hard_limit_usd=1.0)
    recovered = pilot.PilotTransport(
        api_key="not-a-real-key",
        base_url=pilot.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=recovered_ledger,
        response_dir=tmp_path / "raw_responses",
        post=lambda *_args, **_kwargs: pytest.fail("recovery must not repeat the provider call"),
    )
    recovered.set_case("case-a")

    assert json.loads(recovered(**arguments)) == {"ok": True}
    assert len(calls) == 1
    final = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert final["operations"][0]["status"] == "completed"
    assert final["known_cost_in_usd_ticks"] == 1_000_000


def test_ambiguous_transport_failure_blocks_resume_and_reserves_exposure(tmp_path: Path) -> None:
    def post(_url: str, **_kwargs: object) -> FakeResponse:
        raise requests.ConnectionError("ambiguous transmission")

    ledger = pilot.PilotLedger(tmp_path / "cost_ledger.json", model="grok-4.3", hard_limit_usd=1.0)
    transport = pilot.PilotTransport(
        api_key="not-a-real-key",
        base_url=pilot.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "raw_responses",
        post=post,
    )
    transport.set_case("case-a")
    with pytest.raises(requests.ConnectionError):
        transport(
            stage="reviewer",
            model="grok-4.3",
            system_prompt="system",
            user_prompt="user",
            response_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            timeout_seconds=10,
            max_output_tokens=20,
            media_context=None,
        )
    saved = json.loads((tmp_path / "cost_ledger.json").read_text(encoding="utf-8"))
    assert saved["blocked"] is True
    assert saved["ambiguous_exposure_in_usd_ticks"] > 0
    with pytest.raises(pilot.PilotError, match="blocked"):
        pilot.PilotLedger(tmp_path / "cost_ledger.json", model="grok-4.3", hard_limit_usd=1.0)


def test_definite_429_is_retried_without_ambiguous_exposure(tmp_path: Path) -> None:
    responses = iter([
        FakeResponse(
            {"error": {"message": "rate limited"}},
            status_code=429,
            headers={"Retry-After": "2"},
        ),
        FakeResponse({
            "id": "response-after-429",
            "model": "grok-4.3",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "cost_in_usd_ticks": 1_000_000,
            },
            "choices": [{"message": {"content": json.dumps({"ok": True})}}],
        }),
    ])
    sleeps: list[float] = []
    ledger = pilot.PilotLedger(
        tmp_path / "cost_ledger.json",
        model="grok-4.3",
        hard_limit_usd=1.0,
    )
    transport = pilot.PilotTransport(
        api_key="not-a-real-key",
        base_url=pilot.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "raw_responses",
        post=lambda *_args, **_kwargs: next(responses),
        sleep=sleeps.append,
        maximum_rate_limit_retries=1,
    )
    transport.set_case("case-a")

    result = transport(
        stage="proposer",
        model="grok-4.3",
        system_prompt="system",
        user_prompt="user",
        response_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        timeout_seconds=10,
        max_output_tokens=20,
        media_context=None,
    )

    assert json.loads(result) == {"ok": True}
    assert sleeps == [2.0]
    saved = json.loads((tmp_path / "cost_ledger.json").read_text(encoding="utf-8"))
    operation = saved["operations"][0]
    assert operation["status"] == "completed"
    assert operation["attempt_number"] == 2
    assert len(operation["rate_limit_events"]) == 1
    assert saved["ambiguous_exposure_usd"] == 0


def test_exhausted_429_stays_resumable_and_does_not_become_ambiguous(tmp_path: Path) -> None:
    ledger = pilot.PilotLedger(
        tmp_path / "cost_ledger.json",
        model="grok-4.3",
        hard_limit_usd=1.0,
    )
    transport = pilot.PilotTransport(
        api_key="not-a-real-key",
        base_url=pilot.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "raw_responses",
        post=lambda *_args, **_kwargs: FakeResponse(
            {"error": {"message": "rate limited"}},
            status_code=429,
        ),
        sleep=lambda _seconds: None,
        maximum_rate_limit_retries=0,
    )
    transport.set_case("case-a")

    with pytest.raises(pilot.RateLimitReached):
        transport(
            stage="reviewer",
            model="grok-4.3",
            system_prompt="system",
            user_prompt="user",
            response_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            timeout_seconds=10,
            max_output_tokens=20,
            media_context=None,
        )
    saved = json.loads((tmp_path / "cost_ledger.json").read_text(encoding="utf-8"))
    assert saved["blocked"] is False
    assert saved["operations"][0]["status"] == "rate_limited"
    assert saved["ambiguous_exposure_usd"] == 0


def test_definite_503_is_retried_without_ambiguous_exposure(tmp_path: Path) -> None:
    responses = iter([
        FakeResponse({"error": {"message": "temporarily unavailable"}}, status_code=503),
        FakeResponse({
            "id": "response-after-503",
            "model": "grok-4.3",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "cost_in_usd_ticks": 1_000_000,
            },
            "choices": [{"message": {"content": json.dumps({"ok": True})}}],
        }),
    ])
    sleeps: list[float] = []
    ledger = pilot.PilotLedger(
        tmp_path / "cost_ledger.json",
        model="grok-4.3",
        hard_limit_usd=1.0,
    )
    transport = pilot.PilotTransport(
        api_key="not-a-real-key",
        base_url=pilot.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "raw_responses",
        post=lambda *_args, **_kwargs: next(responses),
        sleep=sleeps.append,
        maximum_server_error_retries=1,
    )
    transport.set_case("case-a")

    result = transport(
        stage="reviewer",
        model="grok-4.3",
        system_prompt="system",
        user_prompt="user",
        response_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        timeout_seconds=10,
        max_output_tokens=20,
        media_context=None,
    )

    assert json.loads(result) == {"ok": True}
    assert sleeps == [5.0]
    saved = json.loads((tmp_path / "cost_ledger.json").read_text(encoding="utf-8"))
    operation = saved["operations"][0]
    assert operation["status"] == "completed"
    assert operation["attempt_number"] == 2
    assert operation["server_error_events"][0]["status_code"] == 503
    assert saved["ambiguous_exposure_usd"] == 0


def test_nonretryable_http_error_is_definite_and_unblocked(tmp_path: Path) -> None:
    ledger = pilot.PilotLedger(
        tmp_path / "cost_ledger.json",
        model="grok-4.3",
        hard_limit_usd=1.0,
    )
    transport = pilot.PilotTransport(
        api_key="not-a-real-key",
        base_url=pilot.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "raw_responses",
        post=lambda *_args, **_kwargs: FakeResponse(
            {"error": {"message": "forbidden"}},
            status_code=403,
        ),
    )
    transport.set_case("case-a")

    with pytest.raises(pilot.DefiniteHTTPError, match="HTTP 403"):
        transport(
            stage="proposer",
            model="grok-4.3",
            system_prompt="system",
            user_prompt="user",
            response_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            timeout_seconds=10,
            max_output_tokens=20,
            media_context=None,
        )
    saved = json.loads((tmp_path / "cost_ledger.json").read_text(encoding="utf-8"))
    assert saved["blocked"] is False
    assert saved["operations"][0]["status"] == "http_error"
    assert saved["ambiguous_exposure_usd"] == 0


def test_pilot_cases_are_fixed_and_include_recent_production_regressions() -> None:
    end_to_end, challenges, revisions = pilot.load_cases(Path.cwd())
    assert len(end_to_end) == 10
    assert len(challenges) == 8
    assert len(revisions) == 2
    ids = {row["case_id"] for row in end_to_end}
    assert {
        "risk-burnham-unverified-allegations",
        "recent-berlin-wall-misspelling",
        "recent-berlin-wall-clarification",
        "recent-abusive-attack",
        "recent-incoherent-currency",
    } <= ids
    assert {row["case_id"] for row in revisions} == {
        "forced-revision-direct-answer-first",
        "forced-revision-off-topic-principle",
    }
    assert all(
        pilot.context_for_case(row)["current_date"] == pilot.FIXTURE_CURRENT_DATE
        for row in [*end_to_end, *revisions]
    )


def test_expected_no_reply_does_not_hide_invalid_structured_output() -> None:
    case = {
        "expected_outcomes": ["no_reply"],
        "expected_modes": [],
        "required_term_groups": [],
        "forbidden_phrases": [],
    }
    result = SimpleNamespace(
        reply=None,
        audit=({"stage": "proposer", "status": "invalid"},),
    )

    passed, failures = pilot.grade_end_to_end(case, result)

    assert passed is False
    assert failures == ["invalid structured output at stage(s): proposer"]


def test_recovered_invalid_response_does_not_fail_pilot_grading() -> None:
    case = {
        "expected_outcomes": ["no_reply"],
        "expected_modes": [],
        "required_term_groups": [],
        "forbidden_phrases": [],
    }
    result = SimpleNamespace(
        reply=None,
        audit=(
            {"stage": "proposer", "status": "invalid_response_retry"},
            {"stage": "proposer", "status": "completed"},
        ),
    )

    passed, failures = pilot.grade_end_to_end(case, result)

    assert passed is True
    assert failures == []


def test_forced_revision_grading_requires_both_fresh_revision_stages() -> None:
    case = {
        "expected_outcomes": ["approved"],
        "expected_modes": [],
        "required_term_groups": [],
        "forbidden_phrases": [],
    }
    complete = SimpleNamespace(
        reply="A corrected reply.",
        revision_count=1,
        audit=(
            {"stage": "reviewer", "status": "completed", "verdict": "revise"},
            {"stage": "revision_proposer", "status": "completed"},
            {"stage": "revision_reviewer", "status": "completed", "verdict": "approve"},
        ),
    )

    passed, failures = pilot.grade_forced_revision(case, complete)

    assert passed is True
    assert failures == []

    incomplete = SimpleNamespace(
        reply="A corrected reply.",
        revision_count=1,
        audit=complete.audit[:-1],
    )
    passed, failures = pilot.grade_forced_revision(case, incomplete)
    assert passed is False
    assert failures == ["missing completed revision stage(s): revision_reviewer"]


def test_manifest_resume_fails_if_recorded_source_hash_changes(tmp_path: Path) -> None:
    pilot.write_manifest(Path.cwd(), tmp_path, "grok-4.3", 1.0)
    manifest_path = tmp_path / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "tools/pilot_ai_first_reply_strategy.py" in manifest["source_hashes"]
    assert "tests/fixtures/ai_first_reply_provider_revision_cases.json" in manifest["source_hashes"]
    manifest["source_hashes"]["reply_strategy.py"] = "0" * 64
    pilot.atomic_json(manifest_path, manifest)

    with pytest.raises(pilot.PilotError, match="source inputs"):
        pilot.write_manifest(Path.cwd(), tmp_path, "grok-4.3", 1.0)


def test_adversarial_review_validation_retries_once_then_succeeds() -> None:
    responses = iter(["", json.dumps({"ok": True})])

    value, retries = pilot.bounded_validate_response(
        lambda: next(responses),
        lambda raw: json.loads(raw),
        maximum_invalid_retries=1,
    )

    assert value == {"ok": True}
    assert retries == 1


def test_adversarial_review_validation_fails_after_second_invalid_response() -> None:
    responses = iter(["", ""])

    with pytest.raises(json.JSONDecodeError):
        pilot.bounded_validate_response(
            lambda: next(responses),
            lambda raw: json.loads(raw),
            maximum_invalid_retries=1,
        )


def test_pilot_module_does_not_import_or_invoke_production_bot() -> None:
    source = Path(pilot.__file__).read_text(encoding="utf-8")
    assert "import mrsMThatcher2" not in source
    assert "api.x.com" not in source
    assert "tweets" not in source
    assert "media/upload" not in source
    assert "systemctl" not in source
