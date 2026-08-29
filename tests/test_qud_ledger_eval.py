from __future__ import annotations

import ast
import copy
import csv
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from tools import run_qud_ledger_eval as evaluation


class FakeResponse:
    """Small synchronous provider response for network-free transport tests."""

    def __init__(self, raw: dict, status_code: int = 200) -> None:
        """Retain one isolated JSON response document."""
        self.raw = copy.deepcopy(raw)
        self.status_code = status_code
        self.content = json.dumps(raw, sort_keys=True).encode()
        self.text = self.content.decode()

    def json(self) -> dict:
        """Return a fresh response document."""
        return copy.deepcopy(self.raw)


class RecordingClient:
    """Duck-typed canonical client which records structured requests."""

    def __init__(self, response: object | None = None) -> None:
        """Use one fixed structured response for every request."""
        self.response = response if response is not None else {"outcome": "pass"}
        self.calls: list[dict] = []

    def request(self, **kwargs):
        """Record a request and return a cache-shaped event."""
        self.calls.append(copy.deepcopy(kwargs))
        return copy.deepcopy(self.response), request_event(
            stage=str(kwargs["stage"]),
            request_hash=evaluation.sha256_value(kwargs),
        )


def xai_metadata() -> dict[str, dict[str, int | str]]:
    """Return positive authenticated-style prices for both xAI models."""
    return {
        model: {
            "id": model,
            "prompt_text_token_price": 20_000,
            "cached_prompt_text_token_price": 2_000,
            "completion_text_token_price": 100_000,
        }
        for model in (evaluation.PRODUCTION_XAI_MODEL, evaluation.ISSUE_MODEL)
    }


def raw_xai_response(content: str = '{"outcome":"pass"}') -> dict:
    """Return one complete xAI chat-completion response with billed cost."""
    return {
        "id": "response-xai",
        "model": evaluation.PRODUCTION_XAI_MODEL,
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 2},
            "cost_in_usd_ticks": 1_000_000,
        },
    }


def strict_outcome_schema() -> dict:
    """Return a minimal strict schema for transport-isolation tests."""
    return {
        "type": "object",
        "properties": {"outcome": {"type": "string"}},
        "required": ["outcome"],
        "additionalProperties": False,
    }


def request_event(*, stage: str, request_hash: str = "a" * 64) -> dict:
    """Return a complete zero-cost request-consumer event."""
    return {
        "request_hash": request_hash,
        "stage": stage,
        "logical_provider": "OpenAI",
        "logical_model": evaluation.PRODUCTION_OPENAI_MODEL,
        "logical_reasoning_effort": evaluation.PRODUCTION_OPENAI_EFFORT,
        "effective_provider": "OpenAI",
        "effective_model": evaluation.PRODUCTION_OPENAI_MODEL,
        "effective_reasoning_effort": evaluation.PRODUCTION_OPENAI_EFFORT,
        "provider": "OpenAI",
        "cache_hit": False,
        "cache_source": None,
        "network_request": False,
        "provider_error": None,
        "request_status": "completed",
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "provider_reported_cost_usd": None,
        "estimated_cost_usd": 0.0,
        "provider_latency_seconds": 0.0,
    }


def visible_summary(*, photos: int = 0) -> dict:
    """Return the retained visual summary fields used by candidate filtering."""
    return {"native_photo_count_max": photos}


def prospective_row() -> dict:
    """Return an exact synthetic Liberty-style branch for unit tests only."""
    turns = [
        {
            "post_id": "account-root",
            "parent_post_id": None,
            "author_role": "account",
            "author_key": "account",
            "text": "Liberty is worth defending in every sphere.",
            "lane": "quote_image",
            "created_at": "2026-08-25T09:00:00Z",
            "first_observed_at": "2026-08-25T09:00:01Z",
            "account_turn_asked_for_clarification": False,
            "reply_visual_context_summary": visible_summary(),
        },
        {
            "post_id": "user-question",
            "parent_post_id": "account-root",
            "author_role": "user",
            "author_key": "principal",
            "text": "Is economic liberty necessary for non-economic liberty?",
            "lane": "mention",
            "created_at": "2026-08-25T09:01:00Z",
            "first_observed_at": "2026-08-25T09:01:01Z",
            "account_turn_asked_for_clarification": False,
            "reply_visual_context_summary": visible_summary(),
        },
        {
            "post_id": "account-substitute",
            "parent_post_id": "user-question",
            "author_role": "account",
            "author_key": "account",
            "text": "Security and national independence are also essential.",
            "lane": "mention",
            "created_at": "2026-08-25T09:02:00Z",
            "first_observed_at": "2026-08-25T09:02:01Z",
            "account_turn_asked_for_clarification": False,
            "reply_visual_context_summary": visible_summary(),
        },
        {
            "post_id": "user-target",
            "parent_post_id": "account-substitute",
            "author_role": "user",
            "author_key": "principal",
            "text": (
                "I am not asking about security or independence. Is economic "
                "liberty necessary for non-economic liberty?"
            ),
            "lane": "mention",
            "created_at": "2026-08-25T09:03:00Z",
            "first_observed_at": "2026-08-25T09:03:01Z",
            "account_turn_asked_for_clarification": False,
            "reply_visual_context_summary": visible_summary(),
        },
    ]
    return {
        "schema_version": 4,
        "candidate_key": "candidate-liberty",
        "branch_key": "branch-liberty",
        "principal_author_key": "principal",
        "root_post_id": "account-root",
        "warnings": [],
        "review_reason_codes": ["explicit_correction_cue"],
        "reply_visual_context_summaries": [],
        "sibling_context_refs": [],
        "handoff_context_refs": [],
        "path_turns": turns,
    }


def review_pack_identity() -> dict:
    """Return a compact immutable source identity for candidate unit tests."""
    return {
        "path": "/private/review-pack",
        "name": "unit-pack",
        "manifest_sha256": "a" * 64,
        "pack_content_sha256": "b" * 64,
        "include_open": True,
    }


def candidate_from_row(row: dict | None = None) -> dict:
    """Build and assert one usable synthetic candidate."""
    candidate, skip = evaluation.prospective_candidate_from_row(
        copy.deepcopy(row or prospective_row()),
        source_identity="source-fixture",
        review_pack_identity=review_pack_identity(),
    )
    assert skip is None
    assert candidate is not None
    return candidate


