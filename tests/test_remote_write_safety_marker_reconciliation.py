"""Focused tests for offline remote-write safety marker reconciliation."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import remote_media_upload_receipt as media_receipt
from tools import reconcile_remote_write_safety_marker as reconcile


MARKER_BYTES = b'{"outcome":"ambiguous_remote_post","schema_version":1}\n'
MARKER_SHA256 = hashlib.sha256(MARKER_BYTES).hexdigest()
STOPPED_DAEMON_PID = 2_147_483_647


def installation(tmp_path: Path) -> Path:
    """Create the minimal stopped-bot filesystem required by the tool."""

    project = tmp_path / "bot"
    project.mkdir()
    (project / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="utf-8",
    )
    (project / reconcile.MARKER_BASENAME).write_bytes(MARKER_BYTES)
    return project


def media_incident_installation(
    tmp_path: Path,
) -> tuple[Path, bytes, media_receipt.MediaReceiptSnapshot, media_receipt.MediaReceiptSnapshot]:
    """Create the exact stopped, ambiguous pre-tweet media incident."""

    project = installation(tmp_path)
    marker_value = {
        "made_with_ai": False,
        "media_ids": [],
        "outcome": "ambiguous_remote_post",
        "recorded_at_epoch": 1_800_000_000,
        "reply_to_id": "",
        "schema_version": 1,
        "text_sha256": hashlib.sha256(b"").hexdigest(),
    }
    marker_bytes = reconcile._canonical_json_bytes(marker_value)
    marker = project / reconcile.MARKER_BASENAME
    marker.write_bytes(marker_bytes)
    os.link(marker, project / reconcile.RESTART_BARRIER_BASENAME)
    image = project / "reviewed-image.jpg"
    image.write_bytes(b"reviewed-image-bytes")
    image.chmod(0o600)
    receipt_path = project / reconcile.MEDIA_RECEIPT_BASENAME
    media_receipt.begin_media_upload(
        receipt_path=receipt_path,
        image_path=image,
        lane="quote_image",
        mime_type="image/jpeg",
        payload_metadata={
            "form": {
                "media_category": "tweet_image",
                "media_type": "image/jpeg",
            },
            "request_method": "POST",
            "request_path": "/2/media/upload",
        },
    )
    receipt = media_receipt.inspect_media_upload_receipt(receipt_path)
    assert receipt is not None
    fence = media_receipt._required_fence_snapshot(
        project / reconcile.MEDIA_FENCE_BASENAME
    )
    return project, marker_bytes, receipt, fence


def run_media_reconciliation(
    project: Path,
    marker_bytes: bytes,
    receipt: media_receipt.MediaReceiptSnapshot,
    fence: media_receipt.MediaReceiptSnapshot,
    **overrides: object,
) -> reconcile.UnattachedMediaArchiveResult:
    """Run exact unattached-media reconciliation for one fixture."""

    values: dict[str, object] = {
        "project_root": project,
        "expected_marker_sha256": hashlib.sha256(marker_bytes).hexdigest(),
        "expected_media_receipt_sha256": receipt.sha256,
        "expected_media_fence_sha256": fence.sha256,
        "expected_media_transaction_id": receipt.document["transaction_id"],
        "expected_media_receipt_device": receipt.device,
        "expected_media_receipt_inode": receipt.inode,
        "expected_media_receipt_ctime_ns": receipt.ctime_ns,
        "expected_media_fence_device": fence.device,
        "expected_media_fence_inode": fence.inode,
        "expected_media_fence_ctime_ns": fence.ctime_ns,
        "reconciliation_reference": "x-log-503-no-tweet-create",
        "confirm_no_tweet_create_attempted": True,
        "confirm_unattached_media_abandoned": True,
        "now": lambda: 1_800_000_100,
    }
    values.update(overrides)
    return reconcile.reconcile_unattached_media_upload_offline(**values)


def media_cli_command(
    project: Path,
    marker_bytes: bytes,
    receipt: media_receipt.MediaReceiptSnapshot,
    fence: media_receipt.MediaReceiptSnapshot,
) -> list[str]:
    """Build the complete reviewed-identity CLI invocation."""

    return [
        sys.executable,
        "tools/reconcile_remote_write_safety_marker.py",
        "--project-root",
        str(project),
        "--expected-marker-sha256",
        hashlib.sha256(marker_bytes).hexdigest(),
        "--reconcile-unattached-media-upload",
        "--expected-media-receipt-sha256",
        receipt.sha256,
        "--expected-media-fence-sha256",
        fence.sha256,
        "--expected-media-transaction-id",
        str(receipt.document["transaction_id"]),
        "--expected-media-receipt-device",
        str(receipt.device),
        "--expected-media-receipt-inode",
        str(receipt.inode),
        "--expected-media-receipt-ctime-ns",
        str(receipt.ctime_ns),
        "--expected-media-fence-device",
        str(fence.device),
        "--expected-media-fence-inode",
        str(fence.inode),
        "--expected-media-fence-ctime-ns",
        str(fence.ctime_ns),
        "--reconciliation-reference",
        "operator-reviewed-x-503-without-tweet-create",
    ]


def test_unattached_media_reconciliation_archives_pair_before_marker(
    tmp_path: Path,
) -> None:
    project, marker_bytes, receipt, fence = media_incident_installation(tmp_path)
    receipt_inode = (project / reconcile.MEDIA_RECEIPT_BASENAME).stat().st_ino
    fence_inode = (project / reconcile.MEDIA_FENCE_BASENAME).stat().st_ino

    result = run_media_reconciliation(
        project,
        marker_bytes,
        receipt,
        fence,
    )

    assert result.accepted_media_disposition == "unattached_and_abandoned"
    assert result.no_tweet_create_authority_present is True
    assert result.remote_media_id_absent is True
    assert not (project / reconcile.MEDIA_RECEIPT_BASENAME).exists()
    assert not (project / reconcile.MEDIA_FENCE_BASENAME).exists()
    marker = project / reconcile.MARKER_BASENAME
    successor = project / reconcile.RESTART_BARRIER_BASENAME
    assert marker.read_bytes() == marker_bytes
    assert marker.stat().st_ino == successor.stat().st_ino

    archived_receipt = project / result.receipt_archive_path
    archived_fence = project / result.fence_archive_path
    audit = project / result.audit_receipt_path
    assert archived_receipt.read_bytes() == receipt.data
    assert archived_fence.read_bytes() == fence.data
    assert archived_receipt.stat().st_ino == receipt_inode
    assert archived_fence.stat().st_ino == fence_inode
    assert archived_receipt.stat().st_mode & 0o777 == 0o400
    assert archived_fence.stat().st_mode & 0o777 == 0o400
    assert audit.stat().st_mode & 0o777 == 0o400
    assert json.loads(audit.read_text(encoding="utf-8")) == result.to_dict()

    marker_result = reconcile.reconcile_marker_offline(
        project_root=project,
        expected_marker_sha256=hashlib.sha256(marker_bytes).hexdigest(),
        reconciliation_reference="media-archive-audit-reviewed",
        now=lambda: 1_800_000_101,
    )
    assert marker_result.successful_return_requires_all_active_barriers_absent
    assert not marker.exists()
    assert not successor.exists()


@pytest.mark.parametrize(
    "override",
    [
        {"confirm_no_tweet_create_attempted": False},
        {"confirm_unattached_media_abandoned": False},
        {"expected_media_receipt_sha256": "0" * 64},
        {"expected_media_fence_sha256": "0" * 64},
        {"expected_media_transaction_id": "0" * 64},
        {"expected_media_receipt_inode": 1},
        {"expected_media_fence_ctime_ns": 1},
    ],
)
def test_unattached_media_reconciliation_refuses_unreviewed_identity(
    tmp_path: Path,
    override: dict[str, object],
) -> None:
    project, marker_bytes, receipt, fence = media_incident_installation(tmp_path)

    with pytest.raises(reconcile.UnattachedMediaReconciliationError):
        run_media_reconciliation(
            project,
            marker_bytes,
            receipt,
            fence,
            **override,
        )

    assert (project / reconcile.MEDIA_RECEIPT_BASENAME).read_bytes() == receipt.data
    assert (project / reconcile.MEDIA_FENCE_BASENAME).read_bytes() == fence.data
    assert (project / reconcile.MARKER_BASENAME).read_bytes() == marker_bytes


@pytest.mark.parametrize("authority_name", reconcile.TWEET_AUTHORITY_BASENAMES)
def test_unattached_media_reconciliation_refuses_any_tweet_authority(
    tmp_path: Path,
    authority_name: str,
) -> None:
    project, marker_bytes, receipt, fence = media_incident_installation(tmp_path)
    (project / authority_name).write_bytes(b"authority-must-block")

    with pytest.raises(
        reconcile.UnattachedMediaReconciliationError,
        match="tweet-create authority exists",
    ):
        run_media_reconciliation(project, marker_bytes, receipt, fence)

    assert (project / reconcile.MEDIA_RECEIPT_BASENAME).exists()
    assert (project / reconcile.MEDIA_FENCE_BASENAME).exists()


def test_unattached_media_reconciliation_refuses_live_daemon_lock(
    tmp_path: Path,
) -> None:
    project, marker_bytes, receipt, fence = media_incident_installation(tmp_path)
    descriptor = os.open(project / reconcile.LOCK_BASENAME, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(reconcile.BotStillRunningError):
            run_media_reconciliation(project, marker_bytes, receipt, fence)
    finally:
        os.close(descriptor)

    assert (project / reconcile.MEDIA_RECEIPT_BASENAME).exists()
    assert (project / reconcile.MEDIA_FENCE_BASENAME).exists()


def test_unattached_media_reconciliation_refuses_non_media_only_marker(
    tmp_path: Path,
) -> None:
    project, marker_bytes, receipt, fence = media_incident_installation(tmp_path)
    marker = project / reconcile.MARKER_BASENAME
    successor = project / reconcile.RESTART_BARRIER_BASENAME
    successor.unlink()
    value = json.loads(marker_bytes)
    value["reply_to_id"] = "123"
    changed = reconcile._canonical_json_bytes(value)
    marker.write_bytes(changed)
    os.link(marker, successor)

    with pytest.raises(
        reconcile.UnattachedMediaReconciliationError,
        match="media-only pre-tweet incident",
    ):
        run_media_reconciliation(
            project,
            changed,
            receipt,
            fence,
        )


def test_unattached_media_reconciliation_refuses_symlinked_receipt(
    tmp_path: Path,
) -> None:
    project, marker_bytes, receipt, fence = media_incident_installation(tmp_path)
    receipt_path = project / reconcile.MEDIA_RECEIPT_BASENAME
    displaced = project / "displaced-media-receipt.json"
    receipt_path.rename(displaced)
    receipt_path.symlink_to(displaced.name)

    with pytest.raises(
        reconcile.UnattachedMediaReconciliationError,
        match="not strict canonical sending state",
    ):
        run_media_reconciliation(project, marker_bytes, receipt, fence)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == marker_bytes


def test_unattached_media_hard_exit_keeps_ambiguity_marker(
    tmp_path: Path,
) -> None:
    project, marker_bytes, receipt, fence = media_incident_installation(tmp_path)
    child_code = textwrap.dedent(
        f"""
        import os
        from pathlib import Path
        from tools import reconcile_remote_write_safety_marker as reconcile

        original = reconcile._fsync_directory
        calls = 0
        def exit_after_first_archive_sync(descriptor):
            global calls
            original(descriptor)
            calls += 1
            if calls == 2:
                os._exit(77)
        reconcile._fsync_directory = exit_after_first_archive_sync
        reconcile.reconcile_unattached_media_upload_offline(
            project_root=Path({str(project)!r}),
            expected_marker_sha256={hashlib.sha256(marker_bytes).hexdigest()!r},
            expected_media_receipt_sha256={receipt.sha256!r},
            expected_media_fence_sha256={fence.sha256!r},
            expected_media_transaction_id={receipt.document['transaction_id']!r},
            expected_media_receipt_device={receipt.device},
            expected_media_receipt_inode={receipt.inode},
            expected_media_receipt_ctime_ns={receipt.ctime_ns},
            expected_media_fence_device={fence.device},
            expected_media_fence_inode={fence.inode},
            expected_media_fence_ctime_ns={fence.ctime_ns},
            reconciliation_reference='hard-exit-test',
            confirm_no_tweet_create_attempted=True,
            confirm_unattached_media_abandoned=True,
        )
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", child_code],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 77
    marker = project / reconcile.MARKER_BASENAME
    successor = project / reconcile.RESTART_BARRIER_BASENAME
    assert marker.read_bytes() == marker_bytes
    assert successor.read_bytes() == marker_bytes
    assert marker.stat().st_ino == successor.stat().st_ino


