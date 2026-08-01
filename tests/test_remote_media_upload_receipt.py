from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import remote_media_upload_receipt as media_receipt
import remote_write_transport_journal as transport_journal
from transaction_mutation_authority import issue_transaction_mutation_authority


def test_filesystem_and_image_size_policy_literals_are_stable() -> None:
    """Prevent host-width and byte-limit tests from following policy drift."""

    assert (
        media_receipt.MAX_FILESYSTEM_IDENTITY_INTEGER,
        media_receipt.MAX_FILESYSTEM_TIMESTAMP_NS,
        media_receipt.RECEIPT_MAX_BYTES,
        media_receipt.IMAGE_MAX_BYTES,
    ) == (
        18_446_744_073_709_551_615,
        18_446_744_073_709_551_615,
        131_072,
        268_435_456,
    )
    assert (
        media_receipt.MAX_FILESYSTEM_IDENTITY_INTEGER
        == transport_journal.MAX_FILESYSTEM_IDENTITY_INTEGER
    )
    assert (
        media_receipt.MAX_FILESYSTEM_TIMESTAMP_NS
        == transport_journal.MAX_FILESYSTEM_TIMESTAMP_NS
    )
    assert media_receipt.RECEIPT_MAX_BYTES == transport_journal.JOURNAL_MAX_BYTES


