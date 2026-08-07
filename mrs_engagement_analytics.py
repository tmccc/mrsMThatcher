#!/usr/bin/env python3
"""Read-only engagement analytics for quote posts and historical-context replies."""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import logging
import os
import re
import sqlite3
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence
from zoneinfo import ZoneInfo
from logging.handlers import RotatingFileHandler

import requests
from requests_oauthlib import OAuth1


SCHEMA_VERSION = 1
COLLECTOR_VERSION = "engagement-analytics-v1"
IDENTITY_CORRECTION_SCHEMA_VERSION = 1
SNAPSHOT_TARGETS = (3600, 6 * 3600, 24 * 3600, 72 * 3600, 168 * 3600)
TARGET_LABELS = {
    3600: "1h",
    6 * 3600: "6h",
    24 * 3600: "24h",
    72 * 3600: "72h",
    168 * 3600: "168h",
}
METRIC_FIELDS = (
    "impressions",
    "likes",
    "replies",
    "reposts",
    "quote_posts",
    "bookmarks",
    "url_link_clicks",
    "user_profile_clicks",
    "provider_total_engagements",
)
RATE_FIELDS = (
    "engagement_rate",
    "bookmark_rate",
    "reply_rate",
    "repost_rate",
    "quote_post_rate",
    "url_click_rate",
    "profile_click_rate",
)
IDENTITY_FIELDS = ("quote_id", "main_post_id", "context_post_id")
MIN_REPORT_SAMPLE = 5
X_SNOWFLAKE_EPOCH_MS = 1_288_834_974_657
LOG_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*? - EVENT (\{.*\})$")
POST_ID_RE = re.compile(r"\d{1,30}")
QUOTE_ID_RE = re.compile(r"[0-9a-f]{64}")
SECRET_KEY_RE = re.compile(r"authorization|api.?key|access.?token|secret|cookie", re.I)
SAFE_RESPONSE_HEADERS = (
    "content-type",
    "date",
    "retry-after",
    "x-rate-limit-limit",
    "x-rate-limit-remaining",
    "x-rate-limit-reset",
    "x-request-id",
    "x-transaction-id",
)
LOG = logging.getLogger("mrs_engagement_analytics")


class AnalyticsError(RuntimeError):
    """Collector validation or operational failure."""


class IdentityConflict(AnalyticsError):
    """Structured discovery sources disagree about a post identity."""


class CollectorAlreadyRunning(AnalyticsError):
    """A second collector process attempted to acquire the runtime lock."""


@dataclass(frozen=True)
class AnalyticsPaths:
    """Represent analytics paths data."""
    project_dir: Path
    runtime_dir: Path
    database: Path
    state: Path
    lock: Path
    log: Path
    raw_responses: Path
    exports: Path
    reports: Path
    config: Path
    identity_corrections: Path

    @classmethod
    def for_project(cls, project_dir: Path) -> "AnalyticsPaths":
        """Return the for project."""
        project = project_dir.expanduser().resolve()
        runtime = project / "engagement_analytics"
        return cls(
            project_dir=project,
            runtime_dir=runtime,
            database=runtime / "engagement_analytics.sqlite3",
            state=runtime / "collector_state.json",
            lock=runtime / ".collector.lock",
            log=runtime / "collector.log",
            raw_responses=runtime / "raw_responses",
            exports=runtime / "exports",
            reports=runtime / "reports",
            config=runtime / "config.json",
            identity_corrections=runtime / "quote_identity_corrections.json",
        )


@dataclass
class ReadResult:
    """Represent read result data."""
    status_code: int
    body: dict[str, Any]
    headers: dict[str, str]
    endpoint: str
    request_id: str | None
    elapsed_seconds: float


def utc_now() -> datetime:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    """Return the iso UTC."""
    value = value or utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    """Parse datetime."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is empty")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def quote_text_hash(text: str) -> str:
    """Return whether quote text hash."""
    normalised = re.sub(r"\s+", " ", str(text or "").strip())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def snowflake_datetime(post_id: str) -> datetime:
    """Return the snowflake datetime."""
    if not POST_ID_RE.fullmatch(str(post_id or "")):
        raise ValueError(f"invalid X post ID: {post_id!r}")
    milliseconds = (int(post_id) >> 22) + X_SNOWFLAKE_EPOCH_MS
    return datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Write bytes atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    """Write JSON atomically and optionally durably."""
    content = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_bytes(path, content)


def atomic_write_text(path: Path, value: str) -> None:
    """Write text atomically."""
    atomic_write_bytes(path, value.encode("utf-8"))


def _runtime_path(paths: AnalyticsPaths, path: Path) -> Path:
    resolved = path.resolve()
    runtime = paths.runtime_dir.resolve()
    if resolved != runtime and runtime not in resolved.parents:
        raise AnalyticsError(f"refusing analytics write outside runtime directory: {resolved}")
    return resolved


@contextmanager
def collector_lock(paths: AnalyticsPaths) -> Iterator[None]:
    """Yield collector lock values."""
    _runtime_path(paths, paths.lock)
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    handle = paths.lock.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CollectorAlreadyRunning(f"analytics collector lock is already held: {paths.lock}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} started_at={iso_utc()}\n")
        handle.flush()
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "x_api_base_url": "https://api.x.com",
    "request_timeout_seconds": 60,
    "maximum_batch_size": 100,
    "retry_backoff_seconds": 2,
    "default_max_api_requests": 2,
    "snapshot_target_ages_seconds": list(SNAPSHOT_TARGETS),
    "on_time_tolerance_seconds": 20 * 60,
    "report_minimum_sample": MIN_REPORT_SAMPLE,
}


def load_config(paths: AnalyticsPaths) -> dict[str, Any]:
    """Load config."""
    config = dict(DEFAULT_CONFIG)
    if paths.config.exists():
        loaded = json.loads(paths.config.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise AnalyticsError("engagement analytics config must be a JSON object")
        unknown = set(loaded) - set(DEFAULT_CONFIG)
        if unknown:
            raise AnalyticsError(f"unknown engagement analytics config fields: {sorted(unknown)}")
        config.update(loaded)
    if config.get("schema_version") != 1:
        raise AnalyticsError("unsupported engagement analytics config schema")
    batch = config.get("maximum_batch_size")
    if type(batch) is not int or not 1 <= batch <= 100:
        raise AnalyticsError("maximum_batch_size must be an integer from 1 to 100")
    timeout = config.get("request_timeout_seconds")
    if type(timeout) not in (int, float) or isinstance(timeout, bool) or not 1 <= float(timeout) <= 300:
        raise AnalyticsError("request_timeout_seconds must be from 1 to 300")
    retry_backoff = config.get("retry_backoff_seconds")
    if type(retry_backoff) not in (int, float) or isinstance(retry_backoff, bool) or not 0 <= float(retry_backoff) <= 300:
        raise AnalyticsError("retry_backoff_seconds must be from 0 to 300")
    default_requests = config.get("default_max_api_requests")
    if type(default_requests) is not int or not 1 <= default_requests <= 100:
        raise AnalyticsError("default_max_api_requests must be an integer from 1 to 100")
    tolerance = config.get("on_time_tolerance_seconds")
    if type(tolerance) is not int or not 0 <= tolerance <= 24 * 3600:
        raise AnalyticsError("on_time_tolerance_seconds must be an integer from 0 to 86400")
    minimum_sample = config.get("report_minimum_sample")
    if type(minimum_sample) is not int or not 1 <= minimum_sample <= 10_000:
        raise AnalyticsError("report_minimum_sample must be a positive integer")
    targets = config.get("snapshot_target_ages_seconds")
    if targets != list(SNAPSHOT_TARGETS):
        raise AnalyticsError(f"snapshot_target_ages_seconds must be exactly {list(SNAPSHOT_TARGETS)}")
    return config


def configure_logging(paths: AnalyticsPaths, *, file_logging: bool) -> None:
    """Configure logging."""
    LOG.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
    if not any(getattr(handler, "_mrs_engagement_console", False) for handler in LOG.handlers):
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        console._mrs_engagement_console = True  # type: ignore[attr-defined]
        LOG.addHandler(console)
    if file_logging and not any(getattr(handler, "_mrs_engagement_file", False) for handler in LOG.handlers):
        _runtime_path(paths, paths.log)
        paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(paths.log, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(formatter)
        handler._mrs_engagement_file = True  # type: ignore[attr-defined]
        LOG.addHandler(handler)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS post_pairs (
    pair_id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id TEXT NOT NULL,
    canonical_quote_hash TEXT NOT NULL,
    quote_text TEXT NOT NULL,
    main_post_id TEXT NOT NULL UNIQUE,
    main_posted_at TEXT NOT NULL,
    context_post_id TEXT UNIQUE,
    context_posted_at TEXT,
    context_missing_reason TEXT,
    formatter_version TEXT,
    verification_label TEXT,
    source_class TEXT,
    historical_confidence TEXT,
    context_weighted_character_count INTEGER,
    meaning_included INTEGER,
    shortening_applied INTEGER,
    image_source TEXT,
    image_filename TEXT,
    image_score REAL,
    made_with_ai INTEGER,
    quotation_topic TEXT,
    discovery_sources_json TEXT NOT NULL,
    first_discovered_at TEXT NOT NULL,
    last_discovered_at TEXT NOT NULL,
    CHECK (length(quote_id) = 64),
    CHECK (context_post_id IS NULL OR context_post_id <> main_post_id),
    CHECK (meaning_included IS NULL OR meaning_included IN (0, 1)),
    CHECK (shortening_applied IS NULL OR shortening_applied IN (0, 1)),
    CHECK (made_with_ai IS NULL OR made_with_ai IN (0, 1))
);

CREATE TABLE IF NOT EXISTS post_pair_revisions (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id INTEGER NOT NULL REFERENCES post_pairs(pair_id),
    revision_number INTEGER NOT NULL,
    changed_at TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    record_json TEXT NOT NULL,
    UNIQUE(pair_id, revision_number)
);

CREATE TABLE IF NOT EXISTS posts (
    post_id TEXT PRIMARY KEY,
    pair_id INTEGER NOT NULL REFERENCES post_pairs(pair_id),
    role TEXT NOT NULL CHECK (role IN ('main_quote', 'historical_context')),
    posted_at TEXT NOT NULL,
    terminal_state TEXT,
    UNIQUE(pair_id, role)
);

CREATE TABLE IF NOT EXISTS snapshot_schedule (
    post_id TEXT NOT NULL REFERENCES posts(post_id),
    target_age_seconds INTEGER NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'terminal')),
    completed_snapshot_id INTEGER,
    terminal_reason TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY(post_id, target_age_seconds)
);

CREATE TABLE IF NOT EXISTS metric_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT NOT NULL REFERENCES posts(post_id),
    role TEXT NOT NULL CHECK (role IN ('main_quote', 'historical_context')),
    target_age_seconds INTEGER NOT NULL,
    revision_number INTEGER NOT NULL DEFAULT 1,
    actual_age_seconds INTEGER NOT NULL,
    due_at TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    auth_class TEXT NOT NULL,
    impressions INTEGER,
    likes INTEGER,
    replies INTEGER,
    reposts INTEGER,
    quote_posts INTEGER,
    bookmarks INTEGER,
    url_link_clicks INTEGER,
    user_profile_clicks INTEGER,
    provider_total_engagements INTEGER,
    engagement_count INTEGER,
    engagement_rate REAL,
    bookmark_rate REAL,
    reply_rate REAL,
    repost_rate REAL,
    quote_post_rate REAL,
    url_click_rate REAL,
    profile_click_rate REAL,
    engagement_rate_basis TEXT,
    unavailable_fields_json TEXT NOT NULL,
    request_attempt_number INTEGER NOT NULL,
    response_status INTEGER NOT NULL,
    request_id TEXT,
    raw_response_path TEXT NOT NULL,
    raw_response_sha256 TEXT NOT NULL,
    terminal_state TEXT,
    supersedes_snapshot_id INTEGER REFERENCES metric_snapshots(snapshot_id),
    UNIQUE(post_id, target_age_seconds, revision_number)
);

CREATE TABLE IF NOT EXISTS collection_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_key TEXT NOT NULL,
    post_ids_json TEXT NOT NULL,
    request_attempt_number INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    elapsed_seconds REAL NOT NULL,
    endpoint TEXT NOT NULL,
    auth_class TEXT NOT NULL,
    outcome TEXT NOT NULL,
    response_status INTEGER,
    request_id TEXT,
    error_class TEXT,
    error_message TEXT,
    rate_limit_reset_at TEXT,
    retry_after_seconds INTEGER,
    raw_response_path TEXT,
    raw_response_sha256 TEXT
);

CREATE TABLE IF NOT EXISTS metric_snapshot_revision_audit (
    snapshot_id INTEGER PRIMARY KEY REFERENCES metric_snapshots(snapshot_id),
    superseded_snapshot_id INTEGER NOT NULL REFERENCES metric_snapshots(snapshot_id),
    revised_at TEXT NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_or_capability_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS metric_snapshots_no_update
BEFORE UPDATE ON metric_snapshots BEGIN
    SELECT RAISE(ABORT, 'metric snapshots are append-only');
END;

CREATE TRIGGER IF NOT EXISTS metric_snapshots_no_delete
BEFORE DELETE ON metric_snapshots BEGIN
    SELECT RAISE(ABORT, 'metric snapshots are append-only');
END;

CREATE TRIGGER IF NOT EXISTS collection_attempts_no_update
BEFORE UPDATE ON collection_attempts BEGIN
    SELECT RAISE(ABORT, 'collection attempts are append-only');
END;

CREATE TRIGGER IF NOT EXISTS collection_attempts_no_delete
BEFORE DELETE ON collection_attempts BEGIN
    SELECT RAISE(ABORT, 'collection attempts are append-only');
END;

CREATE TRIGGER IF NOT EXISTS metric_snapshot_revision_audit_no_update
BEFORE UPDATE ON metric_snapshot_revision_audit BEGIN
    SELECT RAISE(ABORT, 'metric snapshot revision audit is append-only');
END;

CREATE TRIGGER IF NOT EXISTS metric_snapshot_revision_audit_no_delete
BEFORE DELETE ON metric_snapshot_revision_audit BEGIN
    SELECT RAISE(ABORT, 'metric snapshot revision audit is append-only');
END;
"""


