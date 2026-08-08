#!/usr/bin/env python3
"""Resolve an explicit project root for historical-context research tools.

Completed research runners may live outside the Git worktree, so deriving the
project root from ``__file__`` is not reliable.  Reusable runners should call
``resolve_project_root`` and may be directed at a registered project worktree
with ``MRS_MTHATCHER_PROJECT_ROOT``.

The environment override is deliberately read only from the process
environment.  This module never loads ``mrsMThatcher.env``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping


PROJECT_ROOT_ENV = "MRS_MTHATCHER_PROJECT_ROOT"
RESEARCH_RELATIVE = Path("semantic_alignment_research/quote_research_full_001")
REQUIRED_PROJECT_PATHS = (
    Path("mrsMThatcher.txt"),
    Path("quote_analysis.json"),
    RESEARCH_RELATIVE / "corpus_manifest.json",
    RESEARCH_RELATIVE / "research_packets.json",
    RESEARCH_RELATIVE / "final_unresolved/final_research_status.json",
    RESEARCH_RELATIVE / "final_unresolved/unresolved_cases.json",
    RESEARCH_RELATIVE / "historical_context_source_role_audit.json",
    RESEARCH_RELATIVE / "unresolved_quotes.json",
    Path("historical_context_published_reply_semantic_review.json"),
    Path(
        "semantic_alignment_research/quote_attribution_cleanup_001/"
        "deployment_candidate/runtime_eligible_quote_manifest.json"
    ),
)


class ProjectRootError(ValueError):
    """The configured historical-context project root is unsafe or incomplete."""


def _git_toplevel(root: Path) -> Path:
    """Return the registered Git worktree root without exposing configuration."""

    try:
        result = subprocess.run(
            ("git", "-C", str(root), "rev-parse", "--show-toplevel"),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProjectRootError(
            "configured project root is not a readable registered Git worktree"
        ) from exc
    value = result.stdout.strip()
    if not value:
        raise ProjectRootError("configured project root has no Git worktree root")
    return Path(value).resolve()


def validate_project_root(root: Path) -> Path:
    """Validate and return one canonical registered project-worktree path."""

    candidate = Path(root)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ProjectRootError("configured project root does not exist") from exc
    if not resolved.is_dir():
        raise ProjectRootError("configured project root is not a directory")
    if _git_toplevel(resolved) != resolved:
        raise ProjectRootError(
            "configured project root must be the top level of a registered Git worktree"
        )
    missing = [
        str(relative)
        for relative in REQUIRED_PROJECT_PATHS
        if not (resolved / relative).is_file()
    ]
    if missing:
        raise ProjectRootError(
            "configured project root is missing required authoritative inputs: "
            + ", ".join(missing)
        )
    return resolved


def resolve_project_root(
    default_root: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the default root or the explicit process-environment override."""

    values = os.environ if environment is None else environment
    configured = values.get(PROJECT_ROOT_ENV)
    if configured is None:
        candidate = Path(default_root)
    else:
        configured = configured.strip()
        if not configured:
            raise ProjectRootError(f"{PROJECT_ROOT_ENV} must not be empty")
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            raise ProjectRootError(f"{PROJECT_ROOT_ENV} must be an absolute path")
    return validate_project_root(candidate)
