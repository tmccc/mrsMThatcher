"""Literal-process regressions for restart-persistent remote-write barriers."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from tools import reconcile_remote_write_safety_marker as reconcile


ROOT = Path(__file__).resolve().parents[1]
DRIVER = (
    ROOT
    / "tests"
    / "helpers"
    / "remote_write_safety_second_restart_driver.py"
)
OUTPUT_PREFIX = "REMOTE_WRITE_SECOND_RESTART_JSON="
MARKER_BASENAME = "ambiguous_post_outcome.json"
RESTART_BARRIER_BASENAME = "ambiguous_post_outcome.restart_barrier.json"
STOPPED_DAEMON_PID = 2_147_483_647
MARKER_VALUE = {
    "made_with_ai": False,
    "media_ids": [],
    "outcome": "ambiguous_remote_post",
    "recorded_at_epoch": 1_800_000_000,
    "reply_to_id": "",
    "schema_version": 1,
    "text_sha256": hashlib.sha256(
        b"Offline synthetic ambiguous post"
    ).hexdigest(),
}
MARKER_BYTES = (
    json.dumps(
        MARKER_VALUE,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    + "\n"
).encode("utf-8")
MARKER_SHA256 = hashlib.sha256(MARKER_BYTES).hexdigest()
NEW_MARKER_VALUE = {
    "made_with_ai": False,
    "media_ids": [],
    "outcome": "ambiguous_remote_post",
    "recorded_at_epoch": 1_800_000_000,
    "reply_to_id": "",
    "schema_version": 1,
    "text_sha256": hashlib.sha256(
        b"Offline successor-first ambiguous post"
    ).hexdigest(),
}
NEW_MARKER_BYTES = (
    json.dumps(
        NEW_MARKER_VALUE,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    + "\n"
).encode("utf-8")


def _fsync_directory(path: Path) -> None:
    """Synchronise one test-owned directory."""

    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_marker(state_directory: Path) -> Path:
    """Create and synchronise the sole initial restart barrier."""

    marker = state_directory / MARKER_BASENAME
    descriptor = os.open(
        marker,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    try:
        assert os.write(descriptor, MARKER_BYTES) == len(MARKER_BYTES)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(state_directory)
    return marker


def _child_environment(state_directory: Path) -> dict[str, str]:
    """Return a test-only environment with inert provider endpoints."""

    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONSTARTUP", None)
    environment.update(
        {
            "MRS_BASE_DIR": str(state_directory),
            "MRS_LOG_FILE": str(state_directory / "literal-process.log"),
            "MRS_TEST_MODE": "1",
            "PYTHONNOUSERSITE": "1",
            "X_ACCESS_SECRET": "dummy",
            "X_ACCESS_TOKEN": "dummy",
            "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
            "XAI_API_KEY": "dummy",
            "X_API_BASE_URL": "http://127.0.0.1:9",
            "X_BEARER_TOKEN": "dummy",
            "X_CONSUMER_KEY": "dummy",
            "X_CONSUMER_SECRET": "dummy",
            "X_MY_USER_ID": "12345",
            "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
        }
    )
    return environment


def _run_child(
    mode: str,
    state_directory: Path,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    """Run one literal interpreter and parse its sole structured result."""

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(DRIVER),
            mode,
            str(ROOT),
            str(state_directory),
        ],
        cwd=ROOT,
        env=_child_environment(state_directory),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    records = [
        json.loads(line.removeprefix(OUTPUT_PREFIX))
        for line in completed.stdout.splitlines()
        if line.startswith(OUTPUT_PREFIX)
    ]
    assert len(records) == 1, (
        f"expected one child record, got {len(records)}; "
        f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
    )
    return completed, records[0]


def _source_sha256() -> str:
    """Return the exact candidate runtime source hash expected by children."""

    return hashlib.sha256((ROOT / "mrsMThatcher2.py").read_bytes()).hexdigest()


def _active_receipts(state_directory: Path) -> dict[str, bool]:
    """Report receipt paths which could mask the restart-barrier result."""

    return {
        "confirmed_reply": (
            state_directory / "confirmed_reply_receipt.json"
        ).exists(),
        "historical_context": (
            state_directory / "historical_context_reply_receipt.json"
        ).exists(),
        "meme": (state_directory / "meme_post_receipt.json").exists(),
        "regular": (state_directory / "regular_post_receipt.json").exists(),
    }


def test_literal_second_process_blocks_all_remote_lanes_after_marker_loss_and_hard_exit(
    tmp_path: Path,
) -> None:
    """A second interpreter must inherit a durable barrier, not process memory."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    marker = _write_marker(state_directory)
    successor = state_directory / RESTART_BARRIER_BASENAME

    first, first_record = _run_child("fault", state_directory)
    assert first.returncode == 73, first.stderr
    assert first_record["phase"] == "fault_and_hard_exit"
    assert first_record["source_sha256"] == _source_sha256()

    second, second_record = _run_child("blocked", state_directory)
    assert second.returncode == 0, second.stderr
    assert second_record["phase"] == "blocked_second_process"
    assert second_record["source_sha256"] == _source_sha256()

    expected_direct = {
        "create_post": "blocked",
        "media_upload": "blocked",
        "provider_request": "blocked",
        "remote_operation_preflight": "blocked",
        "shared_barrier": "blocked",
        "x_bearer_request": "blocked",
        "x_request": "blocked",
    }
    expected_receipts = {
        "confirmed_reply": False,
        "historical_context": False,
        "meme": False,
        "regular": False,
    }
    successor_metadata = os.lstat(successor) if successor.exists() else None

    assert first_record == {
        "initial_durability_uncertain": False,
        "initial_remote_seen": False,
        "marker_observed_before_fault": True,
        "marker_removed": True,
        "phase": "fault_and_hard_exit",
        "real_parent_fsync_completed": True,
        "receipts_present": expected_receipts,
        "source_sha256": _source_sha256(),
        "successor_nlink_after": 1,
        "successor_nlink_before": 2,
        "successor_ordinary_after": True,
        "successor_present_after": True,
        "successor_present_before": True,
        "successor_same_inode_before": True,
    }
    assert not marker.exists()
    assert successor_metadata is not None
    assert stat.S_ISREG(successor_metadata.st_mode)
    assert successor_metadata.st_nlink == 1
    assert successor.read_bytes() == MARKER_BYTES
    assert _active_receipts(state_directory) == expected_receipts
    assert second_record["initial"] == {
        "durability_uncertain": False,
        "marker_present": False,
        "receipts_present": expected_receipts,
        "remote_seen": False,
        "successor_present": True,
    }
    assert second_record["blocking_before_direct"] is True
    assert second_record["direct_results"] == expected_direct
    assert second_record["transport_sentinel_calls"] == []
    assert second_record["scheduler_sleep_calls"] == 3
    assert second_record["scheduler_entries"] == []
    assert second_record["blocking_after_scheduler"] is True


