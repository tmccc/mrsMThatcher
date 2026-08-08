from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import historical_context_project_root as project_root


def test_default_project_root_is_validated() -> None:
    expected = Path(__file__).resolve().parents[1]

    assert project_root.resolve_project_root(expected, environment={}) == expected


def test_absolute_environment_project_root_is_accepted() -> None:
    expected = Path(__file__).resolve().parents[1]

    assert project_root.resolve_project_root(
        Path("/definitely/not/the/default"),
        environment={project_root.PROJECT_ROOT_ENV: str(expected)},
    ) == expected


@pytest.mark.parametrize("configured", ["", "relative/project/root", "."])
def test_empty_or_relative_environment_project_root_is_rejected(
    configured: str,
) -> None:
    with pytest.raises(project_root.ProjectRootError, match="empty|absolute"):
        project_root.resolve_project_root(
            Path(__file__).resolve().parents[1],
            environment={project_root.PROJECT_ROOT_ENV: configured},
        )


def test_nonexistent_environment_project_root_is_rejected(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(project_root.ProjectRootError, match="does not exist"):
        project_root.resolve_project_root(
            Path(__file__).resolve().parents[1],
            environment={project_root.PROJECT_ROOT_ENV: str(missing)},
        )


def test_non_worktree_environment_project_root_is_rejected(tmp_path: Path) -> None:
    for relative in project_root.REQUIRED_PROJECT_PATHS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(project_root.ProjectRootError, match="registered Git worktree"):
        project_root.resolve_project_root(
            Path(__file__).resolve().parents[1],
            environment={project_root.PROJECT_ROOT_ENV: str(tmp_path)},
        )


def test_incomplete_registered_worktree_is_rejected(tmp_path: Path) -> None:
    subprocess.run(
        ("git", "init", "--quiet", str(tmp_path)),
        check=True,
        capture_output=True,
        text=True,
    )

    with pytest.raises(project_root.ProjectRootError, match="missing required"):
        project_root.resolve_project_root(
            Path(__file__).resolve().parents[1],
            environment={project_root.PROJECT_ROOT_ENV: str(tmp_path)},
        )
