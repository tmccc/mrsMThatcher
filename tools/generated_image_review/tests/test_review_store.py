from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.generated_image_review.review_store import (
    DuplicateDecisionError,
    InvalidDecisionError,
    ReviewStore,
    STAGE_CONFIRM_ALLOWED,
    STAGE_RECONFIRM_ALLOWED,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def test_allow_and_reject_persist_after_reopen(tmp_path: Path) -> None:
    db = tmp_path / "review.sqlite3"
    store = ReviewStore(db)
    store.record_decision(HASH_A, "allow", 1)
    store.record_decision(HASH_B, "reject", 2)
    store.close()

    reopened = ReviewStore(db)
    progress = reopened.progress([HASH_A, HASH_B])

    assert progress.reviewed == 2
    assert progress.allowed == 1
    assert progress.rejected == 1
    reopened.close()


def test_duplicate_and_invalid_decisions_are_rejected(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review.sqlite3")
    store.record_decision(HASH_A, "allow", 1)

    with pytest.raises(DuplicateDecisionError):
        store.record_decision(HASH_A, "reject", 1)
    with pytest.raises(InvalidDecisionError):
        store.record_decision(HASH_B, "maybe", 2)

    assert store.progress([HASH_A, HASH_B]).reviewed == 1
    store.close()


def test_undo_reverses_latest_decision_and_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "review.sqlite3"
    store = ReviewStore(db)
    store.record_decision(HASH_A, "allow", 1)
    store.record_decision(HASH_B, "reject", 2)

    undone = store.undo_last()

    assert undone is not None
    assert undone["quote_hash"] == HASH_B
    assert store.progress([HASH_A, HASH_B]).reviewed == 1
    store.close()

    reopened = ReviewStore(db)
    assert reopened.progress([HASH_A, HASH_B]).remaining == 1
    assert reopened.undo_last()["quote_hash"] == HASH_A
    assert reopened.undo_last() is None
    reopened.close()


def test_export_is_strict_json_and_contains_only_active_decisions(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review.sqlite3")
    store.record_decision(HASH_A, "allow", 1)
    store.record_decision(HASH_B, "reject", 2)
    store.undo_last()
    export_path = tmp_path / "out" / "overrides.json"

    payload = store.export_overrides(export_path, [HASH_A, HASH_B])

    loaded = json.loads(export_path.read_text(encoding="utf-8"))
    assert loaded == payload
    assert loaded["schema_version"] == 1
    assert list(loaded["items"].keys()) == [HASH_A]
    assert loaded["items"][HASH_A]["decision"] == "allow"
    assert loaded["items"][HASH_A]["stage"] == "initial"
    assert "NaN" not in export_path.read_text(encoding="utf-8")
    store.close()


def test_confirmation_stage_preserves_initial_decision_and_sets_final_export(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review.sqlite3")
    store.record_decision(HASH_A, "allow", 1)
    store.record_decision(HASH_A, "reject", 1, stage=STAGE_CONFIRM_ALLOWED)

    assert store.progress([HASH_A], stage=STAGE_CONFIRM_ALLOWED).rejected == 1
    export_path = tmp_path / "overrides.json"
    payload = store.export_overrides(export_path, [HASH_A])

    exported = payload["items"][HASH_A]
    assert exported["decision"] == "reject"
    assert exported["stage"] == STAGE_CONFIRM_ALLOWED
    assert exported["initial_decision"] == "allow"
    assert exported["confirmation_decision"] == "reject"
    store.close()


def test_confirmation_undo_does_not_remove_initial_decision(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review.sqlite3")
    store.record_decision(HASH_A, "allow", 1)
    store.record_decision(HASH_A, "allow", 1, stage=STAGE_CONFIRM_ALLOWED)

    undone = store.undo_last(STAGE_CONFIRM_ALLOWED)

    assert undone["stage"] == STAGE_CONFIRM_ALLOWED
    assert store.has_decision(HASH_A)
    assert not store.has_decision(HASH_A, STAGE_CONFIRM_ALLOWED)
    store.close()


def test_reconfirmation_stage_overrides_export_after_confirmation(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review.sqlite3")
    store.record_decision(HASH_A, "allow", 1)
    store.record_decision(HASH_A, "allow", 1, stage=STAGE_CONFIRM_ALLOWED)
    store.record_decision(HASH_A, "reject", 1, stage=STAGE_RECONFIRM_ALLOWED)

    payload = store.export_overrides(tmp_path / "overrides.json", [HASH_A])
    exported = payload["items"][HASH_A]

    assert exported["decision"] == "reject"
    assert exported["stage"] == STAGE_RECONFIRM_ALLOWED
    assert exported["initial_decision"] == "allow"
    assert exported["confirmation_decision"] == "allow"
    assert exported["reconfirmation_decision"] == "reject"
    store.close()
