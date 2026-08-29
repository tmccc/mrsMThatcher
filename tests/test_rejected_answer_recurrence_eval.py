"""Network-free tests for the explicit rejected-answer recurrence evaluation."""

from __future__ import annotations

import csv
import json
import subprocess
from collections import Counter
from pathlib import Path

import pytest
import requests

from tools import run_rejected_answer_recurrence_eval as recurrence


def exact_transcript() -> list[dict[str, object]]:
    """Return a synthetic Liberty-style exact principal-author branch."""
    return [
        {
            "turn_id": "u1",
            "post_id": "u1",
            "parent_turn_id": None,
            "author_role": "principal_contributor",
            "author_key": "principal-1",
            "text": "Is economic liberty necessary for non-economic liberty?",
            "created_at": "2026-01-01T00:00:00Z",
        },
        {
            "turn_id": "a1",
            "post_id": "a1",
            "parent_turn_id": "u1",
            "author_role": "account",
            "author_key": "account",
            "text": "Economic liberty provides security and independence.",
            "created_at": "2026-01-01T00:01:00Z",
        },
        {
            "turn_id": "u2",
            "post_id": "u2",
            "parent_turn_id": "a1",
            "author_role": "principal_contributor",
            "author_key": "principal-1",
            "text": (
                "I am not asking about security or independence. My question was "
                "whether economic liberty is necessary for non-economic liberty."
            ),
            "created_at": "2026-01-01T00:02:00Z",
        },
    ]


def exact_case(case_id: str = "synthetic-liberty") -> dict[str, object]:
    """Return one synthetic case; it is never written to the paid corpus."""
    transcript = exact_transcript()
    candidate = "Economic liberty gives people security and independence."
    return {
        "case_id": case_id,
        "source_kind": "synthetic_test_only",
        "source_priority": 0,
        "source_path": "/test",
        "source_snapshot_identity": "test",
        "conversation_identity": "conversation-test",
        "branch_identity": "branch-test",
        "principal_contributor_key": "principal-1",
        "repair_candidate_turn_id": "u2",
        "candidate_account_reply_id": "a2",
        "candidate_identity": "a2",
        "transcript": transcript,
        "candidate_reply": candidate,
        "source_evidence": {},
        "reconstruction_confidence": "high",
        "transcript_sha256": recurrence.sha256_value(transcript),
        "candidate_sha256": recurrence.sha256_bytes(candidate.encode()),
        "carried_forward_human_label": None,
        "plausible_explicit_repair_candidate": True,
        "liberty_case": True,
    }


def valid_repair_record(*, confidence: str = "high") -> dict[str, object]:
    """Return one strict, exact-substring repair extraction."""
    return {
        "status": "explicit_repair_found",
        "repair_turn_id": "u2",
        "repair_evidence_quote": "I am not asking about security or independence.",
        "rejected_account_turn_id": "a1",
        "rejected_answer_quote": "security and independence",
        "restored_issue": {
            "text": "Whether economic liberty is necessary for non-economic liberty.",
            "source_turn_ids": ["u1", "u2"],
            "source_quotes": [
                "Is economic liberty necessary for non-economic liberty?",
                "whether economic liberty is necessary for non-economic liberty.",
            ],
        },
        "repair_type": "wrong_proposition",
        "confidence": confidence,
    }


def detector_result(
    *,
    status: str = "recurrence",
    confidence: str = "high",
    addresses: str = "not_addressed",
    treatment: str = "substantially_repeats_as_answer",
    focus: str = "rejected_answer",
    quote: str = "security and independence",
) -> dict[str, object]:
    """Return one strict detector-shaped object."""
    return {
        "status": status,
        "repair_turn_id": "u2",
        "rejected_account_turn_id": "a1",
        "addresses_restored_issue": addresses,
        "treatment_of_rejected_answer": treatment,
        "candidate_first_focus": focus,
        "candidate_evidence_quote": quote,
        "confidence": confidence,
    }


