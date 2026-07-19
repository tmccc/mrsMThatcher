#!/usr/bin/env python3
"""Offline production-parity quotation/image selection harness.

The harness snapshots production inputs read-only, imports the bot in test mode,
blocks outbound networking, and runs the real selectors against private state.
It never posts and never writes production state, histories, receipts or media.
"""

from __future__ import annotations

import argparse
import builtins
import copy
import hashlib
import importlib
import io
import json
import os
import random
import resource
import socket
import sqlite3
import statistics
import subprocess
import sys
import time
import zlib
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator, Sequence
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

from historical_context_formatter import packet_is_attributed_to_margaret_thatcher
from semantic_alignment.io import atomic_write_json, atomic_write_text, sha256_file
from semantic_alignment.quote_image_semantic_veto import ShadowRuntime
from tools import simulate_regular_post_futures as production_sim


ROOT = Path(__file__).resolve().parent
DEFAULT_RUN_DIR = ROOT / "semantic_alignment_research" / "quote_image_selection_harness_001"
SNAPSHOT_DIRNAME = "source_snapshot"
DB_NAME = "simulation.sqlite3"
SCHEMA_VERSION = 1
HARNESS_VERSION = "quote-image-selection-harness-v1"
TZ_NAME = "Europe/London"
TZ = ZoneInfo(TZ_NAME)
EXPECTED_COMPLETED = 626
EXPECTED_UNRESOLVED = 6
EXPECTED_ATTRIBUTION_EXCLUDED = 16
EXPECTED_ATTRIBUTION_ACTIVE = 610
EXPECTED_ACTIVE = 610
DEFAULT_YEARS = (2026, 2028, 2029)
SELECTION_CONFIG_KEYS = (
    "POST_SLEEP_MIN",
    "POST_SLEEP_MAX",
    "ENABLE_GENERATED_IMAGE_POOL",
    "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN",
    "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST",
    "MAX_QUOTE_IMAGE_PAIR_ATTEMPTS",
    "QUOTE_SEASON_SOFT_WEIGHT",
    "QUOTE_SEASON_STRONG_WEIGHT",
    "QUOTE_SEASON_DATE_SPECIFIC_WEIGHT",
    "QUOTE_QUALITY_WEIGHT_MAX_MULTIPLIER",
    "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING",
    "ORIGINAL_EDITORIAL_SHADOW_WEIGHT",
    "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT",
    "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING",
    "ENABLE_GENERATED_IDENTITY_POLICY_SCORING",
    "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY",
    "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY",
)
MUTABLE_SOURCES = ("bot_state.json", "images_used.json", "lines_used.json")
RECEIPT_BARRIERS = (
    "regular_post_receipt.json",
    "meme_post_receipt.json",
    "confirmed_reply_receipt.json",
    "historical_context_reply_receipt.json",
    "ambiguous_post_outcome.json",
)
IMMUTABLE_SOURCES = (
    "mrsMThatcher.txt",
    "quote_analysis.json",
    "quote_analysis_overrides.json",
    "image_analysis.json",
    "generated_image_analysis.json",
    "original_image_editorial_analysis_experiment_v1.json",
    "generated_image_identity_dependence_audit.json",
    "mrsMThatcher2.py",
    "semantic_quote_image_veto.py",
    "semantic_alignment/quote_image_semantic_veto.py",
    "semantic_alignment_research/quote_research_full_001/research_packets.json",
    "semantic_alignment_research/quote_research_full_001/final_unresolved/final_research_status.json",
    "semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/attribution_exclusion_tombstones.json",
    "semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json",
)


class HarnessError(RuntimeError):
    """Raised when the offline selection harness violates an invariant."""
    pass


class IsolationError(HarnessError):
    """Raised when the harness attempts an unsafe external operation."""
    pass


def canonical_json(value: Any) -> bytes:
    """Return the canonical JSON."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def hash_value(value: Any) -> str:
    """Hash value."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Return the percentile."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def inside(path: Path, parent: Path) -> bool:
    """Return the inside."""
    path = path.resolve(strict=False)
    parent = parent.resolve(strict=False)
    return path == parent or parent in path.parents


def validate_run_dir(path: Path) -> Path:
    """Validate run dir."""
    resolved = path.expanduser().resolve(strict=False)
    required_parent = (ROOT / "semantic_alignment_research").resolve()
    if not inside(resolved, required_parent) or resolved == required_parent:
        raise IsolationError(f"run directory must be below {required_parent}: {resolved}")
    production_paths = {ROOT / name for name in (*MUTABLE_SOURCES, *RECEIPT_BARRIERS)}
    if any(resolved == path.resolve() for path in production_paths):
        raise IsolationError(f"unsafe run directory: {resolved}")
    return resolved


class OpenAudit:
    """Reject writes outside the run directory and account for source reads."""

    def __init__(self, run_dir: Path):
        """Initialise the open audit."""
        self.run_dir = run_dir.resolve()
        self.production_reads: Counter[str] = Counter()
        self.forbidden_write_attempts: list[dict[str, str]] = []
        self._originals: dict[str, Any] = {}

    @staticmethod
    def _mode_writes(mode: object) -> bool:
        text = str(mode or "r")
        return any(marker in text for marker in ("w", "a", "x", "+"))

    @staticmethod
    def _flags_write(flags: int) -> bool:
        return bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))

    def _check(self, path: object, *, write: bool, operation: str) -> None:
        if isinstance(path, int):
            return
        try:
            resolved = Path(os.fspath(path)).expanduser().resolve(strict=False)
        except (TypeError, ValueError, OSError):
            return
        if write and not inside(resolved, self.run_dir):
            item = {"operation": operation, "path": str(resolved)}
            self.forbidden_write_attempts.append(item)
            raise IsolationError(f"write outside harness run directory blocked: {resolved}")
        if not write and inside(resolved, ROOT) and not inside(resolved, self.run_dir):
            self.production_reads[str(resolved)] += 1

    def __enter__(self) -> "OpenAudit":
        """Enter the context manager."""
        self._originals = {
            "builtins.open": builtins.open,
            "io.open": io.open,
            "os.open": os.open,
            "Path.open": Path.open,
        }

        def guarded_builtin(path: object, mode: str = "r", *args: object, **kwargs: object):
            self._check(path, write=self._mode_writes(mode), operation="open")
            return self._originals["builtins.open"](path, mode, *args, **kwargs)

        def guarded_io(path: object, mode: str = "r", *args: object, **kwargs: object):
            self._check(path, write=self._mode_writes(mode), operation="io.open")
            return self._originals["io.open"](path, mode, *args, **kwargs)

        def guarded_os(path: object, flags: int, *args: object, **kwargs: object):
            self._check(path, write=self._flags_write(flags), operation="os.open")
            return self._originals["os.open"](path, flags, *args, **kwargs)

        def guarded_path_open(
            path: Path,
            mode: str = "r",
            buffering: int = -1,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ):
            self._check(path, write=self._mode_writes(mode), operation="Path.open")
            return self._originals["Path.open"](
                path,
                mode,
                buffering,
                encoding,
                errors,
                newline,
            )

        builtins.open = guarded_builtin
        io.open = guarded_io
        os.open = guarded_os
        Path.open = guarded_path_open
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Exit the context manager."""
        builtins.open = self._originals["builtins.open"]
        io.open = self._originals["io.open"]
        os.open = self._originals["os.open"]
        Path.open = self._originals["Path.open"]

    def summary(self) -> dict[str, Any]:
        """Return the summary."""
        return {
            "schema_version": 1,
            "production_read_open_count": sum(self.production_reads.values()),
            "distinct_production_paths_read": len(self.production_reads),
            "production_paths_opened_for_writing": [],
            "forbidden_write_attempt_count": len(self.forbidden_write_attempts),
            "forbidden_write_attempts": self.forbidden_write_attempts,
        }


@contextmanager
def block_outbound_network() -> Iterator[None]:
    """Block networking and ordinary child-process escape routes while offline."""
    originals = {
        "create_connection": socket.create_connection,
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "sendto": socket.socket.sendto,
        "getaddrinfo": socket.getaddrinfo,
        "gethostbyname": socket.gethostbyname,
        "gethostbyname_ex": socket.gethostbyname_ex,
        "gethostbyaddr": socket.gethostbyaddr,
    }

    def blocked(*_args: object, **_kwargs: object):
        raise IsolationError("network and DNS are disabled in the selection harness")

    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.socket.sendto = blocked
    socket.getaddrinfo = blocked
    socket.gethostbyname = blocked
    socket.gethostbyname_ex = blocked
    socket.gethostbyaddr = blocked
    process_originals: dict[str, Any] = {"subprocess.Popen": subprocess.Popen}
    subprocess.Popen = blocked  # type: ignore[assignment]
    guarded_os_calls = (
        "fork", "forkpty", "popen", "posix_spawn", "posix_spawnp", "spawnl", "spawnle",
        "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "system",
    )
    for name in guarded_os_calls:
        if hasattr(os, name):
            process_originals[f"os.{name}"] = getattr(os, name)
            setattr(os, name, blocked)
    try:
        yield
    finally:
        socket.create_connection = originals["create_connection"]
        socket.socket.connect = originals["connect"]
        socket.socket.connect_ex = originals["connect_ex"]
        socket.socket.sendto = originals["sendto"]
        socket.getaddrinfo = originals["getaddrinfo"]
        socket.gethostbyname = originals["gethostbyname"]
        socket.gethostbyname_ex = originals["gethostbyname_ex"]
        socket.gethostbyaddr = originals["gethostbyaddr"]
        subprocess.Popen = process_originals["subprocess.Popen"]  # type: ignore[assignment]
        for name in guarded_os_calls:
            original = process_originals.get(f"os.{name}")
            if original is not None:
                setattr(os, name, original)


def stable_file_record(source: Path, destination: Path, retries: int = 8) -> dict[str, Any]:
    """Return the stable file record."""
    for attempt in range(retries):
        before = production_sim.stat_identity(source)
        payload = source.read_bytes()
        after = production_sim.stat_identity(source)
        if before == after and len(payload) == before[1]:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            return {
                "source_path": str(source.resolve()),
                "snapshot_path": str(destination),
                "size": before[1],
                "mtime_ns": before[2],
                "ctime_ns": before[3],
                "inode": before[0],
                "sha256": hashlib.sha256(payload).hexdigest(),
                "stable_read_attempts": attempt + 1,
            }
        time.sleep(0.05)
    raise HarnessError(f"could not obtain stable read of {source}")


def stable_json_object(source: Path, retries: int = 8) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the stable JSON object."""
    for attempt in range(retries):
        before = production_sim.stat_identity(source)
        payload = source.read_bytes()
        after = production_sim.stat_identity(source)
        if before != after or len(payload) != before[1]:
            time.sleep(0.05)
            continue
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise HarnessError(f"{source.name} must be an object")
        return value, {
            "source_size": before[1],
            "source_mtime_ns": before[2],
            "source_ctime_ns": before[3],
            "source_inode": before[0],
            "source_sha256": hashlib.sha256(payload).hexdigest(),
            "stable_read_attempts": attempt + 1,
        }
    raise HarnessError(f"could not obtain stable read of {source}")


