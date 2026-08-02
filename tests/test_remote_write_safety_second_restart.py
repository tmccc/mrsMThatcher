"""Literal-process regressions for restart-persistent remote-write barriers."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import exact_receipt_retirement as retirement
import remote_write_safety_protocol as protocol
from tests.helpers.protocol_activation import (
    create_test_protocol_activation,
    initialise_test_retirement_ledgers,
)
from tools import activate_remote_write_safety_protocol as activate
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
CONTEXT_SENDING_RECEIPT = {
    "attempt_number": 1,
    "lifecycle_state": "sending",
    "parent_post_id": "111",
    "quote_id": "a" * 64,
    "reply_epoch": 1_800_000_000,
    "reply_text": "Context — exact transaction owner.",
    "schema_version": 1,
    "started_at": "2026-07-31T09:00:00Z",
}


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


def _write_legacy_marker(state_directory: Path) -> Path:
    """Create and synchronise one legacy-only initial barrier."""

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


def _write_barrier_pair(state_directory: Path) -> tuple[Path, Path]:
    """Create the exact two-link active barrier used by the new protocol."""

    marker = _write_legacy_marker(state_directory)
    successor = state_directory / RESTART_BARRIER_BASENAME
    os.link(marker, successor, follow_symlinks=False)
    _fsync_directory(state_directory)
    return marker, successor


def _write_context_sending_receipt(state_directory: Path) -> Path:
    """Create and synchronise one exact historical-context sending receipt."""

    receipt = state_directory / "historical_context_reply_receipt.json"
    payload = (
        json.dumps(
            CONTEXT_SENDING_RECEIPT,
            indent=2,
            sort_keys=True,
            allow_nan=False,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        receipt,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    try:
        assert os.write(descriptor, payload) == len(payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(state_directory)
    return receipt


def _activate_protocol(state_directory: Path) -> Path:
    """Create the exact test-owned protocol activation sentinel."""

    path = state_directory / activate.ACTIVATION_BASENAME
    create_test_protocol_activation(path)
    return path


def _write_established_activation_state(state_directory: Path) -> None:
    """Create the direct regular files which bind a real existing install."""

    for basename in activate.ESTABLISHED_STATE_BASENAMES:
        (state_directory / basename).write_text("{}\n", encoding="utf-8")


def _activation_transaction_namespace_snapshot(
    state_directory: Path,
) -> dict[str, tuple[tuple[int, ...], bytes] | None]:
    """Snapshot activation and permanent-ledger entries without following links."""

    paths = (
        state_directory / activate.INSTALLATION_IN_PROGRESS_BASENAME,
        state_directory / protocol.ACTIVATION_BASENAME,
        state_directory / protocol.ACTIVATION_AUDIT_BASENAME,
        state_directory / protocol.LEGACY_ACTIVATION_BASENAME,
        state_directory / protocol.LEGACY_ACTIVATION_AUDIT_BASENAME,
        *(
            path
            for receipt_basename in activate.RECEIPT_BASENAMES
            for path in retirement.retirement_ledger_paths(
                state_directory / receipt_basename
            )
        ),
    )
    snapshot: dict[str, tuple[tuple[int, ...], bytes] | None] = {}
    for path in paths:
        try:
            identity = os.lstat(path)
        except FileNotFoundError:
            snapshot[path.name] = None
            continue
        snapshot[path.name] = (
            (
                int(identity.st_dev),
                int(identity.st_ino),
                int(identity.st_mode),
                int(identity.st_nlink),
                int(identity.st_uid),
                int(identity.st_size),
                int(identity.st_ctime_ns),
                int(identity.st_mtime_ns),
            ),
            path.read_bytes(),
        )
    return snapshot


def _write_legacy_protocol_activation(state_directory: Path) -> tuple[Path, Path]:
    """Publish the exact v1 pair which a stopped migration must retire."""

    identity = os.stat(state_directory, follow_symlinks=False)
    audit_bytes = protocol.build_legacy_established_install_activation_audit_bytes(
        project_device=int(identity.st_dev),
        project_inode=int(identity.st_ino),
        clean_state_attestation_sha256="3" * 64,
        clean_state_attestation_size=1,
        activator_cli_sha256="4" * 64,
        reconciliation_reference="isolated-v1-migration-fixture",
    )
    audit = state_directory / protocol.LEGACY_ACTIVATION_AUDIT_BASENAME
    sentinel = state_directory / protocol.LEGACY_ACTIVATION_BASENAME
    audit.write_bytes(audit_bytes)
    audit.chmod(protocol.ACTIVATION_AUDIT_MODE)
    sentinel.write_bytes(protocol.LEGACY_ACTIVATION_BYTES)
    sentinel.chmod(protocol.ACTIVATION_MODE)
    for path in (audit, sentinel):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    _fsync_directory(state_directory)
    return sentinel, audit


def _write_pre_ledger_protocol_activation(
    state_directory: Path,
) -> tuple[Path, Path]:
    """Publish the exact schema-2 v2 pair accepted only by the migrator."""

    identity = os.stat(state_directory, follow_symlinks=False)
    audit_bytes = (
        protocol.build_pre_ledger_established_install_activation_audit_bytes(
            project_device=int(identity.st_dev),
            project_inode=int(identity.st_ino),
            clean_state_attestation_sha256="3" * 64,
            clean_state_attestation_size=1,
            activator_cli_sha256="4" * 64,
            reconciliation_reference="isolated-pre-ledger-v2-migration-fixture",
        )
    )
    audit = state_directory / protocol.ACTIVATION_AUDIT_BASENAME
    sentinel = state_directory / protocol.ACTIVATION_BASENAME
    audit.write_bytes(audit_bytes)
    audit.chmod(protocol.ACTIVATION_AUDIT_MODE)
    sentinel.write_bytes(protocol.ACTIVATION_BYTES)
    sentinel.chmod(protocol.ACTIVATION_MODE)
    for path in (audit, sentinel):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    _fsync_directory(state_directory)
    return sentinel, audit


def _activation_kwargs(state_directory: Path) -> dict[str, object]:
    """Bind the API to exact project and external-attestation identities."""

    identity = os.stat(state_directory, follow_symlinks=False)
    cli_sha256 = activate._stable_cli_sha256()
    attestation_bytes = activate.build_clean_state_attestation_bytes(
        project_root=state_directory,
        project_device=int(identity.st_dev),
        project_inode=int(identity.st_ino),
        activator_cli_sha256=cli_sha256,
        reconciliation_reference="test-reviewed-clean-state",
    )
    attestation = state_directory.parent / (
        f"{state_directory.name}.clean-state-attestation.json"
    )
    if attestation.exists():
        assert attestation.read_bytes() == attestation_bytes
    else:
        descriptor = os.open(
            attestation,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            assert os.write(descriptor, attestation_bytes) == len(attestation_bytes)
            os.fsync(descriptor)
            os.fchmod(descriptor, activate.CLEAN_STATE_ATTESTATION_MODE)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(attestation.parent)
    return {
        "project_root": state_directory,
        "expected_project_root": state_directory.resolve(strict=True),
        "expected_project_device": int(identity.st_dev),
        "expected_project_inode": int(identity.st_ino),
        "supervisor_stopped_confirmed": True,
        "clean_state_attestation_path": attestation,
        "expected_clean_state_attestation_sha256": hashlib.sha256(
            attestation_bytes
        ).hexdigest(),
    }


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
    marker, successor = _write_barrier_pair(state_directory)
    _activate_protocol(state_directory)

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
        "protocol_active": True,
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
    _activate_protocol(state_directory)

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


def test_literal_protocol_absence_survives_legacy_loss_and_second_process(
    tmp_path: Path,
) -> None:
    """Protocol inactivity remains the sole barrier after marker and process loss."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    marker = _write_legacy_marker(state_directory)
    successor = state_directory / RESTART_BARRIER_BASENAME

    first, first_record = _run_child("inactive_legacy_loss", state_directory)
    assert first.returncode == 77, first.stderr
    assert first_record == {
        "blocked_before_loss": True,
        "legacy_marker_present": False,
        "phase": "inactive_protocol_legacy_loss",
        "protocol_active": False,
        "receipts_present": {
            "confirmed_reply": False,
            "historical_context": False,
            "meme": False,
            "regular": False,
        },
        "source_sha256": _source_sha256(),
        "successor_present": False,
    }
    assert not marker.exists()
    assert not successor.exists()
    assert _active_receipts(state_directory) == {
        "confirmed_reply": False,
        "historical_context": False,
        "meme": False,
        "regular": False,
    }

    second, second_record = _run_child("blocked", state_directory)
    assert second.returncode == 0, second.stderr
    assert second_record["phase"] == "blocked_second_process"
    assert second_record["initial"]["protocol_active"] is False
    assert second_record["blocking_before_direct"] is True
    assert set(second_record["direct_results"].values()) == {"blocked"}
    assert second_record["transport_sentinel_calls"] == []
    assert second_record["scheduler_sleep_calls"] == 3
    assert second_record["scheduler_entries"] == []
    assert second_record["blocking_after_scheduler"] is True