def prospective_turn(
    post_id: str,
    role: str,
    author: str,
    text: str,
    parent: str | None,
    *,
    published: bool = False,
) -> dict[str, object]:
    """Build a minimal prospective-v4-shaped exact turn."""
    return {
        "turn_id": f"turn-{post_id}",
        "post_id": post_id,
        "parent_post_id": parent,
        "parent_observation_status": "confirmed_none" if parent is None else "observed",
        "author_role": role,
        "author_key": author,
        "text": text,
        "public_text": text if role == "account" else None,
        "created_at": f"2026-01-01T00:0{len(post_id)}:00Z",
        "publication_status": "published" if published else "observed",
        "publication_authority": "structured_confirmation" if published else None,
        "publication_evidence": [{"event_kind": "reply_posted"}] if published else [],
        "reconstruction_confidence": "high",
        "account_graph_ambiguous_fields": [],
        "account_graph_conflicts": [],
        "account_content_conflicts": [],
        "reply_visual_context_summary": {"native_photo_count_max": 0},
    }


def prospective_conversation() -> dict[str, object]:
    """Build a complete repair followed by a proved account reply."""
    return {
        "conversation_key": "conv-1",
        "conversation_id": "conv-id-1",
        "reconstruction_confidence": "high",
        "turns": [
            prospective_turn(
                "u1", "user", "principal-1", "Is liberty necessary?", None
            ),
            prospective_turn(
                "a1",
                "account",
                "account",
                "Security and independence are important.",
                "u1",
                published=True,
            ),
            prospective_turn(
                "u2",
                "user",
                "principal-1",
                "That is not what I asked. My question was necessity, not security.",
                "a1",
            ),
            prospective_turn(
                "a2", "account", "account", "Security is the foundation.", "u2", published=True
            ),
        ],
    }


def write_history_fixture(root: Path, *, proved_candidate: bool = True) -> None:
    """Write a minimal retained-history graph with exact publication evidence."""
    root.mkdir()
    recurrence.atomic_json(root / "run_manifest.json", {"tool_version": "test"})
    recurrence.atomic_jsonl(
        root / "unique_log_records.jsonl",
        [
            {
                "record_id": "edge-1",
                "message": (
                    "Built AI reply context for mention 1003. chain_items=2 "
                    "immediate_parent=1002 quoted=False"
                ),
            }
        ],
    )
    recurrence.atomic_jsonl(
        root / "conversational_candidates.jsonl",
        [
            {
                "candidate_id": "history-root",
                "target_id": "1001",
                "author_id": "principal-1",
                "incoming_text": "Is economic liberty necessary for other liberties?",
                "lane": "quote-tweet",
                "thread_id": "thread-1",
                "outcome": "posted",
                "actual_reply_text": "The important issue is security and independence.",
                "reply_post_id": "1002",
                "reconstruction_status": "complete",
                "final_outcome_evidence_id": "published-1",
                "source_record_ids": ["source-1"],
            },
            {
                "candidate_id": "history-repair",
                "target_id": "1003",
                "author_id": "principal-1",
                "incoming_text": (
                    "That is not what I asked. My question was necessity, not security."
                ),
                "lane": "mention",
                "thread_id": "thread-1",
                "outcome": "posted" if proved_candidate else "drafted",
                "actual_reply_text": "Economic liberty ensures security.",
                "reply_post_id": "1004" if proved_candidate else None,
                "reconstruction_status": "complete" if proved_candidate else "partial",
                "final_outcome_evidence_id": "published-2" if proved_candidate else None,
                "source_record_ids": ["source-2"],
            },
        ],
    )


def test_production_modules_are_unchanged_and_clean() -> None:
    """The follow-on must not touch any protected production source."""
    recurrence.assert_production_sources_clean(recurrence.PROJECT_ROOT)
    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "a2e8ff90dab5c3edd6d716a6c4158c31bbc18700",
            "--",
            *sorted(recurrence.PROTECTED_PRODUCTION_PATHS),
        ],
        cwd=recurrence.PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == ""


@pytest.mark.parametrize(
    "path",
    [
        recurrence.PRODUCTION_CHECKOUT / "research-output",
        recurrence.PREVIOUS_EXPERIMENT_ROOT / "new-output",
        recurrence.PROJECT_ROOT / "private-output",
    ],
)
def test_production_and_previous_experiment_paths_cannot_be_written(path: Path) -> None:
    """Private-output validation refuses every protected tree."""
    with pytest.raises(recurrence.EvaluationError):
        recurrence.ensure_private_output_dir(path, project_dir=recurrence.PROJECT_ROOT)


