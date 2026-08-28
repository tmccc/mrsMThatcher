from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from tools import run_writer_model_eval as evaluation


class FakeResponse:
    """Small synchronous requests response used by transport tests."""

    def __init__(self, raw: dict, status_code: int = 200) -> None:
        self.raw = raw
        self.status_code = status_code
        self.content = json.dumps(raw, sort_keys=True).encode()
        self.text = self.content.decode()

    def json(self) -> dict:
        """Return an isolated response document."""
        return copy.deepcopy(self.raw)


class FakePriorCache:
    """Record frozen-cache use without touching disk or a provider."""

    def __init__(self, *, exact_audit: bool = False) -> None:
        self.calls: list[dict] = []
        self.exact_audit = exact_audit

    def contains_call(self, **kwargs):
        """Report whether the candidate audit has an exact old identity."""
        self.calls.append({"operation": "contains", **copy.deepcopy(kwargs)})
        return self.exact_audit

    def replay(self, **kwargs):
        """Return one structured cached response and a cache-only event."""
        self.calls.append({"operation": "replay", **copy.deepcopy(kwargs)})
        stage = kwargs["stage"]
        content = (
            '{"status":"reply","reply":"Cached baseline draft."}'
            if stage in evaluation.WRITER_STAGES
            else '{"outcome":"pass"}'
        )
        return content, {
            "request_hash": "a" * 64,
            "stage": stage,
            "logical_provider": kwargs["logical_provider"],
            "logical_model": kwargs["logical_model"],
            "logical_reasoning_effort": kwargs["logical_reasoning_effort"],
            "effective_provider": kwargs["logical_provider"],
            "effective_model": kwargs["logical_model"],
            "effective_reasoning_effort": kwargs["logical_reasoning_effort"],
            "provider": kwargs["logical_provider"],
            "cache_hit": True,
            "cache_source": kwargs["cache_source"],
            "network_request": False,
            "provider_error": None,
            "request_status": "completed",
            "input_tokens": 10,
            "cached_input_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 4,
            "reasoning_tokens": 0,
            "provider_reported_cost_usd": None,
            "estimated_cost_usd": 0.001,
            "provider_latency_seconds": 0.25,
            "response_model": kwargs["logical_model"],
        }


def xai_metadata() -> dict[str, dict[str, int | str]]:
    """Return positive authenticated xAI pricing metadata for tests."""
    return {
        model: {
            "id": model,
            "prompt_text_token_price": 20_000,
            "cached_prompt_text_token_price": 2_000,
            "completion_text_token_price": 100_000,
        }
        for model in ("grok-4.3", "grok-4.6")
    }


def writer_schema() -> dict:
    """Return a minimal strict writer response schema."""
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "reply": {"type": ["string", "null"]},
        },
        "required": ["status", "reply"],
        "additionalProperties": False,
    }


def pipeline_call(
    transport,
    *,
    stage: str,
    provider: str = "OpenAI",
    payload: dict | None = None,
):
    """Invoke one production-identity pipeline call through a test transport."""
    model, effort = (
        ("gpt-5.6-sol", "medium")
        if provider == "OpenAI"
        else ("grok-4.3", "low")
    )
    return transport(
        provider=provider,
        stage=stage,
        model=model,
        system_prompt="Frozen production prompt",
        payload=payload or {"context": {"incoming_contribution": "Visible point"}},
        response_schema=writer_schema(),
        timeout_seconds=180,
        max_output_tokens=900,
        reasoning_effort=effort,
    )