def issue_state(*, confidence: str = "high", status: str = "open") -> dict:
    """Return a valid issue state grounded in the Liberty fixture."""
    return {
        "status": "issue_found",
        "live_issue": {
            "issue_id": "I1",
            "question_under_discussion": (
                "Is economic liberty necessary for non-economic liberty?"
            ),
            "raised_by_turn_ids": ["user-question", "user-target"],
            "user_stance": {
                "status": "questioned",
                "canonical_text": (
                    "Economic liberty may be necessary for non-economic liberty."
                ),
                "source_turn_ids": ["user-question", "user-target"],
            },
            "signature": {
                "subject": "economic liberty",
                "relation": "requires",
                "object": "non-economic liberty",
                "polarity": "questioned",
                "modality": "necessity",
                "scope": "qualified",
                "kind": "conceptual",
            },
            "status": status,
            "confidence": confidence,
        },
        "rejected_answer_targets": [
            {
                "account_turn_ids": ["account-substitute"],
                "answered_question": "Are security and independence important?",
                "rejected_by_turn_ids": ["user-target"],
                "repair_type": "not_the_question",
                "status": "deprecated_as_answer_target",
                "confidence": "high",
            }
        ],
        "source_turn_ids": [
            "user-question",
            "account-substitute",
            "user-target",
        ],
    }


def critic_result(
    *,
    addresses: str = "substitute",
    relation: str = "compatible",
    confidence: str = "high",
    explicit_repair: bool = True,
    revives: bool = True,
) -> dict:
    """Return a strict synthetic answerhood certificate."""
    differences = (
        ["relation_changed", "live_issue_not_answered"]
        if addresses in {"substitute", "unrelated"}
        else ["none"]
    )
    return {
        "candidate_answered_question": "Are security and independence important?",
        "candidate_proposition": "Security and independence are important.",
        "addresses_live_issue": addresses,
        "logical_relation_to_user_stance": relation,
        "explicit_repair_present": explicit_repair,
        "revives_rejected_answer_target": revives,
        "alignment_differences": differences,
        "evidence_turn_ids": ["account-substitute", "user-target"],
        "confidence": confidence,
    }


def base_result_row(
    *, arm: str = "A", candidate: str | None = "Baseline reply."
) -> dict:
    """Return one minimal completed result row used for arm invariants."""
    digest = (
        evaluation.sha256_bytes(candidate.encode())
        if isinstance(candidate, str)
        else None
    )
    return {
        "schema_version": 1,
        "case_id": "qud:branch-liberty:user-target",
        "source_kind": "prospective",
        "arm": arm,
        "execution_status": "completed",
        "issue_transcript_sha256": "1" * 64,
        "production_context_sha256": "2" * 64,
        "trusted_facts_sha256": "3" * 64,
        "recent_replies_sha256": "4" * 64,
        "baseline_routing_outcome": {"reply_requirement": "optional"},
        "routing_result_sha256": "5" * 64,
        "routing_request_hashes": [
            {"stage": "candidate_backed_engagement", "request_hash": "6" * 64}
        ],
        "baseline_reached_writer": True,
        "pipeline_stage_status": "approved",
        "pipeline_stage_reason": "approved",
        "final_status": "approved_for_publication" if candidate else "no_reply",
        "final_reason": "fixture",
        "initial_public_candidate": candidate,
        "final_public_candidate": candidate,
        "starting_candidate_sha256": digest,
        "final_public_candidate_sha256": digest,
        "critic_type": None,
        "critic_result": None,
        "broad_substitution_flag": False,
        "narrow_gate_trigger": False,
        "repair_attempts": 0,
        "repair_outcome": None,
        "direct_answer_repair": None,
        "deterministic_validation_results": [],
        "claim_audit_and_cleanup_results": [],
        "schema_failures": [],
        "provider_failures": [],
        "ambiguous_call_failures": [],
        "model_call_count": 0,
        "revision_count": 0,
        "requests": [],
        "usage_and_cost": evaluation._event_totals([]),
        "arm_latency_seconds": 0.1,
        "completed_at": "2026-08-29T00:00:00Z",
    }


def test_only_two_new_research_files_and_no_production_module_changed() -> None:
    """The branch must keep every protected production source byte-identical."""
    process = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "origin/master",
            "--",
            *sorted(evaluation.PROTECTED_PRODUCTION_PATHS),
            "*.service",
            "*.timer",
        ],
        cwd=evaluation.PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert process.stdout == ""
    uncommitted = subprocess.run(
        ["git", "status", "--short"],
        cwd=evaluation.PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert {
        line[3:] for line in uncommitted if line.startswith("?? ")
    } <= {
        "tools/run_qud_ledger_eval.py",
        "tests/test_qud_ledger_eval.py",
    }


def test_runner_imports_no_x_client_and_rejects_production_output_paths(
    tmp_path: Path,
) -> None:
    """The isolated tool has neither an X import nor a production write root."""
    source = Path(evaluation.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        str(node.module)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        name.startswith(("tweepy", "twitter", "mrsMThatcher2"))
        for name in imports
    )
    with pytest.raises(evaluation.EvaluationError, match="inside production"):
        evaluation.ensure_private_output_dir(
            evaluation.PRODUCTION_CHECKOUT / "research-output",
            project_dir=evaluation.PROJECT_ROOT,
        )
    with pytest.raises(evaluation.EvaluationError, match="must be beneath"):
        evaluation.ensure_private_output_dir(
            tmp_path / "elsewhere", project_dir=evaluation.PROJECT_ROOT
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/i/api/graphql",
        "https://api.twitter.com/2/tweets/search/recent",
        "https://example.com/v1/chat/completions",
        "http://api.x.ai/v1/chat/completions",
        "https://api.x.ai/v1/chat/completions?search=x",
        "https://api.openai.com/v1/responses",
    ],
)
def test_only_exact_provider_hosts_and_paths_are_allowed(url: str) -> None:
    """No X, Twitter, search, redirect, or arbitrary provider path is allowed."""
    with pytest.raises(evaluation.EvaluationError):
        evaluation.validate_provider_url(url)
    assert evaluation.validate_provider_url(
        "https://api.x.ai/v1/chat/completions", expected_host="api.x.ai"
    )
    assert evaluation.validate_provider_url(
        "https://api.openai.com/v1/chat/completions",
        expected_host="api.openai.com",
    )


def test_live_execution_flag_is_required_before_any_provider_work(tmp_path: Path) -> None:
    """The executor fails before reading repository or provider state without the flag."""
    with pytest.raises(evaluation.EvaluationError, match="--execute-live-models"):
        evaluation.execute_experiment(
            execute_live_models=False,
            project_dir=evaluation.PROJECT_ROOT,
            output_dir=tmp_path,
            manifest={},
            cases=[],
            api_keys={},
        )


def test_cost_reservation_stops_before_the_ceiling(tmp_path: Path) -> None:
    """A request whose reservation would cross the limit is never added."""
    ledger = evaluation.QudRequestLedger(
        tmp_path / "ledger.json", case_set_sha256="a" * 64, hard_limit_usd=0.000001
    )
    limit = ledger.limit_ticks
    ledger.reserve(
        request_hash="1" * 64,
        provider="xAI",
        stage="first",
        logical_model=evaluation.ISSUE_MODEL,
        logical_effort=evaluation.ISSUE_EFFORT,
        effective_model=evaluation.ISSUE_MODEL,
        effective_effort=evaluation.ISSUE_EFFORT,
        maximum_possible_cost_ticks=limit,
        consumer_id="case:B:1:first",
    )
    with pytest.raises(evaluation.CostLimitReached):
        ledger.reserve(
            request_hash="2" * 64,
            provider="xAI",
            stage="second",
            logical_model=evaluation.ISSUE_MODEL,
            logical_effort=evaluation.ISSUE_EFFORT,
            effective_model=evaluation.ISSUE_MODEL,
            effective_effort=evaluation.ISSUE_EFFORT,
            maximum_possible_cost_ticks=1,
            consumer_id="case:B:2:second",
        )
    assert len(ledger.data["operations"]) == 1


