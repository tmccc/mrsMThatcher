#!/usr/bin/env python3
"""Check PEP 257 documentation coverage for maintained Python code."""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_EXCLUDED_DIRECTORIES = frozenset({
    "tests", ".git", ".venv", "venv", "site-packages", "build", "dist",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    ".tox", ".nox", ".hypothesis",
})
UNTRACKED_OPERATIONAL_SNAPSHOT_PATHS = (
    "production_deployments/**",
    "production_incident_reviews/**",
)


@dataclass(frozen=True, order=True)
class DocumentationViolation:
    """Describe one missing module or public-definition docstring."""

    path: str
    line: int
    kind: str
    name: str

    def render(self) -> str:
        """Render the violation in compiler-style form."""
        target = f" {self.name}" if self.name else ""
        return f"{self.path}:{self.line}: missing {self.kind} docstring{target}"


def maintained_python_files(project_root: Path = PROJECT_ROOT) -> list[Path]:
    """Return maintained Python files from a checkout or unpacked source archive.

    A source archive checks shipped modules and operational snapshots, excluding
    virtual environments, build outputs and caches. Git checkouts keep
    the existing distinction and ignore only untracked operational snapshots.
    """
    if not (project_root / ".git").exists():
        paths = []
        for directory, child_directories, filenames in os.walk(project_root):
            child_directories[:] = [
                name for name in child_directories
                if name not in ARCHIVE_EXCLUDED_DIRECTORIES
            ]
            for name in filenames:
                if name.endswith(".py") and not name.startswith("._"):
                    path = Path(directory) / name
                    if path.is_file():
                        paths.append(path.relative_to(project_root))
        return sorted(paths)
    tracked_result = subprocess.run(
        ["git", "ls-files", "--cached", "--", "*.py"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    untracked_result = subprocess.run(
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "*.py",
            *(
                f":(top,exclude){path}"
                for path in UNTRACKED_OPERATIONAL_SNAPSHOT_PATHS
            ),
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = set()
    for relative_text in (
        *tracked_result.stdout.splitlines(),
        *untracked_result.stdout.splitlines(),
    ):
        relative = Path(relative_text)
        if relative.name.startswith("._"):
            continue
        if "tests" in relative.parts:
            continue
        paths.add(relative)
    return sorted(paths)


def public_definition_nodes(tree: ast.Module) -> Iterable[tuple[ast.AST, str, str]]:
    """Yield public top-level definitions and public methods from a module AST."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                yield node, "function", node.name
            continue
        if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
            continue
        yield node, "class", node.name
        for method in node.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not method.name.startswith("_") or method.name == "__init__":
                yield method, "method", f"{node.name}.{method.name}"


def collect_violations(project_root: Path = PROJECT_ROOT) -> list[DocumentationViolation]:
    """Collect missing module and public-definition docstrings deterministically."""
    violations: list[DocumentationViolation] = []
    for relative in maintained_python_files(project_root):
        source = (project_root / relative).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(relative))
        if ast.get_docstring(tree, clean=False) is None:
            violations.append(DocumentationViolation(str(relative), 1, "module", ""))
        for node, kind, name in public_definition_nodes(tree):
            if ast.get_docstring(node, clean=False) is None:
                violations.append(
                    DocumentationViolation(str(relative), int(node.lineno), kind, name)
                )
    return sorted(violations)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=PROJECT_ROOT,
        help="Git working tree to inspect (default: this repository).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the documentation coverage check."""
    args = build_parser().parse_args(argv)
    violations = collect_violations(args.project_dir.resolve())
    for violation in violations:
        print(violation.render())
    if violations:
        print(f"documentation violations: {len(violations)}")
        return 1
    module_count = len(maintained_python_files(args.project_dir.resolve()))
    print(f"documentation coverage passed: {module_count} modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
