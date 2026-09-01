from __future__ import annotations

import copy
import json
import runpy
import socket
import sys
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from tools import proposition_ledger_semantic_delta as semantic
from tools import proposition_ledger_xai_live_probe as live_probe


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_DIR / "tools/proposition_ledger_xai_live_probe.py"


@pytest.fixture(scope="module")
def tracked_inputs() -> dict[str, Any]:
    return live_probe.validate_tracked_inputs()


@pytest.fixture(scope="module")
def persisted_ledger_schema() -> dict[str, Any]:
    value = json.loads(
        semantic.DEFAULT_LEDGER_SCHEMA_PATH.read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def _valid_delta(tracked: Mapping[str, Any]) -> dict[str, Any]:
    case = tracked["case"]
    participant = case["speaker"]["participant_reference"]
    return {
        "schema_version": semantic.SEMANTIC_DELTA_SCHEMA_VERSION,
        "conversation_key": case["conversation_key"],
        "target_turn_id": case["target_turn_id"],
        "as_of_turn_index": case["turn_index"],
        "prior_ledger_reference": None,
        "new_propositions": [
            {
                "local_ref": "new-proposition-1",
                "canonical_text": case["visible_text"],
                "speaker_or_attributor": {
                    "kind": "speaker",
                    "participant_id": participant,
                    "attributed_participant_id": None,
                },
                "exact_evidence_spans": [
                    copy.deepcopy(case["exact_evidence_span"])
                ],
                "original_language": "en",
                "speech_act": "assertion",
                "proposition_kind": "descriptive",
                "polarity": "positive",
                "modality": {"type": "none", "strength": "none"},
                "quantification": {"type": "none", "surface_marker": None},
                "temporal_scope": {
                    "type": "present",
                    "start": None,
                    "end": None,
                    "surface_marker": None,
                },
                "epistemic_status": "asserted",
                "commitment_status": "speaker_committed",
                "lifecycle_status": "live",
                "proposition_group_ref": None,
                "derivation": {
                    "kind": "direct_span",
                    "source_proposition_refs": [],
                    "normalisation_note": None,
                },
                "confidence": 1.0,
                "uncertainty_reason": None,
            }
        ],
        "proposition_updates": [],
        "new_proposition_groups": [],
        "proposition_group_updates": [],
        "new_issue_states": [],
        "issue_state_updates": [],
        "commitment_changes": [],
        "obligation_changes": [],
        "new_relations": [],
        "answer_target_changes": [],
        "rejected_answer_target_changes": [],
        "repair_records": [],
        "resolved_items": [],
        "extraction_status": "complete",
        "abstentions": [],
        "unsupported_inferences_rejected": 0,
        "warnings": [],
    }


def _raw_delta(delta: Mapping[str, Any]) -> bytes:
    return json.dumps(
        delta,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _process(
    raw: bytes,
    tracked: Mapping[str, Any],
    persisted_schema: Mapping[str, Any],
) -> dict[str, Any]:
    return live_probe.process_response_bytes(
        raw,
        case=tracked["case"],
        canonical_schema=tracked["canonical_schema"],
        provider_schema=tracked["provider_schema"],
        persisted_ledger_schema=persisted_schema,
    )


def _observation(model: str, raw: bytes) -> live_probe.LiveObservation:
    return live_probe.LiveObservation(
        raw_text=raw.decode("utf-8"),
        returned_model_id=model,
        provider_response_id=f"synthetic-response-{model}",
        finish_reason="stop",
        usage={
            "prompt_tokens": 101,
            "completion_tokens": 89,
            "total_tokens": 190,
        },
    )


class _RpcCode:
    def __init__(self, name: str):
        self.name = name


class _FakeRpcError(Exception):
    def __init__(self, code: str | None, detail: str):
        super().__init__("sanitised fake provider error")
        self._code = code
        self._detail = detail

    def code(self) -> _RpcCode | None:
        return _RpcCode(self._code) if self._code is not None else None

    def details(self) -> str:
        return self._detail


class _FakeTransport:
    def __init__(
        self,
        outcomes: Sequence[live_probe.LiveObservation | BaseException],
    ) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def sample(
        self, profile: Mapping[str, Any], tracked: Mapping[str, Any]
    ) -> live_probe.LiveObservation:
        self.calls.append(
            {
                "profile": copy.deepcopy(dict(profile)),
                "messages": copy.deepcopy(tracked["messages"]),
                "provider_schema_sha256": tracked["hashes"][
                    "provider_schema_sha256"
                ],
            }
        )
        if not self.outcomes:
            raise AssertionError("fake transport received an unplanned call")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def close(self) -> None:
        self.closed = True


def _patch_local_preconditions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        live_probe,
        "verify_pinned_environment",
        lambda: {"artifact_evidence": "observed", "status": "passed"},
    )
    monkeypatch.setattr(
        live_probe,
        "compile_local_requests",
        lambda _tracked: {
            "artifact_evidence": "derived",
            "provider_calls_made": 0,
            "transport_rpc_invocations": 0,
            "requests_differ_only_by_model_id": True,
            "message_arrays_byte_identical": True,
        },
    )

    def fake_git(*arguments: str) -> str:
        if arguments == ("branch", "--show-current"):
            return "research/proposition-ledger-phase1.4-xai-live-probe"
        if arguments == ("rev-parse", "origin/master"):
            return "f" * 40
        if arguments == ("rev-parse", "HEAD"):
            return live_probe.SOURCE_COMMIT
        raise AssertionError(f"unexpected Git query: {arguments!r}")

    monkeypatch.setattr(live_probe, "_git_output", fake_git)


def _prepare_private_run(
    monkeypatch: pytest.MonkeyPatch, parent: Path, name: str = "private-run"
) -> Path:
    _patch_local_preconditions(monkeypatch)
    output = parent / name
    result = live_probe.prepare_run(output)
    assert result["status"] == "prepared"
    assert result["provider_call_count"] == 0
    return output


def _enable_fake_live_key(monkeypatch: pytest.MonkeyPatch, value: str = "fake-xai-key") -> None:
    monkeypatch.setenv("XAI_API_KEY", value)
    monkeypatch.setattr(
        live_probe,
        "UNRELATED_CREDENTIAL_VARIABLES",
        ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_BEARER_TOKEN"),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-test-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unrelated-test-value")
    monkeypatch.setenv("XAI_BEARER_TOKEN", "unrelated-test-value")


def _execute_with(
    monkeypatch: pytest.MonkeyPatch,
    output: Path,
    transport: _FakeTransport,
    *,
    key: str = "fake-xai-key",
) -> dict[str, Any]:
    _enable_fake_live_key(monkeypatch, key)
    return live_probe.execute_live_probe(
        output,
        confirm_provider_call_budget=2,
        transport_factory=lambda: transport,
    )


class _SyntheticProcessCrash(RuntimeError):
    """Test-only interruption raised outside the provider sampling boundary."""


def _interrupt_on_second_transition(
    monkeypatch: pytest.MonkeyPatch,
    output: Path,
    transport: _FakeTransport,
    *,
    target_state: str,
    after_persisting_transition: bool,
) -> None:
    """Run until a chosen second-profile transition, then emulate process death."""

    _enable_fake_live_key(monkeypatch)
    original_transition = live_probe._transition_call
    transition_reached = False

    def crashing_transition(
        output_dir: Path,
        index: int,
        new_state: str,
        **evidence: Any,
    ) -> dict[str, Any]:
        nonlocal transition_reached
        if index == 1 and new_state == target_state:
            transition_reached = True
            if after_persisting_transition:
                original_transition(output_dir, index, new_state, **evidence)
            raise _SyntheticProcessCrash(
                f"synthetic crash at second-profile {new_state} transition"
            )
        return original_transition(output_dir, index, new_state, **evidence)

    monkeypatch.setattr(live_probe, "_transition_call", crashing_transition)
    try:
        with pytest.raises(_SyntheticProcessCrash):
            live_probe.execute_live_probe(
                output,
                confirm_provider_call_budget=2,
                transport_factory=lambda: transport,
            )
    finally:
        monkeypatch.setattr(live_probe, "_transition_call", original_transition)
    assert transition_reached is True


def test_importing_module_makes_zero_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_attempts: list[tuple[Any, ...]] = []

    def deny_connection(*args: Any, **kwargs: Any) -> None:
        network_attempts.append((*args, kwargs))
        raise AssertionError("network attempted while importing live-probe module")

    monkeypatch.setattr(socket, "create_connection", deny_connection)
    xai_modules_before = {name for name in sys.modules if name.startswith("xai_sdk")}

    imported = runpy.run_path(str(MODULE_PATH), run_name="phase14_import_safety_test")

    assert imported["PROVIDER_CALL_BUDGET"] == 2
    assert network_attempts == []
    assert {
        name for name in sys.modules if name.startswith("xai_sdk")
    } == xai_modules_before


def test_help_makes_zero_provider_calls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in (
        "prepare_run",
        "execute_live_probe",
        "verify_only",
        "publish_report",
        "_create_real_transport",
    ):
        monkeypatch.setattr(
            live_probe,
            name,
            lambda *_args, _name=name, **_kwargs: (_ for _ in ()).throw(
                AssertionError(f"{_name} called by --help")
            ),
        )

    with pytest.raises(SystemExit) as exc_info:
        live_probe.main(["--help"])

    assert exc_info.value.code == 0
    assert "--execute-live-probe" in capsys.readouterr().out


def test_prepare_mode_makes_zero_provider_calls_and_writes_a_two_call_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_local_preconditions(monkeypatch)
    monkeypatch.setattr(
        live_probe,
        "_create_real_transport",
        lambda: (_ for _ in ()).throw(AssertionError("provider transport created")),
    )
    output = tmp_path / "prepared-run"

    return_code = live_probe.main(
        ["--prepare", "--private-output", str(output)]
    )

    assert return_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["provider_call_count"] == 0
    ledger = live_probe._load_call_ledger(output)
    assert ledger["provider_call_count"] == 0
    assert [entry["state"] for entry in ledger["entries"]] == [
        "planned",
        "planned",
    ]
    assert all(path.stat().st_mode & 0o777 == 0o700 for path in [output, output / "grok-4.3", output / "grok-4.6"])
    assert all(
        path.stat().st_mode & 0o777 == 0o600
        for path in output.rglob("*")
        if path.is_file()
    )


def test_verify_only_on_prepared_run_needs_no_key_and_makes_zero_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    ledger_before = (output / "call-ledger.json").read_bytes()
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        live_probe,
        "_create_real_transport",
        lambda: (_ for _ in ()).throw(AssertionError("provider transport created")),
    )

    return_code = live_probe.main(
        ["--verify-only", "--private-output", str(output)]
    )

    assert return_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["provider_calls_made"] == 0
    assert result["network_transport_instantiated"] is False
    assert result["provider_call_count"] == 0
    assert (output / "call-ledger.json").read_bytes() == ledger_before


