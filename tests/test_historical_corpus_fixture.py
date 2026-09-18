"""Keep the immutable historical corpus usable without repository history."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from tests.helpers import historical_corpus as fixture


def test_historical_fixture_preserves_original_contract_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact historical data remains available with every subprocess disabled."""
    def reject_subprocess(*_args, **_kwargs):
        """Prohibit hidden dependencies on Git, downloads or extraction tools."""
        raise AssertionError("Historical fixture must use no subprocess")

    monkeypatch.setattr(subprocess, "run", reject_subprocess)
    root = fixture._materialise_historical_corpus(tmp_path / "historical")
    assert not (root / ".git").exists()
    research = root / fixture.RESEARCH_RELATIVE
    assert len((root / "mrsMThatcher.txt").read_text().splitlines()) == 620
    assert len(json.loads((research / "research_packets.json").read_text())["items"]) == 627
    assert len(json.loads((research / "corpus_manifest.json").read_text())["records"]) == 632
    assert (root / "historical_context_reply_history.json").read_bytes() == (
        root / "tests/fixtures/historical_context_reply_history.reviewed.json"
    ).read_bytes()
    assert fixture._materialise_historical_corpus(root) == root


def test_changed_historical_bundle_fails_closed(tmp_path: Path, monkeypatch) -> None:
    """Corruption is rejected before any historical output is written."""
    archive = tmp_path / "damaged.tar.xz"
    archive.write_bytes(fixture.FIXTURE_ARCHIVE.read_bytes() + b"changed")
    monkeypatch.setattr(fixture, "FIXTURE_ARCHIVE", archive)
    destination = tmp_path / "historical"
    with pytest.raises(AssertionError, match="hash mismatch"):
        fixture._materialise_historical_corpus(destination)
    assert not destination.exists()


@pytest.mark.parametrize("case", ["unexpected", "traversal", "symlink", "duplicate", "missing"])
def test_historical_bundle_rejects_unsafe_or_incomplete_members(
    case: str, tmp_path: Path, monkeypatch,
) -> None:
    """Even a repinned bundle must have exactly one regular file per allowed path."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:xz") as archive:
        if case != "missing":
            name = {"unexpected": "unexpected.txt", "traversal": "../outside"}.get(
                case, "mrsMThatcher.txt",
            )
            member = tarfile.TarInfo(name)
            if case == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = "../outside"
            archive.addfile(member)
            if case == "duplicate":
                archive.addfile(member)
    bundle = stream.getvalue()
    archive_path = tmp_path / "unsafe.tar.xz"
    archive_path.write_bytes(bundle)
    monkeypatch.setattr(fixture, "FIXTURE_ARCHIVE", archive_path)
    monkeypatch.setattr(fixture, "FIXTURE_SHA256", hashlib.sha256(bundle).hexdigest())
    destination = tmp_path / "historical"
    with pytest.raises(AssertionError, match="fixture (member|archive has missing)"):
        fixture._materialise_historical_corpus(destination)
    assert not destination.exists()
    assert not (tmp_path / "outside").exists()


def test_historical_fixture_refuses_existing_destination_symlink(tmp_path: Path) -> None:
    """An existing tree cannot redirect writes outside the fresh fixture root."""
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = tmp_path / "historical"
    destination.symlink_to(outside, target_is_directory=True)
    with pytest.raises(FileExistsError):
        fixture._materialise_historical_corpus(destination)
    assert list(outside.iterdir()) == []