def test_ambiguous_transmission_is_durable_and_never_retried(tmp_path: Path) -> None:
    """A timeout remains ambiguous, blocks resumption, and causes one send only."""
    calls = 0

    def timeout_post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise requests.Timeout("unknown transmission outcome")

    ledger = evaluation.QudRequestLedger(
        tmp_path / "ledger.json", case_set_sha256="b" * 64
    )
    response_dir = tmp_path / "responses"
    response_dir.mkdir()
    kwargs = {
        "provider": "xAI",
        "stage": "answerhood_transcript_only_initial",
        "model": evaluation.CRITIC_MODEL,
        "reasoning_effort": evaluation.CRITIC_EFFORT,
        "system_prompt": "Prompt",
        "payload": {"case": "same"},
        "response_schema": strict_outcome_schema(),
        "timeout_seconds": 180,
        "max_output_tokens": 100,
    }
    for _ in range(2):
        client = evaluation.CanonicalProviderClient(
            arm="B",
            case_id="ambiguous-case",
            ledger=ledger,
            response_dir=response_dir,
            api_keys={"xAI": "not-a-real-key", "OpenAI": "not-a-real-key"},
            xai_model_metadata=xai_metadata(),
            post=timeout_post,
            sleep=lambda _seconds: None,
        )
        with pytest.raises(evaluation.AmbiguousRequestError):
            client.request(**kwargs)
    assert calls == 1
    assert ledger.data["blocked"] is True
    assert [row["status"] for row in ledger.data["operations"]] == ["ambiguous"]


def test_identical_ordinary_request_is_sent_once_and_shared_across_arms(
    tmp_path: Path,
) -> None:
    """Canonical caching samples one ordinary routing request for A and C."""
    calls = 0

    def post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse(raw_xai_response())

    ledger = evaluation.QudRequestLedger(
        tmp_path / "ledger.json", case_set_sha256="c" * 64
    )
    response_dir = tmp_path / "responses"
    response_dir.mkdir()
    events = []
    for arm in ("A", "C"):
        client = evaluation.CanonicalProviderClient(
            arm=arm,
            case_id="shared-case",
            ledger=ledger,
            response_dir=response_dir,
            api_keys={"xAI": "not-a-real-key", "OpenAI": "not-a-real-key"},
            xai_model_metadata=xai_metadata(),
            post=post,
            sleep=lambda _seconds: None,
        )
        content, event = client.request(
            provider="xAI",
            stage="candidate_backed_engagement",
            model=evaluation.PRODUCTION_XAI_MODEL,
            reasoning_effort=evaluation.PRODUCTION_XAI_EFFORT,
            system_prompt="Frozen production gate prompt",
            payload={"same": True},
            response_schema=strict_outcome_schema(),
            timeout_seconds=180,
            max_output_tokens=100,
        )
        assert content == '{"outcome":"pass"}'
        events.append(event)
    assert calls == 1
    assert events[0]["network_request"] is True
    assert events[1]["cache_hit"] is True
    assert events[0]["request_hash"] == events[1]["request_hash"]
    assert len(ledger.data["operations"]) == 1


def test_candidate_pool_is_frozen_deduplicated_and_deterministic() -> None:
    """Priority-band hash ordering does not depend on discovery order."""
    first = candidate_from_row()
    second_row = prospective_row()
    second_row["candidate_key"] = "candidate-second"
    second_row["branch_key"] = "branch-second"
    for turn in second_row["path_turns"]:
        turn["post_id"] = "second-" + str(turn["post_id"])
        if turn["parent_post_id"] is not None:
            turn["parent_post_id"] = "second-" + str(turn["parent_post_id"])
    second = candidate_from_row(second_row)
    selected_one, skipped_one = evaluation.select_candidate_pool(
        [first, second, copy.deepcopy(first)], frozen_source_identity="frozen"
    )
    selected_two, skipped_two = evaluation.select_candidate_pool(
        [copy.deepcopy(second), copy.deepcopy(first)], frozen_source_identity="frozen"
    )
    assert skipped_one == skipped_two == []
    assert [row["case_id"] for row in selected_one] == [
        row["case_id"] for row in selected_two
    ]
    assert evaluation.sha256_value(selected_one) == evaluation.sha256_value(
        selected_two
    )
    assert len(selected_one) == 2
    assert all(row["selection_index"] == index for index, row in enumerate(selected_one))


def test_future_account_reply_is_excluded_from_both_contexts() -> None:
    """An account reply observed after the target cannot leak into either input."""
    row = prospective_row()
    row["path_turns"].append(
        {
            "post_id": "future-account",
            "parent_post_id": "user-target",
            "author_role": "account",
            "author_key": "account",
            "text": "This reply happened after the experimental target.",
            "lane": "mention",
            "created_at": "2026-08-25T09:04:00Z",
            "first_observed_at": "2026-08-25T09:04:01Z",
            "account_turn_asked_for_clarification": False,
            "reply_visual_context_summary": visible_summary(),
        }
    )
    candidate = candidate_from_row(row)
    assert [turn["turn_id"] for turn in candidate["issue_transcript"]][-1] == (
        "user-target"
    )
    assert "future-account" not in {
        turn["turn_id"] for turn in candidate["issue_transcript"]
    }
    assert "This reply happened" not in json.dumps(candidate["production_context"])
    assert all("after the experimental" not in reply for reply in candidate["recent_replies"])


