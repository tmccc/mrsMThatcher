from __future__ import annotations

import copy
import json
import socket
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import proposition_ledger_xai_semantic_probe as probe


def _offline_environment() -> dict[str, str]:
    return {
        "python_executable": "synthetic-test-python",
        "xai_sdk_version": probe.PINNED_XAI_SDK_VERSION,
    }


def _offline_compiler(
    requests: Sequence[Mapping[str, Any]], tracked: Mapping[str, Any]
) -> dict[str, Any]:
    assert len(requests) == probe.PROVIDER_CALL_BUDGET
    assert all(
        request["response_format"]["schema"] == tracked["provider_schema"]
        for request in requests
    )
    return {
        "requests_constructed": len(requests),
        "provider_calls_made": 0,
        "transport_rpc_invocations": 0,
        "full_schema_in_response_format": True,
        "full_schema_in_messages": False,
    }


def _prepare(output: Path) -> dict[str, Any]:
    return probe.prepare_run(
        output,
        environment_check=_offline_environment,
        local_compiler=_offline_compiler,
    )


def _deny_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    attempts: list[str] = []

    def denied(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        attempts.append("network")
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    return attempts


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _call_directory(output: Path, entry: Mapping[str, Any]) -> Path:
    return output / (
        f"call-{int(entry['order']):02d}-{entry['case_id']}-"
        f"turn-{entry['turn_index']}-{entry['model']}"
    )


def _call_artifact(
    output: Path, entry: Mapping[str, Any], name: str
) -> dict[str, Any]:
    return _load_object(_call_directory(output, entry) / name)


def _process_witness(
    tracked: Mapping[str, Any],
    turn_index: int,
    prior: Mapping[str, Any] | None,
) -> dict[str, Any]:
    turn = tracked["chain"]["turns"][turn_index]
    response = probe.build_expected_transport_delta(tracked, turn, prior)
    return probe.process_response_bytes(
        probe.canonical_json_bytes(response),
        turn=turn,
        prior_ledger=prior,
        tracked=tracked,
    )


def _witness_chain(tracked: Mapping[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    prior: Mapping[str, Any] | None = None
    for turn_index in range(3):
        processed = _process_witness(tracked, turn_index, prior)
        assert processed["validation"]["overall_validation_status"] == "passed"
        assert processed["ledger"] is not None
        results.append(processed)
        prior = processed["ledger"]
    return results


class FakeTransport:
    """Return local semantic witnesses and record each one-shot sample."""

    def __init__(
        self,
        tracked: Mapping[str, Any],
        *,
        failing_orders: set[int] | None = None,
    ) -> None:
        self.tracked = tracked
        self.failing_orders = set(failing_orders or ())
        self.attempted_orders: list[int] = []
        self.requests: dict[int, dict[str, Any]] = {}
        self.contexts: dict[int, dict[str, Any]] = {}

    def sample(
        self,
        entry: Mapping[str, Any],
        request: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> probe.ProviderObservation:
        order = int(entry["order"])
        self.attempted_orders.append(order)
        self.requests[order] = copy.deepcopy(dict(request))
        self.contexts[order] = copy.deepcopy(dict(context))
        if order in self.failing_orders:
            raise RuntimeError("synthetic provider failure")

        turn = context["turn"]
        prior = context["prior_ledger"]
        response = probe.build_expected_transport_delta(self.tracked, turn, prior)
        if int(entry["turn_index"]) == 0:
            response["new_propositions"][0]["canonical_text"] += (
                f" Extracted by {entry['model']}."
            )
        return probe.ProviderObservation(
            raw_text=probe.canonical_json_bytes(response).decode("utf-8"),
            returned_model_id=str(entry["model"]),
            finish_reason="stop",
            usage={
                "prompt_tokens": 100 + order,
                "reasoning_tokens": order,
                "completion_tokens": 20 + order,
                "total_tokens": 120 + (3 * order),
            },
        )


@pytest.fixture(scope="module")
def tracked() -> dict[str, Any]:
    return probe.validate_tracked_inputs()


@pytest.fixture(scope="module")
def local_requests(
    tracked: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    return probe.construct_six_local_requests(tracked)


@pytest.fixture(scope="module")
def successful_run(
    tmp_path_factory: pytest.TempPathFactory,
    tracked: Mapping[str, Any],
) -> tuple[Path, FakeTransport, dict[str, Any]]:
    output = tmp_path_factory.mktemp("phase2d-success") / "private-run"
    _prepare(output)
    transport = FakeTransport(tracked)
    summary = probe.run_probe(
        output,
        confirm_calls=probe.PROVIDER_CALL_BUDGET,
        transport=transport,
        environment_check=_offline_environment,
    )
    return output, transport, summary


def test_prepare_and_prepared_verify_make_no_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = _deny_network(monkeypatch)
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    class ForbiddenLiveTransport:
        def __init__(self) -> None:
            raise AssertionError("offline mode constructed the live transport")

    monkeypatch.setattr(probe, "XaiTransport", ForbiddenLiveTransport)
    output = tmp_path / "prepared-private-run"
    prepared = _prepare(output)
    verified = probe.verify_run(output, environment_check=_offline_environment)

    assert attempts == []
    assert prepared["planned_calls"] == 6
    assert prepared["provider_calls"] == 0
    assert verified == {
        "status": "passed_prepared",
        "provider_calls_made": 0,
        "saved_responses_reprocessed": 0,
        "api_key_required": False,
    }


def test_exactly_six_calls_are_planned_as_two_independent_three_turn_chains(
    tracked: Mapping[str, Any],
    local_requests: tuple[list[dict[str, Any]], dict[str, dict[str, Any]]],
) -> None:
    requests, final_priors = local_requests
    log = probe.make_call_log(tracked)

    assert len(requests) == len(log["entries"]) == 6
    assert log["planned_call_count"] == 6
    assert Counter(entry["model"] for entry in log["entries"]) == {
        "grok-4.3": 3,
        "grok-4.6": 3,
    }
    assert [(entry["turn_index"], entry["model"]) for entry in log["entries"]] == [
        (0, "grok-4.3"),
        (0, "grok-4.6"),
        (1, "grok-4.3"),
        (1, "grok-4.6"),
        (2, "grok-4.3"),
        (2, "grok-4.6"),
    ]
    assert [entry["dependency_order"] for entry in log["entries"]] == [
        None,
        None,
        1,
        2,
        3,
        4,
    ]
    assert all(entry["state"] == "planned" for entry in log["entries"])
    assert set(final_priors) == {"grok-4.3", "grok-4.6"}
    assert all(prior["as_of_turn_index"] == 2 for prior in final_priors.values())


def test_v3_prompt_and_semantic_gate_prevent_empty_no_stable_issue(
    tracked: Mapping[str, Any],
) -> None:
    prompt = tracked["system_prompt"]
    assert (
        "`no_stable_issue` means that no stable issue or question under discussion "
        "can be identified" in prompt
    )
    assert "does not permit omission of explicit propositions" in prompt
    assert "A turn can contain explicit propositions even when it creates no issue" in prompt
    assert "explicit assertions must still be represented" in prompt

    turn = tracked["chain"]["turns"][0]
    response = probe.build_expected_transport_delta(tracked, turn, None)
    response["new_propositions"] = []
    response["commitment_changes"] = []
    response["extraction_status"] = "abstained"
    response["abstentions"] = ["no_stable_issue"]
    processed = probe.process_response_bytes(
        probe.canonical_json_bytes(response),
        turn=turn,
        prior_ledger=None,
        tracked=tracked,
    )

    validation = processed["validation"]
    assert validation["structural_validity_status"] == "passed"
    assert validation["semantic_expectation_status"] == "failed"
    assert validation["overall_validation_status"] == "failed"
    checks = validation["semantic_checks"]
    assert checks["checks"]["not_abstained"] is False
    assert checks["checks"]["semantic_records_present"] is False


def test_explicit_proposition_without_issue_is_valid(
    tracked: Mapping[str, Any],
) -> None:
    processed = _process_witness(tracked, 0, None)
    delta = processed["canonical"]
    checks = processed["validation"]["semantic_checks"]["checks"]

    assert processed["validation"]["overall_validation_status"] == "passed"
    assert delta["new_issue_states"] == []
    assert delta["abstentions"] == []
    assert len(delta["new_propositions"]) == 1
    proposition_ref = delta["new_propositions"][0]["local_ref"]
    assert any(
        change["operation"] == "add"
        and change["participant_id"] == "participant-contributor"
        and change["proposition_ref"] == proposition_ref
        and change["stance"] == "asserted"
        for change in delta["commitment_changes"]
    )
    assert checks["explicit_proposition_without_issue_valid"] is True


def test_issue_level_no_stable_issue_can_coexist_with_complete_extraction(
    tracked: Mapping[str, Any],
) -> None:
    turn = tracked["chain"]["turns"][0]
    response = probe.build_expected_transport_delta(tracked, turn, None)
    response["abstentions"] = ["no_stable_issue"]
    response["new_issue_states"] = [
        {
            "local_ref": "new-issue-1",
            "initiating_speaker": "participant-contributor",
            "canonical_question": None,
            "issue_type": "no_stable_issue",
            "live_alternatives": [],
            "addressed_participant": None,
            "answer_requirements": [],
            "related_proposition_refs": ["new-proposition-1"],
            "status": "no_stable_issue",
            "resolution_type": "no_stable_issue",
            "confidence": 1.0,
            "exact_evidence_spans": [
                {"exact_text": turn["exact_text"], "occurrence_index": 0}
            ],
        }
    ]

    processed = probe.process_response_bytes(
        probe.canonical_json_bytes(response),
        turn=turn,
        prior_ledger=None,
        tracked=tracked,
    )

    assert processed["validation"]["structural_validity_status"] == "passed"
    assert processed["validation"]["semantic_expectation_status"] == "passed"
    assert processed["validation"]["overall_validation_status"] == "passed"
    assert processed["validation"]["semantic_checks"]["checks"]["not_abstained"]
    assert len(processed["canonical"]["new_propositions"]) == 1
    assert len(processed["canonical"]["commitment_changes"]) == 1


def test_no_stable_issue_does_not_substitute_for_explicit_question(
    tracked: Mapping[str, Any],
) -> None:
    genesis = _process_witness(tracked, 0, None)
    turn = tracked["chain"]["turns"][1]
    response = probe.build_expected_transport_delta(tracked, turn, genesis["ledger"])
    response["abstentions"] = ["no_stable_issue"]
    response["new_issue_states"] = [
        {
            "local_ref": "new-issue-1",
            "initiating_speaker": "participant-account",
            "canonical_question": None,
            "issue_type": "no_stable_issue",
            "live_alternatives": [],
            "addressed_participant": None,
            "answer_requirements": [],
            "related_proposition_refs": ["new-proposition-2"],
            "status": "no_stable_issue",
            "resolution_type": "no_stable_issue",
            "confidence": 1.0,
            "exact_evidence_spans": [
                {"exact_text": turn["exact_text"], "occurrence_index": 0}
            ],
        }
    ]

    processed = probe.process_response_bytes(
        probe.canonical_json_bytes(response),
        turn=turn,
        prior_ledger=genesis["ledger"],
        tracked=tracked,
    )

    assert processed["validation"]["structural_validity_status"] == "passed"
    assert processed["validation"]["semantic_expectation_status"] == "failed"
    checks = processed["validation"]["semantic_checks"]
    assert checks["checks"]["open_entrance_question"] is False
    assert checks["checks"]["not_abstained"] is False


def test_correction_and_question_reference_valid_prior_objects(
    tracked: Mapping[str, Any],
) -> None:
    genesis = _process_witness(tracked, 0, None)
    processed = _process_witness(tracked, 1, genesis["ledger"])
    delta = processed["canonical"]
    prior_ids = {
        item["proposition_id"] for item in genesis["ledger"]["propositions"]
    }
    opened = {
        item["local_ref"]
        for item in delta["new_propositions"]
        if "bridge" in item["canonical_text"].lower()
        and "open" in item["canonical_text"].lower()
    }

    assert processed["validation"]["semantic_reference"]["status"] == "passed"
    assert processed["validation"]["overall_validation_status"] == "passed"
    assert any(
        relation["relation_type"] in {"corrects", "contradicts", "supersedes"}
        and opened.intersection(relation["source_proposition_refs"])
        and prior_ids.intersection(relation["target_proposition_refs"])
        for relation in delta["new_relations"]
    )
    assert any(
        issue["status"] == "open"
        and "entrance" in issue["canonical_question"].lower()
        for issue in delta["new_issue_states"]
    )
    assert len(
        [
            item
            for item in delta["new_propositions"]
            if "bridge" in item["canonical_text"].lower()
            and "open" in item["canonical_text"].lower()
        ]
    ) == 1
    assert len(
        [
            item
            for item in delta["new_propositions"]
            if "east" in item["canonical_text"].lower()
            and "blocked" in item["canonical_text"].lower()
        ]
    ) == 1


def test_withdrawal_and_resolution_reference_existing_live_objects(
    tracked: Mapping[str, Any],
) -> None:
    genesis, second, third = _witness_chain(tracked)
    del genesis
    prior = second["ledger"]
    delta = third["canonical"]
    prior_commitment_ids = {
        item["commitment_id"]
        for item in prior["participant_commitments"]
        if item["participant_id"] == "participant-contributor"
    }
    live_issue_ids = {
        item["issue_id"]
        for item in prior["issue_states"]
        if item["status"] in {"open", "partly_answered", "challenged"}
    }

    assert third["validation"]["semantic_reference"]["status"] == "passed"
    assert third["validation"]["overall_validation_status"] == "passed"
    assert any(
        change["operation"] == "update"
        and change["commitment_id"] in prior_commitment_ids
        and change["changes"]["stance"] == "withdrawn"
        for change in delta["commitment_changes"]
    )
    answered_ids = {
        update["issue_id"]
        for update in delta["issue_state_updates"]
        if update["changes"]["status"] == "answered"
    }
    resolved_ids = {
        item["item_ref"]
        for item in delta["resolved_items"]
        if item["item_type"] == "issue"
    }
    assert answered_ids
    assert answered_ids == resolved_ids
    assert answered_ids <= live_issue_ids


def test_missing_and_stale_resolved_item_references_fail_closed(
    tracked: Mapping[str, Any],
) -> None:
    genesis = _process_witness(tracked, 0, None)
    second = _process_witness(tracked, 1, genesis["ledger"])
    prior = second["ledger"]
    turn = tracked["chain"]["turns"][2]
    response = probe.build_expected_transport_delta(tracked, turn, prior)
    existing_id = response["resolved_items"][0]["item_ref"]
    last = "0" if existing_id[-1] != "0" else "1"
    missing_id = existing_id[:-1] + last
    response["resolved_items"][0]["item_ref"] = missing_id

    missing = probe.process_response_bytes(
        probe.canonical_json_bytes(response),
        turn=turn,
        prior_ledger=prior,
        tracked=tracked,
    )
    assert missing["validation"]["canonical_semantic_schema"]["status"] == "passed"
    assert missing["validation"]["semantic_reference"]["status"] == "failed"
    assert missing["ledger"] is None
    assert any(
        missing_id in error
        for error in missing["validation"]["semantic_reference"]["errors"]
    )

    valid_third = probe.process_response_bytes(
        probe.canonical_json_bytes(
            probe.build_expected_transport_delta(tracked, turn, prior)
        ),
        turn=turn,
        prior_ledger=prior,
        tracked=tracked,
    )
    assert valid_third["validation"]["overall_validation_status"] == "passed"
    resolved_prior = valid_third["ledger"]
    assert any(
        item["item_type"] == "issue" and item["item_id"] == existing_id
        for item in resolved_prior["resolved_items"]
    )
    stale_response = probe.build_expected_transport_delta(tracked, turn, resolved_prior)
    stale_errors = probe.semantic_reference_errors(stale_response, resolved_prior)
    assert any(
        "resolved_item_already_resolved:issue:" in error for error in stale_errors
    )
    stale = probe.process_response_bytes(
        probe.canonical_json_bytes(stale_response),
        turn=turn,
        prior_ledger=resolved_prior,
        tracked=tracked,
    )
    assert stale["validation"]["semantic_reference"]["status"] == "failed"
    assert stale["validation"]["semantic_reference"]["errors"] == stale_errors
    assert stale["validation"]["deterministic_materialisation"]["status"] == "not_run"
    assert stale["validation"]["persisted_ledger"]["status"] == "not_run"
    assert stale["validation"]["overall_validation_status"] == "failed"
    assert stale["ledger"] is None


def test_exact_evidence_transport_resolver_is_used(
    tracked: Mapping[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    original = probe.evidence.resolve_transport_delta

    def recording_resolver(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, copy.deepcopy(kwargs)))
        return original(*args, **kwargs)

    monkeypatch.setattr(probe.evidence, "resolve_transport_delta", recording_resolver)
    processed = _process_witness(tracked, 0, None)

    assert len(calls) == 1
    assert processed["validation"]["evidence_resolution"]["status"] == "passed"
    summary = processed["validation"]["resolution_summary"]
    assert summary["resolver_version"] == probe.evidence.EVIDENCE_RESOLVER_VERSION
    assert summary["selector_count"] > 0
    selector = processed["parsed"]["new_propositions"][0]["exact_evidence_spans"][0]
    span = processed["canonical"]["new_propositions"][0]["exact_evidence_spans"][0]
    assert set(selector) == {"exact_text", "occurrence_index"}
    assert set(span) == {"turn_id", "start_char", "end_char", "exact_text"}


def test_provider_schema_stays_out_of_messages_and_request_is_one_shot(
    tracked: Mapping[str, Any],
    local_requests: tuple[list[dict[str, Any]], dict[str, dict[str, Any]]],
) -> None:
    requests, _ = local_requests
    schema_encodings = [
        probe.canonical_json_bytes(tracked[name]).decode("utf-8")
        for name in ("canonical_schema", "transport_schema", "provider_schema")
    ]
    for request in requests:
        assert request["response_format"] == {
            "format_type": "json_schema",
            "schema": tracked["provider_schema"],
        }
        assert all(
            schema not in message["content"]
            for schema in schema_encodings
            for message in request["messages"]
        )
        assert request["max_tokens"] == 8192
        assert request["reasoning_effort"] == "low"
        assert request["tools"] == []
        assert "tool_choice" not in request
        assert request["tool_choice_parameter_sent"] is False
        assert request["application_retry_count"] == 0
        assert request["sdk_grpc_retries"] is False
        assert request["fallback_model"] is None
        assert request["store_messages"] is False
        assert request["streaming"] is False
        assert request["no_retry_channel_options"] == [
            ["grpc.enable_retries", 0],
            ["grpc.service_config", "{}"],
        ]


def test_each_live_call_uses_only_its_models_materialised_predecessor(
    successful_run: tuple[Path, FakeTransport, dict[str, Any]],
) -> None:
    output, transport, summary = successful_run
    log = _load_object(output / "call-log.json")
    ledgers = {
        int(entry["order"]): _call_artifact(
            output, entry, "materialised-ledger.json"
        )
        for entry in log["entries"]
    }

    assert ledgers[1]["ledger_sha256"] != ledgers[2]["ledger_sha256"]
    for order, dependency in ((3, 1), (4, 2), (5, 3), (6, 4)):
        prior = transport.contexts[order]["prior_ledger"]
        assert prior["ledger_sha256"] == ledgers[dependency]["ledger_sha256"]
        other_dependency = dependency + 1 if dependency % 2 else dependency - 1
        assert prior["ledger_sha256"] != ledgers[other_dependency]["ledger_sha256"]
        payload = probe.strict_json_loads(
            transport.requests[order]["messages"][1]["content"].encode("utf-8")
        )
        assert payload["validated_prior_persisted_ledger"] == prior
    assert summary["three_turn_chain_structurally_valid_by_model"] == {
        "grok-4.3": True,
        "grok-4.6": True,
    }
    assert summary["three_turn_semantic_expectations_passed_by_model"] == {
        "grok-4.3": True,
        "grok-4.6": True,
    }


def test_valid_fake_responses_complete_all_validation_and_materialisation(
    successful_run: tuple[Path, FakeTransport, dict[str, Any]],
) -> None:
    output, transport, summary = successful_run
    log = _load_object(output / "call-log.json")

    assert transport.attempted_orders == [1, 2, 3, 4, 5, 6]
    assert summary["attempted_call_count"] == 6
    assert summary["provider_call_count"] == 6
    assert summary["completed_call_count"] == 6
    assert summary["failed_call_count"] == 0
    assert summary["blocked_call_count"] == 0
    assert summary["server_acceptance_success_count"] == 6
    assert summary["structural_success_count"] == 6
    assert summary["transport_success_count"] == 6
    assert summary["canonical_success_count"] == 6
    assert summary["semantic_reference_success_count"] == 6
    assert summary["materialisation_success_count"] == 6
    assert summary["persisted_ledger_success_count"] == 6
    assert summary["semantic_expectation_success_count"] == 6
    assert summary["maximum_output_tokens"] == 8192
    assert summary["output_ceiling_reached_count"] == 0
    assert summary["retry_call_count"] == 0
    assert summary["repair_call_count"] == 0
    assert summary["fallback_call_count"] == 0

    for entry in log["entries"]:
        validation = _call_artifact(output, entry, "validation.json")
        assert validation["overall_validation_status"] == "passed"
        assert validation["structural_validity_status"] == "passed"
        assert validation["semantic_expectation_status"] == "passed"
        assert validation["materialiser_status"] == "ok"
        assert validation["deterministic_materialisation_equal"] is True
        for stage in validation["validation_sequence"][:-1]:
            assert validation[stage]["status"] == "passed"


def test_verify_reprocesses_saved_responses_deterministically_without_api(
    successful_run: tuple[Path, FakeTransport, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, transport, _summary = successful_run
    attempts = _deny_network(monkeypatch)
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    class ForbiddenLiveTransport:
        def __init__(self) -> None:
            raise AssertionError("verify constructed the live transport")

    monkeypatch.setattr(probe, "XaiTransport", ForbiddenLiveTransport)
    before = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    verified = probe.verify_run(output, environment_check=_offline_environment)
    after = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }

    assert attempts == []
    assert transport.attempted_orders == [1, 2, 3, 4, 5, 6]
    assert verified["status"] == "passed"
    assert verified["provider_calls_made"] == 0
    assert verified["saved_responses_reprocessed"] == 6
    assert verified["api_key_required"] is False
    assert verified["call_log_unchanged"] is True
    assert verified["checksum_status"] == "passed"
    assert before == after


def test_failed_predecessor_is_attempted_once_and_blocks_only_same_model_chain(
    tmp_path: Path,
    tracked: Mapping[str, Any],
) -> None:
    output = tmp_path / "failed-predecessor-private-run"
    _prepare(output)
    transport = FakeTransport(tracked, failing_orders={1})
    summary = probe.run_probe(
        output,
        confirm_calls=6,
        transport=transport,
        environment_check=_offline_environment,
    )
    log = _load_object(output / "call-log.json")

    assert transport.attempted_orders == [1, 2, 4, 6]
    assert Counter(transport.attempted_orders) == {1: 1, 2: 1, 4: 1, 6: 1}
    assert [entry["state"] for entry in log["entries"]] == [
        "failed",
        "completed",
        "blocked",
        "completed",
        "blocked",
        "completed",
    ]
    assert log["entries"][2]["blocked_by_order"] == 1
    assert log["entries"][4]["blocked_by_order"] == 3
    assert log["entries"][2]["provider_call_count"] == 0
    assert log["entries"][4]["provider_call_count"] == 0
    assert summary["attempted_call_count"] == 4
    assert summary["provider_call_count"] == 4
    assert summary["failed_call_count"] == 1
    assert summary["blocked_call_count"] == 2
    assert summary["completed_call_count"] == 3
    assert summary["retry_call_count"] == 0
    assert summary["repair_call_count"] == 0
    assert summary["fallback_call_count"] == 0

    with pytest.raises(probe.ProbeError):
        probe.run_probe(
            output,
            confirm_calls=6,
            transport=transport,
            environment_check=_offline_environment,
        )
    assert transport.attempted_orders == [1, 2, 4, 6]


def test_raw_response_is_preserved_before_local_processing_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "local-processing-failure-private-run"
    _prepare(output)
    raw_text = '{"verbatim":"café ☕"}\r\n'
    raw_bytes = raw_text.encode("utf-8")
    attempted_orders: list[int] = []
    processing_inputs: list[bytes] = []

    class RawResponseTransport:
        def sample(
            self,
            entry: Mapping[str, Any],
            _request: Mapping[str, Any],
            _context: Mapping[str, Any],
        ) -> probe.ProviderObservation:
            attempted_orders.append(int(entry["order"]))
            return probe.ProviderObservation(
                raw_text=raw_text,
                returned_model_id=str(entry["model"]),
                finish_reason="stop",
                usage={"completion_tokens": 7, "total_tokens": 11},
            )

    def fail_local_processing(raw: bytes, **_kwargs: Any) -> dict[str, Any]:
        processing_inputs.append(raw)
        raise RuntimeError("synthetic local validation failure")

    monkeypatch.setattr(probe, "process_response_bytes", fail_local_processing)
    summary = probe.run_probe(
        output,
        confirm_calls=6,
        transport=RawResponseTransport(),
        environment_check=_offline_environment,
    )
    log = _load_object(output / "call-log.json")
    first = log["entries"][0]

    assert attempted_orders.count(1) == 1
    assert processing_inputs[0] == raw_bytes
    assert first["state"] == "failed"
    assert first["attempt_number"] == 1
    assert first["provider_call_count"] == 1
    assert (_call_directory(output, first) / "raw-response.txt").read_bytes() == raw_bytes
    assert summary["retry_call_count"] == 0


def test_run_requires_exact_six_call_confirmation(tmp_path: Path) -> None:
    output = tmp_path / "confirmation-private-run"
    _prepare(output)
    transport = SimpleNamespace(
        sample=lambda *_args, **_kwargs: pytest.fail("provider call was attempted")
    )
    for value in (None, 0, 5, 7):
        with pytest.raises(probe.ProbeError, match="requires --confirm-calls 6"):
            probe.run_probe(
                output,
                confirm_calls=value,
                transport=transport,
                environment_check=_offline_environment,
            )


def test_xai_sample_boundary_omits_tool_choice_and_samples_once(
    tracked: Mapping[str, Any],
    local_requests: tuple[list[dict[str, Any]], dict[str, dict[str, Any]]],
) -> None:
    request = local_requests[0][0]
    response_formats: list[dict[str, Any]] = []
    create_calls: list[dict[str, Any]] = []
    sample_calls: list[str] = []
    usage_object = object()

    def response_format(**kwargs: Any) -> dict[str, Any]:
        response_formats.append(copy.deepcopy(kwargs))
        return {"synthetic_response_format": copy.deepcopy(kwargs)}

    class FakeChat:
        def sample(self) -> Any:
            sample_calls.append("sample")
            return SimpleNamespace(
                content="{}",
                usage=usage_object,
                proto=SimpleNamespace(model="grok-4.3"),
                finish_reason="stop",
            )

    class FakeChatEndpoint:
        def create(self, **kwargs: Any) -> FakeChat:
            create_calls.append(copy.deepcopy(kwargs))
            return FakeChat()

    transport = object.__new__(probe.XaiTransport)
    transport._chat_pb2 = SimpleNamespace(
        FORMAT_TYPE_JSON_SCHEMA=7,
        ResponseFormat=response_format,
    )
    transport._system = lambda content: ("system", content)
    transport._user = lambda content: ("user", content)
    transport._client = SimpleNamespace(chat=FakeChatEndpoint())

    def message_to_dict(value: Any, **kwargs: Any) -> dict[str, int]:
        assert value is usage_object
        assert kwargs == {"preserving_proto_field_name": True}
        return {"total_tokens": 123}

    transport._message_to_dict = message_to_dict
    observation = transport.sample(probe.CALL_SPECS[0], request, {})

    assert sample_calls == ["sample"]
    assert len(create_calls) == 1
    call = create_calls[0]
    assert "tool_choice" not in call
    assert call["tools"] == []
    assert call["search_parameters"] is None
    assert call["max_tokens"] == 8192
    assert call["reasoning_effort"] == "low"
    assert call["store_messages"] is False
    assert call["parallel_tool_calls"] is False
    assert response_formats == [
        {
            "format_type": 7,
            "schema": probe.canonical_json_bytes(tracked["provider_schema"]).decode(
                "utf-8"
            ),
        }
    ]
    assert observation == probe.ProviderObservation(
        raw_text="{}",
        returned_model_id="grok-4.3",
        finish_reason="stop",
        usage={"total_tokens": 123},
    )