def live_recorder(calls: list[dict]):
    """Return an injected live sender which records effective identities."""

    def send(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        return '{"status":"reply","reply":"Candidate draft."}', {
            "request_hash": evaluation.sha256_value(kwargs),
            "stage": kwargs["stage"],
            "logical_provider": kwargs["logical_provider"],
            "logical_model": kwargs["logical_model"],
            "logical_reasoning_effort": kwargs["logical_reasoning_effort"],
            "effective_provider": kwargs["effective_provider"],
            "effective_model": kwargs["effective_model"],
            "effective_reasoning_effort": kwargs["effective_reasoning_effort"],
            "provider": kwargs["effective_provider"],
            "cache_hit": False,
            "cache_source": None,
            "network_request": True,
            "provider_error": None,
            "request_status": "completed",
            "input_tokens": 20,
            "cached_input_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 5,
            "reasoning_tokens": 0,
            "provider_reported_cost_usd": 0.001
            if kwargs["effective_provider"] == "xAI"
            else None,
            "estimated_cost_usd": 0.002
            if kwargs["effective_provider"] == "OpenAI"
            else None,
            "provider_latency_seconds": 0.5,
            "response_model": kwargs["effective_model"],
        }

    return send


def fixture_case(case_id: str = "case-1") -> dict:
    """Return one minimal private writer case for report tests."""
    return {
        "case_id": case_id,
        "source": "challenge:fixture",
        "selection_index": 0,
        "context": {
            "target_id": case_id,
            "thread_id": f"thread-{case_id}",
            "lane": "mention",
            "incoming_contribution": "What actually happened?",
            "quoted_post": {"post_id": "root", "text": "Visible account post."},
            "parent_thread": [],
            "clarification_request": None,
            "current_date": "2026-08-28",
        },
        "recent_replies": [],
        "media_context": None,
        "expected_public_outcome": None,
        "writer_arm_execution_order": list(evaluation.ARMS),
        "canonical_writer_input_sha256": "b" * 64,
    }


def completed_result(case_id: str, arm: str, reply: str | None = "Reply.") -> dict:
    """Return one completed result row suitable for blind-score tests."""
    return {
        "case_id": case_id,
        "arm": arm,
        "execution_status": "completed",
        "final_status": "approved" if reply else "no_reply",
        "final_reason": "pipeline_approved" if reply else "writer_cannot_compose_safely",
        "final_public_reply": reply,
        "schema_failures": [],
        "provider_errors": [],
        "audit": [],
        "xai_claim_audit_outcomes": [],
        "requests": [],
    }


def test_previous_arm_a_routing_and_sol_reviewers_are_cache_only() -> None:
    prior = FakePriorCache()
    live_calls: list[dict] = []
    transport = evaluation.WriterPipelineTransport(
        arm="B",
        case_id="case",
        prior_cache=prior,
        registry=evaluation.WriterInputRegistry(),
        live_request=live_recorder(live_calls),
    )
    pipeline_call(transport, stage="candidate_backed_engagement", provider="xAI")
    pipeline_call(transport, stage="reply_necessity_1", provider="OpenAI")
    assert live_calls == []
    replayed = [call for call in prior.calls if call["operation"] == "replay"]
    assert [call["stage"] for call in replayed] == [
        "candidate_backed_engagement",
        "reply_necessity_1",
    ]
    reviewer = replayed[1]
    assert reviewer["logical_provider"] == "OpenAI"
    assert reviewer["logical_model"] == "gpt-5.6-sol"
    assert reviewer["logical_reasoning_effort"] == "medium"
    assert all(event["cache_source"] == "previous_arm_a" for event in transport.call_events)


def test_only_writer_family_changes_and_claim_audits_remain_grok_43_low() -> None:
    prior = FakePriorCache()
    live_calls: list[dict] = []
    transport = evaluation.WriterPipelineTransport(
        arm="D",
        case_id="case",
        prior_cache=prior,
        registry=evaluation.WriterInputRegistry(),
        live_request=live_recorder(live_calls),
    )
    pipeline_call(transport, stage="writer_v3_initial")
    pipeline_call(transport, stage="bounded_claim_cleanup")
    pipeline_call(transport, stage="exact_duplicate_repair")
    pipeline_call(transport, stage="direct_answer_repair")
    pipeline_call(transport, stage="narrow_claim_audit", provider="xAI")
    assert [call["stage"] for call in live_calls] == [
        "writer_v3_initial",
        "bounded_claim_cleanup",
        "exact_duplicate_repair",
        "direct_answer_repair",
        "narrow_claim_audit",
    ]
    for call in live_calls[:4]:
        assert call["logical_provider"] == "OpenAI"
        assert call["logical_model"] == "gpt-5.6-sol"
        assert call["effective_provider"] == "xAI"
        assert call["effective_model"] == "grok-4.6"
        assert call["effective_reasoning_effort"] == "low"
    audit = live_calls[-1]
    assert (
        audit["effective_provider"],
        audit["effective_model"],
        audit["effective_reasoning_effort"],
    ) == ("xAI", "grok-4.3", "low")


def test_every_arm_receives_identical_initial_canonical_writer_payload() -> None:
    registry = evaluation.WriterInputRegistry()
    observed = []
    for arm in evaluation.ARMS:
        prior = FakePriorCache()
        live_calls: list[dict] = []
        transport = evaluation.WriterPipelineTransport(
            arm=arm,
            case_id="same-case",
            prior_cache=prior,
            registry=registry,
            live_request=live_recorder(live_calls),
        )
        pipeline_call(
            transport,
            stage="writer_v3_initial",
            payload={"context": {"incoming_contribution": "Exact same visible point"}},
        )
        observed.append(
            transport.call_events[0]["canonical_writer_input_sha256"]
        )
    assert len(set(observed)) == 1
    assert registry.initial_hash("same-case") == observed[0]


def test_different_initial_payload_is_rejected_across_arms() -> None:
    registry = evaluation.WriterInputRegistry()
    first = evaluation.WriterPipelineTransport(
        arm="A",
        case_id="same-case",
        prior_cache=FakePriorCache(),
        registry=registry,
    )
    pipeline_call(first, stage="writer_v3_initial", payload={"same": True})
    second = evaluation.WriterPipelineTransport(
        arm="B",
        case_id="same-case",
        prior_cache=FakePriorCache(),
        registry=registry,
        live_request=live_recorder([]),
    )
    with pytest.raises(evaluation.EvaluationError, match="changed across arms"):
        pipeline_call(second, stage="writer_v3_initial", payload={"same": False})


def test_openai_and_xai_writer_request_formats_are_separate() -> None:
    common = {
        "stage": "writer_v3_initial",
        "system_prompt": "Frozen prompt",
        "payload": {"same": True},
        "response_schema": writer_schema(),
        "max_output_tokens": 900,
    }
    openai = evaluation.provider_request_body(
        provider="OpenAI",
        model="gpt-5.6-terra",
        reasoning_effort="medium",
        **common,
    )
    xai = evaluation.provider_request_body(
        provider="xAI",
        model="grok-4.6",
        reasoning_effort="low",
        **common,
    )
    assert openai["max_completion_tokens"] == 900
    assert openai["store"] is False
    assert openai["prompt_cache_options"] == {"mode": "explicit"}
    assert "max_tokens" not in openai
    assert xai["max_tokens"] == 900
    assert "max_completion_tokens" not in xai
    assert "store" not in xai
    openai_without_identity = copy.deepcopy(openai)
    xai_without_identity = copy.deepcopy(xai)
    for body in (openai_without_identity, xai_without_identity):
        body.pop("model")
        body.pop("reasoning_effort")
    for key in ("max_completion_tokens", "store", "prompt_cache_options"):
        openai_without_identity.pop(key, None)
    xai_without_identity.pop("max_tokens", None)
    assert openai_without_identity == xai_without_identity


def test_baseline_writer_uses_prior_cached_sol_result() -> None:
    prior = FakePriorCache()

    def forbidden_live(**_kwargs):
        raise AssertionError("baseline writer attempted a live call")

    transport = evaluation.WriterPipelineTransport(
        arm="A",
        case_id="case",
        prior_cache=prior,
        registry=evaluation.WriterInputRegistry(),
        live_request=forbidden_live,
    )
    result = pipeline_call(transport, stage="writer_v3_initial")
    assert "Cached baseline draft" in result
    replay = [call for call in prior.calls if call["operation"] == "replay"][0]
    assert replay["logical_provider"] == "OpenAI"
    assert replay["logical_model"] == "gpt-5.6-sol"
    assert replay["logical_reasoning_effort"] == "medium"
    assert transport.call_events[0]["cache_hit"] is True


def test_arm_a_exact_replay_compares_status_reason_reply_and_call_path() -> None:
    result = SimpleNamespace(
        status="approved", reason="pipeline_approved", reply="Exact reply."
    )
    transport = SimpleNamespace(
        call_events=[
            {
                "stage": "candidate_backed_engagement",
                "request_hash": "a" * 64,
                "cache_source": "previous_arm_a",
            },
            {
                "stage": "writer_v3_initial",
                "request_hash": "b" * 64,
                "cache_source": "previous_arm_a",
            },
        ]
    )
    stored = {
        "final_status": "approved",
        "final_reason": "pipeline_approved",
        "final_public_reply": "Exact reply.",
        "requests": [
            {"stage": "candidate_backed_engagement", "request_hash": "a" * 64},
            {"stage": "writer_v3_initial", "request_hash": "b" * 64},
        ],
    }
    evaluation.assert_baseline_replay_exact(
        result=result, transport=transport, stored=stored
    )
    changed = copy.deepcopy(stored)
    changed["final_public_reply"] = "Different reply."
    with pytest.raises(evaluation.EvaluationError, match="public outcome"):
        evaluation.assert_baseline_replay_exact(
            result=result, transport=transport, stored=changed
        )


def test_model_and_provider_are_part_of_candidate_request_cache_identity() -> None:
    common = {
        "stage": "writer_v3_initial",
        "system_prompt": "Frozen prompt",
        "payload": {"same": True},
        "response_schema": writer_schema(),
        "maximum_output_tokens": 900,
        "timeout_seconds": 180,
    }
    identities = []
    for provider, model, effort in (
        ("OpenAI", "gpt-5.6-sol", "medium"),
        ("OpenAI", "gpt-5.6-terra", "medium"),
        ("OpenAI", "gpt-5.6-luna", "medium"),
        ("xAI", "grok-4.6", "low"),
        ("xAI", "grok-4.3", "low"),
    ):
        _body, identity, request_hash = evaluation.candidate_request_identity(
            provider=provider,
            model=model,
            reasoning_effort=effort,
            **common,
        )
        assert identity["provider"] == provider
        assert identity["model"] == model
        identities.append(request_hash)
    assert len(set(identities)) == 5


def test_ambiguous_transmission_is_never_retried(tmp_path: Path) -> None:
    calls = 0

    def post(_url, **_kwargs):
        nonlocal calls
        calls += 1
        raise requests.Timeout("outcome unknown")

    ledger = evaluation.WriterRequestLedger(
        tmp_path / "ledger.json", case_set_sha256="c" * 64
    )
    registry = evaluation.WriterInputRegistry()
    first = evaluation.WriterPipelineTransport(
        arm="D",
        case_id="case",
        prior_cache=FakePriorCache(),
        registry=registry,
        ledger=ledger,
        response_dir=tmp_path / "responses",
        api_keys={"OpenAI": "openai-key", "xAI": "xai-key"},
        xai_model_metadata=xai_metadata(),
        post=post,
    )
    with pytest.raises(evaluation.AmbiguousRequestError):
        pipeline_call(first, stage="writer_v3_initial")
    assert calls == 1
    assert ledger.data["blocked"] is True

    second = evaluation.WriterPipelineTransport(
        arm="D",
        case_id="case",
        prior_cache=FakePriorCache(),
        registry=registry,
        ledger=ledger,
        response_dir=tmp_path / "responses",
        api_keys={"OpenAI": "openai-key", "xAI": "xai-key"},
        xai_model_metadata=xai_metadata(),
        post=post,
    )
    with pytest.raises(evaluation.AmbiguousRequestError):
        pipeline_call(second, stage="writer_v3_initial")
    assert calls == 1


def test_live_flag_is_required_before_availability_or_provider_work() -> None:
    with pytest.raises(evaluation.EvaluationError, match="execute-live-models"):
        evaluation.execute_experiment(execute_live_models=False)
    assert evaluation._build_parser().parse_args([]).execute_live_models is False


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/anything",
        "https://api.twitter.com/2/tweets",
        "https://example.com/v1/chat/completions",
        "http://api.x.ai/v1/chat/completions",
        "https://api.x.ai.evil.example/v1/chat/completions",
        "https://api.openai.com/v1/files",
        "https://api.openai.com/v1/chat/completions?redirect=x.com",
    ],
)
def test_non_provider_and_x_twitter_hosts_are_rejected(url: str) -> None:
    with pytest.raises(evaluation.EvaluationError, match="allowlist|permitted"):
        evaluation.validate_provider_url(url)
    assert evaluation.validate_provider_url(
        "https://api.openai.com/v1/chat/completions",
        expected_host="api.openai.com",
    )
    assert evaluation.validate_provider_url(
        "https://api.x.ai/v1/chat/completions", expected_host="api.x.ai"
    )


