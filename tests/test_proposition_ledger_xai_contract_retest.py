from __future__ import annotations

import copy
import json
import socket
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import proposition_ledger_xai_contract_retest as retest


def _offline_environment() -> dict[str, str]:
    return {
        "python_executable": "synthetic-test-python",
        "xai_sdk_version": retest.PINNED_XAI_SDK_VERSION,
    }


def _offline_compiler(
    requests: Sequence[Mapping[str, Any]], tracked: Mapping[str, Any]
) -> dict[str, Any]:
    assert len(requests) == 4
    assert all(
        request["response_format"]["schema"] == tracked["provider_schema"]
        for request in requests
    )
    return {
        "requests_constructed": 4,
        "provider_calls_made": 0,
        "transport_rpc_invocations": 0,
    }


def _prepare(output: Path) -> dict[str, Any]:
    return retest.prepare_run(
        output,
        environment_check=_offline_environment,
        local_compiler=_offline_compiler,
    )


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _call_dir(output: Path, entry: Mapping[str, Any]) -> Path:
    return retest.base._call_directory(output, entry)


def _sentinel(turn: Mapping[str, Any], *related: str) -> dict[str, Any]:
    return {
        "local_ref": "new-issue-1",
        "initiating_speaker": turn["participant"]["participant_id"],
        "canonical_question": None,
        "issue_type": "no_stable_issue",
        "live_alternatives": [],
        "addressed_participant": None,
        "answer_requirements": [],
        "related_proposition_refs": list(related),
        "status": "no_stable_issue",
        "resolution_type": "no_stable_issue",
        "confidence": 1.0,
        "exact_evidence_spans": [
            {"exact_text": turn["exact_text"], "occurrence_index": 0}
        ],
    }


