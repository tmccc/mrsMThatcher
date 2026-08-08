from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from semantic_alignment import io


def test_atomic_writes_replace_existing_files_with_deterministic_content(
    tmp_path: Path,
) -> None:
    json_path = tmp_path / "document.json"
    text_path = tmp_path / "document.txt"
    json_path.write_text("old json\n", encoding="utf-8")
    text_path.write_text("old text\n", encoding="utf-8")
    json_path.chmod(0o644)
    text_path.chmod(0o644)

    io.atomic_write_json(json_path, {"z": 1, "a": "£"})
    io.atomic_write_text(text_path, "replacement\n")

    assert json_path.read_text(encoding="utf-8") == (
        '{\n  "a": "£",\n  "z": 1\n}\n'
    )
    assert json.loads(json_path.read_text(encoding="utf-8")) == {"a": "£", "z": 1}
    assert text_path.read_text(encoding="utf-8") == "replacement\n"
    assert json_path.stat().st_mode & 0o777 == 0o644
    assert text_path.stat().st_mode & 0o777 == 0o644
    assert not list(tmp_path.glob(".*.tmp"))


def test_atomic_write_creates_new_destination_with_private_permissions(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"

    io.atomic_write_text(target, "private\n")

    assert target.stat().st_mode & 0o777 == 0o600


def test_atomic_write_does_not_follow_existing_destination_symlink(tmp_path: Path) -> None:
    symlink_target = tmp_path / "target.txt"
    symlink_target.write_text("target\n", encoding="utf-8")
    symlink_target.chmod(0o644)
    destination = tmp_path / "destination.txt"
    destination.symlink_to(symlink_target)

    io.atomic_write_text(destination, "replacement\n")

    assert not destination.is_symlink()
    assert destination.read_text(encoding="utf-8") == "replacement\n"
    assert destination.stat().st_mode & 0o777 == 0o600
    assert symlink_target.read_text(encoding="utf-8") == "target\n"


def test_atomic_write_cleans_uncommitted_temporary_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "document.json"
    target.write_text("original\n", encoding="utf-8")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(io.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        io.atomic_write_json(target, {"replacement": True})

    assert target.read_text(encoding="utf-8") == "original\n"
    assert not list(tmp_path.glob(f".{target.name}.*.tmp"))


def test_repeated_and_concurrent_atomic_writes_use_noncolliding_temporaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "shared.txt"
    real_replace = io.os.replace
    temporary_names: list[str] = []

    def record_replace(source: Path, destination: Path) -> None:
        temporary_names.append(Path(source).name)
        real_replace(source, destination)

    monkeypatch.setattr(io.os, "replace", record_replace)
    values = [f"value-{index}\n" for index in range(12)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda value: io.atomic_write_text(target, value), values))

    assert target.read_text(encoding="utf-8") in values
    assert len(temporary_names) == len(values)
    assert len(set(temporary_names)) == len(values)
    assert not list(tmp_path.glob(f".{target.name}.*.tmp"))
