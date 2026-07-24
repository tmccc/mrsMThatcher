from __future__ import annotations

from pathlib import Path

from tools.check_python_documentation import collect_violations


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_maintained_python_public_api_has_docstrings() -> None:
    assert collect_violations(PROJECT_ROOT) == []


def test_readme_uses_current_runtime_corpus_and_spacing_semantics() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    normalised_readme = " ".join(readme.split())

    assert "611 attribution-eligible" in normalised_readme
    assert "619 canonical" in normalised_readme
    assert "two original-image posts" in normalised_readme
    assert "currently no generated-image frequency cap" not in normalised_readme
    assert "docs/python_api.md" in readme