def test_sibling_and_handoff_context_is_non_authoritative() -> None:
    """Off-path references remain provenance and cannot enter the live transcript."""
    row = prospective_row()
    row["sibling_context_refs"] = [
        {
            "post_id": "sibling-user",
            "author_key": "other-user",
            "text": "A sibling raises an entirely different proposition.",
        }
    ]
    row["handoff_context_refs"] = [
        {
            "post_id": "handoff-user",
            "author_key": "third-user",
            "text": "A hand-off contributor changes the actor and time.",
        }
    ]
    candidate = candidate_from_row(row)
    transcript = json.dumps(candidate["issue_transcript"])
    assert "sibling-user" not in transcript
    assert "handoff-user" not in transcript
    assert {row["context_kind"] for row in candidate["non_authoritative_context_refs"]} == {
        "sibling",
        "handoff",
    }
    assert all(
        row["authoritative_for_live_issue"] is False
        for row in candidate["non_authoritative_context_refs"]
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("mixed_author", "mixed_principal_authors"),
        ("ambiguous_parent", "ambiguous_parentage"),
        ("image", "image_dependent"),
    ],
)
def test_mixed_author_ambiguous_parent_and_image_cases_are_skipped(
    mutation: str, reason: str
) -> None:
    """Invalid exact paths fail with explicit unusable-case reasons."""
    row = prospective_row()
    if mutation == "mixed_author":
        row["path_turns"][1]["author_key"] = "other-contributor"
    elif mutation == "ambiguous_parent":
        row["path_turns"][-1]["parent_post_id"] = "unknown-parent"
    else:
        row["path_turns"][-1]["reply_visual_context_summary"] = visible_summary(
            photos=1
        )
        row["reply_visual_context_summaries"] = [
            {"post_id": "user-target", "native_photo_count_max": 1}
        ]
    candidate, skip = evaluation.prospective_candidate_from_row(
        row,
        source_identity="fixture",
        review_pack_identity=review_pack_identity(),
    )
    assert candidate is None
    assert skip is not None
    assert skip["reason_code"] == reason


def test_more_than_twenty_path_turns_is_skipped_without_truncation() -> None:
    """An overlong issue path is excluded rather than silently shortened."""
    row = prospective_row()
    turns = [copy.deepcopy(row["path_turns"][0])]
    parent = "account-root"
    for index in range(1, 22):
        role = "user" if index % 2 else "account"
        post_id = f"long-{index}"
        turns.append(
            {
                "post_id": post_id,
                "parent_post_id": parent,
                "author_role": role,
                "author_key": "principal" if role == "user" else "account",
                "text": (
                    f"Substantive contributor question number {index}?"
                    if role == "user"
                    else f"Substantive account answer number {index}."
                ),
                "lane": "mention",
                "created_at": f"2026-08-25T09:{index:02d}:00Z",
                "first_observed_at": f"2026-08-25T09:{index:02d}:01Z",
                "account_turn_asked_for_clarification": False,
                "reply_visual_context_summary": visible_summary(),
            }
        )
        parent = post_id
    row["path_turns"] = turns
    candidate, skip = evaluation.prospective_candidate_from_row(
        row,
        source_identity="fixture",
        review_pack_identity=review_pack_identity(),
    )
    assert candidate is None
    assert skip is not None
    assert skip["reason_code"] == "path_over_20_turns"


def test_liberty_case_is_prioritised_without_assigning_ground_truth() -> None:
    """The requested exact text pattern is a selection signal, never a label."""
    candidate = candidate_from_row()
    assert candidate["trigger_reason_codes"][0] == (
        "requested_liberty_conversation_match"
    )
    assert candidate["historical_labels"] == []
    assert candidate["target_turn_id"] == "user-target"


def test_all_issue_and_critic_evidence_ids_must_exist_in_branch() -> None:
    """Structured model output cannot cite a fabricated or sibling turn ID."""
    valid_ids = {
        turn["turn_id"] for turn in candidate_from_row()["issue_transcript"]
    }
    assert evaluation.validate_issue_state(
        issue_state(), valid_turn_ids=valid_ids
    ) == issue_state()
    assert evaluation.validate_answerhood_critic(
        critic_result(), valid_turn_ids=valid_ids
    ) == critic_result()
    bad_state = issue_state()
    bad_state["source_turn_ids"].append("sibling-user")
    with pytest.raises(ValueError, match="unknown turn IDs"):
        evaluation.validate_issue_state(bad_state, valid_turn_ids=valid_ids)
    bad_critic = critic_result()
    bad_critic["evidence_turn_ids"] = ["invented-turn"]
    with pytest.raises(ValueError, match="unknown turn IDs"):
        evaluation.validate_answerhood_critic(
            bad_critic, valid_turn_ids=valid_ids
        )


def test_issue_prompt_forbids_silence_as_concession_and_rejected_target_withdrawal() -> None:
    """The extractor contract preserves silence and explicit answer-target repair."""
    prompt = evaluation.ISSUE_STATE_PROMPT.casefold()
    assert "do not treat silence as concession" in prompt
    assert "rejects x as the answer target" in prompt
    assert "not withdrawal of a proposition owned by the user" in prompt
    assert "do not import a sibling contributor's issue" in prompt
    assert "private beliefs" in prompt


def test_issue_state_separates_live_question_from_contributor_stance() -> None:
    """Question-under-discussion and logical stance are distinct strict fields."""
    state = issue_state()
    live = state["live_issue"]
    assert live["question_under_discussion"].endswith("?")
    assert live["user_stance"]["canonical_text"].endswith(".")
    assert live["question_under_discussion"] != live["user_stance"]["canonical_text"]
    schema = evaluation.ISSUE_STATE_SCHEMA
    assert schema["additionalProperties"] is False
    assert schema["properties"]["live_issue"]["anyOf"][0]["additionalProperties"] is False


def test_user_replacement_keeps_only_the_new_live_issue() -> None:
    """A user's own replacement is not encoded as rejection of an account answer."""
    replacement = issue_state()
    replacement["live_issue"]["question_under_discussion"] = (
        "What institutional safeguard could protect non-economic liberty?"
    )
    replacement["live_issue"]["raised_by_turn_ids"] = ["user-target"]
    replacement["live_issue"]["user_stance"] = {
        "status": "questioned",
        "canonical_text": "A safeguard may protect non-economic liberty.",
        "source_turn_ids": ["user-target"],
    }
    replacement["live_issue"]["signature"] = {
        "subject": "institutional safeguard",
        "relation": "enables",
        "object": "non-economic liberty",
        "polarity": "questioned",
        "modality": "possibility",
        "scope": "qualified",
        "kind": "conceptual",
    }
    replacement["rejected_answer_targets"] = []
    replacement["source_turn_ids"] = ["user-target"]
    valid = evaluation.validate_issue_state(
        replacement,
        valid_turn_ids={
            turn["turn_id"] for turn in candidate_from_row()["issue_transcript"]
        },
    )
    assert valid["live_issue"]["status"] == "open"
    assert valid["rejected_answer_targets"] == []
    assert "replacing their own issue" in evaluation.ISSUE_STATE_PROMPT


@pytest.mark.parametrize(
    ("addresses", "relation"),
    [
        ("substitute", "compatible"),
        ("direct", "contradicts"),
        ("elaboration_after_answer", "strengthens"),
    ],
)
def test_answerhood_is_independent_of_logical_relation(
    addresses: str, relation: str
) -> None:
    """Compatible substitution, direct disagreement, and elaboration all validate."""
    value = critic_result(
        addresses=addresses,
        relation=relation,
        explicit_repair=addresses == "substitute",
        revives=addresses == "substitute",
    )
    valid = evaluation.validate_answerhood_critic(
        value,
        valid_turn_ids={
            turn["turn_id"] for turn in candidate_from_row()["issue_transcript"]
        },
    )
    assert valid["addresses_live_issue"] == addresses
    assert valid["logical_relation_to_user_stance"] == relation
    if addresses == "substitute":
        assert evaluation.broad_substitution_flag(valid) is True
    else:
        assert evaluation.broad_substitution_flag(valid) is False