def effective_selection_config() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the effective selection config."""
    raw, record = stable_json_object(ROOT / "mrsMThatcher.local.json")
    return {key: raw[key] for key in SELECTION_CONFIG_KEYS if key in raw}, record


def write_eligible_quotes(snapshot: Path) -> tuple[list[str], set[str]]:
    """Write the attribution-eligible regular-post source subset."""
    packets = json.loads((snapshot / "research_packets.json").read_text(encoding="utf-8"))
    status = json.loads((snapshot / "final_research_status.json").read_text(encoding="utf-8"))
    items = packets.get("items") if isinstance(packets, dict) else None
    unresolved = set(status.get("unresolved_quote_ids") or [])
    if not isinstance(items, dict) or len(items) != EXPECTED_COMPLETED:
        raise HarnessError(f"completed packet count must be {EXPECTED_COMPLETED}")
    if len(unresolved) != EXPECTED_UNRESOLVED or set(items) & unresolved:
        raise HarnessError("completed/unresolved quotation partition is invalid")
    source_lines = (snapshot / "mrsMThatcher.txt").read_text(encoding="utf-8").splitlines()
    selected: list[str] = []
    seen: set[str] = set()
    attribution_seen: set[str] = set()
    for raw in source_lines:
        text = raw.rstrip()
        if not text:
            continue
        quote_id = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if quote_id in items and quote_id not in seen:
            packet = items[quote_id]
            if str(packet.get("quote_text") or "") != text or str(packet.get("quote_id") or quote_id) != quote_id:
                raise HarnessError(f"immutable quote identity mismatch: {quote_id}")
            if not packet_is_attributed_to_margaret_thatcher(packet):
                continue
            attribution_seen.add(quote_id)
            selected.append(text)
            seen.add(quote_id)
    missing_completed = set(items).difference(attribution_seen)
    tombstone_path = snapshot / "attribution_exclusion_tombstones.json"
    if not tombstone_path.is_file():
        tombstone_path = ROOT / "semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/attribution_exclusion_tombstones.json"
    if not tombstone_path.is_file():
        raise HarnessError("attribution exclusion tombstones are unavailable")
    tombstones = json.loads(tombstone_path.read_text(encoding="utf-8"))
    excluded = {str(row.get("quote_id") or "") for row in tombstones.get("records", []) if isinstance(row, dict)}
    if len(excluded) != 13 or not excluded <= missing_completed or len(missing_completed) != EXPECTED_ATTRIBUTION_EXCLUDED:
        raise HarnessError(
            "active source attribution exclusions do not reconcile to 13 historical tombstones plus three corrected records"
        )
    if len(attribution_seen) != EXPECTED_ATTRIBUTION_ACTIVE:
        raise HarnessError(
            f"attribution-eligible active quote count must be {EXPECTED_ATTRIBUTION_ACTIVE}"
        )
    if len(selected) != EXPECTED_ACTIVE:
        raise HarnessError(f"eligible active quote count must be {EXPECTED_ACTIVE}")
    (snapshot / "eligible_quotes.txt").write_text("".join(f"{text}\n" for text in selected), encoding="utf-8")
    return selected, unresolved


def source_snapshot(run_dir: Path) -> dict[str, Any]:
    """Return the source snapshot."""
    run_dir.mkdir(parents=True, exist_ok=True)
    snapshot = run_dir / SNAPSHOT_DIRNAME
    if (snapshot / "manifest.json").is_file():
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        verify_snapshot(snapshot, manifest)
        return manifest
    if snapshot.exists():
        raise HarnessError(f"incomplete source snapshot exists: {snapshot}")
    snapshot.mkdir(parents=True)
    records: dict[str, dict[str, Any]] = {}
    barriers = [ROOT / name for name in RECEIPT_BARRIERS]
    if any(path.exists() for path in barriers):
        raise HarnessError("a production posting/reply receipt is pending; snapshot deferred")

    mutable_paths = [ROOT / name for name in MUTABLE_SOURCES]
    mutable_payload = production_sim.read_consistent_group(mutable_paths, retries=12, quiescence_seconds=0.1)
    if any(path.exists() for path in barriers):
        raise HarnessError("a production receipt appeared during stable snapshot capture")
    production_sim.validate_mutable_snapshot(mutable_payload)
    for source, payload in mutable_payload.items():
        destination = snapshot / source.name
        destination.write_bytes(payload)
        stat = source.stat()
        records[source.name] = {
            "source_path": str(source.resolve()),
            "snapshot_path": str(destination),
            "size": len(payload),
            "mtime_ns": stat.st_mtime_ns,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "mutable_source": True,
            "stable_group_read": True,
        }

    destination_names = {
        "semantic_alignment_research/quote_research_full_001/research_packets.json": "research_packets.json",
        "semantic_alignment_research/quote_research_full_001/final_unresolved/final_research_status.json": "final_research_status.json",
        "semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/attribution_exclusion_tombstones.json": "attribution_exclusion_tombstones.json",
        "semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json": "material_veto_v3_shadow_manifest.json",
        "semantic_alignment/quote_image_semantic_veto.py": "code/semantic_alignment_quote_image_semantic_veto.py",
    }
    for relative in IMMUTABLE_SOURCES:
        source = ROOT / relative
        if not source.is_file():
            raise HarnessError(f"required source missing: {source}")
        destination = snapshot / destination_names.get(relative, relative)
        record = stable_file_record(source, destination)
        record["mutable_source"] = False
        records[relative] = record

    config, config_source_record = effective_selection_config()
    atomic_write_json(snapshot / "mrsMThatcher.local.json", config)
    records["mrsMThatcher.local.json"] = {
        "source_path": str((ROOT / "mrsMThatcher.local.json").resolve()),
        "snapshot_path": str(snapshot / "mrsMThatcher.local.json"),
        "sha256": sha256_file(snapshot / "mrsMThatcher.local.json"),
        "size": (snapshot / "mrsMThatcher.local.json").stat().st_size,
        "sanitised_selection_keys_only": True,
        "mutable_source": True,
        **config_source_record,
    }

    for source_dir, destination_name, pattern in (
        (ROOT / "images", "images", "t*"),
        (ROOT / "generated_review_approved_images", "generated_images", "*.png"),
    ):
        destination_dir = snapshot / destination_name
        destination_dir.mkdir()
        for source in sorted(source_dir.glob(pattern)):
            if not source.is_file():
                continue
            key = f"{source_dir.name}/{source.name}"
            records[key] = stable_file_record(source, destination_dir / source.name)
            records[key]["mutable_source"] = False

    log_dir = snapshot / "logs"
    log_dir.mkdir()
    for source in sorted(ROOT.glob("mrsMThatcher*.log*")):
        if not source.is_file() or "selftest" in source.name.lower():
            continue
        records[f"logs/{source.name}"] = stable_file_record(source, log_dir / source.name)
        records[f"logs/{source.name}"]["mutable_source"] = True

    eligible, unresolved = write_eligible_quotes(snapshot)
    veto = json.loads((snapshot / "material_veto_v3_shadow_manifest.json").read_text(encoding="utf-8"))
    expected_veto = (610, 91, 22_066, 21_938, 128)
    actual_veto = tuple(veto.get(key) for key in ("quote_count", "image_count", "pair_count", "allow_count", "veto_count"))
    if actual_veto != expected_veto:
        raise HarnessError(f"corrected semantic-veto manifest counts differ: {actual_veto}")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "harness_version": HARNESS_VERSION,
        "created_at": utc_now(),
        "project_dir": str(ROOT),
        "snapshot_dir": str(snapshot),
        "files": records,
        "completed_quote_count": len(eligible),
        "historical_completed_packet_count": EXPECTED_COMPLETED,
        "attribution_eligible_quote_count": EXPECTED_ATTRIBUTION_ACTIVE,
        "attribution_excluded_count": EXPECTED_ATTRIBUTION_EXCLUDED,
        "unresolved_quote_count": len(unresolved),
        "unresolved_quote_ids": sorted(unresolved),
        "eligible_quote_id_set_sha256": hashlib.sha256(
            "\n".join(sorted(hashlib.sha256(text.encode("utf-8")).hexdigest() for text in eligible)).encode()
        ).hexdigest(),
        "veto_manifest_counts": dict(zip(("quote_count", "image_count", "pair_count", "allow_count", "veto_count"), actual_veto)),
        "stable_read_protocol": "stat/read/stat plus matching two-pass group snapshot for mutable state",
        "production_files_opened_for_writing": [],
    }
    atomic_write_json(snapshot / "manifest.json", manifest)
    atomic_write_json(run_dir / "source_snapshot_manifest.json", manifest)
    for path in sorted(snapshot.rglob("*"), reverse=True):
        if path.is_file():
            path.chmod(0o444)
        elif path.is_dir():
            path.chmod(0o555)
    snapshot.chmod(0o555)
    return manifest


def verify_snapshot(snapshot: Path, manifest: dict[str, Any]) -> None:
    """Verify snapshot."""
    for key, record in manifest.get("files", {}).items():
        destination = Path(str(record.get("snapshot_path") or ""))
        if not destination.is_absolute():
            destination = snapshot / destination
        if not destination.is_file() or sha256_file(destination) != record.get("sha256"):
            raise HarnessError(f"snapshot hash mismatch: {key}")
    if int(manifest.get("completed_quote_count", 0)) != EXPECTED_ACTIVE:
        raise HarnessError(f"snapshot active completed quote count is not {EXPECTED_ACTIVE}")
    if int(manifest.get("historical_completed_packet_count", EXPECTED_COMPLETED)) != EXPECTED_COMPLETED:
        raise HarnessError("snapshot historical completed packet count is not 626")
    if int(manifest.get("unresolved_quote_count", 0)) != EXPECTED_UNRESOLVED:
        raise HarnessError("snapshot unresolved quote count is not six")


def initialise_database(path: Path) -> sqlite3.Connection:
    """Initialise database."""
    connection = sqlite3.connect(path, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, mode TEXT NOT NULL, parameters_json TEXT NOT NULL,
            status TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
            wall_seconds REAL, cpu_seconds REAL, peak_rss_kib INTEGER
        );
        CREATE TABLE IF NOT EXISTS source_snapshots (
            source_key TEXT PRIMARY KEY, source_path TEXT NOT NULL, snapshot_path TEXT NOT NULL,
            sha256 TEXT NOT NULL, size INTEGER, mtime_ns INTEGER, mutable_source INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS seasonal_states (
            signature TEXT PRIMARY KEY, representative_timestamp TEXT NOT NULL,
            state_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS state_profiles (
            profile_name TEXT PRIMARY KEY, profile_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS simulation_events (
            event_key TEXT PRIMARY KEY, mode TEXT NOT NULL, simulated_timestamp TEXT NOT NULL,
            simulated_year INTEGER, seed INTEGER, event_index INTEGER, state_profile TEXT,
            state_hash TEXT NOT NULL, seasonal_signature TEXT NOT NULL,
            quote_id TEXT, quote_text TEXT, quote_weight REAL, quote_excluded INTEGER NOT NULL DEFAULT 0,
            active_rules_json TEXT NOT NULL, production_image TEXT, production_image_hash TEXT,
            production_source TEXT, production_score REAL, production_components_json TEXT,
            editorial_image TEXT, editorial_score REAL, editorial_rank INTEGER,
            veto_status TEXT, veto_reasons_json TEXT, semantic_image TEXT,
            combined_image TEXT, allowed_alternative TEXT, editorial_allowed_alternative TEXT,
            alternative_score_loss REAL, editorial_alternative_score_loss REAL,
            eligible_candidate_count INTEGER NOT NULL DEFAULT 0, allowed_candidate_count INTEGER NOT NULL DEFAULT 0,
            vetoed_candidate_count INTEGER NOT NULL DEFAULT 0, unknown_candidate_count INTEGER NOT NULL DEFAULT 0,
            no_safe_image INTEGER NOT NULL DEFAULT 0, generated_spacing_allowed INTEGER,
            quote_cycle_reset INTEGER NOT NULL DEFAULT 0, image_cycle_reset INTEGER NOT NULL DEFAULT 0,
            repeated_quote INTEGER NOT NULL DEFAULT 0, repeated_image INTEGER NOT NULL DEFAULT 0,
            reconstruction_complete INTEGER NOT NULL DEFAULT 1, detail_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS simulation_mode_idx ON simulation_events(mode);
        CREATE INDEX IF NOT EXISTS simulation_veto_idx ON simulation_events(veto_status);
        CREATE INDEX IF NOT EXISTS simulation_season_idx ON simulation_events(seasonal_signature);
        CREATE TABLE IF NOT EXISTS candidate_score_sets (
            event_key TEXT PRIMARY KEY REFERENCES simulation_events(event_key) ON DELETE CASCADE,
            encoding TEXT NOT NULL, candidate_count INTEGER NOT NULL, payload BLOB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS historical_replay_events (
            event_key TEXT PRIMARY KEY, timestamp TEXT, quote_id TEXT, production_image TEXT,
            veto_status TEXT, reconstruction_complete INTEGER NOT NULL, detail_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS invariant_checks (
            check_key TEXT PRIMARY KEY, passed INTEGER NOT NULL, detail_json TEXT NOT NULL,
            checked_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    connection.commit()
    return connection


def record_snapshot_rows(connection: sqlite3.Connection, manifest: dict[str, Any]) -> None:
    """Record snapshot rows."""
    for key, record in manifest.get("files", {}).items():
        connection.execute(
            "INSERT OR REPLACE INTO source_snapshots VALUES (?,?,?,?,?,?,?)",
            (
                key, str(record.get("source_path") or ""), str(record.get("snapshot_path") or ""),
                str(record.get("sha256") or ""), int(record.get("size") or 0),
                int(record.get("mtime_ns") or 0), int(bool(record.get("mutable_source"))),
            ),
        )
    connection.commit()


@dataclass
class HarnessContext:
    """Represent harness context data."""
    run_dir: Path
    snapshot: Path
    bot: Any
    veto: ShadowRuntime
    packets: dict[str, dict[str, Any]]
    quote_text: dict[str, str]
    quote_line: dict[str, int]
    connection: sqlite3.Connection
    analysis_aliases: dict[str, str] = field(default_factory=dict)
    seasonal_cache: dict[str, dict[str, Any]] = field(default_factory=dict)


def import_bot(run_dir: Path) -> Any:
    """Return the import bot."""
    import_sentinel = Path("/tmp") / f"mrsMThatcher-harness-import-{os.getpid()}"
    if import_sentinel.exists():
        raise IsolationError(f"import sentinel unexpectedly exists: {import_sentinel}")
    env = {
        "MRS_TEST_MODE": "1",
        "MRS_BASE_DIR": str(import_sentinel),
        "MRS_LOG_FILE": str(import_sentinel / "harness-import.log"),
        "LOG_LEVEL": "CRITICAL",
        "X_API_BASE_URL": "http://127.0.0.1:9",
        "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
        "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
        "X_CONSUMER_KEY": "offline-disabled",
        "X_CONSUMER_SECRET": "offline-disabled",
        "X_ACCESS_TOKEN": "offline-disabled",
        "X_ACCESS_SECRET": "offline-disabled",
        "X_MY_USER_ID": "0",
        "XAI_API_KEY": "offline-disabled",
        "X_BEARER_TOKEN": "offline-disabled",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        bot = importlib.import_module("mrsMThatcher2")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    if import_sentinel.exists():
        raise IsolationError(f"production import created mutable state outside the run directory: {import_sentinel}")
    production_sim.configure_session_logging(bot, run_dir / "harness.log")
    bot.log.setLevel("ERROR")
    return bot


def install_immutable_score_caches(bot: Any) -> None:
    """Install immutable score caches."""
    original_sha = bot.current_image_sha256
    original_idf = bot.build_image_topic_idf
    original_score = bot.score_image_for_quote
    sha_cache: dict[str, str] = {}
    idf_cache: dict[int, Any] = {}
    score_cache: dict[tuple[int, int], tuple[float, dict[str, Any], bool]] = {}
    counters: Counter[str] = Counter()
    for function_name, counter_name in (
        ("load_quote_analysis", "quote_analysis"),
        ("load_image_analysis", "image_analysis"),
    ):
        if not hasattr(bot, function_name):
            continue
        original_loader = getattr(bot, function_name)
        parsed_cache: list[Any] = []

        def cached_loader(
            _original=original_loader,
            _cache=parsed_cache,
            _counter=counter_name,
        ):
            if not _cache:
                _cache.append(_original())
                counters[f"{_counter}_misses"] += 1
            else:
                counters[f"{_counter}_hits"] += 1
            return _cache[0]

        setattr(bot, function_name, cached_loader)

    def cached_sha(path: str) -> str:
        key = str(path)
        if key not in sha_cache:
            sha_cache[key] = original_sha(path)
            counters["sha_misses"] += 1
        else:
            counters["sha_hits"] += 1
        return sha_cache[key]

    def cached_idf(image_analysis: dict[str, Any]) -> Any:
        key = id(image_analysis)
        if key not in idf_cache:
            idf_cache[key] = original_idf(image_analysis)
            counters["idf_misses"] += 1
        else:
            counters["idf_hits"] += 1
        return idf_cache[key]

    def cached_score(
        quote_analysis: dict[str, Any] | None,
        image_analysis: dict[str, Any] | None,
        idf: Any,
    ) -> tuple[float, dict[str, Any], bool]:
        key = (id(quote_analysis), id(image_analysis))
        if key not in score_cache:
            score, components, eligible = original_score(quote_analysis, image_analysis, idf)
            score_cache[key] = (float(score), dict(components), bool(eligible))
            counters["score_misses"] += 1
        else:
            counters["score_hits"] += 1
        score, components, eligible = score_cache[key]
        return score, dict(components), eligible

    bot.current_image_sha256 = cached_sha
    bot.build_image_topic_idf = cached_idf
    bot.score_image_for_quote = cached_score
    bot._HARNESS_UNCACHED_SCORE_IMAGE_FOR_QUOTE = original_score
    bot._HARNESS_IMMUTABLE_CACHE_COUNTERS = counters
    bot._HARNESS_IMMUTABLE_SCORE_CACHE = score_cache
    if hasattr(bot, "quote_candidate_weight"):
        original_quote_weight = bot.quote_candidate_weight
        weight_cache: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}

        def cached_quote_weight(
            analysis: dict[str, Any] | None,
            *,
            today_mm_dd: str,
        ) -> tuple[float, dict[str, Any]]:
            key = (id(analysis), str(today_mm_dd))
            if key not in weight_cache:
                weight, status = original_quote_weight(analysis, today_mm_dd=today_mm_dd)
                weight_cache[key] = (float(weight), dict(status))
                counters["quote_weight_misses"] += 1
            else:
                counters["quote_weight_hits"] += 1
            weight, status = weight_cache[key]
            return weight, dict(status)

        bot.quote_candidate_weight = cached_quote_weight
    if hasattr(bot, "load_quote_lines_and_analysis"):
        original_quote_inputs = bot.load_quote_lines_and_analysis
        quote_inputs_cache: list[tuple[list[str], dict[str, Any] | None]] = []

        def cached_quote_inputs() -> tuple[list[str], dict[str, Any] | None, str]:
            if not quote_inputs_cache:
                lines, analysis, _today = original_quote_inputs()
                quote_inputs_cache.append((lines, analysis))
                counters["quote_input_misses"] += 1
            else:
                counters["quote_input_hits"] += 1
            lines, analysis = quote_inputs_cache[0]
            return lines, analysis, bot.current_datetime().strftime("%m-%d")

        bot.load_quote_lines_and_analysis = cached_quote_inputs
    if hasattr(bot, "current_quote_hashes_by_line"):
        original_quote_hashes = bot.current_quote_hashes_by_line
        quote_hash_cache: dict[int, dict[int, str]] = {}

        def cached_quote_hashes(lines: list[str]) -> dict[int, str]:
            key = id(lines)
            if key not in quote_hash_cache:
                quote_hash_cache[key] = original_quote_hashes(lines)
                counters["quote_hash_misses"] += 1
            else:
                counters["quote_hash_hits"] += 1
            return dict(quote_hash_cache[key])

        bot.current_quote_hashes_by_line = cached_quote_hashes
    if hasattr(bot, "current_image_paths"):
        original_image_paths = bot.current_image_paths
        image_paths_cache: list[list[str]] = []

        def cached_image_paths() -> list[str]:
            if not image_paths_cache:
                image_paths_cache.append(original_image_paths())
                counters["image_path_misses"] += 1
            else:
                counters["image_path_hits"] += 1
            return list(image_paths_cache[0])

        bot.current_image_paths = cached_image_paths
    if hasattr(bot, "quote_text_hash"):
        original_quote_text_hash = bot.quote_text_hash
        quote_text_hash_cache: dict[str, str] = {}

        def cached_quote_text_hash(text: str) -> str:
            key = str(text)
            if key not in quote_text_hash_cache:
                quote_text_hash_cache[key] = original_quote_text_hash(text)
                counters["quote_text_hash_misses"] += 1
            else:
                counters["quote_text_hash_hits"] += 1
            return quote_text_hash_cache[key]

        bot.quote_text_hash = cached_quote_text_hash
    if hasattr(bot, "quote_metadata_for_hash"):
        original_quote_metadata = bot.quote_metadata_for_hash
        quote_metadata_cache: dict[tuple[int, str, str], Any] = {}

        def cached_quote_metadata(
            quote_analysis: dict[str, Any] | None,
            quote_hash: str,
            text: str = "",
        ) -> dict[str, Any] | None:
            key = (id(quote_analysis), str(quote_hash), str(text))
            if key not in quote_metadata_cache:
                quote_metadata_cache[key] = original_quote_metadata(quote_analysis, quote_hash, text)
                counters["quote_metadata_misses"] += 1
            else:
                counters["quote_metadata_hits"] += 1
            return quote_metadata_cache[key]

        bot.quote_metadata_for_hash = cached_quote_metadata
    if hasattr(bot, "original_editorial_shadow_score"):
        original_editorial_score = bot.original_editorial_shadow_score
        editorial_score_cache: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}

        def cached_editorial_score(
            quote_analysis: dict[str, Any] | None,
            editorial: dict[str, Any] | None,
        ) -> tuple[float, dict[str, Any]]:
            key = (id(quote_analysis), id(editorial))
            if key not in editorial_score_cache:
                adjustment, detail = original_editorial_score(quote_analysis, editorial)
                editorial_score_cache[key] = (float(adjustment), copy.deepcopy(detail))
                counters["editorial_score_misses"] += 1
            else:
                counters["editorial_score_hits"] += 1
            adjustment, detail = editorial_score_cache[key]
            return adjustment, copy.deepcopy(detail)

        bot.original_editorial_shadow_score = cached_editorial_score


def load_context(run_dir: Path) -> HarnessContext:
    """Load context."""
    snapshot = run_dir / SNAPSHOT_DIRNAME
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    verify_snapshot(snapshot, manifest)
    bot = import_bot(run_dir)
    production_sim.apply_snapshot_config(bot, snapshot)
    private = run_dir / "runtime" / "selector"
    private.mkdir(parents=True, exist_ok=True)
    production_sim.configure_snapshot_paths(bot, snapshot, private)
    completed_research_hash_cache = frozenset(bot.completed_research_quote_hashes())
    bot.completed_research_quote_hashes = lambda: set(completed_research_hash_cache)
    install_immutable_score_caches(bot)
    bot.LINES_FILE = snapshot / "eligible_quotes.txt"
    bot.QUOTE_ANALYSIS_OVERRIDES_FILE = snapshot / "quote_analysis_overrides.json"
    bot._QUOTE_IMAGE_SEMANTIC_VETO_SHADOW = None
    production_sim.install_hard_guards(bot, production_sim.PrivateWriter(run_dir))
    config = {
        "enabled": True,
        "mode": "shadow",
        "manifest_path": str(snapshot / "material_veto_v3_shadow_manifest.json"),
        "fail_open": True,
        "record_best_allowed_alternative": True,
        "maximum_shadow_history": 100,
    }
    veto = ShadowRuntime.load(snapshot, config, verify_source_hashes=False, enable_history=False)
    if not veto.available or len(veto.pairs or {}) != 22_066:
        raise HarnessError(f"corrected semantic-veto manifest unavailable: {veto.reason}")
    packets = json.loads((snapshot / "research_packets.json").read_text(encoding="utf-8"))["items"]
    lines = (snapshot / "eligible_quotes.txt").read_text(encoding="utf-8").splitlines()
    quote_text = {hashlib.sha256(text.encode("utf-8")).hexdigest(): text for text in lines}
    quote_line = {hashlib.sha256(text.encode("utf-8")).hexdigest(): index for index, text in enumerate(lines)}
    connection = initialise_database(run_dir / DB_NAME)
    quote_analysis = bot.load_quote_analysis()
    analysis_items = quote_analysis.get("items", {}) if isinstance(quote_analysis, dict) else {}
    normalised: dict[str, list[str]] = defaultdict(list)
    for analysis_id, row in analysis_items.items():
        if isinstance(row, dict) and isinstance(row.get("analysis"), dict):
            normalised[" ".join(str(row.get("text") or "").split())].append(str(analysis_id))
    aliases: dict[str, str] = {}
    for quote_id, text in quote_text.items():
        row = analysis_items.get(quote_id)
        if isinstance(row, dict) and isinstance(row.get("analysis"), dict):
            continue
        matches = normalised.get(" ".join(text.split()), [])
        if len(matches) != 1:
            raise HarnessError(
                f"completed quote lacks one unique production-analysis twin: {quote_id} matches={matches}"
            )
        aliases[quote_id] = matches[0]
    if len(aliases) != 5:
        raise HarnessError(f"expected five whitespace-only production-analysis aliases, found {len(aliases)}")
    connection.execute(
        "INSERT OR REPLACE INTO invariant_checks VALUES (?,?,?,?)",
        (
            "completed_quote_analysis_coverage",
            1,
            json.dumps({"exact": EXPECTED_ACTIVE - len(aliases), "whitespace_alias": aliases}, sort_keys=True),
            utc_now(),
        ),
    )
    connection.commit()
    return HarnessContext(run_dir, snapshot, bot, veto, packets, quote_text, quote_line, connection, aliases)


def local_datetime(epoch: int) -> datetime:
    """Return the local datetime."""
    return datetime.fromtimestamp(epoch, TZ)


def epoch_for(year: int, month: int, day: int, hour: int, minute: int) -> int:
    """Return the epoch for."""
    return int(datetime(year, month, day, hour, minute, tzinfo=TZ).timestamp())


def date_range(year: int) -> Iterator[date]:
    """Yield date range values."""
    current = date(year, 1, 1)
    end = date(year + 1, 1, 1)
    while current < end:
        yield current
        current += timedelta(days=1)


def dst_transition_observations(year: int) -> list[datetime]:
    """Minute-before/at/after observations for Europe/London UTC offset changes."""
    current = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    previous_offset = current.astimezone(TZ).utcoffset()
    transitions: list[datetime] = []
    while current < end:
        current += timedelta(minutes=30)
        offset = current.astimezone(TZ).utcoffset()
        if offset != previous_offset:
            transitions.extend(
                (current - timedelta(minutes=1), current, current + timedelta(minutes=1))
            )
            previous_offset = offset
    return [value.astimezone(TZ) for value in transitions]


def seasonal_state(ctx: HarnessContext, when: datetime) -> dict[str, Any]:
    """Return the seasonal state."""
    bot = ctx.bot
    mm_dd = when.strftime("%m-%d")
    cached = ctx.seasonal_cache.get(mm_dd)
    if cached is not None:
        return {**cached, "timestamp": when.isoformat()}
    image_analysis = bot.load_image_analysis()
    quote_boosts: dict[str, float] = {}
    quote_exclusions: list[str] = []
    active: list[str] = []
    for quote_id in sorted(ctx.quote_text):
        analysis, _analysis_source, _analysis_id = quote_analysis_for_completed_id(ctx, quote_id)
        _weight, status = bot.quote_candidate_weight(analysis, today_mm_dd=mm_dd)
        if status.get("hard_excluded"):
            quote_exclusions.append(quote_id)
        if status.get("in_window"):
            multiplier = {
                "soft": float(bot.QUOTE_SEASON_SOFT_WEIGHT),
                "strong": float(bot.QUOTE_SEASON_STRONG_WEIGHT),
                "date_specific": float(bot.QUOTE_SEASON_DATE_SPECIFIC_WEIGHT),
            }.get(str(status.get("relevance")), 1.0)
            quote_boosts[quote_id] = multiplier
            active.append(f"quote:{quote_id}:{status.get('relevance')}")
    image_exclusions: list[str] = []
    path_index = image_analysis.get("path_index", {}) if isinstance(image_analysis, dict) else {}
    for basename, digest in sorted(path_index.items()):
        item = image_analysis.get("items", {}).get(digest, {})
        analysis = item.get("analysis") if isinstance(item, dict) else None
        if isinstance(analysis, dict) and bot.image_is_out_of_season(analysis, mm_dd):
            image_exclusions.append(str(basename))
            active.append(f"image:{basename}:excluded")
    signature_basis = {
        "quote_boosts": quote_boosts,
        "quote_exclusions": quote_exclusions,
        "image_boosts": {},
        "image_exclusions": image_exclusions,
    }
    result = {
        "timestamp": when.isoformat(),
        "timezone": TZ_NAME,
        "active_seasonal_rules": sorted(active),
        **signature_basis,
        "seasonal_state_signature": hash_value(signature_basis),
    }
    ctx.seasonal_cache[mm_dd] = result
    return result


def configured_boundaries(ctx: HarnessContext, years: Sequence[int]) -> list[dict[str, Any]]:
    """Return the configured boundaries."""
    windows: set[tuple[str, str, str]] = set()
    for quote_id in sorted(ctx.quote_text):
        analysis, _analysis_source, _analysis_id = quote_analysis_for_completed_id(ctx, quote_id)
        analysis = analysis or {}
        seasonality = analysis.get("seasonality") if isinstance(analysis.get("seasonality"), dict) else {}
        for window in seasonality.get("preferred_windows", []) or []:
            if isinstance(window, dict):
                windows.add((str(window.get("start_mm_dd") or ""), str(window.get("end_mm_dd") or ""), f"quote:{quote_id}"))
    image_analysis = ctx.bot.load_image_analysis()
    for basename, digest in sorted((image_analysis.get("path_index") or {}).items()):
        item = (image_analysis.get("items") or {}).get(digest, {})
        analysis = item.get("analysis") if isinstance(item, dict) else {}
        seasonality = analysis.get("seasonality") if isinstance(analysis, dict) and isinstance(analysis.get("seasonality"), dict) else {}
        if not seasonality.get("avoid_outside_season_or_occasion"):
            continue
        occasions = {str(value).lower() for value in seasonality.get("occasions", [])}
        visible = str(seasonality.get("visible_season") or "").lower()
        if "christmas" in occasions:
            windows.add(("12-10", "12-28", f"image:{basename}:christmas"))
        elif visible == "winter":
            windows.add(("12-01", "02-28", f"image:{basename}:winter"))
        elif visible == "spring":
            windows.add(("03-01", "05-31", f"image:{basename}:spring"))
        elif visible == "summer":
            windows.add(("06-01", "08-31", f"image:{basename}:summer"))
        elif visible == "autumn":
            windows.add(("09-01", "11-30", f"image:{basename}:autumn"))
    rows: list[dict[str, Any]] = []
    for year in years:
        for start, end, rule in sorted(windows):
            for kind, mm_dd in (("start", start), ("end", end)):
                try:
                    month, day = (int(value) for value in mm_dd.split("-"))
                    boundary = date(year, month, day)
                except ValueError:
                    continue
                rows.append({"year": year, "rule": rule, "window_start": start, "window_end": end, "boundary_kind": kind, "boundary_date": boundary.isoformat()})
    return rows


def map_seasons(ctx: HarnessContext, years: Sequence[int]) -> dict[str, Any]:
    """Return the map seasons."""
    rows: list[dict[str, Any]] = []
    unique: dict[str, dict[str, Any]] = {}
    representative_times = ((0, 1), (11, 59), (12, 1), (23, 59))
    for year in years:
        for day in date_range(year):
            for hour, minute in representative_times:
                when = datetime.combine(day, datetime_time(hour, minute), TZ)
                state = seasonal_state(ctx, when)
                rows.append(state)
                unique.setdefault(state["seasonal_state_signature"], state)
    dst_observations: list[dict[str, Any]] = []
    for year in years:
        for when in dst_transition_observations(year):
            state = seasonal_state(ctx, when)
            rows.append(state)
            unique.setdefault(state["seasonal_state_signature"], state)
            dst_observations.append({
                "timestamp": when.isoformat(),
                "utc_offset_seconds": int((when.utcoffset() or timedelta()).total_seconds()),
                "seasonal_state_signature": state["seasonal_state_signature"],
            })
    boundaries = configured_boundaries(ctx, years)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "years": list(years),
        "timezone": TZ_NAME,
        "representative_times": ["00:01", "11:59", "12:01", "23:59"],
        "dst_transition_observations": dst_observations,
        "calendar_state_count": len(rows),
        "unique_seasonal_state_count": len(unique),
        "states": rows,
        "boundaries": boundaries,
    }
    atomic_write_json(ctx.run_dir / "seasonal_calendar_map.json", payload)
    atomic_write_json(ctx.run_dir / "unique_seasonal_states.json", {"schema_version": 1, "states": list(unique.values())})
    for signature, state in unique.items():
        ctx.connection.execute(
            "INSERT OR REPLACE INTO seasonal_states VALUES (?,?,?)",
            (signature, state["timestamp"], json.dumps(state, sort_keys=True)),
        )
    ctx.connection.commit()
    lines = [
        "# Seasonal Calendar Map", "", f"Years: {', '.join(str(year) for year in years)}", "",
        f"Calendar observations: {len(rows)}", f"Unique seasonal signatures: {len(unique)}",
        f"Configured boundary records: {len(boundaries)}", "",
        "Production seasonality is month/day based. Time-of-day and DST observations are retained, but do not create additional rule states.",
    ]
    atomic_write_text(ctx.run_dir / "seasonal_calendar_map.md", "\n".join(lines) + "\n")
    return payload


def load_seasonal_states(run_dir: Path) -> list[dict[str, Any]]:
    """Load seasonal states."""
    path = run_dir / "unique_seasonal_states.json"
    if not path.is_file():
        raise HarnessError("seasonal map is missing; run map-seasons first")
    rows = json.loads(path.read_text(encoding="utf-8")).get("states")
    if not isinstance(rows, list) or not rows:
        raise HarnessError("unique seasonal state file is empty")
    return sorted(rows, key=lambda row: (row["timestamp"], row["seasonal_state_signature"]))


def filtered_snapshot_state(ctx: HarnessContext) -> tuple[dict[str, Any], set[str], set[str]]:
    """Return the filtered snapshot state."""
    state, images_used, lines_used = production_sim.load_private_state(ctx.snapshot)
    lines_used &= production_quote_ids(ctx)
    image_names = {Path(path).name for path in ctx.bot.current_image_paths()}
    images_used &= image_names
    return state, images_used, lines_used


def production_quote_ids(ctx: HarnessContext) -> set[str]:
    """IDs used by the live selector/history, including proven whitespace aliases."""
    return (set(ctx.packets) - set(ctx.analysis_aliases)) | set(ctx.analysis_aliases.values())


def state_profiles(ctx: HarnessContext) -> dict[str, dict[str, Any]]:
    """Return the state profiles."""
    bot = ctx.bot
    current_state, current_images, current_lines = filtered_snapshot_state(ctx)
    image_names = sorted(Path(path).name for path in bot.current_image_paths())
    quote_ids = sorted(production_quote_ids(ctx))
    generated = [name for name in image_names if bot.generated_image_origin_quote_hash(name)]
    required = bot.generated_image_spacing_required()

    fresh = bot.default_state()
    fresh["original_regular_posts_since_generated_image"] = required
    profiles = {
        "fresh_state": {"state": fresh, "images_used": [], "lines_used": []},
        "current_production_snapshot": {
            "state": current_state, "images_used": sorted(current_images), "lines_used": sorted(current_lines),
        },
        "partially_used_image_cycle": {
            "state": copy.deepcopy(fresh),
            "images_used": image_names[: max(1, len(image_names) // 2)],
            "lines_used": quote_ids[: len(quote_ids) // 2],
        },
        "near_exhausted_image_cycle": {
            "state": {**copy.deepcopy(fresh), "last_regular_image_filename": image_names[-1] if image_names else None},
            "images_used": image_names[:-1],
            "lines_used": quote_ids[:-1],
        },
        "recent_generated_image_used": {
            "state": {
                **copy.deepcopy(fresh),
                "original_regular_posts_since_generated_image": 0,
                "last_regular_image_filename": generated[0] if generated else "generated-out-of-scope-placeholder.png",
            },
            "images_used": [generated[0]] if generated else [],
            "lines_used": [],
        },
        "generated_spacing_satisfied": {
            "state": {**copy.deepcopy(fresh), "original_regular_posts_since_generated_image": required},
            "images_used": [],
            "lines_used": [],
        },
    }
    for name, profile in profiles.items():
        ctx.connection.execute(
            "INSERT OR REPLACE INTO state_profiles VALUES (?,?)",
            (name, json.dumps(profile, sort_keys=True)),
        )
    ctx.connection.commit()
    return profiles


def quote_analysis_for_completed_id(
    ctx: HarnessContext,
    quote_id: str,
) -> tuple[dict[str, Any] | None, str, str]:
    """Return the quote analysis for completed ID."""
    analysis_id = ctx.analysis_aliases.get(quote_id, quote_id)
    analysis_source = "whitespace_alias" if analysis_id != quote_id else "exact"
    quote_analysis = ctx.bot.load_quote_analysis()
    row = (quote_analysis.get("items") or {}).get(analysis_id) if isinstance(quote_analysis, dict) else None
    analysis_text = str((row or {}).get("text") or ctx.quote_text[quote_id])
    analysis = ctx.bot.quote_metadata_for_hash(quote_analysis, analysis_id, analysis_text)
    return analysis, analysis_source, analysis_id


def quote_choice(ctx: HarnessContext, quote_id: str, epoch: int) -> dict[str, Any]:
    """Return the quote choice."""
    bot = ctx.bot
    text = ctx.quote_text[quote_id]
    analysis, analysis_source, analysis_id = quote_analysis_for_completed_id(ctx, quote_id)
    if analysis is None:
        raise HarnessError(f"production quote analysis missing for completed quote {quote_id}")
    weight, season_status = bot.quote_candidate_weight(
        analysis,
        today_mm_dd=local_datetime(epoch).strftime("%m-%d"),
    )
    return {
        "line_no": ctx.quote_line[quote_id],
        "text": text,
        "quote_hash": quote_id,
        "analysis": analysis,
        "analysis_source": analysis_source,
        "analysis_source_quote_id": analysis_id,
        "naturally_production_selectable": analysis_source == "exact",
        "weight": weight,
        "season_status": season_status,
    }


def canonicalise_production_quote(ctx: HarnessContext, quote: dict[str, Any]) -> dict[str, Any]:
    """Return the canonicalise production quote."""
    production_hash = str(quote.get("quote_hash") or "")
    if production_hash in ctx.packets:
        canonical_id = production_hash
    else:
        reverse_aliases = {analysis_id: quote_id for quote_id, analysis_id in ctx.analysis_aliases.items()}
        canonical_id = reverse_aliases.get(production_hash, "")
    if not canonical_id or canonical_id not in ctx.packets:
        raise HarnessError(f"production selector returned quote outside completed corpus: {production_hash}")
    result = dict(quote)
    result["production_quote_hash"] = production_hash
    result["quote_hash"] = canonical_id
    result["analysis_source"] = "production_selector"
    result["analysis_source_quote_id"] = production_hash
    result["naturally_production_selectable"] = True
    return result


def editorial_rows(ctx: HarnessContext, quote: dict[str, Any], candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the editorial rows."""
    editorial = ctx.bot.load_original_editorial_analysis()
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("image_source") != "original":
            continue
        basename = str(candidate.get("basename") or "")
        record = editorial.get(basename)
        if not isinstance(record, dict):
            continue
        adjustment, detail = ctx.bot.original_editorial_shadow_score(quote.get("analysis"), record)
        score = float(candidate.get("score") or 0.0)
        rows.append({
            **dict(candidate),
            "editorial_adjustment": float(adjustment),
            "editorial_score": score + float(adjustment),
            "editorial_detail": detail,
        })
    rows.sort(key=lambda row: (-float(row["editorial_score"]), str(row["basename"])))
    for rank, row in enumerate(rows, 1):
        row["editorial_rank"] = rank
    return rows


