from __future__ import annotations

import copy
import importlib.util
import json
import socket
import stat
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import proposition_ledger_xai_transport_live_probe as probe


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_DIR / "tools/proposition_ledger_xai_transport_live_probe.py"


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


class FakeTransport:
    """Return local schema-valid witnesses and record every attempted call."""

    def __init__(self, *, failing_orders: set[int] | None = None) -> None:
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

        case = context["case"]
        turn = context["turn"]
        prior = context["prior_ledger"]
        response = probe.build_expected_transport_delta(case, turn, prior)
        # Make the two genesis ledgers observably model-specific.  This field is
        # semantic content, not evidence, so all hidden evidence checks still pass.
        if entry["case_id"] == "format-chain" and entry["turn_index"] == 0:
            response["new_propositions"][0]["canonical_text"] = (
                f"Synthetic genesis emitted for {entry['model']}."
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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return probe.construct_six_local_requests(tracked)


@pytest.fixture(scope="module")
def successful_run(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, FakeTransport, dict[str, Any]]:
    output = tmp_path_factory.mktemp("phase2c-success") / "private-run"
    _prepare(output)
    transport = FakeTransport()
    summary = probe.run_probe(
        output,
        confirm_calls=probe.PROVIDER_CALL_BUDGET,
        transport=transport,
        environment_check=_offline_environment,
    )
    return output, transport, summary


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


def _call_artifact(
    output: Path, entry: Mapping[str, Any], name: str
) -> dict[str, Any]:
    path = probe._call_directory(output, entry) / name
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _processed_witness(
    tracked: Mapping[str, Any],
    case: Mapping[str, Any],
    turn: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
) -> dict[str, Any]:
    response = probe.build_expected_transport_delta(case, turn, prior)
    return probe.process_response_bytes(
        probe.canonical_json_bytes(response),
        case=case,
        turn=turn,
        prior_ledger=prior,
        tracked=tracked,
    )


def test_import_prepare_and_prepared_verify_are_inert_and_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = _deny_network(monkeypatch)
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    module_name = "_synthetic_phase2c_probe_inert_import"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    imported = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = imported
    try:
        spec.loader.exec_module(imported)
    finally:
        sys.modules.pop(module_name, None)

    class ForbiddenLiveTransport:
        def __init__(self) -> None:
            raise AssertionError("prepare or verify constructed the live transport")

    monkeypatch.setattr(probe, "XaiTransport", ForbiddenLiveTransport)
    output = tmp_path / "prepared-private-run"
    prepared = _prepare(output)
    verified = probe.verify_run(output, environment_check=_offline_environment)

    assert attempts == []
    assert prepared["provider_calls"] == 0
    assert prepared["planned_calls"] == 6
    assert verified == {
        "status": "passed_prepared",
        "provider_calls_made": 0,
        "saved_responses_reprocessed": 0,
        "api_key_required": False,
    }
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE((output / "call-log.json").stat().st_mode) == 0o600


def test_exactly_six_calls_are_planned_three_per_model(
    tracked: Mapping[str, Any],
    local_requests: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    requests, bootstrap_priors = local_requests
    log = probe.make_call_log(tracked)

    assert len(requests) == len(log["entries"]) == 6
    assert log["planned_call_count"] == 6
    assert all(entry["state"] == "planned" for entry in log["entries"])
    assert Counter(entry["model"] for entry in log["entries"]) == {
        "grok-4.3": 3,
        "grok-4.6": 3,
    }
    assert [entry["order"] for entry in log["entries"]] == list(range(1, 7))
    assert [entry["dependency_order"] for entry in log["entries"]] == [
        None,
        None,
        2,
        1,
        None,
        None,
    ]
    assert set(bootstrap_priors) == {"grok-4.3", "grok-4.6"}


def test_unicode_crlf_tab_and_two_spaces_round_trip_exactly(
    tracked: Mapping[str, Any],
) -> None:
    case = tracked["cases"]["format-chain"]
    turn = case["turns"][0]
    expected = "Café 🧭 status is “ready  now”.\r\nCode:\tA-1."
    assert turn["exact_text"] == expected
    assert len(expected) == 42

    request = probe.build_request(tracked, probe.CALL_SPECS[0], None)
    payload = probe.strict_json_loads(
        request["messages"][1]["content"].encode("utf-8")
    )
    assert payload["exact_current_visible_text"] == expected
    assert "\r\n" in payload["exact_current_visible_text"]
    assert "\t" in payload["exact_current_visible_text"]
    assert "ready  now" in payload["exact_current_visible_text"]

    processed = _processed_witness(tracked, case, turn, None)
    assert processed["validation"]["overall_validation_status"] == "passed"
    parsed_span = processed["parsed"]["new_propositions"][0][
        "exact_evidence_spans"
    ][0]
    canonical_span = processed["canonical"]["new_propositions"][0][
        "exact_evidence_spans"
    ][0]
    assert parsed_span["exact_text"] == expected
    assert canonical_span == {
        "turn_id": turn["turn_id"],
        "start_char": 0,
        "end_char": 42,
        "exact_text": expected,
    }
    assert processed["ledger"]["turn_refs"][0]["text_sha256"] == probe.sha256_bytes(
        expected.encode("utf-8")
    )


def test_repeated_phrase_resolves_occurrence_index_one(
    tracked: Mapping[str, Any],
) -> None:
    case = tracked["cases"]["format-chain"]
    genesis = _processed_witness(tracked, case, case["turns"][0], None)
    assert genesis["ledger"] is not None
    turn = case["turns"][1]
    processed = _processed_witness(tracked, case, turn, genesis["ledger"])

    assert processed["validation"]["overall_validation_status"] == "passed"
    selector = processed["parsed"]["new_propositions"][0][
        "exact_evidence_spans"
    ][0]
    span = processed["canonical"]["new_propositions"][0][
        "exact_evidence_spans"
    ][0]
    summary = processed["validation"]["resolution_summary"]["selectors"][0]
    assert selector == {"exact_text": "the gate is open", "occurrence_index": 1}
    assert summary["occurrence_count"] == 2
    assert summary["selected_occurrence_index"] == 1
    assert (span["start_char"], span["end_char"]) == (46, 62)
    assert turn["exact_text"][46:62] == span["exact_text"]


def test_overlapping_matches_count_three_and_index_one_is_middle(
    tracked: Mapping[str, Any],
) -> None:
    case = tracked["cases"]["overlap"]
    turn = case["turns"][0]
    assert probe.evidence.find_overlapping_occurrences(turn["exact_text"], "aa") == (
        (7, 9),
        (8, 10),
        (9, 11),
    )

    processed = _processed_witness(tracked, case, turn, None)
    assert processed["validation"]["overall_validation_status"] == "passed"
    selector = processed["parsed"]["new_propositions"][0][
        "exact_evidence_spans"
    ][0]
    span = processed["canonical"]["new_propositions"][0][
        "exact_evidence_spans"
    ][0]
    summary = processed["validation"]["resolution_summary"]["selectors"][0]
    assert selector == {"exact_text": "aa", "occurrence_index": 1}
    assert summary["occurrence_count"] == 3
    assert (span["start_char"], span["end_char"]) == (8, 10)
    assert turn["exact_text"][8:10] == "aa"


def test_schema_only_in_response_format_and_live_controls_are_fixed(
    tracked: Mapping[str, Any],
    local_requests: tuple[list[dict[str, Any]], dict[str, Any]],
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
        assert "tool_choice" not in request
        assert request["tool_choice_parameter_sent"] is False
        assert request["tools"] == []
        assert request["application_retry_count"] == 0
        assert request["sdk_grpc_retries"] is False
        assert request["fallback_model"] is None
        assert request["no_retry_channel_options"] == [
            ["grpc.enable_retries", 0],
            ["grpc.service_config", "{}"],
        ]
        assert request["max_tokens"] == 4096
        assert request["reasoning_effort"] == "low"
        assert request["store_messages"] is False
        assert request["streaming"] is False


def test_turn_one_uses_only_the_same_models_validated_prior(
    successful_run: tuple[Path, FakeTransport, dict[str, Any]],
) -> None:
    output, transport, _summary = successful_run
    log = probe._load_json(output / "call-log.json", "call log")
    ledger_one = _call_artifact(
        output, log["entries"][0], "materialised-ledger.json"
    )
    ledger_two = _call_artifact(
        output, log["entries"][1], "materialised-ledger.json"
    )
    assert ledger_one["ledger_sha256"] != ledger_two["ledger_sha256"]

    prior_for_46 = transport.contexts[3]["prior_ledger"]
    prior_for_43 = transport.contexts[4]["prior_ledger"]
    assert prior_for_46["ledger_sha256"] == ledger_two["ledger_sha256"]
    assert prior_for_43["ledger_sha256"] == ledger_one["ledger_sha256"]
    assert prior_for_46["ledger_sha256"] != ledger_one["ledger_sha256"]
    assert prior_for_43["ledger_sha256"] != ledger_two["ledger_sha256"]

    payload_46 = probe.strict_json_loads(
        transport.requests[3]["messages"][1]["content"].encode("utf-8")
    )
    payload_43 = probe.strict_json_loads(
        transport.requests[4]["messages"][1]["content"].encode("utf-8")
    )
    assert payload_46["validated_prior_persisted_ledger"] == ledger_two
    assert payload_43["validated_prior_persisted_ledger"] == ledger_one


def test_attempted_call_entry_cannot_be_repeated(
    tmp_path: Path,
    tracked: Mapping[str, Any],
) -> None:
    output = tmp_path / "attempted-private-run"
    _prepare(output)
    log = probe._load_call_log(output, tracked)
    probe._mark_attempted(output, log, 0)
    first_bytes = (output / "call-log.json").read_bytes()

    with pytest.raises(probe.ProbeError, match="only a planned call may be attempted"):
        probe._mark_attempted(output, log, 0)

    assert (output / "call-log.json").read_bytes() == first_bytes
    persisted = probe._load_call_log(output, tracked)
    assert persisted["entries"][0]["state"] == "attempted"
    assert persisted["entries"][0]["provider_call_count"] == 1
    assert persisted["attempted_call_count"] == 1
    assert persisted["provider_call_count"] == 1


def test_one_genesis_failure_blocks_only_its_same_model_dependency(
    tmp_path: Path,
) -> None:
    output = tmp_path / "failed-genesis-private-run"
    _prepare(output)
    transport = FakeTransport(failing_orders={1})
    summary = probe.run_probe(
        output,
        confirm_calls=6,
        transport=transport,
        environment_check=_offline_environment,
    )
    log = probe._load_json(output / "call-log.json", "call log")

    assert transport.attempted_orders == [1, 2, 3, 5, 6]
    assert [entry["state"] for entry in log["entries"]] == [
        "failed",
        "completed",
        "completed",
        "blocked",
        "completed",
        "completed",
    ]
    assert log["entries"][3]["blocked_by_order"] == 1
    assert log["entries"][3]["provider_call_count"] == 0
    assert summary["attempted_call_count"] == 5
    assert summary["provider_call_count"] == 5
    assert summary["failed_call_count"] == 1
    assert summary["blocked_call_count"] == 1
    assert summary["completed_call_count"] == 4
    assert summary["retry_call_count"] == 0


def test_valid_fake_responses_complete_full_validation_and_materialisation(
    successful_run: tuple[Path, FakeTransport, dict[str, Any]],
) -> None:
    output, transport, summary = successful_run
    log = probe._load_json(output / "call-log.json", "call log")

    assert transport.attempted_orders == [1, 2, 3, 4, 5, 6]
    assert summary["attempted_call_count"] == 6
    assert summary["provider_call_count"] == 6
    assert summary["completed_call_count"] == 6
    assert summary["failed_call_count"] == 0
    assert summary["blocked_call_count"] == 0
    assert summary["server_acceptance_success_count"] == 6
    assert summary["transport_success_count"] == 6
    assert summary["canonical_success_count"] == 6
    assert summary["materialisation_success_count"] == 6
    assert summary["persisted_ledger_success_count"] == 6
    assert summary["hidden_expectation_success_count"] == 6
    assert summary["two_turn_chain_completed_by_model"] == {
        "grok-4.3": True,
        "grok-4.6": True,
    }
    assert summary["retry_call_count"] == 0
    assert summary["repair_call_count"] == 0
    assert summary["fallback_call_count"] == 0

    for entry in log["entries"]:
        validation = _call_artifact(output, entry, "validation.json")
        assert validation["overall_validation_status"] == "passed"
        assert validation["structural_validity_status"] == "passed"
        assert validation["hidden_expectation_status"] == "passed"
        assert validation["materialiser_status"] == "ok"
        assert validation["deterministic_materialisation_equal"] is True
        for stage in validation["validation_sequence"][:-1]:
            assert validation[stage]["status"] == "passed"
        call_dir = probe._call_directory(output, entry)
        assert (call_dir / "raw-response.txt").is_file()
        assert (call_dir / "parsed-transport.json").is_file()
        assert (call_dir / "resolved-canonical-delta.json").is_file()
        assert (call_dir / "materialised-ledger.json").is_file()


def test_verify_reprocesses_saved_responses_without_network_or_api_key(
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
    result = probe.verify_run(output, environment_check=_offline_environment)
    after = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }

    assert attempts == []
    assert transport.attempted_orders == [1, 2, 3, 4, 5, 6]
    assert result["status"] == "passed"
    assert result["provider_calls_made"] == 0
    assert result["saved_responses_reprocessed"] == 6
    assert result["api_key_required"] is False
    assert result["call_log_unchanged"] is True
    assert result["checksum_status"] == "passed"
    assert before == after


def test_run_refuses_any_confirmation_other_than_six(tmp_path: Path) -> None:
    output = tmp_path / "confirmation-private-run"
    _prepare(output)
    transport = FakeTransport()
    for value in (None, 0, 5, 7):
        with pytest.raises(probe.ProbeError, match="requires --confirm-calls 6"):
            probe.run_probe(
                output,
                confirm_calls=value,
                transport=transport,
                environment_check=_offline_environment,
            )
    assert transport.attempted_orders == []


def test_cli_prepare_and_verify_dispatch_offline_without_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    attempts = _deny_network(monkeypatch)
    dispatched: list[tuple[str, Path]] = []

    class ForbiddenLiveTransport:
        def __init__(self) -> None:
            raise AssertionError("offline CLI mode constructed the live transport")

    def fake_prepare(output: str | Path) -> dict[str, Any]:
        dispatched.append(("prepare", Path(output)))
        return {"status": "prepared", "provider_calls": 0}

    def fake_verify(output: str | Path) -> dict[str, Any]:
        dispatched.append(("verify", Path(output)))
        return {
            "status": "passed",
            "provider_calls_made": 0,
            "api_key_required": False,
        }

    monkeypatch.setattr(probe, "XaiTransport", ForbiddenLiveTransport)
    monkeypatch.setattr(probe, "prepare_run", fake_prepare)
    monkeypatch.setattr(probe, "verify_run", fake_verify)
    prepare_output = tmp_path / "cli-prepare"
    verify_output = tmp_path / "cli-verify"

    assert probe.main(["--prepare", "--output", str(prepare_output)]) == 0
    assert probe.main(["--verify", "--output", str(verify_output)]) == 0

    captured = capsys.readouterr()
    results = [json.loads(line) for line in captured.out.splitlines()]
    assert results == [
        {"provider_calls": 0, "status": "prepared"},
        {
            "api_key_required": False,
            "provider_calls_made": 0,
            "status": "passed",
        },
    ]
    assert captured.err == ""
    assert dispatched == [("prepare", prepare_output), ("verify", verify_output)]
    assert attempts == []


def test_xai_transport_sample_boundary_omits_tool_choice_and_samples_once(
    tracked: Mapping[str, Any],
    local_requests: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    requests, _ = local_requests
    request = requests[0]
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
    assert call["max_tokens"] == 4096
    assert call["reasoning_effort"] == "low"
    assert call["store_messages"] is False
    assert call["parallel_tool_calls"] is False
    assert call["messages"] == [
        ("system", request["messages"][0]["content"]),
        ("user", request["messages"][1]["content"]),
    ]
    assert response_formats == [
        {
            "format_type": 7,
            "schema": probe.canonical_json_bytes(tracked["provider_schema"]).decode(
                "utf-8"
            ),
        }
    ]
    assert call["response_format"] == {
        "synthetic_response_format": response_formats[0]
    }
    assert observation == probe.ProviderObservation(
        raw_text="{}",
        returned_model_id="grok-4.3",
        finish_reason="stop",
        usage={"total_tokens": 123},
    )


def test_private_run_lock_is_nonblocking_under_contention(tmp_path: Path) -> None:
    output = tmp_path / "lock-target"
    output.mkdir(mode=0o700)

    with probe._exclusive_execution_lock(output):
        with pytest.raises(
            probe.ProbeError,
            match="another probe process holds the private-run lock",
        ):
            with probe._exclusive_execution_lock(output):
                pytest.fail("contended non-blocking lock was acquired")

    with probe._exclusive_execution_lock(output):
        assert output.is_dir()


def test_live_environment_hardening_preserves_only_xai_key_and_disables_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_environment = {
        "XAI_API_KEY": "synthetic-value-not-inspected",
        "PHASE2C_UNRELATED_FAKE_SECRET": "remove-me",
        "PHASE2C_ORDINARY_SETTING": "keep-me",
    }
    monkeypatch.setattr(probe.os, "environ", fake_environment)

    probe._harden_live_environment()

    assert "XAI_API_KEY" in fake_environment
    assert "PHASE2C_UNRELATED_FAKE_SECRET" not in fake_environment
    assert "PHASE2C_ORDINARY_SETTING" in fake_environment
    assert fake_environment["XAI_SDK_DISABLE_SENSITIVE_TELEMETRY_ATTRIBUTES"] == "1"
    assert fake_environment["XAI_SDK_DISABLE_TRACING"] == "1"