def test_literal_new_barrier_survives_hard_exit_before_legacy_link(
    tmp_path: Path,
) -> None:
    """A new incident commits its successor before adding the legacy name."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    marker = state_directory / MARKER_BASENAME
    successor = state_directory / RESTART_BARRIER_BASENAME

    first, first_record = _run_child("new_fault", state_directory)
    assert first.returncode == 75, first.stderr
    assert first_record == {
        "legacy_marker_present": False,
        "phase": "new_barrier_fault_before_legacy_link",
        "receipts_present": {
            "confirmed_reply": False,
            "historical_context": False,
            "meme": False,
            "regular": False,
        },
        "source_sha256": _source_sha256(),
        "successor_nlink": 1,
        "successor_ordinary": True,
        "successor_present": True,
    }
    assert not marker.exists()
    assert successor.read_bytes() == NEW_MARKER_BYTES
    assert successor.stat().st_nlink == 1

    second, second_record = _run_child("blocked", state_directory)
    assert second.returncode == 0, second.stderr
    assert second_record["initial"]["marker_present"] is False
    assert second_record["initial"]["successor_present"] is True
    assert second_record["blocking_before_direct"] is True
    assert set(second_record["direct_results"].values()) == {"blocked"}
    assert second_record["transport_sentinel_calls"] == []
    assert second_record["scheduler_entries"] == []
    assert second_record["blocking_after_scheduler"] is True


def test_literal_legacy_prelink_loss_remains_blocked_by_real_sending_receipt(
    tmp_path: Path,
) -> None:
    """A legacy migration race cannot repeat its scheduled X transaction."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    marker = _write_marker(state_directory)
    successor = state_directory / RESTART_BARRIER_BASENAME

    first, first_record = _run_child("legacy_receipt_fault", state_directory)
    assert first.returncode == 77, first.stderr
    assert first_record["durable_marker"] is False
    assert first_record["legacy_marker_present"] is False
    assert first_record["successor_present"] is False
    assert first_record["remote_seen"] is True
    assert first_record["uncertain"] is True
    assert first_record["receipts_present"]["regular"] is True
    assert not marker.exists()
    assert not successor.exists()

    second, second_record = _run_child("receipt_blocked", state_directory)
    assert second.returncode == 0, second.stderr
    assert second_record["phase"] == "receipt_backed_second_process"
    assert second_record["regular_receipt_status"] == "sending"
    assert second_record["blocking_before_direct"] is True
    assert second_record["runtime_results"] == {
        "create_post": "blocked",
        "media_upload": "blocked",
        "shared_barrier": "blocked",
    }
    assert second_record["transport_sentinel_calls"] == []
    assert second_record["scheduler_sleep_calls"] == 3
    assert second_record["scheduler_entries"] == []
    assert second_record["blocking_after_scheduler"] is True


