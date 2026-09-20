"""Scope synthetic quotation eligibility to one supplied test bot."""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def patch_completed_research_quotes(
    monkeypatch: pytest.MonkeyPatch,
    bot: ModuleType,
    completed: Callable[[], set[str]],
) -> None:
    """Override eligibility without changing shared production owner classes."""
    owner_factory = bot._quote_candidates_owner

    class FixtureQuoteCandidates(type(owner_factory())):
        def completed(self):
            return completed()

    monkeypatch.setattr(
        bot,
        "_quote_candidates_owner",
        lambda: FixtureQuoteCandidates(**vars(owner_factory())),
    )
