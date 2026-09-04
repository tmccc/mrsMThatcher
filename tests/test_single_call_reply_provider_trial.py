from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools import run_single_call_reply_provider_trial as trial


def payload() -> dict:
    return {
        "lane": "mention",
        "roles": {
            "account": "Margaret Thatcher quotation account; not Margaret Thatcher",
            "user": "latest external contributor",
            "other_user": "other external participant",
        },
        "identities": {
            "target_post_id": "target",
            "root_post_id": "root",
            "parent_post_id": "root",
            "subject_post_id": "root",
        },
        "visible_conversation": [
            {"post_id": "root", "role": "account", "text": "A point."},
            {"post_id": "target", "role": "user", "text": "Thank you."},
        ],
        "recent_same_author_account_interactions": [],
        "recent_account_replies": [],
        "trusted_facts": [],
        "visual_description": None,
    }


def case() -> dict:
    value = payload()
    return {
        "case_id": "calibration:test",
        "phase": "calibration",
        "payload": value,
        "payload_sha256": trial.value_sha256(value),
    }


def output_text(**changes: object) -> str:
    value: dict[str, object] = {
        "decision": "reply",
        "reply_kind": "social",
        "reply": "Thank you; that is kind of you.",
        "used_fact_ids": [],
        "reason_code": "useful_reply",
    }
    value.update(changes)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class FakeResponse:
    def __init__(self, status_code: int, value: dict):
        self.status_code = status_code
        self._value = value
        self.content = json.dumps(value).encode()

    def json(self) -> dict:
        return self._value


def xai_response(text: str | None = None) -> FakeResponse:
    return FakeResponse(
        200,
        {
            "model": "grok-4.6",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": text or output_text()},
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cost_in_usd_ticks": 1_000_000,
            },
        },
    )


def test_all_provider_envelopes_carry_identical_prompt_and_case_payload() -> None:
    expected = payload()
    envelopes = {
        provider: trial.provider_request(
            provider,
            prompt=trial.SYSTEM_PROMPT,
            payload=expected,
            claude_explicit_temperature=True,
        )[1]
        for provider in trial.PROVIDERS
    }
    observed = {
        "xai": json.loads(envelopes["xai"]["messages"][1]["content"]),
        "openai": json.loads(envelopes["openai"]["input"]),
        "anthropic": json.loads(envelopes["anthropic"]["messages"][0]["content"]),
    }
    assert observed == {provider: expected for provider in trial.PROVIDERS}
    assert envelopes["xai"]["messages"][0]["content"] == trial.SYSTEM_PROMPT
    assert envelopes["openai"]["instructions"] == trial.SYSTEM_PROMPT
    assert envelopes["anthropic"]["system"] == trial.SYSTEM_PROMPT


def test_compact_fact_records_are_bounded_resolvable_and_omit_internal_fields() -> None:
    records = [
        {
            "evidence_id": "long-private-id-1",
            "passage": "  The retained passage.  ",
            "source_title": "Archive",
            "stable_locator": "Volume 1, page 2",
            "source_url": "https://not-model-facing.example",
            "opaque_hash": "private",
            "actor": "",
        },
        {
            "evidence_id": "long-private-id-2",
            "statement": "A second passage.",
            "source": "Fixture",
            "locator": "F-2",
        },
    ]
    compact, private = trial.compact_fact_records(records)
    assert compact == [
        {
            "id": "F1",
            "passage": "The retained passage.",
            "source": "Archive",
            "locator": "Volume 1, page 2",
        },
        {
            "id": "F2",
            "passage": "A second passage.",
            "source": "Fixture",
            "locator": "F-2",
        },
    ]
    assert private["F1"]["source_identity"] == "long-private-id-1"
    assert private["F1"]["source_record"]["source_url"].startswith("https://")
    assert set(compact[0]) == {"id", "passage", "source", "locator"}


