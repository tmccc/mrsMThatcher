from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import exact_receipt_retirement as retirement
from transaction_mutation_authority import issue_transaction_mutation_authority


RECEIPT = b'{"lifecycle_state":"confirmed","post_id":"123"}\n'
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _authority():
    return issue_transaction_mutation_authority(
        lambda _operation: None,
        operation="exact-retirement focused test",
    )


def _retire_exact_receipt(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _authority())
    return retirement.retire_exact_receipt(*args, **kwargs)


def _prepare_exact_receipt_retirement(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _authority())
    return retirement.prepare_exact_receipt_retirement(*args, **kwargs)


def _resume_interrupted_receipt_retirement(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _authority())
    return retirement.resume_interrupted_receipt_retirement(*args, **kwargs)


def write_receipt(path: Path, data: bytes = RECEIPT) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def test_shared_retirement_boundary_latches_even_for_process_control_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SystemExit/KeyboardInterrupt cannot bypass the caller's safety latch."""

    source = tmp_path / "receipt.json"
    write_receipt(source)
    callbacks: list[str] = []
    monkeypatch.setattr(
        retirement,
        "retire_exact_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        retirement.retire_or_resume_exact_receipt(
            source,
            RECEIPT,
            mutation_authority=_authority(),
            on_retirement_uncertainty=lambda: callbacks.append("latched"),
        )

    assert callbacks == ["latched"]


def phase(path: Path) -> str:
    return retirement.inspect_exact_receipt_retirement(path, RECEIPT).phase