def connect_database(paths: AnalyticsPaths, *, readonly: bool = False, create: bool = False) -> sqlite3.Connection:
    """Open the isolated engagement SQLite database with required pragmas."""
    if readonly:
        if not paths.database.exists():
            raise FileNotFoundError(paths.database)
        connection = sqlite3.connect(f"file:{paths.database}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
    else:
        if not create and not paths.database.exists():
            raise AnalyticsError(f"analytics database is not initialised: {paths.database}")
        _runtime_path(paths, paths.database)
        paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(paths.database)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def initialise_database(paths: AnalyticsPaths) -> dict[str, Any]:
    """Create or migrate the engagement analytics schema."""
    for path in (paths.runtime_dir, paths.raw_responses, paths.exports, paths.reports):
        _runtime_path(paths, path)
        path.mkdir(parents=True, exist_ok=True)
    with connect_database(paths, create=True) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, iso_utc()),
        )
        connection.commit()
    state = {
        "schema_version": 1,
        "collector_version": COLLECTOR_VERSION,
        "database": str(paths.database),
        "initialised_at": iso_utc(),
        "cooldown_until": None,
        "cooldown_reason": None,
        "last_successful_collection": None,
    }
    if not paths.state.exists():
        atomic_write_json(paths.state, state)
        return state
    preserved = _json_file(paths.state, {})
    if not isinstance(preserved, dict):
        raise AnalyticsError("invalid engagement analytics collector state")
    return preserved


def state_value(connection: sqlite3.Connection, key: str, default: Any = None) -> Any:
    """Return the state value."""
    row = connection.execute(
        "SELECT value_json FROM account_or_capability_state WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value_json"])
    except (TypeError, json.JSONDecodeError):
        return default