def test_critic_prompt_encodes_the_required_answerhood_distinctions() -> None:
    """The shared critic contract distinguishes relevance, disagreement, and sequence."""
    prompt = evaluation.ANSWERHOOD_CRITIC_PROMPT.casefold()
    required = (
        "judge answerhood",
        "do not judge whether a reply was necessary",
        "contradict the contributor and still directly answer",
        "compatible and relevant yet still substitute",
        "elaboration_after_answer only when the live issue was answered first",
        "asking for clarification is not automatically aligned",
        "use unclear rather than inventing",
    )
    assert all(fragment in prompt for fragment in required)


@pytest.mark.parametrize(
    "critic",
    [
        critic_result(addresses="partial"),
        critic_result(addresses="elaboration_after_answer"),
        critic_result(confidence="medium"),
        critic_result(explicit_repair=False),
        critic_result(revives=False),
        critic_result(addresses="direct", relation="contradicts"),
    ],
)
def test_only_the_exact_narrow_trigger_authorises_repair(critic: dict) -> None:
    """Partial, elaborative, medium-confidence, and first-time nearby material do not gate."""
    valid_ids = {
        turn["turn_id"] for turn in candidate_from_row()["issue_transcript"]
    }
    assert evaluation.narrow_gate_trigger(critic, valid_turn_ids=valid_ids) is False
    assert evaluation.narrow_gate_trigger(
        critic_result(), valid_turn_ids=valid_ids
    ) is True


@pytest.mark.parametrize("confidence", ["low", "medium", "high"])
def test_only_usable_issue_states_can_reach_writer(confidence: str) -> None:
    """Low confidence is blocked; medium and high states may be injected."""
    state = issue_state(confidence=confidence)
    client = RecordingClient()
    if confidence == "low":
        assert evaluation.issue_state_usable(state) is False
        with pytest.raises(evaluation.EvaluationError, match="usable issue state"):
            evaluation.PipelineTransport(
                client=client, inject_issue_state=True, issue_state=state
            )
    else:
        assert evaluation.issue_state_usable(state) is True
        evaluation.PipelineTransport(
            client=client, inject_issue_state=True, issue_state=state
        )
    unclear = {
        "status": "unclear",
        "live_issue": None,
        "rejected_answer_targets": [],
        "source_turn_ids": [],
    }
    assert evaluation.issue_state_usable(unclear) is False


def call_pipeline_transport(
    transport: evaluation.PipelineTransport,
    *,
    stage: str,
    provider: str,
    prompt: str = "Frozen production prompt",
    payload: dict | None = None,
) -> object:
    """Invoke one exact production-identity stage through the QUD transport."""
    model, effort = (
        (
            evaluation.PRODUCTION_OPENAI_MODEL,
            evaluation.PRODUCTION_OPENAI_EFFORT,
        )
        if provider == "OpenAI"
        else (evaluation.PRODUCTION_XAI_MODEL, evaluation.PRODUCTION_XAI_EFFORT)
    )
    return transport(
        provider=provider,
        stage=stage,
        model=model,
        system_prompt=prompt,
        payload=copy.deepcopy(payload or {"context": "unchanged"}),
        response_schema=strict_outcome_schema(),
        timeout_seconds=180,
        max_output_tokens=100,
        reasoning_effort=effort,
    )


def test_issue_state_reaches_only_declared_writer_family_stages() -> None:
    """Routing, reviewers, and every factual auditor retain exact production input."""
    client = RecordingClient()
    transport = evaluation.PipelineTransport(
        client=client, inject_issue_state=True, issue_state=issue_state()
    )
    non_writer_stages = (
        "candidate_backed_engagement",
        "reply_necessity_1",
        "focused_group_review",
        "authentication_review_1",
        "allegation_review_1",
        "narrow_claim_audit",
        "cleanup_claim_audit",
        "diversity_claim_audit",
        "direct_answer_repair_claim_audit",
    )
    for stage in sorted(evaluation.WRITER_STAGES):
        call_pipeline_transport(transport, stage=stage, provider="OpenAI")
    for stage in non_writer_stages:
        provider = "OpenAI" if (
            stage.startswith("reply_necessity_")
            or stage == "focused_group_review"
            or stage.startswith("authentication_review_")
            or stage.startswith("allegation_review_")
        ) else "xAI"
        call_pipeline_transport(transport, stage=stage, provider=provider)
    calls = {str(call["stage"]): call for call in client.calls}
    for stage in evaluation.WRITER_STAGES:
        assert calls[stage]["payload"]["issue_state"] == issue_state()
        assert calls[stage]["system_prompt"].endswith(
            evaluation.ISSUE_STATE_WRITER_INSTRUCTION
        )
    for stage in non_writer_stages:
        assert calls[stage]["payload"] == {"context": "unchanged"}
        assert calls[stage]["system_prompt"] == "Frozen production prompt"


def test_arm_a_transport_is_byte_identical_to_production_request() -> None:
    """Arm A changes no prompt, payload, schema, model, effort, timeout, or bound."""
    client = RecordingClient()
    transport = evaluation.PipelineTransport(client=client)
    prompt = "Exact production system prompt."
    payload = {"context": {"incoming_contribution": "Exact visible text."}}
    schema = strict_outcome_schema()
    transport(
        provider="OpenAI",
        stage="writer_v3_initial",
        model=evaluation.PRODUCTION_OPENAI_MODEL,
        reasoning_effort=evaluation.PRODUCTION_OPENAI_EFFORT,
        system_prompt=prompt,
        payload=copy.deepcopy(payload),
        response_schema=copy.deepcopy(schema),
        timeout_seconds=180,
        max_output_tokens=900,
    )
    assert client.calls == [
        {
            "provider": "OpenAI",
            "stage": "writer_v3_initial",
            "model": evaluation.PRODUCTION_OPENAI_MODEL,
            "reasoning_effort": evaluation.PRODUCTION_OPENAI_EFFORT,
            "system_prompt": prompt,
            "payload": payload,
            "response_schema": schema,
            "timeout_seconds": 180,
            "max_output_tokens": 900,
        }
    ]
    assert transport.call_events[0]["issue_state_injected"] is False
    expected_identity = evaluation.base_harness.canonical_request_identity(
        provider="OpenAI",
        model=evaluation.PRODUCTION_OPENAI_MODEL,
        reasoning_effort=evaluation.PRODUCTION_OPENAI_EFFORT,
        system_prompt=prompt,
        user_payload=payload,
        response_schema=schema,
        maximum_output_tokens=900,
        timeout_seconds=180,
    )
    assert transport.call_events[0][
        "canonical_unmodified_production_request_sha256"
    ] == evaluation.sha256_value(expected_identity)