def test_production_and_runtime_paths_cannot_be_written() -> None:
    with pytest.raises(evaluation.EvaluationError, match="production checkout"):
        evaluation.ensure_private_output_dir(
            evaluation.PRODUCTION_CHECKOUT / "writer-output",
            project_dir=evaluation.PROJECT_ROOT,
        )
    with pytest.raises(evaluation.EvaluationError, match="evaluation worktree"):
        evaluation.ensure_private_output_dir(
            evaluation.PROJECT_ROOT / "writer-output",
            project_dir=evaluation.PROJECT_ROOT,
        )
    with pytest.raises(evaluation.EvaluationError, match="unsafe|escapes"):
        evaluation.private_path(evaluation.DEFAULT_OUTPUT_ROOT, "../production.txt")
    source = Path(evaluation.__file__).read_text(encoding="utf-8")
    assert "import mrsMThatcher2" not in source
    assert "import tweepy" not in source
    assert "api.twitter.com" not in source
    assert "https://x.com" not in source


def test_blind_outputs_reveal_neither_definitions_nor_private_key() -> None:
    case = fixture_case("blind-case")
    results = [
        completed_result(
            "blind-case",
            arm,
            reply=None if arm == "C" else f"Distinct public outcome {index}.",
        )
        for index, arm in enumerate(evaluation.ARMS, start=1)
    ]
    key = {"A": "Z", "B": "V", "C": "X", "D": "W", "E": "Y"}
    rows = evaluation.build_blind_rows(
        cases=[case], results=results, arm_key=key
    )
    markdown = evaluation.render_blind_review(rows)
    evaluation._validate_blind_outputs(rows=rows, markdown=markdown, arm_key=key)
    serialised = json.dumps(rows, sort_keys=True) + markdown
    for secret in (
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "grok-4.6",
        "grok-4.3",
        "reasoning_effort",
        "effective_provider",
    ):
        assert secret not in serialised
    assert set(rows[0]["outcomes"]) == set(evaluation.BLIND_LABELS)
    assert "[NO REPLY]" in markdown