def test_live_execution_requires_the_explicit_mode_and_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        live_probe._build_parser().parse_args(
            ["--private-output", str(tmp_path / "run")]
        )
    monkeypatch.setattr(
        live_probe,
        "_create_real_transport",
        lambda: (_ for _ in ()).throw(AssertionError("provider transport created")),
    )

    return_code = live_probe.main(
        ["--execute-live-probe", "--private-output", str(tmp_path / "run")]
    )

    assert return_code == 2
    assert "must be exactly 2" in capsys.readouterr().err


@pytest.mark.parametrize("budget", [None, -1, 0, 1, 3, 20])
def test_any_call_budget_other_than_exactly_two_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, budget: int | None
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "must-not-be-used")
    monkeypatch.setattr(
        live_probe,
        "_create_real_transport",
        lambda: (_ for _ in ()).throw(AssertionError("provider transport created")),
    )

    with pytest.raises(live_probe.ProbeError, match="exactly 2"):
        live_probe.execute_live_probe(
            tmp_path / "does-not-exist",
            confirm_provider_call_budget=budget,
        )


def test_missing_key_stops_before_tracked_or_private_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "must-not-be-created"
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        live_probe,
        "validate_tracked_inputs",
        lambda: (_ for _ in ()).throw(AssertionError("tracked files inspected")),
    )
    monkeypatch.setattr(
        live_probe,
        "_create_real_transport",
        lambda: (_ for _ in ()).throw(AssertionError("provider transport created")),
    )

    return_code = live_probe.main(
        [
            "--execute-live-probe",
            "--private-output",
            str(output),
            "--confirm-provider-call-budget",
            "2",
        ]
    )

    captured = capsys.readouterr()
    assert return_code == 4
    assert captured.out.strip() == "live_probe_not_run_missing_xai_api_key"
    assert captured.err == ""
    assert not output.exists()


def test_api_key_is_never_printed_serialised_or_hashed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    secret = "unit-test-xai-secret-never-retain"
    raw = _raw_delta(_valid_delta(tracked_inputs))
    transport = _FakeTransport(
        [_observation(profile["model"], raw) for profile in live_probe.PROFILES]
    )
    hashed_inputs: list[bytes] = []
    original_sha256_bytes = live_probe.sha256_bytes

    def recording_sha256(value: bytes) -> str:
        hashed_inputs.append(value)
        return original_sha256_bytes(value)

    monkeypatch.setattr(live_probe, "sha256_bytes", recording_sha256)

    summary = _execute_with(monkeypatch, output, transport, key=secret)

    captured = capsys.readouterr()
    secret_bytes = secret.encode("utf-8")
    assert summary["provider_call_count"] == 2
    assert secret not in captured.out + captured.err
    assert all(secret_bytes not in value for value in hashed_inputs)
    assert all(
        secret_bytes not in path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    )


def test_call_plan_contains_exactly_the_two_frozen_profiles(
    tracked_inputs: Mapping[str, Any],
) -> None:
    plan = live_probe.make_call_plan(tracked_inputs)

    assert plan["provider_call_budget"] == 2
    assert plan["provider_call_count"] == 0
    assert [
        (entry["profile_id"], entry["model"], entry["provider"])
        for entry in plan["entries"]
    ] == [
        ("xai-grok-4.3-low-ledger-v1", "grok-4.3", "xAI"),
        ("xai-grok-4.6-low-ledger-v1", "grok-4.6", "xAI"),
    ]
    assert len({entry["call_identity"] for entry in plan["entries"]}) == 2