def test_arm_a_runs_current_pipeline_with_exact_case_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The baseline wrapper calls the current pipeline without an experiment payload."""
    case = candidate_from_row()
    config = evaluation.production_config()
    captured: dict = {}

    def fake_pipeline(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            reply=None,
            status="no_reply",
            reason="fixture",
            model_call_count=0,
            revision_count=0,
            audit=(),
        )

    monkeypatch.setattr(evaluation.tested_pipeline, "run_reply_pipeline", fake_pipeline)
    transport = evaluation.PipelineTransport(client=RecordingClient())
    result = evaluation.run_current_public_pipeline(
        case=case,
        config=config,
        repository=object(),
        transport=transport,
    )
    assert captured["context"] == case["production_context"]
    assert captured["config"] == config
    assert captured["recent_replies"] == case["recent_replies"]
    assert captured["media_context"] is None
    assert captured["maximum_reply_length"] == 270
    assert captured["transport"] is transport
    assert result.final_public_candidate is None


def test_arm_candidate_sharing_and_routing_invariants() -> None:
    """A/B and C/D share their exact pre-gate candidates and A/C share routing."""
    arm_a = base_result_row(arm="A", candidate="Baseline exact candidate.")
    arm_b = copy.deepcopy(arm_a)
    arm_b.update(
        {
            "arm": "B",
            "starting_candidate_sha256": evaluation.sha256_bytes(
                b"Baseline exact candidate."
            ),
        }
    )
    arm_c = base_result_row(arm="C", candidate="Issue-aware exact candidate.")
    arm_d = copy.deepcopy(arm_c)
    arm_d.update(
        {
            "arm": "D",
            "starting_candidate_sha256": evaluation.sha256_bytes(
                b"Issue-aware exact candidate."
            ),
        }
    )
    evaluation.assert_arm_invariants(
        arm_a=arm_a, arm_b=arm_b, arm_c=arm_c, arm_d=arm_d
    )
    arm_d["starting_candidate_sha256"] = "0" * 64
    with pytest.raises(evaluation.EvaluationError, match="Arm C and D"):
        evaluation.assert_arm_invariants(
            arm_a=arm_a, arm_b=arm_b, arm_c=arm_c, arm_d=arm_d
        )


def test_unusable_issue_state_reuses_a_for_c_and_c_for_d() -> None:
    """Unclear extraction produces no alternative sample or fallback B critic."""
    case = candidate_from_row()
    arm_a = base_result_row(arm="A", candidate="Exact baseline.")
    arm_a["case_id"] = case["case_id"]
    arm_c = evaluation.reuse_arm_result(case=case, arm="C", source=arm_a)
    arm_d = evaluation.reuse_arm_result(case=case, arm="D", source=arm_c)
    assert arm_c["reused_exact_outcome_from_arm"] == "A"
    assert arm_d["reused_exact_outcome_from_arm"] == "C"
    assert arm_c["final_public_candidate"] == arm_d["final_public_candidate"]
    assert arm_c["requests"] == arm_d["requests"] == []
    assert arm_d["critic_type"] is None


def test_one_failed_alignment_repair_becomes_no_reply() -> None:
    """A cannot-compose repair is attempted once and fails closed without fallback."""
    case = candidate_from_row()
    certificate = critic_result()
    client = RecordingClient(
        {"status": "cannot_compose_safely", "reply": None}
    )
    repair = evaluation.perform_alignment_repair(
        arm="B",
        case=case,
        original_candidate="Security and independence are what matter.",
        critic=certificate,
        critic_type="transcript_only",
        issue_state=None,
        trusted_facts=[],
        reply_requirement="optional",
        config=evaluation.production_config(),
        repository=object(),
        client=client,
    )
    assert len(client.calls) == 1
    assert repair["success"] is False
    assert repair["repair_attempts"] == 1
    assert repair["final_public_candidate"] is None
    starting = base_result_row(
        arm="A", candidate="Security and independence are what matter."
    )
    starting["case_id"] = case["case_id"]
    row = evaluation.critic_arm_result(
        case=case,
        arm="B",
        starting_row=starting,
        critic_type="transcript_only",
        critic=certificate,
        critic_event=request_event(stage="answerhood_transcript_only_initial"),
        repair=repair,
        wall_latency_seconds=0.2,
    )
    assert row["final_public_candidate"] is None
    assert row["final_reason"] == "alignment_repair_failed"
    assert row["repair_attempts"] == 1
    assert row["final_status"] == "no_reply"


def test_more_than_one_alignment_rewrite_is_rejected() -> None:
    """Result assembly enforces the hard one-rewrite bound."""
    case = candidate_from_row()
    starting = base_result_row(arm="A", candidate="Starting candidate.")
    starting["case_id"] = case["case_id"]
    repair = {
        "success": True,
        "outcome": "alignment_repair_succeeded",
        "final_public_candidate": "Repaired candidate.",
        "repair_attempts": 2,
        "checks": [],
        "requests": [],
    }
    with pytest.raises(evaluation.EvaluationError, match="more than one"):
        evaluation.critic_arm_result(
            case=case,
            arm="B",
            starting_row=starting,
            critic_type="transcript_only",
            critic=critic_result(),
            critic_event=request_event(stage="answerhood_transcript_only_initial"),
            repair=repair,
            wall_latency_seconds=0.1,
        )


def test_transcript_critic_never_receives_ledger_but_ledger_critic_does() -> None:
    """The same critic model gets precisely the declared experimental evidence."""
    case = candidate_from_row()
    certificate = critic_result(addresses="direct", relation="contradicts")
    client = RecordingClient(certificate)
    transcript_result, _event = evaluation.run_answerhood_critic(
        case=case,
        candidate="No, economic liberty is not necessary for every other liberty.",
        critic_type="transcript_only",
        issue_state=None,
        client=client,
    )
    ledger_result, _event = evaluation.run_answerhood_critic(
        case=case,
        candidate="No, economic liberty is not necessary for every other liberty.",
        critic_type="ledger_backed",
        issue_state=issue_state(),
        client=client,
    )
    assert transcript_result == ledger_result == certificate
    assert "issue_state" not in client.calls[0]["payload"]
    assert client.calls[1]["payload"]["issue_state"] == issue_state()
    for call in client.calls:
        assert call["model"] == evaluation.CRITIC_MODEL
        assert call["reasoning_effort"] == evaluation.CRITIC_EFFORT
        assert call["payload"]["issue_transcript"] == case["issue_transcript"]
        assert call["payload"]["target_turn_id"] == case["target_turn_id"]


def test_issue_extraction_record_binds_raw_transcript_hash_and_source_ids() -> None:
    """A model hypothesis is retained beside, not in place of, exact raw turns."""
    case = candidate_from_row()
    state = issue_state()
    client = RecordingClient(state)
    observed, record = evaluation.extract_issue_state(case=case, client=client)
    assert observed == state
    assert record["issue_transcript_sha256"] == evaluation.issue_transcript_hash(
        case["issue_transcript"]
    )
    assert record["source_turn_ids"] == state["source_turn_ids"]
    assert record["rejected_answer_targets"] == state["rejected_answer_targets"]
    assert record["usable_for_writer"] is True
    assert client.calls[0]["payload"] == {
        "issue_transcript": case["issue_transcript"],
        "target_turn_id": case["target_turn_id"],
    }


def test_frozen_production_architecture_and_research_models_are_exact() -> None:
    """The experiment refuses to silently adapt model identity or effort."""
    config = evaluation.production_config()
    assert (
        config["xai_model"],
        config["xai_reasoning_effort"],
        config["openai_model"],
        config["openai_reasoning_effort"],
    ) == ("grok-4.3", "low", "gpt-5.6-sol", "medium")
    assert (
        evaluation.ISSUE_MODEL,
        evaluation.ISSUE_EFFORT,
        evaluation.CRITIC_MODEL,
        evaluation.CRITIC_EFFORT,
        evaluation.REPAIR_MODEL,
        evaluation.REPAIR_EFFORT,
    ) == ("grok-4.6", "low", "grok-4.6", "low", "gpt-5.6-sol", "medium")


def test_repair_prompt_and_payload_are_bounded_and_grounded() -> None:
    """The sole rewrite receives all declared evidence and the frozen constraints."""
    case = candidate_from_row()
    client = RecordingClient(
        {"status": "cannot_compose_safely", "reply": None}
    )
    evaluation.perform_alignment_repair(
        arm="D",
        case=case,
        original_candidate="Security and independence are what matter.",
        critic=critic_result(),
        critic_type="ledger_backed",
        issue_state=issue_state(),
        trusted_facts=[{"fact_id": "fixture", "text": "Grounded fact."}],
        reply_requirement="optional",
        config=evaluation.production_config(),
        repository=object(),
        client=client,
    )
    assert len(client.calls) == 1
    request = client.calls[0]
    assert request["provider"] == "OpenAI"
    assert request["model"] == "gpt-5.6-sol"
    assert request["reasoning_effort"] == "medium"
    assert request["response_schema"] == evaluation.tested_pipeline.WRITER_SCHEMA
    assert set(request["payload"]) == {
        "issue_transcript",
        "production_context",
        "trusted_facts",
        "recent_replies",
        "original_candidate",
        "critic_certificate",
        "issue_state",
    }
    prompt = request["system_prompt"].casefold()
    for fragment in (
        "first sentence",
        "disagreement is allowed",
        "merely nearby proposition",
        "trusted_facts",
        "one or two short sentences",
        "270 characters",
        "no emoji",
        "cannot_compose_safely",
    ):
        assert fragment in prompt


def test_blind_output_contains_only_exact_branch_and_public_outcomes() -> None:
    """Blind material leaks no arm definition, model, diagnostics, or key."""
    case = candidate_from_row()
    results = []
    for arm, reply in zip(evaluation.ARMS, ("Reply A.", "Reply B.", None, "Reply D.")):
        row = base_result_row(arm=arm, candidate=reply)
        row["case_id"] = case["case_id"]
        row["critic_result"] = critic_result() if arm in {"B", "D"} else None
        results.append(row)
    arm_key = {"A": "Z", "B": "W", "C": "Y", "D": "X"}
    rows = evaluation.build_blind_rows(
        cases=[case], results=results, arm_key=arm_key
    )
    markdown = evaluation.render_blind_review(rows)
    evaluation._validate_blind_outputs(rows=rows, markdown=markdown)
    serialised = json.dumps(rows, sort_keys=True)
    assert set(rows[0]) == {
        "case_id",
        "exact_visible_principal_author_branch",
        "public_outcomes",
    }
    assert rows[0]["public_outcomes"] == {
        "W": "Reply B.",
        "X": "Reply D.",
        "Y": "[NO REPLY]",
        "Z": "Reply A.",
    }
    for forbidden in (
        "Arm A",
        "Arm B",
        "grok-",
        "gpt-",
        "issue_state",
        "critic_result",
        "narrow_gate",
        "provider",
        "route_reason",
        '"A": "Z"',
    ):
        assert forbidden.casefold() not in (markdown + serialised).casefold()


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    """Write a deterministic temporary CSV used only by unit tests."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_score_reporting_decodes_the_global_four_arm_mapping(tmp_path: Path) -> None:
    """Blind ratings become the correct A/B/C/D paired descriptive counts."""
    case = candidate_from_row()
    case_id = str(case["case_id"])
    arm_key = {"A": "W", "B": "X", "C": "Y", "D": "Z"}
    blind_fields = [
        "case_id",
        "W_rating",
        "W_substitution",
        "X_rating",
        "X_substitution",
        "Y_rating",
        "Y_substitution",
        "Z_rating",
        "Z_substitution",
        "preferred_outcome",
        "notes",
    ]
    write_csv(
        tmp_path / "blind.csv",
        blind_fields,
        [
            {
                "case_id": case_id,
                "W_rating": "unacceptable",
                "W_substitution": "yes",
                "X_rating": "acceptable",
                "X_substitution": "no",
                "Y_rating": "minor",
                "Y_substitution": "no",
                "Z_rating": "unacceptable",
                "Z_substitution": "no",
                "preferred_outcome": "X",
                "notes": "fixture",
            }
        ],
    )
    diagnostic_fields = [
        "case_id",
        "issue_state_rating",
        "user_stance_rating",
        "rejected_target_rating",
        "transcript_critic_rating",
        "ledger_critic_rating",
        "notes",
    ]
    write_csv(
        tmp_path / "diagnostic.csv",
        diagnostic_fields,
        [{field: case_id if field == "case_id" else "" for field in diagnostic_fields}],
    )
    results = []
    for arm, reply in {
        "A": "Substituting baseline.",
        "B": "Direct repair.",
        "C": "Direct issue-aware reply.",
        "D": None,
    }.items():
        row = base_result_row(arm=arm, candidate=reply)
        row["case_id"] = case_id
        if arm == "B":
            row["repair_outcome"] = "alignment_repair_succeeded"
        if arm == "D":
            row["repair_outcome"] = "alignment_repair_failed"
        results.append(row)
    report = evaluation.load_completed_scores(
        scores_path=tmp_path / "blind.csv",
        diagnostic_path=tmp_path / "diagnostic.csv",
        cases=[case],
        results=results,
        issue_rows=[],
        arm_key=arm_key,
    )
    assert report["arms"]["A"]["unacceptable"] == 1
    assert report["arms"]["A"]["substitution_yes"] == 1
    assert report["arms"]["B"]["acceptable"] == 1
    assert report["arms"]["B"]["wins_against_A"] == 1
    assert report["arms"]["B"]["repaired_substitutions"] == 1
    assert report["arms"]["D"]["no_reply_from_failed_repair"] == 1
    assert report["preferred_outcome_totals"]["B"] == 1
    assert report["diagnostic_scoring"] is None
    assert report["significance_tests"] == "not_performed"


