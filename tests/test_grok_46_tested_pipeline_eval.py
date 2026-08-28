from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from tools import run_grok_46_tested_pipeline_eval as evaluation


class FakeResponse:
    def __init__(self, raw: dict, status_code: int = 200) -> None:
        self.raw = raw
        self.status_code = status_code
        self.content = json.dumps(raw, sort_keys=True).encode()
        self.text = self.content.decode()

    def json(self) -> dict:
        return copy.deepcopy(self.raw)


def xai_metadata() -> dict[str, dict[str, int | str]]:
    return {
        model: {
            "id": model,
            "prompt_text_token_price": 20_000,
            "cached_prompt_text_token_price": 2_000,
            "completion_text_token_price": 100_000,
        }
        for model in ("grok-4.3", "grok-4.6")
    }


def raw_response(provider: str, content: str = '{"outcome":"pass"}') -> dict:
    usage = {
        "prompt_tokens": 20,
        "completion_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 2},
    }
    if provider == "xAI":
        usage["cost_in_usd_ticks"] = 1_000_000
    return {
        "id": f"response-{provider}",
        "model": "grok-4.3" if provider == "xAI" else "gpt-5.6-sol",
        "choices": [{"message": {"content": content}}],
        "usage": usage,
    }


def pipeline_request(transport, *, provider: str, payload: dict | None = None, timeout: int = 180):
    if provider == "xAI":
        model, effort = "grok-4.3", "low"
    else:
        model, effort = "gpt-5.6-sol", "medium"
    return transport(
        provider=provider,
        stage="same_stage",
        model=model,
        system_prompt="Frozen prompt",
        payload=payload or {"visible": "same"},
        response_schema={
            "type": "object",
            "properties": {"outcome": {"type": "string"}},
            "required": ["outcome"],
            "additionalProperties": False,
        },
        timeout_seconds=timeout,
        max_output_tokens=100,
        reasoning_effort=effort,
    )


def fixture_case(case_id: str, source: str = "prospective") -> dict:
    context = {
        "target_id": case_id,
        "thread_id": f"thread-{case_id}",
        "lane": "mention",
        "incoming_contribution": f"Contribution {case_id}",
        "quoted_post": None,
        "parent_thread": [],
        "clarification_request": None,
        "current_date": "2026-08-25",
    }
    return {
        "case_id": case_id,
        "source": source,
        "source_identity": case_id,
        "canonical_target_identity": f"target:{case_id}",
        "context": context,
        "recent_replies": [],
        "recent_reply_history": {
            "available": False,
            "basis": "exact_recent_reply_history_unavailable",
        },
        "media_context": None,
        "retained_metadata": {},
        "expected_public_outcome": None,
    }


def prospective_row(*, image: bool = False) -> dict:
    visual_summary = {
        "native_photo_count_max": 1 if image else 0,
    }
    return {
        "candidate_key": "candidate-1",
        "branch_key": "branch-1",
        "principal_author_key": "principal-1",
        "root_post_id": "root-1",
        "warnings": [],
        "reply_visual_context_summaries": (
            [{"post_id": "user-1", "native_photo_count_max": 1}] if image else []
        ),
        "path_turns": [
            {
                "post_id": "root-1",
                "parent_post_id": None,
                "author_role": "account",
                "author_key": "account",
                "text": "A retained account post.",
                "lane": "other conversational lane",
                "account_turn_asked_for_clarification": False,
                "reply_visual_context_summary": {"native_photo_count_max": 0},
            },
            {
                "post_id": "user-1",
                "parent_post_id": "root-1",
                "author_role": "user",
                "author_key": "principal-1",
                "text": "A retained user question?",
                "lane": "mention",
                "first_observed_at": "2026-08-25T23:59:00Z",
                "account_turn_asked_for_clarification": False,
                "reply_visual_context_summary": visual_summary,
            },
        ],
    }


def test_tracked_challenge_set_includes_requested_regression_coverage() -> None:
    cases, skips = evaluation.load_challenge_cases(evaluation.PROJECT_ROOT)
    assert skips == []
    regression_cases = {
        case["retained_metadata"]["coverage_categories"][0]: case
        for case in cases
        if case["source"] == "challenge:tested_pipeline_regression"
    }
    assert set(regression_cases) == {
        "already_answered_question",
        "group_hostility",
        "civil_criticism",
    }
    for category, case in regression_cases.items():
        expected = case["expected_public_outcome"]["allowed_statuses"][0]
        grade = evaluation.grade_challenge_result(
            case,
            final_status=expected,
            final_reply="A concise public reply." if expected == "approved" else None,
        )
        assert grade == {
            "status": "pass",
            "failures": [],
            "basis": "explicit_tracked_fixture_constraint",
        }, category