def set_state_value(connection: sqlite3.Connection, key: str, value: Any, *, now: datetime | None = None) -> None:
    """Set state value."""
    connection.execute(
        """INSERT INTO account_or_capability_state(key, value_json, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
        (key, json.dumps(value, sort_keys=True), iso_utc(now)),
    )


def sync_state_file(paths: AnalyticsPaths, connection: sqlite3.Connection) -> None:
    """Synchronise state file."""
    existing = _json_file(paths.state, {})
    state = dict(existing) if isinstance(existing, dict) else {}
    state.update({
        "schema_version": 1,
        "collector_version": COLLECTOR_VERSION,
        "database": str(paths.database),
        "updated_at": iso_utc(),
        "cooldown_until": state_value(connection, "cooldown_until"),
        "cooldown_reason": state_value(connection, "cooldown_reason"),
        "last_successful_collection": state_value(connection, "last_successful_collection"),
        "metric_capabilities": state_value(connection, "metric_capabilities", {}),
    })
    state.setdefault("initialised_at", iso_utc())
    atomic_write_json(_runtime_path(paths, paths.state), state)


def _json_file(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _load_quote_identity_corrections(
    path: Path,
    packets: dict[str, dict[str, Any]],
    unresolved: set[str],
) -> dict[tuple[str, str, int, str, str], dict[str, Any]]:
    """Load narrowly scoped corrections for identities derived from mutable line numbers."""
    document = _json_file(path, {"schema_version": IDENTITY_CORRECTION_SCHEMA_VERSION, "corrections": []})
    if not isinstance(document, dict) or document.get("schema_version") != IDENTITY_CORRECTION_SCHEMA_VERSION:
        raise AnalyticsError(f"invalid quote identity correction schema: {path}")
    records = document.get("corrections")
    if not isinstance(records, list):
        raise AnalyticsError(f"invalid quote identity corrections: {path}")

    corrections: dict[tuple[str, str, int, str, str], dict[str, Any]] = {}
    correction_ids: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise AnalyticsError(f"invalid quote identity correction record: {path}")
        correction_id = str(record.get("correction_id") or "")
        post_id = str(record.get("post_id") or "")
        source = str(record.get("source") or "")
        line_no = record.get("historical_line_no")
        observed_id = str(record.get("observed_derived_quote_id") or "")
        canonical_id = str(record.get("canonical_quote_id") or "")
        observed_text = record.get("observed_derived_quote_text")
        canonical_text = record.get("canonical_quote_text")
        evidence = record.get("evidence")
        if not correction_id or correction_id in correction_ids:
            raise AnalyticsError(f"duplicate or missing quote identity correction ID: {correction_id!r}")
        if not POST_ID_RE.fullmatch(post_id) or source != "structured_log_main_post_event":
            raise AnalyticsError(f"invalid correction scope for {correction_id}")
        if type(line_no) is not int or line_no < 0:
            raise AnalyticsError(f"invalid historical line number for {correction_id}")
        if not QUOTE_ID_RE.fullmatch(observed_id) or not QUOTE_ID_RE.fullmatch(canonical_id):
            raise AnalyticsError(f"invalid quote identity in correction {correction_id}")
        if observed_id == canonical_id:
            raise AnalyticsError(f"redundant quote identity correction {correction_id}")
        if not isinstance(observed_text, str) or quote_text_hash(observed_text) != observed_id:
            raise AnalyticsError(f"observed text/hash mismatch in correction {correction_id}")
        if not isinstance(canonical_text, str) or quote_text_hash(canonical_text) != canonical_id:
            raise AnalyticsError(f"canonical text/hash mismatch in correction {correction_id}")
        packet = packets.get(canonical_id)
        if not isinstance(packet, dict) or packet.get("quote_text") != canonical_text or canonical_id in unresolved:
            raise AnalyticsError(f"correction {correction_id} does not resolve to an eligible canonical packet")
        if record.get("classification") != "incorrect_derived_analytics_record":
            raise AnalyticsError(f"unsupported correction classification for {correction_id}")
        if not isinstance(record.get("reason"), str) or not str(record["reason"]).strip():
            raise AnalyticsError(f"missing correction reason for {correction_id}")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(value, str) and value for value in evidence):
            raise AnalyticsError(f"missing correction evidence for {correction_id}")
        key = (post_id, source, line_no, observed_id, canonical_id)
        if key in corrections:
            raise AnalyticsError(f"duplicate quote identity correction scope for {correction_id}")
        correction_ids.add(correction_id)
        corrections[key] = record
    return corrections


def _structured_log_events(project_dir: Path) -> list[dict[str, Any]]:
    events: dict[tuple[Any, ...], dict[str, Any]] = {}
    london = ZoneInfo("Europe/London")
    candidates = [project_dir / "mrsMThatcher.log"]
    candidates.extend(sorted(project_dir.glob("mrsMThatcher.log.[0-9]*")))
    for path in candidates:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = LOG_TIMESTAMP_RE.match(line.rstrip("\n"))
                if not match:
                    continue
                try:
                    event = json.loads(match.group(2))
                    local_time = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=london)
                except (ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                event_name = str(event.get("event") or "")
                post_id = str(event.get("post_id") or event.get("parent_post_id") or "")
                if event_name in {"main_post_posted", "historical_context_reply"} and post_id:
                    try:
                        snowflake_time = snowflake_datetime(post_id)
                    except ValueError:
                        continue
                    # Reject copied test events and malformed records whose ID epoch does not
                    # correspond to the structured log timestamp.
                    if abs((snowflake_time - local_time.astimezone(timezone.utc)).total_seconds()) > 3600:
                        continue
                item = {**event, "_event_time": iso_utc(local_time), "_log_path": str(path)}
                key = (
                    event_name,
                    post_id,
                    str(event.get("quote_id") or ""),
                    str(event.get("status") or ""),
                    item["_event_time"],
                )
                events.setdefault(key, item)
    return sorted(events.values(), key=lambda item: (item["_event_time"], str(item.get("event") or "")))


def _merge_identity(candidate: dict[str, Any], field: str, value: Any, source: str) -> None:
    if value in (None, ""):
        return
    value = str(value)
    current = candidate.get(field)
    if current not in (None, "") and str(current) != value:
        raise IdentityConflict(
            f"{field} conflict for main post {candidate.get('main_post_id')}: {current!r} versus {value!r} from {source}"
        )
    candidate[field] = value
    if field == "quote_id":
        sources = candidate.setdefault("_quote_identity_sources", [])
        if source not in sources:
            sources.append(source)


def _fill(candidate: dict[str, Any], field: str, value: Any) -> None:
    if candidate.get(field) in (None, "", []):
        candidate[field] = value


def _topic_for_quote(quote_analysis: dict[str, Any], quote_id: str) -> str | None:
    row = (quote_analysis.get("items") or {}).get(quote_id)
    analysis = row.get("analysis") if isinstance(row, dict) else None
    topics = analysis.get("primary_topics") if isinstance(analysis, dict) else None
    if isinstance(topics, list) and topics and isinstance(topics[0], str):
        return topics[0]
    return None


def discover_post_pairs(
    paths: AnalyticsPaths,
    *,
    since_days: int = 14,
    now: datetime | None = None,
    max_pairs: int | None = None,
) -> list[dict[str, Any]]:
    """Build a deterministic, conflict-checked view without writing analytics state."""
    from historical_context_formatter import (
        HISTORICAL_CONTEXT_FORMATTER_V2,
        format_context_reply,
        format_context_reply_v2,
        load_and_validate_corpus,
    )

    now = (now or utc_now()).astimezone(timezone.utc)
    if type(since_days) is not int or since_days < 0:
        raise AnalyticsError("since_days must be a non-negative integer")
    cutoff = now - timedelta(days=since_days)
    packets, unresolved = load_and_validate_corpus(
        paths.project_dir / "semantic_alignment_research" / "quote_research_full_001"
    )
    identity_corrections = _load_quote_identity_corrections(paths.identity_corrections, packets, unresolved)
    quote_analysis = _json_file(paths.project_dir / "quote_analysis.json", {})
    lines = (paths.project_dir / "mrsMThatcher.txt").read_text(encoding="utf-8").splitlines()
    history = _json_file(paths.project_dir / "historical_context_reply_history.json", {"items": {}})
    if not isinstance(history, dict) or not isinstance(history.get("items"), dict):
        raise AnalyticsError("invalid historical context reply history")
    events = _structured_log_events(paths.project_dir)
    candidates: dict[str, dict[str, Any]] = {}

    def candidate_for(main_post_id: str, source: str) -> dict[str, Any]:
        if not POST_ID_RE.fullmatch(main_post_id):
            raise IdentityConflict(f"invalid main post ID from {source}: {main_post_id!r}")
        item = candidates.setdefault(
            main_post_id,
            {
                "main_post_id": main_post_id,
                "main_posted_at": iso_utc(snowflake_datetime(main_post_id)),
                "discovery_sources": [],
            },
        )
        if source not in item["discovery_sources"]:
            item["discovery_sources"].append(source)
        return item

    def apply_formatter_metadata(item: dict[str, Any], record: dict[str, Any]) -> None:
        metadata = record.get("formatter_metadata")
        if not isinstance(metadata, dict):
            return
        for target, source in (
            ("formatter_version", "formatter_version"),
            ("context_weighted_character_count", "weighted_character_count"),
            ("verification_label", "verification_label"),
            ("source_class", "source_class"),
            ("historical_confidence", "historical_confidence"),
            ("meaning_included", "meaning_included"),
            ("shortening_applied", "shortening_applied"),
        ):
            _fill(item, target, metadata.get(source))

    # Highest-precedence durable source: context-reply history.
    for main_post_id, record in sorted(history["items"].items()):
        if not isinstance(record, dict):
            raise AnalyticsError(f"invalid context history item: {main_post_id}")
        item = candidate_for(str(main_post_id), "historical_context_reply_history")
        _merge_identity(item, "quote_id", record.get("quote_id"), "historical_context_reply_history")
        apply_formatter_metadata(item, record)
        if record.get("status") == "completed":
            _merge_identity(item, "context_post_id", record.get("reply_post_id"), "historical_context_reply_history")
            _fill(item, "context_posted_at", iso_utc(snowflake_datetime(str(record["reply_post_id"]))))
            item["context_history_status"] = "completed"
        elif record.get("status") == "failed":
            item["context_history_status"] = "failed"
            item["context_missing_reason"] = "context_reply_failed"
        else:
            raise AnalyticsError(f"unsupported context history status: {record.get('status')!r}")

    # The durable analytics ledger preserves explicit identities discovered before
    # mutable source-line positions changed. Read it without creating or updating
    # the database, and retain only identities still present in the completed
    # canonical research corpus.
    if paths.database.is_file():
        try:
            ledger = sqlite3.connect(f"file:{paths.database}?mode=ro", uri=True)
            ledger.row_factory = sqlite3.Row
            stored_pairs = ledger.execute(
                "SELECT * FROM post_pairs ORDER BY main_posted_at, main_post_id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise AnalyticsError(f"cannot read engagement post-pair ledger: {exc}") from exc
        finally:
            if "ledger" in locals():
                ledger.close()
        for record in stored_pairs:
            main_post_id = str(record["main_post_id"] or "")
            try:
                main_time = snowflake_datetime(main_post_id)
            except ValueError as exc:
                raise IdentityConflict(f"invalid main post ID in engagement post-pair ledger: {main_post_id!r}") from exc
            if main_time < cutoff:
                continue
            quote_id = str(record["quote_id"] or "")
            if quote_id not in packets:
                continue
            quote_text = str(record["quote_text"] or "")
            # Stored post-pair rows are bound to canonical corpus IDs, which
            # are raw UTF-8 SHA-256 identities and can preserve repeated
            # whitespace.
            if hashlib.sha256(quote_text.encode("utf-8")).hexdigest() != quote_id:
                raise IdentityConflict(f"stored quote text conflicts for main post {main_post_id}")
            item = candidate_for(main_post_id, "engagement_post_pair_ledger")
            _merge_identity(item, "quote_id", quote_id, "engagement_post_pair_ledger")
            _fill(item, "quote_text", quote_text)
            _merge_identity(item, "context_post_id", record["context_post_id"], "engagement_post_pair_ledger")
            for field in (
                "context_posted_at",
                "context_missing_reason",
                "formatter_version",
                "verification_label",
                "source_class",
                "historical_confidence",
                "context_weighted_character_count",
                "meaning_included",
                "shortening_applied",
                "image_source",
                "image_filename",
                "image_score",
                "made_with_ai",
                "quotation_topic",
            ):
                _fill(item, field, record[field])

    # Confirmed/sending context receipt, if one exists, is retained as a secondary source.
    receipt_path = paths.project_dir / "historical_context_reply_receipt.json"
    if receipt_path.exists():
        receipt = _json_file(receipt_path, {})
        if not isinstance(receipt, dict):
            raise AnalyticsError("invalid context reply receipt")
        main_post_id = str(receipt.get("parent_post_id") or "")
        item = candidate_for(main_post_id, "historical_context_reply_receipt")
        _merge_identity(item, "quote_id", receipt.get("quote_id"), "historical_context_reply_receipt")
        apply_formatter_metadata(item, receipt)
        if receipt.get("reply_post_id"):
            _merge_identity(item, "context_post_id", receipt.get("reply_post_id"), "historical_context_reply_receipt")

    # A regular-post receipt is transient but authoritative when present.
    regular_receipt_path = paths.project_dir / "regular_post_receipt.json"
    if regular_receipt_path.exists():
        receipt = _json_file(regular_receipt_path, {})
        if not isinstance(receipt, dict):
            raise AnalyticsError("invalid regular post receipt")
        main_post_id = str(receipt.get("post_id") or "")
        item = candidate_for(main_post_id, "regular_post_receipt")
        _merge_identity(item, "quote_id", receipt.get("quote_hash"), "regular_post_receipt")
        _fill(item, "image_filename", receipt.get("image_basename"))

    # Structured state cache is used only to enrich identities and text.
    state = _json_file(paths.project_dir / "bot_state.json", {})
    cache = state.get("tweet_cache") if isinstance(state, dict) else None
    if isinstance(cache, dict):
        for post_id, record in cache.items():
            if not isinstance(record, dict) or record.get("post_type") != "quote":
                continue
            try:
                post_time = snowflake_datetime(str(post_id))
            except ValueError:
                continue
            if post_time < cutoff:
                continue
            item = candidate_for(str(post_id), "bot_state_tweet_cache")
            text = str(record.get("text") or "")
            candidate_id = quote_text_hash(text)
            if candidate_id in packets or candidate_id in unresolved:
                _merge_identity(item, "quote_id", candidate_id, "bot_state_tweet_cache")
                _fill(item, "quote_text", text)

    context_events: dict[str, dict[str, Any]] = {}
    ignored_line_identities: list[tuple[str, str, str]] = []
    for event in events:
        if event.get("event") == "historical_context_reply":
            main_post_id = str(event.get("parent_post_id") or "")
            if not main_post_id:
                continue
            item = candidate_for(main_post_id, "structured_log_context_event")
            _merge_identity(item, "quote_id", event.get("quote_id"), "structured_log_context_event")
            context_events[main_post_id] = event
        if event.get("event") != "main_post_posted" or event.get("lane") != "quote_image":
            continue
        main_post_id = str(event.get("post_id") or "")
        item = candidate_for(main_post_id, "structured_log_main_post_event")
        line_no = event.get("line_no")
        if type(line_no) is int and 0 <= line_no < len(lines):
            quote_text = lines[line_no].rstrip()
            derived_quote_id = quote_text_hash(quote_text)
            canonical_quote_id = str(item.get("quote_id") or "")
            correction_key = (
                main_post_id,
                "structured_log_main_post_event",
                line_no,
                derived_quote_id,
                canonical_quote_id,
            )
            correction = identity_corrections.get(correction_key)
            scoped_correction = next(
                (
                    record
                    for key, record in identity_corrections.items()
                    if key[0] == main_post_id
                    and key[1] == "structured_log_main_post_event"
                    and key[2] == line_no
                    and key[4] == canonical_quote_id
                ),
                None,
            )
            if scoped_correction is not None and correction is None:
                raise IdentityConflict(
                    f"registered identity correction evidence mismatch for main post {main_post_id}"
                )
            if correction:
                correction_id = str(correction["correction_id"])
                if quote_text != correction["observed_derived_quote_text"]:
                    raise IdentityConflict(f"identity correction text no longer matches for main post {main_post_id}")
                source = f"quote_identity_correction:{correction_id}"
                if source not in item["discovery_sources"]:
                    item["discovery_sources"].append(source)
                LOG.warning(
                    "Applied audited quote identity correction post_id=%s canonical_quote_id=%s "
                    "ignored_derived_quote_id=%s source=structured_log_main_post_event correction_id=%s",
                    main_post_id,
                    canonical_quote_id,
                    derived_quote_id,
                    correction_id,
                )
            elif canonical_quote_id and canonical_quote_id != derived_quote_id:
                # line_no identifies a position in the source file used when the
                # post was created. It is not a durable quote identity after an
                # audited corpus edit shifts retained records. Explicit IDs from
                # receipts, durable context history, exact cached text or context
                # events have already been conflict-checked and take precedence.
                source = "stale_main_post_line_number_ignored"
                if source not in item["discovery_sources"]:
                    item["discovery_sources"].append(source)
                ignored_line_identities.append((main_post_id, canonical_quote_id, derived_quote_id))
            else:
                _merge_identity(item, "quote_id", derived_quote_id, "structured_log_main_post_event")
                _fill(item, "quote_text", quote_text)
        _fill(item, "image_filename", event.get("image_basename"))
        _fill(item, "image_score", event.get("image_score"))

    if ignored_line_identities:
        LOG.info(
            "Ignored %d non-authoritative line-derived quote identities after explicit identity resolution; "
            "post_ids=%s",
            len(ignored_line_identities),
            ",".join(post_id for post_id, _canonical, _derived in ignored_line_identities),
        )

    context_to_main: dict[str, str] = {}
    earliest_context_time = min(
        (snowflake_datetime(item["main_post_id"]) for item in candidates.values() if item.get("context_post_id")),
        default=None,
    )
    output: list[dict[str, Any]] = []
    for main_post_id, item in candidates.items():
        main_time = snowflake_datetime(main_post_id)
        if main_time < cutoff:
            continue
        quote_id = str(item.get("quote_id") or "")
        if not QUOTE_ID_RE.fullmatch(quote_id):
            # A structured post without a resolvable canonical quote identity is unsafe to track.
            continue
        packet = packets.get(quote_id)
        quote_text = packet.get("quote_text") if isinstance(packet, dict) else item.get("quote_text")
        if not isinstance(quote_text, str):
            raise IdentityConflict(f"canonical quote identity mismatch for main post {main_post_id}")

        # Canonical research-packet IDs are raw UTF-8 SHA-256 identities.
        # Fallback text discovered outside the canonical packet corpus retains
        # the bot's whitespace-normalised identity rule.
        resolved_quote_id = (
            hashlib.sha256(quote_text.encode("utf-8")).hexdigest()
            if isinstance(packet, dict)
            else quote_text_hash(quote_text)
        )
        if resolved_quote_id != quote_id:
            raise IdentityConflict(f"canonical quote identity mismatch for main post {main_post_id}")
        context_post_id = item.get("context_post_id")
        if context_post_id:
            _fill(item, "context_posted_at", iso_utc(snowflake_datetime(str(context_post_id))))
            previous_main = context_to_main.setdefault(str(context_post_id), main_post_id)
            if previous_main != main_post_id:
                raise IdentityConflict(
                    f"context post {context_post_id} linked to both {previous_main} and {main_post_id}"
                )
        event = context_events.get(main_post_id, {})
        _fill(item, "formatter_version", event.get("formatter_version"))
        formatter_version = item.get("formatter_version") or "historical_context_reply_schema_v1"
        renderer = format_context_reply_v2 if formatter_version == HISTORICAL_CONTEXT_FORMATTER_V2 else format_context_reply
        formatted = renderer(packet) if packet and context_post_id else None
        for field, event_key in (
            ("context_weighted_character_count", "character_count"),
            ("verification_label", "verification_label"),
            ("source_class", "source_class"),
            ("historical_confidence", "historical_confidence"),
            ("shortening_applied", "shortening_applied"),
        ):
            _fill(item, field, event.get(event_key))
        if formatted:
            _fill(item, "context_weighted_character_count", formatted.get("character_count"))
            _fill(item, "verification_label", formatted.get("verification_label"))
            _fill(item, "source_class", formatted.get("source_class"))
            _fill(item, "historical_confidence", formatted.get("historical_confidence"))
            _fill(item, "shortening_applied", formatted.get("shortening_applied"))
            _fill(item, "meaning_included", formatted.get("meaning_included"))
            _fill(item, "formatter_version", formatter_version)
        if packet:
            _fill(item, "historical_confidence", packet.get("research_confidence"))
        image_filename = str(item.get("image_filename") or "")
        image_source = "generated" if image_filename.startswith("tg_") else "original" if image_filename else None
        _fill(item, "image_source", image_source)
        _fill(item, "made_with_ai", image_source == "generated" if image_source else None)
        _fill(item, "quotation_topic", _topic_for_quote(quote_analysis, quote_id))
        if not context_post_id and not item.get("context_missing_reason"):
            if event.get("status", "").startswith("skipped"):
                item["context_missing_reason"] = str(event.get("reason") or event.get("status"))
            elif earliest_context_time and main_time < earliest_context_time:
                item["context_missing_reason"] = "before_context_reply_feature"
            else:
                item["context_missing_reason"] = "context_reply_not_recorded"
        output.append({
            "quote_id": quote_id,
            "canonical_quote_hash": quote_id,
            "quote_text": quote_text,
            "main_post_id": main_post_id,
            "main_posted_at": iso_utc(main_time),
            "context_post_id": str(context_post_id) if context_post_id else None,
            "context_posted_at": item.get("context_posted_at"),
            "context_missing_reason": item.get("context_missing_reason"),
            "formatter_version": item.get("formatter_version"),
            "verification_label": item.get("verification_label"),
            "source_class": item.get("source_class"),
            "historical_confidence": item.get("historical_confidence"),
            "context_weighted_character_count": item.get("context_weighted_character_count"),
            "meaning_included": item.get("meaning_included"),
            "shortening_applied": item.get("shortening_applied"),
            "image_source": item.get("image_source"),
            "image_filename": item.get("image_filename"),
            "image_score": item.get("image_score"),
            "made_with_ai": item.get("made_with_ai"),
            "quotation_topic": item.get("quotation_topic"),
            "discovery_sources": sorted(item.get("discovery_sources") or []),
        })
    output.sort(key=lambda row: (row["main_posted_at"], row["main_post_id"]))
    if max_pairs is not None:
        if type(max_pairs) is not int or max_pairs < 1:
            raise AnalyticsError("max_pairs must be a positive integer")
        output = output[-max_pairs:]
    return output


PAIR_COLUMNS = (
    "quote_id", "canonical_quote_hash", "quote_text", "main_post_id", "main_posted_at",
    "context_post_id", "context_posted_at", "context_missing_reason", "formatter_version",
    "verification_label", "source_class", "historical_confidence",
    "context_weighted_character_count", "meaning_included", "shortening_applied",
    "image_source", "image_filename", "image_score", "made_with_ai", "quotation_topic",
)


def _db_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return value


def _pair_record(connection: sqlite3.Connection, pair_id: int) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM post_pairs WHERE pair_id = ?", (pair_id,)).fetchone()
    return dict(row) if row else {}


def apply_discovery(connection: sqlite3.Connection, pairs: Sequence[dict[str, Any]], *, now: datetime | None = None) -> dict[str, int]:
    """Merge discovered post identities while preserving conflict detection."""
    now_text = iso_utc(now)
    inserted = updated = unchanged = 0
    with connection:
        for pair in pairs:
            existing = connection.execute(
                "SELECT * FROM post_pairs WHERE main_post_id = ?", (pair["main_post_id"],)
            ).fetchone()
            if existing:
                for field in ("quote_id", "canonical_quote_hash"):
                    if str(existing[field]) != str(pair[field]):
                        raise IdentityConflict(f"stored {field} conflicts for main post {pair['main_post_id']}")
                if existing["context_post_id"] and pair.get("context_post_id") and existing["context_post_id"] != pair["context_post_id"]:
                    raise IdentityConflict(f"stored context post conflicts for main post {pair['main_post_id']}")
                changes: dict[str, Any] = {}
                for field in PAIR_COLUMNS:
                    new = _db_value(pair.get(field))
                    old = existing[field]
                    if old in (None, "") and new not in (None, ""):
                        changes[field] = new
                    elif field in {"discovery_sources"}:
                        pass
                if pair.get("context_post_id") and pair.get("context_missing_reason") in (None, ""):
                    # A later structured receipt/history entry resolves the earlier
                    # explicitly-missing state. Keep the old state in the revision
                    # history, but do not retain it on the live pair record.
                    if existing["context_missing_reason"] not in (None, ""):
                        changes["context_missing_reason"] = None
                merged_sources = sorted(set(json.loads(existing["discovery_sources_json"])) | set(pair["discovery_sources"]))
                if merged_sources != json.loads(existing["discovery_sources_json"]):
                    changes["discovery_sources_json"] = json.dumps(merged_sources, sort_keys=True)
                if changes:
                    assignments = ", ".join(f"{field} = ?" for field in changes)
                    connection.execute(
                        f"UPDATE post_pairs SET {assignments}, last_discovered_at = ? WHERE pair_id = ?",
                        (*changes.values(), now_text, existing["pair_id"]),
                    )
                    revision = connection.execute(
                        "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM post_pair_revisions WHERE pair_id = ?",
                        (existing["pair_id"],),
                    ).fetchone()[0]
                    record = _pair_record(connection, existing["pair_id"])
                    connection.execute(
                        "INSERT INTO post_pair_revisions(pair_id, revision_number, changed_at, change_reason, record_json) VALUES (?, ?, ?, ?, ?)",
                        (existing["pair_id"], revision, now_text, "structured discovery enrichment", json.dumps(record, sort_keys=True)),
                    )
                    updated += 1
                else:
                    connection.execute(
                        "UPDATE post_pairs SET last_discovered_at = ? WHERE pair_id = ?",
                        (now_text, existing["pair_id"]),
                    )
                    unchanged += 1
                pair_id = existing["pair_id"]
            else:
                values = [_db_value(pair.get(field)) for field in PAIR_COLUMNS]
                columns = ", ".join(PAIR_COLUMNS)
                placeholders = ", ".join("?" for _ in PAIR_COLUMNS)
                cursor = connection.execute(
                    f"INSERT INTO post_pairs({columns}, discovery_sources_json, first_discovered_at, last_discovered_at) "
                    f"VALUES ({placeholders}, ?, ?, ?)",
                    (*values, json.dumps(pair["discovery_sources"], sort_keys=True), now_text, now_text),
                )
                pair_id = int(cursor.lastrowid)
                record = _pair_record(connection, pair_id)
                connection.execute(
                    "INSERT INTO post_pair_revisions(pair_id, revision_number, changed_at, change_reason, record_json) VALUES (?, 1, ?, ?, ?)",
                    (pair_id, now_text, "initial structured discovery", json.dumps(record, sort_keys=True)),
                )
                inserted += 1

            posts = [(pair["main_post_id"], "main_quote", pair["main_posted_at"])]
            if pair.get("context_post_id"):
                posts.append((pair["context_post_id"], "historical_context", pair["context_posted_at"]))
            for post_id, role, posted_at in posts:
                existing_post = connection.execute("SELECT pair_id, role, posted_at FROM posts WHERE post_id = ?", (post_id,)).fetchone()
                if existing_post and (existing_post["pair_id"] != pair_id or existing_post["role"] != role):
                    raise IdentityConflict(f"post {post_id} is linked to contradictory pair or role")
                connection.execute(
                    "INSERT OR IGNORE INTO posts(post_id, pair_id, role, posted_at) VALUES (?, ?, ?, ?)",
                    (post_id, pair_id, role, posted_at),
                )
                posted = parse_datetime(posted_at)
                for target in SNAPSHOT_TARGETS:
                    due = iso_utc(posted + timedelta(seconds=target))
                    connection.execute(
                        "INSERT OR IGNORE INTO snapshot_schedule(post_id, target_age_seconds, due_at, created_at) VALUES (?, ?, ?, ?)",
                        (post_id, target, due, now_text),
                    )
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged, "total_input": len(pairs)}


def _safe_header_map(headers: Any) -> dict[str, str]:
    return {
        key: str(headers.get(key))
        for key in SAFE_RESPONSE_HEADERS
        if headers.get(key) not in (None, "")
    }


def sanitise_response(value: Any) -> Any:
    """Sanitise response."""
    if isinstance(value, dict):
        return {
            str(key): ("[REDACTED]" if SECRET_KEY_RE.search(str(key)) else sanitise_response(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitise_response(item) for item in value]
    return value


class XReadClient:
    """Narrow X API client that exposes one GET-only owned-post lookup operation."""

    endpoint_path = "/2/tweets"
    auth_class = "oauth1_user_context"

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        session: Any = requests,
        env: dict[str, str] | None = None,
    ) -> None:
        """Initialise the x read client."""
        env = env or os.environ
        credentials = (
            env.get("X_CONSUMER_KEY", ""),
            env.get("X_CONSUMER_SECRET", ""),
            env.get("X_ACCESS_TOKEN", ""),
            env.get("X_ACCESS_SECRET", ""),
        )
        if not all(credentials):
            raise AnalyticsError("X OAuth1 credentials are required for engagement metric reads")
        self.base_url = str(base_url).rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.session = session
        self.auth = OAuth1(*credentials)

    def lookup_posts(self, post_ids: Sequence[str]) -> ReadResult:
        """Return the lookup posts."""
        ids = [str(post_id) for post_id in post_ids]
        if not ids or len(ids) > 100 or any(not POST_ID_RE.fullmatch(post_id) for post_id in ids):
            raise AnalyticsError("lookup_posts requires from 1 to 100 valid post IDs")
        started = time.monotonic()
        response = self.session.get(
            f"{self.base_url}{self.endpoint_path}",
            params={
                "ids": ",".join(ids),
                "tweet.fields": "created_at,public_metrics,non_public_metrics,organic_metrics",
            },
            auth=self.auth,
            timeout=self.timeout_seconds,
        )
        elapsed = time.monotonic() - started
        try:
            body = response.json() if response.content else {}
        except (ValueError, json.JSONDecodeError):
            body = {"_non_json_response": str(response.text or "")[:2000]}
        if not isinstance(body, dict):
            body = {"_malformed_response_type": type(body).__name__}
        headers = _safe_header_map(response.headers)
        return ReadResult(
            status_code=int(response.status_code),
            body=body,
            headers=headers,
            endpoint=self.endpoint_path,
            request_id=headers.get("x-request-id") or headers.get("x-transaction-id"),
            elapsed_seconds=elapsed,
        )


def write_raw_response(
    paths: AnalyticsPaths,
    result: ReadResult,
    post_ids: Sequence[str],
    request_attempt_number: int,
    *,
    collected_at: datetime,
) -> tuple[str, str]:
    """Write raw response."""
    record = sanitise_response({
        "schema_version": 1,
        "collector_version": COLLECTOR_VERSION,
        "request": {
            "operation": "owned_post_metrics_lookup",
            "method": "GET",
            "endpoint": result.endpoint,
            "post_ids": list(post_ids),
            "attempt_number": request_attempt_number,
        },
        "response": {
            "status_code": result.status_code,
            "request_id": result.request_id,
            "headers": result.headers,
            "body": result.body,
        },
        "collected_at": iso_utc(collected_at),
    })
    content = (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    name = f"{collected_at.strftime('%Y%m%dT%H%M%SZ')}_{digest[:16]}.json"
    path = _runtime_path(paths, paths.raw_responses / name)
    atomic_write_bytes(path, content)
    return str(path.relative_to(paths.runtime_dir)), digest


def _metric_integer(value: Any) -> int | None:
    if type(value) is int and value >= 0:
        return value
    return None


def extract_metrics(post: dict[str, Any]) -> tuple[dict[str, int | None], dict[str, str], dict[str, Any]]:
    """Extract metrics."""
    public = post.get("public_metrics") if isinstance(post.get("public_metrics"), dict) else {}
    non_public = post.get("non_public_metrics") if isinstance(post.get("non_public_metrics"), dict) else {}
    organic = post.get("organic_metrics") if isinstance(post.get("organic_metrics"), dict) else {}

    def first(*values: Any) -> int | None:
        for value in values:
            parsed = _metric_integer(value)
            if parsed is not None:
                return parsed
        return None

    metrics: dict[str, int | None] = {
        "impressions": first(non_public.get("impression_count"), organic.get("impression_count"), public.get("impression_count")),
        "likes": first(public.get("like_count"), organic.get("like_count")),
        "replies": first(public.get("reply_count"), organic.get("reply_count")),
        "reposts": first(public.get("retweet_count"), organic.get("retweet_count")),
        "quote_posts": first(public.get("quote_count"), organic.get("quote_count")),
        "bookmarks": first(public.get("bookmark_count"), non_public.get("bookmark_count"), organic.get("bookmark_count")),
        "url_link_clicks": first(non_public.get("url_link_clicks"), organic.get("url_link_clicks")),
        "user_profile_clicks": first(non_public.get("user_profile_clicks"), organic.get("user_profile_clicks")),
        "provider_total_engagements": first(
            non_public.get("engagements"), organic.get("engagements"),
            non_public.get("engagement_count"), organic.get("engagement_count"), public.get("engagement_count"),
            non_public.get("total_engagements"), organic.get("total_engagements"),
        ),
    }
    unavailable = {
        field: "not_returned_by_x_api_for_current_auth_or_post_age"
        for field, value in metrics.items()
        if value is None
    }
    derived = derive_metrics(metrics)
    return metrics, unavailable, derived


def _rate(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def derive_metrics(metrics: dict[str, int | None]) -> dict[str, Any]:
    """Derive metrics."""
    impressions = metrics.get("impressions")
    engagement_count = metrics.get("provider_total_engagements")
    basis = "provider_total" if engagement_count is not None else None
    public_components = [metrics.get(field) for field in ("likes", "replies", "reposts", "quote_posts", "bookmarks")]
    if engagement_count is None and all(value is not None for value in public_components):
        engagement_count = sum(int(value) for value in public_components if value is not None)
        basis = "sum_public_interactions"
    return {
        "engagement_count": engagement_count,
        "engagement_rate": _rate(engagement_count, impressions),
        "bookmark_rate": _rate(metrics.get("bookmarks"), impressions),
        "reply_rate": _rate(metrics.get("replies"), impressions),
        "repost_rate": _rate(metrics.get("reposts"), impressions),
        "quote_post_rate": _rate(metrics.get("quote_posts"), impressions),
        "url_click_rate": _rate(metrics.get("url_link_clicks"), impressions),
        "profile_click_rate": _rate(metrics.get("user_profile_clicks"), impressions),
        "engagement_rate_basis": basis,
    }


def revise_snapshots_from_preserved_raw(
    paths: AnalyticsPaths,
    connection: sqlite3.Connection,
    *,
    reason: str,
    now: datetime | None = None,
) -> dict[str, int]:
    """Append corrected metric revisions from already-sanitised raw responses."""
    if not str(reason or "").strip():
        raise AnalyticsError("snapshot revision reason is required")
    revised_at = iso_utc(now)
    raw_records = connection.execute(
        """SELECT DISTINCT raw_response_path, raw_response_sha256
             FROM metric_snapshots
            WHERE terminal_state IS NULL
            ORDER BY raw_response_path"""
    ).fetchall()
    raw_files = revisions = unchanged = 0
    available_posts: dict[str, set[str]] = {field: set() for field in METRIC_FIELDS}
    with connection:
        for raw_record in raw_records:
            relative = Path(raw_record["raw_response_path"])
            raw_path = _runtime_path(paths, paths.runtime_dir / relative)
            try:
                content = raw_path.read_bytes()
            except FileNotFoundError as exc:
                raise AnalyticsError(f"preserved raw response is missing: {relative}") from exc
            if hashlib.sha256(content).hexdigest() != raw_record["raw_response_sha256"]:
                raise AnalyticsError(f"preserved raw response hash mismatch: {relative}")
            try:
                record = json.loads(content)
            except json.JSONDecodeError as exc:
                raise AnalyticsError(f"preserved raw response is not valid JSON: {relative}") from exc
            body = ((record.get("response") or {}).get("body") or {}) if isinstance(record, dict) else {}
            data = body.get("data") if isinstance(body, dict) else None
            posts = {
                str(item.get("id")): item
                for item in data
                if isinstance(item, dict) and POST_ID_RE.fullmatch(str(item.get("id") or ""))
            } if isinstance(data, list) else {}
            raw_files += 1
            latest_rows = connection.execute(
                """SELECT snapshot.*
                     FROM metric_snapshots snapshot
                    WHERE snapshot.raw_response_sha256 = ?
                      AND snapshot.terminal_state IS NULL
                      AND snapshot.revision_number = (
                          SELECT MAX(candidate.revision_number)
                            FROM metric_snapshots candidate
                           WHERE candidate.post_id = snapshot.post_id
                             AND candidate.target_age_seconds = snapshot.target_age_seconds
                      )
                    ORDER BY snapshot.snapshot_id""",
                (raw_record["raw_response_sha256"],),
            ).fetchall()
            for row in latest_rows:
                post = posts.get(row["post_id"])
                if post is None:
                    unchanged += 1
                    continue
                metrics, unavailable, derived = extract_metrics(post)
                for field, value in metrics.items():
                    if value is not None:
                        available_posts[field].add(row["post_id"])
                expected = {
                    **metrics,
                    "engagement_count": derived.get("engagement_count"),
                    **{field: derived.get(field) for field in RATE_FIELDS},
                    "engagement_rate_basis": derived.get("engagement_rate_basis"),
                    "unavailable_fields_json": json.dumps(unavailable, sort_keys=True),
                }
                if all(row[field] == value for field, value in expected.items()):
                    unchanged += 1
                    continue
                columns = [
                    "post_id", "role", "target_age_seconds", "revision_number",
                    "actual_age_seconds", "due_at", "collected_at", "endpoint", "auth_class",
                    *METRIC_FIELDS, "engagement_count", *RATE_FIELDS, "engagement_rate_basis",
                    "unavailable_fields_json", "request_attempt_number", "response_status",
                    "request_id", "raw_response_path", "raw_response_sha256", "terminal_state",
                    "supersedes_snapshot_id",
                ]
                values = [
                    row["post_id"], row["role"], row["target_age_seconds"], row["revision_number"] + 1,
                    row["actual_age_seconds"], row["due_at"], row["collected_at"], row["endpoint"], row["auth_class"],
                    *(metrics.get(field) for field in METRIC_FIELDS), derived.get("engagement_count"),
                    *(derived.get(field) for field in RATE_FIELDS), derived.get("engagement_rate_basis"),
                    json.dumps(unavailable, sort_keys=True), row["request_attempt_number"], row["response_status"],
                    row["request_id"], row["raw_response_path"], row["raw_response_sha256"],
                    row["terminal_state"], row["snapshot_id"],
                ]
                cursor = connection.execute(
                    f"INSERT INTO metric_snapshots({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    values,
                )
                revision_id = int(cursor.lastrowid)
                connection.execute(
                    """INSERT INTO metric_snapshot_revision_audit(
                           snapshot_id, superseded_snapshot_id, revised_at, reason
                       ) VALUES (?, ?, ?, ?)""",
                    (revision_id, row["snapshot_id"], revised_at, reason),
                )
                connection.execute(
                    """UPDATE snapshot_schedule SET completed_snapshot_id = ?
                        WHERE post_id = ? AND target_age_seconds = ?""",
                    (revision_id, row["post_id"], row["target_age_seconds"]),
                )
                revisions += 1
        capabilities = state_value(connection, "metric_capabilities", {})
        if isinstance(capabilities, dict):
            counts = capabilities.get("available_counts")
            if not isinstance(counts, dict):
                counts = {}
            for field, post_ids in available_posts.items():
                counts[field] = max(int(counts.get(field, 0) or 0), len(post_ids))
            capabilities["available_counts"] = counts
            capabilities["adapter_revision_observed_at"] = revised_at
            set_state_value(connection, "metric_capabilities", capabilities, now=now)
    sync_state_file(paths, connection)
    return {"raw_files": raw_files, "revisions_appended": revisions, "unchanged_snapshots": unchanged}


def due_snapshot_plan(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
    max_pairs: int | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return the due snapshot plan."""
    now = (now or utc_now()).astimezone(timezone.utc)
    clauses = ["s.status = 'pending'", "s.due_at <= ?"]
    params: list[Any] = [iso_utc(now)]
    if from_date:
        clauses.append("p.main_posted_at >= ?")
        params.append(iso_utc(from_date))
    if to_date:
        clauses.append("p.main_posted_at <= ?")
        params.append(iso_utc(to_date))
    rows = connection.execute(
        f"""SELECT s.post_id, s.target_age_seconds, s.due_at, posts.role, posts.posted_at,
                   posts.pair_id, p.main_posted_at
              FROM snapshot_schedule s
              JOIN posts ON posts.post_id = s.post_id
              JOIN post_pairs p ON p.pair_id = posts.pair_id
             WHERE {' AND '.join(clauses)}
             ORDER BY s.due_at, s.post_id, s.target_age_seconds""",
        params,
    ).fetchall()
    if max_pairs is not None:
        selected_pairs: list[int] = []
        for row in rows:
            pair_id = int(row["pair_id"])
            if pair_id not in selected_pairs:
                if len(selected_pairs) >= max_pairs:
                    continue
                selected_pairs.append(pair_id)
        allowed = set(selected_pairs)
        rows = [row for row in rows if int(row["pair_id"]) in allowed]
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = grouped.setdefault(
            row["post_id"],
            {
                "post_id": row["post_id"],
                "role": row["role"],
                "posted_at": row["posted_at"],
                "pair_id": row["pair_id"],
                "targets": [],
            },
        )
        item["targets"].append({"target_age_seconds": row["target_age_seconds"], "due_at": row["due_at"]})
    return list(grouped.values())


def _batches(values: Sequence[Any], size: int) -> Iterator[list[Any]]:
    for index in range(0, len(values), size):
        yield list(values[index:index + size])


def _retry_after(result: ReadResult, now: datetime) -> tuple[int | None, str | None]:
    retry_after: int | None = None
    reset_at: str | None = None
    try:
        if result.headers.get("retry-after"):
            retry_after = max(0, int(float(result.headers["retry-after"])))
    except (TypeError, ValueError):
        pass
    try:
        reset_epoch = int(result.headers.get("x-rate-limit-reset", ""))
        reset = datetime.fromtimestamp(reset_epoch, tz=timezone.utc)
        reset_at = iso_utc(reset)
        retry_after = max(retry_after or 0, int((reset - now).total_seconds()))
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    return retry_after, reset_at


def _record_attempt(
    connection: sqlite3.Connection,
    *,
    batch_key: str,
    post_ids: Sequence[str],
    attempt_number: int,
    started_at: datetime,
    finished_at: datetime,
    elapsed: float,
    outcome: str,
    response_status: int | None = None,
    request_id: str | None = None,
    error: BaseException | None = None,
    rate_limit_reset_at: str | None = None,
    retry_after_seconds: int | None = None,
    raw_path: str | None = None,
    raw_hash: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO collection_attempts(
               batch_key, post_ids_json, request_attempt_number, started_at, finished_at,
               elapsed_seconds, endpoint, auth_class, outcome, response_status, request_id,
               error_class, error_message, rate_limit_reset_at, retry_after_seconds,
               raw_response_path, raw_response_sha256
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            batch_key,
            json.dumps(list(post_ids)),
            attempt_number,
            iso_utc(started_at),
            iso_utc(finished_at),
            elapsed,
            XReadClient.endpoint_path,
            XReadClient.auth_class,
            outcome,
            response_status,
            request_id,
            type(error).__name__ if error else None,
            str(error)[:1000] if error else None,
            rate_limit_reset_at,
            retry_after_seconds,
            raw_path,
            raw_hash,
        ),
    )


def _error_by_post(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    errors = body.get("errors")
    if not isinstance(errors, list):
        return result
    for error in errors:
        if not isinstance(error, dict):
            continue
        post_id = str(error.get("resource_id") or error.get("value") or "")
        if POST_ID_RE.fullmatch(post_id):
            result[post_id] = error
    return result


def _terminal_error(error: dict[str, Any]) -> str | None:
    text = " ".join(str(error.get(key) or "") for key in ("title", "detail", "type")).lower()
    if "not found" in text or "deleted" in text:
        return "not_found_or_deleted"
    if "forbidden" in text or "not authorized" in text or "not authorised" in text:
        return "forbidden"
    return None


def _insert_snapshot(
    connection: sqlite3.Connection,
    *,
    plan: dict[str, Any],
    target: dict[str, Any],
    collected_at: datetime,
    metrics: dict[str, int | None],
    unavailable: dict[str, str],
    derived: dict[str, Any],
    attempt_number: int,
    response_status: int,
    request_id: str | None,
    raw_path: str,
    raw_hash: str,
    terminal_state: str | None = None,
) -> int:
    posted_at = parse_datetime(plan["posted_at"])
    actual_age = max(0, int((collected_at - posted_at).total_seconds()))
    values = [metrics.get(field) for field in METRIC_FIELDS]
    rate_values = [derived.get(field) for field in RATE_FIELDS]
    cursor = connection.execute(
        f"""INSERT INTO metric_snapshots(
            post_id, role, target_age_seconds, revision_number, actual_age_seconds, due_at,
            collected_at, endpoint, auth_class, {', '.join(METRIC_FIELDS)}, engagement_count,
            {', '.join(RATE_FIELDS)}, engagement_rate_basis, unavailable_fields_json,
            request_attempt_number, response_status, request_id, raw_response_path,
            raw_response_sha256, terminal_state
        ) VALUES ({', '.join('?' for _ in range(9 + len(METRIC_FIELDS) + 1 + len(RATE_FIELDS) + 8))})""",
        (
            plan["post_id"], plan["role"], target["target_age_seconds"], 1, actual_age,
            target["due_at"], iso_utc(collected_at), XReadClient.endpoint_path,
            XReadClient.auth_class, *values, derived.get("engagement_count"), *rate_values,
            derived.get("engagement_rate_basis"), json.dumps(unavailable, sort_keys=True),
            attempt_number, response_status, request_id, raw_path, raw_hash, terminal_state,
        ),
    )
    snapshot_id = int(cursor.lastrowid)
    connection.execute(
        """UPDATE snapshot_schedule
              SET status = ?, completed_snapshot_id = ?, terminal_reason = ?, completed_at = ?
            WHERE post_id = ? AND target_age_seconds = ? AND status = 'pending'""",
        (
            "terminal" if terminal_state else "completed",
            snapshot_id,
            terminal_state,
            iso_utc(collected_at),
            plan["post_id"],
            target["target_age_seconds"],
        ),
    )
    return snapshot_id


def collect_due_snapshots(
    paths: AnalyticsPaths,
    connection: sqlite3.Connection,
    *,
    execute_read: bool,
    max_api_requests: int,
    max_pairs: int | None = None,
    now: datetime | None = None,
    client_factory: Callable[[], Any] | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict[str, Any]:
    """Collect due read-only engagement snapshots within the request budget."""
    now = (now or utc_now()).astimezone(timezone.utc)
    if type(max_api_requests) is not int or max_api_requests < 1:
        raise AnalyticsError("max_api_requests must be a positive integer")
    config = load_config(paths)
    plan = due_snapshot_plan(
        connection, now=now, max_pairs=max_pairs, from_date=from_date, to_date=to_date
    )
    planned_batches = list(_batches(plan, int(config["maximum_batch_size"])))
    preflight = {
        "due_posts": len(plan),
        "due_snapshots": sum(len(item["targets"]) for item in plan),
        "planned_post_ids": [item["post_id"] for item in plan],
        "planned_request_batches": [[item["post_id"] for item in batch] for batch in planned_batches],
        "estimated_request_count": min(len(planned_batches), max_api_requests),
        "max_api_requests": max_api_requests,
    }
    cooldown_until = state_value(connection, "cooldown_until")
    if cooldown_until:
        try:
            cooldown_time = parse_datetime(cooldown_until)
        except ValueError:
            cooldown_time = now
        preflight["active_cooldown"] = cooldown_until if cooldown_time > now else None
        preflight["cooldown_reason"] = state_value(connection, "cooldown_reason")
        if cooldown_time > now:
            return {**preflight, "status": "cooldown_active", "requests_made": 0, "completed_snapshots": 0}
    else:
        preflight["active_cooldown"] = None
    if not plan:
        return {**preflight, "status": "nothing_due", "requests_made": 0, "completed_snapshots": 0}
    if not execute_read:
        return {**preflight, "status": "dry_run", "requests_made": 0, "completed_snapshots": 0}

    client = client_factory() if client_factory else XReadClient(
        base_url=config["x_api_base_url"],
        timeout_seconds=float(config["request_timeout_seconds"]),
    )
    requests_made = completed = terminal = returned_posts = 0
    stopped_reason: str | None = None
    for batch_index, batch in enumerate(planned_batches, start=1):
        if requests_made >= max_api_requests:
            stopped_reason = "maximum_api_requests_reached"
            break
        post_ids = [item["post_id"] for item in batch]
        batch_key = hashlib.sha256("\n".join(post_ids).encode()).hexdigest()
        result: ReadResult | None = None
        result_raw_path: str | None = None
        result_raw_hash: str | None = None
        result_finished_at: datetime | None = None
        successful_attempt_number: int | None = None
        for attempt_number in (1, 2):
            if requests_made >= max_api_requests:
                stopped_reason = "maximum_api_requests_reached"
                break
            started = utc_now()
            try:
                result = client.lookup_posts(post_ids)
                finished = utc_now()
                requests_made += 1
                raw_path, raw_hash = write_raw_response(
                    paths, result, post_ids, attempt_number, collected_at=finished
                )
                result_raw_path = raw_path
                result_raw_hash = raw_hash
                result_finished_at = finished
                successful_attempt_number = attempt_number
                retry_after, reset_at = _retry_after(result, finished)
                outcome = "success" if 200 <= result.status_code < 300 else "rate_limited" if result.status_code == 429 else "http_error"
                with connection:
                    _record_attempt(
                        connection,
                        batch_key=batch_key,
                        post_ids=post_ids,
                        attempt_number=attempt_number,
                        started_at=started,
                        finished_at=finished,
                        elapsed=result.elapsed_seconds,
                        outcome=outcome,
                        response_status=result.status_code,
                        request_id=result.request_id,
                        rate_limit_reset_at=reset_at,
                        retry_after_seconds=retry_after,
                        raw_path=raw_path,
                        raw_hash=raw_hash,
                    )
                    if result.status_code == 429:
                        seconds = max(retry_after or 0, 15 * 60)
                        set_state_value(connection, "cooldown_until", iso_utc(finished + timedelta(seconds=seconds)), now=finished)
                        set_state_value(connection, "cooldown_reason", "X metrics lookup returned 429", now=finished)
                sync_state_file(paths, connection)
                if result.status_code == 429:
                    stopped_reason = "rate_limited"
                    break
                if result.status_code >= 500 and attempt_number == 1 and requests_made < max_api_requests:
                    delay = max(float(config["retry_backoff_seconds"]), float(retry_after or 0))
                    time.sleep(delay)
                    continue
                break
            except requests.RequestException as exc:
                finished = utc_now()
                requests_made += 1
                with connection:
                    _record_attempt(
                        connection,
                        batch_key=batch_key,
                        post_ids=post_ids,
                        attempt_number=attempt_number,
                        started_at=started,
                        finished_at=finished,
                        elapsed=max(0.0, (finished - started).total_seconds()),
                        outcome="transport_error",
                        error=exc,
                    )
                sync_state_file(paths, connection)
                if attempt_number == 1 and requests_made < max_api_requests:
                    time.sleep(float(config["retry_backoff_seconds"]))
                    continue
                stopped_reason = "transport_error"
                result = None
                break
        if stopped_reason in {"rate_limited", "transport_error", "maximum_api_requests_reached"} and result is None:
            break
        if result is None or not (200 <= result.status_code < 300):
            if stopped_reason == "rate_limited":
                break
            if result and result.status_code in (401, 403):
                with connection:
                    set_state_value(connection, "last_capability_error", {
                        "status": result.status_code,
                        "observed_at": iso_utc(),
                        "reason": "authentication_or_metric_capability_denied",
                    })
                sync_state_file(paths, connection)
            if stopped_reason is None:
                stopped_reason = f"http_{result.status_code}" if result is not None else "request_failed"
            break

        if not result_raw_path or not result_raw_hash or result_finished_at is None or successful_attempt_number is None:
            raise AnalyticsError("successful X response is missing its persisted audit record")
        data = result.body.get("data")
        posts = {
            str(item.get("id")): item
            for item in data
            if isinstance(data, list) and isinstance(item, dict) and POST_ID_RE.fullmatch(str(item.get("id") or ""))
        } if isinstance(data, list) else {}
        errors = _error_by_post(result.body)
        returned_posts += len(posts)
        finished = result_finished_at
        raw_path, raw_hash = result_raw_path, result_raw_hash
        capability_counts = {field: 0 for field in METRIC_FIELDS}
        with connection:
            for item in batch:
                post_id = item["post_id"]
                if post_id in posts:
                    metrics, unavailable, derived = extract_metrics(posts[post_id])
                    for field, value in metrics.items():
                        if value is not None:
                            capability_counts[field] += 1
                    for target in item["targets"]:
                        _insert_snapshot(
                            connection,
                            plan=item,
                            target=target,
                            collected_at=finished,
                            metrics=metrics,
                            unavailable=unavailable,
                            derived=derived,
                            attempt_number=successful_attempt_number,
                            response_status=result.status_code,
                            request_id=result.request_id,
                            raw_path=raw_path,
                            raw_hash=raw_hash,
                        )
                        completed += 1
                elif post_id in errors:
                    terminal_state = _terminal_error(errors[post_id])
                    if terminal_state:
                        unavailable = {field: terminal_state for field in METRIC_FIELDS}
                        metrics = {field: None for field in METRIC_FIELDS}
                        for target in item["targets"]:
                            _insert_snapshot(
                                connection,
                                plan=item,
                                target=target,
                                collected_at=finished,
                                metrics=metrics,
                                unavailable=unavailable,
                                derived=derive_metrics(metrics),
                                attempt_number=successful_attempt_number,
                                response_status=result.status_code,
                                request_id=result.request_id,
                                raw_path=raw_path,
                                raw_hash=raw_hash,
                                terminal_state=terminal_state,
                            )
                            terminal += 1
            set_state_value(connection, "metric_capabilities", {
                "observed_at": iso_utc(finished),
                "returned_post_count": len(posts),
                "available_counts": capability_counts,
                "authentication_class": XReadClient.auth_class,
            }, now=finished)
            set_state_value(connection, "last_successful_collection", iso_utc(finished), now=finished)
            set_state_value(connection, "cooldown_until", None, now=finished)
            set_state_value(connection, "cooldown_reason", None, now=finished)
        sync_state_file(paths, connection)
        if requests_made >= max_api_requests and batch_index < len(planned_batches):
            stopped_reason = "maximum_api_requests_reached"
            break
    return {
        **preflight,
        "status": "completed" if not stopped_reason else "stopped",
        "stopped_reason": stopped_reason,
        "requests_made": requests_made,
        "returned_posts": returned_posts,
        "completed_snapshots": completed,
        "terminal_snapshots": terminal,
    }


def database_status(paths: AnalyticsPaths, *, now: datetime | None = None) -> dict[str, Any]:
    """Return the database status."""
    now = (now or utc_now()).astimezone(timezone.utc)
    if not paths.database.exists():
        return {
            "initialised": False,
            "database": str(paths.database),
            "discovered_post_pairs": 0,
            "due_snapshots": 0,
            "overdue_snapshots": 0,
            "planned_post_ids": [],
            "planned_request_batches": [],
            "estimated_request_count": 0,
            "active_cooldown": None,
            "last_successful_collection": None,
            "unavailable_metric_capabilities": "unavailable",
        }
    with connect_database(paths, readonly=True) as connection:
        config = load_config(paths)
        pair_count = connection.execute("SELECT COUNT(*) FROM post_pairs").fetchone()[0]
        post_count = connection.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        due_count = connection.execute(
            "SELECT COUNT(*) FROM snapshot_schedule WHERE status='pending' AND due_at <= ?", (iso_utc(now),)
        ).fetchone()[0]
        overdue_count = connection.execute(
            "SELECT COUNT(*) FROM snapshot_schedule WHERE status='pending' AND due_at < ?",
            (iso_utc(now - timedelta(seconds=int(config["on_time_tolerance_seconds"]))),),
        ).fetchone()[0]
        plan = due_snapshot_plan(connection, now=now)
        batch_size = int(config["maximum_batch_size"])
        batches = [[row["post_id"] for row in batch] for batch in _batches(plan, batch_size)]
        cooldown_until = state_value(connection, "cooldown_until")
        active_cooldown = None
        if cooldown_until:
            try:
                active_cooldown = cooldown_until if parse_datetime(cooldown_until) > now else None
            except ValueError:
                active_cooldown = "invalid persisted cooldown"
        capabilities = state_value(connection, "metric_capabilities", {})
        available = capabilities.get("available_counts", {}) if isinstance(capabilities, dict) else {}
        unavailable = [field for field in METRIC_FIELDS if not int(available.get(field, 0) or 0)]
        return {
            "initialised": True,
            "database": str(paths.database),
            "discovered_post_pairs": pair_count,
            "tracked_posts": post_count,
            "due_snapshots": due_count,
            "overdue_snapshots": overdue_count,
            "planned_post_ids": [row["post_id"] for row in plan],
            "planned_request_batches": batches,
            "estimated_request_count": len(batches),
            "active_cooldown": active_cooldown,
            "cooldown_reason": state_value(connection, "cooldown_reason"),
            "last_successful_collection": state_value(connection, "last_successful_collection"),
            "unavailable_metric_capabilities": unavailable or [],
        }


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    clean = sorted(float(value) for value in values if value is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    position = (len(clean) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(clean) - 1)
    fraction = position - lower
    return clean[lower] * (1 - fraction) + clean[upper] * fraction


def _distribution(values: Sequence[float | int | None], minimum_sample: int) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None]
    return {
        "sample_size": len(clean),
        "median": statistics.median(clean) if clean else None,
        "q1": _percentile(clean, 0.25),
        "q3": _percentile(clean, 0.75),
        "interpretation": "observational association only" if len(clean) >= minimum_sample else "insufficient sample",
    }


def _latest_snapshots(connection: sqlite3.Connection, pair_ids: Sequence[int]) -> dict[str, sqlite3.Row]:
    if not pair_ids:
        return {}
    placeholders = ",".join("?" for _ in pair_ids)
    rows = connection.execute(
        f"""SELECT m.*, posts.pair_id
              FROM metric_snapshots m
              JOIN posts ON posts.post_id = m.post_id
             WHERE posts.pair_id IN ({placeholders})
             ORDER BY m.post_id, m.target_age_seconds DESC, m.collected_at DESC, m.revision_number DESC""",
        tuple(pair_ids),
    ).fetchall()
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        latest.setdefault(row["post_id"], row)
    return latest


def _image_score_band(value: Any) -> str:
    if value is None:
        return "unavailable"
    number = float(value)
    if number < 20:
        return "under_20"
    if number < 40:
        return "20_to_39"
    if number < 60:
        return "40_to_59"
    return "60_plus"


def report_summary(
    connection: sqlite3.Connection,
    *,
    window_days: int | None,
    now: datetime | None = None,
    minimum_sample: int = MIN_REPORT_SAMPLE,
) -> dict[str, Any]:
    """Report summary."""
    now = (now or utc_now()).astimezone(timezone.utc)
    params: list[Any] = []
    where = ""
    if window_days is not None:
        where = "WHERE main_posted_at >= ?"
        params.append(iso_utc(now - timedelta(days=window_days)))
    pairs = connection.execute(f"SELECT * FROM post_pairs {where} ORDER BY main_posted_at", params).fetchall()
    pair_ids = [int(row["pair_id"]) for row in pairs]
    latest = _latest_snapshots(connection, pair_ids)
    main_rows = [latest[row["main_post_id"]] for row in pairs if row["main_post_id"] in latest]
    context_rows = [latest[row["context_post_id"]] for row in pairs if row["context_post_id"] in latest]
    context_view_ratios: list[float] = []
    context_engagement_ratios: list[float] = []
    context_bookmark_ratios: list[float] = []
    pair_observations: list[dict[str, Any]] = []
    for pair in pairs:
        main = latest.get(pair["main_post_id"])
        context = latest.get(pair["context_post_id"]) if pair["context_post_id"] else None
        if not main or not context:
            continue
        view_ratio = _rate(context["impressions"], main["impressions"])
        engagement_ratio = _rate(context["engagement_count"], main["engagement_count"])
        bookmark_ratio = _rate(context["bookmarks"], main["bookmarks"])
        if view_ratio is not None:
            context_view_ratios.append(view_ratio)
        if engagement_ratio is not None:
            context_engagement_ratios.append(engagement_ratio)
        if bookmark_ratio is not None:
            context_bookmark_ratios.append(bookmark_ratio)
        pair_observations.append({
            "pair_id": pair["pair_id"],
            "context_view_ratio": view_ratio,
            "context_engagement_ratio": engagement_ratio,
            "context_bookmark_ratio": bookmark_ratio,
            "context_delay_seconds": int((parse_datetime(pair["context_posted_at"]) - parse_datetime(pair["main_posted_at"])).total_seconds()),
        })

    coverage = {}
    if pair_ids:
        placeholders = ",".join("?" for _ in pair_ids)
        rows = connection.execute(
            f"""SELECT s.target_age_seconds, s.status, s.due_at, COUNT(*) AS count
                  FROM snapshot_schedule s JOIN posts ON posts.post_id=s.post_id
                 WHERE posts.pair_id IN ({placeholders})
                 GROUP BY s.target_age_seconds, s.status, s.due_at""",
            tuple(pair_ids),
        ).fetchall()
        for target in SNAPSHOT_TARGETS:
            coverage[TARGET_LABELS[target]] = {
                "pending": 0, "completed": 0, "terminal": 0, "total": 0,
                "due_total": 0, "overdue_pending": 0, "not_due": 0, "due_completion_rate": None,
            }
        for row in rows:
            item = coverage[TARGET_LABELS[row["target_age_seconds"]]]
            item[row["status"]] += row["count"]
            item["total"] += row["count"]
            due = parse_datetime(row["due_at"]) <= now
            if due:
                item["due_total"] += row["count"]
                if row["status"] == "pending":
                    item["overdue_pending"] += row["count"]
            else:
                item["not_due"] += row["count"]
        for item in coverage.values():
            observed_due = item["completed"] + item["terminal"]
            item["due_completion_rate"] = observed_due / item["due_total"] if item["due_total"] else None

    def group_results(field: str, transform: Callable[[Any], str] | None = None) -> dict[str, Any]:
        groups: dict[str, dict[str, list[float | None]]] = {}
        for pair in pairs:
            key_value = transform(pair[field]) if transform else str(pair[field] or "unavailable")
            main = latest.get(pair["main_post_id"])
            context = latest.get(pair["context_post_id"]) if pair["context_post_id"] else None
            values = groups.setdefault(key_value, {
                "main_post_engagement_rate": [],
                "context_engagement_rate": [],
                "context_view_ratio": [],
                "context_bookmark_rate": [],
                "source_link_click_rate": [],
            })
            values["main_post_engagement_rate"].append(main["engagement_rate"] if main else None)
            values["context_engagement_rate"].append(context["engagement_rate"] if context else None)
            values["context_view_ratio"].append(
                _rate(context["impressions"], main["impressions"]) if main and context else None
            )
            values["context_bookmark_rate"].append(context["bookmark_rate"] if context else None)
            values["source_link_click_rate"].append(context["url_click_rate"] if context else None)
        return {
            key: {metric: _distribution(observations, minimum_sample) for metric, observations in values.items()}
            for key, values in sorted(groups.items())
        }

    missing_impressions = sum(row["impressions"] is None for row in latest.values())
    missing_clicks = sum(row["url_link_clicks"] is None for row in latest.values())
    return {
        "schema_version": 1,
        "generated_at": iso_utc(now),
        "window_days": window_days,
        "labels": ["observational association only", "insufficient sample", "metric unavailable"],
        "tracked_main_posts": len(pairs),
        "tracked_context_replies": sum(pair["context_post_id"] is not None for pair in pairs),
        "context_completion_rate": (
            sum(pair["context_post_id"] is not None for pair in pairs) / len(pairs) if pairs else None
        ),
        "snapshot_coverage": coverage,
        "main_post_engagement_rate": _distribution([row["engagement_rate"] for row in main_rows], minimum_sample),
        "context_engagement_rate": _distribution([row["engagement_rate"] for row in context_rows], minimum_sample),
        "context_bookmark_rate": _distribution([row["bookmark_rate"] for row in context_rows], minimum_sample),
        "context_source_link_click_rate": _distribution([row["url_click_rate"] for row in context_rows], minimum_sample),
        "context_view_ratio": _distribution(context_view_ratios, minimum_sample),
        "context_engagement_ratio": _distribution(context_engagement_ratios, minimum_sample),
        "context_bookmark_ratio": _distribution(context_bookmark_ratios, minimum_sample),
        "pair_observations": pair_observations,
        "groups": {
            "formatter_version": group_results("formatter_version"),
            "verification_label": group_results("verification_label"),
            "source_class": group_results("source_class"),
            "historical_confidence": group_results("historical_confidence"),
            "quotation_topic": group_results("quotation_topic"),
            "image_source": group_results("image_source"),
            "image_score_band": group_results("image_score", _image_score_band),
        },
        "missing_metrics": {
            "snapshots_with_unavailable_impressions": missing_impressions,
            "snapshots_with_unavailable_url_clicks": missing_clicks,
            "latest_snapshot_count": len(latest),
        },
        "sample_size_warnings": [
            name
            for name, summary in (
                ("main_post_engagement_rate", _distribution([row["engagement_rate"] for row in main_rows], minimum_sample)),
                ("context_view_ratio", _distribution(context_view_ratios, minimum_sample)),
                ("context_source_link_click_rate", _distribution([row["url_click_rate"] for row in context_rows], minimum_sample)),
            )
            if summary["sample_size"] < minimum_sample
        ],
    }


def _format_rate(value: Any) -> str:
    return "metric unavailable" if value is None else f"{float(value) * 100:.2f}%"


def render_report(summary: dict[str, Any]) -> str:
    """Render report."""
    window = "all observed data" if summary["window_days"] is None else f"trailing {summary['window_days']} days"
    lines = [
        "# Historical context engagement analytics",
        "",
        f"Window: **{window}**",
        f"Generated: `{summary['generated_at']}`",
        "",
        "> All comparisons are observational associations. They do not establish that a context reply caused engagement.",
        "",
        f"Tracked main posts: **{summary['tracked_main_posts']}**",
        f"Posts with context replies: **{summary['tracked_context_replies']}**",
        f"Context completion rate: **{_format_rate(summary['context_completion_rate'])}**",
        "",
        "## Snapshot coverage",
        "",
        "| target | completed | terminal | overdue | not due | due coverage | total |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for target, values in summary["snapshot_coverage"].items():
        lines.append(
            f"| {target} | {values['completed']} | {values['terminal']} | "
            f"{values['overdue_pending']} | {values['not_due']} | "
            f"{_format_rate(values['due_completion_rate'])} | {values['total']} |"
        )
    lines.extend(["", "## Headline distributions", "", "| measure | n | median | IQR | interpretation |", "|---|---:|---:|---:|---|"])
    for key in (
        "main_post_engagement_rate", "context_engagement_rate", "context_view_ratio",
        "context_bookmark_rate", "context_source_link_click_rate",
    ):
        item = summary[key]
        median = _format_rate(item["median"])
        iqr = "metric unavailable" if item["q1"] is None else f"{_format_rate(item['q1'])}–{_format_rate(item['q3'])}"
        lines.append(f"| {key.replace('_', ' ')} | {item['sample_size']} | {median} | {iqr} | {item['interpretation']} |")
    lines.extend([
        "",
        "## Missing metrics",
        "",
        f"Latest snapshots with unavailable impressions: **{summary['missing_metrics']['snapshots_with_unavailable_impressions']}**",
        f"Latest snapshots with unavailable URL-click metrics: **{summary['missing_metrics']['snapshots_with_unavailable_url_clicks']}**",
        "",
        "## Group summaries",
        "",
    ])
    for group_name, groups in summary["groups"].items():
        lines.append(f"### {group_name.replace('_', ' ').title()}")
        lines.append("")
        lines.append("| group | main n | main engagement | context n | context engagement | view-ratio n | context view ratio | interpretation |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
        for group, values in groups.items():
            safe_group = str(group).replace("|", "\\|")
            main = values["main_post_engagement_rate"]
            context = values["context_engagement_rate"]
            view = values["context_view_ratio"]
            interpretations = {main["interpretation"], context["interpretation"], view["interpretation"]}
            interpretation = "observational association only" if interpretations == {"observational association only"} else "insufficient sample"
            lines.append(
                f"| {safe_group} | {main['sample_size']} | {_format_rate(main['median'])} | "
                f"{context['sample_size']} | {_format_rate(context['median'])} | "
                f"{view['sample_size']} | {_format_rate(view['median'])} | {interpretation} |"
            )
        lines.append("")
    if summary["sample_size_warnings"]:
        lines.append("Sample-size warnings: " + ", ".join(summary["sample_size_warnings"]) + ".")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate_reports(paths: AnalyticsPaths, *, window_days: int | None = None) -> dict[str, str]:
    """Generate bounded engagement reports from local analytics data."""
    windows = [window_days] if window_days is not None else [7, 28, 90, None]
    written: dict[str, str] = {}
    with connect_database(paths, readonly=True) as connection:
        minimum = int(load_config(paths)["report_minimum_sample"])
        for window in windows:
            summary = report_summary(connection, window_days=window, minimum_sample=minimum)
            name = "all_observed.md" if window is None else f"trailing_{window}d.md"
            path = _runtime_path(paths, paths.reports / name)
            atomic_write_text(path, render_report(summary))
            atomic_write_json(path.with_suffix(".json"), summary)
            written[str(window)] = str(path)
    return written


def export_data(paths: AnalyticsPaths, formats: Sequence[str]) -> dict[str, str]:
    """Export engagement data deterministically without changing production state."""
    allowed = {"json", "csv", "markdown"}
    requested = {value.strip().lower() for value in formats if value.strip()}
    if not requested or not requested <= allowed:
        raise AnalyticsError(f"export formats must be drawn from {sorted(allowed)}")
    paths.exports.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    with connect_database(paths, readonly=True) as connection:
        pairs = [dict(row) for row in connection.execute("SELECT * FROM post_pairs ORDER BY main_posted_at")]
        snapshots = [dict(row) for row in connection.execute("SELECT * FROM metric_snapshots ORDER BY collected_at, snapshot_id")]
        if "json" in requested:
            target = _runtime_path(paths, paths.exports / "engagement_export.json")
            atomic_write_json(target, {"schema_version": 1, "exported_at": iso_utc(), "post_pairs": pairs, "metric_snapshots": snapshots})
            written["json"] = str(target)
        if "csv" in requested:
            for name, rows in (("post_pairs.csv", pairs), ("metric_snapshots.csv", snapshots)):
                target = _runtime_path(paths, paths.exports / name)
                temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
                with temporary.open("w", encoding="utf-8", newline="") as handle:
                    fieldnames = list(rows[0]) if rows else []
                    writer = csv.DictWriter(handle, fieldnames=fieldnames)
                    if fieldnames:
                        writer.writeheader()
                        writer.writerows(rows)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
                written[name] = str(target)
        if "markdown" in requested:
            summary = report_summary(connection, window_days=None, minimum_sample=int(load_config(paths)["report_minimum_sample"]))
            target = _runtime_path(paths, paths.exports / "engagement_export.md")
            atomic_write_text(target, render_report(summary))
            written["markdown"] = str(target)
    return written


def read_digest_summary(project_dir: Path, *, window_days: int = 28) -> dict[str, Any]:
    """Read the local analytics DB in query-only mode; never create or update it."""
    paths = AnalyticsPaths.for_project(project_dir)
    if not paths.database.is_file():
        return {
            "available": False,
            "reason": "analytics database not initialised",
            "tracked_post_pairs": 0,
            "latest_snapshot_coverage": None,
        }
    try:
        with connect_database(paths, readonly=True) as connection:
            summary = report_summary(connection, window_days=window_days)
    except (sqlite3.Error, AnalyticsError, ValueError) as exc:
        return {"available": False, "reason": f"analytics database unavailable: {type(exc).__name__}", "tracked_post_pairs": 0}
    coverage_values = list(summary["snapshot_coverage"].values())
    completed = sum(item["completed"] + item["terminal"] for item in coverage_values)
    total = sum(item["due_total"] for item in coverage_values)
    return {
        "available": True,
        "window_days": window_days,
        "tracked_post_pairs": summary["tracked_main_posts"],
        "posts_with_context_replies": summary["tracked_context_replies"],
        "latest_snapshot_coverage": completed / total if total else None,
        "median_context_view_ratio": summary["context_view_ratio"]["median"],
        "median_main_post_engagement_rate": summary["main_post_engagement_rate"]["median"],
        "median_context_engagement_rate": summary["context_engagement_rate"]["median"],
        "median_context_bookmark_rate": summary["context_bookmark_rate"]["median"],
        "median_source_link_click_rate": summary["context_source_link_click_rate"]["median"],
        "unavailable_impressions_count": summary["missing_metrics"]["snapshots_with_unavailable_impressions"],
        "unavailable_click_metrics_count": summary["missing_metrics"]["snapshots_with_unavailable_url_clicks"],
        "sample_size_warnings": summary["sample_size_warnings"],
    }


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def _parse_cli_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Europe/London"))
    return parsed.astimezone(timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def project_argument(command: argparse.ArgumentParser) -> None:
        command.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parent)

    initialise = subparsers.add_parser("initialise", help="Create the isolated analytics database and runtime directories")
    project_argument(initialise)

    discover = subparsers.add_parser("discover", help="Discover structured quote/context post pairs")
    project_argument(discover)
    discover.add_argument("--since-days", type=int, default=14)
    discover.add_argument("--max-pairs", type=int)
    discover.add_argument("--dry-run", action="store_true")

    status = subparsers.add_parser("status", help="Show local collector status without network access")
    project_argument(status)

    collect = subparsers.add_parser("collect", help="Collect due metric snapshots")
    project_argument(collect)
    mode = collect.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute-read", action="store_true")
    collect.add_argument("--max-api-requests", type=int, default=2)
    collect.add_argument("--max-pairs", type=int)
    collect.add_argument("--resume", action="store_true")

    report = subparsers.add_parser("report", help="Generate prospective local reports")
    project_argument(report)
    report.add_argument("--window-days", type=int)

    export = subparsers.add_parser("export", help="Export append-only analytics data")
    project_argument(export)
    export.add_argument("--format", default="json,csv,markdown")

    backfill = subparsers.add_parser("backfill", help="Bounded read-only historical collection")
    project_argument(backfill)
    mode = backfill.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute-read", action="store_true")
    backfill.add_argument("--from-date")
    backfill.add_argument("--to-date")
    backfill.add_argument("--max-pairs", type=int)
    backfill.add_argument("--max-api-requests", type=int, required=True)
    backfill.add_argument("--confirm-read-only", action="store_true")

    scheduled = subparsers.add_parser("scheduled-run", help="Discover and collect due snapshots for a timer")
    project_argument(scheduled)
    scheduled.add_argument("--since-days", type=int, default=14)
    scheduled.add_argument("--execute-read", action="store_true", required=True)
    scheduled.add_argument("--max-api-requests", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line entry point."""
    args = build_parser().parse_args(argv)
    paths = AnalyticsPaths.for_project(args.project_dir)
    file_logging = (
        args.command in {"initialise", "report", "export", "scheduled-run"}
        or (args.command == "discover" and not args.dry_run)
        or (args.command in {"collect", "backfill"} and bool(args.execute_read))
    )
    configure_logging(paths, file_logging=file_logging)
    if args.command == "status":
        _print_json(database_status(paths))
        return 0
    if args.command == "initialise":
        with collector_lock(paths):
            result = initialise_database(paths)
        LOG.info("Initialised engagement analytics database path=%s", paths.database)
        _print_json(result)
        return 0
    if args.command == "discover":
        pairs = discover_post_pairs(paths, since_days=args.since_days, max_pairs=args.max_pairs)
        if args.dry_run:
            _print_json({"status": "dry_run", "pair_count": len(pairs), "pairs": pairs})
            return 0
        with collector_lock(paths), connect_database(paths) as connection:
            result = apply_discovery(connection, pairs)
            sync_state_file(paths, connection)
        LOG.info("Discovered engagement pairs inserted=%s updated=%s unchanged=%s", result["inserted"], result["updated"], result["unchanged"])
        _print_json(result)
        return 0
    if args.command == "collect":
        with collector_lock(paths), connect_database(paths) as connection:
            result = collect_due_snapshots(
                paths,
                connection,
                execute_read=bool(args.execute_read),
                max_api_requests=args.max_api_requests,
                max_pairs=args.max_pairs,
            )
        LOG.info("Engagement collection status=%s requests=%s snapshots=%s", result["status"], result["requests_made"], result["completed_snapshots"])
        _print_json(result)
        return 0
    if args.command == "report":
        with collector_lock(paths):
            result = generate_reports(paths, window_days=args.window_days)
        LOG.info("Generated engagement reports count=%s", len(result))
        _print_json(result)
        return 0
    if args.command == "export":
        with collector_lock(paths):
            result = export_data(paths, args.format.split(","))
        LOG.info("Generated engagement exports count=%s", len(result))
        _print_json(result)
        return 0
    if args.command == "backfill":
        if not (args.from_date or args.to_date or args.max_pairs):
            raise SystemExit("backfill requires a date bound or --max-pairs")
        if args.execute_read and not args.confirm_read_only:
            raise SystemExit("live backfill requires --confirm-read-only")
        with collector_lock(paths), connect_database(paths) as connection:
            result = collect_due_snapshots(
                paths,
                connection,
                execute_read=bool(args.execute_read),
                max_api_requests=args.max_api_requests,
                max_pairs=args.max_pairs,
                from_date=_parse_cli_datetime(args.from_date),
                to_date=_parse_cli_datetime(args.to_date),
            )
        LOG.info("Bounded engagement backfill status=%s requests=%s", result["status"], result["requests_made"])
        _print_json({**result, "read_only_endpoint_confirmation": bool(args.confirm_read_only)})
        return 0
    if args.command == "scheduled-run":
        with collector_lock(paths), connect_database(paths) as connection:
            pairs = discover_post_pairs(paths, since_days=args.since_days)
            discovery = apply_discovery(connection, pairs)
            result = collect_due_snapshots(
                paths,
                connection,
                execute_read=True,
                max_api_requests=args.max_api_requests,
            )
        LOG.info("Scheduled engagement run discovery=%s collection=%s", discovery, result["status"])
        _print_json({"discovery": discovery, "collection": result})
        return 0
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