def test_live_call_order_is_grok_43_then_grok_46(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    raw = _raw_delta(_valid_delta(tracked_inputs))
    transport = _FakeTransport(
        [_observation("grok-4.3", raw), _observation("grok-4.6", raw)]
    )

    summary = _execute_with(monkeypatch, output, transport)

    assert [call["profile"]["model"] for call in transport.calls] == [
        "grok-4.3",
        "grok-4.6",
    ]
    assert summary["provider_call_count"] == 2
    assert transport.closed is True


def test_local_requests_differ_only_by_model_and_profile_identity(
    tracked_inputs: Mapping[str, Any],
) -> None:
    requests = [
        live_probe.request_representation(profile, tracked_inputs)
        for profile in live_probe.PROFILES
    ]
    differing_fields = {
        key for key in requests[0] if requests[0][key] != requests[1][key]
    }
    normalized_profiles = []
    for profile in live_probe.PROFILES:
        normalized = dict(profile)
        normalized.pop("model")
        normalized.pop("profile_id")
        normalized_profiles.append(normalized)

    assert differing_fields == {"model"}
    assert normalized_profiles[0] == normalized_profiles[1]
    assert live_probe.canonical_json_bytes(requests[0]["messages"]) == (
        live_probe.canonical_json_bytes(requests[1]["messages"])
    )
    live_probe._verify_request_equivalence(requests)


def test_both_requests_use_low_reasoning_and_the_same_provider_schema(
    tracked_inputs: Mapping[str, Any],
) -> None:
    requests = [
        live_probe.request_representation(profile, tracked_inputs)
        for profile in live_probe.PROFILES
    ]

    assert {request["reasoning_effort"] for request in requests} == {"low"}
    assert {
        request["response_format"]["schema_sha256"] for request in requests
    } == {live_probe.EXPECTED_PROVIDER_SCHEMA_SHA256}
    assert tracked_inputs["hashes"]["provider_schema_sha256"] == (
        live_probe.EXPECTED_PROVIDER_SCHEMA_SHA256
    )


def test_tools_search_code_execution_and_streaming_are_disabled(
    tracked_inputs: Mapping[str, Any],
) -> None:
    for profile in live_probe.PROFILES:
        request = live_probe.request_representation(profile, tracked_inputs)
        assert request["tools"] == []
        assert request["tool_choice"] == "none"
        assert request["parallel_tool_calls"] is False
        assert request["search_parameters"] is None
        assert request["code_execution"] is False
        assert request["streaming"] is False
        assert request["fallback_model"] is None
        assert request["sampling_parameters_set"] == []


def test_no_retry_is_configured_at_sdk_or_application_layer(
    tracked_inputs: Mapping[str, Any],
) -> None:
    assert live_probe.NO_RETRY_CHANNEL_OPTIONS == (
        ("grpc.enable_retries", 0),
        ("grpc.service_config", "{}"),
    )
    for profile in live_probe.PROFILES:
        request = live_probe.request_representation(profile, tracked_inputs)
        assert request["application_retry_count"] == 0
        assert request["sdk_retry_enabled"] is False


def test_completed_calls_cannot_be_repeated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    raw = _raw_delta(_valid_delta(tracked_inputs))
    first_transport = _FakeTransport(
        [_observation("grok-4.3", raw), _observation("grok-4.6", raw)]
    )
    _execute_with(monkeypatch, output, first_transport)
    factory_calls = 0

    def forbidden_factory() -> _FakeTransport:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("completed call was repeated")

    with pytest.raises(live_probe.ProbeError, match="finalized provider call plan"):
        live_probe.execute_live_probe(
            output,
            confirm_provider_call_budget=2,
            transport_factory=forbidden_factory,
        )

    assert factory_calls == 0
    assert [entry["state"] for entry in live_probe._load_call_ledger(output)["entries"]] == [
        "fully_validated",
        "fully_validated",
    ]


def test_sending_and_uncertain_calls_are_never_repeated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable_fake_live_key(monkeypatch)
    sending_run = _prepare_private_run(monkeypatch, tmp_path, "sending-run")
    live_probe._transition_call(
        sending_run, 0, "sending", started_at_utc="2026-01-01T00:00:00Z"
    )
    # A synthetic checksum refresh makes the non-repeatability state directly
    # testable. A real interrupted process cannot perform this refresh.
    live_probe._write_checksums(sending_run)
    factory_calls = 0

    def forbidden_factory() -> _FakeTransport:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("sending call was repeated")

    summary = live_probe.execute_live_probe(
        sending_run,
        confirm_provider_call_budget=2,
        transport_factory=forbidden_factory,
    )
    assert factory_calls == 0
    assert summary["profiles"][0]["call_state"] == "uncertain_after_send"
    assert summary["profiles"][1]["call_state"] == (
        "not_attempted_due_to_global_failure"
    )

    uncertain_run = _prepare_private_run(monkeypatch, tmp_path, "uncertain-run")
    live_probe._transition_call(
        uncertain_run, 0, "sending", started_at_utc="2026-01-01T00:00:00Z"
    )
    live_probe._transition_call(
        uncertain_run,
        0,
        "uncertain_after_send",
        completed_at_utc="2026-01-01T00:00:01Z",
    )
    live_probe._write_checksums(uncertain_run)

    uncertain_summary = live_probe.execute_live_probe(
        uncertain_run,
        confirm_provider_call_budget=2,
        transport_factory=forbidden_factory,
    )

    assert factory_calls == 0
    assert uncertain_summary["phase1_4_disposition"] == (
        "phase1_4_uncertain_after_send"
    )
    assert uncertain_summary["profiles"][1]["call_state"] == (
        "not_attempted_due_to_global_failure"
    )


@pytest.mark.parametrize(
    ("code", "detail", "category", "acceptance", "stops"),
    [
        (
            "INVALID_ARGUMENT",
            "JSON schema is unsupported",
            "server_schema_rejection",
            "rejected",
            False,
        ),
        (
            "NOT_FOUND",
            "grok-4.3 model is unavailable",
            "model_profile_rejection",
            "not_determined",
            False,
        ),
        (
            "UNAUTHENTICATED",
            "authentication failed",
            "authentication_failure",
            "not_determined",
            True,
        ),
        (
            "RESOURCE_EXHAUSTED",
            "account quota exhausted",
            "rate_limit_or_account_quota_failure",
            "not_determined",
            True,
        ),
        (
            "UNAVAILABLE",
            "DNS name resolution failed",
            "transport_failure_before_send",
            "not_determined",
            True,
        ),
    ],
)
def test_provider_error_classes_keep_schema_transport_auth_and_quota_distinct(
    code: str,
    detail: str,
    category: str,
    acceptance: str,
    stops: bool,
) -> None:
    classification = live_probe.classify_provider_error(
        _FakeRpcError(code, detail)
    )

    assert classification.category == category
    assert classification.server_schema_acceptance_status == acceptance
    assert classification.stops_following_calls is stops


@pytest.mark.parametrize(
    ("code", "detail", "expected_category"),
    [
        ("UNAUTHENTICATED", "authentication failed", "authentication_failure"),
        (
            "RESOURCE_EXHAUSTED",
            "account-wide quota exhausted",
            "rate_limit_or_account_quota_failure",
        ),
    ],
)
def test_authentication_or_global_quota_failure_prevents_second_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    code: str,
    detail: str,
    expected_category: str,
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    transport = _FakeTransport([_FakeRpcError(code, detail)])

    summary = _execute_with(monkeypatch, output, transport)

    assert [call["profile"]["model"] for call in transport.calls] == ["grok-4.3"]
    assert summary["provider_call_count"] == 1
    assert summary["profiles"][0]["provider_error_class"] == expected_category
    assert summary["profiles"][1]["call_state"] == (
        "not_attempted_due_to_global_failure"
    )


def test_definite_model_specific_schema_rejection_permits_second_profile_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    raw = _raw_delta(_valid_delta(tracked_inputs))
    transport = _FakeTransport(
        [
            _FakeRpcError("INVALID_ARGUMENT", "JSON schema rejected"),
            _observation("grok-4.6", raw),
        ]
    )

    summary = _execute_with(monkeypatch, output, transport)

    assert [call["profile"]["model"] for call in transport.calls] == [
        "grok-4.3",
        "grok-4.6",
    ]
    assert summary["provider_call_count"] == 2
    assert summary["profiles"][0]["server_schema_acceptance_status"] == "rejected"
    assert summary["profiles"][0]["profile_disposition"] == "server_rejected_schema"
    assert summary["profiles"][1]["profile_disposition"] == (
        "accepted_and_fully_validated"
    )


def test_timeout_after_possible_transmission_becomes_uncertain_and_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    transport = _FakeTransport(
        [_FakeRpcError("DEADLINE_EXCEEDED", "request timed out")]
    )

    summary = _execute_with(monkeypatch, output, transport)

    assert len(transport.calls) == 1
    assert summary["phase1_4_disposition"] == "phase1_4_uncertain_after_send"
    assert summary["profiles"][0]["call_state"] == "uncertain_after_send"
    assert summary["profiles"][1]["call_state"] == (
        "not_attempted_due_to_global_failure"
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"member":1,"member":2}',
        b'{"number":NaN}',
        b'{"number":Infinity}',
        b"\xff",
        b'```json\n{"member":1}\n```',
        b'{"member":1} trailing prose',
        b'{"member":1} {"second":2}',
        b"[]",
    ],
    ids=(
        "duplicate-member",
        "nan",
        "infinity",
        "invalid-utf8",
        "code-fence",
        "trailing-prose",
        "multiple-values",
        "non-object-top-level",
    ),
)
def test_strict_json_rejects_every_forbidden_raw_response_form(raw: bytes) -> None:
    with pytest.raises(live_probe.StrictJSONError):
        live_probe.strict_json_loads(raw)