def test_literal_clean_process_allows_preflight_after_supported_offline_reconciliation(
    tmp_path: Path,
) -> None:
    """Supported offline reconciliation must reopen a later clean interpreter."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    marker = _write_marker(state_directory)
    successor = state_directory / RESTART_BARRIER_BASENAME

    first, first_record = _run_child("fault", state_directory)
    assert first.returncode == 73, first.stderr
    assert first_record["marker_removed"] is True
    assert not marker.exists()
    assert successor.read_bytes() == MARKER_BYTES
    assert successor.stat().st_nlink == 1

    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    result = reconcile.reconcile_marker_offline(
        project_root=state_directory,
        expected_marker_sha256=MARKER_SHA256,
        reconciliation_reference="review-IR-DEBC079-01",
        now=lambda: 1_800_000_000,
    )

    archive = state_directory / result.archive_path
    receipt = state_directory / result.receipt_path
    assert not marker.exists()
    assert not successor.exists()
    assert archive.read_bytes() == MARKER_BYTES
    assert archive.stat().st_nlink == 1
    assert stat.S_IMODE(archive.stat().st_mode) == 0o400
    assert receipt.exists()
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o400

    clean, clean_record = _run_child("clean", state_directory)
    assert clean.returncode == 0, clean.stderr
    assert clean_record["phase"] == "clean_process"
    assert clean_record["source_sha256"] == _source_sha256()
    assert clean_record["initial"] == {
        "durability_uncertain": False,
        "marker_present": False,
        "receipts_present": {
            "confirmed_reply": False,
            "historical_context": False,
            "meme": False,
            "regular": False,
        },
        "remote_seen": False,
        "successor_present": False,
    }
    assert clean_record["blocking_before_direct"] is False
    assert clean_record["direct_results"] == {
        "create_post": "local_transport_reached",
        "media_upload": "local_transport_reached",
        "provider_request": "local_transport_reached",
        "remote_operation_preflight": "returned",
        "shared_barrier": "returned",
        "x_bearer_request": "local_transport_reached",
        "x_request": "local_transport_reached",
    }
    assert clean_record["transport_sentinel_calls"] == [
        "requests.request",
        "requests.request",
        "requests.request",
        "requests.request",
        "requests.post",
    ]
    assert clean_record["scheduler_sleep_calls"] == 3
    assert clean_record["scheduler_entries"] == [
        "historical_context",
        "reply",
        "quote",
        "meme",
        "historical_context",
        "reply",
        "quote",
        "meme",
        "historical_context",
        "reply",
        "quote",
        "meme",
    ]
    assert clean_record["blocking_after_scheduler"] is False
