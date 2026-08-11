"""Offline safety and determinism tests for reply prompt calibration."""

from __future__ import annotations

import copy
import hashlib
import csv
import io
import json
import os
import shutil
import stat
import sys
from argparse import Namespace
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import pilot_ai_first_reply_strategy as pilot
from tools import run_reply_prompt_calibration as runner


PACK: Path
SYNTHETIC_API_KEY = "synthetic-calibration-key-not-valid"
SYNTHETIC_RUNNER_COMMIT = "a" * 40
SYNTHETIC_RECOVERY_CANDIDATE_ID = (
    "synthetic-factual_or_historical_question-1"
)
SYNTHETIC_EXCLUDED_CANDIDATE_ID = (
    "synthetic-civil_challenge_or_disagreement-1"
)
SYNTHETIC_HISTORY_SOURCE_IDENTITY = "synthetic-snapshot-selected-00"
SYNTHETIC_HISTORY_TIMESTAMP = "2026-08-10T12:10:00Z"
SYNTHETIC_RECOVERED_THREAD_ID = "synthetic-recovered-thread"
SYNTHETIC_RECOVERED_PARENT_IDS = (
    "synthetic-recovered-parent-1",
    "synthetic-recovered-parent-2",
)


def _write_json_document(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_json_lines(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def build_synthetic_pack(pack: Path) -> Path:
    """Create a complete offline 48-case pack with six immutable strata."""
    pack.mkdir(mode=0o700)
    descriptors = [
        (stratum, index)
        for stratum in sorted(runner.REQUIRED_STRATA)
        for index in range(8)
    ]
    wit_warning = ("safe_wit_opportunity", 1)
    quote_descriptors = set(descriptors[:10]) | {wit_warning}
    model_rows: list[dict[str, Any]] = []
    historical_rows: list[dict[str, Any]] = []
    recent_rows: list[dict[str, Any]] = []
    frozen_rows: list[dict[str, Any]] = []
    calibration_rows: list[dict[str, Any]] = []
    for ordinal, (stratum, index) in enumerate(descriptors, 1):
        candidate_id = f"synthetic-{stratum}-{index}"
        incoming = f"Synthetic contribution for {stratum} case {index}."
        parent_thread: list[dict[str, Any]] = []
        if (stratum, index) == ("civil_challenge_or_disagreement", 1):
            incoming = "What did he mean by this?"
        elif (stratum, index) == ("formulaic_substantive_posted", 1):
            incoming = "Source for the quote?"
        elif (stratum, index) == ("genuine_social_courtesy", 1):
            incoming = "Thank you for these words."
        elif (stratum, index) == ("justified_safety_no_reply", 1):
            incoming = "Why?"
            parent_thread = [{
                "post_id": f"parent-{ordinal}",
                "author_role": "user",
                "text": "",
            }]
        elif (stratum, index) == ("safe_wit_opportunity", 2):
            incoming = "Who said this?"

        lane = "quote_tweet" if (stratum, index) in quote_descriptors else "mention"
        quoted_post = None
        if lane == "quote_tweet":
            quoted_post = {
                "post_id": f"quote-{ordinal}",
                "author_role": "account",
                "text": (
                    "" if (stratum, index) == wit_warning
                    else f"Synthetic quoted context {ordinal}."
                ),
            }
        context = {
            "target_id": f"target-{ordinal}",
            "thread_id": f"thread-{ordinal}",
            "lane": lane,
            "incoming_contribution": incoming,
            "quoted_post": quoted_post,
            "parent_thread": parent_thread,
            "clarification_request": None,
            "current_date": "2026-08-10",
        }
        recent_text = [f"Synthetic recent reply {ordinal}."]
        recent_records = [{
            "reply_text": recent_text[0],
            "terminal_timestamp": f"2026-08-09T00:{ordinal:02d}:00Z",
        }]
        model_rows.append({
            "schema_version": 1,
            "tool_version": runner.PACK_TOOL_VERSION,
            "candidate_id": candidate_id,
            "current_pipeline_lane": lane,
            "validated_context": context,
            "recent_account_replies_text": recent_text,
        })
        historical_rows.append({
            "schema_version": 1,
            "tool_version": runner.PACK_TOOL_VERSION,
            "candidate_id": candidate_id,
            "historical_reply": f"SYNTHETIC_HISTORICAL_REPLY_MARKER_{ordinal}",
            "historical_outcome": "approved",
        })
        recent_rows.append({
            "schema_version": 1,
            "tool_version": runner.PACK_TOOL_VERSION,
            "candidate_id": candidate_id,
            "recent_account_replies": recent_records,
            "recent_account_replies_text": recent_text,
        })
        frozen_rows.append({
            "schema_version": 1,
            "candidate_id": candidate_id,
            "final_stratum": stratum,
            "replay_ready": True,
            "current_pipeline_lane": lane,
            "validated_context": context,
            "recent_account_replies_text": recent_text,
            "first_timestamp": f"2026-08-10T12:{ordinal:02d}:00Z",
            "terminal_timestamp": f"2026-08-10T12:{ordinal:02d}:05Z",
        })
        if index == 0:
            calibration_rows.append({
                "schema_version": 1,
                "candidate_id": candidate_id,
                "final_stratum": stratum,
                "calibration_role": "synthetic-calibration",
                "final_rank": 1,
                "purpose": "offline test",
            })

    _write_json_lines(pack / "model_inputs.jsonl", model_rows)
    _write_json_lines(pack / "historical_baselines.jsonl", historical_rows)
    _write_json_lines(pack / "recent_account_replies.jsonl", recent_rows)
    _write_json_lines(pack / "calibration_cases.jsonl", calibration_rows)
    _write_json_lines(pack / "frozen_cases.jsonl", frozen_rows)
    _write_json_lines(
        pack / "quote_context_recovery.jsonl",
        [
            {"schema_version": 1, "candidate_id": row["candidate_id"]}
            for row in model_rows
            if row["validated_context"]["quoted_post"] is not None
        ],
    )
    _write_json_document(pack / "run_manifest.json", {
        "schema_version": runner.PACK_SCHEMA_VERSION,
        "tool_version": runner.PACK_TOOL_VERSION,
        "case_pack_version": runner.CASE_PACK_VERSION,
        "current_git_commit": runner.FROZEN_GIT_COMMIT,
        "reply_strategy_sha256": runner.FROZEN_REPLY_STRATEGY_SHA256,
        "selected_count": 48,
        "replay_ready_count": 48,
        "calibration_count": 6,
        "selected_quote_tweet_count": 11,
        "quote_contexts_recovered_from_snapshot_cache": 11,
        "quote_context_recovery_failures": 0,
        "quote_context_conflicts": 0,
    })
    _write_json_document(pack / "replay_plan.json", {
        "calibration": {"pipeline_executions": 12},
        "full_run": {"pipeline_executions": 96},
        "model_calls_performed": 0,
    })
    _write_json_document(pack / "leakage_audit.json", {
        "result": "pass",
        "violations": [],
    })
    _write_json_document(pack / "source_verification.json", {"result": "pass"})
    (pack / "case_pack_report.md").write_text(
        "# Synthetic replay pack\n", encoding="utf-8"
    )
    payloads = sorted(path for path in pack.iterdir() if path.is_file())
    assert len(payloads) == 11
    (pack / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in payloads
        ),
        encoding="utf-8",
    )
    return pack


@pytest.fixture(scope="session", autouse=True)
def synthetic_pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    global PACK
    PACK = build_synthetic_pack(tmp_path_factory.mktemp("reply-pack") / "pack")
    return PACK


class FakeResponse:
    def __init__(
        self,
        document: dict[str, Any],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.document = document
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self.document

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code))


def model_metadata() -> dict[str, Any]:
    return {
        "model": "grok-4.3",
        "retrieved_at": "2026-08-10T00:00:00Z",
        "usd_ticks_per_dollar": pilot.USD_TICKS_PER_DOLLAR,
        "prompt_text_token_price": 1,
        "cached_prompt_text_token_price": 1,
        "completion_text_token_price": 1,
    }


def successful_response() -> FakeResponse:
    return FakeResponse({
        "id": "response-test",
        "model": "grok-4.3",
        "usage": {
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "cost_in_usd_ticks": 3,
        },
        "choices": [{"message": {"content": json.dumps({"ok": True})}}],
    })


def transport_arguments(
    user_prompt: str = "user", *, stage: str = "proposer"
) -> dict[str, Any]:
    return {
        "stage": stage,
        "model": "grok-4.3",
        "system_prompt": "system",
        "user_prompt": user_prompt,
        "response_schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        "timeout_seconds": 10,
        "max_output_tokens": 2,
        "media_context": None,
    }


def cli_args(output: Path, *extra: str) -> Namespace:
    return runner.build_parser().parse_args([
        "--pack", str(PACK), "--output", str(output), *extra
    ])


def paid_cli_args(output: Path, *extra: str) -> Namespace:
    return cli_args(
        output,
        "--execute",
        "--hard-limit-usd", "1",
        "--expected-runner-git-commit", SYNTHETIC_RUNNER_COMMIT,
        "--acknowledge-paid-model-calls", runner.PAID_ACKNOWLEDGEMENT,
        *extra,
    )


class SyntheticReply:
    pipeline_metadata = {"synthetic": True}

    def __str__(self) -> str:
        return "Synthetic approved reply."


def install_synthetic_execute(
    monkeypatch: pytest.MonkeyPatch,
    output: Path,
    *,
    status: str = "approved",
    use_transport: bool = True,
    outcome_statuses: dict[tuple[str, str], str] | None = None,
) -> dict[str, int]:
    counters = {"metadata": 0, "post": 0, "pipeline": 0}
    hashes = runner.runner_source_hashes()

    def provenance(expected: str | None = None, *, require_clean_checkout: bool = False) -> dict[str, Any]:
        if require_clean_checkout and expected != SYNTHETIC_RUNNER_COMMIT:
            raise runner.CalibrationError("runner Git commit mismatch")
        return {
            "runner_git_commit": SYNTHETIC_RUNNER_COMMIT,
            "runner_git_commit_expected": expected,
            "worktree_clean": True,
            **hashes,
        }

    def metadata(**_kwargs: Any) -> dict[str, Any]:
        counters["metadata"] += 1
        assert (output / "run_identity.json").is_file()
        return model_metadata()

    def post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        counters["post"] += 1
        metadata_path = output / "provider_model_metadata.json"
        phase_path = output / "provider_phase_identity.json"
        assert metadata_path.is_file()
        assert phase_path.is_file()
        metadata_document = json.loads(metadata_path.read_text(encoding="utf-8"))
        phase_document = json.loads(phase_path.read_text(encoding="utf-8"))
        assert phase_document["provider_model_metadata_sha256"] == hashlib.sha256(
            metadata_path.read_bytes()
        ).hexdigest()
        assert phase_document["prompt_text_token_price"] == metadata_document[
            "prompt_text_token_price"
        ]
        return successful_response()

    def pipeline(**kwargs: Any) -> SimpleNamespace:
        counters["pipeline"] += 1
        execution_identity = getattr(kwargs["transport"], "identity", {})
        execution_key = (
            execution_identity.get("candidate_id"),
            execution_identity.get("variant"),
        )
        outcome_status = (outcome_statuses or {}).get(execution_key, status)
        model_stages = (
            [
                "proposer",
                "reviewer",
                "revision_proposer",
                "revision_claim_auditor",
                "revision_reviewer",
            ]
            if outcome_status == "operational_failure"
            else ["proposer"]
        )
        if use_transport:
            for stage in model_stages:
                assert json.loads(
                    kwargs["transport"](**transport_arguments(stage=stage))
                ) == {"ok": True}
        reply = SyntheticReply() if outcome_status == "approved" else None
        return SimpleNamespace(
            status=outcome_status,
            reason=f"synthetic-{outcome_status}",
            reply=reply,
            model_call_count=len(model_stages) if use_transport else 0,
            revision_count=1 if outcome_status == "operational_failure" else 0,
            audit=(
                {"stage": "quotation_resolution", "status": "not_resolved"},
                *(
                    {"stage": stage, "status": "completed"}
                    for stage in model_stages
                ),
            ),
        )

    monkeypatch.setattr(runner, "execution_provenance", provenance)
    monkeypatch.setattr(pilot, "fetch_model_metadata", metadata)
    monkeypatch.setattr(pilot.requests, "post", post)
    monkeypatch.setattr(runner.reply_strategy, "run_reply_pipeline", pipeline)
    return counters


def rewrite_pack_json(pack: Path, filename: str, transform: Any) -> None:
    path = pack / filename
    if filename.endswith(".jsonl"):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        transform(rows)
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        transform(value)
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    checksum_path = pack / "SHA256SUMS"
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line[66:] == filename:
            lines[index] = f"{digest}  {filename}"
            replaced = True
    assert replaced
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fresh_holdout_pack_data() -> dict[str, Any]:
    return runner.verify_replay_pack(PACK, case_set="holdout")


def synthetic_recovered_context(
    pack_data: dict[str, Any],
    **updates: Any,
) -> dict[str, Any]:
    case = next(
        row
        for row in pack_data["cases"]
        if row["candidate_id"] == SYNTHETIC_RECOVERY_CANDIDATE_ID
    )
    context = json.loads(json.dumps(case["context"], ensure_ascii=False))
    context.update({
        "thread_id": SYNTHETIC_RECOVERED_THREAD_ID,
        "parent_thread": [
            {
                "post_id": SYNTHETIC_RECOVERED_PARENT_IDS[0],
                "author_role": "account",
                "text": "Synthetic bounded account context.",
            },
            {
                "post_id": SYNTHETIC_RECOVERED_PARENT_IDS[1],
                "author_role": "user",
                "text": "Synthetic bounded user context.",
            },
        ],
        **updates,
    })
    return context


def synthetic_history_record(
    pack_data: dict[str, Any],
    *,
    context_updates: dict[str, Any] | None = None,
    record_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    updates = dict(record_updates or {})
    context = synthetic_recovered_context(
        pack_data,
        **(context_updates or {}),
    )
    source_sequence = updates.get("source_stream_sequence", 101)
    message = updates.get(
        "message",
        runner.HISTORY_CONTEXT_MESSAGE_PREFIX
        + " "
        + json.dumps(context, ensure_ascii=False, sort_keys=True),
    )
    raw_record_text = (
        "synthetic immutable raw record "
        + str(source_sequence)
        + "\n"
        + str(message)
    )
    raw_record_sha256 = hashlib.sha256(
        raw_record_text.encode("utf-8")
    ).hexdigest()
    record_id = "record-" + hashlib.sha256(
        ("synthetic-record-id:" + raw_record_text).encode("utf-8")
    ).hexdigest()
    record = {
        "record_id": record_id,
        "raw_record_sha256": raw_record_sha256,
        "raw_record_text": raw_record_text,
        "source_identity": SYNTHETIC_HISTORY_SOURCE_IDENTITY,
        "source_stream_sequence": source_sequence,
        "source_type": "snapshot",
        "timestamp": SYNTHETIC_HISTORY_TIMESTAMP,
        "function": "log_json_debug",
        "message": message,
        "parse_warnings": [],
        "occurrence_count": 2,
        "source_occurrence_ids": [
            "synthetic-occurrence-1",
            "synthetic-occurrence-2",
        ],
    }
    record.update(updates)
    if "raw_record_text" in updates and "raw_record_sha256" not in updates:
        record["raw_record_sha256"] = hashlib.sha256(
            str(record["raw_record_text"]).encode("utf-8")
        ).hexdigest()
    if "raw_record_text" in updates and "record_id" not in updates:
        record["record_id"] = "record-" + hashlib.sha256(
            (
                "synthetic-record-id:" + str(record["raw_record_text"])
            ).encode("utf-8")
        ).hexdigest()
    return record


def write_history_checksums(history: Path) -> None:
    names = ("run_manifest.json", "unique_log_records.jsonl")
    (history / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((history / name).read_bytes()).hexdigest()}  {name}\n"
            for name in names
        ),
        encoding="utf-8",
    )


def build_synthetic_history_corpus(
    history: Path,
    pack_data: dict[str, Any],
    *,
    records: list[dict[str, Any]] | None = None,
    manifest_updates: dict[str, Any] | None = None,
) -> Path:
    history.mkdir(mode=0o700)
    manifest = {
        "schema_version": runner.HISTORY_SCHEMA_VERSION,
        "tool_version": runner.HISTORY_TOOL_VERSION,
        "extractor_git_commit": runner.HISTORY_EXTRACTOR_GIT_COMMIT,
        "extractor_git_commit_confidence": "exact",
        "live_project_included": False,
        "selected_snapshots": [
            SYNTHETIC_HISTORY_SOURCE_IDENTITY,
            *[
                f"synthetic-snapshot-selected-{index:02d}"
                for index in range(1, runner.HISTORY_SELECTED_SNAPSHOT_COUNT)
            ],
        ],
    }
    manifest.update(manifest_updates or {})
    _write_json_document(history / "run_manifest.json", manifest)
    _write_json_lines(
        history / "unique_log_records.jsonl",
        records
        if records is not None
        else [synthetic_history_record(pack_data)],
    )
    write_history_checksums(history)
    return history


def rewrite_history_manifest(
    history: Path,
    transform: Any,
    *,
    refresh_checksums: bool = True,
) -> None:
    path = history / "run_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    transform(manifest)
    _write_json_document(path, manifest)
    if refresh_checksums:
        write_history_checksums(history)


@pytest.fixture
def synthetic_history_corpus(tmp_path: Path) -> dict[str, Any]:
    pack_data = fresh_holdout_pack_data()
    record = synthetic_history_record(pack_data)
    history = build_synthetic_history_corpus(
        tmp_path / "synthetic-history",
        pack_data,
        records=[record],
    )
    return {"history": history, "pack_data": pack_data, "record": record}


@pytest.fixture(scope="module")
def pack_data() -> dict[str, Any]:
    return runner.verify_replay_pack(PACK)


@pytest.fixture(scope="module")
def holdout_pack_data() -> dict[str, Any]:
    return runner.verify_replay_pack(PACK, case_set="holdout")


