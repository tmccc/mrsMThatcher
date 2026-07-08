from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

VALID_DECISIONS = {"allow", "reject"}
STAGE_INITIAL = "initial"
STAGE_CONFIRM_ALLOWED = "confirm_allowed"
STAGE_RECONFIRM_ALLOWED = "reconfirm_allowed"
VALID_STAGES = {STAGE_INITIAL, STAGE_CONFIRM_ALLOWED, STAGE_RECONFIRM_ALLOWED}


class ReviewStoreError(RuntimeError):
    pass


class DuplicateDecisionError(ReviewStoreError):
    pass


class InvalidDecisionError(ReviewStoreError):
    pass


@dataclass(frozen=True)
class Progress:
    total: int
    reviewed: int
    remaining: int
    allowed: int
    rejected: int


class ReviewStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.database_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def active_decisions(self, stage: str | None = None) -> dict[str, dict[str, str]]:
        if stage is not None and stage not in VALID_STAGES:
            raise InvalidDecisionError(f"Unsupported decision stage: {stage}")
        where = "WHERE undone_at IS NULL"
        params: tuple[str, ...] = ()
        if stage is not None:
            where += " AND stage = ?"
            params = (stage,)
        with self._lock:
            rows = self.conn.execute(
                f"""
                SELECT quote_hash, decision, reviewed_at, stage
                FROM decision_history
                {where}
                ORDER BY quote_hash
                """,
                params,
            ).fetchall()
        return {
            row["quote_hash"]: {"decision": row["decision"], "reviewed_at": row["reviewed_at"], "stage": row["stage"]}
            for row in rows
        }

    def has_decision(self, quote_hash: str, stage: str = STAGE_INITIAL) -> bool:
        if stage not in VALID_STAGES:
            raise InvalidDecisionError(f"Unsupported decision stage: {stage}")
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM decision_history WHERE quote_hash = ? AND stage = ? AND undone_at IS NULL LIMIT 1",
                (quote_hash, stage),
            ).fetchone()
        return row is not None

    def record_decision(
        self,
        quote_hash: str,
        decision: str,
        item_order: int,
        stage: str = STAGE_INITIAL,
    ) -> dict[str, str]:
        if decision not in VALID_DECISIONS:
            raise InvalidDecisionError(f"Unsupported decision: {decision}")
        if stage not in VALID_STAGES:
            raise InvalidDecisionError(f"Unsupported decision stage: {stage}")
        now = utc_now()
        with self._lock:
            with self.conn:
                existing = self.conn.execute(
                    "SELECT id FROM decision_history WHERE quote_hash = ? AND stage = ? AND undone_at IS NULL",
                    (quote_hash, stage),
                ).fetchone()
                if existing is not None:
                    raise DuplicateDecisionError(f"Already reviewed in stage {stage}: {quote_hash}")
                self.conn.execute(
                    """
                    INSERT INTO decision_history (quote_hash, decision, reviewed_at, item_order, stage)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (quote_hash, decision, now, int(item_order), stage),
                )
        return {"quote_hash": quote_hash, "decision": decision, "reviewed_at": now, "stage": stage}

    def undo_last(self, stage: str | None = None) -> dict[str, str] | None:
        if stage is not None and stage not in VALID_STAGES:
            raise InvalidDecisionError(f"Unsupported decision stage: {stage}")
        now = utc_now()
        where = "WHERE undone_at IS NULL"
        params: tuple[str, ...] = ()
        if stage is not None:
            where += " AND stage = ?"
            params = (stage,)
        with self._lock:
            with self.conn:
                row = self.conn.execute(
                    f"""
                    SELECT id, quote_hash, decision, reviewed_at, stage
                    FROM decision_history
                    {where}
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    params,
                ).fetchone()
                if row is None:
                    return None
                self.conn.execute(
                    "UPDATE decision_history SET undone_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
        return {
            "quote_hash": row["quote_hash"],
            "decision": row["decision"],
            "reviewed_at": row["reviewed_at"],
            "stage": row["stage"],
            "undone_at": now,
        }

    def progress(self, eligible_hashes: Iterable[str], stage: str = STAGE_INITIAL) -> Progress:
        eligible = set(eligible_hashes)
        decisions = self.active_decisions(stage)
        active = {key: value for key, value in decisions.items() if key in eligible}
        allowed = sum(1 for value in active.values() if value["decision"] == "allow")
        rejected = sum(1 for value in active.values() if value["decision"] == "reject")
        total = len(eligible)
        reviewed = allowed + rejected
        return Progress(
            total=total,
            reviewed=reviewed,
            remaining=max(total - reviewed, 0),
            allowed=allowed,
            rejected=rejected,
        )

    def export_overrides(self, export_path: Path, eligible_hashes: Iterable[str] | None = None) -> dict:
        eligible = set(eligible_hashes) if eligible_hashes is not None else None
        initial_decisions = self.active_decisions(STAGE_INITIAL)
        confirmation_decisions = self.active_decisions(STAGE_CONFIRM_ALLOWED)
        reconfirmation_decisions = self.active_decisions(STAGE_RECONFIRM_ALLOWED)
        if eligible is not None:
            initial_decisions = {key: value for key, value in initial_decisions.items() if key in eligible}
            confirmation_decisions = {key: value for key, value in confirmation_decisions.items() if key in eligible}
            reconfirmation_decisions = {key: value for key, value in reconfirmation_decisions.items() if key in eligible}
        items: dict[str, dict[str, str]] = {}
        for quote_hash, initial in sorted(initial_decisions.items()):
            final = reconfirmation_decisions.get(quote_hash) or confirmation_decisions.get(quote_hash) or initial
            exported = {
                "decision": final["decision"],
                "reviewed_at": final["reviewed_at"],
                "stage": final["stage"],
                "initial_decision": initial["decision"],
                "initial_reviewed_at": initial["reviewed_at"],
            }
            if quote_hash in confirmation_decisions:
                confirmation = confirmation_decisions[quote_hash]
                exported["confirmation_decision"] = confirmation["decision"]
                exported["confirmation_reviewed_at"] = confirmation["reviewed_at"]
            if quote_hash in reconfirmation_decisions:
                reconfirmation = reconfirmation_decisions[quote_hash]
                exported["reconfirmation_decision"] = reconfirmation["decision"]
                exported["reconfirmation_reviewed_at"] = reconfirmation["reviewed_at"]
            items[quote_hash] = exported
        payload = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "items": items,
        }
        atomic_write_json(Path(export_path), payload)
        return payload

    def _create_schema(self) -> None:
        with self._lock:
            with self.conn:
                self.conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS decision_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        quote_hash TEXT NOT NULL,
                        decision TEXT NOT NULL CHECK (decision IN ('allow', 'reject')),
                        reviewed_at TEXT NOT NULL,
                        item_order INTEGER NOT NULL,
                        stage TEXT NOT NULL DEFAULT 'initial',
                        undone_at TEXT
                    )
                    """
                )
                columns = {
                    row["name"]
                    for row in self.conn.execute("PRAGMA table_info(decision_history)").fetchall()
                }
                if "stage" not in columns:
                    self.conn.execute(
                        "ALTER TABLE decision_history ADD COLUMN stage TEXT NOT NULL DEFAULT 'initial'"
                    )
                self.conn.execute("DROP INDEX IF EXISTS decision_history_one_active")
                self.conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS decision_history_one_active_stage
                    ON decision_history(quote_hash, stage)
                    WHERE undone_at IS NULL
                    """
                )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        _fsync_parent(path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _fsync_parent(path: Path) -> None:
    try:
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