def test_completed_score_markdown_reports_all_paired_arm_metrics(tmp_path: Path) -> None:
    """The score-report markdown exposes descriptive counts without a winner."""
    case = candidate_from_row()
    candidate_hash = evaluation.sha256_value([case])
    manifest = {
        "source_master_sha": "a" * 40,
        "candidate_pool_sha256": candidate_hash,
        "paid_comparison": {
            "case_ids": [case["case_id"]],
            "case_count": 1,
            "case_set_sha256": evaluation.sha256_value([case["case_id"]]),
            "maximum_cases": 60,
        },
        "candidate_counts": {
            "selected_prospective_cases": 1,
            "selected_prior_evaluation_cases": 0,
            "deduplicated_valid_candidates": 1,
            "unusable_or_unselected": 0,
            "skip_reason_counts": {},
        },
        "provider_model_availability": {"endpoint_call_count": 2},
    }
    results = []
    for arm in evaluation.ARMS:
        row = base_result_row(arm=arm)
        row["case_id"] = case["case_id"]
        results.append(row)
    ledger = evaluation.QudRequestLedger(
        tmp_path / "ledger.json", case_set_sha256=candidate_hash
    )
    metric = {
        "acceptable": 1,
        "minor": 0,
        "unacceptable": 0,
        "substitution_yes": 0,
        "substitution_no": 1,
        "substitution_unclear": 0,
        "preferred_outcomes": 1,
        "wins_against_A": 0,
        "losses_against_A": 0,
        "ties_against_A": 1,
        "newly_introduced_unacceptable": 0,
        "newly_introduced_substitutions": 0,
        "repaired_substitutions": 0,
        "false_interventions_on_sound_replies": 0,
        "no_reply_from_failed_repair": 0,
        "billed_cost_per_acceptable_outcome_usd": 0.01,
        "estimated_cost_per_acceptable_outcome_usd": 0.02,
        "latency": {"median_seconds": 1.0, "p95_seconds": 2.0},
    }
    human = {
        "scored_case_count": 1,
        "arms": {arm: copy.deepcopy(metric) for arm in evaluation.ARMS},
        "preferred_outcome_totals": {"A": 0, "B": 1, "C": 0, "D": 0, "tie": 0},
        "diagnostic_scoring": {
            "scored_case_count": 1,
            "counts": {"issue_state_rating": {"accurate": 1}},
            "issue_state_accuracy": 1.0,
            "transcript_critic_accuracy": 1.0,
            "ledger_critic_accuracy": 1.0,
        },
    }
    comparison = evaluation.build_comparison(
        manifest=manifest,
        cases=[case],
        issue_rows=[],
        results=results,
        ledger=ledger,
        blocker=None,
        human_scoring=human,
    )
    markdown = evaluation.render_comparison_markdown(comparison)
    assert "W/L/T vs A" in markdown
    assert "New unacceptable" in markdown
    assert "Repaired substitutions" in markdown
    assert "False interventions" in markdown
    assert "Failed-repair no reply" in markdown
    assert "Diagnostic extraction and critic scoring" in markdown
    assert "No production activation is recommended" in markdown
    assert "No winning arm is declared" in markdown