def test_arms_change_only_effective_xai_model_and_effort() -> None:
    base_body = None
    observed = {}
    for arm, definition in evaluation.ARMS.items():
        effective = evaluation.effective_provider_identity(
            "xAI", "grok-4.3", "low", arm
        )
        assert effective == (
            definition["effective_xai_model"],
            definition["effective_xai_effort"],
        )
        body = evaluation.provider_request_body(
            provider="xAI",
            stage="gate",
            model=effective[0],
            reasoning_effort=effective[1],
            system_prompt="prompt",
            payload={"same": True},
            response_schema={"type": "object"},
            max_output_tokens=900,
        )
        observed[arm] = (body.pop("model"), body.pop("reasoning_effort"))
        base_body = body if base_body is None else base_body
        assert body == base_body
        assert evaluation.effective_provider_identity(
            "OpenAI", "gpt-5.6-sol", "medium", arm
        ) == ("gpt-5.6-sol", "medium")
    assert observed == {
        arm: (
            definition["effective_xai_model"],
            definition["effective_xai_effort"],
        )
        for arm, definition in evaluation.ARMS.items()
    }


def test_frozen_config_passed_to_pipeline_and_case_inputs_are_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = evaluation.production_config()
    assert evaluation.validate_strategy_config(config) == []
    evidence = {"logical_research_corpus_path": "corpus", "files": {}}
    case = fixture_case("case-1")
    case["selection_index"] = 0
    case["arm_execution_order"] = list(evaluation.ARMS)
    case["canonical_case_input_sha256"] = evaluation.sha256_value(
        evaluation.canonical_case_input(
            case, config=config, evidence_identity=evidence
        )
    )
    captured = []

    def fake_pipeline(**kwargs):
        captured.append(copy.deepcopy(kwargs))
        return SimpleNamespace(
            reply=None,
            status="no_reply",
            reason="fixture",
            model_call_count=0,
            audit=(),
        )

    monkeypatch.setattr(evaluation, "run_reply_pipeline", fake_pipeline)
    monkeypatch.setattr(evaluation, "EvidenceRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        evaluation,
        "fetch_xai_model_metadata",
        lambda **_kwargs: xai_metadata(),
    )
    manifest = {
        "case_set_sha256": "a" * 64,
        "evidence_corpus": evidence,
    }
    results, _ledger, blocker, _metadata = evaluation.execute_experiment(
        project_dir=evaluation.PROJECT_ROOT,
        output_dir=tmp_path,
        manifest=manifest,
        cases=[case],
        api_keys={"xAI": "x", "OpenAI": "o"},
        sleep=lambda _seconds: None,
    )
    assert blocker is None
    assert len(captured) == 4
    assert all(call["config"] == config for call in captured)
    assert all(evaluation.validate_strategy_config(call["config"]) == [] for call in captured)
    assert all(call["context"] == case["context"] for call in captured)
    assert {row["canonical_case_input_sha256"] for row in results} == {
        case["canonical_case_input_sha256"]
    }


def test_openai_cache_is_shared_only_for_the_entire_canonical_request(
    tmp_path: Path,
) -> None:
    calls = []

    def post(url, **kwargs):
        calls.append((url, copy.deepcopy(kwargs["json"])))
        return FakeResponse(raw_response("OpenAI"))

    ledger = evaluation.RequestLedger(
        tmp_path / "ledger.json", case_set_sha256="b" * 64
    )
    first = evaluation.ResearchTransport(
        arm="A",
        case_id="case",
        api_keys={"xAI": "x", "OpenAI": "o"},
        xai_model_metadata=xai_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "responses",
        post=post,
    )
    second = evaluation.ResearchTransport(
        arm="D",
        case_id="case",
        api_keys={"xAI": "x", "OpenAI": "o"},
        xai_model_metadata=xai_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "responses",
        post=post,
    )
    pipeline_request(first, provider="OpenAI")
    pipeline_request(second, provider="OpenAI")
    assert len(calls) == 1
    assert second.call_events[0]["cache_hit"] is True

    third = evaluation.ResearchTransport(
        arm="B",
        case_id="case",
        api_keys={"xAI": "x", "OpenAI": "o"},
        xai_model_metadata=xai_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "responses",
        post=post,
    )
    pipeline_request(third, provider="OpenAI", payload={"visible": "changed"})
    pipeline_request(third, provider="OpenAI", timeout=179)
    assert len(calls) == 3


