from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

import remote_media_upload_receipt as media_receipt


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
) -> None:
    media_receipt.consume_media_upload_authority(
        receipt_path,
        authority,
        image_path=image_path,
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
    confirmation = media_receipt.confirm_media_upload(
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

    with pytest.raises(media_receipt.MediaUploadReceiptError, match="does not bind"):
        media_receipt.consume_media_upload_authority(
            receipt_path,
            authority,
            image_path=image_path,
            lane="quote_image",
            mime_type="image/jpeg",
            payload_metadata={**metadata, "media_category": "other"},
        )

    _consume(receipt_path, image_path, metadata, authority)
    with pytest.raises(media_receipt.MediaUploadReceiptError, match="already consumed"):
        _consume(receipt_path, image_path, metadata, authority)


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


def test_same_byte_receipt_replacement_is_rejected(tmp_path: Path) -> None:
    receipt_path, image_path, metadata = _fixture(tmp_path)
    authority = _begin(receipt_path, image_path, metadata)
    replacement = tmp_path / "replacement-receipt"
    _durable_write(replacement, receipt_path.read_bytes())
    os.replace(replacement, receipt_path)

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
        media_receipt.confirm_media_upload(receipt_path, authority, media_id="780001")

    _consume(receipt_path, image_path, metadata, authority)
    confirmation = media_receipt.confirm_media_upload(
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
        media_receipt.confirm_media_upload(
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
        media_receipt.confirm_media_upload(
            receipt_path,
            authority,
            media_id="780001",
        )
    assert media_receipt.media_upload_receipt_is_blocking(receipt_path) is True


def test_successful_retirement_leaves_exact_main_post_receipt(tmp_path: Path) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    main_receipt_path = tmp_path / "regular_post_receipt.json"
    main_receipt_bytes = _bound_main_receipt_bytes()
    _durable_write(main_receipt_path, main_receipt_bytes)
    before = main_receipt_path.stat()

    media_receipt.retire_confirmed_media_upload(
        receipt_path,
        confirmation,
        main_post_receipt_path=main_receipt_path,
        expected_main_post_receipt_bytes=main_receipt_bytes,
    )

    after = main_receipt_path.stat()
    assert not receipt_path.exists()
    assert not media_receipt.fence_path_for_receipt(receipt_path).exists()
    assert main_receipt_path.read_bytes() == main_receipt_bytes
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert after.st_nlink == 1
    assert not any(
        item.name.startswith(media_receipt.RETIREMENT_GUARD_PREFIX)
        for item in tmp_path.iterdir()
    )


@pytest.mark.parametrize("fail_at", (1, 2, 3, 4))
def test_retirement_directory_fsync_failure_always_leaves_a_durable_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_at: int,
) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    main_receipt_path = tmp_path / "regular_post_receipt.json"
    main_receipt_bytes = _bound_main_receipt_bytes()
    _durable_write(main_receipt_path, main_receipt_bytes)
    real_fsync = media_receipt.os.fsync
    calls = 0

    def failing_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError("synthetic retirement fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(media_receipt.os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="synthetic retirement"):
        media_receipt.retire_confirmed_media_upload(
            receipt_path,
            confirmation,
            main_post_receipt_path=main_receipt_path,
            expected_main_post_receipt_bytes=main_receipt_bytes,
        )

    assert main_receipt_path.exists()
    assert main_receipt_path.read_bytes() == main_receipt_bytes
    durable_paths = [
        receipt_path,
        media_receipt.fence_path_for_receipt(receipt_path),
        main_receipt_path,
    ]
    durable_paths.extend(
        item
        for item in tmp_path.iterdir()
        if item.name.startswith(media_receipt.RETIREMENT_GUARD_PREFIX)
    )
    assert any(path.exists() and stat.S_ISREG(path.stat().st_mode) for path in durable_paths)


def test_retirement_rejects_stale_confirmation_and_wrong_main_receipt(
    tmp_path: Path,
) -> None:
    receipt_path, _image_path, _metadata, confirmation = _confirmed(tmp_path)
    main_receipt_path = tmp_path / "regular_post_receipt.json"
    actual = _bound_main_receipt_bytes()
    _durable_write(main_receipt_path, actual)
    stale = media_receipt.ConfirmedMediaUpload(
        **{
            **confirmation.__dict__,
            "receipt_inode": confirmation.receipt_inode + 1,
        }
    )
    with pytest.raises(media_receipt.MediaUploadReceiptError, match="stale"):
        media_receipt.retire_confirmed_media_upload(
            receipt_path,
            stale,
            main_post_receipt_path=main_receipt_path,
            expected_main_post_receipt_bytes=actual,
        )
    with pytest.raises(media_receipt.MediaUploadReceiptError, match="does not"):
        media_receipt.retire_confirmed_media_upload(
            receipt_path,
            confirmation,
            main_post_receipt_path=main_receipt_path,
            expected_main_post_receipt_bytes=media_receipt.canonical_json_bytes(
                {"post_id": "different"}
            ),
        )
    assert receipt_path.exists()
    assert main_receipt_path.exists()
