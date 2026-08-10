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
from typing import Any

import pytest

from tools import pilot_ai_first_reply_strategy as pilot
from tools import run_reply_prompt_calibration as runner


PACK = Path(
    "/disks/disk1/research/"
    "mrsMThatcher-reply-replay-pack-committed-20260810T185001Z"
)
SYNTHETIC_API_KEY = "synthetic-calibration-key-not-valid"


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


def test_execute_requires_explicit_acknowledgement(tmp_path: Path) -> None:
    args = cli_args(tmp_path / "out", "--execute", "--hard-limit-usd", "1")
    with pytest.raises(runner.CalibrationError, match="acknowledge-paid-model-calls"):
        runner.validate_arguments(args, {"XAI_API_KEY": SYNTHETIC_API_KEY})


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_execute_requires_positive_finite_hard_limit(tmp_path: Path, value: str) -> None:
    args = cli_args(
        tmp_path / "out",
        "--execute",
        "--hard-limit-usd", value,
        "--acknowledge-paid-model-calls", runner.PAID_ACKNOWLEDGEMENT,
    )
    with pytest.raises(runner.CalibrationError, match="positive --hard-limit-usd"):
        runner.validate_arguments(args, {"XAI_API_KEY": SYNTHETIC_API_KEY})


def test_execute_requires_xai_api_key(tmp_path: Path) -> None:
    args = cli_args(
        tmp_path / "out",
        "--execute",
        "--hard-limit-usd", "1",
        "--acknowledge-paid-model-calls", runner.PAID_ACKNOWLEDGEMENT,
    )
    with pytest.raises(runner.CalibrationError, match="requires XAI_API_KEY"):
        runner.validate_arguments(args, {})


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
        (case["candidate_id"], "historical"): "Alpha response.",
        (case["candidate_id"], "current"): "Beta response.",
        (case["candidate_id"], "compact"): None,
    }
    markdown, csv_text = runner.build_blind_review([case], assignments, outputs)
    assert "Response A" in markdown and "Response B" in markdown and "Response C" in markdown
    assert "NO REPLY" in markdown
    assert "historical" not in markdown.casefold()
    assert "current" not in markdown.casefold()
    assert "compact" not in markdown.casefold()
    assert "historical" not in csv_text.casefold()


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