def test_xai_caches_are_distinct_across_model_and_effort(tmp_path: Path) -> None:
    calls = []

    def post(url, **kwargs):
        calls.append(copy.deepcopy(kwargs["json"]))
        raw = raw_response("xAI")
        raw["model"] = kwargs["json"]["model"]
        return FakeResponse(raw)

    ledger = evaluation.RequestLedger(
        tmp_path / "ledger.json", case_set_sha256="c" * 64
    )
    hashes = []
    for arm in evaluation.ARMS:
        transport = evaluation.ResearchTransport(
            arm=arm,
            case_id="case",
            api_keys={"xAI": "x", "OpenAI": "o"},
            xai_model_metadata=xai_metadata(),
            ledger=ledger,
            response_dir=tmp_path / "responses",
            post=post,
        )
        pipeline_request(transport, provider="xAI")
        hashes.append(transport.call_events[0]["request_hash"])
    assert len(calls) == 4
    assert len(set(hashes)) == 4
    again = evaluation.ResearchTransport(
        arm="A",
        case_id="case",
        api_keys={"xAI": "x", "OpenAI": "o"},
        xai_model_metadata=xai_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "responses",
        post=post,
    )
    pipeline_request(again, provider="xAI")
    assert len(calls) == 4
    assert again.call_events[0]["cache_hit"] is True


def test_corpus_selection_is_deterministic_and_capped() -> None:
    config = evaluation.production_config()
    evidence = {"logical_research_corpus_path": "corpus", "files": {}}
    challenges = [
        fixture_case(f"challenge-{index}", "challenge:fixture") for index in range(3)
    ]
    prospective = [fixture_case(f"prospective-{index}") for index in range(200)]
    first, first_skips = evaluation.select_cases(
        challenge_cases=challenges,
        prospective_cases=prospective,
        frozen_corpus_hash="d" * 64,
        config=config,
        evidence_identity=evidence,
    )
    second, second_skips = evaluation.select_cases(
        challenge_cases=list(reversed(challenges)),
        prospective_cases=list(reversed(prospective)),
        frozen_corpus_hash="d" * 64,
        config=config,
        evidence_identity=evidence,
    )
    assert len(first) == evaluation.MAX_CASES
    assert [row["case_id"] for row in first] == [row["case_id"] for row in second]
    assert first_skips == second_skips
    assert {row["case_id"] for row in challenges} <= {
        row["case_id"] for row in first
    }
    assert all(set(row["arm_execution_order"]) == set(evaluation.ARMS) for row in first)


def test_invalid_prospective_rows_are_skipped_without_guessed_fields() -> None:
    row = prospective_row()
    del row["root_post_id"]
    case, skipped = evaluation.prospective_case_from_row(row)
    assert case is None
    assert skipped["reason_code"] == "missing_authoritative_identity"

    row = prospective_row()
    row["path_turns"][-1]["lane"] = "other conversational lane"
    case, skipped = evaluation.prospective_case_from_row(row)
    assert case is None
    assert skipped["reason_code"] == "lane_unavailable"

    case, skipped = evaluation.prospective_case_from_row(prospective_row())
    assert skipped is None
    assert case["context"]["target_id"] == "user-1"
    assert case["context"]["thread_id"] == "root-1"
    assert case["context"]["current_date"] == "2026-08-25"
    assert case["context"]["parent_thread"] == [
        {"post_id": "root-1", "author_role": "account", "text": "A retained account post."}
    ]


def test_image_dependent_prospective_rows_are_excluded() -> None:
    case, skipped = evaluation.prospective_case_from_row(prospective_row(image=True))
    assert case is None
    assert skipped["reason_code"] == "image_dependent"


def test_blind_outputs_do_not_leak_models_efforts_or_arm_key() -> None:
    case = fixture_case("blind-case")
    case["selection_index"] = 0
    result_rows = []
    for arm in evaluation.ARMS:
        result_rows.append(
            {
                "case_id": case["case_id"],
                "arm": arm,
                "execution_status": "completed",
                "final_status": "approved" if arm != "D" else "no_reply",
                "final_public_reply": f"Public outcome {arm}" if arm != "D" else None,
            }
        )
    arm_key = {"A": "Y", "B": "W", "C": "Z", "D": "X"}
    rows = evaluation.build_blind_rows(
        cases=[case], results=result_rows, arm_key=arm_key
    )
    rendered = evaluation.render_blind_review(rows)
    serialised = json.dumps(rows, sort_keys=True)
    for secret in ("grok-4.3", "grok-4.6", "gpt-5.6-sol", "reasoning_effort"):
        assert secret not in rendered
        assert secret not in serialised
    assert "arm_key" not in rendered
    assert "arm_key" not in serialised
    assert "[NO REPLY]" in rendered
    assert set(rows[0]["outcomes"]) == set(evaluation.BLIND_LABELS)