def test_provider_schema_and_intended_canonical_results_are_separate(
    monkeypatch: pytest.MonkeyPatch,
    tracked_inputs: Mapping[str, Any],
    persisted_ledger_schema: Mapping[str, Any],
) -> None:
    calls: list[str] = []

    def fake_intended(
        schema: Mapping[str, Any],
        _instance: Mapping[str, Any],
        *,
        pattern_mode: str,
    ) -> list[str]:
        calls.append(pattern_mode)
        if schema is tracked_inputs["provider_schema"]:
            return ["synthetic provider-schema observation"]
        return ["synthetic intended-canonical failure"]

    monkeypatch.setattr(
        live_probe.preflight, "intended_validation_errors", fake_intended
    )
    monkeypatch.setattr(
        live_probe.preflight,
        "validation_errors",
        lambda _schema, _instance: ["synthetic ordinary diagnostic"],
    )

    result = _process(
        _raw_delta(_valid_delta(tracked_inputs)),
        tracked_inputs,
        persisted_ledger_schema,
    )
    validation = result["validation"]

    assert calls == ["xai_full_string", "canonical_outer_anchors"]
    assert validation["provider_schema_validation_status"] == "failed"
    assert validation["canonical_validation_status"] == "failed"
    assert validation["provider_schema_errors"] != validation[
        "canonical_validation_errors"
    ]
    assert validation["ordinary_python_jsonschema_status"] == "failed"


def test_intended_regex_layer_is_authoritative_and_python_jsonschema_is_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tracked_inputs: Mapping[str, Any],
    persisted_ledger_schema: Mapping[str, Any],
) -> None:
    intended_modes: list[str] = []

    def intended_passes(
        _schema: Mapping[str, Any],
        _instance: Mapping[str, Any],
        *,
        pattern_mode: str,
    ) -> list[str]:
        intended_modes.append(pattern_mode)
        return []

    monkeypatch.setattr(
        live_probe.preflight, "intended_validation_errors", intended_passes
    )
    monkeypatch.setattr(
        live_probe.preflight,
        "validation_errors",
        lambda _schema, _instance: ["Python regex implementation divergence"],
    )

    result = _process(
        _raw_delta(_valid_delta(tracked_inputs)),
        tracked_inputs,
        persisted_ledger_schema,
    )
    validation = result["validation"]

    assert intended_modes == ["xai_full_string", "canonical_outer_anchors"]
    assert validation["canonical_validation_status"] == "passed"
    assert validation["ordinary_python_jsonschema_status"] == "failed"
    assert validation["ordinary_python_jsonschema_is_authority"] is False
    assert validation["materialisation_status"] == "passed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("conversation_key", "synthetic:wrong-conversation"),
        ("target_turn_id", "synthetic-future-turn-1"),
        ("as_of_turn_index", 1),
        (
            "prior_ledger_reference",
            {"ledger_id": "synthetic-prior-ledger", "as_of_turn_index": 0},
        ),
    ],
)
def test_conversation_turn_and_genesis_binding_mismatches_fail_closed(
    tracked_inputs: Mapping[str, Any], field: str, value: Any
) -> None:
    delta = _valid_delta(tracked_inputs)
    delta[field] = value

    errors = live_probe.validate_bindings(delta, tracked_inputs["case"])

    assert errors
    assert any("binding_mismatch" in error for error in errors)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("turn_id", "synthetic-future-turn-1", "non_current_evidence_turn"),
        ("start_char", -1, "evidence_span_out_of_bounds"),
        ("start_char", True, "evidence_span_out_of_bounds"),
        ("end_char", 1000, "evidence_span_out_of_bounds"),
        ("exact_text", "The lamp is off.", "evidence_span_mismatch"),
    ],
)
def test_invalid_evidence_offsets_and_text_fail_closed(
    tracked_inputs: Mapping[str, Any],
    field: str,
    value: Any,
    expected: str,
) -> None:
    delta = _valid_delta(tracked_inputs)
    delta["new_propositions"][0]["exact_evidence_spans"][0][field] = value

    errors = live_probe.validate_evidence_spans(delta, tracked_inputs["case"])

    assert any(error.startswith(expected) for error in errors)


def test_unknown_participant_reference_fails_closed(
    tracked_inputs: Mapping[str, Any],
) -> None:
    delta = _valid_delta(tracked_inputs)
    delta["new_propositions"][0]["speaker_or_attributor"][
        "participant_id"
    ] = "synthetic-unknown-participant"

    errors = live_probe.validate_bindings(delta, tracked_inputs["case"])

    assert any("unknown_participant_reference" in error for error in errors)


def test_valid_synthetic_genesis_delta_materialises_and_passes_persisted_validation(
    tracked_inputs: Mapping[str, Any],
    persisted_ledger_schema: Mapping[str, Any],
) -> None:
    result = _process(
        _raw_delta(_valid_delta(tracked_inputs)),
        tracked_inputs,
        persisted_ledger_schema,
    )
    validation = result["validation"]

    assert validation["strict_json_status"] == "passed"
    assert validation["provider_schema_validation_status"] == "passed"
    assert validation["canonical_validation_status"] == "passed"
    assert validation["binding_validation_status"] == "passed"
    assert validation["evidence_span_validation_status"] == "passed"
    assert validation["semantic_reference_validation_status"] == "passed"
    assert validation["materialisation_status"] == "passed"
    assert validation["persisted_ledger_validation_status"] == "passed"
    assert validation["semantic_smoke_status"] == "passed"
    assert result["ledger"] is not None
    assert result["ledger"]["ledger_sha256"] == semantic.phase1.ledger_sha256(
        result["ledger"]
    )