def test_context_bounds_keep_subject_target_and_nearest_context() -> None:
    turns = [
        {"post_id": f"p{index}", "author_role": "user", "text": str(index) * 40}
        for index in range(20)
    ]
    bounded = trial.bound_visible_conversation(turns)
    assert len(bounded) == trial.MAX_VISIBLE_TURNS
    assert bounded[0]["post_id"] == "p0"
    assert bounded[-1]["post_id"] == "p19"
    assert [row["post_id"] for row in bounded[1:]] == [f"p{index}" for index in range(9, 20)]
    assert sum(len(row["text"]) for row in bounded) <= trial.MAX_VISIBLE_CHARACTERS


def test_canonical_payload_bounds_optional_context_and_target_once() -> None:
    turns = [
        {"post_id": "root", "role": "account", "text": "Root"},
        {"post_id": "target", "role": "user", "text": "Target"},
    ]
    value = trial._payload(
        lane="mention",
        target_id="target",
        root_id="root",
        parent_id="root",
        subject_id="root",
        visible=turns,
        same_author=[{"contributor": str(index), "account_reply": str(index)} for index in range(12)],
        recent_replies=[str(index) for index in range(40)],
        facts=[{"id": f"F{index}", "passage": "p", "source": "s", "locator": "l"} for index in range(1, 40)],
        visual_description=None,
    )
    assert len(value["recent_same_author_account_interactions"]) == 8
    assert len(value["recent_account_replies"]) == 30
    assert len(value["trusted_facts"]) == 32
    assert sum(row["post_id"] == "target" for row in value["visible_conversation"]) == 1


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ('{"decision":"reply","decision":"no_reply"}', "strict_json:ValueError"),
        (output_text(extra="bad"), "response_fields_mismatch"),
        (output_text(reply="Visit https://example.com"), "reply_contains_link_or_address"),
        (output_text(reply="Hello\u0001"), "control_or_format_character"),
        (output_text(reply="First. Second. Third."), "reply_sentence_limit_exceeded"),
        (output_text(used_fact_ids=["F99"]), "unknown_fact_id"),
        (output_text(reply_kind="direct_factual"), "direct_factual_missing_fact_id"),
    ],
)
def test_strict_local_validation_rejects_invalid_outputs(raw: str, error: str) -> None:
    result = trial.validate_output_text(
        raw,
        fact_ids={"F1"},
        comparison_replies=[],
        payload=payload(),
    )
    assert not result["valid"]
    assert error in result["errors"]


def test_no_reply_contract_and_weighted_length_are_enforced() -> None:
    good = output_text(
        decision="no_reply",
        reply_kind="no_reply",
        reply="",
        used_fact_ids=[],
        reason_code="completed_exchange",
    )
    result = trial.validate_output_text(good, fact_ids=set(), comparison_replies=[], payload=payload())
    assert result["valid"]
    bad = output_text(reply="x" * 271)
    result = trial.validate_output_text(bad, fact_ids=set(), comparison_replies=[], payload=payload())
    assert "invalid_reply_length_or_whitespace" in result["errors"]


def test_exact_and_near_duplicate_measurement_is_a_local_failure() -> None:
    text = "Responsibility belongs with the person who makes the choice."
    result = trial.validate_output_text(
        output_text(reply=text),
        fact_ids=set(),
        comparison_replies=[text],
        payload=payload(),
    )
    assert not result["valid"]
    assert result["duplicates"]["exact"]
    assert "exact_duplicate_reply" in result["errors"]