@pytest.fixture(scope="module")
def validation_pair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("reply-calibration-validation")
    first = root / "first"
    second = root / "second"
    original_get = pilot.requests.get
    original_post = pilot.requests.post
    original_transport = pilot.PilotTransport

    def forbidden_http(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("validate-only must perform no HTTP request")

    class ForbiddenTransport:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pytest.fail("validate-only must not instantiate a paid transport")

    pilot.requests.get = forbidden_http
    pilot.requests.post = forbidden_http
    pilot.PilotTransport = ForbiddenTransport  # type: ignore[assignment]
    try:
        runner.run(cli_args(first), environ={})
        runner.run(cli_args(second, "--validate-only"), environ={})
    finally:
        pilot.requests.get = original_get
        pilot.requests.post = original_post
        pilot.PilotTransport = original_transport  # type: ignore[assignment]
    return first, second


@pytest.fixture(scope="module")
def holdout_validation(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("reply-holdout-validation") / "output"
    patcher = pytest.MonkeyPatch()

    def forbidden_http(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("holdout validate-only must perform no HTTP request")

    class ForbiddenTransport:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pytest.fail("holdout validate-only must not instantiate a paid transport")

    def forbidden_pipeline(**_kwargs: Any) -> Any:
        pytest.fail("holdout validate-only must not run the reply pipeline")

    patcher.setattr(pilot.requests, "get", forbidden_http)
    patcher.setattr(pilot.requests, "post", forbidden_http)
    patcher.setattr(pilot, "PilotTransport", ForbiddenTransport)
    patcher.setattr(runner.reply_strategy, "run_reply_pipeline", forbidden_pipeline)
    try:
        runner.run(
            cli_args(output, "--case-set", "holdout", "--validate-only"),
            environ={},
        )
    finally:
        patcher.undo()
    return output


@pytest.fixture(scope="module")
def excluded_holdout_validation(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    output = tmp_path_factory.mktemp("reply-excluded-holdout-validation") / "output"
    patcher = pytest.MonkeyPatch()

    def forbidden_call(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("excluded holdout validate-only must perform no calls")

    class ForbiddenTransport:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pytest.fail("excluded validate-only must not instantiate paid transport")

    patcher.setattr(pilot.requests, "get", forbidden_call)
    patcher.setattr(pilot.requests, "post", forbidden_call)
    patcher.setattr(pilot, "PilotTransport", ForbiddenTransport)
    patcher.setattr(
        runner.reply_strategy, "run_reply_pipeline", forbidden_call
    )
    try:
        runner.run(
            cli_args(
                output,
                "--case-set",
                "holdout",
                "--exclude-from-blind-quality-candidate",
                SYNTHETIC_EXCLUDED_CANDIDATE_ID,
            ),
            environ={},
        )
    finally:
        patcher.undo()
    return output


@pytest.fixture(scope="module")
def recovered_holdout_validation(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("reply-recovered-holdout-validation")
    output = root / "output"
    pack_data = fresh_holdout_pack_data()
    record = synthetic_history_record(pack_data)
    history = build_synthetic_history_corpus(
        root / "history",
        pack_data,
        records=[record],
    )
    patcher = pytest.MonkeyPatch()

    def forbidden_http(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("recovered validate-only must perform no HTTP request")

    class ForbiddenTransport:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pytest.fail("recovered validate-only must not instantiate paid transport")

    def forbidden_pipeline(**_kwargs: Any) -> Any:
        pytest.fail("recovered validate-only must not run the reply pipeline")

    patcher.setattr(pilot.requests, "get", forbidden_http)
    patcher.setattr(pilot.requests, "post", forbidden_http)
    patcher.setattr(pilot, "PilotTransport", ForbiddenTransport)
    patcher.setattr(runner.reply_strategy, "run_reply_pipeline", forbidden_pipeline)
    try:
        runner.run(
            cli_args(
                output,
                "--case-set",
                "holdout",
                "--validate-only",
                "--history-corpus",
                str(history),
                "--recover-context-candidate",
                SYNTHETIC_RECOVERY_CANDIDATE_ID,
            ),
            environ={},
        )
    finally:
        patcher.undo()
    return {"output": output, "history": history, "record": record}


def read_clearance_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_clearance_rows(path: Path, rows: list[dict[str, str]]) -> Path:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=runner.CONTEXT_CLEARANCE_COLUMNS,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    return path


def all_ready_clearance(template: Path, destination: Path) -> Path:
    rows = read_clearance_rows(template)
    for row in rows:
        row["decision"] = "ready"
    return write_clearance_rows(destination, rows)


@pytest.fixture(scope="module")
def completed_execute_output(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("reply-calibration-execute") / "completed"
    patcher = pytest.MonkeyPatch()
    counters = install_synthetic_execute(patcher, output)
    try:
        runner.run(
            paid_cli_args(output),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    finally:
        patcher.undo()
    assert counters == {"metadata": 1, "post": 12, "pipeline": 12}
    return output


def paid_holdout_cli_args(
    output: Path,
    clearance: Path,
    pre_exposed_candidate_id: str,
    *extra: str,
) -> Namespace:
    return paid_cli_args(
        output,
        "--case-set",
        "holdout",
        "--context-clearance",
        str(clearance),
        "--continue-on-operational-failure",
        "--exclude-from-blind-quality-candidate",
        pre_exposed_candidate_id,
        *extra,
    )


@pytest.fixture(scope="module")
def completed_holdout_operational_output(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("reply-holdout-operational-execute")
    output = root / "completed"
    pack_data = fresh_holdout_pack_data()
    context_artifacts = runner.build_holdout_context_artifacts(pack_data)
    clearance_template = root / "clearance-template.csv"
    clearance_template.write_text(
        context_artifacts["clearance_template_text"], encoding="utf-8"
    )
    clearance = all_ready_clearance(
        clearance_template, root / "all-ready-clearance.csv"
    )
    plan = runner.build_execution_plan(
        pack_data,
        runner.profile_manifests(),
        runner.DEFAULT_BLIND_SEED,
    )
    execution_keys = [
        (row["candidate_id"], row["variant"]) for row in plan["executions"]
    ]
    failure_key = execution_keys[0]
    no_reply_key = next(
        key for key in reversed(execution_keys) if key[0] != failure_key[0]
    )
    pre_exposed_candidate_id = next(
        case["candidate_id"]
        for case in pack_data["cases"]
        if case["candidate_id"] not in {failure_key[0], no_reply_key[0]}
    )
    outcome_statuses = {
        failure_key: "operational_failure",
        no_reply_key: "no_reply",
    }
    patcher = pytest.MonkeyPatch()
    counters = install_synthetic_execute(
        patcher,
        output,
        outcome_statuses=outcome_statuses,
    )
    try:
        runner.run(
            paid_holdout_cli_args(
                output, clearance, pre_exposed_candidate_id
            ),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    finally:
        patcher.undo()
    assert counters == {"metadata": 1, "post": 88, "pipeline": 84}
    return {
        "output": output,
        "clearance": clearance,
        "pre_exposed_candidate_id": pre_exposed_candidate_id,
        "failure_key": failure_key,
        "no_reply_key": no_reply_key,
        "outcome_statuses": outcome_statuses,
    }


def copy_as_incomplete(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination)
    for name in runner.FINAL_OUTPUT_FILES:
        (destination / name).unlink()
    return destination


def copy_before_model_operations(source: Path, destination: Path) -> Path:
    output = copy_as_incomplete(source, destination)
    for name in ("prompt_receipts.jsonl", "pipeline_results.jsonl", "pipeline_audits.jsonl"):
        (output / name).write_text("", encoding="utf-8")
    raw_responses = output / "raw_responses"
    shutil.rmtree(raw_responses)
    raw_responses.mkdir(mode=0o700)
    ledger_path = output / "cost_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger.update({
        "operations": [],
        "known_cost_in_usd_ticks": 0,
        "known_cost_usd": 0.0,
        "ambiguous_exposure_in_usd_ticks": 0,
        "ambiguous_exposure_usd": 0.0,
        "combined_exposure_usd": 0.0,
    })
    ledger_path.write_text(
        json.dumps(ledger, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def synthetic_call_contract(
    stages: list[str], audit: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    case = synthetic_case()
    identity = runner.pipeline_execution_identity(
        case,
        "compact",
        runner.profile_manifests(),
        "f" * 64,
        "grok-4.3",
    )
    binding = runner.pipeline_execution_binding(identity)
    receipts: dict[str, dict[str, Any]] = {}
    operations: list[dict[str, Any]] = []
    for sequence, stage in enumerate(stages, 1):
        logical_call_id = f"{binding['case_identity']}:{sequence}:{stage}"
        request_hash = hashlib.sha256(logical_call_id.encode("utf-8")).hexdigest()
        receipt = {
            **identity,
            **binding,
            "stage": stage,
            "call_sequence": sequence,
            "logical_call_id": logical_call_id,
            "request_hash": request_hash,
        }
        receipts[logical_call_id] = receipt
        operations.append({
            "logical_call_id": logical_call_id,
            "case_id": binding["case_identity"],
            "stage": stage,
            "request_hash": request_hash,
            "status": "completed",
        })
    return identity, receipts, {"operations": operations}


@pytest.mark.parametrize(
    "extra",
    [
        ("--history-corpus", "/synthetic/history"),
        ("--recover-context-candidate", SYNTHETIC_RECOVERY_CANDIDATE_ID),
        (
            "--history-corpus",
            "/synthetic/history",
            "--recover-context-candidate",
            SYNTHETIC_RECOVERY_CANDIDATE_ID,
        ),
    ],
)
def test_context_recovery_arguments_are_refused_in_calibration_mode(
    extra: tuple[str, ...], tmp_path: Path
) -> None:
    args = cli_args(tmp_path / "calibration-recovery-args", *extra)
    with pytest.raises(
        runner.CalibrationError,
        match="context-recovery arguments are valid only with --case-set holdout",
    ):
        runner.validate_arguments(args, {})


def test_recovery_candidate_requires_history_corpus(tmp_path: Path) -> None:
    args = cli_args(
        tmp_path / "missing-history",
        "--case-set",
        "holdout",
        "--recover-context-candidate",
        SYNTHETIC_RECOVERY_CANDIDATE_ID,
    )
    with pytest.raises(
        runner.CalibrationError,
        match="--recover-context-candidate requires --history-corpus",
    ):
        runner.validate_arguments(args, {})


def test_continue_on_operational_failure_is_refused_in_calibration_mode(
    tmp_path: Path,
) -> None:
    args = cli_args(
        tmp_path / "calibration-continuation",
        "--continue-on-operational-failure",
    )
    with pytest.raises(
        runner.CalibrationError,
        match="continue-on-operational-failure.*case-set holdout",
    ):
        runner.validate_arguments(args, {})


def test_continue_on_operational_failure_requires_execute_mode(
    tmp_path: Path,
) -> None:
    args = cli_args(
        tmp_path / "validate-continuation",
        "--case-set",
        "holdout",
        "--continue-on-operational-failure",
    )
    with pytest.raises(
        runner.CalibrationError,
        match="continue-on-operational-failure.*--execute",
    ):
        runner.validate_arguments(args, {})


def test_blind_quality_exclusion_is_refused_outside_holdout(
    tmp_path: Path,
) -> None:
    args = cli_args(
        tmp_path / "calibration-exclusion",
        "--exclude-from-blind-quality-candidate",
        SYNTHETIC_EXCLUDED_CANDIDATE_ID,
    )
    with pytest.raises(
        runner.CalibrationError,
        match="exclude-from-blind-quality-candidate.*case-set holdout",
    ):
        runner.validate_arguments(args, {})


def test_unknown_blind_quality_exclusion_is_refused(
    tmp_path: Path,
) -> None:
    output = tmp_path / "unknown-exclusion"
    args = cli_args(
        output,
        "--case-set",
        "holdout",
        "--exclude-from-blind-quality-candidate",
        "synthetic-not-selected",
    )
    with pytest.raises(
        runner.CalibrationError,
        match="not a selected holdout candidate",
    ):
        runner.run(args, environ={})
    assert not output.exists()


def test_history_schema_mismatch_is_refused(
    synthetic_history_corpus: dict[str, Any],
) -> None:
    history = synthetic_history_corpus["history"]
    rewrite_history_manifest(
        history,
        lambda manifest: manifest.update(schema_version=1),
    )
    with pytest.raises(runner.CalibrationError, match="schema_version mismatch"):
        runner.verify_history_corpus(history)


def test_history_extractor_mismatch_is_refused(
    synthetic_history_corpus: dict[str, Any],
) -> None:
    history = synthetic_history_corpus["history"]
    rewrite_history_manifest(
        history,
        lambda manifest: manifest.update(extractor_git_commit="0" * 40),
    )
    with pytest.raises(
        runner.CalibrationError,
        match="extractor_git_commit mismatch",
    ):
        runner.verify_history_corpus(history)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("tool_version", "synthetic-wrong-tool", "tool_version mismatch"),
        (
            "extractor_git_commit_confidence",
            "inferred",
            "extractor_git_commit_confidence mismatch",
        ),
        (
            "live_project_included",
            True,
            "live_project_included must be false",
        ),
        ("selected_snapshots", ["only-one"], "exactly 32 unique snapshots"),
    ],
)
def test_history_manifest_contract_is_exact(
    field: str,
    value: Any,
    error: str,
    synthetic_history_corpus: dict[str, Any],
) -> None:
    history = synthetic_history_corpus["history"]
    rewrite_history_manifest(
        history,
        lambda manifest: manifest.update({field: value}),
    )
    with pytest.raises(runner.CalibrationError, match=error):
        runner.verify_history_corpus(history)


def test_history_checksum_mismatch_is_refused(
    synthetic_history_corpus: dict[str, Any],
) -> None:
    history = synthetic_history_corpus["history"]
    with (history / "unique_log_records.jsonl").open("ab") as handle:
        handle.write(b" ")
    with pytest.raises(runner.CalibrationError, match="checksum mismatch"):
        runner.verify_history_corpus(history)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("absolute", "unsafe or duplicate"),
        ("parent", "unsafe or duplicate"),
        ("duplicate", "unsafe or duplicate"),
        ("malformed", "malformed history SHA256SUMS"),
        ("missing", "omits required payloads"),
    ],
)
def test_history_sha256sums_rejects_unsafe_entries(
    mutation: str,
    error: str,
    synthetic_history_corpus: dict[str, Any],
) -> None:
    history = synthetic_history_corpus["history"]
    checksum_path = history / "SHA256SUMS"
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
    digest = lines[0][:64]
    if mutation == "absolute":
        lines.insert(0, f"{digest}  /synthetic/run_manifest.json")
    elif mutation == "parent":
        lines.insert(0, f"{digest}  ../run_manifest.json")
    elif mutation == "duplicate":
        lines.append(lines[0])
    elif mutation == "malformed":
        lines[0] = "g" + lines[0][1:]
    elif mutation == "missing":
        lines = [
            line
            for line in lines
            if not line.endswith("unique_log_records.jsonl")
        ]
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(runner.CalibrationError, match=error):
        runner.verify_history_corpus(history)


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_history_payloads_must_be_non_symlink_regular_files(
    kind: str,
    synthetic_history_corpus: dict[str, Any],
) -> None:
    history = synthetic_history_corpus["history"]
    payload = history / "unique_log_records.jsonl"
    retained = history / "retained-records.jsonl"
    payload.rename(retained)
    if kind == "symlink":
        payload.symlink_to(retained.name)
    else:
        payload.mkdir()
    with pytest.raises(runner.CalibrationError, match="non-symlink regular file"):
        runner.verify_history_corpus(history)


def test_unique_log_records_is_streamed(
    synthetic_history_corpus: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = synthetic_history_corpus["history"]
    pack_data = synthetic_history_corpus["pack_data"]
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes
    original_read_jsonl = runner.read_jsonl

    def guarded_read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        if path.name == "unique_log_records.jsonl":
            pytest.fail("unique history records must not use Path.read_text")
        return original_read_text(path, *args, **kwargs)

    def guarded_read_bytes(path: Path, *args: Any, **kwargs: Any) -> bytes:
        if path.name == "unique_log_records.jsonl":
            pytest.fail("unique history records must not use Path.read_bytes")
        return original_read_bytes(path, *args, **kwargs)

    def guarded_read_jsonl(path: Path) -> list[dict[str, Any]]:
        if path.name == "unique_log_records.jsonl":
            pytest.fail("unique history records must not use the materialising reader")
        return original_read_jsonl(path)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(runner, "read_jsonl", guarded_read_jsonl)
    recovery = runner.recover_holdout_contexts(
        pack_data,
        history,
        [SYNTHETIC_RECOVERY_CANDIDATE_ID],
    )
    assert recovery["recovered_context_count"] == 1


def test_exact_logged_context_is_recovered(
    synthetic_history_corpus: dict[str, Any],
) -> None:
    pack_data = synthetic_history_corpus["pack_data"]
    history = synthetic_history_corpus["history"]
    case = next(
        row
        for row in pack_data["cases"]
        if row["candidate_id"] == SYNTHETIC_RECOVERY_CANDIDATE_ID
    )
    frozen = dict(case["context"])
    logged = synthetic_recovered_context(pack_data)
    recovery = runner.recover_holdout_contexts(
        pack_data,
        history,
        [SYNTHETIC_RECOVERY_CANDIDATE_ID],
    )
    assert recovery["recovered_context_count"] == 1
    assert case["context"]["target_id"] == frozen["target_id"]
    assert case["context"]["lane"] == frozen["lane"]
    assert case["context"]["incoming_contribution"] == frozen[
        "incoming_contribution"
    ]
    for field in (
        "thread_id",
        "parent_thread",
        "quoted_post",
        "clarification_request",
        "current_date",
    ):
        assert case["context"][field] == logged[field]


def test_whitespace_only_incoming_difference_is_accepted(tmp_path: Path) -> None:
    pack_data = fresh_holdout_pack_data()
    case = next(
        row
        for row in pack_data["cases"]
        if row["candidate_id"] == SYNTHETIC_RECOVERY_CANDIDATE_ID
    )
    frozen_incoming = case["context"]["incoming_contribution"]
    whitespace_variant = " \n\t" + frozen_incoming.replace(" ", "  \n\t") + "  "
    record = synthetic_history_record(
        pack_data,
        context_updates={"incoming_contribution": whitespace_variant},
    )
    history = build_synthetic_history_corpus(
        tmp_path / "whitespace-history",
        pack_data,
        records=[record],
    )
    runner.recover_holdout_contexts(
        pack_data,
        history,
        [SYNTHETIC_RECOVERY_CANDIDATE_ID],
    )
    assert case["context"]["incoming_contribution"] == frozen_incoming


def test_substantive_incoming_difference_is_refused(tmp_path: Path) -> None:
    pack_data = fresh_holdout_pack_data()
    logged = synthetic_recovered_context(pack_data)
    record = synthetic_history_record(
        pack_data,
        context_updates={
            "incoming_contribution": logged["incoming_contribution"] + " changed"
        },
    )
    history = build_synthetic_history_corpus(
        tmp_path / "substantive-history",
        pack_data,
        records=[record],
    )
    with pytest.raises(runner.CalibrationError, match="no exact logged pipeline context"):
        runner.recover_holdout_contexts(
            pack_data,
            history,
            [SYNTHETIC_RECOVERY_CANDIDATE_ID],
        )


@pytest.mark.parametrize(
    "context_updates",
    [
        {"target_id": "synthetic-wrong-target"},
        {"lane": "__opposite__"},
    ],
    ids=["target", "lane"],
)
def test_target_or_lane_mismatch_is_refused(
    context_updates: dict[str, Any],
    tmp_path: Path,
) -> None:
    pack_data = fresh_holdout_pack_data()
    if context_updates.get("lane") == "__opposite__":
        original_lane = next(
            case["context"]["lane"]
            for case in pack_data["cases"]
            if case["candidate_id"] == SYNTHETIC_RECOVERY_CANDIDATE_ID
        )
        context_updates = {
            "lane": "mention" if original_lane == "quote_tweet" else "quote_tweet"
        }
    record = synthetic_history_record(
        pack_data,
        context_updates=context_updates,
    )
    history = build_synthetic_history_corpus(
        tmp_path / "identity-mismatch-history",
        pack_data,
        records=[record],
    )
    with pytest.raises(runner.CalibrationError, match="no exact logged pipeline context"):
        runner.recover_holdout_contexts(
            pack_data,
            history,
            [SYNTHETIC_RECOVERY_CANDIDATE_ID],
        )


def test_unselected_snapshot_source_is_refused(tmp_path: Path) -> None:
    pack_data = fresh_holdout_pack_data()
    record = synthetic_history_record(
        pack_data,
        record_updates={"source_identity": "synthetic-unselected-snapshot"},
    )
    history = build_synthetic_history_corpus(
        tmp_path / "unselected-history",
        pack_data,
        records=[record],
    )
    with pytest.raises(runner.CalibrationError, match="unselected snapshot"):
        runner.recover_holdout_contexts(
            pack_data,
            history,
            [SYNTHETIC_RECOVERY_CANDIDATE_ID],
        )


def test_matching_record_with_parse_warnings_is_refused(tmp_path: Path) -> None:
    pack_data = fresh_holdout_pack_data()
    record = synthetic_history_record(
        pack_data,
        record_updates={"parse_warnings": ["synthetic warning"]},
    )
    history = build_synthetic_history_corpus(
        tmp_path / "warning-history",
        pack_data,
        records=[record],
    )
    with pytest.raises(runner.CalibrationError, match="no exact logged pipeline context"):
        runner.recover_holdout_contexts(
            pack_data,
            history,
            [SYNTHETIC_RECOVERY_CANDIDATE_ID],
        )


def test_missing_context_record_is_refused(tmp_path: Path) -> None:
    pack_data = fresh_holdout_pack_data()
    history = build_synthetic_history_corpus(
        tmp_path / "empty-history",
        pack_data,
        records=[],
    )
    with pytest.raises(runner.CalibrationError, match="no exact logged pipeline context"):
        runner.recover_holdout_contexts(
            pack_data,
            history,
            [SYNTHETIC_RECOVERY_CANDIDATE_ID],
        )


def test_conflicting_matching_contexts_are_refused(tmp_path: Path) -> None:
    pack_data = fresh_holdout_pack_data()
    first = synthetic_history_record(pack_data)
    second = synthetic_history_record(
        pack_data,
        context_updates={"thread_id": "synthetic-conflicting-thread"},
        record_updates={"source_stream_sequence": 102},
    )
    history = build_synthetic_history_corpus(
        tmp_path / "conflicting-history",
        pack_data,
        records=[first, second],
    )
    with pytest.raises(
        runner.CalibrationError,
        match="conflicting matching history contexts",
    ):
        runner.recover_holdout_contexts(
            pack_data,
            history,
            [SYNTHETIC_RECOVERY_CANDIDATE_ID],
        )


def test_repeated_canonical_occurrences_are_corroboration(tmp_path: Path) -> None:
    pack_data = fresh_holdout_pack_data()
    record = synthetic_history_record(
        pack_data,
        record_updates={
            "occurrence_count": 3,
            "source_occurrence_ids": [
                "synthetic-occurrence-a",
                "synthetic-occurrence-b",
                "synthetic-occurrence-c",
            ],
        },
    )
    history = build_synthetic_history_corpus(
        tmp_path / "corroborated-history",
        pack_data,
        records=[record],
    )
    recovery = runner.recover_holdout_contexts(
        pack_data,
        history,
        [SYNTHETIC_RECOVERY_CANDIDATE_ID],
    )
    assert recovery["recovered_context_count"] == 1
    assert recovery["rows"][0]["source_occurrence_count"] == 3


def test_recovered_context_passes_reply_strategy_validation(
    synthetic_history_corpus: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack_data = synthetic_history_corpus["pack_data"]
    observed: list[dict[str, Any]] = []
    validate = runner.reply_strategy.validate_reply_context

    def validating_spy(context: object) -> dict[str, Any]:
        result = validate(context)
        if result["thread_id"] == SYNTHETIC_RECOVERED_THREAD_ID:
            observed.append(result)
        return result

    monkeypatch.setattr(
        runner.reply_strategy,
        "validate_reply_context",
        validating_spy,
    )
    recovery = runner.recover_holdout_contexts(
        pack_data,
        synthetic_history_corpus["history"],
        [SYNTHETIC_RECOVERY_CANDIDATE_ID],
    )
    assert len(observed) == 1
    assert recovery["rows"][0]["validator_result"] == "pass"


def test_only_requested_candidate_context_changes(
    synthetic_history_corpus: dict[str, Any],
) -> None:
    pack_data = synthetic_history_corpus["pack_data"]
    before = {
        case["candidate_id"]: runner.value_sha256(case["context"])
        for case in pack_data["cases"]
    }
    preserved = next(
        dict(case["context"])
        for case in pack_data["cases"]
        if case["candidate_id"] == SYNTHETIC_RECOVERY_CANDIDATE_ID
    )
    runner.recover_holdout_contexts(
        pack_data,
        synthetic_history_corpus["history"],
        [SYNTHETIC_RECOVERY_CANDIDATE_ID],
    )
    after = {
        case["candidate_id"]: runner.value_sha256(case["context"])
        for case in pack_data["cases"]
    }
    assert {
        candidate_id
        for candidate_id in before
        if before[candidate_id] != after[candidate_id]
    } == {SYNTHETIC_RECOVERY_CANDIDATE_ID}
    recovered = next(
        case["context"]
        for case in pack_data["cases"]
        if case["candidate_id"] == SYNTHETIC_RECOVERY_CANDIDATE_ID
    )
    for field in ("target_id", "lane", "incoming_contribution"):
        assert recovered[field] == preserved[field]


def test_holdout_identity_and_strata_survive_recovery(
    synthetic_history_corpus: dict[str, Any],
) -> None:
    pack_data = synthetic_history_corpus["pack_data"]
    candidate_ids = sorted(case["candidate_id"] for case in pack_data["cases"])
    candidate_hash = pack_data["holdout_candidate_ids_sha256"]
    strata = dict(pack_data["cases_per_stratum"])
    runner.recover_holdout_contexts(
        pack_data,
        synthetic_history_corpus["history"],
        [SYNTHETIC_RECOVERY_CANDIDATE_ID],
    )
    plan = runner.build_execution_plan(
        pack_data,
        runner.profile_manifests(),
        runner.DEFAULT_BLIND_SEED,
    )
    assert len(pack_data["cases"]) == 42
    assert sorted(case["candidate_id"] for case in pack_data["cases"]) == candidate_ids
    assert pack_data["holdout_candidate_ids_sha256"] == candidate_hash
    assert pack_data["cases_per_stratum"] == strata
    assert set(pack_data["cases_per_stratum"].values()) == {7}
    assert len(plan["executions"]) == plan["planned_pipeline_executions"] == 84


def test_recovery_changes_context_audit_and_retains_manual_review(
    synthetic_history_corpus: dict[str, Any],
) -> None:
    pack_data = synthetic_history_corpus["pack_data"]
    original_artifacts = runner.build_holdout_context_artifacts(pack_data)
    runner.recover_holdout_contexts(
        pack_data,
        synthetic_history_corpus["history"],
        [SYNTHETIC_RECOVERY_CANDIDATE_ID],
    )
    recovered_artifacts = runner.build_holdout_context_artifacts(pack_data)
    assert recovered_artifacts["context_audit_sha256"] != original_artifacts[
        "context_audit_sha256"
    ]
    audit_row = next(
        row
        for row in recovered_artifacts["audit_document"]["cases"]
        if row["candidate_id"] == SYNTHETIC_RECOVERY_CANDIDATE_ID
    )
    assert audit_row["context_recovered"] is True
    assert audit_row["parent_post_count"] == 2
    assert audit_row["manual_review_required"] is True
    assert all(
        parent_id in recovered_artifacts["review_text"]
        for parent_id in SYNTHETIC_RECOVERED_PARENT_IDS
    )


def test_recovery_refuses_stale_clearance_and_emits_blank_template(
    synthetic_history_corpus: dict[str, Any],
    tmp_path: Path,
) -> None:
    pack_data = synthetic_history_corpus["pack_data"]
    original_artifacts = runner.build_holdout_context_artifacts(pack_data)
    old_template = tmp_path / "old-clearance-template.csv"
    old_template.write_text(
        original_artifacts["clearance_template_text"],
        encoding="utf-8",
    )
    stale_clearance = all_ready_clearance(
        old_template,
        tmp_path / "old-all-ready.csv",
    )
    runner.recover_holdout_contexts(
        pack_data,
        synthetic_history_corpus["history"],
        [SYNTHETIC_RECOVERY_CANDIDATE_ID],
    )
    recovered_artifacts = runner.build_holdout_context_artifacts(pack_data)
    assert recovered_artifacts["audit_document"][
        "superseded_context_audit_sha256"
    ] == runner.STALE_HOLDOUT_CONTEXT_AUDIT_SHA256
    assert recovered_artifacts["old_context_audit_sha256_rejected"] is True
    with pytest.raises(runner.CalibrationError, match="audit SHA-256 is stale"):
        runner.validate_context_clearance(
            stale_clearance,
            recovered_artifacts,
            require_all_ready=True,
        )
    new_template = tmp_path / "new-clearance.csv"
    new_template.write_text(
        recovered_artifacts["clearance_template_text"],
        encoding="utf-8",
    )
    rows = read_clearance_rows(new_template)
    assert {row["context_audit_sha256"] for row in rows} == {
        recovered_artifacts["context_audit_sha256"]
    }
    assert all(
        row["decision"] == "" and row["reviewer_note"] == ""
        for row in rows
    )
    for row in rows:
        row["context_audit_sha256"] = runner.STALE_HOLDOUT_CONTEXT_AUDIT_SHA256
        row["decision"] = "ready"
    superseded = write_clearance_rows(
        tmp_path / "superseded-real-audit.csv", rows
    )
    with pytest.raises(runner.CalibrationError, match="audit SHA-256 is stale"):
        runner.validate_context_clearance(
            superseded,
            recovered_artifacts,
            require_all_ready=True,
        )


def test_incompatible_history_timestamp_is_refused(tmp_path: Path) -> None:
    pack_data = fresh_holdout_pack_data()
    record = synthetic_history_record(
        pack_data,
        record_updates={"timestamp": "2026-08-10T12:09:59Z"},
    )
    history = build_synthetic_history_corpus(
        tmp_path / "incompatible-time-history",
        pack_data,
        records=[record],
    )
    with pytest.raises(runner.CalibrationError, match="timestamp is incompatible"):
        runner.recover_holdout_contexts(
            pack_data,
            history,
            [SYNTHETIC_RECOVERY_CANDIDATE_ID],
        )


def test_unknown_or_calibration_recovery_candidate_is_refused(
    synthetic_history_corpus: dict[str, Any],
) -> None:
    pack_data = synthetic_history_corpus["pack_data"]
    calibration_id = pack_data["calibration_candidate_ids"][0]
    for candidate_id in ("synthetic-unknown-candidate", calibration_id):
        with pytest.raises(runner.CalibrationError, match="not in the 42-case holdout"):
            runner.recover_holdout_contexts(
                fresh_holdout_pack_data(),
                synthetic_history_corpus["history"],
                [candidate_id],
            )


def test_recovery_provenance_jsonl_is_exact(
    recovered_holdout_validation: dict[str, Any],
) -> None:
    output = recovered_holdout_validation["output"]
    rows = runner.read_jsonl(output / "holdout_context_recovery.jsonl")
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {
        "schema_version",
        "runner_version",
        "candidate_id",
        "target_id",
        "recovery_status",
        "recovery_confidence",
        "history_manifest_sha256",
        "unique_log_records_sha256",
        "record_id",
        "raw_record_sha256",
        "source_identity",
        "source_stream_sequence",
        "source_timestamp",
        "source_occurrence_count",
        "original_context_sha256",
        "recovered_context_sha256",
        "recovered_thread_id",
        "recovered_parent_post_ids",
        "recovered_parent_post_count",
        "validator_result",
    }
    source_record = recovered_holdout_validation["record"]
    history = recovered_holdout_validation["history"]
    assert row["schema_version"] == 1
    assert row["runner_version"] == "reply-prompt-calibration-v5"
    assert row["candidate_id"] == SYNTHETIC_RECOVERY_CANDIDATE_ID
    assert row["recovery_status"] == "exact_logged_pipeline_context"
    assert row["recovery_confidence"] == "exact"
    assert row["history_manifest_sha256"] == runner.file_sha256(
        history / "run_manifest.json"
    )
    assert row["unique_log_records_sha256"] == runner.file_sha256(
        history / "unique_log_records.jsonl"
    )
    field_map = {
        "record_id": "record_id",
        "raw_record_sha256": "raw_record_sha256",
        "source_identity": "source_identity",
        "source_stream_sequence": "source_stream_sequence",
        "timestamp": "source_timestamp",
        "occurrence_count": "source_occurrence_count",
    }
    for source_field, output_field in field_map.items():
        assert row[output_field] == source_record[source_field]
    assert row["recovered_thread_id"] == SYNTHETIC_RECOVERED_THREAD_ID
    assert row["recovered_parent_post_ids"] == list(SYNTHETIC_RECOVERED_PARENT_IDS)
    assert row["recovered_parent_post_count"] == 2
    assert row["validator_result"] == "pass"
    assert not (
        set(row)
        & {
            "message",
            "raw_record_text",
            "historical_reply",
            "generated_reply",
            "prompt_output",
            "model_response",
        }
    )


def test_recovery_is_bound_into_execution_plan(
    recovered_holdout_validation: dict[str, Any],
) -> None:
    output = recovered_holdout_validation["output"]
    plan = json.loads((output / "execution_plan.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    binding_fields = set(runner.context_recovery_binding(fresh_holdout_pack_data()))
    assert binding_fields <= set(plan)
    assert {field: plan[field] for field in binding_fields} == {
        field: manifest[field] for field in binding_fields
    }
    assert plan["recovered_context_count"] == 1
    assert plan["context_recovery_failures"] == 0
    assert plan["context_recovery_conflicts"] == 0
    recovered_rows = [
        row
        for row in plan["executions"]
        if row["candidate_id"] == SYNTHETIC_RECOVERY_CANDIDATE_ID
    ]
    assert len(recovered_rows) == 2
    assert len({row["validated_context_sha256"] for row in recovered_rows}) == 1


def test_recovery_is_bound_into_run_identity(
    synthetic_history_corpus: dict[str, Any],
    tmp_path: Path,
) -> None:
    pack_data = synthetic_history_corpus["pack_data"]
    history = synthetic_history_corpus["history"]
    runner.recover_holdout_contexts(
        pack_data,
        history,
        [SYNTHETIC_RECOVERY_CANDIDATE_ID],
    )
    manifests = runner.profile_manifests()
    plan = runner.build_execution_plan(
        pack_data,
        manifests,
        runner.DEFAULT_BLIND_SEED,
    )
    artifacts = runner.build_holdout_context_artifacts(pack_data)
    provenance = {
        "runner_git_commit": SYNTHETIC_RUNNER_COMMIT,
        "runner_git_commit_expected": SYNTHETIC_RUNNER_COMMIT,
        "worktree_clean": True,
        **runner.runner_source_hashes(),
        "evidence_repository_fingerprint": "e" * 64,
    }
    args = paid_cli_args(
        tmp_path / "recovered-identity",
        "--case-set",
        "holdout",
        "--context-clearance",
        str(tmp_path / "unused-clearance.csv"),
        "--history-corpus",
        str(history),
        "--recover-context-candidate",
        SYNTHETIC_RECOVERY_CANDIDATE_ID,
    )
    clearance = {"normalized_sha256": "c" * 64}
    identity = runner.build_run_identity(
        args,
        pack_data,
        manifests,
        plan,
        provenance,
        context_artifacts=artifacts,
        clearance=clearance,
    )
    binding = runner.context_recovery_binding(pack_data)
    assert {field: identity[field] for field in binding} == binding
    assert identity["context_audit_sha256"] == artifacts["context_audit_sha256"]
    assert identity["execution_plan_sha256"] == runner.value_sha256(plan)


@pytest.mark.parametrize(
    "recovery_argument_mode",
    ["none", "history-only", "candidate-only"],
)
def test_execute_without_identical_recovery_arguments_is_refused_before_calls(
    recovery_argument_mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_pack = fresh_holdout_pack_data()
    history = build_synthetic_history_corpus(
        tmp_path / "execute-binding-history",
        source_pack,
    )
    runner.recover_holdout_contexts(
        source_pack,
        history,
        [SYNTHETIC_RECOVERY_CANDIDATE_ID],
    )
    artifacts = runner.build_holdout_context_artifacts(source_pack)
    template = tmp_path / "recovered-clearance-template.csv"
    template.write_text(artifacts["clearance_template_text"], encoding="utf-8")
    clearance = all_ready_clearance(template, tmp_path / "recovered-ready.csv")
    monkeypatch.setattr(
        pilot.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("unexpected provider request"),
    )
    monkeypatch.setattr(
        pilot.requests,
        "post",
        lambda *_args, **_kwargs: pytest.fail("unexpected model request"),
    )
    monkeypatch.setattr(
        runner,
        "execute_run",
        lambda *_args, **_kwargs: pytest.fail("invalid recovery reached execute"),
    )
    if recovery_argument_mode == "history-only":
        extra = ("--history-corpus", str(history))
        expected_error = "audit SHA-256 is stale"
    elif recovery_argument_mode == "candidate-only":
        extra = (
            "--recover-context-candidate",
            SYNTHETIC_RECOVERY_CANDIDATE_ID,
        )
        expected_error = "requires --history-corpus"
    else:
        extra = ()
        expected_error = "audit SHA-256 is stale"
    args = paid_cli_args(
        tmp_path / f"execute-mismatch-{recovery_argument_mode}",
        "--case-set",
        "holdout",
        "--context-clearance",
        str(clearance),
        *extra,
    )
    with pytest.raises(runner.CalibrationError, match=expected_error):
        runner.run(args, environ={"XAI_API_KEY": SYNTHETIC_API_KEY})


def test_recovered_validate_only_preserves_prompt_and_profile_hashes(
    recovered_holdout_validation: dict[str, Any],
) -> None:
    output = recovered_holdout_validation["output"]
    written = json.loads((output / "profile_manifests.json").read_text(encoding="utf-8"))
    expected = runner.profile_manifests()
    runner.verify_frozen_profile_manifests(written)
    assert written == expected
    assert {
        variant: {
            name: prompt["sha256"]
            for name, prompt in manifest["prompts"].items()
        }
        for variant, manifest in written.items()
    } == {
        variant: {
            name: prompt["sha256"]
            for name, prompt in manifest["prompts"].items()
        }
        for variant, manifest in expected.items()
    }


def test_recovered_validate_only_performs_zero_model_and_http_calls(
    recovered_holdout_validation: dict[str, Any],
) -> None:
    output = recovered_holdout_validation["output"]
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    report = json.loads((output / "validation_report.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "validate-only"
    assert manifest["model_calls_performed"] == 0
    assert manifest["http_requests_performed"] == 0
    assert report["model_calls_performed"] == 0
    assert report["http_requests_performed"] == 0


def test_recovered_validate_only_sha256sums_verifies(
    recovered_holdout_validation: dict[str, Any],
) -> None:
    output = recovered_holdout_validation["output"]
    runner.verify_output_sha256sums(output)
    checksum_names = {
        line[66:]
        for line in (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    }
    assert "holdout_context_recovery.jsonl" in checksum_names
    assert runner.HOLDOUT_CONTEXT_FILES <= checksum_names


def test_replay_pack_checksum_verification(pack_data: dict[str, Any], tmp_path: Path) -> None:
    assert len(pack_data["checksums"]) == 11
    corrupt = tmp_path / "corrupt-pack"
    shutil.copytree(PACK, corrupt)
    with (corrupt / "run_manifest.json").open("ab") as handle:
        handle.write(b" ")
    with pytest.raises(runner.CalibrationError, match="checksum mismatch"):
        runner.verify_replay_pack(corrupt)


def test_wrong_pack_commit_is_refused(tmp_path: Path) -> None:
    altered = tmp_path / "wrong-commit-pack"
    shutil.copytree(PACK, altered)
    rewrite_pack_json(altered, "run_manifest.json", lambda value: value.update(current_git_commit="0" * 40))
    with pytest.raises(runner.CalibrationError, match="current_git_commit mismatch"):
        runner.verify_replay_pack(altered)


def test_wrong_reply_strategy_hash_is_refused(tmp_path: Path) -> None:
    altered = tmp_path / "wrong-strategy-pack"
    shutil.copytree(PACK, altered)
    rewrite_pack_json(altered, "run_manifest.json", lambda value: value.update(reply_strategy_sha256="0" * 64))
    with pytest.raises(runner.CalibrationError, match="reply_strategy_sha256 mismatch"):
        runner.verify_replay_pack(altered)


def test_six_unique_calibration_cases_are_required(tmp_path: Path) -> None:
    altered = tmp_path / "duplicate-calibration-pack"
    shutil.copytree(PACK, altered)

    def duplicate(rows: list[dict[str, Any]]) -> None:
        rows[-1]["candidate_id"] = rows[0]["candidate_id"]

    rewrite_pack_json(altered, "calibration_cases.jsonl", duplicate)
    with pytest.raises(runner.CalibrationError, match="repeats candidate_id"):
        runner.verify_replay_pack(altered)


def test_one_calibration_case_per_stratum_is_required(tmp_path: Path) -> None:
    altered = tmp_path / "duplicate-stratum-pack"
    shutil.copytree(PACK, altered)

    def duplicate_stratum(rows: list[dict[str, Any]]) -> None:
        rows[-1]["final_stratum"] = rows[0]["final_stratum"]

    rewrite_pack_json(altered, "calibration_cases.jsonl", duplicate_stratum)
    with pytest.raises(runner.CalibrationError, match="one case in each final stratum"):
        runner.verify_replay_pack(altered)


def test_exactly_twelve_pipeline_executions_are_planned(pack_data: dict[str, Any]) -> None:
    manifests = runner.profile_manifests()
    plan = runner.build_execution_plan(pack_data, manifests, runner.DEFAULT_BLIND_SEED)
    assert plan["planned_pipeline_executions"] == 12
    assert len(plan["executions"]) == 12
    assert Counter(row["variant"] for row in plan["executions"]) == {"current": 6, "compact": 6}


def test_default_case_set_remains_calibration(tmp_path: Path) -> None:
    args = cli_args(tmp_path / "default")
    assert args.case_set == "calibration"
    selected = runner.verify_replay_pack(PACK)
    assert selected["case_set"] == "calibration"
    assert selected["selected_case_count"] == 6


def test_holdout_is_exact_unique_complement_with_seven_per_stratum(
    pack_data: dict[str, Any], holdout_pack_data: dict[str, Any]
) -> None:
    calibration_ids = {case["candidate_id"] for case in pack_data["cases"]}
    holdout_ids = {case["candidate_id"] for case in holdout_pack_data["cases"]}
    model_ids = {
        row["candidate_id"] for row in runner.read_jsonl(PACK / "model_inputs.jsonl")
    }
    selected_calibration_ids = {
        row["candidate_id"]
        for row in runner.read_jsonl(PACK / "calibration_cases.jsonl")
    }
    all_ids = {case["candidate_id"] for case in holdout_pack_data["all_cases"]}
    assert len(all_ids) == 48
    assert len(calibration_ids) == 6
    assert len(holdout_ids) == 42
    assert calibration_ids == selected_calibration_ids
    assert all_ids == model_ids
    assert calibration_ids.isdisjoint(holdout_ids)
    assert holdout_ids == model_ids - selected_calibration_ids
    assert calibration_ids | holdout_ids == all_ids
    assert holdout_pack_data["excluded_calibration_case_count"] == 6
    assert holdout_pack_data["cases_per_stratum"] == {
        stratum: 7 for stratum in sorted(runner.REQUIRED_STRATA)
    }


def test_holdout_plan_has_exactly_84_disjoint_executions(
    pack_data: dict[str, Any], holdout_pack_data: dict[str, Any]
) -> None:
    manifests = runner.profile_manifests()
    calibration_plan = runner.build_execution_plan(
        pack_data, manifests, runner.DEFAULT_BLIND_SEED
    )
    holdout_plan = runner.build_execution_plan(
        holdout_pack_data, manifests, runner.DEFAULT_BLIND_SEED
    )
    assert calibration_plan["planned_pipeline_executions"] == 12
    assert holdout_plan["planned_pipeline_executions"] == 84
    assert len(holdout_plan["executions"]) == 84
    assert Counter(row["variant"] for row in holdout_plan["executions"]) == {
        "current": 42,
        "compact": 42,
    }
    calibration_ids = {row["candidate_id"] for row in calibration_plan["executions"]}
    holdout_ids = {row["candidate_id"] for row in holdout_plan["executions"]}
    raw_model_ids = {
        row["candidate_id"] for row in runner.read_jsonl(PACK / "model_inputs.jsonl")
    }
    raw_calibration_ids = {
        row["candidate_id"]
        for row in runner.read_jsonl(PACK / "calibration_cases.jsonl")
    }
    expected_holdout_ids = raw_model_ids - raw_calibration_ids
    assert calibration_ids.isdisjoint(holdout_ids)
    assert holdout_ids == expected_holdout_ids
    assert Counter(row["candidate_id"] for row in holdout_plan["executions"]) == {
        candidate_id: 2 for candidate_id in expected_holdout_ids
    }
    assert all(
        count == 1
        for count in Counter(
            (row["candidate_id"], row["variant"])
            for row in holdout_plan["executions"]
        ).values()
    )
    assert calibration_plan["case_set"] == "calibration"
    assert holdout_plan["case_set"] == "holdout"
    assert holdout_plan["selected_candidate_ids_sha256"] == holdout_pack_data[
        "holdout_candidate_ids_sha256"
    ]


def test_candidate_id_hashes_are_sorted_and_deterministic(
    holdout_pack_data: dict[str, Any]
) -> None:
    candidate_ids = [case["candidate_id"] for case in holdout_pack_data["cases"]]
    expected = hashlib.sha256(
        runner.canonical_json_bytes(sorted(candidate_ids))
    ).hexdigest()
    assert runner.candidate_ids_sha256(candidate_ids) == expected
    assert runner.candidate_ids_sha256(reversed(candidate_ids)) == expected
    assert holdout_pack_data["holdout_candidate_ids_sha256"] == expected


def test_case_set_changes_durable_run_identity(
    pack_data: dict[str, Any], holdout_pack_data: dict[str, Any], tmp_path: Path
) -> None:
    manifests = runner.profile_manifests()
    source_hashes = runner.runner_source_hashes()
    provenance = {
        "runner_git_commit": SYNTHETIC_RUNNER_COMMIT,
        "runner_git_commit_expected": SYNTHETIC_RUNNER_COMMIT,
        "worktree_clean": True,
        **source_hashes,
        "evidence_repository_fingerprint": "e" * 64,
    }
    calibration_args = paid_cli_args(tmp_path / "calibration-identity")
    holdout_args = paid_cli_args(
        tmp_path / "holdout-identity",
        "--case-set",
        "holdout",
        "--context-clearance",
        str(tmp_path / "unused-clearance.csv"),
    )
    calibration_plan = runner.build_execution_plan(
        pack_data, manifests, runner.DEFAULT_BLIND_SEED
    )
    holdout_plan = runner.build_execution_plan(
        holdout_pack_data, manifests, runner.DEFAULT_BLIND_SEED
    )
    calibration_identity = runner.build_run_identity(
        calibration_args, pack_data, manifests, calibration_plan, provenance
    )
    holdout_identity = runner.build_run_identity(
        holdout_args, holdout_pack_data, manifests, holdout_plan, provenance
    )
    assert calibration_identity["case_set"] == "calibration"
    assert holdout_identity["case_set"] == "holdout"
    assert calibration_identity["selected_candidate_ids_sha256"] != holdout_identity[
        "selected_candidate_ids_sha256"
    ]
    assert runner.value_sha256(calibration_identity) != runner.value_sha256(
        holdout_identity
    )


def test_calibration_execute_output_cannot_resume_as_holdout(
    completed_execute_output: Path,
    holdout_validation: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "cross-case-set-resume"
    shutil.copytree(completed_execute_output, output)
    clearance_path = all_ready_clearance(
        holdout_validation / "holdout_context_clearance.csv",
        tmp_path / "resume-all-ready.csv",
    )
    counters = install_synthetic_execute(monkeypatch, output)
    args = paid_cli_args(
        output,
        "--resume",
        "--case-set",
        "holdout",
        "--context-clearance",
        str(clearance_path),
    )
    with pytest.raises(runner.CalibrationError, match="run identity differs"):
        runner.run(args, environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_all_factual_holdout_cases_require_manual_context_review(
    holdout_validation: Path,
) -> None:
    audit = json.loads(
        (holdout_validation / "holdout_context_audit.json").read_text(encoding="utf-8")
    )
    factual = [
        row
        for row in audit["cases"]
        if row["final_stratum"] == "factual_or_historical_question"
    ]
    assert len(factual) == 7
    assert all(row["manual_review_required"] is True for row in factual)
    expected_fields = {
        "candidate_id",
        "final_stratum",
        "lane",
        "validated_context_sha256",
        "context_recovered",
        "incoming_text_sha256",
        "quoted_post_present",
        "quoted_post_text_sha256",
        "parent_post_count",
        "nonempty_parent_post_count",
        "parent_context_sha256",
        "clarification_request_present",
        "context_dependency_flags",
        "manual_review_required",
    }
    assert all(set(row) == expected_fields for row in audit["cases"])
    forbidden_fields = {
        "historical",
        "historical_reply",
        "historical_outcome",
        "current_output",
        "compact_output",
        "calibration_score",
        "score",
        "rank",
    }
    assert all(not (set(row) & forbidden_fields) for row in audit["cases"])


@pytest.mark.parametrize(
    ("incoming", "lane", "quoted_text", "parent_text", "expected_flags"),
    [
        (
            "Why?",
            "mention",
            None,
            None,
            ("short_elliptical_question", "missing_or_empty_bounded_parent_context"),
        ),
        (
            "He offered an answer.",
            "mention",
            None,
            None,
            (
                "unresolved_third_person_pronoun",
                "missing_or_empty_bounded_parent_context",
            ),
        ),
        (
            "Herself alone.",
            "mention",
            None,
            None,
            (
                "unresolved_third_person_pronoun",
                "missing_or_empty_bounded_parent_context",
            ),
        ),
        (
            "This matters.",
            "mention",
            None,
            None,
            ("demonstrative_reference", "missing_or_empty_bounded_parent_context"),
        ),
        (
            "What did the speaker mean?",
            "mention",
            None,
            None,
            (
                "short_elliptical_question",
                "what_did_mean_question",
                "source_or_attribution_question",
                "missing_or_empty_bounded_parent_context",
            ),
        ),
        (
            "Who wrote the passage?",
            "mention",
            None,
            None,
            (
                "short_elliptical_question",
                "source_or_attribution_question",
                "missing_or_empty_bounded_parent_context",
            ),
        ),
        (
            "Could you please identify the author responsible for composing the passage in question?",
            "mention",
            None,
            None,
            (
                "source_or_attribution_question",
                "missing_or_empty_bounded_parent_context",
            ),
        ),
        (
            "The quote is striking.",
            "mention",
            None,
            None,
            ("quote_or_above_reference", "missing_or_empty_bounded_parent_context"),
        ),
        (
            "A standalone note.",
            "quote_tweet",
            "",
            None,
            ("missing_quoted_post_text",),
        ),
        (
            "Those are striking.",
            "mention",
            None,
            "",
            ("demonstrative_reference", "missing_or_empty_bounded_parent_context"),
        ),
        ("The policy has merit.", "mention", None, None, ()),
    ],
)
def test_context_dependency_flags_are_deterministic(
    incoming: str,
    lane: str,
    quoted_text: str | None,
    parent_text: str | None,
    expected_flags: tuple[str, ...],
) -> None:
    context = dict(synthetic_case()["context"])
    context["incoming_contribution"] = incoming
    context["lane"] = lane
    context["quoted_post"] = (
        None
        if quoted_text is None
        else {"post_id": "quote", "author_role": "account", "text": quoted_text}
    )
    context["parent_thread"] = (
        []
        if parent_text is None
        else [{"post_id": "parent", "author_role": "user", "text": parent_text}]
    )
    first = runner.context_dependency_flags(context)
    second = runner.context_dependency_flags(json.loads(json.dumps(context)))
    assert first == second
    assert first == list(expected_flags)
    assert first == [flag for flag in runner.CONTEXT_DEPENDENCY_FLAG_ORDER if flag in first]


def test_holdout_context_review_contains_no_outcomes_or_experiment_results(
    holdout_validation: Path,
) -> None:
    review = (holdout_validation / "holdout_context_review.md").read_text(
        encoding="utf-8"
    )
    assert "SYNTHETIC_HISTORICAL_REPLY_MARKER_" not in review
    assert "historical_outcome" not in review
    assert "SYNTHETIC_CURRENT_RESULT_MARKER" not in review
    assert "SYNTHETIC_COMPACT_RESULT_MARKER" not in review
    adversarial_case = synthetic_case()
    adversarial_case["context"] = dict(adversarial_case["context"])
    adversarial_case["context"].update({
        "incoming_contribution": "Corrected exact incoming context.",
        "quoted_post": {
            "post_id": "exact-quoted-post-id",
            "author_role": "account",
            "text": "Exact quoted-post text.",
        },
        "parent_thread": [{
            "post_id": "exact-parent-post-id",
            "author_role": "user",
            "text": "Exact bounded parent text.",
        }],
        "clarification_request": {
            "original_question": "Exact original question?",
            "correction": "Corrected exact incoming context.",
        },
    })
    adversarial_case.update({
        "historical": {
            "historical_reply": "SYNTHETIC_HISTORICAL_REPLY_MARKER_ADVERSARIAL",
            "historical_outcome": "SYNTHETIC_HISTORICAL_OUTCOME_MARKER",
        },
        "current_output": "SYNTHETIC_CURRENT_RESULT_MARKER",
        "compact_output": "SYNTHETIC_COMPACT_RESULT_MARKER",
        "prompt_profile": "SYNTHETIC_PROMPT_PROFILE_MARKER",
        "blind_assignment": "SYNTHETIC_BLIND_ASSIGNMENT_MARKER",
        "score": "SYNTHETIC_SCORE_MARKER",
        "rank": "SYNTHETIC_RANK_MARKER",
    })
    adversarial_review = runner._render_holdout_context_review([adversarial_case])
    for permitted_exact_value in (
        "Corrected exact incoming context.",
        "exact-quoted-post-id",
        "Author role: account",
        "Exact quoted-post text.",
        "exact-parent-post-id",
        "Author role: user",
        "Exact bounded parent text.",
        "Exact original question?",
    ):
        assert permitted_exact_value in adversarial_review
    for marker in (
        "SYNTHETIC_HISTORICAL_REPLY_MARKER_ADVERSARIAL",
        "SYNTHETIC_HISTORICAL_OUTCOME_MARKER",
        "SYNTHETIC_CURRENT_RESULT_MARKER",
        "SYNTHETIC_COMPACT_RESULT_MARKER",
        "SYNTHETIC_PROMPT_PROFILE_MARKER",
        "SYNTHETIC_BLIND_ASSIGNMENT_MARKER",
        "SYNTHETIC_SCORE_MARKER",
        "SYNTHETIC_RANK_MARKER",
    ):
        assert marker not in adversarial_review
    for forbidden in ("Response A", "Response B", "Response C", "prompt profile", "blind assignment", "score", "rank"):
        assert forbidden.casefold() not in review.casefold()


def test_clearance_template_is_blank_and_repeats_exact_audit_hash(
    holdout_validation: Path,
) -> None:
    audit_path = holdout_validation / "holdout_context_audit.json"
    expected_hash = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    rows = read_clearance_rows(holdout_validation / "holdout_context_clearance.csv")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert (holdout_validation / "holdout_context_clearance.csv").read_text(
        encoding="utf-8"
    ).splitlines()[0] == ",".join(runner.CONTEXT_CLEARANCE_COLUMNS)
    assert len(rows) == audit["manual_context_review_count"]
    assert rows
    expected_review_inventory = {
        (row["candidate_id"], row["final_stratum"])
        for row in audit["cases"]
        if row["manual_review_required"]
    }
    assert {
        (row["candidate_id"], row["final_stratum"]) for row in rows
    } == expected_review_inventory
    assert {row["context_audit_sha256"] for row in rows} == {expected_hash}
    assert all(row["decision"] == "" and row["reviewer_note"] == "" for row in rows)


def test_pending_clearance_allows_holdout_validate_only(
    holdout_validation: Path,
) -> None:
    manifest = json.loads(
        (holdout_validation / "run_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["case_set"] == "holdout"
    assert manifest["pack_case_count"] == 48
    assert manifest["calibration_case_count"] == 6
    assert manifest["selected_case_count"] == 42
    assert manifest["selected_unique_candidate_count"] == 42
    assert manifest["excluded_calibration_case_count"] == 6
    assert manifest["calibration_holdout_overlap_count"] == 0
    assert set(manifest["cases_per_stratum"].values()) == {7}
    assert manifest["planned_pipeline_executions"] == 84
    expected_pack = runner.verify_replay_pack(PACK, case_set="holdout")
    assert manifest["calibration_candidate_ids_sha256"] == expected_pack[
        "calibration_candidate_ids_sha256"
    ]
    assert manifest["holdout_candidate_ids_sha256"] == expected_pack[
        "holdout_candidate_ids_sha256"
    ]
    assert manifest["context_audit_sha256"] == hashlib.sha256(
        (holdout_validation / "holdout_context_audit.json").read_bytes()
    ).hexdigest()
    assert manifest["manual_context_review_count"] >= 7
    assert manifest["context_clearance_status"] == "pending"
    assert manifest["paid_execution_ready"] is False
    assert manifest["model_calls_performed"] == 0
    assert manifest["http_requests_performed"] == 0
    report = json.loads(
        (holdout_validation / "validation_report.json").read_text(encoding="utf-8")
    )
    assert report["current_profile_uses_exact_production_prompt_functions"] is True
    assert report["current_reply_strategy_matches_frozen_pack"] is True
    assert report["replay_pack_provenance_pass"] is True
    assert report["leakage_check_pass"] is True
    assert report["posting_enabled"] is False
    assert report["search_enabled"] is False
    assert report["tools_enabled"] is False
    assert report["media_enabled"] is False
    assert len(manifest["evidence_repository_fingerprint"]) == 64


def test_calibration_validate_only_creates_no_holdout_context_artifacts(
    validation_pair: tuple[Path, Path],
) -> None:
    manifest = json.loads(
        (validation_pair[0] / "run_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["case_set"] == "calibration"
    assert manifest["selected_case_count"] == 6
    assert manifest["planned_pipeline_executions"] == 12
    assert manifest["context_clearance_status"] == "not_applicable"
    assert not any((validation_pair[0] / name).exists() for name in runner.HOLDOUT_CONTEXT_FILES)


def test_holdout_execute_without_clearance_is_refused_before_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pilot.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("unexpected provider request"),
    )
    monkeypatch.setattr(
        pilot.requests,
        "post",
        lambda *_args, **_kwargs: pytest.fail("unexpected model request"),
    )
    args = paid_cli_args(tmp_path / "no-clearance", "--case-set", "holdout")
    with pytest.raises(runner.CalibrationError, match="requires --context-clearance"):
        runner.run(args, environ={"XAI_API_KEY": SYNTHETIC_API_KEY})


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("missing", "missing reviewed candidates"),
        ("duplicate", "repeats candidate"),
        ("stale", "audit SHA-256 is stale"),
        ("needs_recovery", "contains needs_recovery"),
        ("exclude", "contains exclude"),
    ],
)
def test_invalid_holdout_clearance_is_refused(
    mutation: str,
    error: str,
    holdout_validation: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = read_clearance_rows(holdout_validation / "holdout_context_clearance.csv")
    for row in rows:
        row["decision"] = "ready"
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(dict(rows[0]))
    elif mutation == "stale":
        rows[0]["context_audit_sha256"] = "0" * 64
    elif mutation in {"needs_recovery", "exclude"}:
        rows[0]["decision"] = mutation
    clearance = write_clearance_rows(tmp_path / f"{mutation}.csv", rows)
    monkeypatch.setattr(
        runner,
        "execute_run",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid clearance reached holdout execution"
        ),
    )
    args = paid_cli_args(
        tmp_path / f"invalid-{mutation}-output",
        "--case-set",
        "holdout",
        "--context-clearance",
        str(clearance),
    )
    with pytest.raises(runner.CalibrationError, match=error):
        runner.run(args, environ={"XAI_API_KEY": SYNTHETIC_API_KEY})


def test_exact_all_ready_clearance_permits_holdout_execution_planning(
    holdout_pack_data: dict[str, Any], holdout_validation: Path, tmp_path: Path
) -> None:
    artifacts = runner.build_holdout_context_artifacts(holdout_pack_data)
    clearance_path = all_ready_clearance(
        holdout_validation / "holdout_context_clearance.csv",
        tmp_path / "all-ready.csv",
    )
    clearance = runner.validate_context_clearance(
        clearance_path, artifacts, require_all_ready=True
    )
    plan = runner.build_execution_plan(
        holdout_pack_data, runner.profile_manifests(), runner.DEFAULT_BLIND_SEED
    )
    assert clearance["status"] == "ready"
    assert clearance["paid_execution_ready"] is True
    assert len(plan["executions"]) == plan["planned_pipeline_executions"] == 84


def test_all_ready_clearance_reaches_execute_only_after_84_case_plan_is_bound(
    holdout_validation: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clearance_path = all_ready_clearance(
        holdout_validation / "holdout_context_clearance.csv",
        tmp_path / "run-all-ready.csv",
    )
    observed: dict[str, Any] = {}

    def fake_execute(
        args: Namespace,
        pack_data: dict[str, Any],
        api_key: str,
        *,
        context_artifacts: dict[str, Any] | None,
        clearance: dict[str, Any] | None,
    ) -> Path:
        plan = runner.build_execution_plan(
            pack_data, runner.profile_manifests(), args.blind_seed
        )
        observed.update({
            "api_key": api_key,
            "case_set": pack_data["case_set"],
            "selected": pack_data["selected_case_count"],
            "planned": plan["planned_pipeline_executions"],
            "audit": context_artifacts["context_audit_sha256"],
            "clearance": clearance["status"],
        })
        return args.output

    monkeypatch.setattr(runner, "execute_run", fake_execute)
    args = paid_cli_args(
        tmp_path / "would-execute",
        "--case-set",
        "holdout",
        "--context-clearance",
        str(clearance_path),
    )
    assert runner.run(args, environ={"XAI_API_KEY": SYNTHETIC_API_KEY}) == args.output
    assert observed == {
        "api_key": SYNTHETIC_API_KEY,
        "case_set": "holdout",
        "selected": 42,
        "planned": 84,
        "audit": hashlib.sha256(
            (holdout_validation / "holdout_context_audit.json").read_bytes()
        ).hexdigest(),
        "clearance": "ready",
    }


def test_holdout_execute_manifest_binds_case_set_ids_audit_and_clearance(
    holdout_pack_data: dict[str, Any],
    holdout_validation: Path,
    tmp_path: Path,
) -> None:
    clearance_path = all_ready_clearance(
        holdout_validation / "holdout_context_clearance.csv",
        tmp_path / "manifest-all-ready.csv",
    )
    context_artifacts = runner.build_holdout_context_artifacts(holdout_pack_data)
    clearance = runner.validate_context_clearance(
        clearance_path, context_artifacts, require_all_ready=True
    )
    plan = runner.build_execution_plan(
        holdout_pack_data, runner.profile_manifests(), runner.DEFAULT_BLIND_SEED
    )
    output = tmp_path / "execute-manifest-inputs"
    output.mkdir()
    (output / "provider_phase_identity.json").write_text("{}\n", encoding="utf-8")
    (output / "provider_model_metadata.json").write_text("{}\n", encoding="utf-8")
    results = {
        (row["candidate_id"], row["variant"]): {
            "status": "approved",
            "model_call_count": 1,
        }
        for row in plan["executions"]
    }
    manifest = runner.execute_manifest(
        paid_cli_args(
            tmp_path / "unused-execute-output",
            "--case-set",
            "holdout",
            "--context-clearance",
            str(clearance_path),
        ),
        holdout_pack_data,
        {},
        plan=plan,
        context_artifacts=context_artifacts,
        clearance=clearance,
        output=output,
        identity_sha256="i" * 64,
        ledger_data={"operations": []},
        result_rows=results,
    )
    assert manifest["case_set"] == "holdout"
    assert manifest["selected_candidate_ids_sha256"] == holdout_pack_data[
        "holdout_candidate_ids_sha256"
    ]
    assert manifest["context_audit_sha256"] == context_artifacts[
        "context_audit_sha256"
    ]
    assert manifest["context_clearance_status"] == "ready"
    assert manifest["paid_execution_ready"] is True
    assert manifest["selected_case_count"] == 42
    assert manifest["completed_pipeline_executions"] == 84


def test_clearance_cannot_be_supplied_for_calibration_mode(tmp_path: Path) -> None:
    args = cli_args(
        tmp_path / "calibration-with-clearance",
        "--context-clearance",
        str(tmp_path / "clearance.csv"),
    )
    with pytest.raises(runner.CalibrationError, match="only with --case-set holdout"):
        runner.validate_arguments(args, {})


def test_one_changed_context_invalidates_old_clearance(
    holdout_validation: Path, tmp_path: Path
) -> None:
    original = runner.verify_replay_pack(PACK, case_set="holdout")
    original_artifacts = runner.build_holdout_context_artifacts(original)
    clearance_path = all_ready_clearance(
        holdout_validation / "holdout_context_clearance.csv",
        tmp_path / "old-clearance.csv",
    )
    altered = tmp_path / "changed-context-pack"
    shutil.copytree(PACK, altered)
    candidate_id = next(
        case["candidate_id"]
        for case in original["cases"]
        if case["stratum"] == "factual_or_historical_question"
    )

    def change_context(rows: list[dict[str, Any]]) -> None:
        row = next(row for row in rows if row["candidate_id"] == candidate_id)
        row["validated_context"]["current_date"] = "2026-08-11"

    rewrite_pack_json(altered, "model_inputs.jsonl", change_context)
    rewrite_pack_json(altered, "frozen_cases.jsonl", change_context)
    changed = runner.verify_replay_pack(altered, case_set="holdout")
    changed_artifacts = runner.build_holdout_context_artifacts(changed)
    assert changed_artifacts["context_audit_sha256"] != original_artifacts[
        "context_audit_sha256"
    ]
    with pytest.raises(runner.CalibrationError, match="audit SHA-256 is stale"):
        runner.validate_context_clearance(
            clearance_path, changed_artifacts, require_all_ready=True
        )


def test_accounting_only_context_provenance_difference_is_compatible(
    holdout_pack_data: dict[str, Any],
) -> None:
    artifacts = runner.build_holdout_context_artifacts(holdout_pack_data)
    current = artifacts["audit_document"]
    stored = json.loads(json.dumps(current))
    stored["runner_source_sha256"] = "0" * 64

    compatible = runner.verify_accounting_recovery_context_compatibility(
        stored,
        current,
        stored_recovery_bytes=artifacts["recovery_bytes"],
        current_recovery_bytes=artifacts["recovery_bytes"],
    )

    assert compatible["validated_context_count"] == 42
    assert compatible["manual_context_review_count"] == current[
        "manual_context_review_count"
    ]
    assert len(compatible["manual_review_candidate_ids"]) == current[
        "manual_context_review_count"
    ]


def test_substantive_context_difference_is_not_accounting_compatible(
    holdout_pack_data: dict[str, Any],
) -> None:
    artifacts = runner.build_holdout_context_artifacts(holdout_pack_data)
    current = json.loads(json.dumps(artifacts["audit_document"]))
    stored = json.loads(json.dumps(current))
    stored["runner_source_sha256"] = "0" * 64
    current["cases"][0]["validated_context_sha256"] = "1" * 64

    with pytest.raises(runner.CalibrationError, match="substantive difference"):
        runner.verify_accounting_recovery_context_compatibility(
            stored,
            current,
            stored_recovery_bytes=artifacts["recovery_bytes"],
            current_recovery_bytes=artifacts["recovery_bytes"],
        )


def test_accounting_context_compatibility_requires_exact_recovery_bytes(
    holdout_pack_data: dict[str, Any],
) -> None:
    artifacts = runner.build_holdout_context_artifacts(holdout_pack_data)
    current = artifacts["audit_document"]
    stored = json.loads(json.dumps(current))
    stored["runner_source_sha256"] = "0" * 64

    with pytest.raises(runner.CalibrationError, match="context-recovery record differs"):
        runner.verify_accounting_recovery_context_compatibility(
            stored,
            current,
            stored_recovery_bytes=artifacts["recovery_bytes"],
            current_recovery_bytes=artifacts["recovery_bytes"] + b"\n",
        )


def test_frozen_profile_and_prompt_hashes_remain_exact() -> None:
    manifests = runner.profile_manifests()
    runner.verify_frozen_profile_manifests(manifests)
    expected = {
        "current": {
            "profile_version": "current-production-profile-v1",
            "reviewer_version": "independent-reply-reviewer-v13",
            "manifest_sha256": "edd2985d37c690c02556c518dd6e92ad39db8e379267a61b90ddb9d4650368f8",
            "prompts": {
                "proposer": "06b00d02ce6c0182b9ec2e9ca52a22e9ca03f9b40ef45a3dc9f198ca30351f72",
                "reviewer": "778e9d6c325bdfb3d5f9b0a83814dd0f16acc355bd43d8c6fb817b7fb96d349e",
                "no_reply_review": "db578711a2f5ea36d7e4bc78e4997188e410407f57545680fe5498a4ee0e5b1d",
                "claim_auditor": "53aa8015b1ea90719d05578c2b2ba20fc9ddc939d23e5287255c44ded24f6e03",
            },
        },
        "compact": {
            "profile_version": "compact-reply-profile-v4",
            "reviewer_version": "compact-reviewer-v4",
            "manifest_sha256": "be7784eb3ffb0e851fda598ca71326bdd6cf95cc001803f4b7ca1d26e822b30e",
            "prompts": {
                "proposer": "922aff370f775ff9c18e2e7a445600519f14bfba8610ee6db99f9daf99e0f8da",
                "reviewer": "36c0577d9b0ee8c536ea638591c9ce1a0e052a9e482f80aedc16b489b3cad089",
                "no_reply_review": "2b6677ed5766676acde2c9010ba04feda57b077e02daaabb103a319921643b67",
                "claim_auditor": "5a0ccdd28239b6eb5808870cfa6c6fe2cee0c32342f9243a9e1c8ec057ac05fe",
            },
        },
    }
    for variant, identity in expected.items():
        assert manifests[variant]["profile_version"] == identity["profile_version"]
        assert manifests[variant]["prompt_version_constants"][
            "REVIEWER_PROMPT_VERSION"
        ] == identity["reviewer_version"]
        assert manifests[variant]["manifest_sha256"] == identity["manifest_sha256"]
        assert {
            name: row["sha256"]
            for name, row in manifests[variant]["prompts"].items()
        } == identity["prompts"]


def test_holdout_validate_output_is_private_and_checksummed(
    holdout_validation: Path,
) -> None:
    assert stat.S_IMODE(holdout_validation.stat().st_mode) == 0o700
    for path in holdout_validation.rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (
            0o700 if path.is_dir() else 0o600
        )
    runner.verify_output_sha256sums(holdout_validation)
    checksum_names = {
        line[66:]
        for line in (holdout_validation / "SHA256SUMS").read_text(
            encoding="utf-8"
        ).splitlines()
    }
    assert runner.HOLDOUT_CONTEXT_FILES <= checksum_names


def test_excluded_candidate_remains_in_holdout_execution_plan(
    excluded_holdout_validation: Path,
) -> None:
    plan = json.loads(
        (excluded_holdout_validation / "execution_plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan["planned_pipeline_executions"] == 84
    assert sum(
        row["candidate_id"] == SYNTHETIC_EXCLUDED_CANDIDATE_ID
        for row in plan["executions"]
    ) == 2
    assert plan["selected_candidate_ids_sha256"] == runner.candidate_ids_sha256(
        case["candidate_id"] for case in fresh_holdout_pack_data()["cases"]
    )
    assert plan["excluded_from_blind_quality_candidate_ids"] == [
        SYNTHETIC_EXCLUDED_CANDIDATE_ID
    ]
    assert plan["pre_exposed_candidate_count"] == 1
    assert plan["initial_blind_quality_candidate_count"] == 41


def test_excluded_candidate_is_omitted_from_blind_quality_review(
    excluded_holdout_validation: Path,
) -> None:
    markdown = (excluded_holdout_validation / "blind_review.md").read_text(
        encoding="utf-8"
    )
    with (excluded_holdout_validation / "blind_review.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    assert SYNTHETIC_EXCLUDED_CANDIDATE_ID not in markdown
    assert all(
        row["candidate_id"] != SYNTHETIC_EXCLUDED_CANDIDATE_ID for row in rows
    )
    assert len(rows) == 41 * 3
    assert "response_status" in (reader.fieldnames or [])
    assert {row["response_status"] for row in rows} == {""}
    assert (
        "Primary quality set excludes pre-exposed candidates and candidates with "
        "operational pipeline failures. Reliability outcomes are reported separately."
        in markdown
    )


def test_validate_only_reliability_outputs_are_aggregate_and_checksummed(
    excluded_holdout_validation: Path,
) -> None:
    summary_path = excluded_holdout_validation / "holdout_reliability_summary.json"
    detail_path = excluded_holdout_validation / "holdout_reliability_failures.jsonl"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert set(summary) == runner.RELIABILITY_SUMMARY_FIELDS
    assert summary == {
        "selected_holdout_candidates": 42,
        "planned_pipeline_executions": 84,
        "completed_pipeline_executions": 0,
        "approved_outcomes": 0,
        "no_reply_outcomes": 0,
        "operational_failure_outcomes": 0,
        "candidates_with_operational_failure": 0,
        "pre_exposed_candidate_count": 1,
        "blind_quality_candidate_count": 41,
    }
    assert detail_path.read_bytes() == b""
    manifest = json.loads(
        (excluded_holdout_validation / "run_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["continue_on_operational_failure"] is False
    assert manifest["pre_exposed_candidate_count"] == 1
    assert manifest["operational_failure_count"] == 0
    assert manifest["operational_failure_candidate_count"] == 0
    assert manifest["blind_quality_candidate_count"] == 41
    assert manifest["reliability_summary_sha256"] == hashlib.sha256(
        summary_path.read_bytes()
    ).hexdigest()
    checksum_names = {
        line[66:]
        for line in (excluded_holdout_validation / "SHA256SUMS").read_text(
            encoding="utf-8"
        ).splitlines()
    }
    assert runner.HOLDOUT_RELIABILITY_FILES <= checksum_names
    runner.verify_output_sha256sums(excluded_holdout_validation)


def test_excluded_holdout_validate_only_performs_zero_calls(
    excluded_holdout_validation: Path,
) -> None:
    report = json.loads(
        (excluded_holdout_validation / "validation_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["model_calls_performed"] == 0
    assert report["http_requests_performed"] == 0
    assert report["blind_quality_candidate_count"] == 41


def test_validate_only_performs_zero_http_requests(validation_pair: tuple[Path, Path]) -> None:
    report = json.loads((validation_pair[0] / "validation_report.json").read_text(encoding="utf-8"))
    assert report["http_requests_performed"] == 0
    assert report["model_calls_performed"] == 0


def test_validate_only_requires_no_xai_api_key(validation_pair: tuple[Path, Path]) -> None:
    manifest = json.loads((validation_pair[0] / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "validate-only"
    assert not (validation_pair[0] / "provider_model_metadata.json").exists()
    assert not (validation_pair[0] / "cost_ledger.json").exists()


def test_validate_only_contains_source_and_evidence_provenance(
    validation_pair: tuple[Path, Path],
) -> None:
    manifest = json.loads((validation_pair[0] / "run_manifest.json").read_text(encoding="utf-8"))
    for name in (
        "runner_git_commit",
        "worktree_clean",
        "runner_source_sha256",
        "prompt_profiles_source_sha256",
        "pilot_transport_source_sha256",
        "reply_strategy_sha256",
        "reply_evidence_sha256",
        "evidence_repository_fingerprint",
        "execution_plan_sha256",
        "current_profile_manifest_sha256",
        "compact_profile_manifest_sha256",
    ):
        assert name in manifest
    report = json.loads(
        (validation_pair[0] / "validation_report.json").read_text(encoding="utf-8")
    )
    assert report["compact_no_reply_recent_reply_payload_contract_pass"] is True


def test_execute_requires_explicit_acknowledgement(tmp_path: Path) -> None:
    args = cli_args(
        tmp_path / "out",
        "--execute",
        "--hard-limit-usd", "1",
        "--expected-runner-git-commit", SYNTHETIC_RUNNER_COMMIT,
    )
    with pytest.raises(runner.CalibrationError, match="acknowledge-paid-model-calls"):
        runner.validate_arguments(args, {"XAI_API_KEY": SYNTHETIC_API_KEY})


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_execute_requires_positive_finite_hard_limit(tmp_path: Path, value: str) -> None:
    args = cli_args(
        tmp_path / "out",
        "--execute",
        "--hard-limit-usd", value,
        "--expected-runner-git-commit", SYNTHETIC_RUNNER_COMMIT,
        "--acknowledge-paid-model-calls", runner.PAID_ACKNOWLEDGEMENT,
    )
    with pytest.raises(runner.CalibrationError, match="positive --hard-limit-usd"):
        runner.validate_arguments(args, {"XAI_API_KEY": SYNTHETIC_API_KEY})


def test_execute_requires_xai_api_key(tmp_path: Path) -> None:
    args = cli_args(
        tmp_path / "out",
        "--execute",
        "--hard-limit-usd", "1",
        "--expected-runner-git-commit", SYNTHETIC_RUNNER_COMMIT,
        "--acknowledge-paid-model-calls", runner.PAID_ACKNOWLEDGEMENT,
    )
    with pytest.raises(runner.CalibrationError, match="requires XAI_API_KEY"):
        runner.validate_arguments(args, {})


def test_resume_is_refused_in_validate_only_mode(tmp_path: Path) -> None:
    with pytest.raises(runner.CalibrationError, match="valid only with --execute"):
        runner.validate_arguments(cli_args(tmp_path / "out", "--resume"), {})


def test_accounting_recovery_option_is_refused_in_validate_only(
    tmp_path: Path,
) -> None:
    args = cli_args(
        tmp_path / "out",
        "--case-set", "holdout",
        "--resume",
        "--continue-on-operational-failure",
        "--resume-audit-accounting-recovery-from-commit",
        runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT,
    )
    with pytest.raises(runner.CalibrationError, match="valid only with --execute"):
        runner.validate_arguments(args, {})


def test_accounting_recovery_option_requires_resume(tmp_path: Path) -> None:
    args = paid_cli_args(
        tmp_path / "out",
        "--case-set", "holdout",
        "--context-clearance", str(tmp_path / "clearance.csv"),
        "--continue-on-operational-failure",
        "--resume-audit-accounting-recovery-from-commit",
        runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT,
    )
    with pytest.raises(runner.CalibrationError, match="requires --resume"):
        runner.validate_arguments(args, {"XAI_API_KEY": SYNTHETIC_API_KEY})


def test_accounting_recovery_option_requires_holdout(tmp_path: Path) -> None:
    args = paid_cli_args(
        tmp_path / "out",
        "--resume",
        "--continue-on-operational-failure",
        "--resume-audit-accounting-recovery-from-commit",
        runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT,
    )
    with pytest.raises(runner.CalibrationError, match="requires --case-set holdout"):
        runner.validate_arguments(args, {"XAI_API_KEY": SYNTHETIC_API_KEY})


def test_accounting_recovery_option_requires_failure_continuation(
    tmp_path: Path,
) -> None:
    args = paid_cli_args(
        tmp_path / "out",
        "--case-set", "holdout",
        "--context-clearance", str(tmp_path / "clearance.csv"),
        "--resume",
        "--resume-audit-accounting-recovery-from-commit",
        runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT,
    )
    with pytest.raises(
        runner.CalibrationError, match="requires --continue-on-operational-failure"
    ):
        runner.validate_arguments(args, {"XAI_API_KEY": SYNTHETIC_API_KEY})


def test_accounting_recovery_option_rejects_wrong_predecessor(
    tmp_path: Path,
) -> None:
    args = paid_cli_args(
        tmp_path / "out",
        "--case-set", "holdout",
        "--context-clearance", str(tmp_path / "clearance.csv"),
        "--resume",
        "--continue-on-operational-failure",
        "--resume-audit-accounting-recovery-from-commit", "f" * 40,
    )
    with pytest.raises(runner.CalibrationError, match="exact approved predecessor"):
        runner.validate_arguments(args, {"XAI_API_KEY": SYNTHETIC_API_KEY})


def test_exact_accounting_recovery_gate_is_accepted(tmp_path: Path) -> None:
    args = paid_cli_args(
        tmp_path / "out",
        "--case-set", "holdout",
        "--context-clearance", str(tmp_path / "clearance.csv"),
        "--resume",
        "--continue-on-operational-failure",
        "--resume-audit-accounting-recovery-from-commit",
        runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT,
    )
    assert runner.validate_arguments(
        args, {"XAI_API_KEY": SYNTHETIC_API_KEY}
    ) == SYNTHETIC_API_KEY


def test_execute_requires_expected_runner_git_commit(tmp_path: Path) -> None:
    args = cli_args(
        tmp_path / "out",
        "--execute",
        "--hard-limit-usd", "1",
        "--acknowledge-paid-model-calls", runner.PAID_ACKNOWLEDGEMENT,
    )
    with pytest.raises(runner.CalibrationError, match="expected-runner-git-commit"):
        runner.validate_arguments(args, {"XAI_API_KEY": SYNTHETIC_API_KEY})


def test_wrong_expected_runner_commit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "wrong-commit"
    counters = install_synthetic_execute(monkeypatch, output)
    args = paid_cli_args(output)
    args.expected_runner_git_commit = "b" * 40
    with pytest.raises(runner.CalibrationError, match="runner Git commit mismatch"):
        runner.run(args, environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters["metadata"] == counters["post"] == 0


def test_dirty_tracked_worktree_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_git(*arguments: str, text: bool = True) -> str | bytes:
        if arguments[:2] == ("rev-parse", "HEAD"):
            return SYNTHETIC_RUNNER_COMMIT
        if arguments and arguments[0] == "status":
            return " M tools/run_reply_prompt_calibration.py\n"
        raise AssertionError(arguments)

    monkeypatch.setattr(runner, "_git", fake_git)
    with pytest.raises(runner.CalibrationError, match="clean tracked worktree and index"):
        runner.execution_provenance(
            SYNTHETIC_RUNNER_COMMIT, require_clean_checkout=True
        )


def install_synthetic_accounting_recovery_git(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: str = "",
    merge_base: str | None = None,
    changed_paths: tuple[str, ...] = (
        "tools/run_reply_prompt_calibration.py",
        "tests/test_reply_prompt_calibration.py",
    ),
    predecessor_source_overrides: dict[str, bytes] | None = None,
    current_source_overrides: dict[str, bytes] | None = None,
) -> str:
    predecessor = runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT
    current = "b" * 40
    live_sources = {
        path.relative_to(runner.PROJECT_ROOT).as_posix(): path.read_bytes()
        for path in runner.SOURCE_PATHS.values()
    }
    predecessor_sources = dict(live_sources)
    predecessor_sources["tools/run_reply_prompt_calibration.py"] = (
        b"synthetic predecessor runner source\n"
    )
    predecessor_sources.update(predecessor_source_overrides or {})
    current_sources = dict(live_sources)
    current_sources.update(current_source_overrides or {})
    changed = b"".join(path.encode("utf-8") + b"\0" for path in changed_paths)

    def fake_git(*arguments: str, text: bool = True) -> str | bytes:
        if arguments == ("rev-parse", "HEAD"):
            return current
        if arguments == ("status", "--porcelain=v1", "--untracked-files=all"):
            return status
        if arguments == ("merge-base", predecessor, current):
            return (merge_base or predecessor) + "\n"
        if arguments == (
            "diff", "--name-only", "-z", predecessor, current, "--"
        ):
            assert text is False
            return changed
        if arguments[0] == "show" and len(arguments) == 2:
            assert text is False
            commit, relative = arguments[1].split(":", 1)
            if commit == predecessor:
                return predecessor_sources[relative]
            if commit == current:
                return current_sources[relative]
        raise AssertionError(arguments)

    monkeypatch.setattr(runner, "_git", fake_git)
    return current


def test_accounting_recovery_checkout_accepts_exact_accounting_only_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = install_synthetic_accounting_recovery_git(monkeypatch)

    compatible = runner.verify_accounting_recovery_checkout(
        runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT
    )

    assert compatible["recovery_runner_commit"] == current
    assert set(compatible["changed_paths"]) == (
        runner.ACCOUNTING_RECOVERY_ALLOWED_CHANGED_PATHS
    )
    assert compatible["predecessor_runner_source_sha256"] != compatible[
        "recovery_runner_source_sha256"
    ]


def test_accounting_recovery_checkout_rejects_wrong_predecessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_accounting_recovery_git(monkeypatch)
    with pytest.raises(runner.CalibrationError, match="predecessor must be exactly"):
        runner.verify_accounting_recovery_checkout("f" * 40)


def test_accounting_recovery_checkout_rejects_untracked_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_accounting_recovery_git(
        monkeypatch, status="?? untracked-recovery-input\n"
    )
    with pytest.raises(runner.CalibrationError, match="including untracked files"):
        runner.verify_accounting_recovery_checkout(
            runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT
        )


def test_accounting_recovery_checkout_rejects_non_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_accounting_recovery_git(monkeypatch, merge_base="c" * 40)
    with pytest.raises(runner.CalibrationError, match="not a descendant"):
        runner.verify_accounting_recovery_checkout(
            runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT
        )


def test_accounting_recovery_checkout_rejects_forbidden_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_accounting_recovery_git(
        monkeypatch,
        changed_paths=("tools/run_reply_prompt_calibration.py", "reply_strategy.py"),
    )
    with pytest.raises(runner.CalibrationError, match="forbidden tracked paths"):
        runner.verify_accounting_recovery_checkout(
            runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT
        )


@pytest.mark.parametrize(
    "relative_path",
    [
        "reply_strategy.py",
        "reply_evidence.py",
        "tools/pilot_ai_first_reply_strategy.py",
        "tools/reply_prompt_profiles.py",
    ],
)
def test_accounting_recovery_checkout_rejects_protected_source_drift(
    relative_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_accounting_recovery_git(
        monkeypatch,
        predecessor_source_overrides={relative_path: b"changed predecessor source\n"},
    )
    with pytest.raises(runner.CalibrationError, match="protected execute source"):
        runner.verify_accounting_recovery_checkout(
            runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT
        )


def test_accounting_recovery_checkout_rejects_uncommitted_source_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_accounting_recovery_git(
        monkeypatch,
        current_source_overrides={"reply_strategy.py": b"different committed source\n"},
    )
    with pytest.raises(runner.CalibrationError, match="not its committed blob"):
        runner.verify_accounting_recovery_checkout(
            runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT
        )


def test_non_empty_execute_output_is_refused_without_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "non-empty"
    output.mkdir(mode=0o700)
    marker = output / "marker"
    marker.write_text("occupied", encoding="utf-8")
    marker.chmod(0o600)
    counters = install_synthetic_execute(monkeypatch, output)
    with pytest.raises(runner.CalibrationError, match="non-empty.*--resume"):
        runner.run(
            paid_cli_args(output), environ={"XAI_API_KEY": SYNTHETIC_API_KEY}
        )
    assert counters["metadata"] == counters["post"] == 0


def test_endpoint_must_be_exact_xai_https_api(tmp_path: Path) -> None:
    valid = cli_args(tmp_path / "valid", "--xai-base", "https://api.x.ai/v1/")
    runner.validate_arguments(valid, {})
    assert valid.xai_base == runner.DEFAULT_XAI_BASE
    for value in ("http://api.x.ai/v1", "https://api.x.com/v1", "https://api.x.ai/v1/chat"):
        args = cli_args(tmp_path / "invalid", "--xai-base", value)
        with pytest.raises(runner.CalibrationError, match="exactly https://api.x.ai/v1"):
            runner.validate_arguments(args, {})


def test_no_x_or_posting_module_is_imported() -> None:
    assert "mrsMThatcher2" not in sys.modules
    assert "tweepy" not in sys.modules
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "import mrsMThatcher2" not in source
    assert "import tweepy" not in source


def test_current_and_compact_receive_identical_context_and_recent_replies(
    validation_pair: tuple[Path, Path],
) -> None:
    plan = json.loads((validation_pair[0] / "execution_plan.json").read_text(encoding="utf-8"))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in plan["executions"]:
        grouped.setdefault(row["candidate_id"], []).append(row)
    for rows in grouped.values():
        assert len(rows) == 2
        assert rows[0]["validated_context_sha256"] == rows[1]["validated_context_sha256"]
        assert rows[0]["recent_replies_sha256"] == rows[1]["recent_replies_sha256"]
    previews = [
        json.loads(line)
        for line in (validation_pair[0] / "prompt_preview_receipts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    preview_groups: dict[str, list[dict[str, Any]]] = {}
    for row in previews:
        preview_groups.setdefault(row["candidate_id"], []).append(row)
    assert all(rows[0]["proposer_user_prompt_sha256"] == rows[1]["proposer_user_prompt_sha256"] for rows in preview_groups.values())


def test_execution_order_is_deterministic_and_not_always_current_first(
    pack_data: dict[str, Any],
) -> None:
    first = runner.execution_order(
        pack_data["cases"], blind_seed="seed", pack_sha256=pack_data["pack_sha256"]
    )
    second = runner.execution_order(
        list(reversed(pack_data["cases"])), blind_seed="seed", pack_sha256=pack_data["pack_sha256"]
    )
    assert first == second
    first_variant: dict[str, str] = {}
    for row in first:
        first_variant.setdefault(row["candidate_id"], row["variant"])
    assert "compact" in first_variant.values()


def test_three_way_blind_assignment_is_deterministic(pack_data: dict[str, Any]) -> None:
    first = runner.blind_assignments(
        pack_data["cases"], blind_seed="blind", pack_sha256=pack_data["pack_sha256"]
    )
    second = runner.blind_assignments(
        list(reversed(pack_data["cases"])), blind_seed="blind", pack_sha256=pack_data["pack_sha256"]
    )
    assert first == second
    assert all(set(mapping.values()) == {"historical", "current", "compact"} for mapping in first.values())


def synthetic_case() -> dict[str, Any]:
    return {
        "candidate_id": "candidate-blind-test",
        "stratum": "safe_wit_opportunity",
        "context": {
            "target_id": "target",
            "thread_id": "thread",
            "lane": "mention",
            "incoming_contribution": "A neutral calibration contribution.",
            "quoted_post": None,
            "parent_thread": [],
            "clarification_request": None,
            "current_date": "2026-08-10",
        },
        "recent_replies": ["Recent neutral reply."],
        "historical": {"historical_reply": "Historical marker output."},
    }


def test_blind_review_contains_no_variant_labels() -> None:
    case = synthetic_case()
    assignments = {case["candidate_id"]: {
        "Response A": "historical", "Response B": "current", "Response C": "compact"
    }}
    outputs = {
        (case["candidate_id"], "historical"): {
            "status": "approved", "public_reply": "Alpha response."
        },
        (case["candidate_id"], "current"): {
            "status": "approved", "public_reply": "Beta response."
        },
        (case["candidate_id"], "compact"): {
            "status": "no_reply", "public_reply": None
        },
    }
    markdown, csv_text = runner.build_blind_review([case], assignments, outputs)
    assert "Response A" in markdown and "Response B" in markdown and "Response C" in markdown
    assert "NO REPLY" in markdown
    assert "historical" not in markdown.casefold()
    assert "current" not in markdown.casefold()
    assert "compact" not in markdown.casefold()
    assert "historical" not in csv_text.casefold()


def test_operational_failure_is_never_rendered_as_no_reply() -> None:
    rendered = runner.render_outcome({
        "status": "operational_failure", "public_reply": None
    })
    assert rendered == "PIPELINE FAILURE"
    assert rendered != "NO REPLY"


def test_approved_reply_is_rendered_as_its_text() -> None:
    assert runner.render_outcome({
        "status": "approved", "public_reply": "Approved marker."
    }) == "Approved marker."


def test_deliberate_no_reply_alone_renders_as_no_reply() -> None:
    assert runner.render_outcome({
        "status": "no_reply", "public_reply": None
    }) == "NO REPLY"
    with pytest.raises(runner.CalibrationError, match="invalid calibration outcome"):
        runner.render_outcome({"status": "no_reply", "public_reply": "not silence"})


def test_completed_audit_row_represents_model_attempt() -> None:
    assert runner.audit_row_represents_model_attempt({
        "stage": "proposer",
        "status": "completed",
    })


def test_invalid_response_retry_audit_row_represents_model_attempt() -> None:
    assert runner.audit_row_represents_model_attempt({
        "stage": "proposer",
        "status": "invalid_response_retry",
        "reason": "received response failed validation",
    })


def test_received_non_retryable_invalid_reviewer_is_model_attempt() -> None:
    assert runner.audit_row_represents_model_attempt({
        "stage": "reviewer",
        "status": "invalid",
        "reason": "received reviewer response is non-retryable",
        "retry_suppressed": True,
    })


def test_received_ordinary_invalid_row_represents_model_attempt() -> None:
    assert runner.audit_row_represents_model_attempt({
        "stage": "reviewer",
        "status": "invalid",
        "reason": "received response failed validation",
    })


def test_successful_insufficient_evidence_row_represents_model_attempt() -> None:
    assert runner.audit_row_represents_model_attempt({
        "stage": "evidence",
        "status": "insufficient",
        "supported": False,
    })


def test_claim_without_candidate_passage_remains_non_call_event() -> None:
    assert not runner.audit_row_represents_model_attempt({
        "stage": "evidence",
        "status": "insufficient",
        "reason": "claim_without_candidate_passage",
    })


def test_model_call_ceiling_invalid_row_is_not_model_attempt() -> None:
    assert not runner.audit_row_represents_model_attempt({
        "stage": "revision_reviewer",
        "status": "invalid",
        "reason": "reply pipeline model-call ceiling reached",
    })


@pytest.mark.parametrize("stage", sorted(runner.MODEL_AUDIT_STAGES))
def test_model_call_ceiling_exclusion_applies_to_every_model_stage(stage: str) -> None:
    assert not runner.audit_row_represents_model_attempt({
        "stage": stage,
        "status": "invalid",
        "reason": "reply pipeline model-call ceiling reached",
    })


def test_other_invalid_reason_is_not_accidentally_excluded() -> None:
    assert runner.audit_row_represents_model_attempt({
        "stage": "revision_reviewer",
        "status": "invalid",
        "reason": "received response failed validation",
        "error": "validation error",
    })


def test_exact_ceiling_diagnostic_derives_same_six_audit_and_receipt_stages() -> None:
    stages = [
        "proposer",
        "evidence",
        "evidence",
        "reviewer",
        "revision_proposer",
        "revision_evidence",
    ]
    terminal_ceiling_event = {
        "stage": "revision_reviewer",
        "status": "invalid",
        "reason": "reply pipeline model-call ceiling reached",
    }
    audit = [
        {"stage": "proposer", "status": "completed"},
        {"stage": "evidence", "status": "invalid_response_retry"},
        {"stage": "evidence", "status": "insufficient", "supported": False},
        {"stage": "reviewer", "status": "completed"},
        {"stage": "revision_proposer", "status": "completed"},
        {"stage": "revision_evidence", "status": "insufficient", "supported": False},
        terminal_ceiling_event,
    ]

    assert runner.audit_model_stage_sequence(audit) == stages
    identity, receipts, ledger = synthetic_call_contract(stages, audit)
    inventory = runner.collect_call_inventory(
        identity,
        audit=audit,
        receipts=receipts,
        ledger_data=ledger,
    )

    assert inventory["model_call_count"] == 6
    assert inventory["model_stage_sequence"] == stages
    assert [row["stage"] for row in inventory["call_inventory"]] == stages
    assert inventory["pipeline_audit_sha256"] == runner.value_sha256(audit)
    assert audit[-1] == terminal_ceiling_event


@pytest.mark.parametrize(
    ("stages", "audit"),
    [
        (
            ["proposer", "reviewer"],
            [
                {"stage": "quotation_resolution", "status": "not_resolved"},
                {"stage": "proposer", "status": "completed"},
                {"stage": "reviewer", "status": "completed"},
            ],
        ),
        (
            ["proposer", "no_reply_reviewer"],
            [
                {"stage": "proposer", "status": "completed"},
                {"stage": "no_reply_reviewer", "status": "completed"},
            ],
        ),
        (
            ["proposer", "proposer", "reviewer"],
            [
                {"stage": "proposer", "status": "invalid_response_retry", "attempt": 1},
                {"stage": "proposer", "status": "completed"},
                {"stage": "reviewer", "status": "completed"},
            ],
        ),
        (
            ["proposer", "reviewer", "revision_proposer", "revision_reviewer"],
            [
                {"stage": "proposer", "status": "completed"},
                {"stage": "reviewer", "status": "completed", "verdict": "revise"},
                {"stage": "revision_proposer", "status": "completed"},
                {"stage": "revision_reviewer", "status": "completed"},
            ],
        ),
        (
            ["proposer", "evidence", "reviewer"],
            [
                {"stage": "proposer", "status": "completed"},
                {"stage": "evidence", "status": "completed"},
                {"stage": "reviewer", "status": "completed"},
            ],
        ),
    ],
)
def test_realistic_pipeline_audit_model_stage_sequences_pass(
    stages: list[str], audit: list[dict[str, Any]]
) -> None:
    identity, receipts, ledger = synthetic_call_contract(stages, audit)
    inventory = runner.collect_call_inventory(
        identity, audit=audit, receipts=receipts, ledger_data=ledger
    )
    assert inventory["model_call_count"] == len(stages)
    assert inventory["model_stage_sequence"] == stages
    assert len(inventory["logical_call_ids"]) == len(stages)


def test_audit_and_receipt_stage_order_mismatch_is_refused() -> None:
    audit = [
        {"stage": "proposer", "status": "completed"},
        {"stage": "reviewer", "status": "completed"},
    ]
    identity, receipts, ledger = synthetic_call_contract(
        ["proposer", "no_reply_reviewer"], audit
    )
    with pytest.raises(runner.CalibrationError, match="audit and receipt stages differ"):
        runner.collect_call_inventory(
            identity, audit=audit, receipts=receipts, ledger_data=ledger
        )


def test_missing_proposer_call_is_refused() -> None:
    audit = [{"stage": "reviewer", "status": "completed"}]
    identity, receipts, ledger = synthetic_call_contract(["reviewer"], audit)
    with pytest.raises(runner.CalibrationError, match="first model stage is not proposer"):
        runner.collect_call_inventory(
            identity, audit=audit, receipts=receipts, ledger_data=ledger
        )


def test_duplicate_or_non_contiguous_logical_call_sequence_is_refused() -> None:
    audit = [
        {"stage": "proposer", "status": "completed"},
        {"stage": "reviewer", "status": "completed"},
    ]
    identity, receipts, ledger = synthetic_call_contract(["proposer", "reviewer"], audit)
    second_id, second = list(receipts.items())[1]
    second["call_sequence"] = 3
    with pytest.raises(runner.CalibrationError, match="not contiguous"):
        runner.collect_call_inventory(
            identity, audit=audit, receipts=receipts, ledger_data=ledger
        )
    assert second_id in receipts


def test_blind_key_reconstructs_mapping(validation_pair: tuple[Path, Path]) -> None:
    key = json.loads((validation_pair[0] / "blind_key.json").read_text(encoding="utf-8"))
    for mapping in key["assignments"].values():
        assert list(mapping) == ["Response A", "Response B", "Response C"]
        assert set(mapping.values()) == {"historical", "current", "compact"}


def test_historical_baseline_is_absent_from_provider_prompts(
    validation_pair: tuple[Path, Path], pack_data: dict[str, Any]
) -> None:
    marker = "historical_deterministic_rejection_reason"
    previews = (validation_pair[0] / "prompt_preview_receipts.jsonl").read_text(encoding="utf-8")
    assert marker not in previews
    assert all(marker not in json.dumps(case["context"], sort_keys=True) for case in pack_data["cases"])


def test_cost_ceiling_stops_before_excess_request(tmp_path: Path) -> None:
    calls = 0

    def post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        nonlocal calls
        calls += 1
        return successful_response()

    ledger = pilot.PilotLedger(tmp_path / "ledger.json", model="grok-4.3", hard_limit_usd=1e-12)
    transport = pilot.PilotTransport(
        api_key=SYNTHETIC_API_KEY,
        base_url=runner.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "raw",
        post=post,
    )
    transport.set_case("candidate:current:identity")
    with pytest.raises(pilot.CostLimitReached):
        transport(**transport_arguments())
    assert calls == 0


def test_completed_request_resumes_only_under_same_hash(tmp_path: Path) -> None:
    calls = 0

    def post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        nonlocal calls
        calls += 1
        return successful_response()

    ledger_path = tmp_path / "ledger.json"
    ledger = pilot.PilotLedger(ledger_path, model="grok-4.3", hard_limit_usd=1)
    transport = pilot.PilotTransport(
        api_key=SYNTHETIC_API_KEY,
        base_url=runner.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "raw",
        post=post,
    )
    transport.set_case("candidate:compact:identity")
    assert json.loads(transport(**transport_arguments())) == {"ok": True}
    transport.set_case("candidate:compact:identity")
    assert json.loads(transport(**transport_arguments())) == {"ok": True}
    assert calls == 1
    transport.set_case("candidate:compact:identity")
    with pytest.raises(pilot.PilotError, match="identity changed"):
        transport(**transport_arguments(user_prompt="changed"))


def test_ambiguous_request_blocks_resume(tmp_path: Path) -> None:
    def post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        raise pilot.requests.ConnectionError("ambiguous")

    ledger_path = tmp_path / "ledger.json"
    ledger = pilot.PilotLedger(ledger_path, model="grok-4.3", hard_limit_usd=1)
    transport = pilot.PilotTransport(
        api_key=SYNTHETIC_API_KEY,
        base_url=runner.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "raw",
        post=post,
    )
    transport.set_case("candidate:current:ambiguous")
    with pytest.raises(pilot.requests.ConnectionError):
        transport(**transport_arguments())
    saved = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert saved["ambiguous_exposure_usd"] > 0
    with pytest.raises(pilot.PilotError, match="blocked"):
        pilot.PilotLedger(ledger_path, model="grok-4.3", hard_limit_usd=1)


def test_definite_429_retry_is_bounded(tmp_path: Path) -> None:
    calls = 0

    def post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        nonlocal calls
        calls += 1
        return FakeResponse({"error": "limited"}, status_code=429, headers={"Retry-After": "0"})

    ledger = pilot.PilotLedger(tmp_path / "ledger.json", model="grok-4.3", hard_limit_usd=1)
    transport = pilot.PilotTransport(
        api_key=SYNTHETIC_API_KEY,
        base_url=runner.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "raw",
        post=post,
        sleep=lambda _seconds: None,
        maximum_rate_limit_retries=1,
    )
    transport.set_case("candidate:current:429")
    with pytest.raises(pilot.RateLimitReached):
        transport(**transport_arguments())
    assert calls == 2


def test_definite_5xx_retry_is_bounded(tmp_path: Path) -> None:
    calls = 0

    def post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        nonlocal calls
        calls += 1
        return FakeResponse({"error": "server"}, status_code=503, headers={"Retry-After": "0"})

    ledger = pilot.PilotLedger(tmp_path / "ledger.json", model="grok-4.3", hard_limit_usd=1)
    transport = pilot.PilotTransport(
        api_key=SYNTHETIC_API_KEY,
        base_url=runner.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=ledger,
        response_dir=tmp_path / "raw",
        post=post,
        sleep=lambda _seconds: None,
        maximum_server_error_retries=1,
    )
    transport.set_case("candidate:compact:503")
    with pytest.raises(pilot.ServerErrorReached):
        transport(**transport_arguments())
    assert calls == 2


def test_raw_responses_are_private(tmp_path: Path) -> None:
    ledger = pilot.PilotLedger(tmp_path / "ledger.json", model="grok-4.3", hard_limit_usd=1)
    response_dir = tmp_path / "raw"
    transport = pilot.PilotTransport(
        api_key=SYNTHETIC_API_KEY,
        base_url=runner.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=ledger,
        response_dir=response_dir,
        post=lambda *_args, **_kwargs: successful_response(),
    )
    transport.set_case("candidate:compact:private")
    transport(**transport_arguments())
    raw = next(response_dir.iterdir())
    assert stat.S_IMODE(raw.stat().st_mode) == 0o600


def test_run_identity_precedes_provider_and_binds_all_immutable_inputs(
    completed_execute_output: Path,
) -> None:
    identity = json.loads(
        (completed_execute_output / "run_identity.json").read_text(encoding="utf-8")
    )
    assert identity["schema_version"] == runner.RUN_IDENTITY_SCHEMA_VERSION
    assert identity["runner_version"] == runner.RUNNER_VERSION
    assert identity["replay_pack_sha256"]
    assert identity["execution_plan_sha256"]
    assert identity["current_profile_manifest_sha256"]
    assert identity["compact_profile_manifest_sha256"]
    assert identity["model"] == "grok-4.3"
    assert identity["xai_endpoint"] == runner.DEFAULT_XAI_BASE
    assert identity["blind_seed"] == runner.DEFAULT_BLIND_SEED
    assert identity["hard_limit_usd"] == 1
    assert identity["maximum_rate_limit_retries"] == 1
    assert identity["maximum_server_error_retries"] == 1
    assert identity["runner_git_commit_expected"] == SYNTHETIC_RUNNER_COMMIT
    assert identity["runner_git_commit_actual"] == SYNTHETIC_RUNNER_COMMIT
    assert identity["worktree_clean"] is True
    for name in (
        "reply_strategy_sha256",
        "reply_evidence_sha256",
        "pilot_ai_first_reply_strategy_sha256",
        "reply_prompt_profiles_sha256",
        "run_reply_prompt_calibration_sha256",
        "evidence_repository_fingerprint",
    ):
        assert len(identity[name]) == 64


def test_provider_phase_identity_binds_metadata_and_exact_prices(
    completed_execute_output: Path,
) -> None:
    metadata_path = completed_execute_output / "provider_model_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    phase = json.loads(
        (completed_execute_output / "provider_phase_identity.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(phase) == runner.PROVIDER_PHASE_FIELDS
    assert phase["schema_version"] == 1
    assert phase["provider_model_metadata_sha256"] == hashlib.sha256(
        metadata_path.read_bytes()
    ).hexdigest()
    assert phase["run_identity_sha256"] == hashlib.sha256(
        (completed_execute_output / "run_identity.json").read_bytes()
    ).hexdigest()
    for name in (
        "usd_ticks_per_dollar",
        "prompt_text_token_price",
        "cached_prompt_text_token_price",
        "completion_text_token_price",
    ):
        assert phase[name] == metadata[name]


@pytest.mark.parametrize("changed", ["blind-seed", "hard-limit"])
def test_resume_with_changed_identity_or_hard_limit_is_refused(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    output = tmp_path / changed
    shutil.copytree(completed_execute_output, output)
    counters = install_synthetic_execute(monkeypatch, output)
    args = paid_cli_args(output, "--resume")
    if changed == "blind-seed":
        args.blind_seed = "changed-seed"
    else:
        args.hard_limit_usd = 2
    with pytest.raises(runner.CalibrationError, match="run identity differs"):
        runner.run(args, environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters["metadata"] == counters["post"] == counters["pipeline"] == 0


def test_provider_phase_exists_before_first_synthetic_model_post(
    completed_execute_output: Path,
) -> None:
    assert (completed_execute_output / "provider_model_metadata.json").is_file()
    assert (completed_execute_output / "provider_phase_identity.json").is_file()


def test_resume_complete_provider_phase_with_zero_operations_skips_metadata_get(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = copy_before_model_operations(completed_execute_output, tmp_path / "phase-complete")
    counters = install_synthetic_execute(monkeypatch, output)
    runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 12, "pipeline": 12}


def test_resume_without_provider_files_and_zero_operations_repeats_one_get(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = copy_before_model_operations(completed_execute_output, tmp_path / "phase-neither")
    (output / "provider_model_metadata.json").unlink()
    (output / "provider_phase_identity.json").unlink()
    counters = install_synthetic_execute(monkeypatch, output)
    runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 1, "post": 12, "pipeline": 12}


@pytest.mark.parametrize(
    "retained",
    ["provider_model_metadata.json", "provider_phase_identity.json"],
)
def test_resume_one_incomplete_provider_file_republishes_phase(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retained: str,
) -> None:
    output = copy_before_model_operations(
        completed_execute_output, tmp_path / f"phase-only-{retained}"
    )
    for name in ("provider_model_metadata.json", "provider_phase_identity.json"):
        if name != retained:
            (output / name).unlink()
    counters = install_synthetic_execute(monkeypatch, output)
    runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 1, "post": 12, "pipeline": 12}
    assert (output / "provider_model_metadata.json").is_file()
    assert (output / "provider_phase_identity.json").is_file()


def test_complete_provider_files_that_disagree_are_refused(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = copy_before_model_operations(completed_execute_output, tmp_path / "phase-disagree")
    phase_path = output / "provider_phase_identity.json"
    phase = json.loads(phase_path.read_text(encoding="utf-8"))
    phase["prompt_text_token_price"] += 1
    phase_path.write_text(json.dumps(phase, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    counters = install_synthetic_execute(monkeypatch, output)
    with pytest.raises(runner.CalibrationError, match="provider phase identity differs"):
        runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_missing_provider_phase_with_model_operations_is_refused(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = copy_as_incomplete(completed_execute_output, tmp_path / "phase-missing-with-calls")
    (output / "provider_phase_identity.json").unlink()
    counters = install_synthetic_execute(monkeypatch, output)
    with pytest.raises(runner.CalibrationError, match="mandatory after model operations"):
        runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_altered_provider_metadata_with_model_operations_is_refused(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = copy_as_incomplete(completed_execute_output, tmp_path / "metadata-altered-with-calls")
    metadata_path = output / "provider_model_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["completion_text_token_price"] += 1
    metadata_path.write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    counters = install_synthetic_execute(monkeypatch, output)
    with pytest.raises(runner.CalibrationError, match="provider phase identity differs"):
        runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_missing_empty_ledger_is_recreated_only_before_provider_or_execution_evidence(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe = copy_before_model_operations(completed_execute_output, tmp_path / "ledger-safe")
    (safe / "cost_ledger.json").unlink()
    (safe / "provider_model_metadata.json").unlink()
    (safe / "provider_phase_identity.json").unlink()
    counters = install_synthetic_execute(monkeypatch, safe)
    runner.run(paid_cli_args(safe, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 1, "post": 12, "pipeline": 12}

    unsafe = copy_before_model_operations(completed_execute_output, tmp_path / "ledger-unsafe")
    (unsafe / "cost_ledger.json").unlink()
    counters = install_synthetic_execute(monkeypatch, unsafe)
    with pytest.raises(runner.CalibrationError, match="cannot be recreated"):
        runner.run(paid_cli_args(unsafe, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}

    evidence = copy_before_model_operations(
        completed_execute_output, tmp_path / "ledger-execution-evidence"
    )
    (evidence / "cost_ledger.json").unlink()
    (evidence / "provider_model_metadata.json").unlink()
    (evidence / "provider_phase_identity.json").unlink()
    runner.write_jsonl(evidence / "prompt_receipts.jsonl", [{"existing": "receipt"}])
    counters = install_synthetic_execute(monkeypatch, evidence)
    with pytest.raises(runner.CalibrationError, match="cannot be recreated"):
        runner.run(
            paid_cli_args(evidence, "--resume"),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_holdout_reliability_file_is_execution_evidence_for_missing_ledger(
    tmp_path: Path,
) -> None:
    output = tmp_path / "reliability-evidence"
    output.mkdir()
    runner.write_json(
        output / "holdout_reliability_summary.json",
        {"completed_pipeline_executions": 1},
    )
    assert runner.execution_evidence_exists(output) is True
    with pytest.raises(
        runner.CalibrationError,
        match="missing cost ledger cannot be recreated after provider or execution evidence",
    ):
        runner.open_resume_ledger(output, model="grok-4.3", hard_limit_usd=1)


def test_resume_with_blocked_ledger_is_refused(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = copy_as_incomplete(completed_execute_output, tmp_path / "blocked")
    ledger_path = output / "cost_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["blocked"] = True
    ledger["blocked_reason"] = "synthetic blocker"
    ledger_path.write_text(json.dumps(ledger, sort_keys=True) + "\n", encoding="utf-8")
    counters = install_synthetic_execute(monkeypatch, output)
    with pytest.raises(runner.CalibrationError, match="cost ledger is blocked"):
        runner.run(
            paid_cli_args(output, "--resume"),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    assert counters["post"] == counters["pipeline"] == 0


@pytest.mark.parametrize(
    "operation_status",
    ["sending", "prepared", "rate_limited", "server_error", "http_error", "ambiguous"],
)
def test_resume_refuses_every_incomplete_operation_status(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_status: str,
) -> None:
    output = copy_as_incomplete(
        completed_execute_output, tmp_path / f"incomplete-{operation_status}"
    )
    ledger_path = output / "cost_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["operations"][0]["status"] = operation_status
    ledger_path.write_text(json.dumps(ledger, sort_keys=True) + "\n", encoding="utf-8")
    counters = install_synthetic_execute(monkeypatch, output)
    with pytest.raises(runner.CalibrationError, match=f"status {operation_status}"):
        runner.run(
            paid_cli_args(output, "--resume"),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    assert counters["post"] == counters["pipeline"] == 0


def test_completed_logical_calls_resume_only_with_exact_request_hashes(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = copy_as_incomplete(completed_execute_output, tmp_path / "changed-request")
    ledger_path = output / "cost_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["operations"][0]["request_hash"] = "0" * 64
    ledger_path.write_text(json.dumps(ledger, sort_keys=True) + "\n", encoding="utf-8")
    counters = install_synthetic_execute(monkeypatch, output)
    with pytest.raises(runner.CalibrationError, match="response cache differs"):
        runner.run(
            paid_cli_args(output, "--resume"),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    assert counters["post"] == counters["pipeline"] == 0


def test_completed_pipeline_rows_and_receipts_are_not_duplicated_on_resume(
    completed_execute_output: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before_results = (completed_execute_output / "pipeline_results.jsonl").read_bytes()
    before_audits = (completed_execute_output / "pipeline_audits.jsonl").read_bytes()
    before_receipts = (completed_execute_output / "prompt_receipts.jsonl").read_bytes()
    counters = install_synthetic_execute(monkeypatch, completed_execute_output)
    runner.run(
        paid_cli_args(completed_execute_output, "--resume"),
        environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
    )
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}
    assert (completed_execute_output / "pipeline_results.jsonl").read_bytes() == before_results
    assert (completed_execute_output / "pipeline_audits.jsonl").read_bytes() == before_audits
    assert (completed_execute_output / "prompt_receipts.jsonl").read_bytes() == before_receipts


@pytest.mark.parametrize(
    "last_written",
    [
        "blind_review.md",
        "blind_review.csv",
        "blind_key.json",
        "calibration_report.md",
        "run_manifest.json",
    ],
)
def test_interrupted_finalisation_regenerates_identical_bytes_with_zero_calls(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    last_written: str,
) -> None:
    output = tmp_path / f"interrupted-{last_written}"
    shutil.copytree(completed_execute_output, output)
    final_order = list(runner.DISPOSABLE_DERIVED_FILES)
    cutoff = final_order.index(last_written)
    for name in final_order[cutoff + 1:]:
        (output / name).unlink()
    durable_before = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file() and path.name not in runner.FINAL_OUTPUT_FILES
    }
    counters = install_synthetic_execute(monkeypatch, output)
    runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}
    expected = {
        path.relative_to(completed_execute_output).as_posix(): path.read_bytes()
        for path in completed_execute_output.rglob("*")
        if path.is_file()
    }
    recovered = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert recovered == expected
    assert {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file() and path.name not in runner.FINAL_OUTPUT_FILES
    } == durable_before


def test_valid_sha256sums_is_an_idempotent_zero_call_marker(
    completed_execute_output: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = {
        path.relative_to(completed_execute_output).as_posix(): path.read_bytes()
        for path in completed_execute_output.rglob("*")
        if path.is_file()
    }
    counters = install_synthetic_execute(monkeypatch, completed_execute_output)
    runner.run(
        paid_cli_args(completed_execute_output, "--resume"),
        environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
    )
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}
    assert {
        path.relative_to(completed_execute_output).as_posix(): path.read_bytes()
        for path in completed_execute_output.rglob("*")
        if path.is_file()
    } == before


def test_invalid_existing_sha256sums_is_refused_without_regeneration(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "invalid-final-marker"
    shutil.copytree(completed_execute_output, output)
    marker = output / "SHA256SUMS"
    marker.write_text("invalid marker\n", encoding="utf-8")
    before = marker.read_bytes()
    counters = install_synthetic_execute(monkeypatch, output)
    with pytest.raises(runner.CalibrationError, match="SHA256SUMS line"):
        runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}
    assert marker.read_bytes() == before


def test_missing_result_is_reconstructed_from_exact_completed_cache(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = copy_as_incomplete(completed_execute_output, tmp_path / "reconstruct")
    result_path = output / "pipeline_results.jsonl"
    rows = result_path.read_text(encoding="utf-8").splitlines()
    result_path.write_text("\n".join(rows[1:]) + "\n", encoding="utf-8")
    receipt_count = len(
        (output / "prompt_receipts.jsonl").read_text(encoding="utf-8").splitlines()
    )
    counters = install_synthetic_execute(monkeypatch, output)
    runner.run(
        paid_cli_args(output, "--resume"),
        environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
    )
    assert counters == {"metadata": 0, "post": 0, "pipeline": 1}
    results = runner.index_execution_records(result_path, label="pipeline results")
    audits = runner.index_execution_records(output / "pipeline_audits.jsonl", label="pipeline audits")
    assert len(results) == len(audits) == 12
    assert len((output / "prompt_receipts.jsonl").read_text(encoding="utf-8").splitlines()) == receipt_count
    runner.verify_output_sha256sums(output)


def test_missing_audit_is_reconstructed_from_exact_completed_cache(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = copy_as_incomplete(completed_execute_output, tmp_path / "reconstruct-audit")
    audit_path = output / "pipeline_audits.jsonl"
    rows = audit_path.read_text(encoding="utf-8").splitlines()
    audit_path.write_text("\n".join(rows[1:]) + "\n", encoding="utf-8")
    counters = install_synthetic_execute(monkeypatch, output)
    runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 0, "pipeline": 1}
    assert len(runner.read_jsonl(audit_path)) == 12
    assert len(runner.read_jsonl(output / "pipeline_results.jsonl")) == 12


def test_result_call_counts_equal_owned_receipts_and_ledger_operations(
    completed_execute_output: Path,
) -> None:
    results = runner.read_jsonl(completed_execute_output / "pipeline_results.jsonl")
    receipts = runner.read_jsonl(completed_execute_output / "prompt_receipts.jsonl")
    ledger = json.loads(
        (completed_execute_output / "cost_ledger.json").read_text(encoding="utf-8")
    )
    for result in results:
        case_identity = result["case_identity"]
        assert result["model_call_count"] == len(result["logical_call_ids"])
        assert result["model_call_count"] == sum(
            row["case_identity"] == case_identity for row in receipts
        )
        assert result["model_call_count"] == sum(
            row["case_id"] == case_identity for row in ledger["operations"]
        )


@pytest.mark.parametrize("missing_side", ["receipt", "ledger"])
def test_orphan_receipt_or_ledger_operation_is_refused(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_side: str,
) -> None:
    output = copy_as_incomplete(
        completed_execute_output, tmp_path / f"orphan-{missing_side}"
    )
    if missing_side == "receipt":
        receipt_path = output / "prompt_receipts.jsonl"
        runner.write_jsonl(receipt_path, runner.read_jsonl(receipt_path)[1:])
    else:
        ledger_path = output / "cost_ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["operations"] = ledger["operations"][1:]
        runner.write_json(ledger_path, ledger)
    counters = install_synthetic_execute(monkeypatch, output)
    with pytest.raises(runner.CalibrationError, match="has no ledger operation|has no prompt receipt"):
        runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_receipt_ledger_request_hash_mismatch_is_refused(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = copy_as_incomplete(completed_execute_output, tmp_path / "receipt-hash-mismatch")
    receipt_path = output / "prompt_receipts.jsonl"
    receipts = runner.read_jsonl(receipt_path)
    receipts[0]["request_hash"] = "0" * 64
    runner.write_jsonl(receipt_path, receipts)
    counters = install_synthetic_execute(monkeypatch, output)
    with pytest.raises(runner.CalibrationError, match="request hash differs"):
        runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_operation_assigned_to_wrong_case_identity_is_refused(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = copy_as_incomplete(completed_execute_output, tmp_path / "wrong-operation-case")
    ledger_path = output / "cost_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["operations"][0]["case_id"] = ledger["operations"][1]["case_id"]
    runner.write_json(ledger_path, ledger)
    counters = install_synthetic_execute(monkeypatch, output)
    with pytest.raises(runner.CalibrationError, match="has no ledger operation"):
        runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_pipeline_audit_sha_mismatch_is_refused(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = copy_as_incomplete(completed_execute_output, tmp_path / "audit-sha-mismatch")
    result_path = output / "pipeline_results.jsonl"
    results = runner.read_jsonl(result_path)
    results[0]["pipeline_audit_sha256"] = "0" * 64
    runner.write_jsonl(result_path, results)
    counters = install_synthetic_execute(monkeypatch, output)
    with pytest.raises(runner.CalibrationError, match="result/audit binding differs"):
        runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_extra_completed_operation_blocks_finalisation_as_orphan(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = copy_as_incomplete(completed_execute_output, tmp_path / "extra-operation")
    receipt_path = output / "prompt_receipts.jsonl"
    receipts = runner.read_jsonl(receipt_path)
    extra_receipt = dict(receipts[0])
    case_identity = extra_receipt["case_identity"]
    extra_id = f"{case_identity}:2:reviewer"
    extra_hash = hashlib.sha256(extra_id.encode("utf-8")).hexdigest()
    extra_receipt.update({
        "stage": "reviewer",
        "call_sequence": 2,
        "logical_call_id": extra_id,
        "request_hash": extra_hash,
    })
    runner.write_jsonl(receipt_path, [*receipts, extra_receipt])
    ledger_path = output / "cost_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    original_operation = ledger["operations"][0]
    extra_operation = dict(original_operation)
    extra_operation.update({
        "logical_call_id": extra_id,
        "case_id": case_identity,
        "stage": "reviewer",
        "request_hash": extra_hash,
    })
    ledger["operations"].append(extra_operation)
    runner.write_json(ledger_path, ledger)
    original_cache = output / "raw_responses" / (
        hashlib.sha256(original_operation["logical_call_id"].encode("utf-8")).hexdigest()
        + ".json"
    )
    cache = json.loads(original_cache.read_text(encoding="utf-8"))
    cache.update({"logical_call_id": extra_id, "request_hash": extra_hash})
    runner.write_json(
        output / "raw_responses" / (hashlib.sha256(extra_id.encode("utf-8")).hexdigest() + ".json"),
        cache,
    )
    counters = install_synthetic_execute(monkeypatch, output)
    with pytest.raises(runner.CalibrationError, match="audit and receipt stages differ"):
        runner.run(paid_cli_args(output, "--resume"), environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_operational_failure_prevents_blind_review_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "operational-failure"
    install_synthetic_execute(
        monkeypatch, output, status="operational_failure", use_transport=False
    )
    with pytest.raises(runner.CalibrationError, match="non-calibration status"):
        runner.run(
            paid_cli_args(output), environ={"XAI_API_KEY": SYNTHETIC_API_KEY}
        )
    failures = runner.read_jsonl(output / "execution_failures.jsonl")
    assert len(failures) == 1
    assert failures[0]["status"] == "operational_failure"
    assert not any((output / name).exists() for name in runner.FINAL_OUTPUT_FILES)


def test_holdout_operational_failure_without_continuation_remains_fail_fast(
    completed_holdout_operational_output: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "holdout-fail-fast"
    data = completed_holdout_operational_output
    counters = install_synthetic_execute(
        monkeypatch,
        output,
        outcome_statuses=data["outcome_statuses"],
    )
    args = paid_cli_args(
        output,
        "--case-set",
        "holdout",
        "--context-clearance",
        str(data["clearance"]),
    )
    with pytest.raises(runner.CalibrationError, match="non-calibration status"):
        runner.run(args, environ={"XAI_API_KEY": SYNTHETIC_API_KEY})
    assert counters == {"metadata": 1, "post": 5, "pipeline": 1}
    assert runner.read_jsonl(output / "pipeline_results.jsonl") == []
    assert runner.read_jsonl(output / "pipeline_audits.jsonl") == []
    failures = runner.read_jsonl(output / "execution_failures.jsonl")
    assert len(failures) == 1
    assert failures[0]["status"] == "operational_failure"


@pytest.mark.parametrize("status", ["disabled", "unexpected_status"])
def test_holdout_continuation_does_not_continue_unknown_pipeline_statuses(
    completed_holdout_operational_output: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    data = completed_holdout_operational_output
    output = tmp_path / f"holdout-{status}"
    outcomes = {data["failure_key"]: status}
    counters = install_synthetic_execute(
        monkeypatch, output, outcome_statuses=outcomes
    )
    with pytest.raises(runner.CalibrationError, match="non-calibration status"):
        runner.run(
            paid_holdout_cli_args(
                output,
                data["clearance"],
                data["pre_exposed_candidate_id"],
            ),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    assert counters == {"metadata": 1, "post": 1, "pipeline": 1}
    assert runner.read_jsonl(output / "pipeline_results.jsonl") == []
    assert runner.read_jsonl(output / "pipeline_audits.jsonl") == []
    assert runner.read_jsonl(output / "execution_failures.jsonl")[0][
        "status"
    ] == status


def test_holdout_continuation_refuses_unbound_failure_call_inventory(
    completed_holdout_operational_output: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = completed_holdout_operational_output
    output = tmp_path / "holdout-unbound-failure"
    counters = install_synthetic_execute(
        monkeypatch,
        output,
        outcome_statuses={data["failure_key"]: "operational_failure"},
        use_transport=False,
    )
    with pytest.raises(runner.CalibrationError, match="call inventory is empty"):
        runner.run(
            paid_holdout_cli_args(
                output,
                data["clearance"],
                data["pre_exposed_candidate_id"],
            ),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    assert counters == {"metadata": 1, "post": 0, "pipeline": 1}
    assert runner.read_jsonl(output / "pipeline_results.jsonl") == []
    assert runner.read_jsonl(output / "pipeline_audits.jsonl") == []
    assert not (output / "execution_failures.jsonl").exists()


def test_holdout_continuation_records_failure_and_completes_all_executions(
    completed_holdout_operational_output: dict[str, Any],
) -> None:
    data = completed_holdout_operational_output
    output = data["output"]
    results = runner.read_jsonl(output / "pipeline_results.jsonl")
    audits = runner.read_jsonl(output / "pipeline_audits.jsonl")
    failures = runner.read_jsonl(output / "execution_failures.jsonl")
    assert len(results) == len(audits) == 84
    assert len({(row["candidate_id"], row["variant"]) for row in results}) == 84
    assert len({(row["candidate_id"], row["variant"]) for row in audits}) == 84
    assert Counter(row["status"] for row in results) == {
        "approved": 82,
        "no_reply": 1,
        "operational_failure": 1,
    }
    assert len(failures) == 1
    failure_result = next(
        row for row in results if row["status"] == "operational_failure"
    )
    assert (failure_result["candidate_id"], failure_result["variant"]) == data[
        "failure_key"
    ]
    assert failure_result["public_reply"] is None
    assert failure_result["pipeline_metadata"] is None
    assert results[-1]["status"] in runner.EDITORIAL_PIPELINE_STATUSES


def test_operational_failure_result_audit_and_journal_share_exact_binding(
    completed_holdout_operational_output: dict[str, Any],
) -> None:
    output = completed_holdout_operational_output["output"]
    results = runner.index_execution_records(
        output / "pipeline_results.jsonl", label="pipeline results"
    )
    audits = runner.index_execution_records(
        output / "pipeline_audits.jsonl", label="pipeline audits"
    )
    failures = runner.index_execution_records(
        output / "execution_failures.jsonl", label="execution failures"
    )
    key = completed_holdout_operational_output["failure_key"]
    result = results[key]
    audit = audits[key]
    failure = failures[key]
    assert result["model_call_count"] == len(result["call_inventory"]) == 5
    assert result["revision_count"] == failure["revision_count"] == 1
    for name in runner.CALL_BINDING_FIELDS:
        assert result[name] == audit[name] == failure[name]
    assert failure == runner.expected_execution_failure_row(result, audit)
    receipts = runner.index_prompt_receipts(output / "prompt_receipts.jsonl")
    ledger = json.loads((output / "cost_ledger.json").read_text(encoding="utf-8"))
    operations = {
        row["logical_call_id"]: row for row in ledger["operations"]
    }
    for inventory_row in result["call_inventory"]:
        logical_call_id = inventory_row["logical_call_id"]
        assert inventory_row["request_hash"] == receipts[logical_call_id][
            "request_hash"
        ] == operations[logical_call_id]["request_hash"]
        assert inventory_row["prompt_receipt_sha256"] == runner.value_sha256(
            receipts[logical_call_id]
        )
        assert inventory_row[
            "cost_ledger_operation_sha256"
        ] == runner.value_sha256(operations[logical_call_id])


@pytest.mark.parametrize("changed", ["continuation", "exclusion"])
def test_holdout_research_arguments_bind_plan_identity_manifest_and_resume(
    completed_holdout_operational_output: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    data = completed_holdout_operational_output
    output = data["output"]
    plan = json.loads((output / "execution_plan.json").read_text(encoding="utf-8"))
    identity = json.loads((output / "run_identity.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    exclusion_hash = runner.candidate_ids_sha256(
        [data["pre_exposed_candidate_id"]]
    )
    assert plan["continue_on_operational_failure"] is True
    assert plan["excluded_from_blind_quality_candidate_ids_sha256"] == exclusion_hash
    assert identity["continue_on_operational_failure"] is True
    assert identity["excluded_from_blind_quality_candidate_ids_sha256"] == exclusion_hash
    assert manifest["continue_on_operational_failure"] is True
    assert manifest["excluded_from_blind_quality_candidate_ids_sha256"] == exclusion_hash

    copied = tmp_path / f"changed-resume-{changed}"
    shutil.copytree(output, copied)
    counters = install_synthetic_execute(
        monkeypatch,
        copied,
        outcome_statuses=data["outcome_statuses"],
    )
    if changed == "continuation":
        changed_args = paid_cli_args(
            copied,
            "--resume",
            "--case-set",
            "holdout",
            "--context-clearance",
            str(data["clearance"]),
            "--exclude-from-blind-quality-candidate",
            data["pre_exposed_candidate_id"],
        )
    else:
        replacement = next(
            row["candidate_id"]
            for row in plan["executions"]
            if row["candidate_id"] != data["pre_exposed_candidate_id"]
        )
        changed_args = paid_holdout_cli_args(
            copied,
            data["clearance"],
            replacement,
            "--resume",
        )
    with pytest.raises(runner.CalibrationError, match="run identity differs"):
        runner.run(
            changed_args, environ={"XAI_API_KEY": SYNTHETIC_API_KEY}
        )
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_completed_operational_failure_resume_is_zero_call_and_idempotent(
    completed_holdout_operational_output: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = completed_holdout_operational_output
    output = data["output"]
    journal_names = (
        "pipeline_results.jsonl",
        "pipeline_audits.jsonl",
        "execution_failures.jsonl",
        "prompt_receipts.jsonl",
    )
    before = {name: (output / name).read_bytes() for name in journal_names}
    counters = install_synthetic_execute(
        monkeypatch,
        output,
        outcome_statuses=data["outcome_statuses"],
    )
    runner.run(
        paid_holdout_cli_args(
            output,
            data["clearance"],
            data["pre_exposed_candidate_id"],
            "--resume",
        ),
        environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
    )
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}
    assert {name: (output / name).read_bytes() for name in journal_names} == before


def test_holdout_quality_review_omits_failure_and_pre_exposed_candidates(
    completed_holdout_operational_output: dict[str, Any],
) -> None:
    data = completed_holdout_operational_output
    output = data["output"]
    failure_candidate_id = data["failure_key"][0]
    with (output / "blind_review.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    quality_ids = {row["candidate_id"] for row in rows}
    markdown = (output / "blind_review.md").read_text(encoding="utf-8")
    assert failure_candidate_id not in quality_ids
    assert data["pre_exposed_candidate_id"] not in quality_ids
    assert failure_candidate_id not in markdown
    assert data["pre_exposed_candidate_id"] not in markdown
    assert "PIPELINE FAILURE" not in markdown
    assert len(quality_ids) == 40
    assert len(rows) == 40 * 3
    assert {row["response_status"] for row in rows} == {"approved", "no_reply"}
    assert all(row["response_text"] != "PIPELINE FAILURE" for row in rows)
    unaffected = next(
        candidate_id
        for candidate_id in quality_ids
        if candidate_id != data["no_reply_key"][0]
    )
    assert sum(row["candidate_id"] == unaffected for row in rows) == 3


def test_holdout_manifest_and_reliability_count_statuses_separately(
    completed_holdout_operational_output: dict[str, Any],
) -> None:
    output = completed_holdout_operational_output["output"]
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["completed_pipeline_executions"] == 84
    assert manifest["valid_approved_count"] == 82
    assert manifest["valid_no_reply_count"] == 1
    assert manifest["operational_failure_count"] == 1
    assert manifest["operational_failure_candidate_count"] == 1
    assert manifest["pre_exposed_candidate_count"] == 1
    assert manifest["blind_quality_candidate_count"] == 40
    assert (
        manifest["valid_approved_count"] + manifest["valid_no_reply_count"]
    ) == 83

    summary_path = output / "holdout_reliability_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert set(summary) == runner.RELIABILITY_SUMMARY_FIELDS
    assert summary == {
        "selected_holdout_candidates": 42,
        "planned_pipeline_executions": 84,
        "completed_pipeline_executions": 84,
        "approved_outcomes": 82,
        "no_reply_outcomes": 1,
        "operational_failure_outcomes": 1,
        "candidates_with_operational_failure": 1,
        "pre_exposed_candidate_count": 1,
        "blind_quality_candidate_count": 40,
    }
    assert manifest["reliability_summary_sha256"] == hashlib.sha256(
        summary_path.read_bytes()
    ).hexdigest()
    details = runner.read_jsonl(output / "holdout_reliability_failures.jsonl")
    failures = runner.read_jsonl(output / "execution_failures.jsonl")
    assert details == failures
    assert len(details) == 1
    assert details[0]["call_inventory"]
    assert details[0]["pipeline_audit_sha256"]
    runner.verify_output_sha256sums(output)
    checksum_names = {
        line[66:]
        for line in (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    }
    assert runner.HOLDOUT_RELIABILITY_FILES <= checksum_names


def copy_holdout_for_corrupt_resume(
    completed: dict[str, Any], destination: Path
) -> Path:
    shutil.copytree(completed["output"], destination)
    (destination / "SHA256SUMS").unlink()
    return destination


def test_orphan_failure_journal_is_refused(
    completed_holdout_operational_output: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = completed_holdout_operational_output
    output = copy_holdout_for_corrupt_resume(data, tmp_path / "orphan-failure-row")
    results = runner.read_jsonl(output / "pipeline_results.jsonl")
    approved = next(row for row in results if row["status"] == "approved")
    orphan = dict(runner.read_jsonl(output / "execution_failures.jsonl")[0])
    orphan.update({
        "candidate_id": approved["candidate_id"],
        "stratum": approved["stratum"],
        "variant": approved["variant"],
    })
    runner.write_jsonl(
        output / "execution_failures.jsonl",
        [*runner.read_jsonl(output / "execution_failures.jsonl"), orphan],
    )
    counters = install_synthetic_execute(
        monkeypatch, output, outcome_statuses=data["outcome_statuses"]
    )
    with pytest.raises(runner.CalibrationError, match="orphan execution failure"):
        runner.run(
            paid_holdout_cli_args(
                output,
                data["clearance"],
                data["pre_exposed_candidate_id"],
                "--resume",
            ),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_duplicate_failure_journal_is_refused(
    completed_holdout_operational_output: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = completed_holdout_operational_output
    output = copy_holdout_for_corrupt_resume(data, tmp_path / "duplicate-failure-row")
    failures = runner.read_jsonl(output / "execution_failures.jsonl")
    runner.write_jsonl(output / "execution_failures.jsonl", [*failures, failures[0]])
    counters = install_synthetic_execute(
        monkeypatch, output, outcome_statuses=data["outcome_statuses"]
    )
    with pytest.raises(runner.CalibrationError, match="repeats execution identity"):
        runner.run(
            paid_holdout_cli_args(
                output,
                data["clearance"],
                data["pre_exposed_candidate_id"],
                "--resume",
            ),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_operational_failure_result_without_failure_journal_is_refused(
    completed_holdout_operational_output: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = completed_holdout_operational_output
    output = copy_holdout_for_corrupt_resume(data, tmp_path / "missing-failure-row")
    runner.write_text(output / "execution_failures.jsonl", "")
    counters = install_synthetic_execute(
        monkeypatch, output, outcome_statuses=data["outcome_statuses"]
    )
    with pytest.raises(
        runner.CalibrationError,
        match="operational-failure result lacks an execution failure row",
    ):
        runner.run(
            paid_holdout_cli_args(
                output,
                data["clearance"],
                data["pre_exposed_candidate_id"],
                "--resume",
            ),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_partially_recorded_operational_failure_is_refused_without_rerun(
    completed_holdout_operational_output: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = completed_holdout_operational_output
    output = copy_holdout_for_corrupt_resume(data, tmp_path / "partial-failure")
    audits = runner.read_jsonl(output / "pipeline_audits.jsonl")
    failure_key = data["failure_key"]
    runner.write_jsonl(
        output / "pipeline_audits.jsonl",
        [
            row
            for row in audits
            if (row["candidate_id"], row["variant"]) != failure_key
        ],
    )
    counters = install_synthetic_execute(
        monkeypatch, output, outcome_statuses=data["outcome_statuses"]
    )
    with pytest.raises(runner.CalibrationError, match="orphan execution failure row"):
        runner.run(
            paid_holdout_cli_args(
                output,
                data["clearance"],
                data["pre_exposed_candidate_id"],
                "--resume",
            ),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


@pytest.mark.parametrize("missing_side", ["receipt", "ledger"])
def test_failure_calls_absent_from_receipts_or_ledger_are_refused_before_resume(
    completed_holdout_operational_output: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_side: str,
) -> None:
    data = completed_holdout_operational_output
    output = copy_holdout_for_corrupt_resume(
        data, tmp_path / f"failure-{missing_side}-missing"
    )
    failure = runner.read_jsonl(output / "execution_failures.jsonl")[0]
    owned = set(failure["logical_call_ids"])
    if missing_side == "receipt":
        runner.write_jsonl(
            output / "prompt_receipts.jsonl",
            [
                row
                for row in runner.read_jsonl(output / "prompt_receipts.jsonl")
                if row["logical_call_id"] not in owned
            ],
        )
    else:
        ledger_path = output / "cost_ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["operations"] = [
            row
            for row in ledger["operations"]
            if row["logical_call_id"] not in owned
        ]
        runner.write_json(ledger_path, ledger)
    counters = install_synthetic_execute(
        monkeypatch, output, outcome_statuses=data["outcome_statuses"]
    )
    with pytest.raises(
        runner.CalibrationError,
        match="has no prompt receipt|has no ledger operation",
    ):
        runner.run(
            paid_holdout_cli_args(
                output,
                data["clearance"],
                data["pre_exposed_candidate_id"],
                "--resume",
            ),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


@pytest.mark.parametrize("review_format", ["csv", "markdown"])
def test_finalisation_refuses_failure_candidate_rendered_as_no_reply(
    completed_holdout_operational_output: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    review_format: str,
) -> None:
    data = completed_holdout_operational_output
    output = tmp_path / f"failure-in-quality-{review_format}"
    shutil.copytree(data["output"], output)
    if review_format == "csv":
        csv_path = output / "blind_review.csv"
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fieldnames = list(reader.fieldnames or [])
        injected = dict(rows[0])
        injected.update({
            "candidate_id": data["failure_key"][0],
            "response_text": "NO REPLY",
            "response_status": "no_reply",
        })
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows([*rows, injected])
        runner.write_text(csv_path, buffer.getvalue())
    else:
        markdown_path = output / "blind_review.md"
        runner.write_text(
            markdown_path,
            markdown_path.read_text(encoding="utf-8")
            + f"## {data['failure_key'][0]}\n\n### Response A\n\nNO REPLY\n",
        )
    runner.write_sha256sums(output)
    counters = install_synthetic_execute(
        monkeypatch, output, outcome_statuses=data["outcome_statuses"]
    )
    with pytest.raises(
        runner.CalibrationError,
        match=f"operational-failure candidate appears in blind_review.{review_format.replace('markdown', 'md')}",
    ):
        runner.run(
            paid_holdout_cli_args(
                output,
                data["clearance"],
                data["pre_exposed_candidate_id"],
                "--resume",
            ),
            environ={"XAI_API_KEY": SYNTHETIC_API_KEY},
        )
    assert counters == {"metadata": 0, "post": 0, "pipeline": 0}


def test_evidence_fingerprint_is_stable_and_changes_with_one_input() -> None:
    class Passage:
        def __init__(self, evidence_id: str, source_hash: str, model_hash: str) -> None:
            self.evidence_id = evidence_id
            self.source_hash = source_hash
            self.model_hash = model_hash

        def model_input_hash(self) -> str:
            return self.model_hash

    def repository(model_hash: str) -> SimpleNamespace:
        passage = Passage("evidence-one", "source-one", model_hash)
        return SimpleNamespace(
            completed_packet_count=2,
            unresolved_packet_count=1,
            attribution_eligible_packet_count=2,
            factual_evidence_count=1,
            passages={passage.evidence_id: passage},
        )

    first = runner.evidence_repository_fingerprint(repository("model-one"))
    second = runner.evidence_repository_fingerprint(repository("model-one"))
    changed = runner.evidence_repository_fingerprint(repository("model-two"))
    assert first == second
    assert changed != first


def test_final_manifest_contains_all_execution_provenance_fields(
    completed_execute_output: Path,
) -> None:
    manifest = json.loads(
        (completed_execute_output / "run_manifest.json").read_text(encoding="utf-8")
    )
    required = {
        "runner_git_commit",
        "runner_git_commit_expected",
        "worktree_clean",
        "runner_source_sha256",
        "prompt_profiles_source_sha256",
        "pilot_transport_source_sha256",
        "reply_strategy_sha256",
        "reply_evidence_sha256",
        "evidence_repository_fingerprint",
        "execution_plan_sha256",
        "current_profile_manifest_sha256",
        "compact_profile_manifest_sha256",
        "provider_phase_identity_sha256",
        "provider_model_metadata_sha256",
        "run_identity_sha256",
        "cost_ledger_status",
        "known_cost_usd",
        "ambiguous_exposure_usd",
        "completed_pipeline_executions",
        "valid_approved_count",
        "valid_no_reply_count",
        "operational_failure_count",
    }
    assert required <= set(manifest)
    assert manifest["completed_pipeline_executions"] == 12
    assert manifest["valid_approved_count"] + manifest["valid_no_reply_count"] == 12
    assert manifest["operational_failure_count"] == 0


def test_final_output_has_exactly_twelve_unique_valid_results_and_checksums(
    completed_execute_output: Path,
) -> None:
    rows = runner.read_jsonl(completed_execute_output / "pipeline_results.jsonl")
    keys = {(row["candidate_id"], row["variant"]) for row in rows}
    assert len(rows) == len(keys) == 12
    assert {row["status"] for row in rows} <= runner.VALID_PIPELINE_STATUSES
    assert all(row["model_call_count"] >= 1 for row in rows)
    assert all(row["model_call_count"] == len(row["logical_call_ids"]) for row in rows)
    assert all(row["call_inventory_sha256"] for row in rows)
    assert all(row["pipeline_audit_sha256"] for row in rows)
    audits = runner.read_jsonl(completed_execute_output / "pipeline_audits.jsonl")
    assert len(audits) == 12
    assert {
        (row["candidate_id"], row["variant"], row["execution_identity_sha256"])
        for row in rows
    } == {
        (row["candidate_id"], row["variant"], row["execution_identity_sha256"])
        for row in audits
    }
    receipts = runner.read_jsonl(completed_execute_output / "prompt_receipts.jsonl")
    assert len(receipts) == len({row["logical_call_id"] for row in receipts})
    assert {row["transport_status"] for row in receipts} <= {
        "prepared_for_transport",
        "returned_from_completed_cache",
        "transmitted_and_completed",
    }
    runner.verify_output_sha256sums(completed_execute_output)


def test_api_key_is_absent_from_all_validate_only_output(
    validation_pair: tuple[Path, Path],
) -> None:
    for path in validation_pair[0].rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert SYNTHETIC_API_KEY.encode() not in content
            assert b'"Authorization"' not in content


def test_validate_only_fixed_inputs_are_byte_identical(
    validation_pair: tuple[Path, Path],
) -> None:
    first, second = validation_pair
    first_files = {
        path.relative_to(first).as_posix(): path.read_bytes()
        for path in first.rglob("*") if path.is_file()
    }
    second_files = {
        path.relative_to(second).as_posix(): path.read_bytes()
        for path in second.rglob("*") if path.is_file()
    }
    assert first_files == second_files


def test_validate_only_output_permissions_are_private(
    validation_pair: tuple[Path, Path],
) -> None:
    output = validation_pair[0]
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    for path in output.rglob("*"):
        expected = 0o700 if path.is_dir() else 0o600
        assert stat.S_IMODE(path.stat().st_mode) == expected


def test_generated_sha256sums_verifies(validation_pair: tuple[Path, Path]) -> None:
    runner.verify_output_sha256sums(validation_pair[0])
    checksum_names = {
        line[66:]
        for line in (validation_pair[0] / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    }
    assert "validation_report.json" in checksum_names
    assert "prompt_preview_receipts.jsonl" in checksum_names
    assert "provider_model_metadata.json" not in checksum_names


def synthetic_accounting_recovery_identity() -> dict[str, Any]:
    return runner.build_accounting_recovery_identity(
        original_run_identity_sha256="1" * 64,
        predecessor_runner_source_sha256="2" * 64,
        recovery_runner_commit="3" * 40,
        recovery_runner_source_sha256="4" * 64,
        execution_plan_sha256="5" * 64,
        provider_phase_identity_sha256="6" * 64,
        context_audit_sha256="7" * 64,
        created_at="2026-08-11T14:30:00+00:00",
    )


def synthetic_accounting_recovery_signature(
    *, repaired: bool = False
) -> dict[str, Any]:
    results: dict[tuple[str, str], dict[str, Any]] = {}
    audits: dict[tuple[str, str], dict[str, Any]] = {}
    failures: dict[tuple[str, str], dict[str, Any]] = {}
    receipts: dict[str, dict[str, Any]] = {}
    operations: list[dict[str, Any]] = []

    for ordinal in range(79):
        candidate_id = f"synthetic-accounting-candidate-{ordinal:02d}"
        variant = "current"
        key = (candidate_id, variant)
        execution_sha256 = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()
        case_identity = f"{candidate_id}:{variant}:{execution_sha256}"
        stages = (
            ["proposer", "evidence", "reviewer"]
            if ordinal < 68
            else ["proposer", "reviewer"]
        )
        audit = [
            {"stage": stage, "status": "completed"}
            for stage in stages
        ]
        logical_call_ids: list[str] = []
        inventory: list[dict[str, Any]] = []
        for sequence, stage in enumerate(stages, 1):
            logical_call_id = f"{case_identity}:{sequence}:{stage}"
            request_hash = hashlib.sha256(
                logical_call_id.encode("utf-8")
            ).hexdigest()
            receipt = {
                "candidate_id": candidate_id,
                "variant": variant,
                "execution_identity_sha256": execution_sha256,
                "case_identity": case_identity,
                "call_sequence": sequence,
                "stage": stage,
                "logical_call_id": logical_call_id,
                "request_hash": request_hash,
                "transport_status": "transmitted_and_completed",
            }
            operation = {
                "logical_call_id": logical_call_id,
                "case_id": case_identity,
                "stage": stage,
                "request_hash": request_hash,
                "status": "completed",
            }
            receipts[logical_call_id] = receipt
            operations.append(operation)
            logical_call_ids.append(logical_call_id)
            inventory.append({
                "sequence": sequence,
                "stage": stage,
                "logical_call_id": logical_call_id,
                "request_hash": request_hash,
                "prompt_receipt_sha256": runner.value_sha256(receipt),
                "cost_ledger_operation_sha256": runner.value_sha256(operation),
            })
        binding = {
            "execution_identity_sha256": execution_sha256,
            "case_identity": case_identity,
            "model_call_count": len(stages),
            "logical_call_ids": logical_call_ids,
            "model_stage_sequence": stages,
            "call_inventory": inventory,
            "call_inventory_sha256": runner.value_sha256(inventory),
            "pipeline_audit_sha256": runner.value_sha256(audit),
        }
        operational_failure = ordinal < 9
        result = {
            "candidate_id": candidate_id,
            "stratum": "synthetic",
            "variant": variant,
            "status": (
                "operational_failure" if operational_failure else "approved"
            ),
            "reason": "synthetic_operational_failure" if operational_failure else None,
            "public_reply": None,
            "revision_count": 1 if operational_failure else 0,
            "pipeline_metadata": None,
            **binding,
        }
        audit_row = {
            "candidate_id": candidate_id,
            "stratum": "synthetic",
            "variant": variant,
            "audit": audit,
            **binding,
        }
        results[key] = result
        audits[key] = audit_row
        if operational_failure:
            failures[key] = runner.expected_execution_failure_row(
                result, audit_row
            )

    target_ids: list[str] = []
    target_inventory: list[dict[str, Any]] = []
    for sequence, stage in enumerate(
        runner.ACCOUNTING_RECOVERY_RECEIPT_STAGE_SEQUENCE, 1
    ):
        logical_call_id = (
            f"{runner.ACCOUNTING_RECOVERY_CASE_IDENTITY}:{sequence}:{stage}"
        )
        request_hash = hashlib.sha256(
            logical_call_id.encode("utf-8")
        ).hexdigest()
        receipt = {
            "candidate_id": runner.ACCOUNTING_RECOVERY_CANDIDATE_ID,
            "variant": runner.ACCOUNTING_RECOVERY_VARIANT,
            "execution_identity_sha256": (
                runner.ACCOUNTING_RECOVERY_EXECUTION_IDENTITY_SHA256
            ),
            "case_identity": runner.ACCOUNTING_RECOVERY_CASE_IDENTITY,
            "call_sequence": sequence,
            "stage": stage,
            "logical_call_id": logical_call_id,
            "request_hash": request_hash,
            "transport_status": "transmitted_and_completed",
        }
        operation = {
            "logical_call_id": logical_call_id,
            "case_id": runner.ACCOUNTING_RECOVERY_CASE_IDENTITY,
            "stage": stage,
            "request_hash": request_hash,
            "status": "completed",
        }
        receipts[logical_call_id] = receipt
        operations.append(operation)
        target_ids.append(logical_call_id)
        target_inventory.append({
            "sequence": sequence,
            "stage": stage,
            "logical_call_id": logical_call_id,
            "request_hash": request_hash,
            "prompt_receipt_sha256": runner.value_sha256(receipt),
            "cost_ledger_operation_sha256": runner.value_sha256(operation),
        })

    if repaired:
        target_audit = [
            {"stage": "proposer", "status": "completed"},
            {"stage": "evidence", "status": "invalid_response_retry"},
            {"stage": "evidence", "status": "insufficient"},
            {"stage": "reviewer", "status": "completed"},
            {"stage": "revision_proposer", "status": "completed"},
            {"stage": "revision_evidence", "status": "insufficient"},
            dict(runner.ACCOUNTING_RECOVERY_TERMINAL_AUDIT_ROW),
        ]
        target_binding = {
            "execution_identity_sha256": (
                runner.ACCOUNTING_RECOVERY_EXECUTION_IDENTITY_SHA256
            ),
            "case_identity": runner.ACCOUNTING_RECOVERY_CASE_IDENTITY,
            "model_call_count": 6,
            "logical_call_ids": target_ids,
            "model_stage_sequence": list(
                runner.ACCOUNTING_RECOVERY_RECEIPT_STAGE_SEQUENCE
            ),
            "call_inventory": target_inventory,
            "call_inventory_sha256": runner.value_sha256(target_inventory),
            "pipeline_audit_sha256": runner.value_sha256(target_audit),
        }
        target_key = (
            runner.ACCOUNTING_RECOVERY_CANDIDATE_ID,
            runner.ACCOUNTING_RECOVERY_VARIANT,
        )
        target_result = {
            "candidate_id": runner.ACCOUNTING_RECOVERY_CANDIDATE_ID,
            "stratum": "synthetic",
            "variant": runner.ACCOUNTING_RECOVERY_VARIANT,
            "status": "operational_failure",
            "reason": "revision_reviewer_invalid",
            "public_reply": None,
            "revision_count": 1,
            "pipeline_metadata": None,
            **target_binding,
        }
        target_audit_row = {
            "candidate_id": runner.ACCOUNTING_RECOVERY_CANDIDATE_ID,
            "stratum": "synthetic",
            "variant": runner.ACCOUNTING_RECOVERY_VARIANT,
            "audit": target_audit,
            **target_binding,
        }
        results[target_key] = target_result
        audits[target_key] = target_audit_row
        failures[target_key] = runner.expected_execution_failure_row(
            target_result, target_audit_row
        )

    assert len(receipts) == len(operations) == 232
    return {
        "results": results,
        "audits": audits,
        "failures": failures,
        "receipts": receipts,
        "ledger_data": {
            "blocked": False,
            "status": "active",
            "ambiguous_exposure_usd": 0.0,
            "ambiguous_exposure_in_usd_ticks": 0,
            "operations": operations,
        },
    }


def test_accounting_recovery_identity_has_exact_safe_fields() -> None:
    identity = synthetic_accounting_recovery_identity()
    assert set(identity) == set(runner.ACCOUNTING_RECOVERY_IDENTITY_FIELDS)
    assert identity["receipt_stage_sequence"] == list(
        runner.ACCOUNTING_RECOVERY_RECEIPT_STAGE_SEQUENCE
    )
    assert not {
        "candidate_text",
        "reply_text",
        "claims",
        "prompts",
        "provider_response",
        "raw_response",
    } & set(identity)
    altered = {**identity, "prompt_text": "not permitted"}
    with pytest.raises(runner.CalibrationError, match="identity fields differ"):
        runner.verify_accounting_recovery_identity(altered)


def test_existing_accounting_recovery_identity_verification_is_idempotent() -> None:
    expected = synthetic_accounting_recovery_identity()
    existing = copy.deepcopy(expected)
    assert runner.verify_accounting_recovery_identity(
        existing, expected
    ) == expected
    assert runner.verify_accounting_recovery_identity(
        existing, expected
    ) == expected
    existing["recovery_runner_source_sha256"] = "8" * 64
    with pytest.raises(
        runner.CalibrationError, match="stored accounting recovery identity differs"
    ):
        runner.verify_accounting_recovery_identity(existing, expected)


def test_exact_interrupted_accounting_recovery_signature_passes() -> None:
    state = synthetic_accounting_recovery_signature()
    verified = runner.verify_accounting_recovery_interrupted_signature(**state)
    assert verified["state"] == "interrupted"
    assert verified["receipt_stage_sequence"] == list(
        runner.ACCOUNTING_RECOVERY_RECEIPT_STAGE_SEQUENCE
    )
    assert (
        verified["result_count"],
        verified["audit_count"],
        verified["failure_count"],
        verified["receipt_count"],
    ) == (79, 79, 9, 232)


def test_accounting_recovery_wrong_journal_counts_are_rejected() -> None:
    state = synthetic_accounting_recovery_signature()
    removed = next(iter(state["results"]))
    state["results"].pop(removed)
    with pytest.raises(runner.CalibrationError, match="journal counts differ"):
        runner.verify_accounting_recovery_interrupted_signature(**state)


def test_accounting_recovery_wrong_candidate_identity_is_rejected() -> None:
    state = synthetic_accounting_recovery_signature()
    target_id = next(
        logical_call_id
        for logical_call_id in state["receipts"]
        if logical_call_id.startswith(runner.ACCOUNTING_RECOVERY_CASE_IDENTITY)
    )
    state["receipts"][target_id]["candidate_id"] = "candidate-wrong"
    with pytest.raises(runner.CalibrationError, match="target identity"):
        runner.verify_accounting_recovery_interrupted_signature(**state)


def test_accounting_recovery_wrong_receipt_stages_are_rejected() -> None:
    state = synthetic_accounting_recovery_signature()
    target_id = next(
        logical_call_id
        for logical_call_id in state["receipts"]
        if logical_call_id.startswith(runner.ACCOUNTING_RECOVERY_CASE_IDENTITY)
    )
    state["receipts"][target_id]["stage"] = "reviewer"
    with pytest.raises(runner.CalibrationError, match="receipt stages differ"):
        runner.verify_accounting_recovery_interrupted_signature(**state)


def test_accounting_recovery_missing_ledger_operation_is_rejected() -> None:
    state = synthetic_accounting_recovery_signature()
    state["ledger_data"]["operations"].pop()
    with pytest.raises(runner.CalibrationError, match="inventories differ"):
        runner.verify_accounting_recovery_interrupted_signature(**state)


def test_accounting_recovery_noncompleted_ledger_operation_is_rejected() -> None:
    state = synthetic_accounting_recovery_signature()
    state["ledger_data"]["operations"][-1]["status"] = "prepared"
    with pytest.raises(runner.CalibrationError, match="not completed"):
        runner.verify_accounting_recovery_interrupted_signature(**state)


def test_accounting_recovery_unexpected_orphan_call_is_rejected() -> None:
    state = synthetic_accounting_recovery_signature()
    key = next(
        key for key, row in state["results"].items()
        if row["status"] == "approved" and row["model_call_count"] == 3
    )
    result = state["results"][key]
    audit = state["audits"][key]
    orphaned = result["logical_call_ids"][-1]
    logical_call_ids = result["logical_call_ids"][:-1]
    inventory = result["call_inventory"][:-1]
    model_stages = result["model_stage_sequence"][:-1]
    audit_payload = audit["audit"][:-1]
    for row in (result, audit):
        row["model_call_count"] = 2
        row["logical_call_ids"] = list(logical_call_ids)
        row["model_stage_sequence"] = list(model_stages)
        row["call_inventory"] = copy.deepcopy(inventory)
        row["call_inventory_sha256"] = runner.value_sha256(inventory)
        row["pipeline_audit_sha256"] = runner.value_sha256(audit_payload)
    audit["audit"] = audit_payload
    assert orphaned in state["receipts"]
    with pytest.raises(runner.CalibrationError, match="orphan call inventory"):
        runner.verify_accounting_recovery_interrupted_signature(**state)


def test_exact_repaired_accounting_recovery_signature_is_idempotent() -> None:
    state = synthetic_accounting_recovery_signature(repaired=True)
    first = runner.verify_accounting_recovery_repaired_signature(**state)
    second = runner.verify_accounting_recovery_repaired_signature(**state)
    assert first == second
    assert (
        first["result_count"],
        first["audit_count"],
        first["failure_count"],
        first["receipt_count"],
    ) == (80, 80, 10, 232)


def build_synthetic_cache_only_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Create six harmless completed caches for the exact recovery identity."""
    output = tmp_path / "cache-only-recovery"
    output.mkdir(mode=0o700)
    raw_responses = output / "raw_responses"
    raw_responses.mkdir(mode=0o700)
    runner.write_text(output / "prompt_receipts.jsonl", "")

    case = synthetic_case()
    case["candidate_id"] = runner.ACCOUNTING_RECOVERY_CANDIDATE_ID
    manifests = runner.profile_manifests()
    pack_data = {
        "cases": [case],
        "pack_sha256": "f" * 64,
    }
    execution_identity = runner.pipeline_execution_identity(
        case,
        runner.ACCOUNTING_RECOVERY_VARIANT,
        manifests,
        pack_data["pack_sha256"],
        runner.DEFAULT_MODEL,
    )
    original_binding = runner.pipeline_execution_binding

    def exact_target_binding(identity: dict[str, Any]) -> dict[str, str]:
        if identity.get("candidate_id") == runner.ACCOUNTING_RECOVERY_CANDIDATE_ID:
            return {
                "execution_identity_sha256": (
                    runner.ACCOUNTING_RECOVERY_EXECUTION_IDENTITY_SHA256
                ),
                "case_identity": runner.ACCOUNTING_RECOVERY_CASE_IDENTITY,
            }
        return original_binding(identity)

    monkeypatch.setattr(runner, "pipeline_execution_binding", exact_target_binding)
    ledger = pilot.PilotLedger(
        output / "cost_ledger.json",
        model=runner.DEFAULT_MODEL,
        hard_limit_usd=1,
        run_version=runner.RUNNER_VERSION,
    )
    setup_posts = 0

    def setup_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        nonlocal setup_posts
        setup_posts += 1
        return successful_response()

    delegate = pilot.PilotTransport(
        api_key=SYNTHETIC_API_KEY,
        base_url=runner.DEFAULT_XAI_BASE,
        model_metadata=model_metadata(),
        ledger=ledger,
        response_dir=raw_responses,
        post=setup_post,
    )
    receipt_transport = runner.PromptReceiptTransport(
        delegate,
        output / "prompt_receipts.jsonl",
        model=runner.DEFAULT_MODEL,
    )
    receipt_transport.set_case(execution_identity)
    for stage in runner.ACCOUNTING_RECOVERY_RECEIPT_STAGE_SEQUENCE:
        assert json.loads(
            receipt_transport(**transport_arguments(stage=stage))
        ) == {"ok": True}
    assert setup_posts == 6

    for name in (
        "run_identity.json",
        "provider_phase_identity.json",
        "provider_model_metadata.json",
        "pack_verification.json",
        "profile_manifests.json",
        "execution_plan.json",
        "holdout_context_audit.json",
    ):
        runner.write_json(output / name, {"synthetic": name})
    for name in (
        "holdout_context_review.md",
        "holdout_context_clearance.csv",
        "holdout_context_recovery.jsonl",
    ):
        runner.write_text(output / name, f"synthetic {name}\n")

    receipts = runner.index_prompt_receipts(output / "prompt_receipts.jsonl")
    args = Namespace(
        model=runner.DEFAULT_MODEL,
        xai_base=runner.DEFAULT_XAI_BASE,
        maximum_rate_limit_retries=1,
        maximum_server_error_retries=1,
    )
    return {
        "output": output,
        "pack_data": pack_data,
        "manifests": manifests,
        "case": case,
        "identity": execution_identity,
        "ledger": ledger,
        "receipts": receipts,
        "metadata": model_metadata(),
        "args": args,
    }


@pytest.fixture
def synthetic_cache_only_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    data = build_synthetic_cache_only_recovery(tmp_path, monkeypatch)

    def reconstructed_pipeline(**kwargs: Any) -> SimpleNamespace:
        audit: list[dict[str, Any]] = []
        for stage in runner.ACCOUNTING_RECOVERY_RECEIPT_STAGE_SEQUENCE:
            assert json.loads(
                kwargs["transport"](**transport_arguments(stage=stage))
            ) == {"ok": True}
            audit.append({"stage": stage, "status": "completed"})
        audit.append(dict(runner.ACCOUNTING_RECOVERY_TERMINAL_AUDIT_ROW))
        return SimpleNamespace(
            status="operational_failure",
            reason="revision_reviewer_invalid",
            reply=None,
            model_call_count=6,
            revision_count=1,
            audit=audit,
        )

    monkeypatch.setattr(
        runner.reply_strategy,
        "run_reply_pipeline",
        reconstructed_pipeline,
    )
    immutable_before = runner.accounting_recovery_immutable_snapshot(data["output"])
    result, audit, failure, metrics = runner.reconstruct_accounting_recovery_execution(
        data["args"],
        data["pack_data"],
        data["manifests"],
        SimpleNamespace(),
        output=data["output"],
        metadata=data["metadata"],
        ledger=data["ledger"],
        receipts=data["receipts"],
    )
    data.update({
        "result": result,
        "audit": audit,
        "failure": failure,
        "metrics": metrics,
        "immutable_before": immutable_before,
    })
    return data


def test_accounting_reconstruction_uses_six_caches_and_zero_http(
    synthetic_cache_only_recovery: dict[str, Any],
) -> None:
    data = synthetic_cache_only_recovery
    assert data["metrics"] == {
        "completed_cache_returns": 6,
        "http_requests": 0,
    }
    assert data["result"]["status"] == "operational_failure"
    assert data["result"]["reason"] == "revision_reviewer_invalid"
    assert data["result"]["model_call_count"] == 6
    assert data["result"]["revision_count"] == 1
    assert data["audit"]["audit"][-1] == (
        runner.ACCOUNTING_RECOVERY_TERMINAL_AUDIT_ROW
    )
    assert data["result"]["model_stage_sequence"] == list(
        runner.ACCOUNTING_RECOVERY_RECEIPT_STAGE_SEQUENCE
    )
    assert runner.accounting_recovery_immutable_snapshot(data["output"]) == (
        data["immutable_before"]
    )


def test_cache_only_guard_refuses_missing_call_before_transport_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = build_synthetic_cache_only_recovery(tmp_path, monkeypatch)
    forbidden_posts = 0

    def forbidden_post(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal forbidden_posts
        forbidden_posts += 1
        pytest.fail("cache-only recovery reached model transport")

    delegate = pilot.PilotTransport(
        api_key="cache-only",
        base_url=runner.DEFAULT_XAI_BASE,
        model_metadata=data["metadata"],
        ledger=data["ledger"],
        response_dir=data["output"] / "raw_responses",
        post=forbidden_post,
    )
    transport = runner.PromptReceiptTransport(
        delegate,
        data["output"] / "prompt_receipts.jsonl",
        model=runner.DEFAULT_MODEL,
        existing_receipts=data["receipts"],
        cache_only=True,
    )
    transport.set_case(data["identity"])
    before = runner.accounting_recovery_immutable_snapshot(data["output"])
    for stage in runner.ACCOUNTING_RECOVERY_RECEIPT_STAGE_SEQUENCE:
        transport(**transport_arguments(stage=stage))
    with pytest.raises(runner.CalibrationError, match="without an exact completed cache"):
        transport(**transport_arguments(stage="revision_reviewer"))
    assert forbidden_posts == 0
    assert transport.completed_cache_returns == 6
    assert runner.accounting_recovery_immutable_snapshot(data["output"]) == before


def test_recovery_triplet_appends_once_with_exact_post_counts(
    synthetic_cache_only_recovery: dict[str, Any],
) -> None:
    data = synthetic_cache_only_recovery
    state = synthetic_accounting_recovery_signature()
    output = data["output"]
    runner.write_jsonl(output / "pipeline_results.jsonl", state["results"].values())
    runner.write_jsonl(output / "pipeline_audits.jsonl", state["audits"].values())
    runner.write_jsonl(output / "execution_failures.jsonl", state["failures"].values())
    before = {
        name: (output / name).read_bytes()
        for name in (
            "pipeline_results.jsonl",
            "pipeline_audits.jsonl",
            "execution_failures.jsonl",
        )
    }
    runner.append_accounting_recovery_journal_triplet(
        output,
        data["result"],
        data["audit"],
        data["failure"],
    )
    results = runner.index_execution_records(
        output / "pipeline_results.jsonl", label="pipeline results"
    )
    audits = runner.index_execution_records(
        output / "pipeline_audits.jsonl", label="pipeline audits"
    )
    failures = runner.index_execution_records(
        output / "execution_failures.jsonl", label="execution failures"
    )
    assert (len(results), len(audits), len(failures), len(state["receipts"])) == (
        80,
        80,
        10,
        232,
    )
    for name, row in (
        ("pipeline_results.jsonl", data["result"]),
        ("pipeline_audits.jsonl", data["audit"]),
        ("execution_failures.jsonl", data["failure"]),
    ):
        assert (output / name).read_bytes() == (
            before[name] + runner.canonical_json_bytes(row) + b"\n"
        )


def test_ordinary_strict_resume_still_rejects_cross_commit_runner(
    completed_execute_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "ordinary-cross-commit"
    shutil.copytree(completed_execute_output, output)
    changed_commit = "b" * 40
    hashes = runner.runner_source_hashes()
    hashes["runner_source_sha256"] = "9" * 64

    def changed_provenance(
        expected: str | None = None,
        *,
        require_clean_checkout: bool = False,
    ) -> dict[str, Any]:
        assert expected == changed_commit
        assert require_clean_checkout is True
        return {
            "runner_git_commit": changed_commit,
            "runner_git_commit_expected": changed_commit,
            "worktree_clean": True,
            **hashes,
        }

    def forbidden_http(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("strict cross-commit refusal reached HTTP")

    monkeypatch.setattr(runner, "execution_provenance", changed_provenance)
    monkeypatch.setattr(pilot.requests, "get", forbidden_http)
    monkeypatch.setattr(pilot.requests, "post", forbidden_http)
    args = paid_cli_args(output, "--resume")
    args.expected_runner_git_commit = changed_commit
    with pytest.raises(runner.CalibrationError, match="run identity differs"):
        runner.run(args, environ={"XAI_API_KEY": SYNTHETIC_API_KEY})


def test_recovery_final_manifest_records_both_commits_and_checksum(
    completed_holdout_operational_output: dict[str, Any],
    tmp_path: Path,
) -> None:
    data = completed_holdout_operational_output
    output = tmp_path / "recovery-manifest"
    shutil.copytree(data["output"], output)
    pack_data = fresh_holdout_pack_data()
    context_artifacts = runner.build_holdout_context_artifacts(pack_data)
    clearance = runner.validate_context_clearance(
        data["clearance"], context_artifacts, require_all_ready=True
    )
    plan = runner.read_json(output / "execution_plan.json")
    results = runner.index_execution_records(
        output / "pipeline_results.jsonl", label="pipeline results"
    )
    ledger_data = runner.read_json(output / "cost_ledger.json")
    while len(ledger_data["operations"]) < 235:
        ledger_data["operations"].append({"attempt_number": 1})
    prior_manifest = runner.read_json(output / "run_manifest.json")
    provenance_fields = (
        "runner_git_commit",
        "runner_git_commit_expected",
        "worktree_clean",
        "runner_source_sha256",
        "prompt_profiles_source_sha256",
        "pilot_transport_source_sha256",
        "reply_strategy_sha256",
        "reply_evidence_sha256",
        "evidence_repository_fingerprint",
        "execution_plan_sha256",
        "current_profile_manifest_sha256",
        "compact_profile_manifest_sha256",
    )
    provenance = {name: prior_manifest[name] for name in provenance_fields}
    recovery_identity = runner.build_accounting_recovery_identity(
        original_run_identity_sha256=runner.file_sha256(
            output / "run_identity.json"
        ),
        predecessor_runner_source_sha256="2" * 64,
        recovery_runner_commit=provenance["runner_git_commit"],
        recovery_runner_source_sha256=provenance["runner_source_sha256"],
        execution_plan_sha256=runner.value_sha256(plan),
        provider_phase_identity_sha256=runner.file_sha256(
            output / "provider_phase_identity.json"
        ),
        context_audit_sha256=runner.file_sha256(
            output / "holdout_context_audit.json"
        ),
        created_at="2026-08-11T15:00:00Z",
    )
    runner.write_json(
        output / runner.ACCOUNTING_RECOVERY_IDENTITY_FILE,
        recovery_identity,
    )
    args = paid_holdout_cli_args(
        output,
        data["clearance"],
        data["pre_exposed_candidate_id"],
    )
    manifest = runner.execute_manifest(
        args,
        pack_data,
        provenance,
        plan=plan,
        context_artifacts=context_artifacts,
        clearance=clearance,
        output=output,
        identity_sha256=runner.file_sha256(output / "run_identity.json"),
        ledger_data=ledger_data,
        result_rows=results,
    )
    assert manifest["accounting_recovery_applied"] is True
    assert manifest["original_runner_git_commit"] == (
        runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT
    )
    assert manifest["accounting_recovery_runner_git_commit"] == (
        provenance["runner_git_commit"]
    )
    assert manifest["accounting_recovery_identity_sha256"] == runner.file_sha256(
        output / runner.ACCOUNTING_RECOVERY_IDENTITY_FILE
    )
    assert manifest["reconstructed_from_completed_cache_count"] == 6
    assert manifest["new_provider_calls_after_recovery"] == 3
    runner.write_json(output / "run_manifest.json", manifest)
    runner.write_sha256sums(output)
    runner.verify_output_sha256sums(output)
    checksum_names = {
        line[66:]
        for line in (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    }
    assert runner.ACCOUNTING_RECOVERY_IDENTITY_FILE in checksum_names


def test_all_protected_prompt_profile_and_pipeline_hashes_remain_unchanged() -> None:
    predecessor = runner.committed_source_hashes(
        runner.ACCOUNTING_RECOVERY_PREDECESSOR_COMMIT
    )
    current = runner.runner_source_hashes()
    for name in set(runner.SOURCE_PATHS) - {"runner_source_sha256"}:
        assert current[name] == predecessor[name]
    manifests = runner.profile_manifests()
    runner.verify_frozen_profile_manifests(manifests)
    assert manifests["compact"]["profile_version"] == "compact-reply-profile-v4"


def test_recovery_orchestrator_repairs_once_then_releases_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interrupted = synthetic_accounting_recovery_signature()
    repaired = synthetic_accounting_recovery_signature(repaired=True)
    output = tmp_path / "orchestrated-recovery"
    output.mkdir(mode=0o700)
    runner.write_jsonl(
        output / "pipeline_results.jsonl", interrupted["results"].values()
    )
    runner.write_jsonl(
        output / "pipeline_audits.jsonl", interrupted["audits"].values()
    )
    runner.write_jsonl(
        output / "execution_failures.jsonl", interrupted["failures"].values()
    )
    target_key = (
        runner.ACCOUNTING_RECOVERY_CANDIDATE_ID,
        runner.ACCOUNTING_RECOVERY_VARIANT,
    )
    events: list[str] = []

    def verified_partial(**_kwargs: Any) -> set[str]:
        events.append("partial_verified")
        return set()

    def exact_prefix(
        _plan: dict[str, Any],
        results: dict[tuple[str, str], dict[str, Any]],
    ) -> int:
        return len(results)

    def stable_snapshot(_output: Path) -> dict[str, str]:
        return {"protected": "unchanged"}

    def publish_once(
        _args: Namespace,
        marker_output: Path,
        _checkout: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        marker = marker_output / runner.ACCOUNTING_RECOVERY_IDENTITY_FILE
        created = not marker.exists()
        if created:
            runner.write_json(marker, synthetic_accounting_recovery_identity())
            events.append("identity_published")
        else:
            events.append("identity_verified")
        return synthetic_accounting_recovery_identity(), created

    def reconstruct_once(*_args: Any, **_kwargs: Any) -> tuple[Any, Any, Any, Any]:
        events.append("reconstructed")
        return (
            copy.deepcopy(repaired["results"][target_key]),
            copy.deepcopy(repaired["audits"][target_key]),
            copy.deepcopy(repaired["failures"][target_key]),
            {"completed_cache_returns": 6, "http_requests": 0},
        )

    original_repaired_verifier = (
        runner.verify_accounting_recovery_repaired_signature
    )

    def verified_repair(**kwargs: Any) -> dict[str, Any]:
        verified = original_repaired_verifier(**kwargs)
        events.append("repair_verified")
        return verified

    monkeypatch.setattr(runner, "verify_partial_execution_journals", verified_partial)
    monkeypatch.setattr(runner, "verify_accounting_recovery_plan_prefix", exact_prefix)
    monkeypatch.setattr(
        runner, "accounting_recovery_immutable_snapshot", stable_snapshot
    )
    monkeypatch.setattr(
        runner, "verify_or_publish_accounting_recovery_identity", publish_once
    )
    monkeypatch.setattr(
        runner, "reconstruct_accounting_recovery_execution", reconstruct_once
    )
    monkeypatch.setattr(
        runner, "verify_accounting_recovery_repaired_signature", verified_repair
    )
    args = Namespace(model=runner.DEFAULT_MODEL)
    ledger = SimpleNamespace(data=interrupted["ledger_data"])
    common = {
        "args": args,
        "pack_data": {"pack_sha256": "f" * 64},
        "manifests": {},
        "repository": SimpleNamespace(),
        "output": output,
        "checkout": {},
        "metadata": {},
        "ledger": ledger,
        "plan": {},
        "cases": {},
        "receipts": interrupted["receipts"],
    }
    first = runner.apply_or_verify_accounting_recovery(
        **common,
        result_rows=interrupted["results"],
        audit_rows=interrupted["audits"],
        failure_rows=interrupted["failures"],
    )
    events.append("remaining_released")
    journal_bytes = {
        name: (output / name).read_bytes()
        for name in (
            "pipeline_results.jsonl",
            "pipeline_audits.jsonl",
            "execution_failures.jsonl",
        )
    }
    second = runner.apply_or_verify_accounting_recovery(
        **common,
        result_rows=first[0],
        audit_rows=first[1],
        failure_rows=first[2],
    )
    assert [len(first[0]), len(first[1]), len(first[2])] == [80, 80, 10]
    assert [len(second[0]), len(second[1]), len(second[2])] == [80, 80, 10]
    assert events.count("reconstructed") == 1
    assert events.count("identity_published") == 1
    assert events.count("identity_verified") == 1
    assert events.index("identity_published") < events.index("reconstructed")
    assert events.index("reconstructed") < events.index("repair_verified")
    assert events.index("repair_verified") < events.index("remaining_released")
    assert journal_bytes == {
        name: (output / name).read_bytes() for name in journal_bytes
    }
