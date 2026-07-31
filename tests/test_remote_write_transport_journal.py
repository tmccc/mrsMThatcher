from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import remote_write_transport_journal as journal


def _write_receipt(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _transaction(tmp_path: Path) -> tuple[Path, dict[str, object], dict[str, object]]:
    receipt_path = tmp_path / "regular_post_receipt.json"
    receipt = {
        "schema_version": 4,
        "lifecycle_state": "attempting",
        "attempt_epoch": 1_800_000_000,
        "lane": "quote_image",
    }
    payload = {"text": "A reviewed quotation", "media": {"media_ids": ["42"]}}
    _write_receipt(receipt_path, receipt)
    return receipt_path, receipt, payload


def test_journal_survives_source_receipt_deletion_and_blocks_restart(
    tmp_path: Path,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    authority = journal.begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    fence_path = journal.fence_path_for_journal(journal_path)
    armed = journal.arm_transport_transaction(journal_path, authority)

    receipt_path.unlink()
    directory = os.open(tmp_path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

    journal.consume_transport_authority(
        journal_path,
        armed,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    assert journal.transport_journal_is_blocking(journal_path) is True
    snapshot = journal.inspect_transport_journal(journal_path)
    assert snapshot is not None
    assert snapshot.document["lifecycle_state"] == "attempting"
    assert fence_path.exists()


def test_authority_is_payload_bound_and_one_shot(tmp_path: Path) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    prepared = journal.begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = journal.arm_transport_transaction(journal_path, prepared)

    with pytest.raises(journal.TransportJournalError, match="does not bind"):
        journal.consume_transport_authority(
            journal_path,
            armed,
            method="POST",
            request_path="/2/tweets",
            payload={"text": "different"},
        )

    journal.consume_transport_authority(
        journal_path,
        armed,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    with pytest.raises(journal.TransportJournalError, match="already consumed"):
        journal.consume_transport_authority(
            journal_path,
            armed,
            method="POST",
            request_path="/2/tweets",
            payload=payload,
        )


def test_confirm_and_retire_keep_a_receipt_barrier(tmp_path: Path) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    prepared = journal.begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = journal.arm_transport_transaction(journal_path, prepared)
    journal.consume_transport_authority(
        journal_path,
        armed,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    confirmed = journal.confirm_transport_transaction(
        journal_path,
        armed,
        post_id="950001",
    )
    assert confirmed.document["lifecycle_state"] == "confirmed"

    confirmed_receipt = {
        **receipt,
        "lifecycle_state": "confirmed",
        "post_id": "950001",
    }
    _write_receipt(receipt_path, confirmed_receipt)
    journal.retire_confirmed_transport_transaction(
        receipt_path=receipt_path,
        expected_confirmed_receipt=confirmed_receipt,
        lane="quote_image",
        post_id="950001",
    )
    assert not journal_path.exists()
    assert not journal.fence_path_for_journal(journal_path).exists()
    assert receipt_path.exists()
    assert not any(
        item.name.startswith(journal.JOURNAL_RETIREMENT_PREFIX)
        for item in tmp_path.iterdir()
    )


def test_invalid_or_torn_journal_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / journal.JOURNAL_BASENAME
    path.write_text("{not-json", encoding="utf-8")
    assert journal.transport_journal_is_blocking(path) is True
    path.unlink()
    (tmp_path / f"{journal.JOURNAL_STAGING_PREFIX}deadbeef").write_text(
        "partial",
        encoding="utf-8",
    )
    assert journal.transport_journal_is_blocking(path) is True


def test_changed_source_receipt_cannot_publish_authority(tmp_path: Path) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    changed = {**receipt, "attempt_epoch": 1_800_000_001}
    _write_receipt(receipt_path, changed)
    with pytest.raises(journal.TransportJournalError, match="does not match"):
        journal.begin_transport_transaction(
            receipt_path=receipt_path,
            expected_receipt=receipt,
            lane="quote_image",
            payload=payload,
        )


def test_journal_disappearance_or_symlink_replacement_never_authorises_transport(
    tmp_path: Path,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    prepared = journal.begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = journal.arm_transport_transaction(journal_path, prepared)
    original = journal_path.read_bytes()

    journal_path.unlink()
    with pytest.raises(journal.TransportJournalError, match="disappeared"):
        journal.consume_transport_authority(
            journal_path,
            armed,
            method="POST",
            request_path="/2/tweets",
            payload=payload,
        )

    target = tmp_path / "unrelated.json"
    target.write_bytes(original)
    journal_path.symlink_to(target.name)
    assert journal.transport_journal_is_blocking(journal_path) is True
    with pytest.raises(journal.TransportJournalError, match="unsafe metadata"):
        journal.consume_transport_authority(
            journal_path,
            armed,
            method="POST",
            request_path="/2/tweets",
            payload=payload,
        )


def test_source_receipt_replacement_after_successor_publication_keeps_barrier(
    tmp_path: Path,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    prepared = journal.begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = journal.arm_transport_transaction(journal_path, prepared)
    journal.consume_transport_authority(
        journal_path,
        armed,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    journal.confirm_transport_transaction(journal_path, armed, post_id="950001")
    replacement = {
        **receipt,
        "attempt_epoch": 1_800_000_001,
        "lifecycle_state": "confirmed",
        "post_id": "950001",
    }
    _write_receipt(receipt_path, replacement)
    assert journal.transport_journal_is_blocking(journal_path) is True
    with pytest.raises(journal.TransportJournalError, match="does not match"):
        journal.retire_confirmed_transport_transaction(
            receipt_path=receipt_path,
            expected_confirmed_receipt={
                **receipt,
                "lifecycle_state": "confirmed",
                "post_id": "950001",
            },
            lane="quote_image",
            post_id="950001",
        )


@pytest.mark.parametrize("fail_at", (1, 2, 3, 4))
def test_retirement_failure_leaves_a_restart_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_at: int,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    prepared = journal.begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = journal.arm_transport_transaction(journal_path, prepared)
    journal.consume_transport_authority(
        journal_path,
        armed,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    journal.confirm_transport_transaction(journal_path, armed, post_id="950001")
    confirmed_receipt = {
        **receipt,
        "lifecycle_state": "confirmed",
        "post_id": "950001",
    }
    _write_receipt(receipt_path, confirmed_receipt)

    real_fsync = journal.os.fsync
    calls = 0

    def fail_after_guard(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError("synthetic directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(journal.os, "fsync", fail_after_guard)
    with pytest.raises(OSError, match="synthetic"):
        journal.retire_confirmed_transport_transaction(
            receipt_path=receipt_path,
            expected_confirmed_receipt=confirmed_receipt,
            lane="quote_image",
            post_id="950001",
        )
    assert (
        journal.transport_journal_is_blocking(journal_path)
        or receipt_path.exists()
    )


def test_byte_identical_journal_replacement_is_not_the_authorised_generation(
    tmp_path: Path,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    prepared = journal.begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = journal.arm_transport_transaction(journal_path, prepared)
    original = journal_path.read_bytes()
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(original)
    replacement.chmod(journal.JOURNAL_MODE)
    with replacement.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(replacement, journal_path)
    directory = os.open(tmp_path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

    with pytest.raises(journal.TransportJournalError, match="does not bind"):
        journal.consume_transport_authority(
            journal_path,
            armed,
            method="POST",
            request_path="/2/tweets",
            payload=payload,
        )
    assert journal.transport_journal_is_blocking(journal_path) is True


def test_immutable_fence_keeps_restart_barrier_when_journal_is_deleted(
    tmp_path: Path,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    prepared = journal.begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    journal.arm_transport_transaction(journal_path, prepared)
    fence_path = journal.fence_path_for_journal(journal_path)

    receipt_path.unlink()
    journal_path.unlink()
    directory = os.open(tmp_path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

    assert fence_path.exists()
    assert journal.transport_journal_is_blocking(journal_path) is True


def test_canonical_receipt_anchor_rejects_another_path(tmp_path: Path) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    prepared = journal.begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = journal.arm_transport_transaction(journal_path, prepared)

    with pytest.raises(journal.TransportJournalError, match="canonical"):
        journal.consume_transport_authority(
            journal_path,
            armed,
            method="POST",
            request_path="/2/tweets",
            payload=payload,
            expected_receipt_path=tmp_path / "meme_post_receipt.json",
        )


@pytest.mark.parametrize("transition", ("arm", "confirm"))
@pytest.mark.parametrize("fail_at", (1, 2, 3, 4))
def test_lifecycle_transition_fsync_failure_always_leaves_a_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
    fail_at: int,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    prepared = journal.begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    authority = prepared
    if transition == "confirm":
        authority = journal.arm_transport_transaction(journal_path, prepared)
        journal.consume_transport_authority(
            journal_path,
            authority,
            method="POST",
            request_path="/2/tweets",
            payload=payload,
        )

    real_fsync = journal.os.fsync
    calls = 0

    def fail_one_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError("synthetic lifecycle fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(journal.os, "fsync", fail_one_fsync)
    with pytest.raises((OSError, journal.TransportJournalError)):
        if transition == "arm":
            journal.arm_transport_transaction(journal_path, authority)
        else:
            journal.confirm_transport_transaction(
                journal_path,
                authority,
                post_id="950001",
            )
    assert journal.transport_journal_is_blocking(journal_path) is True