@pytest.mark.parametrize("field", ["proposition_id", "ledger_sha256"])
def test_provider_cannot_control_persistent_ids_or_ledger_hashes(
    tracked_inputs: Mapping[str, Any],
    persisted_ledger_schema: Mapping[str, Any],
    field: str,
) -> None:
    delta = _valid_delta(tracked_inputs)
    delta["new_propositions"][0][field] = "provider-controlled-value"

    result = _process(
        _raw_delta(delta), tracked_inputs, persisted_ledger_schema
    )

    assert result["validation"]["canonical_validation_status"] == "failed"
    assert result["validation"]["provider_controls_persistent_fields"] is False
    assert result["ledger"] is None


def test_server_acceptance_remains_distinct_from_semantic_smoke_quality(
    tracked_inputs: Mapping[str, Any],
    persisted_ledger_schema: Mapping[str, Any],
) -> None:
    delta = _valid_delta(tracked_inputs)
    delta["new_propositions"] = []
    raw = _raw_delta(delta)
    processed = _process(raw, tracked_inputs, persisted_ledger_schema)
    profile_result = live_probe._profile_result_from_validation(
        live_probe.PROFILES[0],
        _observation("grok-4.3", raw),
        processed["validation"],
        latency_seconds=0.01,
        received_at_utc="2026-01-01T00:00:00Z",
    )

    assert processed["validation"]["provider_schema_validation_status"] == "passed"
    assert processed["validation"]["canonical_validation_status"] == "passed"
    assert processed["validation"]["materialisation_status"] == "passed"
    assert processed["validation"]["semantic_smoke_status"] == "failed"
    assert profile_result["server_schema_acceptance_status"] == "accepted"
    assert profile_result["profile_disposition"] == (
        "accepted_but_semantic_smoke_failed"
    )
    assert "rejected" not in profile_result["profile_disposition"]


def test_schema_valid_but_semantically_empty_response_is_not_schema_rejection(
    tracked_inputs: Mapping[str, Any],
    persisted_ledger_schema: Mapping[str, Any],
) -> None:
    delta = _valid_delta(tracked_inputs)
    delta["new_propositions"] = []
    validation = _process(
        _raw_delta(delta), tracked_inputs, persisted_ledger_schema
    )["validation"]

    assert live_probe.derive_profile_disposition(validation) == (
        "accepted_but_semantic_smoke_failed"
    )
    assert validation["structured_output_status"] == "valid"


def test_stored_provider_errors_are_redacted_single_line_and_bounded() -> None:
    secret = "test-secret-that-must-disappear"
    other_secret = "cookie-secret-that-must-disappear"
    detail = (
        f"Authorization: Bearer {secret}\nCookie={other_secret} "
        + "bounded-message " * 100
    )

    classification = live_probe.classify_provider_error(
        _FakeRpcError("UNAUTHENTICATED", detail)
    )
    redacted_detail = live_probe._sanitize_error_message(detail)

    assert classification.category == "authentication_failure"
    assert secret not in classification.message
    assert other_secret not in classification.message
    assert "\n" not in classification.message
    assert len(classification.message) <= live_probe.ERROR_MESSAGE_LIMIT
    assert secret not in redacted_detail
    assert other_secret not in redacted_detail
    assert redacted_detail == "provider error detail withheld by credential boundary"


