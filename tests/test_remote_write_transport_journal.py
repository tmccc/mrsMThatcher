from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import remote_write_transport_journal as journal
from transaction_mutation_authority import issue_transaction_mutation_authority


def _mutation_authority():
    return issue_transaction_mutation_authority(
        lambda _operation: None,
        operation="transport journal focused test",
    )


def _arm_transport_transaction(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return journal.arm_transport_transaction(*args, **kwargs)


def _retire_confirmed_transport_transaction(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return journal.retire_confirmed_transport_transaction(*args, **kwargs)


def _abort_untransmitted_transport_transaction(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return journal.abort_untransmitted_transport_transaction(*args, **kwargs)


def _source_validator(
    lane: str,
    receipt: dict[str, object],
    payload: dict[str, object],
) -> bool:
    return (
        lane == "quote_image"
        and receipt.get("lane") == lane
        and receipt.get("lifecycle_state") == "attempting"
        and bool(payload.get("text") or payload.get("media"))
    )


def _begin_transport_transaction(**kwargs: object) -> journal.TransportAuthority:
    """Call the real API with an explicit test-only lane policy."""

    if kwargs.get("source_binding") is None:
        kwargs.setdefault("source_validator_id", "tests.quote-image-source.v1")
        kwargs.setdefault("source_validator", _source_validator)
    return journal.begin_transport_transaction(**kwargs)


def _confirm_transport_transaction(
    path: Path,
    authority: journal.TransportAuthority,
    *,
    post_id: str,
    confirmation_epoch: int = 1_800_000_010,
) -> journal.JournalSnapshot:
    """Call the real confirmation API with an explicit durable epoch."""

    return journal.confirm_transport_transaction(
        path,
        authority,
        mutation_authority=_mutation_authority(),
        post_id=post_id,
        confirmation_epoch=confirmation_epoch,
    )


@pytest.fixture(autouse=True)
def _reset_process_authorities() -> None:
    journal.reset_consumed_authorities_for_tests()


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


def _durable_write_bytes(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        mode,
    )
    try:
        view = memoryview(data)
        written = 0
        while written < len(view):
            written += os.write(descriptor, view[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    path.chmod(mode)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _mutate_and_restore_same_inode(path: Path) -> tuple[os.stat_result, os.stat_result]:
    original = path.read_bytes()
    mutated = bytes((original[0] ^ 1,)) + original[1:]
    before = path.stat()
    _durable_write_bytes(path, mutated, mode=before.st_mode & 0o777)
    _durable_write_bytes(path, original, mode=before.st_mode & 0o777)
    after = path.stat()
    assert after.st_ino == before.st_ino
    assert after.st_ctime_ns != before.st_ctime_ns
    assert path.read_bytes() == original
    return before, after


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
    authority = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    fence_path = journal.fence_path_for_journal(journal_path)
    armed = _arm_transport_transaction(journal_path, authority)

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
    prepared = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = _arm_transport_transaction(journal_path, prepared)

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
    prepared = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = _arm_transport_transaction(journal_path, prepared)
    journal.consume_transport_authority(
        journal_path,
        armed,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    confirmed = _confirm_transport_transaction(
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
    _retire_confirmed_transport_transaction(
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


@pytest.mark.parametrize("schema_version", (2.0, True))
def test_transport_journal_requires_integer_schema_version(
    tmp_path: Path,
    schema_version: object,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    authority = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = Path(authority.journal_path)
    document = json.loads(journal_path.read_bytes())
    document["schema_version"] = schema_version
    _durable_write_bytes(
        journal_path,
        journal.canonical_json_bytes(document),
        mode=journal.JOURNAL_MODE,
    )

    with pytest.raises(journal.TransportJournalError, match="semantics are invalid"):
        journal.inspect_transport_journal(journal_path)
    assert journal.transport_journal_is_blocking(journal_path) is True


def test_confirmed_transport_journal_requires_string_remote_post_id(
    tmp_path: Path,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    prepared = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = _arm_transport_transaction(journal_path, prepared)
    journal.consume_transport_authority(
        journal_path,
        armed,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    _confirm_transport_transaction(journal_path, armed, post_id="123")
    document = json.loads(journal_path.read_bytes())
    document["remote_post_id"] = 123
    _durable_write_bytes(
        journal_path,
        journal.canonical_json_bytes(document),
        mode=journal.JOURNAL_MODE,
    )

    with pytest.raises(
        journal.TransportJournalError,
        match="no valid post ID",
    ):
        journal.inspect_transport_journal(journal_path)
    assert journal.transport_journal_is_blocking(journal_path) is True


def test_changed_source_receipt_cannot_publish_authority(tmp_path: Path) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    changed = {**receipt, "attempt_epoch": 1_800_000_001}
    _write_receipt(receipt_path, changed)
    with pytest.raises(journal.TransportJournalError, match="does not match"):
        _begin_transport_transaction(
            receipt_path=receipt_path,
            expected_receipt=receipt,
            lane="quote_image",
            payload=payload,
        )


def test_source_binding_rejects_same_inode_content_change_and_restore(
    tmp_path: Path,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    binding = journal.bind_transport_source(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
        validator_id="tests.quote-image-source.v1",
        validator=_source_validator,
    )
    before, after = _mutate_and_restore_same_inode(receipt_path)
    assert binding.receipt_ctime_ns == before.st_ctime_ns
    assert binding.receipt_ctime_ns != after.st_ctime_ns

    with pytest.raises(journal.TransportJournalError, match="changed after semantic"):
        _begin_transport_transaction(
            receipt_path=receipt_path,
            source_binding=binding,
        )


def test_exact_source_receipt_generation_replacement_succeeds(
    tmp_path: Path,
) -> None:
    receipt_path, receipt, _payload = _transaction(tmp_path)
    original = receipt_path.read_bytes()
    _durable_write_bytes(receipt_path, original, mode=journal.JOURNAL_MODE)
    expected = receipt_path.stat()
    replacement = journal.canonical_json_bytes(
        {**receipt, "attempt_epoch": 1_800_000_001}
    )

    journal.replace_exact_source_receipt_generation(
        receipt_path,
        expected_bytes=original,
        replacement_bytes=replacement,
        expected_device=expected.st_dev,
        expected_inode=expected.st_ino,
        expected_ctime_ns=expected.st_ctime_ns,
        mutation_authority=_mutation_authority(),
    )

    assert receipt_path.read_bytes() == replacement
    assert receipt_path.stat().st_ino != expected.st_ino
    assert not any(
        item.name.startswith(journal.JOURNAL_STAGING_PREFIX)
        for item in tmp_path.iterdir()
    )


def test_exact_source_receipt_generation_replacement_rejects_stale_ctime(
    tmp_path: Path,
) -> None:
    receipt_path, receipt, _payload = _transaction(tmp_path)
    original = receipt_path.read_bytes()
    _durable_write_bytes(receipt_path, original, mode=journal.JOURNAL_MODE)
    expected, changed = _mutate_and_restore_same_inode(receipt_path)
    assert changed.st_ctime_ns != expected.st_ctime_ns

    with pytest.raises(journal.BoundSourceReceiptTransitionError, match="did not complete"):
        journal.replace_exact_source_receipt_generation(
            receipt_path,
            expected_bytes=original,
            replacement_bytes=journal.canonical_json_bytes(
                {**receipt, "attempt_epoch": 1_800_000_001}
            ),
            expected_device=expected.st_dev,
            expected_inode=expected.st_ino,
            expected_ctime_ns=expected.st_ctime_ns,
            mutation_authority=_mutation_authority(),
        )

    assert receipt_path.read_bytes() == original
    assert any(
        item.name.startswith(journal.JOURNAL_STAGING_PREFIX)
        for item in tmp_path.iterdir()
    )
    assert journal.transport_journal_is_blocking(
        journal.journal_path_for_receipt(receipt_path)
    ) is True


def test_exact_source_document_replacement_rejects_same_bytes_new_inode_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path, receipt, _payload = _transaction(tmp_path)
    original = receipt_path.read_bytes()
    _durable_write_bytes(receipt_path, original, mode=journal.JOURNAL_MODE)
    original_inode = receipt_path.stat().st_ino
    replacement = journal.canonical_json_bytes(
        {**receipt, "attempt_epoch": 1_800_000_001}
    )
    real_replace_exact = journal._replace_exact

    def replace_after_peer_aba(path: Path, **kwargs: object) -> None:
        peer = tmp_path / "peer-receipt"
        _durable_write_bytes(peer, original, mode=journal.JOURNAL_MODE)
        os.replace(peer, path)
        real_replace_exact(path, **kwargs)

    monkeypatch.setattr(journal, "_replace_exact", replace_after_peer_aba)

    with pytest.raises(
        journal.BoundSourceReceiptTransitionError,
        match="did not complete",
    ):
        journal.replace_exact_source_receipt_document(
            receipt_path,
            expected_bytes=original,
            replacement_bytes=replacement,
            mutation_authority=_mutation_authority(),
        )

    assert receipt_path.read_bytes() == original
    assert receipt_path.stat().st_ino != original_inode
    assert any(
        item.name.startswith(journal.JOURNAL_STAGING_PREFIX)
        for item in tmp_path.iterdir()
    )


def test_exact_source_document_publication_never_overwrites_existing_entry(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "source-receipt.json"
    existing = b"peer-owned\n"
    _durable_write_bytes(receipt_path, existing, mode=journal.JOURNAL_MODE)

    with pytest.raises(FileExistsError):
        journal.publish_exact_source_receipt_document(
            receipt_path,
            receipt_bytes=journal.canonical_json_bytes({"state": "sending"}),
            mutation_authority=_mutation_authority(),
        )

    assert receipt_path.read_bytes() == existing


def test_journal_disappearance_or_symlink_replacement_never_authorises_transport(
    tmp_path: Path,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    prepared = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = _arm_transport_transaction(journal_path, prepared)
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
    prepared = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = _arm_transport_transaction(journal_path, prepared)
    journal.consume_transport_authority(
        journal_path,
        armed,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    _confirm_transport_transaction(journal_path, armed, post_id="950001")
    replacement = {
        **receipt,
        "attempt_epoch": 1_800_000_001,
        "lifecycle_state": "confirmed",
        "post_id": "950001",
    }
    _write_receipt(receipt_path, replacement)
    assert journal.transport_journal_is_blocking(journal_path) is True
    with pytest.raises(journal.TransportJournalError, match="does not match"):
        _retire_confirmed_transport_transaction(
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
    prepared = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = _arm_transport_transaction(journal_path, prepared)
    journal.consume_transport_authority(
        journal_path,
        armed,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    _confirm_transport_transaction(journal_path, armed, post_id="950001")
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
        _retire_confirmed_transport_transaction(
            receipt_path=receipt_path,
            expected_confirmed_receipt=confirmed_receipt,
            lane="quote_image",
            post_id="950001",
        )
    assert (
        journal.transport_journal_is_blocking(journal_path)
        or receipt_path.exists()
    )
    monkeypatch.setattr(journal.os, "fsync", real_fsync)
    # A literal fresh invocation must finish from every persisted prefix of
    # the transition, including guard-only and already-unlinked states.
    _retire_confirmed_transport_transaction(
        receipt_path=receipt_path,
        expected_confirmed_receipt=confirmed_receipt,
        lane="quote_image",
        post_id="950001",
    )
    assert journal.inspect_transport_state(journal_path).classification == "clear"
    assert receipt_path.exists()


def test_byte_identical_journal_replacement_is_not_the_authorised_generation(
    tmp_path: Path,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    prepared = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = _arm_transport_transaction(journal_path, prepared)
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


@pytest.mark.parametrize("target_kind", ("journal", "fence"))
@pytest.mark.parametrize("boundary", ("arm", "consume", "confirm"))
def test_same_inode_change_and_restore_invalidates_transport_authority(
    tmp_path: Path,
    target_kind: str,
    boundary: str,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    authority = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    if boundary != "arm":
        authority = _arm_transport_transaction(journal_path, authority)
    if boundary == "confirm":
        journal.consume_transport_authority(
            journal_path,
            authority,
            method="POST",
            request_path="/2/tweets",
            payload=payload,
        )
    target = (
        journal_path
        if target_kind == "journal"
        else journal.fence_path_for_journal(journal_path)
    )
    expected_ctime_ns = (
        authority.journal_ctime_ns
        if target_kind == "journal"
        else authority.fence_ctime_ns
    )
    before, after = _mutate_and_restore_same_inode(target)
    assert expected_ctime_ns == before.st_ctime_ns
    assert expected_ctime_ns != after.st_ctime_ns

    expected_error = "stale" if boundary != "consume" else "does not bind"
    with pytest.raises(journal.TransportJournalError, match=expected_error):
        if boundary == "arm":
            _arm_transport_transaction(journal_path, authority)
        elif boundary == "consume":
            journal.consume_transport_authority(
                journal_path,
                authority,
                method="POST",
                request_path="/2/tweets",
                payload=payload,
            )
        else:
            _confirm_transport_transaction(
                journal_path,
                authority,
                post_id="950001",
            )
    assert journal.transport_journal_is_blocking(journal_path) is True


@pytest.mark.parametrize("transition", ("arm", "confirm"))
def test_same_byte_replacement_inside_lifecycle_exchange_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    authority = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    if transition == "confirm":
        authority = _arm_transport_transaction(journal_path, authority)
        journal.consume_transport_authority(
            journal_path,
            authority,
            method="POST",
            request_path="/2/tweets",
            payload=payload,
        )
    expected_bytes = journal_path.read_bytes()
    real_exchange = journal._rename_exchange
    injected_inode: int | None = None

    def inject_same_bytes_before_exchange(
        directory_fd: int,
        first: str,
        second: str,
    ) -> None:
        nonlocal injected_inode
        assert first == journal_path.name
        peer = tmp_path / "same-byte-exchange-peer.json"
        _durable_write_bytes(peer, expected_bytes, mode=journal.JOURNAL_MODE)
        injected_inode = peer.stat().st_ino
        os.replace(peer, journal_path)
        os.fsync(directory_fd)
        real_exchange(directory_fd, first, second)

    monkeypatch.setattr(journal, "_rename_exchange", inject_same_bytes_before_exchange)
    with pytest.raises(
        journal.TransportJournalError,
        match="changed during atomic lifecycle transition",
    ):
        if transition == "arm":
            _arm_transport_transaction(journal_path, authority)
        else:
            _confirm_transport_transaction(
                journal_path,
                authority,
                post_id="950001",
            )

    staging = tuple(
        item
        for item in tmp_path.iterdir()
        if item.name.startswith(journal.JOURNAL_STAGING_PREFIX)
    )
    assert injected_inode is not None
    assert len(staging) == 1
    assert staging[0].stat().st_ino == injected_inode
    assert journal.transport_journal_is_blocking(journal_path) is True


def test_immutable_fence_keeps_restart_barrier_when_journal_is_deleted(
    tmp_path: Path,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    prepared = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    _arm_transport_transaction(journal_path, prepared)
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
    prepared = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    armed = _arm_transport_transaction(journal_path, prepared)

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
    prepared = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    journal_path = journal.journal_path_for_receipt(receipt_path)
    authority = prepared
    if transition == "confirm":
        authority = _arm_transport_transaction(journal_path, prepared)
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
            _arm_transport_transaction(journal_path, authority)
        else:
            _confirm_transport_transaction(
                journal_path,
                authority,
                post_id="950001",
            )
    assert journal.transport_journal_is_blocking(journal_path) is True


def test_payload_freeze_rejects_coercion_and_is_immutable() -> None:
    payload = {"text": "Bound", "media": {"media_ids": ["42"]}}
    frozen = journal.freeze_tweet_request(
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    payload["text"] = "mutated"
    payload["media"]["media_ids"][0] = "43"  # type: ignore[index]
    assert frozen.payload() == {
        "text": "Bound",
        "media": {"media_ids": ["42"]},
    }
    with pytest.raises(journal.TransportJournalError, match="ordinary JSON"):
        journal.freeze_tweet_request(
            method="POST",
            request_path="/2/tweets",
            payload=dict(text="x").items(),  # type: ignore[arg-type]
        )
    with pytest.raises(journal.TransportJournalError, match="fields"):
        journal.freeze_tweet_request(
            method="POST",
            request_path="/2/tweets",
            payload={"text": "x", "unsupported": True},
        )
    with pytest.raises(journal.TransportJournalError, match="value type"):
        journal.freeze_tweet_request(
            method="POST",
            request_path="/2/tweets",
            payload={"text": "x", "media": {"media_ids": ("42",)}},
        )


def test_lane_owned_source_validator_is_mandatory_and_bound(tmp_path: Path) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    real_begin = journal.begin_transport_transaction
    with pytest.raises(journal.TransportJournalError, match="semantic source"):
        real_begin(
            receipt_path=receipt_path,
            expected_receipt=receipt,
            lane="quote_image",
            payload=payload,
        )
    with pytest.raises(journal.TransportJournalError, match="semantically bind"):
        journal.bind_transport_source(
            receipt_path=receipt_path,
            expected_receipt=receipt,
            lane="quote_image",
            payload=payload,
            validator_id="tests.reject-source.v1",
            validator=lambda _lane, _receipt, _payload: False,
        )
    binding = journal.bind_transport_source(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
        validator_id="tests.quote-image-source.v1",
        validator=_source_validator,
    )
    changed = {**receipt, "attempt_epoch": 1_800_000_001}
    _write_receipt(receipt_path, changed)
    with pytest.raises(journal.TransportJournalError, match="changed after"):
        real_begin(receipt_path=receipt_path, source_binding=binding)


@pytest.mark.parametrize("lifecycle", ("prepared", "attempting"))
def test_provably_untransmitted_transaction_can_be_aborted(
    tmp_path: Path,
    lifecycle: str,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    binding = journal.bind_transport_source(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
        validator_id="tests.quote-image-source.v1",
        validator=_source_validator,
    )
    real_begin = journal.begin_transport_transaction
    authority = real_begin(receipt_path=receipt_path, source_binding=binding)
    if lifecycle == "attempting":
        authority = _arm_transport_transaction(
            Path(authority.journal_path),
            authority,
        )
    _abort_untransmitted_transport_transaction(
        source_binding=binding,
        authority=authority,
    )
    assert journal.inspect_transport_state(
        journal.journal_path_for_receipt(receipt_path)
    ).classification == "clear"


def test_restarted_attempting_transaction_cannot_be_aborted(tmp_path: Path) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    binding = journal.bind_transport_source(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
        validator_id="tests.quote-image-source.v1",
        validator=_source_validator,
    )
    authority = _begin_transport_transaction(
        receipt_path=receipt_path,
        source_binding=binding,
    )
    authority = _arm_transport_transaction(
        Path(authority.journal_path),
        authority,
    )
    journal.reset_consumed_authorities_for_tests()
    with pytest.raises(journal.TransportJournalError, match="not provably"):
        _abort_untransmitted_transport_transaction(
            source_binding=binding,
            authority=authority,
        )


def test_confirmed_details_and_source_recovery_are_strict(tmp_path: Path) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    authority = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    path = Path(authority.journal_path)
    authority = _arm_transport_transaction(path, authority)
    journal.consume_transport_authority(
        path,
        authority,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    _confirm_transport_transaction(
        path,
        authority,
        post_id="950001",
        confirmation_epoch=1_800_000_123,
    )
    details = journal.inspect_confirmed_transport_transaction(path)
    assert details.post_id == "950001"
    assert details.confirmation_epoch == 1_800_000_123
    assert details.payload() == payload
    recovery = journal.bind_confirmed_transport_source(
        journal_path=path,
        receipt_path=receipt_path,
        validator_id="tests.quote-image-source.v1",
        validator=_source_validator,
    )
    assert recovery.details == details
    assert recovery.source_binding.receipt_document == receipt


@pytest.mark.parametrize("unlink_stage", ("journal", "fence", "guard"))
def test_retirement_unlink_failure_is_resumable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unlink_stage: str,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    authority = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    path = Path(authority.journal_path)
    authority = _arm_transport_transaction(path, authority)
    journal.consume_transport_authority(
        path,
        authority,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    _confirm_transport_transaction(path, authority, post_id="950001")
    confirmed = {**receipt, "lifecycle_state": "confirmed", "post_id": "950001"}
    _write_receipt(receipt_path, confirmed)
    real_unlink = journal.os.unlink
    targets = {
        "journal": path.name,
        "fence": journal.FENCE_BASENAME,
        "guard": None,
    }
    failed = False

    def fail_one(path_value: object, *args: object, **kwargs: object) -> None:
        nonlocal failed
        name = os.fspath(path_value)
        is_target = (
            (targets[unlink_stage] is not None and name == targets[unlink_stage])
            or (
                unlink_stage == "guard"
                and name.startswith(journal.JOURNAL_RETIREMENT_PREFIX)
            )
        )
        if is_target and not failed:
            failed = True
            raise OSError("synthetic unlink failure")
        real_unlink(path_value, *args, **kwargs)

    monkeypatch.setattr(journal.os, "unlink", fail_one)
    with pytest.raises(OSError, match="synthetic"):
        _retire_confirmed_transport_transaction(
            receipt_path=receipt_path,
            expected_confirmed_receipt=confirmed,
            lane="quote_image",
            post_id="950001",
        )
    assert journal.inspect_transport_state(path).blocking or receipt_path.exists()
    monkeypatch.setattr(journal.os, "unlink", real_unlink)
    _retire_confirmed_transport_transaction(
        receipt_path=receipt_path,
        expected_confirmed_receipt=confirmed,
        lane="quote_image",
        post_id="950001",
    )
    assert journal.inspect_transport_state(path).classification == "clear"


def test_retirement_link_failure_is_cleanly_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    authority = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    path = Path(authority.journal_path)
    authority = _arm_transport_transaction(path, authority)
    journal.consume_transport_authority(
        path,
        authority,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    _confirm_transport_transaction(path, authority, post_id="950001")
    confirmed = {**receipt, "lifecycle_state": "confirmed", "post_id": "950001"}
    _write_receipt(receipt_path, confirmed)
    real_link = journal.os.link
    monkeypatch.setattr(
        journal.os,
        "link",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("synthetic link failure")),
    )
    with pytest.raises(OSError, match="synthetic"):
        _retire_confirmed_transport_transaction(
            receipt_path=receipt_path,
            expected_confirmed_receipt=confirmed,
            lane="quote_image",
            post_id="950001",
        )
    assert journal.inspect_transport_state(path).classification == "confirmed_pair"
    monkeypatch.setattr(journal.os, "link", real_link)
    _retire_confirmed_transport_transaction(
        receipt_path=receipt_path,
        expected_confirmed_receipt=confirmed,
        lane="quote_image",
        post_id="950001",
    )
    assert journal.inspect_transport_state(path).classification == "clear"


def test_transport_state_inspection_is_deterministic(tmp_path: Path) -> None:
    receipt_path, receipt, payload = _transaction(tmp_path)
    authority = _begin_transport_transaction(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        lane="quote_image",
        payload=payload,
    )
    path = Path(authority.journal_path)
    first = journal.inspect_transport_state(path)
    second = journal.inspect_transport_state(path)
    assert first == second
    assert first.classification == "prepared_pair"
    assert first.blocking is True


def test_unavailable_journal_directory_blocks_without_proving_durability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fail-closed inspection is not authority to release a process guard."""

    journal_path = tmp_path / "tweet.transport.json"

    def unavailable(_path: object) -> list[str]:
        raise OSError("transient directory inspection failure")

    monkeypatch.setattr(journal.os, "listdir", unavailable)
    state = journal.inspect_transport_state(journal_path)
    assert state.classification == "directory_unavailable"
    assert state.blocking is True
    assert journal.transport_journal_has_valid_restart_barrier(journal_path) is False