def run_resume_process(path: Path) -> subprocess.CompletedProcess[str]:
    script = r'''
import dataclasses
import json
import sys
from pathlib import Path
import exact_receipt_retirement as retirement
from transaction_mutation_authority import issue_transaction_mutation_authority

try:
    result = retirement.resume_interrupted_receipt_retirement(
        Path(sys.argv[1]),
        mutation_authority=issue_transaction_mutation_authority(
            lambda _operation: None,
            operation="resume subprocess test",
        ),
    )
except retirement.ExactReceiptRetirementError as exc:
    print(str(exc))
    raise SystemExit(74)
print(json.dumps(dataclasses.asdict(result), sort_keys=True))
'''
    return subprocess.run(
        [sys.executable, "-c", script, os.fspath(path)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_absence_is_unproved_unless_idempotence_is_independently_authorised(
    tmp_path: Path,
) -> None:
    source = tmp_path / "regular_post_receipt.json"
    write_receipt(source)

    first = _retire_exact_receipt(source, RECEIPT)
    assert first.completed is True
    assert first.resumed is False
    assert first.initial_phase == "fresh"
    assert first.transitions == (
        "prepare_marker_staged",
        "prepare_guard_published",
        "source_moved_to_cleanup",
        "commit_marker_staged",
        "commit_guard_published",
        "displaced_source_removed",
        "prepare_guard_moved_to_cleanup",
        "prepare_guard_removed",
        "commit_guard_moved_to_cleanup",
        "commit_guard_removed",
    )

    inspection = retirement.inspect_exact_receipt_retirement(source, RECEIPT)
    assert inspection.phase == "absent_unproven"
    assert inspection.valid is False
    assert inspection.blocking is True
    with pytest.raises(
        retirement.ExactReceiptRetirementError,
        match="absent without independent idempotence authority",
    ):
        _retire_exact_receipt(source, RECEIPT)

    authorised_inspection = retirement.inspect_exact_receipt_retirement(
        source,
        RECEIPT,
        independently_authorised_absence=True,
    )
    assert authorised_inspection.phase == "complete"
    assert authorised_inspection.valid is True
    assert authorised_inspection.blocking is False
    authorised = _retire_exact_receipt(
        source,
        RECEIPT,
        independently_authorised_absence=True,
    )
    assert authorised.completed is True
    assert authorised.initial_phase == "complete"
    assert authorised.transitions == ()


def test_prepare_publishes_guard_without_moving_source_and_is_exactly_idempotent(
    tmp_path: Path,
) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    paths = retirement._retirement_paths(source)
    source_before = source.stat()

    prepared = _prepare_exact_receipt_retirement(source, RECEIPT)

    assert prepared.phase == "prepared"
    assert prepared.valid is True
    assert prepared.blocking is True
    source_after = source.stat()
    assert source.read_bytes() == RECEIPT
    assert (source_after.st_dev, source_after.st_ino) == (
        source_before.st_dev,
        source_before.st_ino,
    )
    assert paths.guard.is_file()
    assert not paths.guard_staging.exists()
    assert not paths.commit.exists()
    assert not paths.commit_staging.exists()
    assert not paths.cleanup.exists()
    guard_before = paths.guard.stat()

    repeated = _prepare_exact_receipt_retirement(source, RECEIPT)

    assert repeated.phase == "prepared"
    guard_after = paths.guard.stat()
    assert (guard_after.st_dev, guard_after.st_ino) == (
        guard_before.st_dev,
        guard_before.st_ino,
    )
    assert source.stat().st_ino == source_before.st_ino


@pytest.mark.parametrize(
    ("fault_function", "expected_interrupted_phase"),
    [
        ("stage", "prepare_marker_staged"),
        ("publish", "prepared"),
    ],
)
def test_prepare_reenters_after_literal_hard_exit_with_the_same_exact_source(
    tmp_path: Path,
    fault_function: str,
    expected_interrupted_phase: str,
) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    source_inode = source.stat().st_ino
    script = r'''
import os
import sys
from pathlib import Path
import exact_receipt_retirement as retirement
from transaction_mutation_authority import issue_transaction_mutation_authority

source = Path(sys.argv[1])
fault = sys.argv[2]
original_stage = retirement._stage_new
original_publish = retirement._publish_staged

def stage(directory_fd, name, data):
    result = original_stage(directory_fd, name, data)
    if fault == "stage":
        os._exit(73)
    return result

def publish(directory_fd, **kwargs):
    original_publish(directory_fd, **kwargs)
    if fault == "publish":
        os._exit(73)

retirement._stage_new = stage
retirement._publish_staged = publish
retirement.prepare_exact_receipt_retirement(
    source,
    source.read_bytes(),
    mutation_authority=issue_transaction_mutation_authority(
        lambda _operation: None,
        operation="prepare subprocess test",
    ),
)
raise SystemExit(0)
'''
    crashed = subprocess.run(
        [sys.executable, "-c", script, os.fspath(source), fault_function],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert crashed.returncode == 73, crashed.stderr
    assert phase(source) == expected_interrupted_phase
    assert source.read_bytes() == RECEIPT
    assert source.stat().st_ino == source_inode

    prepared = _prepare_exact_receipt_retirement(source, RECEIPT)

    assert prepared.phase == "prepared"
    assert source.read_bytes() == RECEIPT
    assert source.stat().st_ino == source_inode


@pytest.mark.parametrize("replacement", [RECEIPT, b'{"replacement":true}\n'])
def test_prepare_rejects_source_replacement_after_guard_publication(
    tmp_path: Path,
    replacement: bytes,
) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    _prepare_exact_receipt_retirement(source, RECEIPT)
    original_inode = source.stat().st_ino
    replacement_path = tmp_path / "replacement.tmp"
    write_receipt(replacement_path, replacement)
    os.replace(replacement_path, source)
    replacement_inode = source.stat().st_ino
    assert replacement_inode != original_inode

    with pytest.raises(
        retirement.ExactReceiptRetirementError,
        match="bytes or identity changed",
    ):
        _prepare_exact_receipt_retirement(source, RECEIPT)

    assert source.read_bytes() == replacement
    assert source.stat().st_ino == replacement_inode
    assert retirement._retirement_paths(source).guard.exists()


def test_marker_only_resume_completes_after_public_prepare(tmp_path: Path) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    prepared = _prepare_exact_receipt_retirement(source, RECEIPT)
    assert prepared.phase == "prepared"

    resumed = run_resume_process(source)

    assert resumed.returncode == 0, resumed.stderr
    result = json.loads(resumed.stdout)
    assert result["initial_phase"] == "prepared"
    assert result["resumed"] is True
    assert result["completed"] is True
    assert result["expected_sha256"] == hashlib.sha256(RECEIPT).hexdigest()
    assert not any(
        os.path.lexists(path)
        for path in retirement.retirement_barrier_paths(source)
    )


def test_prepare_refuses_every_later_retirement_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    _prepare_exact_receipt_retirement(source, RECEIPT)
    paths = retirement._retirement_paths(source)
    original_stage = retirement._stage_new

    def stop_before_commit_stage(directory_fd: int, name: str, data: bytes):
        if name == paths.commit_staging.name:
            raise OSError("stop in source-moved phase")
        return original_stage(directory_fd, name, data)

    monkeypatch.setattr(retirement, "_stage_new", stop_before_commit_stage)
    with pytest.raises(OSError, match="source-moved"):
        _resume_interrupted_receipt_retirement(source)
    assert phase(source) == "source_moved"

    with pytest.raises(
        retirement.ExactReceiptRetirementError,
        match="refuses retirement phase 'source_moved'",
    ):
        _prepare_exact_receipt_retirement(source, RECEIPT)


HARD_EXIT_PHASES = [
    ("prepare_marker_staged", "prepare_marker_staged", True),
    ("prepare_guard_published", "prepared", True),
    ("source_moved_to_cleanup", "source_moved", True),
    ("commit_marker_staged", "commit_marker_staged", True),
    ("commit_guard_published", "source_committed", True),
    ("displaced_source_removed", "guards_committed", True),
    ("prepare_guard_moved_to_cleanup", "prepare_guard_moved", True),
    ("prepare_guard_removed", "commit_guard_only", True),
    ("commit_guard_moved_to_cleanup", "commit_guard_moved", True),
    ("commit_guard_removed", "absent_unproven", False),
]


@pytest.mark.parametrize(
    ("fault_transition", "expected_phase", "resume_is_proved"),
    HARD_EXIT_PHASES,
)
def test_literal_hard_exit_at_every_phase_is_safe_for_fresh_process_resume(
    tmp_path: Path,
    fault_transition: str,
    expected_phase: str,
    resume_is_proved: bool,
) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    script = r'''
import json
import os
import sys
from pathlib import Path
import exact_receipt_retirement as retirement

source = Path(sys.argv[1])
fault = sys.argv[2]
expected = source.read_bytes()
paths = retirement._retirement_paths(source)
original_stage = retirement._stage_new
original_publish = retirement._publish_staged
original_move = retirement._move_exact_to_cleanup
original_unlink = retirement._unlink_exact_cleanup

def stop_if(name):
    if name == fault:
        os._exit(73)

def stage(directory_fd, name, data):
    result = original_stage(directory_fd, name, data)
    transition = (
        "prepare_marker_staged"
        if name == paths.guard_staging.name
        else "commit_marker_staged"
    )
    stop_if(transition)
    return result

def publish(directory_fd, **kwargs):
    original_publish(directory_fd, **kwargs)
    transition = (
        "prepare_guard_published"
        if kwargs["final_name"] == paths.guard.name
        else "commit_guard_published"
    )
    stop_if(transition)

def move(directory_fd, **kwargs):
    original_move(directory_fd, **kwargs)
    name = kwargs["source_name"]
    transition = (
        "source_moved_to_cleanup" if name == source.name else
        "prepare_guard_moved_to_cleanup" if name == paths.guard.name else
        "commit_guard_moved_to_cleanup"
    )
    stop_if(transition)

def unlink(directory_fd, **kwargs):
    marker_or_receipt = kwargs["expected_entry"].data
    original_unlink(directory_fd, **kwargs)
    if marker_or_receipt == expected:
        transition = "displaced_source_removed"
    else:
        marker = json.loads(marker_or_receipt)
        transition = (
            "prepare_guard_removed"
            if marker["phase"] == "prepared"
            else "commit_guard_removed"
        )
    stop_if(transition)

retirement._stage_new = stage
retirement._publish_staged = publish
retirement._move_exact_to_cleanup = move
retirement._unlink_exact_cleanup = unlink
from transaction_mutation_authority import issue_transaction_mutation_authority
retirement.retire_exact_receipt(
    source,
    expected,
    mutation_authority=issue_transaction_mutation_authority(
        lambda _operation: None,
        operation="retire subprocess test",
    ),
)
raise SystemExit(0)
'''
    crashed = subprocess.run(
        [sys.executable, "-c", script, os.fspath(source), fault_transition],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert crashed.returncode == 73, crashed.stderr
    assert phase(source) == expected_phase
    assert retirement.retirement_auxiliary_barrier_exists(source) is resume_is_proved

    resumed = run_resume_process(source)
    if not resume_is_proved:
        assert resumed.returncode == 74
        assert "no marker-proved interrupted" in resumed.stdout
        explicitly_authorised = _retire_exact_receipt(
            source,
            RECEIPT,
            independently_authorised_absence=True,
        )
        assert explicitly_authorised.initial_phase == "complete"
        return

    assert resumed.returncode == 0, resumed.stderr
    result = json.loads(resumed.stdout)
    assert result["completed"] is True
    assert result["resumed"] is True
    assert result["initial_phase"] == expected_phase
    assert result["expected_sha256"] == hashlib.sha256(RECEIPT).hexdigest()
    assert not any(
        os.path.lexists(path)
        for path in retirement.retirement_barrier_paths(source)
    )


@pytest.mark.parametrize("initial", ["absent", "fresh", "cleanup_without_marker"])
def test_fresh_process_resume_rejects_namespaces_without_auxiliary_proof(
    tmp_path: Path,
    initial: str,
) -> None:
    source = tmp_path / "receipt.json"
    if initial != "absent":
        write_receipt(source)
    if initial == "cleanup_without_marker":
        paths = retirement._retirement_paths(source)
        source.rename(paths.cleanup)

    completed = run_resume_process(source)

    assert completed.returncode == 74
    if initial in {"absent", "fresh"}:
        assert "no marker-proved interrupted" in completed.stdout
    else:
        assert "retirement marker" in completed.stdout
    if initial == "fresh":
        assert source.read_bytes() == RECEIPT
    if initial == "cleanup_without_marker":
        assert retirement._retirement_paths(source).cleanup.read_bytes() == RECEIPT


@pytest.mark.parametrize("entry_kind", ["guard_staging", "guard"])
def test_torn_prepare_marker_entries_are_inventoried_and_fail_closed(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    paths = retirement._retirement_paths(source)
    torn = getattr(paths, entry_kind)
    torn.write_bytes(b'{"document_kind":')
    torn.chmod(0o600)

    assert retirement.retirement_auxiliary_barrier_exists(source) is True
    inspection = retirement.inspect_exact_receipt_retirement(source, RECEIPT)
    assert inspection.phase == "invalid"
    assert inspection.valid is False
    assert inspection.blocking is True
    with pytest.raises(retirement.ExactReceiptRetirementError, match="invalid .*marker JSON"):
        _resume_interrupted_receipt_retirement(source)

    assert source.read_bytes() == RECEIPT
    assert torn.read_bytes() == b'{"document_kind":'


def test_torn_commit_staging_after_source_move_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    paths = retirement._retirement_paths(source)
    original_stage = retirement._stage_new

    def stop_before_commit_stage(directory_fd: int, name: str, data: bytes):
        if name == paths.commit_staging.name:
            raise OSError("stop before commit stage")
        return original_stage(directory_fd, name, data)

    monkeypatch.setattr(retirement, "_stage_new", stop_before_commit_stage)
    with pytest.raises(OSError, match="stop before commit stage"):
        _retire_exact_receipt(source, RECEIPT)
    paths.commit_staging.write_bytes(b"{")
    paths.commit_staging.chmod(0o600)

    assert phase(source) == "invalid"
    with pytest.raises(retirement.ExactReceiptRetirementError, match="invalid .*marker JSON"):
        _resume_interrupted_receipt_retirement(source)
    assert paths.guard.exists()
    assert paths.cleanup.read_bytes() == RECEIPT
    assert paths.commit_staging.read_bytes() == b"{"


@pytest.mark.parametrize("replacement", [RECEIPT, b'{"post_id":"changed"}\n'])
def test_replaced_source_after_prepare_publication_is_never_retired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: bytes,
) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    paths = retirement._retirement_paths(source)
    original_publish = retirement._publish_staged

    def stop_after_prepare(directory_fd: int, **kwargs: object) -> None:
        original_publish(directory_fd, **kwargs)
        if kwargs["final_name"] == paths.guard.name:
            raise OSError("stop after prepare publication")

    monkeypatch.setattr(retirement, "_publish_staged", stop_after_prepare)
    with pytest.raises(OSError, match="stop after prepare publication"):
        _retire_exact_receipt(source, RECEIPT)
    original_inode = source.stat().st_ino
    replacement_path = tmp_path / "replacement.tmp"
    write_receipt(replacement_path, replacement)
    os.replace(replacement_path, source)
    replacement_inode = source.stat().st_ino
    assert replacement_inode != original_inode

    completed = run_resume_process(source)

    assert completed.returncode == 74
    assert "bytes or identity changed" in completed.stdout
    assert source.read_bytes() == replacement
    assert source.stat().st_ino == replacement_inode
    assert paths.guard.exists()
    assert not paths.cleanup.exists()


def test_staging_replacement_at_atomic_publication_boundary_is_restored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    paths = retirement._retirement_paths(source)
    original_rename = retirement._rename_noreplace
    replacement = b'{"torn":"replacement"}\n'
    injected = False

    def replace_then_rename(
        directory_fd: int,
        source_name: str,
        destination_name: str,
    ) -> None:
        nonlocal injected
        if source_name == paths.guard_staging.name and not injected:
            injected = True
            temporary = tmp_path / "replacement-marker.tmp"
            temporary.write_bytes(replacement)
            temporary.chmod(0o600)
            os.replace(temporary, paths.guard_staging)
        original_rename(directory_fd, source_name, destination_name)

    monkeypatch.setattr(retirement, "_rename_noreplace", replace_then_rename)
    with pytest.raises(
        retirement.ExactReceiptRetirementError,
        match="staging marker changed during publication",
    ):
        _retire_exact_receipt(source, RECEIPT)

    assert injected is True
    assert source.read_bytes() == RECEIPT
    assert paths.guard_staging.read_bytes() == replacement
    assert not paths.guard.exists()
    assert retirement.retirement_auxiliary_barrier_exists(source) is True


def test_existing_final_marker_is_not_replaced_by_staged_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    paths = retirement._retirement_paths(source)
    original_publish = retirement._publish_staged

    def collide_before_publish(directory_fd: int, **kwargs: object) -> None:
        if kwargs["final_name"] == paths.guard.name:
            paths.guard.write_bytes(b'{"foreign":"marker"}\n')
            paths.guard.chmod(0o600)
        original_publish(directory_fd, **kwargs)

    monkeypatch.setattr(retirement, "_publish_staged", collide_before_publish)
    with pytest.raises(OSError):
        _retire_exact_receipt(source, RECEIPT)

    assert source.read_bytes() == RECEIPT
    assert paths.guard.read_bytes() == b'{"foreign":"marker"}\n'
    assert paths.guard_staging.exists()


def test_wrong_expected_bytes_do_not_create_or_remove_any_entry(tmp_path: Path) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    before = source.stat()

    inspection = retirement.inspect_exact_receipt_retirement(source, b"different\n")
    assert inspection.valid is False
    assert inspection.blocking is True
    with pytest.raises(retirement.ExactReceiptRetirementError):
        _retire_exact_receipt(source, b"different\n")

    after = source.stat()
    assert source.read_bytes() == RECEIPT
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert not any(
        os.path.lexists(path)
        for path in retirement.retirement_auxiliary_paths(source)
    )


def test_cleanup_change_visible_before_final_validation_is_preserved_and_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    paths = retirement._retirement_paths(source)
    original_publish = retirement._publish_staged

    def stop_after_commit(directory_fd: int, **kwargs: object) -> None:
        original_publish(directory_fd, **kwargs)
        if kwargs["final_name"] == paths.commit.name:
            raise OSError("stop after commit")

    monkeypatch.setattr(retirement, "_publish_staged", stop_after_commit)
    with pytest.raises(OSError, match="stop after commit"):
        _retire_exact_receipt(source, RECEIPT)
    replacement = tmp_path / "foreign-cleanup"
    replacement_bytes = b"changed cleanup entry\n"
    write_receipt(replacement, replacement_bytes)
    replacement_inode = replacement.stat().st_ino
    os.replace(replacement, paths.cleanup)

    with pytest.raises(retirement.ExactReceiptRetirementError):
        _resume_interrupted_receipt_retirement(source)

    assert paths.cleanup.read_bytes() == replacement_bytes
    assert paths.cleanup.stat().st_ino == replacement_inode
    assert paths.guard.exists()
    assert paths.commit.exists()
    assert retirement.retirement_auxiliary_barrier_exists(source) is True


def test_final_cleanup_unlink_failure_before_effect_preserves_blocking_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "receipt.json"
    write_receipt(source)
    paths = retirement._retirement_paths(source)
    original_move = retirement._move_exact_to_cleanup

    def stop_after_final_move(directory_fd: int, **kwargs: object) -> None:
        original_move(directory_fd, **kwargs)
        if kwargs["source_name"] == paths.commit.name:
            raise OSError("stop after final cleanup move")

    monkeypatch.setattr(retirement, "_move_exact_to_cleanup", stop_after_final_move)
    with pytest.raises(OSError, match="stop after final cleanup move"):
        _retire_exact_receipt(source, RECEIPT)

    monkeypatch.setattr(retirement, "_move_exact_to_cleanup", original_move)
    assert phase(source) == "commit_guard_moved"
    before = paths.cleanup.stat()
    cleanup_bytes = paths.cleanup.read_bytes()
    original_unlink = retirement.os.unlink

    def fail_final_cleanup_unlink(
        path: str | bytes,
        *args: object,
        **kwargs: object,
    ) -> None:
        if os.fspath(path) == paths.cleanup.name:
            raise OSError("synthetic final cleanup unlink failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(retirement.os, "unlink", fail_final_cleanup_unlink)
    with pytest.raises(OSError, match="synthetic final cleanup unlink failure"):
        _resume_interrupted_receipt_retirement(source)

    after = paths.cleanup.stat()
    assert paths.cleanup.read_bytes() == cleanup_bytes
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert phase(source) == "commit_guard_moved"
    assert retirement.retirement_auxiliary_barrier_exists(source) is True


def test_symlink_source_and_auxiliary_entries_fail_closed(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    write_receipt(real)
    source = tmp_path / "receipt.json"
    source.symlink_to(real)

    with pytest.raises(retirement.ExactReceiptRetirementError, match="unsafe"):
        retirement.inspect_exact_receipt_retirement(source, RECEIPT)

    source.unlink()
    write_receipt(source)
    paths = retirement._retirement_paths(source)
    paths.guard_staging.symlink_to(real)
    with pytest.raises(retirement.ExactReceiptRetirementError, match="unsafe"):
        _retire_exact_receipt(source, RECEIPT)
    assert source.read_bytes() == RECEIPT


def test_symlink_parent_component_fails_closed(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    source = linked_parent / "receipt.json"
    write_receipt(real_parent / "receipt.json")

    with pytest.raises(
        retirement.ExactReceiptRetirementError,
        match="parent is unavailable or unsafe",
    ):
        _retire_exact_receipt(source, RECEIPT)

    assert (real_parent / "receipt.json").read_bytes() == RECEIPT


def test_canonical_receipt_bytes_match_durable_writer_format() -> None:
    assert retirement.canonical_receipt_json_bytes({"z": 2, "a": 1}) == (
        b'{\n  "a": 1,\n  "z": 2\n}\n'
    )


def test_auxiliary_paths_include_fixed_staging_inventory(tmp_path: Path) -> None:
    source = tmp_path / "confirmed_reply_receipt.json"
    assert retirement.retirement_auxiliary_paths(source) == (
        tmp_path / ".confirmed_reply_receipt.json.retirement.guard.json",
        tmp_path / ".confirmed_reply_receipt.json.retirement.commit.json",
        tmp_path / ".confirmed_reply_receipt.json.retirement.cleanup",
        tmp_path / ".confirmed_reply_receipt.json.retirement.guard.json.staging",
        tmp_path / ".confirmed_reply_receipt.json.retirement.commit.json.staging",
    )


@pytest.mark.parametrize("staging_index", [3, 4])
def test_auxiliary_barrier_detection_includes_broken_staging_symlinks(
    tmp_path: Path,
    staging_index: int,
) -> None:
    source = tmp_path / "receipt.json"
    staging = retirement.retirement_auxiliary_paths(source)[staging_index]
    staging.symlink_to(tmp_path / "missing")
    assert retirement.retirement_auxiliary_barrier_exists(source) is True
