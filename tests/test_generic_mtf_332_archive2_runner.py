from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "run_generic_mtf_332_archive2_assessment.py"


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "generic_mtf_332_archive2_runner_test",
        SOURCE,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def mirror(tmp_path: Path) -> Path:
    root = tmp_path / "www.margaretthatcher.org"
    (root / "document").mkdir(parents=True)
    return root


def test_html_suffix_and_extensionless_representations_are_supported(
    tmp_path: Path,
) -> None:
    runner = load_runner()
    root = mirror(tmp_path)
    extensionless = root / "document" / "103384"
    html = root / "document" / "103618.html"
    extensionless.write_text("first", encoding="utf-8")
    html.write_text("second", encoding="utf-8")

    assert runner.candidate_paths(root, "103384") == [
        extensionless.resolve()
    ]
    assert runner.candidate_paths(root, "103618") == [html.resolve()]


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    runner = load_runner()
    root = mirror(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("private", encoding="utf-8")
    (root / "document" / "103384.html").symlink_to(outside)

    with pytest.raises(RuntimeError, match="not a regular file"):
        runner.candidate_paths(root, "103384")


def test_conflicting_duplicate_representations_fail_closed(
    tmp_path: Path,
) -> None:
    runner = load_runner()
    root = mirror(tmp_path)
    (root / "document" / "103384").write_text("first", encoding="utf-8")
    (root / "document" / "103384.html").write_text(
        "different",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="conflicting local files"):
        runner.candidate_paths(root, "103384")


def test_runner_requires_explicit_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = load_runner()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SOURCE),
            "--output-dir",
            str(tmp_path / "output"),
        ],
    )

    assert runner.main() == 2
    assert "NOT STARTED" in capsys.readouterr().out
    assert not (tmp_path / "output").exists()


def test_execute_requires_operator_supplied_private_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = load_runner()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SOURCE),
            "--execute",
            "--output-dir",
            str(tmp_path / "output"),
        ],
    )

    assert runner.main() == 2
    assert "requires --preserved-run-dir" in capsys.readouterr().err
    assert not (tmp_path / "output").exists()
