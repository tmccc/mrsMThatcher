#!/usr/bin/env python3
"""Generate Phase 2B schemas and perform guarded local xAI SDK construction.

This helper deliberately contains the provider-specific imports that are
forbidden from the pure evidence resolver.  It reuses the frozen Phase 1.3 xAI
schema transform and network-denial guard.  It never creates a live channel or
calls an inference, model-list, or other provider method.
"""

from __future__ import annotations

import argparse
import copy
import importlib.metadata
import json
import os
import socket
import stat
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from tools import proposition_ledger_evidence_transport as transport
from tools import proposition_ledger_xai_provider_preflight as established


DEFAULT_PROMPT_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/phase2b/"
    "incremental-ledger-system-prompt-v2.txt"
)


class Phase2BTransportPreflightError(RuntimeError):
    """Fail-closed local schema/SDK preflight error."""


PINNED_PYTHON_VERSION = (3, 10, 12)
PINNED_PACKAGE_VERSIONS = {
    "xai-sdk": "1.19.0",
    "pydantic": "2.13.5",
    "protobuf": "6.33.6",
    "grpcio": "1.83.1",
    "jsonschema": "4.26.0",
}


def build_xai_provider_transport_artifacts(
    transport_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the unchanged established xAI transformation twice and audit it."""

    first, first_ledger = established.transform_provider_schema(transport_schema)
    second, second_ledger = established.transform_provider_schema(transport_schema)
    deterministic = (
        transport.canonical_json_bytes(first)
        == transport.canonical_json_bytes(second)
        and transport.canonical_json_bytes(first_ledger)
        == transport.canonical_json_bytes(second_ledger)
    )
    if not deterministic:
        raise Phase2BTransportPreflightError(
            "established xAI provider transformation is not deterministic"
        )
    allowed = {
        "insert_explicit_additional_properties_true",
        "remove_redundant_outer_anchors_for_xai_full_string_pattern",
        "identity",
    }
    unexpected = sorted(
        {
            item.get("transformation_kind")
            for item in first_ledger
            if item.get("transformation_kind") not in allowed
        }
    )
    if unexpected:
        raise Phase2BTransportPreflightError(
            f"unexpected established xAI transformation kinds: {unexpected}"
        )
    keyword_audit = established.audit_provider_keywords(first)
    reference_audit = established.audit_reference_graph(first)
    oneof_audit = established.prove_oneof_disjointness(first)
    provider_pattern_errors = transport.intended_validation_errors(
        first,
        _minimal_transport_delta(),
        pattern_mode="xai_full_string",
    )
    # The minimal root witness carries no pattern-bearing semantic additions.
    # It still checks the transformed root structure and both version constants.
    if provider_pattern_errors:
        raise Phase2BTransportPreflightError(
            "provider transport schema rejected the minimal transport witness: "
            + "; ".join(provider_pattern_errors[:8])
        )
    audit = {
        "audit_version": "phase2b-xai-provider-transport-schema-audit-v1",
        "status": "passed",
        "established_transformation_function": (
            "tools.proposition_ledger_xai_provider_preflight."
            "transform_provider_schema"
        ),
        "deterministic": deterministic,
        "transport_schema_value_sha256": transport.value_sha256(transport_schema),
        "xai_provider_schema_value_sha256": transport.value_sha256(first),
        "transformation_count": len(first_ledger),
        "additional_properties_default_expansion_count": sum(
            item.get("transformation_kind")
            == "insert_explicit_additional_properties_true"
            for item in first_ledger
        ),
        "proved_outer_anchor_removal_count": sum(
            item.get("transformation_kind")
            == "remove_redundant_outer_anchors_for_xai_full_string_pattern"
            for item in first_ledger
        ),
        "unexpected_transformation_kinds": unexpected,
        "reference_graph_all_local_and_resolved": reference_audit.get(
            "all_references_local_and_resolved"
        ),
        "oneof_pairs_structurally_disjoint": oneof_audit.get(
            "all_oneof_pairs_structurally_disjoint"
        ),
        "provider_keyword_rejected_count": len(keyword_audit.get("rejected", [])),
        "minimal_provider_witness_valid": True,
    }
    return {
        "xai_provider_transport_schema": first,
        "xai_provider_transformation_ledger": first_ledger,
        "xai_provider_transport_schema_audit": audit,
    }


def _minimal_transport_delta() -> dict[str, Any]:
    return {
        "schema_version": transport.TRANSPORT_SCHEMA_VERSION,
        "canonical_schema_version": transport.CANONICAL_SCHEMA_VERSION,
        "conversation_key": "synthetic:phase2b-local-sdk",
        "target_turn_id": "synthetic-turn-0",
        "as_of_turn_index": 0,
        "prior_ledger_reference": None,
        "new_propositions": [],
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


def build_synthetic_local_request_representations(
    *,
    system_prompt: str,
    transport_schema: Mapping[str, Any],
    xai_provider_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the two wholly synthetic retained-profile request representations."""

    manifest = transport.build_response_contract_manifest(
        transport_schema=transport_schema,
        xai_provider_schema=xai_provider_schema,
    )
    payload = transport.build_phase2b_user_payload(
        protocol_version="proposition-ledger-phase2b-evidence-transport-v1.0.0",
        protocol_hash="0" * 64,
        conversation_key="synthetic:phase2b-local-sdk",
        current_turn_id="synthetic-turn-0",
        turn_index=0,
        parent_turn_id=None,
        current_turn_text="Synthetic exact turn: aa, café, and 😀.",
        speaker_descriptor={
            "participant_id": "synthetic-participant",
            "role": "synthetic",
        },
        prior_ledger=None,
        response_contract_manifest=manifest,
    )
    return transport.build_retained_profile_request_representations(
        user_payload=payload,
        system_prompt=system_prompt,
        xai_provider_schema=xai_provider_schema,
    )


def _installed_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def compile_local_sdk_requests(
    request_representations: Sequence[Mapping[str, Any]],
    *,
    exercise_network_guard: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    """Serialize exact requests under the established fail-closed guard.

    Provider credential variables are removed without inspecting their values
    before any provider package is imported.  Registration-only channels have
    no transport and raise if an RPC method is invoked.
    """

    if len(request_representations) != 2:
        raise Phase2BTransportPreflightError("exactly two request representations required")
    established.scrub_provider_environment()
    if sys.version_info[:3] != PINNED_PYTHON_VERSION:
        raise Phase2BTransportPreflightError(
            "pinned Python mismatch: "
            f"expected={PINNED_PYTHON_VERSION} actual={sys.version_info[:3]}"
        )
    actual_versions = {
        package: _installed_version(package)
        for package in PINNED_PACKAGE_VERSIONS
    }
    version_mismatches = {
        package: {
            "expected": expected,
            "actual": actual_versions[package],
        }
        for package, expected in PINNED_PACKAGE_VERSIONS.items()
        if actual_versions[package] != expected
    }
    if version_mismatches:
        raise Phase2BTransportPreflightError(
            "pinned SDK environment mismatch: "
            + transport.canonical_json_bytes(version_mismatches).decode("utf-8")
        )
    guard = established.NetworkDenialGuard()
    results: list[dict[str, Any]] = []
    protos: list[Any] = []
    with guard:
        try:
            import grpc
        except ImportError as exc:
            raise Phase2BTransportPreflightError(
                "grpc unavailable in pinned SDK environment"
            ) from exc
        guard.patch_grpc(grpc)
        try:
            from xai_sdk.chat import BaseChat, system, user
            from xai_sdk.proto import chat_pb2
            from xai_sdk.sync.chat import Client as ChatClient
        except ImportError as exc:
            raise Phase2BTransportPreflightError(
                "xai-sdk unavailable in pinned SDK environment"
            ) from exc

        if exercise_network_guard:
            attempts = (
                ("socket", lambda: socket.create_connection(("127.0.0.1", 9))),
                ("dns", lambda: socket.getaddrinfo("synthetic.invalid", 443)),
                ("grpc", lambda: grpc.insecure_channel("synthetic.invalid:443")),
            )
            for name, attempt in attempts:
                try:
                    attempt()
                except established.NetworkDenied:
                    pass
                else:
                    raise Phase2BTransportPreflightError(
                        f"network guard did not deny deliberate {name} attempt"
                    )

        for representation in request_representations:
            if "tool_choice" in representation:
                raise Phase2BTransportPreflightError(
                    "tool_choice must be absent from the request representation"
                )
            messages = representation.get("messages")
            if (
                not isinstance(messages, list)
                or len(messages) != 2
                or messages[0].get("role") != "system"
                or messages[1].get("role") != "user"
            ):
                raise Phase2BTransportPreflightError("request messages are malformed")
            response_format_record = representation.get("response_format")
            if not isinstance(response_format_record, Mapping):
                raise Phase2BTransportPreflightError("response_format is absent")
            provider_schema = response_format_record.get("schema")
            if not isinstance(provider_schema, Mapping):
                raise Phase2BTransportPreflightError("provider schema is absent")
            schema_text = transport.canonical_json_bytes(provider_schema).decode("utf-8")
            user_text = messages[1].get("content")
            if not isinstance(user_text, str):
                raise Phase2BTransportPreflightError("user content is not text")
            if schema_text in user_text:
                raise Phase2BTransportPreflightError(
                    "complete provider schema is embedded in user message"
                )

            response_format = chat_pb2.ResponseFormat(
                format_type=chat_pb2.FORMAT_TYPE_JSON_SCHEMA,
                schema=schema_text,
            )
            channel = established._RegistrationOnlyChannel()
            client = ChatClient(channel)
            # tool_choice is intentionally omitted.  No sampling method is called.
            chat = client.create(
                model=representation["model"],
                messages=[
                    system(messages[0]["content"]),
                    user(user_text),
                ],
                max_tokens=representation["max_tokens"],
                reasoning_effort=representation["reasoning_effort"],
                tools=[],
                parallel_tool_calls=False,
                response_format=response_format,
                search_parameters=None,
                store_messages=False,
            )
            request = BaseChat._make_request(chat, 1)
            if channel.rpc_invocation_count:
                raise Phase2BTransportPreflightError(
                    "local SDK request construction invoked an RPC"
                )
            if request.HasField("tool_choice"):
                raise Phase2BTransportPreflightError(
                    "SDK protobuf unexpectedly contains tool_choice"
                )
            emitted_schema = json.loads(request.response_format.schema)
            if emitted_schema != provider_schema:
                raise Phase2BTransportPreflightError(
                    "SDK did not preserve the exact provider schema value"
                )
            serialized = request.SerializeToString(deterministic=True)
            schema_wire_occurrences = serialized.count(schema_text.encode("utf-8"))
            schema_wire_bytes = schema_text.encode("utf-8")
            message_schema_occurrences = sum(
                message.SerializeToString(deterministic=True).count(schema_wire_bytes)
                for message in request.messages
            )
            if request.response_format.schema != schema_text:
                raise Phase2BTransportPreflightError(
                    "complete schema is not exactly preserved in response_format"
                )
            if schema_wire_occurrences != 1 or message_schema_occurrences != 0:
                raise Phase2BTransportPreflightError(
                    "complete schema placement invariant failed: "
                    f"wire_occurrences={schema_wire_occurrences} "
                    f"message_occurrences={message_schema_occurrences}"
                )
            result = {
                "profile_id": f"xai-{representation['model']}-low-ledger-v2",
                "model": request.model,
                "reasoning_effort": chat_pb2.ReasoningEffort.Name(
                    request.reasoning_effort
                ).removeprefix("EFFORT_").lower(),
                "maximum_output_tokens": request.max_tokens,
                "request_contract_revision": transport.REQUEST_CONTRACT_REVISION,
                "request_construction_status": "succeeded_without_transport",
                "sdk_schema_conversion_status": "raw_schema_preserved",
                "sdk_emitted_schema_equal": True,
                "provider_schema_sha256": transport.value_sha256(provider_schema),
                "sdk_emitted_schema_sha256": transport.value_sha256(emitted_schema),
                "serialized_request_sha256": transport.sha256_bytes(serialized),
                "serialized_request_byte_length": len(serialized),
                "tool_count": len(request.tools),
                "tool_choice_present": False,
                "tool_choice_parameter_sent": False,
                "search_parameters_present": request.HasField("search_parameters"),
                "store_messages": request.store_messages,
                "streaming": False,
                "fallback_model": None,
                "application_retry_count": 0,
                "schema_absent_from_user_message": message_schema_occurrences == 0,
                "schema_complete_occurrences_in_messages": message_schema_occurrences,
                "schema_complete_occurrences_in_serialized_request": (
                    schema_wire_occurrences
                ),
                "schema_present_once_in_response_format": (
                    request.response_format.schema == schema_text
                    and schema_wire_occurrences == 1
                ),
                "message_count": len(request.messages),
                "web_search_enabled": False,
                "x_search_enabled": False,
                "code_execution_enabled": False,
                "rpc_invocation_count": channel.rpc_invocation_count,
                "provider_calls": 0,
                "inference_requests": 0,
                "model_list_requests": 0,
                "server_acceptance_status": "not_tested",
                "live_probe_authorised": False,
                "development_rerun_authorised": False,
                "sdk_versions": actual_versions,
                "python_version": ".".join(
                    str(part) for part in sys.version_info[:3]
                ),
            }
            results.append(result)
            protos.append(request)

    differ_only_by_model = False
    if len(protos) == 2:
        first = copy.deepcopy(protos[0])
        second = copy.deepcopy(protos[1])
        first.model = ""
        second.model = ""
        differ_only_by_model = (
            first.SerializeToString(deterministic=True)
            == second.SerializeToString(deterministic=True)
        )
    if not differ_only_by_model:
        raise Phase2BTransportPreflightError(
            "local SDK requests differ by more than model identity"
        )
    counts = Counter(item["category"] for item in guard.attempts)
    network_audit = {
        "guard_method": "established_phase1_3_network_denial_guard",
        "guard_active_during_provider_import_and_request_construction": True,
        "deliberate_connection_attempts_blocked": (
            all(counts[name] >= 1 for name in ("socket", "dns", "grpc"))
            if exercise_network_guard
            else True
        ),
        "attempts_observed": dict(sorted(counts.items())),
        "provider_key_environment_names_removed_before_sdk_import": list(
            established.PROVIDER_KEY_ENV_NAMES
        ),
        "provider_key_values_read": False,
        "transport_rpc_invocations": 0,
        "provider_calls": 0,
    }
    return results, network_audit, differ_only_by_model


def _pretty_json_bytes(value: Any) -> bytes:
    """Render one deterministic private JSON artifact."""

    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _private_output_directory(path: Path) -> Path:
    """Require an existing real mode-0700 private output directory."""

    candidate = Path(os.path.abspath(os.fspath(path)))
    metadata = candidate.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise Phase2BTransportPreflightError("private output is not a real directory")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise Phase2BTransportPreflightError("private output mode must be 0700")
    return candidate


def _atomic_write_private(path: Path, content: bytes) -> None:
    """Atomically write a regular mode-0600 private artifact."""

    parent = _private_output_directory(path.parent)
    if os.path.lexists(path):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise Phase2BTransportPreflightError("unsafe private artifact target")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.lexists(temporary):
            temporary.unlink()


def build_private_preflight_artifacts(
    *,
    transport_schema: Mapping[str, Any],
    schema_artifacts: Mapping[str, Any],
    provider_artifacts: Mapping[str, Any],
    local_sdk_results: Sequence[Mapping[str, Any]],
    network_audit: Mapping[str, Any],
    requests_differ_only_by_model: bool,
) -> dict[str, bytes]:
    """Build the six deterministic private schema and SDK artifacts."""

    provider_schema = provider_artifacts["xai_provider_transport_schema"]
    transformation_ledger = {
        "artifact_evidence": "deterministic schema transformation",
        "canonical_to_transport": schema_artifacts[
            "transport_schema_transformation_ledger"
        ],
        "transport_to_xai_provider": provider_artifacts[
            "xai_provider_transformation_ledger"
        ],
        "transport_schema_file_sha256": schema_artifacts[
            "transport_schema_generated_file_sha256"
        ],
        "xai_provider_schema_value_sha256": transport.value_sha256(
            provider_schema
        ),
    }
    equivalence_audit = {
        "artifact_evidence": "deterministic structural audit",
        "transport_schema_equivalence": schema_artifacts[
            "transport_schema_equivalence_audit"
        ],
        "xai_provider_schema_audit": provider_artifacts[
            "xai_provider_transport_schema_audit"
        ],
    }
    if len(local_sdk_results) != 2:
        raise Phase2BTransportPreflightError("two local SDK results are required")
    files: dict[str, Any] = {
        "xai-provider-transport-schema.json": provider_schema,
        "transport-schema-transformation-ledger.json": transformation_ledger,
        "transport-schema-equivalence-audit.json": equivalence_audit,
    }
    for expected_model, record in zip(("grok-4.3", "grok-4.6"), local_sdk_results):
        if record.get("model") != expected_model:
            raise Phase2BTransportPreflightError("local SDK result order changed")
        files[f"local-sdk-{expected_model}.json"] = {
            **copy.deepcopy(dict(record)),
            "network_denial_audit": copy.deepcopy(dict(network_audit)),
            "requests_differ_only_by_model": requests_differ_only_by_model,
        }
    rendered = {
        name: _pretty_json_bytes(value) for name, value in sorted(files.items())
    }
    rendered["transport-schema.json"] = transport.generated_schema_bytes(
        transport_schema
    )
    return dict(sorted(rendered.items()))


def write_or_verify_private_preflight_artifacts(
    output: Path,
    artifacts: Mapping[str, bytes],
    *,
    verify_only: bool,
) -> dict[str, Any]:
    """Write private preflight artifacts or verify exact existing bytes."""

    root = _private_output_directory(output)
    for name, expected in sorted(artifacts.items()):
        path = root / name
        if verify_only:
            if not path.is_file() or path.is_symlink() or path.read_bytes() != expected:
                raise Phase2BTransportPreflightError(
                    f"private preflight artifact differs: {name}"
                )
            if stat.S_IMODE(path.stat().st_mode) != 0o600:
                raise Phase2BTransportPreflightError(
                    f"private preflight artifact mode differs: {name}"
                )
        else:
            _atomic_write_private(path, expected)
    return {
        "artifact_count": len(artifacts),
        "private_artifact_status": "verified" if verify_only else "written",
        "provider_calls": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check the deterministic Phase 2B transport schema and optionally "
            "compile two synthetic requests locally under network denial."
        )
    )
    parser.add_argument("--check", action="store_true", help="byte-check tracked schema")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="alias for --check; performs no writes or provider calls",
    )
    parser.add_argument(
        "--local-sdk-check",
        action="store_true",
        help="also serialize both retained profiles locally without a transport",
    )
    parser.add_argument(
        "--write-private",
        action="store_true",
        help="write deterministic private schema/SDK artifacts",
    )
    parser.add_argument(
        "--private-output",
        type=Path,
        help="existing mode-0700 private output directory",
    )
    parser.add_argument("--system-prompt", type=Path, default=DEFAULT_PROMPT_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run deterministic schema checking and optional local-only SDK compilation."""

    args = _parser().parse_args(argv)
    if not (args.check or args.verify_only or args.local_sdk_check):
        raise Phase2BTransportPreflightError(
            "one of --check, --verify-only, or --local-sdk-check is required"
        )
    transport.main(["--check"])
    canonical = json.loads(transport.DEFAULT_CANONICAL_SCHEMA_PATH.read_text("utf-8"))
    transport_schema = json.loads(
        transport.DEFAULT_TRANSPORT_SCHEMA_PATH.read_text("utf-8")
    )
    schema_artifacts = transport.generate_transport_schema_artifacts(canonical)
    provider_artifacts = build_xai_provider_transport_artifacts(transport_schema)
    summary: dict[str, Any] = {
        "transport_schema_file_sha256": schema_artifacts[
            "transport_schema_generated_file_sha256"
        ],
        "transport_schema_value_sha256": schema_artifacts[
            "transport_schema_value_sha256"
        ],
        "xai_provider_schema_value_sha256": transport.value_sha256(
            provider_artifacts["xai_provider_transport_schema"]
        ),
        "schema_check": "passed",
        "provider_calls": 0,
    }
    if args.local_sdk_check:
        prompt = args.system_prompt.read_text(encoding="utf-8")
        representations = build_synthetic_local_request_representations(
            system_prompt=prompt,
            transport_schema=transport_schema,
            xai_provider_schema=provider_artifacts[
                "xai_provider_transport_schema"
            ],
        )
        results, network_audit, differ_only = compile_local_sdk_requests(
            representations
        )
        summary.update(
            {
                "local_sdk_results": results,
                "network_denial_audit": network_audit,
                "requests_differ_only_by_model": differ_only,
            }
        )
        if args.write_private or args.private_output is not None:
            if args.private_output is None:
                raise Phase2BTransportPreflightError(
                    "--write-private requires --private-output"
                )
            if args.write_private and args.verify_only:
                raise Phase2BTransportPreflightError(
                    "--write-private and --verify-only are mutually exclusive"
                )
            private_artifacts = build_private_preflight_artifacts(
                transport_schema=transport_schema,
                schema_artifacts=schema_artifacts,
                provider_artifacts=provider_artifacts,
                local_sdk_results=results,
                network_audit=network_audit,
                requests_differ_only_by_model=differ_only,
            )
            summary.update(
                write_or_verify_private_preflight_artifacts(
                    args.private_output,
                    private_artifacts,
                    verify_only=args.verify_only,
                )
            )
    elif args.write_private or args.private_output is not None:
        raise Phase2BTransportPreflightError(
            "private artifacts require --local-sdk-check"
        )
    sys.stdout.buffer.write(transport.canonical_json_bytes(summary) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