def _processed(
    tracked: Mapping[str, Any],
    turn_index: int,
    prior: Mapping[str, Any] | None,
    response: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    turn = tracked["chain"]["turns"][turn_index]
    value = (
        retest.phase2d.build_expected_transport_delta(tracked, turn, prior)
        if response is None
        else response
    )
    return retest.process_response_bytes(
        retest.canonical_json_bytes(value),
        turn=turn,
        prior_ledger=prior,
        tracked=tracked,
    )


class FakeTransport:
    def __init__(
        self, tracked: Mapping[str, Any], *, failing_orders: set[int] | None = None
    ) -> None:
        self.tracked = tracked
        self.failing_orders = set(failing_orders or ())
        self.orders: list[int] = []
        self.contexts: dict[int, dict[str, Any]] = {}

    def sample(
        self,
        entry: Mapping[str, Any],
        _request: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> retest.ProviderObservation:
        order = int(entry["order"])
        self.orders.append(order)
        self.contexts[order] = copy.deepcopy(dict(context))
        if order in self.failing_orders:
            raise RuntimeError("synthetic provider failure")
        response = retest.phase2d.build_expected_transport_delta(
            self.tracked, context["turn"], context["prior_ledger"]
        )
        return retest.ProviderObservation(
            raw_text=retest.canonical_json_bytes(response).decode("utf-8"),
            returned_model_id=str(entry["model"]),
            finish_reason="REASON_STOP",
            usage={
                "prompt_tokens": 100 + order,
                "reasoning_tokens": order,
                "completion_tokens": 20 + order,
                "total_tokens": 120 + 3 * order,
            },
        )


@pytest.fixture(scope="module")
def tracked() -> dict[str, Any]:
    return retest.validate_tracked_inputs()


def _old_versions(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result["schema_version"] = retest.HISTORICAL_TRANSPORT_VERSION
    result["canonical_schema_version"] = retest.HISTORICAL_CANONICAL_VERSION
    return result


def _historical_fixture(root: Path, tracked: Mapping[str, Any]) -> Path:
    """Build content-equivalent synthetic observations in the Phase 2D layout."""

    source = retest.base._create_private_output(root)
    turns = tracked["chain"]["turns"]

    valid0 = retest.phase2d.build_expected_transport_delta(tracked, turns[0], None)
    ledger1 = _processed(tracked, 0, None, valid0)["ledger"]
    assert ledger1 is not None
    response1 = copy.deepcopy(valid0)
    response1["commitment_changes"] = []

    response2 = copy.deepcopy(valid0)
    response2["abstentions"] = ["no_stable_issue"]
    response2["new_issue_states"] = [
        _sentinel(turns[0], response2["new_propositions"][0]["local_ref"])
    ]
    result2 = _processed(tracked, 0, None, response2)
    assert result2["validation"]["overall_validation_status"] == "passed"
    ledger2 = result2["ledger"]
    assert ledger2 is not None

    response3 = retest.phase2d.build_expected_transport_delta(
        tracked, turns[1], ledger1
    )
    response3["commitment_changes"] = []
    question = retest.phase2d._proposition(
        "new-proposition-3",
        "Which entrance should pedestrians use?",
        turns[1]["participant"]["participant_id"],
        [{"exact_text": "Which entrance should pedestrians use?", "occurrence_index": 0}],
        speech_act="question",
        proposition_kind="question_presupposition",
        epistemic_status="questioned",
        commitment_status="speaker_not_committed",
    )
    response3["new_propositions"].append(question)

    response4 = retest.phase2d.build_expected_transport_delta(
        tracked, turns[1], ledger2
    )
    result4 = _processed(tracked, 1, ledger2, response4)
    assert result4["validation"]["overall_validation_status"] == "passed"
    ledger4 = result4["ledger"]
    assert ledger4 is not None

    response6 = retest.phase2d.build_expected_transport_delta(
        tracked, turns[2], ledger4
    )
    result6 = _processed(tracked, 2, ledger4, response6)
    assert result6["validation"]["overall_validation_status"] == "passed"

    log = retest.phase2d.make_call_log(tracked)
    states = {1: "failed", 2: "failed", 3: "failed", 4: "completed", 5: "blocked", 6: "completed"}
    for entry in log["entries"]:
        order = int(entry["order"])
        attempted = order != 5
        entry.update(
            state=states[order],
            attempt_number=int(attempted),
            provider_call_count=int(attempted),
            attempted_at_utc="2026-09-01T00:00:00Z" if attempted else None,
            finished_at_utc="2026-09-01T00:00:01Z",
        )
        if order == 5:
            entry["blocked_by_order"] = 3
    log.update(attempted_call_count=5, provider_call_count=5)
    retest.base._write_private_json(source / "call-log.json", log)

    responses = {1: response1, 2: response2, 3: response3, 4: response4, 6: response6}
    ledgers = {1: ledger1, 2: ledger2, 4: ledger4, 6: result6["ledger"]}
    entries = {int(item["order"]): item for item in log["entries"]}
    for order, response in responses.items():
        call = _call_dir(source, entries[order])
        retest.base._ensure_private_directory(call)
        retest.base._write_private_bytes(
            call / "raw-response.txt",
            retest.canonical_json_bytes(_old_versions(response)),
        )
        if order in ledgers:
            retest.base._write_private_json(
                call / "materialised-ledger.json", ledgers[order]
            )
    retest.base.write_checksums(source)
    return source


def test_four_call_plan_and_request_controls(tracked: Mapping[str, Any]) -> None:
    requests, priors = retest.construct_four_local_requests(tracked)
    log = retest.make_call_log(tracked)

    assert [(item["model"], item["turn_index"]) for item in log["entries"]] == [
        ("grok-4.6", 0),
        ("grok-4.3", 0),
        ("grok-4.3", 1),
        ("grok-4.3", 2),
    ]
    assert [item["dependency_order"] for item in log["entries"]] == [None, None, 2, 3]
    assert len(requests) == log["planned_call_count"] == 4
    assert set(priors) == {"grok-4.3", "grok-4.6"}
    assert all(request["max_tokens"] == 8192 for request in requests)
    assert all(request["reasoning_effort"] == "low" for request in requests)
    assert all(request["tools"] == [] and "tool_choice" not in request for request in requests)
    assert all(request["application_retry_count"] == 0 for request in requests)


def test_issue_level_no_stable_issue_with_content_passes(
    tracked: Mapping[str, Any],
) -> None:
    turn = tracked["chain"]["turns"][0]
    response = retest.phase2d.build_expected_transport_delta(tracked, turn, None)
    response["abstentions"] = ["no_stable_issue"]
    response["new_issue_states"] = [
        _sentinel(turn, response["new_propositions"][0]["local_ref"])
    ]

    result = _processed(tracked, 0, None, response)
    checks = result["validation"]["semantic_checks"]["checks"]

    assert result["validation"]["overall_validation_status"] == "passed"
    assert checks["bridge_closed_proposition"] is True
    assert checks["speaker_commitment_to_closed_proposition"] is True
    assert checks["not_abstained"] is True or checks.get("no_unsupported_abstention") is True


def test_no_stable_issue_cannot_replace_explicit_correction_and_question(
    tracked: Mapping[str, Any],
) -> None:
    first = _processed(tracked, 0, None)
    turn = tracked["chain"]["turns"][1]
    response = retest.phase2d.build_expected_transport_delta(
        tracked, turn, first["ledger"]
    )
    for field in (
        "new_propositions",
        "commitment_changes",
        "new_relations",
        "new_issue_states",
    ):
        response[field] = []
    response["abstentions"] = ["no_stable_issue"]
    response["new_issue_states"] = [_sentinel(turn)]

    result = _processed(tracked, 1, first["ledger"], response)
    checks = result["validation"]["semantic_checks"]

    assert result["validation"]["structural_validity_status"] == "passed"
    assert result["validation"]["semantic_expectation_status"] == "failed"
    assert {
        "bridge_open_proposition",
        "east_entrance_blocked_proposition",
        "correction_references_prior_closed_proposition",
        "open_entrance_question",
    } <= set(checks["failed_checks"])


def test_corrected_offline_classification_of_five_phase2d_responses(
    tmp_path: Path, tracked: Mapping[str, Any]
) -> None:
    source = _historical_fixture(tmp_path / "phase2d-run", tracked)
    output = tmp_path / "phase2e-run"

    report = retest.offline_recheck(
        output,
        source_run=source,
        environment_check=_offline_environment,
    )

    assert report["provider_calls_made"] == 0
    assert report["saved_response_count"] == 5
    assert report["root_version_rebinding_only"] is True
    assert report["historical_files_modified"] is False
    assert {
        row["source_order"]: row["corrected_classification"]
        for row in report["classifications"]
    } == retest.PHASE2D_EXPECTED
    row3 = next(row for row in report["classifications"] if row["source_order"] == 3)
    assert row3["historical_predecessor_order"] == 1
    assert row3["provider_transport_schema_status"] == "failed"
    assert _load(output / "offline-recheck.json") == report


@pytest.mark.parametrize(
    ("failed", "attempted", "states"),
    [
        ({2}, [1, 2], ["completed", "failed", "blocked", "blocked"]),
        ({3}, [1, 2, 3], ["completed", "completed", "failed", "blocked"]),
    ],
)
def test_grok43_dependency_blocking(
    tmp_path: Path,
    tracked: Mapping[str, Any],
    failed: set[int],
    attempted: list[int],
    states: list[str],
) -> None:
    output = tmp_path / f"blocked-{next(iter(failed))}"
    _prepare(output)
    transport = FakeTransport(tracked, failing_orders=failed)

    summary = retest.run_probe(
        output,
        confirm_calls=4,
        transport=transport,
        environment_check=_offline_environment,
    )
    log = _load(output / "call-log.json")

    assert transport.orders == attempted
    assert [entry["state"] for entry in log["entries"]] == states
    assert summary["provider_call_count"] == len(attempted)
    assert summary["retry_call_count"] == 0
    assert summary["repair_call_count"] == 0
    assert summary["fallback_call_count"] == 0
    verified = retest.verify_run(output, environment_check=_offline_environment)
    assert verified["status"] == "passed"
    assert verified["provider_calls_made"] == 0
    assert verified["saved_responses_reprocessed"] == (1 if failed == {2} else 2)


def test_each_live_call_uses_own_model_predecessor(
    tmp_path: Path, tracked: Mapping[str, Any]
) -> None:
    output = tmp_path / "own-predecessor"
    _prepare(output)
    transport = FakeTransport(tracked)
    summary = retest.run_probe(
        output,
        confirm_calls=4,
        transport=transport,
        environment_check=_offline_environment,
    )
    log = _load(output / "call-log.json")
    entries = {entry["order"]: entry for entry in log["entries"]}
    ledger2 = _load(_call_dir(output, entries[2]) / "materialised-ledger.json")
    ledger3 = _load(_call_dir(output, entries[3]) / "materialised-ledger.json")

    assert transport.orders == [1, 2, 3, 4]
    assert transport.contexts[1]["prior_ledger"] is None
    assert transport.contexts[2]["prior_ledger"] is None
    assert transport.contexts[3]["prior_ledger"]["ledger_sha256"] == ledger2["ledger_sha256"]
    assert transport.contexts[4]["prior_ledger"]["ledger_sha256"] == ledger3["ledger_sha256"]
    assert summary["completed_call_count"] == 4
    assert summary["provider_call_count"] == 4


def test_verify_reprocesses_offline_and_live_without_provider(
    tmp_path: Path,
    tracked: Mapping[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _historical_fixture(tmp_path / "phase2d-source", tracked)
    output = tmp_path / "verified-run"
    retest.offline_recheck(
        output,
        source_run=source,
        environment_check=_offline_environment,
    )
    _prepare(output)
    transport = FakeTransport(tracked)
    retest.run_probe(
        output,
        confirm_calls=4,
        transport=transport,
        environment_check=_offline_environment,
    )
    before = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    attempts: list[str] = []

    def denied(*_args: Any, **_kwargs: Any) -> Any:
        attempts.append("network")
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        retest,
        "XaiTransport",
        lambda: pytest.fail("verify constructed provider transport"),
    )

    result = retest.verify_run(
        output,
        source_run=source,
        environment_check=_offline_environment,
    )
    after = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }

    assert attempts == []
    assert result["status"] == "passed"
    assert result["provider_calls_made"] == 0
    assert result["saved_responses_reprocessed"] == 4
    assert result["offline_responses_reprocessed"] == 5
    assert result["api_key_required"] is False
    assert result["call_log_unchanged"] is True
    assert result["checksum_status"] == "passed"
    assert before == after


def test_run_requires_exact_confirmation(tmp_path: Path) -> None:
    output = tmp_path / "confirmation"
    _prepare(output)
    forbidden = SimpleNamespace(
        sample=lambda *_args, **_kwargs: pytest.fail("provider call attempted")
    )
    for value in (None, 0, 3, 5):
        with pytest.raises(retest.ProbeError, match="requires --confirm-calls 4"):
            retest.run_probe(
                output,
                confirm_calls=value,
                transport=forbidden,
                environment_check=_offline_environment,
            )
