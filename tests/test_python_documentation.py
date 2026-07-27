from __future__ import annotations

import subprocess
from pathlib import Path

from tools.check_python_documentation import collect_violations, maintained_python_files


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_maintained_python_public_api_has_docstrings() -> None:
    assert collect_violations(PROJECT_ROOT) == []


def test_discovery_excludes_only_untracked_operational_snapshots(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    sources = {
        "ordinary_pending.py": "def pending():\n    pass\n",
        "production_deployments/run/untracked_backup.py": "def backup():\n    pass\n",
        "production_incident_reviews/run/untracked_backup.py": (
            "def incident_backup():\n    pass\n"
        ),
        "production_deployments_source/module.py": "def similar_name():\n    pass\n",
        "production_deployments/run/tracked.py": "def tracked_snapshot():\n    pass\n",
    }
    for relative_text, source in sources.items():
        path = tmp_path / relative_text
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    subprocess.run(
        ["git", "add", "production_deployments/run/tracked.py"],
        cwd=tmp_path,
        check=True,
    )

    first = maintained_python_files(tmp_path)
    second = maintained_python_files(tmp_path)
    assert first == second
    assert first == [
        Path("ordinary_pending.py"),
        Path("production_deployments/run/tracked.py"),
        Path("production_deployments_source/module.py"),
    ]

    violation_paths = {violation.path for violation in collect_violations(tmp_path)}
    assert violation_paths == {str(path) for path in first}


def test_readme_uses_current_runtime_corpus_and_spacing_semantics() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    normalised_readme = " ".join(readme.split())

    assert "611 attribution-eligible" in normalised_readme
    assert "619 canonical" in normalised_readme
    assert "two original-image posts" in normalised_readme
    assert "currently no generated-image frequency cap" not in normalised_readme
    assert "docs/python_api.md" in readme