def test_completed_score_reporting_decodes_all_five_arms(
    tmp_path: Path,
) -> None:
    case = fixture_case("scored-case")
    results = [completed_result("scored-case", arm) for arm in evaluation.ARMS]
    key = {"A": "Z", "B": "V", "C": "W", "D": "X", "E": "Y"}
    scores = tmp_path / "blind_scores.csv"
    evaluation.atomic_text(
        scores,
        "case_id,V_rating,W_rating,X_rating,Y_rating,Z_rating,preferred_outcome,notes\n"
        "scored-case,acceptable,minor,unacceptable,minor,unacceptable,V,checked\n",
    )
    comparison = {
        "arms": {
            arm: {
                "variable_path_attributed_billed_cost_usd": 0.01,
                "variable_path_attributed_estimated_cost_usd": 0.02,
                "writer_request_latency": {
                    "median_seconds": 1.0,
                    "p95_seconds": 2.0,
                    "maximum_seconds": 2.0,
                },
            }
            for arm in evaluation.ARMS
        }
    }
    decoded = evaluation.load_completed_scores(
        scores_path=scores,
        cases=[case],
        results=results,
        arm_key=key,
        comparison=comparison,
    )
    assert decoded["arms"]["B"]["acceptable"] == 1
    assert decoded["arms"]["B"]["preferred"] == 1
    assert decoded["arms"]["C"]["minor"] == 1
    assert decoded["arms"]["D"]["unacceptable"] == 1
    assert decoded["arms"]["E"]["minor"] == 1
    assert decoded["arms"]["A"]["unacceptable"] == 1
    assert decoded["preferred_outcome_totals"]["B"] == 1
    assert len(decoded["pairwise"]) == 10
    assert decoded["arms"]["B"][
        "billed_cost_per_acceptable_final_outcome_usd"
    ] == pytest.approx(0.01)


def test_openai_usage_estimates_use_each_frozen_rate() -> None:
    raw = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "prompt_tokens_details": {"cached_tokens": 40},
        }
    }
    estimates = {
        model: evaluation.estimate_openai_cost_ticks(
            raw, model=model, fallback_ticks=1
        )[0]
        for model in evaluation.OPENAI_PRICING_USD_PER_MILLION
    }
    assert estimates["gpt-5.6-sol"] > estimates["gpt-5.6-terra"]
    assert estimates["gpt-5.6-terra"] > estimates["gpt-5.6-luna"]


def test_nonbaseline_order_is_stable_per_case_and_excludes_a() -> None:
    first = evaluation.stable_nonbaseline_arm_order("d" * 64, "case-one")
    second = evaluation.stable_nonbaseline_arm_order("d" * 64, "case-one")
    assert first == second
    assert set(first) == {"B", "C", "D", "E"}
    assert "A" not in first