def test_exact_unlink_rejects_same_inode_mutation_after_path_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A peer-held inode cannot change unnoticed during exact retirement."""

    target = tmp_path / "media-receipt.json"
    expected_bytes = b'{"a":1}\n'
    replacement_bytes = b'{"b":2}\n'
    target.write_bytes(expected_bytes)
    target.chmod(media_receipt.RECEIPT_MODE)
    expected = media_receipt._read_stable_regular(
        target,
        maximum=media_receipt.RECEIPT_MAX_BYTES,
        expected_mode=media_receipt.RECEIPT_MODE,
    )
    peer_fd = os.open(target, os.O_RDWR)
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real_fsync = os.fsync
    mutated = False

    def mutate_after_directory_sync(descriptor: int) -> None:
        nonlocal mutated
        real_fsync(descriptor)
        if descriptor == directory_fd and not mutated:
            mutated = True
            os.lseek(peer_fd, 0, os.SEEK_SET)
            os.write(peer_fd, replacement_bytes)
            os.ftruncate(peer_fd, len(replacement_bytes))
            real_fsync(peer_fd)

    monkeypatch.setattr(media_receipt.os, "fsync", mutate_after_directory_sync)
    try:
        with pytest.raises(
            media_receipt.MediaUploadReceiptError,
            match="unchanged validated inode",
        ):
            media_receipt._unlink_exact_stable_file(
                directory_fd,
                target,
                expected,
                maximum=media_receipt.RECEIPT_MAX_BYTES,
                label="test media receipt",
            )
    finally:
        os.close(directory_fd)
        os.close(peer_fd)

    assert mutated is True
    assert not target.exists()


def _mutation_authority():
    return issue_transaction_mutation_authority(
        lambda _operation: None,
        operation="media receipt focused test",
    )


def _confirm_media_upload(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return media_receipt.confirm_media_upload(*args, **kwargs)


def _abort_untransmitted_media_upload(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return media_receipt.abort_untransmitted_media_upload(*args, **kwargs)


def _retire_confirmed_media_upload(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return media_receipt.retire_confirmed_media_upload(*args, **kwargs)


def _resume_interrupted_confirmed_media_retirement(*args, **kwargs):
    kwargs.setdefault("mutation_authority", _mutation_authority())
    return media_receipt.resume_interrupted_confirmed_media_retirement(
        *args,
        **kwargs,
    )


def _durable_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        mode,
    )
    try:
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _mutate_and_restore_same_inode(path: Path) -> tuple[os.stat_result, os.stat_result]:
    original = path.read_bytes()
    mutated = bytes((original[0] ^ 1,)) + original[1:]
    before = path.stat()
    mode = before.st_mode & 0o777
    _durable_write(path, mutated, mode=mode)
    path.chmod(mode)
    _durable_write(path, original, mode=mode)
    path.chmod(mode)
    after = path.stat()
    assert after.st_ino == before.st_ino
    assert after.st_ctime_ns != before.st_ctime_ns
    assert path.read_bytes() == original
    return before, after


def _fixture(
    tmp_path: Path,
    *,
    lane: str = "quote_image",
) -> tuple[Path, Path, dict[str, object]]:
    image_path = tmp_path / ("001_meme.png" if lane == "daily_meme" else "001.jpg")
    _durable_write(image_path, b"synthetic-image-bytes\x00\x01", mode=0o600)
    receipt_path = tmp_path / f"{lane}_media_upload_receipt.json"
    metadata: dict[str, object] = {
        "endpoint": "/2/media/upload",
        "media_category": "tweet_image",
        "additional_owners": [],
    }
    return receipt_path, image_path, metadata


def _begin(
    receipt_path: Path,
    image_path: Path,
    metadata: dict[str, object],
    *,
    lane: str = "quote_image",
) -> media_receipt.MediaUploadAuthority:
    return media_receipt.begin_media_upload(
        receipt_path=receipt_path,
        image_path=image_path,
        lane=lane,
        mime_type="image/png" if lane == "daily_meme" else "image/jpeg",
        payload_metadata=metadata,
    )


def _consume(
    receipt_path: Path,
    image_path: Path,
    metadata: dict[str, object],
    authority: media_receipt.MediaUploadAuthority,
    *,
    lane: str = "quote_image",
) -> media_receipt.ReceiptBoundMediaPayload:
    payload = media_receipt.bind_media_upload_payload(
        receipt_path,
        authority,
        image_path=image_path,
        lane=lane,
        mime_type="image/png" if lane == "daily_meme" else "image/jpeg",
        payload_metadata=metadata,
    )
    return media_receipt.consume_media_upload_authority(
        receipt_path,
        authority,
        payload=payload,
        lane=lane,
        mime_type="image/png" if lane == "daily_meme" else "image/jpeg",
        payload_metadata=metadata,
    )


def _confirmed(
    tmp_path: Path,
    *,
    lane: str = "quote_image",
) -> tuple[
    Path,
    Path,
    dict[str, object],
    media_receipt.ConfirmedMediaUpload,
]:
    receipt_path, image_path, metadata = _fixture(tmp_path, lane=lane)
    authority = _begin(receipt_path, image_path, metadata, lane=lane)
    _consume(receipt_path, image_path, metadata, authority, lane=lane)
    confirmation = _confirm_media_upload(
        receipt_path,
        authority,
        media_id="780001",
    )
    return receipt_path, image_path, metadata, confirmation


def _bound_main_receipt_bytes() -> bytes:
    return media_receipt.canonical_json_bytes(
        {
            "lifecycle_state": "sending",
            "lane": "quote_image",
            "media_ids": ["780001"],
            "selected_identity": {"image_basename": "001.jpg"},
        }
    )


def _prepared_handoff(
    tmp_path: Path,
    receipt_path: Path,
    confirmation: media_receipt.ConfirmedMediaUpload,
) -> tuple[
    Path,
    Path,
    media_receipt.MediaHandoffAuthority,
]:
    main_receipt_path = tmp_path / "regular_post_receipt.json"
    main_receipt_bytes = _bound_main_receipt_bytes()
    _durable_write(main_receipt_path, main_receipt_bytes)
    transport = transport_journal.begin_transport_transaction(
        receipt_path=main_receipt_path,
        expected_receipt=json.loads(main_receipt_bytes),
        lane="quote_image",
        payload={
            "text": "synthetic quote",
            "media": {"media_ids": [confirmation.media_id]},
        },
        source_validator_id="tests.media-handoff-source.v1",
        source_validator=lambda lane, receipt, payload: (
            lane == "quote_image"
            and receipt.get("lifecycle_state") == "sending"
            and receipt.get("lane") == lane
            and payload.get("media", {}).get("media_ids")
            == [confirmation.media_id]
        ),
    )
    journal_path = Path(transport.journal_path)
    fence_path = Path(transport.fence_path)
    handoff = media_receipt.bind_media_handoff_to_transport(
        receipt_path,
        confirmation,
        transport_journal_path=journal_path,
        transport_fence_path=fence_path,
        source_receipt_path=main_receipt_path,
    )
    return main_receipt_path, journal_path, handoff


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("basename", "."),
        ("basename", ".."),
        ("device", -1),
        (
            "device",
            media_receipt.MAX_FILESYSTEM_IDENTITY_INTEGER + 1,
        ),
        ("inode", 0),
        (
            "inode",
            media_receipt.MAX_FILESYSTEM_IDENTITY_INTEGER + 1,
        ),
        ("ctime_ns", -1),
        (
            "ctime_ns",
            media_receipt.MAX_FILESYSTEM_TIMESTAMP_NS + 1,
        ),
        ("size", 0),
        ("size", transport_journal.JOURNAL_MAX_BYTES + 1),
        ("sha256", int("1" * 64)),
    ),
)
def test_transport_handoff_owner_requires_exact_source_identity(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    _main_receipt, journal_path, _handoff = _prepared_handoff(
        tmp_path,
        receipt_path,
        confirmation,
    )
    document = json.loads(journal_path.read_bytes())
    document["source_receipt"][field] = value
    if field == "sha256":
        document["source_validation"]["receipt_sha256"] = value
    _durable_write(
        journal_path,
        media_receipt.canonical_json_bytes(document),
        mode=media_receipt.RECEIPT_MODE,
    )

    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="transport handoff owner semantics are invalid",
    ):
        media_receipt._transport_owner_snapshot(
            journal_path,
            expected_kind="mrsMThatcher_remote_write_transport_journal",
        )


def test_sending_receipt_is_deterministic_and_contains_exact_image_identity(
    tmp_path: Path,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    first = _begin(receipt_path, image_path, metadata)
    first_bytes = receipt_path.read_bytes()
    first_document = json.loads(first_bytes)
    fence_path = media_receipt.fence_path_for_receipt(receipt_path)
    fence_document = json.loads(fence_path.read_bytes())

    assert first_document["lifecycle_state"] == "sending"
    assert first_document["image"]["basename"] == image_path.name
    assert first_document["image"]["device"] == image_path.stat().st_dev
    assert first_document["image"]["inode"] == image_path.stat().st_ino
    assert first_document["image"]["size"] == image_path.stat().st_size
    assert first_document["image"]["mime_type"] == "image/jpeg"
    assert str(tmp_path) not in first_bytes.decode("utf-8")
    assert fence_document["document_kind"] == media_receipt.FENCE_DOCUMENT_KIND
    assert fence_document["transaction_id"] == first.transaction_id
    assert fence_document["lifecycle_state"] == "sending"
    assert first.fence_device == fence_path.stat().st_dev
    assert first.fence_inode == fence_path.stat().st_ino
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True

    receipt_path.unlink()
    fence_path.unlink()
    second = _begin(receipt_path, image_path, metadata)
    assert receipt_path.read_bytes() == first_bytes
    assert second.transaction_id == first.transaction_id


def test_lane_and_payload_metadata_are_part_of_deterministic_transaction_id(
    tmp_path: Path,
) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_path, first_image, first_metadata = _fixture(first_dir)
    first = _begin(first_path, first_image, first_metadata)

    second_path, second_image, second_metadata = _fixture(second_dir)
    second_metadata["media_category"] = "different"
    second = _begin(second_path, second_image, second_metadata)
    assert second.transaction_id != first.transaction_id


def test_authority_is_exact_and_one_shot(tmp_path: Path) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    payload = media_receipt.bind_media_upload_payload(
        receipt_path,
        authority,
        image_path=image_path,
        lane="quote_image",
        mime_type="image/jpeg",
        payload_metadata=metadata,
    )

    with pytest.raises(media_receipt.MediaUploadReceiptError, match="does not bind"):
        media_receipt.consume_media_upload_authority(
            receipt_path,
            authority,
            payload=payload,
            lane="quote_image",
            mime_type="image/jpeg",
            payload_metadata={**metadata, "media_category": "other"},
        )

    media_receipt.consume_media_upload_authority(
        receipt_path,
        authority,
        payload=payload,
        lane="quote_image",
        mime_type="image/jpeg",
        payload_metadata=metadata,
    )
    with pytest.raises(media_receipt.MediaUploadReceiptError, match="already consumed"):
        media_receipt.consume_media_upload_authority(
            receipt_path,
            authority,
            payload=payload,
            lane="quote_image",
            mime_type="image/jpeg",
            payload_metadata=metadata,
        )


def test_exact_process_issued_unconsumed_authority_aborts_sending_pair(
    tmp_path: Path,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    fence_path = media_receipt.fence_path_for_receipt(receipt_path)

    _abort_untransmitted_media_upload(receipt_path, authority)

    assert not receipt_path.exists()
    assert not fence_path.exists()
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is False
    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="not issued",
    ):
        _abort_untransmitted_media_upload(receipt_path, authority)


def test_reconstructed_or_consumed_authority_cannot_abort_sending_pair(
    tmp_path: Path,
) -> None:
    reconstructed_dir = tmp_path / "reconstructed"
    consumed_dir = tmp_path / "consumed"
    reconstructed_dir.mkdir()
    consumed_dir.mkdir()

    receipt_path, image_path, metadata = _fixture(reconstructed_dir)
    authority = _begin(receipt_path, image_path, metadata)
    reconstructed = media_receipt.MediaUploadAuthority(**authority.__dict__)
    payload = media_receipt.bind_media_upload_payload(
        receipt_path,
        authority,
        image_path=image_path,
        lane="quote_image",
        mime_type="image/jpeg",
        payload_metadata=metadata,
    )
    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="not issued",
    ):
        media_receipt.consume_media_upload_authority(
            receipt_path,
            reconstructed,
            payload=payload,
            lane="quote_image",
            mime_type="image/jpeg",
            payload_metadata=metadata,
        )
    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="not issued",
    ):
        _abort_untransmitted_media_upload(receipt_path, reconstructed)
    assert receipt_path.exists()
    assert media_receipt.fence_path_for_receipt(receipt_path).exists()
    _abort_untransmitted_media_upload(receipt_path, authority)

    receipt_path, image_path, metadata = _fixture(consumed_dir)
    consumed_authority = _begin(receipt_path, image_path, metadata)
    _consume(receipt_path, image_path, metadata, consumed_authority)
    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="consumed.*cannot be aborted",
    ):
        _abort_untransmitted_media_upload(receipt_path, consumed_authority)
    assert receipt_path.exists()
    assert media_receipt.fence_path_for_receipt(receipt_path).exists()
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


@pytest.mark.parametrize(
    "mutation",
    (
        "auxiliary",
        "receipt_hardlink",
        "fence_hardlink",
        "receipt_same_bytes_replace",
        "receipt_symlink",
        "receipt_directory",
        "missing_fence",
    ),
)
def test_untransmitted_abort_rejects_nonexact_or_unsafe_companions(
    tmp_path: Path,
    mutation: str,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    fence_path = media_receipt.fence_path_for_receipt(receipt_path)
    if mutation == "auxiliary":
        _durable_write(
            tmp_path / f"{media_receipt.TRANSITION_PREFIX}synthetic",
            b"barrier",
        )
    elif mutation == "receipt_hardlink":
        os.link(receipt_path, tmp_path / "receipt-peer")
    elif mutation == "fence_hardlink":
        os.link(fence_path, tmp_path / "fence-peer")
    elif mutation == "receipt_same_bytes_replace":
        replacement = tmp_path / "replacement-receipt"
        _durable_write(replacement, receipt_path.read_bytes())
        os.replace(replacement, receipt_path)
    elif mutation == "receipt_symlink":
        target = tmp_path / "receipt-target"
        _durable_write(target, receipt_path.read_bytes())
        receipt_path.unlink()
        receipt_path.symlink_to(target.name)
    elif mutation == "receipt_directory":
        receipt_path.unlink()
        receipt_path.mkdir()
    else:
        fence_path.unlink()

    with pytest.raises(media_receipt.MediaUploadReceiptError):
        _abort_untransmitted_media_upload(receipt_path, authority)
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


@pytest.mark.parametrize("operation", ("unlink", "fsync"))
@pytest.mark.parametrize("fail_at", (1, 2))
@pytest.mark.parametrize("after_effect", (False, True))
def test_untransmitted_abort_faults_never_authorise_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    fail_at: int,
    after_effect: bool,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    fence_path = media_receipt.fence_path_for_receipt(receipt_path)
    real_unlink = media_receipt.os.unlink
    real_fsync = media_receipt.os.fsync
    unlink_calls = 0
    fsync_calls = 0

    def maybe_fail_unlink(*args: object, **kwargs: object) -> None:
        nonlocal unlink_calls
        unlink_calls += 1
        if operation == "unlink" and unlink_calls == fail_at and not after_effect:
            raise OSError("synthetic media-abort unlink failure")
        real_unlink(*args, **kwargs)
        if operation == "unlink" and unlink_calls == fail_at and after_effect:
            raise OSError("synthetic media-abort unlink failure")

    def maybe_fail_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if operation == "fsync" and fsync_calls == fail_at and not after_effect:
            raise OSError("synthetic media-abort fsync failure")
        real_fsync(descriptor)
        if operation == "fsync" and fsync_calls == fail_at and after_effect:
            raise OSError("synthetic media-abort fsync failure")

    monkeypatch.setattr(media_receipt.os, "unlink", maybe_fail_unlink)
    monkeypatch.setattr(media_receipt.os, "fsync", maybe_fail_fsync)
    with pytest.raises(OSError, match="synthetic media-abort"):
        _abort_untransmitted_media_upload(receipt_path, authority)

    # Until fence removal takes effect, the immutable companion is the durable
    # blocker.  Once both removals take effect, the local non-transmission
    # transition is observably complete even if the final call reports an
    # error; the application layers its durable ambiguity marker over that
    # error before releasing its SIGINT guard.
    if fence_path.exists():
        assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True
    else:
        assert not receipt_path.exists()
    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="not issued|consumed",
    ):
        _abort_untransmitted_media_upload(receipt_path, authority)


def test_fresh_process_cannot_reconstruct_untransmitted_abort_authority(
    tmp_path: Path,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority_path = tmp_path / "authority.json"
    script = "\n".join(
        (
            "import json",
            "from dataclasses import asdict",
            "from pathlib import Path",
            "import remote_media_upload_receipt as m",
            f"receipt = Path({str(receipt_path)!r})",
            f"image = Path({str(image_path)!r})",
            f"metadata = {metadata!r}",
            "authority = m.begin_media_upload(",
            "    receipt_path=receipt, image_path=image, lane='quote_image',",
            "    mime_type='image/jpeg', payload_metadata=metadata,",
            ")",
            f"Path({str(authority_path)!r}).write_text(json.dumps(asdict(authority)))",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(media_receipt.__file__).parent,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    reconstructed = media_receipt.MediaUploadAuthority(
        **json.loads(authority_path.read_text(encoding="utf-8"))
    )

    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="not issued",
    ):
        _abort_untransmitted_media_upload(receipt_path, reconstructed)
    assert receipt_path.exists()
    assert media_receipt.fence_path_for_receipt(receipt_path).exists()
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


@pytest.mark.parametrize(
    ("phase", "receipt_present", "fence_present"),
    (
        ("before_receipt_unlink", True, True),
        ("after_receipt_unlink", False, True),
        ("at_first_directory_fsync", False, True),
        ("before_fence_unlink", False, True),
        ("after_fence_unlink", False, False),
        ("at_final_directory_fsync", False, False),
    ),
)
def test_hard_exit_during_untransmitted_abort_is_fresh_process_safe(
    tmp_path: Path,
    phase: str,
    receipt_present: bool,
    fence_present: bool,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority_path = tmp_path / "hard-exit-authority.json"
    script = "\n".join(
        (
            "import json",
            "import os",
            "from dataclasses import asdict",
            "from pathlib import Path",
            "import remote_media_upload_receipt as m",
            "from transaction_mutation_authority import issue_transaction_mutation_authority",
            f"phase = {phase!r}",
            f"receipt = Path({str(receipt_path)!r})",
            f"image = Path({str(image_path)!r})",
            f"metadata = {metadata!r}",
            "authority = m.begin_media_upload(",
            "    receipt_path=receipt, image_path=image, lane='quote_image',",
            "    mime_type='image/jpeg', payload_metadata=metadata,",
            ")",
            f"Path({str(authority_path)!r}).write_text(json.dumps(asdict(authority)))",
            "real_unlink = m.os.unlink",
            "real_fsync = m.os.fsync",
            "unlink_calls = 0",
            "fsync_calls = 0",
            "def controlled_unlink(*args, **kwargs):",
            "    global unlink_calls",
            "    unlink_calls += 1",
            "    if unlink_calls == 1 and phase == 'before_receipt_unlink':",
            "        os._exit(86)",
            "    real_unlink(*args, **kwargs)",
            "    if unlink_calls == 1 and phase == 'after_receipt_unlink':",
            "        os._exit(86)",
            "    if unlink_calls == 2 and phase == 'after_fence_unlink':",
            "        os._exit(86)",
            "def controlled_fsync(descriptor):",
            "    global fsync_calls",
            "    fsync_calls += 1",
            "    if fsync_calls == 1 and phase == 'at_first_directory_fsync':",
            "        os._exit(86)",
            "    real_fsync(descriptor)",
            "    if fsync_calls == 1 and phase == 'before_fence_unlink':",
            "        os._exit(86)",
            "    if fsync_calls == 2 and phase == 'at_final_directory_fsync':",
            "        os._exit(86)",
            "m.os.unlink = controlled_unlink",
            "m.os.fsync = controlled_fsync",
            "m.abort_untransmitted_media_upload(",
            "    receipt, authority,",
            "    mutation_authority=issue_transaction_mutation_authority(",
            "        lambda _operation: None, operation='hard-exit abort test',",
            "    ),",
            ")",
            "raise AssertionError('hard-exit checkpoint was not reached')",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(media_receipt.__file__).parent,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 86, completed.stderr

    fence_path = media_receipt.fence_path_for_receipt(receipt_path)
    assert receipt_path.exists() is receipt_present
    assert fence_path.exists() is fence_present
    assert (
        media_receipt.media_upload_receipt_is_blocking(receipt_path)
        is (receipt_present or fence_present)
    )
    reconstructed = media_receipt.MediaUploadAuthority(
        **json.loads(authority_path.read_text(encoding="utf-8"))
    )
    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="not issued",
    ):
        _abort_untransmitted_media_upload(receipt_path, reconstructed)
    assert receipt_path.exists() is receipt_present
    assert fence_path.exists() is fence_present

    if fence_present and not receipt_present:
        with pytest.raises(media_receipt.MediaUploadReceiptError):
            _resume_interrupted_confirmed_media_retirement(
                receipt_path,
                transport_journal_path=tmp_path / "missing-journal",
                transport_fence_path=tmp_path / "missing-transport-fence",
                source_receipt_path=tmp_path / "missing-source",
            )
        assert fence_path.exists()


def test_same_byte_image_replacement_is_rejected(tmp_path: Path) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    original = image_path.read_bytes()
    replacement = tmp_path / "replacement-image"
    _durable_write(replacement, original)
    os.replace(replacement, image_path)

    with pytest.raises(media_receipt.MediaUploadReceiptError, match="identity changed"):
        _consume(receipt_path, image_path, metadata, authority)
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


def test_same_inode_image_change_and_restore_before_binding_is_rejected(
    tmp_path: Path,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    expected_ctime_ns = int(
        json.loads(receipt_path.read_bytes())["image"]["ctime_ns"]
    )
    before, after = _mutate_and_restore_same_inode(image_path)
    assert expected_ctime_ns == before.st_ctime_ns
    assert expected_ctime_ns != after.st_ctime_ns

    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="identity changed",
    ):
        _consume(receipt_path, image_path, metadata, authority)
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


def test_transport_proof_is_immutable_bytes_not_a_file_name(tmp_path: Path) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    payload = media_receipt.bind_media_upload_payload(
        receipt_path,
        authority,
        image_path=image_path,
        lane="quote_image",
        mime_type="image/jpeg",
        payload_metadata=metadata,
    )

    class MisleadingNamedBody:
        name = str(image_path)

        def read(self) -> bytes:
            return b"different multipart bytes"

    misleading = MisleadingNamedBody()
    forged_data = misleading.read()
    forged = replace(
        payload,
        data=forged_data,
        size=len(forged_data),
        sha256=media_receipt.hashlib.sha256(forged_data).hexdigest(),
    )
    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="immutable media payload",
    ):
        media_receipt.consume_media_upload_authority(
            receipt_path,
            authority,
            payload=forged,
            lane="quote_image",
            mime_type="image/jpeg",
            payload_metadata=metadata,
        )

    consumed = media_receipt.consume_media_upload_authority(
        receipt_path,
        authority,
        payload=payload,
        lane="quote_image",
        mime_type="image/jpeg",
        payload_metadata=metadata,
    )
    assert consumed.data == b"synthetic-image-bytes\x00\x01"
    assert consumed.basename == image_path.name


def test_same_inode_mutation_after_binding_cannot_change_transmitted_bytes(
    tmp_path: Path,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    payload = media_receipt.bind_media_upload_payload(
        receipt_path,
        authority,
        image_path=image_path,
        lane="quote_image",
        mime_type="image/jpeg",
        payload_metadata=metadata,
    )
    before_inode = image_path.stat().st_ino
    mutated = b"post-validation bytes!!"
    assert len(mutated) == len(payload.data)
    descriptor = os.open(image_path, os.O_WRONLY | os.O_TRUNC)
    try:
        os.write(descriptor, mutated)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    assert image_path.stat().st_ino == before_inode

    consumed = media_receipt.consume_media_upload_authority(
        receipt_path,
        authority,
        payload=payload,
        lane="quote_image",
        mime_type="image/jpeg",
        payload_metadata=metadata,
    )
    assert consumed.data != image_path.read_bytes()
    assert consumed.data == b"synthetic-image-bytes\x00\x01"


def test_same_byte_receipt_replacement_is_rejected(tmp_path: Path) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    replacement = tmp_path / "replacement-receipt"
    _durable_write(replacement, receipt_path.read_bytes())
    os.replace(replacement, receipt_path)

    with pytest.raises(media_receipt.MediaUploadReceiptError, match="does not bind"):
        _consume(receipt_path, image_path, metadata, authority)
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


def test_same_inode_receipt_change_and_restore_before_transport_is_rejected(
    tmp_path: Path,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    before, after = _mutate_and_restore_same_inode(receipt_path)
    assert authority.receipt_ctime_ns == before.st_ctime_ns
    assert authority.receipt_ctime_ns != after.st_ctime_ns

    with pytest.raises(media_receipt.MediaUploadReceiptError, match="does not bind"):
        _consume(receipt_path, image_path, metadata, authority)
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


def test_receipt_deletion_before_transport_leaves_immutable_fence_barrier(
    tmp_path: Path,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    fence_path = media_receipt.fence_path_for_receipt(receipt_path)
    receipt_path.unlink()
    directory = os.open(tmp_path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

    with pytest.raises(media_receipt.MediaUploadReceiptError, match="disappeared"):
        _consume(receipt_path, image_path, metadata, authority)
    assert fence_path.exists()
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


@pytest.mark.parametrize("mutation", ("delete", "same_byte_replace", "symlink"))
def test_fence_mutation_before_transport_never_authorises_upload(
    tmp_path: Path,
    mutation: str,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    fence_path = media_receipt.fence_path_for_receipt(receipt_path)
    fence_bytes = fence_path.read_bytes()
    if mutation == "delete":
        fence_path.unlink()
    elif mutation == "same_byte_replace":
        replacement = tmp_path / "replacement-fence"
        _durable_write(replacement, fence_bytes)
        os.replace(replacement, fence_path)
    else:
        target = tmp_path / "fence-target"
        _durable_write(target, fence_bytes)
        fence_path.unlink()
        fence_path.symlink_to(target.name)
    directory = os.open(tmp_path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

    expected = "disappeared" if mutation == "delete" else "does not bind|unsafe metadata"
    with pytest.raises(media_receipt.MediaUploadReceiptError, match=expected):
        _consume(receipt_path, image_path, metadata, authority)
    assert receipt_path.exists()
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


def test_invalid_fence_is_a_fail_closed_barrier(tmp_path: Path) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    fence_path = media_receipt.fence_path_for_receipt(receipt_path)
    _durable_write(fence_path, b'{"broken":NaN}\n')

    with pytest.raises(media_receipt.MediaUploadReceiptError, match="invalid JSON"):
        _consume(receipt_path, image_path, metadata, authority)
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


def test_confirm_requires_consumption_and_atomically_binds_media_id(
    tmp_path: Path,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    with pytest.raises(media_receipt.MediaUploadReceiptError, match="stale"):
        _confirm_media_upload(receipt_path, authority, media_id="780001")

    _consume(receipt_path, image_path, metadata, authority)
    confirmation = _confirm_media_upload(
        receipt_path,
        authority,
        media_id="780001",
    )
    snapshot = media_receipt.inspect_media_upload_receipt(receipt_path)
    assert snapshot is not None
    assert snapshot.document["lifecycle_state"] == "confirmed"
    assert snapshot.document["remote_media_id"] == "780001"
    assert confirmation.receipt_inode == snapshot.inode
    assert confirmation.receipt_sha256 == snapshot.sha256
    fence_path = media_receipt.fence_path_for_receipt(receipt_path)
    assert fence_path.exists()
    assert json.loads(fence_path.read_bytes())["lifecycle_state"] == "sending"
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


@pytest.mark.parametrize("target_kind", ("receipt", "fence"))
def test_same_inode_change_and_restore_after_consumption_prevents_confirmation(
    tmp_path: Path,
    target_kind: str,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    _consume(receipt_path, image_path, metadata, authority)
    target = (
        receipt_path
        if target_kind == "receipt"
        else media_receipt.fence_path_for_receipt(receipt_path)
    )
    expected_ctime_ns = (
        authority.receipt_ctime_ns
        if target_kind == "receipt"
        else authority.fence_ctime_ns
    )
    before, after = _mutate_and_restore_same_inode(target)
    assert expected_ctime_ns == before.st_ctime_ns
    assert expected_ctime_ns != after.st_ctime_ns

    with pytest.raises(media_receipt.MediaUploadReceiptError, match="stale"):
        _confirm_media_upload(receipt_path, authority, media_id="780001")
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


def test_same_byte_replacement_inside_confirmation_exchange_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    _consume(receipt_path, image_path, metadata, authority)
    expected_bytes = receipt_path.read_bytes()
    real_exchange = media_receipt._rename_exchange
    injected_inode: int | None = None

    def inject_same_bytes_before_exchange(
        directory_fd: int,
        first: str,
        second: str,
    ) -> None:
        nonlocal injected_inode
        assert first == receipt_path.name
        peer = tmp_path / "same-byte-media-exchange-peer.json"
        _durable_write(peer, expected_bytes, mode=media_receipt.RECEIPT_MODE)
        injected_inode = peer.stat().st_ino
        os.replace(peer, receipt_path)
        os.fsync(directory_fd)
        real_exchange(directory_fd, first, second)

    monkeypatch.setattr(
        media_receipt,
        "_rename_exchange",
        inject_same_bytes_before_exchange,
    )
    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="changed during atomic lifecycle transition",
    ):
        _confirm_media_upload(receipt_path, authority, media_id="780001")

    staging = tuple(
        item
        for item in tmp_path.iterdir()
        if item.name.startswith(media_receipt.TRANSITION_PREFIX)
    )
    assert injected_inode is not None
    assert len(staging) == 1
    assert staging[0].stat().st_ino == injected_inode
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


def test_confirmed_loader_binds_exact_immutable_fence(tmp_path: Path) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    loaded = media_receipt.load_confirmed_media_upload(receipt_path)
    assert loaded == confirmation

    fence_path = media_receipt.fence_path_for_receipt(receipt_path)
    fence_path.unlink()
    with pytest.raises(media_receipt.MediaUploadReceiptError, match="fence"):
        media_receipt.load_confirmed_media_upload(receipt_path)
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


def test_invalid_torn_disappeared_symlink_and_directory_states_fail_closed(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "media-receipt.json"
    _durable_write(receipt_path, b'{"broken":NaN}\n')
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True
    with pytest.raises(media_receipt.MediaUploadReceiptError, match="invalid JSON"):
        media_receipt.inspect_media_upload_receipt(receipt_path)

    receipt_path.unlink()
    torn = tmp_path / f"{media_receipt.TRANSITION_PREFIX}deadbeef"
    _durable_write(torn, b"partial")
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True
    torn.unlink()

    target = tmp_path / "target"
    _durable_write(target, b"{}\n")
    receipt_path.symlink_to(target.name)
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True
    receipt_path.unlink()
    receipt_path.mkdir()
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


@pytest.mark.parametrize(
    "content, message",
    (
        (b'{"schema_version":1,"schema_version":1}\n', "duplicate JSON key"),
        (
            b'{"outer":{"payload":"first","payload":"second"}}\n',
            "duplicate JSON key",
        ),
        (b'{"schema_version":Infinity}\n', "invalid JSON constant"),
        (b'{}', "not canonical JSON"),
    ),
)
def test_strict_json_rejects_duplicate_nonfinite_and_noncanonical_documents(
    tmp_path: Path,
    content: bytes,
    message: str,
) -> None:
    receipt_path = tmp_path / "strict-receipt.json"
    _durable_write(receipt_path, content)
    with pytest.raises(media_receipt.MediaUploadReceiptError, match=message):
        media_receipt.inspect_media_upload_receipt(receipt_path)
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


@pytest.mark.parametrize("schema_version", (1.0, True))
def test_media_receipt_requires_integer_schema_version(
    tmp_path: Path,
    schema_version: object,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    _begin(receipt_path, image_path, metadata)
    document = json.loads(receipt_path.read_bytes())
    document["schema_version"] = schema_version
    _durable_write(
        receipt_path,
        media_receipt.canonical_json_bytes(document),
    )

    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="semantics are invalid",
    ):
        media_receipt.inspect_media_upload_receipt(receipt_path)
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("payload_metadata_sha256", int("1" * 64)),
        ("image.sha256", int("1" * 64)),
        ("image.mime_type", 123),
        ("image.basename", ""),
        ("image.basename", "."),
        ("image.basename", ".."),
        ("image.device", -1),
        (
            "image.device",
            media_receipt.MAX_FILESYSTEM_IDENTITY_INTEGER + 1,
        ),
        ("image.inode", 0),
        (
            "image.inode",
            media_receipt.MAX_FILESYSTEM_IDENTITY_INTEGER + 1,
        ),
        ("image.ctime_ns", -1),
        (
            "image.ctime_ns",
            media_receipt.MAX_FILESYSTEM_TIMESTAMP_NS + 1,
        ),
        ("image.size", 0),
        ("image.size", media_receipt.IMAGE_MAX_BYTES + 1),
    ),
)
def test_media_receipt_requires_exact_string_hash_and_image_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    _begin(receipt_path, image_path, metadata)
    document = json.loads(receipt_path.read_bytes())
    if field.startswith("image."):
        document["image"][field.split(".", 1)[1]] = value
    else:
        document[field] = value
    if field == "image.size":
        document["transaction_id"] = media_receipt._transaction_id(
            lane=document["lane"],
            image_basename=document["image"]["basename"],
            image_size=document["image"]["size"],
            image_sha256=document["image"]["sha256"],
            mime_type=document["image"]["mime_type"],
            payload_metadata_sha256=document["payload_metadata_sha256"],
        )
    _durable_write(receipt_path, media_receipt.canonical_json_bytes(document))

    with pytest.raises(media_receipt.MediaUploadReceiptError):
        media_receipt.inspect_media_upload_receipt(receipt_path)
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


@pytest.mark.parametrize("schema_version", (2.0, True))
def test_media_handoff_owner_requires_integer_schema_version(
    tmp_path: Path,
    schema_version: object,
) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    source_path, journal_path, _handoff = _prepared_handoff(
        tmp_path,
        receipt_path,
        confirmation,
    )
    document = json.loads(journal_path.read_bytes())
    document["schema_version"] = schema_version
    _durable_write(
        journal_path,
        media_receipt.canonical_json_bytes(document),
    )

    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="owner semantics are invalid",
    ):
        media_receipt.bind_media_handoff_to_transport(
            receipt_path,
            confirmation,
            transport_journal_path=journal_path,
            transport_fence_path=(
                transport_journal.fence_path_for_journal(journal_path)
            ),
            source_receipt_path=source_path,
        )


def test_confirmed_media_receipt_requires_string_remote_media_id(
    tmp_path: Path,
) -> None:
    receipt_path, _image_path, _metadata, _confirmation = _confirmed(tmp_path)
    document = json.loads(receipt_path.read_bytes())
    document["remote_media_id"] = 780001
    _durable_write(
        receipt_path,
        media_receipt.canonical_json_bytes(document),
    )

    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="no valid media ID",
    ):
        media_receipt.inspect_media_upload_receipt(receipt_path)
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


def test_confirmed_handoff_owner_requires_string_remote_post_id(
    tmp_path: Path,
) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    _source_path, journal_path, _handoff = _prepared_handoff(
        tmp_path,
        receipt_path,
        confirmation,
    )
    document = json.loads(journal_path.read_bytes())
    document.update(
        lifecycle_state="confirmed",
        remote_post_id=123,
        confirmation_epoch=1_800_000_010,
    )
    _durable_write(
        journal_path,
        media_receipt.canonical_json_bytes(document),
    )

    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="confirmed transport handoff owner is invalid",
    ):
        media_receipt._transport_owner_snapshot(
            journal_path,
            expected_kind="mrsMThatcher_remote_write_transport_journal",
        )


@pytest.mark.parametrize(
    "confirmation_epoch",
    (
        transport_journal.MIN_CONFIRMATION_EPOCH - 1,
        transport_journal.MAX_CONFIRMATION_EPOCH + 1,
    ),
)
def test_confirmed_handoff_owner_requires_writer_epoch_range(
    tmp_path: Path,
    confirmation_epoch: int,
) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    _source_path, journal_path, _handoff = _prepared_handoff(
        tmp_path,
        receipt_path,
        confirmation,
    )
    document = json.loads(journal_path.read_bytes())
    document.update(
        lifecycle_state="confirmed",
        remote_post_id="950001",
        confirmation_epoch=confirmation_epoch,
    )
    _durable_write(
        journal_path,
        media_receipt.canonical_json_bytes(document),
    )

    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="confirmed transport handoff owner is invalid",
    ):
        media_receipt._transport_owner_snapshot(
            journal_path,
            expected_kind="mrsMThatcher_remote_write_transport_journal",
        )


def test_existing_unknown_receipt_is_never_overwritten(tmp_path: Path) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    _durable_write(receipt_path, b"not-json")
    before = receipt_path.read_bytes()
    with pytest.raises(media_receipt.MediaUploadReceiptError, match="unresolved"):
        _begin(receipt_path, image_path, metadata)
    assert receipt_path.read_bytes() == before


def test_disappeared_receipt_cannot_authorise_or_confirm(tmp_path: Path) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    receipt_path.unlink()

    with pytest.raises(media_receipt.MediaUploadReceiptError, match="disappeared"):
        _consume(receipt_path, image_path, metadata, authority)
    with pytest.raises(media_receipt.MediaUploadReceiptError, match="disappeared"):
        _confirm_media_upload(
            receipt_path,
            authority,
            media_id="780001",
        )


def test_image_symlink_and_non_image_mime_fail_closed(tmp_path: Path) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    real_image = tmp_path / "real-image"
    image_path.rename(real_image)
    image_path.symlink_to(real_image.name)
    with pytest.raises(media_receipt.MediaUploadReceiptError, match="unsafe metadata"):
        _begin(receipt_path, image_path, metadata)
    with pytest.raises(media_receipt.MediaUploadReceiptError, match="MIME"):
        media_receipt.begin_media_upload(
            receipt_path=receipt_path,
            image_path=real_image,
            lane="quote_image",
            mime_type="application/octet-stream",
            payload_metadata=metadata,
        )


@pytest.mark.parametrize("fail_at", (1, 2, 3, 4))
def test_confirm_transition_fsync_failure_always_leaves_a_blocking_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_at: int,
) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    _consume(receipt_path, image_path, metadata, authority)
    real_fsync = media_receipt.os.fsync
    calls = 0

    def failing_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError("synthetic confirmation fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(media_receipt.os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="synthetic confirmation"):
        _confirm_media_upload(
            receipt_path,
            authority,
            media_id="780001",
        )
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


def test_successful_retirement_leaves_independent_transport_pair(
    tmp_path: Path,
) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    main_receipt_path, journal_path, handoff = _prepared_handoff(
        tmp_path,
        receipt_path,
        confirmation,
    )
    journal_before = journal_path.stat()

    result = _retire_confirmed_media_upload(receipt_path, handoff)

    assert not receipt_path.exists()
    assert not media_receipt.fence_path_for_receipt(receipt_path).exists()
    assert result.state == "retired"
    assert main_receipt_path.exists()
    assert journal_path.exists()
    assert (journal_path.stat().st_dev, journal_path.stat().st_ino) == (
        journal_before.st_dev,
        journal_before.st_ino,
    )
    assert Path(handoff.transport_fence_path).exists()


def test_late_main_receipt_loss_cannot_remove_all_handoff_owners(
    tmp_path: Path,
) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    main_receipt_path, journal_path, handoff = _prepared_handoff(
        tmp_path,
        receipt_path,
        confirmation,
    )
    main_receipt_path.unlink()
    directory = os.open(tmp_path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

    result = _retire_confirmed_media_upload(receipt_path, handoff)
    assert result.state == "retired"
    assert not main_receipt_path.exists()
    assert journal_path.exists()
    assert Path(handoff.transport_fence_path).exists()


@pytest.mark.parametrize("owner", ("journal", "fence"))
def test_transport_owner_mutation_prevents_media_retirement(
    tmp_path: Path,
    owner: str,
) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    _main_receipt_path, journal_path, handoff = _prepared_handoff(
        tmp_path,
        receipt_path,
        confirmation,
    )
    target = journal_path if owner == "journal" else Path(handoff.transport_fence_path)
    replacement = tmp_path / f"replacement-{owner}"
    _durable_write(replacement, target.read_bytes())
    os.replace(replacement, target)

    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="stale or mismatched",
    ):
        _retire_confirmed_media_upload(receipt_path, handoff)
    assert receipt_path.exists()
    assert media_receipt.fence_path_for_receipt(receipt_path).exists()


def test_literal_fresh_interpreter_resumes_after_receipt_retirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    main_receipt_path, journal_path, handoff = _prepared_handoff(
        tmp_path,
        receipt_path,
        confirmation,
    )
    real_fsync = media_receipt.os.fsync
    calls = 0

    def fail_after_first_directory_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        real_fsync(descriptor)
        if calls == 1:
            raise OSError("hard interruption after receipt retirement")

    monkeypatch.setattr(media_receipt.os, "fsync", fail_after_first_directory_fsync)
    with pytest.raises(OSError, match="hard interruption"):
        _retire_confirmed_media_upload(receipt_path, handoff)
    monkeypatch.setattr(media_receipt.os, "fsync", real_fsync)
    assert not receipt_path.exists()
    assert media_receipt.fence_path_for_receipt(receipt_path).exists()

    script = "\n".join(
        (
                "from pathlib import Path",
                "import remote_media_upload_receipt as m",
                "from transaction_mutation_authority import issue_transaction_mutation_authority",
                f"r = Path({str(receipt_path)!r})",
                "state = m.resume_interrupted_confirmed_media_retirement(",
                "    r,",
                "    mutation_authority=issue_transaction_mutation_authority(",
                "        lambda _operation: None, operation='fresh-process focused test'",
                "    ),",
            f"    transport_journal_path=Path({str(journal_path)!r}),",
            f"    transport_fence_path=Path({handoff.transport_fence_path!r}),",
            f"    source_receipt_path=Path({str(main_receipt_path)!r}),",
            ")",
            "assert state is not None and state.state == 'retired'",
            "assert not m.media_upload_receipt_is_blocking(r)",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(media_receipt.__file__).parent,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr


def test_fresh_process_resumer_refuses_torn_transport_owner(
    tmp_path: Path,
) -> None:
    """A fence without one intact prepared owner remains fail-closed."""

    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    main_receipt_path, journal_path, handoff = _prepared_handoff(
        tmp_path,
        receipt_path,
        confirmation,
    )
    receipt_path.unlink()
    directory = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    Path(handoff.transport_fence_path).unlink()
    directory = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

    with pytest.raises(
        media_receipt.MediaUploadReceiptError,
        match="missing|cannot be inspected",
    ):
        _resume_interrupted_confirmed_media_retirement(
            receipt_path,
            transport_journal_path=journal_path,
            transport_fence_path=Path(handoff.transport_fence_path),
            source_receipt_path=main_receipt_path,
        )
    assert media_receipt.fence_path_for_receipt(receipt_path).exists()
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


def test_fresh_process_resumer_does_not_take_over_present_receipt(
    tmp_path: Path,
) -> None:
    """The normal in-process handoff retains ownership while receipt exists."""

    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    main_receipt_path, journal_path, handoff = _prepared_handoff(
        tmp_path,
        receipt_path,
        confirmation,
    )
    assert (
        _resume_interrupted_confirmed_media_retirement(
            receipt_path,
            transport_journal_path=journal_path,
            transport_fence_path=Path(handoff.transport_fence_path),
            source_receipt_path=main_receipt_path,
        )
        is None
    )
    assert receipt_path.exists()
    assert media_receipt.fence_path_for_receipt(receipt_path).exists()


@pytest.mark.parametrize("operation", ("fsync", "unlink"))
@pytest.mark.parametrize("fail_at", (1, 2))
@pytest.mark.parametrize("after_effect", (False, True))
def test_every_retirement_interruption_is_inspectable_and_resumable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    fail_at: int,
    after_effect: bool,
) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    main_receipt_path, journal_path, handoff = _prepared_handoff(
        tmp_path,
        receipt_path,
        confirmation,
    )
    real_fsync = media_receipt.os.fsync
    real_unlink = media_receipt.os.unlink
    calls = 0

    def maybe_fail_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if operation == "fsync" and calls == fail_at and not after_effect:
            raise OSError("synthetic retirement fsync failure")
        real_fsync(descriptor)
        if operation == "fsync" and calls == fail_at and after_effect:
            raise OSError("synthetic retirement fsync failure")

    unlink_calls = 0

    def maybe_fail_unlink(*args: object, **kwargs: object) -> None:
        nonlocal unlink_calls
        unlink_calls += 1
        if operation == "unlink" and unlink_calls == fail_at and not after_effect:
            raise OSError("synthetic retirement unlink failure")
        real_unlink(*args, **kwargs)
        if operation == "unlink" and unlink_calls == fail_at and after_effect:
            raise OSError("synthetic retirement unlink failure")

    monkeypatch.setattr(media_receipt.os, "fsync", maybe_fail_fsync)
    monkeypatch.setattr(media_receipt.os, "unlink", maybe_fail_unlink)
    if operation == "fsync" or fail_at <= 2:
        with pytest.raises(OSError, match="synthetic retirement"):
            _retire_confirmed_media_upload(receipt_path, handoff)

    # The independent owner pair survives every destructive boundary.
    assert journal_path.exists()
    assert Path(handoff.transport_fence_path).exists()
    monkeypatch.setattr(media_receipt.os, "fsync", real_fsync)
    monkeypatch.setattr(media_receipt.os, "unlink", real_unlink)

    # Reconstruct the typed authority as a fresh interpreter would.  When both
    # media companions are already absent, the observable state is complete
    # and no process object is needed.
    if media_receipt.media_upload_receipt_is_blocking(receipt_path):
        loaded = (
            media_receipt.load_confirmed_media_upload(receipt_path)
            if receipt_path.exists()
            else None
        )
        resumed = media_receipt.bind_media_handoff_to_transport(
            receipt_path,
            loaded,
            transport_journal_path=journal_path,
            transport_fence_path=Path(handoff.transport_fence_path),
            source_receipt_path=main_receipt_path,
        )
        before = media_receipt.inspect_media_retirement_state(
            receipt_path,
            resumed,
        )
        assert before.state in {"not_started", "receipt_retired"}
        final = _retire_confirmed_media_upload(receipt_path, resumed)
        assert final.state == "retired"
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is False


def test_handoff_rejects_stale_confirmation_and_wrong_media_owner(
    tmp_path: Path,
) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    main_receipt_path, journal_path, handoff = _prepared_handoff(
        tmp_path,
        receipt_path,
        confirmation,
    )
    stale = media_receipt.ConfirmedMediaUpload(
        **{
            **confirmation.__dict__,
            "receipt_inode": confirmation.receipt_inode + 1,
        }
    )
    with pytest.raises(media_receipt.MediaUploadReceiptError, match="stale"):
        media_receipt.bind_media_handoff_to_transport(
            receipt_path,
            stale,
            transport_journal_path=journal_path,
            transport_fence_path=Path(handoff.transport_fence_path),
            source_receipt_path=main_receipt_path,
        )
    wrong = replace(handoff, media_id="different")
    with pytest.raises(media_receipt.MediaUploadReceiptError, match="stale|mismatched"):
        media_receipt.inspect_media_retirement_state(receipt_path, wrong)
    assert receipt_path.exists()
    assert main_receipt_path.exists()


def test_unavailable_media_directory_blocks_without_proving_durability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An inspection error is fail-closed, but is not a durable receipt."""

    receipt_path = tmp_path / "remote_media_upload_receipt.json"

    def unavailable(_path: object) -> list[str]:
        raise OSError("transient media-directory inspection failure")

    monkeypatch.setattr(media_receipt.os, "listdir", unavailable)
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True
    assert (
        media_receipt.media_upload_has_valid_restart_barrier(receipt_path)
        is False
    )
