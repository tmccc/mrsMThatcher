from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tools.generated_image_review.review_store import (
    DuplicateDecisionError,
    InvalidDecisionError,
    ReviewStore,
    STAGE_CONFIRM_ALLOWED,
    STAGE_INITIAL,
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


def test_reject_after_confirmation_undo_invalidates_reconfirmation(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review.sqlite3")
    for stage in (STAGE_INITIAL, STAGE_CONFIRM_ALLOWED, STAGE_RECONFIRM_ALLOWED):
        store.record_decision(HASH_A, "allow", 1, stage=stage)

    store.undo_last(STAGE_CONFIRM_ALLOWED)
    assert not store.has_decision(HASH_A, STAGE_RECONFIRM_ALLOWED)
    store.record_decision(HASH_A, "reject", 1, stage=STAGE_CONFIRM_ALLOWED)

    exported = store.export_overrides(tmp_path / "overrides.json")["items"][HASH_A]
    assert exported["decision"] == "reject"
    assert exported["stage"] == STAGE_CONFIRM_ALLOWED
    assert "reconfirmation_decision" not in exported
    history = store.conn.execute("SELECT * FROM decision_history ORDER BY id").fetchall()
    assert len(history) == 4
    assert history[1]["undone_at"] is not None
    assert history[2]["undone_at"] is not None
    store.close()


def test_downstream_invalidation_failure_rolls_back_entire_undo(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review.sqlite3")
    stages = (STAGE_INITIAL, STAGE_CONFIRM_ALLOWED, STAGE_RECONFIRM_ALLOWED)
    for stage in stages:
        store.record_decision(HASH_A, "allow", 1, stage=stage)
    store.conn.execute(
        """
        CREATE TRIGGER fail_invalidation BEFORE UPDATE OF undone_at ON decision_history
        WHEN OLD.stage = 'reconfirm_allowed'
        BEGIN SELECT RAISE(FAIL, 'invalidation failed'); END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="invalidation failed"):
        store.undo_last(STAGE_CONFIRM_ALLOWED)

    assert all(store.has_decision(HASH_A, stage) for stage in stages)
    assert store.conn.execute("SELECT COUNT(*) FROM decision_history WHERE undone_at IS NOT NULL").fetchone()[0] == 0
    store.conn.execute("DROP TRIGGER fail_invalidation")
    assert store.undo_last(STAGE_CONFIRM_ALLOWED) is not None
    assert store.has_decision(HASH_A, STAGE_INITIAL)
    assert not store.has_decision(HASH_A, STAGE_CONFIRM_ALLOWED)
    assert not store.has_decision(HASH_A, STAGE_RECONFIRM_ALLOWED)
    store.close()


@pytest.mark.parametrize("stage", [STAGE_INITIAL, STAGE_CONFIRM_ALLOWED])
def test_upstream_undo_and_rereview_require_fresh_downstream_reviews(tmp_path: Path, stage: str) -> None:
    store = ReviewStore(tmp_path / "review.sqlite3")
    stages = (STAGE_INITIAL, STAGE_CONFIRM_ALLOWED, STAGE_RECONFIRM_ALLOWED)
    for current in stages:
        store.record_decision(HASH_A, "allow", 1, stage=current)

    store.undo_last(stage)
    for current in stages[stages.index(stage):]:
        assert not store.has_decision(HASH_A, current)
    store.record_decision(HASH_A, "allow", 1, stage=stage)
    for current in stages[stages.index(stage) + 1:]:
        assert store.progress([HASH_A], stage=current).remaining == 1
        store.record_decision(HASH_A, "allow", 1, stage=current)

    exported = store.export_overrides(tmp_path / "overrides.json")["items"][HASH_A]
    assert exported["stage"] == STAGE_RECONFIRM_ALLOWED
    store.close()


@pytest.mark.parametrize("stage", [STAGE_INITIAL, STAGE_CONFIRM_ALLOWED])
@pytest.mark.parametrize("replacement", [None, "allow", "reject"])
def test_reopen_invalidates_legacy_descendants_using_history_order(
    tmp_path: Path, monkeypatch, stage: str, replacement: str | None
) -> None:
    # All actions have one timestamp, so only history IDs can detect stale allows.
    now = "2026-09-19T12:00:00Z"
    monkeypatch.setattr("tools.generated_image_review.review_store.utc_now", lambda: now)
    database = tmp_path / "review.sqlite3"
    store = ReviewStore(database)
    stages = (STAGE_INITIAL, STAGE_CONFIRM_ALLOWED, STAGE_RECONFIRM_ALLOWED)
    for current in stages:
        store.record_decision(HASH_A, "allow", 1, stage=current)
    store.close()

    # Reproduce a database left by the previous implementation, which undid only
    # the selected stage and retained approvals referring to its old decision.
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE decision_history SET undone_at = ? WHERE stage = ?", (now, stage))
        if replacement is not None:
            conn.execute(
                "INSERT INTO decision_history (quote_hash, decision, reviewed_at, item_order, stage) VALUES (?, ?, ?, ?, ?)",
                (HASH_A, replacement, now, 1, stage),
            )

    reopened = ReviewStore(database)
    for current in stages[stages.index(stage) + 1:]:
        assert not reopened.has_decision(HASH_A, current)
        assert reopened.active_decisions(current) == {}
    exported = reopened.export_overrides(tmp_path / "overrides.json")["items"].get(HASH_A)
    if stage == STAGE_INITIAL and replacement is None:
        assert exported is None
    else:
        assert exported["decision"] == (replacement or "allow")
        assert exported["stage"] == (stage if replacement else STAGE_INITIAL)
        assert "reconfirmation_decision" not in exported
    assert reopened.conn.execute("SELECT COUNT(*) FROM decision_history").fetchone()[0] == 3 + (replacement is not None)
    reopened.close()

    reopened = ReviewStore(database)
    assert reopened.active_decisions(STAGE_RECONFIRM_ALLOWED) == {}
    reopened.close()


@pytest.mark.parametrize("prior_decision", [None, "reject"])
def test_confirmation_requires_active_upstream_allow(tmp_path: Path, prior_decision: str | None) -> None:
    store = ReviewStore(tmp_path / "review.sqlite3")
    if prior_decision:
        store.record_decision(HASH_A, prior_decision, 1)
    with pytest.raises(InvalidDecisionError, match="prior-stage allow"):
        store.record_decision(HASH_A, "allow", 1, stage=STAGE_CONFIRM_ALLOWED)
    assert not store.has_decision(HASH_A, STAGE_CONFIRM_ALLOWED)
    store.close()
