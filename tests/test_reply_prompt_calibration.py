"""Offline safety and determinism tests for reply prompt calibration."""

from __future__ import annotations

import hashlib
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


PACK = Path(
    "/disks/disk1/research/"
    "mrsMThatcher-reply-replay-pack-committed-20260810T185001Z"
)
SYNTHETIC_API_KEY = "synthetic-calibration-key-not-valid"
SYNTHETIC_RUNNER_COMMIT = "a" * 40


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


def transport_arguments(user_prompt: str = "user") -> dict[str, Any]:
    return {
        "stage": "proposer",
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
        if use_transport:
            assert json.loads(kwargs["transport"](**transport_arguments())) == {"ok": True}
        reply = SyntheticReply() if status == "approved" else None
        return SimpleNamespace(
            status=status,
            reason=f"synthetic-{status}",
            reply=reply,
            model_call_count=1 if use_transport else 0,
            revision_count=0,
            audit=(
                {"stage": "quotation_resolution", "status": "not_resolved"},
                {"stage": "proposer", "status": "completed"},
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


@pytest.fixture(scope="module")
def pack_data() -> dict[str, Any]:
    return runner.verify_replay_pack(PACK)


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
    with pytest.raises(runner.CalibrationError, match="invalid calibration outcome"):
        runner.render_outcome({"status": "operational_failure", "public_reply": None})


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