def test_completed_blind_scores_decode_fixed_status_false_replies(
    tmp_path: Path,
) -> None:
    case = fixture_case("fixed-no-reply", source="challenge:tested_pipeline_regression")
    case["expected_public_outcome"] = {
        "kind": "fixed_status",
        "allowed_statuses": ["no_reply"],
    }
    results = [
        {
            "case_id": case["case_id"],
            "arm": arm,
            "execution_status": "completed",
            "final_public_reply": "A reply." if arm == "A" else None,
        }
        for arm in evaluation.ARMS
    ]
    scores = tmp_path / "blind_scores.csv"
    evaluation.atomic_text(
        scores,
        "case_id,W_rating,X_rating,Y_rating,Z_rating,preferred_outcome,notes\n"
        "fixed-no-reply,acceptable,acceptable,acceptable,acceptable,W,\n",
    )
    decoded = evaluation.load_completed_scores(
        scores_path=scores,
        cases=[case],
        results=results,
        arm_key={"A": "W", "B": "X", "C": "Y", "D": "Z"},
    )
    assert decoded["arms"]["A"]["false_replies"] == 1
    assert all(
        decoded["arms"][arm]["false_replies"] == 0
        for arm in ("B", "C", "D")
    )


def test_live_execution_flag_is_required_for_any_provider_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"live": False}
    manifest = {
        "case_set_sha256": "e" * 64,
        "case_counts": {"prospective": 0, "challenge": 0, "skipped": 0, "total": 0},
    }

    monkeypatch.setattr(evaluation, "ensure_private_output_dir", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(
        evaluation,
        "prepare_experiment",
        lambda **_kwargs: (manifest, [], []),
    )
    monkeypatch.setattr(evaluation, "load_arm_results", lambda _path: [])
    monkeypatch.setattr(
        evaluation,
        "update_manifest_execution",
        lambda *_args, **_kwargs: manifest,
    )

    def fake_live(**_kwargs):
        called["live"] = True
        raise AssertionError("offline preparation contacted a provider")

    monkeypatch.setattr(evaluation, "execute_experiment", fake_live)
    monkeypatch.setattr(
        evaluation,
        "generate_reports",
        lambda **_kwargs: {
            "selected_case_counts": manifest["case_counts"],
            "case_set_sha256": manifest["case_set_sha256"],
        },
    )
    assert evaluation.main(["--review-pack", str(tmp_path / "pack")]) == 0
    assert called["live"] is False
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
    ],
)
def test_non_provider_and_x_twitter_hosts_are_rejected(url: str) -> None:
    with pytest.raises(evaluation.EvaluationError, match="allowlist|permitted"):
        evaluation.validate_provider_url(url)
    assert evaluation.validate_provider_url(
        "https://api.x.ai/v1/chat/completions", expected_host="api.x.ai"
    )
    assert evaluation.validate_provider_url(
        "https://api.openai.com/v1/chat/completions", expected_host="api.openai.com"
    )


def test_ambiguous_transmitted_request_is_never_silently_retried(
    tmp_path: Path,
) -> None:
    calls = 0

    def post(_url, **_kwargs):
        nonlocal calls
        calls += 1
        raise requests.Timeout("outcome unknown")

    ledger = evaluation.RequestLedger(
        tmp_path / "ledger.json", case_set_sha256="f" * 64
    )
    first = evaluation.ResearchTransport(
        arm="A",
        case_id="case",
        api_keys={"xAI": "x", "OpenAI": "o"},
        xai_model_metadata=xai_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "responses",
        post=post,
    )
    with pytest.raises(evaluation.AmbiguousRequestError):
        pipeline_request(first, provider="xAI")
    assert calls == 1
    assert ledger.data["blocked"] is True

    second = evaluation.ResearchTransport(
        arm="A",
        case_id="case",
        api_keys={"xAI": "x", "OpenAI": "o"},
        xai_model_metadata=xai_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "responses",
        post=post,
    )
    with pytest.raises(evaluation.AmbiguousRequestError):
        pipeline_request(second, provider="xAI")
    assert calls == 1


def test_tool_cannot_select_a_production_or_worktree_output_path() -> None:
    with pytest.raises(evaluation.EvaluationError, match="production checkout"):
        evaluation.ensure_private_output_dir(
            evaluation.PRODUCTION_CHECKOUT / "experiment",
            project_dir=evaluation.PROJECT_ROOT,
        )
    with pytest.raises(evaluation.EvaluationError, match="evaluation worktree"):
        evaluation.ensure_private_output_dir(
            evaluation.PROJECT_ROOT / "private-output",
            project_dir=evaluation.PROJECT_ROOT,
        )
    with pytest.raises(evaluation.EvaluationError, match="unsafe|escapes"):
        evaluation.private_path(evaluation.DEFAULT_OUTPUT_ROOT, "../production.txt")
    source = Path(evaluation.__file__).read_text(encoding="utf-8")
    assert "import mrsMThatcher2" not in source
    assert "import tweepy" not in source