def test_reprocessing_saved_response_bytes_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    tracked_inputs: Mapping[str, Any],
    persisted_ledger_schema: Mapping[str, Any],
) -> None:
    raw = _raw_delta(_valid_delta(tracked_inputs))
    calls = 0
    original = live_probe.process_response_bytes

    def counted(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(live_probe, "process_response_bytes", counted)

    result = live_probe.process_response_twice(
        raw,
        case=tracked_inputs["case"],
        canonical_schema=tracked_inputs["canonical_schema"],
        provider_schema=tracked_inputs["provider_schema"],
        persisted_ledger_schema=persisted_ledger_schema,
    )

    deterministic = result["validation"]["deterministic_reprocessing"]
    assert calls == 2
    assert deterministic["status"] == "passed"
    assert deterministic["byte_identical"] is True
    assert deterministic["first_derived_sha256"] == deterministic[
        "second_derived_sha256"
    ]
    assert deterministic["additional_provider_calls"] == 0


def test_verify_only_reprocesses_saved_bytes_without_network_or_call_count_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    raw = _raw_delta(_valid_delta(tracked_inputs))
    transport = _FakeTransport(
        [_observation("grok-4.3", raw), _observation("grok-4.6", raw)]
    )
    _execute_with(monkeypatch, output, transport)
    ledger_before = (output / "call-ledger.json").read_bytes()
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    transport_creations = 0

    def forbidden_transport() -> None:
        nonlocal transport_creations
        transport_creations += 1
        raise AssertionError("verify-only instantiated live transport")

    monkeypatch.setattr(live_probe, "_create_real_transport", forbidden_transport)

    verification = live_probe.verify_only(output)

    assert verification["status"] == "passed"
    assert verification["saved_responses_reprocessed"] == 2
    assert verification["deterministic_reprocessing_status"] == "passed"
    assert verification["provider_calls_made"] == 0
    assert verification["network_transport_instantiated"] is False
    assert verification["provider_call_count"] == 2
    assert (output / "call-ledger.json").read_bytes() == ledger_before
    assert transport_creations == 0


def test_tracked_probe_inputs_contain_only_the_frozen_synthetic_case(
    tracked_inputs: Mapping[str, Any],
) -> None:
    case = tracked_inputs["case"]
    prompt_and_case = (
        live_probe.SYSTEM_PROMPT_PATH.read_bytes()
        + live_probe.CASE_PATH.read_bytes()
    ).decode("utf-8")
    forbidden_path_markers = (
        "/disks/",
        "file://",
        "mrsMThatcher2.py",
        "production/",
        "held-out/",
        "clean-prefix",
    )

    assert case["synthetic"] is True
    assert case["conversation_key"].startswith("synthetic:")
    assert case["target_turn_id"].startswith("synthetic-")
    assert case["visible_text"] == "The lamp is on."
    assert case["exact_evidence_span"]["end_char"] == len(case["visible_text"])
    assert not any(marker in prompt_and_case for marker in forbidden_path_markers)


@pytest.mark.parametrize(
    ("profile_results", "expected"),
    [
        (
            [
                {
                    "call_state": "fully_validated",
                    "server_schema_acceptance_status": "accepted",
                    "profile_disposition": "accepted_and_fully_validated",
                },
                {
                    "call_state": "fully_validated",
                    "server_schema_acceptance_status": "accepted",
                    "profile_disposition": "accepted_and_fully_validated",
                },
            ],
            "phase1_4_live_schema_acceptance_confirmed_both_profiles",
        ),
        (
            [
                {
                    "call_state": "terminal_validation_failure",
                    "server_schema_acceptance_status": "accepted",
                    "profile_disposition": "accepted_but_semantic_smoke_failed",
                },
                {
                    "call_state": "fully_validated",
                    "server_schema_acceptance_status": "accepted",
                    "profile_disposition": "accepted_and_fully_validated",
                },
            ],
            "phase1_4_live_schema_acceptance_confirmed_with_validation_failure",
        ),
        (
            [
                {
                    "call_state": "provider_error_received",
                    "server_schema_acceptance_status": "rejected",
                    "profile_disposition": "server_rejected_schema",
                },
                {
                    "call_state": "fully_validated",
                    "server_schema_acceptance_status": "accepted",
                    "profile_disposition": "accepted_and_fully_validated",
                },
            ],
            "phase1_4_live_schema_rejected_one_or_both_profiles",
        ),
        (
            [
                {
                    "call_state": "uncertain_after_send",
                    "server_schema_acceptance_status": "not_determined",
                    "profile_disposition": "uncertain_after_send",
                },
                {
                    "call_state": "not_attempted_due_to_global_failure",
                    "server_schema_acceptance_status": "not_determined",
                    "profile_disposition": "not_attempted_due_to_global_failure",
                },
            ],
            "phase1_4_uncertain_after_send",
        ),
        (
            [
                {
                    "call_state": "fully_validated",
                    "server_schema_acceptance_status": "accepted",
                    "profile_disposition": "accepted_and_fully_validated",
                },
                {
                    "call_state": "not_attempted_due_to_global_failure",
                    "server_schema_acceptance_status": "not_determined",
                    "profile_disposition": "not_attempted_due_to_global_failure",
                },
            ],
            "phase1_4_live_schema_acceptance_confirmed_one_profile",
        ),
        (
            [
                {
                    "call_state": "provider_error_received",
                    "server_schema_acceptance_status": "not_determined",
                    "profile_disposition": "authentication_or_authorisation_failed",
                },
                {
                    "call_state": "not_attempted_due_to_global_failure",
                    "server_schema_acceptance_status": "not_determined",
                    "profile_disposition": "not_attempted_due_to_global_failure",
                },
            ],
            "phase1_4_inconclusive_operational_failure",
        ),
    ],
)
def test_final_disposition_is_derived_from_profile_evidence(
    profile_results: Sequence[Mapping[str, Any]], expected: str
) -> None:
    assert live_probe.derive_phase1_4_disposition(profile_results) == expected


def test_concurrent_execute_is_locked_before_second_factory_or_sample(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    raw = _raw_delta(_valid_delta(tracked_inputs))
    first_sample_entered = threading.Event()
    release_first_sample = threading.Event()

    class BlockingTransport(_FakeTransport):
        def sample(
            self, profile: Mapping[str, Any], tracked: Mapping[str, Any]
        ) -> live_probe.LiveObservation:
            if not self.calls:
                first_sample_entered.set()
                if not release_first_sample.wait(timeout=10):
                    raise AssertionError("test did not release the blocked sample")
            return super().sample(profile, tracked)

    first_transport = BlockingTransport(
        [_observation("grok-4.3", raw), _observation("grok-4.6", raw)]
    )
    _enable_fake_live_key(monkeypatch)
    first_result: dict[str, Any] = {}
    first_errors: list[BaseException] = []

    def execute_first() -> None:
        try:
            first_result.update(
                live_probe.execute_live_probe(
                    output,
                    confirm_provider_call_budget=2,
                    transport_factory=lambda: first_transport,
                )
            )
        except BaseException as exc:  # retained for assertion in the test thread
            first_errors.append(exc)

    first_thread = threading.Thread(target=execute_first, daemon=True)
    first_thread.start()
    assert first_sample_entered.wait(timeout=5)
    second_factory_calls = 0

    def forbidden_second_factory() -> _FakeTransport:
        nonlocal second_factory_calls
        second_factory_calls += 1
        raise AssertionError("concurrent execution instantiated a transport")

    try:
        with pytest.raises(live_probe.ProbeError, match="holds the private-run lock"):
            live_probe.execute_live_probe(
                output,
                confirm_provider_call_budget=2,
                transport_factory=forbidden_second_factory,
            )
    finally:
        release_first_sample.set()
        first_thread.join(timeout=10)

    assert not first_thread.is_alive()
    assert first_errors == []
    assert second_factory_calls == 0
    assert [call["profile"]["model"] for call in first_transport.calls] == [
        "grok-4.3",
        "grok-4.6",
    ]
    assert first_result["provider_call_count"] == 2
    assert live_probe._load_call_ledger(output)["provider_call_count"] == 2


def test_verify_only_rejects_rechecksummed_completed_call_ledger_tampering(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    raw = _raw_delta(_valid_delta(tracked_inputs))
    transport = _FakeTransport(
        [_observation("grok-4.3", raw), _observation("grok-4.6", raw)]
    )
    _execute_with(monkeypatch, output, transport)
    ledger = live_probe._load_strict_json_file(
        output / "call-ledger.json", "test call ledger"
    )
    assert [entry["state"] for entry in ledger["entries"]] == [
        "fully_validated",
        "fully_validated",
    ]
    ledger["entries"][0]["call_identity"] = "0" * 64
    live_probe._atomic_write_private_json(output / "call-ledger.json", ledger)
    live_probe._write_checksums(output)

    with pytest.raises(live_probe.ProbeError, match="call ledger plan mismatch"):
        live_probe.verify_only(output)


@pytest.mark.parametrize(
    ("artifact", "expected_error"),
    [
        ("response-metadata", "response metadata differs from call ledger"),
        ("usage", "stored profile summary differs from durable evidence"),
        ("summary", "stored summary call count differs from call ledger"),
    ],
)
def test_verify_only_rejects_observation_or_summary_inconsistency_with_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
    artifact: str,
    expected_error: str,
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    raw = _raw_delta(_valid_delta(tracked_inputs))
    transport = _FakeTransport(
        [_observation("grok-4.3", raw), _observation("grok-4.6", raw)]
    )
    _execute_with(monkeypatch, output, transport)

    if artifact == "response-metadata":
        path = output / "grok-4.3/response-metadata.json"
        value = live_probe._load_strict_json_file(path, "test response metadata")
        value["provider_response_id"] = "synthetic-tampered-response-id"
    elif artifact == "usage":
        path = output / "grok-4.3/usage.json"
        value = live_probe._load_strict_json_file(path, "test usage")
        value["usage"]["total_tokens"] += 1
    else:
        path = output / "result-summary.json"
        value = live_probe._load_strict_json_file(path, "test result summary")
        value["provider_call_count"] = 1
    live_probe._atomic_write_private_json(path, value)
    live_probe._write_checksums(output)

    with pytest.raises(live_probe.ProbeError, match=expected_error):
        live_probe.verify_only(output)


def test_strict_json_rejects_numeric_overflow() -> None:
    with pytest.raises(live_probe.StrictJSONError, match="non-finite"):
        live_probe.strict_json_loads(b'{"overflow":1e9999}')


def test_malformed_nested_response_records_schema_failure_without_smoke_crash(
    tracked_inputs: Mapping[str, Any],
    persisted_ledger_schema: Mapping[str, Any],
) -> None:
    malformed = _valid_delta(tracked_inputs)
    malformed_proposition = malformed["new_propositions"][0]
    malformed_proposition["derivation"] = ["not", "an", "object"]
    malformed_proposition["temporal_scope"] = "not-an-object"
    malformed_proposition["modality"] = None

    processed = _process(
        _raw_delta(malformed), tracked_inputs, persisted_ledger_schema
    )

    assert processed["ledger"] is None
    assert processed["validation"]["provider_schema_validation_status"] == "failed"
    assert processed["validation"]["canonical_validation_status"] == "failed"
    assert processed["validation"]["semantic_smoke_status"] == "failed"
    assert processed["validation"]["semantic_smoke"]["status"] == "failed"


def test_live_cli_output_excludes_private_provider_response_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    raw = _raw_delta(_valid_delta(tracked_inputs))
    private_ids = (
        "synthetic-private-provider-response-grok-4.3",
        "synthetic-private-provider-response-grok-4.6",
    )
    observations = [
        live_probe.LiveObservation(
            raw_text=raw.decode("utf-8"),
            returned_model_id=profile["model"],
            provider_response_id=private_id,
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )
        for profile, private_id in zip(live_probe.PROFILES, private_ids)
    ]
    transport = _FakeTransport(observations)
    _enable_fake_live_key(monkeypatch)
    monkeypatch.setattr(live_probe, "_create_real_transport", lambda: transport)

    return_code = live_probe.main(
        [
            "--execute-live-probe",
            "--private-output",
            str(output),
            "--confirm-provider-call-budget",
            "2",
        ]
    )

    captured = capsys.readouterr()
    assert return_code == 0
    assert captured.err == ""
    assert "provider_response_id" not in captured.out
    assert not any(private_id in captured.out for private_id in private_ids)
    public_result = json.loads(captured.out)
    assert all(
        "provider_response_id" not in profile
        for profile in public_result.get("profiles", [])
    )
    assert all(
        private_id in (output / model / "response-metadata.json").read_text(
            encoding="utf-8"
        )
        for private_id, model in zip(private_ids, ("grok-4.3", "grok-4.6"))
    )


@pytest.mark.parametrize(
    ("operator_record", "expected_error"),
    [
        (
            {
                "artifact_evidence": "derived",
                "record_format": "proposition-ledger-phase1.4-operator-record-v1",
                "final_parent": live_probe.SOURCE_COMMIT,
                "test_records": [
                    {
                        "command": "synthetic offline pytest command",
                        "result": "passed",
                        "passed": 1,
                        "failed": 0,
                        "skipped": 0,
                    }
                ],
            },
            "verified final Git identities",
        ),
        (
            {
                "artifact_evidence": "derived",
                "record_format": "proposition-ledger-phase1.4-operator-record-v1",
                "final_commit": "a" * 40,
                "final_parent": live_probe.SOURCE_COMMIT,
                "test_records": [],
            },
            "passing final test records",
        ),
    ],
    ids=("pending-final-commit", "pending-test-records"),
)
def test_publish_report_refuses_pending_final_commit_or_tests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
    operator_record: Mapping[str, Any],
    expected_error: str,
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    raw = _raw_delta(_valid_delta(tracked_inputs))
    transport = _FakeTransport(
        [_observation("grok-4.3", raw), _observation("grok-4.6", raw)]
    )
    _execute_with(monkeypatch, output, transport)
    live_probe.verify_only(output)
    live_probe._atomic_write_private_json(
        output / "operator-record.json", dict(operator_record)
    )
    summary = live_probe._load_strict_json_file(
        output / "result-summary.json", "test result summary"
    )
    verification = live_probe._load_strict_json_file(
        output / "validation.json", "test verification"
    )
    live_probe._atomic_write_private_bytes(
        output / "phase1.4-report.md",
        live_probe._render_report(output, summary, verification),
    )
    live_probe._write_checksums(output)

    fake_home = tmp_path / "home"
    dropbox = fake_home / "Dropbox"
    dropbox.mkdir(parents=True, mode=0o700)
    destination = dropbox / "phase1.4-report.md"
    monkeypatch.setattr(
        live_probe.Path, "home", classmethod(lambda _cls: fake_home)
    )
    final_commit = operator_record.get("final_commit")
    if isinstance(final_commit, str):
        monkeypatch.setattr(
            live_probe,
            "_git_output",
            lambda *arguments: (
                final_commit
                if arguments == ("rev-parse", "HEAD")
                else live_probe.SOURCE_COMMIT
                if arguments == ("rev-parse", "HEAD^")
                else (_ for _ in ()).throw(
                    AssertionError(f"unexpected Git query: {arguments!r}")
                )
            ),
        )

    with pytest.raises(live_probe.ProbeError, match=expected_error):
        live_probe.publish_report(output, destination)

    assert not destination.exists()


def test_resume_after_first_fully_validated_calls_only_grok_46_and_preserves_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    raw = _raw_delta(_valid_delta(tracked_inputs))
    first_transport = _FakeTransport([_observation("grok-4.3", raw)])
    _interrupt_on_second_transition(
        monkeypatch,
        output,
        first_transport,
        target_state="sending",
        after_persisting_transition=False,
    )
    interrupted_ledger = live_probe._load_call_ledger(output)
    assert [entry["state"] for entry in interrupted_ledger["entries"]] == [
        "fully_validated",
        "planned",
    ]
    assert interrupted_ledger["provider_call_count"] == 1
    first_artifacts_before = {
        path.name: path.read_bytes()
        for path in (output / "grok-4.3").iterdir()
        if path.is_file()
    }

    resumed_transport = _FakeTransport([_observation("grok-4.6", raw)])
    summary = live_probe.execute_live_probe(
        output,
        confirm_provider_call_budget=2,
        transport_factory=lambda: resumed_transport,
    )

    assert [call["profile"]["model"] for call in first_transport.calls] == [
        "grok-4.3"
    ]
    assert [call["profile"]["model"] for call in resumed_transport.calls] == [
        "grok-4.6"
    ]
    assert summary["provider_call_count"] == 2
    assert [profile["call_state"] for profile in summary["profiles"]] == [
        "fully_validated",
        "fully_validated",
    ]
    assert summary["profiles"][0]["provider_response_id"] == (
        "synthetic-response-grok-4.3"
    )
    assert summary["profiles"][0]["provider_call_count"] == 1
    assert summary["deterministic_response_reprocessing_status"] == "passed"
    assert {
        path.name: path.read_bytes()
        for path in (output / "grok-4.3").iterdir()
        if path.is_file()
    } == first_artifacts_before


@pytest.mark.parametrize(
    ("error", "expected_class", "expected_disposition"),
    [
        (
            _FakeRpcError("INVALID_ARGUMENT", "JSON schema rejected"),
            "server_schema_rejection",
            "server_rejected_schema",
        ),
        (
            _FakeRpcError("NOT_FOUND", "model is unavailable"),
            "model_profile_rejection",
            "server_rejected_profile",
        ),
    ],
    ids=("schema-rejection", "model-profile-rejection"),
)
def test_resume_after_first_non_global_rejection_calls_only_second_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
    error: _FakeRpcError,
    expected_class: str,
    expected_disposition: str,
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    first_transport = _FakeTransport([error])
    _interrupt_on_second_transition(
        monkeypatch,
        output,
        first_transport,
        target_state="sending",
        after_persisting_transition=False,
    )
    interrupted_ledger = live_probe._load_call_ledger(output)
    assert [entry["state"] for entry in interrupted_ledger["entries"]] == [
        "provider_error_received",
        "planned",
    ]
    first_error_evidence = (
        output / "grok-4.3/response-metadata.json"
    ).read_bytes()
    raw = _raw_delta(_valid_delta(tracked_inputs))
    resumed_transport = _FakeTransport([_observation("grok-4.6", raw)])

    summary = live_probe.execute_live_probe(
        output,
        confirm_provider_call_budget=2,
        transport_factory=lambda: resumed_transport,
    )

    assert [call["profile"]["model"] for call in first_transport.calls] == [
        "grok-4.3"
    ]
    assert [call["profile"]["model"] for call in resumed_transport.calls] == [
        "grok-4.6"
    ]
    assert summary["provider_call_count"] == 2
    assert summary["profiles"][0]["provider_error_class"] == expected_class
    assert summary["profiles"][0]["profile_disposition"] == expected_disposition
    assert summary["profiles"][1]["profile_disposition"] == (
        "accepted_and_fully_validated"
    )
    assert (
        output / "grok-4.3/response-metadata.json"
    ).read_bytes() == first_error_evidence


@pytest.mark.parametrize(
    ("error", "expected_class"),
    [
        (
            _FakeRpcError("UNAUTHENTICATED", "authentication failed"),
            "authentication_failure",
        ),
        (
            _FakeRpcError("RESOURCE_EXHAUSTED", "account quota exhausted"),
            "rate_limit_or_account_quota_failure",
        ),
    ],
    ids=("authentication", "account-quota"),
)
def test_resume_after_first_global_error_finalizes_without_second_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: _FakeRpcError,
    expected_class: str,
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    first_transport = _FakeTransport([error])
    _interrupt_on_second_transition(
        monkeypatch,
        output,
        first_transport,
        target_state="not_attempted_due_to_global_failure",
        after_persisting_transition=False,
    )
    interrupted_ledger = live_probe._load_call_ledger(output)
    assert [entry["state"] for entry in interrupted_ledger["entries"]] == [
        "provider_error_received",
        "planned",
    ]
    resumed_factory_calls = 0

    def forbidden_factory() -> _FakeTransport:
        nonlocal resumed_factory_calls
        resumed_factory_calls += 1
        raise AssertionError("global-failure resume instantiated a transport")

    summary = live_probe.execute_live_probe(
        output,
        confirm_provider_call_budget=2,
        transport_factory=forbidden_factory,
    )

    assert [call["profile"]["model"] for call in first_transport.calls] == [
        "grok-4.3"
    ]
    assert resumed_factory_calls == 0
    assert summary["provider_call_count"] == 1
    assert summary["profiles"][0]["provider_error_class"] == expected_class
    assert summary["profiles"][1]["call_state"] == (
        "not_attempted_due_to_global_failure"
    )
    assert summary["phase1_4_disposition"] == (
        "phase1_4_inconclusive_operational_failure"
    )
    assert (output / "result-summary.json").is_file()


def test_second_call_sending_recovery_preserves_first_and_makes_no_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    raw = _raw_delta(_valid_delta(tracked_inputs))
    first_transport = _FakeTransport([_observation("grok-4.3", raw)])
    _interrupt_on_second_transition(
        monkeypatch,
        output,
        first_transport,
        target_state="sending",
        after_persisting_transition=True,
    )
    interrupted_ledger = live_probe._load_call_ledger(output)
    assert [entry["state"] for entry in interrupted_ledger["entries"]] == [
        "fully_validated",
        "sending",
    ]
    assert interrupted_ledger["provider_call_count"] == 2
    first_artifacts_before = {
        path.name: path.read_bytes()
        for path in (output / "grok-4.3").iterdir()
        if path.is_file()
    }
    resumed_factory_calls = 0

    def forbidden_factory() -> _FakeTransport:
        nonlocal resumed_factory_calls
        resumed_factory_calls += 1
        raise AssertionError("sending recovery instantiated a transport")

    summary = live_probe.execute_live_probe(
        output,
        confirm_provider_call_budget=2,
        transport_factory=forbidden_factory,
    )

    assert resumed_factory_calls == 0
    assert summary["provider_call_count"] == 2
    assert summary["profiles"][0]["call_state"] == "fully_validated"
    assert summary["profiles"][0]["provider_response_id"] == (
        "synthetic-response-grok-4.3"
    )
    assert summary["profiles"][1]["call_state"] == "uncertain_after_send"
    assert summary["phase1_4_disposition"] == "phase1_4_uncertain_after_send"
    assert {
        path.name: path.read_bytes()
        for path in (output / "grok-4.3").iterdir()
        if path.is_file()
    } == first_artifacts_before


def test_verify_only_incomplete_plan_preserves_and_does_not_foreclose_second_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    raw = _raw_delta(_valid_delta(tracked_inputs))
    first_transport = _FakeTransport([_observation("grok-4.3", raw)])
    _interrupt_on_second_transition(
        monkeypatch,
        output,
        first_transport,
        target_state="sending",
        after_persisting_transition=False,
    )
    ledger_before = (output / "call-ledger.json").read_bytes()
    root_final_artifacts = (
        output / "result-summary.json",
        output / "validation.json",
        output / "phase1.4-report.md",
    )
    assert not any(path.exists() for path in root_final_artifacts)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        live_probe,
        "_create_real_transport",
        lambda: (_ for _ in ()).throw(
            AssertionError("verify-only instantiated a transport")
        ),
    )

    verification = live_probe.verify_only(output)

    assert verification["status"] == "passed_incomplete_call_plan"
    assert verification["provider_call_count"] == 1
    assert verification["provider_calls_made"] == 0
    assert verification["network_transport_instantiated"] is False
    assert verification["call_ledger_unchanged"] is True
    assert (output / "call-ledger.json").read_bytes() == ledger_before
    assert live_probe._load_call_ledger(output)["entries"][1]["state"] == "planned"
    assert not any(path.exists() for path in root_final_artifacts)

    resumed_transport = _FakeTransport([_observation("grok-4.6", raw)])
    summary = _execute_with(monkeypatch, output, resumed_transport)
    assert [call["profile"]["model"] for call in resumed_transport.calls] == [
        "grok-4.6"
    ]
    assert summary["provider_call_count"] == 2


def test_determinism_status_is_incomplete_when_one_saved_response_fails_processing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracked_inputs: Mapping[str, Any],
) -> None:
    output = _prepare_private_run(monkeypatch, tmp_path)
    raw = _raw_delta(_valid_delta(tracked_inputs))
    transport = _FakeTransport(
        [_observation("grok-4.3", raw), _observation("grok-4.6", raw)]
    )
    original_process_twice = live_probe.process_response_twice
    processing_calls = 0

    def fail_second_saved_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal processing_calls
        processing_calls += 1
        if processing_calls == 2:
            raise live_probe.ProbeError(
                "synthetic saved-response double-processing failure"
            )
        return original_process_twice(*args, **kwargs)

    monkeypatch.setattr(
        live_probe, "process_response_twice", fail_second_saved_response
    )

    summary = _execute_with(monkeypatch, output, transport)
    validation = live_probe._load_strict_json_file(
        output / "validation.json", "test run validation"
    )

    assert processing_calls == 2
    assert [call["profile"]["model"] for call in transport.calls] == [
        "grok-4.3",
        "grok-4.6",
    ]
    assert summary["provider_call_count"] == 2
    assert summary["profiles"][0]["call_state"] == "fully_validated"
    assert summary["profiles"][1]["call_state"] == "terminal_validation_failure"
    assert summary["profiles"][1]["provider_response_received"] is True
    assert summary["deterministic_response_reprocessing_status"] == "incomplete"
    assert validation["saved_responses_reprocessed"] == 1
    assert validation["deterministic_reprocessing_status"] == "incomplete"