def test_unattached_media_interrupt_after_receipt_unlink_rolls_back_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, marker_bytes, receipt, fence = media_incident_installation(tmp_path)
    original_unlink = reconcile.os.unlink

    def unlink_then_interrupt(path: object, *args: object, **kwargs: object) -> None:
        original_unlink(path, *args, **kwargs)
        if path == reconcile.MEDIA_RECEIPT_BASENAME:
            raise KeyboardInterrupt

    monkeypatch.setattr(reconcile.os, "unlink", unlink_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_media_reconciliation(project, marker_bytes, receipt, fence)

    receipt_path = project / reconcile.MEDIA_RECEIPT_BASENAME
    fence_path = project / reconcile.MEDIA_FENCE_BASENAME
    assert receipt_path.read_bytes() == receipt.data
    assert fence_path.read_bytes() == fence.data
    assert receipt_path.stat().st_mode & 0o777 == 0o600
    assert fence_path.stat().st_mode & 0o777 == 0o600
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert not list(archive.glob("unattached_media_upload.*"))
    assert (project / reconcile.MARKER_BASENAME).read_bytes() == marker_bytes


def test_unattached_media_interrupt_after_archive_link_rolls_back_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, marker_bytes, receipt, fence = media_incident_installation(tmp_path)
    original_link = reconcile._link_noreplace
    interrupted = False

    def link_then_interrupt(*args: object, **kwargs: object) -> None:
        nonlocal interrupted
        original_link(*args, **kwargs)
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(reconcile, "_link_noreplace", link_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_media_reconciliation(project, marker_bytes, receipt, fence)

    assert (project / reconcile.MEDIA_RECEIPT_BASENAME).read_bytes() == receipt.data
    assert (project / reconcile.MEDIA_FENCE_BASENAME).read_bytes() == fence.data
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert not list(archive.glob("unattached_media_upload.*"))
    assert (project / reconcile.MARKER_BASENAME).read_bytes() == marker_bytes


def run_reconciliation(project: Path) -> reconcile.MarkerArchiveResult:
    """Archive the fixture marker using deterministic receipt time."""

    return reconcile.reconcile_marker_offline(
        project_root=project,
        expected_marker_sha256=MARKER_SHA256,
        reconciliation_reference="review-IR-EE7539C-01",
        now=lambda: 1_800_000_000,
    )


def test_reconciliation_refuses_while_daemon_instance_lock_is_held(
    tmp_path: Path,
) -> None:
    """A live daemon lock must make marker archival impossible."""

    project = installation(tmp_path)
    lock_descriptor = os.open(project / reconcile.LOCK_BASENAME, os.O_RDWR)
    fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(reconcile.BotStillRunningError):
            run_reconciliation(project)
    finally:
        os.close(lock_descriptor)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    assert not (project / reconcile.DEFAULT_ARCHIVE_BASENAME).exists()


def test_reconciliation_refuses_daemon_abstract_singleton_without_flock(
    tmp_path: Path,
) -> None:
    """The non-filesystem daemon singleton excludes path-replacement races."""

    project = installation(tmp_path)
    singleton = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    singleton.bind(reconcile.instance_lock_abstract_socket_name(project))
    try:
        with pytest.raises(reconcile.BotStillRunningError, match="singleton"):
            run_reconciliation(project)
    finally:
        singleton.close()

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    assert not (project / reconcile.DEFAULT_ARCHIVE_BASENAME).exists()


def test_reconciliation_refuses_daemon_ofd_lock_without_flock(
    tmp_path: Path,
) -> None:
    """An OFD owner excludes reconciliation even if BSD flock is available."""

    project = installation(tmp_path)
    descriptor = os.open(project / reconcile.LOCK_BASENAME, os.O_RDWR)
    fcntl.fcntl(
        descriptor,
        fcntl.F_OFD_SETLK,
        reconcile._ofd_lock_record(fcntl.F_WRLCK),
    )
    try:
        with pytest.raises(reconcile.BotStillRunningError, match="OFD"):
            run_reconciliation(project)
    finally:
        os.close(descriptor)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    assert not (project / reconcile.DEFAULT_ARCHIVE_BASENAME).exists()


def test_directory_inode_lock_blocks_reconciler_after_lock_path_replacement(
    tmp_path: Path,
) -> None:
    """A cross-netns/path replacement cannot bypass the directory boundary."""

    project = installation(tmp_path)
    directory_owner = os.open(
        project,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    fcntl.flock(
        directory_owner,
        fcntl.LOCK_EX | fcntl.LOCK_NB,
    )
    original_lock = project / reconcile.LOCK_BASENAME
    displaced = project / "daemon-owned-lock"
    original_lock.rename(displaced)
    original_lock.write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    try:
        with pytest.raises(
            reconcile.BotStillRunningError,
            match="state-directory",
        ):
            run_reconciliation(project)
    finally:
        os.close(directory_owner)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    assert not (project / reconcile.DEFAULT_ARCHIVE_BASENAME).exists()


def test_instance_singleton_name_is_directory_identity_bound_and_deterministic(
    tmp_path: Path,
) -> None:
    """Equivalent roots share one name while different roots do not."""

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    assert reconcile.instance_lock_abstract_socket_name(first) == (
        reconcile.instance_lock_abstract_socket_name(first / ".")
    )
    assert reconcile.instance_lock_abstract_socket_name(first) != (
        reconcile.instance_lock_abstract_socket_name(second)
    )


def test_real_path_reconciler_cannot_bypass_daemon_symlink_alias_singleton(
    tmp_path: Path,
) -> None:
    """Path aliases for one inode must share the exact process singleton."""

    project = installation(tmp_path)
    alias = tmp_path / "bot-alias"
    alias.symlink_to(project, target_is_directory=True)
    assert reconcile.instance_lock_abstract_socket_name(alias) == (
        reconcile.instance_lock_abstract_socket_name(project)
    )

    singleton = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    singleton.bind(reconcile.instance_lock_abstract_socket_name(alias))
    try:
        with pytest.raises(reconcile.BotStillRunningError, match="singleton"):
            run_reconciliation(project)
    finally:
        singleton.close()

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    assert not (project / reconcile.DEFAULT_ARCHIVE_BASENAME).exists()


def test_socket_cleanup_failure_does_not_mask_hard_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup must preserve the original hard process-control exception."""

    class HardProcessExit(BaseException):
        pass

    class FailingSocket:
        def bind(self, _name: bytes) -> None:
            raise HardProcessExit("original hard exit")

        def close(self) -> None:
            raise OSError("injected cleanup close failure")

    project = installation(tmp_path)
    monkeypatch.setattr(
        reconcile.socket,
        "socket",
        lambda *_args, **_kwargs: FailingSocket(),
    )
    with pytest.raises(HardProcessExit, match="original hard exit"):
        run_reconciliation(project)


def test_archive_directory_fstat_failure_does_not_leak_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hard exit after archive open closes the not-yet-returned descriptor."""

    class HardProcessExit(BaseException):
        pass

    project = installation(tmp_path)
    original_open = reconcile.os.open
    original_fstat = reconcile.os.fstat
    archive_descriptors: list[int] = []

    def capture_archive_open(path: object, *args: object, **kwargs: object) -> int:
        descriptor = original_open(path, *args, **kwargs)
        if path == reconcile.DEFAULT_ARCHIVE_BASENAME:
            archive_descriptors.append(descriptor)
        return descriptor

    def fail_archive_fstat(descriptor: int):
        if descriptor in archive_descriptors:
            raise HardProcessExit("archive identity inspection interrupted")
        return original_fstat(descriptor)

    monkeypatch.setattr(reconcile.os, "open", capture_archive_open)
    monkeypatch.setattr(reconcile.os, "fstat", fail_archive_fstat)
    with pytest.raises(HardProcessExit, match="identity inspection"):
        run_reconciliation(project)

    assert len(archive_descriptors) == 1
    with pytest.raises(OSError):
        original_fstat(archive_descriptors[0])


def test_reconciliation_archives_exact_inode_before_removing_active_marker(
    tmp_path: Path,
) -> None:
    """Successful offline archival preserves identity and writes a durable receipt."""

    project = installation(tmp_path)
    source = project / reconcile.MARKER_BASENAME
    source_stat = source.stat()

    result = run_reconciliation(project)

    archive = project / result.archive_path
    receipt = project / result.receipt_path
    assert not source.exists()
    assert archive.read_bytes() == MARKER_BYTES
    assert (archive.stat().st_dev, archive.stat().st_ino) == (
        source_stat.st_dev,
        source_stat.st_ino,
    )
    assert stat.S_IMODE(archive.stat().st_mode) == 0o400
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o400
    receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_value == result.to_dict()
    assert receipt_value["marker_sha256"] == MARKER_SHA256
    assert receipt_value["archive_inode_preserved"] is True
    assert (
        receipt_value["archive_and_receipt_durable_before_source_removal"]
        is True
    )
    assert receipt_value["active_marker_removal_is_final_transition"] is True
    assert receipt_value["successful_return_requires_source_absent"] is True


def test_reconciliation_retires_exact_successor_pair_last(
    tmp_path: Path,
) -> None:
    """The reviewed pair is archived before its successor is retired last."""

    project = installation(tmp_path)
    marker = project / reconcile.MARKER_BASENAME
    successor = project / reconcile.RESTART_BARRIER_BASENAME
    os.link(marker, successor, follow_symlinks=False)
    original_identity = marker.stat()

    result = run_reconciliation(project)

    archive = project / result.archive_path
    receipt = project / result.receipt_path
    assert result.schema_version == 3
    assert result.active_barrier_names == (
        reconcile.MARKER_BASENAME,
        reconcile.RESTART_BARRIER_BASENAME,
    )
    assert result.restart_barrier_present_before_reconciliation is True
    assert result.active_marker_removal_is_final_transition is False
    assert result.restart_barrier_retired_last is True
    assert result.successful_return_requires_all_active_barriers_absent is True
    assert not marker.exists()
    assert not successor.exists()
    assert archive.read_bytes() == MARKER_BYTES
    assert archive.stat().st_nlink == 1
    assert (archive.stat().st_dev, archive.stat().st_ino) == (
        original_identity.st_dev,
        original_identity.st_ino,
    )
    assert json.loads(receipt.read_text(encoding="utf-8")) == result.to_dict()


def test_reconciliation_accepts_exact_successor_only_survivor(
    tmp_path: Path,
) -> None:
    """A successor which survived legacy-name loss remains reconcilable."""

    project = installation(tmp_path)
    marker = project / reconcile.MARKER_BASENAME
    successor = project / reconcile.RESTART_BARRIER_BASENAME
    os.link(marker, successor, follow_symlinks=False)
    marker.unlink()

    result = run_reconciliation(project)

    assert result.source_marker == reconcile.RESTART_BARRIER_BASENAME
    assert result.active_barrier_names == (
        reconcile.RESTART_BARRIER_BASENAME,
    )
    assert result.restart_barrier_present_before_reconciliation is True
    assert result.restart_barrier_retired_last is True
    assert not marker.exists()
    assert not successor.exists()
    assert (project / result.archive_path).read_bytes() == MARKER_BYTES


def test_reconciliation_rejects_split_marker_and_successor_inodes(
    tmp_path: Path,
) -> None:
    """Two expected names cannot disguise two unrelated incident files."""

    project = installation(tmp_path)
    successor = project / reconcile.RESTART_BARRIER_BASENAME
    successor.write_bytes(MARKER_BYTES)

    with pytest.raises(
        reconcile.UnsafeReconciliationPathError,
        match="same-inode",
    ):
        run_reconciliation(project)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    assert successor.read_bytes() == MARKER_BYTES


def test_reconciliation_rejects_wrong_hash_without_moving_marker(
    tmp_path: Path,
) -> None:
    """An operator must bind archival to the exact independently reviewed bytes."""

    project = installation(tmp_path)
    with pytest.raises(reconcile.MarkerIdentityError, match="differs"):
        reconcile.reconcile_marker_offline(
            project_root=project,
            expected_marker_sha256="0" * 64,
            reconciliation_reference="wrong review",
        )

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES


@pytest.mark.parametrize("entry", ["marker", "lock", "archive"])
def test_reconciliation_never_follows_operational_symlinks(
    tmp_path: Path,
    entry: str,
) -> None:
    """Every mutable filesystem boundary must reject a symbolic link."""

    project = installation(tmp_path)
    target = project / "unrelated"
    target.write_bytes(MARKER_BYTES)
    if entry == "marker":
        (project / reconcile.MARKER_BASENAME).unlink()
        (project / reconcile.MARKER_BASENAME).symlink_to(target)
    elif entry == "lock":
        (project / reconcile.LOCK_BASENAME).unlink()
        (project / reconcile.LOCK_BASENAME).symlink_to(target)
    else:
        (project / reconcile.DEFAULT_ARCHIVE_BASENAME).symlink_to(target)

    with pytest.raises(reconcile.UnsafeReconciliationPathError):
        run_reconciliation(project)


def test_reconciliation_rejects_project_path_with_symlink_component(
    tmp_path: Path,
) -> None:
    """The project root itself cannot be reached through a symbolic link."""

    project = installation(tmp_path)
    alias = tmp_path / "bot-alias"
    alias.symlink_to(project, target_is_directory=True)

    with pytest.raises(reconcile.UnsafeReconciliationPathError):
        run_reconciliation(alias)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES


def test_reconciliation_rejects_parent_traversal_before_symlink_normalisation(
    tmp_path: Path,
) -> None:
    """A symlink/.. spelling cannot redirect reconciliation to another tree."""

    lexical_project = installation(tmp_path)
    target_parent = tmp_path / "target-parent"
    target_parent.mkdir()
    (target_parent / "child").mkdir()
    resolved_project = target_parent / "bot"
    resolved_project.mkdir()
    (resolved_project / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="ascii",
    )
    (resolved_project / reconcile.MARKER_BASENAME).write_bytes(MARKER_BYTES)
    alias = tmp_path / "alias"
    alias.symlink_to(target_parent / "child", target_is_directory=True)
    supplied = alias / ".." / "bot"

    assert supplied.stat().st_ino == resolved_project.stat().st_ino
    assert supplied.stat().st_ino != lexical_project.stat().st_ino
    with pytest.raises(
        reconcile.UnsafeReconciliationPathError,
        match="parent-directory traversal",
    ):
        run_reconciliation(supplied)

    assert (
        lexical_project / reconcile.MARKER_BASENAME
    ).read_bytes() == MARKER_BYTES
    assert (
        resolved_project / reconcile.MARKER_BASENAME
    ).read_bytes() == MARKER_BYTES


@pytest.mark.parametrize("entry", ["marker", "lock"])
def test_reconciliation_rejects_hard_linked_operational_files(
    tmp_path: Path,
    entry: str,
) -> None:
    """Archival must not chmod aliases or trust an aliased lock namespace."""

    project = installation(tmp_path)
    selected = project / (
        reconcile.MARKER_BASENAME
        if entry == "marker"
        else reconcile.LOCK_BASENAME
    )
    original_mode = stat.S_IMODE(selected.stat().st_mode)
    alias = project / f"{entry}-hard-link"
    os.link(selected, alias)

    with pytest.raises(
        reconcile.UnsafeReconciliationPathError,
        match="exactly one filesystem link",
    ):
        run_reconciliation(project)

    assert selected.exists()
    assert alias.exists()
    assert selected.stat().st_ino == alias.stat().st_ino
    assert stat.S_IMODE(alias.stat().st_mode) == original_mode
    assert not (project / reconcile.DEFAULT_ARCHIVE_BASENAME).exists()


def test_lock_path_replacement_after_acquisition_preserves_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock on an unlinked predecessor inode is not proof the daemon is offline."""

    project = installation(tmp_path)
    original_write_receipt_temp = reconcile._write_receipt_temp
    replacements: list[Path] = []

    def write_receipt_then_replace_lock(
        archive_fd: int,
        basename: str,
        payload: dict[str, object],
    ) -> tuple[str, int, os.stat_result]:
        temporary = original_write_receipt_temp(archive_fd, basename, payload)
        lock_path = project / reconcile.LOCK_BASENAME
        displaced = project / "displaced-instance-lock"
        lock_path.rename(displaced)
        lock_path.write_text(
            f"pid={STOPPED_DAEMON_PID}\n",
            encoding="utf-8",
        )
        replacements.append(displaced)
        return temporary

    monkeypatch.setattr(
        reconcile,
        "_write_receipt_temp",
        write_receipt_then_replace_lock,
    )

    with pytest.raises(
        reconcile.UnsafeReconciliationPathError,
        match="identity changed",
    ):
        run_reconciliation(project)

    assert replacements == [project / "displaced-instance-lock"]
    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert not list(archive.glob("ambiguous_post_outcome.*"))


def test_project_path_replacement_after_acquisition_preserves_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A receipt must never name a project path rebound to another directory."""

    project = installation(tmp_path)
    displaced = tmp_path / "bot-displaced"
    original_write_receipt_temp = reconcile._write_receipt_temp

    def write_receipt_then_replace_project(
        archive_fd: int,
        basename: str,
        payload: dict[str, object],
    ) -> tuple[str, int, os.stat_result]:
        temporary = original_write_receipt_temp(archive_fd, basename, payload)
        project.rename(displaced)
        project.mkdir()
        return temporary

    monkeypatch.setattr(
        reconcile,
        "_write_receipt_temp",
        write_receipt_then_replace_project,
    )

    with pytest.raises(
        reconcile.UnsafeReconciliationPathError,
        match="project-root pathname identity changed",
    ):
        run_reconciliation(project)

    assert (displaced / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    archive = displaced / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert not list(archive.glob("*.reconciliation.json"))
    assert not list(archive.glob("ambiguous_post_outcome.*.json"))


def test_archive_path_replacement_after_acquisition_preserves_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A receipt must never name a rebound archive directory."""

    project = installation(tmp_path)
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    displaced = project / "archive-displaced"
    original_write_receipt_temp = reconcile._write_receipt_temp

    def write_receipt_then_replace_archive(
        archive_fd: int,
        basename: str,
        payload: dict[str, object],
    ) -> tuple[str, int, os.stat_result]:
        temporary = original_write_receipt_temp(archive_fd, basename, payload)
        archive.rename(displaced)
        archive.mkdir(mode=0o700)
        return temporary

    monkeypatch.setattr(
        reconcile,
        "_write_receipt_temp",
        write_receipt_then_replace_archive,
    )

    with pytest.raises(
        reconcile.UnsafeReconciliationPathError,
        match="archive pathname identity changed",
    ):
        run_reconciliation(project)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    assert not list(displaced.glob("*.reconciliation.json"))
    assert not list(displaced.glob("ambiguous_post_outcome.*.json"))
    assert not list(archive.iterdir())


def test_project_path_replacement_after_receipt_commit_preserves_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Late lexical rebinding cannot produce a receipt naming another tree."""

    project = installation(tmp_path)
    displaced = tmp_path / "bot-displaced-late"
    original_rename_noreplace = reconcile._rename_noreplace
    replaced: list[Path] = []

    def finalize_receipt_then_replace_project(
        source_basename: str,
        destination_basename: str,
        *,
        source_directory_fd: int,
        destination_directory_fd: int,
        label: str,
    ) -> None:
        original_rename_noreplace(
            source_basename,
            destination_basename,
            source_directory_fd=source_directory_fd,
            destination_directory_fd=destination_directory_fd,
            label=label,
        )
        if destination_basename.endswith(".reconciliation.json") and not replaced:
            project.rename(displaced)
            project.mkdir()
            replaced.append(displaced)

    monkeypatch.setattr(
        reconcile,
        "_rename_noreplace",
        finalize_receipt_then_replace_project,
    )
    with pytest.raises(
        reconcile.UnsafeReconciliationPathError,
        match="project-root pathname identity changed",
    ):
        run_reconciliation(project)

    assert replaced == [displaced]
    assert (displaced / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    archive = displaced / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert not list(archive.glob("*.reconciliation.json"))
    assert not list(archive.glob("ambiguous_post_outcome.*.json"))


def test_archive_path_replacement_after_receipt_sync_preserves_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Late archive rebinding cannot make a displaced receipt look committed."""

    project = installation(tmp_path)
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    archive.mkdir(mode=0o700)
    displaced = project / "archive-displaced-late"
    archive_identity = archive.stat()
    original_fsync_directory = reconcile._fsync_directory
    archive_syncs = 0

    def sync_then_replace_archive(descriptor: int) -> None:
        nonlocal archive_syncs
        original_fsync_directory(descriptor)
        opened = os.fstat(descriptor)
        if (
            opened.st_dev == archive_identity.st_dev
            and opened.st_ino == archive_identity.st_ino
        ):
            archive_syncs += 1
            if archive_syncs == 2:
                archive.rename(displaced)
                archive.mkdir(mode=0o700)

    monkeypatch.setattr(
        reconcile,
        "_fsync_directory",
        sync_then_replace_archive,
    )
    with pytest.raises(
        reconcile.UnsafeReconciliationPathError,
        match="archive pathname identity changed",
    ):
        run_reconciliation(project)

    assert archive_syncs >= 2
    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    assert not list(displaced.glob("*.reconciliation.json"))
    assert not list(displaced.glob("ambiguous_post_outcome.*.json"))
    assert not list(archive.iterdir())


def test_lost_reconciler_ofd_lock_is_detected_before_marker_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-descriptor GETLK cannot hide loss of the exclusive OFD lock."""

    project = installation(tmp_path)
    original_fcntl = reconcile.fcntl.fcntl
    unlocked: list[int] = []

    def unlock_before_first_getlk(
        descriptor: int,
        command: int,
        argument: object = 0,
    ):
        if command == fcntl.F_OFD_GETLK and not unlocked:
            original_fcntl(
                descriptor,
                fcntl.F_OFD_SETLK,
                reconcile._ofd_lock_record(fcntl.F_UNLCK),
            )
            unlocked.append(descriptor)
        return original_fcntl(descriptor, command, argument)

    monkeypatch.setattr(reconcile.fcntl, "fcntl", unlock_before_first_getlk)
    with pytest.raises(reconcile.BotStillRunningError, match="exclusive OFD"):
        run_reconciliation(project)

    assert len(unlocked) == 1
    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert not list(archive.glob("ambiguous_post_outcome.*"))


def test_other_directory_flock_cannot_masquerade_as_reconciler_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact fdinfo ownership prevents another directory fd hiding lock loss."""

    project = installation(tmp_path)
    original_revalidate = reconcile._revalidate_locked_instance_lock
    injected: list[int] = []

    def replace_directory_owner(
        project_fd: int,
        lock_fd: int,
        expected: os.stat_result,
        instance_socket: socket.socket,
        expected_socket_name: bytes,
    ) -> None:
        fcntl.flock(project_fd, fcntl.LOCK_UN)
        replacement_owner = os.open(
            ".",
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            dir_fd=project_fd,
        )
        fcntl.flock(
            replacement_owner,
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
        injected.append(replacement_owner)
        try:
            original_revalidate(
                project_fd,
                lock_fd,
                expected,
                instance_socket,
                expected_socket_name,
            )
        finally:
            os.close(replacement_owner)

    monkeypatch.setattr(
        reconcile,
        "_revalidate_locked_instance_lock",
        replace_directory_owner,
    )
    with pytest.raises(
        reconcile.BotStillRunningError,
        match="state-directory lock",
    ):
        run_reconciliation(project)

    assert len(injected) == 1
    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert not list(archive.glob("ambiguous_post_outcome.*"))


@pytest.mark.parametrize("failure", ["unavailable", "malformed"])
def test_reconciler_fdinfo_proof_failure_refuses_marker_retirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Missing or unparseable Linux fdinfo cannot authorise offline removal."""

    project = installation(tmp_path)
    original_read_text = Path.read_text

    def controlled_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if str(path).startswith("/proc/self/fdinfo/"):
            if failure == "unavailable":
                raise OSError("injected unavailable fdinfo")
            return "lock: malformed\n"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", controlled_read_text)
    with pytest.raises(
        reconcile.BotStillRunningError,
        match="state-directory flock acquisition could not be proved",
    ):
        run_reconciliation(project)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    assert not (project / reconcile.DEFAULT_ARCHIVE_BASENAME).exists()


def test_lost_reconciler_socket_is_detected_before_marker_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The supplementary singleton must remain bound through reconciliation."""

    project = installation(tmp_path)
    real_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    class SocketWrapper:
        def bind(self, name: bytes) -> None:
            real_socket.bind(name)

        def getsockname(self):
            raise OSError("injected singleton loss")

        def close(self) -> None:
            real_socket.close()

    monkeypatch.setattr(
        reconcile.socket,
        "socket",
        lambda *_args, **_kwargs: SocketWrapper(),
    )
    with pytest.raises(reconcile.BotStillRunningError, match="singleton"):
        run_reconciliation(project)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert not list(archive.glob("ambiguous_post_outcome.*"))


def test_live_recorded_daemon_pid_refuses_marker_retirement(tmp_path: Path) -> None:
    """A live PID record remains a fail-closed veto even if flock is available."""

    project = installation(tmp_path)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    try:
        (project / reconcile.LOCK_BASENAME).write_text(
            f"pid={child.pid}\n",
            encoding="utf-8",
        )
        with pytest.raises(reconcile.BotStillRunningError, match="still live"):
            run_reconciliation(project)
    finally:
        child.terminate()
        child.wait(timeout=10)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert not list(archive.glob("ambiguous_post_outcome.*"))


def test_existing_archive_collision_preserves_source_marker(tmp_path: Path) -> None:
    """A prior archive must never be overwritten silently."""

    project = installation(tmp_path)
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    archive.mkdir(mode=0o700)
    existing = archive / f"ambiguous_post_outcome.{MARKER_SHA256}.json"
    existing.write_bytes(b"prior archive\n")

    with pytest.raises(reconcile.ArchiveCommitError, match="already"):
        run_reconciliation(project)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    assert existing.read_bytes() == b"prior archive\n"


def test_archive_collision_inserted_after_precheck_is_never_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final archive operation must enforce no-replace atomically."""

    project = installation(tmp_path)
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    archive.mkdir(mode=0o700)
    archive_name = f"ambiguous_post_outcome.{MARKER_SHA256}.json"
    collision_bytes = b"concurrent prior archive\n"
    original_link_noreplace = reconcile._link_noreplace
    injected: list[str] = []

    def inject_archive_collision(
        source_basename: str,
        destination_basename: str,
        *,
        source_directory_fd: int,
        destination_directory_fd: int,
        label: str,
    ) -> None:
        if (
            source_basename == reconcile.MARKER_BASENAME
            and destination_basename == archive_name
            and not injected
        ):
            descriptor = os.open(
                destination_basename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=destination_directory_fd,
            )
            try:
                os.write(descriptor, collision_bytes)
            finally:
                os.close(descriptor)
            injected.append(destination_basename)
        original_link_noreplace(
            source_basename,
            destination_basename,
            source_directory_fd=source_directory_fd,
            destination_directory_fd=destination_directory_fd,
            label=label,
        )

    monkeypatch.setattr(
        reconcile,
        "_link_noreplace",
        inject_archive_collision,
    )

    with pytest.raises(reconcile.ArchiveCommitError, match="already exists"):
        run_reconciliation(project)

    assert injected == [archive_name]
    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    assert (archive / archive_name).read_bytes() == collision_bytes
    assert not list(archive.glob("*.reconciliation.json"))


def test_same_inode_archive_collision_is_never_claimed_or_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raced same-inode link is a collision, not this invocation's property."""

    project = installation(tmp_path)
    marker = project / reconcile.MARKER_BASENAME
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    archive.mkdir(mode=0o700)
    archive_name = f"ambiguous_post_outcome.{MARKER_SHA256}.json"
    original_link_noreplace = reconcile._link_noreplace
    injected: list[str] = []

    def inject_same_inode_collision(
        source_basename: str,
        destination_basename: str,
        *,
        source_directory_fd: int,
        destination_directory_fd: int,
        label: str,
    ) -> None:
        if destination_basename == archive_name and not injected:
            os.link(
                source_basename,
                destination_basename,
                src_dir_fd=source_directory_fd,
                dst_dir_fd=destination_directory_fd,
                follow_symlinks=False,
            )
            injected.append(destination_basename)
        original_link_noreplace(
            source_basename,
            destination_basename,
            source_directory_fd=source_directory_fd,
            destination_directory_fd=destination_directory_fd,
            label=label,
        )

    monkeypatch.setattr(
        reconcile,
        "_link_noreplace",
        inject_same_inode_collision,
    )
    with pytest.raises(reconcile.ArchiveCommitError, match="already exists"):
        run_reconciliation(project)

    collision = archive / archive_name
    assert injected == [archive_name]
    assert collision.exists()
    assert collision.stat().st_ino == marker.stat().st_ino
    assert marker.stat().st_nlink == 2
    assert not list(archive.glob("*.reconciliation.json"))
    assert not list(archive.glob(".*.tmp.*"))


def test_receipt_collision_inserted_after_precheck_is_never_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Receipt finalisation must refuse a name raced in after marker archival."""

    project = installation(tmp_path)
    archive_name = f"ambiguous_post_outcome.{MARKER_SHA256}.json"
    receipt_name = f"{archive_name}.reconciliation.json"
    collision_bytes = b"concurrent prior receipt\n"
    original_rename_noreplace = reconcile._rename_noreplace
    injected: list[str] = []

    def inject_receipt_collision(
        source_basename: str,
        destination_basename: str,
        *,
        source_directory_fd: int,
        destination_directory_fd: int,
        label: str,
    ) -> None:
        if destination_basename == receipt_name and not injected:
            descriptor = os.open(
                destination_basename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=destination_directory_fd,
            )
            try:
                os.write(descriptor, collision_bytes)
            finally:
                os.close(descriptor)
            injected.append(destination_basename)
        original_rename_noreplace(
            source_basename,
            destination_basename,
            source_directory_fd=source_directory_fd,
            destination_directory_fd=destination_directory_fd,
            label=label,
        )

    monkeypatch.setattr(
        reconcile,
        "_rename_noreplace",
        inject_receipt_collision,
    )

    with pytest.raises(reconcile.ArchiveCommitError, match="already exists"):
        run_reconciliation(project)

    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert injected == [receipt_name]
    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    assert not (archive / archive_name).exists()
    assert (archive / receipt_name).read_bytes() == collision_bytes
    assert not list(archive.glob(".*.tmp.*"))


def test_replaced_temporary_receipt_is_rejected_and_not_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creation identity, not a reused temporary basename, owns cleanup."""

    project = installation(tmp_path)
    original_write_receipt_temp = reconcile._write_receipt_temp
    replacement_bytes: list[bytes] = []

    def replace_temporary_receipt(
        archive_fd: int,
        basename: str,
        payload: dict[str, object],
    ) -> tuple[str, int, os.stat_result]:
        (
            temporary,
            original_descriptor,
            original_identity,
        ) = original_write_receipt_temp(
            archive_fd,
            basename,
            payload,
        )
        os.unlink(temporary, dir_fd=archive_fd)
        content = reconcile._canonical_json_bytes(payload)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=archive_fd,
        )
        try:
            assert os.write(descriptor, content) == len(content)
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        replacement_bytes.append(content)
        return temporary, original_descriptor, original_identity

    monkeypatch.setattr(
        reconcile,
        "_write_receipt_temp",
        replace_temporary_receipt,
    )
    with pytest.raises(
        reconcile.ArchiveCommitError,
        match="receipt identity, mode or bytes changed",
    ):
        run_reconciliation(project)

    marker = project / reconcile.MARKER_BASENAME
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    temporary_entries = list(archive.glob(".*.tmp.*"))
    assert marker.read_bytes() == MARKER_BYTES
    assert marker.stat().st_nlink == 1
    assert len(temporary_entries) == 1
    assert temporary_entries[0].read_bytes() == replacement_bytes[0]
    assert stat.S_IMODE(temporary_entries[0].stat().st_mode) == 0o600


def test_final_receipt_mode_change_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The committed audit receipt must remain the bound read-only inode."""

    project = installation(tmp_path)
    original_rename_noreplace = reconcile._rename_noreplace

    def rename_then_make_writable(
        source_basename: str,
        destination_basename: str,
        *,
        source_directory_fd: int,
        destination_directory_fd: int,
        label: str,
    ) -> None:
        original_rename_noreplace(
            source_basename,
            destination_basename,
            source_directory_fd=source_directory_fd,
            destination_directory_fd=destination_directory_fd,
            label=label,
        )
        os.chmod(destination_basename, 0o600, dir_fd=destination_directory_fd)

    monkeypatch.setattr(
        reconcile,
        "_rename_noreplace",
        rename_then_make_writable,
    )
    with pytest.raises(
        reconcile.ArchiveCommitError,
        match="receipt identity, mode or bytes changed",
    ):
        run_reconciliation(project)

    marker = project / reconcile.MARKER_BASENAME
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert marker.read_bytes() == MARKER_BYTES
    assert marker.stat().st_nlink == 1
    assert not list(archive.glob("ambiguous_post_outcome.*"))


def test_final_receipt_identity_change_is_rejected_and_not_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replacement at the final basename cannot inherit receipt authority."""

    project = installation(tmp_path)
    original_rename_noreplace = reconcile._rename_noreplace
    replacement = b"unrelated replacement receipt\n"

    def rename_then_replace_receipt(
        source_basename: str,
        destination_basename: str,
        *,
        source_directory_fd: int,
        destination_directory_fd: int,
        label: str,
    ) -> None:
        original_rename_noreplace(
            source_basename,
            destination_basename,
            source_directory_fd=source_directory_fd,
            destination_directory_fd=destination_directory_fd,
            label=label,
        )
        os.unlink(destination_basename, dir_fd=destination_directory_fd)
        descriptor = os.open(
            destination_basename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o400,
            dir_fd=destination_directory_fd,
        )
        try:
            assert os.write(descriptor, replacement) == len(replacement)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    monkeypatch.setattr(
        reconcile,
        "_rename_noreplace",
        rename_then_replace_receipt,
    )
    with pytest.raises(
        reconcile.ArchiveCommitError,
        match="receipt identity, mode or bytes changed",
    ):
        run_reconciliation(project)

    marker = project / reconcile.MARKER_BASENAME
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    receipt = archive / (
        f"ambiguous_post_outcome.{MARKER_SHA256}.json.reconciliation.json"
    )
    assert marker.read_bytes() == MARKER_BYTES
    assert marker.stat().st_nlink == 1
    assert receipt.read_bytes() == replacement
    assert not (
        archive / f"ambiguous_post_outcome.{MARKER_SHA256}.json"
    ).exists()


def test_reconciliation_rejects_non_private_archive_directory(
    tmp_path: Path,
) -> None:
    """Archived incident evidence cannot be placed in a shared directory."""

    project = installation(tmp_path)
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    archive.mkdir(mode=0o755)
    # mkdir's mode is filtered by the caller's umask; this fixture must be shared.
    archive.chmod(0o755)

    with pytest.raises(reconcile.UnsafeReconciliationPathError, match="private"):
        run_reconciliation(project)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES


def test_archive_finalisation_failure_restores_marker_and_removes_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late receipt-directory failure must not leave a false success record."""

    project = installation(tmp_path)
    real_fsync_directory = reconcile._fsync_directory
    calls = 0

    def fail_last_archive_sync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("final archive-directory fsync failed")
        real_fsync_directory(descriptor)

    monkeypatch.setattr(reconcile, "_fsync_directory", fail_last_archive_sync)
    with pytest.raises(reconcile.ArchiveCommitError):
        run_reconciliation(project)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert not list(archive.glob("ambiguous_post_outcome.*"))


def test_hard_exit_before_marker_move_is_not_converted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-move process exit must propagate unchanged with the marker intact."""

    class HardProcessExit(BaseException):
        pass

    project = installation(tmp_path)

    def hard_exit_before_receipt(
        _archive_fd: int,
        _basename: str,
        _payload: dict[str, object],
    ) -> str:
        raise HardProcessExit

    monkeypatch.setattr(
        reconcile,
        "_write_receipt_temp",
        hard_exit_before_receipt,
    )

    with pytest.raises(HardProcessExit):
        run_reconciliation(project)

    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert not list(archive.glob("ambiguous_post_outcome.*"))


@pytest.mark.parametrize(
    ("exit_after_archive_sync", "receipt_expected"),
    [(1, False), (2, True)],
)
def test_uncatchable_exit_before_active_marker_removal_keeps_barrier(
    tmp_path: Path,
    exit_after_archive_sync: int,
    receipt_expected: bool,
) -> None:
    """Real process loss cannot remove the barrier before durable evidence."""

    project = installation(tmp_path)
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    archive.mkdir(mode=0o700)
    archive_name = f"ambiguous_post_outcome.{MARKER_SHA256}.json"
    receipt_name = f"{archive_name}.reconciliation.json"
    child_code = textwrap.dedent(
        f"""
        import os
        from pathlib import Path
        from tools import reconcile_remote_write_safety_marker as reconcile

        original = reconcile._fsync_directory
        calls = 0

        def exit_after_durable_archive_step(descriptor):
            global calls
            original(descriptor)
            calls += 1
            if calls == {exit_after_archive_sync}:
                os._exit(77)

        reconcile._fsync_directory = exit_after_durable_archive_step
        reconcile.reconcile_marker_offline(
            project_root=Path({str(project)!r}),
            expected_marker_sha256={MARKER_SHA256!r},
            reconciliation_reference="uncatchable-exit-test",
            now=lambda: 1_800_000_000,
        )
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", child_code],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 77
    source = project / reconcile.MARKER_BASENAME
    archived = archive / archive_name
    assert source.read_bytes() == MARKER_BYTES
    assert archived.read_bytes() == MARKER_BYTES
    assert source.stat().st_ino == archived.stat().st_ino
    assert (archive / receipt_name).exists() is receipt_expected


def test_interrupt_before_archive_link_return_preserves_unowned_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A link is not cleanup-owned until the no-replace helper returns."""

    project = installation(tmp_path)
    original_link_noreplace = reconcile._link_noreplace

    def link_then_interrupt(*args: object, **kwargs: object) -> None:
        original_link_noreplace(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(reconcile, "_link_noreplace", link_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_reconciliation(project)

    source = project / reconcile.MARKER_BASENAME
    assert source.read_bytes() == MARKER_BYTES
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    archived = archive / f"ambiguous_post_outcome.{MARKER_SHA256}.json"
    assert archived.read_bytes() == MARKER_BYTES
    assert archived.stat().st_ino == source.stat().st_ino
    assert source.stat().st_nlink == 2
    assert not list(archive.glob("*.reconciliation.json"))


def test_interrupt_after_receipt_rename_removes_only_own_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback discovers a completed rename before its bookkeeping assignment."""

    project = installation(tmp_path)
    original_rename_noreplace = reconcile._rename_noreplace

    def rename_then_interrupt(*args: object, **kwargs: object) -> None:
        original_rename_noreplace(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(reconcile, "_rename_noreplace", rename_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_reconciliation(project)

    source = project / reconcile.MARKER_BASENAME
    assert source.read_bytes() == MARKER_BYTES
    assert source.stat().st_nlink == 1
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert not list(archive.glob("ambiguous_post_outcome.*"))


def test_hard_exit_after_archive_link_rolls_back_then_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-link hard exit gets best-effort rollback without type conversion."""

    class HardProcessExit(BaseException):
        pass

    project = installation(tmp_path)
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    archive.mkdir(mode=0o700)
    marker = project / reconcile.MARKER_BASENAME
    original_mode = stat.S_IMODE(marker.stat().st_mode)
    original_fsync_directory = reconcile._fsync_directory
    calls = 0

    def hard_exit_on_first_post_move_sync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HardProcessExit
        original_fsync_directory(descriptor)

    monkeypatch.setattr(
        reconcile,
        "_fsync_directory",
        hard_exit_on_first_post_move_sync,
    )

    with pytest.raises(HardProcessExit):
        run_reconciliation(project)

    assert calls >= 3
    assert marker.read_bytes() == MARKER_BYTES
    assert stat.S_IMODE(marker.stat().st_mode) == original_mode
    assert not list(archive.glob("ambiguous_post_outcome.*"))


def test_interrupt_after_source_unlink_restores_exact_active_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback inspects the namespace instead of trusting an assignment flag."""

    project = installation(tmp_path)
    source = project / reconcile.MARKER_BASENAME
    original_inode = source.stat().st_ino
    original_unlink = os.unlink

    def unlink_then_interrupt(
        path: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        original_unlink(path, *args, **kwargs)
        if path == reconcile.MARKER_BASENAME and kwargs.get("dir_fd") is not None:
            raise KeyboardInterrupt

    monkeypatch.setattr(reconcile.os, "unlink", unlink_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_reconciliation(project)

    assert source.read_bytes() == MARKER_BYTES
    assert source.stat().st_ino == original_inode
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    assert not list(archive.glob("ambiguous_post_outcome.*"))


def test_hard_exit_after_source_removal_remains_original_if_rollback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rollback error must not convert a hard process exit into RuntimeError."""

    class HardProcessExit(BaseException):
        pass

    project = installation(tmp_path)
    archive = project / reconcile.DEFAULT_ARCHIVE_BASENAME
    archive.mkdir(mode=0o700)
    original_fsync_directory = reconcile._fsync_directory
    original_link_noreplace = reconcile._link_noreplace
    sync_calls = 0

    def hard_exit_after_move(descriptor: int) -> None:
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 3:
            raise HardProcessExit("simulated process termination")
        original_fsync_directory(descriptor)

    def fail_only_rollback(
        source_basename: str,
        destination_basename: str,
        *,
        source_directory_fd: int,
        destination_directory_fd: int,
        label: str,
    ) -> None:
        if source_basename.startswith("ambiguous_post_outcome.") and (
            destination_basename == reconcile.MARKER_BASENAME
        ):
            raise OSError("simulated rollback failure")
        original_link_noreplace(
            source_basename,
            destination_basename,
            source_directory_fd=source_directory_fd,
            destination_directory_fd=destination_directory_fd,
            label=label,
        )

    monkeypatch.setattr(reconcile, "_fsync_directory", hard_exit_after_move)
    monkeypatch.setattr(reconcile, "_link_noreplace", fail_only_rollback)

    with pytest.raises(HardProcessExit):
        run_reconciliation(project)

    assert sync_calls >= 3
    assert not (project / reconcile.MARKER_BASENAME).exists()
    assert (
        archive / f"ambiguous_post_outcome.{MARKER_SHA256}.json"
    ).read_bytes() == MARKER_BYTES
    assert list(archive.glob("*.reconciliation.json"))


def test_cli_requires_explicit_offline_reconciliation_acknowledgement(
    tmp_path: Path,
) -> None:
    """Possessing the hash alone must not make the destructive action implicit."""

    project = installation(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "tools/reconcile_remote_write_safety_marker.py",
            "--project-root",
            str(project),
            "--expected-marker-sha256",
            MARKER_SHA256,
            "--reconciliation-reference",
            "manual review",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "--confirm-offline-reconciliation-complete" in completed.stderr
    assert (project / reconcile.MARKER_BASENAME).read_bytes() == MARKER_BYTES


def test_cli_archives_marker_and_emits_non_secret_receipt(tmp_path: Path) -> None:
    """The supported CLI emits the same receipt that it commits to the archive."""

    project = installation(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "tools/reconcile_remote_write_safety_marker.py",
            "--project-root",
            str(project),
            "--expected-marker-sha256",
            MARKER_SHA256,
            "--reconciliation-reference",
            "operator-ticket-17",
            "--confirm-offline-reconciliation-complete",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    emitted = json.loads(completed.stdout)
    receipt = project / str(emitted["receipt_path"])
    assert json.loads(receipt.read_text(encoding="utf-8")) == emitted
    assert MARKER_BYTES.decode("utf-8").strip() not in completed.stdout


def test_cli_media_mode_requires_both_operator_confirmations(
    tmp_path: Path,
) -> None:
    """The media disposition cannot be inferred from reviewed identities."""

    project, marker_bytes, receipt, fence = media_incident_installation(tmp_path)
    command = media_cli_command(project, marker_bytes, receipt, fence)
    completed = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "--confirm-no-tweet-create-attempted" in completed.stderr
    assert (project / reconcile.MEDIA_RECEIPT_BASENAME).read_bytes() == receipt.data
    assert (project / reconcile.MEDIA_FENCE_BASENAME).read_bytes() == fence.data
    assert (project / reconcile.MARKER_BASENAME).read_bytes() == marker_bytes

    completed = subprocess.run(
        [*command, "--confirm-no-tweet-create-attempted"],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "--confirm-unattached-media-abandoned" in completed.stderr
    assert (project / reconcile.MEDIA_RECEIPT_BASENAME).read_bytes() == receipt.data
    assert (project / reconcile.MEDIA_FENCE_BASENAME).read_bytes() == fence.data


def test_cli_media_mode_archives_pair_and_preserves_marker(tmp_path: Path) -> None:
    """The supported media CLI emits its durable audit without clearing safety."""

    project, marker_bytes, receipt, fence = media_incident_installation(tmp_path)
    completed = subprocess.run(
        [
            *media_cli_command(project, marker_bytes, receipt, fence),
            "--confirm-no-tweet-create-attempted",
            "--confirm-unattached-media-abandoned",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    emitted = json.loads(completed.stdout)
    audit = project / str(emitted["audit_receipt_path"])
    assert json.loads(audit.read_text(encoding="utf-8")) == emitted
    assert not (project / reconcile.MEDIA_RECEIPT_BASENAME).exists()
    assert not (project / reconcile.MEDIA_FENCE_BASENAME).exists()
    marker = project / reconcile.MARKER_BASENAME
    successor = project / reconcile.RESTART_BARRIER_BASENAME
    assert marker.read_bytes() == marker_bytes
    assert marker.stat().st_ino == successor.stat().st_ino