def test_provider_models_reasoning_temperature_and_structured_outputs() -> None:
    bodies = {
        provider: trial.provider_request(
            provider,
            prompt="shared",
            payload=payload(),
            claude_explicit_temperature=True,
        )[1]
        for provider in trial.PROVIDERS
    }
    assert bodies["xai"]["model"] == "grok-4.6"
    assert bodies["xai"]["reasoning_effort"] == "high"
    assert bodies["xai"]["temperature"] == 1
    assert bodies["openai"]["model"] == "gpt-5.6-sol"
    assert bodies["openai"]["reasoning"] == {"effort": "high"}
    assert bodies["openai"]["temperature"] == 1
    assert bodies["openai"]["store"] is False
    assert bodies["anthropic"]["model"] == "claude-sonnet-5"
    assert bodies["anthropic"]["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert bodies["anthropic"]["output_config"]["effort"] == "high"
    assert bodies["anthropic"]["temperature"] == 1
    assert all(body.get("max_tokens", body.get("max_output_tokens")) == 8192 for body in bodies.values())


def test_provider_schema_adaptation_preserves_common_local_contract() -> None:
    assert trial.RESPONSE_SCHEMA["properties"]["used_fact_ids"]["uniqueItems"] is True
    assert "uniqueItems" not in trial.provider_schema("openai")["properties"]["used_fact_ids"]
    assert "pattern" not in trial.provider_schema("anthropic")["properties"]["used_fact_ids"]["items"]
    assert trial.provider_schema("xai") == trial.RESPONSE_SCHEMA


def test_claude_temperature_fallback_omits_all_sampling_parameters() -> None:
    assert trial.claude_temperature_fallback_allowed(
        400, "temperature cannot be set while adaptive thinking is enabled"
    )
    _, body = trial.provider_request(
        "anthropic",
        prompt="shared",
        payload=payload(),
        claude_explicit_temperature=False,
    )
    assert not {"temperature", "top_p", "top_k"} & set(body)


def test_claude_preflight_records_definite_temperature_rejection() -> None:
    calls: list[dict] = []

    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return FakeResponse(
            400,
            {"error": {"message": "temperature is not supported with adaptive thinking"}},
        )

    result = trial.run_claude_temperature_preflight(key="secret", post=post, secrets_to_hide=["secret"])
    assert len(calls) == 1
    assert result["status"] == "temperature_rejected"
    assert result["explicit_temperature_accepted"] is False
    assert result["effective_sampling"] == "provider_default"


def test_one_logical_model_call_and_only_one_definite_transport_retry() -> None:
    responses = [FakeResponse(500, {"error": {"message": "pre-response"}}), xai_response()]
    calls = 0

    def post(*args, **kwargs):
        nonlocal calls
        calls += 1
        return responses.pop(0)

    client = trial.ProviderClient({"xai": "key"}, post=post, sleep=lambda _: None)
    budget = trial.CostBudget([], {})
    result = client.call(
        provider="xai",
        case=case(),
        phase="calibration",
        sample_index=1,
        prompt=trial.SYSTEM_PROMPT,
        claude_explicit_temperature=True,
        budget=budget,
    )
    assert result["status"] == "completed"
    assert result["logical_model_call_count"] == 1
    assert result["attempt_count"] == calls == 2
    assert result["transport_retry_count"] == 1


def test_invalid_json_is_not_retried() -> None:
    calls = 0

    def post(*args, **kwargs):
        nonlocal calls
        calls += 1
        return xai_response("not-json")

    result = trial.ProviderClient({"xai": "key"}, post=post).call(
        provider="xai",
        case=case(),
        phase="calibration",
        sample_index=1,
        prompt=trial.SYSTEM_PROMPT,
        claude_explicit_temperature=True,
        budget=trial.CostBudget([], {}),
    )
    assert result["status"] == "invalid"
    assert result["logical_model_call_count"] == calls == 1
    assert result["transport_retry_count"] == 0


def test_cost_ceiling_stops_only_the_affected_provider_without_network() -> None:
    existing = [{"provider": "xai", "cost_usd": 10.0}]
    calls = 0

    def post(*args, **kwargs):
        nonlocal calls
        calls += 1
        return xai_response()

    result = trial.ProviderClient({"xai": "key"}, post=post).call(
        provider="xai",
        case=case(),
        phase="calibration",
        sample_index=1,
        prompt=trial.SYSTEM_PROMPT,
        claude_explicit_temperature=True,
        budget=trial.CostBudget(existing, {}),
    )
    assert result["status"] == "budget_exceeded"
    assert result["logical_model_call_count"] == 0
    assert calls == 0


def test_stable_orders_and_call_plan_are_reproducible_and_unique() -> None:
    assert trial.stable_provider_order("hash", "case", 1) == trial.stable_provider_order("hash", "case", 1)
    assert trial.blinded_mapping("secret", "case") == trial.blinded_mapping("secret", "case")
    assert set(trial.blinded_mapping("secret", "case")) == set(trial.BLIND_LABELS)
    cases = [case()]
    plan = trial.planned_calls(cases, phase="calibration", case_set_hash_value="hash")
    assert len(plan) == 3
    assert len({row["logical_call_id"] for row in plan}) == 3
    assert {row["payload_sha256"] for row in plan} == {case()["payload_sha256"]}


def test_dry_run_executes_no_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = {
        "status": "prepared",
        "stability_case_ids": [],
        "claude_temperature_preflight": {"status": "not_run"},
    }
    trial.atomic_json(tmp_path / "run.json", run)
    trial.atomic_jsonl(tmp_path / "cases.jsonl", [case()])
    trial.atomic_jsonl(tmp_path / "results.jsonl", [])
    monkeypatch.setattr(trial, "ensure_private_output", lambda value: value)

    def forbidden_factory(*args, **kwargs):
        raise AssertionError("dry run instantiated a provider client")

    plan = trial.execute_phase(
        output=tmp_path,
        phase="calibration",
        live=False,
        client_factory=forbidden_factory,
    )
    assert len(plan) == 3


def test_credentials_are_redacted_and_never_part_of_request_hash() -> None:
    secret = "credential-private-value"
    assert secret not in trial.redact(f"authorization={secret}", [secret])
    endpoint, body = trial.provider_request(
        "openai",
        prompt="shared",
        payload=payload(),
        claude_explicit_temperature=True,
    )
    assert secret not in json.dumps({"endpoint": endpoint, "body": body})
    assert secret in trial._headers("openai", secret)["authorization"]


def test_calibration_and_holdout_identifiers_are_disjoint() -> None:
    assert not set(trial.CALIBRATION_SYNTHETIC_IDS) & set(trial.HOLDOUT_SYNTHETIC_IDS)
    calibration = {
        (f"synthetic:{value}", trial.value_sha256({"id": value}))
        for value in trial.CALIBRATION_SYNTHETIC_IDS
    }
    holdout = {
        (f"synthetic:{value}", trial.value_sha256({"id": value}))
        for value in trial.HOLDOUT_SYNTHETIC_IDS
    }
    assert calibration.isdisjoint(holdout)


def test_private_output_is_confined_and_modes_are_private(tmp_path: Path) -> None:
    allowed = tmp_path / "authorised"
    output = trial.ensure_private_output(allowed, allowed_root=allowed)
    assert output.stat().st_mode & 0o777 == 0o700
    trial.atomic_text(output / "test.txt", "private")
    assert (output / "test.txt").stat().st_mode & 0o777 == 0o600
    with pytest.raises(trial.TrialError):
        trial.ensure_private_output(tmp_path / "elsewhere", allowed_root=allowed)
    with pytest.raises(trial.TrialError):
        trial.private_path(output, "../escape")


def test_only_three_provider_destinations_are_allowed() -> None:
    for setting in trial.PROVIDER_SETTINGS.values():
        assert trial.validate_endpoint(setting["endpoint"]) == setting["endpoint"]
    with pytest.raises(trial.TrialError):
        trial.validate_endpoint("https://api.x.com/2/tweets")
    with pytest.raises(trial.TrialError):
        trial.validate_endpoint("https://api.openai.com/v1/responses?callback=bad")


def test_response_schema_has_exact_contract() -> None:
    assert trial.RESPONSE_SCHEMA["additionalProperties"] is False
    assert set(trial.RESPONSE_SCHEMA["properties"]) == {
        "decision",
        "reply_kind",
        "reply",
        "used_fact_ids",
        "reason_code",
    }
    assert set(trial.RESPONSE_SCHEMA["required"]) == set(trial.RESPONSE_SCHEMA["properties"])
    assert trial.RESPONSE_SCHEMA["properties"]["decision"]["enum"] == ["reply", "no_reply"]


def test_runner_has_no_x_client_or_post_endpoint() -> None:
    source = Path(trial.__file__).read_text(encoding="utf-8")
    assert "api.x.com" not in source
    assert "tweepy" not in source
    assert "tested_reply_pipeline.py" not in source