def candidate_veto_status(ctx: HarnessContext, quote_id: str, candidate: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """Return the candidate veto status."""
    if str(candidate.get("image_source") or "original") == "generated":
        return "out_of_scope_generated", None
    pair = ctx.veto.pair(ctx.veto.canonical_quote_id(quote_id), str(candidate.get("image_hash") or ""))
    return (str(pair.get("decision")) if pair else "unknown_unjudged"), pair


def highest_row(rows: Sequence[dict[str, Any]], key: str, preferred: str | None = None) -> dict[str, Any] | None:
    """Return the highest row."""
    if not rows:
        return None
    maximum = max(float(row.get(key) or 0.0) for row in rows)
    tied = [row for row in rows if float(row.get(key) or 0.0) == maximum]
    if preferred:
        retained = next((row for row in tied if str(row.get("basename")) == preferred), None)
        if retained:
            return retained
    return min(tied, key=lambda row: str(row.get("basename") or ""))


def evaluate_policies(
    ctx: HarnessContext,
    quote: dict[str, Any],
    production: dict[str, Any],
    candidates: Sequence[dict[str, Any]],
    *,
    tie_state: object | None = None,
) -> dict[str, Any]:
    """Evaluate policies."""
    quote_id = str(quote["quote_hash"])
    editorial = editorial_rows(ctx, quote, candidates)
    editorial_winner = editorial[0] if editorial else None
    production_editorial = next(
        (row for row in editorial if row["basename"] == production.get("basename")),
        None,
    )
    veto_event = ctx.veto.evaluate(
        quote_hash=quote_id,
        selected=production,
        candidates=candidates,
        quote_preview=str(quote.get("text") or ""),
        tie_break_state=tie_state,
    )
    classified: list[dict[str, Any]] = []
    editorial_by_name = {row["basename"]: row for row in editorial}
    for candidate in candidates:
        status, pair = candidate_veto_status(ctx, quote_id, candidate)
        editorial_row = editorial_by_name.get(candidate.get("basename"))
        classified.append({
            **dict(candidate),
            "veto_status": status,
            "veto_reason_codes": list((pair or {}).get("veto_reason_codes") or []),
            "editorial_adjustment": float((editorial_row or {}).get("editorial_adjustment") or 0.0),
            "editorial_score": float((editorial_row or {}).get("editorial_score") or candidate.get("score") or 0.0),
        })
    allowed = [row for row in classified if row["veto_status"] == "allow"]
    production_status = veto_event["shadow_status"]
    semantic_winner = production if production_status == "allow" else None
    if semantic_winner is None and allowed:
        alternative_name = veto_event.get("alternative_image_basename")
        if alternative_name:
            semantic_winner = next(
                (row for row in allowed if row.get("basename") == alternative_name),
                None,
            )
        if semantic_winner is None:
            best_score = max(float(row.get("score") or 0.0) for row in allowed)
            tied = [row for row in allowed if float(row.get("score") or 0.0) == best_score]
            if len(tied) == 1 or tie_state is None:
                semantic_winner = min(tied, key=lambda row: str(row.get("basename") or ""))
            else:
                local_rng = random.Random()
                local_rng.setstate(tie_state)
                semantic_winner = local_rng.choice(tied)
    combined_winner = highest_row(allowed, "editorial_score")
    production_score = float(production.get("score") or 0.0)
    semantic_loss = production_score - float(semantic_winner.get("score") or 0.0) if semantic_winner else None
    combined_loss = production_score - float(combined_winner.get("score") or 0.0) if combined_winner else None
    return {
        "editorial_winner": editorial_winner,
        "production_editorial": production_editorial,
        "veto_event": veto_event,
        "semantic_winner": semantic_winner,
        "combined_winner": combined_winner,
        "classified_candidates": classified,
        "semantic_score_loss": semantic_loss,
        "combined_score_loss": combined_loss,
    }


def select_forced_quote(
    ctx: HarnessContext,
    quote: dict[str, Any],
    state: dict[str, Any],
    images_used: set[str],
    *,
    seed: int,
) -> tuple[dict[str, Any], object]:
    """Select forced quote."""
    ctx.bot.random.setstate(random.Random(seed).getstate())
    tie_state = ctx.bot.random.getstate()
    selection = production_sim.select_policy_image_with_recovery(
        ctx.bot, quote, images_used, state, "production"
    )
    return selection, tie_state


def validate_production_observer_parity(ctx: HarnessContext) -> dict[str, Any]:
    """Validate production observer parity."""
    quote_id = sorted(ctx.quote_text)[0]
    epoch = epoch_for(2026, 7, 1, 12, 1)
    profile = state_profiles(ctx)["current_production_snapshot"]
    quote = quote_choice(ctx, quote_id, epoch)
    seed = 9_104_771

    baseline_state = copy.deepcopy(profile["state"])
    baseline_images = set(profile["images_used"])
    baseline, _baseline_tie = select_forced_quote(
        ctx, quote, baseline_state, baseline_images, seed=seed,
    )
    baseline_rng_after = production_sim.rng_state_encode(ctx.bot.random.getstate())

    observed_state = copy.deepcopy(profile["state"])
    observed_images = set(profile["images_used"])
    observed, tie_state = select_forced_quote(
        ctx, quote, observed_state, observed_images, seed=seed,
    )
    observed_rng_before = production_sim.rng_state_encode(ctx.bot.random.getstate())
    evaluate_policies(
        ctx, quote, observed["image"], observed["scored"], tie_state=tie_state,
    )
    observed_rng_after = production_sim.rng_state_encode(ctx.bot.random.getstate())

    def selection_identity(selection: dict[str, Any]) -> dict[str, Any]:
        return {
            "quote_id": str(selection["quote"]["quote_hash"]),
            "image": str(selection["image"]["basename"]),
            "score": float(selection["image"]["score"]),
            "candidate_order": [str(row["basename"]) for row in selection["scored"]],
            "candidate_scores": [float(row["score"]) for row in selection["scored"]],
            "selection_phase": str(selection["selection_phase"]),
        }

    checks = {
        "production_selection_identical": selection_identity(baseline) == selection_identity(observed),
        "production_rng_identical_before_observation": baseline_rng_after == observed_rng_before,
        "observer_did_not_consume_rng": observed_rng_before == observed_rng_after,
        "input_state_unchanged": baseline_state == observed_state == profile["state"],
        "image_use_state_unchanged": baseline_images == observed_images == set(profile["images_used"]),
    }
    detail = {
        "schema_version": 1,
        "quote_id": quote_id,
        "seed": seed,
        "checks": checks,
        "baseline": selection_identity(baseline),
        "observed": selection_identity(observed),
    }
    passed = all(checks.values())
    ctx.connection.execute(
        "INSERT OR REPLACE INTO invariant_checks VALUES (?,?,?,?)",
        ("production_observer_parity", int(passed), json.dumps(detail, sort_keys=True), utc_now()),
    )
    ctx.connection.commit()
    if not passed:
        raise HarnessError(f"production/observer parity failed: {checks}")
    return detail


def compressed_candidates(rows: Sequence[dict[str, Any]], *, full: bool) -> tuple[bytes, int]:
    """Return the compressed candidates."""
    ordered = sorted(rows, key=lambda row: (-float(row.get("score") or 0.0), str(row.get("basename") or "")))
    if not full:
        names = {
            str(row.get("basename"))
            for row in ordered[:10]
        }
        ordered = [row for row in ordered if str(row.get("basename")) in names]
    clean = [{
        "basename": row.get("basename"),
        "image_hash": row.get("image_hash"),
        "image_source": row.get("image_source"),
        "score": row.get("score"),
        "components": row.get("components", {}),
        "editorial_adjustment": row.get("editorial_adjustment"),
        "editorial_score": row.get("editorial_score"),
        "veto_status": row.get("veto_status"),
        "veto_reason_codes": row.get("veto_reason_codes", []),
    } for row in ordered]
    return zlib.compress(canonical_json(clean), level=6), len(rows)


def insert_event(
    ctx: HarnessContext,
    *,
    event_key: str,
    mode: str,
    when: datetime,
    year: int | None,
    seed: int | None,
    event_index: int | None,
    profile: str,
    state_hash: str,
    season: dict[str, Any],
    quote: dict[str, Any],
    selection: dict[str, Any] | None,
    policies: dict[str, Any] | None,
    quote_cycle_reset: bool = False,
    image_cycle_reset: bool = False,
    repeated_quote: bool = False,
    repeated_image: bool = False,
    generated_spacing_allowed: bool | None = None,
    full_candidates: bool = False,
    error: str | None = None,
) -> None:
    """Perform the insert event operation."""
    production = selection.get("image") if selection else None
    candidates = policies.get("classified_candidates", []) if policies else []
    editorial = policies.get("editorial_winner") if policies else None
    production_editorial = policies.get("production_editorial") if policies else None
    veto = policies.get("veto_event", {}) if policies else {}
    semantic = policies.get("semantic_winner") if policies else None
    combined = policies.get("combined_winner") if policies else None
    detail = {
        "selection_phase": selection.get("selection_phase") if selection else None,
        "selection_error": error,
        "season_status": quote.get("season_status"),
        "quote_analysis_source": quote.get("analysis_source", "production_selector"),
        "quote_analysis_source_quote_id": quote.get("analysis_source_quote_id", quote.get("quote_hash")),
        "naturally_production_selectable": quote.get("naturally_production_selectable", True),
        "editorial_adjustment": (production_editorial or {}).get("editorial_adjustment"),
        "editorial_reasons": (production_editorial or {}).get("editorial_detail"),
        "veto_event": veto,
        "quote_research": {
            "intended_argument": ctx.packets[str(quote["quote_hash"])].get("intended_argument"),
            "broader_principle": ctx.packets[str(quote["quote_hash"])].get("broader_principle"),
        },
    }
    values = (
        event_key, mode, when.isoformat(), year, seed, event_index, profile, state_hash,
        season["seasonal_state_signature"], quote.get("quote_hash"), quote.get("text"),
        float(quote.get("weight") or 0.0), int(bool(quote.get("season_status", {}).get("hard_excluded"))),
        json.dumps(season.get("active_seasonal_rules", []), sort_keys=True),
        (production or {}).get("basename"), (production or {}).get("image_hash"),
        (production or {}).get("image_source"), float((production or {}).get("score") or 0.0) if production else None,
        json.dumps((production or {}).get("components", {}), sort_keys=True),
        (editorial or {}).get("basename"), float((editorial or {}).get("editorial_score") or 0.0) if editorial else None,
        int((production_editorial or {}).get("editorial_rank")) if production_editorial else None,
        veto.get("shadow_status"), json.dumps(veto.get("veto_reason_codes", []), sort_keys=True),
        (semantic or {}).get("basename"), (combined or {}).get("basename"),
        veto.get("alternative_image_basename"), (combined or {}).get("basename"),
        policies.get("semantic_score_loss") if policies else None,
        policies.get("combined_score_loss") if policies else None,
        len(candidates), sum(row.get("veto_status") == "allow" for row in candidates),
        sum(row.get("veto_status") == "veto" for row in candidates),
        sum(row.get("veto_status") == "unknown_unjudged" for row in candidates),
        int(not semantic and bool(candidates)),
        int(generated_spacing_allowed) if generated_spacing_allowed is not None else None,
        int(quote_cycle_reset), int(image_cycle_reset), int(repeated_quote), int(repeated_image),
        int(error is None), json.dumps(detail, sort_keys=True, ensure_ascii=False),
    )
    ctx.connection.execute(
        "INSERT OR IGNORE INTO simulation_events VALUES (" + ",".join("?" for _ in values) + ")",
        values,
    )
    if candidates and ctx.connection.execute("SELECT changes()").fetchone()[0]:
        payload, stored_count = compressed_candidates(candidates, full=full_candidates)
        ctx.connection.execute(
            "INSERT OR REPLACE INTO candidate_score_sets VALUES (?,?,?,?)",
            (event_key, "zlib-json-v1", stored_count, payload),
        )


def run_timed(connection: sqlite3.Connection, run_id: str, mode: str, parameters: dict[str, Any]):
    """Run timed."""
    class Timer:
        started = time.perf_counter()
        usage = resource.getrusage(resource.RUSAGE_SELF)

        def finish(self, status: str = "completed") -> None:
            end_usage = resource.getrusage(resource.RUSAGE_SELF)
            connection.execute(
                "UPDATE runs SET status=?, completed_at=?, wall_seconds=?, cpu_seconds=?, peak_rss_kib=? WHERE run_id=?",
                (
                    status, utc_now(), time.perf_counter() - self.started,
                    (end_usage.ru_utime + end_usage.ru_stime) - (self.usage.ru_utime + self.usage.ru_stime),
                    int(end_usage.ru_maxrss), run_id,
                ),
            )
            connection.commit()

    connection.execute(
        "UPDATE runs SET status='interrupted', completed_at=? WHERE status='running' AND run_id=?",
        (utc_now(), run_id),
    )
    connection.execute(
        "INSERT OR REPLACE INTO runs(run_id,mode,parameters_json,status,started_at) VALUES (?,?,?,?,?)",
        (run_id, mode, json.dumps(parameters, sort_keys=True), "running", utc_now()),
    )
    connection.commit()
    return Timer()


def run_sweep(
    ctx: HarnessContext,
    *,
    profiles_requested: Sequence[str] | None = None,
    limit_signatures: int | None = None,
    limit_quotes: int | None = None,
    pilot: bool = False,
) -> dict[str, Any]:
    """Run sweep."""
    validate_production_observer_parity(ctx)
    seasons = load_seasonal_states(ctx.run_dir)
    if limit_signatures:
        seasons = seasons[:limit_signatures]
    profiles = state_profiles(ctx)
    if profiles_requested:
        unknown = set(profiles_requested) - set(profiles)
        if unknown:
            raise HarnessError(f"unknown state profiles: {sorted(unknown)}")
        profiles = {name: profiles[name] for name in profiles_requested}
    quote_ids = sorted(ctx.quote_text)
    if limit_quotes:
        quote_ids = quote_ids[:limit_quotes]
    mode = "pilot_sweep" if pilot else "full_corpus_sweep"
    run_id = f"{mode}:{hash_value([row['seasonal_state_signature'] for row in seasons])[:12]}:{len(quote_ids)}"
    timer = run_timed(ctx.connection, run_id, mode, {
        "signatures": len(seasons), "quotes": len(quote_ids), "profiles": list(profiles),
    })
    completed = 0
    errors = 0
    for season in seasons:
        when = datetime.fromisoformat(season["timestamp"])
        epoch = int(when.timestamp())
        ctx.bot.now_epoch = lambda value=epoch: value
        for profile_name, profile in profiles.items():
            for quote_index, quote_id in enumerate(quote_ids):
                event_key = f"{mode}:{season['seasonal_state_signature']}:{profile_name}:{quote_id}"
                prior = ctx.connection.execute(
                    "SELECT reconstruction_complete FROM simulation_events WHERE event_key=?",
                    (event_key,),
                ).fetchone()
                if prior and prior["reconstruction_complete"]:
                    continue
                if prior:
                    ctx.connection.execute("DELETE FROM simulation_events WHERE event_key=?", (event_key,))
                state = copy.deepcopy(profile["state"])
                images_used = set(profile["images_used"])
                quote = quote_choice(ctx, quote_id, epoch)
                before_hash = hash_value({"state": state, "images": sorted(images_used), "quote": quote_id})
                try:
                    selection, tie_state = select_forced_quote(
                        ctx, quote, state, images_used,
                        seed=int(hashlib.sha256(event_key.encode()).hexdigest()[:16], 16),
                    )
                    policies = evaluate_policies(ctx, quote, selection["image"], selection["scored"], tie_state=tie_state)
                    insert_event(
                        ctx, event_key=event_key, mode=mode, when=when, year=when.year,
                        seed=None, event_index=quote_index, profile=profile_name,
                        state_hash=before_hash, season=season, quote=quote,
                        selection=selection, policies=policies, full_candidates=True,
                        generated_spacing_allowed=ctx.bot.generated_images_allowed_by_spacing(state),
                    )
                except Exception as exc:
                    errors += 1
                    insert_event(
                        ctx, event_key=event_key, mode=mode, when=when, year=when.year,
                        seed=None, event_index=quote_index, profile=profile_name,
                        state_hash=before_hash, season=season, quote=quote,
                        selection=None, policies=None, full_candidates=True,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                completed += 1
                if completed % 100 == 0:
                    ctx.connection.commit()
    ctx.connection.commit()
    timer.finish()
    summary = summarise_mode(ctx.connection, mode)
    summary.update({"seasonal_signatures": len(seasons), "quote_count": len(quote_ids), "state_profiles": len(profiles), "selection_errors": errors})
    if not pilot:
        atomic_write_json(ctx.run_dir / "full_corpus_sweep.json", summary)
        atomic_write_text(ctx.run_dir / "full_corpus_sweep.md", summary_markdown("Full-Corpus Seasonal Sweep", summary))
    return summary


def checkpoint_get(connection: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    """Return the checkpoint get."""
    row = connection.execute("SELECT payload_json FROM checkpoints WHERE checkpoint_key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def checkpoint_put(connection: sqlite3.Connection, key: str, payload: dict[str, Any]) -> None:
    """Perform the checkpoint put operation."""
    connection.execute(
        "INSERT OR REPLACE INTO checkpoints VALUES (?,?,?)",
        (key, json.dumps(payload, sort_keys=True), utc_now()),
    )


def simulation_checkpoint(
    *,
    event_index: int,
    virtual_epoch: int,
    state: dict[str, Any],
    images_used: set[str],
    lines_used: set[str],
    rng_state: object,
) -> dict[str, Any]:
    """Return the simulation checkpoint."""
    return {
        "event_index": event_index,
        "virtual_epoch": virtual_epoch,
        "state": state,
        "images_used": sorted(images_used),
        "lines_used": sorted(lines_used),
        "rng_state": production_sim.rng_state_encode(rng_state),
    }


def restore_simulation_checkpoint(payload: dict[str, Any]) -> tuple[int, int, dict[str, Any], set[str], set[str], object]:
    """Restore simulation checkpoint."""
    try:
        rng_state = production_sim.rng_state_decode(payload["rng_state"])
    except (KeyError, TypeError, production_sim.SimulationSafetyError) as exc:
        raise HarnessError(
            "unsafe, legacy, or malformed simulation checkpoint rejected; restart this seed"
        ) from exc
    return (
        int(payload["event_index"]), int(payload["virtual_epoch"]), payload["state"],
        set(payload["images_used"]), set(payload["lines_used"]), rng_state,
    )


def run_simulation(
    ctx: HarnessContext,
    *,
    years: Sequence[int],
    seeds: int,
    seed_start: int = 0,
    pilot: bool = False,
    max_events: int | None = None,
) -> dict[str, Any]:
    """Run simulation."""
    if seeds < 1:
        raise HarnessError("seed count must be positive")
    mode = "pilot_monte_carlo" if pilot else "monte_carlo"
    run_id = f"{mode}:{','.join(str(year) for year in years)}:{seed_start}:{seeds}"
    timer = run_timed(ctx.connection, run_id, mode, {
        "years": list(years), "seed_start": seed_start, "seeds": seeds, "max_events": max_events,
    })
    completed_now = 0
    for year in years:
        year_start = epoch_for(year, 1, 1, 0, 1)
        year_end = epoch_for(year + 1, 1, 1, 0, 0)
        for seed in range(seed_start, seed_start + seeds):
            seed_value = 10_000 + seed
            key = f"{mode}:{year}:{seed_value}"
            checkpoint = checkpoint_get(ctx.connection, key)
            if checkpoint:
                event_index, virtual_epoch, state, images_used, lines_used, rng_state = restore_simulation_checkpoint(checkpoint)
            else:
                state, images_used, lines_used = filtered_snapshot_state(ctx)
                state = copy.deepcopy(state)
                state["last_quote_post_epoch"] = 0
                state["next_quote_post_epoch"] = year_start
                event_index = 0
                virtual_epoch = year_start
                rng_state = random.Random(seed_value).getstate()
            recent_rows = ctx.connection.execute(
                "SELECT quote_id,production_image FROM simulation_events WHERE mode=? AND simulated_year=? AND seed=? ORDER BY event_index DESC LIMIT 5",
                (mode, year, seed_value),
            ).fetchall()
            recent_quotes = [str(row["quote_id"]) for row in reversed(recent_rows)]
            recent_images = [str(row["production_image"]) for row in reversed(recent_rows)]
            series_events = 0
            while virtual_epoch < year_end:
                if max_events is not None and series_events >= max_events:
                    break
                event_index += 1
                event_key = f"{mode}:{year}:{seed_value}:{event_index}"
                if ctx.connection.execute("SELECT 1 FROM simulation_events WHERE event_key=?", (event_key,)).fetchone():
                    raise HarnessError(f"checkpoint is behind existing event {event_key}")
                ctx.bot.now_epoch = lambda value=virtual_epoch: value
                ctx.bot.random.setstate(rng_state)
                state_before = copy.deepcopy(state)
                images_before = set(images_used)
                lines_before = set(lines_used)
                rng_before = ctx.bot.random.getstate()
                state_hash = hash_value({
                    "state": state_before, "images_used": sorted(images_before),
                    "lines_used": sorted(lines_before),
                    "rng": production_sim.rng_state_encode(rng_before),
                })
                selection = production_sim.select_with_production_recovery(
                    ctx.bot, lines_used, images_used, state,
                )
                quote = canonicalise_production_quote(ctx, selection["quote"])
                policies = evaluate_policies(
                    ctx, quote, selection["image"], selection["scored"], tie_state=rng_before,
                )
                quote_hash = str(quote["quote_hash"])
                image_name = str(selection["image"]["basename"])
                quote_cycle_reset = bool(lines_before - lines_used)
                image_cycle_reset = bool(images_before - images_used) or bool(selection["image"].get("cycle_reset"))
                spacing_allowed = ctx.bot.generated_images_allowed_by_spacing(state_before)
                next_epoch = production_sim.apply_simulated_success(
                    ctx.bot, selection, state, lines_used, images_used,
                    virtual_epoch, f"{year}-{seed_value}", event_index,
                )
                rng_state = ctx.bot.random.getstate()
                season = seasonal_state(ctx, local_datetime(virtual_epoch))
                insert_event(
                    ctx, event_key=event_key, mode=mode, when=local_datetime(virtual_epoch),
                    year=year, seed=seed_value, event_index=event_index,
                    profile="current_production_snapshot", state_hash=state_hash,
                    season=season, quote=quote, selection=selection, policies=policies,
                    quote_cycle_reset=quote_cycle_reset, image_cycle_reset=image_cycle_reset,
                    repeated_quote=quote_hash in recent_quotes[-5:], repeated_image=image_name in recent_images[-5:],
                    generated_spacing_allowed=spacing_allowed, full_candidates=False,
                )
                recent_quotes.append(quote_hash)
                recent_images.append(image_name)
                virtual_epoch = next_epoch
                checkpoint_put(
                    ctx.connection, key,
                    simulation_checkpoint(
                        event_index=event_index, virtual_epoch=virtual_epoch, state=state,
                        images_used=images_used, lines_used=lines_used, rng_state=rng_state,
                    ),
                )
                completed_now += 1
                series_events += 1
                if series_events % 25 == 0:
                    ctx.connection.commit()
            ctx.connection.commit()
    timer.finish()
    summary = summarise_mode(ctx.connection, mode)
    summary.update({
        "years": list(years), "seed_start": seed_start, "seed_count": seeds,
        "events_completed_this_invocation": completed_now,
    })
    if not pilot:
        atomic_write_json(ctx.run_dir / "monte_carlo_summary.json", summary)
        atomic_write_text(ctx.run_dir / "monte_carlo_summary.md", summary_markdown("Stateful Full-Year Monte Carlo", summary))
    return summary


def boundary_instants(boundary_date: date) -> list[datetime]:
    """Return the boundary instants."""
    midnight = datetime.combine(boundary_date, datetime_time(0, 0), TZ)
    return [midnight - timedelta(minutes=1), midnight, midnight + timedelta(minutes=1)]


def stress_boundaries(ctx: HarnessContext) -> dict[str, Any]:
    """Return the stress boundaries."""
    map_payload = json.loads((ctx.run_dir / "seasonal_calendar_map.json").read_text(encoding="utf-8"))
    boundaries = map_payload.get("boundaries") or []
    profiles_all = state_profiles(ctx)
    profile_names = (
        "fresh_state", "near_exhausted_image_cycle",
        "current_production_snapshot", "recent_generated_image_used",
        "generated_spacing_satisfied",
    )
    mode = "seasonal_boundary_stress"
    run_id = f"{mode}:{hash_value(boundaries)[:12]}"
    timer = run_timed(ctx.connection, run_id, mode, {"boundary_count": len(boundaries), "profiles": profile_names})
    events = 0
    failures = 0
    seen_boundary: set[tuple[str, str, str]] = set()
    for boundary in boundaries:
        identity = (boundary["boundary_date"], boundary["boundary_kind"], boundary["rule"])
        if identity in seen_boundary:
            continue
        seen_boundary.add(identity)
        boundary_day = date.fromisoformat(boundary["boundary_date"])
        for when in boundary_instants(boundary_day):
            epoch = int(when.timestamp())
            season = seasonal_state(ctx, when)
            ctx.bot.now_epoch = lambda value=epoch: value
            for profile_name in profile_names:
                profile = profiles_all[profile_name]
                for seed in (101, 202, 303):
                    event_key = f"{mode}:{boundary['rule']}:{boundary['boundary_kind']}:{when.isoformat()}:{profile_name}:{seed}"
                    prior = ctx.connection.execute(
                        "SELECT reconstruction_complete FROM simulation_events WHERE event_key=?",
                        (event_key,),
                    ).fetchone()
                    if prior and prior["reconstruction_complete"]:
                        continue
                    if prior:
                        ctx.connection.execute("DELETE FROM simulation_events WHERE event_key=?", (event_key,))
                    state = copy.deepcopy(profile["state"])
                    images_used = set(profile["images_used"])
                    lines_used = set(profile["lines_used"])
                    ctx.bot.random.setstate(random.Random(seed).getstate())
                    state_hash = hash_value({"state": state, "images": sorted(images_used), "lines": sorted(lines_used)})
                    try:
                        selection = production_sim.select_with_production_recovery(ctx.bot, lines_used, images_used, state)
                        quote = canonicalise_production_quote(ctx, selection["quote"])
                        policies = evaluate_policies(
                            ctx, quote, selection["image"], selection["scored"],
                            tie_state=random.Random(seed).getstate(),
                        )
                        insert_event(
                            ctx, event_key=event_key, mode=mode, when=when, year=when.year,
                            seed=seed, event_index=events, profile=profile_name, state_hash=state_hash,
                            season=season, quote=quote, selection=selection,
                            policies=policies, full_candidates=False,
                            generated_spacing_allowed=ctx.bot.generated_images_allowed_by_spacing(state),
                        )
                    except Exception as exc:
                        failures += 1
                        fallback_quote = quote_choice(ctx, sorted(ctx.quote_text)[0], epoch)
                        insert_event(
                            ctx, event_key=event_key, mode=mode, when=when, year=when.year,
                            seed=seed, event_index=events, profile=profile_name, state_hash=state_hash,
                            season=season, quote=fallback_quote, selection=None, policies=None,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    events += 1
                    if events % 100 == 0:
                        ctx.connection.commit()
    ctx.connection.commit()
    timer.finish()
    summary = summarise_mode(ctx.connection, mode)
    summary.update({"distinct_boundaries": len(seen_boundary), "selection_errors": failures})
    atomic_write_json(ctx.run_dir / "seasonal_boundary_stress.json", summary)
    atomic_write_text(ctx.run_dir / "seasonal_boundary_stress.md", summary_markdown("Seasonal Boundary Stress", summary))
    return summary


def snapshot_replay_manifest_sources(ctx: HarnessContext, replay_root: Path) -> dict[str, Any]:
    """Stable-copy the compiled manifest's hashed dependencies into the private replay root."""
    manifest = json.loads(
        (ctx.snapshot / "material_veto_v3_shadow_manifest.json").read_text(encoding="utf-8")
    )
    records: dict[str, dict[str, Any]] = {}
    for source_key, source_record in sorted((manifest.get("source_file_hashes") or {}).items()):
        relative = Path(str(source_record.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise HarnessError(f"unsafe replay source path: {relative}")
        expected_hash = str(source_record.get("sha256") or "")
        source = ROOT / relative
        destination = replay_root / relative
        if destination.is_file() and sha256_file(destination) == expected_hash:
            record = {
                "source_path": str(source.resolve()),
                "snapshot_path": str(destination),
                "sha256": expected_hash,
                "reused": True,
            }
        else:
            record = stable_file_record(source, destination)
            if record["sha256"] != expected_hash:
                raise HarnessError(f"replay source hash mismatch: {source_key}")
            record["reused"] = False
        records[str(source_key)] = record
    payload = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "source_manifest_sha256": sha256_file(ctx.snapshot / "material_veto_v3_shadow_manifest.json"),
        "records": records,
    }
    atomic_write_json(ctx.run_dir / "replay_source_snapshot_manifest.json", payload)
    return payload


def historical_replay(ctx: HarnessContext, since_days: int) -> dict[str, Any]:
    """Return the historical replay."""
    from semantic_alignment.quote_image_semantic_veto import historical_replay as veto_replay

    replay_root = ctx.run_dir / "runtime" / "replay_project"
    if not replay_root.exists():
        replay_root.mkdir()
        for name in ("image_analysis.json", "generated_image_analysis.json"):
            (replay_root / name).symlink_to(ctx.snapshot / name)
        for path in sorted((ctx.snapshot / "logs").glob("mrsMThatcher*.log*")):
            (replay_root / path.name).symlink_to(path)
    snapshot_replay_manifest_sources(ctx, replay_root)
    payload = veto_replay(
        replay_root,
        ctx.snapshot / "material_veto_v3_shadow_manifest.json",
        since_days=since_days,
    )
    records = replay_records(payload)
    if not isinstance(records, list):
        records = []
    for index, row in enumerate(records):
        key = str(row.get("event_id") or hash_value([index, row]))
        timestamp = str(row.get("time") or row.get("timestamp") or row.get("selected_at") or "")
        ctx.connection.execute(
            "INSERT OR REPLACE INTO historical_replay_events VALUES (?,?,?,?,?,?,?)",
            (
                key, timestamp, str(row.get("quote_id") or row.get("quote_hash") or ""),
                str(
                    row.get("image_basename")
                    or row.get("production_image_basename")
                    or row.get("selected_image_basename")
                    or ""
                ),
                str(row.get("shadow_status") or "unknown_unjudged"),
                int(bool(row.get("reconstruction_complete", False))),
                json.dumps(row, sort_keys=True, ensure_ascii=False),
            ),
        )
    ctx.connection.commit()
    output = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "since_days": since_days,
        "source": "stable snapshotted structured production logs",
        "reconstruction_note": "Missing historical candidate/state telemetry is left incomplete rather than inferred.",
        "veto_replay": payload,
        "record_count": len(records),
        "complete_reconstruction_count": sum(bool(row.get("reconstruction_complete")) for row in records),
    }
    atomic_write_json(ctx.run_dir / "historical_replay.json", output)
    complete_count = sum(bool(row.get("reconstruction_complete")) for row in records)
    lines = [
        "# Historical Replay", "", f"Window: trailing {since_days} days", f"Records: {len(records)}", "",
        f"- Allowed: {payload.get('allowed', 0)}",
        f"- Vetoed: {payload.get('vetoed', 0)}",
        f"- Unknown/unjudged: {payload.get('unknown', 0)}",
        f"- Vetoed with / without logged alternative: {payload.get('vetoed_with_alternative', 0)} / {payload.get('vetoed_without_alternative', 0)}",
        f"- Quotations with no globally allowed image: {payload.get('quotes_with_no_globally_allowed_candidate', 0)}",
        f"- Generated images out of scope: {payload.get('generated_out_of_scope_selections', 0)}",
        f"- Complete candidate/state reconstructions: {complete_count}", "",
        "Replay used only stable snapshotted local logs. Missing historical candidate sets are explicitly incomplete.",
    ]
    vetoed = [row for row in records if row.get("shadow_status") == "veto"]
    if vetoed:
        lines.extend(["", "## Observed Vetoes", ""])
        for row in sorted(
            vetoed,
            key=lambda item: abs(float(item.get("score_delta_from_production_winner") or 0.0)),
            reverse=True,
        )[:5]:
            lines.append(
                f"- `{row.get('quote_id', '')}` / `{row.get('image_basename', '')}`: "
                f"{', '.join(row.get('veto_reason_codes') or []) or 'reason unavailable'}; "
                f"alternative `{row.get('alternative_image_basename') or 'none logged'}`; "
                f"score delta {row.get('score_delta_from_production_winner')}"
            )
    atomic_write_text(ctx.run_dir / "historical_replay.md", "\n".join(lines) + "\n")
    return output


def reproduced_event_identity(
    state_hash: str,
    quote: dict[str, Any],
    selection: dict[str, Any],
    policies: dict[str, Any],
) -> dict[str, Any]:
    """Return the reproduced event identity."""
    return {
        "state_hash": state_hash,
        "quote_id": str(quote.get("quote_hash") or ""),
        "production_image": str(selection["image"].get("basename") or ""),
        "production_score": float(selection["image"].get("score") or 0.0),
        "editorial_image": str((policies.get("editorial_winner") or {}).get("basename") or ""),
        "veto_status": str((policies.get("veto_event") or {}).get("shadow_status") or ""),
        "semantic_image": str((policies.get("semantic_winner") or {}).get("basename") or ""),
        "combined_image": str((policies.get("combined_winner") or {}).get("basename") or ""),
        "candidate_order": [str(row.get("basename") or "") for row in selection["scored"]],
        "candidate_scores": [float(row.get("score") or 0.0) for row in selection["scored"]],
    }


def stored_event_identity(row: sqlite3.Row, candidates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Return the stored event identity."""
    return {
        "state_hash": str(row["state_hash"] or ""),
        "quote_id": str(row["quote_id"] or ""),
        "production_image": str(row["production_image"] or ""),
        "production_score": float(row["production_score"] or 0.0),
        "editorial_image": str(row["editorial_image"] or ""),
        "veto_status": str(row["veto_status"] or ""),
        "semantic_image": str(row["semantic_image"] or ""),
        "combined_image": str(row["combined_image"] or ""),
        "candidate_order": [str(item.get("basename") or "") for item in candidates],
        "candidate_scores": [float(item.get("score") or 0.0) for item in candidates],
    }


def reproduce_event(ctx: HarnessContext, event_key: str) -> dict[str, Any]:
    """Return the reproduce event."""
    row = ctx.connection.execute(
        "SELECT * FROM simulation_events WHERE event_key=?", (event_key,)
    ).fetchone()
    if row is None or not row["reconstruction_complete"]:
        raise HarnessError(f"completed simulation event not found: {event_key}")
    mode = str(row["mode"])
    when = datetime.fromisoformat(str(row["simulated_timestamp"]))
    epoch = int(when.timestamp())
    ctx.bot.now_epoch = lambda value=epoch: value
    profiles = state_profiles(ctx)

    if mode in {"full_corpus_sweep", "pilot_sweep"}:
        profile = profiles[str(row["state_profile"])]
        state = copy.deepcopy(profile["state"])
        images_used = set(profile["images_used"])
        quote = quote_choice(ctx, str(row["quote_id"]), epoch)
        state_hash = hash_value({"state": state, "images": sorted(images_used), "quote": row["quote_id"]})
        selection, tie_state = select_forced_quote(
            ctx,
            quote,
            state,
            images_used,
            seed=int(hashlib.sha256(event_key.encode()).hexdigest()[:16], 16),
        )
        policies = evaluate_policies(ctx, quote, selection["image"], selection["scored"], tie_state=tie_state)
    elif mode == "seasonal_boundary_stress":
        profile = profiles[str(row["state_profile"])]
        state = copy.deepcopy(profile["state"])
        images_used = set(profile["images_used"])
        lines_used = set(profile["lines_used"])
        seed = int(row["seed"])
        state_hash = hash_value({"state": state, "images": sorted(images_used), "lines": sorted(lines_used)})
        tie_state = random.Random(seed).getstate()
        ctx.bot.random.setstate(tie_state)
        selection = production_sim.select_with_production_recovery(ctx.bot, lines_used, images_used, state)
        quote = canonicalise_production_quote(ctx, selection["quote"])
        policies = evaluate_policies(ctx, quote, selection["image"], selection["scored"], tie_state=tie_state)
    elif mode in {"monte_carlo", "pilot_monte_carlo"}:
        year = int(row["simulated_year"])
        seed = int(row["seed"])
        target_index = int(row["event_index"])
        state, images_used, lines_used = filtered_snapshot_state(ctx)
        state = copy.deepcopy(state)
        virtual_epoch = epoch_for(year, 1, 1, 0, 1)
        state["last_quote_post_epoch"] = 0
        state["next_quote_post_epoch"] = virtual_epoch
        ctx.bot.random.setstate(random.Random(seed).getstate())
        for index in range(1, target_index + 1):
            ctx.bot.now_epoch = lambda value=virtual_epoch: value
            state_hash = hash_value({
                "state": copy.deepcopy(state),
                "images_used": sorted(images_used),
                "lines_used": sorted(lines_used),
                "rng": production_sim.rng_state_encode(ctx.bot.random.getstate()),
            })
            tie_state = ctx.bot.random.getstate()
            selection = production_sim.select_with_production_recovery(ctx.bot, lines_used, images_used, state)
            quote = canonicalise_production_quote(ctx, selection["quote"])
            if index == target_index:
                policies = evaluate_policies(
                    ctx, quote, selection["image"], selection["scored"], tie_state=tie_state,
                )
                break
            virtual_epoch = production_sim.apply_simulated_success(
                ctx.bot, selection, state, lines_used, images_used,
                virtual_epoch, f"{year}-{seed}", index,
            )
    else:
        raise HarnessError(f"event mode is not reproducible: {mode}")

    reproduced = reproduced_event_identity(state_hash, quote, selection, policies)
    stored_candidates = decode_candidate_set(ctx.connection, event_key)
    stored = stored_event_identity(row, stored_candidates)
    comparable_stored = {key: value for key, value in stored.items() if key not in {"candidate_order", "candidate_scores"}}
    comparable_reproduced = {
        key: value for key, value in reproduced.items() if key not in {"candidate_order", "candidate_scores"}
    }
    checks = {key: comparable_stored[key] == comparable_reproduced[key] for key in comparable_stored}
    return {
        "schema_version": 1,
        "event_key": event_key,
        "mode": mode,
        "passed": all(checks.values()),
        "checks": checks,
        "stored": stored,
        "reproduced": reproduced,
        "network_calls": 0,
        "production_writes": 0,
    }


def replay_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return replay observations across old and current replay schemas."""
    for key in ("observations", "records", "events"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def summarise_mode(connection: sqlite3.Connection, mode: str) -> dict[str, Any]:
    """Summarise mode."""
    total = 0
    veto_count = 0
    deltas: list[float] = []
    editorial_changes = 0
    statuses: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    analysis_alias_events = 0
    lookup_latencies: list[float] = []
    globally_no_safe_quotes: set[str] = set()
    distinct_quotes: set[str] = set()
    distinct_images: set[str] = set()
    seasonal_signatures: set[str] = set()
    vetoes_with_alternatives = 0
    no_safe_image_count = 0
    quote_cycle_reset_count = 0
    image_cycle_reset_count = 0
    quote_repetition_count = 0
    image_repetition_count = 0
    no_candidate_event_count = 0
    for row in connection.execute("SELECT * FROM simulation_events WHERE mode=?", (mode,)):
        total += 1
        status = str(row["veto_status"] or "unavailable")
        statuses[status] += 1
        if row["quote_id"]:
            distinct_quotes.add(str(row["quote_id"]))
        if row["production_image"]:
            distinct_images.add(str(row["production_image"]))
        if row["seasonal_signature"]:
            seasonal_signatures.add(str(row["seasonal_signature"]))
        editorial_changes += bool(row["editorial_image"] and row["editorial_image"] != row["production_image"])
        if status == "veto":
            veto_count += 1
            vetoes_with_alternatives += bool(row["allowed_alternative"])
            if row["alternative_score_loss"] is not None:
                deltas.append(float(row["alternative_score_loss"]))
            try:
                reasons.update(json.loads(row["veto_reasons_json"] or "[]"))
            except json.JSONDecodeError:
                pass
        no_safe_image_count += bool(row["no_safe_image"])
        quote_cycle_reset_count += bool(row["quote_cycle_reset"])
        image_cycle_reset_count += bool(row["image_cycle_reset"])
        quote_repetition_count += bool(row["repeated_quote"])
        image_repetition_count += bool(row["repeated_image"])
        no_candidate_event_count += not bool(row["reconstruction_complete"])
        try:
            detail = json.loads(row["detail_json"])
            if detail.get("quote_analysis_source") == "whitespace_alias":
                analysis_alias_events += 1
            veto_event = detail.get("veto_event") or {}
            if veto_event.get("lookup_latency_ms") is not None:
                lookup_latencies.append(float(veto_event["lookup_latency_ms"]))
            if veto_event.get("quote_has_no_allowed_candidate_globally") and row["quote_id"]:
                globally_no_safe_quotes.add(str(row["quote_id"]))
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "schema_version": 1,
        "mode": mode,
        "total_simulations": total,
        "distinct_quotations": len(distinct_quotes),
        "distinct_images_selected": len(distinct_images),
        "seasonal_state_coverage": len(seasonal_signatures),
        "production_editorial_disagreement_count": editorial_changes,
        "production_editorial_disagreement_rate": editorial_changes / total if total else None,
        "semantic_veto_status_counts": dict(statuses),
        "semantic_veto_count": veto_count,
        "semantic_veto_rate": veto_count / total if total else None,
        "known_pair_count": statuses["allow"] + statuses["veto"],
        "known_pair_coverage": (statuses["allow"] + statuses["veto"]) / total if total else None,
        "vetoes_with_alternatives": vetoes_with_alternatives,
        "vetoes_without_alternatives": veto_count - vetoes_with_alternatives,
        "no_safe_image_count": no_safe_image_count,
        "globally_no_safe_quotation_count": len(globally_no_safe_quotes),
        "alternative_score_loss_median": statistics.median(deltas) if deltas else None,
        "alternative_score_loss_p95": percentile(deltas, 0.95),
        "alternative_score_loss_max": max(deltas) if deltas else None,
        "generated_image_share": statuses["out_of_scope_generated"] / total if total else 0.0,
        "quote_cycle_reset_count": quote_cycle_reset_count,
        "image_cycle_reset_count": image_cycle_reset_count,
        "quote_repetition_count": quote_repetition_count,
        "image_repetition_count": image_repetition_count,
        "no_candidate_event_count": no_candidate_event_count,
        "forced_whitespace_analysis_alias_event_count": analysis_alias_events,
        "veto_lookup_latency_ms_p50": statistics.median(lookup_latencies) if lookup_latencies else None,
        "veto_lookup_latency_ms_p95": percentile(lookup_latencies, 0.95),
        "veto_lookup_latency_ms_max": max(lookup_latencies) if lookup_latencies else None,
        "veto_reason_counts": dict(reasons.most_common()),
    }


def seasonal_breakdown(connection: sqlite3.Connection, mode: str) -> dict[str, Any]:
    """Return the seasonal breakdown."""
    state_lookup = {
        str(row["signature"]): json.loads(row["state_json"])
        for row in connection.execute("SELECT * FROM seasonal_states")
    }
    grouped: dict[str, dict[str, Any]] = {}
    for row in connection.execute(
        "SELECT * FROM simulation_events WHERE mode=? ORDER BY seasonal_signature,event_key",
        (mode,),
    ):
        signature = str(row["seasonal_signature"])
        item = grouped.setdefault(signature, {
            "selection_count": 0,
            "quotation_selection_distribution": Counter(),
            "production_winner_distribution": Counter(),
            "editorial_shadow_disagreement_count": 0,
            "semantic_veto_count": 0,
            "status_counts": Counter(),
            "vetoes_with_allowed_alternatives": 0,
            "coverage_gaps_no_safe_image": 0,
            "losses": [],
            "repeated_image_count": 0,
            "image_cycle_exhaustion_count": 0,
            "quote_repetition_count": 0,
            "no_candidate_event_count": 0,
        })
        item["selection_count"] += 1
        if row["quote_id"]:
            item["quotation_selection_distribution"][str(row["quote_id"])] += 1
        if row["production_image"]:
            item["production_winner_distribution"][str(row["production_image"])] += 1
        item["editorial_shadow_disagreement_count"] += bool(
            row["editorial_image"] and row["editorial_image"] != row["production_image"]
        )
        status = str(row["veto_status"] or "unavailable")
        item["status_counts"][status] += 1
        if status == "veto":
            item["semantic_veto_count"] += 1
            item["vetoes_with_allowed_alternatives"] += bool(row["allowed_alternative"])
            if row["alternative_score_loss"] is not None:
                item["losses"].append(float(row["alternative_score_loss"]))
        item["coverage_gaps_no_safe_image"] += bool(row["no_safe_image"])
        item["repeated_image_count"] += bool(row["repeated_image"])
        item["image_cycle_exhaustion_count"] += bool(row["image_cycle_reset"])
        item["quote_repetition_count"] += bool(row["repeated_quote"])
        item["no_candidate_event_count"] += not bool(row["reconstruction_complete"])

    summaries: list[dict[str, Any]] = []
    for signature, item in sorted(grouped.items()):
        state = state_lookup.get(signature, {})
        losses = item["losses"]
        status_counts = item["status_counts"]
        summaries.append({
            "seasonal_signature": signature,
            "representative_timestamp": state.get("timestamp"),
            "active_seasonal_rules": state.get("active_seasonal_rules", []),
            "quote_boosts": state.get("quote_boosts", {}),
            "quote_exclusions": state.get("quote_exclusions", []),
            "image_boosts": state.get("image_boosts", {}),
            "image_exclusions": state.get("image_exclusions", []),
            "selection_count": item["selection_count"],
            "quotation_selection_distribution": dict(item["quotation_selection_distribution"]),
            "production_winner_distribution": dict(item["production_winner_distribution"]),
            "editorial_shadow_disagreement_count": item["editorial_shadow_disagreement_count"],
            "semantic_veto_count": item["semantic_veto_count"],
            "unknown_pair_count": status_counts["unknown_unjudged"],
            "vetoes_with_allowed_alternatives": item["vetoes_with_allowed_alternatives"],
            "coverage_gaps_no_safe_image": item["coverage_gaps_no_safe_image"],
            "alternative_score_loss_median": statistics.median(losses) if losses else None,
            "alternative_score_loss_p95": percentile(losses, 0.95),
            "generated_image_count": status_counts["out_of_scope_generated"],
            "repeated_image_count": item["repeated_image_count"],
            "image_cycle_exhaustion_count": item["image_cycle_exhaustion_count"],
            "quote_repetition_count": item["quote_repetition_count"],
            "no_candidate_event_count": item["no_candidate_event_count"],
        })

    controls = [
        row for row in summaries
        if not any(str(rule).startswith("quote:") for rule in row["active_seasonal_rules"])
    ]
    control = max(controls, key=lambda row: row["selection_count"], default=None)
    comparisons: list[dict[str, Any]] = []
    if control:
        control_total = max(1, int(control["selection_count"]))
        control_veto_rate = int(control["semantic_veto_count"]) / control_total
        control_disagreement_rate = int(control["editorial_shadow_disagreement_count"]) / control_total
        for row in summaries:
            if not any(str(rule).startswith("quote:") for rule in row["active_seasonal_rules"]):
                continue
            total = max(1, int(row["selection_count"]))
            boosted_quote_share_changes = []
            for quote_id in sorted(row["quote_boosts"]):
                current_share = int(row["quotation_selection_distribution"].get(quote_id, 0)) / total
                control_share = int(control["quotation_selection_distribution"].get(quote_id, 0)) / control_total
                boosted_quote_share_changes.append({
                    "quote_id": quote_id,
                    "configured_multiplier": row["quote_boosts"][quote_id],
                    "seasonal_selection_share": current_share,
                    "control_selection_share": control_share,
                    "selection_share_delta": current_share - control_share,
                    "share_ratio": current_share / control_share if control_share else None,
                })
            comparisons.append({
                "seasonal_signature": row["seasonal_signature"],
                "control_signature": control["seasonal_signature"],
                "active_seasonal_rules": row["active_seasonal_rules"],
                "selection_count": row["selection_count"],
                "semantic_veto_rate_delta": int(row["semantic_veto_count"]) / total - control_veto_rate,
                "editorial_disagreement_rate_delta": (
                    int(row["editorial_shadow_disagreement_count"]) / total - control_disagreement_rate
                ),
                "boosted_quote_share_changes": boosted_quote_share_changes,
                "observational_note": "Deterministic rule/seed comparison; no causal or statistical claim.",
            })
    return {
        "mode": mode,
        "seasonal_state_count": len(summaries),
        "non_seasonal_control_signature": control["seasonal_signature"] if control else None,
        "states": summaries,
        "matched_non_seasonal_comparisons": comparisons,
    }


def summary_markdown(title: str, summary: dict[str, Any]) -> str:
    """Return the summary markdown."""
    lines = [f"# {title}", ""]
    for key, value in summary.items():
        if isinstance(value, (dict, list)):
            continue
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    if summary.get("veto_reason_counts"):
        lines.extend(["", "## Veto Reasons", ""])
        for reason, count in summary["veto_reason_counts"].items():
            lines.append(f"- `{reason}`: {count}")
    return "\n".join(lines) + "\n"


def verify_immutable_sources(run_dir: Path) -> dict[str, Any]:
    """Verify immutable sources."""
    manifest = json.loads((run_dir / "source_snapshot_manifest.json").read_text(encoding="utf-8"))
    changed: list[dict[str, str]] = []
    checked = 0
    for key, record in manifest.get("files", {}).items():
        if record.get("mutable_source") or record.get("sanitised_selection_keys_only"):
            continue
        source = Path(str(record.get("source_path") or ""))
        if not source.is_file():
            changed.append({"source": key, "reason": "missing"})
            continue
        checked += 1
        current = sha256_file(source)
        if current != record.get("sha256"):
            changed.append({"source": key, "reason": "hash_changed", "before": str(record.get("sha256")), "after": current})
    return {"checked": checked, "changed": changed, "passed": not changed}


def exceptional_cases(connection: sqlite3.Connection, limit: int = 100) -> dict[str, Any]:
    """Return the exceptional cases."""
    veto_rows = connection.execute(
        """
        SELECT mode, simulated_timestamp, quote_id, quote_text, production_image,
               veto_reasons_json, allowed_alternative, alternative_score_loss,
               no_safe_image, state_profile, seed
        FROM simulation_events WHERE veto_status='veto'
        ORDER BY COALESCE(alternative_score_loss, -1) DESC, event_key LIMIT ?
        """,
        (limit,),
    ).fetchall()
    dominance = connection.execute(
        """
        SELECT production_image, COUNT(*) AS selections, COUNT(DISTINCT quote_id) AS quotations
        FROM simulation_events WHERE mode='monte_carlo' AND production_image IS NOT NULL
        GROUP BY production_image ORDER BY quotations DESC, selections DESC LIMIT 20
        """
    ).fetchall()
    no_safe = connection.execute(
        "SELECT DISTINCT quote_id, quote_text FROM simulation_events WHERE no_safe_image=1 ORDER BY quote_id"
    ).fetchall()
    return {
        "schema_version": 1,
        "highest_impact_vetoes": [dict(row) for row in veto_rows],
        "broadly_selected_images": [dict(row) for row in dominance],
        "quotations_with_no_safe_image": [dict(row) for row in no_safe],
    }


def seasonal_calendar_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the seasonal calendar summary."""
    return {
        "years": list(payload.get("years") or []),
        "observation_count": int(payload.get("calendar_state_count") or payload.get("observation_count") or 0),
        "unique_state_count": int(
            payload.get("unique_seasonal_state_count") or payload.get("unique_state_count") or 0
        ),
        "boundary_count": len(payload.get("boundaries") or []),
    }


def runtime_resource_summary(run_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Return the runtime resource summary."""
    completed = [row for row in run_rows if row.get("status") == "completed"]
    return {
        "measurement_scope": "completed command invocations; interrupted invocations are excluded",
        "completed_command_count": len(completed),
        "wall_seconds_total": sum(float(row.get("wall_seconds") or 0.0) for row in completed),
        "cpu_seconds_total": sum(float(row.get("cpu_seconds") or 0.0) for row in completed),
        "peak_rss_kib_max": max((int(row.get("peak_rss_kib") or 0) for row in completed), default=0),
    }


def generate_reports(run_dir: Path) -> dict[str, Any]:
    """Generate reports."""
    connection = initialise_database(run_dir / DB_NAME)
    connection.execute(
        "UPDATE runs SET status='interrupted', completed_at=COALESCE(completed_at,?) WHERE status='running'",
        (utc_now(),),
    )
    connection.commit()
    modes = [row[0] for row in connection.execute("SELECT DISTINCT mode FROM simulation_events ORDER BY mode")]
    mode_summaries = {mode: summarise_mode(connection, mode) for mode in modes}
    seasonal_outcomes = {
        mode: seasonal_breakdown(connection, mode)
        for mode in modes
        if mode in {"full_corpus_sweep", "monte_carlo", "seasonal_boundary_stress"}
    }
    atomic_write_json(run_dir / "seasonal_outcomes.json", {
        "schema_version": 1,
        "generated_at": utc_now(),
        "modes": seasonal_outcomes,
    })
    seasonal_lines = ["# Seasonal Outcomes", ""]
    for mode, outcome in seasonal_outcomes.items():
        seasonal_lines.extend([
            f"## {mode}", "",
            f"- Seasonal states represented: {outcome['seasonal_state_count']}",
            f"- Non-seasonal control signature: `{outcome['non_seasonal_control_signature'] or 'unavailable'}`", "",
        ])
        for comparison in outcome["matched_non_seasonal_comparisons"]:
            seasonal_lines.append(
                f"- `{comparison['seasonal_signature']}`: veto-rate delta "
                f"{comparison['semantic_veto_rate_delta']:.4f}; editorial-disagreement delta "
                f"{comparison['editorial_disagreement_rate_delta']:.4f}"
            )
        seasonal_lines.append("")
    seasonal_lines.append("All comparisons are deterministic simulations, not causal or statistical findings.")
    atomic_write_text(run_dir / "seasonal_outcomes.md", "\n".join(seasonal_lines) + "\n")
    exceptions = exceptional_cases(connection)
    atomic_write_json(run_dir / "exceptional_cases.json", exceptions)
    exception_lines = ["# Exceptional Cases", "", f"Highest-impact vetoes retained: {len(exceptions['highest_impact_vetoes'])}", ""]
    for row in exceptions["highest_impact_vetoes"][:20]:
        exception_lines.append(
            f"- `{row['quote_id']}` / `{row['production_image']}` -> `{row['allowed_alternative'] or 'no safe alternative'}` "
            f"(loss={row['alternative_score_loss']})"
        )
    atomic_write_text(run_dir / "exceptional_cases.md", "\n".join(exception_lines) + "\n")
    immutable = verify_immutable_sources(run_dir)
    write_audit_path = run_dir / "open_audit.json"
    write_audit = json.loads(write_audit_path.read_text(encoding="utf-8")) if write_audit_path.is_file() else {}
    run_rows = [dict(row) for row in connection.execute("SELECT * FROM runs ORDER BY started_at")]
    total = sum(summary["total_simulations"] for summary in mode_summaries.values())
    runtime_resources = runtime_resource_summary(run_rows)
    veto_manifest = json.loads(
        (run_dir / SNAPSHOT_DIRNAME / "material_veto_v3_shadow_manifest.json").read_text(encoding="utf-8")
    )
    global_veto_coverage = {
        "quotes_with_allowed_candidate": int(veto_manifest["quotes_with_allowed_candidate"]),
        "quotes_without_allowed_candidate": int(veto_manifest["quotes_without_allowed_candidate"]),
        "quotes_without_allowed_candidate_ids": list(veto_manifest["quotes_without_allowed_candidate_ids"]),
        "quotes_with_incomplete_pair_coverage": int(
            veto_manifest.get("quotes_with_incomplete_pair_coverage", 0)
        ),
        "quotes_with_incomplete_pair_coverage_ids": list(
            veto_manifest.get("quotes_with_incomplete_pair_coverage_ids") or []
        ),
        "adjudicated_unknown_pair_count": int(
            veto_manifest.get("adjudicated_unknown_pair_count", 0)
        ),
        "not_adjudicated_pair_count": int(
            veto_manifest.get("not_adjudicated_pair_count", 0)
        ),
    }
    calendar_map = json.loads((run_dir / "seasonal_calendar_map.json").read_text(encoding="utf-8"))
    calendar_summary = seasonal_calendar_summary(calendar_map)
    correction_path = run_dir / "simulation_correction_001.json"
    correction = json.loads(correction_path.read_text(encoding="utf-8")) if correction_path.is_file() else None
    replay_payload = json.loads((run_dir / "historical_replay.json").read_text(encoding="utf-8"))
    replay_summary_source = replay_payload.get("veto_replay") or {}
    replay_summary = {
        key: replay_summary_source.get(key)
        for key in (
            "total_selections", "allowed", "vetoed", "unknown",
            "vetoed_with_alternative", "vetoed_without_alternative",
            "quotes_with_no_globally_allowed_candidate", "generated_out_of_scope_selections",
            "production_selection_change_failures",
        )
    }
    invariant_checks = [
        {
            "check_key": row["check_key"],
            "passed": bool(row["passed"]),
            "checked_at": row["checked_at"],
        }
        for row in connection.execute("SELECT * FROM invariant_checks ORDER BY check_key")
    ]
    database_integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    monte_comparisons = (seasonal_outcomes.get("monte_carlo") or {}).get(
        "matched_non_seasonal_comparisons", []
    )
    boost_effects = [
        {"seasonal_signature": comparison["seasonal_signature"], **effect}
        for comparison in monte_comparisons
        for effect in comparison.get("boosted_quote_share_changes", [])
    ]
    largest_boost_effect = max(
        boost_effects,
        key=lambda item: abs(float(item.get("selection_share_delta") or 0.0)),
        default=None,
    )
    seasonal_findings = {
        "selection_failure_count": sum(
            int(summary.get("no_candidate_event_count") or 0)
            for mode, summary in mode_summaries.items()
            if mode in {"full_corpus_sweep", "monte_carlo", "seasonal_boundary_stress"}
        ),
        "largest_boosted_quote_share_change": largest_boost_effect,
        "broadest_selected_image": (exceptions.get("broadly_selected_images") or [None])[0],
        "interpretation": "Deterministic configured-rule behaviour only; no causal or statistical inference.",
    }
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.commit()
    database_size = (run_dir / DB_NAME).stat().st_size if (run_dir / DB_NAME).is_file() else 0
    final = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "harness_version": HARNESS_VERSION,
        "total_simulations": total,
        "mode_summaries": mode_summaries,
        "seasonal_outcomes": {
            "path": str(run_dir / "seasonal_outcomes.json"),
            "sha256": sha256_file(run_dir / "seasonal_outcomes.json"),
            "mode_count": len(seasonal_outcomes),
        },
        "historical_replay_summary": replay_summary,
        "invariant_checks": invariant_checks,
        "all_invariants_passed": bool(invariant_checks) and all(row["passed"] for row in invariant_checks),
        "database_integrity": database_integrity,
        "seasonal_findings": seasonal_findings,
        "immutable_source_verification": immutable,
        "production_write_audit": write_audit,
        "zero_network_design": {
            "socket_connect_blocked": True,
            "socket_connect_ex_blocked": True,
            "udp_sendto_blocked": True,
            "dns_resolution_blocked": True,
            "provider_calls": 0,
        },
        "runtime_runs": run_rows,
        "runtime_resources": runtime_resources,
        "global_semantic_veto_coverage": global_veto_coverage,
        "seasonal_calendar": calendar_summary,
        "source_snapshot_manifest_sha256": sha256_file(run_dir / "source_snapshot_manifest.json"),
        "simulation_correction_audit": {
            "path": str(correction_path),
            "sha256": sha256_file(correction_path),
            "reason": correction.get("reason"),
        } if correction else None,
        "database_size_bytes": database_size,
        "limitations": [
            "Generated images remain out of scope for the corrected historical-photo semantic veto.",
            "Historical replay leaves unavailable candidate/state telemetry incomplete.",
            "Simulation comparisons describe configured algorithms and deterministic seeds, not causal engagement effects.",
        ],
    }
    manifest_path = run_dir / "run_manifest.json"
    run_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    run_manifest.update({
        "schema_version": 1,
        "harness_version": HARNESS_VERSION,
        "updated_at": utc_now(),
        "commands_completed": [dict(row) for row in connection.execute("SELECT mode,status,completed_at FROM runs ORDER BY started_at")],
        "database_sha256": sha256_file(run_dir / DB_NAME),
        "network_calls": 0,
        "production_writes": int(write_audit.get("forbidden_write_attempt_count", 0)),
    })
    atomic_write_json(manifest_path, run_manifest)
    atomic_write_json(run_dir / "final_summary.json", final)
    lines = [
        "# Offline Quote/Image Selection Harness Report", "",
        f"Generated: {final['generated_at']}", "",
        "## Architecture", "",
        "The harness calls the real production quote and image selectors through the existing guarded production-parity simulator. "
        "It applies the original editorial scorer and corrected semantic-veto runtime observationally after each unchanged production selection.",
        "", "## Isolation", "",
        "- Network/provider calls: 0", f"- Forbidden production write attempts: {write_audit.get('forbidden_write_attempt_count', 0)}",
        f"- Immutable source hash verification: {'passed' if immutable['passed'] else 'FAILED'}", "",
        "## Results", "", f"Total simulations: {total}", "",
        f"Seasonal signatures: {calendar_summary['unique_state_count']} across years "
        f"{', '.join(str(year) for year in calendar_summary['years'])}",
        f"Complete all-veto semantic conclusions: {global_veto_coverage['quotes_without_allowed_candidate']} quotations",
        f"Incomplete semantic pair coverage: {global_veto_coverage['quotes_with_incomplete_pair_coverage']} quotations", "",
        f"Corrected isolated-run audit: {correction.get('reason') if correction else 'none required'}", "",
    ]
    for mode, summary in mode_summaries.items():
        lines.extend([
            f"### {mode}", "",
            f"- Events: {summary['total_simulations']}",
            f"- Distinct quotations: {summary['distinct_quotations']}",
            f"- Production/editorial disagreement rate: {summary['production_editorial_disagreement_rate']}",
            f"- Semantic-veto rate: {summary['semantic_veto_rate']}",
            f"- Known-pair coverage: {summary['known_pair_coverage']}",
            f"- Vetoes with alternatives: {summary['vetoes_with_alternatives']}",
            f"- Vetoes without alternatives: {summary['vetoes_without_alternatives']}", "",
        ])
    lines.extend([
        "## Seasonal Findings", "",
        f"- Selection failures across sweep, Monte Carlo and boundary stress: {seasonal_findings['selection_failure_count']}",
        f"- Largest configured boost share change: {json.dumps(largest_boost_effect, sort_keys=True) if largest_boost_effect else 'unavailable'}",
        f"- Broadest selected image: {json.dumps(seasonal_findings['broadest_selected_image'], sort_keys=True) if seasonal_findings['broadest_selected_image'] else 'unavailable'}",
        "- These are deterministic configured-rule observations, not causal or statistical findings.", "",
        "## Historical Replay", "",
        f"- Observed selections: {replay_summary.get('total_selections')}",
        f"- Allowed / vetoed / unknown: {replay_summary.get('allowed')} / {replay_summary.get('vetoed')} / {replay_summary.get('unknown')}",
        f"- Vetoed with / without logged alternative: {replay_summary.get('vetoed_with_alternative')} / {replay_summary.get('vetoed_without_alternative')}",
        f"- Production-selection invariant failures: {replay_summary.get('production_selection_change_failures')}", "",
        "## Parity", "",
        f"- All executable invariants passed: {bool(invariant_checks) and all(row['passed'] for row in invariant_checks)}",
        f"- Checks: {', '.join(row['check_key'] for row in invariant_checks)}", "",
        f"- SQLite quick check: {database_integrity}", "",
        "## Resources", "",
        f"- Completed command wall time: {runtime_resources['wall_seconds_total']:.2f} seconds",
        f"- Completed command CPU time: {runtime_resources['cpu_seconds_total']:.2f} seconds",
        f"- Peak resident memory: {runtime_resources['peak_rss_kib_max']} KiB",
        f"- SQLite database: {database_size} bytes", "",
    ])
    lines.extend([
        "## Interpretation", "",
        "Seasonal and policy comparisons are deterministic simulations. They do not establish causal effects or statistical significance.", "",
        "## Local Inspector", "",
        "```bash",
        f"python3 quote_image_selection_harness.py serve --run-dir {run_dir} --host 127.0.0.1 --port 8770",
        "```", "",
    ])
    atomic_write_text(run_dir / "final_report.md", "\n".join(lines))
    connection.close()
    return final


def decode_candidate_set(connection: sqlite3.Connection, event_key: str) -> list[dict[str, Any]]:
    """Decode candidate set."""
    row = connection.execute(
        "SELECT encoding,payload FROM candidate_score_sets WHERE event_key=?", (event_key,)
    ).fetchone()
    if not row:
        return []
    if row["encoding"] != "zlib-json-v1":
        raise HarnessError(f"unknown candidate encoding: {row['encoding']}")
    return json.loads(zlib.decompress(row["payload"]).decode("utf-8"))


def review_html() -> str:
    """Return the review HTML."""
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quote/Image Selection Harness</title><style>
body{font-family:system-ui,sans-serif;margin:0;background:#f3f3f1;color:#181818}header{position:sticky;top:0;background:#fff;border-bottom:1px solid #bbb;padding:12px;z-index:2}
.filters{display:flex;gap:8px;flex-wrap:wrap}input,select,button{font:inherit;padding:8px;min-height:42px}.wrap{max-width:1200px;margin:auto;padding:14px}
.event{background:#fff;border:1px solid #bbb;border-radius:6px;padding:14px;margin:12px 0}.quote{font-family:Georgia,serif;font-size:1.25rem;white-space:pre-wrap}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.panel{min-width:0}.panel img{width:100%;max-height:360px;object-fit:contain;background:#ddd}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.82rem}.badge{display:inline-block;padding:3px 7px;border:1px solid #777;margin:2px;border-radius:3px}
@media(max-width:820px){.grid{grid-template-columns:1fr}.wrap{padding:8px}.event{padding:10px}}
</style></head><body><header><div class="filters">
<select id="mode"><option value="">all modes</option><option>full_corpus_sweep</option><option>monte_carlo</option><option>seasonal_boundary_stress</option></select>
<select id="veto"><option value="">all veto states</option><option value="veto">vetoed</option><option value="allow">allowed</option><option value="unknown_unjudged">unknown</option></select>
<input id="season" placeholder="seasonal rule or signature"><input id="year" type="number" placeholder="year"><input id="month" type="number" min="1" max="12" placeholder="month">
<label><input id="disagree" type="checkbox"> editorial disagreement</label><label><input id="vetoAlt" type="checkbox"> veto with alternative</label>
<label><input id="noSafe" type="checkbox"> no safe image</label><label><input id="unknown" type="checkbox"> unknown winner</label>
<label><input id="generated" type="checkbox"> generated out of scope</label><label><input id="repeatQuote" type="checkbox"> repeated quote</label>
<label><input id="repeatImage" type="checkbox"> repeated image</label><label><input id="cycle" type="checkbox"> cycle exhaustion</label>
<input id="loss" type="number" step="0.1" placeholder="minimum score loss"><input id="quote" placeholder="quote ID or text"><input id="image" placeholder="image"><button onclick="loadEvents()">Apply</button></div></header>
<main class="wrap"><div id="status"></div><div id="events"></div></main><script>
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
async function loadEvents(){const p=new URLSearchParams({mode:mode.value,veto:veto.value,season:season.value,year:year.value,month:month.value,quote:quote.value,image:image.value,loss:loss.value,disagree:disagree.checked?'1':'',veto_alt:vetoAlt.checked?'1':'',no_safe:noSafe.checked?'1':'',unknown:unknown.checked?'1':'',generated:generated.checked?'1':'',repeat_quote:repeatQuote.checked?'1':'',repeat_image:repeatImage.checked?'1':'',cycle:cycle.checked?'1':''});
const d=await (await fetch('/api/events?'+p)).json();status.textContent=`${d.count} matching events (showing ${d.events.length})`;
events.innerHTML=d.events.map(e=>`<article class=event><div><span class=badge>${esc(e.mode)}</span><span class=badge>${esc(e.simulated_timestamp)}</span><span class=badge>${esc(e.veto_status)}</span></div>
<p class=quote>${esc(e.quote_text)}</p><p>${esc(e.meaning||'')}</p><div class=grid>${panel('Production',e.production_image,e.production_score)}${panel('Editorial shadow',e.editorial_image,e.editorial_score)}${panel('Known-safe alternative',e.allowed_alternative,e.alternative_score_loss)}</div>
<details><summary>Scores, state and veto details</summary><pre>${esc(JSON.stringify(e.detail,null,2))}</pre></details><small>seed ${esc(e.seed)} · state ${esc(e.state_hash)} · ${esc(e.reproduce)}</small></article>`).join('');}
function panel(title,img,score){return `<section class=panel><h3>${esc(title)}</h3>${img?`<img src="/image/${encodeURIComponent(img)}" alt="">`:''}<p>${esc(img||'none')} · ${esc(score)}</p></section>`}loadEvents();
</script></body></html>"""


def serve(run_dir: Path, host: str, port: int) -> None:
    """Serve the configured local interface."""
    connection_path = run_dir / DB_NAME
    snapshot = run_dir / SNAPSHOT_DIRNAME
    packets = json.loads((snapshot / "research_packets.json").read_text(encoding="utf-8"))["items"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def send_bytes(self, payload: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.send_bytes(review_html().encode(), "text/html; charset=utf-8")
                return
            if parsed.path.startswith("/image/"):
                basename = Path(unquote(parsed.path.removeprefix("/image/"))).name
                candidates = [snapshot / "images" / basename, snapshot / "generated_images" / basename]
                path = next((item for item in candidates if item.is_file()), None)
                if path is None:
                    self.send_bytes(b"not found", "text/plain", 404)
                    return
                mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
                self.send_bytes(path.read_bytes(), mime)
                return
            if parsed.path == "/api/events":
                query = parse_qs(parsed.query)
                clauses = ["1=1"]
                values: list[Any] = []
                for key, column in (("mode", "mode"), ("veto", "veto_status")):
                    value = (query.get(key) or [""])[0]
                    if value:
                        clauses.append(f"{column}=?")
                        values.append(value)
                quote_filter = (query.get("quote") or [""])[0]
                if quote_filter:
                    clauses.append("(quote_id LIKE ? OR quote_text LIKE ?)")
                    values.extend([f"%{quote_filter}%", f"%{quote_filter}%"])
                image_filter = (query.get("image") or [""])[0]
                if image_filter:
                    clauses.append("production_image LIKE ?")
                    values.append(f"%{image_filter}%")
                season_filter = (query.get("season") or [""])[0]
                if season_filter:
                    clauses.append("(seasonal_signature LIKE ? OR active_rules_json LIKE ?)")
                    values.extend([f"%{season_filter}%", f"%{season_filter}%"])
                year_filter = (query.get("year") or [""])[0]
                if year_filter:
                    clauses.append("simulated_year=?")
                    values.append(int(year_filter))
                month_filter = (query.get("month") or [""])[0]
                if month_filter:
                    clauses.append("CAST(substr(simulated_timestamp,6,2) AS INTEGER)=?")
                    values.append(int(month_filter))
                if (query.get("disagree") or [""])[0]:
                    clauses.append("editorial_image IS NOT NULL AND editorial_image != production_image")
                if (query.get("veto_alt") or [""])[0]:
                    clauses.append("veto_status='veto' AND allowed_alternative IS NOT NULL")
                if (query.get("no_safe") or [""])[0]:
                    clauses.append("no_safe_image=1")
                if (query.get("unknown") or [""])[0]:
                    clauses.append("veto_status='unknown_unjudged'")
                if (query.get("generated") or [""])[0]:
                    clauses.append("veto_status='out_of_scope_generated'")
                if (query.get("repeat_quote") or [""])[0]:
                    clauses.append("repeated_quote=1")
                if (query.get("repeat_image") or [""])[0]:
                    clauses.append("repeated_image=1")
                if (query.get("cycle") or [""])[0]:
                    clauses.append("(image_cycle_reset=1 OR quote_cycle_reset=1)")
                loss_filter = (query.get("loss") or [""])[0]
                if loss_filter:
                    clauses.append("alternative_score_loss>=?")
                    values.append(float(loss_filter))
                where = " AND ".join(clauses)
                db = sqlite3.connect(f"file:{connection_path}?mode=ro", uri=True)
                db.row_factory = sqlite3.Row
                count = db.execute(f"SELECT COUNT(*) FROM simulation_events WHERE {where}", values).fetchone()[0]
                rows = db.execute(
                    f"SELECT * FROM simulation_events WHERE {where} ORDER BY simulated_timestamp DESC LIMIT 100", values
                ).fetchall()
                events = []
                for row in rows:
                    value = dict(row)
                    value["detail"] = json.loads(value.pop("detail_json"))
                    value["meaning"] = (packets.get(value.get("quote_id")) or {}).get("intended_argument", "")
                    value["reproduce"] = (
                        "python3 quote_image_selection_harness.py reproduce-event "
                        f"--run-dir {run_dir} --event-key {value['event_key']}"
                    )
                    events.append(value)
                db.close()
                self.send_bytes(json.dumps({"count": count, "events": events}, ensure_ascii=False).encode(), "application/json")
                return
            self.send_bytes(b"not found", "text/plain", 404)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Selection harness inspector: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def preflight(project_dir: Path, run_dir: Path) -> dict[str, Any]:
    """Build the deterministic execution preflight."""
    if project_dir.resolve() != ROOT.resolve():
        raise HarnessError(f"project directory must be {ROOT}")
    manifest = source_snapshot(run_dir)
    connection = initialise_database(run_dir / DB_NAME)
    record_snapshot_rows(connection, manifest)
    connection.close()
    run_manifest = {
        "schema_version": 1,
        "harness_version": HARNESS_VERSION,
        "created_at": utc_now(),
        "project_dir": str(ROOT),
        "run_dir": str(run_dir),
        "snapshot_manifest_sha256": sha256_file(run_dir / SNAPSHOT_DIRNAME / "manifest.json"),
        "completed_quote_count": manifest["completed_quote_count"],
        "unresolved_quote_count": manifest["unresolved_quote_count"],
        "network_allowed": False,
        "provider_calls_allowed": False,
        "production_writes_allowed": False,
        "workers_default": 1,
        "workers_maximum": 2,
    }
    atomic_write_json(run_dir / "run_manifest.json", run_manifest)
    return run_manifest


def parse_int_list(value: str) -> list[int]:
    """Parse int list."""
    result = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not result:
        raise argparse.ArgumentTypeError("at least one integer is required")
    return result


def common_run_argument(parser: argparse.ArgumentParser) -> None:
    """Perform the common run argument operation."""
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    parser.add_argument("--nice", type=int, choices=range(0, 20), default=5)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight")
    pre.add_argument("--project-dir", type=Path, default=ROOT)
    common_run_argument(pre)
    seasons = sub.add_parser("map-seasons")
    common_run_argument(seasons)
    seasons.add_argument("--years", type=parse_int_list, default=list(DEFAULT_YEARS))
    sweep = sub.add_parser("sweep")
    common_run_argument(sweep)
    sweep.add_argument("--all-seasonal-signatures", action="store_true")
    sweep.add_argument("--all-state-profiles", action="store_true")
    sweep.add_argument("--profiles")
    sweep.add_argument("--limit-signatures", type=int)
    sweep.add_argument("--limit-quotes", type=int)
    sweep.add_argument("--pilot", action="store_true")
    sweep.add_argument("--resume", action="store_true")
    simulate = sub.add_parser("simulate")
    common_run_argument(simulate)
    simulate.add_argument("--years", type=parse_int_list, default=[2026, 2028])
    simulate.add_argument("--seeds", type=int, default=25)
    simulate.add_argument("--seed-start", type=int, default=0)
    simulate.add_argument("--pilot", action="store_true")
    simulate.add_argument("--max-events", type=int)
    simulate.add_argument("--resume", action="store_true")
    stress = sub.add_parser("stress-seasonal-boundaries")
    common_run_argument(stress)
    stress.add_argument("--resume", action="store_true")
    replay = sub.add_parser("replay")
    common_run_argument(replay)
    replay.add_argument("--project-dir", type=Path, default=ROOT)
    replay.add_argument("--since-days", type=int, default=30)
    reproduce = sub.add_parser("reproduce-event")
    common_run_argument(reproduce)
    reproduce.add_argument("--event-key", required=True)
    report = sub.add_parser("report")
    common_run_argument(report)
    serve_parser = sub.add_parser("serve")
    common_run_argument(serve_parser)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8770)
    return parser


def dispatch(args: argparse.Namespace, run_dir: Path) -> Any:
    """Return the dispatch."""
    if args.command == "preflight":
        return preflight(args.project_dir, run_dir)
    if args.command == "report":
        return generate_reports(run_dir)
    if args.command == "serve":
        return serve(run_dir, args.host, args.port)
    ctx = load_context(run_dir)
    try:
        if args.command == "map-seasons":
            return map_seasons(ctx, args.years)
        if args.command == "sweep":
            profiles = args.profiles.split(",") if args.profiles else None
            return run_sweep(
                ctx, profiles_requested=profiles, limit_signatures=args.limit_signatures,
                limit_quotes=args.limit_quotes, pilot=args.pilot,
            )
        if args.command == "simulate":
            return run_simulation(
                ctx, years=args.years, seeds=args.seeds, pilot=args.pilot,
                seed_start=args.seed_start, max_events=args.max_events,
            )
        if args.command == "stress-seasonal-boundaries":
            return stress_boundaries(ctx)
        if args.command == "replay":
            if args.project_dir.resolve() != ROOT.resolve():
                raise HarnessError(f"project directory must be {ROOT}")
            return historical_replay(ctx, args.since_days)
        if args.command == "reproduce-event":
            return reproduce_event(ctx, args.event_key)
        raise HarnessError(f"unsupported command: {args.command}")
    finally:
        ctx.connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line entry point."""
    args = build_parser().parse_args(argv)
    run_dir = validate_run_dir(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.nice:
        try:
            os.nice(args.nice)
        except OSError:
            pass
    if args.workers > 1:
        print("Two workers requested; production globals require serial selector execution, so effective workers=1", file=sys.stderr)
    if args.command == "serve":
        dispatch(args, run_dir)
        return 0
    audit = OpenAudit(run_dir)
    try:
        with audit, block_outbound_network():
            result = dispatch(args, run_dir)
    finally:
        atomic_write_json(run_dir / "open_audit.json", audit.summary())
    if isinstance(result, dict):
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