def test_live_model_execution_requires_explicit_flag() -> None:
    """No provider path is authorised by offline preparation."""
    with pytest.raises(recurrence.EvaluationError, match="execute-live-models"):
        recurrence.require_live_execution(False)
    recurrence.require_live_execution(True)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.x.com/2/tweets",
        "https://twitter.com/api",
        "https://example.com/v1/chat/completions",
        "https://api.x.ai.evil.test/v1/models",
        "https://api.x.ai/v1/models?redirect=https://twitter.com",
    ],
)
def test_only_two_provider_hosts_are_allowed(url: str) -> None:
    """X/Twitter and every non-provider host are rejected."""
    with pytest.raises(recurrence.EvaluationError):
        recurrence.validate_provider_url(url)
    assert (
        recurrence.validate_provider_url("https://api.x.ai/v1/chat/completions")
        == "https://api.x.ai/v1/chat/completions"
    )
    assert (
        recurrence.validate_provider_url("https://api.openai.com/v1/chat/completions")
        == "https://api.openai.com/v1/chat/completions"
    )


def test_ambiguous_transmitted_request_is_not_retried(tmp_path: Path) -> None:
    """A transmission timeout is durable and a repeat never calls the network."""
    ledger = recurrence.RecurrenceRequestLedger(
        tmp_path / "ledger.json", case_set_sha256="1" * 64
    )
    calls = 0

    def timeout_post(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise requests.Timeout("outcome unknown")

    client = recurrence.ProviderClient(
        consumer="transcript_only",
        case_id="case-1",
        ledger=ledger,
        response_dir=tmp_path / "responses",
        api_keys={"OpenAI": "not-a-real-key"},
        xai_model_metadata={},
        post=timeout_post,
        sleep=lambda _seconds: None,
    )
    kwargs = {
        "provider": "OpenAI",
        "stage": "rejected_answer_recurrence_transcript_only",
        "model": recurrence.DETECTOR_MODEL,
        "reasoning_effort": recurrence.DETECTOR_EFFORT,
        "system_prompt": recurrence.DETECTOR_PROMPT,
        "payload": {"transcript": [], "candidate_reply": "x"},
        "response_schema": recurrence.DETECTOR_SCHEMA,
        "timeout_seconds": 180,
        "max_output_tokens": 20,
    }
    with pytest.raises(recurrence.AmbiguousRequestError):
        client.request(**kwargs)
    with pytest.raises(recurrence.AmbiguousRequestError):
        client.request(**kwargs)
    assert calls == 1
    assert ledger.data["blocked"] is True


def test_cost_reservation_stops_before_ceiling(tmp_path: Path) -> None:
    """The next maximum exposure cannot cross the hard US$10 ceiling."""
    ledger = recurrence.RecurrenceRequestLedger(
        tmp_path / "ledger.json", case_set_sha256="2" * 64
    )
    with pytest.raises(recurrence.CostLimitReached):
        ledger.reserve(
            request_hash="a" * 64,
            provider="OpenAI",
            stage="test",
            logical_model=recurrence.DETECTOR_MODEL,
            logical_effort=recurrence.DETECTOR_EFFORT,
            effective_model=recurrence.DETECTOR_MODEL,
            effective_effort=recurrence.DETECTOR_EFFORT,
            maximum_possible_cost_ticks=ledger.limit_ticks + 1,
            consumer_id="case:test",
        )
    assert ledger.data["operations"] == []


def test_provider_metrics_count_transmitted_schema_failures() -> None:
    """A received response remains a provider call when strict parsing rejects it."""
    class Ledger:
        data = {
            "operations": [
                {
                    "status": "completed",
                    "provider_latency_seconds": 1.0,
                    "attempt_events": [{"status": "completed"}],
                },
                {
                    "status": "completed",
                    "provider_latency_seconds": 2.0,
                    "attempt_events": [{"status": "completed"}],
                },
            ]
        }

        @staticmethod
        def cost_summary() -> dict[str, float]:
            return {
                "total_billed_cost_usd": 0.0,
                "total_estimated_cost_usd": 0.0,
            }

    metrics = recurrence._operation_metrics(Ledger())
    assert metrics["canonical_requests"] == 2
    assert metrics["network_request_events"] == 2
    assert metrics["provider_http_attempts"] == 2


def test_corpus_selection_is_deterministic_deduplicated_and_capped() -> None:
    """Stable identity sorting produces at most 30 exact unique pairs."""
    rows = []
    for index in range(35):
        case = exact_case(f"case-{index:02d}")
        case["candidate_reply"] = f"candidate {index}"
        case["candidate_sha256"] = recurrence.sha256_value(case["candidate_reply"])
        rows.append(case)
    rows.append(dict(rows[0]))
    selected_a, skips_a = recurrence.select_corpus(rows)
    selected_b, skips_b = recurrence.select_corpus(list(reversed(rows)))
    assert [row["case_id"] for row in selected_a] == [row["case_id"] for row in selected_b]
    assert len(selected_a) == 30
    assert skips_a == skips_b
    assert Counter(row["reason"] for row in skips_a) == {
        "duplicate_exact_input": 1,
        "corpus_cap": 5,
    }


def test_future_account_and_user_turns_are_excluded() -> None:
    """A candidate and every later turn remain outside the detector transcript."""
    conversation = prospective_conversation()
    conversation["turns"].extend(
        [
            prospective_turn("u3", "user", "principal-1", "Later comment", "a2"),
            prospective_turn("a3", "account", "account", "Later reply", "u3", published=True),
        ]
    )
    cases, skips = recurrence.prospective_cases_from_conversations(
        [conversation], source_path=Path("/frozen"), review_pack_identity="pack"
    )
    assert not skips
    assert len(cases) == 1
    assert [turn["post_id"] for turn in cases[0]["transcript"]] == ["u1", "a1", "u2"]
    assert cases[0]["candidate_account_reply_id"] == "a2"
    assert "Later" not in json.dumps(recurrence.provider_case_payload(cases[0]))


def test_sibling_contributors_cannot_supply_repair_or_restored_issue() -> None:
    """An author hand-off on the parent path invalidates the principal branch."""
    conversation = prospective_conversation()
    conversation["turns"][0]["author_key"] = "sibling-author"
    cases, skips = recurrence.prospective_cases_from_conversations(
        [conversation], source_path=Path("/frozen"), review_pack_identity="pack"
    )
    assert cases == []
    assert [row["reason"] for row in skips] == ["mixed_principal_contributors"]


def test_ambiguous_parent_case_is_rejected() -> None:
    """Graph conflicts cannot enter an exact recurrence case."""
    conversation = prospective_conversation()
    conversation["turns"][2]["account_graph_conflicts"] = [{"field": "parent"}]
    cases, skips = recurrence.prospective_cases_from_conversations(
        [conversation], source_path=Path("/frozen"), review_pack_identity="pack"
    )
    assert not cases
    assert skips[0]["reason"] == "ambiguous_parentage"


def test_missing_parent_case_is_rejected() -> None:
    """A partial ancestor chain is never guessed or reconstructed by author."""
    conversation = prospective_conversation()
    conversation["turns"][1]["parent_post_id"] = "missing"
    cases, skips = recurrence.prospective_cases_from_conversations(
        [conversation], source_path=Path("/frozen"), review_pack_identity="pack"
    )
    assert not cases
    assert skips[0]["reason"] == "partial_path_reconstruction"


def test_image_dependent_case_is_excluded() -> None:
    """Unavailable user-image premises cannot enter the text-only corpus."""
    conversation = prospective_conversation()
    conversation["turns"][2]["reply_visual_context_summary"] = {
        "native_photo_count_max": 1
    }
    conversation["turns"][2]["visible_media_text"] = None
    cases, skips = recurrence.prospective_cases_from_conversations(
        [conversation], source_path=Path("/frozen"), review_pack_identity="pack"
    )
    assert not cases
    assert skips[0]["reason"] == "unavailable_visual_premise"


def test_unconfirmed_draft_cannot_be_historical_candidate() -> None:
    """Candidate text without publication proof is excluded."""
    conversation = prospective_conversation()
    candidate = conversation["turns"][3]
    candidate["publication_status"] = "observed"
    candidate["publication_evidence"] = []
    cases, skips = recurrence.prospective_cases_from_conversations(
        [conversation], source_path=Path("/frozen"), review_pack_identity="pack"
    )
    assert not cases
    assert skips[0]["reason"] == "unproved_account_reply"


def test_retained_history_uses_only_proved_replies_and_explicit_parent_edges(
    tmp_path: Path,
) -> None:
    """Candidate records and context edges recover one exact principal branch."""
    history_root = tmp_path / "history"
    write_history_fixture(history_root)
    cases, skips, manifest = recurrence.history_cases_from_reconstruction(history_root)
    assert not skips
    assert manifest == {"tool_version": "test"}
    assert len(cases) == 1
    case = cases[0]
    assert [turn["turn_id"] for turn in case["transcript"]] == ["1001", "1002", "1003"]
    assert case["candidate_account_reply_id"] == "1004"
    assert "1004" not in {turn["turn_id"] for turn in case["transcript"]}
    assert case["source_evidence"]["path_record_ids"] == [
        "edge-1",
        "published-1",
        "source-1",
        "source-2",
    ]


def test_unproved_retained_history_reply_is_skipped(tmp_path: Path) -> None:
    """A reconstructed draft without authoritative publication proof is unusable."""
    history_root = tmp_path / "history"
    write_history_fixture(history_root, proved_candidate=False)
    cases, skips, _ = recurrence.history_cases_from_reconstruction(history_root)
    assert cases == []
    assert [row["reason"] for row in skips] == ["unproved_account_reply"]


def test_labels_are_never_in_provider_payloads() -> None:
    """Carried human labels and source provenance stay private."""
    case = exact_case()
    case["carried_forward_human_label"] = {
        "explicit_recurrence": "no",
        "label_source": "human",
    }
    payload = recurrence.provider_case_payload(case)
    serialised = json.dumps(payload, sort_keys=True)
    assert "carried" not in serialised
    assert "human" not in serialised
    assert "source_evidence" not in serialised


@pytest.mark.parametrize(
    "text",
    [
        "That is not what I asked.",
        "Yeah but that's not what I asked, I asked which way people went.",
        "That is not what I meant.",
        "I am not asking about security.",
        "You answered a different question.",
        "That doesn't answer my question.",
        "My question was necessity, not usefulness.",
        (
            "Please don't hedge. She did not say liberty was precarious, "
            "but that there can be none."
        ),
    ],
)
def test_high_precision_explicit_repair_cues_are_selected(text: str) -> None:
    """Metaconversational wrong-answer cues reach extraction."""
    assert recurrence.explicit_repair_prefilter(text)


@pytest.mark.parametrize(
    "text",
    [
        "I don't care. I’m keeping my benefits.",
        "No, I disagree.",
        "Read their books.",
        "That is wrong.",
        "Actually, my view is different.",
    ],
)
def test_ordinary_argument_cues_are_not_selected(text: str) -> None:
    """Disagreement and evidence requests alone are not repair labels."""
    assert not recurrence.explicit_repair_prefilter(text)


def test_user_abandoning_old_question_is_replacement_not_repair() -> None:
    """An explicit user replacement is filtered out of repair candidates."""
    text = "I am abandoning my old question. My new question is why security matters, not liberty."
    assert not recurrence.explicit_repair_prefilter(text)


def test_not_asking_about_security_is_repair_not_withdrawal() -> None:
    """The strict record restores the user issue and has no withdrawal field."""
    parsed = recurrence.validate_repair_record(
        valid_repair_record(),
        transcript=exact_transcript(),
        principal_contributor_key="principal-1",
    )
    assert parsed["status"] == "explicit_repair_found"
    assert parsed["repair_type"] == "wrong_proposition"
    assert "withdraw" not in parsed
    assert parsed["restored_issue"]["text"].startswith("Whether economic liberty")


def test_exact_evidence_quotes_must_be_literal_substrings() -> None:
    """Paraphrased repair, rejected, or restored evidence is invalid."""
    for field in ("repair_evidence_quote", "rejected_answer_quote"):
        record = valid_repair_record()
        record[field] = "not a literal quote"
        with pytest.raises(ValueError, match="literal substring"):
            recurrence.validate_repair_record(
                record,
                transcript=exact_transcript(),
                principal_contributor_key="principal-1",
            )
    record = valid_repair_record()
    record["restored_issue"]["source_quotes"][0] = "paraphrased source"
    with pytest.raises(ValueError, match="literal substring"):
        recurrence.validate_repair_record(
            record,
            transcript=exact_transcript(),
            principal_contributor_key="principal-1",
        )


def test_usable_repair_must_be_final_and_cite_the_restored_issue() -> None:
    """An earlier turn or source-free abstraction cannot enable the detector."""
    transcript = exact_transcript()
    transcript.append(
        {
            "turn_id": "u3",
            "post_id": "u3",
            "parent_turn_id": "u2",
            "author_role": "principal_contributor",
            "author_key": "principal-1",
            "text": "A later continuation.",
            "created_at": "2026-01-01T00:03:00Z",
        }
    )
    record = valid_repair_record()
    with pytest.raises(ValueError, match="final contributor turn"):
        recurrence.validate_repair_record(
            record,
            transcript=transcript,
            principal_contributor_key="principal-1",
        )
    record = valid_repair_record()
    record["restored_issue"]["source_turn_ids"] = []
    record["restored_issue"]["source_quotes"] = []
    with pytest.raises(ValueError, match="restored-issue source"):
        recurrence.validate_repair_record(
            record,
            transcript=exact_transcript(),
            principal_contributor_key="principal-1",
        )


def test_rejected_account_turn_must_precede_repair() -> None:
    """The extractor cannot cite a future account answer as rejected."""
    transcript = exact_transcript()
    transcript.append(
        {
            "turn_id": "a2",
            "post_id": "a2",
            "parent_turn_id": "u2",
            "author_role": "account",
            "author_key": "account",
            "text": "Future answer",
            "created_at": "2026-01-01T00:03:00Z",
        }
    )
    record = valid_repair_record()
    record["rejected_account_turn_id"] = "a2"
    record["rejected_answer_quote"] = "Future answer"
    with pytest.raises(ValueError, match="must precede"):
        recurrence.validate_repair_record(
            record, transcript=transcript, principal_contributor_key="principal-1"
        )


def test_sibling_or_future_restored_issue_source_is_rejected() -> None:
    """Only the exact principal contributor up to the repair may restore an issue."""
    transcript = exact_transcript()
    transcript.append(
        {
            "turn_id": "u3",
            "post_id": "u3",
            "parent_turn_id": "u2",
            "author_role": "principal_contributor",
            "author_key": "principal-1",
            "text": "Future issue",
            "created_at": "2026-01-01T00:03:00Z",
        }
    )
    record = valid_repair_record()
    record["restored_issue"]["source_turn_ids"] = ["u3"]
    record["restored_issue"]["source_quotes"] = ["Future issue"]
    with pytest.raises(ValueError, match="future"):
        recurrence.validate_repair_record(
            record, transcript=transcript, principal_contributor_key="principal-1"
        )


def test_liberty_recurrence_triggers() -> None:
    """Repeating the rejected answer without the restored issue triggers once."""
    parsed = recurrence.validate_detector_result(
        detector_result(),
        transcript=exact_transcript(),
        candidate_reply=str(exact_case()["candidate_reply"]),
        usable_repair_record=valid_repair_record(),
    )
    assert recurrence.narrow_recurrence_trigger(parsed)


@pytest.mark.parametrize(
    ("candidate", "result"),
    [
        (
            "No: economic liberty is not necessary for non-economic liberty.",
            detector_result(
                status="no_recurrence",
                addresses="direct",
                treatment="avoids",
                focus="restored_issue",
                quote="not necessary",
            ),
        ),
        (
            "Yes, it is necessary. It also supports security and independence.",
            detector_result(
                status="no_recurrence",
                addresses="direct",
                treatment="substantially_repeats_as_answer",
                focus="restored_issue",
                quote="Yes, it is necessary.",
            ),
        ),
        (
            "Security is distinct from the question of whether economic liberty is necessary.",
            detector_result(
                status="no_recurrence",
                addresses="direct",
                treatment="references_to_distinguish",
                focus="restored_issue",
                quote="Security is distinct",
            ),
        ),
        (
            "The supplied premise is not established, so I cannot accept that attribution.",
            detector_result(
                status="no_recurrence",
                addresses="direct",
                treatment="avoids",
                focus="restored_issue",
                quote="premise is not established",
            ),
        ),
        (
            "You’re very welcome.",
            detector_result(
                status="no_recurrence",
                addresses="not_addressed",
                treatment="avoids",
                focus="other",
                quote="You’re very welcome.",
            ),
        ),
    ],
)
def test_nonrecurrence_distinctions_do_not_trigger(
    candidate: str, result: dict[str, object]
) -> None:
    """Disagreement, elaboration, distinction, premise neutrality, and pragmatics stay clear."""
    parsed = recurrence.validate_detector_result(
        result, transcript=exact_transcript(), candidate_reply=candidate
    )
    assert not recurrence.narrow_recurrence_trigger(parsed)


@pytest.mark.parametrize("confidence", ["medium", "low"])
def test_medium_and_low_confidence_cannot_trigger(confidence: str) -> None:
    """Only a high-confidence exact certificate can fire."""
    assert not recurrence.narrow_recurrence_trigger(
        detector_result(confidence=confidence)
    )


def test_failed_exact_substring_check_cannot_trigger() -> None:
    """Invalid candidate evidence prevents the machine trigger."""
    with pytest.raises(ValueError, match="literal substring"):
        recurrence.validate_detector_result(
            detector_result(quote="not in candidate"),
            transcript=exact_transcript(),
            candidate_reply=str(exact_case()["candidate_reply"]),
        )
    assert not recurrence.narrow_recurrence_trigger(
        detector_result(), evidence_validation_passed=False
    )


def test_record_backed_detector_cannot_run_without_usable_record() -> None:
    """Missing and medium-confidence extraction records cause no provider call."""

    class NeverClient:
        def request(self, **_kwargs: object) -> object:
            raise AssertionError("provider must not be called")

    with pytest.raises(recurrence.EvaluationError, match="usable repair"):
        recurrence.run_detector(
            case=exact_case(),
            condition="repair_record_backed",
            client=NeverClient(),
            repair_record=None,
        )
    with pytest.raises(recurrence.EvaluationError, match="usable repair"):
        recurrence.run_detector(
            case=exact_case(),
            condition="repair_record_backed",
            client=NeverClient(),
            repair_record=valid_repair_record(confidence="medium"),
        )


def test_blind_review_leaks_no_diagnostics_or_mapping() -> None:
    """Blind case material contains exact inputs but no model or condition result."""
    review = recurrence.render_blind_case_review([exact_case()])
    recurrence._blind_review_forbidden_text(review)
    lowered = review.lower()
    for forbidden in (
        "grok-4.6",
        "gpt-5.6-sol",
        "narrow trigger",
        "repair_record",
        "transcript_only",
        "repair_record_backed",
        '"p"',
        '"q"',
    ):
        assert forbidden not in lowered


def test_detector_mapping_remains_in_sole_private_key(tmp_path: Path) -> None:
    """P/Q condition identity is written only to the named private key file."""
    path = tmp_path / "detector_key.private.json"
    key = recurrence.create_or_load_detector_key(path)
    assert set(key) == {"P", "Q"}
    assert set(key.values()) == set(recurrence.CONDITIONS)
    assert recurrence.create_or_load_detector_key(path) == key
    assert path.stat().st_mode & 0o777 == 0o600


def test_seven_prior_qud_controls_are_private_negative_labels() -> None:
    """Exactly seven Arm A outcomes retain only private carried-forward labels."""
    controls = recurrence.load_qud_negative_controls()
    assert len(controls) == 7
    assert all(
        case["carried_forward_human_label"]
        == {
            "explicit_recurrence": "no",
            "proposition_substitution": "no",
            "label_source": "completed human review, 2026-08-29",
        }
        for case in controls
    )
    assert all(
        "carried_forward_human_label" not in recurrence.provider_case_payload(case)
        and "source_evidence" not in recurrence.provider_case_payload(case)
        for case in controls
    )


def write_score_fixture(root: Path) -> None:
    """Write a tiny completed private score fixture with an explicit P/Q key."""
    root.mkdir(mode=0o700, exist_ok=True)
    cases = [exact_case("c-positive"), exact_case("c-negative")]
    cases[1]["source_kind"] = "qud_human_reviewed_negative_control"
    recurrence.atomic_jsonl(root / "cases.jsonl", cases)
    recurrence.atomic_json(
        root / "manifest.json",
        {
            "liberty_case_found": True,
            "liberty_case_id": "c-positive",
            "case_set_sha256": recurrence.sha256_value(cases),
        },
    )
    recurrence.atomic_json(root / "detector_key.private.json", {
        "P": "repair_record_backed",
        "Q": "transcript_only",
    })
    recurrence.atomic_jsonl(
        root / "repair_records.jsonl",
        [
            {"case_id": "c-positive", "usable": True},
            {"case_id": "c-negative", "usable": False},
        ],
    )
    recurrence.atomic_jsonl(
        root / "detector_results.jsonl",
        [
            {
                "case_id": "c-positive",
                "condition": "transcript_only",
                "narrow_trigger": True,
                "result": {"status": "recurrence"},
            },
            {
                "case_id": "c-positive",
                "condition": "repair_record_backed",
                "narrow_trigger": True,
                "result": {"status": "recurrence"},
            },
            {
                "case_id": "c-negative",
                "condition": "transcript_only",
                "narrow_trigger": False,
                "result": {"status": "no_explicit_repair"},
            },
            {
                "case_id": "c-negative",
                "condition": "repair_record_backed",
                "narrow_trigger": False,
                "result": None,
                "status": "no_usable_repair_record",
            },
        ],
    )
    fields = recurrence.BLIND_LABEL_FIELDS
    with (root / "blind_case_labels.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case_id, truth in (("c-positive", "yes"), ("c-negative", "no")):
            writer.writerow(
                {
                    "case_id": case_id,
                    "explicit_wrong_answer_repair": "yes" if truth == "yes" else "no",
                    "rejected_answer_identifiable": "yes" if truth == "yes" else "not_applicable",
                    "restored_issue_identifiable": "yes" if truth == "yes" else "not_applicable",
                    "candidate_addresses_restored_issue_first": "no" if truth == "yes" else "not_applicable",
                    "candidate_repeats_rejected_answer_as_answer": "yes" if truth == "yes" else "no",
                    "overall_recurrence": truth,
                    "notes": "",
                }
            )
    recurrence.atomic_json(
        root / "comparison.private.json",
        {
            "counts": {
                "total": 2,
                "plausible_repair_candidates": 1,
                "historical_candidates": 1,
                "prospective_candidates": 0,
                "qud_negative_controls": 1,
                "skipped": 0,
                "repair_extractions_completed": 2,
                "usable_repair_records": 1,
                "detectors_completed": 3,
                "record_backed_not_run": 1,
            },
            "provider": {
                "canonical_requests": 5,
                "network_request_events": 5,
                "cache_hit_events": 0,
                "xai_billed_cost_usd": 0.01,
                "openai_estimated_cost_usd": 0.02,
                "median_request_latency_seconds": 1.0,
                "p95_request_latency_seconds": 2.0,
            },
            "failures": {
                "schema": 0,
                "provider": 0,
                "ambiguous": 0,
                "evidence_validation": 0,
            },
            "narrow_trigger_counts": {"P": 1, "Q": 1},
            "liberty_case_found": True,
            "skip_reason_counts": {},
            "blocker": None,
        },
    )


def test_score_reporting_decodes_both_detector_conditions(tmp_path: Path) -> None:
    """The private score report decodes P/Q and counts end-to-end outcomes."""
    write_score_fixture(tmp_path)
    comparison = recurrence.report_completed_scores(tmp_path)
    scoring = comparison["human_scoring"]
    assert scoring["detectors_by_blind_label"]["P"]["true_positives"] == 1
    assert scoring["detectors_by_blind_label"]["Q"]["true_positives"] == 1
    assert scoring["detectors_by_condition"]["repair_record_backed"]["false_negatives"] == 0
    assert scoring["prior_negative_controls"]["detector_P_false_positive_case_ids"] == []
    assert scoring["repair_extraction"]["precision"] == 1.0


def test_prompts_and_schemas_are_narrow_and_strict() -> None:
    """The request contract rejects general quality review and provider tools."""
    assert "Judge only recurrence" in recurrence.DETECTOR_PROMPT
    assert "general relevance or quality review" in recurrence.DETECTOR_PROMPT
    assert "Direct disagreement" in recurrence.DETECTOR_PROMPT
    assert "premise-neutral" in recurrence.DETECTOR_PROMPT
    assert "general issue ledger" in recurrence.EXTRACTION_PROMPT
    assert recurrence.REPAIR_RECORD_SCHEMA["additionalProperties"] is False
    assert recurrence.DETECTOR_SCHEMA["additionalProperties"] is False
