from __future__ import annotations

import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import historical_context_outbox as outbox_module
from historical_context_outbox import (
    CONFIRMED,
    CONTEXT_REPLY_ATTEMPTING,
    CONTEXT_REPLY_PENDING,
    FAILED_RETRYABLE,
    FAILED_TERMINAL,
    HistoricalContextOutbox,
    MAIN_POST_CONFIRMED,
    NOT_REQUIRED,
    OutboxCapacityError,
    OutboxConflictError,
    OutboxPolicyMismatchError,
    OutboxValidationError,
    OutboxWorkerBusy,
)


QUOTE_ID = "a" * 64
QUOTE_TEXT = "The facts of history are stubborn things."


def _attempt_worker_lock(path: str, result_queue) -> None:
    try:
        with HistoricalContextOutbox(path).worker_lock():
            result_queue.put("acquired")
    except OutboxWorkerBusy:
        result_queue.put("busy")


def test_multiple_obligations_separate_confirmed_main_post_from_optional_context(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical_context_outbox.json"
    existing_receipts = {
        tmp_path / "historical_context_reply_receipt.json": b'{"sentinel":"context"}\n',
        tmp_path / "regular_post_receipt.json": b'{"sentinel":"main"}\n',
    }
    for receipt_path, payload in existing_receipts.items():
        receipt_path.write_bytes(payload)

    outbox = HistoricalContextOutbox(path)
    pending = outbox.enqueue(
        "100",
        main_post_confirmed_epoch=1_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    not_required = outbox.enqueue(
        "101",
        main_post_confirmed_epoch=1_001,
        not_required_reason="quote has no eligible context packet",
    )

    assert pending["main_post"] == {
        "state": MAIN_POST_CONFIRMED,
        "confirmed_epoch": 1_000,
    }
    assert pending["main_post"]["state"] == "main_post_confirmed"
    assert pending["context_reply"] == {
        "state": CONTEXT_REPLY_PENDING,
        "quote_id": QUOTE_ID,
        "quote_text": QUOTE_TEXT,
        "attempt_count": 0,
        "next_attempt_epoch": 1_000,
        "backoff_seconds": 0,
        "updated_epoch": 1_000,
    }
    assert pending["context_reply"]["state"] == "context_reply_pending"
    assert not_required["main_post"]["state"] == MAIN_POST_CONFIRMED
    assert not_required["context_reply"]["state"] == NOT_REQUIRED
    assert not_required["context_reply"]["state"] == "context_reply_not_required"

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert set(persisted["obligations"]) == {"100", "101"}
    assert "reply_text" not in path.read_text(encoding="utf-8")
    assert persisted["obligations"]["100"]["parent_post_id"] == "100"
    assert persisted["obligations"]["101"]["parent_post_id"] == "101"

    for receipt_path, payload in existing_receipts.items():
        assert receipt_path.read_bytes() == payload

    detached = outbox.get("100")
    assert detached is not None
    detached["main_post"]["state"] = "tampered"
    assert outbox.get("100")["main_post"]["state"] == MAIN_POST_CONFIRMED


def test_empty_outbox_initialisation_is_explicit_and_non_overwriting(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical_context_outbox.json"
    outbox = HistoricalContextOutbox(path)

    initial = outbox.initialise_empty()

    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600
    assert initial == outbox.snapshot()
    assert initial["obligations"] == {}
    before = path.read_bytes()
    with pytest.raises(OutboxConflictError, match="existing outbox namespace"):
        outbox.initialise_empty()
    assert path.read_bytes() == before


def test_required_established_outbox_never_reinterprets_disappearance_as_empty(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical_context_outbox.json"
    outbox = HistoricalContextOutbox(path, require_existing=True)
    outbox.initialise_empty()
    outbox.enqueue(
        "102",
        main_post_confirmed_epoch=1_002,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    path.unlink()

    with pytest.raises(OutboxValidationError, match="established.*missing"):
        outbox.snapshot()


def test_outbox_initialisation_fails_closed_on_namespace_inspection_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initialisation cannot overwrite a namespace it failed to inspect."""

    path = tmp_path / "historical_context_outbox.json"
    real_lstat = outbox_module.os.lstat

    def fail_target(target, *args, **kwargs):
        if Path(target) == path:
            raise PermissionError("injected outbox inspection failure")
        return real_lstat(target, *args, **kwargs)

    monkeypatch.setattr(outbox_module.os, "lstat", fail_target)
    with pytest.raises(OutboxValidationError, match="cannot be inspected"):
        HistoricalContextOutbox(path).initialise_empty()
    assert not path.exists()


def test_outbox_read_fails_closed_on_namespace_inspection_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reader cannot reinterpret failed inspection as an empty outbox."""

    path = tmp_path / "historical_context_outbox.json"
    outbox = HistoricalContextOutbox(path)
    real_lstat = outbox_module.os.lstat

    def fail_target(target, *args, **kwargs):
        if Path(target) == path:
            raise PermissionError("injected outbox inspection failure")
        return real_lstat(target, *args, **kwargs)

    monkeypatch.setattr(outbox_module.os, "lstat", fail_target)
    with pytest.raises(OutboxValidationError, match="cannot be inspected"):
        outbox.snapshot()


def test_enqueue_and_retry_updates_are_idempotent_and_do_not_regress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "outbox.json"
    writes: list[dict] = []
    real_atomic_write = outbox_module._atomic_write_json

    def counting_write(target: Path, value: dict) -> None:
        writes.append(value)
        real_atomic_write(target, value)

    monkeypatch.setattr(outbox_module, "_atomic_write_json", counting_write)
    outbox = HistoricalContextOutbox(path, base_backoff_seconds=10)
    initial = outbox.enqueue(
        "200",
        main_post_confirmed_epoch=2_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    assert len(writes) == 1
    assert (
        outbox.enqueue(
            "200",
            main_post_confirmed_epoch=2_000,
            quote_id=QUOTE_ID,
            quote_text=QUOTE_TEXT,
        )
        == initial
    )
    assert len(writes) == 1

    outbox.claim_attempt("200", started_epoch=2_000)
    assert len(writes) == 2
    failed = outbox.record_retryable_failure(
        "200",
        attempt_number=1,
        error=TimeoutError("definite pre-send timeout"),
        failed_epoch=2_010,
    )
    assert len(writes) == 3
    assert failed["context_reply"]["state"] == FAILED_RETRYABLE
    assert failed["context_reply"]["attempt_count"] == 1

    assert (
        outbox.record_retryable_failure(
            "200",
            attempt_number=1,
            error=TimeoutError("definite pre-send timeout"),
            failed_epoch=2_010,
        )
        == failed
    )
    assert len(writes) == 3

    # Replaying the original enqueue observes progress without resetting it.
    assert (
        outbox.enqueue(
            "200",
            main_post_confirmed_epoch=2_000,
            quote_id=QUOTE_ID,
            quote_text=QUOTE_TEXT,
        )["context_reply"]["state"]
        == FAILED_RETRYABLE
    )
    assert len(writes) == 3

    with pytest.raises(OutboxConflictError, match="conflicting outcome metadata"):
        outbox.record_retryable_failure(
            "200",
            attempt_number=1,
            error="a different failure",
            failed_epoch=2_010,
        )
    with pytest.raises(OutboxConflictError, match="identity conflicts"):
        outbox.enqueue(
            "200",
            main_post_confirmed_epoch=2_000,
            quote_id="b" * 64,
            quote_text=QUOTE_TEXT,
        )


def test_configuration_change_does_not_erase_existing_context_obligation(
    tmp_path: Path,
) -> None:
    outbox = HistoricalContextOutbox(tmp_path / "outbox.json")
    pending = outbox.enqueue(
        "201",
        main_post_confirmed_epoch=2_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )

    replayed = outbox.enqueue(
        "201",
        main_post_confirmed_epoch=2_000,
        not_required_reason="historical_context_reply_disabled",
    )

    assert replayed == pending
    assert replayed["context_reply"]["state"] == CONTEXT_REPLY_PENDING


def test_due_retry_backoff_is_bounded_and_terminal_states_are_retained(
    tmp_path: Path,
) -> None:
    outbox = HistoricalContextOutbox(
        tmp_path / "outbox.json",
        max_attempts=4,
        base_backoff_seconds=10,
        max_backoff_seconds=25,
    )
    assert outbox.max_attempts == 4
    outbox.enqueue(
        "302",
        main_post_confirmed_epoch=3_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    outbox.enqueue(
        "301",
        main_post_confirmed_epoch=3_000,
        quote_id="b" * 64,
        quote_text="A second quotation.",
    )
    assert outbox.due(2_999) == []
    assert [item["parent_post_id"] for item in outbox.due(3_000)] == ["301", "302"]
    assert [item["parent_post_id"] for item in outbox.due(3_000, limit=1)] == [
        "301"
    ]

    outbox.claim_attempt("302", started_epoch=3_000)
    first = outbox.record_retryable_failure(
        "302",
        attempt_number=1,
        error="temporary failure one",
        failed_epoch=3_010,
    )["context_reply"]
    assert (first["backoff_seconds"], first["next_attempt_epoch"]) == (10, 3_020)
    assert outbox.next_attempt_number("302") == 2
    assert all(item["parent_post_id"] != "302" for item in outbox.due(3_019))

    outbox.claim_attempt("302", started_epoch=3_020)
    second = outbox.record_retryable_failure(
        "302",
        attempt_number=2,
        error="temporary failure two",
        failed_epoch=3_020,
    )["context_reply"]
    assert (second["backoff_seconds"], second["next_attempt_epoch"]) == (20, 3_040)

    outbox.claim_attempt("302", started_epoch=3_040)
    third = outbox.record_retryable_failure(
        "302",
        attempt_number=3,
        error="temporary failure three",
        failed_epoch=3_040,
    )["context_reply"]
    assert (third["backoff_seconds"], third["next_attempt_epoch"]) == (25, 3_065)

    outbox.claim_attempt("302", started_epoch=3_065)
    with pytest.raises(OutboxConflictError, match="final permitted attempt"):
        outbox.record_retryable_failure(
            "302",
            attempt_number=4,
            error="still failing",
            failed_epoch=3_065,
        )
    terminal = outbox.record_terminal_failure(
        "302",
        attempt_number=4,
        error="retry budget exhausted",
        failed_epoch=3_065,
    )
    assert terminal["context_reply"]["state"] == FAILED_TERMINAL
    assert outbox.get("302") == terminal
    assert all(item["parent_post_id"] != "302" for item in outbox.due(9_999))


def test_claim_enforces_due_time_and_cannot_reuse_an_interrupted_claim(
    tmp_path: Path,
) -> None:
    outbox = HistoricalContextOutbox(
        tmp_path / "outbox.json",
        base_backoff_seconds=10,
    )
    outbox.enqueue(
        "303",
        main_post_confirmed_epoch=3_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    outbox.claim_attempt("303", started_epoch=3_000)
    outbox.record_retryable_failure(
        "303",
        attempt_number=1,
        error="temporary",
        failed_epoch=3_010,
    )

    with pytest.raises(ValueError, match="retry is not due"):
        outbox.claim_attempt("303", started_epoch=3_019)
    claimed = outbox.claim_attempt("303", started_epoch=3_020)
    assert claimed["context_reply"]["state"] == CONTEXT_REPLY_ATTEMPTING
    assert claimed["context_reply"]["attempt_count"] == 2
    assert claimed["context_reply"]["remote_transaction_started"] is False
    with pytest.raises(OutboxConflictError, match="already durably claimed"):
        outbox.claim_attempt("303", started_epoch=3_021)
    with pytest.raises(OutboxConflictError, match="already durably claimed"):
        outbox.next_attempt_number("303")


def test_remote_transaction_phase_is_strict_durable_and_one_way(
    tmp_path: Path,
) -> None:
    path = tmp_path / "outbox.json"
    outbox = HistoricalContextOutbox(path)
    outbox.enqueue(
        "306",
        main_post_confirmed_epoch=3_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    claimed = outbox.claim_attempt("306", started_epoch=3_010)

    assert claimed["context_reply"]["remote_transaction_started"] is False
    with pytest.raises(OutboxConflictError, match="no exact source-receipt binding"):
        outbox.mark_remote_transaction_started("306", attempt_number=1)
    bound = outbox.bind_attempt_source_receipt(
        "306",
        attempt_number=1,
        source_receipt_sha256="a" * 64,
        source_receipt_attempt_number=7,
    )
    assert bound["context_reply"]["source_receipt_sha256"] == "a" * 64
    assert bound["context_reply"]["source_receipt_attempt_number"] == 7
    assert (
        outbox.bind_attempt_source_receipt(
            "306",
            attempt_number=1,
            source_receipt_sha256="a" * 64,
            source_receipt_attempt_number=7,
        )
        == bound
    )
    with pytest.raises(OutboxConflictError, match="conflicting source receipt"):
        outbox.bind_attempt_source_receipt(
            "306",
            attempt_number=1,
            source_receipt_sha256="b" * 64,
            source_receipt_attempt_number=7,
        )
    started = outbox.mark_remote_transaction_started(
        "306",
        attempt_number=1,
    )
    assert started["context_reply"]["remote_transaction_started"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["obligations"]["306"][
        "context_reply"
    ]["remote_transaction_started"] is True

    with pytest.raises(OutboxConflictError, match="already durably marked"):
        outbox.mark_remote_transaction_started("306", attempt_number=1)
    with pytest.raises(OutboxConflictError, match="does not match"):
        outbox.mark_remote_transaction_started("306", attempt_number=2)


@pytest.mark.parametrize(
    "outcome",
    ("retryable", "terminal", "not_required"),
)
def test_remote_started_attempt_rejects_every_unproved_local_outcome(
    outcome: str,
    tmp_path: Path,
) -> None:
    """A peer phase transition cannot be recast as safe local failure."""

    outbox = HistoricalContextOutbox(tmp_path / f"{outcome}.json")
    outbox.enqueue(
        "316",
        main_post_confirmed_epoch=3_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    outbox.claim_attempt("316", started_epoch=3_010)
    outbox.bind_attempt_source_receipt(
        "316",
        attempt_number=1,
        source_receipt_sha256="f" * 64,
        source_receipt_attempt_number=7,
    )
    outbox.mark_remote_transaction_started("316", attempt_number=1)

    with pytest.raises(OutboxConflictError, match="remote non-success|not_required"):
        if outcome == "retryable":
            outbox.record_retryable_failure(
                "316",
                attempt_number=1,
                error="unproved local error",
                failed_epoch=3_011,
            )
        elif outcome == "terminal":
            outbox.record_terminal_failure(
                "316",
                attempt_number=1,
                error="unproved local error",
                failed_epoch=3_011,
            )
        else:
            outbox.mark_not_required(
                "316",
                reason="unproved local decision",
                decided_epoch=3_011,
            )

    current = outbox.get("316")["context_reply"]
    assert current["state"] == CONTEXT_REPLY_ATTEMPTING
    assert current["remote_transaction_started"] is True


def test_legacy_unknown_phase_rejects_unproved_failure(
    tmp_path: Path,
) -> None:
    """Missing legacy phase is ambiguous, never an implicit local failure."""

    path = tmp_path / "legacy.json"
    outbox = HistoricalContextOutbox(path)
    outbox.enqueue(
        "317",
        main_post_confirmed_epoch=3_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    outbox.claim_attempt("317", started_epoch=3_010)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["obligations"]["317"]["context_reply"].pop(
        "remote_transaction_started"
    )
    outbox_module._atomic_write_json(path, document)

    with pytest.raises(OutboxConflictError, match="remote non-success"):
        outbox.record_retryable_failure(
            "317",
            attempt_number=1,
            error="unproved legacy error",
            failed_epoch=3_011,
        )


def test_outbox_attempt_one_can_bind_source_receipt_attempt_twenty_one(
    tmp_path: Path,
) -> None:
    path = tmp_path / "outbox.json"
    outbox = HistoricalContextOutbox(path, base_backoff_seconds=10)
    outbox.enqueue(
        "308",
        main_post_confirmed_epoch=3_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    claimed = outbox.claim_attempt("308", started_epoch=3_010)

    assert claimed["context_reply"]["attempt_count"] == 1
    bound = outbox.bind_attempt_source_receipt(
        "308",
        attempt_number=1,
        source_receipt_sha256="c" * 64,
        source_receipt_attempt_number=21,
    )
    assert bound["context_reply"]["source_receipt_attempt_number"] == 21

    failed = outbox.record_retryable_failure(
        "308",
        attempt_number=1,
        error="definite pre-send failure",
        failed_epoch=3_011,
        proved_remote_non_success=True,
    )
    assert failed["context_reply"]["failure"][
        "source_receipt_attempt_number"
    ] == 21
    assert HistoricalContextOutbox(
        path,
        base_backoff_seconds=10,
    ).snapshot()["obligations"]["308"] == failed


def test_attempting_phase_accepts_only_boolean_and_legacy_is_not_upgradeable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "outbox.json"
    outbox = HistoricalContextOutbox(path)
    outbox.enqueue(
        "307",
        main_post_confirmed_epoch=3_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    outbox.claim_attempt("307", started_epoch=3_010)
    document = json.loads(path.read_text(encoding="utf-8"))
    context = document["obligations"]["307"]["context_reply"]
    context["remote_transaction_started"] = 1
    outbox_module._atomic_write_json(path, document)
    with pytest.raises(OutboxValidationError, match="attempting.*metadata"):
        outbox.snapshot()

    context.pop("remote_transaction_started")
    outbox_module._atomic_write_json(path, document)
    assert "remote_transaction_started" not in outbox.snapshot()["obligations"][
        "307"
    ]["context_reply"]
    with pytest.raises(OutboxConflictError, match="legacy attempting record"):
        outbox.mark_remote_transaction_started("307", attempt_number=1)


def test_outcomes_require_a_durable_claim_and_clamp_policy_clock_regression(
    tmp_path: Path,
) -> None:
    outbox = HistoricalContextOutbox(tmp_path / "outbox.json")
    outbox.enqueue(
        "305",
        main_post_confirmed_epoch=3_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    with pytest.raises(OutboxConflictError, match="durably claimed"):
        outbox.record_confirmed(
            "305",
            attempt_number=1,
            reply_post_id="9305",
            confirmed_epoch=3_001,
        )
    with pytest.raises(OutboxConflictError, match="durably claimed"):
        outbox.record_retryable_failure(
            "305",
            attempt_number=1,
            error="not claimed",
            failed_epoch=3_001,
        )
    claimed = outbox.claim_attempt("305", started_epoch=3_010)
    assert claimed["context_reply"]["state"] == CONTEXT_REPLY_ATTEMPTING
    decided = outbox.mark_not_required(
        "305",
        reason="clock regression fixture",
        decided_epoch=3_009,
    )
    assert decided["context_reply"] == {
        "state": NOT_REQUIRED,
        "reason": "clock regression fixture",
        "updated_epoch": 3_010,
    }


def test_proved_non_success_clamps_wall_clock_rollback_to_durable_epoch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "outbox.json"
    durable_epoch = 3_100
    outbox = HistoricalContextOutbox(path, base_backoff_seconds=10)
    outbox.enqueue(
        "309",
        main_post_confirmed_epoch=durable_epoch - 10,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    outbox.claim_attempt("309", started_epoch=durable_epoch)
    outbox.bind_attempt_source_receipt(
        "309",
        attempt_number=1,
        source_receipt_sha256="d" * 64,
        source_receipt_attempt_number=1,
    )

    failed = outbox.record_retryable_failure(
        "309",
        attempt_number=1,
        error="definite pre-send failure after wall-clock rollback",
        failed_epoch=durable_epoch - 1,
        proved_remote_non_success=True,
    )
    context_reply = failed["context_reply"]
    assert context_reply["updated_epoch"] == durable_epoch
    assert context_reply["failure"]["failed_epoch"] == durable_epoch
    assert context_reply["next_attempt_epoch"] == durable_epoch + 10
    assert HistoricalContextOutbox(
        path,
        base_backoff_seconds=10,
    ).snapshot()["obligations"]["309"] == failed


def test_pre_remote_failure_clamps_wall_clock_rollback_to_durable_epoch(
    tmp_path: Path,
) -> None:
    """A local pre-transport crash is not stranded by CLOCK_REALTIME rollback."""

    path = tmp_path / "outbox.json"
    durable_epoch = 3_150
    outbox = HistoricalContextOutbox(path, base_backoff_seconds=10)
    outbox.enqueue(
        "311",
        main_post_confirmed_epoch=durable_epoch - 10,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    outbox.claim_attempt("311", started_epoch=durable_epoch)

    failed = outbox.record_retryable_failure(
        "311",
        attempt_number=1,
        error="interrupted before the transport transaction",
        failed_epoch=durable_epoch - 1,
    )

    context_reply = failed["context_reply"]
    assert context_reply["updated_epoch"] == durable_epoch
    assert context_reply["failure"]["failed_epoch"] == durable_epoch
    assert context_reply["next_attempt_epoch"] == durable_epoch + 10
    assert HistoricalContextOutbox(
        path,
        base_backoff_seconds=10,
    ).snapshot()["obligations"]["311"] == failed


def test_confirmed_outcome_clamps_wall_clock_rollback_to_durable_epoch(
    tmp_path: Path,
) -> None:
    """Remote identity proof is not stranded by CLOCK_REALTIME rollback."""

    path = tmp_path / "outbox.json"
    durable_epoch = 3_200
    outbox = HistoricalContextOutbox(path)
    outbox.enqueue(
        "310",
        main_post_confirmed_epoch=durable_epoch - 10,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    outbox.claim_attempt("310", started_epoch=durable_epoch)

    confirmed = outbox.record_confirmed(
        "310",
        attempt_number=1,
        reply_post_id="9310",
        confirmed_epoch=durable_epoch - 1,
    )

    context_reply = confirmed["context_reply"]
    assert context_reply["updated_epoch"] == durable_epoch
    assert context_reply["confirmed_epoch"] == durable_epoch
    assert HistoricalContextOutbox(path).snapshot()["obligations"]["310"] == (
        confirmed
    )


def test_confirmed_outcome_retains_exact_source_receipt_lineage(
    tmp_path: Path,
) -> None:
    """A terminal outcome cannot forget which local source it confirmed."""

    path = tmp_path / "outbox.json"
    outbox = HistoricalContextOutbox(path)
    outbox.enqueue(
        "312",
        main_post_confirmed_epoch=3_200,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    outbox.claim_attempt("312", started_epoch=3_210)
    outbox.bind_attempt_source_receipt(
        "312",
        attempt_number=1,
        source_receipt_sha256="d" * 64,
        source_receipt_attempt_number=7,
    )
    outbox.mark_remote_transaction_started("312", attempt_number=1)

    confirmed = outbox.record_confirmed(
        "312",
        attempt_number=1,
        reply_post_id="9312",
        confirmed_epoch=3_220,
    )["context_reply"]

    assert confirmed["source_receipt_sha256"] == "d" * 64
    assert confirmed["source_receipt_attempt_number"] == 7
    assert outbox.record_confirmed(
        "312",
        attempt_number=1,
        reply_post_id="9312",
        confirmed_epoch=3_220,
    )["context_reply"] == confirmed
    assert HistoricalContextOutbox(path).get("312")["context_reply"] == confirmed


@pytest.mark.parametrize(
    "source_fields",
    (
        {"source_receipt_sha256": "d" * 64},
        {"source_receipt_attempt_number": 1},
        {
            "source_receipt_sha256": "not-a-sha256",
            "source_receipt_attempt_number": 1,
        },
        {
            "source_receipt_sha256": "d" * 64,
            "source_receipt_attempt_number": True,
        },
        {
            "source_receipt_sha256": "d" * 64,
            "source_receipt_attempt_number": 0,
        },
    ),
)
def test_confirmed_outcome_rejects_partial_or_invalid_source_lineage(
    tmp_path: Path,
    source_fields: dict,
) -> None:
    path = tmp_path / "outbox.json"
    outbox = HistoricalContextOutbox(path)
    outbox.enqueue(
        "313",
        main_post_confirmed_epoch=3_200,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    outbox.claim_attempt("313", started_epoch=3_210)
    outbox.record_confirmed(
        "313",
        attempt_number=1,
        reply_post_id="9313",
        confirmed_epoch=3_220,
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    document["obligations"]["313"]["context_reply"].update(source_fields)
    outbox_module._atomic_write_json(path, document)

    with pytest.raises(OutboxValidationError, match="confirmed context reply"):
        outbox.snapshot()


def test_long_or_nul_failure_text_is_safely_bounded_before_persistence(
    tmp_path: Path,
) -> None:
    outbox = HistoricalContextOutbox(tmp_path / "outbox.json")
    outbox.enqueue(
        "304",
        main_post_confirmed_epoch=3_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )

    outbox.claim_attempt("304", started_epoch=3_000)
    failed = outbox.record_retryable_failure(
        "304",
        attempt_number=1,
        error="prefix\x00" + ("x" * 10_000),
        failed_epoch=3_010,
    )["context_reply"]["failure"]["error"]

    assert "\x00" not in failed
    assert "\\x00" in failed
    assert failed.endswith("… [truncated]")
    assert len(failed) == outbox_module.MAX_ERROR_LENGTH
    assert outbox.snapshot()["obligations"]["304"]["context_reply"]["failure"][
        "error"
    ] == failed


def test_confirmed_and_not_required_transitions_are_final_and_idempotent(
    tmp_path: Path,
) -> None:
    outbox = HistoricalContextOutbox(tmp_path / "outbox.json")
    outbox.enqueue(
        "400",
        main_post_confirmed_epoch=4_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    assert outbox.next_attempt_number("400") == 1
    outbox.claim_attempt("400", started_epoch=4_000)
    confirmed = outbox.record_confirmed(
        "400",
        attempt_number=1,
        reply_post_id="900",
        confirmed_epoch=4_010,
    )
    assert confirmed["context_reply"]["state"] == CONFIRMED
    assert (
        outbox.record_confirmed(
            "400",
            attempt_number=1,
            reply_post_id="900",
            confirmed_epoch=4_010,
        )
        == confirmed
    )
    with pytest.raises(OutboxConflictError, match="not attemptable"):
        outbox.next_attempt_number("400")
    with pytest.raises(OutboxConflictError, match="durably claimed"):
        outbox.record_terminal_failure(
            "400",
            attempt_number=2,
            error="late contradictory failure",
            failed_epoch=4_020,
        )

    outbox.enqueue(
        "401",
        main_post_confirmed_epoch=4_001,
        quote_id="b" * 64,
        quote_text="A quotation without a usable packet.",
    )
    outbox.claim_attempt("401", started_epoch=4_001)
    not_required = outbox.mark_not_required(
        "401",
        reason="formatter found no eligible historical context",
        decided_epoch=4_002,
    )
    assert not_required["context_reply"]["state"] == NOT_REQUIRED
    assert (
        outbox.mark_not_required(
            "401",
            reason="formatter found no eligible historical context",
            decided_epoch=4_002,
        )
        == not_required
    )
    assert (
        outbox.enqueue(
            "401",
            main_post_confirmed_epoch=4_001,
            quote_id="b" * 64,
            quote_text="A quotation without a usable packet.",
        )
        == not_required
    )
    assert {item["parent_post_id"] for item in outbox.snapshot()["obligations"].values()} == {
        "400",
        "401",
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "parent_post_id": "500",
                "main_post_confirmed_epoch": 5_000,
                "quote_id": QUOTE_ID,
            },
            "provided together",
        ),
        (
            {
                "parent_post_id": "500",
                "main_post_confirmed_epoch": 5_000,
                "quote_id": QUOTE_ID,
                "quote_text": "   ",
            },
            "quote_text must be nonempty",
        ),
        (
            {
                "parent_post_id": "500",
                "main_post_confirmed_epoch": True,
                "quote_id": QUOTE_ID,
                "quote_text": QUOTE_TEXT,
            },
            "integer Unix epoch",
        ),
        (
            {
                "parent_post_id": "not-a-post",
                "main_post_confirmed_epoch": 5_000,
                "quote_id": QUOTE_ID,
                "quote_text": QUOTE_TEXT,
            },
            "digit post id",
        ),
    ],
)
def test_enqueue_rejects_invalid_input(
    tmp_path: Path,
    kwargs: dict,
    message: str,
) -> None:
    outbox = HistoricalContextOutbox(tmp_path / "outbox.json")
    with pytest.raises(ValueError, match=message):
        outbox.enqueue(**kwargs)
    assert not outbox.path.exists()


@pytest.mark.parametrize(
    "payload",
    [
        (
            '{"schema_version":1,"schema_version":1,'
            '"retry_policy":{},"obligations":{}}'
        ),
        '{"schema_version": NaN, "retry_policy": {}, "obligations": {}}',
        '{"schema_version": 1, "retry_policy": {}, "obligations": {}, "extra": 1}',
    ],
)
def test_strict_loader_rejects_duplicate_non_finite_and_unknown_fields(
    tmp_path: Path,
    payload: str,
) -> None:
    path = tmp_path / "outbox.json"
    path.write_text(payload, encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(OutboxValidationError):
        HistoricalContextOutbox(path).snapshot()

    assert path.read_bytes() == before


def test_strict_loader_rejects_tampered_schedule_and_policy_mismatch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "outbox.json"
    outbox = HistoricalContextOutbox(
        path,
        max_attempts=3,
        base_backoff_seconds=10,
        max_backoff_seconds=20,
    )
    outbox.enqueue(
        "600",
        main_post_confirmed_epoch=6_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    outbox.claim_attempt("600", started_epoch=6_000)
    outbox.record_retryable_failure(
        "600",
        attempt_number=1,
        error="temporary",
        failed_epoch=6_010,
    )

    with pytest.raises(OutboxPolicyMismatchError):
        HistoricalContextOutbox(path).snapshot()

    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["obligations"]["600"]["context_reply"]["next_attempt_epoch"] += 1
    outbox_module._atomic_write_json(path, tampered)
    with pytest.raises(OutboxValidationError, match="scheduling metadata"):
        outbox.snapshot()


@pytest.mark.parametrize(
    ("field_path", "value", "message"),
    [
        (("failure", "failed_epoch"), True, "failure failed_epoch"),
        (("backoff_seconds",), False, "scheduling metadata"),
        (("next_attempt_epoch",), True, "scheduling metadata"),
    ],
)
def test_strict_loader_rejects_boolean_retry_metadata(
    tmp_path: Path,
    field_path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    path = tmp_path / "outbox.json"
    outbox = HistoricalContextOutbox(
        path,
        max_attempts=3,
        base_backoff_seconds=10,
        max_backoff_seconds=20,
    )
    outbox.enqueue(
        "601",
        main_post_confirmed_epoch=6_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    outbox.claim_attempt("601", started_epoch=6_000)
    outbox.record_retryable_failure(
        "601",
        attempt_number=1,
        error="temporary",
        failed_epoch=6_010,
    )
    tampered = json.loads(path.read_text(encoding="utf-8"))
    target = tampered["obligations"]["601"]["context_reply"]
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = value
    outbox_module._atomic_write_json(path, tampered)

    with pytest.raises(OutboxValidationError, match=message):
        outbox.snapshot()


def test_atomic_write_replaces_same_directory_and_fsyncs_file_and_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "outbox.json"
    real_replace = os.replace
    real_fsync = os.fsync
    replacements: list[tuple[Path, Path]] = []
    fsync_calls: list[int] = []

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    def recording_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(outbox_module.os, "replace", recording_replace)
    monkeypatch.setattr(outbox_module.os, "fsync", recording_fsync)

    HistoricalContextOutbox(path).enqueue(
        "700",
        main_post_confirmed_epoch=7_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )

    assert len(replacements) == 1
    temporary, destination = replacements[0]
    assert temporary.parent == path.parent
    assert destination == path
    assert len(fsync_calls) == 2
    assert not temporary.exists()
    assert not list(tmp_path.glob(f".{path.name}.*.tmp"))
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_atomic_replace_failure_preserves_previous_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "outbox.json"
    outbox = HistoricalContextOutbox(path)
    outbox.enqueue(
        "700",
        main_post_confirmed_epoch=7_000,
        not_required_reason="existing terminal record",
    )
    before = path.read_bytes()
    monkeypatch.setattr(
        outbox_module.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        outbox.enqueue(
            "701",
            main_post_confirmed_epoch=7_001,
            quote_id=QUOTE_ID,
            quote_text=QUOTE_TEXT,
        )

    assert path.read_bytes() == before
    assert not list(tmp_path.glob(f".{path.name}.*.tmp"))


def test_directory_fsync_failure_is_retryable_without_duplicate_obligation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "outbox.json"
    outbox = HistoricalContextOutbox(path)
    real_fsync = os.fsync
    calls = {"count": 0}

    def fail_directory_fsync(descriptor: int) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("directory fsync failed")
        real_fsync(descriptor)

    monkeypatch.setattr(outbox_module.os, "fsync", fail_directory_fsync)
    with pytest.raises(OSError, match="directory fsync failed"):
        outbox.enqueue(
            "705",
            main_post_confirmed_epoch=7_005,
            quote_id=QUOTE_ID,
            quote_text=QUOTE_TEXT,
        )
    monkeypatch.setattr(outbox_module.os, "fsync", real_fsync)

    recovered = outbox.enqueue(
        "705",
        main_post_confirmed_epoch=7_005,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    assert recovered["parent_post_id"] == "705"
    assert set(outbox.snapshot()["obligations"]) == {"705"}


def test_terminal_retention_is_bounded_without_pruning_active_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outbox_module, "MAX_OUTBOX_OBLIGATIONS", 4)
    monkeypatch.setattr(outbox_module, "TERMINAL_RETENTION_LIMIT", 2)
    outbox = HistoricalContextOutbox(tmp_path / "outbox.json")
    for index in range(1, 4):
        parent_id = str(700 + index)
        outbox.enqueue(
            parent_id,
            main_post_confirmed_epoch=7_000 + index,
            not_required_reason="terminal retention fixture",
        )
    outbox.enqueue(
        "704",
        main_post_confirmed_epoch=7_004,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )

    obligations = outbox.snapshot()["obligations"]
    assert set(obligations) == {"702", "703", "704"}
    assert obligations["704"]["context_reply"]["state"] == CONTEXT_REPLY_PENDING
    assert outbox.snapshot()["retired_parent_post_id_floor"] == "701"
    with pytest.raises(OutboxConflictError, match="already retired"):
        outbox.enqueue(
            "701",
            main_post_confirmed_epoch=7_001,
            quote_id="d" * 64,
            quote_text="A stale receipt cannot recreate pruned terminal work.",
        )


def test_terminal_pruning_never_advances_floor_past_older_active_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outbox_module, "MAX_OUTBOX_OBLIGATIONS", 4)
    monkeypatch.setattr(outbox_module, "TERMINAL_RETENTION_LIMIT", 1)
    outbox = HistoricalContextOutbox(tmp_path / "outbox.json")
    outbox.enqueue(
        "700",
        main_post_confirmed_epoch=7_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    for index in range(1, 4):
        outbox.enqueue(
            str(700 + index),
            main_post_confirmed_epoch=7_000 + index,
            not_required_reason="newer terminal fixture",
        )

    # A newer terminal record cannot be represented by the scalar retirement
    # floor while the smaller active obligation remains.  Capacity therefore
    # fails closed instead of writing an outbox which its own validator rejects.
    with pytest.raises(OutboxCapacityError, match="full of active obligations"):
        outbox.verify_writable()
    snapshot = outbox.snapshot()
    assert set(snapshot["obligations"]) == {"700", "701", "702", "703"}
    assert snapshot["retired_parent_post_id_floor"] == "0"
    assert (
        snapshot["obligations"]["700"]["context_reply"]["state"]
        == CONTEXT_REPLY_PENDING
    )


def test_capacity_refuses_new_main_post_when_only_active_work_remains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outbox_module, "MAX_OUTBOX_OBLIGATIONS", 2)
    monkeypatch.setattr(outbox_module, "TERMINAL_RETENTION_LIMIT", 2)
    outbox = HistoricalContextOutbox(tmp_path / "outbox.json")
    for index in range(2):
        outbox.enqueue(
            str(800 + index),
            main_post_confirmed_epoch=8_000 + index,
            quote_id=chr(ord("a") + index) * 64,
            quote_text=f"Active context obligation {index}.",
        )

    with pytest.raises(OutboxCapacityError, match="full of active obligations"):
        outbox.verify_writable()
    with pytest.raises(OutboxCapacityError, match="full of active obligations"):
        outbox.enqueue(
            "802",
            main_post_confirmed_epoch=8_002,
            quote_id="c" * 64,
            quote_text="This obligation must not displace active work.",
        )
    assert set(outbox.snapshot()["obligations"]) == {"800", "801"}


def test_stale_lock_file_is_harmless_and_concurrent_enqueues_are_serialised(
    tmp_path: Path,
) -> None:
    path = tmp_path / "outbox.json"
    lock_path = tmp_path / "outbox.json.lock"
    lock_path.write_text("stale diagnostic marker\n", encoding="utf-8")

    def enqueue(index: int) -> str:
        parent_id = str(900 + index)
        HistoricalContextOutbox(path).enqueue(
            parent_id,
            main_post_confirmed_epoch=9_000 + index,
            not_required_reason="concurrent fixture",
        )
        return parent_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        expected = set(executor.map(enqueue, range(20)))

    snapshot = HistoricalContextOutbox(path).snapshot()
    assert set(snapshot["obligations"]) == expected
    assert lock_path.read_text(encoding="utf-8") == "stale diagnostic marker\n"


def test_worker_lock_serialises_the_complete_remote_attempt(
    tmp_path: Path,
) -> None:
    outbox = HistoricalContextOutbox(tmp_path / "outbox.json")

    def competing_worker() -> str:
        with pytest.raises(OutboxWorkerBusy):
            with HistoricalContextOutbox(outbox.path).worker_lock():
                pytest.fail("a second context worker acquired the live lease")
        return "blocked"

    with outbox.worker_lock():
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(competing_worker).result(timeout=5) == "blocked"

    with HistoricalContextOutbox(outbox.path).worker_lock():
        pass


def test_worker_lock_excludes_a_second_process(
    tmp_path: Path,
) -> None:
    outbox = HistoricalContextOutbox(tmp_path / "outbox.json")
    context = multiprocessing.get_context("fork")
    result_queue = context.Queue()
    with outbox.worker_lock():
        process = context.Process(
            target=_attempt_worker_lock,
            args=(str(outbox.path), result_queue),
        )
        process.start()
        process.join(timeout=5)
        assert process.exitcode == 0
        assert result_queue.get(timeout=1) == "busy"


@pytest.mark.parametrize(
    "unsafe_kind",
    ("symlink", "hardlink", "public_mode", "wrong_owner"),
)
def test_outbox_authority_requires_owned_private_single_link_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_kind: str,
) -> None:
    path = tmp_path / "outbox.json"
    outbox = HistoricalContextOutbox(path)
    outbox.enqueue(
        "1000",
        main_post_confirmed_epoch=10_000,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    if unsafe_kind == "wrong_owner":
        real_lstat = os.lstat

        def wrong_owner_lstat(candidate):
            metadata = real_lstat(candidate)
            if Path(candidate) != path:
                return metadata
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_nlink=metadata.st_nlink,
                st_uid=metadata.st_uid + 1,
                st_size=metadata.st_size,
            )

        monkeypatch.setattr(outbox_module.os, "lstat", wrong_owner_lstat)
    elif unsafe_kind == "public_mode":
        path.chmod(0o644)
    else:
        original = tmp_path / "outbox-original.json"
        path.replace(original)
        if unsafe_kind == "symlink":
            path.symlink_to(original)
        else:
            os.link(original, path)

    with pytest.raises(OutboxValidationError, match="unsafe filesystem metadata"):
        outbox.snapshot()


def test_outbox_authority_rejects_noncanonical_json(tmp_path: Path) -> None:
    path = tmp_path / "outbox.json"
    outbox = HistoricalContextOutbox(path)
    outbox.enqueue(
        "1001",
        main_post_confirmed_epoch=10_001,
        quote_id=QUOTE_ID,
        quote_text=QUOTE_TEXT,
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(OutboxValidationError, match="not canonical JSON"):
        outbox.snapshot()


@pytest.mark.parametrize("lock_suffix", (".lock", ".worker.lock"))
def test_outbox_lock_paths_reject_symlinks_without_touching_target(
    tmp_path: Path,
    lock_suffix: str,
) -> None:
    path = tmp_path / "outbox.json"
    target = tmp_path / "unrelated.txt"
    target.write_bytes(b"unrelated lock target\n")
    target.chmod(0o644)
    lock_path = path.with_name(path.name + lock_suffix)
    lock_path.symlink_to(target)
    outbox = HistoricalContextOutbox(path)

    with pytest.raises(OutboxValidationError, match="lock pathname is unsafe"):
        if lock_suffix == ".lock":
            outbox.snapshot()
        else:
            with outbox.worker_lock():
                pytest.fail("unsafe worker lock was accepted")

    assert target.read_bytes() == b"unrelated lock target\n"
    assert target.stat().st_mode & 0o777 == 0o644


def test_outbox_writer_and_reader_share_the_same_byte_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "outbox.json"
    assert outbox_module.MAX_OUTBOX_BYTES == 128 * 1024 * 1024
    monkeypatch.setattr(outbox_module, "MAX_OUTBOX_BYTES", 128)

    with pytest.raises(OutboxValidationError, match="writer byte domain"):
        HistoricalContextOutbox(path).enqueue(
            "1002",
            main_post_confirmed_epoch=10_002,
            quote_id=QUOTE_ID,
            quote_text=QUOTE_TEXT,
        )
    assert not path.exists()

    path.write_bytes(b"x" * 129)
    path.chmod(0o600)
    with pytest.raises(OutboxValidationError, match="unsafe filesystem metadata"):
        HistoricalContextOutbox(path).snapshot()