def test_literal_context_sending_receipt_blocks_every_unrelated_remote_lane(
    tmp_path: Path,
) -> None:
    """A context transaction may not leave unrelated writes or schedulers open."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _activate_protocol(state_directory)
    receipt = _write_context_sending_receipt(state_directory)
    receipt_bytes = receipt.read_bytes()

    child, record = _run_child("blocked", state_directory)
    assert child.returncode == 0, child.stderr
    assert record["phase"] == "blocked_second_process"
    assert record["initial"] == {
        "durability_uncertain": False,
        "marker_present": False,
        "protocol_active": True,
        "receipts_present": {
            "confirmed_reply": False,
            "historical_context": True,
            "meme": False,
            "regular": False,
        },
        "remote_seen": False,
        "successor_present": False,
    }
    assert record["blocking_before_direct"] is True
    assert record["direct_results"] == {
        "create_post": "blocked",
        "media_upload": "blocked",
        "provider_request": "blocked",
        "remote_operation_preflight": "blocked",
        "shared_barrier": "blocked",
        "x_bearer_request": "blocked",
        "x_request": "blocked",
    }
    assert record["transport_sentinel_calls"] == []
    assert record["scheduler_sleep_calls"] == 0
    assert record["scheduler_blocked_exception"] == "AmbiguousContextReplyOutcome"
    assert record["scheduler_entries"] == []
    assert record["blocking_after_scheduler"] is True
    assert receipt.read_bytes() == receipt_bytes


def test_offline_activation_refuses_legacy_then_opens_after_reconciliation(
    tmp_path: Path,
) -> None:
    """Only stopped reconciliation followed by activation opens a clean install."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    marker = _write_legacy_marker(state_directory)
    successor = state_directory / RESTART_BARRIER_BASENAME

    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    with pytest.raises(
        activate.ProtocolActivationRefused,
        match="ambiguous_post_outcome.json",
    ):
        activate.activate_protocol_offline(**_activation_kwargs(state_directory))
    assert marker.read_bytes() == MARKER_BYTES
    assert not successor.exists()

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

    activation = activate.activate_protocol_offline(
        **_activation_kwargs(state_directory)
    )
    assert activation.activation_sha256 == hashlib.sha256(
        activate.ACTIVATION_BYTES
    ).hexdigest()
    assert (
        state_directory / activate.ACTIVATION_BASENAME
    ).read_bytes() == activate.ACTIVATION_BYTES
    assert activation.activation_reused_existing is False
    assert activation.activation_kind == protocol.ESTABLISHED_INSTALL_ACTIVATION_KIND
    activation_audit = state_directory / protocol.ACTIVATION_AUDIT_BASENAME
    activation_audit_value = json.loads(
        activation_audit.read_text(encoding="utf-8")
    )
    assert activation_audit_value["activation_kind"] == (
        protocol.ESTABLISHED_INSTALL_ACTIVATION_KIND
    )
    assert activation_audit_value["clean_state_attestation_sha256"] == (
        activation.clean_state_attestation_sha256
    )
    assert activation_audit_value["activator_cli_sha256"] == (
        activation.activator_cli_sha256
    )
    assert activation_audit_value["operator_clean_state_claim_locally_proven"] is False
    assert activation.activation_audit_sha256 == hashlib.sha256(
        activation_audit.read_bytes()
    ).hexdigest()
    assert activation.activation_audit_durable_before_sentinel is True
    assert activation.external_operator_attestation_used is True
    assert activation.operator_clean_state_claim_locally_proven is False
    assert activation.supervisor_stopped_precondition_declared is True
    assert activation.supervisor_stopped_locally_proved is False
    assert activation.rollback_to_protocol_unaware_runtime_prohibited is True

    repeated = activate.activate_protocol_offline(
        **_activation_kwargs(state_directory)
    )
    assert repeated.activation_reused_existing is True
    assert repeated.activation_device == activation.activation_device
    assert repeated.activation_inode == activation.activation_inode

    clean, clean_record = _run_child("clean", state_directory)
    assert clean.returncode == 0, clean.stderr
    assert clean_record["phase"] == "clean_process"
    assert clean_record["source_sha256"] == _source_sha256()
    assert clean_record["initial"] == {
        "durability_uncertain": False,
        "marker_present": False,
        "protocol_active": True,
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
        "create_post": "blocked",
        "media_upload": "local_transport_reached",
        "provider_request": "local_transport_reached",
        "remote_operation_preflight": "returned",
        "shared_barrier": "returned",
        "x_bearer_request": "blocked",
        "x_request": "blocked",
    }
    assert clean_record["transport_sentinel_calls"] == [
        "requests.post",
        "requests.request",
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


def test_stopped_v1_to_v2_migration_disables_pre_v2_runtime(
    tmp_path: Path,
) -> None:
    """A v2 deployment cannot leave the old v1 permission identity usable."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    _write_legacy_protocol_activation(state_directory)

    before_fd = os.open(
        state_directory,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        legacy = protocol._inspect_legacy_protocol_activation_at(before_fd)
    finally:
        os.close(before_fd)
    assert legacy.sha256 == hashlib.sha256(
        protocol.LEGACY_ACTIVATION_BYTES
    ).hexdigest()

    result = activate.activate_protocol_offline(
        **_activation_kwargs(state_directory)
    )

    assert result.protocol_version == 2
    assert result.activation_migrated_from_protocol_version == 1
    assert result.legacy_activation_namespace_absent is True
    assert result.refused_state_basenames == activate.REFUSED_STATE_BASENAMES
    assert result.refused_state_prefixes == activate.REFUSED_STATE_PREFIXES
    assert result.refused_state_inventory_sha256 == (
        activate._refused_state_inventory_sha256()
    )
    assert not os.path.lexists(
        state_directory / protocol.LEGACY_ACTIVATION_BASENAME
    )
    assert not os.path.lexists(
        state_directory / protocol.LEGACY_ACTIVATION_AUDIT_BASENAME
    )
    assert protocol.inspect_protocol_activation(
        state_directory / protocol.ACTIVATION_BASENAME
    ).sha256 == hashlib.sha256(protocol.ACTIVATION_BYTES).hexdigest()
    # This is the exact namespace a pre-v2 runtime inspected.  It can no
    # longer obtain its v1 permission sentinel after migration.
    legacy_fd = os.open(
        state_directory,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        with pytest.raises(protocol.ProtocolActivationError, match="missing"):
            protocol._inspect_legacy_protocol_activation_at(legacy_fd)
    finally:
        os.close(legacy_fd)


def test_runtime_rejects_pre_ledger_v2_activation_pair(tmp_path: Path) -> None:
    """Only the stopped migrator may accept the schema-2 v2 generation."""

    _write_pre_ledger_protocol_activation(tmp_path)
    with pytest.raises(protocol.ProtocolActivationError):
        protocol.inspect_protocol_activation(
            tmp_path / protocol.ACTIVATION_BASENAME
        )


@pytest.mark.parametrize(
    "activation_state",
    (
        "first_activation",
        "legacy",
        "legacy_audit_only",
        "pre_ledger",
        "current",
        "current_audit_only",
    ),
)
def test_activation_refuses_interrupted_initialisation_before_any_mutation(
    tmp_path: Path,
    activation_state: str,
) -> None:
    """The first-install sentinel excludes every activation generation."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )

    if activation_state in {"legacy", "legacy_audit_only"}:
        legacy_sentinel, _legacy_audit = _write_legacy_protocol_activation(
            state_directory
        )
        if activation_state == "legacy_audit_only":
            legacy_sentinel.unlink()
            _fsync_directory(state_directory)
    elif activation_state == "pre_ledger":
        _write_pre_ledger_protocol_activation(state_directory)
    elif activation_state in {"current", "current_audit_only"}:
        activate.activate_protocol_offline(
            **_activation_kwargs(state_directory)
        )
        if activation_state == "current_audit_only":
            (state_directory / protocol.ACTIVATION_BASENAME).unlink()
            _fsync_directory(state_directory)

    initialising = state_directory / activate.INSTALLATION_IN_PROGRESS_BASENAME
    initialising.write_text(
        '{"schema_version":1,"started_at_epoch":1,"state":"initialising"}\n',
        encoding="utf-8",
    )
    _fsync_directory(state_directory)
    before = _activation_transaction_namespace_snapshot(state_directory)

    with pytest.raises(
        activate.ProtocolActivationRefused,
        match=activate.INSTALLATION_IN_PROGRESS_BASENAME,
    ):
        activate.activate_protocol_offline(
            **_activation_kwargs(state_directory)
        )

    assert initialising.is_file()
    assert _activation_transaction_namespace_snapshot(state_directory) == before


@pytest.mark.parametrize("audit_only", (False, True))
def test_stopped_pre_ledger_v2_migration_creates_bound_genesis_ledgers(
    tmp_path: Path,
    audit_only: bool,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    sentinel, old_audit = _write_pre_ledger_protocol_activation(state_directory)
    old_audit_bytes = old_audit.read_bytes()
    if audit_only:
        sentinel.unlink()
        _fsync_directory(state_directory)

    result = activate.activate_protocol_offline(
        **_activation_kwargs(state_directory)
    )

    assert result.activation_migrated_from_protocol_version == 2
    assert result.activation_migrated_from_audit_schema_version == 2
    assert result.schema_version == 3
    assert result.retirement_ledger_basenames == (
        activate.RECEIPT_RETIREMENT_LEDGER_BASENAMES
    )
    assert old_audit.exists()
    assert old_audit.read_bytes() != old_audit_bytes
    assert json.loads(old_audit.read_bytes())["schema_version"] == 3
    receipt_paths = tuple(
        state_directory / name for name in activate.RECEIPT_BASENAMES
    )
    assert result.retirement_ledger_contract_sha256 == (
        retirement.retirement_ledger_contract_sha256(receipt_paths)
    )
    assert result.retirement_ledger_initial_inventory_sha256 == (
        retirement.retirement_ledger_inventory_sha256(receipt_paths)
    )
    for receipt_path in receipt_paths:
        inspection = retirement.inspect_retirement_ledger(receipt_path)
        assert (inspection.valid, inspection.blocking) == (True, False)
        assert (inspection.state, inspection.sequence) == ("idle", 0)


def test_pre_ledger_migration_keeps_old_audit_until_all_ledgers_are_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    old_sentinel, old_audit = _write_pre_ledger_protocol_activation(
        state_directory
    )
    original = activate.initialise_retirement_ledger
    calls = 0

    def fail_during_ledger_creation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected ledger migration interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        activate,
        "initialise_retirement_ledger",
        fail_during_ledger_creation,
    )
    with pytest.raises(
        activate.ProtocolActivationRefused,
        match="offline protocol activation failed",
    ):
        activate.activate_protocol_offline(**_activation_kwargs(state_directory))

    assert not old_sentinel.exists()
    assert old_audit.exists()
    assert not os.path.lexists(state_directory / protocol.ACTIVATION_BASENAME)
    monkeypatch.setattr(activate, "initialise_retirement_ledger", original)
    result = activate.activate_protocol_offline(
        **_activation_kwargs(state_directory)
    )
    assert result.activation_migrated_from_audit_schema_version == 2


@pytest.mark.parametrize("audit_only", (False, True))
def test_current_activation_never_recreates_a_missing_required_ledger(
    tmp_path: Path,
    audit_only: bool,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    activate.activate_protocol_offline(**_activation_kwargs(state_directory))
    if audit_only:
        (state_directory / protocol.ACTIVATION_BASENAME).unlink()
        _fsync_directory(state_directory)
    missing, _exchange = retirement.retirement_ledger_paths(
        state_directory / activate.RECEIPT_BASENAMES[0]
    )
    missing.unlink()
    _fsync_directory(state_directory)

    with pytest.raises(
        activate.ProtocolActivationRefused,
        match="missing or unsafe retirement ledger",
    ):
        activate.activate_protocol_offline(**_activation_kwargs(state_directory))
    assert not os.path.lexists(missing)


def test_activation_rejects_torn_dual_v1_v2_namespace(tmp_path: Path) -> None:
    """A complete v1 pair cannot be silently composed with any v2 entry."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    _write_legacy_protocol_activation(state_directory)
    (state_directory / protocol.ACTIVATION_AUDIT_BASENAME).write_text(
        "{}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        activate.ProtocolActivationRefused,
        match="torn dual v1/v2",
    ):
        activate.activate_protocol_offline(**_activation_kwargs(state_directory))

    assert os.path.lexists(state_directory / protocol.LEGACY_ACTIVATION_BASENAME)
    assert os.path.lexists(
        state_directory / protocol.LEGACY_ACTIVATION_AUDIT_BASENAME
    )
    assert not os.path.lexists(state_directory / protocol.ACTIVATION_BASENAME)


@pytest.mark.parametrize("generation", ("v1", "v2"))
def test_activation_rejects_sentinel_without_companion_audit(
    tmp_path: Path,
    generation: str,
) -> None:
    """No bare permission sentinel can be repaired into an accepted pair."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    if generation == "v1":
        sentinel = state_directory / protocol.LEGACY_ACTIVATION_BASENAME
        sentinel.write_bytes(protocol.LEGACY_ACTIVATION_BYTES)
    else:
        sentinel = state_directory / protocol.ACTIVATION_BASENAME
        sentinel.write_bytes(protocol.ACTIVATION_BYTES)
    sentinel.chmod(protocol.ACTIVATION_MODE)

    with pytest.raises(
        activate.ProtocolActivationRefused,
        match=rf"torn {generation} activation sentinel",
    ):
        activate.activate_protocol_offline(**_activation_kwargs(state_directory))


def test_v2_runtime_rejects_legacy_namespace_beside_valid_v2(
    tmp_path: Path,
) -> None:
    """A v2 pair never hides rollback-compatible v1 activation state."""

    activation = tmp_path / protocol.ACTIVATION_BASENAME
    create_test_protocol_activation(activation)
    (tmp_path / protocol.LEGACY_ACTIVATION_BASENAME).write_bytes(
        protocol.LEGACY_ACTIVATION_BYTES
    )
    (tmp_path / protocol.LEGACY_ACTIVATION_BASENAME).chmod(
        protocol.ACTIVATION_MODE
    )

    with pytest.raises(
        protocol.ProtocolActivationError,
        match="legacy protocol activation namespace remains present",
    ):
        protocol.inspect_protocol_activation(activation)


def test_v1_migration_synchronises_removal_before_v2_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every crash point disables v1 before the v2 permission sentinel exists."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    _write_legacy_protocol_activation(state_directory)
    kwargs = _activation_kwargs(state_directory)
    state_identity = os.stat(state_directory)
    events: list[str] = []
    real_unlink = activate.os.unlink
    real_fsync = activate.os.fsync
    real_rename = protocol._rename_noreplace_at

    def recording_unlink(path, *args, **kwargs):
        if path in {
            protocol.LEGACY_ACTIVATION_BASENAME,
            protocol.LEGACY_ACTIVATION_AUDIT_BASENAME,
        }:
            events.append(f"unlink:{path}")
        return real_unlink(path, *args, **kwargs)

    def recording_fsync(descriptor: int) -> None:
        identity = os.fstat(descriptor)
        if (
            stat.S_ISDIR(identity.st_mode)
            and (identity.st_dev, identity.st_ino)
            == (state_identity.st_dev, state_identity.st_ino)
        ):
            events.append("fsync:state-directory")
        real_fsync(descriptor)

    def recording_rename(
        directory_fd: int,
        source_basename: str,
        destination_basename: str,
    ) -> None:
        if destination_basename in {
            protocol.ACTIVATION_AUDIT_BASENAME,
            protocol.ACTIVATION_BASENAME,
        }:
            events.append(f"publish:{destination_basename}")
        real_rename(directory_fd, source_basename, destination_basename)

    monkeypatch.setattr(activate.os, "unlink", recording_unlink)
    monkeypatch.setattr(activate.os, "fsync", recording_fsync)
    monkeypatch.setattr(protocol, "_rename_noreplace_at", recording_rename)

    activate.activate_protocol_offline(**kwargs)

    sentinel_unlink = events.index(
        f"unlink:{protocol.LEGACY_ACTIVATION_BASENAME}"
    )
    audit_unlink = events.index(
        f"unlink:{protocol.LEGACY_ACTIVATION_AUDIT_BASENAME}"
    )
    audit_publish = events.index(f"publish:{protocol.ACTIVATION_AUDIT_BASENAME}")
    sentinel_publish = events.index(f"publish:{protocol.ACTIVATION_BASENAME}")
    assert sentinel_unlink < audit_unlink < audit_publish < sentinel_publish
    assert "fsync:state-directory" in events[sentinel_unlink + 1 : audit_unlink]
    assert "fsync:state-directory" in events[audit_unlink + 1 : audit_publish]


def test_v1_migration_resumes_after_durable_sentinel_removal(
    tmp_path: Path,
) -> None:
    """The one ordered v1 audit-only crash residue can complete safely."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    legacy_sentinel, legacy_audit = _write_legacy_protocol_activation(
        state_directory
    )
    legacy_sentinel.unlink()
    _fsync_directory(state_directory)

    result = activate.activate_protocol_offline(
        **_activation_kwargs(state_directory)
    )

    assert result.activation_migrated_from_protocol_version == 1
    assert not legacy_audit.exists()
    assert protocol.inspect_protocol_activation(
        state_directory / protocol.ACTIVATION_BASENAME
    ).activation_kind == protocol.ESTABLISHED_INSTALL_ACTIVATION_KIND


def test_v1_migration_failure_never_publishes_v2_before_v1_is_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed first removal sync leaves only fail-closed migration state."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    legacy_sentinel, legacy_audit = _write_legacy_protocol_activation(
        state_directory
    )
    kwargs = _activation_kwargs(state_directory)
    identity = os.stat(state_directory)
    real_fsync = activate.os.fsync
    failed = False

    def fail_first_state_directory_fsync(descriptor: int) -> None:
        nonlocal failed
        current = os.fstat(descriptor)
        if (
            not failed
            and stat.S_ISDIR(current.st_mode)
            and (current.st_dev, current.st_ino)
            == (identity.st_dev, identity.st_ino)
        ):
            failed = True
            raise OSError("injected v1 removal fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(activate.os, "fsync", fail_first_state_directory_fsync)
    with pytest.raises(
        activate.ProtocolActivationRefused,
        match="offline protocol activation failed",
    ):
        activate.activate_protocol_offline(**kwargs)

    assert failed is True
    assert not legacy_sentinel.exists()
    assert legacy_audit.exists()
    assert not os.path.lexists(state_directory / protocol.ACTIVATION_BASENAME)
    assert not os.path.lexists(
        state_directory / protocol.ACTIVATION_AUDIT_BASENAME
    )

    monkeypatch.setattr(activate.os, "fsync", real_fsync)
    result = activate.activate_protocol_offline(**kwargs)
    assert result.activation_migrated_from_protocol_version == 1
    assert protocol.inspect_protocol_activation(
        state_directory / protocol.ACTIVATION_BASENAME
    ).size == len(protocol.ACTIVATION_BYTES)


@pytest.mark.parametrize(
    "basename",
    activate.REFUSED_STATE_BASENAMES,
)
def test_v2_activation_refuses_every_exact_transaction_namespace_entry(
    tmp_path: Path,
    basename: str,
) -> None:
    """The activation inventory includes every fixed transaction companion."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    (state_directory / basename).write_text("unresolved\n", encoding="utf-8")

    with pytest.raises(activate.ProtocolActivationRefused, match=re.escape(basename)):
        activate.activate_protocol_offline(**_activation_kwargs(state_directory))


@pytest.mark.parametrize("prefix", activate.REFUSED_STATE_PREFIXES)
def test_v2_activation_refuses_every_transition_or_guard_prefix(
    tmp_path: Path,
    prefix: str,
) -> None:
    """A crash-left transition or retirement guard blocks v2 activation."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    unresolved = state_directory / f"{prefix}fixture"
    unresolved.write_text("unresolved\n", encoding="utf-8")

    with pytest.raises(
        activate.ProtocolActivationRefused,
        match=re.escape(unresolved.name),
    ):
        activate.activate_protocol_offline(**_activation_kwargs(state_directory))


def test_v2_activation_audit_and_inventory_are_deterministic(
    tmp_path: Path,
) -> None:
    """Identical bound inputs yield byte-identical v2 audit and inventory."""

    ledger_contract = retirement.retirement_ledger_contract_sha256(
        tuple(
            Path(name)
            for name in protocol.RETIREMENT_LEDGER_RECEIPT_BASENAMES
        )
    )
    first = protocol.build_established_install_activation_audit_bytes(
        project_device=11,
        project_inode=22,
        clean_state_attestation_sha256="5" * 64,
        clean_state_attestation_size=33,
        activator_cli_sha256="6" * 64,
        reconciliation_reference="deterministic-v2-audit",
        retirement_ledger_contract_sha256_value=ledger_contract,
        retirement_ledger_initial_inventory_sha256="8" * 64,
    )
    second = protocol.build_established_install_activation_audit_bytes(
        project_device=11,
        project_inode=22,
        clean_state_attestation_sha256="5" * 64,
        clean_state_attestation_size=33,
        activator_cli_sha256="6" * 64,
        reconciliation_reference="deterministic-v2-audit",
        retirement_ledger_contract_sha256_value=ledger_contract,
        retirement_ledger_initial_inventory_sha256="8" * 64,
    )

    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    value = json.loads(first)
    assert value["protocol_version"] == 2
    assert value["legacy_activation_basename"] == (
        protocol.LEGACY_ACTIVATION_BASENAME
    )
    assert value["legacy_namespace_required_absent"] is True
    assert value["retirement_ledger_contract_sha256"] == ledger_contract
    assert value["retirement_ledger_initial_inventory_sha256"] == "8" * 64
    assert activate.REFUSED_STATE_BASENAMES == tuple(
        dict.fromkeys(activate.REFUSED_STATE_BASENAMES)
    )
    assert activate.REFUSED_STATE_PREFIXES == tuple(
        dict.fromkeys(activate.REFUSED_STATE_PREFIXES)
    )
    expected_inventory = (
        json.dumps(
            {
                "basenames": list(activate.REFUSED_STATE_BASENAMES),
                "prefixes": list(activate.REFUSED_STATE_PREFIXES),
            },
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    assert activate._refused_state_inventory_sha256() == hashlib.sha256(
        expected_inventory
    ).hexdigest()
    project = tmp_path / "project"
    project.mkdir()
    identity = os.stat(project)
    attestation_a = activate.build_clean_state_attestation_bytes(
        project_root=project,
        project_device=int(identity.st_dev),
        project_inode=int(identity.st_ino),
        activator_cli_sha256="7" * 64,
        reconciliation_reference="deterministic-v2-inventory",
    )
    attestation_b = activate.build_clean_state_attestation_bytes(
        project_root=project,
        project_device=int(identity.st_dev),
        project_inode=int(identity.st_ino),
        activator_cli_sha256="7" * 64,
        reconciliation_reference="deterministic-v2-inventory",
    )
    assert attestation_a == attestation_b
    attestation_value = json.loads(attestation_a)
    assert attestation_value["refused_state_basenames"] == list(
        activate.REFUSED_STATE_BASENAMES
    )
    assert attestation_value["refused_state_prefixes"] == list(
        activate.REFUSED_STATE_PREFIXES
    )
    assert attestation_value["refused_state_inventory_sha256"] == (
        activate._refused_state_inventory_sha256()
    )


@pytest.mark.parametrize(
    "mutation",
    ("missing_basename", "missing_prefix", "wrong_inventory_hash"),
)
def test_v2_activation_rejects_attestation_with_incomplete_state_inventory(
    tmp_path: Path,
    mutation: str,
) -> None:
    """The operator attestation cannot silently omit a v2 transaction path."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    kwargs = _activation_kwargs(state_directory)
    attestation = Path(kwargs["clean_state_attestation_path"])
    value = json.loads(attestation.read_text(encoding="utf-8"))
    if mutation == "missing_basename":
        value["refused_state_basenames"].remove(
            activate.MEDIA_UPLOAD_FENCE_BASENAME
        )
    elif mutation == "missing_prefix":
        value["refused_state_prefixes"].remove(
            activate.MEDIA_RETIREMENT_GUARD_PREFIX
        )
    else:
        value["refused_state_inventory_sha256"] = "0" * 64
    mutated = (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    attestation.chmod(0o600)
    attestation.write_bytes(mutated)
    attestation.chmod(activate.CLEAN_STATE_ATTESTATION_MODE)
    kwargs["expected_clean_state_attestation_sha256"] = hashlib.sha256(
        mutated
    ).hexdigest()

    with pytest.raises(
        activate.ProtocolActivationRefused,
        match="fields do not bind this activation",
    ):
        activate.activate_protocol_offline(**kwargs)


def test_protocol_activation_never_exposes_partial_final_and_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A staging write failure leaves no final permission sentinel."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    activation = state_directory / protocol.ACTIVATION_BASENAME
    initialise_test_retirement_ledgers(state_directory)
    real_write = protocol.os.write
    writes = 0

    def fail_after_prefix(descriptor: int, value) -> int:
        nonlocal writes
        writes += 1
        if writes == 1:
            prefix = bytes(value[:7])
            return real_write(descriptor, prefix)
        raise OSError("injected staging write failure")

    monkeypatch.setattr(protocol.os, "write", fail_after_prefix)
    with pytest.raises(OSError, match="injected staging write failure"):
        create_test_protocol_activation(activation)
    assert not os.path.lexists(activation)

    monkeypatch.setattr(protocol.os, "write", real_write)
    snapshot = create_test_protocol_activation(activation)
    assert snapshot.size == len(protocol.ACTIVATION_BYTES)
    assert activation.read_bytes() == protocol.ACTIVATION_BYTES


def test_protocol_activation_persists_audit_before_sentinel_and_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hard stop cannot publish the permission sentinel before its audit."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    activation = state_directory / protocol.ACTIVATION_BASENAME
    initialise_test_retirement_ledgers(state_directory)
    directory_identity = os.stat(state_directory)
    real_fsync = protocol.os.fsync
    directory_fsyncs = 0

    def fail_first_directory_fsync(descriptor: int) -> None:
        nonlocal directory_fsyncs
        identity = os.fstat(descriptor)
        if (
            stat.S_ISDIR(identity.st_mode)
            and identity.st_dev == directory_identity.st_dev
            and identity.st_ino == directory_identity.st_ino
        ):
            directory_fsyncs += 1
            if directory_fsyncs == 1:
                raise OSError("injected post-rename directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(protocol.os, "fsync", fail_first_directory_fsync)
    with pytest.raises(OSError, match="post-rename directory fsync failure"):
        create_test_protocol_activation(activation)
    assert not os.path.lexists(activation)
    audit = state_directory / protocol.ACTIVATION_AUDIT_BASENAME
    audit_value = json.loads(audit.read_text(encoding="utf-8"))
    assert audit_value["activation_kind"] == (
        protocol.ESTABLISHED_INSTALL_ACTIVATION_KIND
    )
    assert stat.S_IMODE(audit.stat().st_mode) == protocol.ACTIVATION_AUDIT_MODE

    monkeypatch.setattr(protocol.os, "fsync", real_fsync)
    snapshot = create_test_protocol_activation(activation)
    assert activation.read_bytes() == protocol.ACTIVATION_BYTES
    assert snapshot.activation_kind == protocol.ESTABLISHED_INSTALL_ACTIVATION_KIND
    assert snapshot.audit_sha256 == hashlib.sha256(audit.read_bytes()).hexdigest()


def test_protocol_activation_refuses_malformed_existing_final(
    tmp_path: Path,
) -> None:
    """Idempotence never blesses last-key, partial or otherwise wrong bytes."""

    activation = tmp_path / protocol.ACTIVATION_BASENAME
    activation.write_bytes(protocol.ACTIVATION_BYTES[:-1])
    activation.chmod(protocol.ACTIVATION_MODE)
    with pytest.raises(
        protocol.ProtocolActivationError,
        match="unsafe metadata|unexpected existing bytes",
    ):
        create_test_protocol_activation(activation)


@pytest.mark.parametrize(
    "unsafe_layout",
    ("wrong_bytes", "wrong_mode", "symlink", "hardlink"),
)
def test_unsafe_protocol_sentinel_blocks_direct_and_scheduler_lanes(
    tmp_path: Path,
    unsafe_layout: str,
) -> None:
    """Every unsafe activation entry remains a cross-lane runtime barrier."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    activation = state_directory / protocol.ACTIVATION_BASENAME
    if unsafe_layout == "wrong_bytes":
        activation.write_bytes(b"not the protocol activation\n")
        activation.chmod(protocol.ACTIVATION_MODE)
    elif unsafe_layout == "wrong_mode":
        activation.write_bytes(protocol.ACTIVATION_BYTES)
        activation.chmod(0o600)
    elif unsafe_layout == "symlink":
        target = state_directory / "activation-target"
        target.write_bytes(protocol.ACTIVATION_BYTES)
        target.chmod(protocol.ACTIVATION_MODE)
        activation.symlink_to(target.name)
    else:
        create_test_protocol_activation(activation)
        os.link(activation, state_directory / "activation-hardlink")

    child, record = _run_child("blocked", state_directory)
    assert child.returncode == 0, child.stderr
    assert record["initial"]["protocol_active"] is False
    assert record["blocking_before_direct"] is True
    assert set(record["direct_results"].values()) == {"blocked"}
    assert record["transport_sentinel_calls"] == []
    assert record["scheduler_sleep_calls"] == 3
    assert record["scheduler_entries"] == []
    assert record["blocking_after_scheduler"] is True


def test_offline_activation_binds_expected_root_and_established_state(
    tmp_path: Path,
) -> None:
    """A typo or empty directory cannot be reported as production activation."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    kwargs = _activation_kwargs(state_directory)
    with pytest.raises(activate.MarkerReconciliationError):
        activate.activate_protocol_offline(**kwargs)

    _write_established_activation_state(state_directory)
    with pytest.raises(
        activate.UnsafeReconciliationPathError,
        match="preflight identity token",
    ):
        activate.activate_protocol_offline(
            **{
                **kwargs,
                "expected_project_inode": int(kwargs["expected_project_inode"]) + 1,
            }
        )


def test_offline_activation_requires_supervisor_stopped_declaration(
    tmp_path: Path,
) -> None:
    """Daemon locks do not falsely claim the service wrapper is stopped."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    with pytest.raises(
        activate.ProtocolActivationRefused,
        match="supervisor-stopped",
    ):
        activate.activate_protocol_offline(
            **{
                **_activation_kwargs(state_directory),
                "supervisor_stopped_confirmed": False,
            }
        )


@pytest.mark.parametrize("receipt_basename", activate.RECEIPT_BASENAMES)
def test_offline_activation_refuses_each_unresolved_receipt(
    tmp_path: Path,
    receipt_basename: str,
) -> None:
    """No transaction lane can be silently omitted from activation preflight."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    (state_directory / receipt_basename).write_text("{}\n", encoding="utf-8")
    with pytest.raises(
        activate.ProtocolActivationRefused,
        match=receipt_basename,
    ):
        activate.activate_protocol_offline(**_activation_kwargs(state_directory))
    assert not os.path.lexists(
        state_directory / protocol.ACTIVATION_BASENAME
    )


def test_offline_activation_refuses_held_state_directory_lock(
    tmp_path: Path,
) -> None:
    """A live instance's actual directory flock excludes activation."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    descriptor = os.open(
        state_directory,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(
            activate.BotStillRunningError,
            match="state-directory lock is held",
        ):
            activate.activate_protocol_offline(
                **_activation_kwargs(state_directory)
            )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    assert not os.path.lexists(
        state_directory / protocol.ACTIVATION_BASENAME
    )


def test_established_activation_after_unexplained_marker_loss_requires_attestation(
    tmp_path: Path,
) -> None:
    """Clean pathname absence alone cannot bless lost incident evidence."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    marker = _write_legacy_marker(state_directory)
    marker.unlink()
    _fsync_directory(state_directory)
    kwargs = _activation_kwargs(state_directory)
    kwargs["clean_state_attestation_path"] = None
    kwargs["expected_clean_state_attestation_sha256"] = None

    with pytest.raises(
        activate.ProtocolActivationRefused,
        match="external clean-state/reconciliation attestation is required",
    ):
        activate.activate_protocol_offline(**kwargs)

    assert not os.path.lexists(state_directory / protocol.ACTIVATION_BASENAME)
    assert not os.path.lexists(state_directory / protocol.ACTIVATION_AUDIT_BASENAME)


def test_runtime_requires_hash_bound_companion_audit(tmp_path: Path) -> None:
    """A bare legacy permission sentinel cannot activate the runtime."""

    activation = tmp_path / protocol.ACTIVATION_BASENAME
    activation.write_bytes(protocol.ACTIVATION_BYTES)
    activation.chmod(protocol.ACTIVATION_MODE)

    with pytest.raises(
        protocol.ProtocolActivationError,
        match="activation audit is missing",
    ):
        protocol.inspect_protocol_activation(activation)


@pytest.mark.parametrize("mutation", ("missing", "wrong_bytes", "wrong_mode", "symlink"))
def test_runtime_fails_closed_for_unsafe_activation_audit(
    tmp_path: Path,
    mutation: str,
) -> None:
    """The runtime never treats the sentinel independently from its audit."""

    activation = tmp_path / protocol.ACTIVATION_BASENAME
    create_test_protocol_activation(activation)
    audit = tmp_path / protocol.ACTIVATION_AUDIT_BASENAME
    if mutation == "missing":
        audit.unlink()
    elif mutation == "wrong_bytes":
        audit.chmod(0o600)
        audit.write_text("{}\n", encoding="utf-8")
        audit.chmod(protocol.ACTIVATION_AUDIT_MODE)
    elif mutation == "wrong_mode":
        audit.chmod(0o600)
    else:
        audit.unlink()
        target = tmp_path / "audit-target"
        target.write_text("{}\n", encoding="utf-8")
        target.chmod(protocol.ACTIVATION_AUDIT_MODE)
        audit.symlink_to(target.name)

    with pytest.raises(protocol.ProtocolActivationError):
        protocol.inspect_protocol_activation(activation)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("schema_version", 2.0),
        ("schema_version", True),
        ("protocol_version", 2.0),
        ("protocol_version", True),
        ("activation_size", float(len(protocol.ACTIVATION_BYTES))),
        ("activation_size", True),
    ),
)
def test_runtime_activation_audit_requires_exact_integer_fields(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    activation = tmp_path / protocol.ACTIVATION_BASENAME
    create_test_protocol_activation(activation)
    audit_path = tmp_path / protocol.ACTIVATION_AUDIT_BASENAME
    audit = json.loads(audit_path.read_bytes())
    audit[field] = invalid_value
    audit_path.chmod(0o600)
    audit_path.write_bytes(protocol._canonical_json_bytes(audit))
    audit_path.chmod(protocol.ACTIVATION_AUDIT_MODE)

    with pytest.raises(protocol.ProtocolActivationError):
        protocol.inspect_protocol_activation(activation)


@pytest.mark.parametrize("invalid_schema", (1.0, True))
def test_legacy_activation_audit_keeps_integer_v1_and_rejects_nonintegers(
    tmp_path: Path,
    invalid_schema: object,
) -> None:
    identity = tmp_path.stat()
    audit_bytes = protocol.build_legacy_established_install_activation_audit_bytes(
        project_device=identity.st_dev,
        project_inode=identity.st_ino,
        clean_state_attestation_sha256="1" * 64,
        clean_state_attestation_size=1,
        activator_cli_sha256="2" * 64,
        reconciliation_reference="isolated-legacy-test-fixture",
    )
    parsed = protocol._parse_legacy_activation_audit(audit_bytes)
    assert type(parsed["schema_version"]) is int
    assert parsed["schema_version"] == 1

    parsed["schema_version"] = invalid_schema
    with pytest.raises(protocol.ProtocolActivationError):
        protocol._parse_legacy_activation_audit(
            protocol._canonical_json_bytes(parsed)
        )


def test_unaudited_new_install_helper_is_permanently_refused(
    tmp_path: Path,
) -> None:
    """Namespace absence alone can never manufacture runtime permission."""

    activation = tmp_path / protocol.ACTIVATION_BASENAME
    _write_established_activation_state(tmp_path)
    (tmp_path / ".mrsMThatcher.initialised.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    with pytest.raises(
        protocol.ProtocolActivationError,
        match="unaudited new-install protocol activation is unsupported",
    ):
        protocol.create_protocol_activation_noreplace(activation)

    assert not os.path.lexists(activation)
    assert not os.path.lexists(tmp_path / protocol.ACTIVATION_AUDIT_BASENAME)


def test_activation_pair_revalidates_sentinel_after_audit_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removal between the two stable reads cannot yield active permission."""

    activation = tmp_path / protocol.ACTIVATION_BASENAME
    create_test_protocol_activation(activation)
    real_inspect = protocol._inspect_stable_regular_at

    def inspect_then_remove_sentinel(
        directory_fd: int,
        basename: str,
        **kwargs,
    ):
        inspected = real_inspect(directory_fd, basename, **kwargs)
        if basename == protocol.ACTIVATION_AUDIT_BASENAME:
            os.unlink(protocol.ACTIVATION_BASENAME, dir_fd=directory_fd)
        return inspected

    monkeypatch.setattr(
        protocol,
        "_inspect_stable_regular_at",
        inspect_then_remove_sentinel,
    )
    with pytest.raises(
        protocol.ProtocolActivationError,
        match="sentinel changed while the activation pair was inspected",
    ):
        protocol.inspect_protocol_activation(activation)


def test_activation_pair_cannot_compose_different_namespace_generations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid sentinel read and later valid audit never form a torn pair."""

    activation = tmp_path / protocol.ACTIVATION_BASENAME
    create_test_protocol_activation(activation)
    audit = tmp_path / protocol.ACTIVATION_AUDIT_BASENAME
    valid_audit = audit.read_bytes()
    audit.chmod(0o600)
    audit.write_text("{}\n", encoding="utf-8")
    audit.chmod(protocol.ACTIVATION_AUDIT_MODE)
    real_sentinel_inspect = protocol._inspect_activation_sentinel_at

    def inspect_sentinel_then_swap_generation(
        directory_fd: int,
        *,
        fsync_file: bool = False,
    ):
        inspected = real_sentinel_inspect(
            directory_fd,
            fsync_file=fsync_file,
        )
        os.unlink(protocol.ACTIVATION_BASENAME, dir_fd=directory_fd)
        replacement = tmp_path / "valid-audit-replacement"
        replacement.write_bytes(valid_audit)
        replacement.chmod(protocol.ACTIVATION_AUDIT_MODE)
        os.replace(replacement, audit)
        return inspected

    monkeypatch.setattr(
        protocol,
        "_inspect_activation_sentinel_at",
        inspect_sentinel_then_swap_generation,
    )
    with pytest.raises(
        protocol.ProtocolActivationError,
        match="sentinel changed while the activation pair was inspected",
    ):
        protocol.inspect_protocol_activation(activation)

    assert not os.path.lexists(activation)
    assert audit.read_bytes() == valid_audit


def test_established_activation_rejects_attestation_with_wrong_cli_hash(
    tmp_path: Path,
) -> None:
    """An operator file for another activator source cannot open this install."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    identity = os.stat(state_directory)
    attestation = tmp_path / "wrong-cli-attestation.json"
    data = activate.build_clean_state_attestation_bytes(
        project_root=state_directory,
        project_device=int(identity.st_dev),
        project_inode=int(identity.st_ino),
        activator_cli_sha256="0" * 64,
        reconciliation_reference="wrong-cli-review",
    )
    attestation.write_bytes(data)
    attestation.chmod(activate.CLEAN_STATE_ATTESTATION_MODE)
    kwargs = _activation_kwargs(state_directory)
    kwargs.update(
        clean_state_attestation_path=attestation,
        expected_clean_state_attestation_sha256=hashlib.sha256(data).hexdigest(),
    )

    with pytest.raises(
        activate.ProtocolActivationRefused,
        match="fields do not bind this activation",
    ):
        activate.activate_protocol_offline(**kwargs)

    assert not os.path.lexists(state_directory / protocol.ACTIVATION_BASENAME)
    assert not os.path.lexists(state_directory / protocol.ACTIVATION_AUDIT_BASENAME)


def test_external_clean_state_attestation_requires_integer_schema_version(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    identity = state_directory.stat()
    activator_sha256 = "a" * 64
    attestation = tmp_path / "float-schema-attestation.json"
    value = json.loads(
        activate.build_clean_state_attestation_bytes(
            project_root=state_directory,
            project_device=int(identity.st_dev),
            project_inode=int(identity.st_ino),
            activator_cli_sha256=activator_sha256,
            reconciliation_reference="float-schema-review",
        )
    )
    value["schema_version"] = float(activate.CLEAN_STATE_ATTESTATION_SCHEMA_VERSION)
    data = activate._canonical_json_bytes(value)
    attestation.write_bytes(data)
    attestation.chmod(activate.CLEAN_STATE_ATTESTATION_MODE)

    with pytest.raises(
        activate.ProtocolActivationRefused,
        match="fields do not bind this activation",
    ):
        activate._load_clean_state_attestation(
            attestation,
            expected_sha256=hashlib.sha256(data).hexdigest(),
            project=state_directory,
            project_identity=identity,
            activator_cli_sha256=activator_sha256,
        )


@pytest.mark.parametrize("unsafe", ("symlink", "hardlink"))
def test_established_activation_rejects_unsafe_external_attestation(
    tmp_path: Path,
    unsafe: str,
) -> None:
    """External evidence is a stable, single-link, no-follow ordinary file."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    kwargs = _activation_kwargs(state_directory)
    original = Path(kwargs["clean_state_attestation_path"])
    unsafe_path = tmp_path / f"unsafe-{unsafe}.json"
    if unsafe == "symlink":
        unsafe_path.symlink_to(original.name)
    else:
        os.link(original, unsafe_path)
    kwargs["clean_state_attestation_path"] = unsafe_path

    with pytest.raises(activate.MarkerReconciliationError):
        activate.activate_protocol_offline(**kwargs)

    assert not os.path.lexists(state_directory / protocol.ACTIVATION_BASENAME)


def test_established_activation_audit_survives_crash_before_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Established activation publishes its operator-bound audit first."""

    state_directory = tmp_path / "state"
    state_directory.mkdir()
    _write_established_activation_state(state_directory)
    (state_directory / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    kwargs = _activation_kwargs(state_directory)
    real_rename = protocol._rename_noreplace_at

    def fail_sentinel_publish(
        directory_fd: int,
        source_basename: str,
        destination_basename: str,
    ) -> None:
        if destination_basename == protocol.ACTIVATION_BASENAME:
            raise OSError("injected stop before sentinel publication")
        real_rename(directory_fd, source_basename, destination_basename)

    monkeypatch.setattr(protocol, "_rename_noreplace_at", fail_sentinel_publish)
    with pytest.raises(
        activate.ProtocolActivationRefused,
        match="offline protocol activation failed",
    ):
        activate.activate_protocol_offline(**kwargs)

    audit = state_directory / protocol.ACTIVATION_AUDIT_BASENAME
    assert audit.is_file()
    value = json.loads(audit.read_text(encoding="utf-8"))
    assert value["activation_kind"] == protocol.ESTABLISHED_INSTALL_ACTIVATION_KIND
    assert value["operator_clean_state_claim_locally_proven"] is False
    assert not os.path.lexists(state_directory / protocol.ACTIVATION_BASENAME)
    with pytest.raises(protocol.ProtocolActivationError, match="sentinel is missing"):
        protocol.inspect_protocol_activation(
            state_directory / protocol.ACTIVATION_BASENAME
        )

    monkeypatch.setattr(protocol, "_rename_noreplace_at", real_rename)
    result = activate.activate_protocol_offline(**kwargs)
    assert result.activation_kind == protocol.ESTABLISHED_INSTALL_ACTIVATION_KIND
    assert result.activation_audit_sha256 == hashlib.sha256(audit.read_bytes()).hexdigest()


def test_activation_cli_requires_external_attestation_arguments() -> None:
    """The CLI cannot downgrade to a bare clean-state checkbox."""

    parser = activate.build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(
            [
                "--project-root",
                "/nonexistent",
                "--expected-project-root",
                "/nonexistent",
                "--expected-project-device",
                "1",
                "--expected-project-inode",
                "2",
            ]
        )
    assert exc_info.value.code == 2


def _valid_activation_cli_arguments() -> list[str]:
    """Return one complete, documented activation parser invocation."""

    return [
        "--project-root",
        "/srv/mrs-thatcher",
        "--expected-project-root",
        "/srv/mrs-thatcher",
        "--expected-project-device",
        "101",
        "--expected-project-inode",
        "202",
        "--clean-state-attestation",
        "/operator/clean-state-attestation.json",
        "--clean-state-attestation-sha256",
        "a" * 64,
        "--confirm-clean-offline-activation",
        "--confirm-supervisor-stopped",
    ]


def test_activation_cli_accepts_complete_documented_invocation() -> None:
    """Exact documented option names remain accepted."""

    args = activate.build_parser().parse_args(_valid_activation_cli_arguments())

    assert args.project_root == Path("/srv/mrs-thatcher")
    assert args.expected_project_root == Path("/srv/mrs-thatcher")
    assert args.expected_project_device == 101
    assert args.expected_project_inode == 202
    assert args.clean_state_attestation == Path(
        "/operator/clean-state-attestation.json"
    )
    assert args.clean_state_attestation_sha256 == "a" * 64
    assert args.confirm_clean_offline_activation is True
    assert args.confirm_supervisor_stopped is True


def test_activation_cli_rejects_duplicate_project_identity_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The registry-bound project-identity regression remains a stable node."""

    arguments = _valid_activation_cli_arguments()
    arguments.extend(("--project-root", "/different/project"))

    with pytest.raises(SystemExit) as exc_info:
        activate.build_parser().parse_args(arguments)

    assert exc_info.value.code == 2
    assert "may not be repeated" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("duplicate_option", "duplicate_value"),
    [
        ("--project-root", "/different/project"),
        ("--expected-project-root", "/different/expected-project"),
        ("--expected-project-device", "303"),
        ("--expected-project-inode", "404"),
        (
            "--clean-state-attestation",
            "/operator/different-clean-state-attestation.json",
        ),
        ("--clean-state-attestation-sha256", "b" * 64),
    ],
)
@pytest.mark.parametrize("argument_style", ["separate", "equals"])
def test_activation_cli_rejects_duplicate_value_options(
    duplicate_option: str,
    duplicate_value: str,
    argument_style: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No later value option may silently replace operator intent."""

    arguments = _valid_activation_cli_arguments()
    if argument_style == "equals":
        arguments.append(f"{duplicate_option}={duplicate_value}")
    else:
        arguments.extend((duplicate_option, duplicate_value))

    with pytest.raises(SystemExit) as exc_info:
        activate.build_parser().parse_args(arguments)

    assert exc_info.value.code == 2
    assert "may not be repeated" in capsys.readouterr().err


@pytest.mark.parametrize(
    "duplicate_option",
    [
        "--confirm-clean-offline-activation",
        "--confirm-supervisor-stopped",
    ],
)
def test_activation_cli_rejects_duplicate_confirmation_flags(
    duplicate_option: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Confirmation flags are single operator attestations, not counters."""

    arguments = _valid_activation_cli_arguments()
    arguments.append(duplicate_option)

    with pytest.raises(SystemExit) as exc_info:
        activate.build_parser().parse_args(arguments)

    assert exc_info.value.code == 2
    assert "may not be repeated" in capsys.readouterr().err


def test_exact_activation_parser_rejects_mixed_alias_duplication(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two spellings for one synthetic destination remain one option."""

    parser = activate._ExactActivationArgumentParser(allow_abbrev=False)
    parser.add_argument(
        "--synthetic-primary",
        "--synthetic-alias",
        dest="synthetic_value",
        required=True,
    )

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(
            [
                "--synthetic-primary",
                "first",
                "--synthetic-alias",
                "second",
            ]
        )

    assert exc_info.value.code == 2
    assert "including through an alias" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("exact_option", "abbreviated_option"),
    [
        ("--project-root", "--project-r"),
        ("--expected-project-root", "--expected-project-r"),
        ("--expected-project-device", "--expected-project-d"),
        ("--expected-project-inode", "--expected-project-i"),
        (
            "--clean-state-attestation-sha256",
            "--clean-state-attestation-s",
        ),
        (
            "--confirm-clean-offline-activation",
            "--confirm-clean-o",
        ),
        ("--confirm-supervisor-stopped", "--confirm-s"),
    ],
)
def test_activation_cli_rejects_abbreviated_options(
    exact_option: str,
    abbreviated_option: str,
) -> None:
    """Safety-critical options require their complete documented spelling."""

    arguments = [
        abbreviated_option if value == exact_option else value
        for value in _valid_activation_cli_arguments()
    ]

    with pytest.raises(SystemExit) as exc_info:
        activate.build_parser().parse_args(arguments)

    assert exc_info.value.code == 2