def test_diagnostic_scoring_is_optional_but_must_be_complete(tmp_path: Path) -> None:
    """Blank diagnostics are optional; partial diagnostics cannot be reported."""
    case = candidate_from_row()
    fields = [
        "case_id",
        "issue_state_rating",
        "user_stance_rating",
        "rejected_target_rating",
        "transcript_critic_rating",
        "ledger_critic_rating",
        "notes",
    ]
    row = {field: "" for field in fields}
    row["case_id"] = str(case["case_id"])
    row["issue_state_rating"] = "accurate"
    write_csv(tmp_path / "partial.csv", fields, [row])
    with pytest.raises(evaluation.EvaluationError, match="partially completed"):
        evaluation.load_completed_diagnostic_scores(
            path=tmp_path / "partial.csv", cases=[case]
        )


def test_score_templates_have_the_exact_required_columns(tmp_path: Path) -> None:
    """Human review sheets expose the fixed blind and diagnostic fields only."""
    case = candidate_from_row()
    blind = tmp_path / "blind.csv"
    diagnostic = tmp_path / "diagnostic.csv"
    evaluation.write_blind_scores_template(blind, [case])
    evaluation.write_diagnostic_scores_template(diagnostic, [case])
    assert blind.read_text(encoding="utf-8").splitlines()[0] == (
        "case_id,W_rating,W_substitution,X_rating,X_substitution,"
        "Y_rating,Y_substitution,Z_rating,Z_substitution,preferred_outcome,notes"
    )
    assert diagnostic.read_text(encoding="utf-8").splitlines()[0] == (
        "case_id,issue_state_rating,user_stance_rating,rejected_target_rating,"
        "transcript_critic_rating,ledger_critic_rating,notes"
    )


def test_header_only_preparation_templates_populate_once_for_paid_cases(
    tmp_path: Path,
) -> None:
    """Offline empty templates gain paid IDs once and never overwrite later rows."""
    case = candidate_from_row()
    blind = tmp_path / "blind.csv"
    diagnostic = tmp_path / "diagnostic.csv"
    evaluation.write_blind_scores_template(blind, [])
    evaluation.write_diagnostic_scores_template(diagnostic, [])
    assert len(blind.read_text(encoding="utf-8").splitlines()) == 1
    assert len(diagnostic.read_text(encoding="utf-8").splitlines()) == 1
    evaluation.write_blind_scores_template(blind, [case])
    evaluation.write_diagnostic_scores_template(diagnostic, [case])
    assert len(blind.read_text(encoding="utf-8").splitlines()) == 2
    assert len(diagnostic.read_text(encoding="utf-8").splitlines()) == 2

    replacement = copy.deepcopy(case)
    replacement["case_id"] = "different-case"
    with pytest.raises(evaluation.EvaluationError, match="case set changed"):
        evaluation.write_blind_scores_template(blind, [replacement])
    with pytest.raises(evaluation.EvaluationError, match="case set changed"):
        evaluation.write_diagnostic_scores_template(diagnostic, [replacement])


def test_blind_scores_must_be_completed_before_reporting(tmp_path: Path) -> None:
    """An unscored blind template cannot be decoded as an experiment result."""
    case = candidate_from_row()
    blind = tmp_path / "blind.csv"
    diagnostic = tmp_path / "diagnostic.csv"
    evaluation.write_blind_scores_template(blind, [case])
    evaluation.write_diagnostic_scores_template(diagnostic, [case])
    with pytest.raises(evaluation.EvaluationError, match="invalid blind rating"):
        evaluation.load_completed_scores(
            scores_path=blind,
            diagnostic_path=diagnostic,
            cases=[case],
            results=[],
            issue_rows=[],
            arm_key={"A": "W", "B": "X", "C": "Y", "D": "Z"},
        )
